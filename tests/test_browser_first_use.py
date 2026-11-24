"""First-use credential setup uses disposable libraries and mocked native metadata."""

import json
from pathlib import Path

import pytest
from admission_support import checked_connection
from playwright.sync_api import expect
from test_browser import create_project, provider_advanced, settings_tabs
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.catalog import provider_catalog
from cbs_transcriber.providers import (
    Settings,
    credential_scope,
    default_provider_id,
    profile,
    save_profile,
)
from cbs_transcriber.store import new_id, now
from cbs_transcriber.transcripts import normalize_segments
from cbs_transcriber.ucloud_native import COMPUTE_ID, MIG_PRODUCT

pytestmark = [pytest.mark.browser, pytest.mark.first_use]
MARKER = "cbs-first-use-completed-v1"


@pytest.fixture
def native_account(monkeypatch):
    calls = []

    def discover(settings, key, *, compute_id=None):
        assert key == "synthetic-ucloud-key"
        assert compute_id == MIG_PRODUCT["id"]
        calls.append(settings.model)
        setup = settings.ucloud_transcriber.model_dump()
        setup.update(
            project_id="synthetic-project",
            application={"name": "transcriber-batch", "version": "1.12"},
            product=dict(MIG_PRODUCT),
            storage_path="/drive/CBS-Transcriber",
        )
        return {"ready": True, "setup": setup, "choices": {}, "note": "Synthetic account ready."}

    monkeypatch.setattr("cbs_transcriber.ucloud_discovery.discover", discover)
    return calls


def test_first_use_skip_lasts_for_the_page_while_no_ucloud_key_is_saved(page, local_app):
    requests = []
    page.on("request", lambda request: requests.append((request.method, request.url)))
    page.goto(local_app.url)
    form = page.locator("#first-use-setup")
    expect(form).to_be_visible()
    dialog = page.locator("#first-use-dialog")
    expect(dialog).to_have_js_property("open", True)
    assert form.evaluate("node => node.closest('#library-view') === null")
    expect(page.locator("#first-use-ucloud-key")).to_be_focused()
    expect(page.locator("#welcome")).to_be_visible()
    screenshots = Path("test-results/morning-onboarding")
    screenshots.mkdir(parents=True, exist_ok=True)
    for width in (1440, 390):
        page.set_viewport_size({"width": width, "height": 1000})
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        field = page.locator("#first-use-ucloud-key").bounding_box()
        assert field["width"] >= form.bounding_box()["width"] * 0.75
        page.screenshot(path=str(screenshots / f"first-use-{width}.png"))
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.locator("#first-use-ucloud-key").fill("unsaved-ucloud-draft")
    page.locator("#first-use-hf-token").fill("unsaved-hf-draft")
    form.get_by_role("button", name="Skip for now").click()
    expect(form).to_be_hidden()
    expect(page.locator("#first-use-ucloud-key")).to_have_value("")
    expect(page.locator("#first-use-hf-token")).to_have_value("")
    assert page.evaluate("key => localStorage.getItem(key)", MARKER) == "1"
    assert not any(method in {"POST", "PUT"} for method, _ in requests)
    # No UCloud key is saved, so setup returns on the next load (user decision, 19 November).
    page.reload()
    expect(form).to_be_visible()
    assert not any(method in {"POST", "PUT"} for method, _ in requests)
    create_project(page, "After skipping setup")
    expect(page.locator("#project-title")).to_have_text("After skipping setup")
    page.locator("#settings-open").click()
    expect(page.get_by_role("tab", name="UCloud", exact=True)).to_have_attribute(
        "aria-selected", "true"
    )
    expect(page.get_by_role("tab", name="OpenAI API", exact=True)).to_have_count(0)


def test_first_use_offer_is_not_defined_by_project_count(page, local_app):
    local_app.server.config.app.state.store.create_project("Imported earlier")
    page.goto(local_app.url)
    expect(page.locator("#first-use-setup")).to_be_visible()
    expect(page.locator("#project-title")).to_have_text("Imported earlier")


