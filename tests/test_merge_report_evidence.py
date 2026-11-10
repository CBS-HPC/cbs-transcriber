"""Merge research reports traverse verified parents without leaking their private metadata."""

import copy
import io
import json
from zipfile import ZipFile

import pytest
from report_support import selected_view_report

from cbs_transcriber.history import merge_metadata
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


def seeded_merge(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("PRIVATE_PROJECT")
    recording = store.create_recording(project["id"], "PRIVATE_FILE.wav")
    rid = recording["id"]
    base = store.add_result(
        rid,
        normalize_segments([{"text": "PRIVATE_WORDS", "start": 0, "end": 2}]),
        {},
        "Original",
        {"kind": "provider", "model": "whisper-1"},
    )
    frozen = store.freeze_result(rid, base["id"], store.recording(rid)["revision"])
    job = store.add_job(rid, "enhance", {"source_snapshot": frozen})
    derived = copy.deepcopy(base["segments"])
    derived[0]["end"] = 3
    incoming = store.add_result(
        rid,
        derived,
        {},
        "PRIVATE_LABEL",
        {"kind": "enhancement", "source_result_id": base["id"]},
        job_id=job["id"],
    )
    creation = store.result_versions(rid, incoming["id"])[0]
    context = {
        "format": "cbs-enhancement-merge-v1",
        "base": {
            "result_id": base["id"],
            "version_id": frozen["source_version_id"],
            "payload_sha256": frozen["source_payload_sha256"],
        },
        "incoming": {
            "result_id": incoming["id"],
            "version_id": creation["id"],
            "payload_sha256": creation["payload_sha256"],
        },
        "creation": {
            "result_id": incoming["id"],
            "version_id": creation["id"],
            "payload_sha256": creation["payload_sha256"],
        },
        "summary": {
            "updated_passages": 1,
            "timing_updates": 1,
            "speaker_updates": 0,
            "conflicts": 0,
            "skipped": 0,
            "unchanged": 0,
        },
    }
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        encoded = json.dumps(derived, ensure_ascii=False)
        db.execute("UPDATE transcript_results SET segments=? WHERE id=?", (encoded, base["id"]))
        db.execute(
            "UPDATE recordings SET segments=?,revision=revision+1 WHERE id=?", (encoded, rid)
        )
        row = db.execute("SELECT * FROM transcript_results WHERE id=?", (base["id"],)).fetchone()
        merged = store._append_version(
            db, row, "result_merge", {"merge": context}, "local_user", "PRIVATE_REASON"
        )
    return store, project, recording, base, incoming, context, merged


def test_merge_report_captures_verified_incoming_and_aliases_parents(tmp_path):
    store, project, _, base, incoming, context, _ = seeded_merge(tmp_path)
    data = selected_view_report(store, project["id"])
    with ZipFile(io.BytesIO(data)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        methods = archive.read("methods.md").decode()
    versions = manifest["versions"]
    merged = next(v for v in versions if v["action"] == "result_merge")
    by_id = {v["id"]: v for v in versions}
    assert merged["merge"]["summary"] == context["summary"]
    assert (
        by_id[merged["merge"]["incoming_version"]]["payload_sha256"]
        == context["incoming"]["payload_sha256"]
    )
    assert (
        by_id[merged["merge"]["base_version"]]["payload_sha256"]
        == context["base"]["payload_sha256"]
    )
    assert merged["merge"]["creation_version"] == merged["merge"]["incoming_version"]
    assert "merge" in methods.lower()
    serialized = json.dumps(manifest) + methods
    for private in ("PRIVATE_WORDS", "PRIVATE_LABEL", "PRIVATE_REASON", base["id"], incoming["id"]):
        assert private not in serialized


@pytest.mark.parametrize("field", ["ownership", "hash", "extra"])
def test_merge_reports_refuse_invalid_parent_evidence(tmp_path, field):
    store, project, _, _, _, context, merged = seeded_merge(tmp_path)
    if field == "ownership":
        context["incoming"]["result_id"] = "wrong-result"
    elif field == "hash":
        context["incoming"]["payload_sha256"] = "0" * 64
    else:
        context["private_path"] = "PRIVATE_PATH"
    store.execute(
        "UPDATE result_versions SET context=? WHERE id=?",
        (json.dumps({"merge": context}), merged["id"]),
    )
    with pytest.raises(ValueError, match="merge"):
        selected_view_report(store, project["id"])


def test_merge_metadata_rejects_boolean_counts_and_unknown_fields(tmp_path):
    *_, context, _ = seeded_merge(tmp_path)
    assert merge_metadata(context) == context
    context["summary"]["conflicts"] = True
    with pytest.raises(ValueError, match="merge"):
        merge_metadata(context)
