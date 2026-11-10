"""Standalone, credential-free, fixed-language selected-clip alignment controller."""

import argparse
import contextlib
import hashlib
import importlib.util
import json
import math
import os
import re
import signal
import stat
import struct
import tempfile
import threading
from pathlib import Path

PROTOCOL = "cbs-repair-cloud-v1"
BUNDLE_FORMAT = "cbs-repair-bundle-v1"
OUTPUT_FORMAT = "cbs-repair-alignment-v1"
ACCEPTED_FORMAT = "cbs-repair-accepted-v1"
STARTED_FORMAT = "cbs-repair-started-v1"
RECEIPT_FORMAT = "cbs-repair-output-receipt-v1"
FAILURE_FORMAT = "cbs-repair-failure-v1"
RATE = 16000
MAX_UNITS = 200
MAX_JSON = 4 * 1024 * 1024
MAX_REQUEST = 1024 * 1024
MAX_STATUS = 64 * 1024
MAX_CODE = 2 * 1024 * 1024
MAX_AUDIO = 60 * RATE * 2 + 44
CODE_NAMES = {
    "enhancement_worker.py",
    "enhancement_remote.py",
    "danish_models.py",
    "remote_audio_input.py",
    "danish_whisper_models.json",
}


def encoded(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def failure():
    raise ValueError("The selected-clip repair protocol failed validation.")


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def signature(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        failure()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def owned(path, root):
    path, root = Path(path), Path(root)
    if not path.absolute().is_relative_to(root.absolute()):
        failure()
    for part in (root, *path.parents):
        if part == root.parent:
            break
        if part.is_symlink() or part.is_junction():
            failure()
    if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root.resolve()):
        failure()
    return path


def read(path, root, limit):
    path = owned(path, root)
    before = signature(path.lstat())
    if not 0 < before[2] <= limit:
        failure()
    with path.open("rb") as stream:
        if before != signature(os.fstat(stream.fileno())):
            failure()
        data = stream.read(limit + 1)
        after = signature(os.fstat(stream.fileno()))
    if len(data) != before[2] or before != after or after != signature(path.lstat()):
        failure()
    owned(path, root)
    return data


def pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            failure()
        result[key] = value
    return result


def decoded(raw, limit=MAX_JSON):
    if len(raw) > limit:
        failure()
    depth, quoted, escaped = 0, False, False
    for char in raw.decode("utf-8"):
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
            if depth > 24:
                failure()
        elif char in "]}":
            depth -= 1
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: failure())
    if encoded(value) != raw:
        failure()
    return value


def json_read(path, root, limit=MAX_JSON):
    return decoded(read(path, root, limit), limit)


def receipt(raw):
    return {"sha256": digest(raw), "bytes": len(raw)}


def write(path, value, root, *, immutable=True):
    raw = encoded(value)
    if len(raw) > MAX_REQUEST:
        failure()
    path = owned(path, root)
    if path.exists():
        signature(path.lstat())
    if immutable and path.exists():
        if read(path, root, MAX_REQUEST) != raw:
            failure()
        return receipt(raw)
    if not immutable:
        # Never truncate a leftover pending file which may have acquired another owner/link.
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix="repair-status-", suffix=".pending", delete=False
        ) as target:
            temporary = owned(Path(target.name), root)
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(path)
        return receipt(raw)
    temporary = owned(path.with_name(path.name + ".pending"), root)
    with temporary.open("xb" if immutable else "wb") as target:
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())
    if immutable:
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
    else:
        temporary.replace(path)
    return receipt(raw)


def header(frames):
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


def verify_audio(path, root, expected):
    frames = expected.get("frames")
    if (
        type(frames) is not int
        or not 0 < frames <= 60 * RATE
        or expected.get("bytes") != frames * 2 + 44
    ):
        failure()
    if signature(owned(path, root).lstat())[2] != expected["bytes"]:
        failure()
    raw = read(path, root, MAX_AUDIO)
    if (
        raw[:44] != header(frames)
        or receipt(raw) != {key: expected[key] for key in ("sha256", "bytes")}
        or digest(raw[44:]) != expected["pcm_sha256"]
    ):
        failure()


