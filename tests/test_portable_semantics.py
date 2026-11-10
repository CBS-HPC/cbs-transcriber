"""Synthetic databases only; the heap-limited SQLite opener runs in disposable children."""

import hashlib
import io
import json
import sqlite3
import wave

import pytest

from cbs_transcriber import portable_semantics as subject
from cbs_transcriber.history import encode_payload

PROJECT, RECORDING, RESULT, VERSION = (str(i) * 32 for i in range(1, 5))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def build(path, version=13):
    catalog = subject.catalog(version)
    payload, payload_hash = encode_payload([{"id": "p", "text": "æøå", "start": -0.0}], {})
    with sqlite3.connect(path) as db:
        for kind in ("table", "index"):
            for item in catalog["objects"]:
                if item["type"] == kind and item["sql"]:
                    db.execute(item["sql"])
        db.execute(f"PRAGMA user_version={version}")
        db.execute(
            "INSERT INTO projects(id,name,created,notes) VALUES (?,?,?,?)",
            (PROJECT, "Synthetic", "2026-10-29", "Project note"),
        )
        db.execute(
            "INSERT INTO recordings(id,project_id,name,filename,created,segments,"
            "active_transcript_id) VALUES (?,?,?,?,?,?,?)",
            (
                RECORDING,
                PROJECT,
                "Tiny",
                "tiny.wav",
                "2026-10-29",
                json.dumps([{"id": "p", "text": "æøå", "start": -0.0}]),
                RESULT,
            ),
        )
        db.execute(
            "INSERT INTO transcript_results VALUES (?,?,?,?,?,?,?,?,?)",
            (
                RESULT,
                RECORDING,
                "Imported",
                "{}",
                json.dumps([{"id": "p", "text": "æøå", "start": -0.0}]),
                "{}",
                None,
                "2026-10-29",
                "2026-10-29",
            ),
        )
        db.execute(
            "INSERT INTO result_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                VERSION,
                RESULT,
                1,
                None,
                None,
                payload,
                payload_hash,
                "2026-10-29",
                "legacy_observed",
                "{}",
                "migration",
                None,
            ),
        )
        for identifier, scope, recording, text in (
            ("a" * 32, "project", None, "Project note"),
            ("b" * 32, "recording", RECORDING, ""),
        ):
            db.execute(
                "INSERT INTO note_versions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    PROJECT,
                    recording,
                    scope,
                    0,
                    text,
                    digest(text.encode()),
                    "2026-10-29",
                    "local_user",
                    "notes_created",
                    None,
                ),
            )
        if version >= 14:
            db.execute("INSERT INTO recording_result_roles VALUES (?,?,?)", (RECORDING, VERSION, 1))
            db.execute(
                "INSERT INTO result_role_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "c" * 32,
                    RECORDING,
                    1,
                    None,
                    None,
                    VERSION,
                    payload_hash,
                    "working_marked",
                    "2026-10-29T00:00:00+00:00",
                    "local_user",
                ),
            )


def manifest(path):
    raw = path.read_bytes()
    database_hash = digest(raw)
    with sqlite3.connect(path) as db:
        claims = subject.database_claims(db)
    original_hash = digest(b"tiny synthetic original")
    return {
        "format": "cbs-library-backup-v2",
        "backup_id": "d" * 32,
        "source_library_id": "e" * 32,
        "created_at": "2026-10-29T12:00:00Z",
        "scope": "whole_library",
        "inventory_policy": "portable-files-v1",
        "app_version": "0.1.0",
        "schema_version": claims["schema_version"],
        "database": {
            "object": database_hash,
            "size": len(raw),
            "schema_fingerprint": claims["schema_fingerprint"],
            "tables": claims["tables"],
        },
        "objects": sorted(
            [
                {"sha256": database_hash, "size": len(raw)},
                {"sha256": original_hash, "size": len(b"tiny synthetic original")},
            ],
            key=lambda item: item["sha256"],
        ),
        "files": [
            {
                "project_id": PROJECT,
                "recording_id": RECORDING,
                "role": "original",
                "relative_path": "original.wav",
                "object": original_hash,
            }
        ],
        "legacy_gaps": [],
        "exclusions": "portable-exclusions-v1",
    }


def inspect(path, value=None):
    value = manifest(path) if value is None else value
    return subject.inspect_database(
        path,
        expected_sha256=value["database"]["object"],
        expected_size=value["database"]["size"],
        manifest=value,
    )


