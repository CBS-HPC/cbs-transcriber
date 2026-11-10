"""Current static controls refuse runtime batches on an older local backend before enqueue."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.providers import Settings, save_profile

pytestmark = pytest.mark.browser


@pytest.mark.parametrize(
    ("model", "backend_status", "allowed"),
    [
        ("whisperx/large-v3", 404, False),
        ("whisperx/large-v3", 200, True),
        ("large-v3", 404, True),
        ("whisper-1", 404, True),
    ],
    ids=["old-runtime", "current-runtime", "native-unchanged", "openai-unchanged"],
)
def test_runtime_backend_guard_before_bulk_enqueue(page, local_app, model, backend_status, allowed):
    app = local_app.server.config.app
    store = app.state.store
    save_profile(
        store,
        app.state.credentials,
        "UCloud",
        Settings(
            provider_type="ucloud",
            base_url="https://cloud.invalid",
            model="large-v3",
            ucloud_transcriber={
                "project_id": "synthetic-project",
                "storage_path": "/drive/synthetic",
                "application": {"name": "transcriber", "version": "synthetic"},
                "product": {
                    "id": "gpu-nvidia-b200-1-gpu",
                    "category": "gpu",
                    "provider": "ucloud",
                },
            },
        ),
        api_key="synthetic-cloud-key",
        remember=False,
    )
    save_profile(
        store,
        app.state.credentials,
        "OpenAI whisper-1",
        Settings(model="whisper-1"),
        api_key="synthetic-openai-key",
        remember=False,
    )
    # Runtime models are parked by default; these guard cases explicitly opt in.
    if model.startswith("whisperx/"):
        for row in store.all("SELECT id,name,value FROM providers"):
            settings = Settings.model_validate_json(row["value"])
            if settings.model == model:
                settings.enabled = True
                save_profile(
                    store,
                    app.state.credentials,
                    row["name"],
                    settings,
                    identifier=row["id"],
                )
                break
    project = store.create_project("Synthetic backend capability")
    recordings = [store.create_recording(project["id"], f"Sample {i}.wav") for i in range(2)]
    for recording in recordings:
        store.execute(
            "UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],)
        )
    probes, posts, prompts = [], [], []

    def probe(route):
        probes.append(route.request.method)
        route.fulfill(
            status=backend_status,
            json={"max_allocations": 2} if backend_status == 200 else {"detail": "Not Found"},
        )

    def enqueue(route):
        posts.append(route.request.post_data_json)
        route.fulfill(json={"jobs": []})

    def confirm(dialog):
        prompts.append(dialog.message)
        dialog.accept()

    page.route("**/api/runtime-batches/settings", probe)
    page.route("**/api/transcriptions", enqueue)
    page.on("dialog", confirm)
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#select-all").click()
    page.locator("#bulk-provider-kind").select_option(
        "openai" if model == "whisper-1" else "ucloud"
    )
    provider_id = page.evaluate("model => state.providers.find(p => p.model === model).id", model)
    page.locator("#bulk-provider").select_option(provider_id)
    page.locator("#transcribe-selected").click()
    if allowed:
        expect(page.locator("#toast")).to_have_text("Added to the transcription queue")
        assert len(posts) == 1
        assert posts[0]["provider_ids"] == [provider_id]
        assert set(posts[0]["recording_ids"]) == {recording["id"] for recording in recordings}
        assert len(prompts) == 1
    else:
        expect(page.locator("#toast")).to_have_text(
            "The app backend needs to be restarted before CBS Runtime batches can run. "
            "No job was queued."
        )
        assert posts == []
        assert prompts == []
    assert probes == (["GET"] if model == "whisperx/large-v3" else [])
    assert not store.all("SELECT id FROM jobs")
