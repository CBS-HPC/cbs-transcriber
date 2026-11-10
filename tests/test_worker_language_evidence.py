"""Configured ASR language is source-bound evidence, never detection confidence."""

import copy
import hashlib
import json
import sys
import types
import wave
from array import array
from types import SimpleNamespace

import pytest

from cbs_transcriber import enhancement_worker as worker


def write_wave(path, frames, value=0):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(value.to_bytes(2, "little", signed=True) * frames)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    options = {
        "language": "da",
        "device": "cpu",
        "whisper_model": "small",
        "duration": 3.0,
    }
    request = {"passages": [], "options": options}
    write_wave(tmp_path / "audio.wav", 48000)
    worker.write_json(tmp_path / "request.json", request)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "tokenizer.json").write_text("{}", encoding="utf-8")
    calls = []

    class FakeModel:
        def __init__(self, path, **kwargs):
            assert kwargs["local_files_only"] is True
            calls.append(("load", path))

        def transcribe(self, audio, **kwargs):
            calls.append(("transcribe", len(audio), kwargs))
            return iter(
                [
                    SimpleNamespace(
                        text=" Øresund er åbent.",
                        start=0.0,
                        end=1.5,
                        avg_logprob=-0.3,
                        no_speech_prob=0.02,
                        compression_ratio=1.1,
                        words=[SimpleNamespace(word=" Øresund", start=0.6, end=0.8)],
                    )
                ]
            ), SimpleNamespace(language=kwargs["language"], language_probability=1.0)

    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel)
    )
    monkeypatch.setattr(worker.importlib.metadata, "version", lambda name: "1.2.1")
    return SimpleNamespace(
        folder=tmp_path,
        options=options,
        request=request,
        cache=cache,
        calls=calls,
        audio=[0.0] * 48000,
    )


def transcribe(env):
    return worker.transcribe_audio(
        env.audio, env.options, env.folder, lambda key: str(env.cache), lambda *a: None
    )


def receipt(env):
    return json.loads((env.folder / "transcription.json").read_text(encoding="utf-8"))[
        "language_evidence"
    ]


@pytest.mark.parametrize("language", ["da", "en"])
def test_fixed_asr_captures_exact_input_and_never_calls_forced_probability_detection(
    prepared, language
):
    env = prepared
    env.options["language"] = language
    worker.write_json(env.folder / "request.json", env.request)
    rows = transcribe(env)
    entry = receipt(env)[0]
    assert rows[0]["text"] == "Øresund er åbent."
    assert entry["origin"] == "configured" and entry["probability"] is None
    assert entry["requested"] == entry["effective"] == entry["reported_language"] == language
    assert entry["engine"] == {"name": "faster-whisper", "version": "1.2.1"}
    assert entry["window"] == {"start": 0.0, "end": 3.0, "timebase": "prepared_audio"}
    for field, name in (("input_sha256", "audio.wav"), ("request_sha256", "request.json")):
        assert entry[field] == hashlib.sha256((env.folder / name).read_bytes()).hexdigest()
    assert entry["model"] == {
        "choice": "small",
        "repo": worker.MODELS["coarse"][0],
        "revision": worker.MODELS["coarse"][1],
    }
    assert env.calls[-1][2]["language"] == language


def test_passage_evidence_is_per_actual_padded_sample_window_and_hashes_once(prepared, monkeypatch):
    env = prepared
    env.request["passages"] = [
        {"id": "one", "start": 1.00003, "end": 2.00003, "text": ""},
        {"id": "two", "start": 2.6, "end": 2.9, "text": ""},
    ]
    worker.write_json(env.folder / "request.json", env.request)
    hashed, real_hash = [], worker.file_hash

    def counted(path):
        hashed.append(path.name)
        return real_hash(path)

    monkeypatch.setattr(worker, "file_hash", counted)
    worker.retranscribe_passages(
        env.audio, env.request, env.options, env.folder, lambda key: str(env.cache), lambda *a: None
    )
    data = json.loads((env.folder / "retranscription.json").read_bytes())
    entries = data["language_evidence"]
    assert [e["passage_id"] for e in entries] == ["one", "two"]
    assert [e["window"] for e in entries] == [
        {"start": 0.5, "end": 2.5, "timebase": "prepared_audio"},
        {"start": 2.1, "end": 3.0, "timebase": "prepared_audio"},
    ]
    assert [c[1] for c in env.calls if c[0] == "transcribe"] == [32000, 14400]
    assert hashed.count("audio.wav") == hashed.count("request.json") == 1
    assert all(e["probability"] is None for e in entries)