def test_manual_word_timing_history_survives_contained_observation(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 18)
    segments = [
        {
            "id": "p",
            "text": "æøå",
            "start": 0.0,
            "end": 2.0,
            "words": [
                {
                    "word": "æøå",
                    "start": 0.2,
                    "end": 1.8,
                    "char_start": 0,
                    "char_end": 3,
                    "manual_timing": True,
                }
            ],
            "word_timing": True,
            "words_current": True,
            "edited": True,
        }
    ]
    payload, payload_hash = encode_payload(segments, {})
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO result_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "f" * 32,
                RESULT,
                2,
                VERSION,
                None,
                payload,
                payload_hash,
                "2026-11-04",
                "word_timing_edit",
                "{}",
                "local_user",
                None,
            ),
        )
        encoded = json.dumps(segments, ensure_ascii=False)
        db.execute("UPDATE transcript_results SET segments=? WHERE id=?", (encoded, RESULT))
        db.execute("UPDATE recordings SET segments=? WHERE id=?", (encoded, RECORDING))
    assert inspect(path)["semantic_counts"]["result_versions"] == 2
    with sqlite3.connect(path) as db:
        saved = db.execute(
            "SELECT action,payload FROM result_versions WHERE id=?", ("f" * 32,)
        ).fetchone()
    assert saved[0] == "word_timing_edit"
    assert json.loads(saved[1])["segments"][0]["words"][0]["manual_timing"] is True


@pytest.mark.parametrize("version", [13, 14, 15, 16, 17, 18, 19])
def test_exact_contained_research_observation_keeps_unchecked_scopes(tmp_path, version):
    path = tmp_path / "synthetic.sqlite3"
    build(path, version)
    before = path.read_bytes()
    result = inspect(path)
    assert result["schema_version"] == version
    assert result["semantic_counts"] == {
        "result_versions": 1,
        "results": 1,
        "recordings": 1,
        "note_versions": 2,
        "file_mappings": 1,
        "role_events": int(version >= 14),
        **({"passage_reviews": 0} if version >= 19 else {}),
    }
    assert result["restore_ready"] is False
    assert "remote_owner_authority_and_quarantine" in result["not_checked"]
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE result_versions SET payload_sha256='" + "0" * 64 + "'",
        "UPDATE result_versions SET sequence=1.5",
        "UPDATE result_versions SET sequence=2",
        "UPDATE result_versions SET predecessor_id=id",
        "UPDATE transcript_results SET segments='[]'",
        "UPDATE recordings SET active_transcript_id=''",
        "UPDATE recordings SET active_transcript_id=NULL",
        "UPDATE recordings SET revision=0.5",
        'UPDATE recordings SET segments=\'[{"id":"p","text":"æøå","start":0.0}]\'',
        'UPDATE transcript_results SET speakers=\'{"a":1,"a":2}\'',
        "UPDATE note_versions SET text_sha256='" + "0" * 64 + "'",
        "UPDATE projects SET notes='Changed outside history'",
        "UPDATE note_versions SET notes_revision=0.5",
        "DELETE FROM result_versions",
        "INSERT INTO result_trash VALUES ('" + RESULT + "','2026-10-29')",
        "CREATE TABLE untrusted_extra (id TEXT)",
    ],
)
def test_fresh_matching_row_claims_cannot_conceal_inconsistent_research(tmp_path, statement):
    path = tmp_path / "synthetic.sqlite3"
    build(path)
    with sqlite3.connect(path) as db:
        db.execute(statement)
    with pytest.raises(ValueError, match="inspection"):
        inspect(path)


@pytest.mark.parametrize("damage", ["owner", "original_name", "missing_original"])
def test_mapping_membership_and_original_completeness(tmp_path, damage):
    path = tmp_path / "synthetic.sqlite3"
    build(path)
    value = manifest(path)
    if damage == "owner":
        value["files"][0]["project_id"] = "f" * 32
    elif damage == "original_name":
        value["files"][0]["relative_path"] = "original.mp3"
    else:
        value["files"] = []
        value["objects"] = [
            item for item in value["objects"] if item["sha256"] == value["database"]["object"]
        ]
    with pytest.raises(ValueError, match="inspection.*mapping"):
        inspect(path, value)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE recording_result_roles SET revision=2",
        "UPDATE result_role_events SET sequence=2",
        "UPDATE result_role_events SET next_payload_sha256='" + "0" * 64 + "'",
        "UPDATE result_role_events SET action='working_cleared'",
        "DELETE FROM recording_result_roles",
        "INSERT INTO result_trash VALUES ('" + RESULT + "','2026-10-29')",
    ],
)
def test_schema14_working_history_pins_exact_versions(tmp_path, statement):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 14)
    with sqlite3.connect(path) as db:
        db.execute(statement)
    with pytest.raises(ValueError, match="inspection"):
        inspect(path)


def test_schema12_contract_remains_distinct(tmp_path):
    from cbs_transcriber import portable_database

    assert portable_database.SCHEMA_FINGERPRINT not in {
        item[1] for item in subject.CATALOGS.values()
    }
    with pytest.raises(ValueError, match="schema version"):
        subject.catalog(12)


