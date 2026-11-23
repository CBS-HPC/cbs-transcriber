"""Settings → Backup hands a local backup to Activity (ADR 0192), against a real server.

Synthetic library only; no UCloud connection, transfer or token is used. The server runs
in this process, so a monkeypatched capture seam holds its worker deterministically.
"""

import json
import threading

import pytest
from admission_support import write_original_wav
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser import serve_profile, settings_tabs, skip_first_use

from cbs_transcriber import backup_tasks
from cbs_transcriber import portable_transport as transport

pytestmark = pytest.mark.browser


def seed(local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Backup study")
    recording = store.create_recording(project["id"], "Interview.wav")
    write_original_wav(store, recording)
    return store


def watch_errors(page):
    errors = []
    page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
    return errors


def open_backup_tab(page, local_app):
    page.goto(local_app.url)
    skip_first_use(page)
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.get_by_role("tab")).to_have_text(settings_tabs(page))
    page.get_by_role("tab", name="Backup", exact=True).click()
    panel = page.locator("#backup-panel")
    expect(panel).to_be_visible()
    return panel


@pytest.fixture
def hold_copy(monkeypatch):
    """Hold the server's worker after it has copied its first file."""
    entered, release = threading.Event(), threading.Event()
    original = backup_tasks._Observer.copied_file

    def copied_file(self):
        original(self)
        if not entered.is_set():
            entered.set()
            while not release.wait(0.05):
                if self.task.closing:
                    return

    monkeypatch.setattr(backup_tasks._Observer, "copied_file", copied_file)
    yield entered, release
    release.set()


def test_settings_backup_tab_hands_off_and_stop_cleans_up(page, local_app, hold_copy):
    entered, release = hold_copy
    store = seed(local_app)
    errors = watch_errors(page)
    panel = open_backup_tab(page, local_app)
    expect(page.locator("#provider-fields")).to_be_hidden()
    expect(page.locator("#backup-settings-current")).to_contain_text(str(store.root))
    expect(page.locator("#backup-settings-last")).to_have_text(
        "No backup has been made of this library yet."
    )
    expect(page.locator("#backup-settings-destination")).to_have_attribute(
        "placeholder", str(store.root / "backups")
    )
    # Enter in the folder field never submits the provider form behind this tab.
    page.locator("#backup-settings-destination").press("Enter")
    expect(panel).to_be_visible()

    panel.get_by_role("button", name="Make backup", exact=True).click()
    assert entered.wait(20)
    expect(page.locator("#backup-settings-status")).to_contain_text("Backup started")
    expect(panel.get_by_role("button", name="Make backup", exact=True)).to_be_disabled()
    expect(page.locator("#backup-settings-running")).to_contain_text("A backup is being made")
    # Closing Settings does not stop the server-owned backup.
    panel.get_by_role("button", name="View Activity", exact=True).click()
    expect(page.locator("#settings-dialog")).to_be_hidden()
    expect(page.locator("#activity")).to_be_visible()
    card = page.locator("#jobs [data-backup-task]")
    expect(card).to_have_count(1)
    expect(card.locator("h3")).to_have_text("Making a backup")
    expect(card.locator('[data-backup-stage="copy"]')).to_have_attribute(
        "data-stage-status", "current"
    )
    expect(card.locator('[data-backup-stage="snapshot"]')).to_have_attribute(
        "data-stage-status", "done"
    )
    expect(card.locator("[data-backup-counts]")).to_contain_text("1 of 2 files")
    expect(card.locator("progress")).to_have_count(1)
    assert "Interview" not in card.inner_text()

    card.get_by_role("button", name="Stop backup", exact=True).click()
    expect(card.locator("p").first).to_have_text("Stopping the backup…")
    expect(card.get_by_role("button", name="Stop backup", exact=True)).to_have_count(0)
    release.set()
    expect(card.locator("h3")).to_have_text("Backup stopped", timeout=20000)
    expect(card).to_contain_text("No backup was made")
    expect(card).to_contain_text("incomplete copy was removed")
    expect(card.locator(".badge")).to_have_text("stopped")
    assert not list((store.root / "backups").glob(".portable-*"))
    assert not (store.root / "portable-captures").exists()
    card.get_by_role("button", name="Dismiss", exact=True).click()
    expect(card).to_have_count(0)
    assert not list((store.root / "backup-tasks").glob("*.json"))
    assert errors == []


