"""Historical Activity never interprets archived operational receipts as live protocols."""

import json

import pytest

from cbs_transcriber import (
    native_batch,
    original_inspection,
    restored_owners,
    runtime_batches,
    runtime_store,
)
from cbs_transcriber.store import new_id, now


def mark(store, kind, identifier):
    store.execute(
        "INSERT OR IGNORE INTO restored_owners VALUES (?,?,?,?)",
        (kind, identifier, "a" * 32, "b" * 32),
    )


def raw_owners(store):
    return {
        table: store.all(f"SELECT * FROM {table} ORDER BY rowid")
        for table in ("jobs", "native_batch_runs", "runtime_batch_runs", "runtime_batch_members")
    }


def archive(client):
    store = client.app.state.store
    with store.connection() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='restored_owners'").fetchone():
            db.execute(restored_owners.DDL)
    project = store.create_project("Readable archival work")
    recording = store.create_recording(project["id"], "Recording still present.wav")
    return store, project, recording


@pytest.mark.parametrize("params", ["{private-native", '["unsupported","legacy"]'])
def test_native_history_skips_live_batch_decoding_and_retains_group_metadata(
    client, monkeypatch, params
):
    store, project, recording = archive(client)
    batch_id, missing = new_id(), new_id()
    job = store.add_job(
        recording["id"],
        "transcribe",
        {
            "native_batch_id": batch_id,
            "segment_ids": "private-invalid-json",
            "ucloud_run": {"job_id": "private-remote", "cleanup_state": "pending"},
            "settings": {"provider_type": "ucloud", "base_url": "javascript:private-secret"},
        },
    )
    store.update_job(job["id"], state="running")
    store.execute(
        "INSERT INTO native_batch_runs(id,project_id,state,params,members,created,updated) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            batch_id,
            project["id"],
            "running",
            params,
            json.dumps(
                [
                    {"job_id": job["id"], "recording_id": recording["id"]},
                    {"job_id": missing, "recording_id": new_id()},
                ]
            ),
            now(),
            now(),
        ),
    )
    for kind, identifier in (("job", job["id"]), ("job", missing), ("native_batch", batch_id)):
        mark(store, kind, identifier)

    def refuse(*args, **kwargs):
        pytest.fail("Historical native Activity reached a live protocol decoder")

    monkeypatch.setattr(native_batch, "status_fields", refuse)
    monkeypatch.setattr(store, "job", refuse)
    before = raw_owners(store)
    response = client.get("/api/jobs")
    assert response.status_code == 200, response.text
    [historical] = response.json()
    assert historical["state"] == "historical"
    assert historical["archived_state"] == "running"
    assert historical["ucloud_batch_state"] == "historical"
    assert historical["ucloud_batch_count"] == 2
    assert historical["ucloud_batch_members"][0]["recording_name"] == recording["name"]
    assert historical["ucloud_batch_members"][1]["recording_name"] == "Removed recording"
    assert not historical["ucloud_batch_unresolved"]
    assert historical["ucloud_cleanup_state"] == "historical"
    assert historical["ucloud_job_id"] is None and historical["ucloud_server"] is None
    assert "private-" not in response.text
    assert raw_owners(store) == before


