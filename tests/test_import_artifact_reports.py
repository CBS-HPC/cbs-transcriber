"""Import-byte receipts and metadata-only research reports use synthetic libraries."""

import copy
import hashlib
import io
import json
from contextlib import contextmanager
from zipfile import ZipFile

import pytest
from report_support import selected_view_report

from cbs_transcriber import research_reports, result_merges
from cbs_transcriber.history import MAX_IMPORT_ARTIFACT_BYTES, validate_import_artifact_evidence
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import edit_segment, normalize_segments

RAW = '\ufeff {"segments":[{"text":"PRIVATE_ORIGINAL_TEXT æøå","start":0,"end":2}]}\r\n'.encode()


def imported(tmp_path, *, data=RAW, filename="PRIVATE_FILE.v2.json"):
    store = Store(tmp_path)
    project = store.create_project("PRIVATE_PROJECT")
    recording = store.create_recording(project["id"], "PRIVATE_AUDIO.wav")
    result = store.import_transcript(recording["id"], data, filename, 0)
    return store, project, recording, result


def unpack(data):
    with ZipFile(io.BytesIO(data)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    return json.loads(files["manifest.json"]), files


def set_source(store, result_id, source):
    store.execute(
        "UPDATE transcript_results SET source=? WHERE id=?",
        (json.dumps(source, ensure_ascii=False), result_id),
    )


def set_context(store, result_id, context):
    store.execute(
        "UPDATE result_versions SET context=? WHERE result_id=?",
        (json.dumps(context), result_id),
    )


def selection(store, recording_id, result_id):
    return {
        "recording_id": recording_id,
        "result_id": result_id,
        "version_id": store.result_versions(recording_id, result_id)[0]["id"],
    }


def receipt():
    return {
        "schema": 1,
        "role": "transcript_import",
        "source_id": "a" * 32,
        "sha256": "b" * 64,
        "bytes": 1,
        "format": "json",
    }


@pytest.mark.parametrize("size", [1, MAX_IMPORT_ARTIFACT_BYTES])
@pytest.mark.parametrize("format", ["json", "csv"])
@pytest.mark.parametrize("schema", [1, 2])
def test_v127_pure_receipt_validator_returns_exact_fresh_canonical_value(size, format, schema):
    original = receipt() | {"schema": schema, "bytes": size, "format": format}
    supplied = dict(reversed(list(original.items())))
    validated = validate_import_artifact_evidence(supplied)
    assert validated == original and validated is not supplied
    assert list(validated) == ["schema", "role", "source_id", "sha256", "bytes", "format"]
    supplied["bytes"] = 10
    assert validated["bytes"] == size


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("schema", True),
        ("schema", 1.0),
        ("schema", 3),
        ("role", "original_media"),
        ("role", None),
        ("source_id", "A" * 32),
        ("source_id", "a" * 31),
        ("source_id", "../PRIVATE_PATH"),
        ("source_id", None),
        ("sha256", "B" * 64),
        ("sha256", "b" * 63),
        ("sha256", "g" * 64),
        ("sha256", ["PRIVATE_TOKEN"]),
        ("bytes", True),
        ("bytes", 1.0),
        ("bytes", 0),
        ("bytes", -1),
        ("bytes", MAX_IMPORT_ARTIFACT_BYTES + 1),
        ("format", "JSON"),
        ("format", "txt"),
        ("filename", "PRIVATE_FILE.json"),
        ("path", "C:/PRIVATE_FOLDER/file.json"),
    ],
)
def test_v127_receipt_validator_rejects_malformed_and_private_fields(key, value):
    with pytest.raises(ValueError, match="artifact evidence") as error:
        validate_import_artifact_evidence(receipt() | {key: value})
    assert "PRIVATE_" not in str(error.value)


@pytest.mark.parametrize("value", [None, "not_captured", {}, [], False])
def test_v127_receipt_validator_does_not_coerce_unknown_or_incomplete_values(value):
    with pytest.raises(ValueError):
        validate_import_artifact_evidence(value)


