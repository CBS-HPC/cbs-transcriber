"""Small synthetic many-file preparation assessment: local measurements, no remote calls.

Run: uv run python scripts/evaluate_batch_preparation.py --output NEW_DIRECTORY
All inputs are generated tones/noise. This is not an inference, network or UCloud benchmark.
"""

import argparse
import hashlib
import json
import math
import os
import platform
import runpy
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import imageio_ffmpeg

from cbs_transcriber.audio_inputs import POLICY, RECIPE, TIMEBASE

# Reuse the earlier bounded generator/FFmpeg measurements, without its CLI/codec experiment.
BASELINE = Path(__file__).with_name("evaluate_media_transport.py")
_helpers = runpy.run_path(str(BASELINE))
Experiment, generate, pcm = (_helpers[name] for name in ("Experiment", "generate", "pcm"))
PROFILES = ("m4a", "wav", "m4a", "video", "m4a", "wav", "m4a", "video")


def file_info(path):
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return {"bytes": path.stat().st_size, "sha256": digest}


def rate(value):
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 10_000:
        raise ValueError("Assumed rates must be finite, positive Mbit/s, at most 10,000.")
    return number


def transfer_estimates(original_bytes, wav_bytes, rates):
    """Payload arithmetic only. Signed headroom includes cases where originals are larger."""
    if any(type(value) is not int or value < 0 for value in (original_bytes, wav_bytes)):
        raise ValueError("Payload sizes must be nonnegative integer byte counts.")
    result = []
    for value in rates:
        mbits = rate(value)
        original = original_bytes * 8 / (mbits * 1_000_000)
        wav = wav_bytes * 8 / (mbits * 1_000_000)
        result.append(
            {
                "assumed_effective_mbit_per_second": mbits,
                "original_payload_seconds": original,
                "wav_payload_seconds": wav,
                "wav_minus_original_seconds": wav - original,
            }
        )
    return result


def exact_pcm(candidate, reference):
    return all(
        candidate[field] == reference[field]
        for field in ("channels", "sample_width", "sample_rate", "frames", "pcm_sha256")
    )


def normalize(experiment, label, source, output):
    measurement, _ = experiment.run(
        label,
        [
            "-xerror",
            "-err_detect",
            "explode",
            "-i",
            source,
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            output,
        ],
    )
    data = pcm(experiment.directory / output)
    if (data["channels"], data["sample_width"], data["sample_rate"]) != (1, 2, 16000):
        raise ValueError("Synthetic preparation produced an unexpected PCM shape.")
    return {
        "measurement": measurement,
        "pcm": data,
        "output": output,
        **file_info(experiment.directory / output),
    }


def prepare_batch(experiment, files, workers, label):
    if workers not in {1, 2} or not files:
        raise ValueError("Use one or two local workers and a nonempty bounded batch.")
    started, cpu = time.perf_counter(), time.process_time()

    def prepare(item):
        result = normalize(
            experiment,
            f"{label}-{item['id']}",
            item["file"],
            f"prepared/{label}-{item['id']}.wav",
        )
        result.update(
            id=item["id"],
            profile=item["profile"],
            finished_after_seconds=time.perf_counter() - started,
        )
        return result

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(prepare, files))
    measurements = [result["measurement"] for result in results]
    return batch_summary(results, measurements, started, cpu, workers)


def batch_summary(results, measurements, started, cpu, workers):
    ffmpeg_cpu = [item["ffmpeg_cpu_seconds"] for item in measurements]
    return {
        "file_count": len(results),
        "local_workers": workers,
        "batch_wall_seconds": time.perf_counter() - started,
        "python_process_cpu_seconds": time.process_time() - cpu,
        "ffmpeg_cpu_seconds_sum": sum(ffmpeg_cpu)
        if all(x is not None for x in ffmpeg_cpu)
        else None,
        "subprocess_wall_seconds_sum": sum(
            item["subprocess_wall_seconds"] for item in measurements
        ),
        "files": results,
    }


