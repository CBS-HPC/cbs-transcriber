"""Offline constructor-count experiment; never loads a speech model or contacts UCloud.

Run with the existing environment: uv run --no-sync python
scripts/evaluate_runtime_model_reuse.py --files 4

The current worker executes with synthetic model adapters. The comparison is an
unintegrated, single-slot stage-cohort reference, not a production resident worker.
Measured wall time covers this Python/JSON harness only, never model or GPU time.
"""

import argparse
import hashlib
import json
import sys
import tempfile
import time
import wave
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

STAGES = ("transcription", "alignment", "diarization")


class OneSlot:
    """Experiment-only cache: exact identity, at most one retained fake model."""

    def __init__(self):
        self.identity = None
        self.value = None
        self.hits = 0
        self.evictions = 0

    def clear(self):
        if self.identity is not None:
            self.evictions += 1
        self.identity = self.value = None

    def get(self, identity, factory):
        if self.identity == identity:
            self.hits += 1
            return self.value
        self.clear()
        try:
            value = factory()
        except BaseException:
            self.clear()
            raise
        self.identity, self.value = identity, value
        return value


def _run(root, files, cohorts, *, resident, production=False, language="da"):
    from cbs_transcriber import enhancement_worker as worker

    counts, calls, progress = Counter(), Counter(), []
    receipts = []
    slot = (
        worker.RuntimeModelSlot(lambda identity, ordinal: receipts.append(identity))
        if production
        else OneSlot()
        if resident
        else None
    )
    cache = root / "fake-cache"
    cache.mkdir(parents=True)
    (cache / "tokenizer.json").write_text("{}", encoding="utf-8")
    request = {
        "passages": [],
        "options": {
            "language": language,
            "device": "cuda",
            "whisper_model": "large-v3",
            "duration": 3.0,
            "allow_downloads": False,
            "transcribe": True,
            "retranscribe": False,
            "align": True,
            "diarize": True,
            "whole_recording": True,
        },
    }
    folders = []
    for index in range(files):
        folder = root / f"task-{index:03d}"
        folder.mkdir()
        with wave.open(str(folder / "audio.wav"), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 48000)
        worker.write_json(folder / "request.json", request)
        folders.append(folder)

    def load(stage, factory, *identity):
        calls[stage] += 1

        def create():
            counts[stage] += 1
            return factory()

        return slot.get((stage, *identity), create) if slot and not production else create()

    class Model:
        model = SimpleNamespace(is_multilingual=True)

        def transcribe(self, audio, **kwargs):
            assert kwargs["language"] == (None if language == "auto" else language)
            return iter(
                [
                    SimpleNamespace(
                        text=" Hej Øresund.",
                        start=0.0,
                        end=1.5,
                        avg_logprob=-0.3,
                        no_speech_prob=0.02,
                        compression_ratio=1.1,
                    )
                ]
            ), SimpleNamespace(language="da", language_probability=1.0, duration_after_vad=1.5)

    def asr(path, **kwargs):
        assert kwargs["local_files_only"] is True
        return load("transcription", Model, path, tuple(sorted(kwargs.items())))

    def align_model(language, device, **kwargs):
        assert kwargs["model_cache_only"] is True
        return load("alignment", lambda: (object(), {}), language, device, str(kwargs))

    def align(segments, *args, **kwargs):
        assert kwargs["interpolate_method"] == "ignore"
        words = segments[0]["text"].split()
        return {
            "word_segments": [
                {"word": word, "start": index * 0.5, "end": (index + 1) * 0.5, "score": 0.9}
                for index, word in enumerate(words)
            ]
        }

    class Pipeline:
        def __call__(self, *args, **kwargs):
            return SimpleNamespace(
                itertuples=lambda: iter([SimpleNamespace(start=0.0, end=1.5, speaker="A")])
            )

    def speakers(**kwargs):
        return load("diarization", Pipeline, str(kwargs))

    def snapshot(repo, **kwargs):
        assert kwargs["local_files_only"] is True
        assert kwargs["revision"] in {pin for _, pin in worker.MODELS.values()}
        return str(cache / repo.replace("/", "--"))

    # The ASR tokenizer must exist in the synthetic snapshot; no model files exist.
    for repo, _ in worker.MODELS.values():
        path = cache / repo.replace("/", "--")
        path.mkdir(exist_ok=True)
        (path / "tokenizer.json").write_text("{}", encoding="utf-8")

    real_write = worker.write_json

    def write(path, value):
        if path.name == "progress.json":
            progress.append({"task": int(path.parent.name.split("-")[1]), **value})
        real_write(path, value)

    modules = {
        "torch": SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: True, empty_cache=lambda: None)
        ),
        "faster_whisper": SimpleNamespace(WhisperModel=asr),
        "whisperx": SimpleNamespace(align=align, load_align_model=align_model),
        "whisperx.diarize": SimpleNamespace(DiarizationPipeline=speakers),
        "huggingface_hub": SimpleNamespace(snapshot_download=snapshot),
        "nltk": SimpleNamespace(data=SimpleNamespace(load=lambda *a: None)),
    }
    started = time.perf_counter()
    with (
        patch.dict(sys.modules, modules),
        patch.object(worker, "audio_data", lambda folder: [0.0] * 48000),
        patch.object(worker, "write_json", write),
        patch.object(worker.importlib.metadata, "version", lambda name: "1.2.1"),
        patch.object(worker, "CACHE_ALIGNMENT", False),
        patch.object(worker, "ALIGNMENT_CACHE", None),
        patch.object(worker, "RUNTIME_CACHE", slot if production else None),
    ):
        offset = 0
        for length in cohorts:
            cohort = folders[offset : offset + length]
            # Finite stage cohorts are a reference experiment only. Each execute()
            # writes 'complete' for its reduced options: those progress records are
            # deliberately NOT claimed as a task/result completion protocol.
            passes = (*STAGES, "complete") if production else STAGES if resident else ("pipeline",)
            for stage in passes:
                for folder in cohort:
                    options = dict(request["options"])
                    if stage == "transcription" and not production:
                        options.update(align=False, diarize=False)
                    elif stage == "alignment" and not production:
                        options.update(diarize=False)
                    assert (
                        worker.execute(
                            {**request, "options": options},
                            folder,
                            **({"stage": stage} if production else {}),
                        )
                        is None
                    )
                    assert json.loads((folder / "request.json").read_bytes()) == request
            if slot:
                slot.clear()
            offset += length
    elapsed = time.perf_counter() - started
    digests = [
        {
            stage: hashlib.sha256((folder / f"{stage}.json").read_bytes()).hexdigest()
            for stage in STAGES
        }
        for folder in folders
    ]
    return {
        "model_loads": dict(counts),
        "constructor_requests": dict(calls),
        "cache_hits": slot.hits if slot else 0,
        "cache_evictions": slot.evictions if slot and not production else 0,
        "harness_wall_seconds": elapsed,
        "stage_hashes": digests,
        "progress": progress if not resident or production else [],
        "model_receipts": receipts,
    }


