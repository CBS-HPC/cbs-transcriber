"""Real import/review/report downloads preserve raw receipts without publishing private data."""

import hashlib
import json
import threading
from zipfile import ZipFile

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_working_reports import mark_active_latest

from cbs_transcriber.media import ffmpeg

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("old_view", [False, True], ids=["initial-load", "switch-recording"])
def test_import_during_recording_load_is_refused_and_can_be_retried(page, local_app, old_view):
    store = local_app.server.config.app.state.store
    project = store.create_project("Import loading")
    recording = store.create_recording(project["id"], "Loading.wav")
    rid = recording["id"]
    held, uploads = [], []
    previous = store.create_recording(project["id"], "Previous.wav") if old_view else None
    if previous:
        page.goto(local_app.url + f"/#/recordings/{previous['id']}")
        expect(page.locator("#recording-title")).to_have_text(previous["name"])

    def hold(route):
        held.append(route)
        page.evaluate("window.recordingLoadHeld = true")

    def observe(request):
        if request.method == "POST" and request.url.endswith("/transcript"):
            uploads.append(request.url)

    pattern = f"**/api/recordings/{rid}"
    page.route(pattern, hold)
    page.on("request", observe)
    payload = {
        "name": "retry.json",
        "mimeType": "application/json",
        "buffer": b'{"segments":[{"start":0,"end":3,"text":"Retry import."}]}',
    }
    if previous:
        page.evaluate("id => { location.hash = '#/recordings/' + id; }", rid)
    else:
        page.goto(local_app.url + f"/#/recordings/{rid}")
    page.wait_for_function("() => window.recordingLoadHeld === true")
    try:
        page.locator("#transcript-file").set_input_files(payload)
        expect(page.locator("#toast")).to_have_text(
            "Wait for this recording to finish loading, then choose the transcript again."
        )
        assert uploads == [] and store.results(rid) == []
        if previous:
            assert store.results(previous["id"]) == []
    finally:
        for route in held:
            route.fulfill(response=route.fetch())
        page.unroute(pattern, hold)
    expect(page.locator("#recording-title")).to_have_text(recording["name"])
    page.locator("#transcript-file").set_input_files(payload)
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Retry import.")
    assert uploads == [local_app.url + f"/api/recordings/{rid}/transcript"]


@pytest.mark.parametrize("kind", ["json", "csv"])
def test_import_edit_and_metadata_report_preserve_exact_raw_artifact(
    page, local_app, tmp_path, kind
):
    store = local_app.server.config.app.state.store
    project = store.create_project("PRIVATE_IMPORT_STUDY")
    recording = store.create_recording(project["id"], "PRIVATE_RECORDING.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (rid,))
    directory = store.directory(rid)
    directory.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        ["-f", "lavfi", "-i", "sine=frequency=330:duration=3", str(directory / "audio.mp3")],
        threading.Event(),
    )
    original = "PRIVATE_ORIGINAL æøå."
    reviewed = "PRIVATE_REVIEWED æøå."
    if kind == "json":
        payload = {"segments": [{"start": 0, "end": 3, "text": original}]}
        text = json.dumps(payload, ensure_ascii=False, indent=2).replace("\n", "\r\n") + "\r\n"
        mime = "application/json"
    else:
        text = f'start,end,text\r\n0,3,"{original}"\r\n'
        mime = "text/csv"
    raw = b"\xef\xbb\xbf" + text.encode("utf-8")
    filename = f"PRIVATE_INTERVIEW.multiple.æøå.{kind}"
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#recording-title")).to_have_text(recording["name"])
    page.locator("#transcript-file").set_input_files(
        {"name": filename, "mimeType": mime, "buffer": raw}
    )
    expect(page.locator("#passage-0 .passage-text")).to_have_text(original)
    result = store.results(rid)[0]
    receipt = result["source"]["artifact_evidence"]
    assert receipt["schema"] == 2  # Fresh imports use the strict parser generation (ADR 0111).
    assert receipt["sha256"] == hashlib.sha256(raw).hexdigest()
    assert receipt["bytes"] == len(raw) and receipt["format"] == kind
    preserved = directory / "sources" / f"{receipt['source_id']}.{kind}"
    assert preserved.read_bytes() == raw

    page.locator("#passage-0 [data-action='edit']").click()
    page.locator("#passage-0 textarea").fill(reviewed)
    page.locator("#passage-0 [data-action='save-edit']").click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text(reviewed)
    assert len(store.result_versions(rid, result["id"])) == 2
    # Default reports select the researcher's explicit Working version (ADR 0093).
    mark_active_latest(store, project["id"])

    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#research-report-open").click()
    expect(page.locator("#research-report-text")).not_to_be_checked()
    with page.expect_download() as downloaded:
        page.locator("#research-report-download").click()
    target = tmp_path / "import-evidence.zip"
    downloaded.value.save_as(target)
    with ZipFile(target) as archive:
        assert not any(name.startswith("transcripts/") for name in archive.namelist())
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files["manifest.json"])
    artifacts = [item["artifact_evidence"] for item in manifest["versions"]]
    assert len(artifacts) == 2
    assert len({artifact["source"] for artifact in artifacts}) == 1
    for artifact in artifacts:
        assert artifact == {
            "source": artifacts[0]["source"],
            "role": "transcript_import",
            "sha256": receipt["sha256"],
            "bytes": len(raw),
            "format": kind,
            "parser_generation": 2,
            "availability": "not_checked",
        }
    shared = b"\n".join(files.values())
    for private in (original, reviewed, filename, receipt["source_id"], str(directory)):
        assert private.encode("utf-8") not in shared
    assert preserved.read_bytes() == raw
    assert store.all("SELECT id FROM jobs") == []
