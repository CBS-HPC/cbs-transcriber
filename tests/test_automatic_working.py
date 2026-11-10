"""Automatic Working follows only an exact frozen, still-current role."""

import json
import sqlite3
from copy import deepcopy

import pytest
from test_input_admission import item
from test_input_admission import library as library

from cbs_transcriber import input_admission, restored_owners, result_roles
from cbs_transcriber import store as store_module
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def synthetic_store(tmp_path):
    return store_module.Store(tmp_path / "synthetic-library")


def make_recording(store):
    project = store.create_project("Synthetic")
    return store.create_recording(project["id"], "synthetic.wav")


def add(store, recording_id, text, **kwargs):
    return store.add_result(
        recording_id,
        normalize_segments([{"start": 0, "end": 1, "text": text}]),
        {},
        "Synthetic result",
        {},
        **kwargs,
    )


def role(store, recording_id):
    with store.connection() as db:
        return result_roles.read(db, store, recording_id)


def enhancement_job(store, recording_id, frozen, expected):
    job = store.add_job(recording_id, "enhance", {"source_snapshot": frozen})
    store.execute(
        "UPDATE jobs SET params=? WHERE id=?",
        (
            json.dumps(
                {
                    "source_snapshot": frozen,
                    "working_role": expected,
                    "working_role_policy": result_roles.WORKING_POLICY,
                }
            ),
            job["id"],
        ),
    )
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        result_roles.authorize_job(db, store, job["id"], recording_id, "result", expected, frozen)
    return job


def mark(store, recording_id, result_id):
    version = store.result_versions(recording_id, result_id)[0]
    recording = store.recording(recording_id)
    state = role(store, recording_id)
    preview = result_roles.preview(
        store, recording_id, version["id"], recording["revision"], state["revision"]
    )
    result_roles.apply(
        store,
        recording_id,
        version["id"],
        recording["revision"],
        state["revision"],
        preview["fingerprint"],
    )


def test_new_admission_freezes_working_role_in_accepted_digest(library):
    store, _, provider, records = library
    with store.library_lock():
        job = input_admission.accept(store, [item(provider, records[0])])[0]
    intent = job["params"]["input_intent"]
    assert intent["baseline"]["working_role"] == {"revision": 0, "working": None}
    input_admission._validate(intent)
    intent["baseline"]["working_role"]["revision"] = 1
    with pytest.raises(ValueError):
        input_admission._validate(intent)


def test_schema17_migration_preserves_existing_role_history(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 17)
    old = store_module.Store(tmp_path / "library")
    record = make_recording(old)
    saved = add(old, record["id"], "Legacy")
    mark(old, record["id"], saved["id"])
    before = old.all("SELECT * FROM result_role_events ORDER BY sequence")
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 18)
    migrated = store_module.Store(old.root)
    assert migrated.all("SELECT * FROM result_role_events ORDER BY sequence") == before
    assert role(migrated, record["id"])["working"]["result_id"] == saved["id"]
    assert list((old.root / "backups").glob("schema-17-*.sqlite3"))


def test_first_result_is_automatic_but_old_results_and_retries_do_not_move_working(synthetic_store):
    store = synthetic_store
    record = make_recording(store)
    first = add(store, record["id"], "First", auto_first=True)
    state = role(store, record["id"])
    assert state["working"]["result_id"] == first["id"]
    event = store.one("SELECT * FROM result_role_events WHERE recording_id=?", (record["id"],))
    assert (event["action"], event["actor_kind"]) == ("working_auto_first", "system")
    with store.connection() as db:
        history = result_roles.public_history(db, store, record["id"], "synthetic")
    assert history["events"][0]["actor_kind"] == "system"
    add(store, record["id"], "Alternative", auto_first=True)
    assert role(store, record["id"]) == state

    older = make_recording(store)
    add(store, older["id"], "Legacy")
    add(store, older["id"], "New", auto_first=True)
    assert role(store, older["id"])["working"] is None


def test_automatic_role_event_failure_rolls_back_the_result(synthetic_store):
    store = synthetic_store
    record = make_recording(store)
    store.execute(
        "CREATE TRIGGER reject_auto BEFORE INSERT ON result_role_events "
        "BEGIN SELECT RAISE(ABORT, 'synthetic role failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic role failure"):
        add(store, record["id"], "First", auto_first=True)
    assert not store.all("SELECT id FROM transcript_results WHERE recording_id=?", (record["id"],))
    assert role(store, record["id"])["working"] is None


