"""Inert selected-audio alignment bundles; no extraction, routing or upload authority."""

import copy
import hashlib
import json
import math
import os
import re
import stat
import struct
import threading
import wave
from pathlib import Path

from . import audio_inputs, repair_sources
from .enhancement import EnhancementOptions, joined_text, passage_inputs, timing_units
from .history import decode_payload, encode_payload, text_hash
from .media import Cancelled

FORMAT = "cbs-repair-bundle-v1"
REQUEST_FORMAT = "cbs-repair-unit-v1"
COMMIT_FORMAT = "cbs-repair-bundle-commit-v1"
OUTPUT_FORMAT = "cbs-repair-alignment-v1"
MAPPED_FORMAT = "cbs-repair-alignment-recording-v1"
RATE = 16000
# Store.record_audio_preparation currently retains at most 200 individually bound windows.
MAX_UNITS = 200
MAX_UNIT_FRAMES = 60 * RATE
MAX_REQUEST_BYTES = 1024 * 1024
MAX_REQUEST_TOTAL_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_COMMIT_BYTES = 4096
MAX_JSON_DEPTH = 24
# Input metadata is not transferred: the container is rewrapped around the same PCM frames.
MAX_WAV_OVERHEAD = 64 * 1024
MAX_BUNDLE_BYTES = (
    MAX_UNITS * (MAX_UNIT_FRAMES * 2 + 44)
    + MAX_REQUEST_TOTAL_BYTES
    + MAX_MANIFEST_BYTES
    + MAX_COMMIT_BYTES
)


