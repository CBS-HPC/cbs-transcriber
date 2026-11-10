"""MVP release profile in the browser (ADR 0145): no hidden feature is offered anywhere."""

import json

import pytest
from admission_support import write_original_wav
from playwright.sync_api import expect
from test_browser import LocalApp, create_project
from test_browser import page as page
from test_browser_activity_stop import activity_job

from cbs_transcriber import providers
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser

HIDDEN_REQUESTS = (
    "/retranscriptions",
    "/codebook/",
    "/annotation-runs",
    "/research-report",
    "/enhancement-api/check",
    "/enhancement-api/prepare",
    "/runtime-batches/settings",
    "/enhancement-routing",
    "/wording-",
    "/portable-simple/",
    "/portable-retention",
    "/portable-restore",
)


@pytest.fixture
def mvp_app(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("CBS_TRANSCRIBER_PROFILE", "mvp")
    app = LocalApp(tmp_path_factory.mktemp("mvp") / "library")
    app.start()
    yield app
    app.stop()


def watch(page):
    """Console errors, uncaught exceptions and any request for a hidden feature."""
    seen = {"errors": [], "hidden": []}
    # Failed requests are checked by URL below; the synthetic recording has no prepared
    # playback file, so only its media request may answer 404.
    page.on(
        "console",
        lambda message: (
            seen["errors"].append(message.text)
            if message.type == "error" and not message.text.startswith("Failed to load resource")
            else None
        ),
    )
    page.on("pageerror", lambda error: seen["errors"].append(str(error)))
    page.on(
        "response",
        lambda response: (
            seen["errors"].append(f"{response.status} {response.url}")
            if response.status >= 400 and "/media/audio" not in response.url
            else None
        ),
    )
    page.on(
        "request",
        lambda request: (
            seen["hidden"].append(request.url)
            if any(part in request.url for part in HIDDEN_REQUESTS)
            else None
        ),
    )
    return seen


def configure(local_app):
    app = local_app.server.config.app
    store, credentials = app.state.store, app.state.credentials
    providers.save_profile(
        store,
        credentials,
        "OpenAI",
        providers.Settings(model="gpt-transcribe", capability="json"),
        api_key="synthetic-openai-key",
        remember=False,
    )
    providers.save_profile(
        store,
        credentials,
        "UCloud",
        providers.Settings(
            provider_type="ucloud",
            base_url="https://cloud.invalid",
            model="large-v3",
            language="da",
            ucloud_transcriber={
                "project_id": "synthetic-project",
                "storage_path": "/drive/cbs",
                "application": {"name": "transcriber", "version": "1.12"},
                "product": {
                    "id": "gpu-nvidia-b200-1-gpu",
                    "category": "gpu",
                    "provider": "ucloud",
                },
            },
        ),
        api_key="synthetic-cloud-token",
        remember=False,
    )
    # Runtime models stay saved and enabled; only the profile hides them.
    for row in store.all("SELECT id,value FROM providers"):
        value = json.loads(row["value"])
        if value["model"].startswith("whisperx/"):
            value["enabled"] = True
            store.execute("UPDATE providers SET value=? WHERE id=?", (json.dumps(value), row["id"]))
    return store


def recording_with_results(store, project_id):
    recording = store.create_recording(project_id, "Interview.wav")
    write_original_wav(store, recording)
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    words = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate("abc")]
    # The first saved result is the active one under review.
    target = store.add_result(
        recording["id"],
        normalize_segments([{"text": "a b c", "start": 0, "end": 1.5, "words": words}]),
        {},
        "Native",
        {"capability": "words"},
    )
    source = store.add_result(
        recording["id"],
        normalize_segments([{"text": "a b x", "start": 0, "end": 1.5, "words": words}]),
        {},
        "Comparison",
        {},
    )
    return recording, target, source


