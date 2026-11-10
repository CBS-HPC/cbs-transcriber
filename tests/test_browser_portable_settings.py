"""Actual backup UI script with delayed synthetic replies; no server, Store or cloud."""

import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_browser import page as page

pytestmark = pytest.mark.browser
PROVIDER_A, PROVIDER_B = "a" * 32, "b" * 32
SOURCE = Path(__file__).parents[1] / "src/cbs_transcriber/static/portable.js"


@pytest.fixture
def panel(page):
    script = SOURCE.read_text(encoding="utf-8")
    identifiers = set(re.findall(r'"((?:portable-|library-)[a-z-]+)"', script))
    buttons = set(re.findall(r'bind\("([a-z-]+)"', script)) | {"library-open"}
    html = []
    for identifier in sorted(identifiers):
        if identifier in buttons:
            disabled = identifier in {
                "portable-upload",
                "portable-apply-retention",
                "portable-restore",
            }
            html.append(
                f'<button id="{identifier}" {"disabled" if disabled else ""}>Action</button>'
            )
        elif identifier == "library-dialog":
            html.append('<dialog id="library-dialog" open style="pointer-events:none"></dialog>')
        elif identifier == "portable-restore-recovery":
            html.append(
                '<details id="portable-restore-recovery" hidden>'
                "<summary>Recovery</summary></details>"
            )
        elif identifier in {
            "portable-provider",
            "portable-restore-history",
            "portable-simple-location",
            "portable-simple-recovery",
        }:
            html.append(f'<select id="{identifier}"></select>')
        elif identifier == "portable-receipt-file":
            html.append(f'<input id="{identifier}" type="file">')
        elif identifier in {"portable-simple-cloud", "portable-cloud-recovery"}:
            html.append(f'<details id="{identifier}"><summary>Open</summary></details>')
        elif identifier in {"portable-cloud-approved", "portable-restore-cloud"}:
            html.append(f'<input id="{identifier}" type="checkbox">')
        elif identifier in {
            "portable-status",
            "portable-destination-default",
            "portable-volume-hint",
            "portable-check-status",
            "portable-selected-backup",
            "portable-simple-status",
        }:
            html.append(f'<p id="{identifier}"></p>')
        elif identifier == "portable-retention-preview":
            html.append(f'<ul id="{identifier}"></ul>')
        else:
            html.append(f'<input id="{identifier}">')
    page.set_content("\n".join(html))
    page.evaluate("""() => {
      window.pending = [];
      // The in-memory about:blank fixture is not a secure context.
      let operationNumber = 0;
      Object.defineProperty(crypto, 'randomUUID', {value: () =>
        (++operationNumber).toString(16).padStart(32, '0')});
      window.api = (path, options = {}) => new Promise((resolve, reject) => {
        pending.push({path, options, resolve, reject});
      });
      document.getElementById('library-open').addEventListener('click', () => {
        const dialog = document.getElementById('library-dialog');
        if (!dialog.open) dialog.show();
      });
    }""")
    page.add_script_tag(content=script)
    return page


def settings(*, parent="/drive/saved-A", provider=PROVIDER_A, approved=True, keep=3):
    return {
        "storage_revision": "f" * 64,
        "target": {
            "provider_id": provider,
            "server": "https://cloud.example",
            "project_id": "project-a" if provider == PROVIDER_A else "project-b",
            "parent": parent,
            "allowed_transfer_origins": ["https://storage.example"],
            "whole_library_approved": approved,
            "fingerprint": "1" * 64,
        },
        "retention": {"keep_last": keep, "minimum_age_days": 30, "holds": []},
    }


def requests(page, path, method="GET"):
    return page.evaluate(
        """({path, method}) => pending.filter(r =>
          r.path === path && (r.options.method || 'GET') === method
        ).map(r => r.options.body || null)""",
        {"path": path, "method": method},
    )


def reply(page, path, value, *, method="GET", index=-1, error=False, status=None):
    if path == "/library/portable-storage" and method == "PUT" and not error:
        value = {**value, "storage_revision": "e" * 64, "fingerprint": "1" * 64}
    page.evaluate(
        """({path, method, value, index, error, status}) => {
          const request = pending.filter(r => r.path === path &&
            (r.options.method || 'GET') === method).at(index);
          if (!request) throw new Error('Missing synthetic request');
          if (error) { const failure = new Error(value); failure.status = status;
            request.reject(failure); } else request.resolve(value);
        }""",
        {
            "path": path,
            "method": method,
            "value": value,
            "index": index,
            "error": error,
            "status": status,
        },
    )


def open_simple(page, *, operations=None):
    page.evaluate(
        "window.confirm = () => true; document.getElementById('portable-simple-cloud').open = true"
    )
    page.wait_for_function("pending.some(r => r.path === '/library/portable-simple/locations')")
    reply(page, "/library/portable-operations", {"uploads": operations or [], "restores": []})
    reply(page, "/library/portable-storage", settings())
    reply(
        page,
        "/library/portable-simple/locations",
        {
            "connection": {
                "provider_id": PROVIDER_A,
                "server": "https://cloud.example",
                "project_id": "project-a",
            },
            "storage_revision": "f" * 64,
            "target": settings()["target"],
            "problem": None,
            "locations": [
                {"id": "/drive/saved-A", "label": "Research"},
                {"id": "/drive/B", "label": "Other"},
            ],
            "selected": "/drive/saved-A",
        },
    )
    expect(page.locator("#portable-simple-location")).to_have_value("/drive/saved-A")


def test_simple_stop_uses_displayed_snapshot_even_if_other_tab_changes(panel):
    open_simple(panel)
    panel.locator("#portable-simple-stop").click()
    assert requests(panel, "/library/portable-simple/stop", "POST") == [
        {
            "expected_revision": "f" * 64,
            "expected_target_fingerprint": "1" * 64,
        }
    ]
    reply(
        panel,
        "/library/portable-simple/stop",
        "Changed in another tab",
        method="POST",
        error=True,
        status=409,
    )
    expect(panel.locator("#portable-status")).to_contain_text("Changed in another tab")


@pytest.mark.parametrize("outcome", ["stopped", "conflict", "late"])
def test_simple_stop_invalidates_checked_cloud_restore_without_changing_draft(panel, outcome):
    opened(panel)
    panel.locator("#portable-restore-source").fill("/drive/old-backup")
    panel.locator("#portable-restore-reference").fill("a" * 64)
    panel.locator("#portable-restore-cloud").check()
    panel.locator("#portable-check-restore").click()
    reply(
        panel,
        "/library/portable-backup-check",
        {"verification": "transport_integrity_only"},
        method="POST",
    )
    expect(panel.locator("#portable-restore")).to_be_enabled()
    open_simple(panel)
    panel.locator("#portable-simple-stop").click()
    if outcome == "late":
        panel.evaluate("document.getElementById('library-dialog').close()")
        panel.locator("#library-open").click()
        panel.locator("#portable-restore-source").fill("/drive/newer-draft")
    if outcome == "conflict":
        reply(
            panel, "/library/portable-simple/stop", "Changed", method="POST", error=True, status=409
        )
    else:
        stopped = {
            **settings()["target"],
            "whole_library_approved": False,
            "storage_revision": "e" * 64,
            "restore_target_identity": "identity-A",
        }
        reply(panel, "/library/portable-simple/stop", stopped, method="POST")
    expect(panel.locator("#portable-restore")).to_be_disabled()
    expect(panel.locator("#portable-restore-source")).to_have_value(
        "/drive/newer-draft" if outcome == "late" else "/drive/old-backup"
    )


