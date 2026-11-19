"""Branch view (ADR 0182): lanes, collapsed runs, branch points and existing actions."""

import time

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_change_undo import edit_text, open_recording

pytestmark = pytest.mark.browser


def derive(store, rid, label="Synthetic enhancement"):
    current = store.recording(rid)
    ours = current["active_transcript_id"]
    frozen = store.freeze_result(rid, ours, current["revision"])
    job = store.add_job(rid, "enhance", {"source_snapshot": frozen})
    return store.add_result(
        rid,
        current["segments"],
        current["speakers"],
        label,
        {"kind": "enhancement", "source_result_id": ours, "model": "synthetic-aligner"},
        job_id=job["id"],
    )


def open_branches(page):
    page.locator("#history-open").click()
    page.locator("#history-graph-tab").click()
    expect(page.locator("#history-graph")).to_be_visible()
    expect(page.locator("#history-versions")).to_be_hidden()
    return page.locator("#history-graph-svg")


def test_branch_view_lanes_runs_selection_and_open_for_review(page, local_app):
    store, rid, _ = open_recording(page, local_app, "Synthetic branches")
    for number in range(4):
        edit_text(page, 0, f"First passage, revision {number}")
    derived = derive(store, rid)
    page.reload()
    svg = open_branches(page)
    expect(svg.locator(".graph-lane")).to_have_count(2)
    expect(svg.locator(".graph-edge")).to_have_count(1)
    expect(svg.locator(".graph-working")).to_have_count(1)
    expect(svg).to_contain_text("Working")
    run = svg.locator('[data-graph-key^="run:"]')
    expect(run).to_have_count(1)
    expect(run).to_have_attribute(
        "aria-label",
        "3 versions, 2 to 4, in " + run.get_attribute("aria-label").split(", in ", 1)[1],
    )
    run.click()
    expect(svg.locator('[data-graph-key^="run:"]')).to_have_count(0)
    svg.locator('[aria-label^="Version 2 · Text edit"]').click()
    changes = page.locator("#history-graph-changes")
    expect(changes).to_contain_text("1 passage changed compared with version 1.")
    expect(changes.locator("ins")).to_contain_text(["passage, revision 0"])
    expect(page.locator("#history-graph-review")).to_be_hidden()
    svg.locator('[aria-label*="Synthetic enhancement"]').first.click()
    expect(changes).to_contain_text("Made from version 5 of")
    review = page.locator("#history-graph-review")
    expect(review).to_be_visible()
    expect(page.locator("#history-graph-compare")).to_be_visible()
    review.click()
    expect(page.locator("#history-dialog")).to_be_hidden()
    deadline = time.monotonic() + 10
    while store.recording(rid)["active_transcript_id"] != derived["id"]:
        assert time.monotonic() < deadline
        page.wait_for_timeout(100)
    # Back to History: the list tab is shown again on the next opening.
    page.locator("#history-open").click()
    expect(page.locator("#history-versions")).to_be_visible()
    expect(page.locator("#history-graph")).to_be_hidden()


def test_undo_nodes_keyboard_selection_and_mark_working(page, local_app):
    store, rid, _ = open_recording(page, local_app, "Synthetic branch undo")
    edit_text(page, 1, "Second passage, edited")
    page.locator("#undo-last").click()
    expect(page.locator("#passage-1 .passage-text")).to_have_text("Second passage")
    svg = open_branches(page)
    expect(svg.locator(".graph-revert")).to_have_count(1)
    undo = svg.locator('[aria-label^="Version 3 · Undo last change"]')
    undo.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#history-graph-changes")).to_contain_text("Undid version 2 of")
    first = svg.locator('[aria-label^="Version 1 ·"]')
    first.click()
    working = page.locator("#history-graph-working")
    expect(working).to_be_enabled()
    working.click()
    dialog = page.locator("#working-version-dialog")
    expect(dialog).to_be_visible()
    page.locator("#working-version-apply").click()
    expect(dialog).to_be_hidden()
    first_version = store.result_versions(rid, store.recording(rid)["active_transcript_id"])[-1]
    deadline = time.monotonic() + 10
    while store.recording(rid)["result_roles"]["working"]["version_id"] != first_version["id"]:
        assert time.monotonic() < deadline
        page.wait_for_timeout(100)
    expect(svg.locator(".graph-working")).to_have_count(1)
    expect(svg.locator('[aria-label^="Version 1 ·"]')).to_have_attribute(
        "aria-label", first.get_attribute("aria-label").removesuffix(" · Working") + " · Working"
    )
