"""Pinned, bounded original-to-PCM transport for fresh whole CBS Runtime tasks.

This file is copied with the frozen worker. Importing it never opens audio, downloads
anything or loads an inference runtime. Local full original inspection remains required.
"""

import ctypes
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
import wave
from pathlib import Path

PROTOCOL = "cbs-original-transport-v1"
DESCRIPTOR = "original.input.json"
PREPARATION = "audio.preparation.json"
RATE = 16000
MAX_SOURCE = 512 * 1024**2
MAX_FRAMES = RATE * 6 * 3600
MAX_JSON = 65536
MAX_REQUEST = 32 * 1024**2
MAX_SECONDS = 3600
RECIPE = "mono-16000-pcm16-v1"
TIMEBASE = "first-audio-zero-v1"
DECODER = {
    "version": "7.0.2-static",
    "sha256": "e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99",
}
DEMUXERS = {"mp3": "mp3", "m4a": "mov", "mp4": "mov", "flac": "flac", "ogg": "ogg", "aac": "aac"}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


class PreparationError(ValueError):
    """Curated preparation refusal, before any inference can start."""


def failure():
    raise PreparationError(
        "The compressed original transport or its preparation receipt is invalid."
    )


def _sha(value):
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def safe(path):
    path = Path(path)
    for item in (path, *path.parents):
        if item.is_symlink() or sys.platform == "win32" and item.is_junction():
            failure()
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            failure()
    return path


def file_receipt(path, limit):
    path = safe(path)
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            failure()
        result, size = hashlib.sha256(), 0
        while block := stream.read(min(1024**2, before.st_size + 1 - size)):
            size += len(block)
            if size > before.st_size:
                failure()
            result.update(block)
        after = os.fstat(stream.fileno())

    def signature(s):
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns

    if (
        size != before.st_size
        or signature(before) != signature(after)
        or signature(after) != signature(path.stat())
    ):
        failure()
    safe(path)
    return {"sha256": result.hexdigest(), "bytes": size}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            failure()
        result[key] = value
    return result


def read(path, limit=MAX_JSON):
    path = safe(path)
    if path.stat().st_size > limit:
        failure()
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        failure()
    try:
        return json.loads(raw, object_pairs_hook=_unique, parse_constant=lambda _: failure())
    except (ValueError, TypeError, RecursionError):
        failure()


def publish(path, data):
    """Create-if-absent; retries cannot replace either valid or contradictory evidence."""
    path = safe(path)
    if path.exists():
        if file_receipt(path, MAX_JSON) != {"bytes": len(data), "sha256": digest(data)}:
            failure()
        return
    pending = safe(path.with_name(path.name + ".pending"))
    with pending.open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    try:
        os.link(pending, path)
    except FileExistsError:
        if file_receipt(path, MAX_JSON) != {"bytes": len(data), "sha256": digest(data)}:
            failure()
    finally:
        pending.unlink()


def validate_descriptor(value):
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "protocol",
            "source",
            "source_snapshot_sha256",
            "decoder",
            "recipe",
            "timebase",
            "frames",
            "pcm_sha256",
            "window",
        }
        or value["protocol"] != PROTOCOL
        or value["decoder"] != DECODER
        or value["recipe"] != RECIPE
        or value["timebase"] != TIMEBASE
        or not _sha(value["source_snapshot_sha256"])
        or not _sha(value["pcm_sha256"])
        or type(value["frames"]) is not int
        or not 0 < value["frames"] <= MAX_FRAMES
        or value["window"] != {"start_frame": 0, "end_frame": value["frames"], "timebase": TIMEBASE}
        or any(type(value["window"][key]) is not int for key in ("start_frame", "end_frame"))
    ):
        failure()
    source = value["source"]
    if (
        not isinstance(source, dict)
        or set(source) != {"sha256", "bytes", "format", "stream"}
        or not _sha(source["sha256"])
        or type(source["bytes"]) is not int
        or not 0 < source["bytes"] <= MAX_SOURCE
        or source["bytes"] >= value["frames"] * 2
        or not isinstance(source["format"], str)
        or source["format"] not in DEMUXERS
        or source["stream"] != "0:a:0"
    ):
        failure()
    return value


