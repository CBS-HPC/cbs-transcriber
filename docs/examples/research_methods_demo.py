"""Generate genuine research reports from a disposable, entirely synthetic workflow.

No existing library/media input, server, worker, provider call or inference is used.
The enhancement is inserted at the frozen-result seam and explicitly has no run capture.
"""

import argparse
import copy
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
WORKING_TEXT = "Hej København 🧪.\r\nVi bevarer cafe\u0301 og café."
SECOND_TEXT = "The reviewed English wording remains available."
MODEL = "synthetic-model-not-executed"
PRIVATE = (
    "PRIVATE-PROJECT",
    "PRIVATE-RECORDING",
    "PRIVATE-NOTES",
    "PRIVATE-REASON",
    "PRIVATE-ORIGINAL",
)
DECOYS = ("UNSELECTED-SOURCE-REWRITE", "UNSELECTED-ACTIVE-REWRITE")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def unpack(data):
    with ZipFile(io.BytesIO(data)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    return json.loads(files["manifest.json"]), files


def build(output):
    if not __debug__:
        raise ValueError("Run without Python optimization; example checks are required.")
    if "CBS_TRANSCRIBER_MODELS" in os.environ:
        raise ValueError("Remove CBS_TRANSCRIBER_MODELS before generating this synthetic example.")
    from cbs_transcriber import research_reports, result_roles
    from cbs_transcriber import store as store_module
    from cbs_transcriber.transcripts import edit_segment

    for module in (store_module, research_reports, result_roles):
        expected = ROOT / "src" / (module.__name__.replace(".", "/") + ".py")
        if Path(module.__file__).resolve() != expected.resolve():
            raise ValueError("Use PYTHONPATH=src from this example's checkout.")
    output = Path(output).absolute()
    if not output.parent.is_dir():
        raise ValueError("Choose a new output directory below an existing parent.")
    output.mkdir()  # Exclusive: an existing file, directory or link is never replaced.
    files, steps = {}, []

    with tempfile.TemporaryDirectory(prefix="cbs-m-") as temporary:
        store = store_module.Store(Path(temporary) / "l")
        project = store.create_project(PRIVATE[0])
        recording = store.create_recording(project["id"], PRIVATE[1] + ".wav")
        rid = recording["id"]
        store.create_recording(project["id"], "unassigned.wav")
        store.save_notes("projects", project["id"], PRIVATE[2], 0)
        raw = encoded(
            {
                "segments": [
                    {"text": PRIVATE[4], "start": 0, "end": 2, "speaker": "A"},
                    {"text": SECOND_TEXT, "start": 2, "end": 4, "speaker": "B"},
                ]
            }
        )
        imported = store.import_transcript(rid, raw, "synthetic.json", 0)

        def observe(label, result_id):
            version = store.result_versions(rid, result_id)[0]
            steps.append(
                {
                    "step": label,
                    "sequence": version["sequence"],
                    "action": version["action"],
                    "payload_sha256": version["payload_sha256"],
                }
            )
            return version

        observe("imported", imported["id"])
        changed = copy.deepcopy(imported["segments"])
        changed[0] = edit_segment(changed[0], "free", WORKING_TEXT)
        store.save_transcript(
            rid,
            changed,
            {},
            1,
            action="free_rewrite",
            context={"segment_id": changed[0]["id"]},
            reason=PRIVATE[3],
        )
        observe("reviewed wording", imported["id"])
        names = {"A": "Synthetic voice A", "B": "Synthetic voice B"}
        store.save_transcript(rid, changed, names, 2, action="speaker_rename")
        source_version = observe("named voices and frozen source", imported["id"])
        frozen = store.freeze_result(rid, imported["id"], 3)
        job = store.add_job(rid, "enhance", {"source_snapshot": frozen})
        enhanced = store.add_result(
            rid,
            copy.deepcopy(frozen["segments"]),
            frozen["speakers"],
            "Synthetic enhancement",
            {"kind": "enhancement", "source_result_id": imported["id"], "model": MODEL},
            job_id=job["id"],
        )
        working = observe("mock enhancement; no inference", enhanced["id"])
        assert working["source_version_id"] == source_version["id"]
        assert working["context"]["source_payload_sha256"] == source_version["payload_sha256"]

        # Mark an exact version, then change both its source and its current review head.
        current = store.recording(rid)
        preview = result_roles.preview(
            store, rid, working["id"], current["revision"], current["result_roles"]["revision"]
        )
        result_roles.apply(
            store,
            rid,
            working["id"],
            current["revision"],
            current["result_roles"]["revision"],
            preview["fingerprint"],
        )
        later_source = copy.deepcopy(changed)
        later_source[0] = edit_segment(later_source[0], "free", DECOYS[0])
        store.save_transcript(rid, later_source, names, 3, action="free_rewrite")
        observe("later source edit, outside frozen ancestry", imported["id"])
        current = store.activate_result(rid, enhanced["id"], 4)
        later_active = copy.deepcopy(enhanced["segments"])
        later_active[0] = edit_segment(later_active[0], "free", DECOYS[1])
        store.save_transcript(rid, later_active, names, current["revision"], action="free_rewrite")
        active = observe("later active edit; Working advances", enhanced["id"])
        current = store.recording(rid)
        assert current["result_roles"]["working"]["version_id"] == active["id"]
        # Edits to Working now advance it (ADR 0133). Explicitly select the earlier
        # creation again to demonstrate a deliberate rollback of the export designation.
        preview = result_roles.preview(
            store, rid, working["id"], current["revision"], current["result_roles"]["revision"]
        )
        result_roles.apply(
            store,
            rid,
            working["id"],
            current["revision"],
            current["result_roles"]["revision"],
            preview["fingerprint"],
        )
        before = store.all("SELECT * FROM result_versions ORDER BY id")
        files["metadata-report.zip"] = research_reports.project_report(store, project["id"])
        files["selected-text-report.zip"] = research_reports.project_report(
            store, project["id"], include_selected_transcripts=True
        )
        metadata, metadata_files = unpack(files["metadata-report.zip"])
        report, text_files = unpack(files["selected-text-report.zip"])
        assert set(metadata_files) == {"manifest.json", "methods.md"}
        assert report["selection"]["selected_versions"] == 1
        assert report["selection"]["skipped_unassigned_recordings"] == 1
        selected = next(item for item in report["versions"] if item["selected"])
        assert selected["payload_sha256"] == working["payload_sha256"]
        assert selected["sequence"] == working["sequence"] == 1
        source = next(
            item for item in report["versions"] if item["id"] == selected["source_version"]
        )
        assert source["payload_sha256"] == source_version["payload_sha256"]
        assert all(
            step["payload_sha256"] not in {active["payload_sha256"], steps[-2]["payload_sha256"]}
            for step in report["versions"]
        )
        private_ids = (project["id"], rid, imported["id"], enhanced["id"], job["id"], working["id"])
        for content in (*metadata_files.values(), *text_files.values()):
            assert not any(word.encode() in content for word in (*PRIVATE, *DECOYS, *private_ids))
        assert WORKING_TEXT.encode() not in b"".join(metadata_files.values())
        exported = text_files[report["exports"][0]["path"]]
        assert WORKING_TEXT.encode() in exported and SECOND_TEXT.encode() in exported
        assert digest(exported) == report["exports"][0]["sha256"]
        assert "producer_artifacts" not in selected
        assert selected["processing"]["model"] == MODEL and selected["provenance_gaps"]
        assert store.all("SELECT * FROM result_versions ORDER BY id") == before
        assert store.recording(rid)["result_roles"]["working"]["version_id"] == working["id"]
        receipt = imported["source"]["artifact_evidence"]
        assert receipt["sha256"] == digest(raw) and receipt["bytes"] == len(raw)
        files["oracle.json"] = encoded(
            {
                "format": "cbs-synthetic-methods-example-v1",
                "steps": steps,
                "expected_visible_text": [WORKING_TEXT, SECOND_TEXT],
                "working_payload_sha256": working["payload_sha256"],
                "frozen_source_payload_sha256": source_version["payload_sha256"],
                "active_payload_sha256": active["payload_sha256"],
                "working_reselected_after_edit": True,
                "imported_bytes": {"sha256": receipt["sha256"], "bytes": receipt["bytes"]},
                "declared_mock_model": MODEL,
                "model_executed": False,
                "producer_capture": "not_captured",
                "historical_file_availability": "not_checked",
                "report_reads_preserved_versions": True,
            }
        )
    files["START-HERE.md"] = (
        b"# Synthetic research methods example\n\n"
        b"Read oracle.json alongside the two genuine CBS research-report ZIPs.\n"
        b"The metadata report contains no transcript text. The opt-in report contains only\n"
        b"the pinned Working wording, even though the source and review head were edited later.\n"
        b"Editing Working advanced its designation; the example then explicitly selected\n"
        b"the earlier saved version as Working again, demonstrating an intentional rollback.\n"
        b"Follow selected/source_version/predecessor aliases and hashes in manifest.json.\n\n"
        b"The enhancement was inserted from a frozen source at the Store seam. No worker or\n"
        b"model ran. Its declared synthetic model is source metadata, not execution proof.\n"
        b"Original ASR settings/environment before import are unknown. Passage bounds are\n"
        b"fixture values; no audio, decoding or alignment quality was tested.\n\n"
        b"methods.md is the actual exporter draft and needs researcher review. Hashes are\n"
        b"integrity references, not authorship, anonymity, live-file availability or journal\n"
        b"approval. The disposable database, raw import and hidden originals are not shipped.\n"
    )
    for name, content in files.items():
        with (output / name).open("xb") as stream:
            stream.write(content)
    manifest = {
        "format": "cbs-synthetic-methods-pack-v1",
        "files": {
            name: {"bytes": len(content), "sha256": digest(content)}
            for name, content in files.items()
        },
        "source_sha256": {
            path: digest((ROOT / path).read_bytes())
            for path in (
                "docs/examples/research_methods_demo.py",
                "src/cbs_transcriber/store.py",
                "src/cbs_transcriber/research_reports.py",
                "src/cbs_transcriber/result_roles.py",
                "src/cbs_transcriber/history.py",
                "src/cbs_transcriber/exports.py",
                "src/cbs_transcriber/transcripts.py",
            )
        },
        "scope": "synthetic Store/history/report example; no inference or acceptance claim",
    }
    with (output / "example-manifest.json").open("xb") as stream:
        stream.write(encoded(manifest))
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New output directory below an existing parent")
    args = parser.parse_args(argv)
    build(args.output)
    print("Synthetic research methods example created; no real processing was performed.")


if __name__ == "__main__":
    main()