def test_first_fresh_job_sets_working_and_legacy_job_does_not(library):
    store, _, provider, records = library
    fresh = records[0]
    with store.library_lock():
        job = input_admission.accept(store, [item(provider, fresh)])[0]
    intent = job["params"]["input_intent"]
    store.execute(
        "UPDATE jobs SET params=? WHERE id=?",
        (
            json.dumps(
                {
                    "input_acceptance": {
                        "working_role": intent["baseline"]["working_role"],
                        "accepted_sha256": intent["accepted_sha256"],
                    }
                }
            ),
            job["id"],
        ),
    )
    result = add(store, fresh["id"], "New", job_id=job["id"])
    assert role(store, fresh["id"])["working"]["result_id"] == result["id"]
    legacy = make_recording(store)
    old_job = store.add_job(legacy["id"], "transcribe", {})
    add(store, legacy["id"], "Legacy", job_id=old_job["id"])
    assert role(store, legacy["id"])["working"] is None


def test_followup_cannot_forge_a_working_designation(library):
    store, _, provider, records = library
    record = records[0]
    with store.library_lock():
        job = input_admission.accept(store, [item(provider, record)])[0]
    intent = job["params"]["input_intent"]
    params = {
        "input_acceptance": {
            "accepted_sha256": intent["accepted_sha256"],
            "working_role": intent["baseline"]["working_role"],
        },
        "followup": {
            "id": store_module.new_id(),
            "params": {"working_role": {"revision": 0, "working": None}},
        },
    }
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job["id"]))
    with pytest.raises(ValueError, match="cannot assert a Working designation"):
        add(store, record["id"], "Synthetic", job_id=job["id"])
    assert not store.all("SELECT id FROM transcript_results WHERE recording_id=?", (record["id"],))
    assert role(store, record["id"])["working"] is None


def test_processing_successor_follows_only_frozen_unchanged_working(synthetic_store):
    store = synthetic_store
    record = make_recording(store)
    first = add(store, record["id"], "First", auto_first=True)
    current = store.recording(record["id"])
    frozen = store.freeze_result(record["id"], first["id"], current["revision"])
    expected = role(store, record["id"])
    job = enhancement_job(store, record["id"], frozen, expected)
    successor = add(store, record["id"], "Processed", job_id=job["id"])
    after = role(store, record["id"])
    assert after["working"]["result_id"] == successor["id"]
    events = store.all(
        "SELECT * FROM result_role_events WHERE recording_id=? ORDER BY sequence", (record["id"],)
    )
    assert [(event["action"], event["actor_kind"]) for event in events] == [
        ("working_auto_first", "system"),
        ("working_auto_successor", "system"),
    ]
    assert add(store, record["id"], "Retry", job_id=job["id"])["id"] == successor["id"]
    assert role(store, record["id"]) == after


def test_processing_does_not_override_an_explicit_newer_choice(synthetic_store):
    store = synthetic_store
    record = make_recording(store)
    first = add(store, record["id"], "First", auto_first=True)
    frozen = store.freeze_result(
        record["id"], first["id"], store.recording(record["id"])["revision"]
    )
    job = enhancement_job(store, record["id"], frozen, role(store, record["id"]))
    alternate = add(store, record["id"], "Alternative")
    mark(store, record["id"], alternate["id"])
    chosen = role(store, record["id"])
    add(store, record["id"], "Processed", job_id=job["id"])
    assert role(store, record["id"]) == chosen


@pytest.mark.parametrize("assigned", [True, False])
def test_explicit_keep_fences_earlier_processing(synthetic_store, assigned):
    store = synthetic_store
    record = make_recording(store)
    source = add(store, record["id"], "First", auto_first=assigned)
    frozen = store.freeze_result(
        record["id"], source["id"], store.recording(record["id"])["revision"]
    )
    previous = role(store, record["id"])
    job = enhancement_job(store, record["id"], frozen, previous)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        result_roles.keep_consented(db, store, record["id"], previous)
    kept = role(store, record["id"])
    assert kept == {"revision": previous["revision"] + 1, "working": previous["working"]}
    add(store, record["id"], "Processed", job_id=job["id"])
    assert role(store, record["id"]) == kept
    with store.connection() as db:
        history = result_roles.public_history(db, store, record["id"], "synthetic")
    assert history["events"][-1]["action"] == "working_kept"
    assert history["events"][-1]["actor_kind"] == "local_user"


