"""Synthetic role histories retain complete evidence within existing report budgets."""

import json
from io import BytesIO
from zipfile import ZipFile

import pytest

from cbs_transcriber import research_reports, result_roles
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


def mark(store, recording_id, version_id):
    row = store.recording(recording_id)
    revision, role_revision = row["revision"], row["result_roles"]["revision"]
    plan = result_roles.preview(store, recording_id, version_id, revision, role_revision)
    result_roles.apply(
        store, recording_id, version_id, revision, role_revision, plan["fingerprint"]
    )


def history(store):
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "tiny.wav")
    versions = []
    for word in ("first private transcript", "second private transcript"):
        result = store.add_result(
            recording["id"], normalize_segments([{"text": word}]), {}, "X", {}
        )
        saved = store.result_versions(recording["id"], result["id"])[0]
        versions.append(saved)
        mark(store, recording["id"], saved["id"])
    return project, recording, versions


def test_report_reuses_exact_selected_version_without_charging_it_twice(tmp_path, monkeypatch):
    store = Store(tmp_path)
    project, recording, versions = history(store)
    # Repeated references retain all events but do not charge a version twice.
    mark(store, recording["id"], None)
    mark(store, recording["id"], versions[-1]["id"])
    sizes = [
        store.one(
            "SELECT length(CAST(payload AS BLOB)) AS n FROM result_versions WHERE id=?", (v["id"],)
        )["n"]
        for v in versions
    ]
    limit = max(sizes)
    assert sum(sizes) > limit
    monkeypatch.setattr(research_reports, "MAX_PAYLOAD_BYTES", limit)
    output = research_reports.project_report(store, project["id"])
    with ZipFile(BytesIO(output)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    events = manifest["role_history"][0]["events"]
    assert len(events) == 4
    assert events[0]["next"]["payload_sha256"] == versions[0]["payload_sha256"]
    assert events[1]["next"]["payload_sha256"] == versions[1]["payload_sha256"]
    assert events[2]["previous"] == events[1]["next"]
    assert events[3]["next"] == events[1]["next"]
    assert recording["id"] not in json.dumps(manifest["role_history"])


def test_reused_reference_refuses_wrong_recording_and_hash(tmp_path):
    store = Store(tmp_path)
    _, recording, versions = history(store)
    selected = versions[-1]
    verified = {selected["id"]: {**selected, "recording_id": "foreign"}}
    with store.connection() as db, pytest.raises(ValueError, match="belong"):
        result_roles.public_history(db, store, recording["id"], "r", verified_versions=verified)
    verified[selected["id"]]["recording_id"] = recording["id"]
    verified[selected["id"]]["payload_sha256"] = "0" * 64
    with store.connection() as db, pytest.raises(ValueError, match="hash"):
        result_roles.public_history(db, store, recording["id"], "r", verified_versions=verified)


def test_unselected_role_payload_still_obeys_budget(tmp_path):
    store = Store(tmp_path)
    _, recording, versions = history(store)
    selected = versions[-1]
    verified = {selected["id"]: {**selected, "recording_id": recording["id"]}}
    size = store.one(
        "SELECT length(CAST(payload AS BLOB)) AS n FROM result_versions WHERE id=?",
        (versions[0]["id"],),
    )["n"]
    with store.connection() as db, pytest.raises(ValueError, match="content limit"):
        result_roles.public_history(
            db,
            store,
            recording["id"],
            "r",
            payload_budget={"remaining": size - 1},
            verified_versions=verified,
        )


def test_role_event_limit_still_refuses_incomplete_history(tmp_path, monkeypatch):
    store = Store(tmp_path)
    _, recording, _ = history(store)
    monkeypatch.setattr(result_roles, "MAX_ROLE_EVENTS", 1)
    with store.connection() as db, pytest.raises(ValueError, match="event limit"):
        result_roles.public_history(db, store, recording["id"], "r")
