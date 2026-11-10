"""Automatic-language file protocol contracts without inference, providers or weights."""

import copy
import hashlib
import importlib.util
import marshal
import struct
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_runtime_batch_remote import Harness, read

from cbs_transcriber import enhancement_worker as worker
from cbs_transcriber import runtime_batch_remote as remote


def request():
    return {
        "options": {
            "execution": "ucloud",
            "device": "cuda",
            "language": "auto",
            "align": True,
            "diarize": False,
            "allow_downloads": False,
            "duration": 3,
            "timeout_minutes": 10,
            "transcribe": True,
            "retranscribe": False,
            "whisper_model": "small",
            "whole_recording": False,
        },
        "passages": [],
    }


def audio(path, value):
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(value.to_bytes(2, "little") * (3 * 16000))


class AutoHarness(Harness):
    def __init__(self, directory):
        super().__init__(directory)
        source = Path(worker.__file__).read_bytes().replace(b"\r\n", b"\n")
        (directory / "enhancement_worker.py").write_bytes(source)
        self.manifest["code_hashes"]["enhancement_worker.py"] = hashlib.sha256(source).hexdigest()
        remote.write_json(directory / "batch.json", self.manifest)
        self.controller = self.engine()

    def publish(self, row, *, frozen=None, audio_value=1):
        super().publish(row, request=frozen or request())
        audio(self.folder(row) / "audio.wav", audio_value)
        self.refresh_inputs(row)

    def refresh_inputs(self, row, carried=()):
        folder = self.folder(row)
        inputs = read(folder / "inputs.json")
        inputs["files"] = {
            name: remote.file_receipt(folder / name, remote.OUTPUT_LIMIT)
            for name in ("request.json", "audio.wav", *carried)
        }
        remote.write_json(folder / "inputs.json", inputs)
        self.ready(row)

    def checkpoint(self, row, *, language="da", probability=0.99, empty=False):
        folder = self.folder(row)
        options = read(folder / "request.json")["options"]
        rows = [] if empty else [{"text": "Hej verden", "start": 0.1, "end": 2.4}]
        decision = worker._asr_language_decision(
            worker.language_binding(folder),
            options,
            SimpleNamespace(
                language=language,
                language_probability=probability,
                duration_after_vad=0 if empty and probability == 1 else 2.4,
            ),
            len(rows),
        )
        remote.write_json(
            folder / "transcription.json", {"segments": rows, "language_decision": decision}
        )
        if options["align"]:
            alignment = worker._untimed_alignment(worker._asr_passages(rows), decision)
            if decision["status"] == "resolved":
                alignment["language_outcome"] = worker._language_outcome(decision, True)
                alignment["passages"] = [
                    {
                        "id": "0",
                        "words": [
                            {"word": "Hej", "start": 0.1, "end": 0.8, "score": 0.9},
                            {"word": "verden", "start": 0.9, "end": 2.4, "score": 0.9},
                        ],
                    }
                ]
            remote.write_json(folder / "alignment.json", alignment)
        if options["diarize"]:
            remote.write_json(folder / "diarization.json", {"turns": []})
        return decision

    def complete(self, row, code=0):
        self.children[-1].returncode = code
        self.controller.step()
        return read(self.folder(row) / "outcome.json")


@pytest.fixture
def batch(tmp_path):
    return AutoHarness(tmp_path)


@pytest.mark.parametrize(
    ("language", "probability", "empty", "status"),
    [
        ("da", 0.99, False, "resolved"),
        ("en", 0.99, False, "resolved"),
        ("fr", 0.99, False, "unsupported"),
        ("da", 0.2, False, "uncertain"),
        ("da", 0.2, True, "uncertain"),
        ("da", 1, True, "no_speech"),
    ],
)
def test_auto_complete_preserves_bound_resolved_unavailable_and_empty_results(
    batch, language, probability, empty, status
):
    row = batch.reserve()
    batch.publish(row)
    assert batch.controller.step()["phase"] == "running"
    decision = batch.checkpoint(row, language=language, probability=probability, empty=empty)
    assert decision["status"] == status
    outcome = batch.complete(row)
    assert outcome["state"] == "complete"
    assert set(outcome["outputs"]) == {"transcription.json", "alignment.json"}
    assert read(batch.folder(row) / "transcription.json")["language_decision"] == decision


