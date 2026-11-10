"""Optional local alignment/diarization with frozen input and independent results."""

import copy
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
from bisect import bisect_left, bisect_right
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_serializer, model_validator

from . import media
from .enhancement_worker import (
    DANISH_CHOICES,
    MAX_SEGMENTS,
    MAX_WINDOW_CHARS,
    MAX_WINDOW_SECONDS,
    MAX_WINDOW_WORDS,
    MAX_WORDS,
    MODELS,
    WHISPER_MODELS,
    WHISPERX_VERSION,
    needs_coarse,
    write_json,
)
from .transcripts import MAX_TRANSCRIPT_BYTES, normalize_segments, slice_hints

HF_SCOPE = "huggingface:https://huggingface.co"
WORKER = Path(__file__).with_name("enhancement_worker.py")
STAGES = {
    "loading": "Loading local models…",
    "transcribing": "Transcribing with WhisperX…",
    "locating": "Locating wording in long audio…",
    "aligning": "Aligning words…",
    "speakers": "Detecting speakers…",
    "complete": "Saving enhanced result…",
}
ERRORS = {
    "access": "Model access was denied. Check the HF read token and accepted terms.",
    "memory": "The local runtime ran out of memory. Try a smaller recording or a suitable GPU.",
    "runtime": f"Install WhisperX {WHISPERX_VERSION} in the selected Python runtime.",
    "dependencies": "The local inference runtime has missing dependencies. Check its installation.",
    "gpu": "CUDA is unavailable in this runtime. Choose CPU or configure a supported GPU runtime.",
    "cache": "Models or tokenizer data are not cached. Prepare the cache or allow downloads.",
    "audio": "The prepared enhancement audio has an unsupported format. Retry preparation.",
    "language_scope": (
        "Automatic language is not yet available for this processing operation. "
        "Choose Danish or English."
    ),
    "language_model": (
        "Automatic language requires a multilingual ASR model. Check the prepared model cache."
    ),
    "language_uncertain": (
        "The ASR engine could not resolve Automatic language reliably. "
        "Its text checkpoint is retained; choose Danish or English for a new request."
    ),
    "language_unsupported": (
        "The ASR engine reported a language without a supported Danish/English aligner. "
        "Its text checkpoint is retained; choose a supported language for a new request."
    ),
    "language_no_speech": (
        "The ASR engine reported no speech after filtering, so language could not be resolved. "
        "Its checkpoint is retained."
    ),
    "clip_language_model": (
        "The cached clip-language detector is incompatible. "
        "Prepare the pinned multilingual model and its expected audio configuration."
    ),
    "clip_language_uncertain": (
        "Language could not be resolved for this audio clip. Its decision checkpoint is "
        "retained and no word timing was added. Choose Danish or English for a new request."
    ),
    "clip_language_unsupported": (
        "The clip detector reported a language without a supported Danish/English aligner. "
        "Its decision checkpoint is retained and no word timing was added."
    ),
    "processing": (
        "Local enhancement failed. Check model access/cache, dependencies and memory. "
        "Source text was not changed."
    ),
}


class LocalSettings(BaseModel):
    """Settings → Processing (ADR 0059)."""

    runtime_python: str = Field(default=sys.executable, min_length=1, max_length=2000)
    # "auto" uses a CUDA GPU when this computer's runtime check finds one; each job records
    # the concrete device it ran on. Saved explicit choices are never changed silently.
    device: Literal["auto", "cpu", "cuda"] = "auto"
    language: Literal["da", "en"] = "da"
    # Standing consent to fetch missing pinned local models when a job needs them.
    download_models: bool = False


class ProcessingSettings(LocalSettings):
    """Saved Settings → Processing choices, including those that never enter job options."""

    # Fresh/unset Processing choices permit preparation when a requested job needs it.
    # Keep LocalSettings defaults unchanged for frozen EnhancementOptions snapshots.
    download_models: bool = True
    # Standing consent (ADR 0098) to prepare the UCloud CBS Runtime and its pinned models
    # when an explicit UCloud request needs them. Preserve explicitly saved choices.
    # Kept off LocalSettings so EnhancementOptions and frozen job identities stay unchanged.
    prepare_ucloud_models: bool = True


class EnhancementOptions(LocalSettings):
    # Automatic is gated to full ASR; saved Processing choices retain their da/en default.
    language: Literal["da", "en", "auto"] = "da"
    # "auto" is resolved per recording when queued; frozen jobs are local or ucloud.
    execution: Literal["local", "ucloud", "auto"] = "local"
    align: bool = True
    diarize: bool = False
    allow_downloads: bool = False
    min_score: float = Field(default=0.3, ge=0, le=1, allow_inf_nan=False)
    timing_policy: Literal["legacy-score-filter", "retain-estimates"] = "legacy-score-filter"
    min_speakers: int | None = Field(default=None, ge=1, le=20)
    max_speakers: int | None = Field(default=None, ge=1, le=20)
    timeout_minutes: int = Field(default=360, ge=1, le=720)
    # Explicit one-time preparation of the reusable UCloud runtime; never implied.
    prepare_runtime: bool = False
    # Full WhisperX transcription (T70): transcribe the audio, then align as usual.
    transcribe: bool = False
    whisper_model: Literal[
        "small", "medium", "large-v3-turbo", "large-v3", "edda-turbo", "edda-large", "roest-v3"
    ] = "large-v3"
    # Re-transcribe selected bounded passages (T71); other passages are copied unchanged.
    retranscribe: bool = False

    @model_serializer(mode="wrap")
    def frozen_options(self, handler):
        values = handler(self)
        if "timing_policy" not in self.model_fields_set:
            # Absent in old snapshots: do not change their request/checkpoint identities.
            values.pop("timing_policy", None)
        return values

    @model_validator(mode="after")
    def meaningful(self):
        if self.whisper_model in DANISH_CHOICES and (
            self.language != "da" or not self.transcribe or self.retranscribe
        ):
            raise ValueError("Edda and Røst support Danish full-recording transcription only.")
        if self.language == "auto" and (not self.transcribe or self.retranscribe):
            raise ValueError("Automatic language is available only for full ASR transcription.")
        if not self.align and not self.diarize:
            raise ValueError("Select word alignment, speaker detection, or both.")
        if self.min_speakers and self.max_speakers and self.min_speakers > self.max_speakers:
            raise ValueError("Minimum speakers cannot exceed maximum speakers.")
        return self


def local_settings(store):
    rows = store.all("SELECT value FROM settings WHERE id=3")
    return (
        ProcessingSettings.model_validate_json(rows[0]["value"]) if rows else ProcessingSettings()
    )


REALIGN_SETTING = 6


def realign_enabled(store):
    """Opt-in: realign edited passages automatically (T69, ADR 0052)."""
    rows = store.all("SELECT value FROM settings WHERE id=?", (REALIGN_SETTING,))
    return bool(rows and json.loads(rows[0]["value"]).get("realign_edits") is True)


def save_realign(store, enabled):
    store.execute(
        "INSERT INTO settings(id,value) VALUES (?,?) "
        "ON CONFLICT(id) DO UPDATE SET value=excluded.value",
        (REALIGN_SETTING, json.dumps({"realign_edits": bool(enabled)})),
    )


def save_local_settings(store, settings, *, preserve_fields=()):
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT value FROM settings WHERE id=3").fetchone()
        saved = (
            ProcessingSettings.model_validate_json(current[0]) if current else ProcessingSettings()
        )
        values = settings.model_dump()
        for field in preserve_fields:
            if field not in ProcessingSettings.model_fields:
                raise ValueError("Invalid preserved processing setting.")
            values[field] = getattr(saved, field)
        updated = ProcessingSettings.model_validate(values)
        db.execute(
            "INSERT INTO settings(id,value) VALUES (3,?) "
            "ON CONFLICT(id) DO UPDATE SET value=excluded.value",
            (updated.model_dump_json(),),
        )


def python_path(settings):
    path = Path(settings.runtime_python)
    if not path.is_absolute() or not path.is_file():
        raise ValueError(
            "Choose an existing absolute Python executable path for local enhancement."
        )
    return str(path)


def child_environment(allow_downloads=False, hf_token=""):
    # Do not inherit API/cloud credentials into the optional inference runtime.
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "SYSTEMDRIVE",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "LANG",
        "LC_ALL",
        "NUMBER_OF_PROCESSORS",
        "OMP_NUM_THREADS",
        "CUDA_VISIBLE_DEVICES",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "TORCH_HOME",
        "NLTK_DATA",
    }
    result = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    result.update(
        {
            "HF_HUB_OFFLINE": "0" if allow_downloads else "1",
            "TRANSFORMERS_OFFLINE": "0" if allow_downloads else "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "PYANNOTE_METRICS_ENABLED": "0",
            "TOKENIZERS_PARALLELISM": "false",
        }
    )
    if hf_token:
        result["HF_TOKEN"] = hf_token
    return result


