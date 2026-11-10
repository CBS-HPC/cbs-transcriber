"""Explicit Working versions survive review switches, drafts and historical restoration."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.media import ffmpeg
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_current_working_action_is_compact_aligned_and_keyboard_accessible(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Working toolbar")
    recording = store.create_recording(project["id"], "Synthetic.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (rid,))
    directory = store.directory(rid)
    directory.mkdir(parents=True, exist_ok=True)
    ffmpeg(["-f", "lavfi", "-i", "sine=frequency=330:duration=3", str(directory / "audio.mp3")])
    result = store.add_result(
        rid,
        normalize_segments([{"text": "Current saved wording.", "start": 0, "end": 2}]),
        {},
        "Reviewed",
        {},
    )
    version = store.result_versions(rid, result["id"])[0]
    page.goto(local_app.url + f"/#/recordings/{rid}")
    action = page.get_by_role("button", name="Set as working", exact=True)
    expect(action).to_be_enabled()
    expect(action).to_have_attribute("aria-describedby", "working-version-status")
    expect(page.locator("#working-version-status")).to_contain_text("No Working version")

    for width in (1920, 1440, 1024, 768, 390):
        page.set_viewport_size({"width": width, "height": 1000})
        transcribe = page.locator("#transcribe-one").bounding_box()
        working = action.bounding_box()
        group = page.locator(".editor-action-buttons").bounding_box()
        search = page.locator(".transcript-search").bounding_box()
        assert abs(transcribe["y"] - working["y"]) < 2, width
        assert 0 < working["x"] - transcribe["x"] - transcribe["width"] <= 12, width
        assert working["width"] < 180, width
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        if width == 1920:
            language = page.locator("#file-language").bounding_box()
            left_gap = group["x"] - language["x"] - language["width"]
            right_gap = search["x"] - group["x"] - group["width"]
            assert 0 < right_gap <= 12
            assert 0 < left_gap <= 12
        if width in (1920, 390):
            page.screenshot(path=f"test-results/working-toolbar-{width}.png", full_page=True)

    action.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#working-version-dialog")).to_be_visible()
    page.locator("#working-version-apply").click()
    expect(page.locator("#working-version-dialog")).to_be_hidden()
    expect(page.locator("#working-version-status")).to_contain_text("Working transcript: Reviewed")
    assert store.recording(rid)["result_roles"]["working"]["version_id"] == version["id"]
    assert store.recording(rid)["active_transcript_id"] == result["id"]

    empty = store.create_recording(project["id"], "Empty.wav")
    page.goto(local_app.url + f"/#/recordings/{empty['id']}")
    expect(action).to_be_disabled()
    expect(page.locator("#working-version-status")).to_contain_text("No Working version")


def test_mark_historical_working_version_leaves_review_and_notes_draft_unchanged(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Working versions")
    recording = store.create_recording(project["id"], "a.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (rid,))
    result = store.add_result(
        rid, normalize_segments([{"text": "Original version.", "start": 0, "end": 2}]), {}, "A", {}
    )
    first = store.result_versions(rid, result["id"])[0]
    store.save_transcript(
        rid,
        normalize_segments([{"text": "Current reviewed wording.", "start": 0, "end": 2}]),
        {},
        store.recording(rid)["revision"],
        result_id=result["id"],
    )
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#working-version-status")).to_contain_text("No Working version")
    page.locator("#recording-notes").locator("..").locator("summary").click()
    page.locator("#recording-notes").fill("Unsent notes remain here.")
    page.locator("#history-open").click()
    page.locator("[data-history-version]").last.click()
    page.locator("#history-mark-working").click()
    expect(page.locator("#working-version-preview")).to_contain_text("Version 1")
    page.locator("#working-version-apply").click()
    expect(page.locator("#working-version-dialog")).to_be_hidden()
    expect(page.locator("#working-version-status")).to_contain_text("Version 1")
    expect(page.locator("[data-history-version]").last).to_contain_text("Working transcript")
    expect(page.locator("[data-history-version]").first).not_to_contain_text("Working transcript")
    expect(page.locator("#recording-notes")).to_have_value("Unsent notes remain here.")
    assert store.recording(rid)["result_roles"]["working"]["version_id"] == first["id"]
    assert store.recording(rid)["segments"][0]["text"] == "Current reviewed wording."
    page.locator("[data-close='history-dialog']").first.click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Current reviewed wording.")
    page.locator("#passage-0 [data-action='edit']").click()
    page.locator('#working-edit-dialog [data-working-edit-choice][value="keep"]').click()
    page.locator("#passage-0 textarea").fill("Another later edit.")
    page.locator("#passage-0 [data-action='save-edit']").click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Another later edit.")
    expect(page.locator("#working-version-status")).to_contain_text("Version 1")


def test_mark_current_preview_detects_concurrent_role_choice(page, local_app):
    from cbs_transcriber import result_roles

    store = local_app.server.config.app.state.store
    project = store.create_project("Concurrent designation")
    recording = store.create_recording(project["id"], "a.wav")
    rid = recording["id"]
    store.add_result(rid, normalize_segments([{"text": "A"}]), {}, "A", {})
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#working-mark-current").click()
    expect(page.locator("#working-version-dialog")).to_be_visible()
    current = store.recording(rid)
    vid = store.result_versions(rid, current["active_transcript_id"])[0]["id"]
    preview = result_roles.preview(store, rid, vid, current["revision"], 0)
    result_roles.apply(store, rid, vid, current["revision"], 0, preview["fingerprint"])
    page.locator("#working-version-apply").click()
    expect(page.locator("#working-version-error")).to_contain_text("changed")
    assert len(store.all("SELECT * FROM result_role_events")) == 1


def working_pair(local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Delayed Working previews")
    recording = store.create_recording(project["id"], "source.wav")
    rid = recording["id"]
    result = store.add_result(
        rid, normalize_segments([{"text": "First exact version."}]), {}, "A", {}
    )
    first = store.result_versions(rid, result["id"])[0]
    store.save_transcript(
        rid,
        normalize_segments([{"text": "Second exact version."}]),
        {},
        store.recording(rid)["revision"],
        result_id=result["id"],
    )
    second = store.result_versions(rid, result["id"])[0]
    return store, rid, first, second


def test_delayed_historical_working_preview_stays_closed_after_history_closes(page, local_app):
    store, rid, _, _ = working_pair(local_app)
    held = []

    def hold(route):
        held.append((route, route.fetch()))
        page.evaluate("window.workingPreviewHeld = true")

    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#history-open").click()
    page.locator("[data-history-version]").last.click()
    page.route("**/working-preview", hold)
    page.locator("#history-mark-working").click()
    page.wait_for_function("() => window.workingPreviewHeld === true")
    page.locator("#history-dialog [data-close='history-dialog']").first.click()
    expect(page.locator("#history-dialog")).to_be_hidden()
    route, response = held.pop()
    with page.expect_response(lambda reply: reply.url.endswith("/working-preview")) as released:
        route.fulfill(response=response)
    released.value.finished()
    page.evaluate("() => new Promise(requestAnimationFrame)")
    expect(page.locator("#working-version-dialog")).to_be_hidden()
    assert store.recording(rid)["result_roles"]["working"] is None
    assert not store.all("SELECT * FROM result_role_events")


def test_older_working_preview_cannot_replace_a_later_historical_choice(page, local_app):
    store, rid, first, second = working_pair(local_app)
    held = []

    def hold(route):
        held.append((route, route.fetch()))
        page.evaluate("count => { window.workingPreviewCount = count; }", len(held))

    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#history-open").click()
    page.locator(f'[data-history-version="{first["id"]}"]').click()
    page.route("**/working-preview", hold)
    page.locator("#history-mark-working").click()
    page.wait_for_function("() => window.workingPreviewCount === 1")
    page.locator(f'[data-history-version="{second["id"]}"]').click()
    expect(page.locator("#history-mark-working")).to_have_attribute("data-version", second["id"])
    page.locator("#history-mark-working").click()
    page.wait_for_function("() => window.workingPreviewCount === 2")
    route, response = held[1]
    with page.expect_response(lambda reply: reply.url.endswith("/working-preview")) as newer:
        route.fulfill(response=response)
    newer.value.finished()
    expect(page.locator("#working-version-preview")).to_contain_text("Version 2")
    route, response = held[0]
    with page.expect_response(lambda reply: reply.url.endswith("/working-preview")) as older:
        route.fulfill(response=response)
    older.value.finished()
    page.evaluate("() => new Promise(requestAnimationFrame)")
    expect(page.locator("#working-version-preview")).to_contain_text("Version 2")
    page.locator("#working-version-apply").click()
    expect(page.locator("#working-version-dialog")).to_be_hidden()
    assert store.recording(rid)["result_roles"]["working"]["version_id"] == second["id"]
    assert len(store.all("SELECT * FROM result_role_events")) == 1