@pytest.mark.parametrize("action", ["close", "escape"])
def test_setup_modal_dismisses_without_writes_and_restores_focus(page, local_app, action):
    writes = []
    page.on(
        "request",
        lambda request: writes.append(request.url) if request.method in {"POST", "PUT"} else None,
    )
    page.goto(local_app.url)
    dialog = page.locator("#first-use-dialog")
    expect(dialog).to_have_js_property("open", True)
    key = page.locator("#first-use-ucloud-key")
    expect(key).to_have_class("credential-missing")
    assert key.evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(255, 252, 200)"
    key.fill("synthetic-draft")
    expect(key).not_to_have_class("credential-missing")
    key.fill("")
    expect(key).to_have_class("credential-missing")
    page.locator("#first-use-hf-token").fill("hf_synthetic-draft")
    if action == "close":
        page.get_by_role("button", name="Close setup", exact=True).click()
    else:
        page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(key).to_have_value("")
    expect(page.locator("#first-use-hf-token")).to_have_value("")
    expect(page.locator("#settings-open")).to_be_focused()
    assert page.evaluate("key => localStorage.getItem(key)", MARKER) == "1"
    assert not writes
    # Without a saved UCloud key the dismissal lasts only for this page.
    page.reload()
    expect(dialog).to_have_js_property("open", True)


def test_ucloud_key_highlight_distinguishes_saved_key_and_changed_endpoint(
    page, local_app, native_account
):
    page.goto(local_app.url)
    page.locator("#first-use-ucloud-key").fill("synthetic-ucloud-key")
    page.locator("#first-use-save").click()
    expect(page.locator("#first-use-dialog")).to_be_hidden()
    page.locator("#settings-open").click()
    key = page.locator("#api-key")
    expect(key).to_have_value("")
    expect(key).not_to_have_class("credential-missing")
    provider_advanced(page)
    endpoint = page.locator("#base-url")
    original = endpoint.input_value()
    endpoint.fill("https://different.invalid")
    expect(key).to_have_class("credential-missing")
    key.fill("unsaved-synthetic-key")
    expect(key).not_to_have_class("credential-missing")
    key.fill("")
    expect(key).to_have_class("credential-missing")
    endpoint.fill(original)
    expect(key).not_to_have_class("credential-missing")
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#forget-key").click()
    expect(key).to_have_class("credential-missing")
    assert not local_app.server.config.app.state.credentials.get(
        "ucloud-control:https://cloud.sdu.dk"
    )


def test_first_use_connect_saves_exact_checked_mig_without_hf(page, local_app, native_account):
    writes = []
    page.on(
        "request",
        lambda request: (
            writes.append((request.url, request.post_data_json))
            if request.method in {"POST", "PUT"}
            else None
        ),
    )
    page.goto(local_app.url)
    page.locator("#first-use-ucloud-key").fill("synthetic-ucloud-key")
    page.locator("#first-use-save").click()
    expect(page.locator("#first-use-setup")).to_be_hidden()
    app = local_app.server.config.app
    assert (
        app.state.credentials.get("ucloud-control:https://cloud.sdu.dk") == "synthetic-ucloud-key"
    )
    assert native_account == ["large-v3-turbo"]
    saved = next(body for url, body in writes if url.endswith("/api/providers"))
    assert saved["native_allocation_choice"] == "mig1g"
    assert saved["ucloud_transcriber"]["product"] == MIG_PRODUCT
    assert saved["connection_check"]
    assert saved["model"] == "large-v3-turbo"
    providers = [
        json.loads(row["value"]) for row in app.state.store.all("SELECT value FROM providers")
    ]
    assert {
        provider["model"]
        for provider in providers
        if provider["provider_type"] == "ucloud" and provider["enabled"]
    } == {"large-v3-turbo", "nvidia/parakeet-tdt-0.6b-v3"}
    assert not app.state.store.all("SELECT id FROM jobs")
    assert not any("huggingface-settings" in url for url, _ in writes)
    assert page.evaluate("key => localStorage.getItem(key)", MARKER) == "1"
    storage = page.evaluate("() => JSON.stringify({...localStorage})")
    assert "synthetic-ucloud-key" not in storage
    page.reload()
    page.wait_for_function("() => firstUse.ready")
    expect(page.locator("#first-use-setup")).to_be_hidden()
    app.state.credentials.set("ucloud-control:https://cloud.sdu.dk", "", False)
    page.reload()
    page.wait_for_function("() => firstUse.ready")
    expect(page.locator("#first-use-setup")).to_be_visible()


