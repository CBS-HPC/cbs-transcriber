"""Standalone JSON worker for an optional WhisperX environment; no app dependencies."""

import argparse
import copy
import gc
import hashlib
import importlib.metadata
import io
import json
import math
import os
import platform
import re
import sys
import unicodedata
import wave
from array import array
from bisect import bisect_left, bisect_right
from contextlib import redirect_stderr, redirect_stdout
from difflib import SequenceMatcher
from pathlib import Path
from types import ModuleType

WHISPERX_VERSION = "3.8.6"
ALIGNMENT_CACHE = None
CACHE_ALIGNMENT = False
RUNTIME_CACHE = None
MAX_WINDOW_SECONDS = 60
MAX_WINDOW_WORDS = 120
MAX_WINDOW_CHARS = 1200
MAX_WORDS = 100_000
MODELS = {
    "coarse": ("Systran/faster-whisper-small", "536b0662742c02347bc0e980a01041f333bce120"),
    "da": ("saattrupdan/wav2vec2-xls-r-300m-ftspeech", "7a60985d596e7a9e26b01a0707fe59daac6ffe84"),
    "en": ("facebook/wav2vec2-base-960h", "22aad52d435eb6dbaf354bdad9b0da84ce7d6156"),
    "speakers": (
        "pyannote/speaker-diarization-community-1",
        "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee",
    ),
    # Full transcription (T70); MIT-licensed CTranslate2 conversions, pinned like the rest.
    "whisper-medium": ("Systran/faster-whisper-medium", "08e178d48790749d25932bbc082711ddcfdfbc4f"),
    "whisper-large-v3-turbo": (
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
    ),
    "whisper-large-v3": (
        "Systran/faster-whisper-large-v3",
        "edaa852ec7e145841d8ffdb056a99866b5f0a478",
    ),
    "edda-turbo": (
        "danish-foundation-models/edda-v0.2",
        "698f8d04b5970f963d31c4079c05a76fa7f4f5bb",
    ),
    "edda-large": (
        "danish-foundation-models/edda-v0.2-large",
        "796bf033ce51d03bfe793844fa3412e72997b826",
    ),
    "roest-v3": (
        "CoRal-project/roest-v3-whisper-1.5b",
        "7182cced29631ef7d7776eb8c01f24624b9b9b04",
    ),
}
# Transcription model choice -> pinned snapshot; "small" reuses the coarse locator model.
WHISPER_MODELS = {
    "small": "coarse",
    "medium": "whisper-medium",
    "large-v3-turbo": "whisper-large-v3-turbo",
    "large-v3": "whisper-large-v3",
    "edda-turbo": "edda-turbo",
    "edda-large": "edda-large",
    "roest-v3": "roest-v3",
}
DANISH_CHOICES = frozenset({"edda-turbo", "edda-large", "roest-v3"})
# Standalone workers use python -I: load only these exact sibling source bytes, never pycache.
DANISH_HELPER_SHA256 = "3bbc29aa00f0b6e5abfd7bf9a1cd1e261da630480b338928b2b0321221d4577d"
_DANISH_MODULE = None


class RuntimeModelSlot:
    """One engine object, scoped to a finite allocation cohort; never a disk cache."""

    def __init__(self, receipt):
        self.identity = self.value = None
        self.receipt = receipt
        self.loads = self.hits = 0
        self.release_failed = False

    def clear(self):
        previous = self.value
        self.identity = self.value = None
        unload = getattr(getattr(previous, "model", None), "unload_model", None)
        try:
            if callable(unload):
                unload(to_cpu=False)
        except BaseException:
            self.release_failed = True
            raise
        finally:
            previous = unload = None
            gc.collect()
            torch = sys.modules.get("torch")
            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()

    def get(self, identity, factory):
        if self.release_failed:
            raise RuntimeError("The resident engine could not be released.")
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if self.identity == encoded:
            self.hits += 1
            return self.value
        self.clear()
        try:
            value = factory()
            self.value = value
            self.receipt(identity, self.loads + 1)
        except BaseException:
            self.clear()
            raise
        self.identity, self.value = encoded, value
        self.loads += 1
        return value


def runtime_model(kind, key, options, factory, *, conversion=None):
    if RUNTIME_CACHE is None:
        return factory()
    identity = {
        "engine": kind,
        "model": list(MODELS[key]),
        "device": options["device"],
        "compute_type": "int8" if options["device"] == "cpu" else "float16",
        "whisperx_version": WHISPERX_VERSION,
        "conversion": conversion,
    }
    return RUNTIME_CACHE.get(identity, factory)


def danish_module():
    """Load the pinned conversion/receipt validator without importing inference packages."""
    global _DANISH_MODULE
    if _DANISH_MODULE is None:
        path = Path(__file__).with_name("danish_models.py")
        raw = path.read_bytes().replace(b"\r\n", b"\n")
        if len(raw) > 256 * 1024 or hashlib.sha256(raw).hexdigest() != DANISH_HELPER_SHA256:
            raise ValueError("The Danish conversion helper does not match this worker.")
        module = ModuleType("cbs_danish_conversion")
        module.__file__ = str(path)
        exec(compile(raw, str(path), "exec"), module.__dict__)
        _DANISH_MODULE = module
    return _DANISH_MODULE


def danish_scope(options, passages=None):
    """Converted candidates support only explicitly Danish full-recording ASR."""
    return options.get("whisper_model") not in DANISH_CHOICES or (
        options.get("language") == "da"
        and options.get("transcribe") is True
        and options.get("retranscribe") is False
        and (passages is None or passages == [])
    )


MAX_SEGMENTS = 20_000
AUTO_JSON_LIMIT = 32 * 1024 * 1024
# Re-transcription context around each passage; only words centred inside it are kept.
PAD_SECONDS = 0.5
# Experimental abstention policy, not a calibrated accuracy claim or a user-facing default.
AUTO_LANGUAGE_POLICY = {
    "id": "faster-whisper-vad-info-v1",
    "experimental": True,
    "min_probability": 0.8,
    "language_detection_segments": 1,
    "language_detection_threshold": 0.5,
}
CLIP_LANGUAGE_POLICY = {
    "id": "faster-whisper-first-clip-v1",
    "experimental": True,
    "min_probability": 0.8,
    "vad_filter": False,
    "language_detection_segments": 1,
    "language_detection_threshold": 0.5,
    "sampling_rate": 16000,
    "max_samples": 480000,
}


def file_hash(path):
    """Hash one stable file without loading its audio into memory."""
    before = path.stat()
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ino,
    ):
        raise ValueError("The processing input changed while its identity was checked.")
    return digest


