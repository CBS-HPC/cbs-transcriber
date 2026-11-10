"""Deletion continuations keep pending views and do not act on a newer route."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser
OPERATION = "a" * 32


def setup_recordings(local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Delete test")
    first = store.create_recording(project["id"], "First.wav")
    second = store.create_recording(project["id"], "Second.wav")
    return store, project, first, second


def test_card_waits_for_worker_drain_and_keeps_single_continuation(page, local_app):
    _, project, first, _ = setup_recordings(local_app)
    calls, held = [], []

    def deletion(route):
        calls.append(route.request.url)
        if len(calls) == 1:
            route.fulfill(
                status=202,
                json={
                    "ok": False,
                    "status": "cancelling",
                    "operation_id": OPERATION,
                    "message": "Waiting for the media worker to stop.",
                },
            )
        else:
            held.append(route)
            page.evaluate("window.deleteContinuationHeld = true")

    page.route(
        f"**/api/recordings/{first['id']}*",
        lambda route: deletion(route) if route.request.method == "DELETE" else route.continue_(),
    )
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    card = page.locator(f'article[data-recording="{first["id"]}"]')
    card.locator("[data-delete]").click()
    expect(card.get_by_role("status")).to_have_text("Waiting for the media worker to stop.")
    expect(card.locator("[data-delete]")).to_be_disabled()
    page.wait_for_function("() => pendingDeletions.size === 1")
    page.wait_for_function("() => window.deleteContinuationHeld === true")
    assert len(calls) == 2
    assert f"operation_id={OPERATION}" in calls[1]
    expect(card).to_be_visible()
    held.pop().fulfill(status=409, json={"detail": "Newer work started; recordings were kept."})
    expect(page.locator("#toast")).to_have_text("Newer work started; recordings were kept.")
    expect(card.locator("[data-delete]")).to_be_enabled()


def test_shared_batch_requires_confirmation_before_second_delete(page, local_app):
    _, project, first, second = setup_recordings(local_app)
    calls = []

    def deletion(route):
        calls.append(route.request.url)
        route.fulfill(
            status=409,
            json={
                "detail": {
                    "code": "confirm_shared_cancellation",
                    "operation_id": OPERATION,
                    "message": "Stopping this shared UCloud allocation cancels other recordings.",
                    "affected_recordings": [{"id": second["id"], "name": "Second"}],
                }
            },
        )

    page.route(f"**/api/recordings/{first['id']}", deletion)
    dialogs = []

    def confirm(dialog):
        dialogs.append(dialog.message)
        dialog.accept() if len(dialogs) == 1 else dialog.dismiss()

    page.on("dialog", confirm)
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator(f'[data-delete="{first["id"]}"]').click()
    page.wait_for_function("() => pendingDeletions.size === 0")
    assert len(calls) == 1
    assert len(dialogs) == 2
    assert "Second" in dialogs[1]
    expect(page.locator(f'article[data-recording="{first["id"]}"]')).to_be_visible()


def test_file_deletion_error_is_inside_open_dialog(page, local_app):
    _, _, first, _ = setup_recordings(local_app)

    def deletion(route):
        if route.request.method == "DELETE":
            route.fulfill(status=409, json={"detail": "Remote output needs recovery first."})
        else:
            route.continue_()

    page.route(f"**/api/recordings/{first['id']}", deletion)
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(local_app.url + f"/#/recordings/{first['id']}")
    page.locator("#file-settings-open").click()
    page.locator("#file-delete").click()
    expect(page.locator("#file-delete-error")).to_have_text("Remote output needs recovery first.")
    expect(page.locator("#file-delete-error")).to_be_visible()
    expect(page.locator("#file-settings-dialog")).to_be_visible()
    expect(page.locator("#file-delete")).to_be_enabled()


def test_late_delete_reply_does_not_navigate_away_from_new_recording(page, local_app):
    _, _, first, second = setup_recordings(local_app)
    held = []

    def deletion(route):
        if route.request.method == "DELETE":
            held.append(route)
            page.evaluate("window.deleteReplyHeld = true")
        else:
            route.continue_()

    page.route(f"**/api/recordings/{first['id']}", deletion)
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(local_app.url + f"/#/recordings/{first['id']}")
    page.locator("#file-settings-open").click()
    page.locator("#file-delete").click()
    page.wait_for_function("() => window.deleteReplyHeld === true")
    page.locator("#file-settings-dialog [data-close]").click()
    page.evaluate("id => { location.hash = `/recordings/${id}`; }", second["id"])
    expect(page.locator("#recording-title")).to_have_text("Second")
    held.pop().fulfill(status=200, json={"ok": True})
    page.wait_for_function("() => pendingDeletions.size === 0")
    expect(page).to_have_url(local_app.url + f"/#/recordings/{second['id']}")
    expect(page.locator("#recording-title")).to_have_text("Second")


@pytest.mark.parametrize("project_refresh", [False, True])
def test_project_filesystem_failure_clears_progress_and_allows_retry(
    page, local_app, project_refresh
):
    _, project, first, _ = setup_recordings(local_app)
    held, calls = [], []
    message = "Windows could not remove the recording folder. Files and project were kept."

    def deletion(route):
        calls.append(route.request.url)
        if len(calls) == 1:
            route.fulfill(
                status=202,
                json={
                    "ok": False,
                    "status": "cancelling",
                    "operation_id": OPERATION,
                    "message": "Stopping jobs before deletion…",
                },
            )
        else:
            held.append(route)
            page.evaluate("window.projectDeleteReplyHeld = true")
            page.evaluate("count => { window.projectDeleteRequestCount = count; }", len(calls))

    page.route(
        f"**/api/projects/{project['id']}*",
        lambda route: deletion(route) if route.request.method == "DELETE" else route.continue_(),
    )
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#project-settings-open").click()
    page.locator("#project-delete").click()
    page.locator("#delete-project-confirm").fill(project["name"])
    page.locator("#delete-project-submit").click()
    page.wait_for_function("() => window.projectDeleteReplyHeld === true")
    expect(page.locator("#delete-project-progress")).to_have_text("Stopping jobs before deletion…")
    if project_refresh:
        # Polling can replace the project projection while this same dialog owns its request.
        page.evaluate("state.project = null")
    held.pop().fulfill(status=409, json={"detail": message})
    page.wait_for_function("() => pendingDeletions.size === 0")
    expect(page.locator("#delete-project-dialog")).to_be_visible()
    expect(page.locator("#delete-project-error")).to_have_text(message)
    expect(page.locator("#delete-project-error")).to_be_visible()
    expect(page.locator("#delete-project-progress")).to_be_hidden()
    expect(page.locator("#delete-project-submit")).to_be_enabled()
    expect(page).to_have_url(local_app.url + f"/#/projects/{project['id']}")
    expect(page.locator(f'article[data-recording="{first["id"]}"]')).to_be_visible()
    page.locator("#delete-project-submit").click()
    page.wait_for_function("() => window.projectDeleteRequestCount === 3")
    expect(page.locator("#delete-project-error")).to_be_hidden()
    expect(page.locator("#delete-project-progress")).to_be_visible()
    held.pop().fulfill(status=409, json={"detail": message})
    page.wait_for_function("() => pendingDeletions.size === 0")
    expect(page.locator("#delete-project-error")).to_have_text(message)
    expect(page.locator("#delete-project-progress")).to_be_hidden()
    expect(page.locator("#delete-project-submit")).to_be_enabled()


def test_reopened_project_delete_dialog_ignores_old_failure(page, local_app):
    _, project, _, _ = setup_recordings(local_app)
    held = []

    def deletion(route):
        if route.request.method == "DELETE":
            held.append(route)
            page.evaluate("window.oldProjectDeleteHeld = true")
        else:
            route.continue_()

    page.route(f"**/api/projects/{project['id']}*", deletion)
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#project-settings-open").click()
    page.locator("#project-delete").click()
    page.locator("#delete-project-confirm").fill(project["name"])
    page.locator("#delete-project-submit").click()
    page.wait_for_function("() => window.oldProjectDeleteHeld === true")
    expect(page.locator("#delete-project-progress")).to_be_visible()
    page.locator('#delete-project-dialog [data-close="delete-project-dialog"]').first.click()
    page.locator("#project-settings-open").click()
    page.locator("#project-delete").click()
    page.locator("#delete-project-confirm").fill(project["name"])
    expect(page.locator("#delete-project-progress")).to_be_hidden()
    held.pop().fulfill(status=409, json={"detail": "An earlier folder removal was refused."})
    page.wait_for_function("() => pendingDeletions.size === 0")
    expect(page.locator("#delete-project-dialog")).to_be_visible()
    expect(page.locator("#delete-project-error")).to_be_hidden()
    expect(page.locator("#delete-project-progress")).to_be_hidden()
    expect(page.locator("#delete-project-confirm")).to_have_value(project["name"])
    expect(page.locator("#delete-project-submit")).to_be_enabled()
