"""Bounded synthetic T87 transport/timebase experiment; no app, network or inference calls.

Run from the repository: uv run python scripts/evaluate_media_transport.py --output NEW_DIR
The output directory must not exist. Audio/logs/JSON belong under ignored test-results/.
"""

import argparse
import hashlib
import json
import math
import os
import platform
import random
import re
import subprocess
import time
import wave
from array import array
from importlib.metadata import version
from pathlib import Path

import imageio_ffmpeg


def digest(data):
    return hashlib.sha256(data).hexdigest()


def generate(path, seconds):
    """Seeded stereo tones/noise with silence; deliberately not interview/speech evidence."""
    generator, samples, rate = random.Random(870027), array("h"), 48000
    for frame in range(seconds * rate):
        t = frame / rate
        envelope = 0 if t < 1 or int(t) % 7 == 6 else 0.55 + 0.25 * math.sin(7 * t) ** 2
        noise = generator.uniform(-0.12, 0.12)
        left = envelope * (0.34 * math.sin(2 * math.pi * (220 * t + 2 * t * t)) + noise)
        right = envelope * (0.28 * math.sin(2 * math.pi * 741 * t) + 0.6 * noise)
        samples.extend((round(32767 * left), round(32767 * right)))
    if os.sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setparams((2, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(samples.tobytes())


def pcm(path):
    with wave.open(str(path), "rb") as source:
        channels, width, rate, frames = (
            source.getnchannels(),
            source.getsampwidth(),
            source.getframerate(),
            source.getnframes(),
        )
        raw = source.readframes(frames)
    if len(raw) != frames * channels * width:
        raise ValueError("Decoded WAV is truncated.")
    values = array("h", raw)
    if os.sys.byteorder != "little":
        values.byteswap()
    first_signal = None
    block = rate // 100
    for index in range(0, len(values) - block + 1, block):
        rms = math.sqrt(sum(v * v for v in values[index : index + block]) / block)
        if rms > 500:
            first_signal = index / rate
            break
    return {
        "channels": channels,
        "sample_width": width,
        "sample_rate": rate,
        "frames": frames,
        "seconds": frames / rate,
        "pcm_sha256": digest(raw),
        "first_10ms_block_rms_above_500_seconds": first_signal,
    }


class Experiment:
    def __init__(self, directory):
        self.directory, self.runs = directory, []
        self.executable = imageio_ffmpeg.get_ffmpeg_exe()

    def run(self, label, args, *, failure_allowed=False):
        command = [
            self.executable,
            "-hide_banner",
            "-nostdin",
            "-nostats",
            "-y",
            "-benchmark",
            *args,
        ]
        started = time.perf_counter()
        result = subprocess.run(
            command,
            cwd=self.directory,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        wall = time.perf_counter() - started
        log = result.stderr.decode("utf-8", errors="replace")
        (self.directory / (label + ".log")).write_text(log, encoding="utf-8")
        bench = re.search(r"utime=([\d.]+)s\s+stime=([\d.]+)s\s+rtime=([\d.]+)s", log)
        record = {
            "label": label,
            "command": ["[bundled ffmpeg]", *command[1:]],
            "returncode": result.returncode,
            "subprocess_wall_seconds": wall,
            "ffmpeg_cpu_seconds": float(bench[1]) + float(bench[2]) if bench else None,
            "ffmpeg_reported_wall_seconds": float(bench[3]) if bench else None,
        }
        self.runs.append(record)
        if result.returncode and not failure_allowed:
            raise RuntimeError(f"Synthetic stage {label} failed; inspect its local log.")
        return record, log

    def decode(self, label, filename, *, stream="0:a:0", strict=False):
        args = [*(["-xerror", "-err_detect", "explode"] if strict else []), "-i", filename]
        if stream is not None:
            args += ["-map", stream]
        output = label + ".wav"
        record, _ = self.run(
            label,
            [*args, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", output],
            failure_allowed=True,
        )
        target = self.directory / output
        if target.exists():
            try:
                record["pcm"] = pcm(target)
            except (wave.Error, ValueError, EOFError):
                record["pcm"] = None
        else:
            record["pcm"] = None
        return record

    def first_pts(self, filename, stream):
        label = filename.replace(".", "-") + "-" + stream.replace(":", "-") + "-pts"
        _, log = self.run(
            label,
            [
                "-copyts",
                "-i",
                filename,
                "-map",
                stream,
                "-af",
                "ashowinfo",
                "-frames:a",
                "1",
                "-f",
                "null",
                "-",
            ],
        )
        found = re.search(r"pts_time:([\d.e+-]+)", log)
        return float(found[1]) if found else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seconds", type=int, default=30, choices=range(5, 61), metavar="5..60")
    args = parser.parse_args()
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    experiment = Experiment(directory)
    generate(directory / "generated.wav", args.seconds)
    experiment.run(
        "make-original",
        [
            "-i",
            "generated.wav",
            "-map",
            "0:a:0",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            "original.m4a",
        ],
    )
    experiment.run(
        "make-browser",
        [
            "-i",
            "original.m4a",
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            "browser.mp3",
        ],
    )
    current = experiment.decode("current", "browser.mp3")
    if current["returncode"] or not current["pcm"]:
        raise AssertionError("Synthetic current-path baseline did not decode.")
    replay = experiment.decode("browser-remote-simulation", "browser.mp3")
    experiment.run(
        "make-normalized-flac",
        [
            "-i",
            "browser.mp3",
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-sample_fmt",
            "s16",
            "-c:a",
            "flac",
            "-compression_level",
            "5",
            "normalized.flac",
        ],
    )
    restored = experiment.decode("flac-remote-simulation", "normalized.flac")
    direct = experiment.decode("original-remote-simulation", "original.m4a")
    experiment.run(
        "remux-original", ["-i", "original.m4a", "-map", "0:a:0", "-c:a", "copy", "remux.mkv"]
    )
    remux = experiment.decode("remux-decoded", "remux.mkv")
    experiment.run(
        "make-multistream",
        [
            "-i",
            "original.m4a",
            "-itsoffset",
            "2.5",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=880:sample_rate=48000:duration={args.seconds}",
            "-map",
            "0:a:0",
            "-map",
            "1:a:0",
            "-c:a:0",
            "copy",
            "-c:a:1",
            "aac",
            "-b:a:1",
            "64k",
            "-ac:a:1",
            "2",
            "-disposition:a:0",
            "0",
            "-disposition:a:1",
            "default",
            "-copyts",
            "multistream.mkv",
        ],
    )
    mapped = experiment.decode("multistream-first", "multistream.mkv")
    second = experiment.decode("multistream-second", "multistream.mkv", stream="0:a:1")
    automatic = experiment.decode("multistream-automatic", "multistream.mkv", stream=None)
    raw = (directory / "original.m4a").read_bytes()
    (directory / "truncated.m4a").write_bytes(raw[: len(raw) // 2])
    truncated = experiment.decode("truncated-default", "truncated.m4a")
    strict = experiment.decode("truncated-strict", "truncated.m4a", strict=True)
    expected = current["pcm"]
    for label, candidate in (("browser payload", replay), ("normalized FLAC", restored)):
        if candidate["returncode"] or candidate["pcm"] != expected:
            raise AssertionError(f"{label} changed the exact prepared PCM/timebase.")
    if mapped["pcm"]["pcm_sha256"] == second["pcm"]["pcm_sha256"]:
        raise AssertionError("Synthetic audio-stream fixture was not distinctive.")
    if truncated["returncode"] == 0 and truncated["pcm"] == direct["pcm"]:
        raise AssertionError("Synthetic truncation did not exercise incomplete media.")
    names = [
        "generated.wav",
        "original.m4a",
        "browser.mp3",
        "current.wav",
        "normalized.flac",
        "remux.mkv",
        "multistream.mkv",
        "truncated.m4a",
    ]
    sizes = {
        name: {
            "bytes": (directory / name).stat().st_size,
            "sha256": digest((directory / name).read_bytes()),
        }
        for name in names
    }
    report = {
        "schema": 1,
        "synthetic_seconds": args.seconds,
        "fixture_seed": 870027,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "imageio_ffmpeg": version("imageio-ffmpeg"),
        "ffmpeg": imageio_ffmpeg.get_ffmpeg_version(),
        "ffmpeg_binary_sha256": digest(Path(experiment.executable).read_bytes()),
        "measurements": experiment.runs,
        "files": sizes,
        "checks": {
            "browser_payload_pcm_exact": True,
            "normalized_flac_pcm_exact": True,
            "original_pcm_matches_current": direct["pcm"] == expected,
            "remux_pcm_matches_original": remux["pcm"] == direct["pcm"],
            "automatic_stream_matches_first": automatic["pcm"] == mapped["pcm"],
            "automatic_stream_matches_second": automatic["pcm"] == second["pcm"],
            "truncated_default_returncode": truncated["returncode"],
            "truncated_strict_returncode": strict["returncode"],
        },
        "first_decoded_pts_seconds": {
            "original_first": experiment.first_pts("original.m4a", "0:a:0"),
            "remux_first": experiment.first_pts("remux.mkv", "0:a:0"),
            "multi_first": experiment.first_pts("multistream.mkv", "0:a:0"),
            "multi_second": experiment.first_pts("multistream.mkv", "0:a:1"),
        },
        "scope": "All timings are local subprocess measurements, "
        "including remote-decode simulations. "
        "No network transfer, UCloud allocation, GPU time, inference or cost was measured.",
    }
    (directory / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "checks": report["checks"],
                "bytes": {k: v["bytes"] for k, v in sizes.items()},
                "first_decoded_pts_seconds": report["first_decoded_pts_seconds"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
