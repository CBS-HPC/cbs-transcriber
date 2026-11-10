"""Historical Danish conversion evidence is copied and verified without model/runtime access."""

import copy
import io
import json
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest
from test_working_reports import mark_active_latest

from cbs_transcriber import danish_models as dm
from cbs_transcriber import enhancement_worker as worker
from cbs_transcriber import history, research_reports
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


def captured(choice="edda-turbo"):
    repo, revision = worker.MODELS[choice]
    recipe = dict(dm.CONTRACT, contract_sha256=dm.RECIPE_CONTRACT_SHA256, torch_build="2.8.0+cpu")
    recipe["sha256"] = dm._digest(recipe)
    receipt = {
        "schema": 1,
        "choice": choice,
        "source": {"repo": repo, "revision": revision, "manifest_sha256": "a" * 64},
        "recipe": recipe,
        "files": [
            {"name": name, "bytes": 1, "sha256": "b" * 64} for name in sorted(dm.OUTPUT_NAMES)
        ],
    }
    receipt["artifact_sha256"] = dm._digest(receipt)
    receipt = dm.evidence(choice, receipt, request_sha256="1" * 64, input_sha256="2" * 64)
    language = {
        "schema": 1,
        "requested": "da",
        "effective": "da",
        "origin": "configured",
        "probability": None,
        "scope": "recording",
        "window": {"start": 0.0, "end": 2.0, "timebase": "prepared_audio"},
        "input_sha256": "2" * 64,
        "request_sha256": "1" * 64,
        "model": {"choice": choice, "repo": repo, "revision": revision},
        "engine": {"name": "faster-whisper", "version": "1.2.1"},
    }
    return {
        "kind": "provider",
        "provider_type": "whisperx",
        "model": choice,
        "language": "da",
        "engine": "whisperx",
        "version": "3.8.6",
        "execution": "ucloud",
        "capability": "words",
        "model_revisions": {choice: {"repo": repo, "revision": revision}},
        "options": {
            "language": "da",
            "whisper_model": choice,
            "transcribe": True,
            "retranscribe": False,
            "align": True,
            "diarize": False,
            "timing_policy": "retain-estimates",
            "runtime_python": "C:/PRIVATE_RUNTIME/python.exe",
        },
        "language_evidence": [language],
        "asr_conversion": receipt,
        "api_key": "PRIVATE_KEY",
        "base_url": "https://PRIVATE_ENDPOINT.example",
        "job_id": "PRIVATE_JOB_ID",
        "provider_id": "PRIVATE_PROVIDER_ID",
    }


@pytest.mark.parametrize("choice", dm.CHOICES)
def test_historical_proof_is_pure_and_does_not_read_current_models_or_files(choice, monkeypatch):
    source = captured(choice)
    before = copy.deepcopy(source)

    def refuse(*args, **kwargs):
        pytest.fail("Historical validation must not read model files or current registry")

    for name in ("models", "_models", "source_identity", "prepared", "prepare"):
        monkeypatch.setattr(dm, name, refuse)
    monkeypatch.setattr(worker, "MODELS", {})
    monkeypatch.setattr(Path, "open", refuse)
    for name in ("torch", "faster_whisper", "whisperx", "transformers", "ctranslate2"):
        monkeypatch.setitem(sys.modules, name, None)
    metadata = history.processing_metadata(source)
    assert metadata["asr_conversion"] == before["asr_conversion"]
    report = research_reports._processing(metadata)
    assert report["asr_conversion"] == before["asr_conversion"]
    assert report["model_revisions"][choice] == before["model_revisions"][choice]
    assert report["options"]["timing_policy"] == "retain-estimates"
    assert source == before and "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize(
    "damage",
    [
        "request",
        "audio",
        "source",
        "choice",
        "recipe",
        "artifact",
        "scope",
        "long_scope",
        "frozen",
        "options",
        "parent_model",
        "extra",
    ],
)
def test_invalid_conversion_is_not_published_as_provenance(damage):
    source = captured()
    receipt = source["asr_conversion"]
    if damage == "request":
        receipt["request_sha256"] = "0" * 64
    elif damage == "audio":
        receipt["input_sha256"] = "0" * 64
    elif damage == "source":
        receipt["source"]["revision"] = "0" * 40
    elif damage == "choice":
        receipt["choice"] = "edda-large"
    elif damage == "recipe":
        receipt["recipe"]["local_files_only"] = False
    elif damage == "artifact":
        receipt["files"][0]["sha256"] = "0" * 64
    elif damage == "scope":
        source["language_evidence"][0]["scope"] = "passage"
        source["language_evidence"][0]["passage_id"] = "PRIVATE_PASSAGE_ID"
    elif damage == "long_scope":
        source["language_evidence"][0]["window"]["end"] = 6 * 3600 + 1
    elif damage == "frozen":
        source["model_revisions"]["edda-turbo"]["revision"] = "0" * 40
    elif damage == "options":
        source["options"]["whisper_model"] = "roest-v3"
    elif damage == "parent_model":
        source["model"] = "edda-large"
    else:
        receipt["private_path"] = "C:/PRIVATE_FILE"
    assert "asr_conversion" not in history.processing_metadata(source)
    assert "asr_conversion" not in research_reports._processing(source)


@pytest.mark.parametrize("policy", [None, True, False, 0, "unknown", "C:/PRIVATE_PATH", []])
def test_timing_policy_is_a_strict_report_enum(policy):
    source = {"options": {"timing_policy": policy}}
    assert "timing_policy" not in history.processing_metadata(source).get("options", {})
    assert "timing_policy" not in research_reports._processing(source).get("options", {})


def test_legacy_without_conversion_remains_unchanged():
    source = {"provider_type": "openai", "model": "whisper-1", "language": "da"}
    assert history.processing_metadata(source) == source
    assert research_reports._processing(source) == source


def test_report_explains_captured_conversion_and_excludes_private_data(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("PRIVATE_PROJECT")
    recording = store.create_recording(project["id"], "PRIVATE_RECORDING.wav")
    source = captured("roest-v3")
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "PRIVATE_TRANSCRIPT", "start": 0, "end": 2}]),
        {},
        "PRIVATE_PROVIDER_NAME",
        source,
    )
    before = store.all("SELECT * FROM result_versions ORDER BY id")
    mark_active_latest(store, project["id"])
    with ZipFile(io.BytesIO(research_reports.project_report(store, project["id"]))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        methods = archive.read("methods.md").decode("utf-8")
    selected = next(version for version in manifest["versions"] if version["selected"])
    assert selected["processing"]["asr_conversion"] == source["asr_conversion"]
    assert "CTranslate2 conversion of a pinned Danish Whisper source" in methods
    assert (
        "does not check model files, establish current availability or measure model accuracy"
        in methods
    )
    assert source["asr_conversion"]["artifact_sha256"] in methods
    assert (
        "valid estimated word intervals were retained below the legacy alignment-score cutoff"
        in methods
    )
    combined = json.dumps(manifest) + methods
    for private in (
        "PRIVATE",
        project["id"],
        recording["id"],
        result["id"],
        "runtime_python",
        "api_key",
        "base_url",
    ):
        assert private not in combined
    assert store.all("SELECT * FROM result_versions ORDER BY id") == before
