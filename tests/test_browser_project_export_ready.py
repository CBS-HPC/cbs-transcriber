"""Project Export Settings waits for the project's own recording list (B467)."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser


def test_project_export_waits_for_the_project_recording_list(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Export list")
    first = store.create_recording(project["id"], "first.wav")
    store.create_recording(project["id"], "second.wav")
    page.goto(local_app.url + f"/#/recordings/{first['id']}")
    expect(page.locator("#back-library")).to_be_visible()
    held = []
    page.route(f"**/api/projects/{project['id']}/recordings", lambda route: held.append(route))
    page.locator("#back-library").click()
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    assert held, "the project's recording list was not requested"
    # Until the list arrives, export cannot open with a partial or another project's list.
    expect(page.locator("#project-export-open")).to_be_disabled()
    held[0].continue_()
    page.unroute(f"**/api/projects/{project['id']}/recordings")
    expect(page.locator("#project-export-open")).to_be_enabled()
    page.locator("#project-export-open").click()
    expect(page.locator("#export-recording-list [data-export-recording]")).to_have_count(2)
