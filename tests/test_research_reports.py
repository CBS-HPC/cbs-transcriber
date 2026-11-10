import copy
import hashlib
import io
import json
from contextlib import contextmanager
from zipfile import ZipFile

import pytest
from report_support import selected_view_report
from test_research_history import legacy5

from cbs_transcriber import research_reports, result_roles
from cbs_transcriber.exports import export_bytes
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import edit_segment, normalize_segments


def fixture_result(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("PRIVATE_PROJECT_NAME")
    recording = store.create_recording(project["id"], "PRIVATE_RECORDING_NAME.wav")
    result = store.add_result(
        recording["id"],
        normalize_segments(
            [
                {
                    "text": "PRIVATE_ORIGINAL_TEXT",
                    "start": 0,
                    "end": 2,
                    "speaker": "PRIVATE_SPEAKER_ID",
                }
            ]
        ),
        {"PRIVATE_SPEAKER_ID": "PRIVATE_SPEAKER_NAME"},
        "PRIVATE_PROVIDER_LABEL",
        {
            "kind": "provider",
            "model": "whisper-1",
            "language": "da",
            "capability": "words",
            "provider_type": "openai",
            "base_url": "https://PRIVATE_ENDPOINT.example/v1",
            "api_key": "PRIVATE_SECRET",
            "job_id": "PRIVATE_REMOTE_JOB",
            "runtime_python": "C:/PRIVATE_RUNTIME/python.exe",
        },
    )
    return store, project, recording, result


@pytest.mark.parametrize(
    "damage",
    [
        "predecessor_cycle",
        "sequence_gap",
        "source_retarget",
        "noncanonical_payload",
        "private_observed_at",
        "private_sequence",
        "private_role_sequence",
    ],
)
def test_v65_malformed_preserved_history_is_refused_before_report_projection(tmp_path, damage):
    store, project, recording, result = fixture_result(tmp_path)
    rid = recording["id"]
    initial = store.result_versions(rid, result["id"])[0]
    selected = initial
    if damage in {"sequence_gap", "source_retarget"}:
        store.save_transcript(rid, normalize_segments([{"text": "Later wording."}]), {}, 1)
        selected = store.result_versions(rid, result["id"])[0]
    if damage == "predecessor_cycle":
        store.execute("UPDATE result_versions SET predecessor_id=id WHERE id=?", (initial["id"],))
    elif damage == "sequence_gap":
        store.execute("UPDATE result_versions SET sequence=3 WHERE id=?", (selected["id"],))
    elif damage == "source_retarget":
        other = store.add_result(
            rid, normalize_segments([{"text": "Other source."}]), {}, "Other", {}
        )
        other_version = store.result_versions(rid, other["id"])[0]
        store.execute(
            "UPDATE result_versions SET source_version_id=? WHERE id=?",
            (other_version["id"], selected["id"]),
        )
    elif damage == "noncanonical_payload":
        saved = store.one("SELECT payload FROM result_versions WHERE id=?", (initial["id"],))
        payload = json.loads(saved["payload"])
        payload["unsupported"] = "PRIVATE_UNSUPPORTED"
        encoded = json.dumps(payload)
        store.execute(
            "UPDATE result_versions SET payload=?,payload_sha256=? WHERE id=?",
            (encoded, hashlib.sha256(encoded.encode()).hexdigest(), initial["id"]),
        )
    elif damage == "private_observed_at":
        store.execute(
            "UPDATE result_versions SET observed_at='C:/PRIVATE_REPORT_PATH/token' WHERE id=?",
            (initial["id"],),
        )
    elif damage == "private_sequence":
        # SQLite's INTEGER affinity and CHECK(sequence>0) still permit this text value.
        store.execute(
            "UPDATE result_versions SET sequence='PRIVATE_REPORT_SEQUENCE' WHERE id=?",
            (initial["id"],),
        )
    else:
        other = store.add_result(
            rid, normalize_segments([{"text": "Unselected Working."}]), {}, "Other", {}
        )
        other_version = store.result_versions(rid, other["id"])[0]
        current = store.recording(rid)
        plan = result_roles.preview(store, rid, other_version["id"], current["revision"], 0)
        result_roles.apply(
            store, rid, other_version["id"], current["revision"], 0, plan["fingerprint"]
        )
        store.execute(
            "UPDATE result_versions SET sequence='PRIVATE_REPORT_ROLE_SEQUENCE' WHERE id=?",
            (other_version["id"],),
        )
    before = store.all("SELECT * FROM result_versions ORDER BY id")
    with pytest.raises(ValueError):
        selected_view_report(
            store,
            project["id"],
            [{"recording_id": rid, "result_id": result["id"], "version_id": selected["id"]}],
        )
    assert store.all("SELECT * FROM result_versions ORDER BY id") == before


@pytest.mark.parametrize("field", ["observed_at", "sequence"])
def test_v65_invalid_observation_metadata_is_refused_before_loading_version_row(
    tmp_path, monkeypatch, field
):
    store, project, recording, result = fixture_result(tmp_path)
    version = store.result_versions(recording["id"], result["id"])[0]
    store.execute(
        f"UPDATE result_versions SET {field}=? WHERE id=?",
        ("PRIVATE_REPORT_METADATA" + "x" * 65, version["id"]),
    )
    connection = store.connection

    class Guarded:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, parameters=()):
            assert "SELECT v.*" not in sql, "Unvalidated observation metadata was loaded"
            return self.db.execute(sql, parameters)

    @contextmanager
    def guarded_connection():
        with connection() as db:
            yield Guarded(db)

    monkeypatch.setattr(store, "connection", guarded_connection)
    with pytest.raises(ValueError):
        selected_view_report(
            store,
            project["id"],
            [
                {
                    "recording_id": recording["id"],
                    "result_id": result["id"],
                    "version_id": version["id"],
                }
            ],
        )