def test_ready_backup_is_verified_with_a_receipt_and_listed_in_settings(page, local_app, tmp_path):
    store = seed(local_app)
    panel = open_backup_tab(page, local_app)
    panel.get_by_role("button", name="Make backup", exact=True).click()
    expect(page.locator("#backup-settings-status")).to_contain_text("Backup started")
    expect(page.locator("#backup-settings-last")).to_contain_text("Last backup:", timeout=60000)
    expect(panel.get_by_role("button", name="Make backup", exact=True)).to_be_enabled()
    expect(page.locator("#backup-settings-activity")).to_be_hidden()
    panel.get_by_role("button", name="Close", exact=True).click()
    expect(page.locator("#settings-dialog")).to_be_hidden()
    page.locator("#activity-open").click()
    card = page.locator("#jobs [data-backup-task]")
    expect(card.locator("h3")).to_have_text("Backup saved")
    expect(card.locator(".badge")).to_have_text("verified")
    expect(card).to_contain_text("Checked when it was made; this was not a test restore.")
    with page.expect_download() as download:
        card.get_by_role("button", name="Save receipt", exact=True).click()
    path = tmp_path / download.value.suggested_filename
    download.value.save_as(path)
    receipt = json.loads(path.read_text(encoding="utf-8"))
    assert receipt["credentials"] == "excluded"
    transport.inspect_transport(receipt["folder"], expected_commit_sha256=receipt["commit_sha256"])
    assert path.name == f"cbs-backup-{receipt['backup_id']}-receipt.json"
    [saved] = (store.root / "portable-captures").glob("*.json")
    assert (
        json.loads(saved.read_text(encoding="utf-8"))["commit_sha256"] == (receipt["commit_sha256"])
    )


def test_refusal_is_shown_in_settings_and_creates_no_card(page, local_app):
    store = seed(local_app)
    recording = store.all("SELECT id FROM recordings")[0]
    with local_app.server.config.app.state.queue.pause_admission():
        blocker = store.add_job(recording["id"], "media")
        store.execute("UPDATE jobs SET state='running' WHERE id=?", (blocker["id"],))
    panel = open_backup_tab(page, local_app)
    panel.get_by_role("button", name="Make backup", exact=True).click()
    expect(page.locator("#backup-settings-status")).to_contain_text(
        "Finish or cancel queued and running jobs"
    )
    expect(panel.get_by_role("button", name="Make backup", exact=True)).to_be_enabled()
    assert not (store.root / "backup-tasks").exists()
    store.execute("UPDATE jobs SET state='done' WHERE id=?", (blocker["id"],))


@pytest.fixture
def mvp_app(tmp_path_factory, monkeypatch):
    yield from serve_profile(tmp_path_factory, monkeypatch, "mvp")


def test_mvp_offers_local_backup_without_cloud_or_restore_entries(page, mvp_app):
    store = seed(mvp_app)
    panel = open_backup_tab(page, mvp_app)
    assert page.evaluate("featureOn('cloud_backup')") is False
    expect(panel.locator("[data-feature='cloud_backup']")).to_be_hidden()
    expect(panel.locator("[data-feature='restore_ui']")).to_be_hidden()
    panel.get_by_role("button", name="Make backup", exact=True).click()
    expect(page.locator("#backup-settings-last")).to_contain_text("Last backup:", timeout=60000)
    panel.get_by_role("button", name="Library backups", exact=True).click()
    expect(page.locator("#settings-dialog")).to_be_hidden()
    expect(page.locator("#library-dialog")).to_be_visible()
    expect(page.locator("#portable-simple-cloud")).to_be_hidden()
    assert len(list((store.root / "portable-captures").glob("*.json"))) == 1
