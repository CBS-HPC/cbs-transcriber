"""Media handoffs own only the recording/view that requested them."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_review_layout import seed_review

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("destination", ["project", "other-recording", "same-recording", "audio"])
def test_delayed_video_handoff_cannot_restart_a_superseded_view(page, local_app, destination):
    store = local_app.server.config.app.state.store
    project, rid, result, _ = seed_review(store, video=True)
    other_id = seed_review(store, video=True)[1] if destination == "other-recording" else None
    held = []
    pattern = f"**/api/recordings/{rid}/media/video?*"  # current library-instance query
    page.route(pattern, lambda route: held.append(route))
    page.goto(local_app.url + f"/#/recordings/{rid}", wait_until="domcontentloaded")
    expect(page.locator("#passage-0")).to_be_visible()
    page.wait_for_function("() => document.querySelector('#audio').readyState >= 2")
    # A native play request may wait for metadata; playing means media actually started.
    page.evaluate(
        """() => {
          window.videoStarts = 0;
          document.querySelector('#video').addEventListener('playing', () => window.videoStarts++);
        }"""
    )
    page.locator("#audio").evaluate(
        "async e => { e.currentTime = 5; e.playbackRate = .5; await e.play(); }"
    )
    page.locator("#view-mode").select_option("video")
    assert held
    assert page.locator("#video").evaluate("e => e.readyState") == 0
    if destination == "audio":
        # The later choice keeps the original paused-at-handoff position and playing state.
        page.locator("#view-mode").select_option("audio")
    else:
        page.evaluate("hash => location.hash = hash", f"#/projects/{project['id']}")
        expect(page.locator("#player")).to_be_hidden()
        if destination in ("other-recording", "same-recording"):
            page.evaluate("hash => location.hash = hash", f"#/recordings/{other_id or rid}")
            expect(page.locator("#player")).to_be_visible()
            expect(page.locator("#view-mode")).to_have_value("audio")
    # Removing the route releases only generated media, after the old handoff lost ownership.
    page.unroute(pattern)
    page.wait_for_function("() => document.querySelector('#video').readyState >= 2")
    page.wait_for_timeout(200)
    assert page.locator("#video").evaluate("e => e.paused")
    assert page.evaluate("window.videoStarts") == 0
    if destination == "audio":
        assert not page.locator("#audio").evaluate("e => e.paused")
        assert 5 <= page.locator("#audio").evaluate("e => e.currentTime") < 6
        assert page.locator("#audio").evaluate("e => e.playbackRate") == 0.5
    else:
        assert page.locator("#audio").evaluate("e => e.paused")
    assert store.result(rid, result["id"])["segments"] == result["segments"]
    page.locator("#audio").evaluate("e => e.pause()")


@pytest.mark.parametrize("comparison", [False, True])
def test_current_audio_video_handoffs_preserve_paused_position_and_bounded_replay(
    page, local_app, comparison
):
    store = local_app.server.config.app.state.store
    _, rid, _, _ = seed_review(store, video=True)
    segments = normalize_segments(
        [
            {
                "text": "Synthetic replay.",
                "start": 5,
                "end": 5.6,
                "words": [
                    {"word": "Synthetic", "start": 5, "end": 5.3},
                    {"word": "replay.", "start": 5.3, "end": 5.6},
                ],
            }
        ]
    )
    result = store.add_result(rid, segments, {}, "Replay", {"capability": "words"})
    other = store.add_result(rid, segments, {}, "Comparison", {}) if comparison else None
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#result-select").select_option(result["id"])
    expect(page.locator("#passage-0 .word")).to_have_count(2)
    if comparison:
        page.locator("#compare-result").select_option(other["id"])
        expect(page.locator("#compare-passage-0 .word")).to_have_count(2)
    page.wait_for_function(
        "() => ['audio', 'video'].every(id => document.getElementById(id).readyState >= 2)"
    )
    for initial, target in (("audio", "video"), ("video", "audio")):
        # A paused switch keeps the same original-recording clock and speed.
        page.locator("#view-mode").select_option(initial)
        page.locator(f"#{initial}").evaluate("e => { e.currentTime = 3; e.playbackRate = .5; }")
        page.locator("#view-mode").select_option(target)
        page.wait_for_function(
            """id => {
              const e = document.getElementById(id);
              return e.paused && e.currentTime === 3 && e.playbackRate === .5;
            }""",
            arg=target,
        )
        # Replay remains bounded across a playing handoff in either direction.
        page.locator("#passage-0 [data-action='replay']").click()
        page.locator("#view-mode").select_option(initial)
        expect(page.locator("#passage-0 .word.current")).to_have_count(1)
        if comparison:
            expect(page.locator("#compare-passage-0 .word.current")).to_have_count(1)
        page.wait_for_function(
            """id => {
              const e = document.getElementById(id);
              return e.paused && e.currentTime >= 5.6 && e.currentTime < 6;
            }""",
            arg=initial,
        )
        assert page.locator(f"#{target}").evaluate("e => e.paused")
    assert store.result(rid, result["id"])["segments"] == result["segments"]


@pytest.mark.parametrize("destination", ["video", "audio"])
@pytest.mark.parametrize("control", ["seek", "speed", "pause", "play", "pause-play", "replay"])
def test_pending_video_handoff_preserves_newer_playback_controls(
    page, local_app, destination, control
):
    store = local_app.server.config.app.state.store
    _, rid, result, _ = seed_review(store, video=True)
    if control == "replay":
        result = store.add_result(
            rid,
            normalize_segments([{"text": "Bounded replay.", "start": 5, "end": 5.6}]),
            {},
            "Bounded replay",
            {},
        )
    pattern, held = f"**/api/recordings/{rid}/media/video?*", []
    page.route(pattern, lambda route: held.append(route))
    page.goto(local_app.url + f"/#/recordings/{rid}", wait_until="domcontentloaded")
    if control == "replay":
        page.locator("#result-select").select_option(result["id"])
        expect(page.locator("#passage-0 .passage-text")).to_have_text("Bounded replay.")
    page.wait_for_function("() => document.querySelector('#audio').readyState >= 2")
    page.locator("#playback-speed").select_option("0.5")
    page.locator("#audio").evaluate("e => e.currentTime = 5")
    if control not in ("play", "replay"):
        page.locator("#play-pause").click()
    page.locator("#view-mode").select_option("video")
    assert held and page.locator("#video").evaluate("e => e.readyState") == 0
    if control == "seek":
        page.locator("#seek").evaluate(
            "e => { e.value = 20; e.dispatchEvent(new Event('input', {bubbles: true})); }"
        )
    elif control == "speed":
        page.locator("#playback-speed").select_option("1.5")
    elif control in ("pause", "pause-play"):
        expect(page.locator("#play-pause")).to_have_attribute("aria-label", "Pause")
        page.locator("#play-pause").click()
        if control == "pause-play":
            expect(page.locator("#play-pause")).to_have_attribute("aria-label", "Play")
            page.locator("#play-pause").click()
    elif control == "play":
        page.locator("#play-pause").click()
    else:
        page.locator("#passage-0 [data-action='replay']").click()
    if destination == "audio":
        page.locator("#view-mode").select_option("audio")
    page.unroute(pattern)
    page.wait_for_function("() => document.querySelector('#video').readyState >= 2")
    if control == "replay":
        page.wait_for_function(
            """id => {
              const e = document.getElementById(id);
              return e.paused && e.currentTime >= 5.6 && e.currentTime < 6;
            }""",
            arg=destination,
        )
    else:
        page.wait_for_function(
            """([id, control]) => {
              const e = document.getElementById(id), start = control === 'seek' ? 20 : 5;
              return e.paused === (control === 'pause') && e.currentTime >= start &&
                e.currentTime < start + 2 && e.playbackRate === (control === 'speed' ? 1.5 : .5);
            }""",
            arg=[destination, control],
        )
    expect(page.locator("#playback-speed")).to_have_value("1.5" if control == "speed" else "0.5")
    assert page.locator("#audio" if destination == "video" else "#video").evaluate("e => e.paused")
    assert store.result(rid, result["id"])["segments"] == result["segments"]
    page.locator(f"#{destination}").evaluate("e => e.pause()")
