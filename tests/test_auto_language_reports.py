"""Automatic ASR observations remain bounded and truthful in preserved research metadata."""

import copy
import hashlib
import io
import json
from zipfile import ZipFile

import pytest
from report_support import selected_view_report

from cbs_transcriber import enhancement_worker as worker
from cbs_transcriber import history, research_reports
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


def decision(status="resolved", count=1):
    return {
        "schema": 1,
        "requested": "auto",
        "effective": "fr" if status == "unsupported" else "da",
        "origin": "asr_detection",
        "scope": "full_asr_input",
        "window": {"start": 0.0, "end": 3.0, "timebase": "prepared_audio"},
        "detector_window": None,
        "input_sha256": "a" * 64,
        "request_sha256": "b" * 64,
        "model": {"choice": "small", "repo": "historical/asr", "revision": "c" * 40},
        "engine": {"name": "faster-whisper", "version": "0.10.0"},
        "policy": {
            "id": "faster-whisper-vad-info-v1",
            "experimental": True,
            "min_probability": 0.8,
            "language_detection_segments": 1,
            "language_detection_threshold": 0.5,
        },
        "status": status,
        "probability": 0.5 if status == "uncertain" else 0.95,
        "duration_after_vad": 0.0 if status == "no_speech" else 2.0,
        "segment_count": count,
    }


