"""Show filter for review states, model flags and missing timing; synthetic libraries only."""

import re

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_mvp_profile import mvp_app as mvp_app
from test_browser_passage_review import save_status, status

from cbs_transcriber import passage_review
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def timed(text, start, end, **extra):
    tokens = text.split(" ")
    step = (end - start) / len(tokens)
    return {
        "text": text,
        "start": start,
        "end": end,
        "words": [
            {"word": token, "start": start + i * step, "end": start + (i + 0.8) * step}
            for i, token in enumerate(tokens)
        ],
        **extra,
    }


@pytest.fixture
def filtered(local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic Show filter")
    rid = store.create_recording(project["id"], "filter.wav")["id"]
    result = store.add_result(
        rid,
        normalize_segments(
            [
                timed("Alpha checked words.", 0, 2, avg_logprob=-0.1),
                timed("Beta needs review words.", 2, 4, avg_logprob=-0.1),
                {"text": "Gamma untimed words.", "start": 4, "end": 6, "avg_logprob": -0.1},
                # Model signals suggest silence or invented text; review state stays manual.
                timed("Delta uncertain words.", 6, 8, avg_logprob=-2, no_speech_prob=0.9),
                {"text": "Epsilon unbounded words."},
            ]
        ),
        {},
        "Synthetic filter result",
        {},
        auto_first=True,
    )["id"]
    save_status(store, rid, result, "0", "checked")
    save_status(store, rid, result, "1", "needs_review")
    # This word-timing result would mark its untimed passages Needs review automatically
    # (ADR 0191, covered in test_browser_timing_review). Explicit Not checked marks on that
    # exact content win, so these filter checks keep their manual states.
    save_status(store, rid, result, "2", "not_checked")
    save_status(store, rid, result, "4", "not_checked")
    other = store.create_recording(project["id"], "other.wav")["id"]
    store.add_result(
        other,
        normalize_segments([{"text": "Another recording.", "start": 0, "end": 1}]),
        {},
        "Other",
        {},
        auto_first=True,
    )
    return local_app, store, rid, result, other


def show(page):
    return page.get_by_role("combobox", name="Show passages", exact=True)


def visible_ids(page):
    return page.locator("#transcript > .passage:not([hidden])").evaluate_all(
        "nodes => nodes.map(node => node.dataset.segment)"
    )


def expect_visible(page, ids):
    expect(page.locator("#transcript > .passage:not([hidden])")).to_have_count(len(ids))
    assert visible_ids(page) == ids


def open_filtered(page, fixture):
    app, _, rid, _, _ = fixture
    page.goto(app.url + f"/#/recordings/{rid}")
    expect(page.locator("[data-review-summary]")).to_have_text(
        "Manual review · Checked 1 of 5 · Needs review 1"
    )
    expect(page.locator("#passage-3 .review-flag")).to_have_count(1)
    expect(show(page)).to_be_visible()
    expect(show(page)).to_have_value("all")


def option_texts(page):
    return show(page).locator("option").evaluate_all("nodes => nodes.map(node => node.text)")


def test_each_show_option_filters_with_counts_without_changing_marks(page, filtered):
    _, store, rid, result, _ = filtered
    before = passage_review.read(store, rid, result)
    writes = []
    page.on(
        "request",
        lambda request: (
            writes.append(request.url) if request.method in {"POST", "PATCH", "PUT"} else None
        ),
    )
    open_filtered(page, filtered)
    expect(show(page).locator('option[value="missing_timing"]')).to_have_text(
        "Missing word timing (2)"
    )
    assert option_texts(page) == [
        "All (5)",
        "Not checked (3)",
        "Needs review (1)",
        "Checked (1)",
        "Uncertain (1)",
        "Missing word timing (2)",
    ]
    note = page.locator("#transcript-filter-note")
    button = page.get_by_role("button", name="Next to review", exact=True)
    for value, ids in [
        ("not_checked", ["2", "3", "4"]),
        ("needs_review", ["1"]),
        ("uncertain", ["3"]),
        # Untimed and unbounded passages both lack complete reliable word timing.
        ("missing_timing", ["2", "4"]),
        ("all", ["0", "1", "2", "3", "4"]),
    ]:
        show(page).select_option(value)
        expect_visible(page, ids)
        expect(note).to_be_hidden()
        expect(button).to_be_enabled()
    show(page).select_option("checked")
    expect_visible(page, ["0"])
    expect(button).to_be_disabled()
    expect(note).to_have_text(
        "Next to review skips Checked passages. Choose another filter to continue reviewing."
    )

    # Model uncertainty only suggests: it never sets a mark. Without the display the
    # option is unavailable and an active Uncertain filter returns to All.
    show(page).select_option("uncertain")
    expect_visible(page, ["3"])
    page.locator("#show-uncertainty").uncheck()
    expect(show(page)).to_have_value("all")
    expect_visible(page, ["0", "1", "2", "3", "4"])
    uncertain = show(page).locator('option[value="uncertain"]')
    expect(uncertain).to_have_js_property("disabled", True)
    expect(uncertain).to_have_attribute(
        "title", "Turn on Show uncertainty to show its flagged passages here."
    )
    page.locator("#show-uncertainty").check()
    expect(uncertain).to_have_js_property("disabled", False)
    show(page).select_option("uncertain")
    expect_visible(page, ["3"])
    expect(status(page, 4)).to_have_value("not_checked")
    assert passage_review.read(store, rid, result) == before
    assert writes == []


def test_show_filter_combines_with_search_and_counts_the_search_results(page, filtered):
    open_filtered(page, filtered)
    search = page.locator("#transcript-search")
    show(page).select_option("not_checked")
    expect_visible(page, ["2", "3", "4"])
    search.fill("Delta")
    expect(page.locator("#transcript > .passage")).to_have_count(1)
    expect_visible(page, ["3"])
    expect(show(page)).to_have_value("not_checked")
    assert option_texts(page) == [
        "All (1)",
        "Not checked (1)",
        "Needs review (0)",
        "Checked (0)",
        "Uncertain (1)",
        "Missing word timing (0)",
    ]
    show(page).select_option("needs_review")
    expect_visible(page, [])
    expect(page.locator("#transcript-filter-note")).to_have_text("No passages match this filter.")
    search.fill("words")
    expect_visible(page, ["1"])
    search.fill("")
    expect_visible(page, ["1"])
    show(page).select_option("missing_timing")
    search.fill("Gamma")
    expect_visible(page, ["2"])


def test_next_to_review_follows_the_filter_and_marked_passages_stay_until_refiltered(
    page, filtered
):
    open_filtered(page, filtered)
    button = page.get_by_role("button", name="Next to review", exact=True)
    focus = page.locator("#transcript .review-focus")
    show(page).select_option("not_checked")
    for sid in ["2", "3", "4", "2"]:
        button.click()
        expect(focus).to_have_attribute("data-segment", sid)
    show(page).select_option("missing_timing")
    for sid in ["4", "2", "4"]:
        button.click()
        expect(focus).to_have_attribute("data-segment", sid)
    show(page).select_option("needs_review")
    button.click()
    expect(focus).to_have_attribute("data-segment", "1")
    button.click()
    expect(focus).to_have_attribute("data-segment", "1")

    # A mark made in the filtered view keeps its own passage shown; the counts and
    # Next to review update at once, and choosing a filter again re-evaluates it.
    # (Status controls are numbered from 1, passage IDs from 0.)
    status(page, 2).select_option("checked")
    expect(status(page, 2)).to_have_value("checked")
    expect_visible(page, ["1"])
    expect(show(page).locator('option[value="needs_review"]')).to_have_text("Needs review (0)")
    expect(button).to_be_disabled()
    expect(page.locator("#transcript-filter-note")).to_have_text(
        "No Not checked or Needs review passages are shown."
    )
    show(page).select_option("not_checked")
    expect_visible(page, ["2", "3", "4"])
    status(page, 3).select_option("needs_review")
    expect(status(page, 3)).to_have_value("needs_review")
    expect_visible(page, ["2", "3", "4"])
    expect(show(page).locator('option[value="not_checked"]')).to_have_text("Not checked (2)")
    button.click()
    expect(focus).to_have_attribute("data-segment", "2")
    show(page).select_option("needs_review")
    expect_visible(page, ["2"])
    show(page).select_option("checked")
    expect_visible(page, ["0", "1"])
    expect(button).to_be_disabled()


def test_filter_keeps_an_open_draft_survives_rerender_and_resets_per_recording(page, filtered):
    app, store, rid, result, other = filtered
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    open_filtered(page, filtered)
    page.locator("#passage-0 [data-action=edit]").click()
    draft = page.locator("#passage-0 textarea")
    draft.fill("Alpha corrected draft words.")
    draft.evaluate("node => { window.filterDraftNode = node; node.setSelectionRange(6, 15); }")
    show(page).select_option("needs_review")
    # The edited passage no longer matches, but stays with its draft node, caret and focus.
    expect_visible(page, ["0", "1"])
    show(page).select_option("missing_timing")
    expect_visible(page, ["0", "2", "4"])
    expect(draft).to_have_value("Alpha corrected draft words.")
    assert draft.evaluate(
        "node => node === window.filterDraftNode && node.selectionStart === 6 "
        "&& node.selectionEnd === 15"
    )
    assert page.evaluate("state.dirty && state.editing === '0'")
    draft.focus()
    draft.press("End")
    draft.type(" More")
    expect(draft).to_have_value("Alpha corrected draft words. More")
    page.locator("#passage-0 [data-action=save-edit]").click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text(
        "Alpha corrected draft words. More"
    )
    # The saved passage was edited under this filter, so it remains shown after the
    # ordinary full re-render; the filter value is kept.
    expect(show(page)).to_have_value("missing_timing")
    expect_visible(page, ["0", "2", "4"])
    assert dialogs == []
    assert (
        next(
            row
            for row in passage_review.read(store, rid, result)["passages"]
            if row["segment_id"] == "0"
        )["status"]
        == "needs_review"
    )
    page.evaluate("renderTranscript()")
    expect(show(page)).to_have_value("missing_timing")
    expect_visible(page, ["0", "2", "4"])

    page.goto(app.url + f"/#/recordings/{other}")
    expect(page.locator("[data-review-summary]")).to_have_text(
        "Manual review · Checked 0 of 1 · Needs review 0"
    )
    expect(show(page)).to_have_value("all")
    expect_visible(page, ["0"])
    page.goto(app.url + f"/#/recordings/{rid}")
    # Editing the Checked passage made it need review again; the edit also left it without
    # complete word timing, which the summary counts as automatic (ADR 0191).
    expect(page.locator("[data-review-summary]")).to_have_text(
        "Manual review · Checked 0 of 5 · Needs review 2 (1 for word timing)"
    )
    expect(show(page)).to_have_value("all")
    expect_visible(page, ["0", "1", "2", "3", "4"])


def test_show_filter_is_offered_in_the_mvp_profile(page, mvp_app):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    store = mvp_app.server.config.app.state.store
    project = store.create_project("Synthetic MVP Show filter")
    rid = store.create_recording(project["id"], "mvp.wav")["id"]
    result = store.add_result(
        rid,
        normalize_segments(
            [timed("Alpha timed words.", 0, 2), {"text": "Beta untimed.", "start": 2, "end": 3}]
        ),
        {},
        "Synthetic MVP result",
        {},
        auto_first=True,
    )["id"]
    save_status(store, rid, result, "1", "needs_review")
    page.goto(mvp_app.url + f"/#/recordings/{rid}")
    expect(page.locator("html")).to_have_attribute("data-off", re.compile(r"\bretranscribe\b"))
    expect(show(page)).to_be_visible()
    expect(show(page).locator('option[value="needs_review"]')).to_have_text("Needs review (1)")
    show(page).select_option("needs_review")
    expect_visible(page, ["1"])
    show(page).select_option("missing_timing")
    expect_visible(page, ["1"])
    assert errors == []