def unpack(data):
    with ZipFile(io.BytesIO(data)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    return json.loads(files["manifest.json"]), files


def test_v65_default_report_omits_private_content_and_preserves_exact_observations(tmp_path):
    store, project, recording, result = fixture_result(tmp_path)
    changed = [edit_segment(result["segments"][0], "free", "Visible Danish æøå.")]
    store.save_transcript(
        recording["id"],
        changed,
        result["speakers"],
        1,
        action="free_rewrite",
        context={"segment_id": "0"},
        reason="PRIVATE_REVIEW_REASON",
    )
    store.save_notes("projects", project["id"], "PRIVATE_NOTES", 0)
    before = store.all("SELECT * FROM result_versions ORDER BY id")
    manifest, files = unpack(selected_view_report(store, project["id"]))
    assert set(files) == {"manifest.json", "methods.md"}
    combined = b"\n".join(files.values()).decode("utf-8")
    for private in (
        "PRIVATE_",
        project["id"],
        recording["id"],
        result["id"],
        "Visible Danish",
        "api_key",
        "base_url",
        "runtime_python",
    ):
        assert private not in combined
    assert manifest["format"] == "cbs-research-report-v1"
    assert len(manifest["versions"]) == 2
    assert manifest["manual_change_counts"] == {"free_rewrite": 1}
    selected = next(v for v in manifest["versions"] if v["selected"])
    assert selected["processing"]["model"] == "whisper-1"
    assert selected["processing"]["language"] == "da"
    assert selected["coverage"]["timed_lexical_tokens"] == 0
    assert not selected["actor_identity_verified"]
    assert store.all("SELECT * FROM result_versions ORDER BY id") == before


def test_v65_text_opt_in_exports_only_chosen_visible_version_with_verifiable_bytes(tmp_path):
    store, project, recording, result = fixture_result(tmp_path)
    initial = store.result_versions(recording["id"], result["id"])[0]
    changed = [edit_segment(result["segments"][0], "free", "Visible Danish æøå.")]
    store.save_transcript(recording["id"], changed, result["speakers"], 1)
    chosen = store.result_versions(recording["id"], result["id"])[0]
    manifest, files = unpack(
        selected_view_report(
            store,
            project["id"],
            [
                {
                    "recording_id": recording["id"],
                    "result_id": result["id"],
                    "version_id": chosen["id"],
                }
            ],
            True,
        )
    )
    entry = manifest["exports"][0]
    rendered = files[entry["path"]]
    assert rendered == export_bytes(changed, result["speakers"], "speaker", "text")
    assert entry["sha256"] == hashlib.sha256(rendered).hexdigest()
    assert manifest["inclusion"]["speaker_names_in_selected_text"] is True
    assert "speaker labels/names are included" in files["methods.md"].decode()
    assert b"PRIVATE_ORIGINAL_TEXT" not in rendered and "æøå" in rendered.decode()
    assert all(".." not in name and not name.startswith("/") for name in files)
    # Historical rendering still uses the original saved state after the current result changes.
    old_manifest, old_files = unpack(
        selected_view_report(
            store,
            project["id"],
            [
                {
                    "recording_id": recording["id"],
                    "result_id": result["id"],
                    "version_id": initial["id"],
                }
            ],
            True,
        )
    )
    assert old_files[old_manifest["exports"][0]["path"]] == export_bytes(
        result["segments"], result["speakers"], "speaker", "text"
    )
    assert old_manifest["inclusion"]["unselected_history_text"] is False


def test_v65_report_checks_project_recording_result_and_version_ownership(tmp_path):
    store, project, recording, result = fixture_result(tmp_path)
    version = store.result_versions(recording["id"], result["id"])[0]
    other_project = store.create_project("Other")
    other = store.create_recording(project["id"], "Other.wav")
    selection = {
        "recording_id": recording["id"],
        "result_id": result["id"],
        "version_id": version["id"],
    }
    with pytest.raises(KeyError):
        selected_view_report(store, other_project["id"], [selection])
    for key, value in (
        ("recording_id", other["id"]),
        ("result_id", "missing"),
        ("version_id", "missing"),
    ):
        with pytest.raises(KeyError):
            selected_view_report(store, project["id"], [selection | {key: value}])
    with pytest.raises(ValueError, match="only once"):
        selected_view_report(store, project["id"], [selection, selection])
    with pytest.raises(ValueError):
        selected_view_report(store, project["id"], [selection] * 101)


def test_v65_report_is_one_snapshot_when_an_edit_commits_during_capture(tmp_path, monkeypatch):
    store, project, recording, result = fixture_result(tmp_path)
    writer = Store(tmp_path)
    expected = store.result_versions(recording["id"], result["id"])[0]["payload_sha256"]
    connection = store.connection
    edited = False

    class Interleaved:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, params=()):
            nonlocal edited
            cursor = self.db.execute(sql, params)
            if "COUNT(*) FROM recordings" in sql and not edited:
                edited = True
                changed = [edit_segment(result["segments"][0], "free", "Concurrent edit.")]
                writer.save_transcript(recording["id"], changed, result["speakers"], 1)
            return cursor

    @contextmanager
    def interleave():
        with connection() as db:
            yield Interleaved(db)

    monkeypatch.setattr(store, "connection", interleave)
    manifest, _ = unpack(selected_view_report(store, project["id"]))
    assert edited and len(manifest["versions"]) == 1
    assert manifest["versions"][0]["payload_sha256"] == expected
    assert len(writer.result_versions(recording["id"], result["id"])) == 2