def test_explicitly_reselecting_same_working_records_intent(synthetic_store):
    store = synthetic_store
    record = make_recording(store)
    first = add(store, record["id"], "First", auto_first=True)
    previous = role(store, record["id"])
    mark(store, record["id"], first["id"])
    kept = role(store, record["id"])
    assert kept == {"revision": previous["revision"] + 1, "working": previous["working"]}


def test_legacy_unmarked_role_field_cannot_trigger_promotion(synthetic_store):
    store = synthetic_store
    record = make_recording(store)
    first = add(store, record["id"], "First", auto_first=True)
    frozen = store.freeze_result(
        record["id"], first["id"], store.recording(record["id"])["revision"]
    )
    expected = role(store, record["id"])
    job = store.add_job(record["id"], "enhance", {"source_snapshot": frozen})
    store.execute(
        "UPDATE jobs SET params=? WHERE id=?",
        (json.dumps({"source_snapshot": frozen, "working_role": expected}), job["id"]),
    )
    add(store, record["id"], "Processed", job_id=job["id"])
    assert role(store, record["id"]) == expected


def test_authority_rejects_forged_source_projection(synthetic_store):
    store = synthetic_store
    record = make_recording(store)
    first = add(store, record["id"], "First", auto_first=True)
    frozen = store.freeze_result(
        record["id"], first["id"], store.recording(record["id"])["revision"]
    )
    forged = deepcopy(frozen)
    forged["segments"][0]["text"] = "Forged"
    expected = role(store, record["id"])
    job = store.add_job(record["id"], "enhance", {"source_snapshot": forged})
    store.execute(
        "UPDATE jobs SET params=? WHERE id=?",
        (
            json.dumps(
                {
                    "source_snapshot": forged,
                    "working_role": expected,
                    "working_role_policy": result_roles.WORKING_POLICY,
                }
            ),
            job["id"],
        ),
    )
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="does not match its saved version"):
            result_roles.authorize_job(
                db, store, job["id"], record["id"], "result", expected, forged
            )


def test_authority_rejects_source_number_type_mismatch(synthetic_store):
    store = synthetic_store
    record = make_recording(store)
    first = add(store, record["id"], "First", auto_first=True)
    frozen = store.freeze_result(
        record["id"], first["id"], store.recording(record["id"])["revision"]
    )
    altered = deepcopy(frozen)
    start = frozen["segments"][0]["start"]
    altered["segments"][0]["start"] = float(start) if type(start) is int else int(start)
    assert type(altered["segments"][0]["start"]) is not type(frozen["segments"][0]["start"])
    expected = role(store, record["id"])
    job = store.add_job(record["id"], "enhance", {"source_snapshot": altered})
    store.execute(
        "UPDATE jobs SET params=? WHERE id=?",
        (
            json.dumps(
                {
                    "source_snapshot": altered,
                    "working_role": expected,
                    "working_role_policy": result_roles.WORKING_POLICY,
                }
            ),
            job["id"],
        ),
    )
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="differs from the processing job"):
            result_roles.authorize_job(
                db, store, job["id"], record["id"], "result", expected, frozen
            )


def test_promotion_rechecks_restored_owner_in_writer_transaction(synthetic_store, monkeypatch):
    store = synthetic_store
    record = make_recording(store)
    first = add(store, record["id"], "First", auto_first=True)
    frozen = store.freeze_result(
        record["id"], first["id"], store.recording(record["id"])["revision"]
    )
    expected = role(store, record["id"])
    job = enhancement_job(store, record["id"], frozen, expected)

    def quarantine(owner, identifier):
        owner.execute(
            "INSERT INTO restored_owners VALUES ('job',?,?,?)",
            (identifier, "synthetic-restore", "synthetic-backup"),
        )

    monkeypatch.setattr(restored_owners, "require_job", quarantine)
    with pytest.raises(restored_owners.HistoricalOwner):
        add(store, record["id"], "Processed", job_id=job["id"])
    assert role(store, record["id"]) == expected
    assert (
        len(store.all("SELECT id FROM transcript_results WHERE recording_id=?", (record["id"],)))
        == 1
    )


