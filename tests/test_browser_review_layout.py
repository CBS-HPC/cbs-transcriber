"""Compact review and actual playback viewport, using generated public-free media."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_activity_clear import finished_job
from test_browser_result_trash import manage, restore, trash

from cbs_transcriber.media import ffmpeg
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def seed_review(store, video=False):
    project = store.create_project("Synthetic review layout")
    recording = store.create_recording(project["id"], "Review.mp4" if video else "Review.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=120 WHERE id=?", (rid,))
    directory = store.directory(rid)
    directory.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=120",
            "-ac",
            "1",
            str(directory / "audio.mp3"),
        ]
    )
    if video:
        store.execute("UPDATE recordings SET kind='video' WHERE id=?", (rid,))
        ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "color=c=0x242e70:s=320x180:r=5:d=120",
                "-i",
                str(directory / "audio.mp3"),
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(directory / "video.mp4"),
            ]
        )
    words = [{"word": f"word{i}", "start": i / 2, "end": (i + 1) / 2} for i in range(200)]
    segments = normalize_segments(
        [
            {
                "text": " ".join(word["word"] for word in words),
                "start": 0,
                "end": 100,
                "words": words,
            },
            *[
                {"text": f"Later passage {i}.", "start": 100 + i * 2, "end": 102 + i * 2}
                for i in range(10)
            ],
        ]
    )
    first = store.add_result(rid, segments, {}, "First method", {"capability": "words"})
    return project, rid, first, segments


def assert_above_player(page, selector):
    page.wait_for_function(
        """selector => {
      const node = document.querySelector(selector), pane = node?.closest('.transcript');
      if (!node || !pane) return false;
      const r = node.getBoundingClientRect(), p = pane.getBoundingClientRect();
      let bottom = Math.min(innerHeight, p.bottom);
      for (const id of ['player','video-stage']) {
        const overlay = document.getElementById(id), o = overlay.getBoundingClientRect();
        if (!overlay.hidden && o.left < r.right && o.right > r.left)
          bottom = Math.min(bottom, o.top);
      }
      return r.top >= Math.max(0, p.top) - 1 && r.bottom <= bottom + 1;
    }""",
        arg=selector,
    )


def assert_review_header_edges(page, comparison=False):
    panel = page.locator(
        "#comparison-panel" if comparison else "#editor-layout > .transcript-panel"
    ).first.bounding_box()
    search = page.locator(".editor-actions .transcript-search").bounding_box()
    versions = page.locator("#history-open").bounding_box()
    compare = page.locator("#compare-result").bounding_box()
    toolbar = page.locator(".editor-actions").bounding_box()
    assert search["x"] >= toolbar["x"] - 1
    assert search["x"] + search["width"] <= toolbar["x"] + toolbar["width"] + 1
    assert abs(versions["x"] + versions["width"] - panel["x"] - panel["width"]) < 2
    assert abs(versions["y"] - compare["y"]) < 2
    assert abs(versions["height"] - compare["height"]) < 2
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_review_controls_below_title_and_comparison_tracks_live_results(page, local_app):
    store = local_app.server.config.app.state.store
    project, rid, first, segments = seed_review(store)
    page.set_viewport_size({"width": 1920, "height": 1000})
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    expect(page.locator(".topbar, #breadcrumb")).to_have_count(0)
    expect(page.locator(".sidebar #activity-open")).to_be_visible()
    page.get_by_role("button", name="Review", exact=True).click()
    expect(page.locator("#compare-result")).to_be_disabled()
    title = page.locator(".recording-heading-main").bounding_box()
    controls = page.locator(".result-controls").bounding_box()
    assert abs(controls["x"] - title["x"]) < 2
    assert controls["y"] >= title["y"] + title["height"]
    review = page.locator("#result-select").bounding_box()
    versions = page.locator("#history-open").bounding_box()
    compare = page.locator("#compare-result").bounding_box()
    assert abs(review["y"] - compare["y"]) < 2
    assert abs(versions["y"] - review["y"]) < 2
    assert abs(versions["height"] - review["height"]) < 2
    assert compare["x"] >= review["x"] + review["width"] + 9
    assert versions["x"] >= compare["x"] + compare["width"] + 9
    expect(page.locator("#history-open")).to_have_text("Versions")
    expect(page.locator("#result-info, .result-summary")).to_have_count(0)
    actions = page.locator(".editor-actions").bounding_box()
    assert 8 <= actions["y"] - controls["y"] - controls["height"] <= 14
    transcribe = page.locator("#transcribe-one").bounding_box()
    action_buttons = page.locator(".editor-action-buttons").bounding_box()
    search = page.locator(".editor-actions .transcript-search").bounding_box()
    language = page.locator("#file-language").bounding_box()
    left_gap = action_buttons["x"] - language["x"] - language["width"]
    right_gap = search["x"] - action_buttons["x"] - action_buttons["width"]
    assert left_gap >= 5
    assert right_gap >= 5
    assert left_gap <= 12
    assert abs(search["y"] + search["height"] / 2 - transcribe["y"] - transcribe["height"] / 2) < 2
    header = page.locator("#editor-layout > .transcript-panel .transcript-toolbar").first
    expect(header.locator("#play-transcript")).to_be_visible()
    expect(header.locator("#follow-playback")).to_be_checked()
    expect(page.locator("#save-status")).to_have_attribute("data-quiet", "true")
    assert page.locator("#save-status").evaluate("e => e.getBoundingClientRect().width") == 1
    assert_review_header_edges(page)
    page.screenshot(path="test-results/review-compact-desktop.png")
    page.reload()
    expect(page.locator("#compare-result")).to_be_disabled()
    second = store.add_result(rid, segments, {}, "Second method", {"capability": "words"})
    job = finished_job(store, rid, "transcribe", "completed", "New result saved")
    store.execute("UPDATE transcript_results SET job_id=? WHERE id=?", (job, second["id"]))
    # The normal asynchronous recording refresh discovers new results.
    expect(page.locator("#compare-result")).to_be_enabled(timeout=10000)
    page.locator("#result-select").select_option(second["id"])
    expect(page.locator("#compare-result")).to_be_enabled()
    manage(page)
    trash(page, first)
    expect(page.locator("#compare-result")).to_be_disabled()
    restore(page, first)
    expect(page.locator("#compare-result")).to_be_enabled()
    trash(page, second)
    expect(page.locator("#compare-result")).to_be_disabled()
    page.locator('#results-dialog [data-close="results-dialog"]').last.click()
    # Saving an edit must not re-enable the single-result selector.
    page.locator("#passage-1 [data-action=edit]").click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Set edited version as Working"
    ).click()
    page.locator("#passage-1 textarea").fill("Reviewed synthetic passage.")
    expect(page.locator("#save-status")).to_be_visible()
    expect(page.locator("#save-status")).to_have_text("Unsaved changes")
    page.locator("#passage-1 [data-action=save-edit]").click()
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    expect(page.locator("#save-status")).to_have_attribute("data-quiet", "true")
    expect(page.locator("#compare-result")).to_be_disabled()
    for width in (1711, 1600, 1440, 1100):
        page.set_viewport_size({"width": width, "height": 900})
        assert_review_header_edges(page)
        page.screenshot(path=f"test-results/review-compact-{width}.png")
    page.set_viewport_size({"width": 760, "height": 820})
    controls = page.locator(".result-controls").bounding_box()
    title = page.locator(".recording-heading-main").bounding_box()
    assert controls["y"] >= title["y"] + title["height"]
    assert abs(controls["x"] - title["x"]) < 2
    assert_review_header_edges(page)
    page.screenshot(path="test-results/review-compact-small.png")


@pytest.mark.parametrize("video", [False, True], ids=["audio", "video-comparison"])
def test_playback_words_clear_real_player_after_resize_and_manual_scroll(page, local_app, video):
    store = local_app.server.config.app.state.store
    _, rid, _, segments = seed_review(store, video)
    if video:
        other = store.add_result(rid, segments, {}, "Comparison method", {"capability": "words"})
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#passage-0")).to_be_visible()
    if video:
        page.locator("#compare-result").select_option(other["id"])
        expect(page.locator("#comparison-info")).to_contain_text("Matching passages aligned")
        page.locator("#view-mode").select_option("video")
        expect(page.locator("#video-stage")).to_be_visible()
    assert_review_header_edges(page, comparison=video)
    media = "#video" if video else "#audio"
    page.wait_for_function(
        "selector => document.querySelector(selector).readyState >= 2", arg=media
    )
    page.locator(media).evaluate(
        "async e => { e.currentTime = 95.1; e.playbackRate = 0.1; await e.play(); }"
    )
    current = "#passage-0 [data-word='190']"
    expect(page.locator(current)).to_have_class("word current")
    assert_above_player(page, current)
    if video:
        assert_above_player(page, "#compare-passage-0 [data-word='190']")
    page.screenshot(
        path=f"test-results/playback-{'video-comparison' if video else 'audio'}-desktop.png"
    )
    # Resizing while the same word plays must re-measure, without needing a passage change.
    page.set_viewport_size({"width": 1280 if video else 760, "height": 700})
    assert_review_header_edges(page, comparison=video)
    assert_above_player(page, current)
    if video:
        assert_above_player(page, "#compare-passage-0 [data-word='190']")
    page.screenshot(
        path=f"test-results/playback-{'video-comparison' if video else 'audio'}-small.png"
    )
    page.locator("#follow-playback").uncheck()
    page.locator("#transcript").evaluate("e => e.scrollTo({top: 0, behavior: 'instant'})")
    page.locator(media).evaluate("e => e.currentTime = 96.1")
    expect(page.locator("#passage-0 [data-word='192']")).to_have_class("word current")
    assert page.locator("#transcript").evaluate("e => e.scrollTop") == 0
    page.locator("#follow-playback").check()
    assert_above_player(page, "#passage-0 [data-word='192']")
    page.locator(media).evaluate("e => e.pause()")


def test_follow_defaults_on_and_wheel_does_not_change_user_choice(page, local_app):
    store = local_app.server.config.app.state.store
    _, rid, _, _ = seed_review(store)
    page.set_viewport_size({"width": 760, "height": 700})
    page.goto(local_app.url + f"/#/recordings/{rid}")
    follow = page.locator("#follow-playback")
    expect(follow).to_be_checked()
    expect(page.locator("#show-uncertainty")).to_be_checked()

    page.wait_for_function("() => document.querySelector('#audio').readyState >= 2")
    page.locator("#audio").evaluate(
        "async e => { e.currentTime = 95.1; e.playbackRate = 0.1; await e.play(); }"
    )
    expect(page.locator("#passage-0 [data-word='190']")).to_have_class("word current")
    assert_above_player(page, "#passage-0 [data-word='190']")
    page.locator("#audio").evaluate("e => e.pause()")
    pane = page.locator("#transcript")
    before = pane.evaluate("e => e.scrollTop")
    assert before > 0
    pane.hover()
    page.mouse.wheel(0, -350)
    page.wait_for_function(
        "before => document.querySelector('#transcript').scrollTop < before", arg=before
    )
    expect(follow).to_be_checked()

    follow.uncheck()
    page.mouse.wheel(0, 100)
    expect(follow).not_to_be_checked()
    follow.check()
    assert_above_player(page, "#passage-0 [data-word='190']")


def test_follow_remains_enabled_when_wheeling_during_active_playback(page, local_app):
    store = local_app.server.config.app.state.store
    _, rid, _, _ = seed_review(store)
    page.set_viewport_size({"width": 760, "height": 700})
    page.goto(local_app.url + f"/#/recordings/{rid}")
    audio = page.locator("#audio")
    follow = page.locator("#follow-playback")
    expect(follow).to_be_checked()
    page.wait_for_function("() => document.querySelector('#audio').readyState >= 2")
    audio.evaluate("async e => { e.currentTime = 95.1; await e.play(); }")
    expect(page.locator("#passage-0 [data-word='190']")).to_have_class("word current")
    assert_above_player(page, "#passage-0 [data-word='190']")

    before = audio.evaluate("e => e.currentTime")
    page.locator("#transcript").hover()
    page.mouse.wheel(0, -350)
    page.wait_for_function(
        "before => { const e = document.querySelector('#audio'); "
        "return !e.paused && e.currentTime > before + 0.1; }",
        arg=before,
    )
    expect(follow).to_be_checked()

    follow.uncheck()
    expect(follow).not_to_be_checked()
    page.locator("#transcript").hover()
    page.mouse.wheel(0, -100)
    expect(follow).not_to_be_checked()
    follow.check()
    expect(follow).to_be_checked()
    assert_above_player(page, "#passage-0 .word.current")
    audio.evaluate("e => e.pause()")


@pytest.mark.parametrize("video,comparison", [(False, False), (True, False), (True, True)])
def test_last_playing_word_clears_overlays_in_short_viewport(page, local_app, video, comparison):
    store = local_app.server.config.app.state.store
    _, rid, _, _ = seed_review(store, video)
    words = [{"word": f"word{i}", "start": i / 2, "end": (i + 1) / 2} for i in range(197)]
    words[-1]["end"] = 119.9
    segments = normalize_segments(
        [{"text": " ".join(w["word"] for w in words), "start": 0, "end": 119.9, "words": words}]
    )
    result = store.add_result(rid, segments, {}, "Last word", {"capability": "words"})
    other = store.add_result(rid, segments, {}, "Comparison", {}) if comparison else None
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#result-select").select_option(result["id"])
    expect(page.locator("#passage-0 .word")).to_have_count(197)
    if comparison:
        page.locator("#compare-result").select_option(other["id"])
    if video:
        page.locator("#view-mode").select_option("video")
    media = "#video" if video else "#audio"
    page.wait_for_function(
        "selector => document.querySelector(selector).readyState >= 2", arg=media
    )
    page.locator(media).evaluate(
        "async e => { e.currentTime = 100; e.playbackRate = .1; await e.play(); }"
    )
    current = "#passage-0 [data-word='196']"
    expect(page.locator(current)).to_have_class("word current")
    for height in (400, 380, 360):
        page.set_viewport_size({"width": 1280 if comparison else 760, "height": height})
        assert_above_player(page, current)
        if comparison:
            assert_above_player(page, "#compare-passage-0 [data-word='196']")
    page.screenshot(path=f"test-results/playback-short-video-{video}-comparison-{comparison}.png")
    page.locator(media).evaluate("e => e.pause()")
    page.locator("#follow-playback").uncheck()
    # Keyboard users can reach and scroll the bounded panes while Follow is off.
    for selector in ("#transcript", "#comparison-transcript") if comparison else ("#transcript",):
        pane = page.locator(selector)
        expect(pane).to_have_attribute("tabindex", "0")
        pane.focus()
        for key, at_top in (("Control+Home", True), ("PageDown", False)):
            # Finish the native animation before starting a scroll in the other pane.
            pane.evaluate(
                """e => {
                  e.dataset.keyboardScrollComplete = 'false';
                  e.addEventListener('scrollend', () => e.dataset.keyboardScrollComplete = 'true',
                    {once: true});
                }"""
            )
            pane.press(key)
            page.wait_for_function(
                """([selector, atTop]) => {
                  const e = document.querySelector(selector);
                  return e.dataset.keyboardScrollComplete === 'true' &&
                    (atTop ? e.scrollTop === 0 : e.scrollTop > 0);
                }""",
                arg=[selector, at_top],
            )
    assert store.result(rid, result["id"])["segments"] == result["segments"]
