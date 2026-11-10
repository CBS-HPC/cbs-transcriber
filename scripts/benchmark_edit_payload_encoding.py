"""Measure synthetic payload encoding only; never opens a library or contacts a provider."""

import argparse
import copy
import json
import platform
import statistics
import time
from pathlib import Path

import cbs_transcriber
from cbs_transcriber.history import decode_payload, encode_payload, verify_payload
from cbs_transcriber.transcripts import normalize_segments

ROOT = Path(__file__).resolve().parents[1]


def main():
    if not Path(cbs_transcriber.__file__).resolve().is_relative_to(ROOT / "src"):
        raise RuntimeError("Set PYTHONPATH=src for this benchmark checkout")
    parser = argparse.ArgumentParser()
    parser.add_argument("--passages", type=int, default=1000)
    parser.add_argument("--words", type=int, default=20)
    parser.add_argument("--samples", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.passages <= 3000 or not 1 <= args.words <= 29:
        parser.error("pilot limits: passages 1..3000, words 1..29")
    if not 1 <= args.samples <= 100:
        parser.error("pilot limits: samples 1..100")

    vocabulary = ["Hej", "verden", "syntetisk", "tekst", "blue"]
    words = [vocabulary[i % len(vocabulary)] for i in range(args.words)]
    text = " ".join(words) + "."
    rows = [
        {
            "text": text,
            "start": index * 3,
            "end": index * 3 + 2.9,
            "speaker": f"SPEAKER_0{index % 3}",
            "words": [
                {"word": word, "start": index * 3 + n * 0.1, "end": index * 3 + n * 0.1 + 0.08}
                for n, word in enumerate(words)
            ],
        }
        for index in range(args.passages)
    ]
    segments = normalize_segments(rows)
    if not all(segment["word_timing"] for segment in segments):
        raise RuntimeError("Synthetic timing fixture is incomplete")
    speakers = {}
    projection = json.dumps(segments, ensure_ascii=False)
    payload, digest = encode_payload(segments, speakers)
    samples = {
        name: []
        for name in (
            "copy",
            "canonical",
            "projection",
            "decode_canonical",
            "history_decode",
            "warm_history_verify",
        )
    }
    verify_payload(payload, digest)

    def measure(name, fn):
        started = time.perf_counter()
        result = fn()
        samples[name].append(round((time.perf_counter() - started) * 1000, 1))
        return result

    for index in range(args.samples):
        cloned = measure("copy", lambda: copy.deepcopy(segments))
        candidate = measure("canonical", lambda: encode_payload(segments, speakers))
        saved_projection = measure("projection", lambda: json.dumps(segments, ensure_ascii=False))
        reconstructed = measure(
            "decode_canonical", lambda: encode_payload(json.loads(projection), speakers)
        )
        assert cloned == segments and saved_projection == projection
        assert candidate == reconstructed == (payload, digest)
        # Alternate the validation order; neither path grants ownership or role authority.
        validators = [
            ("history_decode", lambda: decode_payload(payload, digest)),
            ("warm_history_verify", lambda: verify_payload(payload, digest)),
        ]
        if index % 2:
            validators.reverse()
        for name, validate in validators:
            measure(name, validate)

    print(
        json.dumps(
            {
                "fixture": "synthetic timed Danish/English passages",
                "platform": platform.platform(),
                "python": platform.python_version(),
                "passages": args.passages,
                "words_per_passage": args.words,
                "samples": args.samples,
                "projection_utf8_bytes": len(projection.encode("utf-8")),
                "canonical_utf8_bytes": len(payload.encode("utf-8")),
                "milliseconds": samples,
                "median_ms": {name: statistics.median(values) for name, values in samples.items()},
                "note": "Pilot only; elapsed times include machine contention.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