def evaluate(files=4, *, cohorts=None, production=False, language="da"):
    if type(files) is not int or not 1 <= files <= 16:
        raise ValueError("Use 1..16 synthetic files.")
    cohorts = list(cohorts) if cohorts is not None else [files]
    if not cohorts or any(type(n) is not int or n < 1 for n in cohorts) or sum(cohorts) != files:
        raise ValueError("Cohorts must be positive counts covering every synthetic file once.")
    from cbs_transcriber import enhancement_worker as worker

    with tempfile.TemporaryDirectory(prefix="cbs-offline-model-reuse-") as temporary:
        root = Path(temporary)
        baseline = _run(root / "baseline", files, cohorts, resident=False, language=language)
        candidate = _run(
            root / "reference",
            files,
            cohorts,
            resident=True,
            production=production,
            language=language,
        )
    return {
        "schema": 1,
        "scope": "offline_synthetic_adapters_no_models_no_gpu_no_provider",
        "candidate_implementation": "production_loaders" if production else "reference_adapter",
        "worker_sha256": hashlib.sha256(Path(worker.__file__).read_bytes()).hexdigest(),
        "files": files,
        "cohort_sizes": cohorts,
        "baseline": baseline,
        "single_slot_reference": candidate,
        "identical_stage_bytes": baseline["stage_hashes"] == candidate["stage_hashes"],
        "limits": [
            "Model construction/inference are synthetic; times are harness overhead only.",
            "The reference has no admission, attempt, Stop or crash-recovery integration.",
            "No GPU memory, real model latency, speech quality or production speedup is measured.",
            "Reference progress is omitted: reduced execute options report stage completion.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=4)
    parser.add_argument("--cohorts", type=int, nargs="+")
    parser.add_argument(
        "--production",
        action="store_true",
        help="Exercise the real resident loaders and stage seam",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            evaluate(args.files, cohorts=args.cohorts, production=args.production),
            indent=2,
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