def check_runtime(settings, *, hardware=True):
    try:
        result = subprocess.run(
            [python_path(settings), "-I", str(WORKER), "--doctor"]
            + (["--hardware"] if hardware else []),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=90 if hardware else 20,
            env=child_environment(),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode or len(result.stdout) > 4096:
            raise ValueError("The selected Python runtime could not be checked.")
        data = json.loads(result.stdout)
        return {
            "ready": data.get("ready") is True and data.get("version") == WHISPERX_VERSION,
            "version": data.get("version"),
            "expected_version": WHISPERX_VERSION,
            "unsupported_platform": data.get("unsupported_platform") is True,
            "gpu": data.get("gpu", {"checked": False, "available": False, "devices": []}),
            "note": (
                "Local WhisperX enhancement is unavailable on Intel macOS with this runtime. "
                "Use UCloud enhancement. API transcription, import, review and export "
                "remain available."
                if data.get("unsupported_platform") is True
                else "Checks packages and CUDA access; model loading still needs validation."
            ),
        }
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, AttributeError) as exc:
        raise ValueError("The local Python runtime check failed or timed out.") from exc


_hardware = {}
_hardware_lock = threading.Lock()


def hardware_status(settings, *, refresh=False):
    """This computer's runtime and GPU, checked once per interpreter (startup or on request).

    Concurrent callers wait for one check rather than starting another. A failed check (a
    slow first import, say) is reported but never cached, so the next caller tries again."""
    key = settings.runtime_python
    with _hardware_lock:
        if refresh or key not in _hardware:
            try:
                _hardware[key] = check_runtime(settings) | {"checked_at": time.time()}
            except ValueError as exc:
                _hardware.pop(key, None)
                return {"ready": False, "error": str(exc), "checked_at": time.time()}
        return _hardware[key]


def cached_hardware(settings):
    return _hardware.get(settings.runtime_python)


def detected_device(settings):
    gpu = (hardware_status(settings).get("gpu") or {}).get("available")
    return "cuda" if gpu else "cpu"


def require_local_runtime(options):
    runtime = check_runtime(options, hardware=options.device == "cuda")
    if runtime.get("unsupported_platform"):
        raise ValueError(runtime["note"])
    if not runtime.get("ready"):
        raise ValueError(
            f"Local processing needs WhisperX {WHISPERX_VERSION}. "
            "Run uv sync --locked in the project to repair the standard installation, "
            "or select a prepared Python runtime in Add word timing / speakers. "
            "Then use Check runtime & GPU."
        )
    if options.device == "cuda" and not runtime.get("gpu", {}).get("available"):
        raise ValueError(
            "The selected local runtime cannot use CUDA. Choose CPU or fix its GPU setup."
        )
    return runtime


def pending_stages(folder, options):
    return [
        name
        for name, enabled in (
            ("transcription", options.transcribe),
            ("retranscription", options.retranscribe),
            ("alignment", options.align),
            ("diarization", options.diarize),
        )
        if enabled and not (folder / f"{name}.json").exists()
    ]


def transcription_source(folder, *, allow_empty=False):
    """The worker's transcription as passages with the same IDs its alignment used."""
    data = read_output(folder / "transcription.json")
    rows = data.get("segments") if isinstance(data, dict) else None
    if (
        not isinstance(rows, list)
        or len(rows) > MAX_SEGMENTS
        or not all(isinstance(row, dict) for row in rows)
    ):
        raise ValueError("The WhisperX transcription output is invalid.")
    keys = ("text", "start", "end", "avg_logprob", "no_speech_prob", "compression_ratio")
    return {
        "id": None,
        "segments": normalize_segments(
            [{key: row[key] for key in keys if key in row} for row in rows]
        )
        if rows or not allow_empty
        else [],
        "speakers": {},
    }


def asr_language_evidence(
    folder, options, *, job=None, selected=None, duration=None, include_alignment=True
):
    """Validate optional ASR provenance without attributing evidence to legacy checkpoints."""
    from .enhancement_worker import (
        auto_transcription_metadata,
        converted_transcription_metadata,
        language_binding,
        language_windows,
        validate_language_evidence,
    )

    if type(include_alignment) is not bool:
        raise ValueError("Choose whether to validate the ASR alignment outcome explicitly.")
    name = "retranscription.json" if options.retranscribe else "transcription.json"
    output = read_output(folder / name)
    converted = options.whisper_model in DANISH_CHOICES
    if isinstance(output, dict) and "asr_conversion" in output and not converted:
        raise ValueError("This model cannot reuse Danish conversion evidence.")
    if converted and (
        not isinstance(output, dict)
        or "language_evidence" not in output
        or "asr_conversion" not in output
    ):
        raise ValueError("Danish ASR requires its captured conversion and language evidence.")
    automatic = options.language == "auto"
    if not automatic and isinstance(output, dict) and "language_decision" in output:
        raise ValueError("Automatic ASR language decisions need the new processing protocol.")
    if automatic and (
        not options.transcribe
        or options.retranscribe
        or selected is not None
        or not isinstance(output, dict)
        or "language_evidence" in output
    ):
        raise ValueError("Automatic language requires its full-ASR decision and output.")
    if not automatic and (not isinstance(output, dict) or "language_evidence" not in output):
        return {}
    request = read_output(folder / "request.json")
    configured = request.get("options") if isinstance(request, dict) else None
    expected_options = options.model_dump(exclude={"runtime_python", "prepare_runtime"})
    if (
        not isinstance(configured, dict)
        or set(configured) != set(expected_options) | {"duration", "whole_recording"}
        or any(
            type(configured[key]) is not type(value) or configured[key] != value
            for key, value in expected_options.items()
        )
    ):
        raise ValueError("The language evidence does not match the frozen ASR request.")
    request_duration = configured.get("duration")
    if (
        configured.get("whole_recording") is not False
        or type(request_duration) not in (int, float)
        or not math.isfinite(request_duration)
        or not 0 < request_duration <= 6 * 3600
        or (duration is not None and request_duration != duration)
    ):
        raise ValueError("The language evidence has another frozen recording scope.")
    passages = request.get("passages")
    if options.retranscribe:
        expected = [
            {key: passage[key] for key in ("id", "start", "end")} | {"text": ""}
            for passage in selected["segments"]
        ]
        if passages != expected:
            raise ValueError("The language evidence refers to other re-transcription passages.")
    elif passages != []:
        raise ValueError("The full-recording language evidence has unexpected passage input.")
    model = None
    remote = (job or {}).get("params", {}).get("ucloud_api", {})
    from .remote_audio_input import observed_binding

    observed = observed_binding(folder, (job or {}).get("params", {}))
    if "batch_plan" in remote:
        plan = remote["batch_plan"]
        identity = plan.get("identity") if isinstance(plan, dict) else None
        revisions = identity.get("model_revisions") if isinstance(identity, dict) else None
        names = (WHISPER_MODELS[options.whisper_model], "whisper/" + options.whisper_model)
        pins = (
            [revisions[name] for name in names if name in revisions]
            if isinstance(revisions, dict)
            else []
        )
        if not pins or any(
            not isinstance(pin, (list, tuple)) or len(pin) != 2 or tuple(pin) != tuple(pins[0])
            for pin in pins
        ):
            raise ValueError("The frozen ASR model revision is missing or inconsistent.")
        model = pins[0]
    if automatic:
        return auto_transcription_metadata(
            folder,
            configured,
            model=model,
            require_alignment=options.align and include_alignment,
            include_alignment=include_alignment,
            **({"binding": observed} if observed is not None else {}),
        )
    if converted:
        return converted_transcription_metadata(
            folder,
            configured,
            model=model,
            **({"binding": observed} if observed is not None else {}),
        )
    binding = observed if observed is not None else language_binding(folder)
    windows = language_windows(
        binding,
        passages=passages if options.retranscribe else None,
        duration=configured.get("duration"),
    )
    evidence = validate_language_evidence(
        output["language_evidence"],
        binding,
        requested=options.language,
        model=model,
        model_choice=options.whisper_model,
        windows=windows,
    )
    return {"language_evidence": evidence}


