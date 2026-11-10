"""Manual passage structure changes exercise real history and synthetic local audio."""

from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_browser import create_project, import_recording
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import edit_segment, merge_passages, normalize_segments

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("unbounded", [False, True])
def test_v79_partial_corrected_words_keep_playback_after_merge_split_and_rewrite(
    page, local_app, wav_bytes, unbounded
):
    page.goto(local_app.url)
    create_project(page, "Partial structure")
    import_recording(page, local_app, "partial.wav", wav_bytes)
    identifier = page.url.rsplit("/", 1)[-1]
    store = local_app.server.config.app.state.store
    source = normalize_segments(
        [
            {
                "text": "one two",
                "start": 0,
                "end": 2,
                "words": [
                    {"word": "one", "start": 0, "end": 0.8},
                    {"word": "two", "start": 1, "end": 1.8},
                ],
            },
            {
                "text": "three gap",
                "start": None if unbounded else 2,
                "end": None if unbounded else 3,
                "words": [{"word": "three", "start": 2.2, "end": 2.8}],
            },
        ]
    )
    source[0] = edit_segment(source[0], "word", "ONE", 0)
    store.add_result(
        identifier, source, {}, "Partially timed", {"capability": "words"}, auto_first=True
    )
    compared = store.add_result(
        identifier, merge_passages(source, "0", "down"), {}, "Comparison", {"capability": "words"}
    )
    page.reload()
    expect(page.locator("#transcript .word")).to_have_count(3)
    page.locator('#passage-0 [data-action="merge-down"]').click()
    page.locator("#passage-merge-form").get_by_role(
        "button", name="Merge passages", exact=True
    ).click()
    expect(page.locator("#transcript .passage")).to_have_count(1)
    expect(page.locator("#passage-0 .word")).to_have_count(3)
    page.locator("#compare-result").select_option(compared["id"])
    expect(page.locator("#compare-passage-0 .word")).to_have_count(3)
    page.locator('#passage-0 [data-word="0"]').click()
    expect(page.locator('#passage-0 [data-word="0"]')).to_have_class("word current")
    expect(page.locator('#compare-passage-0 [data-word="0"]')).to_have_class("word current")
    page.locator("#audio").evaluate(
        "e => { e.currentTime = 2; e.dispatchEvent(new Event('timeupdate')); }"
    )
    expect(page.locator("#transcript .word.current")).to_have_count(0)
    expect(page.locator("#comparison-transcript .word.current")).to_have_count(0)
    page.locator('#passage-0 [data-action="edit"]').click()
    page.locator("#passage-0 textarea").evaluate("e => e.setSelectionRange(8, 8)")
    page.locator('#passage-0 [data-action="split"]').click()
    expect(page.locator("#transcript .passage")).to_have_count(2)
    expect(page.locator("#transcript .word")).to_have_count(3)
    saved = store.recording(identifier)["segments"]
    assert [s["word_timing"] for s in saved] == [True, False]
    assert (saved[0]["start"], saved[0]["end"]) == (0, 1.8 if unbounded else 2)
    right = saved[1]["id"]
    page.locator(f'#passage-{right} [data-word="0"]').click()
    expect(page.locator(f'#passage-{right} [data-word="0"]')).to_have_class("word current")
    page.locator(f'#passage-{right} [data-action="edit"]').click()
    page.locator(f"#passage-{right} textarea").fill("three changed")
    page.locator(f'#passage-{right} [data-action="save-edit"]').click()
    expect(page.locator(f"#passage-{right} .word")).to_have_count(1)
    retained = store.recording(identifier)["segments"][1]["words"][0]
    assert (retained["word"], retained["start"], retained["end"]) == ("three", 2.2, 2.8)
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator(f'#passage-{right} [data-action="restore"]').click()
    expect(page.locator(f"#passage-{right} .word")).to_have_count(1)
    page.reload()
    expect(page.locator("#transcript .word")).to_have_count(3)
    page.screenshot(path=f"test-results/partial-word-structure-{unbounded}.png")


