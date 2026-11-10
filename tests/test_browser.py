"""Opt-in acceptance tests: a real local app and a synthetic compatible HTTP provider."""

import csv
import io
import json
import socket
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest
import uvicorn
from admission_support import write_original_wav
from conftest import MemoryCredentials
from playwright.sync_api import expect, sync_playwright

from cbs_transcriber.app import create_app
from cbs_transcriber.media import ffmpeg
from cbs_transcriber.providers import Settings, default_provider_id, public_profiles, save_profile
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


class LocalApp:
    def __init__(self, root):
        self.root = root

    def start(self):
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        port = self.socket.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.server = uvicorn.Server(
            uvicorn.Config(
                create_app(self.root, credentials=MemoryCredentials()),
                log_level="error",
                access_log=False,
                # A paused browser media request must not keep test teardown alive.
                timeout_graceful_shutdown=2,
            )
        )
        self.thread = threading.Thread(
            target=self.server.run,
            kwargs={"sockets": [self.socket]},
            daemon=True,
        )
        self.thread.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if httpx.get(self.url + "/api/health", timeout=0.5).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        raise RuntimeError("Local acceptance server failed to start.")

    def stop(self):
        self.server.should_exit = True
        self.thread.join(10)
        self.socket.close()
        assert not self.thread.is_alive()


@pytest.fixture
def local_app(tmp_path_factory):
    # Keep generated recording/chunk paths below legacy Windows path limits.
    app = LocalApp(tmp_path_factory.mktemp("web") / "library")
    app.start()
    yield app
    app.stop()


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        yield page
        context.close()
        browser.close()


def create_project(page, name):
    expect(page.locator(".nav-heading button")).to_have_count(0)
    page.locator("#sidebar-create").click()
    page.locator("#name-input").fill(name)
    page.locator("#project-classification").select_option("standard")
    page.locator("#name-form").get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#project-title")).to_have_text(name)


def open_project_settings(page):
    """Classification, storage and deletion live in the sidebar's Project Settings dialog."""
    page.locator("#project-settings-open").click()
    expect(page.locator("#project-settings-dialog")).to_be_visible()


def close_project_settings(page):
    page.locator('#project-settings-dialog [aria-label="Close project settings"]').click()
    expect(page.locator("#project-settings-dialog")).to_be_hidden()


def provider_advanced(page):
    expect(page.locator("#settings-dialog")).to_be_visible()
    if page.locator("#provider-advanced").get_attribute("open") is None:
        page.locator("#provider-advanced > summary").click()


def select_export_formats(page, *formats):
    """Choose the exact format checkboxes in the export dialog."""
    choices = page.locator("#export-dialog [data-export-format]")
    expect(choices.first).to_be_visible()
    expected = set(formats)
    available = choices.evaluate_all("items => items.map(item => item.value)")
    assert expected <= set(available)
    for format_name in formats:
        page.locator(f'#export-dialog [data-export-format][value="{format_name}"]').check()
    for value in available:
        if value not in expected:
            page.locator(f'#export-dialog [data-export-format][value="{value}"]').uncheck()
    selected = page.locator("#export-dialog [data-export-format]:checked")
    assert set(selected.evaluate_all("items => items.map(item => item.value)")) == expected


def import_recording(page, local_app, name, data):
    page.locator("#media-files").set_input_files(
        {"name": name, "mimeType": "audio/wav", "buffer": data}
    )
    expect(page.get_by_role("button", name=Path(name).stem, exact=True)).to_be_visible(
        timeout=20000
    )
    with httpx.Client(base_url=local_app.url) as client:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            jobs = client.get("/api/jobs").json()
            if jobs and all(job["state"] == "completed" for job in jobs):
                break
            time.sleep(0.1)
        assert all(job["state"] == "completed" for job in jobs), jobs
    page.get_by_role("button", name=Path(name).stem, exact=True).click()
    expect(page.locator("#recording-title")).to_have_text(Path(name).stem)