def build_fixtures(experiment, count, seconds):
    results = []
    for index, profile in enumerate(PROFILES[:count]):
        identifier = f"file-{index + 1:02d}"
        duration = seconds + index % 3
        generated = f"inputs/{identifier}-generated.wav"
        generate(experiment.directory / generated, duration)
        if profile == "wav":
            filename = generated
        else:
            suffix = "mp4" if profile == "video" else "m4a"
            filename = f"inputs/{identifier}.{suffix}"
            args = ["-i", generated]
            if profile == "video":
                args += [
                    "-f",
                    "lavfi",
                    "-i",
                    f"testsrc2=size=640x360:rate=15:duration={duration}",
                    "-map",
                    "1:v:0",
                    "-map",
                    "0:a:0",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                ]
            else:
                args += ["-map", "0:a:0"]
            args += ["-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", filename]
            experiment.run(f"fixture-{identifier}", args)
        results.append(
            {
                "id": identifier,
                "profile": profile,
                "file": filename,
                "generated_seconds": duration,
                **file_info(experiment.directory / filename),
            }
        )
    return results


def browser_batch(experiment, files, workers=2):
    """Browser presentation work is measured separately, always from the original."""
    started, cpu = time.perf_counter(), time.process_time()

    def prepare(item):
        measurements = []
        audio = f"browser/{item['id']}.mp3"
        measurement, _ = experiment.run(
            f"browser-audio-{item['id']}",
            [
                "-i",
                item["file"],
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
                audio,
            ],
        )
        measurements.append(measurement)
        result = {
            "id": item["id"],
            "profile": item["profile"],
            "audio": file_info(experiment.directory / audio),
        }
        if item["profile"] == "video":
            output = f"browser/{item['id']}.mp4"
            measurement, _ = experiment.run(
                f"browser-video-{item['id']}",
                [
                    "-i",
                    item["file"],
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0",
                    "-sn",
                    "-dn",
                    "-vf",
                    "scale=w='min(1280,iw)':h=-2",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "25",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-movflags",
                    "+faststart",
                    output,
                ],
            )
            measurements.append(measurement)
            result["video"] = file_info(experiment.directory / output)
        result["measurements"] = measurements
        result["finished_after_seconds"] = time.perf_counter() - started
        return result

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(prepare, files))
    return batch_summary(
        results, [m for r in results for m in r["measurements"]], started, cpu, workers
    )


def candidates(experiment, files, references):
    """One representative per candidate; eligibility requires exact local normalized PCM."""
    wav = next(item for item in files if item["profile"] == "wav")
    video = next(item for item in files if item["profile"] == "video")
    choices = [
        ("wav-source-flac", wav, wav["file"], "candidates/source.flac", ["-c:a", "flac"]),
        ("video-audio-remux", video, video["file"], "candidates/audio.m4a", ["-c:a", "copy"]),
        (
            "normalized-flac",
            wav,
            references[wav["id"]]["output"],
            "candidates/normalized.flac",
            ["-c:a", "flac"],
        ),
    ]
    results = []
    for name, original, source, output, codec in choices:
        before = len(experiment.runs)
        experiment.run(name, ["-i", source, "-map", "0:a:0", "-vn", *codec, output])
        decoded = normalize(experiment, name + "-decode", output, f"candidates/{name}.wav")
        reference = references[original["id"]]
        results.append(
            {
                "candidate": name,
                "source_id": original["id"],
                **file_info(experiment.directory / output),
                "reference_wav_bytes": reference["bytes"],
                "local_pcm_exact": exact_pcm(decoded["pcm"], reference["pcm"]),
                "decoded_pcm": decoded["pcm"],
                "measurements": experiment.runs[before:],
                "qualification_scope": (
                    "This fixture and local decoder only; not a Linux/remote receipt."
                ),
            }
        )
    return results