def test_late_simple_prepare_invalidates_cloud_restore_without_overwriting_new_draft(panel):
    opened(panel)
    saved = {
        "folder": "C:/backups/portable-" + "a" * 32,
        "backup_id": "a" * 32,
        "commit_sha256": "b" * 64,
        "originals": 0,
        "files": 1,
        "legacy_gaps": 0,
    }
    panel.locator("#portable-create").click()
    reply(panel, "/library/portable-backups", saved, method="POST")
    expect(panel.locator("#portable-folder")).to_have_value(saved["folder"])
    panel.locator("#portable-restore-source").fill("/drive/old-backup")
    panel.locator("#portable-restore-reference").fill("c" * 64)
    panel.locator("#portable-restore-cloud").check()
    panel.locator("#portable-check-restore").click()
    reply(
        panel,
        "/library/portable-backup-check",
        {"verification": "transport_integrity_only"},
        method="POST",
    )
    expect(panel.locator("#portable-restore")).to_be_enabled()
    open_simple(panel)
    panel.locator("#portable-simple-upload").click()
    panel.evaluate("document.getElementById('library-dialog').close()")
    panel.locator("#library-open").click()
    panel.locator("#portable-restore-source").fill("/drive/newer-draft")
    reply(
        panel,
        "/library/portable-simple/prepare",
        {
            **settings()["target"],
            "storage_revision": "e" * 64,
            "restore_target_identity": "identity-A",
        },
        method="POST",
    )
    expect(panel.locator("#portable-restore")).to_be_disabled()
    expect(panel.locator("#portable-restore-source")).to_have_value("/drive/newer-draft")
    assert not requests(panel, "/library/portable-simple/folder", "POST")


def test_simple_stop_available_before_remote_locations_respond(panel):
    panel.evaluate(
        "window.confirm = () => true; document.getElementById('portable-simple-cloud').open = true"
    )
    panel.wait_for_function("pending.some(r => r.path === '/library/portable-simple/locations')")
    reply(panel, "/library/portable-operations", {"uploads": [], "restores": []})
    reply(panel, "/library/portable-storage", settings())
    expect(panel.locator("#portable-simple-stop")).to_be_visible()
    expect(panel.locator("#portable-simple-stop-context")).to_contain_text("/drive/saved-A")
    panel.locator("#portable-simple-stop").click()
    assert requests(panel, "/library/portable-simple/stop", "POST") == [
        {
            "expected_revision": "f" * 64,
            "expected_target_fingerprint": "1" * 64,
        }
    ]


def test_simple_rejects_location_and_storage_from_different_revisions(panel):
    panel.evaluate("document.getElementById('portable-simple-cloud').open = true")
    panel.wait_for_function("pending.some(r => r.path === '/library/portable-simple/locations')")
    reply(panel, "/library/portable-operations", {"uploads": [], "restores": []})
    reply(panel, "/library/portable-storage", settings())
    reply(
        panel,
        "/library/portable-simple/locations",
        {
            "connection": {},
            "storage_revision": "e" * 64,
            "target": settings()["target"],
            "locations": [{"id": "/drive/other", "label": "Other"}],
            "selected": "/drive/other",
        },
    )
    expect(panel.locator("#portable-simple-status")).to_contain_text("changed while loading")
    assert not requests(panel, "/library/portable-simple/prepare", "POST")


def test_simple_late_location_reply_cannot_overwrite_successful_stop(panel):
    panel.evaluate(
        "window.confirm = () => true; document.getElementById('portable-simple-cloud').open = true"
    )
    panel.wait_for_function("pending.some(r => r.path === '/library/portable-simple/locations')")
    reply(panel, "/library/portable-operations", {"uploads": [], "restores": []})
    reply(panel, "/library/portable-storage", settings())
    expect(panel.locator("#portable-simple-stop")).to_be_visible()
    panel.locator("#portable-simple-stop").click()
    stopped = {
        **settings()["target"],
        "whole_library_approved": False,
        "storage_revision": "e" * 64,
    }
    reply(panel, "/library/portable-simple/stop", stopped, method="POST")
    expect(panel.locator("#portable-simple-status")).to_contain_text("New requests stopped")
    reply(
        panel,
        "/library/portable-simple/locations",
        {
            "connection": {},
            "storage_revision": "f" * 64,
            "target": settings()["target"],
            "locations": [{"id": "/drive/saved-A", "label": "Research"}],
            "selected": "/drive/saved-A",
        },
    )
    expect(panel.locator("#portable-simple-status")).to_contain_text("New requests stopped")
    expect(panel.locator("#portable-simple-stop")).to_be_hidden()


def test_simple_resume_late_receipt_cannot_overwrite_newer_draft(panel):
    operation = {
        "operation_id": "1" * 32,
        "backup_id": "a" * 32,
        "state": "uploading",
        "local_package": "C:/original",
        "commit_sha256": "a" * 64,
    }
    open_simple(panel, operations=[operation])
    panel.locator("#portable-simple-resume").click()
    panel.locator("#portable-folder").fill("C:/newer")
    reply(
        panel,
        f"/library/portable-backups/{'a' * 32}/verify",
        {"folder": "C:/original", "commit_sha256": "a" * 64},
    )
    expect(panel.locator("#portable-folder")).to_have_value("C:/newer")
    expect(panel.locator("#portable-status")).to_contain_text("choices changed")
    assert not requests(panel, "/library/portable-simple/prepare", "POST")


def test_simple_reconcile_late_preview_does_not_use_changed_selection(panel):
    operations = [
        {
            "operation_id": char * 32,
            "backup_id": char * 32,
            "state": "folder_uncertain",
            "local_package": "C:/original",
            "commit_sha256": char * 64,
        }
        for char in "12"
    ]
    open_simple(panel, operations=operations)
    panel.locator("#portable-simple-reconcile").click()
    panel.locator("#portable-simple-recovery").select_option("1")
    reply(
        panel,
        f"/library/portable-upload/{'1' * 32}/folder-preview",
        {"remote_folder": "/drive/exact", "observed": "empty_folder", "fingerprint": "a" * 64},
        method="POST",
    )
    expect(panel.locator("#portable-status")).to_contain_text("choices changed")
    assert not requests(panel, f"/library/portable-upload/{'1' * 32}/reconcile-folder", "POST")


def test_simple_upload_admits_receipt_before_upload_and_clears_completed_reference(panel):
    saved = {
        "folder": "C:/backups/portable-" + "a" * 32,
        "backup_id": "a" * 32,
        "commit_sha256": "b" * 64,
        "originals": 0,
        "files": 1,
        "legacy_gaps": 0,
    }
    panel.locator("#portable-create").click()
    reply(panel, "/library/portable-backups", saved, method="POST")
    expect(panel.locator("#portable-folder")).to_have_value(saved["folder"])
    open_simple(panel)
    panel.locator("#portable-simple-upload").click()
    reply(
        panel,
        "/library/portable-simple/prepare",
        {**settings()["target"], "storage_revision": "f" * 64},
        method="POST",
    )
    panel.wait_for_function("pending.some(r => r.path === '/library/portable-simple/folder')")
    reply(panel, "/library/portable-simple/folder", {"ready": True}, method="POST")
    panel.wait_for_function("pending.some(r => r.path === '/library/portable-simple/admit')")
    admission = requests(panel, "/library/portable-simple/admit", "POST")[0]
    assert admission["backup_id"] == saved["backup_id"]
    assert not requests(panel, "/library/portable-upload", "POST")
    reply(
        panel,
        "/library/portable-simple/admit",
        {"operation_id": admission["operation_id"]},
        method="POST",
    )
    panel.wait_for_function("pending.some(r => r.path === '/library/portable-upload')")
    upload = requests(panel, "/library/portable-upload", "POST")[0]
    assert upload["operation_id"] == admission["operation_id"]
    reply(
        panel,
        "/library/portable-upload",
        {
            "backup_id": saved["backup_id"],
            "commit_sha256": saved["commit_sha256"],
            "remote_folder": "/drive/cbs-backup-" + "a" * 32,
            "target": settings()["target"],
        },
        method="POST",
    )
    expect(panel.locator("#portable-upload-operation")).to_have_value("")