def test_timing_projection_serializes_selected_passage_once(synthetic_store, monkeypatch):
    store = synthetic_store
    record = make_recording(store)
    result = store.add_result(
        record["id"],
        normalize_segments([{"text": f"Passage {i}"} for i in range(120)]),
        {},
        "Synthetic",
        {},
        auto_first=True,
    )
    source = store.freeze_result(
        record["id"], result["id"], store.recording(record["id"])["revision"]
    )
    source["segments"] = [source["segments"][-1]]
    target = source["segments"][0]
    expected = role(store, record["id"])
    job = store.add_job(record["id"], "enhance", {"source_snapshot": source})
    store.execute(
        "UPDATE jobs SET params=? WHERE id=?",
        (
            json.dumps(
                {
                    "source_snapshot": source,
                    "timing_working_policy": {
                        "format": "cbs-timing-working-v1",
                        "expected_role": expected,
                    },
                }
            ),
            job["id"],
        ),
    )
    dumps, count = json.dumps, 0

    def counted(value, *args, **kwargs):
        nonlocal count
        if value is target:
            count += 1
        return dumps(value, *args, **kwargs)

    monkeypatch.setattr(result_roles.json, "dumps", counted)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        result_roles.authorize_job(db, store, job["id"], record["id"], "timing", expected, source)
    assert count == 1


def test_authority_is_removed_with_its_recording_and_source(synthetic_store):
    store = synthetic_store
    record = make_recording(store)
    first = add(store, record["id"], "First", auto_first=True)
    frozen = store.freeze_result(
        record["id"], first["id"], store.recording(record["id"])["revision"]
    )
    job = enhancement_job(store, record["id"], frozen, role(store, record["id"]))
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM recordings WHERE id=?", (record["id"],))
    assert not store.all("SELECT job_id FROM working_job_authorities WHERE job_id=?", (job["id"],))


def test_authority_survives_trash_then_is_removed_with_project(synthetic_store):
    store = synthetic_store
    record = make_recording(store)
    first = add(store, record["id"], "First", auto_first=True)
    frozen = store.freeze_result(
        record["id"], first["id"], store.recording(record["id"])["revision"]
    )
    job = enhancement_job(store, record["id"], frozen, role(store, record["id"]))
    store.set_result_trashed(
        record["id"], first["id"], store.recording(record["id"])["revision"], trashed=True
    )
    assert store.all("SELECT job_id FROM working_job_authorities WHERE job_id=?", (job["id"],))
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM projects WHERE id=?", (record["project_id"],))
    assert not store.all("SELECT job_id FROM working_job_authorities WHERE job_id=?", (job["id"],))


def test_authority_rejects_deeply_nested_corrupt_role(synthetic_store):
    store = synthetic_store
    record = make_recording(store)
    first = add(store, record["id"], "First", auto_first=True)
    frozen = store.freeze_result(
        record["id"], first["id"], store.recording(record["id"])["revision"]
    )
    job = enhancement_job(store, record["id"], frozen, role(store, record["id"]))
    store.execute(
        "UPDATE working_job_authorities SET role_json=? WHERE job_id=?",
        ("[" * 1100 + "0" + "]" * 1100, job["id"]),
    )
    with store.connection() as db:
        with pytest.raises(ValueError, match="authority is invalid"):
            result_roles.job_authority(db, job["id"], record["id"], "result")


def test_authority_rejects_the_wrong_job_kind(library):
    store, _, provider, records = library
    with store.library_lock():
        job = input_admission.accept(store, [item(provider, records[0])])[0]
    store.execute("UPDATE jobs SET kind='media' WHERE id=?", (job["id"],))
    with store.connection() as db:
        with pytest.raises(ValueError, match="source is invalid"):
            result_roles.job_authority(db, job["id"], records[0]["id"], "result")


def test_authority_role_must_match_its_exact_history_event(synthetic_store):
    store = synthetic_store
    record = make_recording(store)
    first = add(store, record["id"], "First", auto_first=True)
    previous = role(store, record["id"])
    alternate = add(store, record["id"], "Alternative")
    mark(store, record["id"], alternate["id"])
    current = role(store, record["id"])
    frozen = store.freeze_result(
        record["id"], first["id"], store.recording(record["id"])["revision"]
    )
    job = enhancement_job(store, record["id"], frozen, current)
    forged = {"revision": current["revision"], "working": previous["working"]}
    store.execute(
        "UPDATE working_job_authorities SET role_json=? WHERE job_id=?",
        (json.dumps(forged, ensure_ascii=False, sort_keys=True, separators=(",", ":")), job["id"]),
    )
    with store.connection() as db:
        with pytest.raises(ValueError, match="no matching history"):
            result_roles.job_authority(db, job["id"], record["id"], "result")


