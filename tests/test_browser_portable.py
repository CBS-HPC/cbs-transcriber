"""Complete-backup panel in the Library dialog (ADR 0096), against a real local server.

Synthetic library only; no UCloud connection, transfer or token is used.
"""

import json
from pathlib import Path

import pytest
from admission_support import write_original_wav
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import portable_transport as transport

pytestmark = pytest.mark.browser


def watch_errors(page):
    errors = []
    page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
    page.on(
        "console",
        lambda message: (
            errors.append(f"console: {message.text}") if message.type == "error" else None
        ),
    )
    return errors


def test_create_complete_backup_from_library_dialog(page, local_app, tmp_path):
    store = local_app.server.config.app.state.store
    project = store.create_project("Portable panel")
    recording = store.create_recording(project["id"], "tiny.wav")
    write_original_wav(store, recording)
    # A running job no live worker owns (as after an unclean stop). Admission is paused while
    # inserting it so the real media worker cannot claim it first.
    with local_app.server.config.app.state.queue.pause_admission():
        blocker = store.add_job(recording["id"], "media")
        store.execute("UPDATE jobs SET state='running' WHERE id=?", (blocker["id"],))

    errors = watch_errors(page)
    page.goto(local_app.url)
    expect(page.locator("#library-open")).to_be_visible()
    # Removed v1 controls must not leave scripts referencing missing elements on load.
    assert errors == []

    page.locator("#library-open").click()
    panel = page.locator("#portable-panel")
    expect(panel).to_be_visible()
    expect(panel.get_by_role("button", name="Create backup", exact=True)).to_be_enabled()
    expect(page.locator("#portable-receipt")).to_be_disabled()
    expect(page.locator("#portable-keep")).to_have_value("3")
    expect(page.locator("#portable-age")).to_have_value("30")

    # Processing must be finished first; the refusal is shown and the button reopens.
    page.locator("#portable-create").click()
    status = page.locator("#portable-status")
    expect(status).to_contain_text("Finish or cancel queued and running jobs")
    expect(page.locator("#portable-create")).to_be_enabled()
    expect(page.locator("#portable-receipt")).to_be_disabled()

    store.execute("UPDATE jobs SET state='done' WHERE id=?", (blocker["id"],))
    page.locator("#portable-create").click()
    expect(status).to_contain_text("Complete backup saved: 1 original(s)", timeout=60000)
    expect(status).to_contain_text("Save the receipt outside your backup storage")
    expect(page.locator("#portable-receipt")).to_be_enabled()
    [receipt] = (store.root / "portable-captures").glob("*.json")
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    folder = Path(saved["folder"])
    transport.inspect_transport(folder, expected_commit_sha256=saved["commit_sha256"])
    expect(page.locator("#portable-folder")).to_have_value(saved["folder"])
    expect(page.locator("#portable-reference")).to_have_value(saved["commit_sha256"])
    expect(page.locator("#portable-restore-source")).to_have_value(saved["folder"])
    expect(page.locator("#portable-restore-reference")).to_have_value(saved["commit_sha256"])

    with page.expect_download() as download:
        page.locator("#portable-receipt").click()
    path = tmp_path / download.value.suggested_filename
    download.value.save_as(path)
    assert path.name == f"cbs-backup-{saved['backup_id']}-receipt.json"
    downloaded = json.loads(path.read_text(encoding="utf-8"))
    assert downloaded["commit_sha256"] == saved["commit_sha256"]
    assert downloaded["folder"] == saved["folder"] and downloaded["credentials"] == "excluded"

    # After the original request's response is lost, a page reload can recover the
    # creator receipt only by checking the actually published package.
    page.reload()
    page.locator("#library-open").click()
    panel.get_by_text("Restore a backup", exact=True).click()
    expect(page.locator("#portable-captures option")).to_have_count(2)
    page.locator("#portable-captures").select_option(saved["backup_id"])
    page.evaluate("""() => {
      const cloud = document.querySelector('#portable-restore-cloud');
      cloud.checked = true;
      cloud.dispatchEvent(new Event('input', {bubbles: true}));
    }""")
    page.locator("#portable-verify-capture").click()
    expect(status).to_contain_text(
        "Backup files matched the saved receipt when read", timeout=60000
    )
    expect(status).to_contain_text("not a test restore")
    expect(page.locator("#portable-receipt")).to_be_enabled()
    expect(page.locator("#portable-restore-reference")).to_have_value(saved["commit_sha256"])
    expect(page.locator("#portable-restore-cloud")).not_to_be_checked()

    # A long check must not replace a newer draft or erase an uncertain operation reference.
    page.evaluate("""() => {
      const realFetch = window.fetch;
      window.fetch = async (...args) => {
        if (String(args[0]).endsWith('/verify')) {
          await new Promise(resolve => { window.resumeBackupCheck = resolve; });
        }
        return realFetch(...args);
      };
    }""")
    page.locator("#portable-verify-capture").click()
    expect(status).to_contain_text("Checking backup files")
    page.evaluate("""() => {
      const folder = document.querySelector('#portable-folder');
      folder.value = 'newer draft';
      folder.dispatchEvent(new Event('input', {bubbles: true}));
      const operation = document.querySelector('#portable-upload-operation');
      operation.value = 'a'.repeat(32);
      operation.dispatchEvent(new Event('input', {bubbles: true}));
      folder.value = 'newer draft again';
      folder.dispatchEvent(new Event('input', {bubbles: true}));
      window.resumeBackupCheck();
    }""")
    expect(status).to_contain_text("your current choices and operation references were kept")
    expect(page.locator("#portable-folder")).to_have_value("newer draft again")
    expect(page.locator("#portable-upload-operation")).to_have_value("a" * 32)

    # A delayed upload completion must leave an unrelated restore draft and its ID alone.
    page.evaluate("""() => {
      const uploadId = document.querySelector('#portable-upload-operation');
      uploadId.value = '';
      uploadId.dispatchEvent(new Event('input', {bubbles: true}));
      const realFetch = window.fetch;
      window.fetch = async (...args) => {
        if (String(args[0]).endsWith('/portable-upload')) {
          await new Promise(resolve => { window.resumeBackupUpload = resolve; });
          return new Response(JSON.stringify({backup_id: 'c'.repeat(32),
            commit_sha256: 'd'.repeat(64), remote_folder: '/synthetic/backup',
            target: {server: 'https://example.invalid', project_id: 'synthetic'}}),
            {status: 200, headers: {'Content-Type': 'application/json'}});
        }
        return realFetch(...args);
      };
      document.querySelector('#portable-upload').dispatchEvent(new Event('click'));
    }""")
    expect(status).to_contain_text("Uploading the sealed package")
    page.evaluate("""() => {
      const operation = document.querySelector('#portable-restore-operation');
      operation.value = 'b'.repeat(32);
      operation.dispatchEvent(new Event('input', {bubbles: true}));
      const source = document.querySelector('#portable-restore-source');
      source.value = 'keep restore draft';
      source.dispatchEvent(new Event('input', {bubbles: true}));
      window.resumeBackupUpload();
    }""")
    expect(status).to_contain_text("Current restore choices and reference were kept")
    expect(page.locator("#portable-restore-source")).to_have_value("keep restore draft")
    expect(page.locator("#portable-restore-operation")).to_have_value("b" * 32)

    # Loading another receipt cannot erase an unresolved restore operation.
    page.locator("#portable-receipt-file").set_input_files(path)
    expect(status).to_contain_text("reconcile or clear the existing restore reference")
    expect(page.locator("#portable-restore-operation")).to_have_value("b" * 32)
    page.evaluate("""() => {
      const operation = document.querySelector('#portable-restore-operation');
      operation.value = '';
      operation.dispatchEvent(new Event('input', {bubbles: true}));
    }""")

    # The retained receipt fills the restore form; nothing is restored until asked.
    page.locator("#portable-restore-panel summary").first.evaluate(
        "e => { if (!e.parentElement.open) e.click(); }"
    )
    panel.get_by_text("Backup location options").click()
    page.locator("#portable-restore-source").fill("")
    page.evaluate("""() => {
      const input = document.querySelector('#portable-restore-reference');
      input.value = '';
      input.dispatchEvent(new Event('input', {bubbles: true}));
    }""")
    page.locator("#portable-receipt-file").set_input_files(path)
    expect(status).to_contain_text("Receipt loaded")
    expect(page.locator("#portable-restore-source")).to_have_value(saved["folder"])
    expect(page.locator("#portable-restore-reference")).to_have_value(saved["commit_sha256"])
    expect(page.locator("#portable-restore-cloud")).not_to_be_checked()

    # Storage cannot be chosen without a UCloud connection; nothing is saved.
    panel.get_by_text("UCloud backup", exact=True).click()
    expect(page.locator("#portable-simple-status")).to_contain_text("Connect UCloud in Settings")
    cloud_admissions = []
    page.on(
        "request",
        lambda request: (
            cloud_admissions.append(request.url)
            if request.method == "POST" and "/portable-simple/" in request.url
            else None
        ),
    )
    page.locator("#portable-simple-upload").click()
    expect(status).to_contain_text("Choose an available UCloud backup location first.")
    assert cloud_admissions == []
    assert not (store.root / "portable-cloud-settings.json").exists()

    # Earlier database-only inventories are hidden when none exist.
    expect(page.locator("#legacy-backups")).to_be_hidden()
    page.screenshot(path="test-results/portable-backup.png")
    # Only the refused capture and unconfigured UCloud-location lookup are logged;
    # no script error occurs.
    assert errors == [
        "console: Failed to load resource: the server responded with a status of 400 (Bad Request)",
        "console: Failed to load resource: the server responded with a status of 400 (Bad Request)",
    ]