def test_members_resolve_different_languages_in_one_requested_auto_owner(batch):
    first, second = batch.reserve(), batch.reserve()
    batch.publish(first, audio_value=1)
    batch.publish(second, audio_value=2)
    batch.controller.step()
    one = batch.checkpoint(first, language="da")
    assert batch.complete(first)["state"] == "complete"
    two = batch.checkpoint(second, language="en")
    assert batch.complete(second)["state"] == "complete"
    assert one["effective"] == "da" and two["effective"] == "en"
    assert one["input_sha256"] != two["input_sha256"]
    assert batch.started == [first["task_id"], second["task_id"]]


def test_other_members_decision_cannot_be_copied_to_current_member(batch):
    first, second = batch.reserve(), batch.reserve()
    batch.publish(first, audio_value=1)
    batch.publish(second, audio_value=2)
    batch.controller.step()
    first_decision = batch.checkpoint(first)
    assert batch.complete(first)["state"] == "complete"
    batch.checkpoint(second)
    path = batch.folder(second) / "transcription.json"
    saved = read(path)
    saved["language_decision"] = first_decision
    remote.write_json(path, saved)
    assert batch.complete(second)["outputs"] == {}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("transcribe", False),
        ("transcribe", 1),
        ("transcribe", None),
        ("retranscribe", True),
        ("retranscribe", None),
        ("align", 1),
        ("diarize", None),
        ("whole_recording", True),
        ("whole_recording", None),
        ("whisper_model", "not-a-model"),
        ("whisper_model", None),
        ("duration", 6 * 3600 + 1),
    ],
)
def test_auto_admission_rejects_nonliteral_or_unsupported_operation(batch, key, value):
    frozen = request()
    if value is None:
        del frozen["options"][key]
    else:
        frozen["options"][key] = value
    row = batch.reserve()
    batch.publish(row, frozen=frozen)
    batch.controller.step()
    assert batch.started == []
    assert not (batch.folder(row) / "accepted.json").exists()


def test_auto_admission_refuses_source_passages(batch):
    frozen = request()
    frozen["passages"] = [{"id": "0", "text": "Source", "start": 0, "end": 1}]
    row = batch.reserve()
    batch.publish(row, frozen=frozen)
    batch.controller.step()
    assert batch.started == []
    assert not (batch.folder(row) / "accepted.json").exists()


def test_wrong_frozen_source_sha_never_executes_top_level_code(batch):
    marker = batch.directory / "must-not-exist"
    source = batch.directory / "enhancement_worker.py"
    source.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", "utf-8")
    row = batch.reserve()
    batch.publish(row)
    assert batch.controller.step()["phase"] == "failed"
    assert batch.started == []
    assert not marker.exists()


def test_cached_validator_rechecks_frozen_source_before_reuse(batch):
    row = batch.reserve()
    batch.publish(row)
    batch.controller.step()
    assert batch.controller._auto_module is not None
    batch.checkpoint(row)
    source = batch.directory / "enhancement_worker.py"
    source.write_bytes(source.read_bytes() + b"\nraise RuntimeError('must not execute')\n")
    with pytest.raises(remote.OwnershipConflict, match="source hash mismatch"):
        batch.controller._outputs(row["task_id"])
    assert (batch.folder(row) / "transcription.json").exists()


def test_hash_valid_old_worker_helper_cannot_accept_auto_before_work(batch):
    source = (
        "MODELS = {'small': ('example/small', '" + "a" * 40 + "')}\n"
        "WHISPER_MODELS = {'small': 'small'}\n"
        "def auto_transcription_metadata(directory, options, *, model=None, "
        "require_alignment=False):\n    return {}\n"
    ).encode()
    (batch.directory / "enhancement_worker.py").write_bytes(source)
    batch.manifest["code_hashes"]["enhancement_worker.py"] = hashlib.sha256(source).hexdigest()
    remote.write_json(batch.directory / "batch.json", batch.manifest)
    batch.controller = batch.engine()
    row = batch.reserve()
    batch.publish(row)
    batch.controller.step()
    assert batch.started == []
    assert not (batch.folder(row) / "accepted.json").exists()


def test_verified_source_ignores_poisoned_bytecode_cache(batch):
    source = batch.directory / "enhancement_worker.py"
    marker = batch.directory / "bytecode-must-not-execute"
    code = compile(f"from pathlib import Path\nPath({str(marker)!r}).touch()", str(source), "exec")
    cache = Path(importlib.util.cache_from_source(str(source)))
    cache.parent.mkdir()
    cache.write_bytes(
        importlib.util.MAGIC_NUMBER
        + struct.pack("<III", 0, int(source.stat().st_mtime), source.stat().st_size)
        + marshal.dumps(code)
    )
    row = batch.reserve()
    batch.publish(row)
    assert batch.controller.step()["phase"] == "running"
    assert not marker.exists()
    batch.checkpoint(row)
    assert batch.complete(row)["state"] == "complete"