def _json(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _failure():
    raise ValueError("The repair bundle is incomplete, changed or invalid.")


def _regular(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _failure()
    return info


def _runtime(value, language):
    if (
        not isinstance(value, dict)
        or set(value) != {"runtime_id", "worker_sha256", "language", "alignment_model"}
        or not isinstance(value["runtime_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", value["runtime_id"])
        or ".." in value["runtime_id"]
        or not _hash(value["worker_sha256"])
        or value["language"] != language
        or language not in {"da", "en"}
        or not isinstance(value["alignment_model"], dict)
        or set(value["alignment_model"]) != {"repo", "revision"}
    ):
        raise ValueError("The frozen repair runtime identity is invalid.")
    model = value["alignment_model"]
    if (
        not isinstance(model["repo"], str)
        or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,199}", model["repo"]
        )
        or ".." in model["repo"]
        or not isinstance(model["revision"], str)
        or not re.fullmatch(r"[a-f0-9]{40}", model["revision"])
    ):
        raise ValueError("The frozen repair model pin is invalid.")
    return copy.deepcopy(value)


def _source_projection(snapshot):
    result = {
        key: copy.deepcopy(value)
        for key, value in snapshot.items()
        if key not in {"recording_id", "decoder"}
    }
    result["decoder_sha256"] = snapshot["decoder"]["sha256"]
    result["snapshot_sha256"] = _sha(_json(snapshot))
    return result


def _capture(store, job_id, runtime=None, *, preparations=True):
    proof = repair_sources.inspect_repair_source(store, job_id)
    if proof["operation"] not in {"timing", "alignment"}:
        raise ValueError("Repair bundles currently support alignment only, not re-transcription.")
    with store.connection() as db:
        db.execute("BEGIN")
        row = db.execute(
            "SELECT recording_id,length(CAST(params AS BLOB)) size FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        if (
            row["recording_id"] != proof["recording_id"]
            or type(row["size"]) is not int
            or not 0 < row["size"] <= repair_sources.MAX_RECORD_BYTES
        ):
            raise ValueError("The frozen repair parameters changed during inspection.")
        encoded = db.execute("SELECT params FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
        if (
            not isinstance(encoded, str)
            or _sha(encoded.encode("utf-8")) != proof["observed_params_sha256"]
        ):
            raise ValueError("The frozen repair parameters changed during inspection.")
        params = repair_sources._read_params(encoded, row["size"])
        parent = db.execute(
            "SELECT length(CAST(v.payload AS BLOB)) size FROM result_versions v "
            "JOIN transcript_results t ON t.id=v.result_id WHERE v.id=? AND v.result_id=? "
            "AND t.recording_id=? AND v.payload_sha256=?",
            (
                proof["source_version_id"],
                proof["result_id"],
                proof["recording_id"],
                proof["source_payload_sha256"],
            ),
        ).fetchone()
        if (
            parent is None
            or type(parent["size"]) is not int
            or not 0 < parent["size"] <= repair_sources.MAX_RECORD_BYTES
            or parent["size"] + row["size"] > repair_sources.MAX_TOTAL_BYTES
        ):
            raise ValueError("The preserved repair version is missing or oversized.")
        payload = db.execute(
            "SELECT payload FROM result_versions WHERE id=?", (proof["source_version_id"],)
        ).fetchone()[0]
        source = decode_payload(payload, proof["source_payload_sha256"])
    selected = [item for item in source["segments"] if item["id"] in set(proof["segment_ids"])]
    if encode_payload(selected, source["speakers"])[1] != proof["projection_sha256"]:
        raise ValueError("The selected repair projection changed during inspection.")
    try:
        snapshot = audio_inputs.validate(params["audio_input"])
        options = EnhancementOptions.model_validate(params["enhancement"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            "This repair lacks a frozen original input and alignment options."
        ) from None
    if (
        snapshot["recording_id"] != proof["recording_id"]
        or snapshot["duration_basis"] not in {"decoded_pcm_frames", "container_observation"}
        or options.execution != "ucloud"
        or options.device != "cuda"
        or options.allow_downloads
        or options.language not in {"da", "en"}
    ):
        raise ValueError("A repair bundle requires fixed-language, frozen UCloud alignment input.")
    if snapshot["duration_basis"] == "container_observation":
        from . import repair_admission

        repair_admission.require_snapshot(params)
    frozen_runtime = _runtime(params.get("repair_runtime"), options.language)
    if runtime is not None and _runtime(runtime, options.language) != frozen_runtime:
        raise ValueError("The supplied repair runtime differs from its frozen identity.")
    historical = {"segments": source["segments"], "speakers": source["speakers"]}
    local_options = options.model_copy(update={"execution": "local", "device": "cpu"})
    if proof["operation"] == "timing":
        units = timing_units(historical, proof["segment_ids"], local_options, snapshot["duration"])
        if proof["source_scope"] == "selected" and units != params.get("timing_units"):
            raise ValueError("The frozen search windows disagree with their preserved neighbours.")
    else:
        passage_inputs(historical, proof["segment_ids"], local_options, snapshot["duration"])
        units = [
            {"ids": [item["id"]], "start": item["start"], "end": item["end"]} for item in selected
        ]
    if (
        not 0 < len(units) <= MAX_UNITS
        or [item for unit in units for item in unit["ids"]] != proof["segment_ids"]
    ):
        raise ValueError(
            "The repair bundle exceeds its bounded unit limit or expands its selection."
        )
    windows = []
    for unit in units:
        start, end = round(unit["start"] * RATE), round(unit["end"] * RATE)
        source_frames = round(snapshot["duration"] * RATE)
        if not 0 <= start < end <= source_frames or end - start > MAX_UNIT_FRAMES:
            raise ValueError("A repair clip exceeds its bounded audio window.")
        windows.append((start, end))
    if not preparations:
        return {
            "source": proof,
            "snapshot": snapshot,
            "units": [
                {**unit, "start_frame": start, "end_frame": end}
                for unit, (start, end) in zip(units, windows, strict=True)
            ],
        }
    receipts = params.get("audio_preparations")
    if not isinstance(receipts, list) or not 0 < len(receipts) <= MAX_UNITS:
        raise ValueError("The repair lacks recorded clip preparation receipts.")
    by_window = {}
    for receipt in receipts:
        receipt = audio_inputs.validate_evidence(receipt, snapshot)
        if (
            set(receipt)
            != {"schema", "policy", "source", "window", "prepared"}
            | ({"timeline"} if snapshot["duration_basis"] == "container_observation" else set())
            or receipt["prepared"]["format"] != "wav"
        ):
            raise ValueError("Repair clips require individual WAV extraction receipts.")
        key = (receipt["window"]["start_frame"], receipt["window"]["end_frame"])
        if key in by_window:
            raise ValueError("The repair has ambiguous clip preparation receipts.")
        by_window[key] = receipt
    by_id = {item["id"]: item for item in selected}
    planned, requests, used = [], [], set()
    for index, (unit, (start, end)) in enumerate(zip(units, windows, strict=True)):
        receipt = by_window.get((start, end))
        if receipt is None:
            raise ValueError("The recorded clip receipt does not match its selected window.")
        if receipt["prepared"]["bytes"] > (end - start) * 2 + MAX_WAV_OVERHEAD:
            raise ValueError("The prepared clip container exceeds its bounded byte limit.")
        used.add((start, end))
        members = [by_id[identifier] for identifier in unit["ids"]]
        text, ranges = joined_text(members)
        passages = [
            {
                "id": item["id"],
                "start": item["start"],
                "end": item["end"],
                "text_sha256": text_hash(item["text"]),
                "char_start": left,
                "char_end": right,
            }
            for item, (left, right) in zip(members, ranges, strict=True)
        ]
        planned.append(
            {
                "index": index,
                "passages": passages,
                "requested_window": {
                    "start": unit["start"],
                    "end": unit["end"],
                    "kind": "search"
                    if any(item["start"] is None or item["end"] is None for item in members)
                    else "passage",
                },
                "window": copy.deepcopy(receipt["window"]),
                "extraction": copy.deepcopy(receipt["prepared"]),
                **(
                    {"timeline": copy.deepcopy(receipt["timeline"])}
                    if "timeline" in receipt
                    else {}
                ),
            }
        )
        requests.append(
            {
                "format": REQUEST_FORMAT,
                "operation": "alignment",
                "unit_index": index,
                "language": options.language,
                "alignment_model": frozen_runtime["alignment_model"],
                "min_score": options.min_score,
                "text": text,
                "ranges": [
                    {
                        "id": item["id"],
                        "char_start": item["char_start"],
                        "char_end": item["char_end"],
                    }
                    for item in passages
                ],
                "audio": {"file": "audio.wav", "frames": end - start, "sample_rate": RATE},
            }
        )
    if used != set(by_window):
        raise ValueError("The repair contains preparation scopes outside its selection.")
    plan = {
        "format": FORMAT,
        "source": {
            key: copy.deepcopy(value)
            for key, value in proof.items()
            if key != "observed_params_sha256"
        },
        "original_audio": _source_projection(snapshot),
        "runtime": frozen_runtime,
        "options_sha256": _sha(_json(params["enhancement"])),
        "options": {
            "align": True,
            "diarize": False,
            "transcribe": False,
            "retranscribe": False,
            "language": options.language,
            "device": "cuda",
            "min_score": options.min_score,
        },
        "units": planned,
        "transfer_frames": sum(
            unit["window"]["end_frame"] - unit["window"]["start_frame"] for unit in planned
        ),
    }
    if "timing_policy" in params["enhancement"]:
        plan["options"]["timing_policy"] = options.timing_policy
        for request in requests:
            request["timing_policy"] = options.timing_policy
    key = _sha(_json(plan))
    request_bytes = [_json(request | {"bundle_key": key}) for request in requests]
    if (
        any(len(data) > MAX_REQUEST_BYTES for data in request_bytes)
        or sum(map(len, request_bytes)) > MAX_REQUEST_TOTAL_BYTES
    ):
        raise ValueError("The repair requests exceed their bounded JSON byte limit.")
    return {
        "manifest": plan,
        "key": key,
        "requests": request_bytes,
        "receipts": [
            by_window[(unit["window"]["start_frame"], unit["window"]["end_frame"])]
            for unit in planned
        ],
        "snapshot": snapshot,
    }


def _filesystem_path(path):
    """Keep owned paths usable beyond Windows' legacy 260-character limit."""
    path = Path(path).absolute()
    if os.name == "nt" and not str(path).startswith("\\\\?\\"):
        value = str(path)
        path = Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)
    return path


def _directory(store, proof, generation):
    if type(generation) is not int or not 1 <= generation < 2**31:
        raise ValueError("Choose a positive local repair-bundle generation.")
    recording = _filesystem_path(store.directory(proof["recording_id"]))
    path = recording / "enhancements" / proof["job_id"] / f"repair-bundle-{generation:06d}"
    return audio_inputs._owned(path, recording, directory_only=True), recording


def _duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _failure()
        result[key] = value
    return result


def _decode(data, limit, *, canonical=True):
    if len(data) > limit:
        _failure()
    depth, quoted, escaped = 0, False, False
    for char in data.decode("utf-8"):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                _failure()
        elif char in "]}":
            depth -= 1
    try:
        value = json.loads(data, object_pairs_hook=_duplicates, parse_constant=lambda _: _failure())
        if canonical and _json(value) != data:
            _failure()
        return value
    except (TypeError, ValueError, RecursionError, OverflowError):
        _failure()


def _read(path, root, limit):
    audio_inputs._owned(path, root)
    before = _regular(path.lstat())
    if not 0 < before.st_size <= limit:
        _failure()
    with path.open("rb") as stream:
        if audio_inputs._signature(before) != audio_inputs._signature(
            _regular(os.fstat(stream.fileno()))
        ):
            _failure()
        data = stream.read(limit + 1)
        after = _regular(os.fstat(stream.fileno()))
    if (
        len(data) != before.st_size
        or audio_inputs._signature(before) != audio_inputs._signature(after)
        or audio_inputs._signature(after) != audio_inputs._signature(_regular(path.lstat()))
    ):
        _failure()
    audio_inputs._owned(path, root)
    return data


def _header(frames):
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        frames * 2 + 36,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        RATE,
        RATE * 2,
        2,
        16,
        b"data",
        frames * 2,
    )


def _audio_info(path, root, frames):
    audio_inputs._owned(path, root)
    if _regular(path.lstat()).st_size != frames * 2 + 44:
        _failure()
    digest, pcm = hashlib.sha256(), hashlib.sha256()
    with path.open("rb") as stream:
        before = _regular(os.fstat(stream.fileno()))
        header = stream.read(44)
        if header != _header(frames):
            _failure()
        digest.update(header)
        count = 0
        while block := stream.read(64 * 1024):
            count += len(block)
            if count > frames * 2:
                _failure()
            digest.update(block)
            pcm.update(block)
        after = _regular(os.fstat(stream.fileno()))
    if (
        count != frames * 2
        or audio_inputs._signature(before) != audio_inputs._signature(after)
        or audio_inputs._signature(after) != audio_inputs._signature(_regular(path.lstat()))
    ):
        _failure()
    audio_inputs._owned(path, root)
    return _audio_descriptor(frames, pcm.hexdigest(), digest.hexdigest())


def _audio_descriptor(frames, pcm_sha256, sha256):
    return {
        "sha256": sha256,
        "bytes": frames * 2 + 44,
        "frames": frames,
        "pcm_sha256": pcm_sha256,
        "sample_rate": RATE,
        "channels": 1,
        "sample_width": 2,
        "format": "wav",
        "container_recipe": "pcm16-wav-container-v1",
    }


def _copy_audio(source_folder, destination, recording_root, snapshot, receipt, cancel):
    source_folder = _filesystem_path(source_folder)
    audio_inputs._owned(source_folder, recording_root, directory_only=True)
    for name in ("audio.wav", "audio.input.json"):
        audio_inputs._owned(source_folder / name, recording_root)
        _regular((source_folder / name).lstat())
    receipt_bytes = _read(
        source_folder / "audio.input.json", recording_root, audio_inputs.MAX_RECEIPT_BYTES
    )
    if (
        audio_inputs.validate_evidence(
            _decode(receipt_bytes, audio_inputs.MAX_RECEIPT_BYTES, canonical=False), snapshot
        )
        != receipt
    ):
        raise ValueError("The prepared clip receipt or selected window changed.")
    source = source_folder / "audio.wav"
    before = _regular(source.lstat())
    if before.st_size != receipt["prepared"]["bytes"]:
        raise ValueError("The prepared clip byte size changed.")
    descriptor = os.open(
        source,
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0),
    )
    with os.fdopen(descriptor, "rb") as raw:
        if audio_inputs._signature(before) != audio_inputs._signature(
            _regular(os.fstat(raw.fileno()))
        ):
            _failure()
        with wave.open(raw, "rb") as audio, destination.open("xb") as output:
            frames = receipt["prepared"]["frames"]
            if (
                audio.getnchannels(),
                audio.getsampwidth(),
                audio.getframerate(),
                audio.getnframes(),
            ) != (1, 2, RATE, frames):
                _failure()
            output.write(_header(frames))
            count, digest = 0, hashlib.sha256()
            while block := audio.readframes(RATE):
                if cancel.is_set():
                    raise Cancelled()
                count += len(block)
                if count > frames * 2:
                    _failure()
                digest.update(block)
                output.write(block)
            if count != frames * 2 or digest.hexdigest() != receipt["prepared"]["pcm_sha256"]:
                _failure()
            output.flush()
            os.fsync(output.fileno())
        raw.seek(0)
        count, digest = 0, hashlib.sha256()
        while True:
            if cancel.is_set():
                raise Cancelled()
            block = raw.read(min(64 * 1024, before.st_size + 1 - count))
            if not block:
                break
            count += len(block)
            if count > before.st_size:
                _failure()
            digest.update(block)
        if count != before.st_size or digest.hexdigest() != receipt["prepared"]["sha256"]:
            _failure()
        if audio_inputs._signature(before) != audio_inputs._signature(
            _regular(os.fstat(raw.fileno()))
        ):
            _failure()
    if audio_inputs._signature(before) != audio_inputs._signature(_regular(source.lstat())):
        _failure()
    for name in ("audio.wav", "audio.input.json"):
        audio_inputs._owned(source_folder / name, recording_root)
        _regular((source_folder / name).lstat())
    if (
        _read(source_folder / "audio.input.json", recording_root, audio_inputs.MAX_RECEIPT_BYTES)
        != receipt_bytes
    ):
        _failure()