def test_v79_split_timed_words_merge_both_directions_and_choose_speaker(page, local_app, wav_bytes):
    page.goto(local_app.url)
    create_project(page, "Passage structure")
    import_recording(page, local_app, "structure.wav", wav_bytes)
    identifier = page.url.rsplit("/", 1)[-1]
    store = local_app.server.config.app.state.store
    source = normalize_segments(
        [
            {
                "text": "Hej CBS.",
                "start": 0,
                "end": 2,
                "speaker": "A",
                "words": [
                    {"word": "Hej", "start": 0, "end": 0.8},
                    {"word": "CBS.", "start": 1, "end": 2},
                ],
            },
            {
                "text": "Tak.",
                "start": 2,
                "end": 3,
                "speaker": "B",
                "words": [{"word": "Tak.", "start": 2, "end": 3}],
            },
        ]
    )
    store.add_result(identifier, source, {"A": "Anna", "B": "Benny"}, "Timed", {}, auto_first=True)
    page.reload()
    expect(page.locator("#edit-mode")).to_have_value("free")
    expect(page.locator('#passage-0 [data-action="merge-up"]')).to_be_disabled()
    page.locator('#passage-0 [data-action="edit"]').click()
    page.locator("#passage-0 textarea").evaluate("e => e.setSelectionRange(4, 4)")
    with page.expect_response(
        local_app.url + f"/api/recordings/{identifier}/segments/0/split-v2"
    ) as split:
        page.locator('#passage-0 [data-action="split"]').click()
    assert split.value.status == 200, split.value.text()
    assert split.value.request.post_data_json["cursor"] == 4
    expect(page.locator("#transcript .passage")).to_have_count(3)
    result = store.recording(identifier)
    assert [s["text"] for s in result["segments"]] == ["Hej ", "CBS.", "Tak."]
    assert all(s["word_timing"] for s in result["segments"])
    assert result["segments"][0]["end"] == 0.8
    assert result["segments"][1]["start"] == 1
    expect(
        page.get_by_role("button", name="Change speaker for passage 2", exact=True)
    ).to_be_visible()
    expect(page.get_by_role("button", name="Replay 2", exact=True)).to_be_visible()
    second = result["segments"][1]["id"]
    page.locator(f'#passage-{second} [data-action="merge-down"]').click()
    expect(page.locator("#passage-merge-preview")).to_have_text("CBS. Tak.")
    expect(page.locator("#passage-merge-speaker-label")).to_be_visible()
    page.locator("#passage-merge-speaker").select_option("B")
    with page.expect_response(
        local_app.url + f"/api/recordings/{identifier}/segments/{second}/merge"
    ) as merged_down:
        page.locator("#passage-merge-form").get_by_role(
            "button", name="Merge passages", exact=True
        ).click()
    assert merged_down.value.status == 200, merged_down.value.text()
    expect(page.locator("#transcript .passage")).to_have_count(2)
    result = store.recording(identifier)
    assert result["segments"][1]["speaker"] == "B"
    page.locator(f'#passage-{second} [data-action="merge-up"]').click()
    page.locator("#passage-merge-speaker").select_option("A")
    with page.expect_response(
        local_app.url + f"/api/recordings/{identifier}/segments/{second}/merge"
    ) as merged_up:
        page.locator("#passage-merge-form").get_by_role(
            "button", name="Merge passages", exact=True
        ).click()
    assert merged_up.value.status == 200, merged_up.value.text()
    expect(page.locator("#transcript .passage")).to_have_count(1)
    result = store.recording(identifier)
    assert result["segments"][0]["text"] == "Hej  CBS. Tak."
    assert result["segments"][0]["word_timing"]
    assert result["segments"][0]["speaker"] == "A"
    assert (
        len(
            store.all(
                "SELECT v.id FROM result_versions v "
                "JOIN transcript_results r ON r.id=v.result_id WHERE r.recording_id=?",
                (identifier,),
            )
        )
        == 4
    )
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    screenshot = Path("test-results/passage-structure.png")
    screenshot.parent.mkdir(exist_ok=True)
    page.screenshot(path=str(screenshot), full_page=True)


