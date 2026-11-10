"""Explicit, draft-safe conflict choices through the synthetic local browser/API."""

from copy import deepcopy

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_result_merge import branch as branch
from test_browser_result_trash import manage

from cbs_transcriber.transcripts import edit_segment, normalize_segments

pytestmark = pytest.mark.browser


def open_conflicts(page, branch):
    manage(page)
    page.locator(f'[data-conflict-review="{branch["incoming"]["id"]}"]').click()
    expect(page.locator("#conflict-review-dialog")).to_be_visible()
    expect(page.locator("#conflict-review-refresh")).to_be_enabled()
    expect(page.locator("#conflict-review-error")).to_be_hidden()
    expect(page.locator("#conflict-review-status")).to_contain_text("Preview only")


def choose(page, passage_id, group, choice):
    page.locator(
        f'[data-conflict-passage="{passage_id}"] [data-conflict-choice][data-group="{group}"]'
    ).select_option(choice)
    expect(page.locator("#conflict-review-refresh")).to_be_enabled()
    expect(page.locator("#conflict-review-error")).to_be_hidden()


def test_explicit_conflict_choices_preserve_drafts_names_and_unselected_passages(page, branch):
    store, rid = branch["store"], branch["rid"]
    before = store.recording(rid)
    page.goto(branch["url"])
    page.locator("#recording-notes").locator("..").locator("summary").click()
    notes_draft = "Independent notes draft stays here."
    page.locator("#recording-notes").fill(notes_draft)
    page.locator("#passage-1 [data-action=edit]").click()
    draft = "An unsaved passage correction."
    page.locator("#passage-1 textarea").fill(draft)
    open_conflicts(page, branch)
    expect(page.locator("#conflict-review-apply")).to_be_disabled()
    expect(page.locator('[data-conflict-passage="3"]')).to_contain_text("Researcher final wording.")
    expect(page.locator('[data-conflict-passage="3"]')).to_contain_text("Incoming final wording.")
    expect(
        page.locator("#conflict-review-dialog img, #conflict-review-dialog script")
    ).to_have_count(0)
    assert not page.evaluate("window.mergeXss === true")
    assert store.recording(rid)["revision"] == before["revision"]
    choose(page, "3", "wording", "incoming")
    choose(page, "2", "timing", "incoming")
    choose(page, "0", "speaker", "current")
    expect(page.locator("#conflict-review-summary")).to_contain_text("3 decision(s)")
    page.locator("#conflict-review-dialog").screenshot(
        path="test-results/result-conflict-review.png"
    )
    prompts = []

    def decline(dialog):
        prompts.append(dialog.message)
        dialog.dismiss()

    page.once("dialog", decline)
    page.locator("#conflict-review-apply").click()
    assert prompts == ["Discard this unsaved passage correction?"]
    expect(page.locator("#passage-1 textarea")).to_have_value(draft)
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#conflict-review-apply").click()
    expect(page.locator("#conflict-review-dialog")).to_be_hidden()
    expect(page.locator("#results-status")).to_contain_text("Passage decisions recorded")
    expect(page.locator("#recording-notes")).to_have_value(notes_draft)
    current = store.recording(rid)
    assert current["segments"][0] == before["segments"][0]
    assert current["segments"][1] == before["segments"][1]
    assert current["segments"][2]["word_timing"]
    assert current["segments"][2]["speaker"] == before["segments"][2]["speaker"]
    assert current["segments"][3] == edit_segment(
        before["segments"][3], "free", "Incoming final wording."
    )
    assert current["speakers"] == before["speakers"]
    assert current["notes"] == "Saved independent notes æøå"
    head = store.result_versions(rid, branch["ours"]["id"])[0]
    assert head["action"] == "result_conflict_review"
    assert head["source_version_id"] is None


