"""Hidden bounded alignment detects only a prepared clip; no ASR or real inference."""

import copy
import hashlib
import json
import sys
import wave
from types import SimpleNamespace

import pytest

from cbs_transcriber import enhancement_worker as worker


def write_wave(path, frames, value=1000):
    with wave.open(str(path), "wb") as source:
        source.setnchannels(1)
        source.setsampwidth(2)
        source.setframerate(16000)
        source.writeframes(value.to_bytes(2, "little", signed=True) * frames)


@pytest.fixture
def clip(tmp_path, monkeypatch):
    options = {
        "execution": "local",
        "language": "auto",
        "device": "cpu",
        "align": True,
        "transcribe": False,
        "retranscribe": False,
        "diarize": False,
        "whole_recording": False,
        "allow_downloads": False,
        "download_models": False,
        "duration": 3.0,
    }
    request = {
        "operation": "bounded_alignment",
        "options": options,
        "passages": [
            {"id": "private-unit", "text": " Øresund er åbent.\n", "start": 0.0, "end": 3.0}
        ],
    }
    env = SimpleNamespace(
        folder=tmp_path,
        request=request,
        options=options,
        calls=[],
        progress=[],
        language="da",
        probability=0.95,
        multilingual=True,
        gpu=False,
        fail_alignment=False,
        fail_punkt=False,
        geometry={
            "sampling_rate": 16000,
            "n_samples": 480000,
            "nb_max_frames": 3000,
            "hop_length": 160,
        },
    )
    write_wave(tmp_path / "audio.wav", 48000)
    worker.write_json(tmp_path / "request.json", request)
    cache = tmp_path / "cached-model"
    cache.mkdir()
    (cache / "tokenizer.json").write_text("{}", encoding="utf-8")
    env.cache = cache

    class Detector:
        def __init__(self, location, **kwargs):
            env.calls.append(("detector", location, kwargs))
            assert kwargs["local_files_only"] is True
            self.model = SimpleNamespace(is_multilingual=env.multilingual)
            self.feature_extractor = SimpleNamespace(**env.geometry)

        def detect_language(self, **kwargs):
            audio = kwargs.pop("audio")
            assert audio.flags.writeable is False
            env.calls.append(
                (
                    "detect",
                    len(audio),
                    hashlib.sha256(memoryview(audio).cast("B")).hexdigest(),
                    kwargs,
                )
            )
            return env.language, env.probability, [(env.language, env.probability)]

        def transcribe(self, *args, **kwargs):
            pytest.fail("Bounded language detection must never invoke ASR")

    def snapshot(repo, **kwargs):
        env.calls.append(("snapshot", repo, kwargs))
        assert kwargs["local_files_only"] is True
        return str(cache)

    def punkt(name):
        env.calls.append(("punkt", name))
        if env.fail_punkt:
            raise LookupError("synthetic missing tokenizer")

    def load_align(language, device, **kwargs):
        env.calls.append(("align_model", language, device, kwargs))
        return language, {"language": language}

    def align(passages, model, metadata, audio, device, **kwargs):
        env.calls.append(("align", model, device, kwargs))
        if env.fail_alignment:
            raise RuntimeError("synthetic interrupted alignment")
        passage = passages[0]
        tokens = passage["text"].split()
        step = (passage["end"] - passage["start"]) / (len(tokens) + 1)
        return {
            "word_segments": [
                {
                    "word": token,
                    "start": step * (index + 0.1),
                    "end": step * (index + 0.7),
                    "score": 0.9,
                }
                for index, token in enumerate(tokens)
            ]
        }

    for name, module in {
        "torch": SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: env.gpu, empty_cache=lambda: env.calls.append(("empty_cache",))
            )
        ),
        "whisperx": SimpleNamespace(load_align_model=load_align, align=align),
        "faster_whisper": SimpleNamespace(WhisperModel=Detector),
        "huggingface_hub": SimpleNamespace(snapshot_download=snapshot),
        "nltk": SimpleNamespace(
            data=SimpleNamespace(load=punkt),
            download=lambda *a, **k: pytest.fail("implicit tokenizer download"),
        ),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(worker.importlib.metadata, "version", lambda *a: "1.2.1")
    monkeypatch.setattr(worker, "ALIGNMENT_CACHE", None)
    monkeypatch.setattr(worker, "CACHE_ALIGNMENT", False)
    real_write = worker.write_json

    def capture_progress(path, value):
        # These semantic tests finish inference instantly. Capture only volatile progress
        # events to avoid exercising incidental Windows replacement races in a tight loop.
        # Requests, decisions, alignments, errors and every other file use the real writer;
        # its physical I/O/error contract is covered by the separate real-reader regressions.
        if path.name == "progress.json":
            env.progress.append(copy.deepcopy(value))
        else:
            real_write(path, value)

    monkeypatch.setattr(worker, "write_json", capture_progress)
    return env