@pytest.mark.parametrize("version", [13, 14, 15, 16, 17, 18, 19])
def test_contained_restore_overlay_preserves_history_and_quarantines_orphan_members(
    tmp_path, version
):
    library = tmp_path / "staging" / "library"
    evidence = library / "preservation" / ("f" * 32)
    evidence.mkdir(parents=True)
    source = evidence / "source.sqlite3"
    build(source, version)
    with sqlite3.connect(source) as db:
        db.execute(
            "INSERT INTO jobs(id,recording_id,kind,params,created,updated) VALUES (?,?,?,?,?,?)",
            ("d" * 32, RECORDING, "media", '{"frozen":"retain bytes"}', "now", "now"),
        )
        db.execute(
            "INSERT INTO native_batch_runs(id,project_id,params,members,created,updated) "
            "VALUES (?,?,?,?,?,?)",
            ("e" * 32, PROJECT, "{}", json.dumps([{"job_id": "f" * 32}]), "now", "now"),
        )
        db.execute(
            "INSERT INTO runtime_batch_runs(id,project_id,compatibility_hash,identity,"
            "created,updated) VALUES (?,?,?,?,?,?)",
            ("a" * 32, PROJECT, "0" * 64, "{}", "now", "now"),
        )
        db.execute(
            "INSERT INTO runtime_batch_members(job_id,batch_id,recording_id,ordinal,"
            "source_identity,created,updated) VALUES (?,?,?,?,?,?,?)",
            ("b" * 32, "a" * 32, RECORDING, 0, "{}", "now", "now"),
        )
        payload_hash = db.execute("SELECT payload_sha256 FROM result_versions").fetchone()
        role_events = (
            db.execute("SELECT * FROM result_role_events ORDER BY sequence").fetchall()
            if version >= 14
            else []
        )
    before = source.read_bytes()
    value = manifest(source)
    working = library / "library.sqlite3"
    final = tmp_path / "Restored library"
    arguments = dict(
        expected_sha256=value["database"]["object"],
        expected_size=value["database"]["size"],
        manifest=value,
        target_root=final,
        restore_id="9" * 32,
    )
    result = subject.prepare_restore(source, working, **arguments)
    assert result["schema_version"] == 19
    assert result["quarantine_counts"] == {
        "job": 3,
        "native_batch": 1,
        "runtime_batch": 1,
        "runtime_preparation": 0,
    }
    assert subject.prepare_restore(source, working, **arguments) == result
    assert source.read_bytes() == before
    with sqlite3.connect(working) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 19
        assert db.execute("SELECT count(*) FROM working_job_authorities").fetchone()[0] == 0
        assert db.execute("SELECT storage_dir FROM projects").fetchone()[0] == str(
            final / "projects" / PROJECT
        )
        assert db.execute("SELECT status FROM recordings").fetchone()[0] == "preparing"
        assert db.execute("SELECT params,state FROM jobs").fetchone() == (
            '{"frozen":"retain bytes"}',
            "queued",
        )
        assert db.execute("SELECT payload_sha256 FROM result_versions").fetchone() == payload_hash
        assert (
            db.execute("SELECT * FROM result_role_events ORDER BY sequence").fetchall()
            == role_events
        )
        assert db.execute(
            "SELECT owner_id FROM restored_owners WHERE kind='job' ORDER BY owner_id"
        ).fetchall() == [("b" * 32,), ("d" * 32,), ("f" * 32,)]
        saved = json.loads(db.execute("SELECT value FROM providers").fetchone()[0])
        assert saved["enabled"] is False and saved["approved_for"] == []
        assert saved["base_url"] == "https://unconfigured.invalid/v1"


def map_body(value, path, relative, body, role, *, project=PROJECT, recording=RECORDING):
    key = digest(body)
    (path.parent / key).write_bytes(body)
    if key not in {item["sha256"] for item in value["objects"]}:
        value["objects"].append({"sha256": key, "size": len(body)})
        value["objects"].sort(key=lambda item: item["sha256"])
    value["files"].append(
        {
            "project_id": project,
            "recording_id": recording,
            "role": role,
            "relative_path": relative,
            "object": key,
        }
    )
    value["files"].sort(
        key=lambda item: (
            item["project_id"] or "",
            item["recording_id"] or "",
            item["role"],
            item["relative_path"],
        )
    )


@pytest.mark.parametrize("damage", [None, "missing", "malformed", "mismatch"])
def test_present_import_receipt_requires_exact_mapped_bytes(tmp_path, damage):
    from cbs_transcriber.transcripts import parse_transcript, pop_hints

    path = tmp_path / "synthetic.sqlite3"
    build(path)
    original = '{"segments":[{"text":"æøå","start":0,"end":1}]}'.encode()
    raw = (
        b'{"segments":[{"text":"Unrelated words","start":0,"end":1}]}'
        if damage == "mismatch"
        else original
    )
    parsed = parse_transcript(original, "source.json")
    pop_hints(parsed)
    payload, payload_hash = encode_payload(parsed, {})
    receipt = {
        "schema": 1,
        "role": "transcript_import",
        "source_id": "a" * 32,
        "format": "json",
        "sha256": digest(raw),
        "bytes": len(raw),
    }
    if damage == "malformed":
        receipt["sha256"] = "not a hash"
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE result_versions SET context=?,payload=?,payload_sha256=?",
            (json.dumps({"artifact_evidence": receipt}), payload, payload_hash),
        )
        db.execute(
            "UPDATE transcript_results SET source=?,segments=?",
            (json.dumps({"kind": "import", "artifact_evidence": receipt}), json.dumps(parsed)),
        )
        db.execute("UPDATE recordings SET segments=?", (json.dumps(parsed),))
    value = manifest(path)
    if damage != "missing":
        map_body(value, path, "sources/" + "a" * 32 + ".json", raw, "import_source")
    if damage is None:
        assert inspect(path, value)["semantic_counts"]["file_mappings"] == 2
    else:
        with pytest.raises(ValueError, match="inspection"):
            inspect(path, value)