@pytest.mark.parametrize("cancel", ["skip", "navigate", "another-tab"])
def test_late_first_use_discovery_cannot_save_after_cancel(page, local_app, native_account, cancel):
    app = local_app.server.config.app
    project = app.state.store.create_project("Another view")
    held, writes = [], []

    def hold(route):
        held.append((route, route.fetch()))
        page.evaluate("window.firstUseDiscoveryHeld = true")

    page.route("**/api/providers/discover-ucloud", hold)
    page.on(
        "request", lambda request: writes.append(request.url) if request.method == "POST" else None
    )
    page.goto(local_app.url)
    page.locator("#first-use-ucloud-key").fill("synthetic-ucloud-key")
    page.locator("#first-use-save").click()
    expect(page.locator("#first-use-status")).to_contain_text("Checking")
    page.wait_for_function("() => window.firstUseDiscoveryHeld === true")
    assert held
    if cancel == "skip":
        page.locator("#first-use-skip").click()
    elif cancel == "another-tab":
        other = page.context.new_page()
        other.goto(local_app.url)
        other.locator("#first-use-skip").click()
        expect(page.locator("#first-use-setup")).to_be_hidden()
        other.close()
    else:
        # Native modal backgrounds are inert; browser navigation still needs the fence.
        page.evaluate("id => location.hash = '/projects/' + id", project["id"])
        expect(page.locator("#first-use-setup")).to_be_hidden()
    route, response = held.pop()
    route.fulfill(response=response)
    expect(page.locator("#first-use-ucloud-key")).to_have_value("")
    assert not any(url.endswith("/api/providers") for url in writes)
    assert not app.state.credentials.get("ucloud-control:https://cloud.sdu.dk")
    assert not app.state.store.all("SELECT id FROM jobs")


def test_first_use_accepted_save_blocks_skip_until_reply(page, local_app, native_account):
    held = []

    def hold(route):
        if route.request.method == "POST":
            held.append((route, route.fetch()))
            page.evaluate("window.firstUseSaveHeld = true")
        else:
            route.continue_()

    page.route("**/api/providers", hold)
    page.goto(local_app.url)
    page.locator("#first-use-ucloud-key").fill("synthetic-ucloud-key")
    page.locator("#first-use-save").click()
    expect(page.locator("#first-use-status")).to_contain_text("Saving")
    page.wait_for_function("() => window.firstUseSaveHeld === true")
    expect(page.locator("#first-use-skip")).to_be_disabled()
    expect(page.locator("#first-use-close")).to_be_disabled()
    page.keyboard.press("Escape")
    expect(page.locator("#first-use-dialog")).to_have_js_property("open", True)
    expect(page.locator("#settings-open")).to_be_disabled()
    assert local_app.server.config.app.state.credentials.get("ucloud-control:https://cloud.sdu.dk")
    page.evaluate("location.hash = '/projects/' + '0'.repeat(32)")
    expect(page.locator("#toast")).to_contain_text("Wait for the credential save")
    assert page.url.endswith("#/")
    route, response = held.pop()
    route.fulfill(response=response)
    expect(page.locator("#first-use-setup")).to_be_hidden()
    expect(page.locator("#settings-open")).to_be_enabled()


def test_first_use_partial_hf_failure_keeps_saved_native_and_retries_hf_only(
    page, local_app, native_account
):
    writes = []
    page.on(
        "request",
        lambda request: writes.append(request.url) if request.method in {"POST", "PUT"} else None,
    )
    page.route(
        "**/api/huggingface-settings",
        lambda route: (
            route.fulfill(status=400, json={"detail": "Use a Hugging Face token without spaces."})
            if route.request.method == "PUT"
            else route.continue_()
        ),
    )
    page.goto(local_app.url)
    page.locator("#first-use-ucloud-key").fill("synthetic-ucloud-key")
    page.locator("#first-use-hf-token").fill("bad token")
    page.locator("#first-use-save").click()
    status = page.locator("#first-use-status")
    expect(status).to_contain_text("UCloud connection saved")
    expect(status).to_contain_text("Hugging Face token save was not confirmed")
    expect(page.locator("#first-use-setup")).to_be_visible()
    expect(page.locator("#first-use-ucloud-key")).to_have_value("")
    page.evaluate("loadProviders()")
    expect(status).to_contain_text("UCloud connection saved")
    expect(page.locator("#first-use-setup")).to_be_visible()
    page.unroute("**/api/huggingface-settings")
    page.route(
        "**/api/huggingface-settings",
        lambda route: (
            route.fulfill(json={"has_token": True})
            if route.request.method == "PUT"
            else route.continue_()
        ),
    )
    page.locator("#first-use-hf-token").fill("hf_synthetic-token")
    page.locator("#first-use-save").click()
    expect(page.locator("#first-use-setup")).to_be_hidden()
    expect(page.locator("#toast")).to_contain_text("checks/preparation appear in Activity")
    assert native_account == ["large-v3-turbo"]
    assert len([url for url in writes if url.endswith("/api/providers")]) == 1
    assert not any(url.endswith("/huggingface-settings/check") for url in writes)
    assert not any("prepare" in url or "transcriptions" in url for url in writes)
    storage = page.evaluate("() => JSON.stringify({...localStorage})")
    assert "bad token" not in storage and "hf_synthetic-token" not in storage