def test_browser_pending_save_blocks_navigation_until_reply(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Delayed response")
    recordings = []
    for name in ("First", "Second"):
        recording = store.create_recording(project["id"], name + ".wav")
        store.add_result(
            recording["id"],
            normalize_segments([{"text": name + " wording."}]),
            {},
            name,
            {},
            auto_first=True,
        )
        recordings.append(recording)
    page.goto(local_app.url + f"/#/recordings/{recordings[0]['id']}")
    expect(page.locator("#recording-title")).to_have_text("First")
    held = []

    def hold_save(route):
        held.append((route, route.fetch()))
        page.evaluate("window.auditSaveHeld = true")

    page.route(f"**/api/recordings/{recordings[0]['id']}/segments/0/edit-v3", hold_save)
    page.locator("#passage-0 [data-action=edit]").click()
    page.locator("#passage-0 textarea").fill("Reviewed first wording.")
    page.locator("#passage-0 [data-action=save-edit]").click()
    expect(page.locator("#save-status")).to_have_text("Saving…")
    page.wait_for_function("() => window.auditSaveHeld === true")
    page.evaluate("id => location.hash = '/recordings/' + id", recordings[1]["id"])
    expect(page.locator("#toast")).to_have_text("Wait for the current change to finish saving.")
    page.wait_for_function("id => location.hash === '#/recordings/' + id", arg=recordings[0]["id"])
    expect(page.locator("#recording-title")).to_have_text("First")
    route, response = held.pop()
    route.fulfill(response=response)
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    page.evaluate("id => location.hash = '/recordings/' + id", recordings[1]["id"])
    expect(page.locator("#recording-title")).to_have_text("Second")
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Second wording.")
    assert store.recording(recordings[0]["id"])["segments"][0]["text"] == "Reviewed first wording."


def test_browser_reconciles_uncertain_job_without_allocating(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Uncertain submission")
    recording = store.create_recording(project["id"], "synthetic.wav")
    job = store.add_job(recording["id"], "enhance", {"ucloud_run": {"submission_intent": True}})
    store.update_job(job["id"], state="failed", message="Submission outcome unknown.")
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    expect(page.locator(f"[data-reconnect='{job['id']}']")).to_be_visible()
    card = page.locator(".job").first
    expect(card).to_contain_text("CBS enhancement " + job["id"])
    bounds = card.bounding_box()
    for button in card.locator("button").all():
        box = button.bounding_box()
        assert box["x"] + box["width"] <= bounds["x"] + bounds["width"]
    screenshot = Path("test-results/uncertain-ucloud-job.png")
    screenshot.parent.mkdir(exist_ok=True)
    page.screenshot(path=str(screenshot))
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator(f"[data-no-allocation='{job['id']}']").click()
    expect(page.locator(f"[data-no-allocation='{job['id']}']")).to_have_count(0)
    assert store.job(job["id"])["params"]["ucloud_run"]["reconciled_not_created"]


def test_browser_review_modes_and_restart(page, local_app, wav_bytes):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(local_app.url)
    create_project(page, "CBS Research")
    import_recording(page, local_app, "interview.v2.wav", wav_bytes)
    payload = {
        "segments": [
            {
                "start": 0,
                "end": 1.5,
                "text": "🙂 Hej København.",
                "speaker": "SPEAKER_00",
                "words": [
                    {"word": "Hej", "start": 0, "end": 0.6},
                    {"word": "København", "start": 0.6, "end": 1.5},
                ],
            },
            {
                "start": 1.5,
                "end": 3,
                "text": "Velkommen til CBS.",
                "speaker": "SPEAKER_01",
                "words": [
                    {"word": "Velkommen", "start": 1.5, "end": 2},
                    {"word": "til", "start": 2, "end": 2.4},
                    {"word": "CBS", "start": 2.4, "end": 3},
                ],
            },
        ]
    }
    page.locator("#transcript-file").set_input_files(
        {
            "name": "ucloud.json",
            "mimeType": "application/json",
            "buffer": json.dumps(payload).encode(),
        }
    )
    expect(page.locator("#timing-badge")).to_have_text("Word timing")
    first = page.locator("#passage-0")
    first.locator("[data-word='0']").dblclick()
    expect(first.locator("[data-editor-mode]")).to_have_value("text")
    first.locator("[data-editor-mode]").select_option("timing")
    first.locator('[data-action="edit-timed-word"]').click()
    first.locator("[data-timing-text]").fill("Goddag")
    first.get_by_role("button", name="Save word").click()
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    expect(first.locator("[data-timing-text]")).to_have_value("Goddag")
    first.locator("[data-editor-mode]").select_option("text")
    first.get_by_role("textbox", name="Edit passage").fill("🙂 Goddag København. Tak.")
    first.get_by_role("button", name="Save passage").click()
    expect(first.locator(".timing-tag button")).to_have_attribute(
        "title", __import__("re").compile("Word timing incomplete")
    )
    first.get_by_role("button", name="Edit", exact=True).click()
    expect(first.locator("[data-editor-mode] option[value=timing]")).not_to_have_attribute(
        "disabled", ""
    )
    # Fresh Text saves retain proved timing and leave inserted words explicitly untimed,
    # so the current partial mapping remains editable without an alignment job.
    store = local_app.server.config.app.state.store
    edited = store.recording(page.url.rsplit("/", 1)[-1])["segments"][0]
    assert [
        (word["word"], word["start"], word["end"])
        for word in edited["words"]
        if word["start"] is not None
    ] == [
        ("Goddag", 0, 0.6),
        ("København", 0.6, 1.5),
    ]
    inserted = next(word for word in edited["words"] if word["word"] == "Tak.")
    assert inserted["start"] is None and inserted["end"] is None
    assert not store.all("SELECT id FROM jobs WHERE kind='enhance'")
    first.get_by_role("button", name="Cancel", exact=True).first.click()
    page.once("dialog", lambda dialog: dialog.accept())
    first.get_by_role("button", name="Restore original").click()
    expect(first.locator(".passage-text")).to_have_text("🙂 Hej København.")
    first.get_by_role("button", name="Edit", exact=True).click()
    expect(first.locator("[data-editor-mode] option[value=timing]")).not_to_have_attribute(
        "disabled", ""
    )
    first.get_by_role("button", name="Cancel", exact=True).first.click()
    first.locator("[data-word='1']").click()
    expect(first.locator("[data-word='1']")).to_have_class("word current", timeout=5000)
    # Whole-transcript playback overrides a passage's stop point and crosses its boundary.
    first.locator("[data-action='replay']").click()
    page.locator("#play-transcript").click()
    expect(page.locator("#play-transcript")).to_contain_text("Pause transcript")
    page.wait_for_function(
        "() => document.querySelector('#audio').currentTime > 1.8 && "
        "!document.querySelector('#audio').paused"
    )
    page.locator("#play-transcript").click()
    expect(page.locator("#play-transcript")).to_contain_text("Play transcript")
    page.get_by_role("button", name="Play", exact=True).click()
    expect(page.get_by_role("button", name="Pause", exact=True)).to_be_visible()
    page.get_by_role("button", name="Pause", exact=True).click()
    page.locator("#export-open").click()
    with page.expect_download() as download:
        page.locator("#export-link").click()
    assert download.value.suggested_filename.endswith("interview.v2.nvivo-windows.txt")
    exported = Path(download.value.path()).read_text(encoding="utf-8-sig")
    rows = list(csv.reader(io.StringIO(exported), delimiter="\t"))
    assert rows[0] == ["Timespan", "Speaker", "Content"]
    assert rows[1] == ["00:00:00.0-00:00:01.5", "Speaker_01", "🙂 Hej København."]
    assert rows[2][-1] == "Velkommen til CBS."
    expect(page.locator("#export-group-label")).to_be_hidden()
    page.locator("#export-instructions summary").click()
    expect(page.locator("#export-help")).to_contain_text("File includes header row")
    select_export_formats(page, "nvivo-mac")
    expect(page.locator("#export-help")).to_contain_text("Transcript Rows")
    with page.expect_download() as download:
        page.locator("#export-link").click()
    assert download.value.suggested_filename.endswith("interview.v2.nvivo-mac.txt")
    exported = Path(download.value.path()).read_text(encoding="utf-8-sig")
    assert exported.startswith("#00:00:00-0#\nSpeaker_01: 🙂 Hej København. #00:00:01-5#")
    assert "Speaker_02: Velkommen til CBS. #00:00:03-0#" in exported
    select_export_formats(page, "text")
    expect(page.locator("#export-group-label")).to_be_visible()
    page.locator("#export-group").select_option("timestamp")
    with page.expect_download() as download:
        page.locator("#export-link").click()
    assert download.value.suggested_filename.endswith("interview.v2.txt")
    assert (
        Path(download.value.path())
        .read_text(encoding="utf-8")
        .startswith("INTERVIEW\n\n00:00:00\n🙂 Hej København.")
    )
    page.get_by_role("button", name="Close export settings").click()
    page.locator("#transcript-search").fill("København")
    expect(page.locator(".passage")).to_have_count(1)
    expect(page.locator("mark.search-match")).to_have_text("København")
    first.locator("[data-word='1']").click()
    expect(first.locator("[data-word='1']")).to_have_class("word current")
    page.locator("#transcript-search").fill("")
    expect(page.locator("#toast.error")).to_be_hidden()
    page.locator("#recording-title").click()
    screenshot = Path("test-results/workspace.png")
    screenshot.parent.mkdir(exist_ok=True)
    page.screenshot(path=str(screenshot), full_page=True)
    page.get_by_role("button", name="Back to recordings").click()
    create_project(page, "Second project")
    expect(page.locator("#recordings .recording-card")).to_have_count(0)
    page.get_by_role("link", name="CBS Research").click()
    expect(page.locator("#recordings .recording-card")).to_have_count(1)
    local_app.stop()
    local_app.start()
    page.goto(local_app.url)
    page.get_by_role("link", name="CBS Research").click()
    page.get_by_role("button", name="interview.v2", exact=True).click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("🙂 Hej København.")
    # Untimed text must remain exportable, without fabricated transcript timestamps.
    page.locator("#export-open").click()
    select_export_formats(page, "nvivo-windows")
    page.get_by_role("button", name="Close export settings").click()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#transcript-file").set_input_files(
        {
            "name": "untimed.json",
            "mimeType": "application/json",
            "buffer": json.dumps({"text": "Untimed æøå."}).encode(),
        }
    )
    expect(page.locator("#timing-badge")).to_have_text("Untimed")
    page.locator("#export-open").click()
    expect(page.locator("#export-link")).to_have_attribute("aria-disabled", "true")
    expect(page.locator("#export-warning")).to_contain_text("Readable text")
    select_export_formats(page, "nvivo-mac")
    expect(page.locator("#export-link")).to_have_attribute("aria-disabled", "true")
    select_export_formats(page, "text")
    with page.expect_download() as download:
        page.locator("#export-link").click()
    assert "Untimed æøå." in Path(download.value.path()).read_text(encoding="utf-8")
    page.get_by_role("button", name="Close export settings").click()
    assert not errors, errors


def test_browser_comparison_aligns_split_passages_gaps_resize_and_edits(page, local_app, wav_bytes):
    page.goto(local_app.url)
    create_project(page, "Aligned comparison")
    import_recording(page, local_app, "align.wav", wav_bytes)
    with httpx.Client(base_url=local_app.url, headers={"X-CBS-Request": "1"}) as client:
        project = client.get("/api/projects").json()[0]
        recording = client.get(f"/api/projects/{project['id']}/recordings").json()[0]
        left = ["Hej København. Hvordan går det?", "Det går rigtig godt.", "Tak for din tid."]
        right = [
            "Hej København.",
            "Hvordan går det?",
            "Unrelated additional content.",
            "Det går rigtig godt!",
            "Tak for din tid.",
        ]
        tail = [
            f"Efterfølgende passage nummer {i} med ekstra ord til sammenligning." for i in range(18)
        ]
        tail.append("🙂 Et udtryk med [a+b] og æøå.")
        results = []
        for texts in (left + tail, right + tail):
            response = client.post(
                f"/api/recordings/{recording['id']}/transcript",
                data={"revision": recording["revision"], "replace": "true"},
                files={
                    "file": (
                        "transcript.json",
                        json.dumps(
                            {"segments": [{"text": text, "speaker": "A"} for text in texts]}
                        ).encode(),
                        "application/json",
                    )
                },
            )
            assert response.status_code == 200, response.text
            recording = response.json()
            results.append(recording["active_transcript_id"])
    page.reload()
    page.locator("#result-select").select_option(results[0])
    expect(page.locator("#passage-0 .passage-text")).to_have_text(left[0])
    page.locator("#transcript-search").fill("København")
    expect(page.locator("#transcript .passage")).to_have_count(1)
    page.locator("#compare-result").select_option(results[1])
    expect(page.locator("#comparison-info")).to_contain_text("Matching passages aligned")
    expect(page.locator("#transcript .passage")).to_have_count(len(left + tail))
    expect(page.locator("#transcript mark.search-match")).to_have_text("København")
    expect(page.locator("#comparison-transcript mark.search-match")).to_have_text("København")
    page.locator("#transcript-search").fill("")
    expect(page.locator("#transcript .alignment-gap")).to_have_count(1)
    assert "København" in "".join(
        page.locator("#passage-0 mark.comparison-equal").all_text_contents()
    )
    expect(page.locator("#compare-passage-2 mark.comparison-different")).to_be_visible()
    expect(page.locator("#use-wording-anchor")).to_have_count(0)
    expect(page.locator("#wording-tools")).to_have_count(0)
    page.evaluate("window.searchPassage = document.querySelector('#passage-1')")
    page.locator("#transcript-search").fill("det går rigtig godt")
    assert page.evaluate("window.searchPassage === document.querySelector('#passage-1')")
    expect(page.locator("#transcript mark.search-match")).to_have_text("Det går rigtig godt")
    expect(page.locator("#comparison-transcript mark.search-match")).to_have_text(
        "Det går rigtig godt"
    )
    expect(page.locator("#transcript .passage")).to_have_count(len(left + tail))
    expect(page.locator("#comparison-transcript .passage")).to_have_count(len(right + tail))
    expect(page.locator("#comparison-info")).to_contain_text("Matching passages aligned")
    page.locator("#transcript-search").fill("[A+B]")
    expect(page.locator("#transcript mark.search-match")).to_have_text("[a+b]")
    expect(page.locator("#comparison-transcript mark.search-match")).to_have_text("[a+b]")
    page.locator("#transcript-search").fill("passage")
    expect(page.locator("#transcript mark.search-match")).to_have_count(18)
    expect(page.locator("#comparison-transcript mark.search-match")).to_have_count(18)
    page.locator("#transcript-search").fill("")
    page.wait_for_function("""() =>
        Math.abs(document.querySelector('#passage-1').getBoundingClientRect().top -
        document.querySelector('#compare-passage-3').getBoundingClientRect().top) < 2""")
    assert page.locator("#comparison-transcript [data-alignment-row='0'] .passage").count() == 2
    page.locator("#transcript").evaluate("(el) => el.scrollTop = 300")
    page.wait_for_function("""() => Math.abs(
        document.querySelector('#transcript').scrollTop -
        document.querySelector('#comparison-transcript').scrollTop) < 2""")
    page.set_viewport_size({"width": 900, "height": 1000})
    page.wait_for_function("""() =>
        document.querySelector('#transcript .aligned-group').style.minHeight === ''""")
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.locator("#transcript").evaluate("(el) => el.scrollTop = 0")
    page.locator("#passage-1 [data-action='edit']").click()
    page.locator("#passage-1 textarea").fill("Det går rigtig godt, tak.")
    page.locator("#passage-1 [data-action='save-edit']").click()
    expect(page.locator("#passage-1 .passage-text")).to_have_text("Det går rigtig godt, tak.")
    expect(page.locator("#compare-passage-3 .passage-text")).to_have_text("Det går rigtig godt!")
    expect(page.locator("#comparison-info")).to_contain_text("Matching passages aligned")
    screenshot = Path("test-results/aligned-comparison.png")
    screenshot.parent.mkdir(exist_ok=True)
    page.screenshot(path=str(screenshot), full_page=True)
    page.locator("#transcript-search").fill("København")
    expect(page.locator("#comparison-info")).to_contain_text("Matching passages aligned")
    expect(page.locator("#transcript mark.search-match")).to_have_text("København")
    expect(page.locator("#comparison-transcript mark.search-match")).to_have_text("København")
    page.locator("#passage-0 mark.search-match").scroll_into_view_if_needed()
    page.screenshot(path="test-results/comparison-search.png", full_page=True)
    page.locator("#transcript-search").fill("Unrelated")
    expect(page.locator("#transcript .passage")).to_have_count(len(left + tail))
    expect(page.locator("#transcript mark.search-match")).to_have_count(0)
    expect(page.locator("#comparison-transcript mark.search-match")).to_have_text("Unrelated")
    page.locator("#transcript-search").fill("")
    expect(page.locator("#comparison-info")).to_contain_text("Matching passages aligned")
    expect(page.locator("mark.search-match")).to_have_count(0)
    page.locator("#passage-1 [data-action='edit']").click()
    page.locator("#passage-1 textarea").fill("Unsaved comparison correction.")
    page.locator("#transcript-search").fill("København")
    expect(page.locator("#passage-1 textarea")).to_have_value("Unsaved comparison correction.")
    expect(page.locator("#transcript mark.search-match")).to_have_text("København")
    expect(page.locator("#comparison-transcript mark.search-match")).to_have_text("København")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator("#compare-result").select_option("")
    expect(page.locator("#compare-result")).to_have_value(results[1])
    expect(page.locator("#passage-1 textarea")).to_have_value("Unsaved comparison correction.")
    page.locator("#passage-1 [data-action='cancel-edit']").click()
    page.locator("#transcript-search").fill("København")
    page.locator("#compare-result").select_option("")
    expect(page.locator("#comparison-panel")).to_be_hidden()
    expect(page.locator("#transcript .aligned-group")).to_have_count(0)
    expect(page.locator("#transcript .passage")).to_have_count(1)
    expect(page.locator("#transcript mark.search-match")).to_have_text("København")


def test_browser_enabling_follow_jumps_to_current_replay(page, local_app, wav_bytes):
    page.goto(local_app.url)
    create_project(page, "Follow current playback")
    import_recording(page, local_app, "follow.wav", wav_bytes)
    store = local_app.server.config.app.state.store
    recording_id = store.all("SELECT id FROM recordings")[0]["id"]
    segments = normalize_segments(
        [{"text": "Current passage for replay.", "start": 0, "end": 2.5}]
        + [{"text": f"Later passage number {i}.", "start": 2.5, "end": 3} for i in range(15)]
    )
    store.add_result(recording_id, segments, {}, "First method", {})
    other = store.add_result(recording_id, segments, {}, "Second method", {})
    page.reload()
    page.locator("#audio").evaluate("el => el.playbackRate = 0.25")
    try:
        for comparison in ("", other["id"]):
            page.locator("#compare-result").select_option(comparison)
            if comparison:
                expect(page.locator("#comparison-info")).to_contain_text(
                    "Matching passages aligned"
                )
            page.locator("#follow-playback").uncheck()
            page.locator("#passage-0 [data-action='replay']").click()
            page.wait_for_function("() => !document.querySelector('#audio').paused")
            page.locator("#transcript").evaluate("el => el.scrollTop = el.scrollHeight")
            page.wait_for_function("() => document.querySelector('#transcript').scrollTop > 400")
            page.locator("#follow-playback").check()
            expect(page.locator("#passage-0")).to_be_in_viewport(ratio=1, timeout=2000)
            assert not page.locator("#audio").evaluate("el => el.paused")
            assert page.locator("#audio").evaluate("el => el.currentTime") < 2.5
            page.locator("#play-pause").click()
    finally:
        page.locator("#audio").evaluate("el => el.pause()")


def test_browser_video_switch_and_compatible_transcription(page, local_app, tmp_path):
    requests = []

    class FakeProvider(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((self.path, body))
            response = json.dumps(
                {
                    "text": "Hello CBS.",
                    "segments": [{"text": "Hello CBS.", "start": 0, "end": 3}],
                    "words": [
                        {"word": "Hello", "start": 0, "end": 1},
                        {"word": "CBS", "start": 1, "end": 3},
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args):
            pass

    provider = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    try:
        path = tmp_path / "research.mp4"
        ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "color=c=0x183e37:s=320x240:r=15:d=3",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=44100:duration=3",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(path),
            ]
        )
        page.goto(local_app.url)
        create_project(page, "Video research")
        import_recording(page, local_app, path.name, path.read_bytes())
        app = local_app.server.config.app
        saved = save_profile(
            app.state.store,
            app.state.credentials,
            "Synthetic word timing",
            Settings(
                provider_type="openai",
                base_url=f"http://127.0.0.1:{provider.server_port}/v1",
                model="whisper-1",
                capability="words",
            ),
            identifier=default_provider_id(app.state.store),
        )
        page.reload()
        page.locator("#file-provider-kind").select_option("openai")
        page.locator("#file-provider").select_option(saved["id"])
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#transcribe-one").click()
        expect(page.locator("#passage-0 .passage-text")).to_have_text("Hello CBS.", timeout=30000)
        page.locator("#activity-close").click()
        page.locator("#playback-speed").select_option("1.5")
        page.locator("#passage-0 [data-word='1']").click()
        page.locator("#view-mode").select_option("video")
        expect(page.locator("#video-stage")).to_be_visible()
        assert abs(page.locator("#video").evaluate("e => e.currentTime") - 1) < 0.2
        assert page.locator("#video").evaluate("e => e.playbackRate") == 1.5
        page.get_by_role("button", name="Play", exact=True).click()
        page.locator("#view-mode").select_option("audio")
        expect(page.locator("#video-stage")).to_be_hidden()
        assert page.locator("#audio").evaluate("e => !e.paused")
        page.get_by_role("button", name="Pause", exact=True).click()
        assert requests[0][0] == "/v1/audio/transcriptions"
        assert b"verbose_json" in requests[0][1]
    finally:
        provider.shutdown()
        provider.server_close()


def test_browser_saved_providers_and_comparison(page, local_app, wav_bytes, monkeypatch):
    requests = []
    store = local_app.server.config.app.state.store
    execute = store.execute

    def delayed_preference(sql, params=()):
        if sql.startswith("UPDATE recordings SET provider_id"):
            time.sleep(0.4)  # A quick Transcribe click must use the visible selection.
        return execute(sql, params)

    monkeypatch.setattr(store, "execute", delayed_preference)

    class ComparisonProvider(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            speakers = b"gpt-4o-transcribe-diarize" in body
            requests.append("speakers" if speakers else "words")
            if speakers:
                payload = {
                    "text": "Hej fra A. Svar fra B.",
                    "segments": [
                        {"text": "Hej fra A.", "start": 0, "end": 1.5, "speaker": "A"},
                        {"text": "Svar fra B.", "start": 1.5, "end": 3, "speaker": "B"},
                    ],
                }
            else:
                payload = {
                    "text": "Hej CBS.",
                    "segments": [{"text": "Hej CBS.", "start": 0, "end": 3}],
                    "words": [
                        {"word": "Hej", "start": 0, "end": 1},
                        {"word": "CBS", "start": 1, "end": 3},
                    ],
                }
            encoded = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    provider = ThreadingHTTPServer(("127.0.0.1", 0), ComparisonProvider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        app = local_app.server.config.app
        words = save_profile(
            store,
            app.state.credentials,
            "Danish words",
            Settings(
                provider_type="openai",
                base_url=f"http://127.0.0.1:{provider.server_port}/v1",
                model="whisper-1",
                capability="words",
            ),
            identifier=default_provider_id(store),
        )
        speakers = next(
            p
            for p in public_profiles(store, app.state.credentials)["providers"]
            if p["model"] == "gpt-4o-transcribe-diarize"
        )
        words_id, speakers_id = words["id"], speakers["id"]
        page.goto(local_app.url)
        create_project(page, "Provider comparison")
        import_recording(page, local_app, "comparison.wav", wav_bytes)
        page.locator("#back-library").click()
        page.locator("#bulk-provider-kind").select_option("openai")
        expect(page.locator("#bulk-provider option")).to_have_count(5)
        page.locator("#bulk-provider").select_option(words_id)
        page.locator("#select-all").click()
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#transcribe-selected").click()
        expect(page.locator(".recording-card .badge")).to_have_text(
            "Transcript ready", timeout=30000
        )
        page.locator("#activity-close").click()
        page.get_by_role("button", name="comparison", exact=True).click()
        expect(page.locator("#file-provider-kind")).to_have_value("openai")
        with page.expect_response(
            lambda response: (
                response.url.endswith("/provider") and response.request.method == "PATCH"
            )
        ):
            page.locator("#file-provider").select_option(speakers_id)
        page.locator("#back-library").click()
        expect(page.locator("[data-select]:checked")).to_have_count(0)
        page.locator("#select-all").click()
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#bulk-provider").select_option(speakers_id)
        page.locator("#transcribe-selected").click()
        expect(page.locator(".card-results")).to_have_text("2 results", timeout=30000)
        page.locator("#activity-close").click()
        page.get_by_role("button", name="comparison", exact=True).click()
        expect(page.locator("#result-select option")).to_have_count(2)
        expect(page.locator("#file-provider")).to_have_value(speakers_id)
        result_ids = page.locator("#result-select option").evaluate_all(
            "nodes => nodes.map(n => n.value)"
        )
        page.locator("#compare-result").select_option(result_ids[1])
        expect(page.locator("#comparison-panel")).to_be_visible()
        expect(page.locator("#comparison-transcript")).to_contain_text("Svar fra B.")
        expect(page.locator("#comparison-transcript textarea")).to_have_count(0)
        page.evaluate("window.searchWord = document.querySelector('#passage-0 [data-word]')")
        page.locator("#transcript-search").fill("hej")
        assert page.evaluate(
            "window.searchWord === document.querySelector('#passage-0 [data-word]')"
        )
        expect(page.locator("#passage-0 [data-word='0'] mark.search-match")).to_have_text("Hej")
        expect(page.locator("#comparison-transcript mark.search-match")).to_have_text("Hej")
        page.locator("#passage-0 [data-word='1']").click()
        expect(page.locator("#compare-passage-0")).to_have_class("passage active")
        page.locator("#transcript-search").fill("")
        page.locator("#playback-speed").select_option("1.5")
        position = page.locator("#audio").evaluate("e => e.currentTime")
        page.locator("#result-select").select_option(result_ids[1])
        expect(page.locator("#transcript")).to_contain_text("Svar fra B.")
        assert abs(page.locator("#audio").evaluate("e => e.currentTime") - position) < 0.2
        assert page.locator("#audio").evaluate("e => e.playbackRate") == 1.5
        page.locator("#compare-result").select_option(result_ids[0])
        expect(page.locator("#comparison-transcript")).to_contain_text("Hej CBS.")
        page.locator("#passage-0 [data-action='edit']").click()
        page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
            "button", name=__import__("re").compile("Keep current Working|Leave unassigned")
        ).click()
        page.locator("#passage-0 textarea").fill("Reviewed speaker transcript.")
        page.locator("#passage-0 [data-action='save-edit']").click()
        expect(page.locator("#passage-0 .passage-text")).to_have_text(
            "Reviewed speaker transcript."
        )
        expect(page.locator("#comparison-transcript")).to_contain_text("Hej CBS.")
        screenshot = Path("test-results/provider-comparison.png")
        screenshot.parent.mkdir(exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        page.locator("#export-open").click()
        with page.expect_download() as download:
            page.locator("#export-link").click()
        assert "Reviewed speaker transcript." in Path(download.value.path()).read_text(
            encoding="utf-8"
        )
        page.get_by_role("button", name="Close export settings").click()
        # Select another provider to create another result; compare with the saved-result dropdown.
        expect(page.get_by_role("button", name="Compare providers…")).to_have_count(0)
        for count, provider_id in enumerate((words_id, speakers_id), 3):
            page.locator("#file-provider").select_option(provider_id)
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator("#transcribe-one").click()
            expect(page.locator("#result-select option")).to_have_count(count, timeout=30000)
            page.locator("#activity-close").click()
        expect(page.locator("#passage-0 .passage-text")).to_have_text(
            "Reviewed speaker transcript."
        )
        url = page.url
        local_app.stop()
        local_app.start()
        page.goto(local_app.url + url[url.index("/#") :])
        expect(page.locator("#result-select option")).to_have_count(4)
        expect(page.locator("#passage-0 .passage-text")).to_have_text(
            "Reviewed speaker transcript."
        )
        expect(page.locator("#file-provider")).to_have_value(speakers_id)
        assert sorted(requests) == ["speakers", "speakers", "words", "words"]
        assert not errors
    finally:
        provider.shutdown()
        provider.server_close()


def test_browser_project_heading_rename_preserves_drafts_and_ignores_late_reply(page, local_app):
    store = local_app.server.config.app.state.store
    first = store.create_project("First project")
    second = store.create_project("Second project")
    page.goto(local_app.url + f"/#/projects/{first['id']}")
    expect(page.locator("#project-edit-name")).to_have_count(1)
    expect(page.locator(".project-title-row #project-rename")).to_be_visible()
    page.locator("#project-rename").click()
    expect(page.locator("#name-title")).to_have_text("Rename project")
    expect(page.locator("#name-input")).to_have_value("First project")
    expect(page.locator("#new-project-fields")).not_to_be_visible()
    page.locator("#name-input").fill("Cancelled name")
    page.locator("#name-dialog").get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#project-title")).to_have_text("First project")
    open_project_settings(page)
    page.locator("#project-edit-classification").select_option("protected")
    page.locator("#project-settings-save").click()
    expect(page.locator("#project-settings-dialog")).not_to_be_visible()
    page.locator("#project-notes-panel summary").click()
    page.locator("#project-notes").fill("Retain these unsaved notes")
    page.locator("#project-rename").click()
    page.locator("#name-input").fill("Renamed æøå")
    page.locator("#name-form").get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#project-title")).to_have_text("Renamed æøå")
    expect(page.locator("#breadcrumb")).to_have_count(0)
    expect(page.locator("#projects")).to_contain_text("Renamed æøå")
    expect(page.locator("#project-edit-classification")).to_have_value("protected")
    expect(page.locator("#project-notes")).to_have_value("Retain these unsaved notes")
    assert (
        store.one("SELECT classification FROM projects WHERE id=?", (first["id"],))[
            "classification"
        ]
        == "protected"
    )
    open_project_settings(page)
    page.locator("#project-edit-classification").select_option("standard")
    page.locator("#project-settings-save").click()
    expect(page.locator("#project-settings-dialog")).not_to_be_visible()
    page.locator("#project-notes").fill("")
    held = []

    def hold_rename(route):
        held.append((route, route.fetch()))
        page.evaluate("window.projectRenameHeld = true")

    page.route(f"**/api/projects/{first['id']}", hold_rename)
    page.locator("#project-rename").click()
    page.locator("#name-input").fill("Late rename")
    page.locator("#name-form").get_by_role("button", name="Save", exact=True).click()
    page.wait_for_function("() => window.projectRenameHeld === true")
    expect(page.locator("#project-rename")).to_be_disabled()
    page.locator(f"#projects a[href='#/projects/{second['id']}']").click()
    expect(page.locator("#project-title")).to_have_text("Second project")
    page.locator("#project-notes-panel").evaluate("e => { e.open = true; }")
    page.locator("#project-notes").fill("Second project notes draft")
    route, response = held.pop()
    route.fulfill(response=response)
    expect(page.locator("#projects")).to_contain_text("Late rename")
    expect(page.locator("#project-title")).to_have_text("Second project")
    expect(page.locator("#project-notes")).to_have_value("Second project notes draft")
    expect(page.locator("#project-rename")).to_be_enabled()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator(f"#projects a[href='#/projects/{first['id']}']").click()
    expect(page.locator("#project-title")).to_have_text("Late rename")
    page.unroute(f"**/api/projects/{first['id']}", hold_rename)
    page.route(
        f"**/api/projects/{first['id']}",
        lambda route: route.fulfill(status=503, json={"detail": "Could not save this name"}),
    )
    page.locator("#project-rename").click()
    page.locator("#name-input").fill("Failed rename")
    page.locator("#name-form").get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#toast")).to_have_text("Could not save this name")
    expect(page.locator("#project-rename")).to_be_enabled()
    expect(page.locator("#project-edit-classification")).to_be_enabled()
    expect(page.locator("#project-title")).to_have_text("Late rename")
    page.reload()
    expect(page.locator("#project-title")).to_have_text("Late rename")
    page.screenshot(path="test-results/project-heading-rename.png")


def test_browser_project_settings_keep_drafts_and_ignore_late_replies(page, local_app):
    store = local_app.server.config.app.state.store
    first = store.create_project("First project")
    second = store.create_project("Second project")
    page.goto(local_app.url + f"/#/projects/{first['id']}")
    for slogan in (
        "Local workspace",
        "On your computer",
        "Original files are preserved in your local library.",
    ):
        expect(page.get_by_text(slogan, exact=False)).to_have_count(0)
    expect(page.locator("#upload-status")).to_have_attribute("role", "status")
    expect(page.locator("#project-edit-name")).to_have_count(1)
    expect(page.locator("#project-settings-save")).to_have_count(1)
    held = []

    def hold_save(route):
        held.append((route, route.fetch()))
        page.evaluate("window.projectSaveHeld = true")

    page.route(f"**/api/projects/{first['id']}/classification", hold_save)
    # Changing classification is a draft until Save; a pending save cannot be cancelled.
    open_project_settings(page)
    page.locator("#project-edit-classification").select_option("protected")
    assert not held
    page.locator("#project-settings-save").click()
    page.wait_for_function("() => window.projectSaveHeld === true")
    expect(page.locator("#project-edit-classification")).to_be_disabled()
    expect(page.locator("#project-settings-cancel")).to_be_disabled()
    route, response = held.pop()
    route.fulfill(response=response)
    expect(page.locator("#project-settings-dialog")).not_to_be_visible()
    page.locator(f"#projects a[href='#/projects/{second['id']}']").click()
    expect(page.locator("#project-title")).to_have_text("Second project")
    page.locator("#project-notes-panel").evaluate("e => { e.open = true; }")
    page.locator("#project-notes").fill("Second project notes draft")
    expect(page.locator("#project-title")).to_have_text("Second project")
    expect(page.locator("#project-edit-classification")).to_have_value("standard")
    expect(page.locator("#project-notes")).to_have_value("Second project notes draft")
    assert store.one("SELECT classification FROM projects WHERE id=?", (first["id"],))[
        "classification"
    ] == ("protected")
    page.locator("#project-notes").fill("")
    held_browse = []

    def hold_browse(route):
        held_browse.append(route)
        page.evaluate("window.projectBrowseHeld = true")

    page.route("**/api/storage/browse", hold_browse)
    open_project_settings(page)
    page.locator("#browse-project-edit-storage").click()
    page.wait_for_function("() => window.projectBrowseHeld === true")
    close_project_settings(page)
    page.locator(f"#projects a[href='#/projects/{first['id']}']").click()
    expect(page.locator("#project-title")).to_have_text("First project")
    first_path = page.locator("#project-edit-storage").input_value()
    dialogs = []

    def dismiss(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()

    page.on("dialog", dismiss)
    held_browse.pop().fulfill(json={"path": str(local_app.root / "wrong-project")})
    expect(page.locator("#project-edit-storage")).to_have_value(first_path)
    expect(page.locator("#browse-project-edit-storage")).to_be_enabled()
    assert not dialogs
    # A declined move confirmation keeps the current folder.
    open_project_settings(page)
    page.locator("#project-edit-storage").fill(str(local_app.root / "declined-parent"))
    page.locator("#project-settings-save").click()
    expect(page.locator("#project-edit-storage")).to_have_value(
        str(local_app.root / "declined-parent")
    )
    assert len(dialogs) == 1 and "Move this project's files" in dialogs[0]
    assert not (local_app.root / "declined-parent").exists()
    page.remove_listener("dialog", dismiss)
    page.evaluate("window.projectSaveHeld = false")
    page.locator("#project-edit-storage").fill(first_path)
    page.locator("#project-edit-classification").select_option("standard")
    page.locator("#project-settings-save").click()
    page.wait_for_function("() => window.projectSaveHeld === true")
    expect(page.locator("#project-settings-cancel")).to_be_disabled()
    route, response = held.pop()
    route.fulfill(response=response)
    expect(page.locator("#project-settings-dialog")).not_to_be_visible()
    page.locator(f"#projects a[href='#/projects/{second['id']}']").click()
    expect(page.locator("#project-title")).to_have_text("Second project")
    page.locator(f"#projects a[href='#/projects/{first['id']}']").click()
    expect(page.locator("#project-edit-classification")).to_be_enabled()
    page.locator("#project-notes-panel").evaluate("e => { e.open = true; }")
    page.locator("#project-notes").fill("New notes while classification save finishes")
    expect(page.locator("#project-edit-classification")).to_be_enabled()
    expect(page.locator("#project-edit-classification")).to_have_value("standard")
    expect(page.locator("#project-notes")).to_have_value(
        "New notes while classification save finishes"
    )
    expect(page.locator("#project-notes-status")).to_have_text("Unsaved notes")
    page.locator("#project-notes").fill("")
    page.locator("#project-notes-panel summary").click()
    page.set_viewport_size({"width": 780, "height": 900})
    open_project_settings(page)
    form = page.locator("#project-settings-form").bounding_box()
    for element in page.locator(
        "#project-settings-fields input, #project-settings-fields select, "
        "#project-settings-fields button"
    ).all():
        bounds = element.bounding_box()
        assert bounds["x"] >= form["x"]
        assert bounds["x"] + bounds["width"] <= form["x"] + form["width"] + 1
    screenshot = Path("test-results/inline-project-settings.png")
    screenshot.parent.mkdir(exist_ok=True)
    page.screenshot(path=str(screenshot), full_page=True)


def test_browser_project_notes_save_independently_and_preserve_stale_draft(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Notes project")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#project-notes-panel summary").click()
    page.locator("#project-notes").fill("Synthetic Danish notes: æøå\nSecond line")
    page.locator("#project-notes-save").click()
    expect(page.locator("#project-notes-status")).to_have_text("All notes saved")
    expect(page.locator("#project-edit-classification")).to_have_value("standard")
    saved = store.one("SELECT * FROM projects WHERE id=?", (project["id"],))
    assert saved["name"] == "Notes project"
    assert saved["classification"] == "standard"
    assert saved["notes"] == "Synthetic Danish notes: æøå\nSecond line"
    page.reload()
    expect(page.locator("#project-notes")).to_have_value(saved["notes"])
    page.locator("#project-notes-panel summary").click()
    with httpx.Client(base_url=local_app.url, headers={"X-CBS-Request": "1"}) as client:
        response = client.patch(
            f"/api/projects/{project['id']}/notes",
            json={"text": "Another saved revision", "revision": saved["notes_revision"]},
        )
        assert response.status_code == 200
    page.locator("#project-notes").fill("Retain my local notes draft")
    page.locator("#project-notes-save").click()
    expect(page.locator("#project-notes-status")).not_to_have_text("Saving notes…")
    expect(page.locator("#toast")).to_have_class("toast error")
    expect(page.locator("#project-notes")).to_have_value("Retain my local notes draft")
    expect(page.locator("#project-notes")).to_be_enabled()
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator(".brand").click()
    expect(page.locator("#project-notes")).to_have_value("Retain my local notes draft")


def test_browser_provider_tabs_project_storage_and_policy(
    page,
    local_app,
    wav_bytes,
    monkeypatch,
):
    checked = []
    parent = local_app.root.parent / "Chosen projects"
    monkeypatch.setattr("cbs_transcriber.storage.choose_folder", lambda: str(parent))

    def model_list(settings, key):
        checked.append((settings.base_url, key))
        return {
            "models": [
                {"id": "whisper-1", "name": "Whisper", "listed": True},
                {"id": "gpt-transcribe", "name": "GPT Transcribe", "listed": False},
            ],
            "note": "No audio uploaded. Visibility does not verify billing.",
        }

    monkeypatch.setattr("cbs_transcriber.app.check_models", model_list)
    page.goto(local_app.url)
    expect(page.locator("#bulk-provider-kind")).to_have_value("openai")
    expect(page.locator("#bulk-provider")).to_contain_text("gpt-transcribe")
    page.locator("#settings-open").click()
    expect(page.get_by_role("tab")).to_have_count(4)
    expect(page.locator("#provider-preset")).not_to_be_visible()
    expect(page.locator("#provider-advanced")).not_to_have_attribute("open", "")
    expect(page.locator("#check-models")).to_be_visible()
    page.locator("#api-key").fill("draft-synthetic-key")
    page.locator("#check-models").click()
    expect(page.locator("#model-check-results")).to_be_visible()
    expect(page.locator("#model-check-results")).to_contain_text("Not listed")
    assert checked == [("https://api.openai.com/v1", "draft-synthetic-key")]
    assert not local_app.server.config.app.state.credentials.keys
    page.locator("#api-key").fill("")
    expect(page.locator("#model-check-results")).to_be_empty()
    provider_advanced(page)
    choices = page.locator("#provider-model-options [data-enabled-model]")
    expect(choices).to_have_count(5)
    for name in (
        "OpenAI gpt-transcribe",
        "OpenAI gpt-4o-transcribe",
        "OpenAI gpt-4o-mini-transcribe",
        "OpenAI gpt-4o-transcribe-diarize (Speaker Labels)",
        "OpenAI whisper-1 (Word timing)",
    ):
        page.get_by_role("checkbox", name=name, exact=True).check()
    page.get_by_role("checkbox", name="OpenAI gpt-4o-mini-transcribe", exact=True).uncheck()
    page.get_by_role("button", name="Save settings").click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    expect(page.locator("#bulk-provider option")).to_have_count(4)
    expect(page.locator("#bulk-provider")).not_to_contain_text("gpt-4o-mini-transcribe")
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    expect(page.locator("#ucloud-panel")).to_contain_text("Transcriber Batch")
    expect(page.locator("#provider-name")).to_be_disabled()
    expect(page.locator("#provider-name")).not_to_be_visible()
    expect(page.locator("#base-url")).to_have_value("https://cloud.sdu.dk")
    expect(page.get_by_role("button", name="Save settings")).to_be_visible()
    page.get_by_role("tab", name="UCloud", exact=True).press("Home")
    expect(page.get_by_role("tab", name="OpenAI API")).to_be_focused()
    expect(page.locator("#provider-fields")).to_be_visible()
    screenshot = Path("test-results/provider-settings.png")
    screenshot.parent.mkdir(exist_ok=True)
    page.screenshot(path=str(screenshot))
    provider_advanced(page)
    page.locator('[name="approved-for"][value="protected"]').check()
    page.get_by_role("button", name="Save settings").click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    page.locator("#sidebar-create").click()
    page.locator("#name-input").fill("Protected research")
    page.locator("#project-classification").select_option("protected")
    page.locator("#browse-project-storage").click()
    expect(page.locator("#project-storage")).to_have_value(str(parent))
    page.locator("#name-form").get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#project-title")).to_have_text("Protected research")
    expect(page.locator("#project-rename")).to_be_visible()
    expect(page.locator("#bulk-provider option")).to_have_count(1)
    expect(page.locator("#bulk-provider")).to_contain_text("gpt-transcribe")
    import_recording(page, local_app, "protected.wav", wav_bytes)
    expect(page.locator("#file-provider option")).to_have_count(1)
    expect(page.locator("#original-link")).to_have_attribute(
        "href",
        f"/api/recordings/{page.url.rsplit('/', 1)[-1]}/media/original"
        f"?cbs_instance={local_app.server.config.app.state.browser_instance}",
    )
    # The original download lives in File Settings (sidebar), not in the editor header.
    expect(page.locator("#original-link")).to_be_hidden()
    page.locator("#file-settings-open").click()
    expect(page.locator("#file-settings-dialog #original-link")).to_be_visible()
    page.get_by_role("button", name="Close file settings", exact=True).click()
    expect(page.locator("#file-settings-dialog")).to_be_hidden()
    page.locator("#back-library").click()
    expect(page.locator("#project-settings")).to_have_count(0)
    open_project_settings(page)
    expect(page.locator("#project-settings-form")).to_be_visible()
    expect(page.locator("#project-edit-storage")).to_have_value(str(next(parent.iterdir())))
    expect(page.locator("#project-edit-classification")).to_have_value("protected")
    moved_parent = parent.parent / "Moved projects"
    monkeypatch.setattr("cbs_transcriber.storage.choose_folder", lambda: str(moved_parent))
    expect(page.locator("#browse-project-edit-storage")).to_be_enabled()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#browse-project-edit-storage").click()
    expect(page.locator("#project-edit-note")).to_have_text("Unsaved project settings")
    assert not moved_parent.exists()
    page.locator("#project-settings-save").click()
    expect(page.locator("#project-settings-dialog")).to_be_hidden()
    page.locator("#project-rename").click()
    page.locator("#name-input").fill("Protected research renamed")
    page.locator("#name-form").get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#project-title")).to_have_text("Protected research renamed")
    moved_folder = next(moved_parent.iterdir())
    open_project_settings(page)
    expect(page.locator("#project-edit-storage")).to_have_value(str(moved_folder))
    expect(page.locator("#project-edit-note")).to_contain_text("Save to apply changes")
    close_project_settings(page)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="OpenAI API", exact=True).click()
    provider_advanced(page)
    page.locator('[name="approved-for"][value="protected"]').uncheck()
    page.get_by_role("button", name="Save settings").click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    expect(page.locator("#bulk-provider")).to_contain_text("No approved providers")
    page.locator("#select-all").click()
    expect(page.locator("#transcribe-selected")).to_be_disabled()
    assert (moved_folder / "project.json").exists()
    assert not list(parent.iterdir())
    with httpx.Client(base_url=local_app.url, headers={"X-CBS-Request": "1"}) as client:
        response = client.post(
            "/api/providers",
            json={
                "name": "OpenAI · Speaker labels",
                "provider_type": "openai",
                "model": "gpt-4o-transcribe-diarize",
            },
        )
        assert response.status_code == 201
    page.goto(local_app.url)
    expect(page.locator("#project-title")).to_have_text("Protected research renamed")
    expect(page.locator("#bulk-provider")).to_contain_text("No approved providers")
    create_project(page, "Standard model labels")
    page.locator("#bulk-provider-kind").select_option("openai")
    expect(page.locator("#bulk-provider")).to_contain_text(
        "gpt-4o-transcribe-diarize (Speaker Labels)"
    )
    expect(page.locator('#bulk-provider-kind option[value="custom"]')).to_have_count(0)
    expect(page.locator('#bulk-provider-kind option[value="ucloud-vllm"]')).to_have_count(0)


def test_browser_native_ucloud_setup_and_transcription(page, local_app, wav_bytes, monkeypatch):
    from cbs_transcriber import ucloud_native
    from cbs_transcriber.transcripts import normalize_segments

    checked, executed = [], []

    def check_setup(settings, key):
        checked.append((settings.ucloud_transcriber.template_job_id, key))
        return {
            "application": dict(ucloud_native.MIG_APPLICATION),
            "product": dict(ucloud_native.MIG_PRODUCT),
            "parameters": [],
            "note": "Template accessible. No audio uploaded or job started.",
        }

    def run(worker, job, recording, settings, key):
        executed.append((settings.provider_type, key))
        assert settings.ucloud_transcriber.project_id == "synthetic-project"
        params = job["params"]
        params["ucloud_run"] = {"job_id": "synthetic-remote-job"}
        worker.store.update_job(job["id"], params=params)
        worker.store.add_result(
            recording["id"],
            normalize_segments(
                [
                    {
                        "start": 0,
                        "end": 1,
                        "text": "Hej CBS.",
                        "speaker": "SPEAKER_00",
                        "words": [
                            {"word": "Hej", "start": 0, "end": 0.4},
                            {"word": "CBS.", "start": 0.4, "end": 1},
                        ],
                    }
                ]
            ),
            {},
            job["params"]["provider_name"],
            {
                "kind": "provider",
                **settings.model_dump(),
            },
            job_id=job["id"],
        )

    monkeypatch.setattr("cbs_transcriber.ucloud_native.check_setup", check_setup)
    monkeypatch.setattr("cbs_transcriber.ucloud_native.run_transcription", run)

    def discover(settings, key, *, compute_id=None):
        setup = settings.ucloud_transcriber.model_dump()
        setup.update(
            application=dict(ucloud_native.MIG_APPLICATION),
            product=dict(ucloud_native.MIG_PRODUCT),
        )
        return {"ready": True, "setup": setup, "choices": {}, "note": "Ready to save."}

    monkeypatch.setattr("cbs_transcriber.ucloud_discovery.discover", discover)
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    provider_advanced(page)
    page.locator("#native-project").fill("synthetic-project")
    page.locator("#native-template").fill("synthetic-template")
    page.locator("#native-storage").fill("/synthetic-drive/recordings")
    page.locator("#api-key").fill("synthetic-ucloud-token")
    page.locator("#remember-key").uncheck()
    page.locator("#check-ucloud").click()
    expect(page.locator("#ucloud-check-results")).to_contain_text("No audio uploaded")
    assert not executed
    assert checked == [("synthetic-template", "synthetic-ucloud-token")]
    assert not local_app.server.config.app.state.credentials.keys
    screenshot = Path("test-results/ucloud-native-settings.png")
    page.screenshot(path=str(screenshot))
    # Inspecting a legacy template is read-only; fresh requests reconnect to the
    # fixed MIG without inheriting that template's allocation.
    page.locator("#native-template").fill("")
    page.locator("#connect-ucloud").click()
    expect(page.locator("#ucloud-check-results")).to_contain_text("Ready to save")
    page.get_by_role("button", name="Save settings").click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    create_project(page, "Native UCloud")
    import_recording(page, local_app, "native.wav", wav_bytes)
    with httpx.Client(base_url=local_app.url) as client:
        providers = client.get("/api/providers").json()["providers"]
        native = next(
            p for p in providers if p["provider_type"] == "ucloud" and p.get("enabled", True)
        )
    page.locator("#file-provider-kind").select_option("ucloud")
    page.locator("#file-provider").select_option(native["id"])
    page.on("dialog", lambda dialog: dialog.accept())
    with page.expect_response(
        lambda response: response.url.endswith("/api/transcriptions")
    ) as admitted:
        page.locator("#transcribe-one").click()
    assert admitted.value.status == 202, admitted.value.json()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Hej CBS.", timeout=15000)
    expect(page.get_by_role("link", name="Open UCloud job")).to_have_attribute(
        "href",
        "https://cloud.sdu.dk/app/jobs/properties/synthetic-remote-job",
    )
    assert executed == [("ucloud", "synthetic-ucloud-token")]
    page.locator("#activity-close").click()
    expect(page.locator("#timing-badge")).to_have_text("Word timing")
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    expect(page.locator("#api-key")).to_have_value("")
    expect(page.locator("#native-template")).to_have_value("")


def open_processing(page):
    """Provider Settings → Processing (ADR 0059): checked when opened, saved on change."""
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="Local Processing", exact=True).click()
    expect(page.locator("#processing-panel")).to_be_visible()


def test_browser_local_enhancement_keeps_source_and_saves_new_result(
    page, local_app, wav_bytes, monkeypatch
):
    from cbs_transcriber.enhancement_worker import write_json

    calls, asked = [], []
    monkeypatch.setattr(
        "cbs_transcriber.enhancement.check_runtime",
        lambda *args, **kw: {
            "ready": True,
            "version": "3.8.6",
            "expected_version": "3.8.6",
            "note": "Synthetic runtime check.",
        },
    )

    def enhance(folder, options, token, cancel, progress):
        assert not options.allow_downloads and options.device == "cpu"
        calls.append((options.align, options.diarize, token))
        write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": "0",
                        "words": [
                            {"word": "Hej", "start": 0, "end": 1, "score": 0.95},
                            {"word": "København.", "start": 1, "end": 3, "score": 0.95},
                        ],
                    }
                ]
            },
        )
        write_json(
            folder / "diarization.json",
            {
                "turns": [
                    {"start": 0, "end": 1, "speaker": "A"},
                    {"start": 1, "end": 3, "speaker": "B"},
                ]
            },
        )

    monkeypatch.setattr("cbs_transcriber.enhancement.run_worker", enhance)
    page.goto(local_app.url)
    page.locator("#sidebar-create").click()
    page.locator("#name-input").fill("Local protected enhancement")
    page.locator("#project-classification").select_option("protected")
    page.locator("#name-form").get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#project-title")).to_have_text("Local protected enhancement")
    import_recording(page, local_app, "enhance.wav", wav_bytes)
    page.locator("#transcript-file").set_input_files(
        {
            "name": "source.json",
            "mimeType": "application/json",
            "buffer": json.dumps(
                {"segments": [{"text": "Hej København.", "start": 0, "end": 3}]}
            ).encode(),
        }
    )
    expect(page.locator("#timing-badge")).to_have_text("Passage timing")
    original_id = page.locator("#result-select").input_value()
    open_processing(page)
    expect(page.locator("#enhance-downloads")).to_be_checked()
    page.locator("#enhance-downloads").uncheck()
    expect(page.locator("#processing-status")).to_have_text("Saved for new jobs.")
    page.locator("#enhance-check").click()
    expect(page.locator("#processing-local-status")).to_contain_text(
        "WhisperX 3.8.6 is ready on this computer"
    )
    screenshot = Path("test-results/local-enhancement-settings.png")
    screenshot.parent.mkdir(exist_ok=True)
    page.screenshot(path=str(screenshot))
    page.get_by_role("button", name="Close settings", exact=True).click()
    local_app.server.config.app.state.credentials.set(
        "huggingface:https://huggingface.co", "synthetic-browser-hf-key", True
    )
    monkeypatch.setattr(
        "cbs_transcriber.enhancement.check_runtime", lambda *args, **kw: {"ready": False}
    )

    def accept(dialog):
        asked.append(dialog.message)
        dialog.accept()

    page.on("dialog", accept)
    # The result has no speakers: one tag adds them (and word timing) to the whole result.
    speaker_tag = page.locator("#transcript-tags #speaker-tag")
    expect(speaker_tag).to_have_text("Add speaker labels")
    speaker_tag.click()
    expect(page.locator("#toast")).to_contain_text("Queued")
    store = local_app.server.config.app.state.store
    deadline = time.monotonic() + 15
    failed = []
    while time.monotonic() < deadline:
        failed = store.all("SELECT id,state,params FROM jobs WHERE kind='enhance'")
        if len(failed) == 1 and failed[0]["state"] == "failed":
            break
        time.sleep(0.05)
    assert len(failed) == 1 and failed[0]["state"] == "failed"
    assert json.loads(failed[0]["params"])["input_intent"]["error"]["code"] == "preparation_failed"
    assert calls == []
    assert len(store.results(page.url.rsplit("/", 1)[-1])) == 1
    if page.locator("#activity").is_hidden():
        page.locator("#activity-open").click()
    expect(page.locator('#jobs [data-input-preparation="intent"]')).to_contain_text(
        "Check its processing settings."
    )
    page.locator("#activity-close").click()
    assert len(asked) == 1 and "this result is kept" in asked[0]
    expect(speaker_tag).to_be_visible()
    with httpx.Client(base_url=local_app.url) as client:
        [refused] = [job for job in client.get("/api/jobs").json() if job["kind"] == "enhance"]
        assert refused["id"] == failed[0]["id"]
        assert refused["input_preparation"]["error_code"] == "preparation_failed"
    monkeypatch.setattr(
        "cbs_transcriber.enhancement.check_runtime", lambda *args, **kw: {"ready": True}
    )
    speaker_tag.click()
    expect(page.locator("#toast")).to_contain_text("this result is kept")
    expect(page.locator("#enhancement-dialog")).not_to_be_visible()
    expect(page.locator("#result-select option")).to_have_count(2, timeout=15000)
    expect(page.locator("#result-select")).to_have_value(original_id)
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Hej København.")
    assert len(asked) == 2
    enhanced_id = page.locator("#result-select option", has_text="Local WhisperX").get_attribute(
        "value"
    )
    page.locator("#result-select").select_option(enhanced_id)
    expect(page.locator("#timing-badge")).to_have_text("Word timing")
    expect(page.locator("#transcript .passage")).to_have_count(2)
    texts = page.locator("#transcript .passage-text").evaluate_all(
        "nodes => nodes.map(n => n.textContent)"
    )
    assert "".join(texts) == "Hej København."
    page.locator("#passage-1 [data-word='0']").click()
    expect(page.locator("#passage-1 [data-word='0']")).to_have_class("word current")
    page.locator("#result-select").select_option(original_id)
    # The token stays in the credential store: Settings shows only that one is saved.
    open_processing(page)
    expect(page.locator("#enhance-downloads")).not_to_be_checked()
    page.get_by_role("tab", name="Hugging Face", exact=True).click()
    expect(page.locator("#hf-settings-status")).to_have_text("Token saved")
    expect(page.locator("#hf-settings-token")).to_have_value("")
    values = page.locator("input, textarea").evaluate_all("nodes => nodes.map(n => n.value)")
    assert not any("synthetic-browser-hf-key" in value for value in values)
    assert "synthetic-browser-hf-key" not in page.content()
    page.get_by_role("button", name="Close settings", exact=True).click()
    url = page.url
    local_app.stop()
    local_app.start()
    page.goto(local_app.url + url[url.index("/#") :])
    expect(page.locator("#result-select option")).to_have_count(2)
    expect(page.locator("#result-select")).to_have_value(original_id)
    assert calls == [(True, True, "synthetic-browser-hf-key")]