def test_simple_abandoned_admissions_do_not_appear_as_pending(panel):
    operation = {
        "operation_id": "1" * 32,
        "backup_id": "a" * 32,
        "state": "abandoned",
        "local_package": "C:/original",
        "commit_sha256": "a" * 64,
    }
    open_simple(panel, operations=[operation])
    expect(panel.locator("#portable-cloud-recovery")).to_be_hidden()


def test_simple_stop_displays_and_targets_newly_approved_location(panel):
    saved = {
        "folder": "C:/backups/portable-" + "a" * 32,
        "backup_id": "a" * 32,
        "commit_sha256": "b" * 64,
        "originals": 0,
        "files": 1,
        "legacy_gaps": 0,
    }
    panel.locator("#portable-create").click()
    reply(panel, "/library/portable-backups", saved, method="POST")
    expect(panel.locator("#portable-folder")).to_have_value(saved["folder"])
    open_simple(panel)
    panel.locator("#portable-simple-location").select_option("/drive/B")
    panel.locator("#portable-simple-upload").click()
    target = {
        **settings()["target"],
        "parent": "/drive/B",
        "fingerprint": "2" * 64,
        "storage_revision": "e" * 64,
    }
    reply(panel, "/library/portable-simple/prepare", target, method="POST")
    expect(panel.locator("#portable-simple-stop-context")).to_contain_text("/drive/B")
    panel.locator("#portable-simple-stop").click()
    assert requests(panel, "/library/portable-simple/stop", "POST") == [
        {
            "expected_revision": "e" * 64,
            "expected_target_fingerprint": "2" * 64,
        }
    ]
    reply(
        panel,
        "/library/portable-simple/stop",
        {**target, "whole_library_approved": False},
        method="POST",
    )
    reply(panel, "/library/portable-simple/folder", {"ready": True}, method="POST")
    expect(panel.locator("#portable-simple-stop")).to_be_hidden()
    assert not requests(panel, "/library/portable-simple/admit", "POST")


def test_simple_completed_receipt_appears_when_matching_creator_receipt_is_selected(panel):
    saved = {
        "folder": "C:/backups/portable-" + "a" * 32,
        "backup_id": "a" * 32,
        "commit_sha256": "b" * 64,
        "originals": 0,
        "files": 1,
        "legacy_gaps": 0,
    }
    completed = {
        "operation_id": "1" * 32,
        "backup_id": saved["backup_id"],
        "state": "complete",
        "local_package": saved["folder"],
        "commit_sha256": saved["commit_sha256"],
    }
    open_simple(panel, operations=[completed])
    expect(panel.locator("#portable-cloud-recovery")).to_be_hidden()
    panel.locator("#portable-create").click()
    reply(panel, "/library/portable-backups", saved, method="POST")
    panel.wait_for_function(
        "pending.filter(r => r.path === '/library/portable-operations').length === 2"
    )
    reply(panel, "/library/portable-operations", {"uploads": [completed], "restores": []})
    reply(panel, "/library/portable-storage", settings())
    reply(
        panel,
        "/library/portable-simple/locations",
        {
            "connection": {
                "provider_id": PROVIDER_A,
                "server": "https://cloud.example",
                "project_id": "project-a",
            },
            "storage_revision": "f" * 64,
            "target": settings()["target"],
            "problem": None,
            "locations": [{"id": "/drive/saved-A", "label": "Research"}],
            "selected": "/drive/saved-A",
        },
    )
    expect(panel.locator("#portable-cloud-recovery")).to_be_visible()
    expect(panel.locator("#portable-simple-recovery option")).to_have_text(
        "Earlier backup 1 — complete"
    )


def test_backup_destination_frozen_and_late_picker_does_not_overwrite_draft(panel):
    panel.locator("#library-open").click()
    reply(panel, "/storage", {"default_backup_parent": "C:/library/backups"})
    expect(panel.locator("#portable-destination-default")).to_have_text(
        "Default: C:/library/backups"
    )
    destination = panel.locator("#portable-destination")
    panel.locator("#portable-choose-destination").click()
    destination.fill("D:/newer-choice")
    reply(panel, "/storage/browse", {"path": "E:/old-choice"}, method="POST")
    expect(destination).to_have_value("D:/newer-choice")
    panel.locator("#portable-create").click()
    assert requests(panel, "/library/portable-backups", "POST") == [
        {"destination": "D:/newer-choice"}
    ]
    destination.fill("F:/next-backup")
    reply(
        panel,
        "/library/portable-backups",
        {"folder": "D:/newer-choice/portable-test", "commit_sha256": "f" * 64},
        method="POST",
    )
    expect(panel.locator("#portable-status")).to_contain_text("Earlier backup saved at")
    expect(destination).to_have_value("F:/next-backup")
    expect(panel.locator("#portable-folder")).to_have_value("")


def test_backup_volume_hint_is_advisory_and_ignores_stale_response(panel):
    destination = panel.locator("#portable-destination")
    hint = panel.locator("#portable-volume-hint")
    destination.fill("C:/first")
    panel.wait_for_function("pending.some(r => r.path === '/library/portable-backup-volume-hint')")
    destination.fill("D:/newer")
    reply(
        panel,
        "/library/portable-backup-volume-hint",
        {"relationship": "same_volume"},
        method="POST",
    )
    expect(hint).not_to_have_text("This folder is on the same filesystem volume as the library.")
    panel.wait_for_function(
        "pending.filter(r => r.path === '/library/portable-backup-volume-hint').length === 2"
    )
    reply(panel, "/library/portable-backup-volume-hint", {"relationship": "unknown"}, method="POST")
    expect(hint).to_contain_text("cannot determine")
    destination.fill("")
    panel.wait_for_function(
        "pending.filter(r => r.path === '/library/portable-backup-volume-hint').length === 3"
    )
    reply(
        panel,
        "/library/portable-backup-volume-hint",
        {"relationship": "same_volume"},
        method="POST",
    )
    expect(hint).to_contain_text("same filesystem volume")


def test_closed_library_discards_late_picker_reply(panel):
    destination = panel.locator("#portable-destination")
    panel.locator("#portable-choose-destination").click()
    panel.evaluate("document.getElementById('library-dialog').close()")
    reply(panel, "/storage/browse", {"path": "E:/late-choice"}, method="POST")
    expect(destination).to_have_value("")
    panel.locator("#library-open").click()
    expect(destination).to_have_value("")