@pytest.mark.parametrize("damage", [None, "missing_output", "wrong_creation", "wrong_audio"])
def test_present_producer_receipts_bind_creation_job_files_and_audio_geometry(tmp_path, damage):
    from cbs_transcriber import local_asr_capture

    path = tmp_path / "synthetic.sqlite3"
    build(path)
    job_id = "d" * 32
    request, output = b'{"language":"da"}', b'{"segments":[]}'
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * 5)
    wav = buffer.getvalue()

    def checkpoint(stage, out="not_captured"):
        return {
            "format": "cbs-producer-artifacts-v1",
            "producer": {
                "recording_id": RECORDING,
                "job_id": job_id,
                "request_sha256": digest(request),
                "stage": stage,
            },
            "artifacts": {
                "request": {
                    "role": "request",
                    "format": "json",
                    "sha256": digest(request),
                    "bytes": len(request),
                },
                "raw_response": "not_captured",
                "output": out,
                "environment": "not_captured",
            },
        }

    receipt = {
        "format": "cbs-local-asr-capture-v1",
        "origin": {
            "generation": "f" * 32,
            "recording_id": RECORDING,
            "job_id": job_id,
            "audio_binding": {
                "input_sha256": digest(wav),
                "request_sha256": digest(request),
                "audio_frames": 6 if damage == "wrong_audio" else 5,
            },
            "request_checkpoint": checkpoint("preparation"),
        },
        "checkpoints": {
            "transcription": checkpoint(
                "transcription",
                {
                    "role": "output",
                    "format": "json",
                    "sha256": digest(output),
                    "bytes": len(output),
                },
            )
        },
    }
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO jobs(id,recording_id,kind,params,created,updated) VALUES (?,?,?,?,?,?)",
            (
                job_id,
                RECORDING,
                "enhance",
                json.dumps({"producer_artifact_capture": receipt}),
                "now",
                "now",
            ),
        )
        db.execute("UPDATE transcript_results SET job_id=?", (job_id,))
        payload_hash = db.execute("SELECT payload_sha256 FROM result_versions").fetchone()[0]
        bound = local_asr_capture.bind(
            receipt,
            creation={
                "result_id": RESULT,
                "version_id": "e" * 32 if damage == "wrong_creation" else VERSION,
                "payload_sha256": payload_hash,
            },
        )
        db.execute(
            "UPDATE result_versions SET context=?", (json.dumps({"producer_artifacts": bound}),)
        )
    value = manifest(path)
    for name, body, role in (
        ("request.json", request, "processing_input"),
        ("audio.wav", wav, "processing_input"),
        ("transcription.json", output, "processing_output"),
    ):
        if name == "transcription.json" and damage == "missing_output":
            continue
        map_body(value, path, "enhancements/" + job_id + "/" + name, body, role)
    if damage is None:
        assert inspect(path, value)["required_audio"] == [{"sha256": digest(wav), "frames": 5}]
    else:
        with pytest.raises(ValueError, match="inspection|audio"):
            inspect(path, value)


