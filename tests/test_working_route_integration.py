"""Synthetic route-to-worker checks for Working-result progression."""

import json
import threading
import time
from pathlib import Path

import pytest
from test_automatic_working import add, mark, role
from test_feature_followups import materialize_response
from test_feature_followups import prepared as prepared
from test_feature_followups import request as transcribe
from test_retranscription import request
from test_retranscription import reviewed as reviewed

from cbs_transcriber import (
    enhancement,
    media,
    portable_capture,
    portable_semantics,
    portable_transport,
)
from cbs_transcriber.transcripts import normalize_segments


@pytest.mark.parametrize(
    "scenario",
    [
        "unchanged",
        "unassigned",
        "alternative",
        "role_changed",
        "role_kept",
        "legacy",
        "legacy_collision",
    ],
)
def test_retranscription_route_respects_frozen_working_role(reviewed, monkeypatch, scenario):
    client, store, rid, source = reviewed
    if scenario == "alternative":
        alternative = add(store, rid, "Existing Working version")
        mark(store, rid, alternative["id"])
    elif scenario != "unassigned":
        mark(store, rid, source["id"])
    response = request(client, rid, source, ["1"])
    assert response.status_code == 202, response.text
    job = response.json()
    if scenario == "role_changed":
        alternative = add(store, rid, "Another reviewed version")
        mark(store, rid, alternative["id"])
    if scenario == "role_kept":
        # Explicitly keeping the same version is a new choice, and fences delayed work.
        mark(store, rid, source["id"])
    if scenario in {"legacy", "legacy_collision"}:
        params = job["params"]
        if scenario == "legacy":
            params.pop("working_role_policy", None)
        store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job["id"]))
        # A job predating schema 18 has no server-owned promotion receipt, even if
        # historical arbitrary JSON happens to contain names used by new policies.
        store.execute("DELETE FROM working_job_authorities WHERE job_id=?", (job["id"],))
    before = role(store, rid)

    def run(folder, options, token, cancel, progress, **kwargs):
        sent = json.loads((folder / "request.json").read_text(encoding="utf-8"))
        assert [passage["text"] for passage in sent["passages"]] == [""]
        enhancement.write_json(
            folder / "retranscription.json",
            {"passages": [{"id": "1", "text": "Hej verden.", "avg_logprob": -0.4}]},
        )
        enhancement.write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": "1",
                        "words": [
                            {"word": "Hej", "start": 10.1, "end": 10.6, "score": 0.9},
                            {"word": "verden.", "start": 10.7, "end": 11.8, "score": 0.9},
                        ],
                    }
                ]
            },
        )

    monkeypatch.setattr(media, "ffmpeg", lambda args, cancel: Path(args[-1]).write_bytes(b"x"))
    monkeypatch.setattr(enhancement, "run_worker", run)
    queue = client.app.state.queue
    queue.cancel_event = threading.Event()
    queue.process(job["id"])
    saved = store.one("SELECT id FROM transcript_results WHERE job_id=?", (job["id"],))
    after = role(store, rid)
    if scenario in {"unchanged", "unassigned", "alternative"}:
        assert after["working"]["result_id"] == saved["id"]
    else:
        assert after == before


@pytest.mark.parametrize("scenario", ["unassigned", "existing", "role_changed"])
def test_fresh_transcription_frozen_followup_promotes_successor(
    client, prepared, monkeypatch, scenario
):
    rid, baseline, _ = prepared
    store = client.app.state.store
    if scenario != "unassigned":
        mark(store, rid, baseline)
    response = client.post("/api/transcriptions", json=transcribe(rid))
    [parent] = materialize_response(client, response)
    assert parent["params"]["followup"]["params"]["enhancement"]["diarize"] is True
    if scenario == "role_changed":
        alternative = add(store, rid, "Explicitly selected during processing")
        mark(store, rid, alternative["id"])
    before = role(store, rid)
    queue = client.app.state.queue
    queue.process(parent["id"])
    children = store.all("SELECT id FROM jobs WHERE kind='enhance'")
    assert len(children) == 1
    child = store.job(children[0]["id"])
    parent_result = store.one("SELECT id FROM transcript_results WHERE job_id=?", (parent["id"],))
    assert child["params"]["source_snapshot"]["id"] == parent_result["id"]
    assert store.recording(rid)["active_transcript_id"] == baseline
    assert role(store, rid) == before  # Raw alternative never moves Working.

    def execute(worker, job, recording):
        source = job["params"]["source_snapshot"]
        segments, speakers = enhancement.merge_output(
            source,
            None,
            {"turns": [{"start": 0, "end": 3, "speaker": "A"}]},
            enhancement.EnhancementOptions.model_validate(job["params"]["enhancement"]),
            3,
            job["id"],
        )
        worker.store.add_result(rid, segments, speakers, "Enhanced", {}, job_id=job["id"])

    monkeypatch.setattr(enhancement, "run_enhancement", execute)
    queue.process(child["id"])
    final = store.one("SELECT id FROM transcript_results WHERE job_id=?", (child["id"],))
    if scenario == "role_changed":
        assert role(store, rid) == before
    else:
        assert role(store, rid)["working"]["result_id"] == final["id"]