def run_assessment(directory, *, counts=(4, 8), workers=(1, 2), seconds=15, rates=(5, 20, 100)):
    counts, workers = tuple(dict.fromkeys(counts)), tuple(dict.fromkeys(workers))
    if not counts or any(type(x) is not int or x not in {4, 8} for x in counts):
        raise ValueError("Use four and/or eight synthetic files.")
    if not workers or any(type(x) is not int or x not in {1, 2} for x in workers):
        raise ValueError("Use one and/or two local workers.")
    if type(seconds) is not int or not 5 <= seconds <= 20:
        raise ValueError("Use five to twenty seconds, plus at most two seconds per fixture.")
    rates = tuple(rate(x) for x in rates)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    for name in ("inputs", "prepared", "browser", "candidates"):
        (directory / name).mkdir()
    experiment = Experiment(directory)
    fixture_started, fixture_cpu = time.perf_counter(), time.process_time()
    fixtures = build_fixtures(experiment, max(counts), seconds)
    fixture_measurements = list(experiment.runs)
    fixture_metrics = {
        "wall_seconds": time.perf_counter() - fixture_started,
        "python_process_cpu_seconds": time.process_time() - fixture_cpu,
        "measurements": fixture_measurements,
    }
    batches, references = [], {}
    for index, count in enumerate(counts):
        # Alternate order; this still is one host run, with uncontrolled warm caches/load.
        for parallel in workers if index % 2 == 0 else reversed(workers):
            result = prepare_batch(experiment, fixtures[:count], parallel, f"n{count}-w{parallel}")
            for item in result["files"]:
                if item["id"] in references and not exact_pcm(
                    item["pcm"], references[item["id"]]["pcm"]
                ):
                    raise ValueError("Local worker concurrency changed the prepared PCM.")
                references[item["id"]] = item
            batches.append(result)
    playback = browser_batch(experiment, fixtures)
    candidate_results = candidates(experiment, fixtures, references)
    payloads = []
    for count in counts:
        selected = fixtures[:count]
        for profile in ("all", "m4a", "wav", "video"):
            items = [item for item in selected if profile == "all" or item["profile"] == profile]
            original_bytes = sum(item["bytes"] for item in items)
            wav_bytes = sum(references[item["id"]]["bytes"] for item in items)
            payloads.append(
                {
                    "batch_files": count,
                    "profile": profile,
                    "file_count": len(items),
                    "original_bytes": original_bytes,
                    "normalized_wav_bytes": wav_bytes,
                    "transfer_counterfactuals": transfer_estimates(
                        original_bytes, wav_bytes, rates
                    ),
                }
            )
    report = {
        "schema": 1,
        "created": datetime.now(UTC).isoformat(),
        "scope": (
            "Synthetic local transformation measurements only; no network, UCloud or inference."
        ),
        "excluded": [
            "production source inspection/hash/proof overhead",
            "models/inference",
            "network latency/retries",
            "remote decoder CPU/wall/RAM",
            "GPU idle/cost",
            "library/job coordination and cleanup",
            "speech/timing accuracy",
        ],
        "assessed_input_contract": {
            "policy": POLICY,
            "recipe": RECIPE,
            "timebase": TIMEBASE,
            "production_receipts": False,
        },
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
            "imageio_ffmpeg": version("imageio-ffmpeg"),
            "ffmpeg": imageio_ffmpeg.get_ffmpeg_version(),
            "ffmpeg_binary": file_info(Path(experiment.executable)),
            "script": file_info(Path(__file__)),
            "baseline_script": file_info(BASELINE),
        },
        "configuration": {
            "counts": counts,
            "workers": workers,
            "base_seconds": seconds,
            "assumed_effective_mbit_per_second": rates,
            "fixture_seed": 870027,
            "repetitions_per_configuration": 1,
        },
        "fixtures": fixtures,
        "fixture_creation": fixture_metrics,
        "inference_preparation_batches": batches,
        "browser_preparation_separate": playback,
        "payloads": payloads,
        "optional_candidates": candidate_results,
        "measurements": experiment.runs,
    }
    (directory / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--files", type=int, nargs="+", choices=(4, 8), default=[4, 8])
    parser.add_argument("--workers", type=int, nargs="+", choices=(1, 2), default=[1, 2])
    parser.add_argument("--seconds", type=int, choices=range(5, 21), default=15)
    parser.add_argument("--rates", type=rate, nargs="+", default=[5, 20, 100])
    args = parser.parse_args()
    report = run_assessment(
        args.output, counts=args.files, workers=args.workers, seconds=args.seconds, rates=args.rates
    )
    print(
        json.dumps(
            {
                "report": str(args.output / "report.json"),
                "synthetic_recording_seconds": sum(
                    x["generated_seconds"] for x in report["fixtures"]
                ),
                "batches": [
                    {
                        key: batch[key]
                        for key in (
                            "file_count",
                            "local_workers",
                            "batch_wall_seconds",
                            "ffmpeg_cpu_seconds_sum",
                        )
                    }
                    for batch in report["inference_preparation_batches"]
                ],
                "payloads": [
                    {
                        key: row[key]
                        for key in (
                            "batch_files",
                            "profile",
                            "original_bytes",
                            "normalized_wav_bytes",
                        )
                    }
                    for row in report["payloads"]
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