@pytest.mark.parametrize("inherit", [False, True])
def test_valid_timing_successor_and_later_inherited_proof(tmp_path, inherit):
    import copy

    from cbs_transcriber import timing_capture
    from cbs_transcriber.transcripts import normalize_segments

    path = tmp_path / "synthetic.sqlite3"
    build(path, 15)
    segments = normalize_segments([{"text": "Hej", "start": 0, "end": 1}])
    original, original_hash = encode_payload(segments, {})
    output = {
        "words": [{"word": "Hej", "start": 0.0, "end": 0.8, "char_start": 0, "char_end": 3}],
        "word_timing": True,
    }
    successor = copy.deepcopy(segments)
    successor[0].update(words=copy.deepcopy(output["words"]), word_timing=True, words_current=True)
    encoded, encoded_hash = encode_payload(successor, {})
    application_id, job_id = "e" * 32, "d" * 32
    frozen = {
        "id": RESULT,
        "source_version_id": VERSION,
        "source_payload_sha256": original_hash,
        "segments": segments,
        "speakers": {},
    }
    params = {
        "realign": True,
        "segment_id": "0",
        "source_snapshot": frozen,
        "enhancement": {
            "execution": "local",
            "align": True,
            "diarize": False,
            "transcribe": False,
            "retranscribe": False,
        },
        "timing_artifact_policy": timing_capture.POLICY,
        "realign_output": output,
    }
    params["timing_artifact_capture"] = {
        "format": timing_capture.FORMAT,
        "source": timing_capture._source(params),
        "units": {"00000": "not_captured"},
        "assembled_output_sha256": timing_capture._hash(output),
    }
    params["realign_applied"] = "applied"
    params["realign_apply_receipt"] = {
        "format": "cbs-local-timing-apply-v1",
        "version": {"id": application_id, "payload_sha256": encoded_hash},
        "previous": {"id": VERSION, "payload_sha256": original_hash},
        "base_revision": 1,
        "applied_revision": 2,
        "input_sha256": digest(
            json.dumps(
                {"source": frozen, "output": output},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ),
        "applied_ids": ["0"],
        "skipped_ids": [],
    }
    job = {"id": job_id, "recording_id": RECORDING, "kind": "enhance", "params": params}
    proof = timing_capture.bind(
        timing_capture.for_application(job, RECORDING, RESULT, ["0"]),
        creation={
            "result_id": RESULT,
            "version_id": application_id,
            "payload_sha256": encoded_hash,
        },
    )
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE result_versions SET payload=?,payload_sha256=?,context=?",
            (original, original_hash, '{"artifact_evidence":"not_captured"}'),
        )
        db.execute("UPDATE result_role_events SET next_payload_sha256=?", (original_hash,))
        db.execute(
            "INSERT INTO jobs(id,recording_id,kind,state,params,created,updated) "
            "VALUES (?,?,?,?,?,?,?)",
            (job_id, RECORDING, "enhance", "completed", json.dumps(params), "now", "now"),
        )
        db.execute(
            "INSERT INTO result_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                application_id,
                RESULT,
                2,
                VERSION,
                None,
                encoded,
                encoded_hash,
                "now",
                "timing_realign",
                json.dumps({"job_id": job_id, "timing_artifacts": proof}),
                "local_user",
                None,
            ),
        )
        if inherit:
            db.execute(
                "INSERT INTO result_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "f" * 32,
                    RESULT,
                    3,
                    application_id,
                    None,
                    encoded,
                    encoded_hash,
                    "now",
                    "save_no_change",
                    json.dumps({"timing_artifacts": proof}),
                    "local_user",
                    None,
                ),
            )
        db.execute("UPDATE transcript_results SET segments=?", (json.dumps(successor),))
        db.execute("UPDATE recordings SET segments=?", (json.dumps(successor),))
    result = inspect(path)
    assert result["semantic_counts"]["result_versions"] == (3 if inherit else 2)
    assert result["required_audio"] == []


@pytest.mark.parametrize("damage", [None, "codebook_hash", "review_hash", "predecessor"])
def test_annotation_hashes_chains_and_frozen_membership(tmp_path, damage):
    path = tmp_path / "synthetic.sqlite3"
    build(path)
    codebook = '{"format":"cbs-topic-codebook-v1","topics":[]}'
    annotation = '{"format":"cbs-topic-annotation-v1"}'
    with sqlite3.connect(path) as db:
        source_hash = db.execute("SELECT payload_sha256 FROM result_versions").fetchone()[0]
        db.execute(
            "INSERT INTO codebook_versions VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "a" * 32,
                PROJECT,
                1,
                None,
                codebook,
                digest(codebook.encode()),
                "now",
                "codebook_save",
                "local_user",
                None,
            ),
        )
        db.execute(
            "INSERT INTO annotation_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "b" * 32,
                PROJECT,
                RECORDING,
                1,
                RESULT,
                VERSION,
                source_hash,
                "a" * 32,
                digest(codebook.encode()),
                "manual",
                '["p"]',
                "{}",
                "now",
                "request",
                "0" * 64,
            ),
        )
        db.execute(
            "INSERT INTO annotation_items VALUES (?,?,?,?,?,?,?,?,?)",
            ("c" * 32, "b" * 32, 1, "manual", None, None, "now", "request", "0" * 64),
        )
        db.execute(
            "INSERT INTO annotation_review_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "d" * 32,
                "c" * 32,
                1,
                None,
                annotation,
                digest(annotation.encode()),
                "accepted",
                "manual",
                "local_user",
                None,
                "now",
                "request",
                "0" * 64,
            ),
        )
        if damage == "codebook_hash":
            db.execute("UPDATE codebook_versions SET payload_sha256=?", ("0" * 64,))
        elif damage == "review_hash":
            db.execute("UPDATE annotation_review_events SET payload_sha256=?", ("0" * 64,))
        elif damage == "predecessor":
            db.execute("UPDATE annotation_review_events SET predecessor_id=id")
    if damage is None:
        assert inspect(path)["level"] == "database_research_consistency"
    else:
        with pytest.raises(ValueError, match="inspection"):
            inspect(path)


def empty_manifest(path):
    value = manifest(path)
    value["files"] = []
    value["objects"] = [
        item for item in value["objects"] if item["sha256"] == value["database"]["object"]
    ]
    return value


