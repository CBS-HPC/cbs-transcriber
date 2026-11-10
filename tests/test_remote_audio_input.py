"""Compressed transport boundaries; synthetic bytes, fake decoder, no inference or network."""

import copy
import io
import json
import math
import platform
import struct
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_repair_bundles as fixtures

from cbs_transcriber import enhancement
from cbs_transcriber import enhancement_worker as worker
from cbs_transcriber import remote_audio_input as audio
from cbs_transcriber import runtime_batch_remote as controller


def params(snapshot):
    return {
        "enhancement": {"execution": "ucloud"},
        "audio_input": snapshot,
        "ucloud_api": {"batch_plan": {"identity": {"input_protocol": audio.PROTOCOL}}},
    }


@pytest.fixture
def case(tmp_path, monkeypatch):
    snapshot = fixtures.study.__wrapped__(tmp_path)[-2]
    source, pcm = b"synthetic compressed bytes", b"\0\1" * (20 * 16000)
    snapshot.update(
        format="flac", sha256=audio.digest(source), bytes=len(source), pcm_sha256=audio.digest(pcm)
    )
    chosen = params(snapshot)
    descriptor = audio.for_job(chosen)
    folder = tmp_path / "task"
    folder.mkdir()
    (folder / audio.DESCRIPTOR).write_bytes(audio.encoded(descriptor))
    (folder / audio.payload_name(descriptor)).write_bytes(source)
    (folder / "request.json").write_bytes(
        audio.encoded(
            {
                "options": {
                    "execution": "ucloud",
                    "device": "cuda",
                    "retranscribe": False,
                    "allow_downloads": False,
                    "duration": 20,
                },
                "passages": [],
            }
        )
    )
    monkeypatch.setattr(
        audio,
        "capability",
        lambda: (
            {"protocol": audio.PROTOCOL, "decoder": audio.DECODER},
            Path("synthetic-pinned-decoder"),
        ),
    )
    processes = []

    class Decoder:
        def __init__(self, *args, **kwargs):
            assert args[0][2].endswith("remote_audio_input.py")
            assert args[0][3] == "decode"
            self.stdout, self.returncode, self.killed = io.BytesIO(pcm), 0, False
            processes.append(self)

        def wait(self, **kwargs):
            return self.returncode

        def kill(self):
            self.killed = True

    monkeypatch.setattr(audio.subprocess, "Popen", Decoder)
    return chosen, descriptor, folder, pcm, processes


def test_fresh_whole_transport_is_format_and_size_aware_without_changing_old_plans(case):
    chosen, descriptor, *_ = case
    assert descriptor["source"]["format"] == "flac"
    assert descriptor["window"] == {
        "start_frame": 0,
        "end_frame": 320000,
        "timebase": audio.TIMEBASE,
    }
    for change in (
        {"segment_ids": ["one"]},
        {"segment_ids": []},
        {"segment_id": None},
        {"realign": True},
        {"retranscription": True},
        {"enhancement": {"execution": "local"}},
        {"ucloud_api": {}},
        {"audio_input": chosen["audio_input"] | {"bytes": 2000000}},
        {"audio_input": chosen["audio_input"] | {"format": "wav"}},
        {
            "audio_input": chosen["audio_input"]
            | {"source_origin": chosen["audio_input"]["source_origin"] | {"has_video": True}}
        },
    ):
        assert audio.for_job(chosen | change) is None


def test_pinned_decode_publishes_exact_pcm_and_immutable_preparation_receipt(case):
    chosen, descriptor, folder, pcm, processes = case
    value = audio.prepare(folder)
    assert value["prepared"]["pcm_sha256"] == audio.digest(pcm)
    assert value["prepared"]["frames"] == 320000
    assert value["decoder"] == audio.DECODER
    assert (folder / "audio.wav").stat().st_size == 44 + len(pcm)
    assert audio.prepare(folder) == value and len(processes) == 1
    chosen["remote_audio_preparation"] = value
    assert audio.observed_binding(folder, chosen) == {
        "input_sha256": value["prepared"]["sha256"],
        "request_sha256": value["request_sha256"],
        "audio_frames": 320000,
    }
    (folder / "audio.wav").write_bytes(b"changed")
    with pytest.raises(ValueError):
        audio.prepare(folder)
    assert len(processes) == 1


