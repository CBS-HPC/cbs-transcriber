"""Synthetic schema-18 Working authority observations."""

import hashlib
import json
import sqlite3

import pytest
from test_portable_semantics import RECORDING, RESULT, VERSION, build, inspect, manifest

from cbs_transcriber import portable_semantics


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def add_authority(path, mode):
    job_id = "8" * 32
    role = {"revision": 0, "working": None}
    with sqlite3.connect(path) as db:
        payload_hash = db.execute(
            "SELECT payload_sha256 FROM result_versions WHERE id=?", (VERSION,)
        ).fetchone()[0]
        payload = json.loads(
            db.execute("SELECT payload FROM result_versions WHERE id=?", (VERSION,)).fetchone()[0]
        )
        source = {
            "id": RESULT,
            "source_version_id": VERSION,
            "source_payload_sha256": payload_hash,
            "segments": payload["segments"],
            "speakers": payload["speakers"],
        }
        source_id, source_hash, snapshot_hash, admission = (
            VERSION,
            payload_hash,
            hashlib.sha256(canonical(source)).hexdigest(),
            None,
        )
        if mode in {"intent", "intent-asr"}:
            source_id = source_hash = snapshot_hash = None
            source = None
            accepted = {
                "accepted": {"operation": "transcription"},
                "source_snapshot": None,
                "baseline": {"working_role": role},
                "predecessor": None,
            }
            admission = hashlib.sha256(canonical(accepted)).hexdigest()
            params = {"input_intent": {**accepted, "accepted_sha256": admission}}
            if mode == "intent-asr":
                params["transcription"] = True
            kind = "enhance" if mode == "intent-asr" else "transcribe"
        elif mode in {"admitted", "admitted-asr"}:
            source_id = source_hash = snapshot_hash = None
            admission = "9" * 64
            params = {"input_acceptance": {"working_role": role, "accepted_sha256": admission}}
            if mode == "admitted-asr":
                params["transcription"] = True
            kind = "enhance" if mode == "admitted-asr" else "transcribe"
        elif mode == "result":
            params = {
                "source_snapshot": source,
                "working_role": role,
                "working_role_policy": "cbs-working-role-v1",
            }
            kind = "enhance"
        else:
            params = {
                "source_snapshot": source,
                "timing_working_policy": {"format": "cbs-timing-working-v1", "expected_role": role},
            }
            kind = "enhance"
        db.execute(
            "INSERT INTO jobs(id,recording_id,kind,params,created,updated) VALUES (?,?,?,?,?,?)",
            (job_id, RECORDING, kind, json.dumps(params), "now", "now"),
        )
        db.execute(
            "INSERT INTO working_job_authorities VALUES (?,?,?,?,?,?,?,?)",
            (
                job_id,
                RECORDING,
                "timing" if mode == "timing" else "result",
                canonical(role).decode(),
                source_id,
                source_hash,
                snapshot_hash,
                admission,
            ),
        )
    return job_id


@pytest.mark.parametrize(
    "mode", ["intent", "intent-asr", "admitted", "admitted-asr", "result", "timing"]
)
def test_working_authority_matches_frozen_job(tmp_path, mode):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 18)
    add_authority(path, mode)
    assert inspect(path)["schema_version"] == 18


def test_timing_authority_accepts_ordered_saved_projection(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 18)
    job = add_authority(path, "timing")
    with sqlite3.connect(path) as db:
        params = json.loads(db.execute("SELECT params FROM jobs WHERE id=?", (job,)).fetchone()[0])
        params["source_snapshot"]["segments"] = []
        db.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job))
        db.execute(
            "UPDATE working_job_authorities SET source_snapshot_sha256=?",
            (hashlib.sha256(canonical(params["source_snapshot"])).hexdigest(),),
        )
    assert inspect(path)["schema_version"] == 18


@pytest.mark.parametrize("mode", ["intent-asr", "admitted-asr"])
@pytest.mark.parametrize("replacement", [False, 0.0])
def test_frozen_role_numeric_type_must_match_authority(tmp_path, mode, replacement):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 18)
    job = add_authority(path, mode)
    with sqlite3.connect(path) as db:
        params = json.loads(db.execute("SELECT params FROM jobs WHERE id=?", (job,)).fetchone()[0])
        if mode == "intent-asr":
            intent = params["input_intent"]
            intent["baseline"]["working_role"]["revision"] = replacement
            accepted = {
                key: intent[key]
                for key in ("accepted", "source_snapshot", "baseline", "predecessor")
            }
            digest = hashlib.sha256(canonical(accepted)).hexdigest()
            intent["accepted_sha256"] = digest
            db.execute("UPDATE working_job_authorities SET admission_sha256=?", (digest,))
        else:
            params["input_acceptance"]["working_role"]["revision"] = replacement
        db.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job))
    with pytest.raises(ValueError, match="inspection"):
        inspect(path)


