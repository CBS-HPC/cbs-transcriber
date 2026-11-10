"""Frozen original audio and local, hash-verified provider preparation receipts."""

import copy
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

from . import ffmpeg_runtime

POLICY = "original-v1"
RATE = 16000
MAX_BYTES = 20 * 1024**3
MAX_RECEIPT_BYTES = 2 * 1024**2
DECODE_TIMEOUT = 3600
RECIPE = "mono-16000-pcm16-v1"
TIMEBASE = "first-audio-zero-v1"
_ID = re.compile(r"[0-9a-f]{32}")
_SHA = re.compile(r"[0-9a-f]{64}")
_FRAME_PTS = re.compile(
    r"^\[(?:Parsed_ashowinfo_\d+|ashowinfo@[A-Za-z0-9_-]+) @ (?:0[xX])?[0-9a-fA-F]+\]"
    r"\s+n:\d+\s+pts:-?\d+\s+"
    r"pts_time:([-+\deE.]+)",
    re.MULTILINE,
)
_FRAME_SAMPLES = re.compile(
    r"^\[(?:Parsed_ashowinfo_\d+|ashowinfo@[A-Za-z0-9_-]+) @ (?:0[xX])?[0-9a-fA-F]+\]"
    r"\s+n:(\d+)\s+pts:(-?\d+)\s+pts_time:\S+.*?\brate:(\d+)\s+nb_samples:(\d+)"
)
_MP3_INCOMPLETE = re.compile(
    r"^\[mp3 @ (?:0[xX])?[0-9a-fA-F]+\]\s+filesize and duration do not match "
    r"\(growing file\?\)[ \t\r\n]*$"
)
_FORMATS = {
    "mp3",
    "wav",
    "m4a",
    "flac",
    "ogg",
    "mp4",
    "mov",
    "mkv",
    "webm",
    "mpeg",
    "mpg",
    "mpga",
    "aac",
    "wma",
    "avi",
}
_decoder_cache = None
_decoder_flights = {}
_decoder_flights_lock = threading.Lock()


def _invalid():
    raise ValueError("The frozen original audio input is invalid.")


def _receipt_error():
    raise ValueError(
        "The prepared audio receipt is missing, changed or invalid; preserve this job."
    )


def _incomplete_mp3():
    return ValueError("Original audio decoding could not confirm a complete MP3 source.")


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _sha(value):
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def validate(value):
    fields = {
        "policy",
        "recording_id",
        "format",
        "sha256",
        "bytes",
        "audio_stream",
        "audio_stream_count",
        "timebase",
        "recipe",
        "decoder",
        "audio_frames",
        "duration",
        "duration_basis",
        "container_duration_observation",
        "pcm_sha256",
        "source_origin",
    }
    if not isinstance(value, dict) or set(value) != fields:
        _invalid()
    decoder, observation = value["decoder"], value["container_duration_observation"]
    origin = value["source_origin"]
    if (
        value["policy"] != POLICY
        or value["recipe"] != RECIPE
        or value["timebase"] != TIMEBASE
        or value["audio_stream"] != "0:a:0"
        or not isinstance(value["format"], str)
        or value["format"] not in _FORMATS
        or not isinstance(value["recording_id"], str)
        or not _ID.fullmatch(value["recording_id"])
        or not _sha(value["sha256"])
        or type(value["bytes"]) is not int
        or not 0 < value["bytes"] <= MAX_BYTES
        or type(value["audio_stream_count"]) is not int
        or not 1 <= value["audio_stream_count"] <= 100
        or not _number(value["duration"])
        or value["duration"] <= 0
        or (observation is not None and (not _number(observation) or observation <= 0))
        or not isinstance(decoder, dict)
        or set(decoder) != {"version", "sha256"}
        or not isinstance(decoder.get("version"), str)
        or not 0 < len(decoder["version"]) <= 200
        or not _sha(decoder.get("sha256"))
        or not isinstance(origin, dict)
        or set(origin) != {"basis", "first_pts", "normalized_first_pts", "has_video"}
        or not _number(origin.get("first_pts"))
        or type(origin.get("has_video")) is not bool
    ):
        _invalid()
    if value["duration_basis"] == "decoded_pcm_frames":
        if (
            type(value["audio_frames"]) is not int
            or not 0 < value["audio_frames"] <= MAX_BYTES // 2
            or value["duration"] != value["audio_frames"] / RATE
            or not _sha(value["pcm_sha256"])
            or origin["basis"] != "decoded_frame"
            or not _number(origin["normalized_first_pts"])
            or origin["normalized_first_pts"] != 0
            or (origin["has_video"] and origin["first_pts"] != 0)
        ):
            _invalid()
    elif value["duration_basis"] == "container_observation":
        if (
            value["audio_frames"] is not None
            or value["pcm_sha256"] is not None
            or value["duration"] != observation
            or origin["basis"] != "encoded_packet"
            or origin["first_pts"] != 0
            or origin["normalized_first_pts"] is not None
        ):
            _invalid()
    else:
        _invalid()
    return copy.deepcopy(value)