def outcome(value, state=None):
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return {
        "schema": 1,
        "state": state or ("aligned" if value["status"] == "resolved" else "unavailable_language"),
        "decision_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def source_for(value, state=None):
    result = {
        "kind": "provider",
        "provider_type": "whisperx",
        "engine": "whisperx",
        "model": "small",
        "language": "auto",
        "capability": "words" if value["status"] == "resolved" else "segments",
        "options": {"language": "auto", "align": state != "not_requested", "diarize": False},
        "language_decision": copy.deepcopy(value),
        "alignment_outcome": outcome(value, state),
        "api_key": "PRIVATE_TOKEN",
        "ucloud_job_id": "PRIVATE_REMOTE_JOB",
        "runtime_python": "C:/PRIVATE_PATH/python.exe",
        "base_url": "https://PRIVATE_ENDPOINT",
    }
    return result


def configured_receipt(value):
    return {
        key: copy.deepcopy(value[key])
        for key in ("schema", "window", "input_sha256", "request_sha256", "model", "engine")
    } | {
        "requested": "da",
        "effective": "da",
        "origin": "configured",
        "scope": "recording",
        "probability": None,
    }


def saved_report(tmp_path, source, count=1):
    store = Store(tmp_path)
    project = store.create_project("PRIVATE_PROJECT_NAME")
    recording = store.create_recording(project["id"], "PRIVATE_RECORDING.wav")
    segments = (
        normalize_segments(
            [
                {
                    "text": "PRIVATE_TRANSCRIPT æøå",
                    "start": 0.5,
                    "end": 2.0,
                    "speaker": "PRIVATE_SPEAKER_ID",
                }
            ]
        )
        if count
        else []
    )
    result = store.add_result(
        recording["id"],
        segments,
        {"PRIVATE_SPEAKER_ID": "PRIVATE_SPEAKER_NAME"},
        "PRIVATE_LABEL",
        source,
    )
    version = store.result_versions(recording["id"], result["id"])[0]
    before = store.all("SELECT * FROM result_versions")
    with ZipFile(io.BytesIO(selected_view_report(store, project["id"]))) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    assert store.all("SELECT * FROM result_versions") == before
    combined = b"\n".join(files.values()).decode("utf-8")
    for private in ("PRIVATE_", project["id"], recording["id"], result["id"], version["id"]):
        assert private not in combined
    return version, json.loads(files["manifest.json"])["versions"][0], files["methods.md"].decode()


@pytest.mark.parametrize(
    ("status", "state", "count"),
    [
        ("resolved", "aligned", 1),
        ("unsupported", "unavailable_language", 1),
        ("uncertain", "unavailable_language", 1),
        ("no_speech", "unavailable_language", 0),
        ("uncertain", "unavailable_language", 0),
        ("unsupported", "not_requested", 1),
    ],
)
def test_v124_auto_decision_and_alignment_outcome_survive_history_and_report(
    tmp_path, status, state, count
):
    value = decision(status, count)
    source = source_for(value, state)
    version, exported, methods = saved_report(tmp_path, source, count)
    for processing in (version["context"]["processing"], exported["processing"]):
        assert processing["language"] == processing["options"]["language"] == "auto"
        assert processing["language_decision"] == value
        assert processing["alignment_outcome"] == source["alignment_outcome"]
    assert "Bound language processing evidence was not captured." not in exported["provenance_gaps"]
    assert "The word-alignment outcome was not captured." not in exported["provenance_gaps"]
    assert "Automatic ASR requested" in methods
    assert f"model-reported decoder language={value['effective']}" in methods
    assert f"language outcome={status}" in methods
    assert "language scope=full ASR input; detector source window is unknown" in methods
    assert "not measured accuracy" in methods and "experimental uncalibrated" in methods
    assert "ASR model=historical/asr@" + "c" * 40 in methods
    assert "observed package version=0.10.0" in methods
    assert "configured decoding" not in methods
    if state == "aligned":
        assert "word alignment stage ran; usable coverage is reported separately" in methods
        # Stage execution does not establish word coverage, even with a words-capable result.
        assert exported["coverage"]["complete_word_timing_passages"] == 0
    elif state == "not_requested":
        assert "word alignment was not requested" in methods
    else:
        assert "word alignment unavailable for language; ASR output retained" in methods
        assert "word alignment stage ran" not in methods
    if status == "no_speech":
        assert "ASR/VAD reported no speech after filtering; no passages retained" in methods
        assert exported["coverage"]["passages"] == 0
    elif status == "uncertain" and count == 0:
        assert "ASR returned no usable passages; language remains uncertain" in methods
        assert "ASR/VAD reported no speech" not in methods


def test_v124_historical_projection_needs_no_input_runtime_or_current_model(tmp_path, monkeypatch):
    value = decision()
    source = source_for(value)

    def refuse(*args, **kwargs):
        pytest.fail("Historical projection must not inspect processing inputs/runtime/models")

    class NoModels(dict):
        def __getitem__(self, key):
            refuse()

    monkeypatch.setattr(worker, "language_binding", refuse)
    monkeypatch.setattr(worker, "file_hash", refuse)
    monkeypatch.setattr(worker.importlib.metadata, "version", refuse)
    monkeypatch.setattr(worker, "MODELS", NoModels())
    metadata = history.processing_metadata(source)
    assert metadata["language_decision"] == value
    metadata["language_decision"]["engine"]["version"] = None
    assert source["language_decision"]["engine"]["version"] == "0.10.0"
    _, exported, methods = saved_report(tmp_path, source)
    assert exported["processing"]["language_decision"]["model"] == value["model"]
    assert "historical/asr@" in methods


def test_v124_unknown_observations_remain_unknown_in_methods(tmp_path):
    value = decision("uncertain")
    value.update(effective=None, probability=None)
    value["engine"]["version"] = None
    source = source_for(value)
    _, exported, methods = saved_report(tmp_path, source)
    assert exported["processing"]["language_decision"] == value
    assert "decoder language=not captured" in methods
    assert "language probability=not captured" in methods
    assert "observed package version=not captured" in methods


def test_v124_missing_request_envelope_remains_unknown_without_discarding_valid_decision():
    source = source_for(decision())
    del source["language"], source["options"]
    for projected in (history.processing_metadata(source), research_reports._processing(source)):
        assert projected["language_decision"] == source["language_decision"]
        assert projected["alignment_outcome"] == source["alignment_outcome"]
        assert "language" not in projected and "options" not in projected


@pytest.mark.parametrize("changed", ["missing", "digest", "null", "extra", "align"])
def test_v124_bad_or_missing_outcome_retains_decision_with_explicit_gap(tmp_path, changed):
    value = decision()
    source = source_for(value)
    if changed == "missing":
        del source["alignment_outcome"]
    elif changed == "digest":
        source["alignment_outcome"]["decision_sha256"] = "0" * 64
    elif changed == "null":
        source["alignment_outcome"] = None
    elif changed == "extra":
        source["alignment_outcome"]["path"] = "C:/PRIVATE_PATH"
    else:
        source["options"]["align"] = False
    _, exported, methods = saved_report(tmp_path, source)
    assert exported["processing"]["language_decision"] == value
    assert "alignment_outcome" not in exported["processing"]
    assert "The word-alignment outcome was not captured." in exported["provenance_gaps"]
    assert "Bound language processing evidence was not captured." not in exported["provenance_gaps"]
    assert "word alignment stage ran" not in methods


@pytest.mark.parametrize(
    "changed", ["model", "engine", "window", "policy", "extra", "huge", "null", "bool"]
)
def test_v124_metadata_rejects_malformed_decisions_at_capture_and_export(changed):
    source = source_for(decision())
    value = source["language_decision"]
    if changed in {"model", "engine", "window", "policy"}:
        value[changed]["private_path"] = "C:/PRIVATE_PATH"
    elif changed == "extra":
        value["task_id"] = "PRIVATE_TASK"
    elif changed == "huge":
        value["window"]["end"] = 10**400
    elif changed == "null":
        value["model"] = None
    else:
        value["segment_count"] = True
    for projected in (history.processing_metadata(source), research_reports._processing(source)):
        assert "language_decision" not in projected
        assert "alignment_outcome" not in projected
        assert "PRIVATE_" not in json.dumps(projected)


@pytest.mark.parametrize("automatic", [None, "valid"])
def test_v124_mixed_configured_and_automatic_origin_publishes_neither(tmp_path, automatic):
    value = decision()
    source = source_for(value)
    source["language_evidence"] = [configured_receipt(value)]
    if automatic is None:
        source["language_decision"] = None
    _, exported, methods = saved_report(tmp_path, source)
    for key in ("language_evidence", "language_decision", "alignment_outcome"):
        assert key not in exported["processing"]
    assert "Bound language processing evidence was not captured." in exported["provenance_gaps"]
    assert "Automatic ASR requested" not in methods and "configured decoding" not in methods


def test_v124_outcome_without_valid_decision_is_not_published():
    source = source_for(decision())
    del source["language_decision"]
    assert history.automatic_language_metadata(source) == {}
    assert "alignment_outcome" not in research_reports._processing(source)


@pytest.mark.parametrize("changed", ["language", "options", "null"])
def test_v124_conflicting_requested_language_is_not_published(changed):
    source = source_for(decision())
    if changed == "options":
        source["options"]["language"] = "da"
    else:
        source["language"] = None if changed == "null" else "en"
    for projected in (history.processing_metadata(source), research_reports._processing(source)):
        assert "language_decision" not in projected
        assert "alignment_outcome" not in projected


@pytest.mark.parametrize("captured", [False, True])
def test_v124_non_asr_pin_gaps_never_substitute_todays_models(tmp_path, captured):
    source = source_for(decision())
    source["options"]["diarize"] = True
    if captured:
        source["model_revisions"] = {
            "da": {"repo": "historical/aligner", "revision": "d" * 40},
            "speakers": {"repo": "historical/speakers", "revision": "e" * 40},
        }
    _, exported, methods = saved_report(tmp_path, source)
    gaps = exported["provenance_gaps"]
    assert ("The alignment model revision was not captured." in gaps) is not captured
    assert ("The speaker model revision was not captured." in gaps) is not captured
    assert ("historical/aligner@" in methods) is captured
    assert ("historical/speakers@" in methods) is captured


def test_v124_configured_language_receipts_keep_their_previous_metadata_projection():
    receipt = configured_receipt(decision())
    source = {"engine": "whisperx", "language": "da", "language_evidence": [receipt]}
    expected = copy.deepcopy(source)
    assert history.processing_metadata(source) == expected
    assert research_reports._processing(source) == expected
    assert source == expected
