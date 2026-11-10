"""Timing availability counts words separately from complete passages."""

import json
import threading

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.media import ffmpeg
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_partial_word_coverage_and_repair_scope_are_visible(page, local_app):
    store = Store(local_app.root)
    project = store.create_project("Timing coverage")
    recording = store.create_recording(project["id"], "synthetic.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=80 WHERE id=?", (rid,))
    store.directory(rid).mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "80",
            str(store.directory(rid) / "audio.mp3"),
        ],
        threading.Event(),
    )
    text = "Et dansk forsøg med nøjagtige intervaller og ét usikkert ord."
    segments = normalize_segments(
        [
            {
                "text": text,
                "start": 0,
                "end": 10,
                "words": [
                    {
                        "word": token,
                        "start": None if index == 4 else index * 0.5,
                        "end": None if index == 4 else index * 0.5 + 0.4,
                    }
                    for index, token in enumerate(text.split())
                ],
            },
            {
                "text": "Æ Ø Å",
                "start": 10,
                "end": 13,
                "words": [
                    {"word": word, "start": 10 + index, "end": 11 + index}
                    for index, word in enumerate(["Æ", "Ø", "Å"])
                ],
            },
            {"text": "Ingen tider", "start": 13, "end": 15},
            {
                "text": "Ændret sætning",
                "start": 15,
                "end": 17,
                "words": [
                    {"word": "Ændret", "start": 15, "end": 16},
                    {"word": "sætning", "start": None, "end": None},
                ],
            },
            {"text": "Lang passage", "start": 17, "end": 80},
        ]
    )
    segments[3].update(edited=True, words_current=True)
    result = store.add_result(rid, segments, {}, "Synthetic timing", {"capability": "words"})
    compared = store.add_result(rid, segments, {}, "Comparison", {"capability": "words"})
    page.goto(local_app.url + f"/#/recordings/{rid}")

    expect(page.locator("#timing-coverage")).to_have_text(
        "Word timing: 13/19 words (68%) · Passages: 1 complete, 2 partial, 2 untimed"
    )
    expect(page.locator("#timing-warning [data-run-repair]")).to_have_text(
        "Repair 3 of 4 incomplete passages"
    )
    assert json.loads(
        page.locator("#timing-warning [data-repair-ids]").get_attribute("data-repair-ids")
    ) == ["0", "2", "3"]
    expect(page.locator("#passage-0 .word")).to_have_count(9)
    expect(page.locator("#passage-3 .word")).to_have_count(1)
    # No banner-sized replacement: summary and action remain in the compact tag row.
    assert page.locator("#timing-warning").bounding_box()["height"] < 60
    assert page.locator("#timing-coverage").get_attribute("title")
    assert page.locator("#result-select").input_value() == result["id"]
    page.screenshot(path="test-results/timing-word-coverage.png")
    page.locator("#compare-result").select_option(compared["id"])
    expect(page.locator("#comparison-panel [data-run-repair]")).to_have_count(0)
    requests = []

    def capture(route):
        requests.append(route.request.post_data_json)
        route.fulfill(status=200, json={})

    page.route(f"**/api/recordings/{rid}/passage-timing", capture)
    page.locator("#timing-warning [data-run-repair]").click()
    expect(page.locator("#toast")).to_contain_text("Adding word timing to 3")
    assert requests[0]["segment_ids"] == ["0", "2", "3"]
    assert requests[0]["result_id"] == result["id"]


def test_coverage_handles_stale_edits_unicode_and_unknown_timing(page, local_app):
    page.goto(local_app.url)
    metrics = page.evaluate(
        """() => {
          const segment = {
            text: '🙂 ÆØÅ 𝟙 café', start: 0, end: 3, edited: false,
            word_timing: false,
            words: [
              {word:'ÆØÅ', char_start:2, char_end:5, start:0, end:1},
              {word:'𝟙', char_start:6, char_end:7, start:1, end:2},
              {word:'café', char_start:8, char_end:12, start:2, end:3}
            ]
          };
          return [
            passageTimingCoverage(segment),
            passageTimingCoverage({...segment, edited:true}),
            passageTimingCoverage({...segment, edited:true, words_current:true}),
            passageTimingCoverage({...segment, words:[]}),
            passageTimingCoverage({...segment, text:'🙂 —', words:[]})
          ];
        }"""
    )
    assert metrics == [
        {"total": 3, "timed": 3},
        {"total": 3, "timed": 0},
        {"total": 3, "timed": 3},
        {"total": 3, "timed": 0},
        {"total": 0, "timed": 0},
    ]
    hidden = page.evaluate(
        """() => {
          state.recording = {active_transcript_id:'synthetic', duration:10,
            segments:[{text:'No supplied timing', words:[], speaker:'UNKNOWN'}], speakers:{}};
          renderTimingWarning(state.recording, '#timing-warning');
          return document.querySelector('#timing-warning').hidden;
        }"""
    )
    assert hidden
    # A known word-timing source with no estimates reports zero coverage, and a
    # long passage offers whole-result options rather than claiming local repair.
    page.evaluate(
        """() => {
          state.recording = {active_transcript_id:'synthetic', duration:80,
            source:{capability:'words'}, speakers:{},
            segments:[{id:'0', text:'Ingen tider', start:0, end:80, words:[], speaker:'UNKNOWN'}]};
          renderTimingWarning(state.recording, '#timing-warning');
          refreshPassageRepairs();
        }"""
    )
    expect(page.locator("#timing-coverage")).to_have_text(
        "Word timing: 0/2 words (0%) · Passages: 0 complete, 0 partial, 1 untimed"
    )
    expect(page.locator("#timing-warning [data-run-repair]")).to_have_text(
        "Timing options for 1 incomplete passage"
    )