def test_existing_structural_ucloud_setup_suppresses_first_use_without_a_check_receipt(
    page, local_app
):
    app = local_app.server.config.app
    save_profile(
        app.state.store,
        app.state.credentials,
        "Existing UCloud",
        Settings(
            provider_type="ucloud",
            base_url="https://cloud.sdu.dk",
            model="large-v3",
            ucloud_transcriber={
                "project_id": "p",
                "storage_path": "/drive/cbs",
                "application": {"name": "transcriber", "version": "1.12"},
                "product": dict(MIG_PRODUCT),
            },
        ),
        api_key="existing-synthetic-key",
        remember=False,
        native_allocation_choice="mig1g",
    )
    page.goto(local_app.url)
    expect(page.locator("#first-use-setup")).to_be_hidden()
    expect(page.locator("#welcome")).to_be_visible()
    assert page.evaluate("key => localStorage.getItem(key)", MARKER) is None


def existing_ucloud(store, *, complete=True):
    """A saved UCloud connection row; no key is stored."""
    setup = {
        "project_id": "p",
        "storage_path": "/drive/cbs",
        "application": {"name": "transcriber", "version": "1.12"},
    }
    if complete:
        setup["product"] = dict(MIG_PRODUCT)
    settings = Settings(
        provider_type="ucloud",
        base_url="https://cloud.sdu.dk",
        model="large-v3",
        ucloud_transcriber=setup,
    )
    store.execute(
        "INSERT INTO providers(id,name,value,created) VALUES (?,?,?,?)",
        (new_id(), "Existing UCloud", settings.model_dump_json(), now()),
    )
    return settings


def test_returning_browser_without_a_saved_ucloud_key_sees_setup_on_every_load(page, local_app):
    """19 November: a browser that once chose Skip is offered setup again while no key is saved."""
    app = local_app.server.config.app
    settings = existing_ucloud(app.state.store)
    page.add_init_script(f"try {{ localStorage.setItem('{MARKER}', '1'); }} catch {{}}")
    page.goto(local_app.url)
    form = page.locator("#first-use-setup")
    expect(form).to_be_visible()
    expect(page.locator("#first-use-ucloud-key")).to_have_class("credential-missing")
    page.locator("#first-use-skip").click()
    expect(form).to_be_hidden()
    page.reload()
    expect(form).to_be_visible()
    page.locator("#first-use-skip").click()
    expect(form).to_be_hidden()
    # Once a key is saved, the complete connection suppresses setup on later loads.
    app.state.credentials.set(credential_scope(settings), "synthetic-ucloud-key", False)
    page.reload()
    page.wait_for_function("() => firstUse.ready")
    expect(form).to_be_hidden()


def test_skip_lasts_across_reloads_once_a_ucloud_key_is_saved(page, local_app):
    app = local_app.server.config.app
    settings = existing_ucloud(app.state.store, complete=False)
    app.state.credentials.set(credential_scope(settings), "synthetic-ucloud-key", False)
    page.goto(local_app.url)
    form = page.locator("#first-use-setup")
    # An incomplete connection still offers setup until it is skipped.
    expect(form).to_be_visible()
    page.locator("#first-use-skip").click()
    expect(form).to_be_hidden()
    page.reload()
    page.wait_for_function("() => firstUse.ready")
    expect(form).to_be_hidden()


def test_unavailable_browser_storage_still_allows_skipping(page, local_app):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script(
        "Object.defineProperty(window, 'localStorage', { get() {"
        " throw new Error('storage blocked'); } });"
    )
    page.goto(local_app.url)
    expect(page.locator("#first-use-setup")).to_be_visible()
    page.locator("#first-use-skip").click()
    expect(page.locator("#first-use-setup")).to_be_hidden()
    create_project(page, "Without browser storage")
    expect(page.locator("#project-title")).to_have_text("Without browser storage")
    assert not errors