@pytest.mark.parametrize("mode", ["intent-asr", "admitted-asr"])
@pytest.mark.parametrize("flag", [None, False, 1])
def test_source_less_enhancement_requires_full_asr_flag(tmp_path, mode, flag):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 18)
    job = add_authority(path, mode)
    with sqlite3.connect(path) as db:
        params = json.loads(db.execute("SELECT params FROM jobs WHERE id=?", (job,)).fetchone()[0])
        if flag is None:
            params.pop("transcription")
        else:
            params["transcription"] = flag
        db.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job))
    with pytest.raises(ValueError, match="inspection"):
        inspect(path)


@pytest.mark.parametrize("revision", [0, 1])
def test_frozen_role_must_match_its_historical_event(tmp_path, revision):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 18)
    job = add_authority(path, "result")
    with sqlite3.connect(path) as db:
        payload_hash = db.execute(
            "SELECT payload_sha256 FROM result_versions WHERE id=?", (VERSION,)
        ).fetchone()[0]
        role = {
            "revision": revision,
            "working": (
                {
                    "version_id": VERSION,
                    "result_id": RESULT,
                    "sequence": 1,
                    "payload_sha256": payload_hash,
                }
                if revision == 0
                else None
            ),
        }
        params = json.loads(db.execute("SELECT params FROM jobs WHERE id=?", (job,)).fetchone()[0])
        params["working_role"] = role
        db.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job))
        db.execute("UPDATE working_job_authorities SET role_json=?", (canonical(role).decode(),))
    with pytest.raises(ValueError, match="inspection"):
        inspect(path)


def test_frozen_working_role_matches_historical_event(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 18)
    job = add_authority(path, "result")
    with sqlite3.connect(path) as db:
        payload_hash = db.execute(
            "SELECT payload_sha256 FROM result_versions WHERE id=?", (VERSION,)
        ).fetchone()[0]
        role = {
            "revision": 1,
            "working": {
                "version_id": VERSION,
                "result_id": RESULT,
                "sequence": 1,
                "payload_sha256": payload_hash,
            },
        }
        params = json.loads(db.execute("SELECT params FROM jobs WHERE id=?", (job,)).fetchone()[0])
        params["working_role"] = role
        db.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job))
        db.execute("UPDATE working_job_authorities SET role_json=?", (canonical(role).decode(),))
    assert inspect(path)["schema_version"] == 18


@pytest.mark.parametrize(
    "damage",
    [
        "owner",
        "role",
        "snapshot",
        "source_hash",
        "source_payload",
        "timing_payload",
        "admission",
        "kind",
    ],
)
def test_invalid_working_authority_is_refused(tmp_path, damage):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 18)
    job = add_authority(path, "timing" if damage == "timing_payload" else "result")
    with sqlite3.connect(path) as db:
        if damage == "owner":
            db.execute("UPDATE working_job_authorities SET recording_id=?", ("7" * 32,))
        elif damage == "role":
            db.execute(
                "UPDATE working_job_authorities SET role_json=?",
                (canonical({"revision": 1, "working": None}).decode(),),
            )
        elif damage == "snapshot":
            db.execute("UPDATE working_job_authorities SET source_snapshot_sha256=?", ("0" * 64,))
        elif damage == "source_hash":
            db.execute("UPDATE working_job_authorities SET source_payload_sha256=?", ("0" * 64,))
        elif damage in {"source_payload", "timing_payload"}:
            params = json.loads(
                db.execute("SELECT params FROM jobs WHERE id=?", (job,)).fetchone()[0]
            )
            params["source_snapshot"]["segments"][0]["text"] = "Forged text"
            db.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job))
            db.execute(
                "UPDATE working_job_authorities SET source_snapshot_sha256=?",
                (hashlib.sha256(canonical(params["source_snapshot"])).hexdigest(),),
            )
        elif damage == "admission":
            db.execute("UPDATE working_job_authorities SET admission_sha256=?", ("0" * 64,))
        else:
            db.execute("UPDATE jobs SET kind='transcribe' WHERE id=?", (job,))
    with pytest.raises(ValueError, match="inspection"):
        inspect(path)


def test_restore_preserves_authority_as_inert_quarantined_evidence(tmp_path):
    library = tmp_path / "staging" / "library"
    evidence = library / "preservation" / ("f" * 32)
    evidence.mkdir(parents=True)
    source = evidence / "source.sqlite3"
    build(source, 18)
    job_id = add_authority(source, "result")
    with sqlite3.connect(source) as db:
        before = db.execute("SELECT * FROM working_job_authorities").fetchall()
    value = manifest(source)
    working = library / "library.sqlite3"
    portable_semantics.prepare_restore(
        source,
        working,
        expected_sha256=value["database"]["object"],
        expected_size=value["database"]["size"],
        manifest=value,
        target_root=tmp_path / "restored",
        restore_id="9" * 32,
    )
    with sqlite3.connect(working) as db:
        assert db.execute("SELECT * FROM working_job_authorities").fetchall() == before
        assert db.execute(
            "SELECT 1 FROM restored_owners WHERE kind='job' AND owner_id=?", (job_id,)
        ).fetchone() == (1,)
