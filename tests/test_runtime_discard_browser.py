"""Whole-batch discard requires a visible, fresh preview and a deliberate confirmation."""

import copy

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_runtime_batch_browser import owner, route_activity

pytestmark = pytest.mark.browser


def setup(page, local_app):
    activity = owner()
    activity.update(state="failed", ucloud_remote_state="FAILURE")
    activity["runtime_batch"].update(can_cancel=False, can_discard=True)
    activity["runtime_batch"]["members"][2]["recording_name"] = "<Literal & private>.wav"
    route_activity(page, activity)
    preview = {
        "runtime_batch_id": activity["runtime_batch_id"],
        "fingerprint": "a" * 64,
        "members": [
            {
                "job_id": member["job_id"],
                "recording_name": member["recording_name"],
                "state": member["state"],
                "result_saved": bool(member.get("result_id")),
                "partial_stages": 1 if index == 2 else 0,
            }
            for index, member in enumerate(activity["runtime_batch"]["members"])
        ],
    }
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    return activity, preview


def test_discard_preview_cancel_and_explicit_batch_confirmation(page, local_app):
    activity, preview = setup(page, local_app)
    calls = []

    def action(route):
        name = route.request.url.rsplit("/", 1)[-1]
        calls.append((name, route.request.post_data_json))
        if name == "discard-preview":
            route.fulfill(json=preview)
        else:
            activity["runtime_batch"]["can_discard"] = False
            route.fulfill(json={"cleanup_state": "trashed"})

    page.route("**/api/runtime-batches/*/*", action)
    launch = page.get_by_role("button", name="Discard retained run files", exact=True)
    launch.click()
    dialog = page.locator("#runtime-discard-dialog")
    expect(dialog).to_be_visible()
    expect(dialog.locator("li")).to_have_count(4)
    expect(dialog).to_contain_text("<Literal & private>.wav")
    expect(dialog.locator("li").nth(2)).to_contain_text(
        "1 stage file with a matching receipt preserved locally"
    )
    expect(dialog).to_contain_text("4 files in this Runtime batch; 2 without a saved result.")
    assert dialog.locator("script").count() == 0
    page.screenshot(path="test-results/runtime-discard-preview.png", full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    box = dialog.bounding_box()
    assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= 390
    page.screenshot(path="test-results/runtime-discard-preview-narrow.png", full_page=True)
    page.set_viewport_size({"width": 1440, "height": 1000})
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    assert [name for name, _ in calls] == ["discard-preview"]
    launch.click()
    dialog.get_by_role("button", name="Move run files to UCloud Trash", exact=True).click()
    expect(dialog).to_be_hidden()
    assert calls == [
        ("discard-preview", None),
        ("discard-preview", None),
        ("discard", {"fingerprint": "a" * 64, "confirmed": True}),
    ]


def test_failed_confirmation_keeps_details_and_requires_new_preview(page, local_app):
    _, preview = setup(page, local_app)
    calls = []

    def action(route):
        name = route.request.url.rsplit("/", 1)[-1]
        calls.append(name)
        if name == "discard-preview":
            route.fulfill(json=preview)
        else:
            route.fulfill(status=409, json={"detail": "This batch changed. Refresh its preview."})

    page.route("**/api/runtime-batches/*/*", action)
    page.get_by_role("button", name="Discard retained run files", exact=True).click()
    dialog = page.locator("#runtime-discard-dialog")
    submit = dialog.get_by_role("button", name="Move run files to UCloud Trash", exact=True)
    submit.click()
    expect(dialog.get_by_role("alert")).to_have_text("This batch changed. Refresh its preview.")
    expect(dialog.locator("li")).to_have_count(4)
    expect(submit).to_be_disabled()
    assert calls == ["discard-preview", "discard"]
    preview["fingerprint"] = "b" * 64
    dialog.get_by_role("button", name="Refresh preview", exact=True).click()
    expect(submit).to_be_enabled()
    assert calls == ["discard-preview", "discard", "discard-preview"]


def test_late_cancelled_preview_cannot_replace_another_confirmation(page, local_app):
    _, preview = setup(page, local_app)
    held, calls = [], []

    def action(route):
        calls.append(route.request.url.rsplit("/", 1)[-1])
        if not held:
            held.append(route)
        else:
            updated = copy.deepcopy(preview)
            updated["members"][0]["recording_name"] = "Current preview.wav"
            updated["fingerprint"] = "b" * 64
            route.fulfill(json=updated)

    page.route("**/api/runtime-batches/*/*", action)
    launch = page.get_by_role("button", name="Discard retained run files", exact=True)
    launch.click()
    dialog = page.locator("#runtime-discard-dialog")
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    launch.click()
    expect(dialog).to_contain_text("Current preview.wav")
    with page.expect_response("**/api/runtime-batches/*/discard-preview"):
        held[0].fulfill(json=preview)
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )
    expect(dialog).to_contain_text("Current preview.wav")
    assert calls == ["discard-preview", "discard-preview"]


def test_pending_trash_keeps_decision_and_unverified_files_are_labelled(page, local_app):
    _, preview = setup(page, local_app)
    preview["members"][2].update(partial_stages=0, unverified_stages=2)
    calls = []

    def action(route):
        name = route.request.url.rsplit("/", 1)[-1]
        calls.append(name)
        route.fulfill(json=preview if name == "discard-preview" else {"cleanup_state": "pending"})

    page.route("**/api/runtime-batches/*/*", action)
    page.get_by_role("button", name="Discard retained run files", exact=True).click()
    dialog = page.locator("#runtime-discard-dialog")
    row = dialog.locator("li").nth(2)
    expect(row).to_contain_text("2 unverified stage files preserved locally")
    expect(row).not_to_contain_text("matching receipts")
    expect(row).to_contain_text("recovery will be abandoned")
    submit = dialog.get_by_role("button", name="Move run files to UCloud Trash", exact=True)
    submit.click()
    expect(dialog).to_contain_text("Your discard decision is saved.")
    expect(dialog).to_contain_text("is still pending")
    expect(submit).to_be_disabled()
    expect(dialog.get_by_role("button", name="Cancel", exact=True)).to_be_enabled()
    assert calls == ["discard-preview", "discard"]
    preview["already_confirmed"] = True
    dialog.get_by_role("button", name="Refresh preview", exact=True).click()
    expect(dialog).to_contain_text("Retry confirmed move to UCloud Trash")
    expect(
        dialog.get_by_role("button", name="Retry move to UCloud Trash", exact=True)
    ).to_be_enabled()
    assert calls == ["discard-preview", "discard", "discard-preview"]