def test_authority_rejects_corrupt_keep_event_hash(synthetic_store):
    store = synthetic_store
    record = make_recording(store)
    first = add(store, record["id"], "First", auto_first=True)
    previous = role(store, record["id"])
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        result_roles.keep_consented(db, store, record["id"], previous)
    expected = role(store, record["id"])
    frozen = store.freeze_result(
        record["id"], first["id"], store.recording(record["id"])["revision"]
    )
    job = enhancement_job(store, record["id"], frozen, expected)
    store.execute(
        "UPDATE result_role_events SET previous_payload_sha256=? "
        "WHERE recording_id=? AND sequence=?",
        ("0" * 64, record["id"], expected["revision"]),
    )
    with store.connection() as db:
        with pytest.raises(ValueError, match="no matching history"):
            result_roles.job_authority(db, job["id"], record["id"], "result")


def test_authority_rejects_unknown_policy_even_if_sql_check_was_bypassed(synthetic_store):
    store = synthetic_store
    record = make_recording(store)
    first = add(store, record["id"], "First", auto_first=True)
    frozen = store.freeze_result(
        record["id"], first["id"], store.recording(record["id"])["revision"]
    )
    job = enhancement_job(store, record["id"], frozen, role(store, record["id"]))
    with store.connection() as db:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.execute(
            "UPDATE working_job_authorities SET policy='unknown' WHERE job_id=?", (job["id"],)
        )
        with pytest.raises(ValueError, match="invalid policy"):
            result_roles.job_authority(db, job["id"], record["id"], "unknown")


@pytest.mark.parametrize("assigned", [True, False])
def test_explicitly_processed_alternative_becomes_working(synthetic_store, assigned):
    store = synthetic_store
    record = make_recording(store)
    first = add(store, record["id"], "First", auto_first=assigned)
    alternate = add(store, record["id"], "Alternative") if assigned else first
    frozen = store.freeze_result(
        record["id"], alternate["id"], store.recording(record["id"])["revision"]
    )
    expected = role(store, record["id"])
    job = enhancement_job(store, record["id"], frozen, expected)
    successor = add(store, record["id"], "Processed", job_id=job["id"])
    after = role(store, record["id"])
    assert after["working"]["result_id"] == successor["id"]
    events = store.all(
        "SELECT * FROM result_role_events WHERE recording_id=? ORDER BY sequence", (record["id"],)
    )
    assert events[-1]["action"] == "working_auto_successor"
    assert (events[-1]["previous_version_id"] is None) is not assigned
    with store.connection() as db:
        history = result_roles.public_history(db, store, record["id"], "synthetic")
    assert history["events"][-1]["actor_kind"] == "system"


def test_processing_does_not_promote_when_source_head_was_edited(synthetic_store):
    store = synthetic_store
    record = make_recording(store)
    first = add(store, record["id"], "First", auto_first=True)
    current = store.recording(record["id"])
    frozen = store.freeze_result(record["id"], first["id"], current["revision"])
    expected = role(store, record["id"])
    job = enhancement_job(store, record["id"], frozen, expected)
    store.save_transcript(
        record["id"],
        normalize_segments([{"start": 0, "end": 1, "text": "Manual edit"}]),
        {},
        current["revision"],
    )
    chosen = role(store, record["id"])
    add(store, record["id"], "Processed", job_id=job["id"])
    assert role(store, record["id"]) == chosen


def test_processing_does_not_promote_a_trashed_alternative(synthetic_store):
    store = synthetic_store
    record = make_recording(store)
    add(store, record["id"], "First", auto_first=True)
    alternate = add(store, record["id"], "Alternative")
    current = store.recording(record["id"])
    frozen = store.freeze_result(record["id"], alternate["id"], current["revision"])
    expected = role(store, record["id"])
    job = enhancement_job(store, record["id"], frozen, expected)
    store.set_result_trashed(record["id"], alternate["id"], current["revision"], trashed=True)
    result = add(store, record["id"], "Processed", job_id=job["id"])
    assert result["id"] != alternate["id"]
    assert role(store, record["id"]) == expected
