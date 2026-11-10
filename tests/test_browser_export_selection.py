"""Explicit export selections, real ZIP contents and frozen dialog request scopes."""

import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_background_exports import (
    download_background_export,
    prepare_background_export,
    wait_for_intercepted,
)

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def seed_exports(store):
    project = store.create_project("Selected exports")
    recordings = []
    results = []
    for name, timed in [("First.wav", True), ("Second.wav", False), ("Outside.wav", True)]:
        recording = store.create_recording(project["id"], name)
        segments = normalize_segments(
            [{"text": name + " words.", **({"start": 0, "end": 1} if timed else {})}]
        )
        result = store.add_result(recording["id"], segments, {}, "Synthetic", {}, auto_first=True)
        recordings.append(recording)
        results.append(result)
    collection = store.create_collection(project["id"], "Wave one")
    store.move_recordings(
        project["id"], [recording["id"] for recording in recordings[:2]], collection["id"]
    )
    return project, recordings, results, collection


def choose_formats(page, *formats):
    for checkbox in page.locator("[data-export-format]").all():
        checkbox.set_checked(checkbox.get_attribute("value") in formats)


def test_project_exports_only_checked_collection_recordings_and_formats(page, local_app):
    store = local_app.server.config.app.state.store
    project, recordings, _, collection = seed_exports(store)
    unsafe_name = "Interview <img src=x onerror=alert(1)>"
    store.execute("UPDATE recordings SET name=? WHERE id=?", (unsafe_name, recordings[0]["id"]))
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator(f'[data-collection="{collection["id"]}"]').click()
    page.locator("#project-export-open").click()
    rows = page.locator("#export-recording-list")
    expect(rows.locator("input")).to_have_count(2)
    expect(rows.locator("input:checked")).to_have_count(2)
    expect(rows).to_contain_text(unsafe_name)
    expect(rows.locator("img")).to_have_count(0)
    expect(page.locator("#export-select-all")).to_be_disabled()
    page.locator("#export-deselect-all").click()
    expect(page.locator("#export-link")).to_have_attribute("aria-disabled", "true")
    expect(page.locator("#export-warning")).to_have_text("Choose at least one recording.")
    expect(page.locator("#export-deselect-all")).to_be_disabled()
    page.locator("#export-select-all").click()
    rows.locator(f'[value="{recordings[1]["id"]}"]').uncheck()
    choose_formats(page)
    expect(page.locator("#export-link")).to_have_attribute("aria-disabled", "true")
    expect(page.locator("#export-warning")).to_have_text("Choose at least one format.")
    choose_formats(page, "text", "json")
    response, task_id = prepare_background_export(page)
    download = download_background_export(page, task_id)
    assert response.request.post_data_json == {
        "recording_ids": [recordings[0]["id"]],
        "formats": ["text", "json"],
        "grouping": "speaker",
        "results": "working",
        "collection": collection["id"],
    }
    with ZipFile(download.path()) as archive:
        report = json.loads(archive.read("manifest.json"))
        assert report["exported"] == 2 and report["skipped"] == 0
        assert {entry["recording_id"] for entry in report["entries"]} == {recordings[0]["id"]}
        assert {entry["format"] for entry in report["entries"]} == {"text", "json"}
        for entry in report["entries"]:
            assert entry["path"].startswith("transcripts/01 Wave one/Interview _img")
            assert recordings[0]["id"] not in entry["path"]
            assert "First.wav words." in archive.read(entry["path"]).decode("utf-8")