@pytest.mark.parametrize("historical_marker", [False, True])
def test_empty_restore_can_be_recaptured_with_bound_preservation_without_fake_owners(
    tmp_path, historical_marker
):
    from cbs_transcriber import portable_database

    library = tmp_path / "staging" / "library"
    preservation = library / "preservation" / ("7" * 32)
    preservation.mkdir(parents=True)
    source = preservation / "source.sqlite3"
    build(source)
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("DELETE FROM projects")
    old = empty_manifest(source)
    prepared = subject.prepare_restore(
        source,
        library / "library.sqlite3",
        expected_sha256=old["database"]["object"],
        expected_size=old["database"]["size"],
        manifest=old,
        target_root=tmp_path / "Old restored",
        restore_id="7" * 32,
    )
    old_manifest = portable_database._canonical(old)
    commit = portable_database._canonical(
        {
            "format": "cbs-library-backup-commit-v2",
            "backup_id": old["backup_id"],
            "manifest_size": len(old_manifest),
            "manifest_sha256": digest(old_manifest),
        }
    )
    receipt = portable_database._canonical(
        {
            "format": "cbs-portable-restore-v1",
            "operation_id": "7" * 32,
            "state": "prepared",
            "backup_id": old["backup_id"],
            "commit_sha256": digest(commit),
            "manifest_sha256": digest(old_manifest),
            "source_database_sha256": old["database"]["object"],
            "working_copy": prepared,
        }
    )
    working = library / "library.sqlite3"
    if historical_marker:
        with sqlite3.connect(working) as db:
            db.execute(
                "INSERT INTO restored_owners VALUES (?,?,?,?)",
                ("job", "a" * 32, "7" * 32, old["backup_id"]),
            )
    value = empty_manifest(working)
    for name, raw in (
        ("source.sqlite3", source.read_bytes()),
        ("manifest.json", old_manifest),
        ("COMMITTED.json", commit),
        ("restore-receipt.json", receipt),
    ):
        map_body(
            value,
            working,
            "preservation/" + "7" * 32 + "/" + name,
            raw,
            "processing_evidence",
            project=None,
            recording=None,
        )
    observed = inspect(working, value)
    assert observed["semantic_counts"]["recordings"] == 0
    assert observed["semantic_counts"]["file_mappings"] == 4


def test_unknown_restoration_markers_refuse_missing_preserved_controls(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 15)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO restored_owners VALUES (?,?,?,?)", ("job", "a" * 32, "7" * 32, "6" * 32)
        )
    with pytest.raises(ValueError, match="inspection.*mapping"):
        inspect(path)


@pytest.mark.parametrize("malformed", [False, True])
def test_restore_resets_only_operational_inspection_cache_and_retains_frozen_source(
    tmp_path, malformed
):
    from cbs_transcriber import original_inspection

    library = tmp_path / "staging" / "library"
    evidence = library / "preservation" / ("f" * 32)
    evidence.mkdir(parents=True)
    source = evidence / "source.sqlite3"
    build(source)
    job_id, generation = "8" * 32, "7" * 32
    cache = {
        "version": original_inspection.VERSION,
        "generation": generation,
        "state": "pending",
        "inspection_job_id": job_id,
        "epoch": 1,
        "updated_at": "2026-10-29T12:00:00+00:00",
        "cancel_requested": False,
        "anchor": {
            "sha256": digest(b"tiny synthetic original"),
            "bytes": len(b"tiny synthetic original"),
            "format": "wav",
        },
        "receipt": None,
        "receipt_sha256": None,
        "error": None,
    }
    original_inspection._validate(cache, RECORDING, "tiny.wav")
    raw = "{malformed-cache" if malformed else json.dumps(cache)
    params = json.dumps(
        {
            "inspection": {
                "version": original_inspection.VERSION,
                "generation": generation,
                "epoch": 1,
            }
        }
    )
    with sqlite3.connect(source) as db:
        db.execute("UPDATE recordings SET original_input=?", (raw,))
        db.execute(
            "INSERT INTO jobs(id,recording_id,kind,params,created,updated) VALUES (?,?,?,?,?,?)",
            (job_id, RECORDING, "source-inspect", params, "now", "now"),
        )
    before, value = source.read_bytes(), manifest(source)
    subject.prepare_restore(
        source,
        library / "library.sqlite3",
        expected_sha256=value["database"]["object"],
        expected_size=value["database"]["size"],
        manifest=value,
        target_root=tmp_path / "Restored library",
        restore_id="9" * 32,
    )
    assert source.read_bytes() == before
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT original_input FROM recordings").fetchone() == (raw,)
    with sqlite3.connect(library / "library.sqlite3") as db:
        assert db.execute("SELECT original_input FROM recordings").fetchone() == (None,)
        assert db.execute("SELECT params,state FROM jobs").fetchone() == (params, "queued")
        assert db.execute("SELECT owner_id FROM restored_owners WHERE kind='job'").fetchone() == (
            job_id,
        )