def _write(path, data, root):
    audio_inputs._owned(path, root)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    audio_inputs._owned(path, root)
    _regular(path.lstat())


def _manifest(plan, audio, generation):
    value = copy.deepcopy(plan["manifest"])
    value.update(bundle_key=plan["key"], local_generation=generation)
    for index, unit in enumerate(value["units"]):
        unit["audio"] = audio[index]
        unit["request"] = {
            "sha256": _sha(plan["requests"][index]),
            "bytes": len(plan["requests"][index]),
        }
    encoded = _json(value)
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ValueError("The repair manifest exceeds its bounded JSON byte limit.")
    return value, encoded


def _inventory(directory, count):
    files = {"manifest.json", "COMMITTED.json"} | {
        f"units/{i:05d}/{name}" for i in range(count) for name in ("audio.wav", "request.json")
    }
    directories = {"units"} | {f"units/{i:05d}" for i in range(count)}
    seen, total, pending = set(), 0, [directory]
    while pending:
        for child in pending.pop().iterdir():
            relative = child.relative_to(directory).as_posix()
            audio_inputs._owned(child, directory, directory_only=child.is_dir())
            if child.is_dir():
                if relative not in directories:
                    _failure()
                pending.append(child)
            else:
                if relative not in files:
                    _failure()
                total += _regular(child.lstat()).st_size
                if total > MAX_BUNDLE_BYTES:
                    _failure()
                seen.add(relative)
    if seen != files:
        _failure()