def language_binding(directory):
    """Identity of the frozen request and prepared WAV container, never a private path."""
    audio = directory / "audio.wav"
    before = audio.stat()
    digest = file_hash(audio)
    with wave.open(str(audio), "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (16000, 1, 2):
            raise ValueError("Language evidence requires the prepared 16 kHz mono PCM input.")
        frames = source.getnframes()
    after = audio.stat()
    if not 0 < frames <= 16000 * 6 * 3600 or (
        before.st_size,
        before.st_mtime_ns,
        before.st_ino,
    ) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError("The prepared input changed while language evidence was bound.")
    return {
        "input_sha256": digest,
        "request_sha256": file_hash(directory / "request.json"),
        "audio_frames": frames,
    }


def _language_input_binding(audio, directory):
    """Verify a new inference's mapped floats still belong to its WAV container."""
    binding = language_binding(directory)
    filename = getattr(audio, "filename", None)
    if filename is not None:
        path = directory / "audio.f32"
        try:
            marker = json.loads((directory / "audio.f32.source.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            raise ValueError("The decoded input has no verified source binding.") from None
        try:
            view = memoryview(audio)
            if (
                view.ndim != 1
                or view.itemsize != 4
                or not view.c_contiguous
                or view.nbytes != binding["audio_frames"] * 4
                or view.format not in ("f", "<f")
                or (view.format == "f" and sys.byteorder != "little")
            ):
                raise ValueError("The mapped inference input has an invalid shape.")
            data = view.cast("B")
            actual = hashlib.sha256()
            for offset in range(0, len(data), 4 * 1024 * 1024):
                actual.update(data[offset : offset + 4 * 1024 * 1024])
        except (TypeError, BufferError):
            raise ValueError("The mapped inference input cannot be verified.") from None
        if (
            Path(filename).resolve() != path.resolve()
            or not isinstance(marker, dict)
            or set(marker) != {"schema", "input_sha256", "audio_frames", "decoded_sha256"}
            or type(marker.get("schema")) is not int
            or marker["schema"] != 1
            or marker.get("input_sha256") != binding["input_sha256"]
            or marker.get("audio_frames") != binding["audio_frames"]
            or path.stat().st_size != binding["audio_frames"] * 4
            or marker.get("decoded_sha256") != file_hash(path)
            or marker.get("decoded_sha256") != actual.hexdigest()
        ):
            raise ValueError("The decoded input no longer belongs to the prepared WAV.")
    return binding


def language_windows(binding, *, passages=None, duration=None):
    """Actual ASR slices in the prepared WAV's timebase, shared by producer and collector."""
    frames = binding["audio_frames"]
    if passages is None:
        return [
            {
                "scope": "recording",
                "window": {"start": 0.0, "end": frames / 16000, "timebase": "prepared_audio"},
            }
        ]
    if duration is None:
        duration = frames / 16000
    if type(duration) not in {int, float} or not 0 < duration <= 720 * 60:
        raise ValueError("Invalid prepared-audio duration for language evidence.")
    windows = []
    for passage in passages:
        if (
            not isinstance(passage, dict)
            or any(type(passage.get(k)) not in {int, float} for k in ("start", "end"))
            or not 0 <= passage["start"] < passage["end"] <= duration
        ):
            raise ValueError("Invalid bounded passage for language evidence.")
        first = min(frames, int(max(0.0, passage["start"] - PAD_SECONDS) * 16000))
        last = min(frames, int(min(duration, passage["end"] + PAD_SECONDS) * 16000))
        if not 0 <= first < last <= frames:
            raise ValueError("A passage has no bounded prepared-audio language window.")
        windows.append(
            {
                "scope": "passage",
                "passage_id": passage["id"],
                "window": {
                    "start": first / 16000,
                    "end": last / 16000,
                    "timebase": "prepared_audio",
                },
            }
        )
    return windows


def validate_language_evidence(
    entries, binding=None, *, requested=None, model=None, model_choice=None, windows=None
):
    """Validate captured evidence without importing or checking an inference runtime."""
    if not isinstance(entries, list) or not 0 < len(entries) <= 200:
        raise ValueError("Language evidence must contain a bounded nonempty receipt list.")
    required = {
        "schema",
        "requested",
        "effective",
        "origin",
        "probability",
        "scope",
        "window",
        "input_sha256",
        "request_sha256",
        "model",
        "engine",
    }
    if windows is not None and len(entries) != len(windows):
        raise ValueError("Language evidence does not cover the exact requested ASR windows.")
    identities, scopes, passage_ids = set(), set(), set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError("Invalid language evidence receipt.")
        scope = entry.get("scope")
        allowed = required | ({"passage_id"} if scope == "passage" else set())
        if "reported_language" in entry:
            allowed.add("reported_language")
        pin, engine, window = entry.get("model"), entry.get("engine"), entry.get("window")
        if (
            set(entry) != allowed
            or type(entry.get("schema")) is not int
            or entry["schema"] != 1
            or entry.get("requested") not in ("da", "en")
            or entry.get("effective") != entry["requested"]
            or entry.get("origin") != "configured"
            or entry.get("probability") is not None
            or scope not in ("recording", "passage")
            or not isinstance(pin, dict)
            or set(pin) != {"choice", "repo", "revision"}
            or pin.get("choice") not in WHISPER_MODELS
            or (pin.get("choice") in DANISH_CHOICES and entry.get("requested") != "da")
            or not isinstance(pin.get("repo"), str)
            or not isinstance(pin.get("revision"), str)
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", pin["repo"]
            )
            or not re.fullmatch(r"[a-f0-9]{40}", pin["revision"])
            or not isinstance(engine, dict)
            or set(engine) != {"name", "version"}
            or engine.get("name") != "faster-whisper"
            or (
                engine.get("version") is not None
                and (
                    not isinstance(engine["version"], str)
                    or len(engine["version"]) > 64
                    or not re.fullmatch(
                        r"[0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.+-]{0,32}", engine["version"]
                    )
                )
            )
            or not isinstance(window, dict)
            or set(window) != {"start", "end", "timebase"}
            or window.get("timebase") != "prepared_audio"
            or any(type(window.get(k)) not in {int, float} for k in ("start", "end"))
            or not 0 <= window["start"] < window["end"] <= 720 * 60
            or not all(math.isfinite(window[k]) for k in ("start", "end"))
            or any(
                not isinstance(entry.get(k), str) or not re.fullmatch(r"[a-f0-9]{64}", entry[k])
                for k in ("input_sha256", "request_sha256")
            )
            or (
                "reported_language" in entry
                and (
                    not isinstance(entry["reported_language"], str)
                    or not re.fullmatch(r"[a-z]{2,3}", entry["reported_language"])
                )
            )
        ):
            raise ValueError("Invalid configured-language evidence fields.")
        if scope == "recording" and len(entries) != 1:
            raise ValueError("Recording language evidence has exactly one ASR window.")
        if scope == "passage":
            identifier = entry["passage_id"]
            if (
                not isinstance(identifier, str)
                or not 0 < len(identifier) <= 100
                or identifier in passage_ids
                or window["end"] - window["start"] > 61 + 1 / 16000
            ):
                raise ValueError("Invalid or duplicate passage language evidence.")
            passage_ids.add(identifier)
        if requested is not None and entry["requested"] != requested:
            raise ValueError("Language evidence disagrees with the frozen language request.")
        if model is not None and (pin["repo"], pin["revision"]) != tuple(model):
            raise ValueError("Language evidence disagrees with the frozen model revision.")
        if model_choice is not None and pin["choice"] != model_choice:
            raise ValueError("Language evidence disagrees with the frozen model choice.")
        if binding is not None and any(
            entry[k] != binding[k] for k in ("input_sha256", "request_sha256")
        ):
            raise ValueError("Language evidence belongs to another prepared input or request.")
        if windows is not None and {k: entry[k] for k in windows[index]} != windows[index]:
            raise ValueError("Language evidence belongs to another prepared-audio window.")
        identities.add(
            (
                entry["requested"],
                entry["input_sha256"],
                entry["request_sha256"],
                pin["repo"],
                pin["revision"],
                pin["choice"],
                engine["version"],
            )
        )
        scopes.add(scope)
    if len(identities) != 1 or len(scopes) != 1:
        raise ValueError("A stage's language evidence must share one frozen input identity.")
    return copy.deepcopy(entries)


def validate_asr_conversion(saved, options, binding, *, model=None):
    """Check copied conversion proof; completed recovery never inspects today's model cache."""
    choice = options["whisper_model"]
    if choice not in DANISH_CHOICES:
        if "asr_conversion" in saved:
            raise ValueError("This model cannot reuse Danish conversion evidence.")
        return {}
    if not danish_scope(options) or "asr_conversion" not in saved:
        raise ValueError("Danish ASR requires its fixed-language conversion evidence.")
    evidence = validate_language_evidence(
        saved.get("language_evidence"),
        binding,
        requested="da",
        model=model,
        model_choice=choice,
        windows=language_windows(binding),
    )
    pin = evidence[0]["model"]
    receipt = danish_module().validate_evidence(
        saved["asr_conversion"],
        choice=choice,
        model=(pin["repo"], pin["revision"]),
        request_sha256=binding["request_sha256"],
        input_sha256=binding["input_sha256"],
    )
    return {"language_evidence": evidence, "asr_conversion": receipt}


def converted_transcription_metadata(directory, options, *, model=None, binding=None):
    """Validate a complete converted ASR stage from frozen input and captured receipts only."""
    if options.get("whisper_model") not in DANISH_CHOICES or not danish_scope(options):
        raise ValueError("This request is not fixed Danish full-recording ASR.")
    request = json.loads(_read_auto_bytes(directory / "request.json"))
    if (
        not isinstance(request, dict)
        or set(request) != {"options", "passages"}
        or request.get("options") != options
        or request.get("passages") != []
        or options.get("whole_recording") is not False
        or type(options.get("duration")) not in (int, float)
        or not math.isfinite(options["duration"])
        or not 0 < options["duration"] <= 6 * 3600
    ):
        raise ValueError("The Danish ASR checkpoint has another request scope.")
    saved = json.loads(_read_auto_bytes(directory / "transcription.json"))
    if not isinstance(saved, dict) or set(saved) != {
        "segments",
        "language_evidence",
        "asr_conversion",
    }:
        raise ValueError("The Danish ASR checkpoint has invalid fields.")
    binding = observed_language_binding(directory, binding)
    _validate_auto_rows(saved["segments"], binding)
    return validate_asr_conversion(saved, options, binding, model=model)


def _configured_language_evidence(binding, options, window, info):
    try:
        version = importlib.metadata.version("faster-whisper")
    except importlib.metadata.PackageNotFoundError:
        version = None
    repo, revision = MODELS[WHISPER_MODELS[options["whisper_model"]]]
    entry = {
        "schema": 1,
        "requested": options["language"],
        "effective": options["language"],
        "origin": "configured",
        "probability": None,
        **window,
        "input_sha256": binding["input_sha256"],
        "request_sha256": binding["request_sha256"],
        "model": {"choice": options["whisper_model"], "repo": repo, "revision": revision},
        "engine": {"name": "faster-whisper", "version": version},
    }
    reported = getattr(info, "language", None)
    if isinstance(reported, str) and re.fullmatch(r"[a-z]{2,3}", reported):
        entry["reported_language"] = reported
    return entry


def _bounded_observation(value, maximum):
    if type(value) in (int, float) and 0 <= value <= maximum and math.isfinite(value):
        return float(value)
    return None


def _language_status(decision):
    count, duration = decision["segment_count"], decision["duration_after_vad"]
    if duration == 0 and count == 0:
        return "no_speech"
    if (
        count == 0
        or duration in (None, 0)
        or decision["effective"] is None
        or decision["probability"] is None
        or decision["probability"] < AUTO_LANGUAGE_POLICY["min_probability"]
    ):
        return "uncertain"
    return "resolved" if decision["effective"] in ("da", "en") else "unsupported"


def validate_language_decision(
    value, binding=None, *, model_choice=None, segment_count=None, model=None
):
    """Validate a completed full-ASR decision without an inference runtime or current pin."""
    keys = {
        "schema",
        "requested",
        "effective",
        "origin",
        "scope",
        "window",
        "detector_window",
        "input_sha256",
        "request_sha256",
        "model",
        "engine",
        "policy",
        "status",
        "probability",
        "duration_after_vad",
        "segment_count",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Invalid Automatic ASR language decision.")
    # Validate the window before using its duration, including on historical metadata-only reads.
    identity = {
        k: value[k]
        for k in ("schema", "window", "input_sha256", "request_sha256", "model", "engine")
    } | {
        "requested": "da",
        "effective": "da",
        "origin": "configured",
        "probability": None,
        "scope": "recording",
    }
    validate_language_evidence(
        [identity],
        binding,
        model_choice=model_choice,
        model=model,
        windows=language_windows(binding) if binding is not None else None,
    )
    if value["window"]["start"] != 0 or value["window"]["end"] > 6 * 3600:
        raise ValueError("Automatic ASR requires the full prepared input window.")
    policy = value["policy"]
    duration = value["window"]["end"]
    if (
        value["requested"] != "auto"
        or value["origin"] != "asr_detection"
        or value["scope"] != "full_asr_input"
        or value["detector_window"] is not None
        or not isinstance(policy, dict)
        or set(policy) != set(AUTO_LANGUAGE_POLICY)
        or any(
            type(policy[k]) is not type(v) or policy[k] != v
            for k, v in AUTO_LANGUAGE_POLICY.items()
        )
        or (
            value["effective"] is not None
            and (
                not isinstance(value["effective"], str)
                or not re.fullmatch(r"[a-z]{2,3}", value["effective"])
            )
        )
        or any(
            value[k] is not None
            and (type(value[k]) is not float or _bounded_observation(value[k], maximum) is None)
            for k, maximum in (("probability", 1), ("duration_after_vad", duration))
        )
        or type(value["segment_count"]) is not int
        or not 0 <= value["segment_count"] <= MAX_SEGMENTS
        or (
            segment_count is not None
            and (type(segment_count) is not int or value["segment_count"] != segment_count)
        )
        or value["status"] != _language_status(value)
    ):
        raise ValueError("Invalid Automatic ASR language observations or outcome.")
    return copy.deepcopy(value)


def decision_digest(decision):
    clean = validate_language_decision(decision)
    raw = json.dumps(
        clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _language_outcome(decision, align):
    return {
        "schema": 1,
        "state": "not_requested"
        if not align
        else ("aligned" if decision["status"] == "resolved" else "unavailable_language"),
        "decision_sha256": decision_digest(decision),
    }


def validate_language_outcome(value, decision, *, align=None):
    decision = validate_language_decision(decision)
    if (
        (align is not None and type(align) is not bool)
        or not isinstance(value, dict)
        or set(value) != {"schema", "state", "decision_sha256"}
        or type(value.get("schema")) is not int
        or value["schema"] != 1
        or value.get("state") not in ("aligned", "unavailable_language", "not_requested")
        or value.get("decision_sha256") != decision_digest(decision)
        or (
            value["state"] != "not_requested"
            and value["state"] != _language_outcome(decision, True)["state"]
        )
        or (align is not None and value["state"] != _language_outcome(decision, align)["state"])
    ):
        raise ValueError("Invalid Automatic ASR alignment outcome.")
    return copy.deepcopy(value)


def _asr_language_decision(binding, options, info, count):
    entry = _configured_language_evidence(
        binding, dict(options, language="da"), language_windows(binding)[0], None
    )
    language = getattr(info, "language", None)
    entry.update(
        requested="auto",
        effective=language
        if isinstance(language, str) and re.fullmatch(r"[a-z]{2,3}", language)
        else None,
        origin="asr_detection",
        scope="full_asr_input",
        detector_window=None,
        probability=_bounded_observation(getattr(info, "language_probability", None), 1),
        duration_after_vad=_bounded_observation(
            getattr(info, "duration_after_vad", None), binding["audio_frames"] / 16000
        ),
        segment_count=count,
        policy=copy.deepcopy(AUTO_LANGUAGE_POLICY),
    )
    entry["status"] = _language_status(entry)
    return validate_language_decision(
        entry,
        binding,
        model_choice=options["whisper_model"],
        segment_count=count,
        model=MODELS[WHISPER_MODELS[options["whisper_model"]]],
    )


def _read_auto_bytes(path):
    if path.stat().st_size > AUTO_JSON_LIMIT:
        raise ValueError("Automatic ASR metadata exceeds the size limit.")
    with path.open("rb") as stream:
        raw = stream.read(AUTO_JSON_LIMIT + 1)
    if len(raw) > AUTO_JSON_LIMIT or path.stat().st_size > AUTO_JSON_LIMIT:
        raise ValueError("Automatic ASR metadata exceeds the size limit.")
    return raw


def _validate_auto_input(directory, binding, options, *, request_raw=None):
    raw = _read_auto_bytes(directory / "request.json") if request_raw is None else request_raw
    request = json.loads(raw.decode("utf-8"))
    frozen = request.get("options") if isinstance(request, dict) else None
    if (
        hashlib.sha256(raw).hexdigest() != binding["request_sha256"]
        or not isinstance(request, dict)
        or set(request) != {"options", "passages"}
        or request["passages"] != []
        or not isinstance(frozen, dict)
        or frozen.get("language") != "auto"
        or options.get("language") != "auto"
        or frozen.get("whisper_model") != options["whisper_model"]
        or frozen.get("transcribe") is not True
        or frozen.get("retranscribe") is not False
        or options.get("transcribe") is not True
        or options.get("retranscribe") is not False
        or type(options.get("duration")) not in (int, float)
        or type(frozen.get("duration")) not in (int, float)
        or not 0 < frozen["duration"] <= 720 * 60
        or frozen["duration"] != options["duration"]
        or any(
            type(frozen.get(k)) is not bool
            or type(options.get(k)) is not bool
            or frozen[k] != options[k]
            for k in ("align", "diarize")
        )
    ):
        raise ValueError("Automatic ASR does not match its frozen full-transcription request.")


def _validate_auto_rows(rows, binding):
    if (
        not isinstance(rows, list)
        or len(rows) > MAX_SEGMENTS
        or any(
            not isinstance(row, dict)
            or not isinstance(row.get("text"), str)
            or not row["text"]
            or row["text"] != row["text"].strip()
            or any(type(row.get(k)) not in (int, float) for k in ("start", "end"))
            or not 0 <= row["start"] < row["end"] <= binding["audio_frames"] / 16000
            or row["end"] - row["start"] < 0.05
            or not all(math.isfinite(row[k]) for k in ("start", "end"))
            for row in rows
        )
    ):
        raise ValueError("Automatic ASR requires usable normalized bounded segments.")


def observed_language_binding(directory, binding=None):
    """Internal caller-observed preparation; supplying it never proves a WAV was read here."""
    if binding is None:
        return language_binding(directory)
    if (
        not isinstance(binding, dict)
        or set(binding) != {"input_sha256", "request_sha256", "audio_frames"}
        or any(
            not isinstance(binding[k], str) or not re.fullmatch(r"[0-9a-f]{64}", binding[k])
            for k in ("input_sha256", "request_sha256")
        )
        or type(binding["audio_frames"]) is not int
        or not 0 < binding["audio_frames"] <= 16000 * 6 * 3600
        or hashlib.sha256(_read_auto_bytes(directory / "request.json")).hexdigest()
        != binding["request_sha256"]
    ):
        raise ValueError("Invalid observed prepared-input binding.")
    return dict(binding)


def _read_auto_transcription(directory, options, *, model=None, binding=None):
    if (directory / "alignment_language.json").exists():
        raise ValueError("A clip language decision cannot be reused as full ASR provenance.")
    saved = json.loads(_read_auto_bytes(directory / "transcription.json").decode("utf-8"))
    if not isinstance(saved, dict) or set(saved) != {"segments", "language_decision"}:
        raise ValueError("Automatic ASR needs its exact completed decision checkpoint.")
    request_raw = _read_auto_bytes(directory / "request.json")
    binding = observed_language_binding(directory, binding)
    _validate_auto_input(directory, binding, options, request_raw=request_raw)
    rows = saved["segments"]
    _validate_auto_rows(rows, binding)
    decision = validate_language_decision(
        saved["language_decision"],
        binding,
        model_choice=options["whisper_model"],
        segment_count=len(rows),
        model=model,
    )
    return rows, decision


def _asr_passages(rows):
    return [
        {"id": str(i), "text": row["text"], "start": row["start"], "end": row["end"]}
        for i, row in enumerate(rows)
    ]


def _untimed_alignment(passages, decision):
    return {
        "passages": [
            {
                "id": passage["id"],
                "words": [
                    {"word": token, "start": None, "end": None, "score": None}
                    for token in re.findall(r"\S+", passage["text"])
                ],
            }
            for passage in passages
        ],
        "language_outcome": _language_outcome(decision, True),
    }


def _validate_aligned_intervals(passages, rows, duration):
    for passage, row in zip(passages, rows, strict=True):
        previous = -1
        for word in passage["words"]:
            start, end, score = word["start"], word["end"], word["score"]
            try:
                valid = all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                    for value in (start, end, score)
                )
            except OverflowError:
                raise ValueError(
                    "Automatic ASR alignment returned unrepresentable numeric timing."
                ) from None
            if valid:
                if (
                    not 0 <= start <= end <= duration + 0.1
                    or start < previous
                    or start < row["start"] - 0.1
                    or end > row["end"] + 0.1
                ):
                    raise ValueError(
                        "Automatic ASR alignment returned invalid or unordered intervals."
                    )
                previous = start


def auto_transcription_metadata(
    directory, options, *, model=None, require_alignment=False, include_alignment=True, binding=None
):
    """Read source-bound Auto provenance without importing or checking an inference runtime."""
    if type(include_alignment) is not bool or (require_alignment and not include_alignment):
        raise ValueError("ASR-only metadata cannot bypass required alignment validation.")
    rows, decision = _read_auto_transcription(directory, options, model=model, binding=binding)
    metadata = {"language_decision": decision}
    if not include_alignment:
        return metadata
    if not options["align"]:
        metadata["alignment_outcome"] = _language_outcome(decision, False)
        return metadata
    path = directory / "alignment.json"
    if not path.exists():
        if require_alignment:
            raise ValueError("The requested Automatic ASR alignment outcome is missing.")
        return metadata
    alignment = json.loads(_read_auto_bytes(path).decode("utf-8"))
    if not isinstance(alignment, dict) or set(alignment) != {"passages", "language_outcome"}:
        raise ValueError("Automatic ASR alignment needs a bound outcome.")
    outcome = validate_language_outcome(alignment["language_outcome"], decision, align=True)
    if outcome["state"] == "unavailable_language":
        if alignment != _untimed_alignment(_asr_passages(rows), decision):
            raise ValueError("Unavailable alignment must preserve exact ASR tokens without timing.")
    else:
        passages = alignment["passages"]
        if (
            not isinstance(passages, list)
            or len(passages) != len(rows)
            or any(
                not isinstance(passage, dict)
                or set(passage) != {"id", "words"}
                or passage["id"] != str(index)
                or not isinstance(passage["words"], list)
                or len(passage["words"]) != len(re.findall(r"\S+", row["text"]))
                or any(
                    not isinstance(word, dict)
                    or set(word) != {"word", "start", "end", "score"}
                    or word["word"] != token
                    for word, token in zip(
                        passage["words"], re.findall(r"\S+", row["text"]), strict=True
                    )
                )
                for index, (passage, row) in enumerate(zip(passages, rows, strict=True))
            )
        ):
            raise ValueError("Automatic ASR alignment must preserve exact passage IDs and tokens.")
        _validate_aligned_intervals(passages, rows, options["duration"])
    metadata["alignment_outcome"] = outcome
    return metadata


def _clip_scope(request):
    allowed_options = {
        "execution",
        "language",
        "device",
        "align",
        "diarize",
        "transcribe",
        "retranscribe",
        "whole_recording",
        "duration",
        "allow_downloads",
        "download_models",
        "min_score",
        "timing_policy",
        "min_speakers",
        "max_speakers",
        "timeout_minutes",
        "whisper_model",
    }
    if not isinstance(request, dict) or set(request) != {"operation", "options", "passages"}:
        raise ValueError("Bounded alignment needs an explicit isolated worker request.")
    options, passages = request["options"], request["passages"]
    if (
        request["operation"] != "bounded_alignment"
        or not isinstance(options, dict)
        or set(options) - allowed_options
        or (
            "timing_policy" in options
            and options["timing_policy"] not in ("legacy-score-filter", "retain-estimates")
        )
        or options.get("execution") != "local"
        or options.get("language") != "auto"
        or options.get("device") not in ("cpu", "cuda")
        or options.get("align") is not True
        or any(
            options.get(key) is not False
            for key in (
                "transcribe",
                "retranscribe",
                "diarize",
                "whole_recording",
                "allow_downloads",
            )
        )
        or ("download_models" in options and options["download_models"] is not False)
        or type(options.get("duration")) not in (int, float)
        or not 0 < options["duration"] <= MAX_WINDOW_SECONDS
        or not isinstance(passages, list)
        or len(passages) != 1
    ):
        raise ValueError("Bounded alignment requires cached local processing of one short input.")
    passage = passages[0]
    if (
        not isinstance(passage, dict)
        or set(passage) != {"id", "text", "start", "end"}
        or not isinstance(passage.get("id"), str)
        or not 0 < len(passage["id"]) <= 100
        or not isinstance(passage.get("text"), str)
        or len(passage["text"]) > MAX_WINDOW_CHARS
        or not 0 < len(re.findall(r"\S+", passage["text"])) <= MAX_WINDOW_WORDS
        or any(type(passage.get(key)) not in (int, float) for key in ("start", "end"))
        or passage["start"] != 0
        or passage["end"] != options["duration"]
        or needs_coarse(passages, options["duration"])
    ):
        raise ValueError("Bounded alignment needs one complete known-bound text unit.")
    return options


def _clip_context(directory, request=None):
    frozen = json.loads(_read_auto_bytes(directory / "request.json").decode("utf-8"))
    options = _clip_scope(frozen)
    if request is not None:
        _clip_scope(request)
        if json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False) != json.dumps(
            frozen, sort_keys=True, ensure_ascii=False, allow_nan=False
        ):
            raise ValueError("The bounded alignment request differs from its frozen input.")
    path = directory / "audio.wav"
    if path.stat().st_size > 2 * 16000 * MAX_WINDOW_SECONDS + 65536:
        raise ValueError("The supplied bounded alignment audio is too large.")
    with wave.open(str(path), "rb") as source:
        frames = source.getnframes()
        if (
            (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (16000, 1, 2)
            or not 0 < frames <= 16000 * MAX_WINDOW_SECONDS
            or abs(frames - options["duration"] * 16000) > 1.000001
        ):
            raise ValueError(
                "Bounded alignment needs matching prepared audio of at most 60 seconds."
            )
    binding = language_binding(directory)
    if (
        binding["audio_frames"] != frames
        or path.stat().st_size > 2 * 16000 * MAX_WINDOW_SECONDS + 65536
    ):
        raise ValueError("The bounded alignment audio changed during scope validation.")
    return frozen, binding


def _clip_status(decision):
    if (
        decision["effective"] is None
        or decision["probability"] is None
        or decision["probability"] < CLIP_LANGUAGE_POLICY["min_probability"]
    ):
        return "uncertain"
    return "resolved" if decision["effective"] in ("da", "en") else "unsupported"


def validate_clip_language_decision(value, binding=None, *, model=None):
    keys = {
        "schema",
        "requested",
        "effective",
        "origin",
        "scope",
        "window",
        "detector_window",
        "input_sha256",
        "request_sha256",
        "model",
        "engine",
        "policy",
        "status",
        "probability",
        "text_compatibility",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Invalid bounded alignment language decision.")
    identity = {
        key: value[key]
        for key in ("schema", "window", "input_sha256", "request_sha256", "model", "engine")
    } | {
        "requested": "da",
        "effective": "da",
        "origin": "configured",
        "probability": None,
        "scope": "recording",
    }
    validate_language_evidence(
        [identity],
        binding,
        model_choice="small",
        model=model,
        windows=language_windows(binding) if binding is not None else None,
    )
    window, policy, detector_window = value["window"], value["policy"], value["detector_window"]
    frames = round(window["end"] * 16000)
    if (
        value["requested"] != "auto"
        or value["origin"] != "audio_detection"
        or value["scope"] != "bounded_prepared_input"
        or value["text_compatibility"] != "not_assessed"
        or window["start"] != 0
        or not 0 < window["end"] <= MAX_WINDOW_SECONDS
        or abs(frames - window["end"] * 16000) > 0.000001
        or not isinstance(detector_window, dict)
        or set(detector_window) != {"start", "end", "timebase"}
        or any(type(detector_window.get(key)) not in (int, float) for key in ("start", "end"))
        or detector_window
        != {"start": 0.0, "end": min(frames, 480000) / 16000, "timebase": "prepared_audio"}
        or not isinstance(policy, dict)
        or set(policy) != set(CLIP_LANGUAGE_POLICY)
        or any(
            type(policy[key]) is not type(expected) or policy[key] != expected
            for key, expected in CLIP_LANGUAGE_POLICY.items()
        )
        or (
            value["effective"] is not None
            and (
                not isinstance(value["effective"], str)
                or not re.fullmatch(r"[a-z]{2,3}", value["effective"])
            )
        )
        or (
            value["probability"] is not None
            and (
                type(value["probability"]) is not float
                or _bounded_observation(value["probability"], 1) is None
            )
        )
        or value["status"] != _clip_status(value)
    ):
        raise ValueError("Invalid bounded alignment language observations or scope.")
    return copy.deepcopy(value)


def clip_decision_digest(value):
    clean = validate_clip_language_decision(value)
    raw = json.dumps(
        clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _clip_artifacts(directory, *, new=False):
    names = ("windows.json", "transcription.json", "retranscription.json", "diarization.json")
    if (
        any((directory / name).exists() for name in names)
        or next(directory.glob("recognition-*.json"), None)
        or next(directory.glob("aligned-*.json"), None)
        or (new and (directory / "alignment.json").exists())
    ):
        raise ValueError("Bounded alignment refuses pre-existing unbound processing artifacts.")


def clip_alignment_metadata(directory, *, request=None, require_alignment=False):
    frozen, binding = _clip_context(directory, request)
    _clip_artifacts(directory)
    decision = validate_clip_language_decision(
        json.loads(_read_auto_bytes(directory / "alignment_language.json").decode("utf-8")), binding
    )
    metadata = {"clip_language_decision": decision}
    path = directory / "alignment.json"
    if not path.exists():
        if require_alignment:
            raise ValueError("The bounded alignment result is missing.")
        return metadata
    alignment = json.loads(_read_auto_bytes(path).decode("utf-8"))
    outcome = {
        "schema": 1,
        "state": "alignment_attempted",
        "decision_sha256": clip_decision_digest(decision),
    }
    passages = alignment.get("passages") if isinstance(alignment, dict) else None
    source = frozen["passages"][0]
    tokens = re.findall(r"\S+", source["text"])
    if (
        decision["status"] != "resolved"
        or not isinstance(alignment, dict)
        or set(alignment) != {"passages", "language_outcome"}
        or alignment["language_outcome"] != outcome
        or type(alignment["language_outcome"].get("schema")) is not int
        or not isinstance(passages, list)
        or len(passages) != 1
        or not isinstance(passages[0], dict)
        or set(passages[0]) != {"id", "words"}
        or passages[0]["id"] != source["id"]
        or not isinstance(passages[0]["words"], list)
        or len(passages[0]["words"]) != len(tokens)
        or any(
            not isinstance(word, dict)
            or set(word) != {"word", "start", "end", "score"}
            or word["word"] != token
            for word, token in zip(passages[0]["words"], tokens, strict=True)
        )
    ):
        raise ValueError("Bounded alignment needs exact source tokens and its own bound outcome.")
    previous = -1
    upper = min(frozen["options"]["duration"], decision["window"]["end"], source["end"]) + 0.1
    lower = max(0, source["start"] - 0.1)
    for word in passages[0]["words"]:
        for key in ("start", "end", "score"):
            value = word[key]
            if value is None:
                continue
            try:
                finite = type(value) in (int, float) and math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite or (key != "score" and not lower <= value <= upper):
                raise ValueError("Bounded alignment returned an invalid partial timing or score.")
        start, end = word["start"], word["end"]
        if start is not None:
            if start < previous or (end is not None and end < start):
                raise ValueError("Bounded alignment returned unordered partial word intervals.")
            previous = start
    metadata["alignment_outcome"] = outcome
    return metadata


def _detect_clip_language(audio, options, directory, binding, snapshot):
    from faster_whisper import WhisperModel

    location = snapshot("coarse")
    if not (Path(location) / "tokenizer.json").is_file():
        return "cache"
    model = WhisperModel(
        location,
        device=options["device"],
        local_files_only=True,
        compute_type="int8" if options["device"] == "cpu" else "float16",
    )
    geometry = {
        "sampling_rate": 16000,
        "n_samples": 480000,
        "nb_max_frames": 3000,
        "hop_length": 160,
    }
    extractor = getattr(model, "feature_extractor", None)
    if getattr(getattr(model, "model", None), "is_multilingual", None) is not True or any(
        type(getattr(extractor, key, None)) is not int or getattr(extractor, key) != expected
        for key, expected in geometry.items()
    ):
        return "clip_language_model"
    clip = audio[: min(binding["audio_frames"], 480000)].view()
    clip.flags.writeable = False
    language, probability, _ = model.detect_language(
        audio=clip,
        vad_filter=False,
        language_detection_segments=1,
        language_detection_threshold=0.5,
    )
    decision = _configured_language_evidence(
        binding,
        dict(options, language="da", whisper_model="small"),
        language_windows(binding)[0],
        None,
    )
    decision.update(
        requested="auto",
        effective=language
        if isinstance(language, str) and re.fullmatch(r"[a-z]{2,3}", language)
        else None,
        origin="audio_detection",
        scope="bounded_prepared_input",
        text_compatibility="not_assessed",
        detector_window={
            "start": 0.0,
            "end": min(binding["audio_frames"], 480000) / 16000,
            "timebase": "prepared_audio",
        },
        policy=copy.deepcopy(CLIP_LANGUAGE_POLICY),
        probability=_bounded_observation(probability, 1),
    )
    decision["status"] = _clip_status(decision)
    decision = validate_clip_language_decision(decision, binding, model=MODELS["coarse"])
    write_json(directory / "alignment_language.json", decision)
    return decision


def write_json(path, value):
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    try:
        if path.read_bytes() == encoded.encode("utf-8"):
            return  # Frozen retries need no replacement, including with Windows readers open.
    except FileNotFoundError:
        pass
    temporary = path.with_suffix(".pending")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def processing_environment(directory, stage, options, torch):
    """Observe a fixed worker-fact allowlist; never collect host names or environment vars."""
    import platform
    from importlib.metadata import PackageNotFoundError, version

    packages = {}
    allowed = (
        "whisperx",
        "faster-whisper",
        "ctranslate2",
        "torch",
        "torchaudio",
        "transformers",
        "pyannote.audio",
        "numpy",
    )
    release = re.compile(
        r"[0-9]+(?:\.[0-9]+){0,3}(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?"
        r"(?:\.dev[0-9]+)?(?:\+(?:cpu|cu[0-9]{2,3}|rocm[0-9]+(?:\.[0-9]+)*))?"
    )
    for package in allowed:
        try:
            observed = version(package)
            if len(observed) <= 64 and release.fullmatch(observed):
                packages[package] = observed
        except PackageNotFoundError:
            pass
    device = options["device"]
    index = torch.cuda.current_device() if device == "cuda" else None
    system, machine = platform.system(), platform.machine()
    write_json(
        directory / (stage + ".environment.json"),
        {
            "format": "cbs-processing-environment-v1",
            "python": {
                "implementation": sys.implementation.name,
                "version": platform.python_version(),
            },
            "platform": {"system": system, "machine": machine}
            if system in {"Windows", "Darwin", "Linux"}
            and machine in {"x86_64", "AMD64", "arm64", "aarch64", "x86"}
            else None,
            "packages": packages,
            "device": {
                "type": device,
                "index": index,
                "memory_bytes": torch.cuda.get_device_properties(index).total_memory
                if index is not None
                else None,
            },
            "code": {
                "application_sha256": None,
                "worker_sha256": file_hash(Path(__file__)),
                "lock_sha256": None,
            },
            "recipe": {
                "id": "whisperx-" + stage + "-v1",
                "compute_type": ("int8" if device == "cpu" else "float16")
                if stage == "transcription"
                else None,
            },
        },
    )


def detect_gpu():
    """Inspect the selected runtime, without loading models or contacting a provider."""
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            import torch

            available = torch.cuda.is_available()
            devices = (
                [
                    {
                        "name": str(torch.cuda.get_device_name(index))[:160],
                        "memory_gb": round(
                            torch.cuda.get_device_properties(index).total_memory / 1024**3, 1
                        ),
                    }
                    for index in range(min(torch.cuda.device_count(), 8))
                ]
                if available
                else []
            )
        return {"checked": True, "available": bool(available), "devices": devices}
    except Exception:
        return {"checked": False, "available": False, "devices": []}


def doctor(hardware=False):
    try:
        version = importlib.metadata.version("whisperx")
    except importlib.metadata.PackageNotFoundError:
        version = None
    unsupported = sys.platform == "darwin" and platform.machine() == "x86_64"
    return {
        "ready": not unsupported
        and version == WHISPERX_VERSION
        and (3, 10) <= sys.version_info[:2] < (3, 14),
        "unsupported_platform": unsupported,
        "version": version,
        "expected_version": WHISPERX_VERSION,
        "python": ".".join(map(str, sys.version_info[:3])),
        **({"gpu": detect_gpu()} if hardware else {}),
    }


SNAPSHOT_IGNORE = ["*.md", "*.py", "*.onnx", "*.h5", "*.msgpack", "*.ot"]


def hub_cache():
    if os.environ.get("HUGGINGFACE_HUB_CACHE"):
        return Path(os.environ["HUGGINGFACE_HUB_CACHE"])
    return Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"


def snapshot_folder(key, cache=None):
    if key in DANISH_CHOICES:
        return (cache or hub_cache()) / danish_module().relative_folder(key)
    repo, revision = MODELS[key]
    return (cache or hub_cache()) / ("models--" + repo.replace("/", "--")) / "snapshots" / revision


def cache_status():
    """Report pinned model/tokenizer presence only; never downloads or loads a model."""
    models = {}
    for key in MODELS:
        if key in DANISH_CHOICES:
            try:
                danish_module().prepared(key, cache=hub_cache(), verify=False)
                models[key] = True
            except (OSError, ValueError):
                models[key] = False
            continue
        folder = snapshot_folder(key)
        models[key] = folder.is_dir() and any(folder.iterdir())
        if key == "speakers":
            # The gated model is copied file by file; only a completed copy has the marker.
            models[key] = models[key] and (folder / ".cbs-complete").is_file()
    try:
        import nltk

        for language in ("danish", "english"):
            nltk.data.find(f"tokenizers/punkt_tab/{language}/")
        punkt = True
    except (ImportError, LookupError):
        punkt = False
    return {"models": models, "punkt_tab": punkt}


def prefetch(keys=()):
    """Prepare a reusable runtime cache with pinned public models (never the gated speakers)."""
    import nltk
    from huggingface_hub import snapshot_download

    for key in dict.fromkeys(("coarse", "da", "en", *keys)):
        if key == "speakers" or key not in MODELS:
            continue
        if key in DANISH_CHOICES:
            danish_module().prepare(key, cache=hub_cache())
            continue
        repo, revision = MODELS[key]
        snapshot_download(repo, revision=revision, token=False, ignore_patterns=SNAPSHOT_IGNORE)
    target = os.environ.get("NLTK_DATA")
    if not target or not nltk.download("punkt_tab", download_dir=target, quiet=True):
        raise RuntimeError("Tokenizer download failed")
    return cache_status()


def token(value):
    return re.sub(r"\W", "", unicodedata.normalize("NFKC", value).casefold())


def needs_coarse(passages, duration):
    if any(p["start"] is None or p["end"] is None for p in passages):
        text = " ".join(p["text"] for p in passages)
        return (
            duration > MAX_WINDOW_SECONDS
            or len(text) > MAX_WINDOW_CHARS
            or len(re.findall(r"\S+", text)) > MAX_WINDOW_WORDS
        )
    return any(
        len(re.findall(r"\S+", p["text"])) > MAX_WINDOW_WORDS
        or len(p["text"]) > MAX_WINDOW_CHARS
        or (
            duration > MAX_WINDOW_SECONDS
            if p["start"] is None or p["end"] is None
            else p["end"] - p["start"] > MAX_WINDOW_SECONDS
        )
        for p in passages
    )


def distinctive_pairs(left, right):
    """Only unique exact phrases; discard both sides of any ordering conflict."""

    def phrases(words):
        found = {}
        for index in range(len(words) - 3):
            key = tuple(words[index : index + 4])
            if len(set(key)) >= 3:
                found[key] = None if key in found else index
        return found

    first, second = phrases(left), phrases(right)
    candidates = sorted(
        (i, second[key])
        for key, i in first.items()
        if i is not None and second.get(key) is not None
    )
    minimum_after, minimum = [], math.inf
    for _, target in reversed(candidates):
        minimum_after.append(minimum)
        minimum = min(minimum, target)
    maximum, accepted = -1, set()
    for (a, b), following in zip(candidates, reversed(minimum_after), strict=True):
        if maximum < b < following:
            accepted.update((a + i, b + i) for i in range(4))
        maximum = max(maximum, b)
    by_word = {}
    for a, b in accepted:
        by_word.setdefault(a, set()).add(b)
    return sorted((a, next(iter(targets))) for a, targets in by_word.items() if len(targets) == 1)


def coarse_windows(flat, recognized, duration, bounds=None):
    """Derive bounded search intervals; ASR times never become published word times."""
    lexical = [(i, token(w)) for i, w in enumerate(flat) if token(w)]
    pairs = distinctive_pairs([w for _, w in lexical], [token(w["word"]) for w in recognized])
    anchors = [(lexical[a][0], recognized[b]) for a, b in pairs]
    windows = []
    for index, word in anchors:
        if len(flat[index]) > MAX_WINDOW_CHARS:
            continue
        if bounds:
            a, b = bounds[index]
            if (a is not None and word["start"] < a) or (b is not None and word["end"] > b):
                continue
        if windows:
            previous = windows[-1]
            gap = index - previous["last"] - 1
            if (
                0 <= gap <= 12
                and 0 <= word["start"] - previous["anchor_end"] <= 8
                and word["end"] - previous["start"] <= MAX_WINDOW_SECONDS - 1
                and index + 1 - previous["first"] <= MAX_WINDOW_WORDS
                and len(" ".join(flat[previous["first"] : index + 1])) <= MAX_WINDOW_CHARS
            ):
                previous.update(last=index, anchor_end=word["end"])
                continue
        windows.append(
            {"first": index, "last": index, "start": word["start"], "anchor_end": word["end"]}
        )
    for index, window in enumerate(windows):
        # Padding is a search bound around observed audio, never a word timestamp.
        before = windows[index - 1]["anchor_end"] if index else 0
        after = windows[index + 1]["start"] if index + 1 < len(windows) else duration
        window["end"] = min(duration, window["anchor_end"] + 0.4, after)
        window["start"] = max(0, window["start"] - 0.4, before)
    return windows


def alignment_windows(passages, recognized, duration):
    flat = [w for passage in passages for w in re.findall(r"\S+", passage["text"])]
    bounds = [(p["start"], p["end"]) for p in passages for _ in re.findall(r"\S+", p["text"])]
    coarse = coarse_windows(flat, recognized, duration, bounds)
    firsts, lasts = [w["first"] for w in coarse], [w["last"] for w in coarse]
    windows, cursor = [], 0
    for passage in passages:
        words = re.findall(r"\S+", passage["text"])
        finish = cursor + len(words) - 1
        a, b = passage["start"], passage["end"]
        if (
            a is not None
            and b is not None
            and 0 < b - a <= MAX_WINDOW_SECONDS
            and len(words) <= MAX_WINDOW_WORDS
            and len(passage["text"]) <= MAX_WINDOW_CHARS
        ):
            windows.append({"first": cursor, "last": finish, "start": a, "end": b})
        else:
            for window in coarse[bisect_left(lasts, cursor) : bisect_right(firsts, finish)]:
                first, last = max(cursor, window["first"]), min(finish, window["last"])
                start = max(a if a is not None else 0, window["start"])
                end = min(b if b is not None else duration, window["end"])
                if first <= last and start < end:
                    windows.append({"first": first, "last": last, "start": start, "end": end})
        cursor = finish + 1
    return windows


def recognize_windows(model, audio, options, directory, progress):
    """Thirty-second inputs with two seconds of context at each internal edge."""
    rows = []
    duration = options["duration"]
    offsets = list(range(0, math.ceil(duration), 26))
    for index, offset in enumerate(offsets):
        path = directory / f"recognition-{index:05d}.json"
        end = min(offset + 30, duration)
        if path.exists():
            words = json.loads(path.read_text(encoding="utf-8"))
        else:
            segments, _ = model.transcribe(
                audio[int(offset * 16000) : int(end * 16000)],
                language=options["language"],
                word_timestamps=True,
                condition_on_previous_text=False,
                vad_filter=False,
                beam_size=3,
                temperature=0,
            )
            words = []
            for segment in segments:
                for word in segment.words or []:
                    a, b, confidence = word.start, word.end, word.probability
                    if (
                        all(math.isfinite(v) for v in (a, b, confidence))
                        and 0 <= a < b <= end - offset + 0.1
                        and confidence >= 0.5
                        and len(re.findall(r"\S+", word.word)) == 1
                        and token(word.word)
                        and (offset == 0 or (a + b) / 2 >= 2)
                        and (end == duration or (a + b) / 2 < 28)
                    ):
                        words.append(
                            {"word": word.word.strip(), "start": a + offset, "end": b + offset}
                        )
            write_json(path, words)
        rows.extend(words)
        if len(rows) > MAX_WORDS:
            raise ValueError("Too many recognized words")
        progress("locating", 0.05 + 0.2 * (index + 1) / len(offsets))
    # Overlapping context must not create duplicate/out-of-order anchor words.
    ordered, previous_end = [], -1
    for word in rows:
        if word["start"] >= previous_end:
            ordered.append(word)
            previous_end = word["end"]
    return ordered


def align_passages(
    engine, model, metadata, audio, passages, options, progress, windows=None, directory=None
):
    """Keep source words; use only actual engine intervals, never interpolation."""
    source_words = [re.findall(r"\S+", s["text"]) for s in passages]
    flat = [w for words in source_words for w in words]
    if windows is None:
        if options.get("whole_recording", False):
            windows = [{"first": 0, "last": len(flat) - 1, "start": 0, "end": options["duration"]}]
        else:
            windows, cursor = [], 0
            for passage, words in zip(passages, source_words, strict=True):
                windows.append(
                    {
                        "first": cursor,
                        "last": cursor + len(words) - 1,
                        "start": passage["start"],
                        "end": passage["end"],
                    }
                )
                cursor += len(words)
    mapped = {}
    for index, window in enumerate(windows):
        first, last = window["first"], window["last"]
        text = " ".join(flat[first : last + 1])
        if not text:
            continue
        if (
            last - first + 1 > MAX_WINDOW_WORDS
            or len(text) > MAX_WINDOW_CHARS
            or not 0 < window["end"] - window["start"] <= MAX_WINDOW_SECONDS
        ):
            raise ValueError("Alignment window exceeds bounds")
        segment = {"text": text, "start": window["start"], "end": window["end"]}
        checkpoint = directory / f"aligned-{index:05d}.json" if directory else None
        if checkpoint and checkpoint.exists():
            result = json.loads(checkpoint.read_text(encoding="utf-8"))
        else:
            result = engine.align(
                [segment],
                model,
                metadata,
                audio,
                options["device"],
                interpolate_method="ignore",
                return_char_alignments=False,
                print_progress=False,
            )
            if checkpoint:
                write_json(checkpoint, result)
        words = result["word_segments"]
        matches = SequenceMatcher(
            None,
            [token(w) for w in flat[first : last + 1]],
            [token(w["word"]) for w in words],
            autojunk=False,
        )
        mapped.update(
            {
                first + a + i: words[b + i]
                for a, b, length in matches.get_matching_blocks()
                for i in range(length)
                if token(flat[first + a + i])
            }
        )
        progress("aligning", 0.3 + 0.3 * (index + 1) / len(windows))
    cursor, output = 0, []
    for passage, tokens in zip(passages, source_words, strict=True):
        aligned = []
        for word in tokens:
            found = mapped.get(cursor, {})
            aligned.append(
                {
                    "word": word,
                    "start": found.get("start"),
                    "end": found.get("end"),
                    "score": found.get("score"),
                }
            )
            cursor += 1
        output.append({"id": passage["id"], "words": aligned})
    return {"passages": output}


def transcription_rows(segments, duration, progress):
    """Whisper segments as bounded passages; zero-length ones join their neighbour's text."""
    rows, carry = [], ""
    for segment in segments:
        text = segment.text.strip()
        start, end = float(segment.start), min(float(segment.end), duration)
        if not text or not (math.isfinite(start) and math.isfinite(end)):
            continue
        if end - start < 0.05:
            # No audio interval of its own: keep every character, never invent timing.
            if rows:
                rows[-1]["text"] += " " + text
                for key in ("avg_logprob", "no_speech_prob", "compression_ratio"):
                    rows[-1].pop(key, None)
            else:
                carry = f"{carry} {text}".strip()
            continue
        row = {"text": f"{carry} {text}".strip(), "start": start, "end": end}
        if not carry:
            for key in ("avg_logprob", "no_speech_prob", "compression_ratio"):
                value = getattr(segment, key, None)
                if isinstance(value, (int, float)) and math.isfinite(value):
                    row[key] = float(value)
        carry = ""
        rows.append(row)
        if len(rows) > MAX_SEGMENTS:
            raise ValueError("Too many transcription segments")
        progress("transcribing", 0.05 + 0.25 * min(1, end / duration))
    if carry and rows:
        rows[-1]["text"] += " " + carry
    return rows


def transcribe_audio(audio, options, directory, snapshot, progress, *, language_result=None):
    """Full transcription: faster-whisper with its bundled VAD; alignment follows as usual."""
    if not danish_scope(options):
        return "language_scope"
    if (directory / "alignment_language.json").exists():
        raise ValueError("A clip language decision cannot be reused by transcription.")
    path = directory / "transcription.json"
    automatic = options["language"] == "auto"
    if path.exists():
        if automatic:
            rows, decision = _read_auto_transcription(directory, options)
        else:
            saved = json.loads(path.read_text(encoding="utf-8"))
            rows = saved["segments"]
            if "language_decision" in saved:
                raise ValueError("A fixed language request cannot reuse an Automatic ASR decision.")
            if "language_evidence" in saved:
                binding = language_binding(directory)
                validate_language_evidence(
                    saved["language_evidence"],
                    binding,
                    requested=options["language"],
                    model_choice=options["whisper_model"],
                    windows=language_windows(binding),
                )
            if options["whisper_model"] in DANISH_CHOICES:
                converted_transcription_metadata(directory, options)
            elif "asr_conversion" in saved:
                raise ValueError("This model cannot reuse Danish conversion evidence.")
    else:
        progress("transcribing", 0.05)
        conversion = None
        if options["whisper_model"] in DANISH_CHOICES:
            try:
                location, conversion = danish_module().prepared(
                    options["whisper_model"], cache=hub_cache(), verify=True
                )
            except (OSError, ValueError):
                return "cache"
        else:
            location = snapshot(WHISPER_MODELS[options["whisper_model"]])
        # Never let a missing tokenizer trigger faster-whisper's fallback download.
        if not (Path(location) / "tokenizer.json").is_file():
            return "cache"
        from faster_whisper import WhisperModel

        request_raw = _read_auto_bytes(directory / "request.json") if automatic else None
        binding = _language_input_binding(audio, directory)
        if automatic:
            _validate_auto_input(directory, binding, options, request_raw=request_raw)
        model = runtime_model(
            "faster-whisper",
            WHISPER_MODELS[options["whisper_model"]],
            options,
            lambda: WhisperModel(
                location,
                device=options["device"],
                local_files_only=True,
                compute_type="int8" if options["device"] == "cpu" else "float16",
            ),
            conversion=conversion,
        )
        if (
            automatic
            and getattr(getattr(model, "model", None), "is_multilingual", None) is not True
        ):
            return "language_model"
        segments, info = model.transcribe(
            audio,
            language=None if automatic else options["language"],
            vad_filter=True,
            beam_size=5,
            condition_on_previous_text=False,
            **(
                {
                    k: AUTO_LANGUAGE_POLICY[k]
                    for k in ("language_detection_segments", "language_detection_threshold")
                }
                if automatic
                else {}
            ),
        )
        rows = transcription_rows(segments, options["duration"], progress)
        if automatic:
            _validate_auto_input(directory, binding, options)
            _validate_auto_rows(rows, binding)
            decision = _asr_language_decision(binding, options, info, len(rows))
            write_json(path, {"segments": rows, "language_decision": decision})
        else:
            windows = language_windows(binding)
            evidence = [_configured_language_evidence(binding, options, windows[0], info)]
            validate_language_evidence(
                evidence,
                binding,
                requested=options["language"],
                model=MODELS[WHISPER_MODELS[options["whisper_model"]]],
                model_choice=options["whisper_model"],
                windows=windows,
            )
            packet = {"segments": rows, "language_evidence": evidence}
            if conversion is not None:
                packet["asr_conversion"] = danish_module().evidence(
                    options["whisper_model"],
                    conversion,
                    request_sha256=binding["request_sha256"],
                    input_sha256=binding["input_sha256"],
                )
                validate_asr_conversion(packet, options, binding)
            write_json(path, packet)
        del model
    if automatic:
        if language_result is not None:
            language_result.update(decision)
        if decision["status"] != "resolved":
            return "language_" + decision["status"]
    return _asr_passages(rows)


def retranscribe_passages(audio, request, options, directory, snapshot, progress):
    """New wording for bounded passages from their own (padded) audio (T71)."""
    if (directory / "alignment_language.json").exists():
        raise ValueError("A clip language decision cannot be reused by re-transcription.")
    if options["language"] == "auto" or options["whisper_model"] in DANISH_CHOICES:
        return "language_scope"
    path = directory / "retranscription.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        rows = saved["passages"]
        if "language_decision" in saved:
            raise ValueError("Passage re-transcription cannot reuse an Automatic ASR decision.")
        if "language_evidence" in saved:
            binding = language_binding(directory)
            validate_language_evidence(
                saved["language_evidence"],
                binding,
                requested=options["language"],
                model_choice=options["whisper_model"],
                windows=language_windows(
                    binding, passages=request["passages"], duration=options["duration"]
                ),
            )
    else:
        from faster_whisper import WhisperModel

        progress("transcribing", 0.05)
        location = snapshot(WHISPER_MODELS[options["whisper_model"]])
        if not (Path(location) / "tokenizer.json").is_file():
            return "cache"
        binding = _language_input_binding(audio, directory)
        model = runtime_model(
            "faster-whisper",
            WHISPER_MODELS[options["whisper_model"]],
            options,
            lambda: WhisperModel(
                location,
                device=options["device"],
                local_files_only=True,
                compute_type="int8" if options["device"] == "cpu" else "float16",
            ),
        )
        rows, evidence = [], []
        windows = language_windows(
            binding, passages=request["passages"], duration=options["duration"]
        )
        for index, passage in enumerate(request["passages"]):
            begin = max(0.0, passage["start"] - PAD_SECONDS)
            window = windows[index]["window"]
            segments, info = model.transcribe(
                audio[round(window["start"] * 16000) : round(window["end"] * 16000)],
                language=options["language"],
                word_timestamps=True,
                vad_filter=False,
                beam_size=5,
                condition_on_previous_text=False,
            )
            evidence.append(_configured_language_evidence(binding, options, windows[index], info))
            words, metrics = [], []
            for segment in segments:
                metrics.append(segment)
                for word in segment.words or []:
                    middle = begin + (word.start + word.end) / 2
                    # Padding words belong to the neighbouring passages.
                    if passage["start"] <= middle < passage["end"]:
                        words.append(word.word)
            row = {"id": passage["id"], "text": "".join(words).strip()}
            values = {
                "avg_logprob": [m.avg_logprob for m in metrics],
                "no_speech_prob": [m.no_speech_prob for m in metrics],
                "compression_ratio": [m.compression_ratio for m in metrics],
            }
            for key, found in values.items():
                found = [
                    float(v) for v in found if isinstance(v, (int, float)) and math.isfinite(v)
                ]
                if found:
                    # Conservative summary: mean decoding confidence, worst warning signals.
                    row[key] = sum(found) / len(found) if key == "avg_logprob" else max(found)
            rows.append(row)
            progress("transcribing", 0.05 + 0.25 * (index + 1) / len(request["passages"]))
        validate_language_evidence(
            evidence,
            binding,
            requested=options["language"],
            model=MODELS[WHISPER_MODELS[options["whisper_model"]]],
            model_choice=options["whisper_model"],
            windows=windows,
        )
        write_json(path, {"passages": rows, "language_evidence": evidence})
        del model
    texts = {row["id"]: row["text"] for row in rows}
    return [
        {"id": p["id"], "text": texts[p["id"]], "start": p["start"], "end": p["end"]}
        for p in request["passages"]
        if texts.get(p["id"])
    ]


def prepare_audio(directory):
    """Convert in bounded blocks, then memory-map rather than copying the whole recording."""
    path = directory / "audio.f32"
    marker = directory / "audio.f32.source.json"
    source_before = (directory / "audio.wav").stat()
    source_hash = file_hash(directory / "audio.wav")
    with wave.open(str(directory / "audio.wav"), "rb") as audio_file:
        if (audio_file.getframerate(), audio_file.getnchannels(), audio_file.getsampwidth()) != (
            16000,
            1,
            2,
        ):
            raise ValueError("Unsupported audio")
        frames = audio_file.getnframes()
        if not 0 < frames <= 16000 * 6 * 3600:
            raise ValueError("Unsupported duration")
        expected = {"schema": 1, "input_sha256": source_hash, "audio_frames": frames}
        try:
            saved = json.loads(marker.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, UnicodeError):
            saved = {}
        reusable = (
            isinstance(saved, dict)
            and set(saved) == set(expected) | {"decoded_sha256"}
            and type(saved.get("schema")) is int
            and type(saved.get("audio_frames")) is int
            and all(saved.get(key) == value for key, value in expected.items())
            and path.is_file()
            and path.stat().st_size == frames * 4
            and saved.get("decoded_sha256") == file_hash(path)
        )
        if not reusable:
            pending = directory / "audio.f32.pending"
            with pending.open("wb") as output:
                while raw := audio_file.readframes(16000 * 30):
                    samples = array("h", raw)
                    if sys.byteorder != "little":
                        samples.byteswap()
                    floats = array("f", (v / 32768.0 for v in samples))
                    if sys.byteorder != "little":
                        floats.byteswap()
                    floats.tofile(output)
            if pending.stat().st_size != frames * 4:
                raise ValueError("Truncated audio")
            if file_hash(directory / "audio.wav") != source_hash:
                raise ValueError("The processing audio changed during decoding.")
            decoded_hash = file_hash(pending)
            pending.replace(path)
            write_json(marker, {**expected, "decoded_sha256": decoded_hash})
        source_after = (directory / "audio.wav").stat()
        if (source_before.st_size, source_before.st_mtime_ns, source_before.st_ino) != (
            source_after.st_size,
            source_after.st_mtime_ns,
            source_after.st_ino,
        ):
            raise ValueError("The processing audio changed during decoding checks.")
    return path


def audio_data(directory):
    import numpy as np

    return np.memmap(prepare_audio(directory), dtype="<f4", mode="r+")


def execute(request, directory, *, stage=None):
    if stage not in {None, "transcription", "alignment", "diarization", "complete"}:
        raise ValueError("Unknown Runtime stage.")
    capture_policy = request.get("producer_artifact_policy")
    capture = capture_policy in {"local-full-asr-v1", "local-enhancement-v1", "local-timing-v1"}
    capture_binding = None
    if capture:
        origin = json.loads(_read_auto_bytes(directory / "producer-origin.json"))
        if (
            not isinstance(origin, dict)
            or set(origin) != {"generation", "audio_binding"}
            or not isinstance(origin["generation"], str)
            or not re.fullmatch(r"[a-f0-9]{32}", origin["generation"])
            or request["options"].get("execution") != "local"
            or request["options"].get("retranscribe") is not False
            or json.loads(_read_auto_bytes(directory / "request.json")) != request
            or (
                capture_policy == "local-full-asr-v1"
                and (
                    request["options"].get("transcribe") is not True
                    or request.get("passages") != []
                )
            )
            or (
                capture_policy == "local-enhancement-v1"
                and request["options"].get("transcribe") is not False
            )
            or (
                capture_policy == "local-timing-v1"
                and (
                    request["options"].get("transcribe") is not False
                    or request["options"].get("align") is not True
                    or request["options"].get("diarize") is not False
                    or len(request.get("passages", [])) != 1
                )
            )
            or language_binding(directory) != origin["audio_binding"]
        ):
            raise ValueError("The producer request no longer matches its frozen input.")
        capture_binding = origin["audio_binding"]
    clip = request.get("operation") == "bounded_alignment"
    clip_decision = None
    if clip:
        request, clip_binding = _clip_context(directory, request)
        has_decision = (directory / "alignment_language.json").exists()
        _clip_artifacts(directory, new=not has_decision)
        if has_decision:
            metadata = clip_alignment_metadata(directory, request=request)
            clip_decision = metadata["clip_language_decision"]
            if clip_decision["status"] != "resolved":
                return "clip_language_" + clip_decision["status"]
            if "alignment_outcome" in metadata:
                return None
    elif "operation" in request or (directory / "alignment_language.json").exists():
        raise ValueError("This worker operation cannot accept bounded clip language artifacts.")
    options = request["options"]
    if not danish_scope(options, request.get("passages")):
        return "language_scope"
    auto_options = options if options["language"] == "auto" and not clip else None
    auto_decision = None
    if (
        not clip
        and options["language"] == "auto"
        and (
            options.get("transcribe") is not True
            or options.get("retranscribe") is not False
            or request.get("passages") != []
        )
    ):
        return "language_scope"
    import torch
    import whisperx
    from huggingface_hub import snapshot_download

    if options["device"] == "auto":
        # The app records the resolved device first (ADR 0059); a direct call decides here.
        options = dict(options, device="cuda" if torch.cuda.is_available() else "cpu")
        request = dict(request, options=options)
    if options["device"] == "cuda" and not torch.cuda.is_available():
        return "gpu"

    def progress(stage, fraction):
        if stage == "loading" and RUNTIME_CACHE is not None:
            return  # Stage passes retain the last actual inference progress until new work starts.
        write_json(directory / "progress.json", {"stage": stage, "fraction": fraction})

    def snapshot(key):
        repo, revision = MODELS[key]
        return snapshot_download(
            repo,
            revision=revision,
            token=os.environ.get("HF_TOKEN") or False,
            local_files_only=not options["allow_downloads"],
            ignore_patterns=SNAPSHOT_IGNORE,
        )

    audio = audio_data(directory)
    if capture and _language_input_binding(audio, directory) != capture_binding:
        raise ValueError("The producer's actual inference input changed.")
    progress("loading", 0.05)
    if clip:
        if getattr(audio, "filename", None) is None:
            raise ValueError("Bounded alignment requires the verified prepared float mapping.")
        current_binding = _language_input_binding(audio, directory)
        if current_binding != clip_binding:
            raise ValueError("The bounded alignment input changed before inference.")
        if clip_decision is None:
            clip_decision = _detect_clip_language(audio, options, directory, clip_binding, snapshot)
            if isinstance(clip_decision, str):
                return clip_decision
            if options["device"] == "cuda":
                torch.cuda.empty_cache()
        if clip_decision["status"] != "resolved":
            return "clip_language_" + clip_decision["status"]
        options = dict(options, language=clip_decision["effective"])
    if options.get("retranscribe"):
        passages = retranscribe_passages(audio, request, options, directory, snapshot, progress)
        if isinstance(passages, str):
            return passages
        request = dict(request, passages=passages)
    if options.get("transcribe"):
        if capture and not (directory / "transcription.json").exists():
            processing_environment(directory, "transcription", options, torch)
        language_result = {}
        passages = transcribe_audio(
            audio, options, directory, snapshot, progress, language_result=language_result
        )
        if isinstance(passages, str):
            if auto_options is None or passages not in {
                "language_uncertain",
                "language_unsupported",
                "language_no_speech",
            }:
                return passages
            rows, decision = _read_auto_transcription(directory, auto_options)
            if decision != language_result:
                raise ValueError("The completed ASR language decision changed during processing.")
            passages = _asr_passages(rows)
        if auto_options is not None:
            auto_decision = language_result
            if auto_decision["status"] == "resolved":
                options = dict(options, language=auto_decision["effective"])
            elif options["align"] and not (directory / "alignment.json").exists():
                write_json(
                    directory / "alignment.json", _untimed_alignment(passages, auto_decision)
                )
        if options["device"] == "cuda":
            torch.cuda.empty_cache()
        request = dict(request, passages=passages)
    if stage == "transcription":
        return None
    if (
        options["align"]
        and stage in {None, "alignment"}
        and not (directory / "alignment.json").exists()
    ):
        if capture:
            processing_environment(directory, "alignment", options, torch)
        import nltk

        windows = None
        if needs_coarse(request["passages"], options["duration"]):
            path = directory / "windows.json"
            if path.exists():
                windows = json.loads(path.read_text(encoding="utf-8"))
            else:
                from faster_whisper import WhisperModel

                progress("locating", 0.05)
                location = snapshot("coarse")
                # Never let a missing tokenizer trigger faster-whisper's fallback download.
                if not (Path(location) / "tokenizer.json").is_file():
                    return "cache"
                preliminary = runtime_model(
                    "faster-whisper",
                    "coarse",
                    options,
                    lambda: WhisperModel(
                        location,
                        device=options["device"],
                        local_files_only=True,
                        compute_type="int8" if options["device"] == "cpu" else "float16",
                    ),
                )
                recognized = recognize_windows(preliminary, audio, options, directory, progress)
                windows = alignment_windows(request["passages"], recognized, options["duration"])
                write_json(path, windows)
                del preliminary
                if options["device"] == "cuda":
                    torch.cuda.empty_cache()
        language = "danish" if options["language"] == "da" else "english"
        try:
            nltk.data.load(f"tokenizers/punkt_tab/{language}.pickle")
        except LookupError:
            if not options["allow_downloads"]:
                return "cache"
            if not nltk.download("punkt_tab", quiet=True):
                return "cache"
        global ALIGNMENT_CACHE
        identity = (options["language"], options["device"])
        if CACHE_ALIGNMENT and ALIGNMENT_CACHE and ALIGNMENT_CACHE[0] == identity:
            model, metadata = ALIGNMENT_CACHE[1]
        else:
            ALIGNMENT_CACHE = None
            model, metadata = runtime_model(
                "whisperx-align",
                options["language"],
                options,
                lambda: whisperx.load_align_model(
                    options["language"],
                    options["device"],
                    model_name=snapshot(options["language"]),
                    model_cache_only=True,
                ),
            )
            if CACHE_ALIGNMENT:
                ALIGNMENT_CACHE = (identity, (model, metadata))
        result = align_passages(
            whisperx,
            model,
            metadata,
            audio,
            request["passages"],
            options,
            progress,
            windows=windows,
            directory=None if clip else directory,
        )
        if auto_decision is not None:
            result["language_outcome"] = _language_outcome(auto_decision, True)
        elif clip_decision is not None:
            result["language_outcome"] = {
                "schema": 1,
                "state": "alignment_attempted",
                "decision_sha256": clip_decision_digest(clip_decision),
            }
        write_json(directory / "alignment.json", result)
        del model
        if options["device"] == "cuda":
            torch.cuda.empty_cache()
    if stage == "alignment":
        return None
    if auto_decision is not None:
        auto_transcription_metadata(
            directory, auto_options, require_alignment=auto_options["align"]
        )
    if clip:
        clip_alignment_metadata(directory, request=request, require_alignment=True)
    if (
        options["diarize"]
        and stage in {None, "diarization"}
        and not (directory / "diarization.json").exists()
    ):
        if capture:
            processing_environment(directory, "diarization", options, torch)
        from whisperx.diarize import DiarizationPipeline

        progress("speakers", 0.65)
        pipeline = runtime_model(
            "pyannote",
            "speakers",
            options,
            lambda: DiarizationPipeline(
                model_name=snapshot("speakers"),
                token=os.environ.get("HF_TOKEN") or False,
                device=options["device"],
            ),
        )
        turns = pipeline(
            audio,
            min_speakers=options.get("min_speakers"),
            max_speakers=options.get("max_speakers"),
        )
        write_json(
            directory / "diarization.json",
            {
                "turns": [
                    {"start": float(row.start), "end": float(row.end), "speaker": str(row.speaker)}
                    for row in turns.itertuples()
                ],
            },
        )
    if stage in {None, "complete"}:
        progress("complete", 1)
    return None


def process_directory(directory, *, stage=None):
    """Keep errors curated and bodies off subprocess output in either execution mode."""
    error = None
    try:
        if not doctor()["ready"]:
            error = "runtime"
        else:
            request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
            if (directory / "original.input.json").exists():
                helper = Path(__file__).with_name("remote_audio_input.py")
                raw = _read_auto_bytes(helper)
                manifest = json.loads(_read_auto_bytes(helper.parent / "batch.json"))
                if hashlib.sha256(raw).hexdigest() != manifest.get("code_hashes", {}).get(
                    helper.name
                ):
                    raise ValueError("The remote input helper does not match the captured source.")
                module = ModuleType("cbs_remote_audio_input")
                module.__file__ = str(helper)
                exec(compile(raw, str(helper), "exec"), module.__dict__)
                if not (directory / "audio.preparation.json").exists():
                    write_json(
                        directory / "progress.json", {"stage": "preparation", "fraction": 0.05}
                    )
                module.prepare(directory)
            offline = "0" if request["options"]["allow_downloads"] else "1"
            os.environ.update(
                {
                    "HF_HUB_OFFLINE": offline,
                    "TRANSFORMERS_OFFLINE": offline,
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "DO_NOT_TRACK": "1",
                    "PYANNOTE_METRICS_ENABLED": "0",
                    "TOKENIZERS_PARALLELISM": "false",
                }
            )
            error = (
                execute(request, directory)
                if stage is None
                else execute(request, directory, stage=stage)
            )
    except ImportError:
        error = "dependencies"
    except Exception as exc:
        name = type(exc).__name__
        error = (
            "input_preparation"
            if name == "PreparationError"
            else "cache"
            if name in {"LocalEntryNotFoundError", "OfflineModeIsEnabled"}
            else "access"
            if name in {"GatedRepoError", "RepositoryNotFoundError"}
            else "memory"
            if name == "OutOfMemoryError"
            else "processing"
        )
    if error:
        write_json(directory / "error.json", {"code": error})
    return error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--hardware", action="store_true")
    parser.add_argument("--serve-passages", action="store_true")
    parser.add_argument("--cache-status", action="store_true")
    parser.add_argument("--prefetch", action="store_true")
    parser.add_argument("--models", default="", help="Extra pinned model keys to prefetch")
    parser.add_argument("directory", nargs="?")
    arguments = parser.parse_args()
    if arguments.doctor:
        print(json.dumps(doctor(arguments.hardware)))
        return
    if arguments.cache_status:
        print(json.dumps(cache_status()))
        return
    if arguments.prefetch:
        print(json.dumps(prefetch(tuple(filter(None, arguments.models.split(","))))))
        return
    if arguments.serve_passages:
        global CACHE_ALIGNMENT
        CACHE_ALIGNMENT = True
        for line in sys.stdin:
            directory = Path(json.loads(line))
            request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
            options = request["options"]
            passages = request["passages"]
            # Persistent mode accepts only the bounded, offline operation it was built for.
            if (
                not options["align"]
                or options["diarize"]
                or options["allow_downloads"]
                or len(passages) != 1
                or needs_coarse(passages, options["duration"])
                or not 0 < options["duration"] <= MAX_WINDOW_SECONDS
            ):
                write_json(directory / "error.json", {"code": "processing"})
            else:
                process_directory(directory)
            write_json(directory / "done.json", {"complete": True})
    elif process_directory(Path(arguments.directory)):
        sys.exit(1)


if __name__ == "__main__":
    main()
