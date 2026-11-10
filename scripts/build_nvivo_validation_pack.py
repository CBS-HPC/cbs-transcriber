"""Build synthetic exports and a NOT_RUN desktop acceptance pack, entirely offline.

Run from this checkout with PYTHONPATH=src. No server, inference, remote API, real library,
NVivo automation or REFI-QDA writer is involved. Output must be a new directory.
"""

import argparse
import copy
import csv
import hashlib
import io
import json
import math
import os
import struct
import sys
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

# Store's deferred modules can import provider presets. Never consult a private catalog.
if "CBS_TRANSCRIBER_MODELS" in os.environ:
    raise RuntimeError("Remove CBS_TRANSCRIBER_MODELS before building this synthetic pack.")

from cbs_transcriber import annotations, result_roles
from cbs_transcriber.exports import export_bytes, project_archive
from cbs_transcriber.history import decode_payload
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/nvivo_validation/fixture.json"
PROTOCOL = ROOT / "docs/nvivo-refi-validation-pack.md"
BASELINE = "99cb57f0635658c9799f1b4ceb7ae0566f26987f"
FORMATS = {
    "nvivo-windows": "windows.txt",
    "nvivo-mac": "mac.txt",
    "text": "readable.txt",
    "csv": "transcript.csv",
    "json": "transcript.json",
    "srt": "transcript.srt",
    "vtt": "transcript.vtt",
}
PRIVATE_CANARY = "SYNTHETIC-PRIVATE-REASON-NOT-FOR-EXPORT"
CASES = (
    "WIN-01",
    "WIN-02",
    "WIN-03",
    "WIN-04",
    "MAC-01",
    "MAC-02",
    "MAC-03",
    "MAC-04",
    "BOTH-01",
    "BOTH-02",
    "BOTH-03",
    "REFI-01",
    "REFI-02",
    "REFI-03",
    "REFI-04",
)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(directory, name, data):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as target:
        target.write(data)


def expected_mac(rows):
    paragraphs = []
    for span, speaker, text in rows[1:]:
        clocks = span.split("-")
        markers = ["#" + clock.replace(".", "-") + "#" for clock in clocks]
        ending = " " + markers[1] if len(markers) == 2 else ""
        paragraphs.append(f"{markers[0]}\n{speaker}: {text}{ending}")
    return "\n\n".join(paragraphs) + "\n"


def check_exports(case, rendered):
    """Check against the hand-authored oracle, not a second export implementation."""
    if "expected_windows" in case:
        windows = rendered["nvivo-windows"]
        assert windows.startswith(b"\xef\xbb\xbf")
        assert (
            list(csv.reader(io.StringIO(windows.decode("utf-8-sig")), delimiter="\t"))
            == (case["expected_windows"])
        )
        mac = rendered["nvivo-mac"]
        assert mac.startswith(b"\xef\xbb\xbf")
        assert mac.decode("utf-8-sig") == expected_mac(case["expected_windows"])
    current = json.loads(rendered["json"])
    assert [s["text"] for s in current["segments"]] == [s["text"] for s in case["segments"]]
    assert [(s["start"], s["end"]) for s in current["segments"]] == [
        (s.get("start"), s.get("end")) for s in case["segments"]
    ]
    rows = list(csv.reader(io.StringIO(rendered["csv"].decode("utf-8-sig"), newline="")))
    assert [r[3] for r in rows[1:]] == [
        ("'" if s["text"].lstrip().startswith(("=", "+", "-", "@")) else "") + s["text"]
        for s in case["segments"]
    ]


def mark(store, rid, vid):
    current = store.recording(rid)
    rev, role_rev = current["revision"], current["result_roles"]["revision"]
    plan = result_roles.preview(store, rid, vid, rev, role_rev)
    result_roles.apply(store, rid, vid, rev, role_rev, plan["fingerprint"])


def annotation(case, segments, name, topic, polarity):
    entry = case["coding"][name]
    passage = segments[entry["passage"]]
    quote = entry["quote"]
    start, end = entry["start_char"], entry["end_char"]
    assert passage["text"][start:end] == quote
    return {
        "topic_id": topic,
        "polarity": polarity,
        "attribution": "self",
        "evidence": [
            {
                "passage_id": passage["id"],
                "start_char": start,
                "end_char": end,
                "exact_text": quote,
            }
        ],
    }