def _validate(plan, directory, recording_root, generation):
    try:
        marker = _decode(
            _read(directory / "COMMITTED.json", recording_root, MAX_COMMIT_BYTES), MAX_COMMIT_BYTES
        )
        if (
            not isinstance(marker, dict)
            or set(marker) != {"format", "manifest"}
            or marker["format"] != COMMIT_FORMAT
            or not isinstance(marker["manifest"], dict)
            or set(marker["manifest"]) != {"sha256", "bytes"}
        ):
            _failure()
        data = _read(directory / "manifest.json", recording_root, MAX_MANIFEST_BYTES)
        if marker["manifest"] != {"sha256": _sha(data), "bytes": len(data)}:
            _failure()
        actual = _decode(data, MAX_MANIFEST_BYTES)
        audio = []
        for index, receipt in enumerate(plan["receipts"]):
            folder = directory / "units" / f"{index:05d}"
            request = _read(folder / "request.json", recording_root, MAX_REQUEST_BYTES)
            if request != plan["requests"][index]:
                _failure()
            info = _audio_info(folder / "audio.wav", recording_root, receipt["prepared"]["frames"])
            if info["pcm_sha256"] != receipt["prepared"]["pcm_sha256"]:
                _failure()
            audio.append(info)
        expected, canonical = _manifest(plan, audio, generation)
        if actual != expected or data != canonical:
            _failure()
        _inventory(directory, len(audio))
        return {"directory": directory, "manifest": actual, "sha256": _sha(data)}
    except (OSError, KeyError, TypeError, UnicodeError, wave.Error):
        _failure()


