"""Manual passage review is saved independently and cannot replace editor drafts."""

from copy import deepcopy
from time import monotonic

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import passage_review
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


@pytest.fixture
def reviewed(local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic manual review")
    rid = store.create_recording(project["id"], "review.wav")["id"]
    result = store.add_result(
        rid,
        normalize_segments(
            [
                {"text": "First saved passage.", "start": 0, "end": 1},
                {"text": "Second saved passage.", "start": 1, "end": 2},
                {"text": "Third saved passage.", "start": 2, "end": 3},
            ]
        ),
        {},
        "Synthetic result",
        {},
        auto_first=True,
    )
    other = store.create_recording(project["id"], "other.wav")["id"]
    other_result = store.add_result(
        other,
        normalize_segments([{"text": "Another recording."}]),
        {},
        "Other",
        {},
        auto_first=True,
    )
    return local_app, store, rid, result["id"], other, other_result["id"]


def status(page, number):
    return page.get_by_role("combobox", name=f"Review status for passage {number}", exact=True)


def wait_held(page, held):
    deadline = monotonic() + 5
    while not held and monotonic() < deadline:
        page.wait_for_timeout(10)
    assert held, "The expected synthetic request was not intercepted."


def open_review(page, fixture):
    app, _, rid, _, _, _ = fixture
    page.goto(app.url + f"/#/recordings/{rid}")
    expect(status(page, 1)).to_be_enabled()
    expect(page.locator("[data-review-summary]")).to_have_text(
        "Manual review · Checked 0 of 3 · Needs review 0"
    )


def saved_row(store, rid, result, sid):
    return next(
        row
        for row in passage_review.read(store, rid, result)["passages"]
        if row["segment_id"] == sid
    )


def save_status(store, rid, result, sid, desired):
    response = passage_review.read(store, rid, result)
    row = next(row for row in response["passages"] if row["segment_id"] == sid)
    return passage_review.save(
        store,
        rid,
        sid,
        result_id=result,
        revision=response["revision"],
        status=desired,
        review_revision=row["review_revision"],
        passage_sha256=row["passage_sha256"],
    )


def test_review_progress_persists_without_content_versions_or_working_changes(page, reviewed):
    app, store, rid, result, _, _ = reviewed
    before = store.recording(rid)
    versions = len(store.result_versions(rid, result))
    open_review(page, reviewed)
    status(page, 1).select_option("checked")
    expect(status(page, 1)).to_have_value("checked")
    status(page, 2).select_option("needs_review")
    expect(page.locator("[data-review-summary]")).to_have_text(
        "Manual review · Checked 1 of 3 · Needs review 1"
    )
    expect(page.locator("#working-edit-dialog")).to_be_hidden()
    after = store.recording(rid)
    assert after["revision"] == before["revision"]
    assert after["segments"] == before["segments"]
    assert after["result_roles"] == before["result_roles"]
    assert len(store.result_versions(rid, result)) == versions
    page.reload()
    expect(status(page, 1)).to_have_value("checked")
    expect(status(page, 2)).to_have_value("needs_review")

    alternative = store.add_result(
        rid, normalize_segments([{"text": "A new result starts unchecked."}]), {}, "Alternative", {}
    )
    page.reload()
    page.locator("#result-select").select_option(alternative["id"])
    expect(status(page, 1)).to_have_value("not_checked")
    expect(page.locator("[data-review-summary]")).to_have_text(
        "Manual review · Checked 0 of 1 · Needs review 0"
    )
    assert app.url


def test_dirty_passage_cannot_be_checked_and_other_marks_keep_its_draft(page, reviewed):
    _, store, rid, result, _, _ = reviewed
    open_review(page, reviewed)
    status(page, 1).select_option("checked")
    expect(status(page, 1)).to_have_value("checked")
    status(page, 2).select_option("checked")
    expect(status(page, 2)).to_have_value("checked")
    page.locator("#passage-0 [data-action=edit]").click()
    draft = page.locator("#passage-0 textarea")
    draft.fill("Keep my unsaved correction and caret.")
    draft.evaluate("node => { window.reviewDraftNode = node; node.setSelectionRange(5, 12); }")
    expect(status(page, 1).locator('[value="checked"]')).to_be_disabled()
    status(page, 3).select_option("checked")
    expect(status(page, 3)).to_have_value("checked")
    expect(draft).to_have_value("Keep my unsaved correction and caret.")
    assert draft.evaluate(
        "node => node === window.reviewDraftNode && "
        "node.selectionStart === 5 && node.selectionEnd === 12"
    )
    page.locator("#passage-0 [data-action=save-edit]").click()
    expect(status(page, 1)).to_have_value("needs_review")
    expect(status(page, 2)).to_have_value("checked")
    expect(status(page, 3)).to_have_value("checked")
    assert saved_row(store, rid, result, "0")["status"] == "needs_review"


def test_failed_review_does_not_claim_checked_and_retries_its_saved_choice(page, reviewed):
    open_review(page, reviewed)
    reject = {"value": True}
    requests = []

    def patch(route):
        requests.append(route.request.post_data_json)
        if reject["value"]:
            route.fulfill(status=503, json={"detail": "Synthetic temporary refusal."})
        else:
            route.continue_()

    page.route("**/segments/0/review", patch)
    status(page, 1).select_option("checked")
    row = page.locator("#passage-0 [data-manual-review]")
    expect(row.get_by_role("alert")).to_contain_text("Review was not confirmed")
    expect(status(page, 1)).to_have_value("not_checked")
    expect(page.locator("[data-review-summary]")).to_contain_text("Checked 0 of 3")
    reject["value"] = False
    row.get_by_role("button", name="Retry review status for passage 1", exact=True).click()
    expect(status(page, 1)).to_have_value("checked")
    assert len(requests) == 2
    assert requests[0] == requests[1]


def test_lost_review_reply_reconciles_without_a_duplicate_write(page, reviewed):
    open_review(page, reviewed)
    requests = []

    def lose_reply(route):
        requests.append(route.request.post_data_json)
        response = route.fetch()
        assert response.status == 200
        route.fulfill(status=503, json={"detail": "Synthetic lost reply."})

    page.route("**/segments/0/review", lose_reply)
    status(page, 1).select_option("checked")
    row = page.locator("#passage-0 [data-manual-review]")
    expect(row.get_by_role("alert")).to_be_visible()
    expect(status(page, 1)).to_have_value("not_checked")
    row.get_by_role("button", name="Retry review status for passage 1", exact=True).click()
    expect(status(page, 1)).to_have_value("checked")
    expect(row.get_by_role("alert")).to_have_count(0)
    assert len(requests) == 1


@pytest.mark.parametrize("operation", ["get", "patch"])
def test_late_review_reply_cannot_replace_another_recording(page, reviewed, operation):
    app, store, rid, result, other, _ = reviewed
    held = []
    if operation == "patch":
        open_review(page, reviewed)
        page.route("**/segments/0/review", lambda route: held.append(route))
        status(page, 1).select_option("checked")
    else:
        page.route(f"**/recordings/{rid}/passage-reviews?*", lambda route: held.append(route))
        page.goto(app.url + f"/#/recordings/{rid}")
    wait_held(page, held)
    response = (
        save_status(store, rid, result, "0", "checked")
        if operation == "patch"
        else passage_review.read(store, rid, result)
    )
    page.goto(app.url + f"/#/recordings/{other}")
    expect(page.locator("[data-review-summary]")).to_have_text(
        "Manual review · Checked 0 of 1 · Needs review 0"
    )
    held[0].fulfill(json=response)
    page.wait_for_timeout(100)
    expect(status(page, 1)).to_have_value("not_checked")
    expect(page.locator("[data-review-summary]")).to_contain_text("Checked 0 of 1")


def test_older_review_get_cannot_replace_a_newer_row_receipt(page, reviewed):
    _, store, rid, result, _, _ = reviewed
    open_review(page, reviewed)
    reject = {"value": True}

    def patch(route):
        if reject["value"]:
            route.fulfill(status=503, json={"detail": "Synthetic temporary refusal."})
        else:
            route.continue_()

    page.route("**/segments/1/review", patch)
    status(page, 2).select_option("needs_review")
    row = page.locator("#passage-1 [data-manual-review]")
    expect(row.get_by_role("alert")).to_be_visible()
    old = passage_review.read(store, rid, result)
    held = []
    page.route(f"**/recordings/{rid}/passage-reviews?*", lambda route: held.append(route))
    row.get_by_role("button", name="Retry review status for passage 2", exact=True).click()
    status(page, 1).select_option("checked")
    expect(status(page, 1)).to_have_value("checked")
    wait_held(page, held)
    reject["value"] = False
    held[0].fulfill(json=old)
    expect(status(page, 2)).to_have_value("needs_review")
    expect(status(page, 1)).to_have_value("checked")
    expect(page.locator("[data-review-summary]")).to_contain_text("Checked 1 of 3")


def test_review_retry_does_not_overwrite_a_concurrent_manual_choice(page, reviewed):
    _, store, rid, result, _, _ = reviewed
    open_review(page, reviewed)
    requests = []

    def refuse(route):
        requests.append(route.request.post_data_json)
        route.fulfill(status=503, json={"detail": "Synthetic temporary refusal."})

    page.route("**/segments/0/review", refuse)
    status(page, 1).select_option("checked")
    row = page.locator("#passage-0 [data-manual-review]")
    expect(row.get_by_role("alert")).to_be_visible()
    save_status(store, rid, result, "0", "needs_review")
    row.get_by_role("button", name="Retry review status for passage 1", exact=True).click()
    expect(status(page, 1)).to_have_value("needs_review")
    expect(row.get_by_role("alert")).to_contain_text("Review status changed elsewhere")
    assert len(requests) == 1


def test_retained_review_control_uses_new_authority_after_in_place_speaker_rename(page, reviewed):
    open_review(page, reviewed)
    status(page, 1).evaluate("node => { window.retainedReviewControl = node; }")
    page.locator("#speakers [data-speaker]").first.click()
    page.locator("#name-input").fill("Renamed synthetic speaker")
    page.locator("#name-form").get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#speakers [data-speaker]").first).to_contain_text(
        "Renamed synthetic speaker"
    )
    expect(status(page, 1)).to_be_enabled()
    assert status(page, 1).evaluate("node => node === window.retainedReviewControl")
    status(page, 1).select_option("checked")
    expect(status(page, 1)).to_have_value("checked")
    expect(page.locator("[data-review-summary]")).to_contain_text("Checked 1 of 3")