def _model_revision(pin):
    if (
        not isinstance(pin, (list, tuple))
        or len(pin) != 2
        or not isinstance(pin[0], str)
        or not isinstance(pin[1], str)
        or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", pin[0]
        )
        or not re.fullmatch(r"[a-f0-9]{40}", pin[1])
    ):
        raise ValueError("The frozen processing model revision is invalid.")
    return {"repo": pin[0], "revision": pin[1]}


def _model_revisions(models, job=None):
    """Prefer the executed Runtime plan; keep unknown dependencies out of recovery metadata."""
    params = (job or {}).get("params", {})
    remote = params.get("ucloud_api") or {}
    if not isinstance(remote, dict):
        raise ValueError("The frozen processing model revisions are invalid.")
    plan = remote.get("batch_plan")
    if plan is None:
        # Singleton admission accepts absent/null plans, never an existing batch member.
        if params.get("runtime_batch_id"):
            raise ValueError("The frozen processing model revisions are invalid.")
        return {name: {"repo": MODELS[name][0], "revision": MODELS[name][1]} for name in models}
    identity = plan.get("identity") if isinstance(plan, dict) else None
    frozen = identity.get("model_revisions") if isinstance(identity, dict) else None
    if not isinstance(frozen, dict):
        raise ValueError("The frozen processing model revisions are invalid.")
    revisions = {}
    for name in models:
        aliases = [name] + [
            "whisper/" + choice for choice, key in WHISPER_MODELS.items() if key == name
        ]
        pins = [_model_revision(frozen[key]) for key in aliases if key in frozen]
        if pins:
            if any(pin != pins[0] for pin in pins):
                raise ValueError("The frozen processing model revisions are inconsistent.")
            revisions[name] = pins[0]
        # Missing frozen dependencies stay unknown; today's registry is not recovery evidence.
    return revisions


def asr_model_revisions(models, evidence, *, job=None):
    """Retained ASR receipts describe their actual pin, including older cached outputs."""
    revisions = _model_revisions(models, job)
    if evidence:
        pin = evidence["language_evidence"][0]["model"]
        revisions[WHISPER_MODELS[pin["choice"]]] = {key: pin[key] for key in ("repo", "revision")}
    return revisions


def automatic_model_revisions(job, options, metadata):
    """Captured ASR pin plus known frozen dependencies; never today's registry on recovery."""
    decision = metadata["language_decision"]
    revisions = {
        WHISPER_MODELS[options.whisper_model]: {
            key: decision["model"][key] for key in ("repo", "revision")
        }
    }
    remote = job["params"].get("ucloud_api", {})
    frozen = remote.get("batch_plan", {}).get("identity", {}).get("model_revisions", {})
    names = (
        [decision["effective"]] if metadata["alignment_outcome"]["state"] == "aligned" else []
    ) + (["speakers"] if options.diarize else [])
    for name in names:
        if name not in frozen:
            continue  # Local legacy dependencies have no frozen pin evidence.
        revisions[name] = _model_revision(frozen[name])
    return revisions


def pending_job_stages(folder, params, options):
    """Completed bulk targets can be finalized even when their runtime is unavailable."""
    if params.get("retranscription") or params.get("transcription"):
        # These keep their stage checkpoints for the whole job, not per passage.
        return bool(pending_stages(folder, options))
    units = params.get("timing_units")
    if units is not None:
        return any(
            pending_stages(folder / "units" / f"{index:05d}", options)
            for index in range(len(units))
        )
    targets = params.get("segment_ids")
    if targets is not None:
        return any(
            pending_stages(folder / "passages" / f"{index:05d}", options)
            for index in range(len(targets))
        )
    return bool(pending_stages(folder, options))


def passage_inputs(source, segment_ids, options, duration):
    """Validate a complete explicit selection before accepting any bulk repair work."""
    if not isinstance(segment_ids, list) or not 0 < len(segment_ids) <= 1000:
        raise ValueError("Choose between one and 1,000 passages for local repair.")
    if any(
        not isinstance(identifier, str) or not 0 < len(identifier) <= 100
        for identifier in segment_ids
    ):
        raise ValueError("Passage identifiers must be nonempty text of at most 100 characters.")
    if len(set(segment_ids)) != len(segment_ids):
        raise ValueError("Choose each passage only once for local repair.")
    targets = set(segment_ids)
    selected = [segment for segment in source["segments"] if segment["id"] in targets]
    if len(selected) != len(targets):
        raise ValueError("A selected passage does not belong to the saved result.")
    for segment in selected:
        passage_input(source | {"segments": [segment]}, segment["id"], options, duration)
    frozen = source | {"segments": copy.deepcopy(selected)}
    validate_input(frozen, options, duration)
    return frozen


def passage_input(source, segment_id, options, duration):
    """Freeze one real window; never infer bounds from text length or adjacent passages."""
    if (
        options.execution != "local"
        or not options.align
        or options.diarize
        or options.allow_downloads
    ):
        raise ValueError("Passage repair uses local word alignment with cached models only.")
    segment = next((s for s in source["segments"] if s["id"] == segment_id), None)
    if segment is None:
        raise ValueError("This passage does not belong to the selected saved result.")
    if segment["word_timing"]:
        raise ValueError("This passage already has usable word timing.")
    selected = source | {"segments": [copy.deepcopy(segment)]}
    validate_input(selected, options, duration)
    start, end = segment["start"], segment["end"]
    if start is None or end is None or not 0 <= start < end <= duration:
        raise ValueError(
            "Passage repair needs a known positive audio interval. Use full-result enhancement."
        )
    if (
        end - start > MAX_WINDOW_SECONDS
        or len(segment["text"]) > MAX_WINDOW_CHARS
        or len(segment["text"].split()) > MAX_WINDOW_WORDS
    ):
        raise ValueError(
            "This passage exceeds the local repair limit. Use full-result enhancement."
        )
    return selected


def unbounded(segment):
    return segment["start"] is None or segment["end"] is None


def joined_text(members):
    """One alignment text for adjacent passages, with each passage's character range."""
    text, ranges = "", []
    for member in members:
        if text and not text[-1].isspace() and not member["text"][:1].isspace():
            text += " "
        ranges.append((len(text), len(text) + len(member["text"])))
        text += member["text"]
    return text, ranges


def timing_units(source, segment_ids, options, duration, *, skip_invalid=False):
    """Passage word timing (ADR 0056/0058): what to align, and the audio it may be found in.

    A bounded passage uses its own interval. A passage without its own start and end is aligned
    together with adjacent passages like it, searched for only between the neighbouring known
    times (or the recording's start/end). That window only limits the search: it is never
    stored as their timing, and uncertain words stay untimed. `skip_invalid` (Repair all)
    leaves out passages that cannot be timed alone instead of refusing the whole request."""
    if (
        options.execution != "local"
        or not options.align
        or options.diarize
        or options.allow_downloads
    ):
        raise ValueError("Passage word timing uses local alignment with cached models only.")
    if not isinstance(segment_ids, list) or not 0 < len(segment_ids) <= 1000:
        raise ValueError("Choose between one and 1,000 passages for word timing.")
    segments = source["segments"]
    position = {s["id"]: i for i, s in enumerate(segments)}
    if any(i not in position for i in segment_ids):
        raise ValueError("A selected passage does not belong to the saved result.")
    units, covered = [], set()
    for index in sorted({position[i] for i in segment_ids}):
        if index in covered:
            continue
        if segments[index]["word_timing"]:
            raise ValueError("This passage already has usable word timing.")
        if not unbounded(segments[index]):
            first = last = index
            start, end = segments[index]["start"], segments[index]["end"]
        else:
            first = last = index
            while first > 0 and unbounded(segments[first - 1]):
                first -= 1
            while last + 1 < len(segments) and unbounded(segments[last + 1]):
                last += 1
            start = next(
                (
                    segments[j]["end"]
                    for j in range(first - 1, -1, -1)
                    if segments[j]["end"] is not None
                ),
                0,
            )
            end = next(
                (
                    segments[j]["start"]
                    for j in range(last + 1, len(segments))
                    if segments[j]["start"] is not None
                ),
                duration,
            )
        members = segments[first : last + 1]
        text, _ = joined_text(members)
        if start is not None and end is not None:
            # Only the audio that exists is decoded (ends may exceed it by rounding).
            start, end = max(start, 0), min(end, duration)
        refusal = (
            "This passage has no audio interval to search. Use Add word timing / speakers "
            "for the whole result."
            if start is None or end is None or not start < end
            else "This passage exceeds the local word-timing limit (60 seconds, 120 words). "
            "Use Add word timing / speakers for the whole result."
            if end - start > MAX_WINDOW_SECONDS
            or len(text) > MAX_WINDOW_CHARS
            or len(text.split()) > MAX_WINDOW_WORDS
            else None
        )
        if refusal:
            if skip_invalid:
                continue
            raise ValueError(refusal)
        covered.update(range(first, last + 1))
        units.append({"ids": [s["id"] for s in members], "start": start, "end": end})
    if not units:
        raise ValueError(
            "None of these passages can be timed on their own. Use Add word timing / speakers "
            "for the whole result."
        )
    validate_input(source | {"segments": [segments[i] for i in sorted(covered)]}, options, duration)
    return units