def validate_output(value, manifest, index):
    if (
        not isinstance(value, dict)
        or set(value) != {"format", "bundle_sha256", "unit_index", "coordinate_space", "words"}
        or value["format"] != OUTPUT_FORMAT
        or value["coordinate_space"] != "clip"
        or value["bundle_sha256"] != manifest_hash(manifest)
        or type(value["unit_index"]) is not int
        or value["unit_index"] != index
    ):
        failure()
    unit = manifest["units"][index]
    request = unit["_request"] if "_request" in unit else None
    if request is None:
        failure()
    tokens = list(re.finditer(r"\S+", request["text"]))
    words = value["words"]
    if not isinstance(words, list) or len(words) != len(tokens):
        failure()
    duration = (unit["window"]["end_frame"] - unit["window"]["start_frame"]) / RATE
    previous_start, previous_end = -1, -1
    for word, token in zip(words, tokens, strict=True):
        if (
            not isinstance(word, dict)
            or set(word) != {"word", "start", "end", "score", "char_start", "char_end"}
            or word["word"] != token.group()
            or type(word["char_start"]) is not int
            or type(word["char_end"]) is not int
            or (word["char_start"], word["char_end"]) != token.span()
            or sum(
                item["char_start"] <= token.start() < token.end() <= item["char_end"]
                for item in request["ranges"]
            )
            != 1
        ):
            failure()
        a, b, score = word["start"], word["end"], word["score"]
        if (
            any(v is not None and (not finite(v) or not 0 <= v <= duration) for v in (a, b))
            or (a is not None and b is not None and a >= b)
            or (score is not None and (not finite(score) or not 0 <= score <= 1))
            or (a is not None and a < previous_start)
            or (b is not None and b < previous_end)
        ):
            failure()
        previous_start = previous_start if a is None else a
        previous_end = previous_end if b is None else b
    return value


def manifest_hash(manifest):
    # Requests are local parsed conveniences and never part of the immutable manifest.
    return digest(
        encoded(
            {
                **manifest,
                "units": [
                    {k: v for k, v in unit.items() if k != "_request"} for unit in manifest["units"]
                ],
            }
        )
    )


