"""Danish candidate integration uses synthetic CT2 receipts and mocked ASR, never weights."""

import hashlib
import json
import sys
import types
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from cbs_transcriber import danish_models as dm
from cbs_transcriber import enhancement
from cbs_transcriber import enhancement_api as api
from cbs_transcriber import enhancement_worker as worker
from cbs_transcriber import runtime_batch_remote as remote
from cbs_transcriber import runtime_batches as batches


def conversion(choice):
    definition = dm.models()[choice]
    recipe = dict(dm.CONTRACT, contract_sha256=dm.RECIPE_CONTRACT_SHA256, torch_build="2.8.0+cpu")
    recipe["sha256"] = dm._digest(recipe)
    receipt = {
        "schema": 1,
        "choice": choice,
        "source": {
            "repo": definition["repo"],
            "revision": definition["revision"],
            "manifest_sha256": dm._digest(
                sorted(definition["source_files"], key=lambda v: v["name"])
            ),
        },
        "recipe": recipe,
        "files": [
            {"name": name, "bytes": 1, "sha256": "a" * 64} for name in sorted(dm.OUTPUT_NAMES)
        ],
    }
    receipt["artifact_sha256"] = dm._digest(receipt)
    return receipt


def prepare_task(folder, choice="edda-turbo", *, execution="local"):
    options = enhancement.EnhancementOptions(
        whisper_model=choice,
        transcribe=True,
        language="da",
        device="cuda" if execution == "ucloud" else "cpu",
        execution=execution,
    )
    configured = options.model_dump(exclude={"runtime_python", "prepare_runtime"}) | {
        "duration": 2.0,
        "whole_recording": False,
    }
    folder.mkdir(parents=True, exist_ok=True)
    with wave.open(str(folder / "audio.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 32000)
    worker.write_json(folder / "request.json", {"options": configured, "passages": []})
    return options, configured


def saved_stage(folder, choice="edda-turbo", *, execution="local"):
    options, configured = prepare_task(folder, choice, execution=execution)
    binding = worker.language_binding(folder)
    evidence = worker._configured_language_evidence(
        binding, configured, worker.language_windows(binding)[0], SimpleNamespace(language="da")
    )
    packet = {
        "segments": [{"text": "Øresund er åbent.", "start": 0.1, "end": 1.8}],
        "language_evidence": [evidence],
        "asr_conversion": dm.evidence(
            choice,
            conversion(choice),
            request_sha256=binding["request_sha256"],
            input_sha256=binding["input_sha256"],
        ),
    }
    worker.write_json(folder / "transcription.json", packet)
    return options, configured, packet


@pytest.mark.parametrize("choice", sorted(worker.DANISH_CHOICES))
def test_candidates_are_fixed_danish_full_asr_only(choice, tmp_path, monkeypatch):
    assert worker.MODELS[choice] == dm.source_identity(choice)
    assert worker.WHISPER_MODELS[choice] == choice
    for changes in (
        {"language": "en"},
        {"language": "auto"},
        {"transcribe": False},
        {"retranscribe": True},
    ):
        with pytest.raises(ValueError, match="Danish full-recording"):
            enhancement.EnhancementOptions(
                **({"whisper_model": choice, "transcribe": True} | changes)
            )
    options, configured = prepare_task(tmp_path, choice)
    remote_options = configured | {"execution": "ucloud", "device": "cuda"}
    expected = ["transcription.json", "alignment.json"]
    assert remote.BatchController._valid_request(
        {"options": remote_options, "passages": []}, expected
    )
    for changes in (
        {"language": "en"},
        {"language": "auto"},
        {"transcribe": False},
        {"retranscribe": True},
    ):
        assert not remote.BatchController._valid_request(
            {"options": remote_options | changes, "passages": []}, expected
        )
        bad = configured | changes
        monkeypatch.setitem(sys.modules, "torch", None)
        assert worker.execute({"options": bad, "passages": []}, tmp_path) == "language_scope"
    assert options.transcribe


@pytest.mark.parametrize("choice", sorted(worker.DANISH_CHOICES))
def test_inference_uses_verified_conversion_then_cached_recovery_needs_no_model(
    choice, tmp_path, monkeypatch
):
    monkeypatch.setattr(worker, "_DANISH_MODULE", dm)
    options, configured = prepare_task(tmp_path / "task", choice)
    cache = tmp_path / "converted"
    cache.mkdir()
    (cache / "tokenizer.json").write_text("{}", encoding="utf-8")
    calls = []

    def prepared(name, *, cache, verify):
        assert name == choice and verify is True
        assert cache == worker.hub_cache()
        calls.append("prepared")
        return tmp_path / "converted", conversion(name)

    monkeypatch.setattr(dm, "prepared", prepared)

    class Model:
        def __init__(self, location, **kwargs):
            assert location == cache and kwargs["local_files_only"] is True
            calls.append("load")

        def transcribe(self, audio, **kwargs):
            assert kwargs["language"] == "da"
            return iter(
                [SimpleNamespace(text=" Øresund er åbent.", start=0.1, end=1.8)]
            ), SimpleNamespace(language="da")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))

    def refuse(*args, **kwargs):
        pytest.fail("No raw HF snapshot, download or model access is allowed here")

    rows = worker.transcribe_audio(
        [0.0] * 32000, configured, tmp_path / "task", refuse, lambda *a: None
    )
    assert rows[0]["text"] == "Øresund er åbent." and calls == ["prepared", "load"]
    packet = json.loads((tmp_path / "task" / "transcription.json").read_bytes())
    assert packet["asr_conversion"]["source"]["revision"] == dm.source_identity(choice)[1]
    monkeypatch.setattr(dm, "prepared", refuse)
    monkeypatch.setattr(dm, "models", refuse)
    monkeypatch.setattr(dm, "source_identity", refuse)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    assert worker.transcribe_audio(None, configured, tmp_path / "task", refuse, refuse) == rows
    metadata = enhancement.asr_language_evidence(tmp_path / "task", options)
    assert metadata == {key: packet[key] for key in ("language_evidence", "asr_conversion")}


