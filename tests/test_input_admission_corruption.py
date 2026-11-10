"""V142: malformed nested intent metadata cannot starve unrelated admission work."""

import copy
import json
import threading

import pytest
from test_input_admission import inspect, item
from test_input_admission import library as library

from cbs_transcriber import input_admission
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.store import new_id
from cbs_transcriber.transcripts import normalize_segments


def corrupt(store, job, field, value):
    params = copy.deepcopy(job["params"])
    accepted = params["input_intent"]["accepted"]
    if field == "settings":
        accepted["provider"]["settings"] = value
    else:
        accepted[field] = value
    raw = json.dumps(params)
    store.execute(
        "UPDATE jobs SET params=?,created=? WHERE id=?",
        (raw, "2000-01-01T00:00:00+00:00", job["id"]),
    )
    return raw


@pytest.mark.parametrize(
    ("field", "damage"),
    [
        ("settings", []),
        ("settings", {"max_upload_mb": 999}),
        ("settings", {"concurrent_chunks": {"invalid": 1}}),
        ("settings", {"base_url": "file:///invalid"}),
        ("enhancement", []),
        ("enhancement", {"execution": ["local"]}),
        ("enhancement", {"timeout_minutes": 0}),
        ("options", []),
        ("options", {"execution": "invalid"}),
        ("options", {"max_speakers": 21}),
    ],
)
def test_v142_nested_schema_failure_preserves_raw_intent_and_claims_ready_sibling(
    library, wav_bytes, field, damage
):
    store, _, provider, rows = library
    bad_item = item(provider, rows[0])
    if field == "enhancement":
        bad_item["choice"] = input_admission.choice(
            provider=provider, enhancement={"execution": "local"}
        )
    elif field == "options":
        result = store.add_result(
            rows[0]["id"], normalize_segments([{"text": "Synthetic source"}]), {}, "Source", {}
        )
        bad_item = {
            "recording_id": rows[0]["id"],
            "choice": input_admission.choice(
                options={"execution": "local"},
                source={"result_id": result["id"], "revision": 1},
            ),
        }
    with store.library_lock():
        bad, good = input_admission.accept(store, [bad_item, item(provider, rows[1])])
        existing = bad["params"]["input_intent"]["accepted"]
        original = existing["provider"]["settings"] if field == "settings" else existing[field]
        value = original | damage if isinstance(damage, dict) else damage
        raw = corrupt(store, bad, field, value)
        # Both sources are ready, so the damaged intent reaches full validation rather than
        # the pending-inspection prefilter (ADR 0146).
        for row in rows:
            inspect(store, row, wav_bytes)
        before = store.one("SELECT * FROM jobs WHERE id=?", (bad["id"],))
        token = input_admission.claim(store)
        assert [member["id"] for member in token["jobs"]] == [good["id"]]
        assert store.one("SELECT * FROM jobs WHERE id=?", (bad["id"],)) == before
        assert store.one("SELECT params FROM jobs WHERE id=?", (bad["id"],))["params"] == raw


def test_v142_two_admission_lanes_pass_many_bad_rows_without_rewriting_evidence(
    library, credentials, wav_bytes, monkeypatch
):
    store, project, provider, rows = library
    rows.append(store.create_recording(project["id"], "three.wav"))
    claimed, finished = threading.Barrier(3), threading.Event()
    materialized = set()
    release = threading.Event()
    original_prepare, original_materialize = input_admission.prepare, input_admission.materialize

    def prepare(*args, **kwargs):
        claimed.wait(10)
        assert release.wait(10)
        return original_prepare(*args, **kwargs)

    def materialize(*args, **kwargs):
        result = original_materialize(*args, **kwargs)
        materialized.update(job["id"] for job in result)
        if len(materialized) == 2:
            finished.set()
        return result

    monkeypatch.setattr(input_admission, "prepare", prepare)
    monkeypatch.setattr(input_admission, "materialize", materialize)
    with store.library_lock():
        bad, *good = input_admission.accept(store, [item(provider, row) for row in rows])
        settings = bad["params"]["input_intent"]["accepted"]["provider"]["settings"]
        raw = corrupt(store, bad, "settings", settings | {"max_upload_mb": 999})
        # Many earlier invalid candidates remain visible. Each lane must reach a different
        # healthy file in one finite scan rather than retrying the first candidate forever.
        with store.connection() as db:
            db.executemany(
                "INSERT INTO jobs(id,recording_id,kind,state,params,chunks,created,updated) "
                "SELECT ?,recording_id,kind,state,params,chunks,created,updated "
                "FROM jobs WHERE id=?",
                [(new_id(), bad["id"]) for _ in range(31)],
            )
        # Every source is ready, so the damaged copies reach full validation rather than the
        # pending-inspection prefilter (ADR 0146).
        for recording in rows:
            inspect(store, recording, wav_bytes)
        before = store.all("SELECT * FROM jobs WHERE params=? ORDER BY id", (raw,))
        assert len(before) == 32
        queue = JobQueue(store, credentials)
        workers = [worker for worker in queue.workers if worker.kind == "input-admission"]
        for worker in workers:
            worker._start()
        try:
            claimed.wait(10)
            # Two admission tasks run (the default limit); further idle threads claim nothing.
            active = [worker.active_id for worker in workers if worker.active_id]
            assert sorted(active) == sorted(job["id"] for job in good)
            release.set()
            assert finished.wait(10)
        finally:
            release.set()
            queue.stop()
        assert all("input_acceptance" in store.job(job["id"])["params"] for job in good)
        assert store.all("SELECT * FROM jobs WHERE params=? ORDER BY id", (raw,)) == before
