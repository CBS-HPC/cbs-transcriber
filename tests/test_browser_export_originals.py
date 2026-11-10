"""Original media export is explicit, scoped and independent of transcript availability."""

import json
from zipfile import ZipFile

import pytest
from admission_support import write_original_wav
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_background_exports import (
    close_export_if_open,
    download_background_export,
    prepare_background_export,
    wait_for_intercepted,
)
from test_browser_export_selection import choose_formats

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def seed_originals(store):
    project = store.create_project("Original export selection")
    records, results, originals = {}, {}, {}
    for name in ["Working", "Unassigned", "Empty", "Trash only"]:
        recording = store.create_recording(project["id"], name + ".wav")
        records[name] = recording
        originals[name] = write_original_wav(store, recording).read_bytes()
        if name != "Empty":
            result = store.add_result(
                recording["id"],
                normalize_segments([{"text": name + " transcript", "start": 0, "end": 1}]),
                {},
                "Synthetic",
                {},
                auto_first=name == "Working",
            )
            results[name] = result
            if name == "Trash only":
                store.set_result_trashed(
                    recording["id"],
                    result["id"],
                    store.recording(recording["id"])["revision"],
                    trashed=True,
                )
    return project, records, results, originals


def row(page, recording):
    return page.locator(f'[data-export-recording][value="{recording["id"]}"]')


def test_originals_and_result_mode_never_silently_expand_selection(page, local_app):
    store = local_app.server.config.app.state.store
    project, records, _, _ = seed_originals(store)
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#project-export-open").click()
    expect(page.locator("#export-originals")).not_to_be_checked()
    expect(page.locator("[data-export-recording]")).to_have_count(4)
    expect(row(page, records["Working"])).to_be_checked()
    for name in ["Unassigned", "Empty", "Trash only"]:
        expect(row(page, records[name])).to_be_disabled()
        expect(row(page, records[name])).not_to_be_checked()
    expect(row(page, records["Unassigned"]).locator("..")).to_contain_text("No Working transcript")
    expect(row(page, records["Empty"]).locator("..")).to_contain_text("No transcript")
    page.locator("#export-results").select_option("active")
    expect(row(page, records["Unassigned"])).to_be_enabled()
    expect(row(page, records["Unassigned"])).not_to_be_checked()
    page.locator("#export-select-all").click()
    expect(page.locator("[data-export-recording]:checked")).to_have_count(2)
    page.locator("#export-results").select_option("all")
    expect(row(page, records["Trash only"])).to_be_disabled()
    page.locator("#export-originals").check()
    for name in ["Empty", "Trash only"]:
        expect(row(page, records[name])).to_be_enabled()
        expect(row(page, records[name])).not_to_be_checked()
    page.locator("#export-select-all").click()
    expect(page.locator("[data-export-recording]:checked")).to_have_count(4)
    page.locator("#export-results").select_option("working")
    page.locator("#export-originals").uncheck()
    expect(page.locator("[data-export-recording]:checked")).to_have_count(1)
    for name in ["Unassigned", "Empty", "Trash only"]:
        expect(row(page, records[name])).to_be_disabled()
        expect(row(page, records[name])).not_to_be_checked()
    page.locator('[aria-label="Close export settings"]').click()
    page.locator("#project-export-open").click()
    expect(page.locator("#export-originals")).not_to_be_checked()


def test_project_originals_zip_contains_only_chosen_originals_and_transcripts(page, local_app):
    store = local_app.server.config.app.state.store
    project, records, _, originals = seed_originals(store)
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#project-export-open").click()
    choose_formats(page, "text")
    page.locator("#export-originals").check()
    row(page, records["Empty"]).check()
    selected = [records[name]["id"] for name in ["Working", "Empty"]]
    response, task_id = prepare_background_export(page)
    download = download_background_export(page, task_id)
    assert response.request.post_data_json == {
        "recording_ids": selected,
        "formats": ["text"],
        "grouping": "speaker",
        "results": "working",
        "include_originals": True,
    }
    with ZipFile(download.path()) as archive:
        report = json.loads(archive.read("manifest.json"))
        assert {entry["recording_id"] for entry in report["entries"]} == set(selected)
        media = [entry for entry in report["entries"] if entry.get("kind") == "source_media"]
        assert len(media) == 2
        for name in ["Working", "Empty"]:
            entry = next(entry for entry in media if entry["recording_id"] == records[name]["id"])
            assert entry["status"] == "exported" and entry["format"] == "original"
            assert entry["path"].startswith(f"transcripts/{name}/original/")
            assert archive.read(entry["path"]) == originals[name]
        text = next(
            entry
            for entry in report["entries"]
            if entry["format"] == "text" and entry["status"] == "exported"
        )
        assert "Working transcript" in archive.read(text["path"]).decode("utf-8")


