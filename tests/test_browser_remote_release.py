"""Deletion impact consent and retained cleanup remain usable in the browser."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser
OPERATION = "a" * 32
FINGERPRINT = "b" * 64


@pytest.mark.parametrize("accept", [False, True])
def test_delete_lists_remote_folder_and_sends_exact_consent(page, local_app, accept):
    store = local_app.server.config.app.state.store
    project = store.create_project("Delete cleanup")
    recording = store.create_recording(project["id"], "Synthetic.wav")
    calls, dialogs = [], []

    def deletion(route):
        calls.append(route.request.url)
        if len(calls) == 1:
            route.fulfill(
                status=409,
                json={
                    "detail": {
                        "code": "confirm_remote_cleanup",
                        "operation_id": OPERATION,
                        "fingerprint": FINGERPRINT,
                        "affected_recordings": [],
                        "message": "Delete also moves these UCloud working folders to Trash.",
                        "remote_folders": [
                            {
                                "server": "https://cloud.invalid",
                                "folder": "/drive/cbs-owned",
                                "action": "trash",
                            }
                        ],
                    }
                },
            )
        elif len(calls) == 2:
            route.fulfill(
                status=202,
                json={
                    "ok": False,
                    "status": "releasing",
                    "operation_id": OPERATION,
                    "message": "Finishing remote cleanup…",
                },
            )
        else:
            route.fulfill(status=200, json={"ok": True})

    def confirm(dialog):
        dialogs.append(dialog.message)
        dialog.accept() if len(dialogs) == 1 or accept else dialog.dismiss()

    page.route(
        f"**/api/recordings/{recording['id']}*",
        lambda route: deletion(route) if route.request.method == "DELETE" else route.continue_(),
    )
    page.on("dialog", confirm)
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator(f'[data-delete="{recording["id"]}"]').click()
    page.wait_for_function("() => pendingDeletions.size === 0")
    assert len(dialogs) == 2
    assert "/drive/cbs-owned" in dialogs[1] and "https://cloud.invalid" in dialogs[1]
    assert len(calls) == (3 if accept else 1)
    if accept:
        assert all(f"fingerprint={FINGERPRINT}" in call for call in calls[1:])


def test_deleted_owner_activity_retries_cleanup_without_retrying_inference(page, local_app):
    calls = []
    job = {
        "id": "runtime-batch-" + OPERATION,
        "kind": "enhance",
        "recording_id": None,
        "recording_name": "UCloud cleanup for a deleted project",
        "state": "cancelled",
        "progress": 0,
        "created": "2026-10-30T10:00:00Z",
        "updated": "2026-10-30T10:00:00Z",
        "message": "Local project deleted",
        "ucloud_cleanup_state": "pending",
        "ucloud_cleanup_error": "UCloud is offline.",
        "remote_release": {
            "kind": "deletion",
            "owner_kind": "runtime_batch",
            "id": OPERATION,
            "can_retry": True,
            "cleanup_state": "pending",
        },
    }
    page.route("**/api/jobs", lambda route: route.fulfill(json=[job]))

    def retry(route):
        calls.append(route.request.url)
        job["remote_release"]["cleanup_state"] = job["ucloud_cleanup_state"] = "trashed"
        route.fulfill(json={"ok": True, "cleanup_state": "trashed"})

    page.route(f"**/api/remote-releases/runtime_batch/{OPERATION}/retry", retry)
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    card = page.locator("#jobs .job")
    expect(card).to_contain_text("UCloud is offline.")
    expect(card.get_by_role("button", name="Retry", exact=True)).to_have_count(0)
    expect(card.get_by_role("button", name="Remove from Activity")).to_have_count(0)
    card.get_by_role("button", name="Retry remote cleanup", exact=True).click()
    expect(card).to_contain_text("moved to UCloud Trash")
    assert len(calls) == 1