def test_v65_corrupted_payload_and_oversized_lineage_fail_without_fallback(tmp_path, monkeypatch):
    store, project, recording, result = fixture_result(tmp_path)
    version = store.result_versions(recording["id"], result["id"])[0]
    monkeypatch.setattr(research_reports, "MAX_PAYLOAD_BYTES", 1)
    with pytest.raises(ValueError, match="32 MiB"):
        selected_view_report(store, project["id"])
    monkeypatch.setattr(research_reports, "MAX_PAYLOAD_BYTES", 32 * 1024 * 1024)
    monkeypatch.setattr(research_reports, "MAX_VERSIONS", 0)
    with pytest.raises(ValueError, match="1,000"):
        selected_view_report(store, project["id"])
    monkeypatch.setattr(research_reports, "MAX_VERSIONS", 1000)
    # Simulate external corruption rather than exercising a public mutation interface.
    with store.connection() as db:
        for trigger in db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='result_versions'"
        ).fetchall():
            db.execute('DROP TRIGGER "' + trigger["name"].replace('"', '""') + '"')
        db.execute("UPDATE result_versions SET payload='{}' WHERE id=?", (version["id"],))
    with pytest.raises(ValueError, match="integrity"):
        selected_view_report(store, project["id"])


def test_v65_unknown_processing_is_not_invented_or_copied_from_free_form_fields(tmp_path):
    store, project, recording, result = fixture_result(tmp_path)
    source = {"kind": "import", "model": "PRIVATE_MODEL\n<script>", "notes": "PRIVATE_NOTES"}
    imported = store.add_result(
        recording["id"],
        copy.deepcopy(result["segments"]),
        {},
        "Imported",
        source,
        activate=True,
        revision=1,
    )
    manifest, files = unpack(selected_view_report(store, project["id"]))
    assert "PRIVATE_" not in b"\n".join(files.values()).decode()
    selected = next(v for v in manifest["versions"] if v["selected"])
    assert selected["processing"] == {}
    assert "not captured" in " ".join(selected["provenance_gaps"])
    assert store.result(recording["id"], imported["id"])["source"] == source