def decision(env):
    return json.loads((env.folder / "alignment_language.json").read_bytes())


def freeze(env):
    worker.write_json(env.folder / "request.json", env.request)


@pytest.mark.parametrize("language", ["da", "en"])
@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_bounded_alignment_retains_exact_scope_without_asr_and_routes_copied_language(
    clip, language, device
):
    env = clip
    env.language = language
    env.options["device"] = device
    env.gpu = device == "cuda"
    freeze(env)
    frozen = (env.folder / "request.json").read_bytes()
    original = copy.deepcopy(env.request)
    assert worker.execute(env.request, env.folder) is None
    receipt = decision(env)
    assert receipt["requested"] == "auto" and receipt["effective"] == language
    assert receipt["scope"] == "bounded_prepared_input" and receipt["origin"] == "audio_detection"
    assert receipt["text_compatibility"] == "not_assessed" and receipt["status"] == "resolved"
    assert receipt["policy"] == worker.CLIP_LANGUAGE_POLICY
    assert (
        receipt["window"]
        == receipt["detector_window"]
        == {"start": 0.0, "end": 3.0, "timebase": "prepared_audio"}
    )
    assert "no_speech" not in json.dumps(receipt) and "private-unit" not in json.dumps(receipt)
    assert receipt["model"] == {
        "choice": "small",
        "repo": worker.MODELS["coarse"][0],
        "revision": worker.MODELS["coarse"][1],
    }
    for key, name in (("input_sha256", "audio.wav"), ("request_sha256", "request.json")):
        assert receipt[key] == hashlib.sha256((env.folder / name).read_bytes()).hexdigest()
    assert env.request == original and (env.folder / "request.json").read_bytes() == frozen
    assert any(call[0] == "align_model" and call[1:3] == (language, device) for call in env.calls)
    punkt = "danish" if language == "da" else "english"
    assert ("punkt", f"tokenizers/punkt_tab/{punkt}.pickle") in env.calls
    assert not list(env.folder.glob("aligned-*.json"))
    assert not list(env.folder.glob("recognition-*.json"))
    assert not (env.folder / "transcription.json").exists()
    assert [event["stage"] for event in env.progress] == ["loading", "aligning", "complete"]
    assert [event["fraction"] for event in env.progress] == [0.05, 0.6, 1]
    metadata = worker.clip_alignment_metadata(
        env.folder, request=env.request, require_alignment=True
    )
    assert metadata["alignment_outcome"] == {
        "schema": 1,
        "state": "alignment_attempted",
        "decision_sha256": worker.clip_decision_digest(receipt),
    }


@pytest.mark.parametrize("frames", [1, 176001, 480001, 960000])
def test_detector_reads_first_actual_contiguous_samples_only(clip, frames):
    env = clip
    write_wave(env.folder / "audio.wav", frames)
    env.options["duration"] = env.request["passages"][0]["end"] = frames / 16000
    freeze(env)
    assert worker.execute(env.request, env.folder) is None
    observed = next(call for call in env.calls if call[0] == "detect")
    mapped = worker.audio_data(env.folder)
    assert observed[1] == min(frames, 480000)
    assert (
        observed[2]
        == hashlib.sha256(memoryview(mapped[: min(frames, 480000)]).cast("B")).hexdigest()
    )
    assert observed[3] == {
        "vad_filter": False,
        "language_detection_segments": 1,
        "language_detection_threshold": 0.5,
    }
    assert decision(env)["detector_window"]["end"] == min(frames, 480000) / 16000
    assert decision(env)["window"]["end"] == frames / 16000