def test_closed_library_discards_late_default_and_volume_replies(panel):
    panel.locator("#library-open").click()
    panel.wait_for_function("pending.some(r => r.path === '/library/portable-backup-volume-hint')")
    panel.evaluate("document.getElementById('library-dialog').close()")
    reply(panel, "/storage", {"default_backup_parent": "C:/old"})
    reply(
        panel,
        "/library/portable-backup-volume-hint",
        {"relationship": "same_volume"},
        method="POST",
    )
    expect(panel.locator("#portable-destination-default")).not_to_have_text("Default: C:/old")
    expect(panel.locator("#portable-volume-hint")).not_to_have_text(
        "This folder is on the same filesystem volume as the library."
    )


def load(page, value, *, index=-1):
    reply(page, "/library/portable-storage", value, index=index)
    reply(
        page,
        "/providers",
        {
            "providers": [
                {
                    "id": PROVIDER_A,
                    "name": "Synthetic A",
                    "provider_type": "ucloud",
                    "base_url": "https://cloud.example",
                    "ucloud_transcriber": {"project_id": "project-a"},
                },
                {
                    "id": PROVIDER_B,
                    "name": "Synthetic B",
                    "provider_type": "ucloud",
                    "base_url": "https://cloud.example",
                    "ucloud_transcriber": {"project_id": "project-b"},
                },
            ]
        },
        index=index,
    )


def opened(panel, value=None):
    panel.locator("#library-open").click()
    load(panel, value or settings())
    expect(panel.locator("#portable-provider option")).to_have_count(3)
    expect(panel.locator("#portable-cloud-parent")).to_have_value(
        (value or settings())["target"]["parent"]
    )


@pytest.mark.parametrize("field", ["parent", "provider", "origins", "approval"])
def test_stale_storage_save_cannot_reapprove_a_newer_draft(panel, field):
    opened(panel)
    panel.locator("#portable-save-cloud").click()
    assert requests(panel, "/library/portable-storage", "PUT")[0]["whole_library_approved"]
    if field == "parent":
        panel.locator("#portable-cloud-parent").fill("/drive/new-draft-B")
    elif field == "provider":
        panel.locator("#portable-provider").select_option(PROVIDER_B)
    elif field == "origins":
        panel.locator("#portable-origins").fill("https://different.example")
    else:
        panel.locator("#portable-cloud-approved").uncheck()
    expect(panel.locator("#portable-upload")).to_be_disabled()
    reply(panel, "/library/portable-storage", {"whole_library_approved": True}, method="PUT")
    expect(panel.locator("#portable-save-cloud")).to_be_enabled()
    expect(panel.locator("#portable-upload")).to_be_disabled()
    if field == "parent":
        expect(panel.locator("#portable-cloud-parent")).to_have_value("/drive/new-draft-B")
    elif field == "provider":
        expect(panel.locator("#portable-provider")).to_have_value(PROVIDER_B)
    elif field == "origins":
        expect(panel.locator("#portable-origins")).to_have_value("https://different.example")
    else:
        expect(panel.locator("#portable-cloud-approved")).not_to_be_checked()
    # The current draft can still be saved; its matching reply alone restores its approval.
    panel.locator("#portable-save-cloud").click()
    current = requests(panel, "/library/portable-storage", "PUT")[-1]
    reply(
        panel,
        "/library/portable-storage",
        {"whole_library_approved": current["whole_library_approved"]},
        method="PUT",
    )
    expect(panel.locator("#portable-upload")).to_be_enabled(
        enabled=current["whole_library_approved"]
    )


def test_late_load_preserves_cloud_and_retention_drafts_and_populates_choices(panel):
    panel.locator("#library-open").click()
    panel.locator("#portable-cloud-parent").fill("/drive/new-draft-B")
    panel.locator("#portable-origins").fill("https://different.example")
    panel.locator("#portable-cloud-approved").check()
    panel.locator("#portable-keep").fill("8")
    panel.locator("#portable-age").fill("60")
    panel.locator("#portable-holds").fill("c" * 32)
    load(panel, settings())
    expect(panel.locator("#portable-provider option")).to_have_count(3)
    expect(panel.locator("#portable-cloud-parent")).to_have_value("/drive/new-draft-B")
    expect(panel.locator("#portable-origins")).to_have_value("https://different.example")
    expect(panel.locator("#portable-cloud-approved")).to_be_checked()
    expect(panel.locator("#portable-keep")).to_have_value("8")
    expect(panel.locator("#portable-age")).to_have_value("60")
    expect(panel.locator("#portable-holds")).to_have_value("c" * 32)
    expect(panel.locator("#portable-upload")).to_be_disabled()


def test_reopening_preserves_drafts_and_selected_connection(panel):
    opened(panel)
    panel.locator("#portable-provider").select_option(PROVIDER_B)
    panel.locator("#portable-cloud-parent").fill("/drive/new-draft-B")
    panel.locator("#portable-cloud-approved").uncheck()
    panel.locator("#portable-keep").fill("8")
    panel.locator("#library-open").click()
    load(panel, settings())
    expect(panel.locator("#portable-provider")).to_have_value(PROVIDER_B)
    expect(panel.locator("#portable-cloud-parent")).to_have_value("/drive/new-draft-B")
    expect(panel.locator("#portable-cloud-approved")).not_to_be_checked()
    expect(panel.locator("#portable-keep")).to_have_value("8")
    expect(panel.locator("#portable-upload")).to_be_disabled()


def test_refresh_cannot_retarget_a_draft_when_its_connection_is_unavailable(panel):
    opened(panel)
    panel.locator("#portable-provider").select_option(PROVIDER_B)
    panel.locator("#library-open").click()
    reply(panel, "/library/portable-storage", settings())
    reply(
        panel,
        "/providers",
        {"providers": [{"id": PROVIDER_A, "name": "Synthetic A", "provider_type": "ucloud"}]},
    )
    expect(panel.locator("#portable-provider")).to_have_value(PROVIDER_B)
    expect(panel.locator("#portable-provider option:checked")).to_have_text(
        "Unavailable UCloud connection"
    )
    expect(panel.locator("#portable-upload")).to_be_disabled()


def test_connection_identity_placeholder_and_explicit_reapproval(panel):
    panel.locator("#library-open").click()
    load(panel, {**settings(), "target": None})
    expect(panel.locator("#portable-provider")).to_have_value("")
    expect(panel.locator(f'#portable-provider option[value="{PROVIDER_A}"]')).to_have_text(
        "Synthetic A · https://cloud.example · project-a"
    )
    panel.locator("#portable-save-cloud").click()
    expect(panel.locator("#portable-status")).to_contain_text("Choose a UCloud server")
    assert not requests(panel, "/library/portable-storage", "PUT")
    panel.locator("#portable-provider").select_option(PROVIDER_A)
    panel.locator("#portable-cloud-approved").check()
    panel.locator("#portable-provider").select_option(PROVIDER_B)
    expect(panel.locator("#portable-cloud-approved")).not_to_be_checked()
    expect(panel.locator("#portable-upload")).to_be_disabled()


def test_missing_saved_connection_can_be_replaced(panel):
    panel.locator("#library-open").click()
    load(panel, {**settings(), "target": None, "storage_problem": "Saved connection missing."})
    expect(panel.locator("#portable-status")).to_have_text("Saved connection missing.")
    expect(panel.locator("#portable-provider")).to_have_value("")
    expect(panel.locator("#portable-cloud-approved")).not_to_be_checked()
    panel.locator("#portable-provider").select_option(PROVIDER_B)
    expect(panel.locator("#portable-provider")).to_have_value(PROVIDER_B)


