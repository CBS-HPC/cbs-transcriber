"""Exact accepted wording ancestry is verified and private metadata stays out of reports."""

import io
import json
from zipfile import ZipFile

import pytest
from report_support import selected_view_report

from cbs_transcriber.history import encode_payload, wording_adoption_metadata
from cbs_transcriber.transcripts import normalize_segments


def adopted(client):
    store = client.app.state.store
    project = store.create_project("PRIVATE_PROJECT")
    recording = store.create_recording(project["id"], "PRIVATE_FILE.wav")
    rid = recording["id"]
    passages = normalize_segments(
        [
            {"text": "PRIVATE_TARGET", "start": 0, "end": 2, "speaker": "A"},
            {"text": "Untouched passage.", "start": 2, "end": 4},
        ]
    )
    passages[0]["id"] = "PRIVATE_TARGET_ID"
    ours = store.add_result(
        rid,
        passages,
        {"A": "PRIVATE_NAME"},
        "PRIVATE_TARGET_LABEL",
        {"kind": "provider", "model": "whisper-1"},
    )
    source_passages = normalize_segments(
        [{"text": "  Ændret øæå e\u0301  \n", "start": 6, "end": 9}]
    )
    source_passages[0]["id"] = "PRIVATE_SOURCE_ID"
    source = store.add_result(
        rid,
        source_passages,
        {},
        "PRIVATE_SOURCE_LABEL",
        {"kind": "provider", "model": "other-model", "api_key": "PRIVATE_KEY"},
    )
    body = {
        "source_result_id": source["id"],
        "target_passage_id": "PRIVATE_TARGET_ID",
        "source_passage_id": "PRIVATE_SOURCE_ID",
        "revision": store.recording(rid)["revision"],
    }
    prefix = f"/api/recordings/{rid}/transcripts/{ours['id']}"
    preview = client.post(prefix + "/wording-preview", json=body)
    assert preview.status_code == 200, preview.text
    applied = client.post(
        prefix + "/wording-adopt",
        json={**body, "fingerprint": preview.json()["fingerprint"], "reason": "PRIVATE_REASON"},
    )
    assert applied.status_code == 200, applied.text
    version = store.result_version(rid, ours["id"], applied.json()["version_id"])
    return store, project, recording, ours, source, version


def manifest(store, project_id):
    with ZipFile(io.BytesIO(selected_view_report(store, project_id))) as archive:
        data = json.loads(archive.read("manifest.json"))
        methods = archive.read("methods.md").decode()
    return data, methods


def test_adoption_report_verifies_source_and_uses_only_aliases(client):
    store, project, _, ours, source, version = adopted(client)
    report, methods = manifest(store, project["id"])
    event = next(item for item in report["versions"] if item["action"] == "wording_adopt")
    evidence = event["wording_adoption"]
    parents = {item["id"]: item for item in report["versions"]}
    context = version["context"]["wording_adoption"]
    assert (
        parents[evidence["source_version"]]["payload_sha256"] == context["source"]["payload_sha256"]
    )
    assert evidence["source_passage"] == "passage-001"
    assert evidence["target_passage"] == "passage-001"
    assert "adopt" in methods.lower()
    encoded = json.dumps(report) + methods
    for private in ("PRIVATE_", ours["id"], source["id"], "Ændret", context["source_text_sha256"]):
        assert private not in encoded


@pytest.mark.parametrize(
    "field", ["source_hash", "source_passage", "before_hash", "extra", "successor"]
)
def test_adoption_report_refuses_forged_evidence_or_unrelated_changes(client, field):
    store, project, _, _, _, version = adopted(client)
    context = version["context"]
    evidence = context["wording_adoption"]
    if field == "source_hash":
        evidence["source"]["payload_sha256"] = "0" * 64
    elif field == "source_passage":
        evidence["source_passage_id"] = "missing"
    elif field == "before_hash":
        evidence["target_text_sha256"] = "0" * 64
    elif field == "extra":
        evidence["private_path"] = "PRIVATE_PATH"
    else:
        payload = version["payload"]
        payload["segments"][1]["text"] = "An unrelated changed passage."
        encoded, digest = encode_payload(payload["segments"], payload["speakers"])
        store.execute(
            "UPDATE result_versions SET payload=?,payload_sha256=? WHERE id=?",
            (encoded, digest, version["id"]),
        )
    store.execute(
        "UPDATE result_versions SET context=? WHERE id=?", (json.dumps(context), version["id"])
    )
    with pytest.raises(ValueError, match="adoption"):
        manifest(store, project["id"])


def test_historical_adoption_keeps_source_evidence_after_source_trash(client):
    store, project, recording, _, source, _ = adopted(client)
    store.set_result_trashed(
        recording["id"], source["id"], store.recording(recording["id"])["revision"], trashed=True
    )
    report, _ = manifest(store, project["id"])
    event = next(item for item in report["versions"] if item["action"] == "wording_adopt")
    parent = next(
        item
        for item in report["versions"]
        if item["id"] == event["wording_adoption"]["source_version"]
    )
    assert parent["result_in_trash"] is True


def test_adoption_metadata_refuses_control_ids(client):
    *_, version = adopted(client)
    evidence = version["context"]["wording_adoption"]
    assert wording_adoption_metadata(evidence) == evidence
    evidence["target_passage_id"] = "bad\nidentifier"
    with pytest.raises(ValueError, match="adoption"):
        wording_adoption_metadata(evidence)