def test_pcm_disagreement_cannot_publish_or_replay_partial_decode(case):
    _chosen, descriptor, folder, _pcm, processes = case
    descriptor["pcm_sha256"] = "f" * 64
    (folder / audio.DESCRIPTOR).write_bytes(audio.encoded(descriptor))
    with pytest.raises(ValueError):
        audio.prepare(folder)
    assert (folder / "audio.preparing.wav").exists()
    assert not (folder / "audio.wav").exists() and not (folder / audio.PREPARATION).exists()
    with pytest.raises(ValueError):
        audio.prepare(folder)
    assert len(processes) == 1


def test_source_identity_and_decoder_mismatch_fail_before_decode(case, monkeypatch):
    _chosen, _descriptor, folder, _pcm, processes = case
    monkeypatch.setattr(audio, "capability", lambda: audio.failure())
    with pytest.raises(ValueError):
        audio.prepare(folder)
    assert processes == []
    monkeypatch.setattr(audio, "capability", lambda: ({}, None))
    (folder / "audio.source.flac").write_bytes(b"changed")
    with pytest.raises(ValueError):
        audio.prepare(folder)
    assert processes == []


def test_exact_inventory_and_captured_validator_are_required(case):
    _chosen, descriptor, folder, *_ = case
    names = {"request.json", audio.DESCRIPTOR, audio.payload_name(descriptor)}
    files = {
        name: {
            "size": (folder / name).stat().st_size,
            "sha256": audio.digest((folder / name).read_bytes()),
        }
        for name in names
    }
    assert audio.verify_inputs(folder, files) == descriptor
    assert controller.input_names(files) == names
    assert controller.input_limit("audio.source.flac") == audio.MAX_SOURCE
    files["audio.wav"] = {"size": 1, "sha256": "f" * 64}
    with pytest.raises(ValueError):
        audio.verify_inputs(folder, files)
    with pytest.raises(ValueError):
        controller.input_names(files)
    files.pop("audio.wav")
    (folder / "remote_audio_input.py").write_text("raise RuntimeError('must not execute')")
    with pytest.raises(controller.OwnershipConflict):
        controller.audio_transport(
            folder, {"code_hashes": {"remote_audio_input.py": "f" * 64}}, folder, files
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("frames", True),
        ("frames", 0),
        ("pcm_sha256", "bad"),
        ("decoder", {"version": "unverified", "sha256": "f" * 64}),
        ("timebase", "guess"),
    ],
)
def test_descriptor_refuses_changed_semantics(case, field, value):
    descriptor = copy.deepcopy(case[1])
    descriptor[field] = value
    with pytest.raises(ValueError):
        audio.validate_descriptor(descriptor)


def test_preparation_cannot_self_assert_another_source_or_request(case):
    chosen, descriptor, folder, *_ = case
    value = audio.prepare(folder)
    changed = copy.deepcopy(value)
    changed["source"]["sha256"] = "f" * 64
    with pytest.raises(ValueError):
        audio.validate_preparation(changed, descriptor)
    chosen["remote_audio_preparation"] = value
    (folder / "request.json").write_text(json.dumps({"different": "request"}))
    with pytest.raises(ValueError):
        audio.observed_binding(folder, chosen)


def test_producing_version_captures_remote_receipt_and_legacy_context_stays_unchanged(case):
    from cbs_transcriber.store import Store

    chosen, _descriptor, folder, *_ = case
    value = audio.prepare(folder)
    chosen["remote_audio_preparation"] = value
    parent = {"params": json.dumps(chosen), "id": "a" * 32, "kind": "enhance"}
    source = {"kind": "provider", "provider_type": "whisperx"}
    context, _ = Store._result_origin(None, chosen["audio_input"]["recording_id"], source, parent)
    assert context["remote_audio_preparation"] == value
    assert audio.report_preparation(context)["current_files"] == "not_checked"
    chosen.pop("remote_audio_preparation")
    parent["params"] = json.dumps(chosen)
    legacy, _ = Store._result_origin(None, chosen["audio_input"]["recording_id"], source, parent)
    assert "remote_audio_preparation" not in legacy and audio.report_preparation(legacy) is None
    context["remote_audio_preparation"]["prepared"]["frames"] += 1
    with pytest.raises(ValueError):
        audio.report_preparation(context)