def test_fresh_auto_admission_refuses_undeclared_completed_stage(batch):
    row = batch.reserve()
    batch.publish(row)
    batch.checkpoint(row)
    batch.controller.step()
    assert batch.started == []
    assert not (batch.folder(row) / "accepted.json").exists()
    assert (batch.folder(row) / "transcription.json").exists()


@pytest.mark.parametrize("damage", ["request", "audio", "model", "copied_decision"])
def test_auto_outputs_bind_frozen_request_audio_model_and_per_file_decision(batch, damage):
    row = batch.reserve()
    batch.publish(row)
    batch.controller.step()
    batch.checkpoint(row)
    folder = batch.folder(row)
    if damage == "request":
        frozen = read(folder / "request.json")
        frozen["options"]["language"] = "da"  # Must not downgrade out of Auto validation.
        remote.write_json(folder / "request.json", frozen)
    elif damage == "audio":
        audio(folder / "audio.wav", 7)
        batch.checkpoint(row)  # Even a freshly rebound decision cannot change accepted inputs.
    else:
        saved = read(folder / "transcription.json")
        if damage == "model":
            saved["language_decision"]["model"]["revision"] = "a" * 40
        else:
            saved["language_decision"]["input_sha256"] = "a" * 64
        remote.write_json(folder / "transcription.json", saved)
    outcome = batch.complete(row)
    assert outcome["state"] == "failed" and outcome["error_code"] == "invalid_output"
    assert outcome["outputs"] == {}
    assert (folder / "transcription.json").exists()


@pytest.mark.parametrize(
    "damage", ["missing", "outcome", "token", "numeric_bounds", "nulls", "passage_id"]
)
def test_bad_alignment_retains_asr_and_independent_speakers(batch, damage):
    frozen = request()
    frozen["options"]["diarize"] = True
    row = batch.reserve()
    batch.publish(row, frozen=frozen)
    batch.controller.step()
    batch.checkpoint(row, language="fr" if damage == "nulls" else "da")
    path = batch.folder(row) / "alignment.json"
    alignment = read(path)
    if damage == "missing":
        path.unlink()
    else:
        if damage == "outcome":
            alignment["language_outcome"]["decision_sha256"] = "a" * 64
        elif damage == "token":
            alignment["passages"][0]["words"][0]["word"] = "Changed"
        elif damage == "numeric_bounds":
            alignment["passages"][0]["words"][0]["start"] = 10
        elif damage == "nulls":
            alignment["passages"][0]["words"][0]["start"] = 0
        else:
            alignment["passages"][0]["id"] = "other"
        remote.write_json(path, alignment)
    outcome = batch.complete(row)
    assert outcome["state"] == "failed" and outcome["error_code"] == "invalid_output"
    assert set(outcome["outputs"]) == {"transcription.json", "diarization.json"}


def test_explicit_retry_carries_verified_asr_after_bad_alignment_without_replay(batch):
    first = batch.reserve()
    batch.publish(first)
    batch.controller.step()
    batch.checkpoint(first)
    path = batch.folder(first) / "alignment.json"
    path.write_bytes(b'{"passages":[]}')
    failed = batch.complete(first)
    assert set(failed["outputs"]) == {"transcription.json"}
    old_outcome = (batch.folder(first) / "outcome.json").read_bytes()
    retry = batch.reserve(job_id=first["job_id"], attempt=2, ordinal=first["ordinal"])
    batch.publish(retry)
    (batch.folder(retry) / "transcription.json").write_bytes(
        (batch.folder(first) / "transcription.json").read_bytes()
    )
    batch.refresh_inputs(retry, ("transcription.json",))
    assert batch.controller.step()["phase"] == "running"
    assert batch.started == [first["task_id"], retry["task_id"]]
    batch.checkpoint(retry)
    assert batch.complete(retry)["state"] == "complete"
    assert (batch.folder(first) / "outcome.json").read_bytes() == old_outcome


