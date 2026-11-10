"""Receipt-selected import semantics preserve exact legacy creations without fallback."""

import gc
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from cbs_transcriber import portable_capture, portable_restore
from cbs_transcriber.history import encode_payload, validate_import_artifact_evidence
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import parse_transcript, pop_hints

# Exact creation hashes observed from immutable ea5b3c8's legacy parser, before schema2.
LEGACY = [
    (
        b'[{"text":"First voice","speaker":0}]',
        "source.json",
        "547413a597405cac8141919b673dfea629c401348c7868ee8318179eba26ca6b",
    ),
    (
        b"start,end,text,text\n0,1,FIRST,SECOND\n",
        "source.csv",
        "32b217191e4ef2449cf8c3c18878371c6721ad91098848fc8738d4ab9ca8d155",
    ),
    (
        b'start,end,text\n0,1,"FIRST\n1,2,SECOND\n',
        "source.csv",
        "c1cb5bab37af1800510602c2234bcda17198836db2196282bae766c69dcd7a7f",
    ),
    (
        b'start,end,text\n0,1,"FIRST"unexpected\n',
        "source.csv",
        "970f73b5f590fa47ee5969802e2e3b9998352eda6c8f743627f9eff3726103fc",
    ),
    (
        b"start,end,text\n0,1,FIRST,SECOND\n",
        "source.csv",
        "6057fce0daf45fd9ab16ced0302d184944b6e21462eea52f8145e6e2067beced",
    ),
    (
        b"start,text,end\n0,FIRST\n",
        "source.csv",
        "ce7f69c0bb81820e67843d26da5d8ad02374f02030128b0374558e2600f3dbda",
    ),
]


def evidence(raw, filename, schema, source_id="a" * 32):
    return {
        "schema": schema,
        "role": "transcript_import",
        "source_id": source_id,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "format": Path(filename).suffix.lstrip("."),
    }


@pytest.mark.parametrize("raw,filename,digest", LEGACY, ids=range(len(LEGACY)))
def test_schema1_keeps_the_exact_frozen_legacy_creation(raw, filename, digest):
    parsed = parse_transcript(raw, filename, generation=1)
    pop_hints(parsed)
    assert encode_payload(parsed, {})[1] == digest
    assert parsed == parse_transcript(raw, filename)  # Existing provider callers stay legacy.


@pytest.mark.parametrize("raw,filename,digest", LEGACY[1:], ids=range(1, len(LEGACY)))
def test_new_import_refuses_ambiguous_csv_before_publishing(client, raw, filename, digest):
    store = client.app.state.store
    project = store.create_project("Synthetic malformed import")
    recording = store.create_recording(project["id"], "synthetic.wav")
    response = client.post(
        f"/api/recordings/{recording['id']}/transcript",
        data={"revision": 0},
        files={"file": (filename, raw)},
    )
    assert response.status_code == 400, response.text
    assert "FIRST" not in response.text and "SECOND" not in response.text
    assert store.results(recording["id"]) == []
    assert store.recording(recording["id"])["revision"] == 0
    assert not (store.directory(recording["id"]) / "sources").exists()


@pytest.mark.parametrize("shape", ["list", "object"])
def test_new_import_binds_schema2_and_keeps_numeric_zero_voice(client, shape):
    store = client.app.state.store
    project = store.create_project("Synthetic zero voice")
    recording = store.create_recording(project["id"], "synthetic.wav")
    rows = [{"text": "Hej æøå 👩🏽\u200d🔬", "speaker": 0}]
    raw = json.dumps(rows if shape == "list" else {"segments": rows}).encode()
    response = client.post(
        f"/api/recordings/{recording['id']}/transcript",
        data={"revision": 0},
        files={"file": ("many.parts.JSON", raw)},
    )
    assert response.status_code == 200, response.text
    result = store.result(recording["id"], store.results(recording["id"])[0]["id"])
    expected = evidence(raw, "source.json", 2, result["source"]["artifact_evidence"]["source_id"])
    assert result["source"]["artifact_evidence"] == expected
    assert result["segments"][0]["speaker"] == "0"
    assert result["segments"][0]["text"] == rows[0]["text"]
    assert (
        store.result_versions(recording["id"], result["id"])[0]["context"]["artifact_evidence"]
        == expected
    )


@pytest.mark.parametrize("schema", [True, 0, 1.0, 2.0, 3, "2"])
def test_unknown_or_nonliteral_generation_is_never_fallback_authority(schema):
    with pytest.raises(ValueError):
        parse_transcript(LEGACY[0][0], "source.json", generation=schema)
    with pytest.raises(ValueError):
        validate_import_artifact_evidence(evidence(LEGACY[0][0], "source.json", schema))


