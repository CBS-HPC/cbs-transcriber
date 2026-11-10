"""Retained local cleanup remains actionable after logical deletion."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_activity_remove import route_activity

pytestmark = pytest.mark.browser
OPERATION = "a" * 32


def cleanup_card(identifier=OPERATION, *, committed=True):
    summary = {
        "id": identifier,
        "state": "pending" if committed else "recovery_pending",
        "committed": committed,
        "target_kind": "project",
        "target_name": "Deleted synthetic project",
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
        "message": "Deleted from the library; local file cleanup is pending.",
    }
    return {
        "id": "local-deletion-" + identifier,
        "kind": "local-delete",
        "state": "completed" if committed else "interrupted",
        "recording_id": None,
        "recording_name": summary["target_name"],
        "message": summary["message"],
        "progress": 1 if committed else 0,
        "created": summary["created"],
        "updated": summary["updated"],
        "local_deletion": summary,
    }


def test_cleanup_card_retains_only_exact_coalesced_retry_until_success(page, local_app):
    job = cleanup_card()
    jobs = [job]
    # A committed cleanup obligation remains visible despite an earlier Clear finished.
    removals = route_activity(page, jobs, cleared_at="2026-01-02T00:00:00Z")
    held = []
    page.route("**/api/local-deletions/*/retry", lambda route: held.append(route))
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    retained = page.locator("#jobs [data-retained-activity]")
    expect(retained.locator("summary")).to_have_text("1 earlier job needs attention")
    retained.locator("summary").click()
    card = page.locator(f'#jobs .job[data-local-cleanup="{OPERATION}"]')
    expect(card).to_be_visible()
    expect(card).to_contain_text("local file cleanup is pending")
    expect(
        card.locator("[data-activity-stop], [data-cancel], [data-activity-remove]")
    ).to_have_count(0)
    expect(card.get_by_role("button", name="Remove from Activity")).to_have_count(0)
    retry = card.get_by_role("button", name="Retry local cleanup", exact=True)
    expect(card.get_by_role("button")).to_have_count(1)
    retry.click()
    expect(retry).to_be_disabled()
    page.evaluate("async () => await pollJobs()")
    expect(retry).to_be_disabled()
    # A repeated delegated event also cannot bypass the pending-request guard.
    retry.dispatch_event("click")
    retry.dispatch_event("click")
    assert len(held) == 1
    assert held[0].request.method == "POST"
    assert held[0].request.url == f"{local_app.url}/api/local-deletions/{OPERATION}/retry"
    assert removals == []
    jobs.clear()
    held[0].fulfill(
        json={"ok": True, "local_cleanup": {**job["local_deletion"], "state": "complete"}}
    )
    expect(card).to_have_count(0)
    expect(page.locator("#toast")).to_have_text("Local file cleanup complete.")
    assert len(held) == 1


def test_unverifiable_cleanup_identity_offers_no_action(page, local_app):
    job = cleanup_card("../../unverified", committed=None)
    removals = route_activity(page, [job])
    requests = []
    page.route(
        "**/api/local-deletions/**",
        lambda route: (requests.append(route.request.url), route.fulfill(json={})),
    )
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    card = page.locator("#jobs .job")
    expect(card).to_have_count(1)
    expect(card).to_be_visible()
    expect(card.get_by_role("button")).to_have_count(0)
    expect(
        card.locator("[data-local-delete], [data-activity-stop], [data-activity-remove]")
    ).to_have_count(0)
    assert requests == removals == []


def test_successful_delete_with_pending_cleanup_opens_activity_and_keeps_warning(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Delete with cleanup warning")
    recording = store.create_recording(project["id"], "Synthetic.wav")
    job = cleanup_card()
    jobs, calls = [], []
    route_activity(page, jobs)

    def deletion(route):
        if route.request.method != "DELETE":
            route.continue_()
            return
        calls.append(route.request.url)
        store.execute("DELETE FROM recordings WHERE id=?", (recording["id"],))
        jobs.append(job)
        route.fulfill(json={"ok": True, "local_cleanup": job["local_deletion"]})

    page.route(f"**/api/recordings/{recording['id']}", deletion)
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    expect(page.locator("#activity")).to_be_hidden()
    page.locator(f'[data-delete="{recording["id"]}"]').click()
    expect(page.locator("#activity")).to_be_visible()
    card = page.locator(f'#jobs .job[data-local-cleanup="{OPERATION}"]')
    expect(card).to_be_visible()
    expect(card).to_contain_text(job["message"])
    expect(card.get_by_role("button", name="Retry local cleanup", exact=True)).to_be_visible()
    expect(page.locator(f'article[data-recording="{recording["id"]}"]')).to_have_count(0)
    assert calls == [f"{local_app.url}/api/recordings/{recording['id']}"]