def validate_bundle(store, job_id, *, generation=1):
    """Check a committed local artifact against preserved source evidence, not upload policy."""
    plan = _capture(store, job_id)
    directory, root = _directory(store, plan["manifest"]["source"], generation)
    return _validate(plan, directory, root, generation)


def preparation_plan(store, job_id):
    """Read exact selected sample windows before extraction; this is not upload authority."""
    return _capture(store, job_id, preparations=False)


def build_bundle(store, job_id, clips, runtime_identity, *, generation=1, cancel=None):
    """Copy selected validated PCM only; an incomplete generation is never overwritten."""
    try:
        return _build_bundle(store, job_id, clips, runtime_identity, generation, cancel)
    except (OSError, wave.Error, EOFError):
        raise ValueError("The local repair bundle could not be written or verified.") from None


def _build_bundle(store, job_id, clips, runtime_identity, generation, cancel):
    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise Cancelled()
    if not isinstance(clips, list) or not 0 < len(clips) <= MAX_UNITS:
        raise ValueError("Choose between one and 200 prepared repair clips.")
    plan = _capture(store, job_id, runtime_identity)
    if len(clips) != len(plan["requests"]):
        raise ValueError("Every selected repair unit needs exactly one prepared clip.")
    directory, root = _directory(store, plan["manifest"]["source"], generation)
    if directory.exists():
        return _validate(plan, directory, root, generation)
    # Hashes are fixed-width; this is the exact final byte count before any artifact is written.
    preview = [
        _audio_descriptor(item["prepared"]["frames"], item["prepared"]["pcm_sha256"], "0" * 64)
        for item in plan["receipts"]
    ]
    _, manifest = _manifest(plan, preview, generation)
    if (
        sum(item["bytes"] for item in preview)
        + sum(map(len, plan["requests"]))
        + len(manifest)
        + MAX_COMMIT_BYTES
        > MAX_BUNDLE_BYTES
    ):
        raise ValueError("The repair bundle exceeds its bounded total byte limit.")
    directory.parent.mkdir(parents=True, exist_ok=True)
    audio_inputs._owned(directory.parent, root, directory_only=True)
    directory.mkdir()
    audio_inputs._owned(directory, root, directory_only=True)
    (directory / "units").mkdir()
    audio = []
    for index, (source, receipt, request) in enumerate(
        zip(clips, plan["receipts"], plan["requests"], strict=True)
    ):
        if cancel.is_set():
            raise Cancelled()
        folder = directory / "units" / f"{index:05d}"
        folder.mkdir()
        audio_inputs._owned(folder, root, directory_only=True)
        _copy_audio(source, folder / "audio.wav", root, plan["snapshot"], receipt, cancel)
        info = _audio_info(folder / "audio.wav", root, receipt["prepared"]["frames"])
        audio.append(info)
        _write(folder / "request.json", request, root)
    _, data = _manifest(plan, audio, generation)
    _write(directory / "manifest.json", data, root)
    marker = _json(
        {"format": COMMIT_FORMAT, "manifest": {"sha256": _sha(data), "bytes": len(data)}}
    )
    _write(directory / "COMMITTED.pending", marker, root)
    if cancel.is_set():
        raise Cancelled()
    audio_inputs._owned(directory / "COMMITTED.json", root)
    # Atomic create-if-absent. Interrupted unlink leaves an invalid generation for disposition.
    os.link(directory / "COMMITTED.pending", directory / "COMMITTED.json", follow_symlinks=False)
    (directory / "COMMITTED.pending").unlink()
    return _validate(plan, directory, root, generation)