@pytest.mark.parametrize(
    ("data", "filename", "format"),
    [
        (RAW, "PRIVATE_FILE.v2.JSON", "json"),
        (
            '\ufeffstart,end,text\r\n0,2,"PRIVATE_ORIGINAL_TEXT æøå"\r\n'.encode(),
            "PRIVATE.v2.csv",
            "csv",
        ),
    ],
)
def test_v127_report_shows_exact_import_byte_reference_without_private_details_or_file_reads(
    tmp_path, monkeypatch, data, filename, format
):
    store, project, recording, result = imported(tmp_path, data=data, filename=filename)
    evidence = result["source"]["artifact_evidence"]
    source = result["source"] | {
        "path": "C:/PRIVATE_PATH/file.json",
        "filename": filename,
        "api_key": "PRIVATE_TOKEN",
    }
    set_source(store, result["id"], source)
    retained = store.directory(recording["id"]) / "sources" / f"{evidence['source_id']}.{format}"
    assert retained.read_bytes() == data
    retained.unlink()  # Simulate later missing media; export must not assert/check availability.
    before = store.all("SELECT * FROM result_versions")

    def refuse(*args, **kwargs):
        pytest.fail("Research reports must not locate or read retained import files")

    monkeypatch.setattr(store, "directory", refuse)
    manifest, files = unpack(selected_view_report(store, project["id"]))
    assert set(files) == {"manifest.json", "methods.md"}
    assert manifest["versions"][0]["artifact_evidence"] == {
        "source": "source-001",
        "role": "transcript_import",
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "format": format,
        "parser_generation": 2,
        "availability": "not_checked",
    }
    serialized = b"\n".join(files.values()).decode()
    for private in (
        "PRIVATE_",
        project["id"],
        recording["id"],
        result["id"],
        evidence["source_id"],
    ):
        assert private not in serialized
    assert "availability is not checked" in serialized
    assert "do not establish a complete backup" in serialized
    assert "Original media/raw response hashes and full processing environment" in serialized
    assert store.all("SELECT * FROM result_versions") == before


def test_v127_edit_restore_trash_and_merge_keep_import_origin_and_derived_lineage(tmp_path):
    store, project, recording, result = imported(tmp_path)
    rid = recording["id"]
    evidence = copy.deepcopy(result["source"]["artifact_evidence"])
    origin = store.result_versions(rid, result["id"])[0]
    changed = [edit_segment(result["segments"][0], "free", "PRIVATE_REVIEWED_TEXT")]
    current = store.save_transcript(rid, changed, {}, 1, action="free_rewrite")
    current = store.save_transcript(
        rid,
        result["segments"],
        {},
        current["revision"],
        action="version_restore",
        context={"restored_from_version_id": origin["id"]},
    )
    current = store.set_result_trashed(rid, result["id"], current["revision"], trashed=True)
    current = store.set_result_trashed(rid, result["id"], current["revision"], trashed=False)
    frozen = store.freeze_result(rid, result["id"], current["revision"])
    job = store.add_job(rid, "enhance", {"source_snapshot": frozen})
    improved = normalize_segments(
        [
            {
                "text": result["segments"][0]["text"],
                "start": 0,
                "end": 2,
                "words": [
                    {"word": "PRIVATE_ORIGINAL_TEXT", "start": 0.1, "end": 0.9},
                    {"word": "æøå", "start": 1.0, "end": 1.9},
                ],
            }
        ]
    )
    incoming = store.add_result(
        rid,
        improved,
        {},
        "PRIVATE_ENHANCEMENT_LABEL",
        {"kind": "enhancement", "source_result_id": result["id"]},
        job_id=job["id"],
    )
    planned = result_merges.preview(store, rid, result["id"], incoming["id"], current["revision"])
    result_merges.apply(
        store, rid, result["id"], incoming["id"], current["revision"], planned["fingerprint"]
    )
    assert store.result(rid, result["id"])["source"] == result["source"]
    assert "artifact_evidence" not in incoming["source"]
    for item in store.result_versions(rid, result["id"]):
        assert item["context"]["artifact_evidence"] == evidence
    manifest, files = unpack(selected_view_report(store, project["id"]))
    with_receipt = [item for item in manifest["versions"] if "artifact_evidence" in item]
    assert len(with_receipt) >= 5
    assert {item["artifact_evidence"]["source"] for item in with_receipt} == {"source-001"}
    assert any(item["action"] == "result_merge" for item in with_receipt)
    assert any(
        item["source_version"] and "artifact_evidence" not in item for item in manifest["versions"]
    )
    assert "PRIVATE_" not in b"\n".join(files.values()).decode()


