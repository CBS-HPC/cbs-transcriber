"""Native allocation identity tests use fake metadata, never UCloud or inference."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser import provider_advanced

from cbs_transcriber import providers, ucloud_discovery
from cbs_transcriber import ucloud_native as native

pytestmark = pytest.mark.browser
FULL = {"id": native.COMPUTE_ID, "category": "gpu", "provider": "ucloud"}


@pytest.fixture
def configured(local_app, monkeypatch):
    app = local_app.server.config.app
    saved = providers.save_profile(
        app.state.store,
        app.state.credentials,
        "Native turbo",
        providers.Settings(
            provider_type="ucloud",
            base_url="https://cloud.invalid",
            model=native.MIG_MODEL,
            language="da",
            approved_for=["protected"],
            ucloud_transcriber={
                "application": native.MIG_APPLICATION,
                "product": FULL,
                "project_id": "project",
                "storage_path": "/drive/cbs",
            },
        ),
        api_key="synthetic-control-token",
        remember=False,
        native_allocation_choice="full",
    )

    def discover(settings, key, *, compute_id=None):
        assert key == "synthetic-control-token"
        setup = settings.ucloud_transcriber.model_dump()
        setup["application"] = setup["application"] or dict(native.MIG_APPLICATION)
        setup["product"] = dict(
            native.MIG_PRODUCT if compute_id == native.MIG_PRODUCT["id"] else FULL
        )
        return {"ready": True, "setup": setup, "note": "Synthetic metadata only"}

    monkeypatch.setattr(ucloud_discovery, "discover", discover)
    return app.state.store, saved


def open_native(page, local_app):
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    expect(page.get_by_role("group", name="UCloud Transcriber", exact=True)).to_be_visible()
    provider_advanced(page)


def test_new_connection_discovers_only_mig_without_submitting(page, local_app, configured):
    store, _ = configured
    store.execute("DELETE FROM providers WHERE json_extract(value,'$.provider_type')='ucloud'")
    open_native(page, local_app)
    assert page.locator("#native-default-allocation").count() == 0
    page.locator("#base-url").fill("https://cloud.invalid")
    page.locator("#api-key").fill("synthetic-control-token")
    page.locator("#native-project").fill("project")
    page.locator("#native-storage").fill("/drive/cbs")
    with page.expect_response(lambda r: r.url.endswith("/discover-ucloud")) as response:
        page.locator("#connect-ucloud").click()
    assert response.value.request.post_data_json[providers.NATIVE_CHOICE] == "mig1g"
    assert not store.all("SELECT id FROM jobs")


def test_existing_full_connection_recheck_preserves_protected_frozen_job(
    page, local_app, configured
):
    store, saved = configured
    app = local_app.server.config.app
    project = store.create_project("Protected synthetic", classification="protected")
    recording = store.create_recording(project["id"], "synthetic.wav")
    frozen = providers.profile(store, saved["id"])["settings"]
    settings = providers.Settings.model_validate(frozen)
    providers.require_approval(store, project["id"], saved["id"], settings)
    with app.state.queue.lock:
        job = store.add_job(recording["id"], "transcribe", {"settings": frozen})
        store.execute("UPDATE jobs SET state='failed' WHERE id=?", (job["id"],))
    original_job = store.job(job["id"])
    before = store.one("SELECT value FROM providers WHERE id=?", (saved["id"],))["value"]
    open_native(page, local_app)
    with page.expect_response(lambda r: r.url.endswith("/discover-ucloud")) as response:
        page.locator("#connect-ucloud").click()
    assert response.value.request.post_data_json[providers.NATIVE_CHOICE] == "full"
    assert store.one("SELECT value FROM providers WHERE id=?", (saved["id"],))["value"] == before
    page.locator("#settings-form button[type='submit']").click()
    expect(page.locator("#settings-dialog")).to_be_hidden()
    assert providers.profile(store, saved["id"])["settings"] == frozen
    providers.require_approval(store, project["id"], saved["id"], settings)
    assert store.job(job["id"]) == original_job
    _, policies = providers.admission_profiles(store, [saved["id"]])
    assert policies[saved["id"]]["product"] == native.MIG_PRODUCT