def load(directory, generation):
    control = json_read(directory / "repair-run.json", directory, MAX_STATUS)
    if (
        not isinstance(control, dict)
        or set(control) != {"protocol", "generation", "bundle_sha256", "code"}
        or control["protocol"] != PROTOCOL
        or control["generation"] != generation
        or not isinstance(generation, str)
        or re.fullmatch(r"[a-f0-9]{32}", generation) is None
    ):
        failure()
    codes = control["code"]
    if (
        not isinstance(codes, dict)
        or not {"enhancement_worker.py", "enhancement_remote.py"} <= set(codes) <= CODE_NAMES
    ):
        failure()
    for name, expected in codes.items():
        if receipt(read(directory / name, directory, MAX_CODE)) != expected:
            failure()
    bundle = directory / "bundle"
    raw = read(bundle / "manifest.json", bundle, MAX_JSON)
    if digest(raw) != control["bundle_sha256"] or json_read(
        bundle / "COMMITTED.json", bundle, 4096
    ) != {"format": "cbs-repair-bundle-commit-v1", "manifest": receipt(raw)}:
        failure()
    manifest = decoded(raw)
    if (
        manifest.get("format") != BUNDLE_FORMAT
        or manifest.get("source", {}).get("job_id") != generation
        or manifest.get("runtime", {}).get("worker_sha256")
        != codes["enhancement_worker.py"]["sha256"]
        or manifest["runtime"].get("language") not in {"da", "en"}
        or not isinstance(manifest.get("units"), list)
        or not 0 < len(manifest["units"]) <= MAX_UNITS
    ):
        failure()
    known = {"manifest.json", "COMMITTED.json", "units"}
    for index, unit in enumerate(manifest["units"]):
        prefix = f"units/{index:05d}"
        known |= {prefix, prefix + "/audio.wav", prefix + "/request.json"}
        if type(unit.get("index")) is not int or unit["index"] != index:
            failure()
        raw_request = read(bundle / prefix / "request.json", bundle, MAX_REQUEST)
        if receipt(raw_request) != unit["request"]:
            failure()
        request = decoded(raw_request, MAX_REQUEST)
        if (
            set(request)
            != {
                "format",
                "operation",
                "unit_index",
                "language",
                "alignment_model",
                "min_score",
                "timing_policy",
                "text",
                "ranges",
                "audio",
                "bundle_key",
            }
            or request["format"] != "cbs-repair-unit-v1"
            or request["operation"] != "alignment"
            or type(request["unit_index"]) is not int
            or request["unit_index"] != index
            or request["language"] != manifest["runtime"]["language"]
            or request["alignment_model"] != manifest["runtime"]["alignment_model"]
            or request["bundle_key"] != manifest["bundle_key"]
            or not isinstance(request["text"], str)
            or not 0 < len(request["text"]) <= 1200
            or len(request["text"].split()) > 120
            or not finite(request["min_score"])
            or not 0 <= request["min_score"] <= 1
            or request["timing_policy"] != "retain-estimates"
            or request["timing_policy"] != manifest["options"].get("timing_policy")
        ):
            failure()
        start, end = unit["window"]["start_frame"], unit["window"]["end_frame"]
        if type(start) is not int or type(end) is not int:
            failure()
        original = manifest["original_audio"]
        source_frames = original["audio_frames"]
        if original["duration_basis"] == "container_observation":
            if (
                source_frames is not None
                or original["pcm_sha256"] is not None
                or not finite(original["duration"])
                or original["duration"] <= 0
                or original["source_origin"]["basis"] != "encoded_packet"
                or original["source_origin"]["first_pts"] != 0
            ):
                failure()
            source_frames = round(original["duration"] * RATE)
            if unit.get("timeline") != {
                "policy": "selected-pcm-contiguous-v1",
                "start_frame": start,
                "end_frame": end,
                "frames": end - start,
            } or any(
                type(unit["timeline"][key]) is not int
                for key in ("start_frame", "end_frame", "frames")
            ):
                failure()
        if (
            type(start) is not int
            or type(end) is not int
            or type(source_frames) is not int
            or not 0 <= start < end <= source_frames
            or end - start > 60 * RATE
            or unit["audio"]["frames"] != end - start
            or request["audio"] != {"file": "audio.wav", "frames": end - start, "sample_rate": RATE}
        ):
            failure()
        if not isinstance(request["ranges"], list) or len(request["ranges"]) != len(
            unit["passages"]
        ):
            failure()
        previous = 0
        for span, passage in zip(request["ranges"], unit["passages"], strict=True):
            if (
                set(span) != {"id", "char_start", "char_end"}
                or any(span[k] != passage[k] for k in span)
                or type(span["char_start"]) is not int
                or type(span["char_end"]) is not int
                or not previous <= span["char_start"] < span["char_end"] <= len(request["text"])
                or digest(request["text"][span["char_start"] : span["char_end"]].encode("utf-8"))
                != passage["text_sha256"]
            ):
                failure()
            previous = span["char_end"]
        verify_audio(bundle / prefix / "audio.wav", bundle, unit["audio"])
        unit["_request"] = request
    for child in bundle.rglob("*"):
        owned(child, bundle)
        if child.relative_to(bundle).as_posix() not in known:
            failure()
        if child.is_file():
            signature(child.lstat())
    return control, manifest