def test_v65_report_checks_payload_budget_before_loading_large_content(tmp_path, monkeypatch):
    store, project, _, _ = fixture_result(tmp_path)
    connection = store.connection

    class BoundedReader:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, params=()):
            if "SELECT v.*" in sql:
                pytest.fail("Loaded version payload before checking the content budget")
            return self.db.execute(sql, params)

    @contextmanager
    def guarded():
        with connection() as db:
            yield BoundedReader(db)

    monkeypatch.setattr(store, "connection", guarded)
    monkeypatch.setattr(research_reports, "MAX_PAYLOAD_BYTES", 1)
    with pytest.raises(ValueError, match="32 MiB"):
        selected_view_report(store, project["id"])


@pytest.mark.parametrize("provider_type", ["openai", "custom", "ucloud-vllm", "ucloud"])
def test_v65_processing_preserves_actual_provider_types(provider_type):
    assert research_reports._processing({"provider_type": provider_type}) == {
        "provider_type": provider_type,
    }


def test_v65_coverage_counts_text_tokens_when_provider_word_units_differ():
    segments = normalize_segments(
        [
            {
                "text": "can't stop",
                "start": 0,
                "end": 3,
                "words": [
                    {"word": "can", "start": 0, "end": 1},
                    {"word": "'t", "start": 1, "end": 2},
                    {"word": "stop", "start": 2, "end": 3},
                ],
            }
        ]
    )
    coverage = research_reports._coverage({"segments": segments})
    assert coverage["lexical_tokens"] == 2
    assert coverage["timed_lexical_tokens"] == 2
    segments[0]["words"][1].update(start=None, end=None)
    assert research_reports._coverage({"segments": segments})["timed_lexical_tokens"] == 1


def test_v79_coverage_retains_current_partial_maps_but_excludes_legacy_rewrites():
    segments = normalize_segments(
        [
            {
                "text": "ONE two gap",
                "start": 0,
                "end": 3,
                "words": [
                    {"word": "ONE", "start": 0, "end": 0.8},
                    {"word": "two", "start": 1, "end": 1.8},
                ],
            }
        ]
    )
    segments[0]["edited"] = True
    assert research_reports._coverage({"segments": segments})["timed_lexical_tokens"] == 0
    segments[0]["words_current"] = True
    coverage = research_reports._coverage({"segments": segments})
    assert coverage["timed_lexical_tokens"] == 2
    assert coverage["complete_word_timing_passages"] == 0


def test_v65_legacy_processing_is_unverified_observation_not_reconstructed_evidence(tmp_path):
    _, recording, _, _ = legacy5(tmp_path)
    store = Store(tmp_path)
    manifest, files = unpack(selected_view_report(store, recording["project_id"]))
    observed = manifest["versions"][0]
    assert observed["action"] == "legacy_observed"
    assert observed["processing_observation"] == "legacy_unverified"
    assert "before this observation is unknown" in " ".join(observed["provenance_gaps"])
    assert "not verified run evidence" in files["methods.md"].decode()
