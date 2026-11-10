"""Prepared ZIPs outlive dialogs and never copy archive bodies into browser Blobs."""

import errno
import json
import threading
from time import monotonic
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from admission_support import write_original_wav
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser import select_export_formats

from cbs_transcriber import exports
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def close_export_if_open(page):
    if page.locator("#export-dialog").is_visible():
        page.get_by_role("button", name="Close export settings", exact=True).click()


def wait_for_intercepted(page, held):
    deadline = monotonic() + 5
    while not held and monotonic() < deadline:
        page.wait_for_timeout(10)
    assert held, "The expected synthetic export request was not intercepted."


def export_card(page, task_id):
    return page.locator(f'#jobs [data-export-task="{task_id}"]')


def show_activity(page):
    if page.locator("#activity").is_hidden():
        page.locator("#activity-open").click()


def prepare_background_export(page):
    expect(page.locator("#export-link")).not_to_have_attribute("aria-disabled", "true")
    with page.expect_response(
        lambda response: response.request.method == "POST" and "/export-tasks" in response.url
    ) as started:
        page.locator("#export-link").click()
    response = started.value
    assert response.status == 202, response.text()
    return response, response.json()["export_task"]["id"]


def download_background_export(page, task_id):
    close_export_if_open(page)
    show_activity(page)
    link = export_card(page, task_id).locator("[data-export-download]")
    expect(link).to_be_visible(timeout=15000)
    expect(link).to_have_attribute(
        "href", __import__("re").compile(r"/api/export-tasks/.*/download")
    )
    with page.expect_download() as download:
        link.click()
    assert not download.value.url.startswith("blob:")
    return download.value


@pytest.fixture
def blocked_export(local_app, monkeypatch):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic background exports")
    recordings, results, original_bytes = [], [], []
    for index in range(2):
        recording = store.create_recording(project["id"], f"Recording {index + 1}.wav")
        original_bytes.append(write_original_wav(store, recording).read_bytes())
        result = store.add_result(
            recording["id"],
            normalize_segments([{"text": f"Saved wording {index + 1}."}]),
            {},
            "Synthetic",
            {},
            auto_first=True,
        )
        recordings.append(recording)
        results.append(result)
    entered, release = threading.Event(), threading.Event()
    original_stage = exports._stage_original
    control = SimpleNamespace(
        app=local_app,
        store=store,
        project=project,
        recordings=recordings,
        results=results,
        originals=original_bytes,
        entered=entered,
        release=release,
        calls=[],
        fail=False,
    )

    def blocked_stage(store, recording_id, owner, background=None):
        if background is not None:
            control.calls.append(recording_id)
            entered.set()
            assert release.wait(60), "The synthetic export was not released."
            if control.fail:
                raise OSError(errno.ENOSPC, "Synthetic export disk refusal")
        return original_stage(store, recording_id, owner, background)

    monkeypatch.setattr(exports, "_stage_original", blocked_stage)
    try:
        yield control
    finally:
        release.set()


def project_export(page, control):
    page.goto(control.app.url + f"/#/projects/{control.project['id']}")
    page.locator("#project-export-open").click()
    select_export_formats(page, "json")
    page.locator("#export-originals").check()
    page.locator(f'[data-export-recording][value="{control.recordings[1]["id"]}"]').uncheck()


def task_state(page, task_id, expected):
    page.wait_for_function(
        """async ({id, expected}) => {
            const data = await api('/export-tasks', {background:true});
            return data.tasks.find(task => task.export_task.id === id)?.state === expected;
        }""",
        arg={"id": task_id, "expected": expected},
    )