@pytest.mark.parametrize(
    "language,probability,status",
    [
        ("fr", 0.99, "unsupported"),
        ("da", 0.799, "uncertain"),
        ("private/path", 0.99, "uncertain"),
        (None, 0.99, "uncertain"),
        ("en", float("nan"), "uncertain"),
        ("en", 10**400, "uncertain"),
        ("da", True, "uncertain"),
    ],
)
def test_unresolved_decisions_checkpoint_before_alignment_and_retry_without_runtime(
    clip, monkeypatch, language, probability, status
):
    env = clip
    env.language, env.probability = language, probability
    assert worker.execute(env.request, env.folder) == "clip_language_" + status
    raw = (env.folder / "alignment_language.json").read_bytes()
    assert decision(env)["status"] == status
    assert not (env.folder / "alignment.json").exists()
    assert not any(call[0] in ("punkt", "align", "align_model") for call in env.calls)
    for name in ("torch", "whisperx", "faster_whisper", "huggingface_hub"):
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setattr(
        worker, "audio_data", lambda *a: pytest.fail("cached unresolved decision decoded input")
    )
    assert worker.execute(env.request, env.folder) == "clip_language_" + status
    assert (env.folder / "alignment_language.json").read_bytes() == raw


@pytest.mark.parametrize(
    "bad", ["multilingual", "sampling_rate", "n_samples", "nb_max_frames", "hop_length"]
)
def test_wrong_detector_or_geometry_refuses_before_detection(clip, bad):
    env = clip
    if bad == "multilingual":
        env.multilingual = 1
    else:
        env.geometry[bad] += 1
    assert worker.execute(env.request, env.folder) == "clip_language_model"
    assert not any(call[0] == "detect" for call in env.calls)
    assert not (env.folder / "alignment_language.json").exists()


@pytest.mark.parametrize(
    "bad",
    [
        "execution",
        "language",
        "device",
        "align",
        "transcribe",
        "retranscribe",
        "diarize",
        "whole_recording",
        "allow_downloads",
        "download_models",
        "duration",
        "missing_false",
        "start",
        "end",
        "text",
        "words",
        "characters",
        "passages",
        "extra",
    ],
)
def test_invalid_clip_scope_refuses_before_runtime_imports_or_decoding(clip, monkeypatch, bad):
    env = clip
    if bad in {"execution", "language", "device"}:
        env.options[bad] = {"execution": "ucloud", "language": "da", "device": "auto"}[bad]
    elif bad == "align":
        env.options[bad] = False
    elif bad in {
        "transcribe",
        "retranscribe",
        "diarize",
        "whole_recording",
        "allow_downloads",
        "download_models",
    }:
        env.options[bad] = True
    elif bad == "duration":
        env.options[bad] = 61.0
    elif bad == "missing_false":
        del env.options["diarize"]
    elif bad == "start":
        env.request["passages"][0]["start"] = 0.1
    elif bad == "end":
        env.request["passages"][0]["end"] = None
    elif bad == "text":
        env.request["passages"][0]["text"] = "  "
    elif bad == "words":
        env.request["passages"][0]["text"] = "word " * 121
    elif bad == "characters":
        env.request["passages"][0]["text"] = "x" * 1201
    elif bad == "passages":
        env.request["passages"].append(copy.deepcopy(env.request["passages"][0]))
    elif bad == "extra":
        env.request["private"] = "unexpected"
    freeze(env)
    for name in ("torch", "whisperx", "faster_whisper"):
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setattr(worker, "audio_data", lambda *a: pytest.fail("invalid scope decoded input"))
    with pytest.raises(ValueError):
        worker.execute(env.request, env.folder)
    assert not (env.folder / "alignment_language.json").exists()


@pytest.mark.parametrize("frames", [48002, 976000])
def test_wav_geometry_is_checked_before_runtime_and_float_preparation(clip, monkeypatch, frames):
    env = clip
    write_wave(env.folder / "audio.wav", frames)
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setattr(worker, "audio_data", lambda *a: pytest.fail("invalid WAV was decoded"))
    with pytest.raises(ValueError, match="matching prepared audio"):
        worker.execute(env.request, env.folder)


def test_one_sample_prepared_duration_rounding_is_allowed(clip):
    env = clip
    write_wave(env.folder / "audio.wav", 48001)
    assert worker.execute(env.request, env.folder) is None
    assert decision(env)["window"]["end"] == 48001 / 16000
    assert env.options["duration"] == 3.0


@pytest.mark.parametrize(
    "name",
    [
        "alignment.json",
        "windows.json",
        "recognition-00000.json",
        "aligned-00000.json",
        "transcription.json",
        "retranscription.json",
        "diarization.json",
    ],
)
def test_new_clip_decision_never_blesses_unbound_artifacts(clip, monkeypatch, name):
    env = clip
    path = env.folder / name
    path.write_bytes(b"{}")
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setattr(
        worker, "audio_data", lambda *a: pytest.fail("unbound artifacts were decoded")
    )
    with pytest.raises(ValueError, match="unbound"):
        worker.execute(env.request, env.folder)
    assert path.read_bytes() == b"{}" and not (env.folder / "alignment_language.json").exists()