def test_held_save_rejects_changed_connection_and_clears_approval(panel):
    opened(panel)
    panel.locator("#portable-save-cloud").click()
    sent = requests(panel, "/library/portable-storage", "PUT")[-1]
    assert (sent["expected_server"], sent["expected_project_id"]) == (
        "https://cloud.example",
        "project-a",
    )
    reply(
        panel,
        "/library/portable-storage",
        "Connection changed",
        method="PUT",
        error=True,
        status=409,
    )
    expect(panel.locator("#portable-status")).to_have_text("Connection changed")
    expect(panel.locator("#portable-cloud-approved")).not_to_be_checked()
    expect(panel.locator("#portable-upload")).to_be_disabled()


def test_conflict_reload_preserves_draft_and_allows_fresh_review(panel):
    opened(panel)
    panel.locator("#portable-cloud-parent").fill("/drive/my-draft")
    panel.locator("#portable-cloud-approved").check()
    panel.locator("#portable-save-cloud").click()
    reply(
        panel,
        "/library/portable-storage",
        "Changed elsewhere",
        method="PUT",
        error=True,
        status=409,
    )
    expect(panel.locator("#portable-cloud-approved")).not_to_be_checked()
    panel.locator("#library-open").click()
    current = settings(parent="/drive/other-tab")
    current["target"]["allowed_transfer_origins"] = ["https://other-storage.example"]
    current["target"]["whole_library_approved"] = False
    load(panel, {**current, "storage_revision": "a" * 64})
    expect(panel.locator("#portable-cloud-parent")).to_have_value("/drive/my-draft")
    expect(panel.locator("#portable-status")).to_contain_text("/drive/other-tab")
    expect(panel.locator("#portable-status")).to_contain_text("https://other-storage.example")
    expect(panel.locator("#portable-status")).to_contain_text(
        "Whole-library approval: not approved"
    )
    expect(panel.locator("#portable-cloud-approved")).not_to_be_checked()
    panel.locator("#portable-cloud-approved").check()
    panel.locator("#portable-save-cloud").click()
    sent = requests(panel, "/library/portable-storage", "PUT")[-1]
    assert sent["expected_revision"] == "a" * 64
    assert sent["parent"] == "/drive/my-draft" and sent["whole_library_approved"] is True


def test_lost_save_reply_disables_upload_and_requires_reload(panel):
    opened(panel)
    expect(panel.locator("#portable-upload")).to_be_enabled()
    panel.locator("#portable-save-cloud").click()
    expect(panel.locator("#portable-upload")).to_be_disabled()
    reply(panel, "/library/portable-storage", "Connection lost", method="PUT", error=True)
    expect(panel.locator("#portable-cloud-approved")).not_to_be_checked()
    panel.locator("#portable-save-cloud").click()
    expect(panel.locator("#portable-status")).to_contain_text("Reopen Library")
    assert len(requests(panel, "/library/portable-storage", "PUT")) == 1
    panel.locator("#library-open").click()
    load(panel, {**settings(), "storage_revision": "a" * 64})
    expect(panel.locator("#portable-status")).to_contain_text("Whole-library approval: approved")
    expect(panel.locator("#portable-upload")).to_be_disabled()
    expect(panel.locator("#portable-cloud-approved")).not_to_be_checked()


def test_late_stale_action_does_not_clear_newer_draft_approval(panel):
    opened(panel)
    panel.locator("#portable-upload").click()
    panel.locator("#portable-cloud-parent").fill("/drive/new-draft")
    panel.locator("#portable-cloud-approved").check()
    reply(panel, "/library/portable-upload", "Changed", method="POST", error=True, status=409)
    expect(panel.locator("#portable-cloud-approved")).to_be_checked()
    expect(panel.locator("#portable-upload")).to_be_disabled()


def test_late_stale_action_does_not_clear_reloaded_approval(panel):
    opened(panel)
    panel.locator("#portable-upload").click()
    assert requests(panel, "/library/portable-upload", "POST"), panel.locator(
        "#portable-status"
    ).text_content()
    panel.locator("#library-open").click()
    load(panel, {**settings(), "storage_revision": "a" * 64})
    expect(panel.locator("#portable-cloud-approved")).to_be_checked()
    reply(panel, "/library/portable-upload", "Old conflict", method="POST", error=True, status=409)
    expect(panel.locator("#portable-cloud-approved")).to_be_checked()
    expect(panel.locator("#portable-status")).to_contain_text(
        "Earlier UCloud backup upload: Old conflict"
    )


def test_late_lost_restore_reply_reports_uncertainty_without_changing_draft(panel):
    opened(panel)
    panel.locator("#portable-restore-source").fill("C:/synthetic-backup")
    panel.locator("#portable-restore-reference").fill("a" * 64)
    panel.locator("#portable-check-restore").click()
    reply(
        panel,
        "/library/portable-backup-check",
        {"verification": "transport_integrity_only"},
        method="POST",
    )
    expect(panel.locator("#portable-restore")).to_be_enabled()
    panel.locator("#portable-restore").click()
    assert requests(panel, "/library/portable-restore", "POST"), panel.locator(
        "#portable-status"
    ).text_content()
    panel.locator("#library-open").click()
    load(panel, settings())
    panel.locator("#portable-restore-source").fill("new restore draft")
    reply(panel, "/library/portable-restore", "Connection lost", method="POST", error=True)
    expect(panel.locator("#portable-status")).to_contain_text("Earlier restore: Connection lost")
    expect(panel.locator("#portable-status")).to_contain_text("outcome may be uncertain")
    expect(panel.locator("#portable-restore-source")).to_have_value("new restore draft")
    expect(panel.locator("#portable-restore-operation")).not_to_have_value("")
    assert panel.locator("#portable-restore-recovery").evaluate("element => element.open")


def test_restore_recovery_details_are_shown_on_request(panel):
    opened(panel)
    recovery = panel.locator("#portable-restore-recovery")
    expect(recovery).to_be_hidden()
    panel.locator("#portable-show-restore-recovery").click()
    assert recovery.evaluate("element => element.open && !element.hidden")


def test_previous_restore_loads_frozen_choices_and_requires_new_check(panel):
    opened(panel)
    panel.locator("#portable-show-restore-recovery").click()
    panel.locator("#portable-find-restores").click()
    reply(
        panel,
        "/library/portable-operations",
        {
            "restores": [
                {
                    "operation_id": "b" * 32,
                    "folder": "C:/backup",
                    "commit_sha256": "a" * 64,
                    "target": "C:/restored",
                    "cloud": False,
                    "target_identity": None,
                }
            ]
        },
    )
    panel.locator("#portable-load-restore-history").click()
    expect(panel.locator("#portable-status")).to_contain_text("independently saved receipt")
    expect(panel.locator("#portable-restore-operation")).to_have_value("")
    receipt = panel.locator("#portable-receipt-file")
    receipt.set_input_files(
        {
            "name": "receipt.json",
            "mimeType": "application/json",
            "buffer": json.dumps({"folder": "C:/backup", "commit_sha256": "c" * 64}).encode(),
        }
    )
    panel.locator("#portable-load-restore-history").click()
    expect(panel.locator("#portable-restore-operation")).to_have_value("")
    receipt.set_input_files(
        {
            "name": "receipt.json",
            "mimeType": "application/json",
            "buffer": json.dumps({"folder": "C:/backup", "commit_sha256": "a" * 64}).encode(),
        }
    )
    panel.locator("#portable-load-restore-history").click()
    expect(panel.locator("#portable-restore-source")).to_have_value("C:/backup")
    expect(panel.locator("#portable-restore-target")).to_have_value("C:/restored")
    expect(panel.locator("#portable-restore-operation")).to_have_value("b" * 32)
    expect(panel.locator("#portable-restore")).to_be_disabled()