@pytest.mark.parametrize("healthy", [False, True])
def test_disabled_native_shaped_openai_rows_do_not_trap_settings(page, local_app, healthy):
    app = local_app.server.config.app
    archived_id = new_id()
    setup = {
        "project_id": "p",
        "storage_path": "/drive/cbs",
        "application": {"name": "transcriber", "version": "1.12"},
        "product": dict(MIG_PRODUCT),
    }
    archived = Settings(
        provider_type="ucloud",
        base_url="https://api.openai.com",
        model="large-v3",
        ucloud_transcriber=setup,
    )
    app.state.store.execute(
        "INSERT INTO providers(id,name,value,created) VALUES (?,?,?,?)",
        (
            archived_id,
            "Archived native-shaped direct service",
            archived.model_dump_json(),
            "2000-01-01",
        ),
    )
    if healthy:
        saved = save_profile(
            app.state.store,
            app.state.credentials,
            "Healthy UCloud",
            Settings(
                provider_type="ucloud",
                base_url="https://cloud.sdu.dk",
                model="large-v3",
                ucloud_transcriber=setup,
            ),
            api_key="synthetic-key",
            remember=False,
            native_allocation_choice="mig1g",
        )
        checked_connection(app.state.store, app.state.credentials, saved["id"])
    before = app.state.store.one("SELECT * FROM providers WHERE id=?", (archived_id,))
    project = app.state.store.create_project("Service choices")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    expect(page.locator(f'#bulk-provider option[value="{archived_id}"]')).to_have_count(0)
    if healthy:
        expect(page.locator("#bulk-provider-kind")).to_have_value("ucloud")
        expect(page.locator("#bulk-provider")).to_have_value(saved["id"])
    page.locator("#settings-open").click()
    expect(page.get_by_role("tab")).to_have_text(settings_tabs(page))
    expect(page.locator("#base-url")).to_have_value("https://cloud.sdu.dk")
    expect(page.locator("#base-url")).to_have_attribute("placeholder", "https://cloud.sdu.dk")
    expect(page.locator(f'#settings-provider option[value="{archived_id}"]')).to_have_count(0)
    page.get_by_role("tab", name="UCloud", exact=True).press("ArrowRight")
    expect(page.get_by_role("tab", name="Hugging Face", exact=True)).to_be_focused()
    page.get_by_role("tab", name="Hugging Face", exact=True).press("Home")
    expect(page.get_by_role("tab", name="UCloud", exact=True)).to_be_focused()
    assert app.state.store.one("SELECT * FROM providers WHERE id=?", (archived_id,)) == before


def test_ambiguous_first_use_reuses_settings_choices_and_clears_front_page_drafts(
    page, local_app, monkeypatch
):
    def discover(settings, key, *, compute_id=None):
        assert key == "synthetic-ucloud-key" and compute_id == MIG_PRODUCT["id"]
        setup = settings.ucloud_transcriber.model_dump()
        if not setup["project_id"]:
            return {
                "ready": False,
                "setup": setup,
                "choices": {
                    "project_id": [
                        {"id": "p1", "name": "Research one"},
                        {"id": "p2", "name": "Research two"},
                    ]
                },
                "note": "Choose your account.",
            }
        setup.update(
            application={"name": "transcriber", "version": "1.12"},
            storage_path="/drive/cbs",
            product=dict(MIG_PRODUCT),
        )
        return {"ready": True, "setup": setup, "choices": {}, "note": "Ready to save."}

    monkeypatch.setattr("cbs_transcriber.ucloud_discovery.discover", discover)
    page.goto(local_app.url)
    page.locator("#first-use-ucloud-key").fill("synthetic-ucloud-key")
    page.locator("#first-use-save").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.locator("#native-discovery-choices")).to_contain_text("Research two")
    expect(page.locator("#first-use-ucloud-key")).to_have_value("")
    expect(page.locator("#first-use-hf-token")).to_have_value("")
    expect(page.locator("#api-key")).to_have_value("synthetic-ucloud-key")
    page.locator('[data-native-choice="project_id"]').select_option("0")
    expect(page.locator("#ucloud-check-results")).to_contain_text("Ready to save")
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).to_be_hidden()
    expect(page.locator("#api-key")).to_have_value("")
    assert page.evaluate("key => localStorage.getItem(key)", MARKER) == "1"