def test_v79_split_uses_explicit_playback_boundary_and_unicode_cursor(page, local_app, wav_bytes):
    page.goto(local_app.url)
    create_project(page, "Split positions")
    import_recording(page, local_app, "positions.wav", wav_bytes)
    identifier = page.url.rsplit("/", 1)[-1]
    store = local_app.server.config.app.state.store
    store.add_result(
        identifier,
        normalize_segments(
            [
                {"text": "Først senere", "start": 0, "end": 3},
                {"text": "Ø 😊 også"},
            ]
        ),
        {},
        "Passages",
        {},
        auto_first=True,
    )
    page.reload()
    page.locator('#passage-0 [data-action="edit"]').click()
    page.locator("#passage-0 textarea").evaluate("e => e.setSelectionRange(6, 6)")
    expect(page.locator('#passage-0 [data-action="split"]')).to_be_enabled()
    expect(page.locator("#transcript .passage")).to_have_count(2)
    for _ in range(200):
        if page.locator("#audio").evaluate("e => e.readyState") >= 1:
            break
        page.wait_for_timeout(50)
    page.locator("#audio").evaluate("e => e.currentTime = 1.5")
    page.locator('#passage-0 [data-action="split"]').click()
    expect(page.locator("#transcript .passage")).to_have_count(3)
    result = store.recording(identifier)
    assert result["segments"][0]["end"] == result["segments"][1]["start"] == 1.5
    assert all(not s["word_timing"] for s in result["segments"])
    page.locator('#passage-1 [data-action="edit"]').click()
    page.locator("#passage-1 textarea").fill("Changed text")
    expect(page.locator('#passage-1 [data-action="split"]')).to_be_disabled()
    page.locator("#passage-1 textarea").fill("Ø 😊 også")
    expect(page.locator('#passage-1 [data-action="split"]')).to_be_disabled()
    # DOM offsets are UTF-16; the API receives Unicode code points, including the emoji.
    page.locator("#passage-1 textarea").evaluate("e => e.setSelectionRange(5, 5)")
    expect(page.locator('#passage-1 [data-action="split"]')).to_be_enabled()
    page.locator('#passage-1 [data-action="split"]').click()
    expect(page.locator("#transcript .passage")).to_have_count(4)
    result = store.recording(identifier)
    assert [s["text"] for s in result["segments"]][2:] == ["Ø 😊 ", "også"]
    assert all(s["start"] is None and s["end"] is None for s in result["segments"][2:])
    page.reload()
    expect(page.locator("#transcript .passage")).to_have_count(4)


def test_b371_partly_timed_split_uses_cursor_word_boundary(page, local_app, wav_bytes):
    page.goto(local_app.url)
    create_project(page, "Cursor split")
    import_recording(page, local_app, "cursor.wav", wav_bytes)
    identifier = page.url.rsplit("/", 1)[-1]
    store = local_app.server.config.app.state.store
    # "uh" has no timing, so the passage is only partly timed (as after WhisperX estimates).
    source = normalize_segments(
        [
            {
                "text": "one uh two three",
                "start": 0,
                "end": 3,
                "words": [
                    {"word": "one", "start": 0.1, "end": 0.6},
                    {"word": "uh"},
                    {"word": "two", "start": 1.2, "end": 1.6},
                    {"word": "three", "start": 2.0, "end": 2.6},
                ],
            }
        ]
    )
    assert source[0]["word_timing"] is False
    store.add_result(
        identifier, source, {}, "Partly timed", {"capability": "words"}, auto_first=True
    )
    page.reload()
    # Playback stays at 0, outside the passage interior: the cursor alone decides.
    page.locator('#passage-0 [data-action="edit"]').click()
    page.locator("#passage-0 textarea").evaluate("e => e.setSelectionRange(1, 1)")
    expect(page.locator('#passage-0 [data-action="split"]')).to_be_disabled()
    expect(page.locator("#transcript .passage")).to_have_count(1)
    # Between untimed "uh" and timed "two": no audio point is inferred from the cursor.
    page.locator("#passage-0 textarea").evaluate("e => e.setSelectionRange(7, 7)")
    with page.expect_response(
        local_app.url + f"/api/recordings/{identifier}/segments/0/split-v2"
    ) as split:
        page.locator('#passage-0 [data-action="split"]').click()
    assert split.value.status == 200, split.value.text()
    assert "split_time" not in split.value.request.post_data_json
    expect(page.locator("#transcript .passage")).to_have_count(2)
    saved = store.recording(identifier)["segments"]
    assert [s["text"] for s in saved] == ["one uh ", "two three"]
    assert (saved[0]["start"], saved[0]["end"], saved[1]["start"], saved[1]["end"]) == (
        None,
        None,
        1.2,
        2.6,
    )
    assert [w["word"] for w in saved[1]["words"]] == ["two", "three"]
    assert saved[1]["word_timing"] is True
    # The unlocated half never borrows the original full passage's bounds. Its timed word
    # remains available; splitting again can locate only the fully verified "one" half.
    # (Polled with Locator.evaluate: the app's CSP refuses wait_for_function's string eval.)
    for _ in range(200):
        if page.locator("#audio").evaluate("e => e.readyState") >= 1:
            break
        page.wait_for_timeout(50)
    page.locator("#audio").evaluate("e => e.currentTime = 0.9")
    expect(page.locator("#audio")).to_have_js_property("currentTime", 0.9)
    page.locator('#passage-0 [data-action="edit"]').click()
    page.locator("#passage-0 textarea").evaluate("e => e.setSelectionRange(4, 4)")
    page.locator('#passage-0 [data-action="split"]').click()
    expect(page.locator("#transcript .passage")).to_have_count(3)
    saved = store.recording(identifier)["segments"]
    assert [s["text"] for s in saved] == ["one ", "uh ", "two three"]
    assert (saved[0]["start"], saved[0]["end"]) == (0.1, 0.6)
    assert saved[1]["start"] is None and saved[1]["end"] is None