def payload_name(descriptor):
    return "audio.source." + validate_descriptor(descriptor)["source"]["format"]


def input_names(files):
    """Return exact transport inputs; absence of descriptor preserves the WAV protocol."""
    if DESCRIPTOR not in files:
        return {"request.json", "audio.wav"}
    sources = {"audio.source." + extension for extension in DEMUXERS} & set(files)
    if len(sources) != 1 or "audio.wav" in files:
        failure()
    return {"request.json", DESCRIPTOR, *sources}


def verify_inputs(folder, files):
    """Validate a published descriptor against its exact input inventory, before acceptance."""
    names = input_names(files)
    if DESCRIPTOR not in names:
        return None
    descriptor = validate_descriptor(read(folder / DESCRIPTOR))
    raw = encoded(descriptor)
    if files[DESCRIPTOR] != {"size": len(raw), "sha256": digest(raw)}:
        failure()
    source = descriptor["source"]
    expected = {"size": source["bytes"], "sha256": source["sha256"]}
    if payload_name(descriptor) not in names or files[payload_name(descriptor)] != expected:
        failure()
    request = read(folder / "request.json", MAX_REQUEST)
    options = request.get("options") if isinstance(request, dict) else None
    if (
        not isinstance(options, dict)
        or options.get("execution") != "ucloud"
        or options.get("device") != "cuda"
        or options.get("retranscribe") is not False
        or options.get("allow_downloads") is not False
        or options.get("duration") != descriptor["frames"] / RATE
    ):
        failure()
    return descriptor


def for_job(params):
    """Fresh whole requests only; frozen legacy plans never acquire another transport."""
    connection = params.get("ucloud_api")
    batch = connection.get("batch_plan") if isinstance(connection, dict) else None
    plan = batch.get("identity") if isinstance(batch, dict) else None
    if not isinstance(plan, dict):
        return None
    if plan.get("input_protocol") != PROTOCOL or "audio_input" not in params:
        return None
    if params.get("enhancement", {}).get("execution") != "ucloud":
        return None
    if any(k in params for k in ("segment_id", "segment_ids", "timing_units")) or any(
        params.get(k) for k in ("realign", "passage_timing", "retranscription")
    ):
        return None
    return descriptor_for_source(params["audio_input"])


def descriptor_for_source(snapshot):
    from . import audio_inputs

    source = audio_inputs.validate(snapshot)
    if (
        source["duration_basis"] != "decoded_pcm_frames"
        or source["source_origin"]["has_video"]
        or source["audio_stream_count"] != 1
        or source["format"] not in DEMUXERS
        or source["bytes"] > MAX_SOURCE
        or source["bytes"] >= source["audio_frames"] * 2
        or source["audio_frames"] > MAX_FRAMES
    ):
        return None
    return validate_descriptor(
        {
            "protocol": PROTOCOL,
            "source": {k: source[k] for k in ("sha256", "bytes", "format")}
            | {"stream": source["audio_stream"]},
            "source_snapshot_sha256": digest(encoded(source)),
            "decoder": dict(DECODER),
            "recipe": RECIPE,
            "timebase": TIMEBASE,
            "frames": source["audio_frames"],
            "pcm_sha256": source["pcm_sha256"],
            "window": {"start_frame": 0, "end_frame": source["audio_frames"], "timebase": TIMEBASE},
        }
    )


def capability():
    import imageio_ffmpeg

    if sys.platform != "linux" or imageio_ffmpeg.__version__ != "0.6.0":
        failure()
    binary = Path(imageio_ffmpeg.__file__).parent / "binaries" / "ffmpeg-linux-x86_64-v7.0.2"
    if (
        not os.access(binary, os.X_OK)
        or file_receipt(binary, 128 * 1024**2)["sha256"] != DECODER["sha256"]
    ):
        failure()
    return {"protocol": PROTOCOL, "decoder": dict(DECODER)}, binary