def test_first_use_invalid_ucloud_check_retains_draft_without_saving(page, local_app, monkeypatch):
    def reject(settings, key, **kwargs):
        raise ValueError("Synthetic UCloud token was not accepted.")

    monkeypatch.setattr("cbs_transcriber.ucloud_discovery.discover", reject)
    page.goto(local_app.url)
    page.locator("#first-use-ucloud-key").fill("invalid-synthetic-key")
    page.locator("#first-use-save").click()
    expect(page.locator("#first-use-status")).to_contain_text("token was not accepted")
    expect(page.locator("#first-use-ucloud-key")).to_have_value("invalid-synthetic-key")
    expect(page.locator("#first-use-save")).to_be_enabled()
    assert not local_app.server.config.app.state.credentials.get(
        "ucloud-control:https://cloud.sdu.dk"
    )
    assert page.evaluate("key => localStorage.getItem(key)", MARKER) is None


def test_first_use_lost_save_reply_requires_settings_before_retry(page, local_app, native_account):
    def lose_reply(route):
        if route.request.method == "POST":
            route.fetch()
            route.abort("failed")
        else:
            route.continue_()

    page.route("**/api/providers", lose_reply)
    page.goto(local_app.url)
    page.locator("#first-use-ucloud-key").fill("synthetic-ucloud-key")
    page.locator("#first-use-save").click()
    expect(page.locator("#first-use-status")).to_contain_text("save was not confirmed")
    expect(page.locator("#first-use-save")).to_be_disabled()
    expect(page.locator("#settings-open")).to_be_enabled()
    assert local_app.server.config.app.state.credentials.get("ucloud-control:https://cloud.sdu.dk")
    page.locator("#first-use-close").click()
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.locator("#first-use-ucloud-key")).to_have_value("")
    assert native_account == ["large-v3-turbo"]


def test_held_openai_keeps_existing_result_and_activity_evidence(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Existing research")
    recording = store.create_recording(project["id"], "Earlier.wav")
    saved_provider = profile(store, default_provider_id(store))
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Existing reviewed wording."}]),
        {},
        "Earlier OpenAI result",
        {"kind": "provider", "provider_type": "openai", "model": "gpt-transcribe"},
    )
    job_id = new_id()
    store.execute(
        "INSERT INTO jobs(id,recording_id,kind,state,params,created,updated) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            job_id,
            recording["id"],
            "transcribe",
            "completed",
            json.dumps(
                {"provider_id": saved_provider["id"], "settings": saved_provider["settings"]}
            ),
            now(),
            now(),
        ),
    )
    before = {
        table: store.all("SELECT * FROM " + table + " ORDER BY id")
        for table in ("providers", "jobs", "transcript_results", "result_versions")
    }
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Existing reviewed wording.")
    expect(page.locator("#result-select")).to_have_value(result["id"])
    expect(page.locator(f'#file-provider option[value="{saved_provider["id"]}"]')).to_have_count(0)
    page.locator("#activity-open").click()
    expect(page.locator(f'[data-activity-remove="{job_id}"]')).to_be_visible()
    assert {
        table: store.all("SELECT * FROM " + table + " ORDER BY id") for table in before
    } == before


def test_failed_settings_read_does_not_consume_first_use_offer(page, local_app):
    page.goto(local_app.url)
    expect(page.locator("#first-use-setup")).to_be_visible()
    page.locator("#first-use-ucloud-key").fill("unsaved-draft")
    page.route(
        "**/api/providers",
        lambda route: route.fulfill(
            status=500, json={"detail": "Synthetic provider-list failure."}
        ),
    )
    # Exercise the programmatic handoff failure; the background button is now inert.
    page.evaluate("() => handle(() => openSettings())()")
    expect(page.locator("#toast")).to_contain_text("Synthetic provider-list failure")
    expect(page.locator("#first-use-setup")).to_be_visible()
    expect(page.locator("#first-use-ucloud-key")).to_have_value("")
    assert page.evaluate("key => localStorage.getItem(key)", MARKER) is None
    page.unroute("**/api/providers")
    page.evaluate("() => handle(() => openSettings())()")
    expect(page.locator("#settings-dialog")).to_be_visible()