def test_browser_long_untimed_enhancement_shows_partial_timing(local_app, page, monkeypatch):
    from cbs_transcriber import enhancement

    # The synthetic worker models cached inference without gated-model download consent.
    enhancement.save_local_settings(
        local_app.server.config.app.state.store,
        enhancement.ProcessingSettings(download_models=False),
    )
    # Close the browser/range requests before its server during fixture teardown.
    # Buffer this long fixture's response to avoid Windows Proactor reset callbacks at shutdown.
    # Range semantics are exercised separately; preserve the server's actual audio bytes here.
    page.route(
        "**/api/recordings/*/media/audio", lambda route: route.fulfill(response=route.fetch())
    )
    monkeypatch.setattr(
        "cbs_transcriber.enhancement.check_runtime", lambda *args, **kw: {"ready": True}
    )
    from cbs_transcriber.enhancement_worker import write_json

    text = "Hej  København. Uforståeligt mellemrum. Farvel igen."

    def enhance(folder, options, token, cancel, progress):
        request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
        assert request["options"]["duration"] >= 90
        assert request["passages"][0]["text"] == text
        assert not options.allow_downloads
        write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": "0",
                        "words": [
                            {
                                "word": word,
                                "start": a,
                                "end": b,
                                "score": 0.95 if a is not None else None,
                            }
                            for word, a, b in zip(
                                text.split(),
                                [0.2, 0.6, None, None, 85, 85.5],
                                [0.6, 1, None, None, 85.5, 86],
                                strict=True,
                            )
                        ],
                    }
                ]
            },
        )
        # The tag adds speakers too; one voice keeps the timed/untimed passage split.
        assert options.align and options.diarize
        write_json(
            folder / "diarization.json", {"turns": [{"start": 0, "end": 90, "speaker": "A"}]}
        )

    monkeypatch.setattr("cbs_transcriber.enhancement.run_worker", enhance)
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x00\x00" * (16000 * 90))
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(local_app.url)
    create_project(page, "Long alignment")
    import_recording(page, local_app, "long.synthetic.wav", stream.getvalue())
    page.locator("#transcript-file").set_input_files(
        {
            "name": "untimed.json",
            "mimeType": "application/json",
            "buffer": json.dumps({"text": text}).encode(),
        }
    )
    expect(page.locator("#timing-badge")).to_have_text("Untimed")
    expect(page.locator("#result-select option")).to_have_count(1)
    expect(page.locator("#passage-0 .passage-text")).to_have_text(text)
    expect(page.locator("#passage-0 [data-action='replay']")).to_have_text("▷ Replay")
    original_id = page.locator("#result-select").input_value()
    # Untimed text offers no timing tags; the whole-result action is "Add speaker labels".
    expect(page.locator("#transcript-tags .timing-tag")).to_have_count(0)
    asked = []

    def accept(dialog):
        asked.append(dialog.message)
        dialog.accept()

    page.once("dialog", accept)
    page.locator("#transcript-tags #speaker-tag").click()
    expect(page.locator("#toast")).to_contain_text("this result is kept")
    assert len(asked) == 1 and "this result is kept" in asked[0]
    expect(page.locator("#enhancement-dialog")).to_be_hidden()
    expect(page.locator("#result-select option")).to_have_count(2, timeout=15000)
    expect(page.locator("#result-select")).to_have_value(original_id)
    expect(page.locator("#passage-0 .passage-text")).to_have_text(text)
    enhanced_id = page.locator(
        "#result-select option", has_text="partial word timing"
    ).get_attribute("value")
    page.locator("#result-select").select_option(enhanced_id)
    expect(page.locator("#transcript .passage")).to_have_count(3)
    # Every untimed passage offers the same tag; without its own bounds it is searched
    # between its timed neighbours (ADR 0058).
    expect(page.locator("#passage-1 .timing-tag button")).to_have_text("Add word timing")
    expect(page.locator("#passage-1 .timing-tag button")).to_have_attribute(
        "title", __import__("re").compile("No timestamps")
    )
    expect(page.locator("#passage-1 [data-action='replay']")).to_be_enabled()
    expect(page.locator("#passage-1 [data-action='replay']")).to_have_text("▷ Replay")
    expect(page.locator("#passage-1 [data-action='replay']")).to_have_attribute(
        "title",
        "This passage has no start/end timestamps. Plays the full recording; "
        "no timing is inferred for this passage.",
    )
    page.locator("#passage-1 [data-action='replay']").click()
    expect(page.locator("#play-pause")).to_have_attribute("aria-label", "Pause")
    assert 0 <= page.locator("#audio").evaluate("element => element.currentTime") < 1
    expect(page.locator("#passage-1")).not_to_have_class("passage timing-gap active")
    page.locator("#play-pause").click()
    assert "".join(page.locator("#transcript .passage-text").all_text_contents()) == text
    page.locator("#passage-2 [data-word='0']").click()
    expect(page.locator("#passage-2 [data-word='0']")).to_have_class("word current")
    screenshot = Path("test-results/long-partial-enhancement.png")
    screenshot.parent.mkdir(exist_ok=True)
    page.screenshot(path=str(screenshot))
    recording_id = page.url.rsplit("/", 1)[-1]
    with httpx.Client(base_url=local_app.url) as client:
        response = client.get(f"/api/recordings/{recording_id}/transcripts/{enhanced_id}")
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["segments"][1]["start"] is None
        assert result["segments"][1]["end"] is None
        assert result["replay_windows"] == {}
        assert result["source"]["word_coverage"] == {"total": 6, "timed": 4, "untimed": 2}
        assert (
            result["source"]["model_revisions"]["coarse"]["repo"] == "Systran/faster-whisper-small"
        )
    page.locator("#result-select").select_option(original_id)
    expect(page.locator("#transcript .passage")).to_have_count(1)
    expect(page.locator("#passage-0 .passage-text")).to_have_text(text)
    page.locator("#compare-result").select_option(enhanced_id)
    expect(page.locator("#comparison-transcript .passage")).to_have_count(3)
    page.locator("#compare-passage-2 .timestamp").click()
    expect(page.locator("#compare-passage-2 [data-word='0']")).to_have_class("word current")
    # A short finished overlapping passage must not hide a still-active wider passage.
    assert (
        page.evaluate("""() => segmentAt([
        {id: 'wide', start: 0, end: 10},
        {id: 'untimed', start: null, end: null},
        {id: 'short', start: 2, end: 3}], 5).id""")
        == "wide"
    )
    assert not errors


