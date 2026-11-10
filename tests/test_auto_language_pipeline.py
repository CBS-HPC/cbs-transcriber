"""T84c host finalization uses bounded synthetic checkpoints, never inference or services."""

import copy
import hashlib
import json
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from cbs_transcriber import enhancement, enhancement_api, providers
from cbs_transcriber import enhancement_worker as worker
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


def checkpoint(folder, options, case="da"):
    folder.mkdir(parents=True, exist_ok=True)
    with wave.open(str(folder / "audio.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 48000)
    configured = options.model_dump(exclude={"runtime_python", "prepare_runtime"}) | {
        "duration": 3.0,
        "whole_recording": False,
    }
    worker.write_json(folder / "request.json", {"passages": [], "options": configured})
    rows = (
        []
        if case in {"no_speech", "empty"}
        else [{"text": "Øresund er åbent.", "start": 0.2, "end": 2.0}]
    )
    info = SimpleNamespace(
        language="en" if case == "en" else "fr" if case == "unsupported" else "da",
        language_probability=0.5 if case == "uncertain" else 0.95,
        duration_after_vad=0.0 if case == "no_speech" else 2.0,
    )
    decision = worker._asr_language_decision(
        worker.language_binding(folder), configured, info, len(rows)
    )
    worker.write_json(
        folder / "transcription.json", {"segments": rows, "language_decision": decision}
    )
    outcome = {
        "schema": 1,
        "state": "not_requested"
        if not options.align
        else "aligned"
        if decision["status"] == "resolved"
        else "unavailable_language",
        "decision_sha256": worker.decision_digest(decision),
    }
    if options.align:
        aligned = outcome["state"] == "aligned"
        worker.write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": str(index),
                        "words": [
                            {
                                "word": token,
                                "start": 0.3 + word * 0.3 if aligned else None,
                                "end": 0.5 + word * 0.3 if aligned else None,
                                "score": 0.99 if aligned else None,
                            }
                            for word, token in enumerate(row["text"].split())
                        ],
                    }
                    for index, row in enumerate(rows)
                ],
                "language_outcome": outcome,
            },
        )
    if options.diarize:
        worker.write_json(
            folder / "diarization.json",
            {"turns": [{"start": 0.0, "end": 3.0, "speaker": "A"}]},
        )
    return {"language_decision": decision, "alignment_outcome": outcome}


def library(tmp_path, options, *, api=False):
    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "synthetic.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET duration=3,status='ready' WHERE id=?", (rid,))
    reviewed = store.add_result(
        rid,
        normalize_segments([{"text": "Reviewed wording stays.", "start": 0, "end": 3}]),
        {},
        "Reviewed",
        {"kind": "import"},
    )
    params = {"enhancement": options.model_dump(), "transcription": True}
    if api:
        params |= {
            "ucloud_api": {
                "provider_id": "synthetic",
                "runtime_id": "synthetic",
                "settings": {"base_url": "https://synthetic.invalid/v1"},
            },
            "ucloud_run": {"job_id": "synthetic-owned-allocation"},
        }
    job = store.add_job(rid, "enhance", params)
    store.update_job(job["id"], state="running")
    queue = SimpleNamespace(
        store=store,
        cancel_event=threading.Event(),
        credentials=SimpleNamespace(get=lambda _: ""),
    )
    return SimpleNamespace(
        store=store,
        project=project,
        recording=store.recording(rid),
        reviewed=reviewed,
        job=store.job(job["id"]),
        queue=queue,
        folder=store.directory(rid) / "enhancements" / job["id"],
        options=options,
    )