@pytest.mark.parametrize("language", ["da", "auto"])
def test_remote_asr_metadata_uses_verified_preparation_without_downloading_wav(case, language):
    chosen, _descriptor, folder, *_ = case
    options = enhancement.EnhancementOptions(
        execution="ucloud", device="cuda", transcribe=True, language=language
    )
    configured = options.model_dump(exclude={"runtime_python", "prepare_runtime"}) | {
        "duration": 20,
        "whole_recording": False,
    }
    (folder / "request.json").write_bytes(audio.encoded({"passages": [], "options": configured}))
    value = audio.prepare(folder)
    chosen["remote_audio_preparation"] = value
    pin = worker.MODELS[worker.WHISPER_MODELS[options.whisper_model]]
    chosen["ucloud_api"]["batch_plan"]["identity"]["model_revisions"] = {
        worker.WHISPER_MODELS[options.whisper_model]: list(pin)
    }
    binding = audio.observed_binding(folder, chosen)
    output = {"segments": [{"text": "Synthetic text.", "start": 0, "end": 3}]}
    if language == "auto":
        output["language_decision"] = worker._asr_language_decision(
            binding,
            configured,
            SimpleNamespace(language="en", language_probability=0.9, duration_after_vad=3),
            1,
        )
    else:
        output["language_evidence"] = [
            worker._configured_language_evidence(
                binding,
                configured,
                worker.language_windows(binding)[0],
                SimpleNamespace(language="da"),
            )
        ]
    (folder / "transcription.json").write_bytes(audio.encoded(output))
    (folder / "audio.wav").unlink()
    metadata = enhancement.asr_language_evidence(
        folder, options, job={"params": chosen}, duration=20, include_alignment=False
    )
    if language == "auto":
        assert metadata["language_decision"]["effective"] == "en"
    else:
        assert metadata["language_evidence"][0]["origin"] == "configured"
    assert not (folder / "audio.wav").exists()


def test_worker_reports_preparation_refusal_without_starting_inference(tmp_path, monkeypatch):
    root, folder = tmp_path / "owner", tmp_path / "owner/tasks/one"
    folder.mkdir(parents=True)
    helper = root / "remote_audio_input.py"
    raw = (
        b"class PreparationError(ValueError): pass\n"
        b'def prepare(directory): raise PreparationError("synthetic refusal")\n'
    )
    helper.write_bytes(raw)
    (root / "batch.json").write_bytes(
        audio.encoded({"code_hashes": {helper.name: audio.digest(raw)}})
    )
    (folder / audio.DESCRIPTOR).write_text("{}")
    (folder / "request.json").write_bytes(audio.encoded({"options": {"allow_downloads": False}}))
    monkeypatch.setattr(worker, "__file__", str(root / "enhancement_worker.py"))
    monkeypatch.setattr(worker, "doctor", lambda: {"ready": True})
    monkeypatch.setattr(
        worker,
        "execute",
        lambda *_a, **_kw: pytest.fail("preparation refusal cannot start inference"),
    )
    assert worker.process_directory(folder) == "input_preparation"
    assert json.loads((folder / "progress.json").read_bytes())["stage"] == "preparation"
    assert json.loads((folder / "error.json").read_bytes()) == {"code": "input_preparation"}


@pytest.mark.skipif(
    sys.platform != "linux" or platform.machine() != "x86_64",
    reason="requires the pinned Linux x86-64 decoder",
)
def test_actual_pinned_linux_decoder_preserves_lossless_synthetic_samples(tmp_path):
    _, binary = audio.capability()
    pcm = b"".join(
        struct.pack("<h", int(10000 * math.sin(2 * math.pi * 440 * n / 16000)))
        if n > 3200
        else b"\0\0"
        for n in range(32000)
    )
    raw = tmp_path / "fixture.wav"
    with wave.open(str(raw), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(pcm)
    source = tmp_path / "audio.source.flac"
    subprocess.run(
        [
            str(binary),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(raw),
            "-c:a",
            "flac",
            str(source),
        ],
        check=True,
        timeout=30,
    )
    descriptor = {
        "protocol": audio.PROTOCOL,
        "source": audio.file_receipt(source, audio.MAX_SOURCE)
        | {"format": "flac", "stream": "0:a:0"},
        "source_snapshot_sha256": audio.digest(b"synthetic original proof"),
        "decoder": audio.DECODER,
        "recipe": audio.RECIPE,
        "timebase": audio.TIMEBASE,
        "frames": 32000,
        "pcm_sha256": audio.digest(pcm),
        "window": {"start_frame": 0, "end_frame": 32000, "timebase": audio.TIMEBASE},
    }
    (tmp_path / audio.DESCRIPTOR).write_bytes(audio.encoded(descriptor))
    (tmp_path / "request.json").write_bytes(
        audio.encoded({"passages": [], "options": {"duration": 2}})
    )
    value = audio.prepare(tmp_path)
    assert value["prepared"]["pcm_sha256"] == audio.digest(pcm)
    assert value["prepared"]["frames"] == 32000
    assert audio.prepare(tmp_path) == value