def coding_export(store, rid, result, version, book, case):
    segments = result["segments"]
    positive = annotation(case, segments, "positive", "flexibility", "positive")
    negative = annotation(case, segments, "negative", "speed", "negative")
    pending = annotation(case, segments, "pending", "literal_text", "neutral")
    bundle = {
        "format": annotations.IMPORT_FORMAT,
        "result_id": result["id"],
        "source_version_id": version["id"],
        "source_payload_sha256": version["payload_sha256"],
        "codebook_version_id": book["id"],
        "codebook_payload_sha256": book["payload_sha256"],
        "passage_ids": [s["id"] for s in segments],
        "suggestions": [
            {"id": str(i), "annotation": a}
            for i, a in enumerate((positive, positive, negative, pending))
        ],
        "producer": {"model": "synthetic-not-executed", "revision": PRIVATE_CANARY},
    }
    run = store.import_annotation_run(rid, bundle, request_id="synthetic-import")
    items = sorted(store.annotations(rid, run["id"]), key=lambda item: item["sequence"])
    for item, action, payload in zip(
        items[:3], ("accept", "change", "reject"), (None, negative, None), strict=True
    ):
        store.review_annotation(
            rid,
            run["id"],
            item["id"],
            item["revision"],
            action,
            payload=payload,
            request_id=f"review-{item['sequence']}",
            reason=PRIVATE_CANARY,
        )
    store.add_manual_annotation(
        rid,
        run["id"],
        annotation(case, segments, "literal", "literal_text", "neutral"),
        request_id="manual-combining",
        reason=PRIVATE_CANARY,
    )
    exported = store.export_annotation_run(rid, run["id"])
    assert [i["review_state"] for i in exported["items"]] == [
        "accepted",
        "accepted",
        "rejected",
        "unreviewed",
        "accepted",
    ]
    assert "annotation" not in exported["items"][3]
    assert exported["items"][1]["annotation"]["evidence"] == negative["evidence"]
    assert PRIVATE_CANARY not in encoded(exported).decode()
    for item in exported["items"]:
        for span in item.get("annotation", {}).get("evidence", []):
            source = next(s for s in segments if s["id"] == span["passage_id"])
            assert source["text"][span["start_char"] : span["end_char"]] == span["exact_text"]
    return exported


