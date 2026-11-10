"""Explicit comparison wording adoption keeps audio, names and research evidence intact."""

from copy import deepcopy

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.enhancement import save_realign
from cbs_transcriber.media import ffmpeg
from cbs_transcriber.transcripts import edit_segment, normalize_segments

pytestmark = pytest.mark.browser


@pytest.fixture
def wording(local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic wording adoption")
    recording = store.create_recording(project["id"], "Wording.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=10 WHERE id=?", (rid,))
    directory = store.directory(rid)
    directory.mkdir(parents=True, exist_ok=True)
    ffmpeg(["-f", "lavfi", "-i", "sine=frequency=330:duration=10", str(directory / "audio.mp3")])
    text = "Hej København, her er den gennemgåede første sætning."
    tokens = text.split()
    target = store.add_result(
        rid,
        normalize_segments(
            [
                {
                    "text": text,
                    "start": 0.5,
                    "end": 2.75,
                    "speaker": "MANUAL",
                    "words": [
                        {
                            "word": token,
                            "start": 0.5 + i * 2.25 / len(tokens),
                            "end": 0.5 + (i + 1) * 2.25 / len(tokens),
                        }
                        for i, token in enumerate(tokens)
                    ],
                },
                {
                    "text": "En anden sætning om forskning.",
                    "start": 3,
                    "end": 4,
                    "speaker": "MANUAL",
                },
            ]
        ),
        {"MANUAL": "Named researcher"},
        "Reviewed target",
        {"kind": "provider", "model": "synthetic-timing", "capability": "words"},
        auto_first=True,
    )
    source_text = (
        "Hej København, her er den forbedrede første sætning! æ\u0301 🧪\n"
        '<img src=x onerror="window.wordingXss=true">'
    )
    source = store.add_result(
        rid,
        normalize_segments(
            [
                {"text": source_text, "start": 4.25, "end": 8.75, "speaker": "MODEL"},
                {"text": "En anden sætning om forskning.", "start": 8.8, "end": 9.5},
            ]
        ),
        {"MODEL": "Model voice"},
        '<img src=x onerror="window.wordingXss=true"> comparison',
        {"kind": "provider", "model": "synthetic-wording"},
    )
    store.save_notes("recordings", rid, "Saved notes æøå", 0)
    save_realign(store, True)  # Adoption must not implicitly schedule this opt-in processing.
    return {
        "store": store,
        "rid": rid,
        "target": target,
        "source": source,
        "source_text": source_text,
        "url": local_app.url + f"/#/recordings/{rid}",
    }


def open_comparison(page, wording):
    page.goto(wording["url"])
    page.locator("#compare-result").select_option(wording["source"]["id"])
    expect(page.locator("#comparison-info")).to_contain_text("Matching passages aligned")
    expect(page.locator("#compare-passage-0 [data-use-wording]")).to_be_visible()


def open_wording_preview(page):
    page.locator("#compare-passage-0 [data-use-wording]").click()
    expect(page.locator("#wording-adoption-dialog")).to_be_visible()
    expect(page.locator("#wording-adoption-refresh")).to_be_enabled()
    expect(page.locator("#wording-adoption-error")).to_be_hidden()
    expect(page.locator("#wording-adoption-status")).to_contain_text("Preview only")


def test_wording_preview_is_explicit_and_apply_preserves_bounds_names_and_evidence(page, wording):
    store, rid = wording["store"], wording["rid"]
    before = store.recording(rid)
    source_rows = store.all(
        "SELECT * FROM transcript_results WHERE id=?", (wording["source"]["id"],)
    )
    source_history = store.result_versions(rid, wording["source"]["id"])
    open_comparison(page, wording)
    expect(page.locator("#realign-edits")).to_be_checked()
    page.wait_for_function("() => document.querySelector('#audio').readyState >= 1")
    page.locator("#audio").evaluate("e => { e.pause(); e.currentTime=6.25; e.playbackRate=1.25; }")
    page.locator("#recording-notes").locator("..").locator("summary").click()
    notes_draft = "An independent notes draft remains intact."
    page.locator("#recording-notes").fill(notes_draft)
    page.locator("#passage-0 [data-action=edit]").click()
    passage_draft = "A passage correction needs explicit discard."
    page.locator("#passage-0 textarea").fill(passage_draft)
    open_wording_preview(page)
    expect(page.locator("#wording-adoption-target-text")).to_have_text(
        before["segments"][0]["text"]
    )
    expect(page.locator("#wording-adoption-source-text")).to_have_text(wording["source_text"])
    expect(page.locator("#wording-adoption-target-heading")).to_have_text(
        "Current reviewed wording · Passage 1 · 0.5–2.75 s"
    )
    expect(page.locator("#wording-adoption-source-heading")).to_have_text(
        "Comparison wording · Passage 1 · 4.25–8.75 s"
    )
    expect(
        page.locator("#wording-adoption-dialog img, #wording-adoption-dialog script")
    ).to_have_count(0)
    assert not page.evaluate("window.wordingXss === true")
    assert page.locator("#audio").evaluate("e => e.currentTime") == pytest.approx(6.25, abs=0.05)
    assert page.locator("#audio").evaluate("e => e.paused && e.playbackRate === 1.25")
    assert store.recording(rid)["revision"] == before["revision"]
    assert not store.all("SELECT * FROM jobs")
    page.locator("#wording-adoption-dialog").screenshot(
        path="test-results/wording-adoption-preview.png"
    )
    prompts = []

    def decline(dialog):
        prompts.append(dialog.message)
        dialog.dismiss()

    page.once("dialog", decline)
    page.locator("#wording-adoption-apply").click()
    assert prompts == ["Discard this unsaved passage correction?"]
    expect(page.locator("#passage-0 textarea")).to_have_value(passage_draft)
    assert store.recording(rid)["revision"] == before["revision"]
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#wording-adoption-apply").click()
    expect(page.locator("#wording-adoption-dialog")).to_be_hidden()
    expect(page.locator("#passage-0 .passage-text")).to_have_text(wording["source_text"])
    expect(page.locator("#recording-notes")).to_have_value(notes_draft)
    after = store.recording(rid)
    current, original = after["segments"][0], before["segments"][0]
    assert current["text"] == wording["source_text"]
    assert (current["start"], current["end"], current["speaker"]) == (
        original["start"],
        original["end"],
        original["speaker"],
    )
    for key in ("original_text", "original_words", "original_word_timing"):
        assert current[key] == original[key]
    assert not current["word_timing"] and "words_current" not in current
    assert after["segments"][1] == before["segments"][1]
    assert after["speakers"] == before["speakers"]
    assert after["notes"] == "Saved notes æøå"
    assert (
        store.all("SELECT * FROM transcript_results WHERE id=?", (wording["source"]["id"],))
        == source_rows
    )
    assert store.result_versions(rid, wording["source"]["id"]) == source_history
    assert not store.all("SELECT * FROM jobs")
    page.locator("#history-open").click()
    expect(page.locator("[data-history-version]").first).to_contain_text(
        "adopted comparison wording"
    )


def test_stale_wording_requires_refresh_and_reads_the_actual_source_head(page, wording):
    store, rid = wording["store"], wording["rid"]
    open_comparison(page, wording)
    open_wording_preview(page)
    current = store.recording(rid)
    current = store.activate_result(rid, wording["source"]["id"], current["revision"])
    source = deepcopy(current["segments"])
    fresh_text = wording["source_text"] + "\nA newer source correction."
    source[0] = edit_segment(source[0], "free", fresh_text)
    current = store.save_transcript(
        rid, source, current["speakers"], current["revision"], result_id=wording["source"]["id"]
    )
    store.activate_result(rid, wording["target"]["id"], current["revision"])
    page.locator("#wording-adoption-apply").click()
    expect(page.locator("#wording-adoption-error")).to_contain_text("another window")
    expect(page.locator("#wording-adoption-source-text")).to_have_text(wording["source_text"])
    expect(page.locator("#wording-adoption-apply")).to_be_disabled()
    page.locator("#wording-adoption-refresh").click()
    expect(page.locator("#wording-adoption-source-text")).to_have_text(fresh_text)
    expect(page.locator("#wording-adoption-apply")).to_be_enabled()
    page.locator("#wording-adoption-apply").click()
    expect(page.locator("#wording-adoption-dialog")).to_be_hidden()
    expect(page.locator("#passage-0 .passage-text")).to_have_text(fresh_text)
    expect(page.locator("#compare-passage-0 .passage-text")).to_have_text(fresh_text)


def test_wording_action_requires_one_to_one_matching_and_current_alignment(page, wording):
    grouped = {
        "rows": [{"left": ["0"], "right": ["0", "1"]}, {"left": ["1"], "right": []}],
        "confidence": ["partial", "unmatched"],
    }
    pattern = "**/alignment?*"
    page.route(pattern, lambda route: route.fulfill(json=grouped))
    page.goto(wording["url"])
    page.locator("#compare-result").select_option(wording["source"]["id"])
    expect(page.locator("#comparison-info")).to_contain_text("Matching passages aligned")
    expect(page.locator("[data-use-wording]")).to_have_count(0)
    page.locator("#compare-result").select_option("")
    page.unroute(pattern)
    page.reload()
    held = []

    def hold(route):
        held.append((route, route.fetch()))
        page.evaluate("window.wordingAlignmentHeld = true")

    page.route(pattern, hold)
    page.locator("#compare-result").select_option(wording["source"]["id"])
    page.wait_for_function("() => window.wordingAlignmentHeld === true")
    expect(page.locator("[data-use-wording]")).to_have_count(0)
    # A source-view refresh can invalidate the key even before a replacement render arrives.
    page.evaluate("state.comparison.updated = 'newer-source-view'")
    route, response = held.pop()
    with page.expect_response(lambda reply: "/alignment?" in reply.url) as released:
        route.fulfill(response=response)
    released.value.finished()
    page.evaluate("() => new Promise(requestAnimationFrame)")
    expect(page.locator("[data-use-wording]")).to_have_count(0)
    expect(page.locator("#wording-adoption-dialog")).to_be_hidden()


@pytest.mark.parametrize("stage", ["wording-preview", "wording-adopt"], ids=["preview", "apply"])
def test_delayed_wording_reply_cannot_replace_newer_navigation(page, wording, stage):
    store = wording["store"]
    project = store.create_project("Newer wording view")
    other = store.create_recording(project["id"], "Other.wav")
    result = store.add_result(
        other["id"], normalize_segments([{"text": "Keep this newer recording."}]), {}, "Other", {}
    )
    held = []

    def hold(route):
        held.append((route, route.fetch()))
        page.evaluate("window.wordingReplyHeld = true")

    open_comparison(page, wording)
    if stage == "wording-adopt":
        open_wording_preview(page)
    page.route(f"**/transcripts/{wording['target']['id']}/{stage}", hold)
    if stage == "wording-adopt":
        page.locator("#wording-adoption-apply").click()
    else:
        page.locator("#compare-passage-0 [data-use-wording]").click()
    page.wait_for_function("() => window.wordingReplyHeld === true")
    page.evaluate("id => { location.hash = '/recordings/' + id; }", other["id"])
    expect(page.locator("#recording-title")).to_have_text("Other")
    expect(page.locator("#wording-adoption-dialog")).to_be_hidden()
    route, response = held.pop()
    with page.expect_response(lambda reply: reply.url.endswith("/" + stage)) as released:
        route.fulfill(response=response)
    released.value.finished()
    page.evaluate("() => new Promise(requestAnimationFrame)")
    if stage == "wording-adopt":
        page.wait_for_function("id => !pendingResultMutations.has(id)", arg=wording["rid"])
    expect(page.locator("#result-select")).to_have_value(result["id"])
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Keep this newer recording.")
    expect(page.locator("#wording-adoption-dialog")).to_be_hidden()


def test_wording_preview_404_explains_backend_update_without_fallback(page, wording):
    open_comparison(page, wording)
    page.route(
        "**/wording-preview",
        lambda route: route.fulfill(status=404, json={"detail": "Not Found"}),
    )
    revision = wording["store"].recording(wording["rid"])["revision"]
    page.locator("#compare-passage-0 [data-use-wording]").click()
    expect(page.locator("#wording-adoption-error")).to_have_text(
        "Comparison wording previews need the updated backend. "
        "Restart CBS Transcriber to use this feature."
    )
    expect(page.locator("#wording-adoption-apply")).to_be_disabled()
    assert wording["store"].recording(wording["rid"])["revision"] == revision
    assert not wording["store"].all("SELECT * FROM jobs")


def test_confirmed_adoption_survives_a_failed_comparison_reload(page, wording):
    open_comparison(page, wording)
    open_wording_preview(page)
    page.route(
        f"**/api/recordings/{wording['rid']}/transcripts/{wording['source']['id']}",
        lambda route: route.fulfill(status=503, json={"detail": "Comparison reload unavailable"}),
    )
    page.locator("#wording-adoption-apply").click()
    expect(page.locator("#wording-adoption-dialog")).to_be_hidden()
    expect(page.locator("#comparison-panel")).to_be_hidden()
    expect(page.locator("#passage-0 .passage-text")).to_have_text(wording["source_text"])
    expect(page.locator("#toast")).to_have_text(
        "Comparison wording adopted. The comparison could not reload; select it again."
    )
    assert (
        wording["store"].recording(wording["rid"])["segments"][0]["text"] == wording["source_text"]
    )
    assert not wording["store"].all("SELECT * FROM jobs")


@pytest.mark.parametrize("missing", ["source", "target"])
def test_missing_wording_passage_requires_reselection_instead_of_restart(page, wording, missing):
    store, rid = wording["store"], wording["rid"]
    open_comparison(page, wording)
    current = store.recording(rid)
    selected = wording[missing]["id"]
    if missing == "source":
        current = store.activate_result(rid, selected, current["revision"])
    current = store.save_transcript(
        rid,
        current["segments"][1:],
        current["speakers"],
        current["revision"],
        result_id=selected,
        action="passage_delete",
    )
    if missing == "source":
        current = store.activate_result(rid, wording["target"]["id"], current["revision"])
    page.locator("#compare-passage-0 [data-use-wording]").click()
    expect(page.locator("#wording-adoption-error")).to_contain_text("no longer exists")
    expect(page.locator("#wording-adoption-error")).to_contain_text("reselect the comparison")
    expect(page.locator("#wording-adoption-error")).not_to_contain_text("Restart")
    expect(page.locator("#wording-adoption-apply")).to_be_disabled()
    assert store.recording(rid)["revision"] == current["revision"]
    assert not store.all("SELECT * FROM jobs")
