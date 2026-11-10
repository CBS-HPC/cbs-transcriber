"""Merge only safe frozen enhancement changes through the real local browser/API."""

from copy import deepcopy

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_result_trash import manage

from cbs_transcriber import result_roles
from cbs_transcriber.transcripts import edit_segment, normalize_segments

pytestmark = pytest.mark.browser


@pytest.fixture
def branch(local_app):
    app = local_app.server.config.app
    store = app.state.store
    with app.state.queue.lock:
        project = store.create_project("Synthetic merge preview")
        recording = store.create_recording(project["id"], "Synthetic.wav")
        rid = recording["id"]
        store.execute("UPDATE recordings SET status='ready',duration=8 WHERE id=?", (rid,))
        segments = normalize_segments(
            [
                {"text": "Hej København!", "start": 0, "end": 2, "speaker": "MANUAL"},
                {"text": "Review this.", "start": 2, "end": 4},
                {"text": "Tak igen.", "start": 4, "end": 6},
                {"text": "Original final.", "start": 6, "end": 8},
            ]
        )
        ours = store.add_result(
            rid,
            segments,
            {"MANUAL": "Named researcher"},
            "Reviewed source",
            {"kind": "provider", "model": "synthetic-asr"},
        )
        current = store.recording(rid)
        frozen = store.freeze_result(rid, ours["id"], current["revision"])
        job = store.add_job(rid, "enhance", {"source_snapshot": frozen})
        # The fixture is producer evidence, not an executable inference request.
        store.update_job(job["id"], state="completed", progress=1, message="Complete")
        enhanced = normalize_segments(
            [
                {
                    "text": "Hej København!",
                    "start": 0,
                    "end": 2,
                    "speaker": "DETECTED",
                    "words": [
                        {"word": "Hej", "start": 0.1, "end": 0.5},
                        {"word": "København!", "start": 0.8, "end": 1.9},
                    ],
                },
                {"text": "Review this.", "start": 2, "end": 4},
                {
                    "text": "Tak igen.",
                    "start": 4,
                    "end": 6,
                    "speaker": "DETECTED",
                    "words": [
                        {"word": "Tak", "start": 4.1, "end": 4.9},
                        {"word": "igen.", "start": 5.1, "end": 5.8},
                    ],
                },
                {"text": "Original final.", "start": 6, "end": 8},
            ]
        )
        label = '<img src=x onerror="window.mergeXss=true"> enhancement'
        incoming = store.add_result(
            rid,
            enhanced,
            {"DETECTED": "Detected voice"},
            label,
            {"kind": "enhancement", "source_result_id": ours["id"]},
            job_id=job["id"],
        )
        current = store.activate_result(rid, incoming["id"], current["revision"])
        incoming_edit = deepcopy(incoming["segments"])
        incoming_edit[3] = edit_segment(incoming_edit[3], "free", "Incoming final wording.")
        current = store.save_transcript(
            rid,
            incoming_edit,
            incoming["speakers"],
            current["revision"],
            result_id=incoming["id"],
            action="free_rewrite",
        )
        current = store.activate_result(rid, ours["id"], current["revision"])
        reviewed = deepcopy(ours["segments"])
        reviewed[1] = edit_segment(
            reviewed[1], "free", 'Reviewed <img src=x onerror="window.mergeXss=true"> wording.'
        )
        reviewed[3] = edit_segment(reviewed[3], "free", "Researcher final wording.")
        store.save_transcript(
            rid,
            reviewed,
            ours["speakers"],
            current["revision"],
            result_id=ours["id"],
            action="free_rewrite",
        )
        store.save_notes("recordings", rid, "Saved independent notes æøå", 0)
    # These scenarios start from an explicitly reviewed Working head. Working
    # choices for older/unassigned versions have their own browser coverage.
    current = store.recording(rid)
    head = store.result_versions(rid, ours["id"])[0]["id"]
    role_revision = current["result_roles"]["revision"]
    preview = result_roles.preview(store, rid, head, current["revision"], role_revision)
    result_roles.apply(store, rid, head, current["revision"], role_revision, preview["fingerprint"])
    return {
        "store": store,
        "rid": rid,
        "ours": ours,
        "incoming": incoming,
        "reviewed": reviewed,
        "url": local_app.url + f"/#/recordings/{rid}",
    }