def accepted(manifest, index):
    unit = manifest["units"][index]
    return {
        "format": ACCEPTED_FORMAT,
        "bundle_sha256": manifest_hash(manifest),
        "unit_index": index,
        "request": unit["request"],
        "audio": {key: unit["audio"][key] for key in ("sha256", "bytes")},
    }


def success_receipt(manifest, index, output):
    return {
        "format": RECEIPT_FORMAT,
        "bundle_sha256": manifest_hash(manifest),
        "unit_index": index,
        "accepted": receipt(encoded(accepted(manifest, index))),
        "output": receipt(encoded(output)),
    }


def output_value(manifest, index, words):
    tokens = list(re.finditer(r"\S+", manifest["units"][index]["_request"]["text"]))
    if not isinstance(words, list) or len(words) != len(tokens):
        failure()
    value = {
        "format": OUTPUT_FORMAT,
        "bundle_sha256": manifest_hash(manifest),
        "unit_index": index,
        "coordinate_space": "clip",
        "words": [
            {
                "word": word["word"],
                "start": word["start"],
                "end": word["end"],
                "score": word.get("score"),
                "char_start": token.start(),
                "char_end": token.end(),
            }
            for word, token in zip(words, tokens, strict=True)
        ],
    }
    return validate_output(value, manifest, index)


def load_worker(directory, manifest):
    path = directory / "enhancement_worker.py"
    spec = importlib.util.spec_from_file_location("cbs_repair_frozen_worker", path)
    worker = importlib.util.module_from_spec(spec)
    exec(compile(read(path, directory, MAX_CODE), str(path), "exec"), worker.__dict__)
    runtime = manifest["runtime"]
    if tuple(worker.MODELS[runtime["language"]]) != (
        runtime["alignment_model"]["repo"],
        runtime["alignment_model"]["revision"],
    ):
        failure()
    worker.CACHE_ALIGNMENT = True
    return worker


def align_unit(directory, manifest, index, worker):
    unit, bundle = manifest["units"][index], directory / "bundle"
    request = unit["_request"]
    work = directory / "work" / f"{index:05d}"
    owned(work, directory)
    work.mkdir(parents=True, exist_ok=True)
    duration = unit["audio"]["frames"] / RATE
    task = {
        "options": {
            "language": request["language"],
            "device": "cuda",
            "align": True,
            "diarize": False,
            "transcribe": False,
            "retranscribe": False,
            "allow_downloads": False,
            "duration": duration,
            "min_score": request["min_score"],
            "timing_policy": request["timing_policy"],
        },
        "passages": [{"id": "0", "text": request["text"], "start": 0, "end": duration}],
    }
    if worker.needs_coarse(task["passages"], duration):
        failure()
    write(work / "request.json", task, directory)
    source = bundle / "units" / f"{index:05d}" / "audio.wav"
    with (work / "audio.wav").open("xb") as target:
        target.write(read(source, bundle, MAX_AUDIO))
        target.flush()
        os.fsync(target.fileno())
    if worker.process_directory(work):
        raise ValueError("Alignment did not finish.")
    value = json_read(work / "alignment.json", directory, MAX_REQUEST)
    if (
        not isinstance(value, dict)
        or set(value) != {"passages"}
        or len(value["passages"]) != 1
        or value["passages"][0].get("id") != "0"
    ):
        failure()
    return value["passages"][0]["words"]