@pytest.mark.parametrize("project_scope", [False, True])
def test_original_only_export_without_a_transcript_never_fabricates_result_id(
    page, local_app, project_scope
):
    store = local_app.server.config.app.state.store
    project, records, _, originals = seed_originals(store)
    empty = records["Empty"]
    path = f"/projects/{project['id']}" if project_scope else f"/recordings/{empty['id']}"
    page.goto(local_app.url + "/#" + path)
    page.locator("#project-export-open" if project_scope else "#export-open").click()
    page.locator("#export-deselect-all").click() if project_scope else None
    choose_formats(page)
    expect(page.locator("#export-link")).to_have_attribute("aria-disabled", "true")
    page.locator("#export-originals").check()
    expect(row(page, empty)).not_to_be_checked()
    expect(page.locator("#export-link")).to_have_attribute("aria-disabled", "true")
    row(page, empty).check()
    expect(page.locator("#export-link")).not_to_have_attribute("aria-disabled", "true")
    response, task_id = prepare_background_export(page)
    download = download_background_export(page, task_id)
    body = response.request.post_data_json
    assert body["formats"] == [] and body["include_originals"] is True
    if project_scope:
        assert body["recording_ids"] == [empty["id"]]
    else:
        assert body["result_id"] is None
    with ZipFile(download.path()) as archive:
        report = json.loads(archive.read("manifest.json"))
        media = [entry for entry in report["entries"] if entry.get("kind") == "source_media"]
        assert len(media) == 1 and media[0]["recording_id"] == empty["id"]
        assert archive.read(media[0]["path"]) == originals["Empty"]
    assert store.recording(empty["id"])["active_transcript_id"] is None
    close_export_if_open(page)
    page.locator("#project-export-open" if project_scope else "#export-open").click()
    expect(page.locator("#export-originals")).not_to_be_checked()
    expect(row(page, empty)).to_be_disabled()
    expect(row(page, empty)).not_to_be_checked()


def test_current_result_with_one_format_and_original_uses_explicit_zip_post(page, local_app):
    store = local_app.server.config.app.state.store
    _, records, results, originals = seed_originals(store)
    recording = records["Working"]
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    page.locator("#export-open").click()
    choose_formats(page, "text")
    assert "/export?" in page.locator("#export-link").get_attribute("href")
    page.locator("#export-originals").check()
    response, task_id = prepare_background_export(page)
    download = download_background_export(page, task_id)
    assert response.request.post_data_json == {
        "result_id": results["Working"]["id"],
        "formats": ["text"],
        "grouping": "speaker",
        "include_originals": True,
    }
    with ZipFile(download.path()) as archive:
        report = json.loads(archive.read("manifest.json"))
        original = next(entry for entry in report["entries"] if entry.get("kind") == "source_media")
        assert archive.read(original["path"]) == originals["Working"]


def test_old_originals_request_cannot_restore_opt_in_after_navigation(page, local_app):
    store = local_app.server.config.app.state.store
    _, records, _, _ = seed_originals(store)
    page.goto(local_app.url + f"/#/recordings/{records['Working']['id']}")
    page.locator("#export-open").click()
    choose_formats(page, "text")
    page.locator("#export-originals").check()
    held = []
    page.route("**/api/recordings/*/export-tasks*", lambda route: held.append(route))
    page.locator("#export-link").click()
    expect(page.locator("#export-originals")).to_be_disabled()
    expect(page.locator("[data-export-recording]")).to_be_disabled()
    wait_for_intercepted(page, held)
    assert held[0].request.post_data_json["include_originals"] is True
    page.locator('[aria-label="Close export settings"]').click()
    page.evaluate("hash => location.hash = hash", f"#/recordings/{records['Empty']['id']}")
    expect(page.locator("#recording-title")).to_have_text("Empty")
    page.locator("#export-open").click()
    expect(page.locator("#export-originals")).not_to_be_checked()
    held[0].fulfill(
        status=503, json={"detail": "OLD ORIGINAL EXPORT", "code": "library_instance_changed"}
    )
    expect(page.locator("#export-status")).to_have_text("")
    expect(page.locator("#library-instance-notice")).to_be_hidden()
    expect(page.locator("#export-originals")).to_be_enabled()
    expect(page.locator("[data-export-recording]")).to_be_disabled()
    expect(page.locator("[data-export-recording]")).not_to_be_checked()