def validate_preparation(value, descriptor):
    descriptor = validate_descriptor(descriptor)
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "protocol",
            "descriptor_sha256",
            "source",
            "decoder",
            "recipe",
            "timebase",
            "window",
            "prepared",
            "request_sha256",
        }
        or value["protocol"] != PROTOCOL
        or value["descriptor_sha256"] != digest(encoded(descriptor))
        or value["source"] != descriptor["source"]
        or value["decoder"] != DECODER
        or value["recipe"] != RECIPE
        or value["timebase"] != TIMEBASE
        or value["window"] != descriptor["window"]
        or not _sha(value["request_sha256"])
    ):
        failure()
    prepared = value["prepared"]
    expected = {
        "format": "wav",
        "frames": descriptor["frames"],
        "pcm_sha256": descriptor["pcm_sha256"],
        "sample_rate": RATE,
        "channels": 1,
        "sample_width": 2,
        "sample_basis": RECIPE,
    }
    if (
        not isinstance(prepared, dict)
        or set(prepared) != set(expected) | {"sha256", "bytes"}
        or any(type(prepared[k]) is not type(v) or prepared[k] != v for k, v in expected.items())
        or not _sha(prepared["sha256"])
        or type(prepared["bytes"]) is not int
        or prepared["bytes"] != 44 + descriptor["frames"] * 2
    ):
        failure()
    return value


def observed_binding(folder, params):
    """App-side stage validation from a verified transferred receipt, without fetching WAV."""
    descriptor = for_job(params)
    if descriptor is None:
        return None
    value = validate_preparation(read(folder / PREPARATION), descriptor)
    if (
        params.get("remote_audio_preparation") != value
        or value["request_sha256"] != file_receipt(folder / "request.json", MAX_REQUEST)["sha256"]
    ):
        failure()
    return {
        "input_sha256": value["prepared"]["sha256"],
        "request_sha256": value["request_sha256"],
        "audio_frames": descriptor["frames"],
    }


def report_preparation(context):
    """Metadata-only validation for a producing version; no file/runtime/network access."""
    if "remote_audio_preparation" not in context:
        return None
    descriptor = descriptor_for_source(context.get("audio_input"))
    value = validate_preparation(context["remote_audio_preparation"], descriptor)
    return {
        "status": "verified",
        "protocol": PROTOCOL,
        "source": value["source"],
        "source_snapshot_sha256": descriptor["source_snapshot_sha256"],
        "descriptor_sha256": value["descriptor_sha256"],
        "decoder": value["decoder"],
        "recipe": value["recipe"],
        "timebase": value["timebase"],
        "window": value["window"],
        "prepared": value["prepared"],
        "request_sha256": value["request_sha256"],
        "validation_scope": "captured_receipt_and_frozen_original_pcm",
        "current_files": "not_checked",
        "remote_runtime": "not_checked",
        "recognition_quality": "not_checked",
    }


def _prepared(folder, descriptor):
    value = validate_preparation(read(folder / PREPARATION), descriptor)
    if file_receipt(folder / "audio.wav", MAX_FRAMES * 2 + 44) != {
        k: value["prepared"][k] for k in ("sha256", "bytes")
    }:
        failure()
    if file_receipt(folder / "request.json", MAX_REQUEST)["sha256"] != value["request_sha256"]:
        failure()
    return value


