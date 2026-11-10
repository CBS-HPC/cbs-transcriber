"""Project settings are staged until explicitly saved."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser


def test_project_settings_cancel_discards_all_fields(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Original", default_language="da")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#project-settings-open").click()
    page.locator("#project-edit-name").fill("Discarded")
    page.locator("#project-edit-classification").select_option("protected")
    page.locator("#project-edit-language").select_option("en")
    page.locator("#project-edit-storage").fill(str(local_app.root / "unused-parent"))
    assert store.one(
        "SELECT name,classification,default_language FROM projects WHERE id=?", (project["id"],)
    ) == {"name": "Original", "classification": "standard", "default_language": "da"}
    page.locator("#project-settings-cancel").click()
    page.locator("#project-settings-open").click()
    expect(page.locator("#project-edit-name")).to_have_value("Original")
    expect(page.locator("#project-edit-classification")).to_have_value("standard")
    expect(page.locator("#project-edit-language")).to_have_value("da")
    assert not (local_app.root / "unused-parent").exists()


def test_project_settings_save_and_stale_name(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Original", default_language="da")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#project-settings-open").click()
    page.locator("#project-edit-name").fill("Updated")
    page.locator("#project-edit-classification").select_option("protected")
    page.locator("#project-edit-language").select_option("en")
    page.locator("#project-settings-save").click()
    expect(page.locator("#project-settings-dialog")).not_to_be_visible()
    expect(page.locator("#project-title")).to_have_text("Updated")
    row = store.one(
        "SELECT name,classification,default_language FROM projects WHERE id=?", (project["id"],)
    )
    assert row == {"name": "Updated", "classification": "protected", "default_language": "en"}
    page.locator("#project-settings-open").click()
    page.locator("#project-edit-name").fill("Stale")
    store.execute("UPDATE projects SET name=? WHERE id=?", ("Other window", project["id"]))
    page.locator("#project-settings-save").click()
    expect(page.locator("#project-edit-note")).to_contain_text("changed")
    expect(page.locator("#project-settings-save")).to_be_disabled()
    assert (
        store.one("SELECT name FROM projects WHERE id=?", (project["id"],))["name"]
        == "Other window"
    )


def test_lost_save_reply_disables_retry_and_reports_uncertainty(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Original")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#project-settings-open").click()
    page.locator("#project-edit-name").fill("Saved despite lost response")

    def lose_reply(route):
        route.fetch()
        route.abort()

    page.route(f"**/api/projects/{project['id']}", lose_reply)
    page.locator("#project-settings-save").click()
    expect(page.locator("#project-edit-note")).to_contain_text("may still finish")
    expect(page.locator("#project-settings-save")).to_be_disabled()
    assert (
        store.one("SELECT name FROM projects WHERE id=?", (project["id"],))["name"]
        == "Saved despite lost response"
    )
    page.locator("#project-settings-cancel").click()
    page.unroute(f"**/api/projects/{project['id']}", lose_reply)
    page.locator("#project-rename").click()
    expect(page.locator("#toast")).to_contain_text("may still finish")
    expect(page.locator("#name-dialog")).not_to_be_visible()
    page.locator("#project-settings-open").click()
    expect(page.locator("#project-edit-name")).to_have_value("Saved despite lost response")
    expect(page.locator("#project-save-unconfirmed")).to_be_visible()
    expect(page.locator("#project-settings-save")).to_be_disabled()
    page.reload()
    page.locator("#project-settings-open").click()
    expect(page.locator("#project-save-unconfirmed")).to_be_visible()
    expect(page.locator("#project-settings-save")).to_be_disabled()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#project-save-acknowledge").click()
    expect(page.locator("#project-settings-save")).to_be_enabled()


def test_storage_move_only_runs_after_save_and_confirmation(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Original")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#project-settings-open").click()
    target = local_app.root / "new-parent"
    page.locator("#project-edit-storage").fill(str(target))
    assert not target.exists()
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator("#project-settings-save").click()
    expect(page.locator("#project-edit-note")).to_contain_text("Move cancelled")
    assert not target.exists()
    page.locator("#project-settings-cancel").click()
    assert not target.exists()


def test_confirmed_storage_move_and_partial_failure(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Original")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#project-settings-open").click()
    target = local_app.root / "confirmed-parent"
    page.locator("#project-edit-storage").fill(str(target))
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#project-settings-save").click()
    expect(page.locator("#project-settings-dialog")).not_to_be_visible()
    assert store.project_folder(project["id"]).is_relative_to(target)

    page.locator("#project-settings-open").click()
    page.locator("#project-edit-name").fill("Confirmed name")
    page.locator("#project-edit-classification").select_option("protected")
    page.route(
        f"**/api/projects/{project['id']}/classification",
        lambda route: route.fulfill(status=503, json={"detail": "Synthetic failure"}),
    )
    page.locator("#project-settings-save").click()
    expect(page.locator("#project-edit-note")).to_contain_text("Confirmed saved: name")
    expect(page.locator("#project-edit-note")).to_contain_text("may still finish")
    expect(page.locator("#project-settings-save")).to_be_disabled()
    row = store.one("SELECT name,classification FROM projects WHERE id=?", (project["id"],))
    assert row == {"name": "Confirmed name", "classification": "standard"}


def test_failed_reconciliation_blocks_reopen_until_refresh_succeeds(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Original")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#project-settings-open").click()
    page.locator("#project-edit-name").fill("Saved name")

    def lose_reply(route):
        route.fetch()
        route.abort()

    def fail_refresh(route):
        route.fulfill(status=503, json={"detail": "Synthetic refresh failure"})

    page.route(f"**/api/projects/{project['id']}", lose_reply)
    page.route("**/api/projects", fail_refresh)
    page.locator("#project-settings-save").click()
    expect(page.locator("#project-settings-save")).to_be_disabled()
    page.locator("#project-settings-cancel").click()
    page.locator("#project-settings-open").click()
    expect(page.locator("#toast")).to_contain_text("Synthetic refresh failure")
    expect(page.locator("#project-settings-dialog")).not_to_be_visible()
    page.unroute("**/api/projects", fail_refresh)
    page.unroute(f"**/api/projects/{project['id']}", lose_reply)
    page.locator("#project-settings-open").click()
    expect(page.locator("#project-edit-name")).to_have_value("Saved name")
    expect(page.locator("#project-save-unconfirmed")).to_be_visible()
    expect(page.locator("#project-settings-save")).to_be_disabled()


def test_inflight_save_marks_uncertain_before_dispatch_and_reload(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Original")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#project-settings-open").click()
    page.locator("#project-edit-name").fill("Delayed")
    pending = []

    def hold(route):
        pending.append(route)
        page.evaluate("window.projectWriteHeld = true")

    page.route(f"**/api/projects/{project['id']}", hold)
    page.locator("#project-settings-save").click()
    page.wait_for_function("() => window.projectWriteHeld === true")
    assert page.evaluate("id => projectSaveMarkers(id).length", project["id"]) == 1
    page.reload()
    page.locator("#project-settings-open").click()
    expect(page.locator("#project-save-unconfirmed")).to_be_visible()
    expect(page.locator("#project-settings-save")).to_be_disabled()


def test_concurrent_settings_writes_keep_independent_warning_markers(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Original")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#project-settings-open").click()
    page.evaluate("""() => {
      window.heldProjectResponses = [];
      window.fetch = () => new Promise(resolve => window.heldProjectResponses.push(resolve));
      window.firstProjectWrite = projectSettingsWrite(projectForm, '/synthetic', {method: 'PATCH'});
      window.secondProjectWrite = projectSettingsWrite(
        projectForm, '/synthetic', {method: 'PATCH'});
    }""")
    assert page.evaluate("() => projectSaveMarkers(projectForm.id).length") == 2
    page.evaluate("""async () => {
      heldProjectResponses[0](new Response('{}', {status: 200}));
      await firstProjectWrite;
    }""")
    assert page.evaluate("() => projectSaveMarkers(projectForm.id).length") == 1
    page.evaluate("""async () => {
      heldProjectResponses[1](new Response('{}', {status: 200}));
      await secondProjectWrite;
    }""")
    assert page.evaluate("() => projectSaveMarkers(projectForm.id).length") == 0