def test_clip_decision_precedes_tokenizer_and_alignment_and_never_redetects(clip, monkeypatch):
    env = clip
    env.fail_punkt = True
    assert worker.execute(env.request, env.folder) == "cache"
    raw = (env.folder / "alignment_language.json").read_bytes()
    env.fail_punkt = False
    env.fail_alignment = True
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(RuntimeError, match="interrupted alignment"):
        worker.execute(env.request, env.folder)
    assert not list(env.folder.glob("aligned-*.json"))
    assert not (env.folder / "alignment.json").exists()
    env.fail_alignment = False
    assert worker.execute(env.request, env.folder) is None
    assert sum(call[0] == "detect" for call in env.calls) == 1
    assert sum(call[0] == "align" for call in env.calls) == 2
    assert (env.folder / "alignment_language.json").read_bytes() == raw


def test_valid_final_clip_returns_before_imports_cache_or_current_pin_lookup(clip, monkeypatch):
    env = clip
    assert worker.execute(env.request, env.folder) is None
    before = {
        name: (env.folder / name).read_bytes()
        for name in ("request.json", "alignment_language.json", "alignment.json")
    }
    for name in ("audio.f32", "audio.f32.source.json"):
        (env.folder / name).unlink()
    for name in ("torch", "whisperx", "faster_whisper", "huggingface_hub", "nltk"):
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setattr(worker, "MODELS", {})
    monkeypatch.setattr(
        worker, "audio_data", lambda *a: pytest.fail("completed clip decoded input")
    )
    monkeypatch.setattr(
        worker.importlib.metadata,
        "version",
        lambda *a: pytest.fail("completed clip queried runtime"),
    )
    assert worker.execute(env.request, env.folder) is None
    receipt = worker.clip_alignment_metadata(env.folder, require_alignment=True)[
        "clip_language_decision"
    ]
    assert worker.validate_clip_language_decision(receipt) == receipt
    assert worker.clip_decision_digest(receipt) == worker.clip_decision_digest(
        dict(reversed(list(receipt.items())))
    )
    assert {name: (env.folder / name).read_bytes() for name in before} == before


@pytest.mark.parametrize("operation", ["fixed", "full_asr", "unknown"])
def test_clip_receipt_is_refused_by_other_operations_before_imports(clip, monkeypatch, operation):
    env = clip
    assert worker.execute(env.request, env.folder) is None
    request = copy.deepcopy(env.request)
    if operation != "unknown":
        del request["operation"]
    else:
        request["operation"] = "other"
    if operation == "fixed":
        request["options"]["language"] = "da"
    if operation == "full_asr":
        request["options"]["transcribe"] = True
        request["passages"] = []
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(ValueError, match="bounded clip language"):
        worker.execute(request, env.folder)
    with pytest.raises(ValueError, match="clip language"):
        worker.transcribe_audio(None, request["options"], env.folder, None, None)


@pytest.mark.parametrize(
    "tamper",
    [
        "request_hash",
        "audio_hash",
        "window",
        "detector_window",
        "detector_bool",
        "policy",
        "model",
        "private",
        "no_speech",
        "text_compatibility",
    ],
)
def test_clip_decision_tampering_fails_closed_without_reloading_model(clip, tamper):
    env = clip
    assert worker.execute(env.request, env.folder) is None
    value = decision(env)
    if tamper in {"request_hash", "audio_hash"}:
        value["request_sha256" if tamper == "request_hash" else "input_sha256"] = "0" * 64
    elif tamper == "window":
        value["window"]["end"] = 2.0
    elif tamper == "detector_window":
        value["detector_window"]["end"] = 2.0
    elif tamper == "detector_bool":
        value["detector_window"]["start"] = False
    elif tamper == "policy":
        value["policy"]["min_probability"] = 0.1
    elif tamper == "model":
        value["model"]["choice"] = "large-v3"
    elif tamper == "private":
        value["private_path"] = "SHOULD_NOT_LEAK"
    elif tamper == "no_speech":
        value["status"] = "no_speech"
    elif tamper == "text_compatibility":
        value["text_compatibility"] = "verified"
    worker.write_json(env.folder / "alignment_language.json", value)
    before = len(env.calls)
    with pytest.raises(ValueError) as error:
        worker.execute(env.request, env.folder)
    assert len(env.calls) == before and "SHOULD_NOT_LEAK" not in str(error.value)