@pytest.mark.parametrize("stage", ["transcription", "retranscription"])
def test_legacy_cached_stages_keep_exact_bytes_without_input_hash_or_runtime(
    tmp_path, monkeypatch, stage
):
    path = tmp_path / f"{stage}.json"
    raw = (
        b'{ "segments": [{"text":"Legacy","start":0,"end":1}] }\n'
        if stage == "transcription"
        else b'{ "passages": [{"id":"0","text":"Legacy"}] }\n'
    )
    path.write_bytes(raw)

    def refused(*args, **kwargs):
        raise AssertionError("A completed legacy stage cannot need language evidence or a model")

    monkeypatch.setattr(worker, "language_binding", refused)
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=refused))
    options = {"language": "da", "device": "cpu", "whisper_model": "small", "duration": 1}
    if stage == "transcription":
        rows = worker.transcribe_audio(None, options, tmp_path, refused, refused)
    else:
        rows = worker.retranscribe_passages(
            None,
            {"passages": [{"id": "0", "start": 0, "end": 1}]},
            options,
            tmp_path,
            refused,
            refused,
        )
    assert rows[0]["text"] == "Legacy" and path.read_bytes() == raw


def test_cached_evidenced_stage_reuses_historical_pin_without_loading_engine(prepared, monkeypatch):
    env = prepared
    transcribe(env)
    path = env.folder / "transcription.json"
    data = json.loads(path.read_bytes())
    data["language_evidence"][0]["model"]["revision"] = "a" * 40
    data["language_evidence"][0]["engine"]["version"] = "1.1.0"
    worker.write_json(path, data)
    raw, count = path.read_bytes(), len(env.calls)

    def refused(*args):
        raise AssertionError("Historical evidence validation cannot query an installed runtime")

    monkeypatch.setattr(worker.importlib.metadata, "version", refused)
    assert transcribe(env)[0]["text"] == "Øresund er åbent."
    assert path.read_bytes() == raw and len(env.calls) == count


@pytest.mark.parametrize("changed", ["audio", "request", "choice", "window"])
def test_cached_receipt_refuses_changed_frozen_binding(prepared, changed):
    env = prepared
    transcribe(env)
    if changed == "audio":
        write_wave(env.folder / "audio.wav", 48000, value=1000)
    elif changed == "request":
        with (env.folder / "request.json").open("ab") as stream:
            stream.write(b" ")
    else:
        data = json.loads((env.folder / "transcription.json").read_bytes())
        if changed == "choice":
            data["language_evidence"][0]["model"]["choice"] = "medium"
        else:
            data["language_evidence"][0]["window"]["end"] = 2.0
        worker.write_json(env.folder / "transcription.json", data)
    with pytest.raises(ValueError):
        transcribe(env)


@pytest.mark.parametrize(
    "invalid",
    ["probability", "extra", "model", "hash", "scope", "nan", "requested", "huge", "version"],
)
def test_shape_validation_rejects_untrusted_metadata_without_echoing_it(prepared, invalid):
    env = prepared
    transcribe(env)
    entries = copy.deepcopy(receipt(env))
    entry = entries[0]
    if invalid == "probability":
        entry["probability"] = 1.0
    elif invalid == "extra":
        entry["api_key"] = "synthetic-private-value"
    elif invalid == "model":
        entry["model"]["repo"] = "/private/path"
    elif invalid == "hash":
        entry["input_sha256"] = "not-a-sha"
    elif invalid == "scope":
        entry["scope"] = []
    elif invalid == "nan":
        entry["window"]["end"] = float("nan")
    elif invalid == "huge":
        entry["window"]["end"] = 10**400
    elif invalid == "version":
        entry["engine"]["version"] = "1.2." + "1" * 1000
    else:
        entry["requested"] = {"nested": True}
    with pytest.raises(ValueError) as error:
        worker.validate_language_evidence(entries)
    assert "synthetic-private-value" not in str(error.value) and "/private/path" not in str(
        error.value
    )