def effective_recording(job, recording):
    if "audio_input" not in job["params"]:
        return recording
    frozen = validate(job["params"]["audio_input"])
    if frozen["recording_id"] != recording["id"]:
        _invalid()
    return {**recording, "duration": frozen["duration"]}


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _owned(path, directory, *, directory_only=False):
    path, directory = Path(path), Path(directory)
    try:
        if not path.absolute().is_relative_to(directory.absolute()):
            raise ValueError
        if directory.parent.name == "recordings" and (
            directory.parent.is_symlink() or directory.parent.is_junction()
        ):
            raise ValueError
        for item in [directory, *path.relative_to(directory).parents, path]:
            current = item if item.is_absolute() else directory / item
            if current.exists() or current.is_symlink():
                info = current.lstat()
                if current.is_symlink() or current.is_junction():
                    raise ValueError
                if current != path or directory_only:
                    if not stat.S_ISDIR(info.st_mode):
                        raise ValueError
        if path.exists() and not directory_only and not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError
    except (OSError, ValueError):
        raise ValueError(
            "Audio inputs must use an owned regular file inside this recording."
        ) from None
    return path


def _hash(path, directory, cancel=None, *, max_bytes=None):
    from .media import Cancelled

    if cancel is not None and cancel.is_set():
        raise Cancelled()
    path = _owned(path, directory)
    try:
        before = path.lstat()
        if max_bytes is not None and not 0 < before.st_size <= max_bytes:
            raise ValueError
        with path.open("rb") as source:
            opened = os.fstat(source.fileno())
            if _signature(before) != _signature(opened):
                raise ValueError
            digest, count = hashlib.sha256(), 0
            # The sentinel catches growth without following an indefinitely growing source.
            while block := source.read(min(1024 * 1024, opened.st_size + 1 - count)):
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                count += len(block)
                if count > opened.st_size:
                    raise ValueError
                digest.update(block)
            if count != opened.st_size:
                raise ValueError
            final_open = os.fstat(source.fileno())
        after = path.lstat()
        if _signature(before) != _signature(after) or _signature(opened) != _signature(final_open):
            raise ValueError
        _owned(path, directory)
        # Deliver a fully validated observation; its owner anchors it before honoring late Stop.
        return {"sha256": digest.hexdigest(), "bytes": after.st_size}
    except (OSError, ValueError):
        raise ValueError(
            "The original or prepared audio changed while its bytes were checked."
        ) from None


def _decoder(cancel=None, *, executable=None):
    ffmpeg_runtime.check_cancel(cancel)
    path = Path(executable if executable is not None else ffmpeg_runtime.for_identity(cancel))
    while True:
        ffmpeg_runtime.check_cancel(cancel)
        info = path.stat()
        key = (str(path), *_signature(info))
        cached = _decoder_cache
        if cached is not None and cached[0] == key:
            ffmpeg_runtime.check_cancel(cancel)
            return dict(cached[1])
        with _decoder_flights_lock:
            cached = _decoder_cache
            if cached is not None and cached[0] == key:
                continue
            flight = _decoder_flights.get(key)
            owner = flight is None
            if owner:
                flight = threading.Event()
                _decoder_flights[key] = flight
        if not owner:
            # Each waiter retains its own cancellation/lease guard. A cancelled or
            # failed owner never cancels a different recording's inspection.
            while not flight.wait(0.05):
                ffmpeg_runtime.check_cancel(cancel)
            continue
        try:
            return _observe_decoder(path, info, key, cancel)
        finally:
            with _decoder_flights_lock:
                del _decoder_flights[key]
                flight.set()


def _observe_decoder(path, info, key, cancel):
    global _decoder_cache
    digest = hashlib.sha256()
    with path.open("rb") as source:
        if _signature(os.fstat(source.fileno())) != _signature(info):
            raise ValueError("The original audio decoder changed while it was checked.")
        while block := source.read(1024 * 1024):
            ffmpeg_runtime.check_cancel(cancel)
            digest.update(block)
    version = ffmpeg_runtime.version(str(path), cancel)
    if _signature(path.stat()) != _signature(info):
        raise ValueError("The original audio decoder changed while it was checked.")
    cached = key, {"version": version, "sha256": digest.hexdigest()}
    _decoder_cache = cached
    ffmpeg_runtime.check_cancel(cancel)
    return dict(cached[1])