def test_late_review_get_cannot_mark_a_new_content_revision(page, reviewed):
    app, store, rid, result, _, _ = reviewed
    save_status(store, rid, result, "0", "checked")
    old = passage_review.read(store, rid, result)
    held = []

    def first_get(route):
        if not held:
            held.append(route)
        else:
            route.continue_()

    page.route(f"**/recordings/{rid}/passage-reviews?*", first_get)
    page.goto(app.url + f"/#/recordings/{rid}")
    expect(page.locator("#passage-0 .passage-text")).to_be_visible()
    current = store.recording(rid)
    changed = deepcopy(current["segments"])
    changed[0]["text"] = "A newer saved passage."
    store.save_transcript(rid, changed, current["speakers"], current["revision"], result_id=result)
    page.evaluate(
        """async id => {
        state.recording = await api(`/recordings/${id}`);
        renderTranscript();
    }""",
        rid,
    )
    expect(status(page, 1)).to_have_value("needs_review")
    wait_held(page, held)
    held[0].fulfill(json=old)
    page.wait_for_timeout(100)
    expect(status(page, 1)).to_have_value("needs_review")
    expect(page.locator("[data-review-summary]")).to_contain_text("Checked 0 of 3")


def test_next_review_without_model_flags_skips_checked_wraps_and_updates_immediately(
    page, reviewed
):
    app, store, rid, result, _, _ = reviewed
    save_status(store, rid, result, "0", "checked")
    save_status(store, rid, result, "1", "needs_review")
    page.goto(app.url + f"/#/recordings/{rid}")
    button = page.get_by_role("button", name="Next to review", exact=True)
    expect(page.locator("#uncertainty-controls")).to_be_hidden()
    expect(page.locator("#transcript .passage.review")).to_have_count(0)
    expect(button).to_be_visible()
    expect(button).to_be_enabled()
    button.focus()
    for sid in ["1", "2", "1"]:
        button.press("Enter")
        expect(page.locator("#transcript .review-focus")).to_have_attribute("data-segment", sid)
        expect(button).to_be_focused()
    status(page, 2).select_option("checked")
    expect(status(page, 2)).to_have_value("checked")
    button.click()
    expect(page.locator("#transcript .review-focus")).to_have_attribute("data-segment", "2")
    status(page, 3).select_option("checked")
    expect(status(page, 3)).to_have_value("checked")
    expect(button).to_be_disabled()
    status(page, 2).select_option("needs_review")
    expect(status(page, 2)).to_have_value("needs_review")
    expect(button).to_be_enabled()
    button.click()
    expect(page.locator("#transcript .review-focus")).to_have_attribute("data-segment", "1")


