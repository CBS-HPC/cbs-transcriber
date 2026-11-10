import copy
import json
import threading

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.enhancement import EnhancementOptions, merge_output
from cbs_transcriber.media import ffmpeg
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_v59_partial_words_and_replay_refresh_after_result_selection(page, local_app):
    store = Store(local_app.root)
    project = store.create_project("Replay regression")
    recording = store.create_recording(project["id"], "synthetic.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=4 WHERE id=?", (rid,))
    store.directory(rid).mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=4",
            "-ac",
            "1",
            str(store.directory(rid) / "audio.mp3"),
        ],
        threading.Event(),
    )
    source = store.add_result(
        rid,
        normalize_segments([{"text": "Ukendt hej igen.", "start": 0, "end": 4}]),
        {},
        "Original",
        {"capability": "words"},
    )
    segments, speakers = merge_output(
        source,
        {
            "passages": [
                {
                    "id": "0",
                    "words": [
                        {"word": "Ukendt", "start": None, "end": None, "score": None},
                        {"word": "hej", "start": 1, "end": 2, "score": 0.9},
                        {"word": "igen.", "start": 2, "end": 3.5, "score": 0.9},
                    ],
                }
            ]
        },
        None,
        EnhancementOptions(),
        4,
        "partial",
    )
    enhanced = store.add_result(
        rid,
        segments,
        speakers,
        "Local WhisperX enhancement (partial word timing)",
        {"kind": "enhancement", "source_result_id": source["id"], "options": {"align": True}},
    )
    assert store.recording(rid)["result_roles"]["working"] is None
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#result-select option")).to_have_count(2)
    # Completion preserves the source; selection must rebuild playback/highlighting.
    expect(page.locator("#result-select")).to_have_value(source["id"])
    expect(page.locator("#enhancement-ready")).to_contain_text("Enhancement saved separately")
    page.locator("[data-review-enhancement]").click()
    expect(page.locator("#result-select")).to_have_value(enhanced["id"])
    expect(page.locator("#passage-0 [data-action='replay']")).to_be_enabled()
    expect(page.locator("#passage-0 .word")).to_have_count(2)
    page.locator("#passage-0 [data-word='1']").click()
    expect(page.locator("#passage-0 [data-word='1']")).to_have_class("word current")
    page.locator("#passage-0 [data-action='replay']").click()
    expect(page.locator("#passage-0")).to_have_class("passage timing-gap active")
    expect(page.locator("#play-pause")).to_have_attribute("aria-label", "Pause")
    page.locator("#play-pause").click()
    page.locator("#passage-0 [data-action='edit']").click()
    working_choice = page.get_by_role("dialog", name="Which transcript should be Working?")
    expect(working_choice).to_be_visible()
    working_choice.get_by_role("button", name="Leave unassigned", exact=True).click()
    page.locator("#passage-0 textarea").fill("Rewritten words with no known timing.")
    page.locator("#passage-0 [data-action='save-edit']").click()
    expect(page.locator("#passage-0 .word")).to_have_count(0)
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#passage-0 [data-action='restore']").click()
    expect(working_choice).to_be_visible()
    working_choice.get_by_role("button", name="Leave unassigned", exact=True).click()
    expect(page.locator("#passage-0 .word")).to_have_count(2)
    assert store.recording(rid)["result_roles"]["working"] is None
    page.locator("#result-select").select_option(source["id"])
    expect(page.locator("#passage-0 .word")).to_have_count(0)
    page.locator("#compare-result").select_option(enhanced["id"])
    expect(page.locator("#compare-passage-0 .word")).to_have_count(2)
    page.locator("#compare-passage-0 [data-word='1']").click()
    expect(page.locator("#compare-passage-0 [data-word='1']")).to_have_class("word current")
    assert store.result(rid, source["id"])["segments"] == source["segments"]


def test_v59_legacy_fragment_replays_frozen_source_without_changing_timestamps(page, local_app):
    store = Store(local_app.root)
    project = store.create_project("Legacy replay")
    recording = store.create_recording(project["id"], "legacy.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=7 WHERE id=?", (rid,))
    store.directory(rid).mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=7",
            "-ac",
            "1",
            str(store.directory(rid) / "audio.mp3"),
        ],
        threading.Event(),
    )
    source = store.add_result(
        rid,
        normalize_segments([{"text": "Known unknown known.", "start": 2, "end": 5}]),
        {},
        "Original",
        {"capability": "words"},
    )
    frozen = store.freeze_result(rid, source["id"], 1)
    store.execute(
        "INSERT INTO jobs(id,recording_id,kind,state,params,created,updated) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            "legacy-replay",
            rid,
            "enhance",
            "completed",
            json.dumps({"source_snapshot": frozen, "segment_id": "0"}),
            source["created"],
            source["created"],
        ),
    )
    parts = []
    for index, text, start, end in [
        ("0", "Known ", 2, 3),
        ("1", "unknown ", None, None),
        ("2", "known.", 4, 5),
    ]:
        segment = normalize_segments([{"text": text, "start": start, "end": end}])[0]
        segment["id"] = index
        parts.append(segment)
    original_parts = copy.deepcopy(parts)
    enhanced = store.add_result(
        rid,
        parts,
        {},
        "Legacy repair",
        {"kind": "enhancement", "source_result_id": source["id"], "options": {"align": True}},
        job_id="legacy-replay",
    )
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#result-select").select_option(enhanced["id"])
    expect(page.locator("#passage-1 .timestamp")).to_be_disabled()
    replay = page.get_by_role("button", name="Replay source passage 2", exact=True)
    expect(replay).to_be_enabled()
    replay.click()
    expect(page.locator("#play-pause")).to_have_attribute("aria-label", "Pause")
    assert 2 <= page.locator("#audio").evaluate("element => element.currentTime") < 3
    expect(page.locator("#play-pause")).to_have_attribute("aria-label", "Play", timeout=6000)
    assert 5 <= page.locator("#audio").evaluate("element => element.currentTime") < 5.4
    assert store.result(rid, enhanced["id"])["segments"] == original_parts