def test_recording_multi_export_keeps_valid_formats_and_reports_missing_timing(page, local_app):
    store = local_app.server.config.app.state.store
    _, recordings, results, _ = seed_exports(store)
    rid = recordings[1]["id"]
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#export-open").click()
    expect(page.locator("#export-results-label")).to_be_hidden()
    expect(page.locator("[data-export-recording]")).to_have_count(1)
    expect(page.locator("[data-export-recording]")).to_have_attribute("value", rid)
    choose_formats(page, "vtt")
    expect(page.locator("#export-link")).to_have_attribute("aria-disabled", "true")
    expect(page.locator("#export-warning")).to_contain_text("lack required timestamps")
    choose_formats(page, "text", "vtt")
    expect(page.locator("#export-link")).not_to_have_attribute("aria-disabled", "true")
    expect(page.locator("#export-warning")).to_contain_text("listed as skipped")
    response, task_id = prepare_background_export(page)
    download = download_background_export(page, task_id)
    assert response.request.post_data_json == {
        "result_id": results[1]["id"],
        "formats": ["text", "vtt"],
        "grouping": "speaker",
    }
    with ZipFile(download.path()) as archive:
        report = json.loads(archive.read("manifest.json"))
        assert report["exported"] == 1 and report["skipped"] == 1
        exported = next(entry for entry in report["entries"] if entry["status"] == "exported")
        skipped = next(entry for entry in report["entries"] if entry["status"] == "skipped")
        assert exported["format"] == "text" and skipped["format"] == "vtt"
        assert "Second.wav words." in archive.read(exported["path"]).decode("utf-8")


def test_export_request_is_frozen_and_late_failure_cannot_change_new_dialog(page, local_app):
    store = local_app.server.config.app.state.store
    project, recordings, _, collection = seed_exports(store)
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator(f'[data-collection="{collection["id"]}"]').click()
    page.locator("#project-export-open").click()
    choose_formats(page, "text")
    held = []
    page.route("**/api/projects/*/export-tasks*", lambda route: held.append(route))
    # Background project context changes must not widen the dialog's frozen scope.
    page.evaluate("state.collection = null")
    page.locator("#export-link").click()
    expect(page.locator("#export-status")).not_to_have_text("")
    expect(page.locator("[data-export-format]").first).to_be_disabled()
    wait_for_intercepted(page, held)
    assert held[0].request.post_data_json["collection"] == collection["id"]
    assert held[0].request.post_data_json["recording_ids"] == [r["id"] for r in recordings[:2]]
    page.locator('[aria-label="Close export settings"]').click()
    page.evaluate("hash => location.hash = hash", f"#/recordings/{recordings[2]['id']}")
    expect(page.locator("#recording-title")).to_have_text("Outside")
    page.locator("#export-open").click()
    expect(page.locator("#export-scope")).to_contain_text("Outside")
    expect(page.locator("[data-export-format]").first).to_be_enabled()
    held[0].fulfill(
        status=503, json={"detail": "OLD EXPORT ERROR", "code": "library_instance_changed"}
    )
    expect(page.locator("#export-status")).to_have_text("")
    expect(page.locator("#library-instance-notice")).to_be_hidden()
    expect(page.locator("[data-export-format]").first).to_be_enabled()
    expect(page.locator("[data-export-recording]")).to_have_count(1)
    expect(page.locator("[data-export-recording]")).to_have_attribute("value", recordings[2]["id"])
    with page.expect_download() as download:
        page.locator("#export-link").click()
    assert "Outside.wav words." in Path(download.value.path()).read_text()


@pytest.mark.parametrize("project_scope", [False, True])
def test_open_export_retains_page_when_library_instance_changes(page, local_app, project_scope):
    store = local_app.server.config.app.state.store
    project, recordings, _, _ = seed_exports(store)
    path = f"/projects/{project['id']}" if project_scope else f"/recordings/{recordings[0]['id']}"
    page.goto(local_app.url + "/#" + path)
    page.locator("#project-export-open" if project_scope else "#export-open").click()
    choose_formats(page, "text")
    previous_url = page.url
    downloads = []
    page.on("download", lambda download: downloads.append(download))
    app = local_app.server.config.app
    original_instance = app.state.browser_instance
    app.state.browser_instance = "synthetic-new-library-instance"
    try:
        with page.expect_response(
            lambda response: "/export" in response.url and response.status == 409
        ) as response:
            page.locator("#export-link").click()
        assert response.value.request.method == ("POST" if project_scope else "GET")
        expect(page.locator("#library-instance-notice")).to_be_visible()
        expect(page.locator("#export-status")).to_contain_text("earlier library")
        expect(page.locator("#export-dialog")).to_be_visible()
        assert page.url == previous_url and not downloads
    finally:
        app.state.browser_instance = original_instance