def test_browser_ucloud_enhancement_setup_and_separate_result(
    page, local_app, wav_bytes, monkeypatch
):
    from cbs_transcriber import enhancement, enhancement_api, providers, runtime_compute
    from cbs_transcriber.enhancement_worker import write_json
    from cbs_transcriber.providers import Settings, save_profile

    calls, checks, asked = [], [], []
    app = local_app.server.config.app
    enhancement.save_local_settings(
        app.state.store, enhancement.ProcessingSettings(prepare_ucloud_models=False)
    )
    # Opening Processing checks this computer; never run the real runtime/GPU check.
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    # Every read of the UCloud drive finds the runtime unprepared, without network access.
    monkeypatch.setattr(enhancement_api, "stamped", lambda *a: False)
    # This mocked run_api exercises the retained singleton workflow; shared batches have
    # separate transport tests and require real frozen Terminal metadata.
    monkeypatch.setattr("cbs_transcriber.runtime_batches.freeze_plan", lambda *_: None)
    monkeypatch.setattr(
        runtime_compute,
        "freeze",
        lambda *_: {
            "runtime_product": runtime_compute.MIG_PRODUCT,
            "runtime_compute_policy": runtime_compute.POLICY,
        },
    )
    save_profile(
        app.state.store,
        app.state.credentials,
        "UCloud",
        Settings(
            provider_type="ucloud",
            base_url="https://cloud.sdu.dk",
            model="large-v3",
            ucloud_transcriber={
                "project_id": "ucloud-project",
                "storage_path": "/drive/cbs",
                "application": {"name": "transcriber", "version": "1.12"},
                "product": {"id": "gpu-nvidia-b200-1-gpu", "category": "gpu", "provider": "ucloud"},
            },
        ),
        api_key="synthetic-cloud-key",
        remember=False,
    )

    def prepared(store, credentials):
        checks.append(True)
        return {"runtime_id": enhancement_api.runtime_id(), "prepared": False}

    monkeypatch.setattr(enhancement_api, "runtime_prepared", prepared)

    def execute(queue, job, recording, folder, options, validate):
        assert options.execution == "ucloud" and not options.allow_downloads
        assert options.device == "cuda" and options.prepare_runtime
        frozen = job["params"]["ucloud_api"]
        assert frozen["settings"]["ucloud_transcriber"]["project_id"] == "ucloud-project"
        assert frozen["prepare_runtime"] and "cloud_settings" not in job["params"]
        assert queue.credentials.get("ucloud-control:https://cloud.sdu.dk") == "synthetic-cloud-key"
        request = json.loads((folder / "request.json").read_text())
        assert {"runtime_python", "prepare_runtime"}.isdisjoint(request["options"])
        calls.append(job["params"]["source_snapshot"]["id"])
        job["params"]["ucloud_run"] = {
            "job_id": "synthetic-enhancement",
            "termination_requested": True,
        }
        queue.store.update_job(job["id"], params=job["params"])
        write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": "0",
                        "words": [
                            {"word": "Hej", "start": 0, "end": 1, "score": 0.95},
                            {"word": "København.", "start": 1, "end": 3, "score": 0.95},
                        ],
                    }
                ]
            },
        )
        validate()

    monkeypatch.setattr("cbs_transcriber.enhancement_api.run_api", execute)

    def transcribe(*args):
        return {"segments": [{"text": "Hej København.", "start": 0, "end": 3}]}

    monkeypatch.setattr(providers, "transcribe_file", transcribe)
    with httpx.Client(base_url=local_app.url, headers={"X-CBS-Request": "1"}) as client:
        response = client.post(
            "/api/providers",
            json={
                "name": "Synthetic passages",
                "provider_type": "openai",
                "base_url": "http://127.0.0.1:9/v1",
                "model": "synthetic-model",
                "capability": "segments",
            },
        )
        assert response.status_code == 201
        provider_id = response.json()["id"]
    page.goto(local_app.url)
    create_project(page, "Cloud enhancement")
    import_recording(page, local_app, "cloud-enhance.wav", wav_bytes)
    rid = page.url.rsplit("/", 1)[-1]
    # Provider Settings → UCloud shows the connection and checks the runtime read-only.
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    panel = page.locator("#runtime-preparation-panel")
    expect(page.locator("#enhance-cloud-connection")).to_contain_text("ucloud-project")
    expect(panel).not_to_contain_text("SSH template")
    expect(panel.locator("input[type=password]")).to_have_count(0)
    expect(page.locator("#enhance-cloud-check-result")).to_contain_text("not prepared yet")
    assert checks == [True]
    page.locator("#enhance-cloud-check").click()
    expect(page.locator("#enhance-cloud-check-result")).to_contain_text("not prepared yet")
    assert checks == [True, True]
    screenshot = Path("test-results/ucloud-enhancement-settings.png")
    screenshot.parent.mkdir(exist_ok=True)
    page.screenshot(path=str(screenshot))
    page.get_by_role("button", name="Close settings", exact=True).click()
    assert not app.state.store.all("SELECT id FROM jobs WHERE kind='enhance'")
    # Explicit UCloud processing is chosen in the transcription settings (ADR 0059).
    page.locator("#file-provider-kind").select_option("openai")
    page.locator("#file-provider").select_option(provider_id)
    page.locator('#file-features [data-transcription-feature="word_timing"]').check()
    page.locator("#transcribe-one").click()
    expect(page.locator("#enhancement-dialog")).to_be_visible()
    page.locator("#enhance-execution").select_option("ucloud")
    expect(page.locator("#enhancement-dialog input[type=password]")).to_have_count(0)
    expect(page.locator("#enhancement-dialog")).not_to_contain_text("SSH template")

    def accept(dialog):
        asked.append(dialog.message)
        dialog.accept()

    page.on("dialog", accept)
    page.locator("#enhance-start").click()
    expect(page.locator("#enhancement-dialog")).to_be_hidden()
    # Metadata admission defers preflight; no ASR/result is produced before explicit consent.
    if page.locator("#activity").is_hidden():
        page.locator("#activity-open").click()
    prepare = page.locator('#jobs [data-input-action="prepare"]')
    expect(prepare).to_be_visible(timeout=15000)
    assert len(asked) == 1 and calls == []
    assert app.state.store.results(rid) == []
    prepare.click()
    assert len(asked) == 2 and "pinned UCloud runtime" in asked[1]
    assert "1 frozen recording request" in asked[1]
    expect(page.locator("#result-select option")).to_have_count(2, timeout=15000)
    original = app.state.store.recording(rid)["active_transcript_id"]
    assert app.state.store.result(rid, original)["source"]["kind"] == "provider"
    expect(page.locator("#result-select")).to_have_value(original)
    expect(page.get_by_role("link", name="Open UCloud job")).to_have_attribute(
        "href", "https://cloud.sdu.dk/app/jobs/properties/synthetic-enhancement"
    )
    page.locator("#activity-close").click()
    enhanced = page.locator("#result-select option", has_text="UCloud WhisperX").get_attribute(
        "value"
    )
    page.locator("#result-select").select_option(enhanced)
    expect(page.locator("#timing-badge")).to_have_text("Word timing")
    assert calls == [original]