def prepare(folder):
    """Run in the cancellable owned inference child; failed/uncertain outputs never rerun."""
    folder = Path(folder)
    if not (folder / DESCRIPTOR).exists():
        return None
    descriptor = validate_descriptor(read(folder / DESCRIPTOR))
    if (folder / PREPARATION).exists():
        return _prepared(folder, descriptor)
    pending, target = safe(folder / "audio.preparing.wav"), safe(folder / "audio.wav")
    if pending.exists() or target.exists():
        failure()
    capability()
    request_receipt = file_receipt(folder / "request.json", MAX_REQUEST)
    source = folder / payload_name(descriptor)
    if file_receipt(source, MAX_SOURCE) != {
        k: descriptor["source"][k] for k in ("sha256", "bytes")
    }:
        failure()
    if shutil.disk_usage(folder).free < descriptor["frames"] * 2 + 1024**2:
        raise PreparationError("Remote preparation has insufficient bounded output space.")
    expected_bytes, total = descriptor["frames"] * 2, 0
    pcm, errors = hashlib.sha256(), []
    with wave.open(str(pending), "wb") as output:
        output.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        # The bootstrap limits address space/CPU before exec and dies with this owned child.
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "decode",
                str(folder),
                str(os.getpid()),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        def drain():
            nonlocal total
            try:
                while block := process.stdout.read(1024**2):
                    total += len(block)
                    if total > expected_bytes:
                        failure()
                    output.writeframesraw(block)
                    pcm.update(block)
            except BaseException as error:
                errors.append(error)
                process.kill()

        reader = threading.Thread(target=drain)
        reader.start()
        try:
            process.wait(timeout=MAX_SECONDS)
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            reader.join()
            process.stdout.close()
        if (
            errors
            or process.returncode
            or total != expected_bytes
            or pcm.hexdigest() != descriptor["pcm_sha256"]
        ):
            failure()
    with pending.open("r+b") as output:
        os.fsync(output.fileno())
    prepared = {
        **file_receipt(pending, MAX_FRAMES * 2 + 44),
        "format": "wav",
        "frames": descriptor["frames"],
        "pcm_sha256": pcm.hexdigest(),
        "sample_rate": RATE,
        "channels": 1,
        "sample_width": 2,
        "sample_basis": RECIPE,
    }
    value = validate_preparation(
        {
            "protocol": PROTOCOL,
            "descriptor_sha256": digest(encoded(descriptor)),
            "source": descriptor["source"],
            "decoder": dict(DECODER),
            "recipe": RECIPE,
            "timebase": TIMEBASE,
            "window": descriptor["window"],
            "prepared": prepared,
            "request_sha256": request_receipt["sha256"],
        },
        descriptor,
    )
    if file_receipt(source, MAX_SOURCE) != {
        k: descriptor["source"][k] for k in ("sha256", "bytes")
    }:
        failure()
    if file_receipt(folder / "request.json", MAX_REQUEST) != request_receipt:
        failure()
    os.link(pending, target)
    pending.unlink()
    publish(folder / PREPARATION, encoded(value))
    return value


def decode(folder, parent_pid):
    import resource

    # Linux-only, fixed binary; no user command, URI, model or credential reaches exec.
    if (
        sys.platform != "linux"
        or ctypes.CDLL(None).prctl(1, signal.SIGKILL) != 0
        or os.getppid() != parent_pid
    ):
        failure()
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (MAX_SECONDS, MAX_SECONDS))
    _, binary = capability()
    descriptor = validate_descriptor(read(folder / DESCRIPTOR))
    source = safe(folder / payload_name(descriptor))
    demuxer = DEMUXERS[descriptor["source"]["format"]]
    args = [
        str(binary),
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-v",
        "error",
        "-xerror",
        "-err_detect",
        "explode",
        "-max_alloc",
        str(64 * 1024**2),
        "-threads",
        "2",
        "-filter_threads",
        "1",
        "-protocol_whitelist",
        "file,pipe",
        "-f",
        demuxer,
    ]
    if demuxer == "mov":
        args += ["-enable_drefs", "0"]
    args += [
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-ac",
        "1",
        "-ar",
        str(RATE),
        "-c:a",
        "pcm_s16le",
        "-f",
        "s16le",
        "pipe:1",
    ]
    os.execve(binary, args, {"PATH": "/usr/bin:/bin", "LANG": "C"})


if __name__ == "__main__":
    if sys.argv[1:] == ["capability"]:
        print(encoded(capability()[0]).decode())
    elif len(sys.argv) == 4 and sys.argv[1] == "decode":
        decode(Path(sys.argv[2]), int(sys.argv[3]))
    else:
        raise SystemExit("Unsupported remote audio preparation operation")
