"""Project deletion is out of the header and needs the exact project name."""

import time

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import open_project_settings
from test_browser import page as page

pytestmark = pytest.mark.browser


def deleted(store, project_id=None, timeout=10):
    """The dialog hands off to the server at once (ADR 0190); deletion finishes there."""
    query = "SELECT id FROM projects" + (" WHERE id=?" if project_id else "")
    deadline = time.monotonic() + timeout
    while store.all(query, (project_id,) if project_id else ()):
        assert time.monotonic() < deadline, "The server-owned deletion did not finish"
        time.sleep(0.05)


def test_project_settings_rows_and_name_confirmed_deletion(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Interview study")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    # Settings and deletion live in the sidebar's Project Settings; the page stays clean.
    expect(page.locator(".sidebar-bottom #project-settings-open")).to_be_visible()
    expect(page.locator("#project-edit-classification")).to_be_hidden()
    expect(page.locator(".library-toolbar #add-files")).to_be_visible()
    open_project_settings(page)
    expect(page.locator("#project-settings-title")).to_have_text(
        "Project settings · Interview study"
    )
    classification = page.locator("#project-edit-classification").bounding_box()
    folder = page.locator("#project-edit-storage").bounding_box()
    assert folder["y"] > classification["y"] + classification["height"] - 1
    page.locator("#project-delete").click()
    expect(page.locator("#delete-project-dialog")).to_be_visible()
    expect(page.locator("#delete-project-name")).to_have_text("Interview study")
    submit = page.locator("#delete-project-submit")
    expect(submit).to_be_disabled()
    page.locator("#delete-project-confirm").fill("Interview")
    expect(submit).to_be_disabled()
    expect(page.locator("#project-settings-dialog")).to_be_hidden()
    page.locator("#delete-project-dialog").get_by_role("button", name="Cancel").click()
    assert store.all("SELECT id FROM projects WHERE id=?", (project["id"],))
    open_project_settings(page)
    page.locator("#project-delete").click()
    expect(page.locator("#delete-project-confirm")).to_have_value("")
    page.locator("#delete-project-confirm").fill("Interview study")
    expect(submit).to_be_enabled()
    page.screenshot(path="test-results/project-delete-dialog.png")
    submit.click()
    expect(page.locator("#delete-project-dialog")).to_be_hidden()
    deleted(store, project["id"])
    expect(page).to_have_url(local_app.url + "/#")
    # With no project left, home shows the empty-library CBS brand (replaced the old hero text).
    expect(
        page.locator("#welcome").get_by_role("heading", name="Transcriber", exact=True)
    ).to_be_visible()


def delete_open_project(page, name):
    open_project_settings(page)
    page.locator("#project-delete").click()
    page.locator("#delete-project-confirm").fill(name)
    page.locator("#delete-project-submit").click()
    expect(page.locator("#delete-project-dialog")).to_be_hidden()


@pytest.mark.parametrize("address", ["", "/#", "/#/"])
def test_project_shown_on_the_home_page_can_be_deleted(page, local_app, address):
    """B460: after one deletion the home page shows the next project without its address."""
    store = local_app.server.config.app.state.store
    first = store.create_project("First study")
    second = store.create_project("Second study")
    page.goto(local_app.url + f"/#/projects/{first['id']}")
    delete_open_project(page, "First study")
    expect(page.locator("#project-title")).to_have_text("Second study")
    page.goto(local_app.url + address)
    expect(page.locator("#project-title")).to_have_text("Second study")
    assert f"#/projects/{second['id']}" not in page.url
    delete_open_project(page, "Second study")
    deleted(store)
    expect(page.locator("#project-title")).to_have_text("")
    expect(
        page.locator("#welcome").get_by_role("heading", name="Transcriber", exact=True)
    ).to_be_visible()
