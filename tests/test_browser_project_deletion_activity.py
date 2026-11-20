"""Project deletion hands off to Activity; only that project is inactive (ADR 0190)."""

import threading
import time

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import open_project_settings
from test_browser import page as page

from cbs_transcriber import project_deletion_tasks

pytestmark = pytest.mark.browser
TaskOwner = project_deletion_tasks.ProjectDeletionTasks


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("The synthetic server did not reach the expected state")


@pytest.fixture
def hold_stop(monkeypatch):
    """Hold the server's executor as it enters "Stopping jobs", before any Stop."""
    entered, release = threading.Event(), threading.Event()
    original = TaskOwner._phase

    def phase(self, task, value):
        original(self, task, value)
        if value == "stopping":
            entered.set()
            assert release.wait(20), "The test must release the synthetic gate"

    monkeypatch.setattr(TaskOwner, "_phase", phase)
    yield entered, release
    release.set()


def projects(local_app):
    store = local_app.server.config.app.state.store
    deleting = store.create_project("Deleting study")
    other = store.create_project("Other study")
    recording = store.create_recording(deleting["id"], "Interview.wav")
    job = store.add_job(recording["id"], "transcribe", {"provider_name": "Synthetic"})
    return store, deleting, other, recording, job


def delete_from_dialog(page, name):
    open_project_settings(page)
    page.locator("#project-delete").click()
    page.locator("#delete-project-confirm").fill(name)
    page.locator("#delete-project-submit").click()


def project_gone(store, project_id):
    return not store.all("SELECT id FROM projects WHERE id=?", (project_id,))


def test_delete_closes_dialog_at_once_and_activity_owns_the_stages(page, local_app, hold_stop):
    entered, release = hold_stop
    store, deleting, other, recording, job = projects(local_app)
    page.goto(local_app.url + f"/#/projects/{deleting['id']}")
    delete_from_dialog(page, "Deleting study")
    # The dialog closes while the server is still before its first Stop.
    expect(page.locator("#delete-project-dialog")).to_be_hidden()
    assert entered.wait(10)
    expect(page.locator("#activity")).to_be_visible()
    card = page.locator("#jobs .job[data-project-deletion]")
    expect(card).to_have_count(1)
    expect(card.locator("h3")).to_have_text("Deleting project Deleting study")
    expect(card.locator('[data-deletion-stage="stopping"]')).to_have_attribute(
        "data-stage-status", "current"
    )
    expect(card.locator('[data-deletion-stage="removing_library"]')).to_have_text(
        "Removing from the library"
    )
    expect(card.locator('[data-deletion-stage="removing_files"]')).to_have_text("Removing files")
    expect(card.get_by_role("button", name="Keep project")).to_be_visible()
    # Only that project is inactive: greyed, tagged and not a link; home shows the other.
    inactive = page.locator("#projects .project-link.deleting")
    expect(inactive).to_contain_text("Deleting study")
    expect(inactive).to_contain_text("Deleting…")
    expect(page.locator(f'#projects a[href="#/projects/{deleting["id"]}"]')).to_have_count(0)
    expect(page.locator(f'#projects a[href="#/projects/{other["id"]}"]')).to_be_visible()
    expect(page.locator("#project-title")).to_have_text("Other study")
    page.screenshot(path="test-results/project-deletion-activity.png")
    # Its address and its recordings do not open; the rest of the app stays usable.
    page.goto(local_app.url + f"/#/projects/{deleting['id']}")
    expect(page.locator("#project-title")).to_have_text("Other study")
    expect(page.locator("#toast")).to_contain_text("being deleted")
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    expect(page.locator("#project-title")).to_have_text("Other study")
    expect(page.locator("#editor-view")).to_be_hidden()
    release.set()
    wait_for(lambda: project_gone(store, deleting["id"]))
    if page.locator("#activity").is_hidden():
        page.locator("#activity-open").click()
    expect(card.locator("p").first).to_have_text("Project deleted.")
    expect(card.get_by_role("button", name="Keep project")).to_have_count(0)
    expect(card.get_by_role("button", name="Dismiss")).to_be_visible()
    expect(page.locator("#projects .project-link.deleting")).to_have_count(0)
    expect(page.locator("#projects")).not_to_contain_text("Deleting study")
    card.get_by_role("button", name="Dismiss").click()
    expect(page.locator("#jobs .job[data-project-deletion]")).to_have_count(0)


def test_keep_project_keeps_it_and_reactivates_only_that_project(page, local_app, hold_stop):
    entered, release = hold_stop
    store, deleting, other, recording, job = projects(local_app)
    page.goto(local_app.url + f"/#/projects/{deleting['id']}")
    delete_from_dialog(page, "Deleting study")
    assert entered.wait(10)
    card = page.locator("#jobs .job[data-project-deletion]")
    keep = card.get_by_role("button", name="Keep project")
    keep.click()
    expect(card).to_contain_text("Keeping the project…")
    release.set()
    expect(card).to_contain_text("Deletion stopped. The project was kept")
    expect(card.locator(".badge")).to_have_text("kept")
    expect(card.get_by_role("button", name="Keep project")).to_have_count(0)
    link = page.locator(f'#projects a[href="#/projects/{deleting["id"]}"]')
    expect(link).to_be_visible()
    assert not project_gone(store, deleting["id"])
    # Keep came before the first Stop, so the deletion cancelled nothing.
    assert store.job(job["id"])["state"] != "cancelled"
    link.click()
    expect(page.locator("#project-title")).to_have_text("Deleting study")
    expect(page.locator(f'article[data-recording="{recording["id"]}"]')).to_be_visible()


def test_researcher_inside_the_project_lands_on_the_project_list(page, local_app, hold_stop):
    entered, release = hold_stop
    store, deleting, other, recording, _ = projects(local_app)
    page.goto(local_app.url + f"/#/projects/{deleting['id']}")
    expect(page.locator("#project-title")).to_have_text("Deleting study")
    # Another tab confirms the deletion; this page notices on its ordinary poll.
    owner = local_app.server.config.app.state.project_deletions
    deletions = local_app.server.config.app.state.deletions
    summary = deletions.preview("project", deleting["id"])
    owner.start(deleting["id"], operation_id=summary["operation_id"])
    assert entered.wait(10)
    expect(page.locator("#project-title")).to_have_text("Other study", timeout=10000)
    assert f"#/projects/{deleting['id']}" not in page.url
    expect(page.locator("#projects .project-link.deleting")).to_contain_text("Deleting study")
    release.set()
    wait_for(lambda: project_gone(store, deleting["id"]))


def test_dialog_refusal_stays_in_the_dialog_and_nothing_is_inactive(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Refused study")
    page.route(
        "**/api/projects/*/deletion-tasks",
        lambda route: route.fulfill(
            status=409, json={"detail": "This deletion changed or newer work started."}
        ),
    )
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    delete_from_dialog(page, "Refused study")
    expect(page.locator("#delete-project-error")).to_have_text(
        "This deletion changed or newer work started."
    )
    expect(page.locator("#delete-project-dialog")).to_be_visible()
    expect(page.locator("#projects .project-link.deleting")).to_have_count(0)
    assert not project_gone(store, project["id"])