def test_mvp_walkthrough_offers_no_hidden_feature(page, mvp_app):
    seen = watch(page)
    store = configure(mvp_app)
    page.goto(mvp_app.url)
    expect(page.locator("html")).to_have_attribute("data-profile", "mvp")
    create_project(page, "MVP walkthrough")
    project = store.all("SELECT id FROM projects")[0]["id"]

    # Project sidebar: only Export Settings remains above Project Settings.
    expect(page.locator("#project-export-open")).to_be_visible()
    for hidden in ("#research-report-open", "#project-topics-open"):
        expect(page.locator(hidden)).to_be_hidden()
    # Code that shows an element again cannot reveal a hidden feature.
    page.evaluate("document.querySelector('#research-report-open').hidden = false")
    expect(page.locator("#research-report-open")).to_be_hidden()

    # Model choices: native Transcriber and OpenAI only; no CBS Runtime group.
    recording, target, source = recording_with_results(store, project)
    page.reload()
    expect(page.locator(".recording-card")).to_have_count(1)
    runtime = page.evaluate(
        "state.providers.filter(p => p.model.startsWith('whisperx/') && p.enabled !== false).length"
    )
    assert runtime > 0
    page.locator("#bulk-provider-kind").select_option("ucloud")
    expect(page.locator('#bulk-provider optgroup[label="CBS Runtime / WhisperX"]')).to_have_count(0)
    labels = page.locator("#bulk-provider option").all_text_contents()
    assert labels and not any("CBS Runtime" in label for label in labels)

    # OpenAI word timing is added on this computer only.
    page.locator("#bulk-provider-kind").select_option("openai")
    timing = page.locator('#bulk-features [data-transcription-feature="word_timing"]')
    expect(timing.locator("xpath=..")).to_have_attribute(
        "title", "Add after transcription on this computer"
    )
    page.locator("#select-all").click()
    timing.check()
    page.locator("#transcribe-selected").click()
    expect(page.locator("#enhancement-dialog")).to_be_visible()
    options = page.locator("#enhance-execution option")
    expect(options).to_have_count(1)
    expect(page.locator("#enhance-execution")).to_have_value("local")
    expect(page.locator("#enhance-whisper-label")).to_be_hidden()
    page.locator('#enhancement-dialog [data-close="enhancement-dialog"]').first.click()
    assert not store.all("SELECT id FROM jobs WHERE kind!='media'")

    # Editor: Compare stays read-only; Re-transcribe, Topics and Realign are absent.
    page.goto(f"{mvp_app.url}/#/recordings/{recording['id']}")
    expect(page.locator("#passage-0 .passage-text")).to_have_text("a b c")
    expect(page.locator("#export-open")).to_be_visible()
    expect(page.locator("#topics-open")).to_be_hidden()
    expect(page.locator("[data-action=retranscribe]")).to_have_count(0)
    expect(page.locator("#retranscribe-flagged")).to_be_hidden()
    page.locator("#compare-result").select_option(source["id"])
    expect(page.locator("#comparison-info")).to_contain_text("Matching passages aligned")
    expect(page.locator("#compare-passage-0")).to_be_visible()
    expect(page.locator(".wording-adopt")).to_have_count(0)
    page.locator("#compare-result").select_option("")
    page.locator("#passage-0 [data-action=edit]").click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Leave unassigned"
    ).click()
    expect(page.locator("#passage-0 textarea")).to_be_visible()
    expect(page.locator("#passage-0 .passage-realign")).to_be_hidden()
    page.locator("#passage-0 textarea").fill("a b c d")
    with page.expect_request(lambda r: r.url.endswith("/segments/0/edit-v3")) as saved:
        page.locator("#passage-0 [data-action=save-edit]").click()
    assert "realign" not in json.loads(saved.value.post_data)
    expect(page.locator("#passage-0 .passage-text")).to_have_text("a b c d")
    assert not store.all("SELECT id FROM jobs WHERE kind='enhance'")

    # Export.
    page.locator("#export-open").click()
    expect(page.locator("#export-dialog")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#export-dialog")).to_be_hidden()

    # Settings: no Automatic rules, Python path or CBS Runtime panels; the version is named.
    page.locator("#settings-open").click()
    expect(page.locator("#release-note")).to_contain_text("release profile mvp")
    page.locator("#tab-processing").click()
    expect(page.locator("#enhance-device")).to_be_visible()
    for hidden in ("#enhance-python", "#enhance-rules", "#enhance-auto-note"):
        expect(page.locator(hidden)).to_be_hidden()
    page.locator("#tab-huggingface").click()
    expect(page.locator("#hf-settings-token")).to_be_visible()
    page.locator("#tab-ucloud").click()
    expect(page.locator("#connect-ucloud")).to_be_visible()
    expect(page.locator("#runtime-preparation-panel")).to_be_hidden()
    expect(page.locator("#runtime-capacity-panel")).to_be_hidden()
    expect(
        page.locator('#provider-model-list [aria-label="CBS Runtime / WhisperX"]')
    ).to_have_count(0)
    expect(page.locator('#provider-model-list [aria-label="UCloud Transcriber"]')).to_be_visible()
    page.keyboard.press("Escape")

    # Library: local Create backup stays; UCloud backup, retention and restore are hidden.
    page.locator("#library-open").click()
    expect(page.locator("#portable-create")).to_be_visible()
    expect(page.locator("#portable-receipt")).to_be_visible()
    for hidden in (
        "#portable-simple-cloud",
        "#portable-retention-panel",
        "#portable-restore-panel",
    ):
        expect(page.locator(hidden)).to_be_hidden()
    page.keyboard.press("Escape")

    # Activity.
    page.locator("#activity-open").click()
    expect(page.locator("#activity")).to_be_visible()
    page.wait_for_timeout(500)
    assert seen["hidden"] == []
    assert seen["errors"] == []


def test_mvp_activity_folds_routing_details_but_keeps_recovery(page, mvp_app):
    job = activity_job(
        "a" * 32,
        "failed",
        kind="enhance",
        execution="ucloud",
        provider_name="UCloud WhisperX enhancement",
        ucloud_server="https://cloud.invalid",
        ucloud_job_id="synthetic-remote",
        ucloud_remote_state="FAILURE",
        routing_reason="Recording length selects UCloud",
        source_changed=True,
    )
    page.route("**/api/jobs", lambda route: route.fulfill(json=[job]))
    page.route(
        "**/api/activity", lambda route: route.fulfill(json={"cleared_at": None, "hidden": {}})
    )
    page.goto(mvp_app.url)
    page.locator("#activity-open").click()
    card = page.locator("#jobs .job")
    expect(card).to_contain_text("The source transcript was edited after this was queued")
    details = card.locator("details.activity-details")
    expect(details.locator("summary")).to_have_text("Details")
    expect(card.get_by_text("Automatic: Recording length selects UCloud")).to_be_hidden()
    expect(card.locator(".job-footer button").first).to_be_visible()
    details.locator("summary").click()
    expect(card.get_by_text("Automatic: Recording length selects UCloud")).to_be_visible()
    expect(card.get_by_role("link", name="Open UCloud job ↗")).to_be_visible()
    # A later poll re-renders the card without collapsing the reader's choice.
    page.evaluate("async () => await pollJobs()")
    expect(card.get_by_text("Automatic: Recording length selects UCloud")).to_be_visible()


def test_mvp_shows_ucloud_backup_only_to_resolve_an_earlier_upload(page, mvp_app):
    seen = watch(page)
    upload = {
        "operation_id": "b" * 32,
        "backup_id": "c" * 32,
        "state": "folder_uncertain",
        "local_package": "C:/synthetic/portable",
        "commit_sha256": "d" * 64,
    }
    operations = {"uploads": [], "restores": []}
    page.route("**/api/library/portable-operations", lambda route: route.fulfill(json=operations))
    page.goto(mvp_app.url)
    page.locator("#library-open").click()
    expect(page.locator("#portable-create")).to_be_visible()
    page.wait_for_timeout(300)
    expect(page.locator("#portable-simple-cloud")).to_be_hidden()
    page.keyboard.press("Escape")

    operations["uploads"].append(upload)
    page.locator("#library-open").click()
    panel = page.locator("#portable-simple-cloud")
    expect(panel).to_be_visible()
    panel.locator("> summary").click()
    expect(page.locator("#portable-simple-status")).to_contain_text(
        "This version does not upload backups to UCloud"
    )
    expect(page.locator("#portable-simple-reconcile")).to_be_visible()
    for hidden in (
        "#portable-simple-upload",
        "#portable-simple-location",
        "#portable-simple-resume",
    ):
        expect(page.locator(hidden)).to_be_hidden()
    assert seen["hidden"] == []