def test_browser_deferred_vllm_keeps_saved_results_and_frozen_jobs(page, local_app, wav_bytes):
    app = local_app.server.config.app
    store = app.state.store
    saved = save_profile(
        store,
        app.state.credentials,
        "Preserved vLLM model",
        Settings(
            provider_type="ucloud-vllm",
            base_url="http://127.0.0.1:9/v1",
            model="research/whisper-danish",
            capability="segments",
            approved_for=["protected"],
            ucloud={"hf_model": "research/whisper-danish", "hf_revision": "frozen-revision"},
        ),
        api_key="synthetic-serving-key",
        remember=False,
    )
    page.goto(local_app.url)
    create_project(page, "Preserved vLLM result")
    import_recording(page, local_app, "speech.wav", wav_bytes)
    recording_id = page.url.rsplit("/", 1)[-1]
    result = store.add_result(
        recording_id,
        normalize_segments([{"text": "Dansk på UCloud.", "start": 0, "end": 3}]),
        {},
        saved["name"],
        {"kind": "provider", **Settings.model_validate(saved).model_dump()},
    )
    params = {"provider_id": saved["id"], "provider_name": saved["name"], "settings": saved}
    job_id = "f" * 32
    store.execute(
        "INSERT INTO jobs(id,recording_id,kind,state,params,created,updated) "
        "VALUES (?,?,'transcribe','interrupted',?,'2026-10-22','2026-10-22')",
        (job_id, recording_id, json.dumps(params)),
    )
    before_provider = store.one("SELECT * FROM providers WHERE id=?", (saved["id"],))
    before_job = store.job(job_id)
    page.reload()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Dansk på UCloud.")
    expect(page.locator("#passage-0 .segment-note")).to_have_text("Passage timing")
    expect(page.locator("#timing-warning")).to_be_hidden()
    page.locator("#passage-0 [data-action='seek']").click()
    expect(page.locator("#passage-0")).to_have_class("passage active")
    expect(page.locator('#file-provider-kind option[value="ucloud-vllm"]')).to_have_count(0)
    expect(page.locator(f'#file-provider option[value="{saved["id"]}"]')).to_have_count(0)
    page.locator("#settings-open").click()
    expect(page.get_by_role("tab")).to_have_text(
        ["OpenAI API", "UCloud", "Hugging Face", "Local Processing"]
    )
    expect(page.locator("#ucloud-vllm-setup")).not_to_be_visible()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    expect(page.locator("#ucloud-panel")).to_contain_text("Transcriber Batch")
    page.get_by_role("button", name="Close settings", exact=True).click()
    assert store.one("SELECT * FROM providers WHERE id=?", (saved["id"],)) == before_provider
    assert store.job(job_id) == before_job
    assert store.result(recording_id, result["id"])["segments"][0]["text"] == "Dansk på UCloud."
    assert app.state.credentials.get(saved["base_url"]) == "synthetic-serving-key"