@pytest.mark.parametrize(
    "damage", ["missing", "request", "audio", "pin", "choice", "artifact", "recipe"]
)
def test_completed_conversion_refuses_missing_or_mismatched_evidence(tmp_path, monkeypatch, damage):
    monkeypatch.setattr(worker, "_DANISH_MODULE", dm)
    options, configured, packet = saved_stage(tmp_path)
    receipt = packet["asr_conversion"]
    if damage == "missing":
        del packet["asr_conversion"]
    elif damage == "request":
        receipt["request_sha256"] = "0" * 64
    elif damage == "audio":
        receipt["input_sha256"] = "0" * 64
    elif damage == "pin":
        receipt["source"]["revision"] = "0" * 40
    elif damage == "choice":
        receipt["choice"] = "edda-large"
    elif damage == "artifact":
        receipt["files"][0]["sha256"] = "0" * 64
    else:
        receipt["recipe"]["local_files_only"] = False
    worker.write_json(tmp_path / "transcription.json", packet)
    with pytest.raises(ValueError):
        worker.converted_transcription_metadata(tmp_path, configured)
    with pytest.raises(ValueError):
        enhancement.asr_language_evidence(tmp_path, options)


def test_completed_conversion_uses_captured_and_frozen_pin_not_current_model_registry(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(worker, "_DANISH_MODULE", dm)
    options, _, packet = saved_stage(tmp_path)
    pin = dm.source_identity("edda-turbo")
    monkeypatch.setitem(worker.MODELS, "edda-turbo", (pin[0], "0" * 40))
    assert (
        enhancement.asr_language_evidence(tmp_path, options)["asr_conversion"]
        == packet["asr_conversion"]
    )
    job = {
        "params": {
            "ucloud_api": {
                "batch_plan": {"identity": {"model_revisions": {"edda-turbo": list(pin)}}}
            }
        }
    }
    assert (
        enhancement.asr_language_evidence(tmp_path, options, job=job)["asr_conversion"]
        == packet["asr_conversion"]
    )
    job["params"]["ucloud_api"]["batch_plan"]["identity"]["model_revisions"]["edda-turbo"][1] = (
        "0" * 40
    )
    with pytest.raises(ValueError, match="frozen model revision"):
        enhancement.asr_language_evidence(tmp_path, options, job=job)


def test_unprepared_candidate_refuses_before_inference_import_or_raw_fallback(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(worker, "_DANISH_MODULE", dm)
    _, configured = prepare_task(tmp_path)

    def missing(*args, **kwargs):
        raise ValueError("Not prepared")

    monkeypatch.setattr(dm, "prepared", missing)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    assert worker.transcribe_audio(None, configured, tmp_path, None, lambda *a: None) == "cache"


def test_old_choices_preserve_paths_pins_and_refuse_new_receipt(tmp_path):
    assert worker.WHISPER_MODELS["small"] == "coarse"
    assert (
        worker.snapshot_folder("coarse", tmp_path)
        == tmp_path
        / "models--Systran--faster-whisper-small"
        / "snapshots"
        / "536b0662742c02347bc0e980a01041f333bce120"
    )
    options, configured = prepare_task(tmp_path, "small")
    worker.write_json(tmp_path / "transcription.json", {"segments": [], "asr_conversion": {}})
    with pytest.raises(ValueError, match="cannot reuse"):
        enhancement.asr_language_evidence(tmp_path, options)
    with pytest.raises(ValueError, match="cannot reuse"):
        worker.transcribe_audio(None, configured, tmp_path, None, None)


def frozen_controller(tmp_path):
    root = Path(worker.__file__).parent
    controller = remote.BatchController.__new__(remote.BatchController)
    controller.directory, controller._auto_module = tmp_path, None
    controller.manifest = {"code_hashes": {}}
    for name in ("enhancement_worker.py", "danish_models.py", "danish_whisper_models.json"):
        path = root / name if name != "danish_whisper_models.json" else root / "catalogs" / name
        raw = path.read_bytes().replace(b"\r\n", b"\n")
        (tmp_path / name).write_bytes(raw)
        controller.manifest["code_hashes"][name] = hashlib.sha256(raw).hexdigest()
    return controller


def test_remote_bundle_pins_companions_and_isolated_loading(tmp_path):
    names = {path.name for path in api.WORKER_FILES}
    assert {"danish_models.py", "danish_whisper_models.json"} <= names <= remote.SOURCE_NAMES
    controller = frozen_controller(tmp_path)
    options, configured, packet = saved_stage(tmp_path / "task", execution="ucloud")
    module, pin = controller._auto_worker(configured)
    assert pin == dm.source_identity("edda-turbo")
    # Exact helper source executes, independently of Python's ordinary import machinery.
    assert module.danish_module().CHOICES == dm.CHOICES
    assert (
        module.converted_transcription_metadata(tmp_path / "task", configured)["asr_conversion"]
        == packet["asr_conversion"]
    )
    (tmp_path / "danish_models.py").write_bytes(b"raise RuntimeError('changed')")
    with pytest.raises(remote.OwnershipConflict, match="companion"):
        controller._auto_worker(configured)


@pytest.mark.parametrize("choice", sorted(worker.DANISH_CHOICES))
def test_frozen_remote_acceptance_carry_and_output_bind_conversion_receipt(tmp_path, choice):
    controller = frozen_controller(tmp_path)
    folder = tmp_path / "task"
    _, _, packet = saved_stage(folder, choice, execution="ucloud")
    worker.write_json(folder / "alignment.json", {"passages": []})
    names = ("request.json", "audio.wav", "transcription.json", "alignment.json")
    inputs = {
        "files": {name: remote.file_receipt(folder / name, remote.OUTPUT_LIMIT) for name in names},
        "expected_outputs": ["transcription.json", "alignment.json"],
    }
    task = {"task_id": "1" * 32}
    controller._task_directory = lambda task_id: folder
    controller.accepted = {}
    controller.inputs = {task["task_id"]: inputs}
    assert controller._verify_inputs(task, inputs)
    assert set(controller._outputs(task["task_id"])) == {"transcription.json", "alignment.json"}
    packet["asr_conversion"]["input_sha256"] = "0" * 64
    worker.write_json(folder / "transcription.json", packet)
    inputs["files"]["transcription.json"] = remote.file_receipt(
        folder / "transcription.json", remote.OUTPUT_LIMIT
    )
    assert not controller._verify_inputs(task, inputs)
    with pytest.raises(ValueError):
        controller._outputs(task["task_id"])
    assert controller._outputs(task["task_id"], partial=True) == {}


def test_prefetch_and_readiness_require_derived_marker_not_raw_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "_DANISH_MODULE", dm)
    seen = []
    monkeypatch.setitem(sys.modules, "nltk", SimpleNamespace(download=lambda *a, **k: True))
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=lambda repo, **kwargs: seen.append(repo)),
    )
    monkeypatch.setenv("NLTK_DATA", str(tmp_path))
    monkeypatch.setattr(dm, "prepare", lambda choice, *, cache: seen.append(choice))
    monkeypatch.setattr(worker, "cache_status", lambda: {})
    worker.prefetch(["edda-turbo"])
    assert seen[-1] == "edda-turbo" and dm.source_identity("edda-turbo")[0] not in seen
    settings = SimpleNamespace(base_url="https://cloud.example")
    monkeypatch.setattr(api, "runtime_folder", lambda settings: "/drive/runtime")
    monkeypatch.setattr(api, "danish_module", lambda: dm)
    folder = api.model_folder(settings, "edda-turbo")
    assert "/hf/hub/cbs-ct2/edda-turbo/" in folder
    receipt = conversion("edda-turbo")
    monkeypatch.setattr(api, "read_json", lambda *a: receipt)
    files = [
        {"id": folder + "/" + item["name"], "status": {"sizeInBytes": item["bytes"]}}
        for item in receipt["files"]
    ]
    files.append({"id": folder + "/" + dm.MARKER})
    client = SimpleNamespace(browse_files=lambda *a, **k: {"items": files})
    assert api.converted_model_ready(client, settings, "edda-turbo")
    files[0]["status"]["sizeInBytes"] = 2
    assert not api.converted_model_ready(client, settings, "edda-turbo")


@pytest.mark.parametrize("valid", [True, False])
def test_host_partial_stage_needs_conversion_proof_before_advertising_carry(
    tmp_path, monkeypatch, valid
):
    monkeypatch.setattr(worker, "_DANISH_MODULE", dm)
    options, _, packet = saved_stage(tmp_path)
    if not valid:
        del packet["asr_conversion"]
        worker.write_json(tmp_path / "transcription.json", packet)
    item = SimpleNamespace(
        options=options,
        folder=tmp_path,
        job={"id": "1" * 32, "params": {}},
        recording={"duration": 2.0},
    )
    outputs = {
        "transcription.json": remote.file_receipt(
            tmp_path / "transcription.json", remote.OUTPUT_LIMIT
        )
    }
    coordinator = batches.Coordinator.__new__(batches.Coordinator)
    assert coordinator.partial_stages(item, outputs) == (outputs if valid else {})
    assert (tmp_path / "invalid-transcription.json").exists() is not valid