def test_load_receipt_check_and_restore_into_separate_library(page, local_app, tmp_path):
    import sqlite3

    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic restore flow")
    recording = store.create_recording(project["id"], "tiny.wav")
    original = write_original_wav(store, recording)
    page.goto(local_app.url)
    page.locator("#library-open").click()
    page.locator("#portable-create").click()
    expect(page.locator("#portable-status")).to_contain_text("Complete backup saved", timeout=60000)
    with page.expect_download() as download:
        page.locator("#portable-receipt").click()
    receipt = tmp_path / "independent-receipt.json"
    download.value.save_as(receipt)

    page.get_by_text("Restore a backup", exact=True).click()
    expect(page.locator("#portable-restore-reference")).to_be_hidden()
    expect(page.locator("#portable-restore-recovery")).to_be_hidden()
    page.locator("#portable-receipt-file").set_input_files(receipt)
    expect(page.locator("#portable-status")).to_contain_text("Receipt loaded")
    expect(page.locator("#portable-selected-backup")).to_contain_text("this computer")
    expect(page.locator("#portable-restore")).to_be_disabled()
    page.locator("#portable-check-restore").click()
    expect(page.locator("#portable-check-status")).to_contain_text(
        "matched the trusted receipt", timeout=60000
    )
    target = tmp_path / "new-library"
    page.locator("#portable-restore-target").fill(str(target))
    page.locator("#portable-restore").click()
    expect(page.locator("#portable-status")).to_contain_text(
        "Restored library retained", timeout=60000
    )
    with sqlite3.connect(target / "library.sqlite3") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert target.resolve() != store.root.resolve()
    [restored_original] = target.rglob("original.wav")
    assert restored_original.read_bytes() == original.read_bytes()