def test_v79_late_merge_response_cannot_replace_newer_recording_or_draft(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Delayed structural response")
    recordings = []
    for name, texts in (
        ("First", ["First passage.", "Second passage."]),
        ("Second", ["A separate recording."]),
    ):
        recording = store.create_recording(project["id"], name + ".wav")
        store.add_result(
            recording["id"],
            normalize_segments([{"text": text} for text in texts]),
            {},
            name,
            {},
            auto_first=True,
        )
        recordings.append(recording)
    first, second = recordings
    second_before = store.recording(second["id"])
    page.goto(local_app.url + f"/#/recordings/{first['id']}")
    expect(page.locator("#recording-title")).to_have_text("First")
    held = []

    def hold_merge(route):
        response = route.fetch()
        assert response.status == 200
        held.append((route, response))
        page.evaluate("window.structureResponseHeld = true")

    page.route(f"**/api/recordings/{first['id']}/segments/0/merge", hold_merge)
    page.locator('#passage-0 [data-action="merge-down"]').click()
    page.locator("#passage-merge-form").get_by_role(
        "button", name="Merge passages", exact=True
    ).click()
    page.wait_for_function("() => window.structureResponseHeld === true")
    assert store.recording(first["id"])["segments"][0]["text"] == "First passage. Second passage."
    try:
        page.once("dialog", lambda dialog: dialog.accept())
        page.evaluate("id => location.hash = '/recordings/' + id", second["id"])
        expect(page.locator("#recording-title")).to_have_text("Second")
        expect(page.locator("#passage-merge-dialog")).not_to_be_visible()
        expect(page.locator("#transcript .passage")).to_have_count(1)
        page.locator('#passage-0 [data-action="edit"]').click()
        draft = "An unsaved draft in the newer recording."
        page.locator("#passage-0 textarea").fill(draft)
        route, response = held.pop()
        with page.expect_response(
            lambda response: response.url.endswith(f"/recordings/{first['id']}/segments/0/merge")
        ):
            route.fulfill(response=response)
        page.wait_for_function("() => passageStructurePending === false")
        expect(page.locator("#recording-title")).to_have_text("Second")
        expect(page.locator("#transcript .passage")).to_have_count(1)
        expect(page.locator("#passage-0 textarea")).to_have_value(draft)
        expect(page.locator('#passage-0 [data-action="save-edit"]')).to_be_enabled()
        assert store.recording(second["id"]) == second_before
        assert (
            store.recording(first["id"])["segments"][0]["text"] == "First passage. Second passage."
        )
    finally:
        if held:
            route, response = held.pop()
            route.fulfill(response=response)