@pytest.mark.parametrize(
    "damage",
    [
        "source_absent",
        "source_unknown",
        "context_absent",
        "context_unknown",
        "context_null",
        "source_null",
        "wrong_kind",
        "hash",
        "bytes",
        "format",
        "schema",
        "source_id",
        "extra_context",
        "extra_source",
        "corrupt_source",
        "non_object_source",
    ],
)
def test_v127_reports_refuse_one_sided_malformed_or_contradictory_claims(tmp_path, damage):
    store, project, recording, result = imported(tmp_path)
    source = copy.deepcopy(result["source"])
    context = store.result_versions(recording["id"], result["id"])[0]["context"]
    if damage == "source_absent":
        source.pop("artifact_evidence")
    elif damage == "source_unknown":
        source["artifact_evidence"] = "not_captured"
    elif damage == "context_absent":
        context.pop("artifact_evidence")
    elif damage == "context_unknown":
        context["artifact_evidence"] = "not_captured"
    elif damage == "context_null":
        context["artifact_evidence"] = None
    elif damage == "source_null":
        source["artifact_evidence"] = None
    elif damage == "wrong_kind":
        source["kind"] = "provider"
    elif damage in {"hash", "bytes", "format", "source_id", "schema"}:
        key = "sha256" if damage == "hash" else damage
        source["artifact_evidence"][key] = {
            "sha256": "0" * 64,
            "bytes": 1,
            "format": "csv",
            "source_id": "c" * 32,
            "schema": 1,
        }[key]
    elif damage == "extra_context":
        context["artifact_evidence"]["path"] = "PRIVATE_CONTEXT_PATH"
    elif damage == "extra_source":
        source["artifact_evidence"]["api_key"] = "PRIVATE_TOKEN"
    set_context(store, result["id"], context)
    set_source(store, result["id"], source)
    if damage in {"corrupt_source", "non_object_source"}:
        store.execute(
            "UPDATE transcript_results SET source=? WHERE id=?",
            ("{" if damage == "corrupt_source" else "[]", result["id"]),
        )
    before = store.all("SELECT * FROM result_versions")
    with pytest.raises(ValueError):
        selected_view_report(store, project["id"])
    assert store.all("SELECT * FROM result_versions") == before


@pytest.mark.parametrize("source_unknown", [False, True])
@pytest.mark.parametrize("context_unknown", [False, True])
def test_v127_legacy_absence_and_exact_unknown_are_never_backfilled(
    tmp_path, source_unknown, context_unknown
):
    store = Store(tmp_path)
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "synthetic.wav")
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Synthetic"}]),
        {},
        "Legacy",
        {"kind": "import"},
    )
    source = {"kind": "import"} | ({"artifact_evidence": "not_captured"} if source_unknown else {})
    context = {"artifact_evidence": "not_captured"} if context_unknown else {}
    set_source(store, result["id"], source)
    set_context(store, result["id"], context)
    before = store.all("SELECT * FROM result_versions")
    manifest, _ = unpack(selected_view_report(store, project["id"]))
    assert "artifact_evidence" not in manifest["versions"][0]
    assert store.all("SELECT * FROM result_versions") == before


def test_v127_aliases_distinguish_ids_scope_recordings_and_refuse_identity_conflicts(tmp_path):
    store, project, recording, first = imported(tmp_path)
    second = store.import_transcript(recording["id"], RAW, "second.json", 1)
    chosen = [selection(store, recording["id"], item["id"]) for item in (first, second)]
    manifest, _ = unpack(selected_view_report(store, project["id"], chosen))
    assert {item["artifact_evidence"]["source"] for item in manifest["versions"]} == {
        "source-001",
        "source-002",
    }
    assert len({item["artifact_evidence"]["sha256"] for item in manifest["versions"]}) == 1
    other = store.create_recording(project["id"], "Other.wav")
    third = store.import_transcript(
        other["id"], RAW.replace(b"PRIVATE_ORIGINAL_TEXT", b"OTHER_WORDS"), "third.json", 0
    )
    source = copy.deepcopy(third["source"])
    source["artifact_evidence"]["source_id"] = first["source"]["artifact_evidence"]["source_id"]
    context = store.result_versions(other["id"], third["id"])[0]["context"]
    context["artifact_evidence"] = source["artifact_evidence"]
    set_source(store, third["id"], source)
    set_context(store, third["id"], context)
    manifest, _ = unpack(selected_view_report(store, project["id"]))
    assert {item["recording"] for item in manifest["versions"]} == {
        "recording-001",
        "recording-002",
    }
    assert {item["artifact_evidence"]["source"] for item in manifest["versions"]} == {"source-001"}
    # Reusing that opaque ID for different bytes within one recording is contradictory.
    source = copy.deepcopy(second["source"])
    source["artifact_evidence"] = copy.deepcopy(first["source"]["artifact_evidence"])
    source["artifact_evidence"]["sha256"] = "0" * 64
    context = store.result_versions(recording["id"], second["id"])[0]["context"]
    context["artifact_evidence"] = source["artifact_evidence"]
    set_source(store, second["id"], source)
    set_context(store, second["id"], context)
    with pytest.raises(ValueError, match="identity is contradictory"):
        selected_view_report(store, project["id"], chosen)


