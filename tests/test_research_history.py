import hashlib
import json
import sqlite3

import pytest
from prior_schema import remove_later_additions, without_added_languages

from cbs_transcriber.history import encode_payload, processing_metadata
from cbs_transcriber.store import SCHEMA_VERSION, Store, new_id
from cbs_transcriber.transcripts import edit_segment, normalize_segments


def example(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Synthetic project", "protected")
    recording = store.create_recording(project["id"], "synthetic.v2.wav")
    segments = normalize_segments(
        [
            {
                "text": "Hej København. ",
                "start": 0,
                "end": 3,
                "speaker": "A",
                "words": [
                    {"word": "Hej", "start": 0, "end": 1},
                    {"word": "København.", "start": 1, "end": 3},
                ],
            }
        ]
    )
    result = store.add_result(
        recording["id"], segments, {"A": "Øyvind"}, "Synthetic", {"kind": "import"}
    )
    return store, project, store.recording(recording["id"]), result


def legacy5(tmp_path):
    store, project, recording, result = example(tmp_path)
    store.save_notes("projects", project["id"], "æøå\nExact metadata", 0)
    store.add_job(recording["id"], "transcribe", {"synthetic": True})
    with store.connection() as db:
        db.execute("DROP TABLE IF EXISTS result_hints")
        db.execute("DROP TABLE IF EXISTS native_batch_runs")
        db.execute("DROP TABLE IF EXISTS result_trash")
        for table in (
            "annotation_review_events",
            "annotation_items",
            "annotation_runs",
            "codebook_versions",
        ):
            db.execute(f"DROP TABLE {table}")
        db.execute("DROP TABLE note_versions")
        db.execute("DROP TABLE result_versions")
        remove_later_additions(db, 5)
        db.execute("PRAGMA user_version=5")
        before = {
            table: [dict(row) for row in db.execute(f"SELECT * FROM {table}")]
            for table in (
                "projects",
                "recordings",
                "transcript_results",
                "jobs",
                "settings",
                "providers",
            )
        }
    return store, recording, result, before


def test_schema5_backup_atomic_baselines_preserve_exact_rows_and_reopen_once(tmp_path):
    store, recording, result, before = legacy5(tmp_path)
    migrated = Store(tmp_path)
    with migrated.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        for table, rows in before.items():
            current = [dict(row) for row in db.execute(f"SELECT * FROM {table}")]
            assert without_added_languages(table, current) == rows
    versions = migrated.result_versions(recording["id"], result["id"])
    assert len(versions) == 1
    assert versions[0]["action"] == "legacy_observed"
    assert versions[0]["actor_kind"] == "migration"
    assert versions[0]["context"]["history_coverage"] == "before_observation_unknown"
    assert "payload" not in versions[0]
    exact = migrated.result_version(recording["id"], result["id"], versions[0]["id"])
    assert exact["payload"]["segments"] == result["segments"]
    assert exact["payload"]["speakers"] == result["speakers"]
    assert len(migrated.note_versions("recordings", recording["id"])) == 1
    backups = list((tmp_path / "backups").glob("schema-5-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        db.row_factory = sqlite3.Row
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        for table, rows in before.items():
            assert [dict(row) for row in db.execute(f"SELECT * FROM {table}")] == rows
    Store(tmp_path)
    assert migrated.result_versions(recording["id"], result["id"]) == versions
    assert len(list((tmp_path / "backups").glob("schema-5-*.sqlite3"))) == 1
    assert store.db_path == migrated.db_path


@pytest.mark.parametrize("failure", ["baseline", "mirror", "cross-recording", "unselected"])
def test_schema5_failure_leaves_no_partial_migration(tmp_path, monkeypatch, failure):
    store, recording, _, _ = legacy5(tmp_path)
    if failure == "baseline":
        monkeypatch.setattr(Store, "_append_version", lambda *args, **kw: 1 / 0)
    elif failure == "mirror":
        store.execute("UPDATE recordings SET speakers='{}' WHERE id=?", (recording["id"],))
    elif failure == "cross-recording":
        # A foreign key-valid, wrong recording is still an invalid active mirror.
        with store.connection() as db:
            db.execute(
                "INSERT INTO recordings(id,project_id,name,filename,created,active_transcript_id) "
                "VALUES ('other',?,'other','other.wav','2026',?)",
                (recording["project_id"], recording["active_transcript_id"]),
            )
    else:
        store.execute(
            "UPDATE recordings SET active_transcript_id=NULL WHERE id=?", (recording["id"],)
        )
    with pytest.raises((RuntimeError, ZeroDivisionError)):
        Store(tmp_path)
    with store.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE name IN ('result_versions','note_versions')"
            ).fetchall()
            == []
        )
    assert len(list((tmp_path / "backups").glob("schema-5-*.sqlite3"))) == 1


def test_schema5_backup_failure_and_newer_schema_are_read_only(tmp_path, monkeypatch):
    store, _, _, before = legacy5(tmp_path)
    (tmp_path / "backups").write_text("prevent backup folder", encoding="utf-8")
    with pytest.raises(OSError):
        Store(tmp_path)
    with store.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        for table, rows in before.items():
            assert [dict(row) for row in db.execute(f"SELECT * FROM {table}")] == rows
        db.execute("DROP TABLE settings")
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    with sqlite3.connect(store.db_path) as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before_bytes = store.db_path.read_bytes()
    with pytest.raises(RuntimeError, match="newer"):
        Store(tmp_path)
    assert store.db_path.read_bytes() == before_bytes
    with pytest.raises(RuntimeError, match="newer"):
        store.execute("DELETE FROM recordings")
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='settings'").fetchall() == []


def test_canonical_payload_is_exact_unicode_and_preserves_numeric_representation():
    segments = [{"text": "  æ\u0301øå\n", "start": 1.0, "unknown": {"z": 0, "a": 2}}]
    payload, digest = encode_payload(segments, {"B": "β", "A": "Æ"})
    assert payload == (
        '{"format":"cbs-result-snapshot-v1","segments":[{"start":1.0,"text":"  æ\u0301øå\\n",'
        '"unknown":{"a":2,"z":0}}],"speakers":{"A":"Æ","B":"β"}}'
    )
    assert digest == hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert encode_payload([{**segments[0], "start": 1}], {"B": "β", "A": "Æ"})[1] != digest
    with pytest.raises(ValueError):
        encode_payload([{"start": float("nan")}], {})


def test_atomic_review_successors_no_change_and_exact_historical_payload(tmp_path):
    store, _, recording, result = example(tmp_path)
    original = store.result_versions(recording["id"], result["id"])[0]
    segments = result["segments"]
    for action, mode, wording in (
        ("word_correction", "word", "Hallo"),
        ("free_rewrite", "free", "Different reviewed words æøå."),
        ("original_restore", "restore", ""),
    ):
        segments = [edit_segment(segments[0], mode, wording, 0 if mode == "word" else None)]
        recording = store.save_transcript(
            recording["id"],
            segments,
            result["speakers"],
            recording["revision"],
            action=action,
            context={"segment_id": "0"},
            reason="Researcher correction",
        )
    previous = store.result_versions(recording["id"], result["id"])[0]
    store.save_transcript(
        recording["id"], segments, result["speakers"], recording["revision"], action="manual_save"
    )
    versions = store.result_versions(recording["id"], result["id"])
    assert [v["sequence"] for v in versions] == [5, 4, 3, 2, 1]
    assert versions[0]["action"] == "save_no_change"
    assert versions[0]["context"]["requested_action"] == "manual_save"
    assert versions[0]["predecessor_id"] == previous["id"]
    assert [v["action"] for v in versions[1:4]] == [
        "original_restore",
        "free_rewrite",
        "word_correction",
    ]
    baseline = store.result_version(recording["id"], result["id"], original["id"])
    assert baseline["payload"]["segments"] == result["segments"]
    assert baseline["payload"]["speakers"] == result["speakers"]
    assert Store(tmp_path).result_versions(recording["id"], result["id"]) == versions


def test_stale_and_failed_writes_never_change_projection_or_history(tmp_path):
    store, _, recording, result = example(tmp_path)
    versions = store.result_versions(recording["id"], result["id"])
    with pytest.raises(ValueError, match="another window"):
        store.save_transcript(recording["id"], [], {}, recording["revision"] - 1)
    store.execute(
        "CREATE TRIGGER reject_version BEFORE INSERT ON result_versions "
        "BEGIN SELECT RAISE(FAIL,'injected version error'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        store.save_transcript(recording["id"], [], {}, recording["revision"])
    assert store.recording(recording["id"]) == recording
    assert store.result_versions(recording["id"], result["id"]) == versions
    notes = store.note_versions("recordings", recording["id"])
    store.execute(
        "CREATE TRIGGER reject_note BEFORE INSERT ON note_versions "
        "BEGIN SELECT RAISE(FAIL,'injected note error'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        store.save_notes("recordings", recording["id"], "not committed", 0)
    assert store.recording(recording["id"]) == recording
    assert store.note_versions("recordings", recording["id"]) == notes


def test_notes_versions_independent_exact_hash_no_change_and_boundary_validation(tmp_path):
    store, project, recording, result = example(tmp_path)
    for kind, item in (("projects", project), ("recordings", recording)):
        text = " æøå\nLiteral <script>α</script> "
        store.save_notes(kind, item["id"], text, 0, reason="Private local reason")
        store.save_notes(kind, item["id"], text, 1)
        versions = store.note_versions(kind, item["id"])
        assert [v["notes_revision"] for v in versions] == [2, 1, 0]
        assert versions[0]["action"] == "save_no_change"
        assert versions[1]["text_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert "text" not in versions[0]
        for bad in (None, False, 3, "x" * 20001):
            with pytest.raises(ValueError):
                store.save_notes(kind, item["id"], bad, 2)
        for reason in (False, 3, "x" * 2001):
            with pytest.raises(ValueError):
                store.save_notes(kind, item["id"], "new", 2, reason=reason)
        assert store.note_versions(kind, item["id"]) == versions
    assert store.recording(recording["id"])["revision"] == recording["revision"]
    assert len(store.result_versions(recording["id"], result["id"])) == 1


def test_frozen_lineage_keeps_exact_source_after_review_and_idempotent_worker_retry(tmp_path):
    store, _, recording, result = example(tmp_path)
    frozen = store.freeze_result(recording["id"], result["id"], recording["revision"])
    job = store.add_job(recording["id"], "enhance", {"source_snapshot": frozen, "segment_id": "0"})
    store.save_transcript(recording["id"], normalize_segments([{"text": "Later source"}]), {}, 1)
    enhanced = store.add_result(
        recording["id"],
        frozen["segments"],
        frozen["speakers"],
        "Enhanced",
        {"kind": "enhancement", "source_result_id": result["id"]},
        job_id=job["id"],
    )
    version = store.result_versions(recording["id"], enhanced["id"])[0]
    assert version["source_version_id"] == frozen["source_version_id"]
    assert version["context"]["source_payload_sha256"] == frozen["source_payload_sha256"]
    assert version["context"]["segment_ids"] == ["0"]
    assert "source_version_unknown" not in version["context"]
    assert (
        store.add_result(recording["id"], [], {}, "Retry", {}, job_id=job["id"])["id"]
        == enhanced["id"]
    )
    assert store.result_versions(recording["id"], enhanced["id"]) == [version]
    current = store.recording(recording["id"])
    current = store.activate_result(recording["id"], enhanced["id"], current["revision"])
    assert store.result_versions(recording["id"], enhanced["id"]) == [version]
    store.save_transcript(recording["id"], [], {}, current["revision"], action="free_rewrite")
    successor = store.result_versions(recording["id"], enhanced["id"])[0]
    assert successor["source_version_id"] == frozen["source_version_id"]
    assert successor["context"]["source_payload_sha256"] == frozen["source_payload_sha256"]


def test_old_frozen_sources_keep_own_hash_unknown_version_and_reject_cross_recording(tmp_path):
    store, project, recording, result = example(tmp_path)
    frozen = {"id": result["id"], "segments": result["segments"], "speakers": result["speakers"]}
    store.save_transcript(recording["id"], [], {}, recording["revision"])
    job = store.add_job(recording["id"], "enhance", {"source_snapshot": frozen})
    enhanced = store.add_result(recording["id"], [], {}, "Legacy", {}, job_id=job["id"])
    version = store.result_versions(recording["id"], enhanced["id"])[0]
    assert version["source_version_id"] is None
    assert version["context"]["source_version_unknown"] is True
    assert (
        version["context"]["source_payload_sha256"]
        == encode_payload(frozen["segments"], frozen["speakers"])[1]
    )
    other = store.create_recording(project["id"], "other.wav")
    bad = store.add_job(other["id"], "enhance", {"source_snapshot": frozen})
    with pytest.raises(ValueError, match="this recording"):
        store.add_result(other["id"], [], {}, "Invalid", {}, job_id=bad["id"])
    assert store.results(other["id"]) == []


def test_batch_result_version_and_followup_commit_or_rollback_together(tmp_path):
    store, _, recording, _ = example(tmp_path)
    followup_id = new_id()
    job = store.add_job(
        recording["id"], "transcribe", {"followup": {"id": followup_id, "params": {}}}
    )
    store.execute(
        "CREATE TRIGGER reject_child BEFORE INSERT ON jobs WHEN NEW.kind='enhance' "
        "BEGIN SELECT RAISE(FAIL,'injected child error'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected child"):
        store.add_result(recording["id"], [], {}, "Provider", {}, job_id=job["id"])
    assert len(store.results(recording["id"])) == 1
    assert store.one("SELECT COUNT(*) AS n FROM result_versions")["n"] == 1
    store.execute("DROP TRIGGER reject_child")
    result = store.add_result(recording["id"], [], {}, "Provider", {}, job_id=job["id"])
    version = store.result_versions(recording["id"], result["id"])[0]
    frozen = store.job(followup_id)["params"]["source_snapshot"]
    assert frozen["source_version_id"] == version["id"]
    assert frozen["source_payload_sha256"] == version["payload_sha256"]
    store.add_result(recording["id"], [], {}, "Retry", {}, job_id=job["id"])
    assert len(store.result_versions(recording["id"], result["id"])) == 1
    assert store.one("SELECT COUNT(*) AS n FROM jobs WHERE id=?", (followup_id,))["n"] == 1


def test_scoped_pagination_drift_validation_and_deletion(tmp_path):
    store, project, recording, result = example(tmp_path)
    for _ in range(3):
        current = store.recording(recording["id"])
        store.save_transcript(
            recording["id"], current["segments"], current["speakers"], current["revision"]
        )
    first = store.result_versions(recording["id"], result["id"], limit=2)
    second = store.result_versions(
        recording["id"], result["id"], before_sequence=first[-1]["sequence"]
    )
    assert [v["sequence"] for v in first + second] == [4, 3, 2, 1]
    with pytest.raises(KeyError):
        store.result_versions("wrong", result["id"])
    with pytest.raises(KeyError):
        store.result_version(recording["id"], "wrong", first[0]["id"])
    for bad in (0, 101, True, "50"):
        with pytest.raises(ValueError):
            store.result_versions(recording["id"], result["id"], limit=bad)
    store.execute("UPDATE transcript_results SET speakers='{}' WHERE id=?", (result["id"],))
    with pytest.raises(RuntimeError, match="disagree"):
        store.freeze_result(recording["id"], result["id"], 4)
    store.execute("DELETE FROM projects WHERE id=?", (project["id"],))
    assert store.all("SELECT * FROM result_versions") == []
    assert store.all("SELECT * FROM note_versions") == []


def test_processing_metadata_allowlist_never_copies_credentials_paths_or_endpoint():
    source = {
        "model": "synthetic/model",
        "provider_type": "custom",
        "api_key": "secret",
        "base_url": "https://private",
        "runtime_python": "private/path",
        "options": {"align": True, "device": "cpu", "runtime_python": "private", "token": "secret"},
        "model_revisions": {
            "alignment": {"repo": "public/model", "revision": "abc", "token": "secret"}
        },
    }
    safe = processing_metadata(source)
    assert safe == {
        "model": "synthetic/model",
        "provider_type": "custom",
        "options": {"align": True, "device": "cpu"},
        "model_revisions": {"alignment": {"repo": "public/model", "revision": "abc"}},
    }
    assert "secret" not in json.dumps(safe)


@pytest.mark.parametrize("damage", ["payload", "format", "noncanonical", "hash"])
def test_exact_historical_read_rejects_corruption_in_nonhead_version(tmp_path, damage):
    store, _, recording, result = example(tmp_path)
    original = store.result_versions(recording["id"], result["id"])[0]
    store.save_transcript(recording["id"], [], {}, recording["revision"])
    payload, digest = encode_payload([], {})
    if damage == "format":
        payload = payload.replace("cbs-result-snapshot-v1", "unknown-format")
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    elif damage == "noncanonical":
        payload += "\n"
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    elif damage == "hash":
        digest = "0" * 64
    else:
        digest = original["payload_sha256"]
    store.execute(
        "UPDATE result_versions SET payload=?,payload_sha256=? WHERE id=?",
        (payload, digest, original["id"]),
    )
    with pytest.raises(ValueError, match="integrity check"):
        store.result_version(recording["id"], result["id"], original["id"])


@pytest.mark.parametrize("damage", ["hash", "version", "source-result", "source-metadata"])
def test_lineage_rejects_mismatched_hash_or_cross_recording_version(tmp_path, damage):
    store, project, recording, result = example(tmp_path)
    frozen = store.freeze_result(recording["id"], result["id"], recording["revision"])
    other = store.create_recording(project["id"], "another.wav")
    other_result = store.add_result(other["id"], [], {}, "Other", {})
    source = {"kind": "enhancement", "source_result_id": result["id"]}
    if damage == "hash":
        frozen["source_payload_sha256"] = "0" * 64
    elif damage == "version":
        frozen["source_version_id"] = store.result_versions(other["id"], other_result["id"])[0][
            "id"
        ]
    elif damage == "source-result":
        frozen["id"] = other_result["id"]
    else:
        source = {"source_result_id": other_result["id"]}
    job = store.add_job(recording["id"], "enhance", {"source_snapshot": frozen})
    with pytest.raises(ValueError):
        store.add_result(recording["id"], [], {}, "Rejected", source, job_id=job["id"])
    assert len(store.results(recording["id"])) == 1


def test_snapshot_folder_failure_retains_committed_version_and_lineage_deletes_with_recording(
    tmp_path, monkeypatch
):
    store, _, recording, result = example(tmp_path)
    frozen = store.freeze_result(recording["id"], result["id"], recording["revision"])
    job = store.add_job(recording["id"], "enhance", {"source_snapshot": frozen})
    store.add_result(recording["id"], [], {}, "Enhanced", {}, job_id=job["id"])
    original_write = type(tmp_path).write_text

    def fail_snapshot(path, *args, **kwargs):
        if path.suffix == ".pending":
            raise OSError("synthetic full disk")
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "write_text", fail_snapshot)
    saved = store.save_transcript(
        recording["id"], normalize_segments([{"text": "Durable æøå"}]), {}, recording["revision"]
    )
    assert saved["snapshot_error"]
    current = store.result_versions(recording["id"], result["id"])[0]
    assert (
        store.result_version(recording["id"], result["id"], current["id"])["payload"]["segments"][
            0
        ]["text"]
        == "Durable æøå"
    )
    store.execute("DELETE FROM recordings WHERE id=?", (recording["id"],))
    assert store.all("SELECT * FROM result_versions") == []
    assert store.all("SELECT * FROM note_versions WHERE scope='recording'") == []