def test_next_review_keeps_the_editor_node_draft_caret_and_input_cost(page, reviewed):
    open_review(page, reviewed)
    page.locator("#passage-0 [data-action=edit]").click()
    draft = page.locator("#passage-0 textarea")
    draft.fill("My unsaved correction.")
    page.evaluate("""() => {
        window.reviewDraftNode = document.querySelector('#passage-0 textarea');
        window.reviewNavigationScans = 0;
        window.originalReviewQuery = document.querySelectorAll;
        document.querySelectorAll = function(selector) {
            if (selector === '#transcript > .passage') window.reviewNavigationScans++;
            return window.originalReviewQuery.call(this, selector);
        };
    }""")
    draft.press("End")
    draft.type(" Still typing.")
    assert page.evaluate("window.reviewNavigationScans") == 0
    page.evaluate("() => { document.querySelectorAll = window.originalReviewQuery; }")
    draft.evaluate("node => node.setSelectionRange(3, 9)")
    text = draft.input_value()
    button = page.get_by_role("button", name="Next to review", exact=True)
    button.evaluate("node => { node.click(); node.click(); }")
    expect(page.locator("#transcript .review-focus")).to_have_attribute("data-segment", "1")
    expect(draft).to_have_value(text)
    assert draft.evaluate("""node => node === window.reviewDraftNode &&
        document.activeElement === node && node.selectionStart === 3 && node.selectionEnd === 9""")
    assert page.evaluate("state.dirty")
    expect(page.locator("#working-edit-dialog")).to_be_hidden()