def open_preview(page, branch):
    manage(page)
    expect(page.locator("[data-preview-result-merge]")).to_have_count(1)
    page.locator(f'[data-preview-result-merge="{branch["incoming"]["id"]}"]').click()
    expect(page.locator("#result-merge-dialog")).to_be_visible()
    expect(page.locator("#result-merge-refresh")).to_be_enabled()
    expect(page.locator("#result-merge-error")).to_be_hidden()
    expect(page.locator("#result-merge-status")).to_contain_text("Preview only")


def test_merge_preview_and_apply_preserve_reviewed_text_notes_and_incoming(page, branch):
    store, rid = branch["store"], branch["rid"]
    incoming_before = store.all(
        "SELECT * FROM transcript_results WHERE id=?", (branch["incoming"]["id"],)
    )
    incoming_history = store.result_versions(rid, branch["incoming"]["id"])
    revision = store.recording(rid)["revision"]
    page.goto(branch["url"])
    page.locator("#recording-notes").locator("..").locator("summary").click()
    draft = "Independent notes draft remains here."
    page.locator("#recording-notes").fill(draft)
    page.locator("#passage-1 [data-action=edit]").click()
    passage_draft = "An unsaved passage correction needs explicit discard."
    page.locator("#passage-1 textarea").fill(passage_draft)
    open_preview(page, branch)
    expect(page.locator("#result-merge-summary")).to_contain_text("Passages updated: 2")
    expect(page.locator("#result-merge-summary")).to_contain_text("Speakers: 1")
    expect(page.locator("#result-merge-summary")).to_contain_text("Conflicts: 1")
    expect(page.locator('[data-merge-passage="1"]')).to_contain_text(branch["reviewed"][1]["text"])
    expect(page.locator('[data-merge-passage="3"]')).to_have_attribute("data-status", "conflict")
    expect(page.locator("#result-merge-dialog img, #result-merge-dialog script")).to_have_count(0)
    assert not page.evaluate("window.mergeXss === true")
    assert store.recording(rid)["revision"] == revision  # Preview is read-only.
    page.locator("#result-merge-dialog").screenshot(path="test-results/result-merge-preview.png")
    prompts = []

    def decline(dialog):
        prompts.append(dialog.message)
        dialog.dismiss()

    page.once("dialog", decline)
    page.locator("#result-merge-apply").click()
    assert prompts == ["Discard this unsaved passage correction?"]
    expect(page.locator("#passage-1 textarea")).to_have_value(passage_draft)
    assert store.recording(rid)["revision"] == revision
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#result-merge-apply").click()
    expect(page.locator("#result-merge-dialog")).to_be_hidden()
    expect(page.locator("#results-status")).to_contain_text("Safe enhancement updates merged")
    expect(page.locator("#recording-notes")).to_have_value(draft)
    current = store.recording(rid)
    assert current["active_transcript_id"] == branch["ours"]["id"]
    assert current["segments"][0]["word_timing"]
    assert current["segments"][0]["speaker"] == "MANUAL"
    assert current["segments"][1] == branch["reviewed"][1]
    assert current["segments"][2]["speaker"] == "DETECTED"
    assert current["segments"][3] == branch["reviewed"][3]
    assert current["speakers"]["MANUAL"] == "Named researcher"
    assert current["notes"] == "Saved independent notes æøå"
    assert (
        store.all("SELECT * FROM transcript_results WHERE id=?", (branch["incoming"]["id"],))
        == incoming_before
    )
    assert store.result_versions(rid, branch["incoming"]["id"]) == incoming_history
    page.locator("#versions-history-tab").click()
    expect(page.locator("[data-history-version]").first).to_contain_text("merged enhancement")
    page.locator('#history-dialog [data-close="history-dialog"]').last.click()
    # A second preview cannot apply the same updates again.
    open_preview(page, branch)
    expect(page.locator("#result-merge-summary")).to_contain_text("Passages updated: 0")
    expect(page.locator("#result-merge-apply")).to_be_disabled()


