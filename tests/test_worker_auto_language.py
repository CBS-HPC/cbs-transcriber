"""Hidden full-ASR Automatic decisions; no real runtime, weights or network."""

import copy
import hashlib
import json
import sys
import wave
from types import SimpleNamespace

import pytest

from cbs_transcriber import enhancement_worker as worker


def write_wave(path, value=1000):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(value.to_bytes(2, "little", signed=True) * 48000)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    options = {
        "language": "auto",
        "device": "cpu",
        "whisper_model": "small",
        "duration": 3.0,
        "transcribe": True,
        "retranscribe": False,
        "align": True,
        "diarize": False,
        "allow_downloads": False,
    }
    env = SimpleNamespace(
        folder=tmp_path,
        options=options,
        request={"passages": [], "options": options},
        calls=[],
        info=SimpleNamespace(language="da", language_probability=0.95, duration_after_vad=2.0),
        rows=[SimpleNamespace(text=" Øresund er åbent.", start=0.2, end=2.0)],
        multilingual=True,
        fail_alignment=False,
        fail_segments=False,
    )
    write_wave(tmp_path / "audio.wav")
    worker.write_json(tmp_path / "request.json", env.request)
    env.original_request = (tmp_path / "request.json").read_bytes()
    env.original_options = copy.deepcopy(options)
    cache = tmp_path / "model-cache"
    cache.mkdir()
    (cache / "tokenizer.json").write_text("{}", encoding="utf-8")

    class Model:
        def __init__(self, path, **kwargs):
            env.calls.append(("model", path, kwargs))
            self.model = SimpleNamespace(is_multilingual=env.multilingual)

        def transcribe(self, audio, **kwargs):
            env.calls.append(("asr", kwargs))

            def segments():
                yield from env.rows
                if env.fail_segments:
                    raise RuntimeError("synthetic unfinished ASR")

            return segments(), env.info

    def snapshot(repo, **kwargs):
        env.calls.append(("snapshot", repo, kwargs))
        assert kwargs["local_files_only"] is True
        return str(cache)

    def load_align(language, device, **kwargs):
        env.calls.append(("align_model", language, device, kwargs))
        return "aligner-" + language, {"language": language}

    def align(segments, model, metadata, audio, device, **kwargs):
        env.calls.append(("align", model, metadata["language"]))
        if env.fail_alignment:
            raise RuntimeError("synthetic interrupted alignment")
        return {
            "word_segments": [
                {"word": word, "start": 0.3 + index * 0.3, "end": 0.5 + index * 0.3, "score": 0.99}
                for index, word in enumerate(segments[0]["text"].split())
            ]
        }

    class Speakers:
        def __init__(self, **kwargs):
            env.calls.append(("speaker_model", kwargs))

        def __call__(self, audio, **kwargs):
            env.calls.append(("speakers", kwargs))
            return SimpleNamespace(
                itertuples=lambda: iter([SimpleNamespace(start=0.2, end=2.0, speaker="S0")])
            )

    modules = {
        "faster_whisper": SimpleNamespace(WhisperModel=Model),
        "torch": SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)),
        "whisperx": SimpleNamespace(load_align_model=load_align, align=align),
        "whisperx.diarize": SimpleNamespace(DiarizationPipeline=Speakers),
        "huggingface_hub": SimpleNamespace(snapshot_download=snapshot),
        "nltk": SimpleNamespace(
            data=SimpleNamespace(load=lambda name: env.calls.append(("punkt", name)))
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(worker.importlib.metadata, "version", lambda name: "1.2.1")
    monkeypatch.setattr(worker, "ALIGNMENT_CACHE", None)
    monkeypatch.setattr(worker, "CACHE_ALIGNMENT", False)
    env.snapshot = lambda key: str(cache)
    return env


def transcribe(env, *, audio=None, language_result=None):
    return worker.transcribe_audio(
        worker.audio_data(env.folder) if audio is None else audio,
        env.options,
        env.folder,
        env.snapshot,
        lambda *a: None,
        language_result=language_result,
    )


def saved(env):
    return json.loads((env.folder / "transcription.json").read_bytes())


@pytest.mark.parametrize("language", ["da", "en"])
def test_auto_full_asr_binds_real_input_and_routes_only_copied_effective_options(
    prepared, language
):
    env = prepared
    env.info.language = language
    assert worker.execute(env.request, env.folder) is None
    data = saved(env)
    decision = data["language_decision"]
    assert set(data) == {"segments", "language_decision"}
    assert decision["requested"] == "auto" and decision["effective"] == language
    assert decision["origin"] == "asr_detection" and decision["status"] == "resolved"
    assert decision["probability"] == 0.95
    assert decision["duration_after_vad"] == 2.0 and decision["segment_count"] == 1
    assert decision["scope"] == "full_asr_input" and decision["detector_window"] is None
    assert decision["window"] == {"start": 0.0, "end": 3.0, "timebase": "prepared_audio"}
    assert decision["policy"] == worker.AUTO_LANGUAGE_POLICY
    assert decision["policy"]["experimental"] is True
    for key, name in (("input_sha256", "audio.wav"), ("request_sha256", "request.json")):
        assert decision[key] == hashlib.sha256((env.folder / name).read_bytes()).hexdigest()
    assert decision["model"] == {
        "choice": "small",
        "repo": worker.MODELS["coarse"][0],
        "revision": worker.MODELS["coarse"][1],
    }
    assert decision["engine"] == {"name": "faster-whisper", "version": "1.2.1"}
    asr = next(call[1] for call in env.calls if call[0] == "asr")
    assert asr == {
        "language": None,
        "vad_filter": True,
        "beam_size": 5,
        "condition_on_previous_text": False,
        "language_detection_segments": 1,
        "language_detection_threshold": 0.5,
    }
    assert any(call[0] == "align_model" and call[1] == language for call in env.calls)
    punkt = "danish" if language == "da" else "english"
    assert ("punkt", f"tokenizers/punkt_tab/{punkt}.pickle") in env.calls
    assert data["segments"][0]["text"] == "Øresund er åbent."
    assert env.options == env.original_options
    assert env.request["options"]["language"] == "auto"
    assert (env.folder / "request.json").read_bytes() == env.original_request


def test_auto_alignment_cache_uses_resolved_language_across_jobs(prepared, monkeypatch):
    env = prepared
    monkeypatch.setattr(worker, "CACHE_ALIGNMENT", True)
    for index, language in enumerate(("da", "en", "en", "da", "en")):
        folder = env.folder / str(index)
        folder.mkdir()
        write_wave(folder / "audio.wav")
        request = copy.deepcopy(env.request)
        if index == 4:
            request["options"]["language"] = "en"  # Fixed override after Automatic jobs.
        worker.write_json(folder / "request.json", request)
        env.info.language = language
        assert worker.execute(request, folder) is None
        assert worker.ALIGNMENT_CACHE[0] == (language, "cpu")
    assert [call[1] for call in env.calls if call[0] == "align_model"] == ["da", "en", "da", "en"]
    assert env.options["language"] == "auto"


@pytest.mark.parametrize(
    ("language", "probability", "duration", "has_rows", "status"),
    [
        ("fr", 0.99, 2.0, True, "unsupported"),
        ("en", 0.799, 2.0, True, "uncertain"),
        ("da", 0.8, 2.0, False, "uncertain"),
        ("da", 0.99, 0.0, True, "uncertain"),
        ("da", 0.99, 0.0, False, "no_speech"),
        ("private/value", 0.99, 2.0, True, "uncertain"),
        ("da", float("nan"), 2.0, True, "uncertain"),
        ("da", 10**400, 2.0, True, "uncertain"),
        ("da", True, 2.0, True, "uncertain"),
        ("da", 0.99, -1, True, "uncertain"),
        ("da", 0.99, 4.0, True, "uncertain"),
        ("da", 0.99, None, True, "uncertain"),
    ],
)
def test_unresolved_decision_preserves_asr_with_unavailable_timing_and_independent_speakers(
    prepared, language, probability, duration, has_rows, status
):
    env = prepared
    env.info = SimpleNamespace(
        language=language, language_probability=probability, duration_after_vad=duration
    )
    env.options["diarize"] = True
    worker.write_json(env.folder / "request.json", env.request)
    if not has_rows:
        env.rows = []
    result = {}
    assert transcribe(env, language_result=result) == "language_" + status
    assert result["status"] == status
    assert worker.execute(env.request, env.folder) is None
    data = saved(env)
    assert data["language_decision"]["status"] == status
    assert len(data["segments"]) == int(has_rows)
    alignment = json.loads((env.folder / "alignment.json").read_bytes())
    assert alignment["language_outcome"] == {
        "schema": 1,
        "state": "unavailable_language",
        "decision_sha256": worker.decision_digest(data["language_decision"]),
    }
    assert alignment["passages"] == (
        [
            {
                "id": "0",
                "words": [
                    {"word": token, "start": None, "end": None, "score": None}
                    for token in "Øresund er åbent.".split()
                ],
            }
        ]
        if has_rows
        else []
    )
    assert (env.folder / "diarization.json").exists()
    assert [call[0] for call in env.calls].count("speakers") == 1
    assert not any(call[0] in ("align", "align_model", "punkt") for call in env.calls)
    assert "private/value" not in json.dumps(data)
    raw = (env.folder / "transcription.json").read_bytes()
    before = len(env.calls)
    assert worker.execute(env.request, env.folder) is None
    assert not any(
        call[0] in ("model", "asr", "align_model", "align", "speaker_model", "speakers")
        for call in env.calls[before:]
    )
    assert (env.folder / "transcription.json").read_bytes() == raw


@pytest.mark.parametrize("multilingual", [False, None, 1])
def test_auto_requires_actual_multilingual_engine_before_asr(prepared, multilingual):
    env = prepared
    env.multilingual = multilingual
    assert transcribe(env) == "language_model"
    assert not any(call[0] == "asr" for call in env.calls)
    assert not (env.folder / "transcription.json").exists()


@pytest.mark.parametrize("scope", ["alignment", "passage", "retranscription", "speakers"])
def test_unsupported_auto_operations_refuse_before_imports_decoding_or_inference(
    tmp_path, monkeypatch, scope
):
    options = {
        "language": "auto",
        "transcribe": scope == "passage",
        "retranscribe": scope == "retranscription",
    }
    request = {"options": options, "passages": [{"id": "0"}] if scope == "passage" else []}
    for name in ("torch", "whisperx", "huggingface_hub", "faster_whisper"):
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setattr(
        worker, "audio_data", lambda *a: pytest.fail("unsupported scope decoded audio")
    )
    assert worker.execute(request, tmp_path) == "language_scope"
    assert (
        worker.retranscribe_passages(None, request, options, tmp_path, None, None)
        == "language_scope"
    )


@pytest.mark.parametrize(
    "field,value", [("transcribe", 1), ("retranscribe", None), ("passages", None)]
)
def test_auto_scope_requires_literal_full_transcription_before_optional_imports(
    tmp_path, monkeypatch, field, value
):
    request = {
        "options": {"language": "auto", "transcribe": True, "retranscribe": False},
        "passages": [],
    }
    if field == "passages":
        request[field] = value
    else:
        request["options"][field] = value
    monkeypatch.setitem(sys.modules, "torch", None)
    assert worker.execute(request, tmp_path) == "language_scope"


def test_completed_auto_cache_survives_without_models_decoded_cache_or_current_pin(
    prepared, monkeypatch
):
    env = prepared
    assert isinstance(transcribe(env), list)
    path = env.folder / "transcription.json"
    raw = path.read_bytes()
    for name in ("audio.f32", "audio.f32.source.json"):
        (env.folder / name).unlink()
    for name in ("faster_whisper", "torch", "whisperx"):
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setattr(
        worker.importlib.metadata, "version", lambda *a: pytest.fail("historical runtime queried")
    )
    monkeypatch.setitem(worker.MODELS, "coarse", ("future/repo", "a" * 40))
    result = {}
    rows = worker.transcribe_audio(
        None, env.options, env.folder, None, None, language_result=result
    )
    assert rows[0]["text"] == "Øresund er åbent." and result["effective"] == "da"
    assert path.read_bytes() == raw
    assert not (env.folder / "audio.f32").exists()


def test_completed_auto_asr_is_not_repeated_after_alignment_interruption(prepared):
    env = prepared
    env.fail_alignment = True
    with pytest.raises(RuntimeError, match="interrupted alignment"):
        worker.execute(env.request, env.folder)
    path = env.folder / "transcription.json"
    raw = path.read_bytes()
    env.fail_alignment = False
    env.info.language = "en"  # A retry must not reconsider a completed Danish ASR decision.
    assert worker.execute(env.request, env.folder) is None
    assert [call[0] for call in env.calls].count("asr") == 1
    assert path.read_bytes() == raw


def test_incomplete_asr_has_no_completed_decision_checkpoint(prepared):
    env = prepared
    env.fail_segments = True
    with pytest.raises(RuntimeError, match="unfinished ASR"):
        transcribe(env)
    assert not (env.folder / "transcription.json").exists()
    env.fail_segments = False
    assert isinstance(transcribe(env), list)
    assert [call[0] for call in env.calls].count("asr") == 2


def test_auto_engine_version_can_remain_unknown(prepared, monkeypatch):
    env = prepared

    def missing(*args):
        raise worker.importlib.metadata.PackageNotFoundError("faster-whisper")

    monkeypatch.setattr(worker.importlib.metadata, "version", missing)
    assert isinstance(transcribe(env), list)
    assert saved(env)["language_decision"]["engine"]["version"] is None


def test_auto_new_asr_rejects_unusable_generated_rows_before_publication(prepared):
    env = prepared
    env.rows = [SimpleNamespace(text="Invalid negative interval", start=-1.0, end=1.0)]
    with pytest.raises(ValueError, match="usable normalized bounded segments"):
        transcribe(env)
    assert not (env.folder / "transcription.json").exists()


@pytest.mark.parametrize(
    "tamper",
    [
        "missing",
        "status",
        "probability",
        "huge_probability",
        "duration",
        "count",
        "count_bool",
        "policy",
        "policy_type",
        "extra",
        "window",
        "detector_window",
        "choice",
        "pin",
        "request",
        "audio",
        "engine",
        "configured_evidence",
        "language",
    ],
)
def test_cached_auto_decision_tampering_fails_closed_before_any_inference(prepared, tamper):
    env = prepared
    transcribe(env)
    data = saved(env)
    decision = data["language_decision"]
    if tamper == "missing":
        del data["language_decision"]
    elif tamper == "status":
        decision["status"] = "unsupported"
    elif tamper == "probability":
        decision["probability"] = -0.5
    elif tamper == "huge_probability":
        decision["probability"] = 10**400
    elif tamper == "duration":
        decision["duration_after_vad"] = 0.0
    elif tamper == "count":
        decision["segment_count"] = 0
    elif tamper == "count_bool":
        decision["segment_count"] = True
    elif tamper == "policy":
        decision["policy"]["min_probability"] = 0.1
    elif tamper == "policy_type":
        decision["policy"]["language_detection_segments"] = True
    elif tamper == "extra":
        decision["private_path"] = "C:/private/interview.txt"
    elif tamper == "window":
        decision["window"]["end"] = 2.0
    elif tamper == "detector_window":
        decision["detector_window"] = {"start": 0.0, "end": 3.0}
    elif tamper == "choice":
        decision["model"]["choice"] = "medium"
    elif tamper == "pin":
        decision["model"]["revision"] = "invalid"
    elif tamper == "request":
        decision["request_sha256"] = "0" * 64
    elif tamper == "audio":
        decision["input_sha256"] = "0" * 64
    elif tamper == "engine":
        decision["engine"]["version"] = "private/value"
    elif tamper == "configured_evidence":
        data["language_evidence"] = []
    elif tamper == "language":
        decision["effective"] = "private/value"
    worker.write_json(env.folder / "transcription.json", data)
    before = len(env.calls)
    with pytest.raises(ValueError):
        worker.transcribe_audio(None, env.options, env.folder, None, None)
    assert len(env.calls) == before


@pytest.mark.parametrize("changed", ["wav", "float"])
def test_new_auto_asr_refuses_stale_prepared_samples(prepared, changed):
    env = prepared
    mapped = worker.audio_data(env.folder)
    if changed == "wav":
        write_wave(env.folder / "audio.wav", -1000)
    else:
        mapped[0] = -0.5
        mapped.flush()
    with pytest.raises(ValueError, match="decoded input"):
        transcribe(env, audio=mapped)
    assert not any(call[0] in ("asr", "model") for call in env.calls)


@pytest.mark.parametrize(
    "row",
    [
        {"text": "", "start": 0.0, "end": 0.5},
        {"text": "  ", "start": 0.0, "end": 0.5},
        {"text": " Leading", "start": 0.0, "end": 0.5},
        {"text": None, "start": 0.0, "end": 0.5},
        {"text": "Words", "start": True, "end": 2.0},
        {"text": "Words", "start": 0.0, "end": 10**400},
        {"text": "Words", "start": 0.0, "end": float("nan")},
        {"text": "Words", "start": -1.0, "end": 0.5},
        {"text": "Words", "start": 0.0, "end": 0.01},
        None,
    ],
)
def test_cached_auto_requires_usable_normalized_bounded_rows(prepared, row):
    env = prepared
    transcribe(env)
    data = saved(env)
    data["segments"] = [row]
    # Deliberately permit malformed JSON's NaN here to exercise the cached input boundary.
    path = env.folder / "transcription.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    before = len(env.calls)
    with pytest.raises(ValueError):
        worker.transcribe_audio(None, env.options, env.folder, None, None)
    assert len(env.calls) == before


@pytest.mark.parametrize("rows", [None, {}, "private text"])
def test_cached_auto_requires_a_segment_list(prepared, rows):
    env = prepared
    transcribe(env)
    data = saved(env)
    data["segments"] = rows
    worker.write_json(env.folder / "transcription.json", data)
    with pytest.raises(ValueError, match="usable normalized bounded segments"):
        worker.transcribe_audio(None, env.options, env.folder, None, None)


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize(
    "changed", ["language", "whisper_model", "transcribe", "retranscribe", "passages", "duration"]
)
def test_auto_request_binding_checks_frozen_language_model_and_operation(prepared, cached, changed):
    env = prepared
    if cached:
        transcribe(env)
    request = copy.deepcopy(env.request)
    if changed == "passages":
        request["passages"] = [{"id": "0", "text": "", "start": 0.0, "end": 1.0}]
    else:
        request["options"][changed] = {
            "language": "da",
            "whisper_model": "medium",
            "transcribe": False,
            "retranscribe": True,
            "duration": 4.0,
        }[changed]
    worker.write_json(env.folder / "request.json", request)
    if cached:
        data = saved(env)
        data["language_decision"]["request_sha256"] = hashlib.sha256(
            (env.folder / "request.json").read_bytes()
        ).hexdigest()
        worker.write_json(env.folder / "transcription.json", data)
    before = len(env.calls)
    with pytest.raises(ValueError):
        transcribe(env)
    assert not any(call[0] in ("model", "asr") for call in env.calls[before:])


@pytest.mark.parametrize("stage", ["transcription", "retranscription"])
def test_fixed_requests_refuse_auto_decisions_but_legacy_cache_stays_exact(
    tmp_path, monkeypatch, stage
):
    key = "segments" if stage == "transcription" else "passages"
    row = {"id": "0", "text": "Legacy", "start": 0, "end": 1}
    path = tmp_path / (stage + ".json")
    raw = (json.dumps({key: [row]}, indent=2) + "\n").encode()
    path.write_bytes(raw)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    options = {"language": "da", "whisper_model": "small"}

    def run():
        if stage == "transcription":
            return worker.transcribe_audio(None, options, tmp_path, None, None)
        return worker.retranscribe_passages(
            None, {"passages": [row]}, options, tmp_path, None, None
        )

    assert run()[0]["text"] == "Legacy" and path.read_bytes() == raw
    worker.write_json(path, {key: [row], "language_decision": {}})
    with pytest.raises(ValueError, match="Automatic ASR decision"):
        run()


def test_pure_historical_decision_and_outcome_need_no_input_current_pins_or_runtime(
    prepared, monkeypatch
):
    env = prepared
    transcribe(env)
    decision = saved(env)["language_decision"]
    expected = hashlib.sha256(
        json.dumps(
            decision, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
    for name in ("audio.wav", "request.json", "audio.f32", "audio.f32.source.json"):
        (env.folder / name).unlink()
    monkeypatch.setattr(worker, "MODELS", {})
    monkeypatch.setattr(
        worker, "language_binding", lambda *a: pytest.fail("historical file lookup")
    )
    monkeypatch.setattr(
        worker.importlib.metadata, "version", lambda *a: pytest.fail("historical runtime lookup")
    )
    assert worker.validate_language_decision(decision) == decision
    assert worker.decision_digest(dict(reversed(list(decision.items())))) == expected
    outcome = {"schema": 1, "state": "aligned", "decision_sha256": expected}
    assert worker.validate_language_outcome(outcome, decision, align=True) == outcome
    clean = worker.validate_language_decision(decision)
    clean["model"]["repo"] = "different/repo"
    assert clean != decision and worker.validate_language_decision(decision) == decision


@pytest.mark.parametrize(
    "changed", ["start", "end", "huge_end", "nan_end", "private", "probability", "status"]
)
def test_pure_historical_decision_rejects_unproducible_or_private_metadata(prepared, changed):
    env = prepared
    transcribe(env)
    decision = saved(env)["language_decision"]
    if changed == "start":
        decision["window"]["start"] = 1.0
    elif changed == "end":
        decision["window"]["end"] = 21601.0
    elif changed == "huge_end":
        decision["window"]["end"] = 10**400
    elif changed == "nan_end":
        decision["window"]["end"] = float("nan")
    elif changed == "private":
        decision["private_path"] = "SHOULD_NOT_LEAK"
    elif changed == "probability":
        decision["probability"] = float("inf")
    elif changed == "status":
        decision["status"] = "unsupported"
    with pytest.raises(ValueError) as error:
        worker.validate_language_decision(decision)
    assert "SHOULD_NOT_LEAK" not in str(error.value)


@pytest.mark.parametrize(
    "changed",
    ["schema_bool", "state", "digest", "extra", "not_requested", "unavailable", "align_type"],
)
def test_outcome_is_strictly_typed_state_consistent_and_decision_bound(prepared, changed):
    env = prepared
    transcribe(env)
    decision = saved(env)["language_decision"]
    outcome = {"schema": 1, "state": "aligned", "decision_sha256": worker.decision_digest(decision)}
    align = True
    if changed == "schema_bool":
        outcome["schema"] = True
    elif changed == "state":
        outcome["state"] = ["aligned"]
    elif changed == "digest":
        outcome["decision_sha256"] = "0" * 64
    elif changed == "extra":
        outcome["private_note"] = "SHOULD_NOT_LEAK"
    elif changed == "not_requested":
        outcome["state"] = "not_requested"
    elif changed == "unavailable":
        outcome["state"] = "unavailable_language"
    elif changed == "align_type":
        align = 1
    with pytest.raises(ValueError) as error:
        worker.validate_language_outcome(outcome, decision, align=align)
    assert "SHOULD_NOT_LEAK" not in str(error.value)


def test_metadata_distinguishes_pending_requested_and_unrequested_alignment(prepared):
    env = prepared
    transcribe(env)
    metadata = worker.auto_transcription_metadata(env.folder, env.options)
    assert set(metadata) == {"language_decision"}
    with pytest.raises(ValueError, match="outcome is missing"):
        worker.auto_transcription_metadata(env.folder, env.options, require_alignment=True)


@pytest.mark.parametrize("language", ["da", "fr"])
def test_unrequested_alignment_has_derived_outcome_and_independent_speakers(prepared, language):
    env = prepared
    env.options.update(align=False, diarize=True)
    env.info.language = language
    worker.write_json(env.folder / "request.json", env.request)
    assert worker.execute(env.request, env.folder) is None
    assert not (env.folder / "alignment.json").exists()
    assert (env.folder / "diarization.json").exists()
    metadata = worker.auto_transcription_metadata(env.folder, env.options, require_alignment=True)
    assert metadata["alignment_outcome"] == {
        "schema": 1,
        "state": "not_requested",
        "decision_sha256": worker.decision_digest(metadata["language_decision"]),
    }
    # Unrequested artifacts are preserved and ignored, including unreadable unrelated bytes.
    (env.folder / "alignment.json").write_text("SHOULD_NOT_LEAK invalid JSON", encoding="utf-8")
    assert worker.auto_transcription_metadata(env.folder, env.options) == metadata
    assert "SHOULD_NOT_LEAK" not in json.dumps(metadata)
    assert not any(call[0] in ("align", "align_model", "punkt") for call in env.calls)


@pytest.mark.parametrize("field", ["align", "diarize"])
def test_metadata_cannot_relabel_the_frozen_requested_stages(prepared, field):
    env = prepared
    transcribe(env)
    options = dict(env.options, **{field: not env.options[field]})
    with pytest.raises(ValueError, match="frozen full-transcription request"):
        worker.auto_transcription_metadata(env.folder, options)


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_outcome",
        "outcome_digest",
        "state",
        "passage_id",
        "passage_order",
        "token",
        "token_order",
        "word_start",
        "word_end",
        "word_score",
        "missing_null",
        "extra_word_key",
    ],
)
def test_unavailable_alignment_caches_require_exact_original_tokens_and_literal_nulls(
    prepared, tamper
):
    env = prepared
    env.info.language = "fr"
    env.rows.append(SimpleNamespace(text=" Anden sætning.", start=2.2, end=2.8))
    assert worker.execute(env.request, env.folder) is None
    path = env.folder / "alignment.json"
    alignment = json.loads(path.read_bytes())
    word = alignment["passages"][0]["words"][0]
    if tamper == "missing_outcome":
        del alignment["language_outcome"]
    elif tamper == "outcome_digest":
        alignment["language_outcome"]["decision_sha256"] = "0" * 64
    elif tamper == "state":
        alignment["language_outcome"]["state"] = "aligned"
    elif tamper == "passage_id":
        alignment["passages"][0]["id"] = "other"
    elif tamper == "passage_order":
        alignment["passages"].reverse()
    elif tamper == "token":
        word["word"] = "different"
    elif tamper == "token_order":
        alignment["passages"][0]["words"].reverse()
    elif tamper.startswith("word_"):
        word[tamper[5:]] = False if tamper == "word_start" else 0.0
    elif tamper == "missing_null":
        del word["score"]
    elif tamper == "extra_word_key":
        word["private_note"] = "SHOULD_NOT_LEAK"
    worker.write_json(path, alignment)
    raw = path.read_bytes()
    before = len(env.calls)
    with pytest.raises(ValueError):
        worker.auto_transcription_metadata(env.folder, env.options, require_alignment=True)
    with pytest.raises(ValueError):
        worker.execute(env.request, env.folder)
    assert path.read_bytes() == raw
    assert not any(
        call[0] in ("model", "asr", "align", "align_model", "speakers")
        for call in env.calls[before:]
    )


def test_cached_supported_alignment_cannot_gain_a_retroactive_binding(prepared):
    env = prepared
    assert worker.execute(env.request, env.folder) is None
    path = env.folder / "alignment.json"
    alignment = json.loads(path.read_bytes())
    del alignment["language_outcome"]
    worker.write_json(path, alignment)
    raw = path.read_bytes()
    before = len(env.calls)
    with pytest.raises(ValueError, match="bound outcome"):
        worker.execute(env.request, env.folder)
    assert path.read_bytes() == raw and not any(call[0] == "asr" for call in env.calls[before:])


def test_bound_metadata_finalizes_without_runtime_or_current_pin_and_keeps_coverage_separate(
    prepared, monkeypatch
):
    env = prepared
    assert worker.execute(env.request, env.folder) is None
    path = env.folder / "alignment.json"
    alignment = json.loads(path.read_bytes())
    for word in alignment["passages"][0]["words"]:
        word.update(start=None, end=None, score=None)
    worker.write_json(path, alignment)
    frozen_model = tuple(saved(env)["language_decision"]["model"][k] for k in ("repo", "revision"))
    for name in ("audio.f32", "audio.f32.source.json"):
        (env.folder / name).unlink()
    for name in ("torch", "whisperx", "faster_whisper"):
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setattr(worker, "MODELS", {})
    monkeypatch.setattr(
        worker.importlib.metadata, "version", lambda *a: pytest.fail("cached runtime check")
    )
    metadata = worker.auto_transcription_metadata(
        env.folder, env.options, model=frozen_model, require_alignment=True
    )
    assert metadata["alignment_outcome"]["state"] == "aligned"
    assert "word_timing" not in metadata  # Stage execution is separate from measured coverage.
    with pytest.raises(ValueError, match="frozen model revision"):
        worker.auto_transcription_metadata(env.folder, env.options, model=("other/repo", "0" * 40))


@pytest.mark.parametrize("name", ["request.json", "transcription.json", "alignment.json"])
def test_auto_metadata_size_guard_refuses_before_reading_oversized_files(
    prepared, monkeypatch, name
):
    env = prepared
    assert worker.execute(env.request, env.folder) is None
    monkeypatch.setattr(worker, "AUTO_JSON_LIMIT", 2048)
    path = env.folder / name
    raw = path.read_bytes()
    assert len(raw) <= worker.AUTO_JSON_LIMIT
    path.write_bytes(raw + b" " * (worker.AUTO_JSON_LIMIT + 1 - len(raw)))
    real_open = type(path).open

    def guarded_open(candidate, *args, **kwargs):
        if candidate == path:
            pytest.fail("oversized Auto metadata was opened before the size guard")
        return real_open(candidate, *args, **kwargs)

    monkeypatch.setattr(type(path), "open", guarded_open)
    with pytest.raises(ValueError, match="size limit"):
        worker.auto_transcription_metadata(env.folder, env.options, require_alignment=True)
    assert path.exists()  # Refusal never removes the original stage or implies cleanup.


def test_auto_metadata_capped_read_refuses_growth_without_a_large_allocation(monkeypatch):
    monkeypatch.setattr(worker, "AUTO_JSON_LIMIT", 16)
    reads = []

    class GrowingFile:
        def stat(self):
            return SimpleNamespace(st_size=1)

        def open(self, mode):
            assert mode == "rb"
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            reads.append(size)
            return b" " * size

    with pytest.raises(ValueError, match="size limit"):
        worker._read_auto_bytes(GrowingFile())
    assert reads == [17]