def tone_clock(path, fixture, case):
    """Small cue clicks at exact scripted boundaries; deliberately contains no speech."""
    rate = fixture["sample_rate"]
    points = sorted({s[t] for s in case["segments"] for t in ("start", "end")})
    starts = {round(point * rate) for point in points}
    frames = bytearray(rate * fixture["duration_seconds"] * 2)
    for start in starts:
        for offset in range(rate // 50):
            frame = start + offset
            if frame * 2 + 2 <= len(frames):
                sample = round(6000 * math.sin(2 * math.pi * 1000 * offset / rate))
                struct.pack_into("<h", frames, frame * 2, sample)
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(frames)
    return [{"seconds": point, "frame": round(point * rate)} for point in points]


def build(output):
    if not __debug__:
        raise RuntimeError("Run without -O: the synthetic structure assertions must be enabled.")
    if Path(annotations.__file__).resolve().parent != ROOT / "src/cbs_transcriber":
        raise RuntimeError("Use PYTHONPATH=src from this pack's source checkout.")
    if "CBS_TRANSCRIBER_MODELS" in os.environ:
        raise RuntimeError("Remove CBS_TRANSCRIBER_MODELS before building this synthetic pack.")
    fixture = json.loads(FIXTURE.read_bytes())
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    write(output, "fixture.json", FIXTURE.read_bytes())
    write(output, "README.md", PROTOCOL.read_bytes())
    checks, identities, refusals = [], [], []
    with TemporaryDirectory(prefix="cbs-nvivo-synthetic-") as temporary:
        store = Store(Path(temporary) / "library")
        project = store.create_project("Synthetic NVivo DA/EN validation")
        book = store.save_codebook(
            project["id"],
            {
                "approved": True,
                "topics": [
                    {
                        "id": "flexibility",
                        "name": "Flexibility",
                        "definition": "Flexibility evaluations.",
                    },
                    {"id": "speed", "name": "Speed", "definition": "Performance evaluations."},
                    {
                        "id": "literal_text",
                        "name": "Literal text",
                        "definition": "Unicode control.",
                    },
                ],
            },
            0,
            reason=PRIVATE_CANARY,
        )
        for case in fixture["cases"] + fixture["controls"]:
            case_dir = output / case["id"]
            case_dir.mkdir()
            segments = normalize_segments(copy.deepcopy(case["segments"]))
            before = copy.deepcopy(segments)
            result = store.add_result(
                store.create_recording(project["id"], case["id"] + ".wav")["id"],
                segments,
                case["speakers"],
                "Synthetic frozen candidate",
                {"kind": "synthetic-validation-fixture", "setup_canary": PRIVATE_CANARY},
            )
            rid = result["recording_id"]
            version = store.result_versions(rid, result["id"])[0]
            version = store.one("SELECT * FROM result_versions WHERE id=?", (version["id"],))
            mark(store, rid, version["id"])
            payload = decode_payload(version["payload"], version["payload_sha256"])
            write(case_dir, "source-version.json", version["payload"].encode())
            identity = {
                "case": case["id"],
                "recording_id": rid,
                "result_id": result["id"],
                "version_id": version["id"],
                "payload_sha256": version["payload_sha256"],
            }
            identities.append(identity)
            rendered = {}
            for format_name, filename in FORMATS.items():
                try:
                    data = export_bytes(
                        payload["segments"], payload["speakers"], "speaker", format_name
                    )
                except ValueError as exc:
                    if format_name not in case.get("refuse_formats", []):
                        raise
                    refusals.append({"case": case["id"], "format": format_name, "reason": str(exc)})
                    continue
                assert format_name not in case.get("refuse_formats", [])
                assert PRIVATE_CANARY.encode() not in data
                write(case_dir, filename, data)
                rendered[format_name] = data
            check_exports(case, rendered)
            assert segments == before
            if "coding" in case:
                exported = coding_export(store, rid, result, version, book, case)
                write(case_dir, "reviewed-coding.json", annotations.export_bytes(exported))
                # Later wording and the active review selection must not move Working/coding.
                later = store.add_result(
                    rid,
                    normalize_segments(
                        [{"text": "ACTIVE DECOY MUST NOT EXPORT", "start": 0, "end": 1}]
                    ),
                    {},
                    "Synthetic alternative",
                    {},
                    activate=True,
                    revision=store.recording(rid)["revision"],
                )
                assert store.recording(rid)["active_transcript_id"] == later["id"]
                assert store.export_annotation_run(rid, exported["run"]["id"]) == exported
                cues = tone_clock(case_dir / "clock-no-speech.wav", fixture, case)
                write(case_dir, "clock-cues.json", encoded(cues))
            checks.append(
                {"case": case["id"], "status": "PASS", "scope": "CBS export structure only"}
            )
        store.create_recording(project["id"], "missing-working.wav")
        for format_name in ("nvivo-windows", "nvivo-mac", "json"):
            with project_archive(
                store, project["id"], format_name, "speaker", "working"
            ) as archive:
                data = archive.read()
            with ZipFile(io.BytesIO(data)) as archive:
                report = json.loads(archive.read("manifest.json"))
                assert report["exported"] == (4 if format_name == "json" else 3)
                assert report["skipped"] == (1 if format_name == "json" else 2)
                for entry in report["entries"]:
                    if entry["status"] == "exported":
                        assert b"ACTIVE DECOY" not in archive.read(entry["path"])
                        case = next(
                            i for i in identities if i["recording_id"] == entry["recording_id"]
                        )
                        assert entry["version_id"] == case["version_id"]
                        assert entry["payload_sha256"] == case["payload_sha256"]
            write(output, f"working-project-{format_name}.zip", data)
            checks.append(
                {
                    "case": f"working-{format_name}",
                    "status": "PASS",
                    "scope": "Exact pinned versions; incompatible/missing items reported",
                }
            )
    write(output, "identities.json", encoded(identities))
    write(output, "refusals.json", encoded(refusals))
    write(output, "automated-checks.json", encoded(checks))
    results = io.StringIO(newline="")
    writer = csv.writer(results)
    writer.writerow(
        [
            "case_id",
            "platform",
            "nvivo_version_build",
            "os_version",
            "status",
            "observed",
            "evidence_path",
            "issue_reference",
        ]
    )
    writer.writerows([case, "", "", "", "NOT_RUN", "", "", ""] for case in CASES)
    write(output, "manual-results.csv", results.getvalue().encode("utf-8-sig"))
    sources = [
        "src/cbs_transcriber/exports.py",
        "src/cbs_transcriber/transcripts.py",
        "src/cbs_transcriber/store.py",
        "src/cbs_transcriber/annotations.py",
        "src/cbs_transcriber/result_roles.py",
        "src/cbs_transcriber/history.py",
        "scripts/build_nvivo_validation_pack.py",
        str(FIXTURE.relative_to(ROOT)),
        str(PROTOCOL.relative_to(ROOT)),
    ]
    inventory = [
        {
            "path": p.relative_to(output).as_posix(),
            "bytes": p.stat().st_size,
            "sha256": sha(p.read_bytes()),
        }
        for p in sorted(output.rglob("*"))
        if p.is_file()
    ]
    manifest = {
        "format": "cbs-nvivo-validation-pack-v1",
        "synthetic_only": True,
        "baseline_commit": BASELINE,
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "source_sha256": {name: sha((ROOT / name).read_bytes()) for name in sources},
        "desktop_acceptance": "NOT_RUN",
        "refi_qda_app_support": "NOT_IMPLEMENTED",
        "audio": fixture["audio_kind"],
        "files": inventory,
        "limits": "Structure checks do not prove NVivo import, REFI mappings, speech accuracy, "
        "backup/restore readiness or real processing evidence. IDs are fresh per build.",
    }
    write(output, "pack-manifest.json", encoded(manifest))  # Completion marker, always last.
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New directory; existing paths are refused")
    args = parser.parse_args()
    manifest = build(args.output)
    print(f"Synthetic pack complete: {len(manifest['files'])} files. NVivo acceptance: NOT_RUN.")


if __name__ == "__main__":
    main()