@pytest.mark.parametrize("damage", ["asr", "alignment", "diarization"])
def test_carried_auto_stage_requires_semantics_even_when_previous_receipt_matches(batch, damage):
    frozen = request()
    frozen["options"]["diarize"] = True
    first = batch.reserve()
    batch.publish(first, frozen=frozen)
    batch.controller.step()
    batch.checkpoint(first)
    names = ["transcription.json"]
    if damage == "asr":
        path = batch.folder(first) / "transcription.json"
        saved = read(path)
        saved["language_decision"]["input_sha256"] = "a" * 64
    elif damage == "alignment":
        names.append("alignment.json")
        path = batch.folder(first) / "alignment.json"
        saved = read(path)
        saved["passages"][0]["words"][0]["word"] = "Copied"
    else:
        names.append("diarization.json")
        path = batch.folder(first) / "diarization.json"
        saved = {"turns": [{"start": -1, "end": 1, "speaker": "SPEAKER_00"}]}
    remote.write_json(path, saved)
    # A hypothetical old controller recorded this syntactically valid, semantically bad stage.
    batch.children[-1].returncode = 1
    batch.controller.child.wait()
    batch.controller.child = batch.controller.running_task_id = None
    batch.controller._outcome(
        first,
        "failed",
        "processing",
        outputs={
            name: remote.file_receipt(batch.folder(first) / name, remote.OUTPUT_LIMIT)
            for name in names
        },
    )
    retry = batch.reserve(job_id=first["job_id"], attempt=2, ordinal=first["ordinal"])
    batch.publish(retry, frozen=frozen)
    for name in names:
        remote.write_json(
            batch.folder(retry) / name, copy.deepcopy(read(batch.folder(first) / name))
        )
    batch.refresh_inputs(retry, names)
    batch.controller.step()
    assert batch.started == [first["task_id"]]
    assert not (batch.folder(retry) / "accepted.json").exists()


def test_accepted_restart_retains_valid_auto_checkpoint_without_inference_replay(batch):
    row = batch.reserve()
    batch.publish(row)
    batch.controller.step()
    batch.checkpoint(row, empty=True, probability=1)
    (batch.folder(row) / "alignment.json").unlink()
    batch.children[-1].returncode = -9
    batch.children[-1].wait()
    batch.controller = batch.engine()
    batch.controller.step()
    outcome = read(batch.folder(row) / "outcome.json")
    assert outcome["state"] == "interrupted"
    assert set(outcome["outputs"]) == {"transcription.json"}
    assert batch.started == [row["task_id"]]


def test_auto_speakers_without_alignment_need_no_alignment_artifact(batch):
    frozen = request()
    frozen["options"].update(align=False, diarize=True)
    row = batch.reserve()
    batch.publish(row, frozen=frozen)
    batch.controller.step()
    batch.checkpoint(row, language="fr")
    assert not (batch.folder(row) / "alignment.json").exists()
    outcome = batch.complete(row)
    assert outcome["state"] == "complete"
    assert set(outcome["outputs"]) == {"transcription.json", "diarization.json"}


@pytest.mark.parametrize(
    "turn",
    [
        {"start": -1, "end": 1, "speaker": "SPEAKER_00"},
        {"start": 0, "end": 10, "speaker": "SPEAKER_00"},
        {"start": 1, "end": 1, "speaker": "SPEAKER_00"},
        {"start": False, "end": 1, "speaker": "SPEAKER_00"},
        {"start": 0, "end": 1, "speaker": ""},
        {"start": 0, "end": 1, "speaker": "x" * 101},
        {"start": 0, "end": 1, "speaker": None},
        {"start": 0, "end": 10**400, "speaker": "SPEAKER_00"},
        {"start": 10**400, "end": 1, "speaker": "SPEAKER_00"},
        {"start": -(10**400), "end": 1, "speaker": "SPEAKER_00"},
        {"start": 0, "end": -(10**400), "speaker": "SPEAKER_00"},
    ],
)
def test_bad_diarization_retains_independently_valid_asr_and_alignment(batch, turn):
    frozen = request()
    frozen["options"]["diarize"] = True
    row = batch.reserve()
    batch.publish(row, frozen=frozen)
    batch.controller.step()
    batch.checkpoint(row)
    remote.write_json(batch.folder(row) / "diarization.json", {"turns": [turn]})
    outcome = batch.complete(row)
    assert outcome["state"] == "failed" and outcome["error_code"] == "invalid_output"
    assert set(outcome["outputs"]) == {"transcription.json", "alignment.json"}


def test_valid_diarization_with_boundary_tolerance_is_complete(batch):
    frozen = request()
    frozen["options"]["diarize"] = True
    row = batch.reserve()
    batch.publish(row, frozen=frozen)
    batch.controller.step()
    batch.checkpoint(row)
    remote.write_json(
        batch.folder(row) / "diarization.json",
        {"turns": [{"start": 0, "end": 3.1, "speaker": "SPEAKER_00"}]},
    )
    assert batch.complete(row)["state"] == "complete"