def check_timing_units(source, units, options, duration):
    """Retry check for frozen units: the snapshot holds only their passages, not neighbours."""
    if options.execution != "local" or not options.align or options.diarize:
        raise ValueError("Passage word timing uses local alignment with cached models only.")
    by_id = {s["id"]: s for s in source["segments"]}
    if not isinstance(units, list) or not 0 < len(units) <= 1000:
        raise ValueError("This word-timing job has no passages to align.")
    for unit in units:
        members = [by_id.get(i) for i in unit.get("ids") or []]
        start, end = unit.get("start"), unit.get("end")
        text, _ = joined_text([m for m in members if m])
        if (
            not members
            or None in members
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or not 0 <= start < end <= duration
            or end - start > MAX_WINDOW_SECONDS
            or len(text) > MAX_WINDOW_CHARS
            or len(text.split()) > MAX_WINDOW_WORDS
        ):
            raise ValueError("This word-timing job's frozen passages are invalid. Start it again.")
    validate_input(source, options, duration)


def run_timing_units(queue, job, recording, options, source, units):
    """Align each unit in its window; split words back to their passages by character."""
    folder = queue.store.directory(recording["id"]) / "enhancements" / job["id"]
    by_id = {s["id"]: s for s in source["segments"]}
    total = sum(len(unit["ids"]) for unit in units)
    outputs, done = {}, 0
    for index, unit in enumerate(units):
        if queue.cancel_event.is_set():
            raise media.Cancelled()
        members = [by_id[i] for i in unit["ids"]]
        text, ranges = joined_text(members)
        window = normalize_segments(
            [
                {
                    "text": text,
                    "start": unit["start"],
                    "end": unit["end"],
                    "speaker": members[0]["speaker"],
                }
            ]
        )[0] | {"id": unit["ids"][0]}
        queue.store.update_job(
            job["id"], message=f"Aligning passage {done + 1} of {total}…", progress=done / total
        )
        fragments, _ = enhance_window(
            queue,
            job,
            recording,
            options,
            source | {"segments": [window]},
            folder / "units" / f"{index:05d}",
            scoped=True,
            **({"timing_unit": index} if job["params"].get("timing_artifact_policy") else {}),
            progress=lambda message, done=done: queue.store.update_job(
                job["id"], message=f"Passage {done + 1} of {total} · {message}"
            ),
        )
        words = fragments[0]["words"] if len(fragments) == 1 else []
        for member, (left, right) in zip(members, ranges, strict=True):
            mine = [
                {"word": w["word"], "start": w["start"], "end": w["end"]}
                for w in words
                if left <= w["char_start"] < right
            ]
            if not any(w["start"] is not None and w["end"] is not None for w in mine):
                continue  # Nothing found for this passage: leave it unchanged.
            row = normalize_segments(
                [
                    {
                        "text": member["text"],
                        "start": member["start"],
                        "end": member["end"],
                        "words": mine,
                    }
                ]
            )[0]
            output = {"words": row["words"], "word_timing": row["word_timing"]}
            if unbounded(member) and row["word_timing"]:
                # Bounds come only from the passage's own, completely aligned words.
                output |= {"start": row["words"][0]["start"], "end": row["words"][-1]["end"]}
            outputs[member["id"]] = output
        done += len(members)
    params = job["params"]
    complete = sum(output["word_timing"] for output in outputs.values())
    from . import timing_capture

    if timing_capture.enabled(job):
        timing_capture.publish_output(
            queue.store,
            job,
            {"passages": outputs},
            f"Complete word timing for {complete} of {total} passages.",
        )
        queue.store.update_job(job["id"], progress=1)
        return
    if outputs:
        params["realign_output"] = {"passages": outputs}
    queue.store.update_job(
        job["id"],
        params=params,
        progress=1,
        message=f"Complete word timing for {complete} of {total} passages."
        if outputs
        else "Word timing could not be found in these passages' audio.",
    )


def validate_input(source, options, duration):
    if options.language == "auto":
        raise ValueError("Automatic language is not available for existing transcript text.")
    if options.execution == "local":
        python_path(options)
    elif options.allow_downloads:
        raise ValueError("UCloud enhancement requires a prepared runtime and cached models.")
    if not source["segments"]:
        raise ValueError("Choose a saved transcript before enhancement.")
    if duration is None or not math.isfinite(duration) or not 0 < duration <= 6 * 3600:
        raise ValueError("Local enhancement needs a known recording duration of at most six hours.")
    missing = any(s["start"] is None or s["end"] is None for s in source["segments"])
    if missing and not options.align:
        raise ValueError(
            "Untimed text needs word alignment before speaker detection can locate its words."
        )
    if sum(len(re.findall(r"\S+", s["text"])) for s in source["segments"]) > MAX_WORDS:
        raise ValueError("Enhancement supports at most 100,000 source words per run.")
    if any(s["end"] is not None and s["end"] > duration + 0.1 for s in source["segments"]):
        raise ValueError("Transcript intervals exceed the recording duration.")
    return missing


