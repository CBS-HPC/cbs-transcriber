"""Read-only history previews and explicit local research-report downloads."""

import json
import threading
from zipfile import ZipFile

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_working_reports import mark_active_latest

from cbs_transcriber.media import ffmpeg
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def seed_history(local_app):
    store = Store(local_app.root)
    project = store.create_project("Private history study")
    recording = store.create_recording(project["id"], "private-interview.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (rid,))
    directory = store.directory(rid)
    directory.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        ["-f", "lavfi", "-i", "sine=frequency=330:duration=3", str(directory / "audio.mp3")],
        threading.Event(),
    )
    segments = normalize_segments(
        [{"text": '<img src=x onerror="alert(1)"> Hej æøå.', "start": 0, "end": 3}]
    )
    first = store.add_result(rid, segments, {}, "Private method", {"kind": "import"})
    recording = store.recording(rid)
    segments[0]["text"] = "Reviewed text."
    store.save_transcript(rid, segments, {}, recording["revision"], action="free_rewrite")
    second = store.add_result(
        rid,
        normalize_segments([{"text": "Another result.", "start": 0, "end": 3}]),
        {},
        "Other method",
        {"kind": "import"},
    )
    return store, project, rid, first, second


def test_history_is_literal_exact_and_preserves_current_drafts(page, local_app, tmp_path):
    store, _, rid, first, _ = seed_history(local_app)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#result-select")).to_have_value(first["id"])
    page.locator("#passage-0 [data-action='edit']").click()
    page.locator('#working-edit-dialog [data-working-edit-choice][value="keep"]').click()
    draft = "Unsaved researcher correction."
    page.locator("#passage-0 textarea").fill(draft)
    revision = store.recording(rid)["revision"]
    page.locator("#history-open").click()
    expect(page.locator("#history-versions button")).to_have_count(2)
    page.locator("#history-versions button").last.click()
    expect(page.locator("#history-text")).to_contain_text('<img src=x onerror="alert(1)"> Hej æøå.')
    expect(page.locator("#history-text img")).to_have_count(0)
    expect(page.locator("#history-preview-title")).to_contain_text("Version 1")
    page.screenshot(path="test-results/research-history-preview.png")
    with page.expect_download() as downloaded:
        page.locator("#history-export").click()
    path = tmp_path / "version.txt"
    downloaded.value.save_as(path)
    assert '<img src=x onerror="alert(1)"> Hej æøå.' in path.read_text(encoding="utf-8")
    assert "Reviewed text." not in path.read_text(encoding="utf-8")
    page.get_by_role("button", name="Close history", exact=True).click()
    expect(page.locator("#passage-0 textarea")).to_have_value(draft)
    expect(page.locator("#result-select")).to_have_value(first["id"])
    assert store.recording(rid)["revision"] == revision
    assert len(store.result_versions(rid, first["id"])) == 2
    page.screenshot(path="test-results/research-history.png")


def test_history_late_preview_cannot_replace_another_result(page, local_app):
    store, _, rid, first, second = seed_history(local_app)
    old = store.result_versions(rid, first["id"])[-1]
    held = []
    url = f"**/api/recordings/{rid}/transcripts/{first['id']}/versions/{old['id']}"
    page.route(url, lambda route: held.append(route))
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#history-open").click()
    page.locator("#history-versions button").last.click()
    expect(page.locator("#history-status")).to_have_text("Loading exact saved text…")
    assert held
    page.keyboard.press("Escape")
    page.locator("#result-select").select_option(second["id"])
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Another result.")
    page.locator("#history-open").click()
    page.locator("#history-versions button").first.click()
    expect(page.locator("#history-text")).to_contain_text("Another result.")
    held[0].fulfill(json=store.result_version(rid, first["id"], old["id"]))
    expect(page.locator("#history-text")).to_contain_text("Another result.")
    expect(page.locator("#history-text")).not_to_contain_text("Hej æøå")
    assert store.recording(rid)["active_transcript_id"] == second["id"]


def test_research_report_metadata_default_and_explicit_text_opt_in(page, local_app, tmp_path):
    _, project, _, _, _ = seed_history(local_app)
    # Default reports select the researcher's explicit Working versions (ADR 0093).
    mark_active_latest(local_app.server.config.app.state.store, project["id"])
    requests = []

    def capture(route):
        requests.append(json.loads(route.request.post_data))
        route.continue_()

    page.route("**/research-report", capture)
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#research-report-open").click()
    expect(page.locator("#research-report-text")).not_to_be_checked()
    expect(page.locator("#research-report-preview")).to_contain_text("without transcript text")
    with page.expect_download() as downloaded:
        page.locator("#research-report-download").click()
    metadata = tmp_path / "metadata.zip"
    downloaded.value.save_as(metadata)
    assert requests[-1] == {"include_selected_transcripts": False}
    with ZipFile(metadata) as bundle:
        assert not any(name.startswith("transcripts/") for name in bundle.namelist())
    page.locator("#research-report-text").check()
    expect(page.locator("#research-report-preview")).to_contain_text(
        "Transcript text and speaker names are included"
    )
    with page.expect_download() as downloaded:
        page.locator("#research-report-download").click()
    included = tmp_path / "included.zip"
    downloaded.value.save_as(included)
    assert requests[-1] == {"include_selected_transcripts": True}
    with ZipFile(included) as bundle:
        rendered = [
            bundle.read(name) for name in bundle.namelist() if name.startswith("transcripts/")
        ]
        assert len(rendered) == 1 and b"Reviewed text." in rendered[0]
        assert b"onerror" not in rendered[0]
    page.get_by_role("button", name="Close research report", exact=True).click()
    page.locator("#research-report-open").click()
    expect(page.locator("#research-report-text")).not_to_be_checked()


def test_history_and_report_failures_leave_retry_controls_available(page, local_app):
    _, project, rid, first, _ = seed_history(local_app)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    path = f"**/api/recordings/{rid}/transcripts/{first['id']}/versions/*"
    page.route(
        path,
        lambda route: route.fulfill(
            status=503, json={"detail": "History temporarily unavailable."}
        ),
    )
    page.locator("#history-open").click()
    page.locator("#history-versions button").first.click()
    expect(page.locator("#history-status")).to_have_text("History temporarily unavailable.")
    expect(page.locator("#history-preview")).to_be_hidden()
    page.unroute(path)
    page.locator("#history-versions button").first.click()
    expect(page.locator("#history-text")).to_contain_text("Reviewed text.")
    page.keyboard.press("Escape")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.route(
        "**/research-report",
        lambda route: route.fulfill(status=503, json={"detail": "Report temporarily unavailable."}),
    )
    page.locator("#research-report-open").click()
    page.locator("#research-report-download").click()
    expect(page.locator("#research-report-status")).to_have_text("Report temporarily unavailable.")
    expect(page.locator("#research-report-download")).to_be_enabled()
    expect(page.locator("#research-report-text")).to_be_enabled()
