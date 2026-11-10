"""Compare saved methods against reviewed Danish/English clips, entirely offline."""

import argparse
import json
import sys
from pathlib import Path

from cbs_transcriber.evaluation import load_suite, read_predictions
from cbs_transcriber.review_evaluation import score_reviewed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite", type=Path, required=True, help="Folder with manifest.json and hashed clips"
    )
    parser.add_argument("--prediction", action="append", required=True, metavar="METHOD=JSON")
    parser.add_argument(
        "--output", type=Path, required=True, help="New report file; never overwritten"
    )
    args = parser.parse_args(argv)
    try:
        if (args.suite / "manifest.json").stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Reference manifest exceeds the 32 MB limit.")
        manifest = load_suite(args.suite)
        methods = {}
        for item in args.prediction:
            label, separator, filename = item.partition("=")
            if not separator or not 0 < len(label.strip()) <= 160 or label in methods:
                raise ValueError("Use unique METHOD=JSON prediction arguments.")
            path = Path(filename)
            if path.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("Prediction file exceeds the 32 MB limit.")
            methods[label] = score_reviewed(manifest, read_predictions(path))
        payload = (
            json.dumps(
                {"schema": "cbs.transcriber.method_comparison.v1", "methods": methods},
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        detail = (
            str(exc)
            if isinstance(exc, ValueError)
            else "Check input files and choose a new output path."
        )
        print("Evaluation failed. " + detail)
        return 1
    for label, report in methods.items():
        print(f"{label}: WER {report['text']['wer']:.2%}; CER {report['text']['cer']:.2%}")
    print(
        "Saved reviewed metrics. Timing/speaker and measurement coverage "
        "are recorded in the report."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