def test_stale_merge_retains_preview_and_requires_refresh(page, branch):
    store, rid = branch["store"], branch["rid"]
    page.goto(branch["url"])
    open_preview(page, branch)
    old_summary = page.locator("#result-merge-summary").inner_text()
    current = store.recording(rid)
    changed = deepcopy(current["segments"])
    changed[0] = edit_segment(changed[0], "free", "New wording from another window.")
    store.save_transcript(
        rid, changed, current["speakers"], current["revision"], result_id=branch["ours"]["id"]
    )
    page.locator("#result-merge-apply").click()
    expect(page.locator("#result-merge-error")).to_contain_text("another window")
    expect(page.locator("#result-merge-dialog")).to_be_visible()
    expect(page.locator("#result-merge-summary")).to_have_text(old_summary)
    expect(page.locator("#result-merge-apply")).to_be_disabled()
    page.locator("#result-merge-refresh").click()
    expect(page.locator("#result-merge-summary")).to_contain_text("Passages updated: 1")
    expect(page.locator("#result-merge-error")).to_be_hidden()
    expect(page.locator("#result-merge-apply")).to_be_enabled()
    page.locator("#result-merge-apply").click()
    expect(page.locator("#result-merge-dialog")).to_be_hidden()
    assert store.recording(rid)["segments"][0]["text"] == changed[0]["text"]


@pytest.mark.parametrize("stage", ["merge-preview", "merge"], ids=["preview", "apply"])
def test_delayed_merge_reply_cannot_replace_newer_navigation(page, local_app, branch, stage):
    store = branch["store"]
    project = store.create_project("Newer view")
    other = store.create_recording(project["id"], "Other.wav")
    other_result = store.add_result(
        other["id"], normalize_segments([{"text": "Keep this newer recording."}]), {}, "Other", {}
    )
    held = []

    def hold(route):
        held.append((route, route.fetch()))
        page.evaluate("window.mergeReplyHeld = true")

    page.goto(branch["url"])
    if stage == "merge":
        open_preview(page, branch)
    else:
        manage(page)
    page.route(f"**/transcripts/{branch['ours']['id']}/{stage}", hold)
    if stage == "merge":
        page.locator("#result-merge-apply").click()
    else:
        page.locator("[data-preview-result-merge]").click()
    page.wait_for_function("() => window.mergeReplyHeld === true")
    page.evaluate("id => { location.hash = '/recordings/' + id; }", other["id"])
    expect(page.locator("#recording-title")).to_have_text("Other")
    expect(page.locator("#result-merge-dialog")).to_be_hidden()
    route, response = held.pop()
    with page.expect_response(lambda reply: reply.url.endswith("/" + stage)) as released:
        route.fulfill(response=response)
    released.value.finished()
    page.evaluate("() => new Promise(requestAnimationFrame)")
    if stage == "merge":
        page.wait_for_function("id => !pendingResultMutations.has(id)", arg=branch["rid"])
    expect(page.locator("#result-select")).to_have_value(other_result["id"])
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Keep this newer recording.")
    expect(page.locator("#results-dialog")).to_be_hidden()
    expect(page.locator("#result-merge-dialog")).to_be_hidden()
    assert not held


def test_merge_preview_404_explains_updated_backend_without_writing(page, branch):
    page.route(
        "**/merge-preview",
        lambda route: route.fulfill(status=404, json={"detail": "Not Found"}),
    )
    store, rid = branch["store"], branch["rid"]
    revision = store.recording(rid)["revision"]
    page.goto(branch["url"])
    manage(page)
    page.locator("[data-preview-result-merge]").click()
    expect(page.locator("#result-merge-error")).to_have_text(
        "Merge previews need the updated backend. Restart CBS Transcriber to use this feature."
    )
    expect(page.locator("#result-merge-apply")).to_be_disabled()
    assert store.recording(rid)["revision"] == revision