@pytest.mark.parametrize(
    "tamper", ["state", "digest", "schema_bool", "id", "token", "interval", "missing_outcome"]
)
def test_clip_final_alignment_needs_own_exact_bound_outcome_and_source(clip, tamper):
    env = clip
    assert worker.execute(env.request, env.folder) is None
    path = env.folder / "alignment.json"
    alignment = json.loads(path.read_bytes())
    if tamper == "state":
        alignment["language_outcome"]["state"] = "aligned"
    elif tamper == "digest":
        alignment["language_outcome"]["decision_sha256"] = "0" * 64
    elif tamper == "schema_bool":
        alignment["language_outcome"]["schema"] = True
    elif tamper == "id":
        alignment["passages"][0]["id"] = "other"
    elif tamper == "token":
        alignment["passages"][0]["words"][0]["word"] = "other"
    elif tamper == "interval":
        alignment["passages"][0]["words"][0]["end"] = 100.0
    else:
        del alignment["language_outcome"]
    worker.write_json(path, alignment)
    raw = path.read_bytes()
    with pytest.raises(ValueError):
        worker.execute(env.request, env.folder)
    assert path.read_bytes() == raw


def test_clip_alignment_cache_tracks_effective_language_across_units(clip, monkeypatch):
    env = clip
    monkeypatch.setattr(worker, "CACHE_ALIGNMENT", True)
    for index, language in enumerate(("da", "en", "en", "da")):
        folder = env.folder / str(index)
        folder.mkdir()
        write_wave(folder / "audio.wav", 48000)
        worker.write_json(folder / "request.json", env.request)
        env.language = language
        assert worker.execute(env.request, folder) is None
        assert worker.ALIGNMENT_CACHE[0] == (language, "cpu")
    assert [call[1] for call in env.calls if call[0] == "align_model"] == ["da", "en", "da"]
    assert env.options["language"] == "auto"


def test_clip_refuses_source_change_between_scope_and_mapped_inference(clip, monkeypatch):
    env = clip
    real = worker.audio_data

    def stale(directory):
        mapped = real(directory)
        write_wave(directory / "audio.wav", 48000, -1000)
        return mapped

    monkeypatch.setattr(worker, "audio_data", stale)
    with pytest.raises(ValueError, match="decoded input"):
        worker.execute(env.request, env.folder)
    assert not any(call[0] in ("detect", "detector", "align") for call in env.calls)


def test_clip_detection_preserves_original_mapping_and_request_bytes(clip):
    env = clip
    mapped = worker.audio_data(env.folder)
    before = hashlib.sha256(memoryview(mapped).cast("B")).hexdigest()
    raw = (env.folder / "request.json").read_bytes()
    assert worker.execute(env.request, env.folder) is None
    assert mapped.flags.writeable is True
    assert hashlib.sha256(memoryview(mapped).cast("B")).hexdigest() == before
    assert (env.folder / "request.json").read_bytes() == raw


def test_missing_tokenizer_never_constructs_detector_or_downloads(clip):
    env = clip
    (env.cache / "tokenizer.json").unlink()
    assert worker.execute(env.request, env.folder) == "cache"
    assert not any(call[0] in ("detect", "detector", "align") for call in env.calls)
    assert not (env.folder / "alignment_language.json").exists()


def test_clip_refuses_unmapped_inference_even_when_shape_is_plausible(clip, monkeypatch):
    env = clip
    monkeypatch.setattr(worker, "audio_data", lambda *a: [0.0] * 48000)
    with pytest.raises(ValueError, match="verified prepared float mapping"):
        worker.execute(env.request, env.folder)
    assert not any(call[0] in ("detect", "detector", "align") for call in env.calls)


def test_supplied_oversized_wav_is_refused_before_header_or_decoder(clip, monkeypatch):
    env = clip
    path = env.folder / "audio.wav"
    real_stat = type(path).stat

    def oversized(candidate, *args, **kwargs):
        if candidate == path:
            return SimpleNamespace(st_size=2 * 16000 * 60 + 65537)
        return real_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(type(path), "stat", oversized)
    monkeypatch.setattr(
        worker.wave, "open", lambda *a: pytest.fail("oversized WAV header was opened")
    )
    monkeypatch.setattr(worker, "audio_data", lambda *a: pytest.fail("oversized WAV was decoded"))
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(ValueError, match="too large"):
        worker.execute(env.request, env.folder)