def test_loaded_backup_must_be_checked_and_stale_check_cannot_enable_restore(panel):
    opened(panel)
    source = panel.locator("#portable-restore-source")
    reference = panel.locator("#portable-restore-reference")
    restore = panel.locator("#portable-restore")
    source.fill("C:/old-backup")
    reference.fill("a" * 64)
    expect(restore).to_be_disabled()
    panel.locator("#portable-check-restore").click()
    source.fill("C:/new-backup")
    reply(
        panel,
        "/library/portable-backup-check",
        {"verification": "transport_integrity_only"},
        method="POST",
    )
    expect(restore).to_be_disabled()
    expect(source).to_have_value("C:/new-backup")
    panel.locator("#portable-check-restore").click()
    reply(
        panel,
        "/library/portable-backup-check",
        "Incomplete backup",
        method="POST",
        error=True,
        status=400,
    )
    expect(restore).to_be_disabled()
    expect(panel.locator("#portable-check-status")).to_contain_text("incomplete or unavailable")
    panel.locator("#portable-check-restore").click()
    reply(
        panel,
        "/library/portable-backup-check",
        {"verification": "transport_integrity_only"},
        method="POST",
    )
    expect(restore).to_be_enabled()
    reference.fill("b" * 64)
    expect(restore).to_be_disabled()


def test_checking_earlier_backup_preserves_unrelated_upload_draft(panel, tmp_path):
    opened(panel)
    panel.locator("#portable-folder").fill("C:/upload-draft")
    panel.locator("#portable-reference").fill("a" * 64)
    panel.locator("#portable-captures").fill("b" * 32)
    panel.locator("#portable-verify-capture").click()
    saved = {
        "backup_id": "b" * 32,
        "folder": "C:/earlier-backup",
        "commit_sha256": "c" * 64,
        "verification": "transport_integrity_only",
    }
    reply(panel, "/library/portable-backups/" + "b" * 32 + "/verify", saved)
    expect(panel.locator("#portable-restore")).to_be_enabled()
    expect(panel.locator("#portable-restore-source")).to_have_value(saved["folder"])
    expect(panel.locator("#portable-folder")).to_have_value("C:/upload-draft")
    expect(panel.locator("#portable-reference")).to_have_value("a" * 64)
    with panel.expect_download() as download:
        panel.locator("#portable-receipt").click()
    path = tmp_path / download.value.suggested_filename
    download.value.save_as(path)
    assert json.loads(path.read_text(encoding="utf-8"))["folder"] == saved["folder"]


def test_check_cloud_backup_uses_reviewed_destination_and_independent_reference(panel):
    opened(panel)
    receipt = {
        "remote_folder": "/drive/saved-A/cbs-backup-" + "a" * 32,
        "commit_sha256": "b" * 64,
        "server": "https://cloud.example",
        "project_id": "project-a",
    }
    panel.locator("#portable-receipt-file").set_input_files(
        {
            "name": "receipt.json",
            "mimeType": "application/json",
            "buffer": json.dumps(receipt).encode(),
        }
    )
    expect(panel.locator("#portable-restore-cloud")).to_be_checked()
    panel.locator("#portable-check-restore").click()
    [body] = requests(panel, "/library/portable-backup-check", "POST")
    assert body["folder"] == receipt["remote_folder"]
    assert body["commit_sha256"] == receipt["commit_sha256"]
    assert body["expected_revision"] == "f" * 64
    assert body["expected_target_fingerprint"] == "1" * 64
    assert body["receipt_server"] == receipt["server"]
    assert body["receipt_project_id"] == receipt["project_id"]
    reply(
        panel,
        "/library/portable-backup-check",
        {"verification": "transport_integrity_only"},
        method="POST",
    )
    expect(panel.locator("#portable-restore")).to_be_enabled()
    panel.locator("#portable-cloud-parent").fill("/drive/other")
    expect(panel.locator("#portable-restore")).to_be_disabled()


def test_current_cloud_check_conflict_clears_old_approval(panel):
    opened(panel)
    panel.locator("#portable-restore-source").fill("/drive/saved-A/cbs-backup-" + "a" * 32)
    panel.locator("#portable-restore-reference").fill("b" * 64)
    panel.locator("#portable-restore-cloud").check()
    panel.locator("#portable-check-restore").click()
    reply(
        panel,
        "/library/portable-backup-check",
        "Storage changed",
        method="POST",
        error=True,
        status=409,
    )
    expect(panel.locator("#portable-check-status")).to_contain_text("incomplete or unavailable")
    expect(panel.locator("#portable-cloud-approved")).not_to_be_checked()
    expect(panel.locator("#portable-upload")).to_be_disabled()
    expect(panel.locator("#portable-restore")).to_be_disabled()


def test_late_cloud_check_conflict_preserves_newer_approval(panel):
    opened(panel)
    panel.locator("#portable-restore-source").fill("/drive/saved-A/cbs-backup-" + "a" * 32)
    panel.locator("#portable-restore-reference").fill("b" * 64)
    panel.locator("#portable-restore-cloud").check()
    panel.locator("#portable-check-restore").click()
    panel.locator("#portable-cloud-parent").fill("/drive/newer")
    panel.locator("#portable-cloud-approved").check()
    reply(
        panel,
        "/library/portable-backup-check",
        "Old conflict",
        method="POST",
        error=True,
        status=409,
    )
    expect(panel.locator("#portable-cloud-approved")).to_be_checked()
    expect(panel.locator("#portable-restore")).to_be_disabled()
    expect(panel.locator("#portable-status")).to_contain_text("earlier backup check failed")


def test_late_storage_load_invalidates_cloud_check_without_losing_source(panel):
    opened(panel)
    source = panel.locator("#portable-restore-source")
    source.fill("/drive/saved-A/cbs-backup-" + "a" * 32)
    panel.locator("#portable-restore-reference").fill("b" * 64)
    panel.locator("#portable-restore-cloud").check()
    panel.locator("#library-open").click()
    panel.locator("#portable-check-restore").click()
    reply(
        panel,
        "/library/portable-backup-check",
        {"verification": "transport_integrity_only"},
        method="POST",
    )
    expect(panel.locator("#portable-restore")).to_be_enabled()
    fresh = settings()
    fresh["storage_revision"] = "a" * 64
    fresh["target"]["fingerprint"] = "2" * 64
    load(panel, fresh)
    expect(panel.locator("#portable-restore")).to_be_disabled()
    expect(panel.locator("#portable-check-status")).to_contain_text("Check the backup")
    expect(source).to_have_value("/drive/saved-A/cbs-backup-" + "a" * 32)


def test_out_of_sync_target_and_choices_cannot_display_old_approval(panel):
    panel.locator("#library-open").click()
    load(panel, {**settings(), "target": {**settings()["target"], "project_id": "old-project"}})
    expect(panel.locator("#portable-cloud-approved")).not_to_be_checked()
    expect(panel.locator("#portable-upload")).to_be_disabled()
    expect(panel.locator("#portable-status")).to_contain_text("changed")