def test_prepared_export_survives_navigation_and_downloads_its_frozen_selection_without_blob(
    page, blocked_export
):
    control = blocked_export
    starts = []
    page.on(
        "request",
        lambda request: (
            starts.append(request.post_data_json)
            if request.method == "POST" and request.url.endswith("/export-tasks")
            else None
        ),
    )
    page.add_init_script("""
        window.archiveBlobReads = 0;
        Response.prototype.blob = function() {
            window.archiveBlobReads++;
            throw new Error('ZIP bodies must use a native download');
        };
        URL.createObjectURL = function() { throw new Error('No ZIP Blob URL'); };
    """)
    project_export(page, control)
    response, task_id = prepare_background_export(page)
    assert control.entered.wait(5) and not control.release.is_set()
    assert response.request.post_data_json["recording_ids"] == [control.recordings[0]["id"]]
    close_export_if_open(page)
    # A different selection after admission cannot expand the first task or start a duplicate.
    page.locator("#project-export-open").click()
    page.locator(f'[data-export-recording][value="{control.recordings[0]["id"]}"]').uncheck()
    select_export_formats(page, "text")
    if page.locator("#export-link").get_attribute("aria-disabled") != "true":
        with page.expect_response(
            lambda response: response.request.method == "POST" and "/export-tasks" in response.url
        ) as duplicate:
            page.locator("#export-link").click()
        assert duplicate.value.status == 409
    else:
        page.locator("#export-link").evaluate("node => node.click()")
    assert len(control.app.server.config.app.state.export_tasks.activity()) == 1
    explicit_starts = len(starts)
    close_export_if_open(page)
    if page.locator("#activity").is_visible():
        page.locator("#activity-close").click()
    rid = control.recordings[0]["id"]
    page.goto(control.app.url + f"/#/recordings/{rid}")
    page.locator("#passage-0 [data-action=edit]").click()
    page.locator("#passage-0 textarea").fill("New wording saved while media copying was blocked.")
    page.locator("#passage-0 [data-action=save-edit]").click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text(
        "New wording saved while media copying was blocked."
    )
    assert len(control.calls) == 1 and len(starts) == explicit_starts
    show_activity(page)
    expect(export_card(page, task_id)).to_contain_text("Preparing")
    control.release.set()
    task_state(page, task_id, "completed")
    page.reload()
    downloaded = download_background_export(page, task_id)
    with ZipFile(downloaded.path()) as archive:
        report = json.loads(archive.read("manifest.json"))
        assert {entry["recording_id"] for entry in report["entries"]} == {rid}
        transcript = next(entry for entry in report["entries"] if entry["format"] == "json")
        payload = json.loads(archive.read(transcript["path"]))
        assert payload["segments"][0]["text"] == "Saved wording 1."
        original = next(entry for entry in report["entries"] if entry.get("kind") == "source_media")
        assert archive.read(original["path"]) == control.originals[0]
    assert page.evaluate("window.archiveBlobReads") == 0
    assert len(starts) == explicit_starts and control.calls == [rid]


@pytest.mark.parametrize("ending", ["cancelled", "failed"])
def test_background_export_cancel_or_failure_never_restarts_and_can_be_discarded(
    page, blocked_export, ending
):
    control = blocked_export
    project_export(page, control)
    _, task_id = prepare_background_export(page)
    assert control.entered.wait(5)
    close_export_if_open(page)
    show_activity(page)
    card = export_card(page, task_id)
    if ending == "cancelled":
        card.locator("[data-export-cancel]").click()
    else:
        control.fail = True
    control.release.set()
    task_state(page, task_id, ending)
    page.reload()
    show_activity(page)
    expect(card).to_contain_text("cancelled" if ending == "cancelled" else "disk space")
    expect(card.locator("[data-export-download]")).to_have_count(0)
    expect(card.get_by_role("button", name="Retry", exact=True)).to_have_count(0)
    assert control.calls == [control.recordings[0]["id"]]
    card.locator("[data-export-discard]").click()
    expect(card).to_have_count(0)
    assert (
        control.store.directory(control.recordings[0]["id"]).joinpath("original.wav").read_bytes()
        == control.originals[0]
    )


def test_old_library_page_cannot_admit_a_background_export(page, blocked_export):
    control = blocked_export
    project_export(page, control)
    app = control.app.server.config.app
    instance = app.state.browser_instance
    app.state.browser_instance = "synthetic-replacement-instance"
    try:
        with page.expect_response(
            lambda response: response.request.method == "POST" and "/export-tasks" in response.url
        ) as response:
            page.locator("#export-link").click()
        assert response.value.status == 409
        expect(page.locator("#library-instance-notice")).to_be_visible()
        expect(page.locator("#export-dialog")).to_be_visible()
        assert app.state.export_tasks.activity() == [] and control.calls == []
    finally:
        app.state.browser_instance = instance


def test_old_library_page_cannot_download_a_prepared_zip(page, blocked_export):
    control = blocked_export
    project_export(page, control)
    _, task_id = prepare_background_export(page)
    control.release.set()
    task_state(page, task_id, "completed")
    close_export_if_open(page)
    show_activity(page)
    link = export_card(page, task_id).locator("[data-export-download]")
    expect(link).to_be_visible()
    downloads = []
    page.on("download", lambda download: downloads.append(download))
    app = control.app.server.config.app
    instance = app.state.browser_instance
    app.state.browser_instance = "synthetic-replacement-instance"
    try:
        link.click()
        expect(page.locator("#library-instance-notice")).to_be_visible()
        assert downloads == []
        assert app.state.export_tasks.activity()[0]["export_task"]["ready"]
    finally:
        app.state.browser_instance = instance
