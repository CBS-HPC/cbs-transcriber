"""Working report selection pins exact versions without inventing researcher decisions."""

import io
import json
from zipfile import ZipFile

from cbs_transcriber import research_reports, result_roles
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import edit_segment, normalize_segments


def unpack(data):
    with ZipFile(io.BytesIO(data)) as archive:
        return json.loads(archive.read("manifest.json"))


def mark(store, recording_id, version_id):
    current = store.recording(recording_id)
    with store.connection() as db:
        state = result_roles.read(db, store, recording_id)
    plan = result_roles.preview(
        store, recording_id, version_id, current["revision"], state["revision"]
    )
    result_roles.apply(
        store, recording_id, version_id, current["revision"], state["revision"], plan["fingerprint"]
    )


def mark_active_latest(store, project_id):
    """Deliberately designate each active result's latest version, as a researcher would."""
    for row in store.all(
        "SELECT id,active_transcript_id FROM recordings WHERE project_id=? "
        "AND active_transcript_id IS NOT NULL",
        (project_id,),
    ):
        versions = store.result_versions(row["id"], row["active_transcript_id"])
        latest = max(versions, key=lambda version: version["sequence"])
        mark(store, row["id"], latest["id"])


def test_default_report_lists_unassigned_recordings_without_guessing(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Private project")
    recording = store.create_recording(project["id"], "Private recording.wav")
    store.add_result(
        recording["id"], normalize_segments([{"text": "Private words"}]), {}, "First", {}
    )
    manifest = unpack(research_reports.project_report(store, project["id"]))
    assert manifest["versions"] == []
    assert manifest["selection"]["default_scope"] == "working_versions"
    assert manifest["skipped"] == [
        {"recording": "recording-001", "reason": "working_version_unassigned"}
    ]
    assert manifest["role_history"][0]["events"] == []
    assert "Private" not in json.dumps(manifest)


def test_working_report_pins_historical_payload_and_preserves_clear_event(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Research")
    recording = store.create_recording(project["id"], "source.wav")
    result = store.add_result(
        recording["id"], normalize_segments([{"text": "First words"}]), {}, "First", {}
    )
    initial = store.result_versions(recording["id"], result["id"])[0]
    mark(store, recording["id"], initial["id"])
    current = store.recording(recording["id"])
    store.save_transcript(
        recording["id"],
        [edit_segment(current["segments"][0], "free", "Later wording")],
        {},
        current["revision"],
    )
    # ADR 0133: editing the Working head advances Working. Pin the historical version again.
    advanced = store.recording(recording["id"])["result_roles"]["working"]
    assert advanced["version_id"] != initial["id"]
    mark(store, recording["id"], initial["id"])
    alternative = store.add_result(
        recording["id"], normalize_segments([{"text": "Alternative words"}]), {}, "Alternative", {}
    )
    current = store.recording(recording["id"])
    store.activate_result(recording["id"], alternative["id"], current["revision"])
    manifest = unpack(research_reports.project_report(store, project["id"]))
    selected = [version for version in manifest["versions"] if version["selected"]]
    assert len(selected) == 1 and selected[0]["payload_sha256"] == initial["payload_sha256"]
    assert selected[0]["working_designation"]["selected_version_is_working"] is True
    assert manifest["role_history"][0]["events"][0]["action"] == "working_marked"
    for private in (recording["id"], result["id"], alternative["id"], initial["id"], "First words"):
        assert private not in json.dumps(manifest)
    mark(store, recording["id"], None)
    store.add_result(
        recording["id"], normalize_segments([{"text": "Finished later"}]), {}, "New output", {}
    )
    manifest = unpack(research_reports.project_report(store, project["id"]))
    assert not manifest["versions"] and len(manifest["skipped"]) == 1
    assert [
        (event["action"], event["actor_kind"]) for event in manifest["role_history"][0]["events"]
    ] == [
        ("working_marked", "local_user"),
        ("working_auto_edit", "system"),
        ("working_marked", "local_user"),
        ("working_cleared", "local_user"),
    ]