@pytest.mark.parametrize("journal", ["{private-journal", '["unsupported","legacy"]'])
def test_runtime_history_ignores_invalid_protocol_and_missing_attempts_or_members(
    client, monkeypatch, journal
):
    store, project, recording = archive(client)
    batch_id, missing, missing_recording = new_id(), new_id(), new_id()
    job = store.add_job(recording["id"], "enhance")
    store.update_job(job["id"], state="running")
    store.execute("UPDATE jobs SET params='{private-job' WHERE id=?", (job["id"],))
    store.execute(
        "INSERT INTO runtime_batch_runs(id,project_id,compatibility_hash,identity,state,journal,"
        "created,updated) VALUES (?,?,?,?,?,?,?,?)",
        (
            batch_id,
            project["id"],
            "0" * 64,
            "private-invalid-runtime-identity",
            "failed",
            journal,
            now(),
            now(),
        ),
    )
    for index, (identifier, recording_id) in enumerate(
        ((job["id"], recording["id"]), (missing, missing_recording))
    ):
        store.execute(
            "INSERT INTO runtime_batch_members(job_id,batch_id,recording_id,ordinal,"
            "source_identity,"
            "output_receipt,state,created,updated) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                batch_id,
                recording_id,
                index,
                "{private-source",
                "{private-output",
                "running",
                now(),
                now(),
            ),
        )
        mark(store, "job", identifier)
    mark(store, "runtime_batch", batch_id)

    def refuse(*args, **kwargs):
        pytest.fail("Historical Runtime Activity reached a live protocol decoder")

    for module, attribute in (
        (runtime_store, "get"),
        (runtime_batches, "_public_only"),
        (runtime_batches, "_label"),
        (runtime_batches, "_settings"),
    ):
        monkeypatch.setattr(module, attribute, refuse)
    before = raw_owners(store)
    assert runtime_store.claim(store) is None
    with store.connection() as db:
        assert runtime_store._capacity(db) == 0
    response = client.get("/api/jobs")
    assert response.status_code == 200, response.text
    rows = response.json()
    [owner] = [row for row in rows if row.get("runtime_batch")]
    [member] = [row for row in rows if row["id"] == job["id"]]
    assert member["runtime_batch_id"] == batch_id
    assert owner["state"] == "historical" and owner["archived_state"] == "failed"
    info = owner["runtime_batch"]
    assert info["member_count"] == 2
    assert info["members"][0]["recording_name"] == recording["name"]
    assert info["members"][1]["recording_name"] == "Removed recording"
    assert info["members"][1]["removed"]
    assert all(
        not info["can_" + operation] for operation in ("retry", "cancel", "cleanup", "discard")
    )
    assert not info["unresolved"] and info["retryable_members"] == 0
    assert owner["ucloud_job_id"] is None and owner["ucloud_server"] is None
    assert "private-" not in response.text
    assert raw_owners(store) == before
    # Archival malformed enhancement/owner state must not prevent fresh ordinary work.
    fresh = store.add_job(recording["id"], "media")
    assert store.claim_job("media")["id"] == fresh["id"]


def test_archived_runtime_member_projection_is_bounded_without_decoding_receipts(
    client, monkeypatch
):
    store, project, recording = archive(client)
    batch_id = new_id()
    store.execute(
        "INSERT INTO runtime_batch_runs(id,project_id,compatibility_hash,identity,journal,"
        "created,updated) "
        "VALUES (?,?,?,?,?,?,?)",
        (batch_id, project["id"], "0" * 64, "{", "{", now(), now()),
    )
    with store.connection() as db:
        for index in range(103):
            db.execute(
                "INSERT INTO runtime_batch_members(job_id,batch_id,recording_id,ordinal,"
                "source_identity,created,updated) "
                "VALUES (?,?,?,?,?,?,?)",
                (new_id(), batch_id, recording["id"], index, "{", now(), now()),
            )
    mark(store, "runtime_batch", batch_id)
    monkeypatch.setattr(runtime_store, "get", lambda *args: pytest.fail("live decode"))
    [owner] = runtime_batches.activity(store)
    assert owner["runtime_batch"]["member_count"] == 103
    assert len(owner["runtime_batch"]["members"]) == 100


@pytest.mark.parametrize("historical", [False, True])
def test_reset_working_inspection_cache_ignores_only_archived_inspector_rows(client, historical):
    store, _, recording = archive(client)
    with store.library_lock():
        before = original_inspection.ensure(store, recording["id"])
        old = store.job(before["inspection_job_id"])
        if historical:
            mark(store, "job", old["id"])
        # Portable restoration resets this disposable cache; it preserves every job row.
        store.execute("UPDATE recordings SET original_input=NULL WHERE id=?", (recording["id"],))
        if historical:
            fresh = original_inspection.ensure(store, recording["id"])
            assert fresh["inspection_job_id"] != old["id"]
            assert fresh["generation"] != before["generation"]
            assert fresh["state"] == "pending" and fresh["receipt"] is None
            assert original_inspection.read(store, recording["id"]) == fresh
        else:
            with pytest.raises(original_inspection.InspectionError):
                original_inspection.ensure(store, recording["id"])
        assert store.job(old["id"]) == old


def test_historical_jobs_do_not_publish_arbitrary_owner_pointer_bodies(client):
    store, _, recording = archive(client)
    job = store.add_job(recording["id"], "transcribe")
    store.update_job(
        job["id"],
        params={
            "native_batch_id": {"private-body": "x" * 1000},
            "runtime_batch_id": ["private-body", "y" * 1000],
        },
    )
    mark(store, "job", job["id"])
    response = client.get("/api/jobs")
    assert response.status_code == 200, response.text
    [historical] = response.json()
    assert historical["ucloud_batch_id"] is None and historical["runtime_batch_id"] is None
    assert "private-body" not in response.text