@pytest.mark.parametrize(
    "mode",
    [
        "bound",
        "pending",
        "historical",
        "deleted",
        "wrong_gap",
        "missing",
        "malformed",
        "deleted_malformed",
        "missing_gap",
        "live_gap",
        "historical_current_bytes",
        "job_missing_live",
        "original",
        "wrong_operation",
    ],
)
def test_runtime_promises_use_exact_retained_attempt_and_deleted_owner_gap(tmp_path, mode):
    from cbs_transcriber import runtime_capture

    path = tmp_path / "synthetic.sqlite3"
    build(path, 15)
    deleted = mode in {"deleted", "wrong_gap", "deleted_malformed", "missing_gap"}
    historical = mode in {"historical", "historical_current_bytes"}
    job_id, batch_id, task_id = "d" * 32, "9" * 32, "a" * 32
    identity = (
        '{"operation":"alignment"}'
        if mode == "wrong_operation"
        else '{"operation":"transcription"}'
    )
    binding = {
        "batch_id": batch_id,
        "task_id": task_id,
        "attempt": 1,
        "identity_hash": digest(identity.encode()),
    }
    folder = tmp_path / "producer"
    folder.mkdir()
    bodies = {
        "request.json": b'{ "options": {"language":"da"} }\r\n',
        "audio.wav": b"synthetic prepared container",
        "transcription.json": b'{"segments":[{"text":"Synthetic output"}]}',
        "outcome.json": b'{ "state":"complete" }\r\n',
    }
    if mode == "original":
        bodies.pop("audio.wav")
        bodies["original.input.json"] = b'{"protocol":"original-v1"}'
        bodies["audio.source.wav"] = b"tiny synthetic original"
    for name, body in bodies.items():
        (folder / name).write_bytes(body)
    file_receipts = {
        name: {"sha256": digest(body), "size": len(body)} for name, body in bodies.items()
    }
    job = {
        "id": job_id,
        "recording_id": RECORDING,
        "kind": "enhance",
        "params": runtime_capture.freeze(
            {
                "transcription": True,
                "enhancement": {"execution": "ucloud", "transcribe": True, "retranscribe": False},
                "ucloud_api": {"batch_plan": {}},
            }
        ),
    }
    inputs = {
        "files": {
            name: file_receipts[name]
            for name in (
                ("request.json", "original.input.json", "audio.source.wav")
                if mode == "original"
                else ("request.json", "audio.wav")
            )
        },
        "manifest_sha256": "f" * 64,
        "publish_intent": True,
    }
    inputs["artifact_origin"] = runtime_capture.origin(
        job,
        folder,
        binding=binding,
        manifest_sha256=inputs["manifest_sha256"],
        files=inputs["files"],
    )
    outputs = {name: file_receipts[name] for name in ("transcription.json", "outcome.json")}
    remote = {
        "files": {"transcription.json": outputs["transcription.json"]},
        "outcome": "complete",
        "artifact_files": outputs,
        "artifact_checkpoints": runtime_capture.collect(
            inputs["artifact_origin"], folder, files=outputs
        ),
    }
    document = runtime_capture.document(binding, inputs, remote)
    final = {"batch_id": batch_id, "task_id": task_id, "attempt": 1, "epoch": 4, "files": outputs}
    job["params"]["runtime_output_receipt"] = final
    if mode in {"malformed", "deleted_malformed"}:
        inputs["artifact_origin"]["binding"]["task_id"] = "b" * 32
    with sqlite3.connect(path) as db:
        payload_hash = db.execute("SELECT payload_sha256 FROM result_versions").fetchone()[0]
        db.execute(
            "INSERT INTO jobs(id,recording_id,kind,state,params,created,updated) "
            "VALUES (?,?,?,?,?,?,?)",
            (job_id, RECORDING, "enhance", "completed", json.dumps(job["params"]), "now", "now"),
        )
        db.execute(
            "INSERT INTO runtime_batch_runs(id,project_id,compatibility_hash,identity,"
            "created,updated) "
            "VALUES (?,?,?,?,?,?)",
            (batch_id, PROJECT, binding["identity_hash"], identity, "now", "now"),
        )
        db.execute(
            "INSERT INTO runtime_batch_members(job_id,batch_id,recording_id,ordinal,"
            "source_identity,current_attempt,result_id,output_receipt,created,updated) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                batch_id,
                RECORDING,
                0,
                "{}",
                2 if historical else 1,
                RESULT if mode == "bound" else None,
                json.dumps(final),
                "now",
                "now",
            ),
        )
        db.execute(
            "INSERT INTO runtime_batch_attempts(job_id,attempt,task_id,state,"
            "input_receipt,remote_receipt,"
            "created,updated) VALUES (?,?,?,?,?,?,?,?)",
            (job_id, 1, task_id, "complete", json.dumps(inputs), json.dumps(remote), "now", "now"),
        )
        if historical:
            db.execute(
                "INSERT INTO runtime_batch_attempts(job_id,attempt,task_id,state,created,updated) "
                "VALUES (?,?,?,?,?,?)",
                (job_id, 2, "b" * 32, "cancelled", "now", "now"),
            )
        if mode == "bound":
            bound = runtime_capture.bind(
                document,
                creation={
                    "result_id": RESULT,
                    "version_id": VERSION,
                    "payload_sha256": payload_hash,
                },
            )
            db.execute("UPDATE transcript_results SET job_id=?", (job_id,))
            db.execute(
                "UPDATE result_versions SET context=?", (json.dumps({"runtime_artifacts": bound}),)
            )
        if mode == "job_missing_live":
            db.execute("DELETE FROM jobs")
        if deleted:
            db.commit()
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("DELETE FROM recordings")
    value = empty_manifest(path) if deleted else manifest(path)
    if deleted and mode != "missing_gap" or mode == "live_gap":
        value["legacy_gaps"] = [
            {
                "recording_id": RECORDING,
                "job_id": "b" * 32 if mode == "wrong_gap" else job_id,
                "code": "absent_owner_artifacts_not_retained",
            }
        ]
    if not deleted:
        for name, body in bodies.items():
            if name.startswith("audio.source."):
                continue
            if mode == "missing" and name == "transcription.json":
                continue
            prefix = "attempts/1/" if mode == "historical" and name in outputs else ""
            role = (
                "processing_evidence"
                if name == "original.input.json"
                else (
                    "processing_input"
                    if name in inputs["files"]
                    else (
                        "processing_output"
                        if name == "transcription.json"
                        else "processing_evidence"
                    )
                )
            )
            map_body(value, path, "enhancements/" + job_id + "/" + prefix + name, body, role)
    if mode in {
        "missing",
        "malformed",
        "wrong_gap",
        "deleted_malformed",
        "missing_gap",
        "live_gap",
        "historical_current_bytes",
        "job_missing_live",
        "wrong_operation",
    }:
        with pytest.raises(ValueError, match="inspection"):
            inspect(path, value)
    else:
        assert inspect(path, value)["level"] == "database_research_consistency"