def map_alignment_output(bundle, output):
    """Map once from an unmodified in-process build/validate return; never apply the output."""
    try:
        manifest, binding = bundle["manifest"], bundle["sha256"]
        if _sha(_json(manifest)) != binding or not _hash(binding):
            _failure()
        if (
            not isinstance(output, dict)
            or set(output) != {"format", "bundle_sha256", "unit_index", "coordinate_space", "words"}
            or output["format"] != OUTPUT_FORMAT
            or output["coordinate_space"] != "clip"
        ):
            raise ValueError("Choose an unmodified clip-coordinate alignment output.")
        index = output["unit_index"]
        if (
            output["bundle_sha256"] != binding
            or type(index) is not int
            or not 0 <= index < len(manifest["units"])
        ):
            _failure()
        unit = manifest["units"][index]
        request_bytes = _read(
            Path(bundle["directory"]) / "units" / f"{index:05d}" / "request.json",
            Path(bundle["directory"]),
            MAX_REQUEST_BYTES,
        )
        if {"sha256": _sha(request_bytes), "bytes": len(request_bytes)} != unit["request"]:
            _failure()
        request = _decode(request_bytes, MAX_REQUEST_BYTES)
        words = output["words"]
        if not isinstance(words, list) or len(words) > 1200:
            _failure()
        duration = (unit["window"]["end_frame"] - unit["window"]["start_frame"]) / RATE
        previous_char, previous_start, previous_end = 0, -1, -1
        mapped = {key: value for key, value in output.items() if key != "words"}
        mapped["words"] = []
        for word in words:
            if not isinstance(word, dict) or set(word) != {
                "word",
                "start",
                "end",
                "score",
                "char_start",
                "char_end",
            }:
                _failure()
            left, right = word["char_start"], word["char_end"]
            if (
                type(left) is not int
                or type(right) is not int
                or not previous_char <= left < right <= len(request["text"])
                or word["word"] != request["text"][left:right]
                or sum(
                    item["char_start"] <= left < right <= item["char_end"]
                    for item in request["ranges"]
                )
                != 1
            ):
                _failure()
            previous_char = right
            start, end, score = word["start"], word["end"], word["score"]
            if (
                any(
                    value is not None and (not _finite(value) or not 0 <= value <= duration)
                    for value in (start, end)
                )
                or (start is not None and end is not None and start >= end)
                or (score is not None and (not _finite(score) or not 0 <= score <= 1))
            ):
                _failure()
            if (start is not None and start < previous_start) or (
                end is not None and end < previous_end
            ):
                _failure()
            if start is not None:
                previous_start = start
            if end is not None:
                previous_end = end
            word = dict(word)
            for key in ("start", "end"):
                if word[key] is not None:
                    word[key] += unit["window"]["start_frame"] / RATE
            mapped["words"].append(word)
        if len(_json(mapped)) > MAX_REQUEST_BYTES:
            _failure()
        mapped.update(format=MAPPED_FORMAT, coordinate_space="recording")
        mapped["mapping"] = {
            "sample_rate": RATE,
            "start_frame": unit["window"]["start_frame"],
            "end_frame": unit["window"]["end_frame"],
        }
        return mapped
    except (KeyError, TypeError, OSError, UnicodeError, RecursionError, OverflowError):
        _failure()