def test_keep_current_records_an_immutable_same_payload_review(page, branch):
    store, rid = branch["store"], branch["rid"]
    before = store.result_versions(rid, branch["ours"]["id"])[0]
    page.goto(branch["url"])
    open_conflicts(page, branch)
    choose(page, "3", "wording", "current")
    expect(page.locator("#conflict-review-summary")).to_contain_text("Passages changed: 0")
    page.locator("#conflict-review-apply").click()
    expect(page.locator("#conflict-review-dialog")).to_be_hidden()
    current = store.result_versions(rid, branch["ours"]["id"])[0]
    assert current["action"] == "result_conflict_review"
    assert current["predecessor_id"] == before["id"]
    assert current["payload_sha256"] == before["payload_sha256"]
    assert current["context"]["conflict_review"]["decisions"] == [
        {"passage_id": "3", "group": "wording", "choice": "current"}
    ]


def test_stale_conflict_review_requires_refresh_without_losing_choices(page, branch):
    store, rid = branch["store"], branch["rid"]
    page.goto(branch["url"])
    open_conflicts(page, branch)
    choose(page, "3", "wording", "current")
    current = store.recording(rid)
    changed = deepcopy(current["segments"])
    changed[3] = edit_segment(changed[3], "free", "A newer reviewed correction.")
    store.save_transcript(
        rid, changed, current["speakers"], current["revision"], result_id=branch["ours"]["id"]
    )
    page.locator("#conflict-review-apply").click()
    expect(page.locator("#conflict-review-error")).to_contain_text("another window")
    expect(page.locator("#conflict-review-apply")).to_be_disabled()
    expect(page.locator('[data-conflict-passage="3"]')).to_contain_text("Researcher final wording.")
    page.locator("#conflict-review-refresh").click()
    expect(page.locator("#conflict-review-refresh")).to_be_enabled()
    expect(page.locator("#conflict-review-error")).to_be_hidden()
    expect(page.locator('[data-conflict-passage="3"]')).to_contain_text(
        "A newer reviewed correction."
    )
    expect(page.locator('[data-passage="3"][data-group="wording"]')).to_have_value("current")
    page.locator("#conflict-review-apply").click()
    expect(page.locator("#conflict-review-dialog")).to_be_hidden()
    assert store.recording(rid)["segments"][3] == changed[3]


@pytest.mark.parametrize("stage", ["conflicts-preview", "conflicts"])
def test_delayed_conflict_reply_cannot_replace_newer_navigation(page, branch, local_app, stage):
    store = branch["store"]
    project = store.create_project("Newer view")
    other = store.create_recording(project["id"], "Other.wav")
    result = store.add_result(
        other["id"], normalize_segments([{"text": "Keep this newer recording."}]), {}, "Other", {}
    )
    held = []

    def hold(route):
        held.append((route, route.fetch()))
        page.evaluate("window.conflictReplyHeld = true")

    page.goto(branch["url"])
    if stage == "conflicts":
        open_conflicts(page, branch)
        choose(page, "3", "wording", "current")
    else:
        manage(page)
    page.route(f"**/transcripts/{branch['ours']['id']}/{stage}", hold)
    if stage == "conflicts":
        page.locator("#conflict-review-apply").click()
    else:
        page.locator("[data-conflict-review]").click()
    page.wait_for_function("() => window.conflictReplyHeld === true")
    page.evaluate("id => { location.hash = '/recordings/' + id; }", other["id"])
    expect(page.locator("#recording-title")).to_have_text("Other")
    expect(page.locator("#conflict-review-dialog")).to_be_hidden()
    route, response = held.pop()
    with page.expect_response(lambda reply: reply.url.endswith("/" + stage)) as released:
        route.fulfill(response=response)
    released.value.finished()
    page.evaluate("() => new Promise(requestAnimationFrame)")
    if stage == "conflicts":
        page.wait_for_function("id => !pendingResultMutations.has(id)", arg=branch["rid"])
    expect(page.locator("#result-select")).to_have_value(result["id"])
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Keep this newer recording.")
    expect(page.locator("#conflict-review-dialog")).to_be_hidden()
    expect(page.locator("#results-dialog")).to_be_hidden()