def test_next_review_refuses_loading_unavailable_late_views_and_history(page, reviewed):
    app, store, rid, result, other, _ = reviewed
    old = passage_review.read(store, rid, result)
    held = []
    page.route(f"**/recordings/{rid}/passage-reviews?*", lambda route: held.append(route))
    page.goto(app.url + f"/#/recordings/{rid}")
    wait_held(page, held)
    button = page.get_by_role("button", name="Next to review", exact=True)
    expect(button).to_be_disabled()
    reject = {"value": True}

    def other_reviews(route):
        if reject["value"]:
            route.fulfill(status=503, json={"detail": "Synthetic unavailable review progress."})
        else:
            route.continue_()

    page.route(f"**/recordings/{other}/passage-reviews?*", other_reviews)
    page.goto(app.url + f"/#/recordings/{other}")
    expect(page.locator("#manual-review-progress [role=alert]")).to_be_visible()
    expect(button).to_be_disabled()
    held[0].fulfill(json=old)
    button.evaluate("node => node.click()")
    expect(page.locator("#transcript .review-focus")).to_have_count(0)
    expect(button).to_be_disabled()
    reject["value"] = False
    page.get_by_role("button", name="Retry review progress", exact=True).click()
    expect(button).to_be_enabled()
    page.locator("#history-open").click()
    expect(page.locator("#history-dialog")).to_be_visible()
    expect(button).to_be_disabled()
    page.get_by_role("button", name="Close history", exact=True).click()
    expect(button).to_be_enabled()
    button.click()
    expect(page.locator("#transcript .review-focus")).to_have_attribute("data-segment", "0")


def test_next_review_respects_rendered_search_results_without_changing_the_filter(page, reviewed):
    open_review(page, reviewed)
    search = page.locator("#transcript-search")
    search.fill("First")
    expect(page.locator("#transcript > .passage")).to_have_count(1)
    button = page.get_by_role("button", name="Next to review", exact=True)
    button.click()
    expect(page.locator("#transcript .review-focus")).to_have_attribute("data-segment", "0")
    expect(search).to_have_value("First")
    status(page, 1).select_option("checked")
    expect(status(page, 1)).to_have_value("checked")
    expect(button).to_be_disabled()
    search.fill("")
    expect(button).to_be_enabled()
    button.click()
    expect(page.locator("#transcript .review-focus")).to_have_attribute("data-segment", "1")
    search.fill("Third")
    expect(page.locator("#transcript > .passage")).to_have_count(1)
    button.click()
    expect(page.locator("#transcript .review-focus")).to_have_attribute("data-segment", "2")
    expect(search).to_have_value("Third")