def refuse_runtime(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail(
            "Complete Auto checkpoints must not inspect inference, prepare media or load pins"
        )

    class NoPins(dict):
        def __getitem__(self, key):
            refuse()

    monkeypatch.setattr(enhancement, "require_local_runtime", refuse)
    monkeypatch.setattr(enhancement, "detected_device", refuse)
    monkeypatch.setattr(enhancement, "run_worker", refuse)
    monkeypatch.setattr(enhancement.media, "ffmpeg", refuse)
    monkeypatch.setattr(worker.importlib.metadata, "version", refuse)
    monkeypatch.setattr(worker, "MODELS", NoPins())
    monkeypatch.setattr(enhancement, "MODELS", NoPins())


def produced(env):
    return env.store.result(
        env.recording["id"],
        next(
            item["id"]
            for item in env.store.results(env.recording["id"])
            if item["id"] != env.reviewed["id"]
        ),
    )


@pytest.mark.parametrize("case", ["da", "en", "uncertain", "unsupported", "no_speech", "empty"])
@pytest.mark.parametrize("diarize", [False, True])
def test_v124_cached_host_results_preserve_text_replay_review_and_provenance(
    tmp_path, monkeypatch, case, diarize
):
    options = enhancement.EnhancementOptions(
        language="auto", transcribe=True, whisper_model="small", diarize=diarize
    )
    env = library(tmp_path, options)
    metadata = checkpoint(env.folder, options, case)
    frozen = copy.deepcopy(env.job["params"])
    before_request = (env.folder / "request.json").read_bytes()
    old_version = env.store.result_versions(env.recording["id"], env.reviewed["id"])
    refuse_runtime(monkeypatch)
    enhancement.run_enhancement(env.queue, env.job, env.recording)
    result = produced(env)
    source = result["source"]
    assert source["language_decision"] == metadata["language_decision"]
    assert source["alignment_outcome"] == metadata["alignment_outcome"]
    assert source["language"] == source["options"]["language"] == "auto"
    assert source["model_revisions"] == {
        "coarse": {key: metadata["language_decision"]["model"][key] for key in ("repo", "revision")}
    }
    aligned = case in {"da", "en"}
    assert source["capability"] == (
        ("words_speakers" if diarize else "words")
        if aligned
        else ("speakers" if diarize else "segments")
    )
    if case in {"no_speech", "empty"}:
        assert result["segments"] == []
        assert ("no speech detected" if case == "no_speech" else "no ASR text") in result["label"]
    else:
        [passage] = result["segments"]
        assert passage["text"] == passage["original_text"] == "Øresund er åbent."
        if aligned and diarize:
            assert 0.2 <= passage["start"] < passage["end"] <= 2.0
            assert (passage["start"], passage["end"]) == (
                passage["words"][0]["start"],
                passage["words"][-1]["end"],
            )
        else:
            assert (passage["start"], passage["end"]) == (0.2, 2.0)
        assert passage["word_timing"] is aligned
        if not aligned:
            assert all(word["start"] is None and word["end"] is None for word in passage["words"])
            assert "word timing unavailable" in result["label"]
        if diarize:
            assert passage["speaker"] != "UNKNOWN"
    version = env.store.result_versions(env.recording["id"], result["id"])[0]
    assert version["context"]["processing"]["language_decision"] == metadata["language_decision"]
    assert version["context"]["processing"]["alignment_outcome"] == metadata["alignment_outcome"]
    payload = env.store.result_version(env.recording["id"], result["id"], version["id"])["payload"]
    assert payload["segments"] == result["segments"]
    assert env.store.recording(env.recording["id"])["active_transcript_id"] == env.reviewed["id"]
    assert (
        env.store.result(env.recording["id"], env.reviewed["id"])["segments"]
        == env.reviewed["segments"]
    )
    assert env.store.result_versions(env.recording["id"], env.reviewed["id"]) == old_version
    assert env.store.job(env.job["id"])["params"] == frozen
    assert (env.folder / "request.json").read_bytes() == before_request
    assert env.store.job(env.job["id"])["state"] == "completed"


def test_v124_not_requested_alignment_has_speaker_capability_without_claiming_words(
    tmp_path, monkeypatch
):
    options = enhancement.EnhancementOptions(
        language="auto", transcribe=True, whisper_model="small", align=False, diarize=True
    )
    env = library(tmp_path, options)
    checkpoint(env.folder, options)
    refuse_runtime(monkeypatch)
    enhancement.run_enhancement(env.queue, env.job, env.recording)
    result = produced(env)
    assert result["source"]["capability"] == "speakers"
    assert result["source"]["alignment_outcome"]["state"] == "not_requested"
    assert "word timing not requested" in result["label"]
    assert not (env.folder / "alignment.json").exists()
    assert not result["segments"][0]["word_timing"]


def test_v124_frozen_pins_are_used_only_for_actual_requested_dependencies(tmp_path, monkeypatch):
    options = enhancement.EnhancementOptions(
        language="auto", transcribe=True, whisper_model="small", device="cpu", diarize=True
    )
    captured = checkpoint(tmp_path, options, "en")
    asr = captured["language_decision"]["model"]
    revisions = {
        "coarse": [asr["repo"], asr["revision"]],
        "whisper/small": [asr["repo"], asr["revision"]],
        "da": ["historical/danish-aligner", "a" * 40],
        "en": ["historical/english-aligner", "b" * 40],
        "speakers": ["historical/speaker-model", "c" * 40],
    }
    job = {"params": {"ucloud_api": {"batch_plan": {"identity": {"model_revisions": revisions}}}}}
    refuse_runtime(monkeypatch)
    metadata = enhancement.asr_language_evidence(tmp_path, options, job=job, duration=3.0)
    pins = enhancement.automatic_model_revisions(job, options, metadata)
    assert set(pins) == {"coarse", "en", "speakers"}
    assert pins["en"] == {"repo": revisions["en"][0], "revision": "b" * 40}
    assert pins["speakers"]["revision"] == "c" * 40
    revisions["coarse"][1] = "d" * 40
    with pytest.raises(ValueError, match="inconsistent"):
        enhancement.asr_language_evidence(tmp_path, options, job=job, duration=3.0)


@pytest.mark.parametrize(
    "damage",
    [
        "input",
        "request",
        "model",
        "missing_decision",
        "both",
        "outcome",
        "missing_outcome",
        "words",
        "oversized",
    ],
)
def test_v124_invalid_checkpoint_is_refused_before_old_api_cleanup_or_result_commit(
    tmp_path, monkeypatch, damage
):
    options = enhancement.EnhancementOptions(
        execution="ucloud", device="cuda", language="auto", transcribe=True, whisper_model="small"
    )
    env = library(tmp_path, options, api=True)
    checkpoint(env.folder, options)
    stage = json.loads((env.folder / "transcription.json").read_bytes())
    alignment = json.loads((env.folder / "alignment.json").read_bytes())
    if damage in {"input", "request"}:
        stage["language_decision"][damage + "_sha256"] = "0" * 64
    elif damage == "model":
        stage["language_decision"]["model"]["choice"] = "medium"
    elif damage == "missing_decision":
        stage.pop("language_decision")
    elif damage == "both":
        stage["language_evidence"] = []
    elif damage == "outcome":
        alignment["language_outcome"]["decision_sha256"] = "0" * 64
    elif damage == "missing_outcome":
        alignment.pop("language_outcome")
    elif damage == "words":
        alignment["passages"][0]["words"][0]["word"] = "Changed"
    worker.write_json(env.folder / "transcription.json", stage)
    worker.write_json(env.folder / "alignment.json", alignment)
    if damage == "oversized":
        path = env.folder / "alignment.json"
        path.write_bytes(b" " * 4097)
        monkeypatch.setattr(worker, "AUTO_JSON_LIMIT", 4096)
        open_path = Path.open

        def bounded(path_to_open, *args, **kwargs):
            if path_to_open == path:
                pytest.fail("Oversized alignment must be refused before opening its contents")
            return open_path(path_to_open, *args, **kwargs)

        monkeypatch.setattr(Path, "open", bounded)
    cleaned = []
    monkeypatch.setattr(providers, "require_approval", lambda *a, **kw: None)
    monkeypatch.setattr(enhancement_api, "settle", lambda *a: cleaned.append(True))
    refuse_runtime(monkeypatch)
    with pytest.raises(ValueError):
        enhancement.run_enhancement(env.queue, env.job, env.recording)
    assert cleaned == []
    assert len(env.store.results(env.recording["id"])) == 1
    assert env.store.recording(env.recording["id"])["active_transcript_id"] == env.reviewed["id"]


def test_v124_callbacks_reuse_one_validated_snapshot_before_cleanup_and_retry_revalidates(
    tmp_path, monkeypatch
):
    options = enhancement.EnhancementOptions(
        execution="ucloud", device="cuda", language="auto", transcribe=True, whisper_model="small"
    )
    env = library(tmp_path, options, api=True)
    expected = checkpoint(env.folder, options)
    calls, cleaned = [], []
    read_metadata = worker.auto_transcription_metadata

    def observe(*args, **kwargs):
        calls.append("validate")
        return read_metadata(*args, **kwargs)

    def cleanup(*args, validate=None):
        # Real settle validates the local outputs (memoized) before any remote cleanup.
        if validate is not None:
            validate()
        assert calls == ["validate"]
        cleaned.append(True)
        # A settled callback must not reread a changed artifact into the validated result.
        stage = json.loads((env.folder / "transcription.json").read_bytes())
        stage["segments"][0]["text"] = "Changed after validation"
        stage["language_decision"]["input_sha256"] = "0" * 64
        worker.write_json(env.folder / "transcription.json", stage)

    monkeypatch.setattr(worker, "auto_transcription_metadata", observe)
    monkeypatch.setattr(providers, "require_approval", lambda *a, **kw: None)
    monkeypatch.setattr(enhancement_api, "settle", cleanup)
    refuse_runtime(monkeypatch)
    enhancement.run_enhancement(env.queue, env.job, env.recording)
    result = produced(env)
    assert calls == ["validate"] and cleaned == [True]
    assert result["segments"][0]["text"] == "Øresund er åbent."
    assert result["source"]["language_decision"] == expected["language_decision"]
    with pytest.raises(ValueError):
        enhancement.run_enhancement(env.queue, env.job, env.recording)
    assert calls == ["validate", "validate"] and cleaned == [True]
    assert len(env.store.results(env.recording["id"])) == 2


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        (None, "dce6528b0eb32ce167813aa63937621861799fb5e6f241dfbe2c140dc70d6c66"),
        ("da", "dce6528b0eb32ce167813aa63937621861799fb5e6f241dfbe2c140dc70d6c66"),
        ("en", "0cf9bf0b39193f1e039bea15a28b1c41da4974bdabf5af709267b6454b492cf7"),
    ],
)
def test_v124_fixed_and_omitted_language_request_bytes_match_prechange_golden(
    tmp_path, language, expected
):
    options = enhancement.EnhancementOptions(
        device="cpu",
        transcribe=True,
        whisper_model="small",
        **({"language": language} if language else {}),
    )
    request = {
        "passages": [],
        "options": options.model_dump(exclude={"runtime_python", "prepare_runtime"})
        | {"duration": 3.0, "whole_recording": False},
    }
    worker.write_json(tmp_path / "request.json", request)
    assert hashlib.sha256((tmp_path / "request.json").read_bytes()).hexdigest() == expected