def test_browser_timing_gaps_export_settings_and_project_zip(page, local_app):
    from test_browser_background_exports import (
        download_background_export,
        export_card,
        prepare_background_export,
    )

    store = local_app.server.config.app.state.store
    project = store.create_project("Review flags")
    recording = store.create_recording(project["id"], "review.v2.wav")
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    segments = normalize_segments(
        [
            {
                "text": "Hej CBS.",
                "start": 0,
                "end": 1,
                "words": [
                    {"word": "Hej", "start": 0, "end": 0.5},
                    {"word": "CBS", "start": 0.5, "end": 1},
                ],
            },
            {"text": "Manglende timing.", "start": 1},
        ]
    )
    first = store.add_result(
        recording["id"], segments, {}, "Word model", {"capability": "words"}, auto_first=True
    )
    second = store.add_result(
        recording["id"], segments, {}, "Another word model", {"capability": "words"}
    )
    speakers = store.add_result(
        recording["id"],
        normalize_segments(
            [
                {"text": "Speaker only.", "start": 0, "end": 3, "speaker": "A"},
            ]
        ),
        {},
        "Speakers",
        {"capability": "speakers"},
    )
    vanishing_recording = store.create_recording(project["id"], "No transcript.wav")
    vanishing_result = store.add_result(
        vanishing_recording["id"],
        normalize_segments([{"text": "Available at selection time.", "start": 0, "end": 1}]),
        {},
        "Temporary result",
        {},
    )
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    expect(page.locator("#timing-coverage")).to_contain_text("2/4 words (50%)")
    expect(page.locator("#passage-1")).to_have_class("passage timing-gap")
    # Missing timing is one heading tag, like "Review suggested"; its tooltip names the
    # reason. No highlighting: the passage itself looks like any other.
    tag = page.locator("#passage-1 .passage-heading .timing-tag button")
    expect(tag).to_have_text("Add word timing")
    expect(tag).to_have_attribute("title", __import__("re").compile("Word timing missing"))
    style = page.locator("#passage-1").evaluate(
        "e => [getComputedStyle(e).backgroundColor, getComputedStyle(e).borderLeftColor]"
    )
    assert "rgb(255, 250, 240)" not in style and "rgb(162, 99, 0)" not in style
    expect(page.locator("#timing-warning [data-run-repair]")).to_have_text(
        "Repair all 1 incomplete passage"
    )
    expect(page.locator("#passage-1 .segment-note")).to_have_count(0)
    page.locator("#compare-result").select_option(second["id"])
    # The read-only comparison names the count and offers no timing actions.
    expect(page.locator("#comparison-info")).to_contain_text("1 of 2 passages without word timing")
    expect(page.locator("#compare-timing-warning")).to_be_hidden()
    expect(page.locator("#comparison-panel .timing-tag")).to_have_count(0)
    expect(page.locator("#compare-passage-1")).to_have_class("passage timing-gap")
    page.locator("#compare-result").select_option(speakers["id"])
    expect(page.locator("#compare-timing-warning")).to_be_hidden()
    expect(page.locator("#comparison-transcript .timing-gap")).to_have_count(0)
    page.locator("#passage-0 [data-action='edit']").click()
    page.locator("#passage-0 textarea").fill("Hej CBS. Reviewed")
    page.locator("#passage-0 [data-action='save-edit']").click()
    expect(page.locator("#timing-warning [data-run-repair]")).to_have_text(
        "Repair all 2 incomplete passages"
    )
    expect(page.locator("#passage-0 .timing-tag button")).to_have_attribute(
        "title", __import__("re").compile("Word timing incomplete")
    )
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#passage-0 [data-action='restore']").click()
    expect(page.locator("#timing-coverage")).to_contain_text("2/4 words (50%)")
    # The comparison has no export button: review that result, then export from the sidebar.
    page.locator("#result-select").select_option(speakers["id"])
    expect(page.locator("#result-select")).to_have_value(speakers["id"])
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Speaker only.")
    page.locator("#export-open").click()
    expect(page.locator("#export-scope")).to_contain_text("Speakers")
    select_export_formats(page, "vtt")
    with page.expect_download() as download:
        page.locator("#export-link").click()
    assert Path(download.value.path()).read_text().startswith("WEBVTT")
    page.get_by_role("button", name="Close export settings").click()
    # The project export below needs the untimed word-model result as the reviewed one.
    page.locator("#result-select").select_option(first["id"])
    expect(page.locator("#timing-coverage")).to_contain_text("2/4 words (50%)")
    page.locator("#back-library").click()
    page.locator("#project-export-open").click()
    expect(page.locator("#export-results-label")).to_be_visible()
    select_export_formats(page, "srt")
    # Switching Results exposes this unassigned recording but does not select it.
    # The server must recheck availability if another client removes its saved result
    # after the dialog's eligible choices and explicit selection have been frozen.
    vanishing_choice = page.locator(
        f'#export-recording-list [data-export-recording][value="{vanishing_recording["id"]}"]'
    )
    expect(vanishing_choice).to_be_disabled()
    page.locator("#export-results").select_option("active")
    expect(vanishing_choice).to_be_enabled()
    expect(vanishing_choice).not_to_be_checked()
    page.locator("#export-select-all").click()
    expect(vanishing_choice).to_be_checked()
    store.set_result_trashed(
        vanishing_recording["id"],
        vanishing_result["id"],
        store.recording(vanishing_recording["id"])["revision"],
        trashed=True,
    )
    _, failed_task = prepare_background_export(page)
    failed_export = export_card(page, failed_task)
    expect(failed_export).to_contain_text("No results can be exported")
    expect(failed_export.locator("[data-export-download]")).to_have_count(0)
    expect(page.locator("#export-dialog")).to_be_visible()
    select_export_formats(page, "json")
    page.locator("#export-results").select_option("all")
    _, prepared_task = prepare_background_export(page)
    download = download_background_export(page, prepared_task)
    with ZipFile(download.path()) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["exported"] == 3 and manifest["skipped"] == 1
        assert any(e["result_id"] == first["id"] for e in manifest["entries"])
        skipped = next(e for e in manifest["entries"] if e["status"] == "skipped")
        assert skipped["recording_id"] == vanishing_recording["id"]
        assert skipped["reason"] == "No saved transcript."
    page.screenshot(path="test-results/export-settings.png")