@pytest.mark.parametrize("change", ["server", "project", "removed"])
def test_refresh_of_approved_dirty_draft_does_not_transfer_consent(panel, change):
    opened(panel)
    panel.locator("#portable-cloud-parent").fill("/drive/new-draft")
    panel.locator("#portable-cloud-approved").check()
    panel.locator("#library-open").click()
    reply(panel, "/library/portable-storage", settings())
    provider = {
        "id": PROVIDER_A,
        "name": "Synthetic A",
        "provider_type": "ucloud",
        "base_url": "https://changed.example" if change == "server" else "https://cloud.example",
        "ucloud_transcriber": {
            "project_id": "changed-project" if change == "project" else "project-a"
        },
    }
    reply(panel, "/providers", {"providers": [] if change == "removed" else [provider]})
    expect(panel.locator("#portable-cloud-approved")).not_to_be_checked()
    expect(panel.locator("#portable-upload")).to_be_disabled()
    expect(panel.locator("#portable-status")).to_contain_text("changed")
    if change != "removed":
        panel.locator("#portable-save-cloud").click()
        sent = requests(panel, "/library/portable-storage", "PUT")[-1]
        assert sent["whole_library_approved"] is False


@pytest.mark.parametrize("section", ["cloud", "retention"])
def test_stale_save_reply_cannot_mark_a_newer_draft_saved(panel, section):
    opened(panel)
    button = panel.locator(f"#portable-save-{section}")
    button.click()
    cloud = section == "cloud"
    field = panel.locator("#portable-cloud-parent" if cloud else "#portable-keep")
    field.fill("/drive/new-draft-B" if cloud else "8")
    panel.locator("#portable-status").evaluate("node => node.textContent = 'Current draft'")
    reply(
        panel,
        "/library/portable-storage" if cloud else "/library/portable-retention",
        {"whole_library_approved": True} if cloud else {},
        method="PUT",
    )
    expect(button).to_be_enabled()
    expect(panel.locator("#portable-status")).to_have_text("Current draft")
    # A later panel load must still recognize the unsaved draft after the stale success.
    panel.locator("#library-open").click()
    load(panel, settings())
    expect(field).to_have_value("/drive/new-draft-B" if cloud else "8")


def test_latest_load_wins_and_absent_target_clears_old_approval(panel):
    opened(panel)
    panel.locator("#library-open").click()
    panel.locator("#library-open").click()
    absent = settings(keep=7)
    absent["target"] = None
    load(panel, absent)
    expect(panel.locator("#portable-cloud-parent")).to_have_value("")
    expect(panel.locator("#portable-cloud-approved")).not_to_be_checked()
    expect(panel.locator("#portable-upload")).to_be_disabled()
    load(panel, settings(), index=1)
    expect(panel.locator("#portable-cloud-parent")).to_have_value("")
    expect(panel.locator("#portable-keep")).to_have_value("7")
    expect(panel.locator("#portable-upload")).to_be_disabled()


@pytest.mark.parametrize("load_during_save", [False, True])
def test_load_cannot_replace_a_save_started_before_or_after_it(panel, load_during_save):
    opened(panel)
    if not load_during_save:
        panel.locator("#library-open").click()
    panel.locator("#portable-cloud-parent").fill("/drive/new-saved-B")
    panel.locator("#portable-cloud-approved").check()
    panel.locator("#portable-keep").fill("8")
    panel.locator("#portable-save-cloud").click()
    panel.locator("#portable-save-retention").click()
    if load_during_save:
        panel.locator("#library-open").click()
    reply(panel, "/library/portable-storage", {"whole_library_approved": True}, method="PUT")
    reply(panel, "/library/portable-retention", {}, method="PUT")
    expect(panel.locator("#portable-save-retention")).to_be_enabled()
    expect(panel.locator("#portable-upload")).to_be_enabled()
    load(panel, settings())
    expect(panel.locator("#portable-cloud-parent")).to_have_value("/drive/new-saved-B")
    expect(panel.locator("#portable-keep")).to_have_value("8")
    expect(panel.locator("#portable-upload")).to_be_enabled()
    # Once the draft has a matching successful save, a fresh load is allowed to refresh it.
    panel.locator("#library-open").click()
    load(panel, settings(parent="/drive/new-saved-B", keep=8))
    expect(panel.locator("#portable-cloud-parent")).to_have_value("/drive/new-saved-B")
    expect(panel.locator("#portable-keep")).to_have_value("8")


@pytest.mark.parametrize("section", ["cloud", "retention"])
def test_settings_save_error_preserves_draft_for_retry(panel, section):
    opened(panel)
    cloud = section == "cloud"
    field = panel.locator("#portable-cloud-parent" if cloud else "#portable-keep")
    field.fill("/drive/new-draft-B" if cloud else "8")
    path = "/library/portable-storage" if cloud else "/library/portable-retention"
    button = panel.locator(f"#portable-save-{section}")
    button.click()
    reply(panel, path, "Synthetic save failure", method="PUT", error=True)
    expect(button).to_be_enabled()
    expect(panel.locator("#portable-status")).to_have_text("Synthetic save failure")
    panel.locator("#library-open").click()
    load(panel, settings())
    expect(field).to_have_value("/drive/new-draft-B" if cloud else "8")
    if cloud:
        expect(panel.locator("#portable-upload")).to_be_disabled()
        panel.locator("#portable-cloud-approved").check()
    button.click()
    reply(panel, path, {"whole_library_approved": True} if cloud else {}, method="PUT")
    expect(button).to_be_enabled()
    expect(panel.locator("#portable-status")).to_contain_text(
        "Backup storage choices saved" if cloud else "Manual retention policy saved"
    )
    if cloud:
        expect(panel.locator("#portable-upload")).to_be_enabled()


def test_matching_save_reply_cannot_grant_unrequested_approval(panel):
    opened(panel)
    panel.locator("#portable-cloud-approved").uncheck()
    panel.locator("#portable-save-cloud").click()
    assert (
        requests(panel, "/library/portable-storage", "PUT")[-1]["whole_library_approved"] is False
    )
    reply(panel, "/library/portable-storage", {"whole_library_approved": True}, method="PUT")
    expect(panel.locator("#portable-save-cloud")).to_be_enabled()
    expect(panel.locator("#portable-cloud-approved")).not_to_be_checked()
    expect(panel.locator("#portable-upload")).to_be_disabled()


@pytest.mark.parametrize("section", ["cloud", "retention"])
def test_late_retention_preview_cannot_revive_apply_after_settings_edit(panel, section):
    opened(panel)
    panel.locator("#portable-preview-retention").click()
    if section == "cloud":
        panel.locator("#portable-cloud-parent").fill("/drive/new-draft-B")
    else:
        panel.locator("#portable-keep").fill("8")
    reply(
        panel,
        "/library/portable-retention/preview",
        {
            "fingerprint": "old-preview",
            "rows": [
                {"backup_id": "synthetic-backup", "completed_at": "2026-01-01", "eligible": True}
            ],
            "eligible_operations": ["c" * 32],
        },
        method="POST",
    )
    expect(panel.locator("#portable-preview-retention")).to_be_enabled()
    expect(panel.locator("#portable-apply-retention")).to_be_disabled()


