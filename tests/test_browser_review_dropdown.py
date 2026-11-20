"""Derived review labels are display-only; exact-content researcher marks win."""

import re

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_passage_review import save_status, status
from test_browser_timing_review import timed

from cbs_transcriber import passage_review
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


@pytest.fixture
def suggested(local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic dropdown states")
    rid = store.create_recording(project["id"], "states.wav")["id"]
    rows = []
    for index in range(7):
        row = timed(f"Passage {index} words.", index * 3, index * 3 + 2)
        if index in {1, 2}:
            row.pop("words")
        if index in {0, 1, 3, 4, 5}:
            row.update(avg_logprob=-2, no_speech_prob=0.9)
        rows.append(row)
    result = store.add_result(
        rid,
        normalize_segments(rows),
        {},
        "Synthetic words",
        {"capability": "words"},
        auto_first=True,
    )["id"]
    for sid, desired in [("3", "checked"), ("4", "not_checked"), ("5", "needs_review")]:
        save_status(store, rid, result, sid, desired)
    return local_app, store, rid, result


def test_derived_labels_priorities_manual_overrides_and_no_implicit_writes(page, suggested):
    app, store, rid, result = suggested
    before = passage_review.read(store, rid, result)
    writes = []
    page.on(
        "request",
        lambda request: (
            writes.append(request.url)
            if request.method in {"POST", "PATCH", "PUT", "DELETE"}
            else None
        ),
    )
    page.goto(app.url + f"/#/recordings/{rid}")
    for number, value in enumerate(
        [
            "uncertain",
            "uncertain",
            "missing_timing",
            "checked",
            "not_checked",
            "needs_review",
            "not_checked",
        ],
        1,
    ):
        expect(status(page, number)).to_have_value(value)
    expect(page.locator("#transcript .review-flag")).to_have_count(0)
    expect(status(page, 1)).to_have_class(re.compile("review-status-automatic"))
    expect(status(page, 1)).to_have_attribute(
        "title", re.compile("Set automatically.*not a measure of correctness")
    )
    expect(status(page, 1).locator('option[value="uncertain"]')).to_be_disabled()
    expect(status(page, 3).locator('option[value="missing_timing"]')).to_be_disabled()
    # The same native control remains keyboard reachable with all three manual choices.
    status(page, 1).focus()
    expect(status(page, 1)).to_be_focused()
    assert status(page, 1).locator("option:not([disabled])").evaluate_all(
        "nodes => nodes.map(n => n.value)"
    ) == ["not_checked", "checked", "needs_review"]
    colour = status(page, 1).evaluate(
        "n => ({fg: getComputedStyle(n).color, bg: getComputedStyle(n).backgroundColor})"
    )
    assert colour == {"fg": "rgb(122, 75, 0)", "bg": "rgb(255, 241, 214)"}
    assert passage_review.read(store, rid, result) == before
    assert writes == []
    # Turning hints off falls back to timing/manual status; turning back on restores
    # the view-only label without changing exact-content manual overrides.
    page.locator("#show-uncertainty").uncheck()
    expect(status(page, 1)).to_have_value("not_checked")
    expect(status(page, 2)).to_have_value("missing_timing")
    page.locator("#show-uncertainty").check()
    expect(status(page, 2)).to_have_value("uncertain")
    expect(status(page, 5)).to_have_value("not_checked")
    assert passage_review.read(store, rid, result) == before
    assert writes == []


def test_explicit_not_checked_dismisses_uncertain_on_the_same_content(page, suggested):
    app, store, rid, result = suggested
    page.goto(app.url + f"/#/recordings/{rid}")
    expect(status(page, 1)).to_have_value("uncertain")
    status(page, 1).focus()
    status(page, 1).press("Home")
    status(page, 1).press("Enter")
    expect(status(page, 1)).to_have_value("not_checked")
    expect(status(page, 1)).not_to_have_class(re.compile("review-status-automatic"))
    row = next(
        row
        for row in passage_review.read(store, rid, result)["passages"]
        if row["segment_id"] == "0"
    )
    assert (row["status"], row["automatic"], row["review_revision"], row["stale"]) == (
        "not_checked",
        None,
        1,
        False,
    )
    page.reload()
    expect(status(page, 1)).to_have_value("not_checked")
    expect(status(page, 2)).to_have_value("uncertain")
    # The Uncertain filter still shows model signals even where a manual mark wins.
    show = page.get_by_role("combobox", name="Show passages", exact=True)
    show.select_option("uncertain")
    expect(page.locator("#transcript > .passage:not([hidden])")).to_have_count(5)
    expect(status(page, 1)).to_have_value("not_checked")
    next_button = page.get_by_role("button", name="Next to review", exact=True)
    for sid in ["0", "1", "4", "5", "0"]:
        next_button.click()
        expect(page.locator("#transcript .review-focus")).to_have_attribute("data-segment", sid)


def test_explicit_needs_review_replaces_the_automatic_timing_reason(page, suggested):
    app, store, rid, result = suggested
    page.goto(app.url + f"/#/recordings/{rid}")
    expect(status(page, 3)).to_have_value("missing_timing")
    status(page, 3).select_option("needs_review")
    expect(status(page, 3)).to_have_value("needs_review")
    expect(page.locator("#passage-2 [data-review-reason]")).to_have_count(0)
    row = next(
        row
        for row in passage_review.read(store, rid, result)["passages"]
        if row["segment_id"] == "2"
    )
    assert (row["status"], row["automatic"], row["review_revision"]) == ("needs_review", None, 1)


def test_stale_checked_with_preserved_complete_timing_stays_needs_review(page, suggested):
    app, store, rid, result = suggested
    page.goto(app.url + f"/#/recordings/{rid}")
    expect(status(page, 4)).to_have_value("checked")
    page.locator("#passage-3 [data-action=edit]").click()
    page.locator("#passage-3 textarea").fill("Passage 3 corrected.")
    page.locator("#passage-3 [data-action=save-edit]").click()
    expect(status(page, 4)).to_have_value("needs_review")
    row = next(
        row
        for row in passage_review.read(store, rid, result)["passages"]
        if row["segment_id"] == "3"
    )
    assert (row["status"], row["automatic"], row["stale"]) == ("needs_review", None, True)
    assert store.recording(rid)["segments"][3]["word_timing"] is True