def test_missing_engine_metadata_and_unknown_reported_label_stay_unknown(prepared, monkeypatch):
    env = prepared

    def absent(name):
        raise worker.importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(worker.importlib.metadata, "version", absent)
    transcribe(env)
    assert receipt(env)[0]["engine"]["version"] is None
    binding = worker.language_binding(env.folder)
    entry = worker._configured_language_evidence(
        binding,
        env.options,
        worker.language_windows(binding)[0],
        SimpleNamespace(language="arbitrary private value", language_probability=0.99),
    )
    assert "reported_language" not in entry and entry["probability"] is None


@pytest.mark.parametrize("changed", ["source", "decoded", "missing_marker"])
def test_decoded_audio_cache_is_bound_to_both_source_and_float_bytes(tmp_path, changed):
    source = tmp_path / "audio.wav"
    write_wave(source, 16, value=1000)
    decoded = worker.prepare_audio(tmp_path)
    positive = decoded.read_bytes()
    if changed == "source":
        write_wave(source, 16, value=-1000)
    elif changed == "decoded":
        decoded.write_bytes(b"\x00" * len(positive))
    else:
        (tmp_path / "audio.f32.source.json").unlink()
    worker.prepare_audio(tmp_path)
    values = array("f")
    values.frombytes(decoded.read_bytes())
    assert (values[0] < 0) if changed == "source" else (values[0] > 0)
    marker = json.loads((tmp_path / "audio.f32.source.json").read_bytes())
    assert marker["input_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert marker["decoded_sha256"] == hashlib.sha256(decoded.read_bytes()).hexdigest()


def test_duplicate_passage_evidence_cannot_be_reused(prepared):
    env = prepared
    binding = worker.language_binding(env.folder)
    windows = worker.language_windows(
        binding,
        passages=[{"id": "one", "start": 1, "end": 2}],
        duration=3,
    )
    item = worker._configured_language_evidence(binding, env.options, windows[0], None)
    with pytest.raises(ValueError, match="duplicate"):
        worker.validate_language_evidence([item, copy.deepcopy(item)])


@pytest.mark.parametrize("changed", ["source", "foreign", "stale_mapping", "dtype"])
def test_new_asr_checks_actual_mapped_audio_before_loading_model(prepared, changed):
    import numpy as np

    env = prepared
    mapped = worker.audio_data(env.folder)
    if changed == "source":
        write_wave(env.folder / "audio.wav", 48000, value=2000)
    elif changed == "foreign":
        foreign = env.folder / "other.f32"
        foreign.write_bytes((env.folder / "audio.f32").read_bytes())
        mapped = np.memmap(foreign, dtype="<f4", mode="r")
    else:
        # An old Unix mapping can retain old bytes after its filename is replaced. Model it
        # with a buffer carrying the same filename, without relying on Windows replace rules.
        class NamedArray(np.ndarray):
            pass

        stale = np.zeros(48000, dtype=">f4" if changed == "dtype" else "<f4").view(NamedArray)
        stale.filename = str(env.folder / "audio.f32")
        mapped = stale
        if changed == "stale_mapping":
            write_wave(env.folder / "audio.wav", 48000, value=2000)
            worker.prepare_audio(env.folder)
    with pytest.raises(ValueError, match="(decoded input|mapped inference input)"):
        worker.transcribe_audio(
            mapped, env.options, env.folder, lambda key: str(env.cache), lambda *a: None
        )
    assert not env.calls


def test_verified_mapped_asr_succeeds_but_cached_stage_does_not_require_float_cache(prepared):
    env = prepared
    mapped = worker.audio_data(env.folder)
    rows = worker.transcribe_audio(
        mapped, env.options, env.folder, lambda key: str(env.cache), lambda *a: None
    )
    count = len(env.calls)
    # Derived float data is irrelevant once exact ASR output and its WAV/request evidence exist.
    (env.folder / "audio.f32.source.json").unlink()
    assert transcribe(env) == rows and len(env.calls) == count
