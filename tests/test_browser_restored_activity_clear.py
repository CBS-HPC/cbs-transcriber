"""Clear archival Activity without changing its original queued/running evidence."""

import json

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import restored_owners
from cbs_transcriber.store import new_id

pytestmark = pytest.mark.browser


def test_clear_finished_hides_historical_jobs_and_runtime_owners_but_keeps_live_cleanup(
    page, local_app
):
    store, queue = local_app.server.config.app.state.store, local_app.server.config.app.state.queue
    project = store.create_project("Archival Activity")
    recording = store.create_recording(project["id"], "Archived interview.wav")
    job_id, batch_id, cleanup_id = new_id(), new_id(), new_id()
    stamp = "2000-01-01T00:00:00+00:00"
    # Publish the raw rows and quarantine atomically, before any live worker can see them.
    with queue.lock, store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='restored_owners'").fetchone():
            db.execute(restored_owners.DDL)
        db.execute(
            "INSERT INTO jobs(id,recording_id,kind,state,params,created,updated) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                job_id,
                recording["id"],
                "transcribe",
                "queued",
                "{old-private-evidence",
                stamp,
                stamp,
            ),
        )
        db.execute(
            "INSERT INTO runtime_batch_runs(id,project_id,compatibility_hash,identity,state,"
            "journal,created,updated) VALUES (?,?,?,?,?,?,?,?)",
            (
                batch_id,
                project["id"],
                "0" * 64,
                "{old-identity",
                "running",
                "{old-journal",
                stamp,
                stamp,
            ),
        )
        for kind, identifier in (("job", job_id), ("runtime_batch", batch_id)):
            db.execute(
                "INSERT INTO restored_owners VALUES (?,?,?,?)",
                (kind, identifier, "a" * 32, "b" * 32),
            )
        db.execute(
            "INSERT INTO jobs(id,recording_id,kind,state,message,params,created,updated) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                cleanup_id,
                recording["id"],
                "transcribe",
                "completed",
                "Current cleanup obligation",
                json.dumps(
                    {
                        "settings": {
                            "provider_type": "ucloud",
                            "base_url": "https://cloud.invalid",
                        },
                        "ucloud_run": {"cleanup_state": "pending"},
                    }
                ),
                stamp,
                stamp,
            ),
        )
    before = {
        table: store.all(f"SELECT * FROM {table} ORDER BY rowid")
        for table in ("jobs", "runtime_batch_runs", "restored_owners")
    }
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    cards = page.locator("#jobs .job")
    expect(cards).to_have_count(3)
    page.locator("#activity-clear").click()
    expect(cards).to_have_count(1)
    expect(cards).to_contain_text("Current cleanup obligation")
    expect(cards).to_contain_text("Retry remote cleanup")
    # The rendered list alone is filtered; API rows and frozen archival bytes remain exact.
    assert page.evaluate("state.jobs.filter(job => job.restored_history).length") == 2
    assert {table: store.all(f"SELECT * FROM {table} ORDER BY rowid") for table in before} == before
    page.reload()
    page.locator("#activity-open").click()
    expect(cards).to_have_count(1)
    expect(cards).to_contain_text("Current cleanup obligation")