@pytest.mark.parametrize("raw,filename,digest", LEGACY, ids=range(len(LEGACY)))
def test_schema2_cannot_publish_a_legacy_interpretation(tmp_path, raw, filename, digest):
    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic exact generation")
    recording = store.create_recording(project["id"], "synthetic.wav")
    receipt = evidence(raw, filename, 2)
    target = store._import_source_path(recording["id"], receipt)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    parsed = parse_transcript(raw, filename, generation=1)
    pop_hints(parsed)
    assert encode_payload(parsed, {})[1] == digest
    with pytest.raises(ValueError):
        store.add_result(
            recording["id"],
            parsed,
            {},
            "Forged generation",
            {"kind": "import"},
            import_artifact_evidence=receipt,
        )
    assert store.results(recording["id"]) == []
    assert store.all("SELECT * FROM result_versions") == []
    assert target.read_bytes() == raw


@pytest.mark.parametrize("forged", [False, True])
def test_portable_same_raw_bytes_are_verified_separately_for_each_generation(tmp_path, forged):
    from test_portable_semantics import build, inspect, manifest, map_body

    path = tmp_path / "synthetic.sqlite3"
    build(path)
    raw, filename, _ = LEGACY[0]
    with sqlite3.connect(path) as db:
        for generation, result_id, version_id, source_id in (
            (1, "3" * 32, "4" * 32, "a" * 32),
            (2, "5" * 32, "6" * 32, "b" * 32),
        ):
            selected = 1 if forged else generation
            parsed = parse_transcript(raw, filename, generation=selected)
            pop_hints(parsed)
            payload, payload_hash = encode_payload(parsed, {})
            receipt = evidence(raw, filename, generation, source_id)
            source = json.dumps({"kind": "import", "artifact_evidence": receipt})
            context = json.dumps({"artifact_evidence": receipt})
            if generation == 1:
                db.execute(
                    "UPDATE result_versions SET context=?,payload=?,payload_sha256=?",
                    (context, payload, payload_hash),
                )
                db.execute(
                    "UPDATE transcript_results SET source=?,segments=?",
                    (source, json.dumps(parsed)),
                )
                db.execute("UPDATE recordings SET segments=?", (json.dumps(parsed),))
            else:
                db.execute(
                    "INSERT INTO transcript_results VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        result_id,
                        "2" * 32,
                        "Second",
                        source,
                        json.dumps(parsed),
                        "{}",
                        None,
                        "d",
                        "d",
                    ),
                )
                db.execute(
                    "INSERT INTO result_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        version_id,
                        result_id,
                        1,
                        None,
                        None,
                        payload,
                        payload_hash,
                        "d",
                        "legacy_observed",
                        context,
                        "migration",
                        None,
                    ),
                )
    value = manifest(path)
    for source_id in ("a" * 32, "b" * 32):
        map_body(value, path, "sources/" + source_id + ".json", raw, "import_source")
    if forged:
        with pytest.raises(ValueError, match="inspection"):
            inspect(path, value)
    else:
        assert inspect(path, value)["semantic_counts"]["file_mappings"] == 3


@pytest.fixture
def short_portable_root():
    # Sources add a UUID filename beneath both full-ID restore parents and a staging prefix.
    # Keep this synthetic root below native Win32 limits without weakening production checks.
    with tempfile.TemporaryDirectory(prefix="a03-") as directory:
        yield Path(directory)
        gc.collect()  # Release sqlite3's read-only constructor-check connection cycles on Windows.


def test_legacy_and_current_receipts_round_trip_without_reinterpreting_history(
    wav_bytes, short_portable_root
):
    store = Store(short_portable_root / "source")
    project = store.create_project("Synthetic generations")
    recording = store.create_recording(project["id"], "synthetic.wav")
    folder = store.directory(recording["id"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "original.wav").write_bytes(wav_bytes)
    store.execute("UPDATE recordings SET status='ready'")
    queue = JobQueue(store, None)
    rid = recording["id"]
    originals = []
    for index, (raw, filename, digest) in enumerate(LEGACY):
        receipt = evidence(raw, filename, 1, f"{index:032x}")
        target = store._import_source_path(rid, receipt)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        parsed = parse_transcript(raw, filename, generation=1)
        pop_hints(parsed)
        assert encode_payload(parsed, {})[1] == digest
        originals.append(
            store.add_result(
                rid, parsed, {}, "Legacy", {"kind": "import"}, import_artifact_evidence=receipt
            )
        )
    raw, filename, _ = LEGACY[0]
    current = store.import_transcript(rid, raw, filename, store.recording(rid)["revision"])
    assert current["source"]["artifact_evidence"]["schema"] == 2
    assert current["segments"][0]["speaker"] == "0"
    with store.library_lock():
        captured = portable_capture.capture_library(
            store,
            queue,
            short_portable_root / "packages",
            gate=portable_capture.CaptureGate(),
        )
    target = short_portable_root / "restored"
    restored = portable_restore.restore_library(
        captured["folder"],
        expected_commit_sha256=captured["commit_sha256"],
        target=target,
        live_root=store.root,
    )
    assert restored["state"] == "complete"
    reopened = Store(target)
    for original in originals + [current]:
        copied = reopened.result(rid, original["id"])
        assert copied["segments"] == original["segments"]
        assert copied["source"] == original["source"]
        version = reopened.result_versions(rid, original["id"])[0]
        assert version["context"]["artifact_evidence"] == original["source"]["artifact_evidence"]