def test_v127_source_validation_shares_the_report_snapshot_with_versions(tmp_path, monkeypatch):
    store, project, recording, result = imported(tmp_path)
    writer = Store(tmp_path)
    original = store.connection
    changed = False

    class Interleaved:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, params=()):
            nonlocal changed
            cursor = self.db.execute(sql, params)
            if sql.startswith("SELECT length(CAST(source AS BLOB))") and not changed:
                changed = True
                source = copy.deepcopy(result["source"])
                source["artifact_evidence"]["sha256"] = "0" * 64
                set_source(writer, result["id"], source)
            return cursor

    @contextmanager
    def connection():
        with original() as db:
            yield Interleaved(db)

    monkeypatch.setattr(store, "connection", connection)
    manifest, _ = unpack(selected_view_report(store, project["id"]))
    assert changed
    assert manifest["versions"][0]["artifact_evidence"]["sha256"] == hashlib.sha256(RAW).hexdigest()
    with pytest.raises(ValueError, match="contradicts its source"):
        selected_view_report(store, project["id"])


def test_v127_source_is_preflighted_and_budgeted_once_per_result(tmp_path, monkeypatch):
    store, project, recording, result = imported(tmp_path)
    current = store.recording(recording["id"])
    for text in ("First review", "Second review", "Third review"):
        segments = [edit_segment(current["segments"][0], "free", text)]
        current = store.save_transcript(
            recording["id"], segments, {}, current["revision"], action="free_rewrite"
        )
    source_size = store.all("SELECT length(CAST(source AS BLOB)) size FROM transcript_results")[0][
        "size"
    ]
    monkeypatch.setattr(research_reports, "MAX_SOURCE_BYTES", source_size)
    counts = {"preflight": 0, "read": 0}
    original = store.connection

    class Counted:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, params=()):
            if sql.startswith("SELECT length(CAST(source AS BLOB))"):
                counts["preflight"] += 1
            if sql.startswith("SELECT source FROM transcript_results"):
                counts["read"] += 1
                assert counts["preflight"] == 1
            return self.db.execute(sql, params)

    @contextmanager
    def connection():
        with original() as db:
            yield Counted(db)

    monkeypatch.setattr(store, "connection", connection)
    manifest, _ = unpack(selected_view_report(store, project["id"]))
    assert len(manifest["versions"]) == 4
    assert counts == {"preflight": 1, "read": 1}


def test_v127_aggregate_source_budget_refuses_before_loading_the_overflow_source(
    tmp_path, monkeypatch
):
    store, project, recording, result = imported(tmp_path)
    other = store.create_recording(project["id"], "other.wav")
    store.import_transcript(other["id"], RAW, "other.json", 0)
    sizes = [
        item["size"]
        for item in store.all("SELECT length(CAST(source AS BLOB)) size FROM transcript_results")
    ]
    monkeypatch.setattr(research_reports, "MAX_SOURCE_BYTES", sum(sizes) - 1)
    original = store.connection
    counts = {"preflight": 0, "read": 0}

    class Bounded:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, params=()):
            if sql.startswith("SELECT length(CAST(source AS BLOB))"):
                counts["preflight"] += 1
            if sql.startswith("SELECT source FROM transcript_results"):
                counts["read"] += 1
                assert counts["preflight"] == 1, "Loaded overflowing source before its budget check"
            return self.db.execute(sql, params)

    @contextmanager
    def connection():
        with original() as db:
            yield Bounded(db)

    monkeypatch.setattr(store, "connection", connection)
    with pytest.raises(ValueError, match="result sources exceed"):
        selected_view_report(store, project["id"])
    assert counts == {"preflight": 2, "read": 1}