def test_v124_existing_source_auto_refused_before_interpreter_check(monkeypatch):
    def refuse(*args):
        pytest.fail("Existing-source Auto must be refused before interpreter checks")

    monkeypatch.setattr(enhancement, "python_path", refuse)
    options = enhancement.EnhancementOptions(language="auto", transcribe=True)
    with pytest.raises(ValueError, match="existing transcript text"):
        enhancement.validate_input({}, options, 3.0)


@pytest.mark.parametrize(
    "markers",
    [
        {},
        {"transcription": False},
        {"transcription": 1},
        {"transcription": "yes"},
        {"transcription": True, "retranscription": True},
        {"transcription": True, "realign": True},
        {"transcription": True, "segment_ids": ["0"]},
        {"transcription": True, "timing_units": []},
    ],
)
def test_v124_unmarked_or_conflicting_auto_job_refused_before_device_runtime(monkeypatch, markers):
    refuse_runtime(monkeypatch)
    queue = SimpleNamespace(cancel_event=threading.Event())
    job = {
        "params": {
            "enhancement": enhancement.EnhancementOptions(
                language="auto", transcribe=True
            ).model_dump(),
            **markers,
        }
    }
    with pytest.raises(ValueError, match="full-ASR transcription job"):
        enhancement.run_enhancement(queue, job, {})