@pytest.mark.parametrize("name", ["request.json", "alignment_language.json", "alignment.json"])
def test_clip_json_readers_refuse_size_before_opening(clip, monkeypatch, name):
    env = clip
    assert worker.execute(env.request, env.folder) is None
    monkeypatch.setattr(worker, "AUTO_JSON_LIMIT", 2048)
    path = env.folder / name
    raw = path.read_bytes()
    assert len(raw) < 2048
    path.write_bytes(raw + b" " * (2049 - len(raw)))
    real_open = type(path).open

    def guard(candidate, *args, **kwargs):
        if candidate == path:
            pytest.fail("oversized clip JSON was opened")
        return real_open(candidate, *args, **kwargs)

    monkeypatch.setattr(type(path), "open", guard)
    with pytest.raises(ValueError, match="size limit"):
        worker.execute(env.request, env.folder)


def test_clip_request_context_must_match_frozen_text_before_decoding(clip, monkeypatch):
    env = clip
    requested = copy.deepcopy(env.request)
    env.request["passages"][0]["text"] = "Another exact wording"
    freeze(env)
    monkeypatch.setattr(
        worker, "audio_data", lambda *a: pytest.fail("changed request decoded input")
    )
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(ValueError, match="differs from its frozen input"):
        worker.execute(requested, env.folder)


def test_clip_cli_retains_the_existing_runtime_doctor_gate(clip, monkeypatch):
    env = clip
    monkeypatch.setattr(worker, "doctor", lambda: {"ready": False})
    monkeypatch.setattr(worker, "execute", lambda *a: pytest.fail("CLI bypassed runtime doctor"))
    assert worker.process_directory(env.folder) == "runtime"
    assert json.loads((env.folder / "error.json").read_bytes()) == {"code": "runtime"}


@pytest.mark.parametrize(
    "word",
    [
        {"start": None, "end": 999.0, "score": None},
        {"start": 999.0, "end": None, "score": None},
        {"start": None, "end": True, "score": None},
        {"start": None, "end": None, "score": float("nan")},
        {"start": None, "end": None, "score": 10**400},
        {"start": 1.0, "end": 0.5, "score": None},
        {"start": 1.0, "end": None, "score": None},
    ],
)
def test_partial_clip_intervals_cannot_escape_bounds_or_validation(clip, word):
    env = clip
    assert worker.execute(env.request, env.folder) is None
    path = env.folder / "alignment.json"
    alignment = json.loads(path.read_bytes())
    alignment["passages"][0]["words"][0].update(word)
    path.write_text(json.dumps(alignment), encoding="utf-8")
    before = len(env.calls)
    with pytest.raises(ValueError):
        worker.execute(env.request, env.folder)
    assert len(env.calls) == before


def test_clip_size_is_rechecked_after_source_binding_before_runtime(clip, monkeypatch):
    env = clip
    real = worker.language_binding

    def changed(directory):
        binding = real(directory)
        with (directory / "audio.wav").open("ab") as stream:
            stream.write(b" " * (2 * 16000 * 60 + 65537))
        return binding

    monkeypatch.setattr(worker, "language_binding", changed)
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setattr(worker, "audio_data", lambda *a: pytest.fail("grown WAV was decoded"))
    with pytest.raises(ValueError):
        worker.execute(env.request, env.folder)


@pytest.mark.parametrize(
    "partial",
    [
        {"start": None, "end": None, "score": None},
        {"start": None, "end": 0.4, "score": None},
        {"start": 0.1, "end": None, "score": 0.8},
        {"start": 0.1, "end": 0.4, "score": None},
        {"start": 0.1, "end": 0.1, "score": -0.1},
    ],
)
def test_legitimate_partial_clip_words_remain_readable_without_coverage_claim(clip, partial):
    env = clip
    assert worker.execute(env.request, env.folder) is None
    path = env.folder / "alignment.json"
    alignment = json.loads(path.read_bytes())
    alignment["passages"][0]["words"][0].update(partial)
    worker.write_json(path, alignment)
    assert worker.execute(env.request, env.folder) is None
    assert (
        worker.clip_alignment_metadata(env.folder)["alignment_outcome"]["state"]
        == "alignment_attempted"
    )