@pytest.mark.parametrize("mismatch", [False, True])
def test_csv_import_receipt_uses_the_frozen_parser_payload(tmp_path, mismatch):
    from cbs_transcriber.transcripts import parse_transcript, pop_hints

    path = tmp_path / "synthetic.sqlite3"
    build(path)
    original = b"start,end,text\n0,1,Hello\n"
    raw = b"start,end,text\n0,1,Unrelated\n" if mismatch else original
    parsed = parse_transcript(original, "source.csv")
    pop_hints(parsed)
    payload, payload_hash = encode_payload(parsed, {})
    receipt = {
        "schema": 1,
        "role": "transcript_import",
        "source_id": "a" * 32,
        "format": "csv",
        "sha256": digest(raw),
        "bytes": len(raw),
    }
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE result_versions SET context=?,payload=?,payload_sha256=?",
            (json.dumps({"artifact_evidence": receipt}), payload, payload_hash),
        )
        db.execute(
            "UPDATE transcript_results SET source=?,segments=?",
            (json.dumps({"kind": "import", "artifact_evidence": receipt}), json.dumps(parsed)),
        )
        db.execute("UPDATE recordings SET segments=?", (json.dumps(parsed),))
    value = manifest(path)
    map_body(value, path, "sources/" + "a" * 32 + ".csv", raw, "import_source")
    if mismatch:
        with pytest.raises(ValueError, match="inspection"):
            inspect(path, value)
    else:
        assert inspect(path, value)["level"] == "database_research_consistency"


def test_archived_runtime_file_roles_require_actual_attempt_membership():
    from cbs_transcriber import portable_inventory

    job = "a" * 32
    relative = "enhancements/" + job + "/attempts/1/alignment.json"
    with pytest.raises(ValueError):
        portable_inventory.recording_role(
            relative, original="original.wav", job_ids={job}, attempts={}
        )
    assert (
        portable_inventory.recording_role(
            relative, original="original.wav", job_ids={job}, attempts={job: {1}}
        )
        == "processing_output"
    )


@pytest.mark.parametrize(
    ("namespace", "policy"), [("provider", "api-artifact-v1"), ("native", "native-artifact-v1")]
)
@pytest.mark.parametrize("kind", ["context", "policy", "capture"])
def test_malformed_provider_receipts_fail_closed(tmp_path, kind, namespace, policy):
    path = tmp_path / "synthetic.sqlite3"
    build(path)
    with sqlite3.connect(path) as db:
        if kind == "context":
            db.execute(
                "UPDATE result_versions SET context=?",
                (json.dumps({namespace + "_artifacts": {}}),),
            )
        else:
            params = (
                {namespace + "_artifact_policy": policy}
                if kind == "policy"
                else {namespace + "_artifact_capture": {}}
            )
            db.execute(
                "INSERT INTO jobs(id,recording_id,kind,params,created,updated) "
                "VALUES (?,?,?,?,?,?)",
                ("a" * 32, RECORDING, "transcribe", json.dumps(params), "now", "now"),
            )
    with pytest.raises(ValueError, match="inspection"):
        inspect(path)


@pytest.mark.parametrize(
    "action,actor,valid",
    [
        ("working_auto_first", "system", True),
        ("working_auto_first", "local_user", False),
        ("working_auto_successor", "system", True),
        ("working_auto_edit", "system", False),
        ("working_marked", "system", False),
    ],
)
def test_schema18_role_action_actor_and_first_transition(tmp_path, action, actor, valid):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 18)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE result_role_events SET action=?,actor_kind=?", (action, actor))
    if valid:
        assert inspect(path)["semantic_counts"]["role_events"] == 1
    else:
        with pytest.raises(ValueError, match="inspection"):
            inspect(path)