def test_retention_preview_failure_retry_and_confirm_use_current_fingerprint(panel):
    opened(panel)
    preview = panel.locator("#portable-preview-retention")
    preview.click()
    reply(
        panel,
        "/library/portable-retention/preview",
        "Synthetic preview failure",
        method="POST",
        error=True,
    )
    expect(preview).to_be_enabled()
    expect(panel.locator("#portable-status")).to_have_text("Synthetic preview failure")
    expect(panel.locator("#portable-apply-retention")).to_be_disabled()
    preview.click()
    reply(
        panel,
        "/library/portable-retention/preview",
        {
            "fingerprint": "current-preview",
            "rows": [
                {"backup_id": "synthetic-backup", "completed_at": "2026-01-01", "eligible": True}
            ],
            "eligible_operations": ["c" * 32],
        },
        method="POST",
    )
    apply = panel.locator("#portable-apply-retention")
    expect(apply).to_be_enabled()
    expect(panel.locator("#portable-retention-preview")).to_contain_text(
        "Backup from 2026-01-01: eligible for UCloud Trash"
    )
    panel.once("dialog", lambda dialog: dialog.dismiss())
    apply.click()
    assert requests(panel, "/library/portable-retention/apply", "POST") == []
    expect(apply).to_be_enabled()
    panel.once("dialog", lambda dialog: dialog.accept())
    apply.click()
    assert requests(panel, "/library/portable-retention/apply", "POST") == [
        {"fingerprint": "current-preview", "confirmed": True}
    ]
    reply(
        panel,
        "/library/portable-retention/apply",
        {"event_id": "synthetic-event", "outcomes": [{"state": "trashed"}]},
        method="POST",
    )
    expect(panel.locator("#portable-status")).to_contain_text("Retention result: trashed")
    expect(apply).to_be_disabled()


def retention_result(fingerprint, count=1):
    return {
        "fingerprint": fingerprint,
        "rows": [{"backup_id": fingerprint, "completed_at": fingerprint, "eligible": True}],
        "eligible_operations": [str(index) * 32 for index in range(count)],
    }


def show_preview(panel, fingerprint, *, count=1):
    panel.locator("#portable-preview-retention").click()
    reply(
        panel,
        "/library/portable-retention/preview",
        retention_result(fingerprint, count),
        method="POST",
    )
    expect(panel.locator("#portable-retention-preview")).to_contain_text(fingerprint)


def test_cloud_save_cannot_leave_an_old_target_preview_actionable(panel):
    opened(panel)
    panel.locator("#portable-cloud-parent").fill("/drive/new-target-B")
    panel.locator("#portable-cloud-approved").check()
    panel.locator("#portable-save-cloud").click()
    panel.locator("#portable-preview-retention").click()
    # f23 could request the old target's preview during this held save. The fixed UI refuses
    # the request; if any older request exists, completing Save must still invalidate it.
    if requests(panel, "/library/portable-retention/preview", "POST"):
        reply(
            panel,
            "/library/portable-retention/preview",
            retention_result("old-target-A"),
            method="POST",
        )
        expect(panel.locator("#portable-preview-retention")).to_be_enabled()
    reply(panel, "/library/portable-storage", {"whole_library_approved": True}, method="PUT")
    expect(panel.locator("#portable-save-cloud")).to_be_enabled()
    expect(panel.locator("#portable-upload")).to_be_enabled()
    expect(panel.locator("#portable-apply-retention")).to_be_disabled()
    show_preview(panel, "new-target-B")
    expect(panel.locator("#portable-apply-retention")).to_be_enabled()


@pytest.mark.parametrize("section", ["cloud", "retention"])
def test_retention_requires_matching_saved_settings_not_the_visible_unsaved_draft(panel, section):
    opened(panel)
    cloud = section == "cloud"
    field = panel.locator("#portable-cloud-parent" if cloud else "#portable-keep")
    field.fill("/drive/new-target-B" if cloud else "8")
    preview = panel.locator("#portable-preview-retention")
    preview.click()
    assert requests(panel, "/library/portable-retention/preview", "POST") == []
    expect(panel.locator("#portable-status")).to_contain_text("Save the current")
    expect(panel.locator("#portable-apply-retention")).to_be_disabled()
    save = panel.locator(f"#portable-save-{section}")
    save.click()
    preview.click()
    assert requests(panel, "/library/portable-retention/preview", "POST") == []
    path = "/library/portable-storage" if cloud else "/library/portable-retention"
    sent = requests(panel, path, "PUT")[-1]
    assert sent["parent" if cloud else "keep_last"] == ("/drive/new-target-B" if cloud else 8)
    # A definite rejection is retryable; a lost reply requires a reload instead.
    reply(panel, path, "Synthetic save failure", method="PUT", error=True, status=400)
    expect(save).to_be_enabled()
    preview.click()
    assert requests(panel, "/library/portable-retention/preview", "POST") == []
    save.click()
    reply(panel, path, {"whole_library_approved": True} if cloud else {}, method="PUT")
    expect(save).to_be_enabled()
    show_preview(panel, "saved-current-choice")
    expect(panel.locator("#portable-apply-retention")).to_be_enabled()


@pytest.mark.parametrize("failed", [False, True])
def test_late_apply_reply_preserves_a_newer_preview_and_reports_earlier_outcome(panel, failed):
    opened(panel)
    show_preview(panel, "preview-A")
    apply = panel.locator("#portable-apply-retention")
    panel.once("dialog", lambda dialog: dialog.accept())
    apply.click()
    assert requests(panel, "/library/portable-retention/apply", "POST") == [
        {"fingerprint": "preview-A", "confirmed": True}
    ]
    show_preview(panel, "preview-B", count=2)
    expect(apply).to_be_disabled()
    reply(
        panel,
        "/library/portable-retention/apply",
        "Earlier synthetic Apply failed"
        if failed
        else {"event_id": "earlier-event-A", "outcomes": [{"state": "trashed"}]},
        method="POST",
        error=failed,
    )
    expect(apply).to_be_enabled()
    expect(panel.locator("#portable-retention-preview")).to_contain_text("preview-B")
    expect(panel.locator("#portable-status")).to_contain_text("2 backup(s) eligible")
    expect(panel.locator("#portable-status")).to_contain_text(
        "Earlier synthetic Apply failed"
        if failed
        else "Earlier retention request: Retention result: trashed"
    )
    panel.once("dialog", lambda dialog: dialog.accept())
    apply.click()
    assert requests(panel, "/library/portable-retention/apply", "POST")[-1] == {
        "fingerprint": "preview-B",
        "confirmed": True,
    }
    reply(
        panel,
        "/library/portable-retention/apply",
        {"event_id": "current-event-B", "outcomes": []},
        method="POST",
    )
    expect(panel.locator("#portable-status")).to_contain_text("Retention result:")
    expect(apply).to_be_disabled()


def test_apply_error_keeps_the_matching_preview_retryable(panel):
    opened(panel)
    show_preview(panel, "retryable-preview")
    apply = panel.locator("#portable-apply-retention")
    panel.once("dialog", lambda dialog: dialog.accept())
    apply.click()
    reply(
        panel,
        "/library/portable-retention/apply",
        "Synthetic Apply failure",
        method="POST",
        error=True,
    )
    expect(apply).to_be_enabled()
    expect(panel.locator("#portable-status")).to_have_text("Synthetic Apply failure")
    panel.once("dialog", lambda dialog: dialog.accept())
    apply.click()
    assert requests(panel, "/library/portable-retention/apply", "POST") == [
        {"fingerprint": "retryable-preview", "confirmed": True},
        {"fingerprint": "retryable-preview", "confirmed": True},
    ]
    reply(
        panel,
        "/library/portable-retention/apply",
        {"event_id": "retry-success", "outcomes": []},
        method="POST",
    )
    expect(panel.locator("#portable-status")).to_contain_text("Retention result:")
    expect(apply).to_be_disabled()
