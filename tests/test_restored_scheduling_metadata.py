"""Archival ownership is excluded before scheduling loops or body materialization."""

import json
import sqlite3

import pytest

from cbs_transcriber import original_inspection, restored_owners, runtime_store
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.store import new_id, now


def quarantine_table(store):
    with store.connection() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='restored_owners'").fetchone():
            db.execute(restored_owners.DDL)


def mark(store, kind, identifier):
    store.execute(
        "INSERT INTO restored_owners VALUES (?,?,?,?)", (kind, identifier, "a" * 32, "b" * 32)
    )


def test_source_lane_does_not_claim_each_archived_inspector_before_fresh_work(client, monkeypatch):
    store = client.app.state.store
    quarantine_table(store)
    project = store.create_project("Source inspection enumeration")
    archived = store.create_recording(project["id"], "archive.wav")
    fresh = store.create_recording(project["id"], "fresh.wav")
    with store.library_lock():
        old = original_inspection.ensure(store, archived["id"])
        mark(store, "job", old["inspection_job_id"])
        pending = original_inspection.ensure(store, fresh["id"])
        queue = JobQueue(store, client.app.state.credentials, kind="source-inspect")
        called = []

        def observe_claim(selected_store, identifier):
            assert selected_store is store
            called.append(identifier)
            return None

        class OneIteration:
            def wait(self, seconds):
                queue.stopping.set()

            def clear(self):
                pass

        queue.wakeup = OneIteration()
        monkeypatch.setattr(original_inspection, "claim", observe_claim)
        before = store.job(old["inspection_job_id"])
        queue.run()
        assert called == [pending["inspection_job_id"]]
        assert store.job(old["inspection_job_id"]) == before


@pytest.mark.parametrize("archival_kind", ["runtime_batch", "job"])
def test_capacity_never_materializes_archived_journals_or_request_bodies(client, archival_kind):
    store = client.app.state.store
    quarantine_table(store)
    project = store.create_project("Capacity metadata only")
    recording = store.create_recording(project["id"], "archive.wav")
    stamp = now()
    for historical in (False, True):
        if historical and archival_kind != "runtime_batch":
            continue
        identifier = new_id()
        journal = {"capacity_reserved": True}
        if historical:
            journal["private_archived_marker"] = "x" * 1000
        store.execute(
            "INSERT INTO runtime_batch_runs(id,project_id,compatibility_hash,identity,journal,"
            "created,updated) VALUES (?,?,?,?,?,?,?)",
            (identifier, project["id"], "0" * 64, "{}", json.dumps(journal), stamp, stamp),
        )
        if historical:
            mark(store, "runtime_batch", identifier)
    if archival_kind == "job":
        archived = store.add_job(
            recording["id"],
            "enhance",
            {
                "ucloud_api": {},
                "ucloud_run": {"capacity_reserved": True},
                "private_archived_marker": "y" * 1000,
            },
        )
        mark(store, "job", archived["id"])

    def no_archival_bodies(cursor, row):
        for column, value in zip(cursor.description, row, strict=True):
            if column[0] in {"journal", "params"} and isinstance(value, str):
                assert "private_archived_marker" not in value
        return sqlite3.Row(cursor, row)

    with store.connection() as db:
        db.row_factory = no_archival_bodies
        assert runtime_store._capacity(db) == 1  # The current allocation still consumes a slot.