def test_browser_unattended_batch_transcribes_then_adds_only_missing_features(
    page,
    local_app,
    monkeypatch,
):
    from cbs_transcriber import enhancement, providers

    runtime = {
        "ready": True,
        "version": "3.8.6",
        "expected_version": "3.8.6",
        "note": "Synthetic check.",
        "gpu": {
            "checked": True,
            "available": True,
            "devices": [{"name": "Synthetic GPU", "memory_gb": 8}],
        },
    }
    monkeypatch.setattr(enhancement, "check_runtime", lambda options, **kw: runtime)
    calls = []

    def transcribe(*args):
        calls.append("asr")
        return {
            "segments": [{"text": "Hej CBS.", "start": 0, "end": 3}],
            "words": [
                {"word": "Hej", "start": 0, "end": 1},
                {"word": "CBS", "start": 1, "end": 3},
            ],
        }

    def enhance(worker, job, recording):
        options = job["params"]["enhancement"]
        assert not options["align"] and options["diarize"]
        source = job["params"]["source_snapshot"]
        assert source["segments"][0]["text"] == "Hej CBS."
        calls.append("enhance")
        worker.store.add_result(
            recording["id"], source["segments"], {}, "Enhanced", {}, job_id=job["id"]
        )

    monkeypatch.setattr(providers, "transcribe_file", transcribe)
    monkeypatch.setattr(enhancement, "run_enhancement", enhance)
    store = local_app.server.config.app.state.store
    project = store.create_project("Overnight batch")
    recordings = [store.create_recording(project["id"], name) for name in ("One.wav", "Two.wav")]
    for recording in recordings:
        write_original_wav(store, recording)
        store.execute(
            "UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],)
        )
    with httpx.Client(base_url=local_app.url, headers={"X-CBS-Request": "1"}) as client:
        client.put("/api/settings", json={"api_key": "synthetic", "remember": False})
    page.goto(local_app.url)
    # The runtime is chosen and checked once in Provider Settings → Processing (ADR 0059).
    open_processing(page)
    page.locator("#enhance-python").fill(sys.executable)
    page.locator("#enhance-python").blur()
    page.locator("#enhance-check").click()
    expect(page.locator("#processing-local-status")).to_contain_text("Synthetic GPU (8 GB)")
    expect(page.locator("#enhance-downloads")).to_be_checked()
    # This synthetic cached-speaker run deliberately has no Hugging Face token.
    page.locator("#enhance-downloads").uncheck()
    expect(page.locator("#processing-status")).to_have_text("Saved for new jobs.")
    page.get_by_role("button", name="Close settings", exact=True).click()
    with httpx.Client(base_url=local_app.url) as client:
        settings = client.get("/api/enhancement-settings").json()
        assert settings["runtime_python"] == sys.executable
        assert not settings["download_models"]
    page.get_by_role("link", name="Overnight batch").click()
    page.locator("#select-all").click()
    expect(page.locator("#batch-open")).to_have_count(0)
    expect(page.locator("[data-transcribe]")).to_have_count(0)
    page.locator('#bulk-features [data-transcription-feature="speaker_labels"]').check()
    page.locator("#transcribe-selected").click()
    expect(page.locator("#batch-summary")).to_contain_text("2 recordings")
    expect(page.locator("#enhance-start")).to_have_text("Start batch")
    expect(page.locator("#enhance-diarize")).to_be_checked()
    page.locator("#batch-mode").select_option("transcribe")
    expect(page.locator("#enhancement-options")).to_be_hidden()
    page.locator("#batch-mode").select_option("enhance")
    expect(page.locator("#enhance-summary")).to_contain_text("automatic device")
    expect(page.locator("#enhancement-dialog input[type=password]")).to_have_count(0)
    page.locator("#enhance-execution").select_option("local")
    assert calls == []
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#enhance-start").click()
    expect(page.locator("#enhancement-dialog")).to_be_hidden()
    deadline = time.monotonic() + 15
    while (
        time.monotonic() < deadline
        and len(
            store.all(
                "SELECT id FROM jobs WHERE kind IN ('transcribe','enhance') AND state='completed'"
            )
        )
        < 4
    ):
        time.sleep(0.05)
    assert sorted(calls) == ["asr", "asr", "enhance", "enhance"], store.all(
        "SELECT kind,state,message FROM jobs"
    )
    assert all(len(store.results(r["id"])) == 2 for r in recordings)
    assert (
        len(
            store.all(
                "SELECT id FROM jobs WHERE kind IN ('transcribe','enhance') AND state='completed'"
            )
        )
        == 4
    )
    inspections = store.all("SELECT state FROM jobs WHERE kind='source-inspect'")
    assert len(inspections) == 2 and all(job["state"] == "completed" for job in inspections)
    assert len(store.all("SELECT id FROM jobs")) == 6
    for recording in recordings:
        active = store.recording(recording["id"])["active_transcript_id"]
        assert store.result(recording["id"], active)["source"]["kind"] == "provider"