def _run(
    args,
    cancel=None,
    *,
    pcm=False,
    timeline=False,
    expected_frames=None,
    timeline_start_frame=0,
    executable=None,
):
    """Drain decoded PCM incrementally; neither media nor diagnostic logs enter memory unbounded."""
    from .media import Cancelled

    cancel = cancel or threading.Event()
    if timeline and (type(timeline_start_frame) is not int or timeline_start_frame < 0):
        _invalid()
    if expected_frames is not None and (type(expected_frames) is not int or expected_frames <= 0):
        _invalid()
    if timeline and not pcm and (type(expected_frames) is not int or expected_frames <= 0):
        _invalid()
    if cancel.is_set():
        raise Cancelled()
    count, digest, errors = 0, hashlib.sha256(), []
    timeline_frames, timeline_rows, captured = 0, 0, bytearray()
    with tempfile.TemporaryFile() as log:
        with subprocess.Popen(
            [
                executable if executable is not None else ffmpeg_runtime.resolve(cancel),
                "-hide_banner",
                "-nostdin",
                "-nostats",
                "-y",
                *args,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if pcm else subprocess.DEVNULL,
            stderr=subprocess.PIPE if timeline else log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ) as process:

            def drain():
                nonlocal count
                try:
                    while block := process.stdout.read(1024 * 1024):
                        count += len(block)
                        if count > MAX_BYTES:
                            errors.append(
                                ValueError(
                                    "The decoded original exceeds the supported audio limit."
                                )
                            )
                            process.kill()
                            return
                        digest.update(block)
                except OSError as exc:
                    errors.append(exc)
                    process.kill()

            def inspect_timeline():
                nonlocal timeline_frames, timeline_rows
                at_line_start = True
                try:
                    while line := process.stderr.readline(8192):
                        captured.extend(line[: max(0, MAX_RECEIPT_BYTES - len(captured))])
                        parse_line = at_line_start
                        at_line_start = line.endswith(b"\n")
                        if not parse_line:
                            continue
                        diagnostic = line.decode("utf-8", errors="replace")
                        if _MP3_INCOMPLETE.fullmatch(diagnostic):
                            errors.append(_incomplete_mp3())
                            process.kill()
                            return
                        row = _FRAME_SAMPLES.search(diagnostic)
                        if row is None:
                            continue
                        index, pts, rate, frames = map(int, row.groups())
                        if (
                            index != timeline_rows
                            or pts != timeline_start_frame + timeline_frames
                            or rate != RATE
                            or frames <= 0
                        ):
                            errors.append(
                                ValueError(
                                    "The original audio timeline is discontinuous or unsupported."
                                )
                            )
                            process.kill()
                            return
                        timeline_frames += frames
                        timeline_rows += 1
                        if expected_frames is not None and timeline_frames > expected_frames:
                            errors.append(
                                ValueError("The selected original timeline exceeds its window.")
                            )
                            process.kill()
                            return
                except OSError as exc:
                    errors.append(exc)
                    process.kill()

            reader = threading.Thread(target=drain, daemon=True) if pcm else None
            inspector = threading.Thread(target=inspect_timeline, daemon=True) if timeline else None
            if reader:
                reader.start()
            if inspector:
                inspector.start()
            started = time.monotonic()
            interrupted = None
            try:
                while process.poll() is None:
                    if cancel.wait(0.05):
                        interrupted = Cancelled()
                        break
                    if time.monotonic() - started > DECODE_TIMEOUT:
                        interrupted = ValueError(
                            "Original audio decoding exceeded its bounded preparation time."
                        )
                        break
            finally:
                # A durable guard may itself fail; never leave its decoder running or unread.
                if process.poll() is None:
                    try:
                        process.kill()
                    except OSError:
                        pass  # It may have exited between poll and kill.
                try:
                    process.wait()
                finally:
                    if reader:
                        reader.join()
                    if inspector:
                        inspector.join()
            if interrupted:
                raise interrupted
            if errors:
                if isinstance(errors[0], ValueError):
                    raise errors[0]
                raise ValueError(
                    "Original audio decoding failed within its supported limits."
                ) from None
            if process.returncode:
                raise ValueError(
                    "Original audio decoding failed; check for damaged or unsupported media."
                )
        log.seek(0)
        at_line_start = True
        while line := log.readline(8192):
            if at_line_start and _MP3_INCOMPLETE.fullmatch(line.decode("utf-8", errors="replace")):
                raise _incomplete_mp3()
            at_line_start = line.endswith(b"\n")
        log.seek(0)
        output = (captured if timeline else log.read(MAX_RECEIPT_BYTES)).decode(
            "utf-8", errors="replace"
        )
    if pcm and (count == 0 or count % 2):
        raise ValueError("Original audio decoding produced no complete PCM samples.")
    if timeline and (
        timeline_rows == 0 or timeline_frames != (count // 2 if pcm else expected_frames)
    ):
        raise ValueError("The original audio samples have no complete timeline proof.")
    return output, count // 2, digest.hexdigest()


def _decode_args(path, *, window=None):
    args = [
        "-xerror",
        "-err_detect",
        "explode",
        *(["-ss", str(window["start_frame"] / RATE)] if window and window["start_frame"] else []),
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
    ]
    if window is not None:
        args += [
            "-af",
            "aresample=16000,atrim=start_sample="
            + "0"
            + ":end_sample="
            + str(window["end_frame"] - window["start_frame"])
            + ",asetpts=PTS-STARTPTS",
            "-t",
            str((window["end_frame"] - window["start_frame"]) / RATE),
        ]
    return args + ["-ac", "1", "-ar", str(RATE)]


def _source(directory, frozen):
    directory = Path(directory)
    if directory.name != frozen["recording_id"]:
        _invalid()
    return _owned(directory / ("original." + frozen["format"]), directory)


def _verify(directory, frozen):
    path = _source(directory, frozen)
    if _hash(path, directory) != {"sha256": frozen["sha256"], "bytes": frozen["bytes"]}:
        raise ValueError("The original audio changed after this job was frozen; start a new job.")
    return path


def verify_original(store, recording, snapshot):
    frozen = validate(snapshot)
    if (
        frozen["recording_id"] != recording["id"]
        or Path(recording["filename"]).suffix.lower() != "." + frozen["format"]
    ):
        _invalid()
    return _verify(store.directory(recording["id"]), frozen)


def freeze(store, recording, cancel=None, *, scoped=False, identity_observer=None):
    directory = store.directory(recording["id"])
    extension = Path(recording["filename"]).suffix.lower().lstrip(".")
    if extension not in _FORMATS:
        raise ValueError("The recording needs a supported filename extension.")
    seed = {"recording_id": recording["id"], "format": extension}
    path = _source(directory, seed)
    identity = _hash(path, directory, cancel, max_bytes=MAX_BYTES)
    if not 0 < identity["bytes"] <= MAX_BYTES:
        _invalid()
    if identity_observer is not None:
        identity_observer({**identity, "format": extension})
    executable = ffmpeg_runtime.for_identity(cancel)
    decoder = _decoder(cancel, executable=executable)
    probe = [
        "-copyts",
        "-xerror",
        "-err_detect",
        "explode",
        "-i",
        str(path),
        "-map",
        "0:a:0",
    ]
    probe += ["-debug_ts", "-c:a", "copy"] if scoped else ["-af", "ashowinfo"]
    log, _, _ = _run(
        probe + ["-frames:a", "1", "-f", "null", "-"],
        cancel,
        executable=executable,
    )
    streams = re.findall(
        r"^ {2}Stream #0:(\d+).*: Audio:", log.split("Output #", 1)[0], re.MULTILINE
    )
    if not streams:
        raise ValueError("Original audio decoding found no supported audio stream.")
    pattern = (
        r"^(?:\[aist#0:" + streams[0] + r"/[^\]\r\n]+ @ (?:0[xX])?[0-9a-fA-F]+\]\s+)?"
        r"demuxer -> ist_index:(?:0:)?" + streams[0] + r" type:audio .*?"
        r"\bpkt_pts_time:([-+\deE.]+)"
    )
    pts = re.search(pattern, log, re.MULTILINE) if scoped else _FRAME_PTS.search(log)
    video_streams = re.findall(
        r"^ {2}Stream #0:\d+.*: Video:.*$", log.split("Output #", 1)[0], re.MULTILINE
    )
    has_video = any("(attached pic)" not in row for row in video_streams)
    if pts is None or (scoped or has_video) and float(pts[1]) != 0:
        raise ValueError(
            "The original first-audio timeline is unsupported; its offset cannot be guessed."
        )
    first_pts, normalized_pts = float(pts[1]), None
    if not scoped:
        normalized_probe = [arg for arg in probe if arg != "-copyts"]
        if not has_video:
            normalized_probe[normalized_probe.index("-af") + 1] = "asetpts=PTS-STARTPTS,ashowinfo"
        normalized, _, _ = _run(
            normalized_probe + ["-frames:a", "1", "-f", "null", "-"],
            cancel,
            executable=executable,
        )
        normalized_first = _FRAME_PTS.search(normalized)
        if normalized_first is None or float(normalized_first[1]) != 0:
            raise ValueError("The decoder could not prove the original first-audio zero timeline.")
        normalized_pts = float(normalized_first[1])
    observation = recording.get("duration")
    if scoped:
        if not _number(observation) or observation <= 0:
            raise ValueError("Bounded original audio requires a known observed recording duration.")
        frames, pcm_digest, duration = None, None, observation
    else:
        _, frames, pcm_digest = _run(
            _decode_args(path)
            + [
                "-af",
                "aresample=16000,asetpts=PTS-STARTPTS,ashowinfo",
                "-c:a",
                "pcm_s16le",
                "-f",
                "s16le",
                "pipe:1",
            ],
            cancel,
            pcm=True,
            timeline=True,
            executable=executable,
        )
        duration = frames / RATE
    if _hash(path, directory, cancel, max_bytes=MAX_BYTES) != identity:
        raise ValueError("The original audio changed while it was inspected.")
    if _decoder(cancel, executable=executable) != decoder:
        raise ValueError("The original audio decoder changed while it was inspected.")
    return validate(
        {
            "policy": POLICY,
            **seed,
            **identity,
            "audio_stream": "0:a:0",
            "audio_stream_count": len(set(streams)),
            "timebase": TIMEBASE,
            "recipe": RECIPE,
            "decoder": decoder,
            "audio_frames": frames,
            "duration": duration,
            "duration_basis": "container_observation" if scoped else "decoded_pcm_frames",
            "pcm_sha256": pcm_digest,
            "source_origin": {
                "basis": "encoded_packet" if scoped else "decoded_frame",
                "first_pts": first_pts,
                "normalized_first_pts": normalized_pts,
                "has_video": has_video,
            },
            "container_duration_observation": observation
            if _number(observation) and observation > 0
            else None,
        }
    )


def require_native(snapshot):
    frozen = validate(snapshot)
    if frozen["audio_stream_count"] != 1 or frozen["duration_basis"] != "decoded_pcm_frames":
        raise ValueError(
            "Native Transcriber requires a fully validated single audio stream; "
            "its track selector is unverified."
        )


def transport_for(snapshot, settings):
    """Pass unchanged originals only under a known provider contract and byte limit."""
    frozen = validate(snapshot)
    if settings.provider_type == "ucloud":
        require_native(frozen)
        return "original"
    official_formats = {"flac", "mp3", "mp4", "mpeg", "mpga", "m4a", "ogg", "wav", "webm"}
    if (
        settings.provider_type == "openai"
        and settings.base_url.rstrip("/") == "https://api.openai.com/v1"
        and frozen["format"] in official_formats
        and frozen["audio_stream_count"] == 1
        and frozen["duration_basis"] == "decoded_pcm_frames"
        and frozen["bytes"] <= min(settings.max_upload_mb * 1_000_000, 25_000_000)
    ):
        return "original"
    return "mp3"


def _window(frozen, offset, duration):
    if not _number(offset) or not _number(duration) or offset < 0 or duration <= 0:
        _invalid()
    start, end = round(offset * RATE), round((offset + duration) * RATE)
    if not 0 <= start < end <= round(frozen["duration"] * RATE):
        raise ValueError("The prepared audio window exceeds the frozen original samples.")
    return {"start_frame": start, "end_frame": end, "timebase": TIMEBASE}


def _sample_info(path, directory):
    identity = _hash(path, directory)
    try:
        with wave.open(str(path), "rb") as source:
            if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (
                RATE,
                1,
                2,
            ):
                _receipt_error()
            frames = source.getnframes()
            digest, count = hashlib.sha256(), 0
            while block := source.readframes(RATE * 30):
                count += len(block)
                digest.update(block)
        if frames <= 0 or count != frames * 2 or _hash(path, directory) != identity:
            _receipt_error()
    except (OSError, wave.Error, EOFError):
        _receipt_error()
    return {
        **identity,
        "format": "wav",
        "frames": frames,
        "pcm_sha256": digest.hexdigest(),
        "sample_rate": RATE,
        "channels": 1,
        "sample_width": 2,
        "sample_basis": RECIPE,
    }


def _receipt(frozen, window, prepared):
    return {"schema": 1, "policy": POLICY, "source": frozen, "window": window, "prepared": prepared}


def _valid_part(part, frozen):
    fields = {
        "schema",
        "policy",
        "source",
        "window",
        "prepared",
    }
    if not isinstance(part, dict) or set(part) not in (fields, fields | {"timeline"}):
        _receipt_error()
    if (
        part["schema"] != 1
        or type(part["schema"]) is not int
        or part["policy"] != POLICY
        or validate(part["source"]) != frozen
    ):
        _receipt_error()
    window, prepared = part["window"], part["prepared"]
    if (
        not isinstance(window, dict)
        or set(window) != {"start_frame", "end_frame", "timebase"}
        or type(window["start_frame"]) is not int
        or type(window["end_frame"]) is not int
        or window["timebase"] != TIMEBASE
        or not 0 <= window["start_frame"] < window["end_frame"] <= round(frozen["duration"] * RATE)
        or not isinstance(prepared, dict)
        or set(prepared)
        != {
            "sha256",
            "bytes",
            "format",
            "frames",
            "pcm_sha256",
            "sample_rate",
            "channels",
            "sample_width",
            "sample_basis",
        }
        or not _sha(prepared["sha256"])
        or not _sha(prepared["pcm_sha256"])
        or type(prepared["bytes"]) is not int
        or not 0 < prepared["bytes"] <= MAX_BYTES
        or type(prepared["frames"]) is not int
        or prepared["frames"] != window["end_frame"] - window["start_frame"]
        or any(
            type(prepared[k]) is not int or prepared[k] != v
            for k, v in (("sample_rate", RATE), ("channels", 1), ("sample_width", 2))
        )
        or prepared["format"] not in {"wav", "mp3", frozen["format"]}
        or prepared["sample_basis"] != RECIPE
    ):
        _receipt_error()
    if "timeline" in part and (
        frozen["duration_basis"] != "container_observation"
        or prepared["format"] != "wav"
        or part["timeline"]
        != {
            "policy": "selected-pcm-contiguous-v1",
            "start_frame": window["start_frame"],
            "end_frame": window["end_frame"],
            "frames": prepared["frames"],
        }
        or any(
            type(part["timeline"][key]) is not int for key in ("start_frame", "end_frame", "frames")
        )
    ):
        _receipt_error()
    if (
        prepared["format"] == "wav"
        and frozen["duration_basis"] == "decoded_pcm_frames"
        and window["start_frame"] == 0
        and window["end_frame"] == frozen["audio_frames"]
        and prepared["pcm_sha256"] != frozen["pcm_sha256"]
    ):
        _receipt_error()


def validate_evidence(value, snapshot):
    frozen = validate(snapshot)
    try:
        if (
            len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            > MAX_RECEIPT_BYTES
        ):
            _receipt_error()
        if isinstance(value, dict) and set(value) == {
            "schema",
            "policy",
            "source",
            "transport",
            "chunks",
            "parts",
        }:
            if (
                type(value["schema"]) is not int
                or value["schema"] != 1
                or value["policy"] != POLICY
                or value["source"] != frozen
                or value["transport"] not in {"original", "mp3"}
            ):
                _receipt_error()
            chunks, parts = value["chunks"], value["parts"]
            if (
                not isinstance(chunks, list)
                or not 1 <= len(chunks) <= 2000
                or not isinstance(parts, list)
                or len(parts) != len(chunks)
            ):
                _receipt_error()
            cursor = 0
            for index, (chunk, part) in enumerate(zip(chunks, parts, strict=True)):
                _valid_part(part, frozen)
                window = part["window"]
                original = value["transport"] == "original"
                if (
                    not isinstance(chunk, dict)
                    or set(chunk)
                    != (
                        {"file", "offset", "duration", "source"}
                        if original
                        else {"file", "offset", "duration"}
                    )
                    or chunk["file"]
                    != ("original." + frozen["format"] if original else f"{index:04d}.mp3")
                    or (original and (len(chunks) != 1 or chunk["source"] != "original"))
                    or not _number(chunk["offset"])
                    or chunk["offset"] != window["start_frame"] / RATE
                    or not _number(chunk["duration"])
                    or chunk["duration"] != (window["end_frame"] - window["start_frame"]) / RATE
                    or window["start_frame"] != cursor
                ):
                    _receipt_error()
                if original and part["prepared"] != {
                    "sha256": frozen["sha256"],
                    "bytes": frozen["bytes"],
                    "format": frozen["format"],
                    "frames": frozen["audio_frames"],
                    "pcm_sha256": frozen["pcm_sha256"],
                    "sample_rate": RATE,
                    "channels": 1,
                    "sample_width": 2,
                    "sample_basis": RECIPE,
                }:
                    _receipt_error()
                if not original and part["prepared"]["format"] != "mp3":
                    _receipt_error()
                cursor = window["end_frame"]
            if cursor != frozen["audio_frames"]:
                _receipt_error()
        else:
            _valid_part(value, frozen)
            if value["prepared"]["format"] != "wav":
                _receipt_error()
    except (KeyError, TypeError, RecursionError, OverflowError):
        _receipt_error()
    return copy.deepcopy(value)


def _read(path, directory):
    try:
        _owned(path, directory)
        if path.stat().st_size > MAX_RECEIPT_BYTES:
            _receipt_error()
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        _receipt_error()


def _write_receipt(path, value, directory):
    _owned(path, directory)
    pending = path.with_name(path.name + ".pending")
    _owned(pending, directory)
    with pending.open("xb") as output:
        output.write(
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        )
        output.flush()
        os.fsync(output.fileno())
    if path.exists():
        _receipt_error()
    pending.replace(path)


def _ready_folder(folder, directory):
    _owned(folder, directory, directory_only=True)
    folder.mkdir(parents=True, exist_ok=True)
    _owned(folder, directory, directory_only=True)


def _publish(pending, target, directory):
    _owned(pending, directory)
    _owned(target, directory)
    with pending.open("r+b") as output:
        os.fsync(output.fileno())
    if target.exists():
        _receipt_error()
    pending.replace(target)


def prepare_wav(
    directory, recording, snapshot, folder, *, offset, duration, cancel, bounded_timeline=False
):
    frozen = validate(snapshot)
    if (
        type(bounded_timeline) is not bool
        or bounded_timeline
        and frozen["duration_basis"] != "container_observation"
    ):
        _invalid()
    if recording["id"] != frozen["recording_id"]:
        _invalid()
    directory, folder = Path(directory), Path(folder)
    window = _window(frozen, offset, duration)
    if window["end_frame"] - window["start_frame"] > 6 * 3600 * RATE:
        raise ValueError("WhisperX prepared audio is limited to six hours.")
    _ready_folder(folder, directory)
    source = _verify(directory, frozen)
    receipt_path, audio = folder / "audio.input.json", folder / "audio.wav"
    if audio.exists() or receipt_path.exists():
        value = prepared_evidence(folder, frozen)
        if value.get("window") != window:
            _receipt_error()
        if bounded_timeline and "timeline" not in value:
            _receipt_error()
        return value
    executable = ffmpeg_runtime.for_identity(cancel)
    if _decoder(cancel, executable=executable) != frozen["decoder"]:
        raise ValueError("The original audio decoder changed; preserve the frozen job.")
    pending = folder / "audio.pending.wav"
    if pending.exists():
        _receipt_error()
    _owned(pending, directory)
    args = _decode_args(source, window=window)
    check = {}
    if bounded_timeline:
        # Preserve absolute source PTS through seek/resampling/trim. Observe before the
        # final clip-zero rewrite; output PTS=0 alone cannot establish its intended window.
        args.insert(0, "-copyts")
        filter_index = args.index("-af") + 1
        args[filter_index] = args[filter_index].replace(
            ",asetpts=PTS-STARTPTS", ",ashowinfo,asetpts=PTS-STARTPTS"
        )
        check = {
            "timeline": True,
            "expected_frames": window["end_frame"] - window["start_frame"],
            "timeline_start_frame": window["start_frame"],
        }
    _run(args + ["-c:a", "pcm_s16le", str(pending)], cancel, executable=executable, **check)
    if _decoder(cancel, executable=executable) != frozen["decoder"]:
        raise ValueError("The original audio decoder changed; preserve the frozen job.")
    prepared = _sample_info(pending, directory)
    value = _receipt(frozen, window, prepared)
    if bounded_timeline:
        value["timeline"] = {
            "policy": "selected-pcm-contiguous-v1",
            "start_frame": window["start_frame"],
            "end_frame": window["end_frame"],
            "frames": prepared["frames"],
        }
    value = validate_evidence(value, frozen)
    if (
        window["start_frame"] == 0
        and window["end_frame"] == frozen["audio_frames"]
        and prepared["pcm_sha256"] != frozen["pcm_sha256"]
    ):
        _receipt_error()
    _verify(directory, frozen)
    if cancel.is_set():
        from .media import Cancelled

        raise Cancelled()
    _publish(pending, audio, directory)
    _write_receipt(receipt_path, value, directory)
    return value


def _compressed_info(path, directory, cancel=None, *, executable=None):
    identity = _hash(path, directory)
    _, frames, digest = _run(
        _decode_args(path) + ["-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"],
        cancel,
        pcm=True,
        executable=executable,
    )
    if _hash(path, directory) != identity:
        _receipt_error()
    return {
        **identity,
        "format": "mp3",
        "frames": frames,
        "pcm_sha256": digest,
        "sample_rate": RATE,
        "channels": 1,
        "sample_width": 2,
        "sample_basis": RECIPE,
    }


def chunk_path(directory, folder, chunk, snapshot):
    frozen = validate(snapshot)
    directory, folder = Path(directory), Path(folder)
    plan = validate_evidence(_read(folder / "plan.json", directory), frozen)
    if "chunks" not in plan:
        _receipt_error()
    matches = [index for index, row in enumerate(plan["chunks"]) if row == chunk]
    if len(matches) != 1:
        _receipt_error()
    if chunk.get("source") == "original":
        if chunk.get("file") != "original." + frozen["format"]:
            _receipt_error()
        return _verify(directory, frozen)
    if (
        set(chunk) != {"file", "offset", "duration"}
        or not isinstance(chunk["file"], str)
        or not re.fullmatch(r"\d{4}\.mp3", chunk["file"])
    ):
        _receipt_error()
    path = _owned(folder / chunk["file"], directory)
    prepared = plan["parts"][matches[0]]["prepared"]
    if _hash(path, directory) != {key: prepared[key] for key in ("sha256", "bytes")}:
        _receipt_error()
    return path


def prepare_chunks(directory, job_id, duration, max_mb, cancel, *, audio_input, transport="mp3"):
    frozen = validate(audio_input)
    if (
        frozen["duration_basis"] != "decoded_pcm_frames"
        or duration != frozen["duration"]
        or not _ID.fullmatch(job_id)
        or type(max_mb) not in {int, float}
        or not math.isfinite(max_mb)
        or max_mb <= 0
        or transport not in {"original", "mp3"}
    ):
        _invalid()
    directory = Path(directory)
    folder = directory / "chunks" / job_id
    _ready_folder(folder, directory)
    plan_path = folder / "plan.json"
    source = _verify(directory, frozen)
    if plan_path.exists():
        value = prepared_evidence(folder, frozen)
        if value.get("transport") != transport or any(
            part["prepared"]["bytes"] > max_mb * 1_000_000 for part in value["parts"]
        ):
            _receipt_error()
        return folder, value["chunks"]
    if any(folder.iterdir()):
        _receipt_error()
    executable = ffmpeg_runtime.for_identity(cancel)
    if _decoder(cancel, executable=executable) != frozen["decoder"]:
        raise ValueError("The original audio decoder changed; preserve the frozen job.")
    chunks, parts = [], []
    if transport == "original":
        if frozen["bytes"] > max_mb * 1_000_000:
            raise ValueError("The original audio exceeds this provider's upload limit.")
        chunks = [
            {
                "file": "original." + frozen["format"],
                "source": "original",
                "offset": 0,
                "duration": duration,
            }
        ]
        prepared = {key: frozen[key] for key in ("sha256", "bytes", "pcm_sha256")}
        prepared.update(
            format=frozen["format"],
            frames=frozen["audio_frames"],
            sample_rate=RATE,
            channels=1,
            sample_width=2,
            sample_basis=RECIPE,
        )
        parts = [_receipt(frozen, _window(frozen, 0, duration), prepared)]
    else:
        max_frames = int(min(900, max_mb * 1_000_000 * 0.8 / 8000) * RATE)
        if max_frames < 1:
            _invalid()
        silence_ends = []
        if frozen["audio_frames"] > max_frames:
            log, _, _ = _run(
                _decode_args(source)
                + [
                    "-af",
                    "aformat=channel_layouts=mono,aresample=16000,asetpts=PTS-STARTPTS,"
                    "silencedetect=noise=-35dB:d=0.4",
                    "-f",
                    "null",
                    "-",
                ],
                cancel,
                executable=executable,
            )
            silence_ends = [
                float(point)
                for point in re.findall(
                    r"^\[(?:silencedetect|Parsed_silencedetect_\d+) @ (?:0[xX])?[0-9a-fA-F]+\]"
                    r"\s+silence_end:\s*([-+\deE.]+)",
                    log,
                    re.MULTILINE,
                )
            ]
        from .media import chunk_boundaries

        boundaries = chunk_boundaries(duration, silence_ends, max_frames / RATE)
        for start_seconds, end_seconds in boundaries:
            cursor, end = round(start_seconds * RATE), round(end_seconds * RATE)
            if len(chunks) >= 2000:
                raise ValueError("This original requires too many provider audio parts.")
            name = f"{len(chunks):04d}.mp3"
            target, pending = folder / name, folder / (name + ".pending.mp3")
            _owned(pending, directory)
            window = {"start_frame": cursor, "end_frame": end, "timebase": TIMEBASE}
            _run(
                _decode_args(source, window=window)
                + ["-c:a", "libmp3lame", "-b:a", "64k", str(pending)],
                cancel,
                executable=executable,
            )
            prepared = _compressed_info(pending, directory, cancel, executable=executable)
            if prepared["bytes"] > max_mb * 1_000_000:
                raise ValueError("The prepared audio exceeds this provider's upload limit.")
            part = _receipt(frozen, window, prepared)
            _valid_part(part, frozen)
            _publish(pending, target, directory)
            chunks.append(
                {"file": name, "offset": cursor / RATE, "duration": (end - cursor) / RATE}
            )
            parts.append(part)
    if _decoder(cancel, executable=executable) != frozen["decoder"]:
        raise ValueError("The original audio decoder changed; preserve the frozen job.")
    _verify(directory, frozen)
    if cancel.is_set():
        from .media import Cancelled

        raise Cancelled()
    value = validate_evidence(
        {
            "schema": 1,
            "policy": POLICY,
            "source": frozen,
            "transport": transport,
            "chunks": chunks,
            "parts": parts,
        },
        frozen,
    )
    _write_receipt(plan_path, value, directory)
    return folder, chunks


def prepared_evidence(folder, snapshot):
    frozen = validate(snapshot)
    folder = Path(folder)
    # Only known owned layouts are accepted; no path is recovered from receipt metadata.
    directory = next(
        (parent for parent in folder.parents if parent.name == frozen["recording_id"]), None
    )
    if directory is None:
        _receipt_error()
    _owned(folder, directory, directory_only=True)
    if (folder / "audio.input.json").exists():
        value = validate_evidence(_read(folder / "audio.input.json", directory), frozen)
        if _sample_info(folder / "audio.wav", directory) != value["prepared"]:
            _receipt_error()
    else:
        value = validate_evidence(_read(folder / "plan.json", directory), frozen)
        if "parts" not in value:
            _receipt_error()
        for chunk, part in zip(value["chunks"], value["parts"], strict=True):
            path = chunk_path(directory, folder, chunk, frozen)
            prepared = part["prepared"]
            if _hash(path, directory) != {key: prepared[key] for key in ("sha256", "bytes")}:
                _receipt_error()
    return value