def process(directory, generation, *, runner=None, cancel=None):
    """Execute each accepted unit once; completed checkpoints survive controller restarts."""
    directory = Path(directory).absolute()
    cancel = cancel or threading.Event()
    control, manifest = load(directory, generation)
    worker = None
    counts = {"completed": 0, "failed": 0, "uncertain": 0, "unstarted": 0}
    for index, unit in enumerate(manifest["units"]):
        out = directory / "out" / f"{index:05d}"
        owned(out, directory)
        out.mkdir(parents=True, exist_ok=True)
        ack = accepted(manifest, index)
        if (out / "accepted.json").exists() and json_read(
            out / "accepted.json", directory, MAX_STATUS
        ) != ack:
            failure()
        has_ack, has_started = (out / "accepted.json").exists(), (out / "started.json").exists()
        if has_started and (
            not has_ack
            or json_read(out / "started.json", directory, MAX_STATUS)
            != {"format": STARTED_FORMAT, "accepted": receipt(encoded(ack))}
        ):
            failure()
        if (out / "output.json").exists():
            output = validate_output(
                json_read(out / "output.json", directory, MAX_REQUEST), manifest, index
            )
            if not has_ack or not has_started:
                failure()
            write(out / "receipt.json", success_receipt(manifest, index, output), directory)
            counts["completed"] += 1
            continue
        if (out / "failure.json").exists():
            if not has_ack or not has_started:
                failure()
            failed = json_read(out / "failure.json", directory, MAX_STATUS)
            if failed != {
                "format": FAILURE_FORMAT,
                "accepted": receipt(encoded(ack)),
                "code": "processing",
            }:
                failure()
            counts["failed"] += 1
            continue
        if (out / "started.json").exists():
            counts["uncertain"] += 1
            continue
        if cancel.is_set():
            counts["unstarted"] += 1
            continue
        if runner is None and worker is None:
            # Complete/uncertain checkpoint recovery never imports or checks inference.
            worker = load_worker(directory, manifest)
        write(out / "accepted.json", ack, directory)
        write(
            out / "started.json",
            {"format": STARTED_FORMAT, "accepted": receipt(encoded(ack))},
            directory,
        )
        try:
            words = (
                runner(unit["_request"], directory / "bundle/units" / f"{index:05d}" / "audio.wav")
                if runner
                else align_unit(directory, manifest, index, worker)
            )
            output = output_value(manifest, index, words)
            write(out / "output.json", output, directory)
            write(out / "receipt.json", success_receipt(manifest, index, output), directory)
            counts["completed"] += 1
        except Exception:
            # Never replace a possibly completed output with a failure receipt.
            if (out / "output.json").exists() or (out / "output.json.pending").exists():
                raise
            write(
                out / "failure.json",
                {"format": FAILURE_FORMAT, "accepted": receipt(encoded(ack)), "code": "processing"},
                directory,
            )
            counts["failed"] += 1
        write(
            directory / "remote-status.json",
            {
                "protocol": PROTOCOL,
                "generation": generation,
                "bundle_sha256": control["bundle_sha256"],
                "state": "running",
                **counts,
            },
            directory,
            immutable=False,
        )
    status = {
        "protocol": PROTOCOL,
        "generation": generation,
        "bundle_sha256": control["bundle_sha256"],
        "state": "complete" if counts["completed"] == len(manifest["units"]) else "partial",
        **counts,
    }
    write(directory / "remote-status.json", status, directory, immutable=False)
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["run"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("generation")
    args = parser.parse_args()
    import fcntl

    if os.environ.get("CBS_RUNTIME_LOCK_FD") != "9":
        failure()
    os.fstat(9)
    allowed = {
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "LD_LIBRARY_PATH",
        "CUDA_VISIBLE_DEVICES",
        "OMP_NUM_THREADS",
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "TORCH_HOME",
        "NLTK_DATA",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CBS_RUNTIME_LOCK_FD",
    }
    for key in tuple(os.environ):
        if key not in allowed:
            del os.environ[key]

    cancel = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: cancel.set())
    signal.signal(signal.SIGINT, lambda *_: cancel.set())
    with (args.directory / "remote-worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (
            open(os.devnull, "w") as quiet,
            contextlib.redirect_stdout(quiet),
            contextlib.redirect_stderr(quiet),
        ):
            process(args.directory, args.generation, cancel=cancel)


if __name__ == "__main__":
    main()