def read_output(path):
    if path.stat().st_size > MAX_TRANSCRIPT_BYTES:
        raise ValueError("Enhancement output exceeds the 32 MB limit.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("The local runtime returned malformed enhancement output.") from exc


def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass  # Always stop the direct process even if the OS tree command failed.
    else:
        os.killpg(process.pid, signal.SIGKILL)
    if process.poll() is None:
        process.kill()
    process.wait()


def run_worker(folder, options, token, cancel, progress, *, runtime=None):
    if runtime is not None:
        return runtime.run(folder, options, cancel, progress)
    if cancel.is_set():
        raise media.Cancelled()
    (folder / "error.json").unlink(missing_ok=True)
    process = subprocess.Popen(
        [python_path(options), "-I", str(WORKER), str(folder)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=child_environment(options.allow_downloads, token),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    try:
        monitor_worker(process, folder, options, cancel, progress)
    finally:
        stop_process(process)


def monitor_worker(process, folder, options, cancel, progress, *, persistent=False):
    deadline = time.monotonic() + options.timeout_minutes * 60
    previous = None
    try:
        while process.poll() is None and not (persistent and (folder / "done.json").exists()):
            if cancel.wait(0.2):
                raise media.Cancelled()
            if time.monotonic() > deadline:
                raise ValueError(
                    "Enhancement exceeded its time limit. "
                    "Choose a longer limit or smaller recording."
                )
            path = folder / "progress.json"
            if path.exists():
                value = read_output(path)
                stage = value.get("stage") if isinstance(value, dict) else None
                if stage in STAGES and stage != previous:
                    progress(STAGES[stage])
                    previous = stage
        if cancel.is_set():
            raise media.Cancelled()
        if (
            process.returncode
            or (persistent and not (folder / "done.json").exists())
            or (folder / "error.json").exists()
        ):
            error = read_output(folder / "error.json") if (folder / "error.json").exists() else {}
            code = error.get("code") if isinstance(error, dict) else None
            raise ValueError(ERRORS.get(code, ERRORS["processing"]))
    except BaseException:
        stop_process(process)
        raise


def merge_output(source, alignment, diarization, options, duration, scope):
    """Validate untrusted runtime output and retain every character of the frozen text."""
    segments = copy.deepcopy(source["segments"])
    if options.align:
        rows = alignment.get("passages") if isinstance(alignment, dict) else None
        if not isinstance(rows, list) or len(rows) != len(segments):
            raise ValueError("Alignment output does not match the source passages.")
        by_id = {row.get("id"): row for row in rows if isinstance(row, dict)}
        if len(by_id) != len(rows) or set(by_id) != {s["id"] for s in segments}:
            raise ValueError("Alignment passage IDs do not match this saved result.")
        for segment in segments:
            words = by_id[segment["id"]].get("words")
            tokens = re.findall(r"\S+", segment["text"])
            if not isinstance(words, list) or len(words) != len(tokens):
                raise ValueError("Alignment words do not match the preserved source text.")
            clean = []
            previous_start = -1
            for token, word in zip(tokens, words, strict=True):
                if not isinstance(word, dict) or word.get("word") != token:
                    raise ValueError(
                        "Enhancement cannot replace the existing transcript's wording."
                    )
                start, end, score = word.get("start"), word.get("end"), word.get("score")
                valid = all(
                    isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                    for v in (start, end, score)
                )
                if valid and not 0 <= start <= end <= duration + 0.1:
                    raise ValueError("Alignment returned an interval outside the recording.")
                if valid and start < previous_start:
                    raise ValueError("Alignment word intervals are out of order.")
                if valid:
                    previous_start = start
                if valid and segment["start"] is not None and segment["end"] is not None:
                    if start < segment["start"] - 0.1 or end > segment["end"] + 0.1:
                        raise ValueError("Alignment returned a word outside its passage window.")
                reliable = (
                    valid
                    and end > start
                    and (options.timing_policy == "retain-estimates" or score >= options.min_score)
                )
                if not reliable and not re.search(r"\w", token):
                    continue  # Punctuation/emoji need no invented speech interval.
                clean.append(
                    {
                        "word": token,
                        "start": start if reliable else None,
                        "end": end if reliable else None,
                        # Alignment fit becomes a review hint; it is not stored in the words.
                        "score": score if valid else None,
                    }
                )
            start, end = segment["start"], segment["end"]
            if (
                (start is None or end is None)
                and clean
                and all(w["start"] is not None for w in clean)
            ):
                start, end = clean[0]["start"], clean[-1]["end"]
            # Passage-level model hints (e.g. from a WhisperX transcription) survive alignment.
            passage_hints = {k: v for k, v in (segment.get("_hints") or {}).items() if k != "words"}
            segment.update(
                normalize_segments(
                    [
                        {
                            "text": segment["text"],
                            "start": start,
                            "end": end,
                            "speaker": segment["speaker"],
                            "words": clean,
                        }
                    ]
                )[0]
                | {"id": segment["id"]}
            )
            if passage_hints:
                segment["_hints"] = passage_hints | (segment.get("_hints") or {})

    # Retain actual sentence bounds when only some words align. Splitting a bounded
    # passage discarded replay for its untimed fragments. An unlocated source still
    # needs separate reliable runs: their word intervals do not locate the gaps.
    fragments = []
    for segment in segments:
        words = segment["words"]
        reliable = [w["start"] is not None and w["end"] is not None for w in words]
        bounded = (
            segment["start"] is not None
            and segment["end"] is not None
            and segment["end"] > segment["start"]
        )
        if not options.align or not any(reliable) or all(reliable) or bounded:
            fragments.append(segment)
            continue
        groups = []
        for word, timed in zip(words, reliable, strict=True):
            if groups and groups[-1][0] == timed:
                groups[-1][1].append(word)
            else:
                groups.append((timed, [word]))
        begin = 0
        for index, (timed, group) in enumerate(groups):
            finish = (
                groups[index + 1][1][0]["char_start"]
                if index + 1 < len(groups)
                else len(segment["text"])
            )
            fragment = normalize_segments(
                [
                    {
                        "text": segment["text"][begin:finish],
                        "speaker": segment["speaker"],
                        "start": group[0]["start"] if timed else None,
                        "end": group[-1]["end"] if timed else None,
                        "words": group,
                    }
                ]
            )[0]
            if hints := slice_hints(segment.get("_hints"), begin, finish):
                fragment["_hints"] = hints
            fragments.append(fragment)
            begin = finish
    segments = fragments
    # A new result restores to the frozen reviewed text; earlier originals stay in its source.
    for index, segment in enumerate(segments):
        segment.update(
            id=str(index),
            original_text=segment["text"],
            original_words=copy.deepcopy(segment["words"]),
            original_word_timing=segment["word_timing"],
            edited=False,
        )
    speakers = copy.deepcopy(source["speakers"])
    if not options.diarize:
        used = {segment["speaker"] for segment in segments}
        return segments, {raw: name for raw, name in speakers.items() if raw in used}
    turns = diarization.get("turns") if isinstance(diarization, dict) else None
    if not isinstance(turns, list) or len(turns) > 200_000:
        raise ValueError("Speaker output must contain a bounded list of intervals.")
    indexed = []
    names = {}
    for turn in turns:
        if not isinstance(turn, dict):
            raise ValueError("Invalid speaker interval.")
        a, b, speaker = turn.get("start"), turn.get("end"), turn.get("speaker")
        if (
            not all(
                isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v)
                for v in (a, b)
            )
            or not 0 <= a < b <= duration + 0.1
        ):
            raise ValueError("Speaker intervals must stay within the recording.")
        if not isinstance(speaker, str) or not 0 < len(speaker) <= 100:
            raise ValueError("Invalid speaker label.")
        if speaker not in names:
            # Unnamed: the display default (Speaker_01, ...) is derived from the raw label.
            names[speaker] = f"ENH_{scope}_{len(names) + 1}"
        indexed.append((a, b, names[speaker]))
    indexed.sort()
    starts, maximum_ends, maximum = [], [], 0
    for a, b, _ in indexed:
        starts.append(a)
        maximum = max(maximum, b)
        maximum_ends.append(maximum)
    mixed = f"ENH_{scope}_MULTIPLE"
    speakers[mixed] = "Multiple speakers (passage)"

    def label(a, b, fallback):
        if a is None or b is None:
            return fallback
        counts = {}
        for start, end, speaker in indexed[bisect_right(maximum_ends, a) : bisect_left(starts, b)]:
            counts[speaker] = counts.get(speaker, 0) + max(0, min(b, end) - max(a, start))
        if not counts:
            return fallback
        winner = max(counts, key=counts.get)
        return winner if counts[winner] >= 0.8 * sum(counts.values()) else mixed

    result = []
    for segment in segments:
        if not segment["word_timing"]:
            segment["speaker"] = label(segment["start"], segment["end"], segment["speaker"])
            result.append(segment)
            continue
        groups = []
        for word in segment["words"]:
            speaker = label(word["start"], word["end"], segment["speaker"])
            if groups and groups[-1][0] == speaker:
                groups[-1][1].append(word)
            else:
                groups.append((speaker, [word]))
        begin = 0
        for index, (speaker, words) in enumerate(groups):
            finish = (
                groups[index + 1][1][0]["char_start"]
                if index + 1 < len(groups)
                else len(segment["text"])
            )
            fragment = normalize_segments(
                [
                    {
                        "text": segment["text"][begin:finish],
                        "start": words[0]["start"],
                        "end": words[-1]["end"],
                        "speaker": speaker,
                        "words": words,
                    }
                ]
            )[0]
            markers = ("manual_timing", "alignment_hints_stale")
            if not options.align and any(any(key in word for key in markers) for word in words):
                # This speaker-only split copies frozen words. Fresh alignment
                # owns its new timing and never inherits an earlier marker.
                if len(fragment["words"]) != len(words) or any(
                    any(source.get(key) != target.get(key) for key in ("word", "start", "end"))
                    or any(key in source and source[key] is not True for key in markers)
                    for source, target in zip(words, fragment["words"], strict=True)
                ):
                    raise ValueError("The speaker result cannot preserve manual timing evidence.")
                for source, target in zip(words, fragment["words"], strict=True):
                    for key in markers:
                        if source.get(key) is True:
                            target[key] = True
                fragment["original_words"] = copy.deepcopy(fragment["words"])
            if hints := slice_hints(segment.get("_hints"), begin, finish):
                fragment["_hints"] = hints
            result.append(fragment)
            begin = finish
    for index, segment in enumerate(result):
        segment["id"] = str(index)
    # Keep only names this result uses; another result's voices are not this result's speakers.
    used = {segment["speaker"] for segment in result}
    return result, {raw: name for raw, name in speakers.items() if raw in used}


def remote_job_id(queue, job):
    """Read shared allocation ownership without copying its mutable journal to a member."""
    if job["params"].get("runtime_batch_id"):
        from . import runtime_store

        batch = runtime_store.for_job(queue.store, job)
        return batch["journal"].get("job_id")
    return job["params"].get("ucloud_run", {}).get("job_id")


def enhance_window(
    queue,
    job,
    recording,
    options,
    selected,
    folder,
    *,
    scoped=False,
    progress=None,
    merged=None,
    timing_unit=None,
):
    """Execute one resumable window; persistence of the final result belongs to the caller."""
    if queue.cancel_event.is_set():
        raise media.Cancelled()
    if progress is None:

        def progress(message):
            queue.store.update_job(job["id"], message=message, progress=0.5)

    offset = selected["segments"][0]["start"] if scoped else 0
    duration = selected["segments"][0]["end"] - offset if scoped else recording["duration"]
    api = options.execution == "ucloud" and "ucloud_api" in job["params"]
    if api and not job["params"].get("runtime_batch_id"):
        from .enhancement_api import frozen
        from .providers import require_approval as require_provider_approval

        params, settings = frozen(job)
        require_provider_approval(
            queue.store, recording["project_id"], params["provider_id"], settings
        )
    elif not api and options.execution == "ucloud":
        from .enhancement_cloud import CloudEnhancementSettings, require_approval

        require_approval(
            queue.store,
            recording["project_id"],
            CloudEnhancementSettings.model_validate(job["params"]["cloud_settings"]),
        )
    # Transcription has no frozen text: its passages come from the transcription stage.
    whole = validate_input(selected, options, recording["duration"]) if selected else False
    folder.mkdir(parents=True, exist_ok=True)
    request = {
        "passages": [
            {
                key: (s[key] - offset if key in {"start", "end"} and s[key] is not None else s[key])
                for key in ("id", "text", "start", "end")
            }
            # Re-transcription sends only audio bounds, never the reviewed wording.
            | ({"text": ""} if options.retranscribe else {})
            for s in selected["segments"]
        ]
        if options.align and selected
        else [],
        "options": options.model_dump(exclude={"runtime_python", "prepare_runtime"})
        | {
            "duration": duration,
            "whole_recording": whole,
        },
    }
    from . import local_asr_capture, timing_capture

    capture_policy = job["params"].get("producer_artifact_policy") in local_asr_capture.POLICIES
    capture_enabled = capture_policy and (
        job["params"].get("producer_artifact_capture") is not None
        or not any((folder / (stage + ".json")).exists() for stage in local_asr_capture.STAGES)
    )
    timing_enabled = timing_unit is not None and timing_capture.capture_enabled(
        job, folder, timing_unit
    )
    if capture_enabled:
        if not local_asr_capture.eligible(job["params"], job["kind"]) or scoped:
            raise ValueError("Producer artifact capture requires a supported local job.")
        request["producer_artifact_policy"] = job["params"]["producer_artifact_policy"]
        local_asr_capture.verify_request(
            job, folder, json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")
        )
        previous_capture = job["params"].get("producer_artifact_capture")
        if previous_capture is not None:
            local_asr_capture.verify_files(previous_capture, folder)
    if timing_enabled:
        request["producer_artifact_policy"] = timing_capture.POLICY
        timing_capture.verify_request(
            job,
            folder,
            timing_unit,
            json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8"),
        )
    write_json(folder / "request.json", request)

    def default_merged():
        alignment = read_output(folder / "alignment.json") if options.align else None
        if scoped and isinstance(alignment, dict):
            # The persisted checkpoint stays clip-relative; each retry restores offsets once.
            for row in alignment.get("passages", []):
                for word in row.get("words", []):
                    for key in ("start", "end"):
                        value = word.get(key)
                        if isinstance(value, (float, int)) and not isinstance(value, bool):
                            word[key] = value + offset
        diarization = read_output(folder / "diarization.json") if options.diarize else None
        return merge_output(
            selected or transcription_source(folder),
            alignment,
            diarization,
            options,
            recording["duration"],
            job["id"][:8],
        )

    merged = merged or default_merged
    stages_pending = pending_stages(folder, options)
    if capture_enabled and not stages_pending:
        local_asr_capture.begin(queue.store, job, folder)
    if timing_enabled and not stages_pending:
        timing_capture.begin_unit(queue.store, job, folder, timing_unit)
    if options.transcribe and not stages_pending:
        # Cached ASR must be validated before settling an old remote allocation or cleanup.
        merged()
    if stages_pending or job["params"].get("runtime_batch_id"):
        if options.execution == "local":
            require_local_runtime(options)
            if queue.cancel_event.is_set():
                raise media.Cancelled()
        original_input = job["params"].get("audio_input")
        from . import remote_audio_input

        remote_source = remote_audio_input.for_job(job["params"]) if not scoped else None
        if stages_pending and remote_source is not None:
            remote_audio_input.publish(
                folder / remote_audio_input.DESCRIPTOR, remote_audio_input.encoded(remote_source)
            )
        elif stages_pending and (original_input is not None or not (folder / "audio.wav").exists()):
            from .jobs import preparation_slot

            progress("Preparing local enhancement audio…")
            pending = folder / "audio.pending.wav"

            def prepare_guard():
                if queue.cancel_event.is_set():
                    raise media.Cancelled()

            with preparation_slot(getattr(queue, "runtime_preparation_gate", None), prepare_guard):
                if original_input is not None:
                    from . import audio_inputs

                    receipt = audio_inputs.prepare_wav(
                        queue.store.directory(recording["id"]),
                        recording,
                        original_input,
                        folder,
                        offset=offset,
                        duration=duration,
                        cancel=queue.cancel_event,
                    )
                    queue.store.record_audio_preparation(job["id"], receipt)
                    job["params"] = queue.store.job(job["id"])["params"]
                else:
                    media.ffmpeg(
                        [
                            *(["-ss", str(float(offset))] if scoped else []),
                            "-i",
                            str(queue.store.directory(recording["id"]) / "audio.mp3"),
                            *(["-t", str(float(duration))] if scoped else []),
                            "-vn",
                            "-ac",
                            "1",
                            "-ar",
                            "16000",
                            "-c:a",
                            "pcm_s16le",
                            str(pending),
                        ],
                        queue.cancel_event,
                    )
            if original_input is None:
                pending.replace(folder / "audio.wav")
        if api:
            from .enhancement_api import run_api

            run_api(queue, job, recording, folder, options, merged)
        elif options.execution == "ucloud":
            from .enhancement_cloud import run_cloud

            run_cloud(queue, job, recording, folder, options)
        else:
            extra = {}
            if scoped:
                from .local_runtime import LocalPassageRuntime

                if not getattr(queue, "passage_runtime", None):
                    queue.passage_runtime = LocalPassageRuntime()
                extra["runtime"] = queue.passage_runtime
            capture = local_asr_capture.begin(queue.store, job, folder) if capture_enabled else None
            timing = (
                timing_capture.begin_unit(queue.store, job, folder, timing_unit)
                if timing_enabled
                else None
            )
            existing_stages = {
                stage for stage in local_asr_capture.STAGES if (folder / (stage + ".json")).exists()
            }
            try:
                run_worker(
                    folder,
                    options,
                    queue.credentials.get(HF_SCOPE),
                    queue.cancel_event,
                    progress,
                    **extra,
                )
            finally:
                # run_worker drains its child before returning/raising. Valid stages remain
                # useful even if a later stage failed; no preexisting gap is backfilled.
                if capture is not None:
                    for stage in local_asr_capture.STAGES:
                        if stage in existing_stages or not (folder / (stage + ".json")).exists():
                            continue
                        if stage == "transcription":
                            asr_language_evidence(
                                folder, options, job=job, duration=duration, include_alignment=False
                            )
                            transcription_source(folder, allow_empty=options.language == "auto")
                        else:
                            stage_options = options.model_copy(
                                update={
                                    "align": stage == "alignment",
                                    "diarize": stage == "diarization",
                                }
                            )
                            merge_output(
                                selected
                                or transcription_source(
                                    folder, allow_empty=options.language == "auto"
                                ),
                                read_output(folder / "alignment.json")
                                if stage == "alignment"
                                else None,
                                read_output(folder / "diarization.json")
                                if stage == "diarization"
                                else None,
                                stage_options,
                                duration,
                                job["id"][:8],
                            )
                        local_asr_capture.complete_stage(queue.store, job, folder, stage)
                if (
                    timing is not None
                    and "alignment" not in existing_stages
                    and (folder / "alignment.json").exists()
                ):
                    merged()
                    timing_capture.complete_unit(queue.store, job, folder, timing_unit)
    elif api and job["params"].get("ucloud_run", {}).get("job_id"):
        # Outputs are already local (e.g. after a crash): stop the job and trash its folder.
        from .enhancement_api import settle

        settle(queue.store, queue.credentials, job, validate=merged)
    elif (
        options.execution == "ucloud"
        and job["params"].get("ucloud_run", {}).get("job_id")
        and not job["params"]["ucloud_run"].get("termination_requested")
    ):
        from .enhancement_cloud import terminate_owned

        terminate_owned(
            CloudEnhancementSettings.model_validate(job["params"]["cloud_settings"]),
            queue.credentials,
            job["params"]["ucloud_run"]["job_id"],
        )
        job["params"]["ucloud_run"]["termination_requested"] = True
        queue.store.update_job(job["id"], params=job["params"])
    if queue.cancel_event.is_set():
        raise media.Cancelled()
    result = merged()
    if capture_enabled and (capture := job["params"].get("producer_artifact_capture")) is not None:
        # The normalized snapshot must consume the same bytes the stage receipts observed.
        local_asr_capture.verify_files(capture, folder)
    if timing_enabled and job["params"].get("timing_artifact_capture") is not None:
        timing_capture.verify_files(job, folder, timing_unit)
    return result


def finish_realign(queue, job, fragments):
    params = job["params"]
    if len(fragments) != 1:
        queue.store.update_job(
            job["id"], message="Word timing could not be matched to this passage."
        )
        return
    words = fragments[0]["words"]
    timed = sum(w["start"] is not None and w["end"] is not None for w in words)
    if not timed:
        queue.store.update_job(
            job["id"], message="Word timing could not be found in this passage's audio."
        )
        return
    output = {
        "words": words,
        "word_timing": fragments[0]["word_timing"],
    }
    from . import timing_capture

    if timing_capture.enabled(job):
        timing_capture.publish_output(
            queue.store, job, output, f"Word timing ready for {timed} of {len(words)} words."
        )
        return
    params["realign_output"] = output
    queue.store.update_job(
        job["id"],
        params=params,
        message=f"Word timing ready for {timed} of {len(words)} words.",
    )


def finish_realign_passages(queue, job, replacements):
    """Store per-passage timing for the editor to add as one version (ADR 0056)."""
    params = job["params"]
    outputs = {
        segment_id: {"words": fragments[0]["words"], "word_timing": fragments[0]["word_timing"]}
        for segment_id, fragments in replacements.items()
        # Only an output that times at least one word changes anything.
        if len(fragments) == 1
        and any(w["start"] is not None and w["end"] is not None for w in fragments[0]["words"])
    }
    complete = sum(output["word_timing"] for output in outputs.values())
    if outputs:
        params["realign_output"] = {"passages": outputs}
    queue.store.update_job(
        job["id"],
        params=params,
        progress=1,
        message=f"Complete word timing for {complete} of {len(replacements)} passages."
        if outputs
        else "Word timing could not be found in these passages' audio.",
    )


def replace_passages(source, replacements):
    """Combine independently repaired passages without changing any unselected dictionary."""
    identifiers = {s["id"] for s in source["segments"]}
    next_id = 0
    for segment_id, fragments in replacements.items():
        for index, fragment in enumerate(fragments):
            while str(next_id) in identifiers:
                next_id += 1
            fragment["id"] = segment_id if index == 0 else str(next_id)
            identifiers.add(fragment["id"])
    return [
        fragment
        for segment in source["segments"]
        for fragment in replacements.get(segment["id"], [copy.deepcopy(segment)])
    ]


MAX_RETRANSCRIBE = 200


def retranscription_inputs(source, segment_ids, duration):
    """Selected passages with real, bounded audio intervals, in the source's order."""
    if not isinstance(segment_ids, list) or not 0 < len(segment_ids) <= MAX_RETRANSCRIBE:
        raise ValueError(f"Choose between one and {MAX_RETRANSCRIBE} passages to re-transcribe.")
    if len(set(segment_ids)) != len(segment_ids):
        raise ValueError("Choose each passage only once.")
    selected = [s for s in source["segments"] if s["id"] in set(segment_ids)]
    if len(selected) != len(segment_ids):
        raise ValueError("A selected passage does not belong to the saved result.")
    for segment in selected:
        start, end = segment["start"], segment["end"]
        if start is None or end is None or not 0 <= start < end <= duration:
            raise ValueError(
                "Re-transcription needs passages with a known audio interval. "
                "Untimed passages cannot be cut from the recording."
            )
        if end - start > MAX_WINDOW_SECONDS:
            raise ValueError("Split passages longer than 60 seconds before re-transcribing them.")
    return source | {"segments": copy.deepcopy(selected)}


def retranscribed_source(folder, selected):
    """Selected passages with the worker's new wording; empty results keep the original."""
    data = read_output(folder / "retranscription.json")
    rows = data.get("passages") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("The WhisperX re-transcription output is invalid.")
    found = {row.get("id"): row for row in rows}
    keys = ("avg_logprob", "no_speech_prob", "compression_ratio")
    segments = []
    for passage in selected["segments"]:
        row = found.get(passage["id"]) or {}
        text = row.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        segment = normalize_segments(
            [
                {
                    "text": text,
                    "start": passage["start"],
                    "end": passage["end"],
                    "speaker": passage["speaker"],
                    **{key: row[key] for key in keys if key in row},
                }
            ]
        )[0]
        segments.append(segment | {"id": passage["id"]})
    return selected | {"segments": segments}


def run_retranscription(queue, job, recording, options):
    """T71: one combined result with new wording for the selected passages only."""
    source = job["params"]["source_snapshot"]
    segment_ids = job["params"]["segment_ids"]
    selected = retranscription_inputs(source, segment_ids, recording["duration"])
    folder = queue.store.directory(recording["id"]) / "enhancements" / job["id"]
    window = {}

    def merge(folder=folder):
        new = retranscribed_source(folder, selected)
        window["ids"] = [s["id"] for s in new["segments"]]
        if not new["segments"]:
            return [], {}
        alignment = read_output(folder / "alignment.json")
        return merge_output(new, alignment, None, options, recording["duration"], job["id"][:8])

    merged, _ = enhance_window(queue, job, recording, options, selected, folder, merged=merge)
    if queue.cancel_event.is_set():
        raise media.Cancelled()
    language_evidence = asr_language_evidence(
        folder, options, job=job, selected=selected, duration=recording["duration"]
    )
    # Bounded passages are never split, so merged passages follow the new segments' order.
    replacements = {
        identifier: [segment] for identifier, segment in zip(window["ids"], merged, strict=True)
    }
    segments = replace_passages(source, replacements)
    place = "UCloud" if options.execution == "ucloud" else "Local"
    models = [WHISPER_MODELS[options.whisper_model], options.language]
    remote = job["params"].get("ucloud_api")
    queue.store.add_result(
        recording["id"],
        segments,
        copy.deepcopy(source["speakers"]),
        f"{place} WhisperX {options.whisper_model} re-transcription "
        f"({len(replacements)} of {len(segment_ids)} passages)",
        {
            "kind": "enhancement",
            "operation": "retranscription",
            "execution": options.execution,
            "source_result_id": source["id"],
            "segment_ids": segment_ids,
            "model": options.whisper_model,
            "language": options.language,
            "engine": "whisperx",
            "version": WHISPERX_VERSION,
            "model_revisions": asr_model_revisions(models, language_evidence, job=job),
            "options": options.model_dump(exclude={"runtime_python", "prepare_runtime"}),
            "unchanged_segment_ids": [i for i in segment_ids if i not in replacements],
            **language_evidence,
            **(
                {
                    "ucloud_job_id": remote_job_id(queue, job),
                    "ucloud_server": remote["settings"]["base_url"],
                    "ucloud_provider_id": remote["provider_id"],
                    "runtime_id": remote["runtime_id"],
                    "transport": "api",
                }
                if remote
                else {}
            ),
        },
        job_id=job["id"],
        runtime_receipt=job["params"].get("runtime_output_receipt"),
        producer_artifacts=job["params"].get("producer_artifact_capture"),
        runtime_artifacts=job.get("_runtime_artifacts"),
    )


def run_transcription(queue, job, recording, options):
    """Full WhisperX transcription (T70): appends an ordinary provider result."""
    folder = queue.store.directory(recording["id"]) / "enhancements" / job["id"]
    snapshot = None

    def merged():
        nonlocal snapshot
        if snapshot is None:
            metadata = asr_language_evidence(
                folder, options, job=job, duration=recording["duration"]
            )
            automatic = options.language == "auto"
            models = (
                automatic_model_revisions(job, options, metadata)
                if automatic
                else asr_model_revisions(
                    [WHISPER_MODELS[options.whisper_model], options.language]
                    + (["speakers"] if options.diarize else []),
                    metadata,
                    job=job,
                )
            )
            segments, speakers = merge_output(
                transcription_source(folder, allow_empty=automatic),
                read_output(folder / "alignment.json") if options.align else None,
                read_output(folder / "diarization.json") if options.diarize else None,
                options,
                recording["duration"],
                job["id"][:8],
            )
            snapshot = copy.deepcopy((segments, speakers, metadata, models))
        return copy.deepcopy(snapshot[:2])

    enhance_window(queue, job, recording, options, None, folder, merged=merged)
    if queue.cancel_event.is_set():
        raise media.Cancelled()
    segments, speakers = merged()
    _, _, language_evidence, models = snapshot
    place = "UCloud" if options.execution == "ucloud" else "Local"
    label = f"{place} WhisperX {options.whisper_model}"
    aligned = True
    if options.language == "auto":
        aligned = language_evidence["alignment_outcome"]["state"] == "aligned"
        if not segments:
            label += (
                " (no speech detected)"
                if language_evidence["language_decision"]["status"] == "no_speech"
                else " (no ASR text)"
            )
        elif not aligned:
            label += (
                " (word timing not requested)"
                if language_evidence["alignment_outcome"]["state"] == "not_requested"
                else " (word timing unavailable)"
            )
    remote = job["params"].get("ucloud_api")
    queue.store.add_result(
        recording["id"],
        segments,
        speakers,
        label,
        {
            "kind": "provider",
            "provider_type": "whisperx",
            "provider_id": job["params"].get("provider_id"),
            "provider_name": job["params"].get(
                "provider_name", f"WhisperX {options.whisper_model}"
            ),
            "model": options.whisper_model,
            "language": options.language,
            "capability": ("words_speakers" if options.diarize else "words")
            if aligned
            else ("speakers" if options.diarize else "segments"),
            "execution": options.execution,
            "engine": "whisperx",
            "version": WHISPERX_VERSION,
            "model_revisions": models,
            "options": options.model_dump(exclude={"runtime_python", "prepare_runtime"}),
            **language_evidence,
            **(
                {
                    "ucloud_job_id": remote_job_id(queue, job),
                    "ucloud_server": remote["settings"]["base_url"],
                    "ucloud_provider_id": remote["provider_id"],
                    "runtime_id": remote["runtime_id"],
                    "transport": "api",
                }
                if remote
                else {}
            ),
        },
        job_id=job["id"],
        runtime_receipt=job["params"].get("runtime_output_receipt"),
        producer_artifacts=job["params"].get("producer_artifact_capture"),
        runtime_artifacts=job.get("_runtime_artifacts"),
    )


def run_enhancement(queue, job, recording):
    from .audio_inputs import effective_recording

    recording = effective_recording(job, recording)
    if queue.cancel_event.is_set():
        raise media.Cancelled()
    options = EnhancementOptions.model_validate(job["params"]["enhancement"])
    if options.language == "auto" and (
        job["params"].get("transcription") is not True
        or job["params"].get("retranscription")
        or job["params"].get("realign")
        or any(key in job["params"] for key in ("segment_id", "segment_ids", "timing_units"))
    ):
        raise ValueError("Automatic language requires a full-ASR transcription job.")
    if options.execution == "auto":
        raise ValueError("This enhancement has no resolved execution place. Start it again.")
    folder = queue.store.directory(recording["id"]) / "enhancements" / job["id"]
    if options.device == "auto" and pending_job_stages(folder, job["params"], options):
        # Resolved when a stage first runs and recorded on the job (ADR 0059). Complete
        # checkpoints are finalized without checking the runtime.
        options = options.model_copy(update={"device": detected_device(options)})
        job["params"]["enhancement"]["device"] = options.device
        queue.store.update_job(job["id"], params=job["params"])
    if job["params"].get("transcription"):
        return run_transcription(queue, job, recording, options)
    if job["params"].get("retranscription"):
        return run_retranscription(queue, job, recording, options)
    source = job["params"]["source_snapshot"]
    if job["params"].get("timing_units") is not None:
        return run_timing_units(
            queue, job, recording, options, source, job["params"]["timing_units"]
        )
    segment_id = job["params"].get("segment_id")
    segment_ids = job["params"].get("segment_ids")
    if segment_id is not None and segment_ids is not None:
        raise ValueError("Choose one passage or a list of passages, not both.")
    folder = queue.store.directory(recording["id"]) / "enhancements" / job["id"]
    duration = recording["duration"]
    if segment_ids is not None:
        selected = passage_inputs(source, segment_ids, options, duration)
        # Enqueue freezes source order so index-owned checkpoints have a stable identity.
        if [segment["id"] for segment in selected["segments"]] != segment_ids:
            raise ValueError("Frozen passage repair order does not match the saved source.")
        replacements = {}
        for index, segment in enumerate(selected["segments"]):
            if queue.cancel_event.is_set():
                raise media.Cancelled()
            queue.store.update_job(
                job["id"],
                message=f"Repairing passage {index + 1} of {len(segment_ids)}…",
                progress=index / len(segment_ids),
            )
            fragments, _ = enhance_window(
                queue,
                job,
                recording,
                options,
                source | {"segments": [segment]},
                folder / "passages" / f"{index:05d}",
                scoped=True,
                progress=lambda message, index=index: queue.store.update_job(
                    job["id"],
                    message=f"Passage {index + 1} of {len(segment_ids)} · {message}",
                    progress=(index + 0.5) / len(segment_ids),
                ),
            )
            replacements[segment["id"]] = fragments
        if job["params"].get("realign"):
            # Passage word timing (ADR 0056): applied to the same result, never a new one.
            return finish_realign_passages(queue, job, replacements)
        segments = replace_passages(source, replacements)
        speakers = copy.deepcopy(source["speakers"])
    else:
        selected = (
            passage_input(source, segment_id, options, duration)
            if segment_id is not None
            else source
        )
        segments, speakers = enhance_window(
            queue,
            job,
            recording,
            options,
            selected,
            folder,
            scoped=segment_id is not None,
            **({"timing_unit": 0} if job["params"].get("timing_artifact_policy") else {}),
        )
        if job["params"].get("realign"):
            # Timing for an edited passage: the editor applies it to the same result as a
            # timing-only version once no draft is open (ADR 0052). No new result here.
            return finish_realign(queue, job, segments)
        if segment_id is not None:
            segments = replace_passages(source, {segment_id: segments})
            speakers = copy.deepcopy(source["speakers"])
    if queue.cancel_event.is_set():
        raise media.Cancelled()
    # Use the same whitespace words as alignment; standalone punctuation is not speech.
    total = sum(
        sum(bool(re.search(r"\w", w)) for w in re.findall(r"\S+", s["text"])) for s in segments
    )
    timed = sum(
        sum(
            bool(re.search(r"\w", w["word"])) and w["start"] is not None and w["end"] is not None
            for w in s["words"]
        )
        for s in segments
    )
    partial = options.align and timed < total
    models = ([options.language] if options.align else []) + (
        ["speakers"] if options.diarize else []
    )
    if (
        segment_id is None
        and segment_ids is None
        and options.align
        and needs_coarse(selected["segments"], duration)
    ):
        models.append("coarse")
    queue.store.add_result(
        recording["id"],
        segments,
        speakers,
        ("UCloud" if options.execution == "ucloud" else "Local")
        + (
            " WhisperX passage repairs"
            if segment_ids is not None
            else " WhisperX passage repair"
            if segment_id is not None
            else " WhisperX enhancement"
        )
        + (" (partial word timing)" if partial else ""),
        {
            "kind": "enhancement",
            "execution": options.execution,
            "source_result_id": source["id"],
            **({"segment_id": segment_id} if segment_id is not None else {}),
            **({"segment_ids": segment_ids} if segment_ids is not None else {}),
            "engine": "whisperx",
            "version": WHISPERX_VERSION,
            "model_revisions": _model_revisions(models, job),
            "options": options.model_dump(exclude={"runtime_python", "prepare_runtime"}),
            "word_coverage": {"total": total, "timed": timed, "untimed": max(0, total - timed)},
            **(
                {
                    "ucloud_job_id": remote_job_id(queue, job),
                    "ucloud_server": job["params"]["ucloud_api"]["settings"]["base_url"],
                    "ucloud_provider_id": job["params"]["ucloud_api"]["provider_id"],
                    "runtime_id": job["params"]["ucloud_api"]["runtime_id"],
                    "transport": "api",
                }
                if options.execution == "ucloud" and "ucloud_api" in job["params"]
                else {
                    "ucloud_job_id": remote_job_id(queue, job),
                    "ucloud_server": job["params"]["cloud_settings"]["server"],
                    "template_job_id": job["params"]["cloud_settings"]["template_job_id"],
                }
                if options.execution == "ucloud"
                else {}
            ),
        },
        job_id=job["id"],
        runtime_receipt=job["params"].get("runtime_output_receipt"),
        producer_artifacts=job["params"].get("producer_artifact_capture"),
        runtime_artifacts=job.get("_runtime_artifacts"),
    )