@pytest.mark.parametrize("runtime_only", [False, True])
def test_key_lost_existing_connection_hands_off_without_widening_policy(
    page, local_app, monkeypatch, runtime_only
):
    app = local_app.server.config.app
    store = app.state.store
    model = (
        provider_catalog()["ucloud_cbs_runtime"]["models"][0]
        if runtime_only
        else {"model": "large-v3", "capability": "words_speakers", "fixed_language": "da"}
    )
    settings = Settings(
        provider_type="ucloud",
        base_url="https://cloud.sdu.dk",
        model=model["model"],
        language=model.get("fixed_language", "da"),
        capability=model["capability"],
        enabled=False,
        approved_for=[],
        concurrent_chunks=2,
        max_upload_mb=18,
        ucloud_transcriber={
            "project_id": "retained-project",
            "storage_path": "/drive/retained",
            "application": {"name": "transcriber", "version": "1.12"},
            "product": {"id": COMPUTE_ID, "category": "gpu", "provider": "ucloud"},
            "cleanup_after_success": False,
        },
    )
    if runtime_only:
        identifier = new_id()
        store.execute(
            "INSERT INTO providers(id,name,value,created) VALUES (?,?,?,?)",
            (identifier, "Retained connection", settings.model_dump_json(), now()),
        )
    else:
        identifier = save_profile(
            store,
            app.state.credentials,
            "Retained connection",
            settings,
            native_allocation_choice="full",
        )["id"]
    store.execute(
        "UPDATE settings SET value=? WHERE id=2", (json.dumps({"default_provider_id": identifier}),)
    )
    before = store.all("SELECT * FROM providers ORDER BY id")
    writes = []
    page.on(
        "request",
        lambda request: writes.append(request.url) if request.method in {"POST", "PUT"} else None,
    )

    reviewed = []

    def discover(settings, key, *, compute_id=None):
        # The existing connection is reviewed in Settings first; saving the replacement key
        # there then rechecks it automatically (user decision, 12 November).
        if not reviewed:
            raise AssertionError("Existing connection must be reviewed before discovery.")
        assert key == "replacement-synthetic-key"
        setup = settings.ucloud_transcriber.model_dump()
        return {"ready": True, "setup": setup, "choices": {}, "note": "Ready to save."}

    monkeypatch.setattr("cbs_transcriber.ucloud_discovery.discover", discover)
    page.goto(local_app.url)
    expect(page.locator("#first-use-setup")).to_be_visible()
    page.locator("#first-use-ucloud-key").fill("replacement-synthetic-key")
    page.locator("#first-use-hf-token").fill("hf_not-yet-saved")
    page.locator("#first-use-save").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.locator("#settings-provider")).to_have_value(identifier)
    if runtime_only and not page.evaluate("featureOn('cbs_runtime')"):
        for selector in ("#bulk-provider", "#file-provider"):
            expect(page.locator(f'{selector} option[value="{identifier}"]')).to_have_count(0)
    expect(page.locator("#base-url")).to_have_value(settings.base_url)
    expect(page.locator("#base-url")).to_be_visible()
    expect(page.locator("#native-project")).to_have_value("retained-project")
    expect(page.locator("#native-project")).to_be_visible()
    expect(page.locator("#native-storage")).to_have_value("/drive/retained")
    expect(page.locator("#api-key")).to_have_value("replacement-synthetic-key")
    expect(page.locator("#first-use-ucloud-key")).to_have_value("")
    expect(page.locator("#first-use-hf-token")).to_have_value("")
    expect(page.locator("#toast")).to_contain_text("not been saved")
    expect(page.locator("#key-status")).to_have_text("No key saved")
    assert not page.locator('[name="approved-for"]').is_checked()
    assert writes == []
    assert store.all("SELECT * FROM providers ORDER BY id") == before
    assert not app.state.credentials.get("ucloud-control:https://cloud.sdu.dk")
    assert page.evaluate("key => localStorage.getItem(key)", MARKER) is None
    if not runtime_only:
        reviewed.append(True)
        page.get_by_role("button", name="Save settings", exact=True).click()
        expect(page.locator("#settings-dialog")).to_be_hidden()
        assert store.all("SELECT * FROM providers ORDER BY id") == before
        assert (
            app.state.credentials.get("ucloud-control:https://cloud.sdu.dk")
            == "replacement-synthetic-key"
        )
        assert writes == [
            local_app.url + "/api/providers/discover-ucloud",
            local_app.url + f"/api/providers/{identifier}",
        ]
        assert page.evaluate(
            "async id => (await api('/providers')).providers"
            ".find((p) => p.id === id).connection_ready",
            identifier,
        )