@pytest.mark.parametrize(
    "legacy_fields",
    [
        {"working_role": {"legacy": True}},
        {"working_role_policy": "old custom value"},
        {"timing_working_policy": {"legacy": True}},
        {
            "working_role": {"legacy": True},
            "working_role_policy": "old custom value",
            "timing_working_policy": {"legacy": True},
        },
    ],
)
def test_legacy_transcription_followup_preserves_inert_colliding_fields(
    client, prepared, legacy_fields
):
    rid, _, _ = prepared
    store = client.app.state.store
    response = client.post("/api/transcriptions", json=transcribe(rid))
    [parent] = materialize_response(client, response)
    params = parent["params"]
    params["followup"]["params"].update(legacy_fields)
    # Emulate old persisted jobs containing arbitrary formerly-unreserved keys,
    # without granting the new server-owned receipt.
    store.execute("DELETE FROM working_job_authorities WHERE job_id=?", (parent["id"],))
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), parent["id"]))
    before = role(store, rid)
    # `process` only writes the result; the worker run loop claims the job and
    # records its terminal state. Exercise that lifecycle instead of setting it here.
    queue = client.app.state.queue
    worker = threading.Thread(target=queue.run, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 30
        while store.job(parent["id"])["state"] not in {"completed", "failed", "cancelled"}:
            if time.monotonic() >= deadline:
                pytest.fail("Synthetic transcription worker did not finish")
            threading.Event().wait(0.05)
    finally:
        queue.stopping.set()
        queue.wakeup.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert store.job(parent["id"])["state"] == "completed"
    children = store.all("SELECT id FROM jobs WHERE kind='enhance'")
    assert len(children) == 1
    child = store.job(children[0]["id"])
    for name, value in legacy_fields.items():
        assert child["params"][name] == value
    assert not store.all("SELECT * FROM working_job_authorities WHERE job_id=?", (child["id"],))
    assert role(store, rid) == before


def test_real_enqueued_authorities_survive_portable_capture_and_inspection(
    client, prepared, tmp_path
):
    rid, _, _ = prepared
    store = client.app.state.store
    transcription = client.post("/api/transcriptions", json=transcribe(rid))
    [parent] = materialize_response(client, transcription)
    source = store.add_result(
        rid,
        normalize_segments([{"text": "Synthetic source", "start": 0, "end": 1}]),
        {},
        "Synthetic",
        {},
    )
    correction = request(client, rid, source, ["0"])
    assert correction.status_code == 202, correction.text
    child = correction.json()
    authorities = store.all(
        "SELECT job_id FROM working_job_authorities WHERE job_id IN (?,?)",
        (parent["id"], child["id"]),
    )
    assert {row["job_id"] for row in authorities} == {parent["id"], child["id"]}
    # Capture requires an idle synthetic library. No inference is run; these
    # accepted jobs are made terminal solely in the test fixture.
    store.execute("UPDATE jobs SET state='cancelled' WHERE state IN ('queued','running')")
    result = portable_capture.capture_library(
        store, client.app.state.queue, tmp_path / "packages", gate=portable_capture.CaptureGate()
    )
    # The schema-12-only claims inspector is not the schema-18 restore path.
    # Match Restore's bound transport capture and contained semantic validation.
    capture = portable_transport._capture_transport(
        Path(result["folder"]), expected_commit_sha256=result["commit_sha256"]
    )
    manifest = portable_transport._canonical(capture.manifest_bytes)
    database = manifest["database"]
    observed = portable_semantics.inspect_database(
        capture.root / "objects" / database["object"],
        expected_sha256=database["object"],
        expected_size=database["size"],
        manifest=manifest,
    )
    portable_transport._recheck(capture)
    assert observed["tables"]["working_job_authorities"]["rows"] == 2
    assert observed["restore_ready"] is False


@pytest.mark.parametrize("scope", ["recording", "project"])
def test_real_deletion_staging_cascades_job_authority(client, prepared, scope):
    rid, _, _ = prepared
    store = client.app.state.store
    project = store.recording(rid)["project_id"]
    response = client.post("/api/transcriptions", json=transcribe(rid))
    [job] = materialize_response(client, response)
    assert store.all("SELECT job_id FROM working_job_authorities WHERE job_id=?", (job["id"],))
    directory = store.directory(rid)
    assert (directory / "original.wav").exists()
    endpoint = f"/api/recordings/{rid}" if scope == "recording" else f"/api/projects/{project}"
    deleted = client.delete(endpoint)
    assert deleted.status_code == 200, deleted.text
    assert not store.all("SELECT job_id FROM working_job_authorities WHERE job_id=?", (job["id"],))
    assert not directory.exists()
