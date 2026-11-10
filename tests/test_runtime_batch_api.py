"""Runtime API wiring against synthetic libraries and mocked control-plane contracts."""

import copy
import sqlite3
import threading
import wave
from pathlib import Path

import pytest
from admission_support import inspect_and_materialize

from cbs_transcriber import (
    enhancement_api,
    providers,
    runtime_batches,
    runtime_store,
    ucloud_native,
)
from cbs_transcriber.runtime_compute import MIG_PRODUCT
from cbs_transcriber.store import Store, new_id, now
from cbs_transcriber.transcripts import normalize_segments

REAL_ACTIVITY = runtime_batches.activity
REAL_CANCEL = runtime_batches.cancel
REAL_REQUIRE_NO_OBLIGATIONS = runtime_batches.require_no_obligations


@pytest.fixture(autouse=True)
def runtime_contract(monkeypatch):
    """Transport/action implementation has separate tests; exercise its API seam here."""
    monkeypatch.setattr(runtime_batches, "job_batch", runtime_store.for_job, raising=False)
    monkeypatch.setattr(runtime_batches, "activity", lambda store: [], raising=False)

    def require_idle(store, project_id=None, recording_ids=None):
        if runtime_store.obligations(store, project_id, recording_ids):
            raise ValueError("Resolve the CBS Runtime batch obligations in Activity first.")

    monkeypatch.setattr(runtime_batches, "require_no_obligations", require_idle, raising=False)


def add_recording(store, project, filename="synthetic.wav", *, reviewed=False):
    row = store.create_recording(project["id"], filename)
    directory = store.directory(row["id"])
    directory.mkdir(parents=True, exist_ok=True)
    with wave.open(str(directory / ("original" + Path(filename).suffix.lower())), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 48000)
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (row["id"],))
    if reviewed:
        store.add_result(
            row["id"],
            normalize_segments([{"text": "Literal synthetic source.", "start": 0, "end": 3}]),
            {},
            "Reviewed source",
            {},
        )
    return store.recording(row["id"])


def add_profile(store, credentials, model, *, provider_type="ucloud", application=None):
    settings = providers.Settings(
        provider_type=provider_type,
        base_url="https://runtime.invalid" if provider_type == "ucloud" else providers.OPENAI_URL,
        model=model,
        approved_for=["protected"],
        ucloud_transcriber={
            "project_id": "synthetic-remote-project",
            "storage_path": "/drive/synthetic",
            "application": application or {"name": "transcriber", "version": "synthetic"},
            "product": {
                "id": "gpu-nvidia-b200-1-gpu",
                "category": "gpu",
                "provider": "ucloud",
            },
        },
    )
    identifier = new_id()
    store.execute(
        "INSERT INTO providers VALUES (?,?,?,?)",
        (identifier, model, settings.model_dump_json(), now()),
    )
    credentials.set(providers.credential_scope(settings), "synthetic-not-a-real-key", False)
    return providers.profile(store, identifier)


def plan():
    return {
        "identity": {
            "protocol": runtime_store.PROTOCOL,
            "application": {"name": "terminal-ubuntu", "version": "synthetic"},
            "code_hashes": {"supervisor": "1" * 64, "controller": "2" * 64},
            "model_revisions": {"large-v3": "pinned"},
            "recipe_hash": "3" * 64,
            "limits": {
                "max_members": 100,
                "idle_grace_seconds": 60,
                "owner_absence_seconds": 300,
                "late_cutoff_seconds": 120,
                "gpu_concurrency": 1,
            },
        }
    }


def assert_queue_unlocked(queue):
    acquired = []

    def probe():
        available = queue.lock.acquire(blocking=False)
        acquired.append(available)
        if available:
            queue.lock.release()

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(timeout=2)
    assert acquired == [True], "Runtime metadata/preflight ran under the queue lock."


@pytest.fixture
def prepared(client, credentials, monkeypatch):
    store, queue = client.app.state.store, client.app.state.queue
    project = store.create_project("Synthetic batch API study")
    provider = add_profile(store, credentials, "whisperx/large-v3")

    def freeze_profile(store, credentials, selected, project_ids, options):
        assert_queue_unlocked(queue)
        return options.model_copy(update={"device": "cuda", "allow_downloads": False}), {
            "ucloud_api": {
                "provider_id": selected["id"],
                "settings": copy.deepcopy(selected["settings"]),
                "runtime_id": "synthetic-runtime",
                "runtime_product": dict(MIG_PRODUCT),
                "runtime_compute_policy": "mig-v1",
                "prepare_runtime": options.prepare_runtime,
                "batch_plan": plan(),
            }
        }

    monkeypatch.setattr(enhancement_api, "freeze_profile", freeze_profile)
    monkeypatch.setattr(
        enhancement_api,
        "freeze",
        lambda store, credentials, projects, options: freeze_profile(
            store, credentials, provider, projects, options
        ),
    )
    monkeypatch.setattr(enhancement_api, "whisperx_ready", lambda *args: True)
    monkeypatch.setattr(enhancement_api, "runtime_stamped", lambda *args: True)
    with store.library_lock():
        yield store, project, provider


def transcribe(client, provider_ids, recording_ids):
    return client.post(
        "/api/transcriptions",
        json={"provider_ids": provider_ids, "recording_ids": recording_ids},
    )


def runtime_job(client, prepared):
    store, project, provider = prepared
    recording = add_recording(store, project)
    response = transcribe(client, [provider["id"]], [recording["id"]])
    assert response.status_code == 202, response.text
    [accepted] = response.json()
    assert accepted["params"]["input_intent"]["phase"] == "waiting"
    [job] = inspect_and_materialize(store, client.app.state.credentials, [accepted["id"]])
    return recording, job


def test_mixed_request_enqueues_native_and_runtime_in_one_transaction(
    client, prepared, credentials, monkeypatch
):
    store, project, provider = prepared
    native = add_profile(store, credentials, "large-v3", application=ucloud_native.MIG_APPLICATION)

    def discover(settings, key, *, compute_id=None):
        assert compute_id == MIG_PRODUCT["id"]
        assert key == "synthetic-not-a-real-key"
        assert settings.ucloud_transcriber.product == MIG_PRODUCT
        assert settings.ucloud_transcriber.application == ucloud_native.MIG_APPLICATION
        return {"ready": True, "setup": settings.ucloud_transcriber.model_dump()}

    monkeypatch.setattr("cbs_transcriber.ucloud_discovery.discover", discover)
    recordings = [add_recording(store, project) for _ in range(2)]
    response = transcribe(client, [provider["id"], native["id"]], [r["id"] for r in recordings])
    assert response.status_code == 202, response.text
    rows = response.json()
    assert len(rows) == 4
    assert all(row["params"]["input_intent"]["phase"] == "waiting" for row in rows)
    assert store.all("SELECT id FROM runtime_batch_runs") == []
    assert store.all("SELECT id FROM native_batch_runs") == []
    rows = inspect_and_materialize(store, credentials, [row["id"] for row in rows])
    assert all("input_intent" not in row["params"] for row in rows), rows
    runtimes = [j for j in rows if j["kind"] == "enhance"]
    natives = [j for j in rows if j["kind"] == "transcribe"]
    assert len(runtimes) == len(natives) == 2
    assert len({j["params"]["runtime_batch_id"] for j in runtimes}) == 1
    assert len({j["params"]["native_batch_id"] for j in natives}) == 1
    assert len(store.all("SELECT id FROM runtime_batch_runs")) == 1
    assert len(store.all("SELECT id FROM native_batch_runs")) == 1


def test_mixed_acceptance_insert_failure_leaves_no_jobs_inspections_or_batch_reservations(
    client, prepared, credentials
):
    store, project, provider = prepared
    native = add_profile(store, credentials, "large-v3")
    good = add_recording(store, project)
    invalid = add_recording(store, project)
    store.execute(
        "CREATE TRIGGER refuse_selection BEFORE INSERT ON jobs "
        f"WHEN NEW.recording_id='{invalid['id']}' AND NEW.kind='transcribe' "
        "BEGIN SELECT RAISE(ABORT,'Synthetic acceptance insert'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="Synthetic acceptance insert"):
        transcribe(client, [provider["id"], native["id"]], [good["id"], invalid["id"]])
    for table in ("jobs", "runtime_batch_members", "runtime_batch_runs", "native_batch_runs"):
        assert store.all(f"SELECT * FROM {table}") == []
    assert all(
        row["original_input"] is None for row in store.all("SELECT original_input FROM recordings")
    )


def test_late_compatible_requests_share_owner_and_model_comparisons_stay_valid(
    client, prepared, credentials
):
    store, project, provider = prepared
    first_recording, first = runtime_job(client, prepared)
    second_recording = add_recording(store, project)
    late = transcribe(client, [provider["id"]], [second_recording["id"]])
    assert late.status_code == 202, late.text
    [late_job] = inspect_and_materialize(store, credentials, [late.json()[0]["id"]])
    assert late_job["params"]["runtime_batch_id"] == first["params"]["runtime_batch_id"]
    comparison = add_profile(store, credentials, "whisperx/large-v3-turbo")
    different = transcribe(client, [comparison["id"]], [first_recording["id"]])
    assert different.status_code == 202, different.text
    [comparison_job] = inspect_and_materialize(store, credentials, [different.json()[0]["id"]])
    assert comparison_job["params"]["runtime_batch_id"] != first["params"]["runtime_batch_id"]
    duplicate = transcribe(client, [provider["id"]], [first_recording["id"]])
    assert duplicate.status_code == 409
    assert len(store.all("SELECT id FROM jobs WHERE kind IN ('transcribe','enhance')")) == 3
    assert len(store.all("SELECT id FROM jobs WHERE kind='source-inspect'")) == 2


@pytest.mark.parametrize("operation", ["enhancements", "retranscriptions"])
def test_source_freeze_and_batch_membership_share_enqueue_transaction(
    client, prepared, monkeypatch, operation
):
    store, project, _ = prepared
    recording = add_recording(store, project, reviewed=True)
    original = store._freeze_result

    def change_revision_then_freeze(db, recording_id, result_id, revision):
        db.execute("UPDATE recordings SET revision=revision+1 WHERE id=?", (recording_id,))
        return original(db, recording_id, result_id, revision)

    monkeypatch.setattr(store, "_freeze_result", change_revision_then_freeze)
    body = {
        "result_id": recording["active_transcript_id"],
        "revision": recording["revision"],
        "execution": "ucloud",
    }
    if operation == "retranscriptions":
        body["segment_ids"] = ["0"]
    response = client.post(f"/api/recordings/{recording['id']}/{operation}", json=body)
    assert response.status_code == 409, response.text
    assert "another window" in response.json()["detail"]
    assert store.all("SELECT * FROM jobs") == []
    assert store.all("SELECT * FROM runtime_batch_runs") == []


@pytest.mark.parametrize("operation", ["enhancements", "retranscriptions"])
def test_full_enhancement_and_retranscription_join_runtime_with_exact_source(
    client, prepared, operation
):
    store, project, _ = prepared
    recording = add_recording(store, project, reviewed=True)
    body = {
        "result_id": recording["active_transcript_id"],
        "revision": recording["revision"],
        "execution": "ucloud",
    }
    if operation == "retranscriptions":
        body["segment_ids"] = ["0"]
    response = client.post(f"/api/recordings/{recording['id']}/{operation}", json=body)
    assert response.status_code == 202, response.text
    job = response.json()
    if operation == "enhancements":
        assert (
            job["params"]["input_intent"]["source_snapshot"]["id"]
            == recording["active_transcript_id"]
        )
        [job] = inspect_and_materialize(store, client.app.state.credentials, [job["id"]])
    batch = runtime_store.for_job(store, job)
    assert batch["identity"]["operation"] == (
        "passage_retranscription" if operation == "retranscriptions" else "enhancement"
    )
    source = job["params"]["source_snapshot"]
    assert source["id"] == recording["active_transcript_id"]
    version = store.result_versions(recording["id"], source["id"])[0]
    assert source["source_version_id"] == version["id"]
    assert source["source_payload_sha256"] == version["payload_sha256"]
    assert store.recording(recording["id"])["active_transcript_id"] == source["id"]


@pytest.mark.parametrize(
    ("endpoint", "helper", "payload"),
    [
        ("cancel", "cancel", None),
        ("retry", "retry", {}),
        ("cleanup-ucloud", "retry_cleanup", None),
        ("reconcile", "reconcile", {"not_created": True}),
    ],
)
def test_member_actions_route_to_owner_before_completed_and_preflight_guards(
    client, prepared, monkeypatch, endpoint, helper, payload
):
    store, _, _ = prepared
    _, job = runtime_job(client, prepared)
    store.update_job(job["id"], state="completed")
    expected = {"scope": "runtime_batch", "batch_id": job["params"]["runtime_batch_id"]}
    calls = []

    def action(received_store, credentials, batch_id, **kwargs):
        assert received_store is store
        assert_queue_unlocked(client.app.state.queue)
        calls.append((batch_id, kwargs))
        return expected

    monkeypatch.setattr(runtime_batches, helper, action, raising=False)
    response = client.post(f"/api/jobs/{job['id']}/{endpoint}", json=payload)
    assert response.status_code == 200, response.text
    assert response.json() == expected
    assert calls == [
        (expected["batch_id"], {"confirm_not_created": True} if helper == "reconcile" else {})
    ]


@pytest.mark.parametrize("action", ["cancel", "retry", "reconcile", "cleanup"])
def test_direct_runtime_actions_share_origin_boundary(client, monkeypatch, action):
    calls = []
    helper = "retry_cleanup" if action == "cleanup" else action

    def callback(store, credentials, batch_id, **kwargs):
        calls.append((batch_id, kwargs))
        return {"scope": "runtime_batch", "batch_id": batch_id}

    monkeypatch.setattr(runtime_batches, helper, callback, raising=False)
    # Stop/Retry first lock the batch's member jobs; this synthetic batch has none.
    monkeypatch.setattr(runtime_store, "get", lambda *_: {"members": []})
    url = "/api/runtime-batches/synthetic-batch/" + action
    assert client.post(url, json={}, headers={"Origin": "https://other.invalid"}).status_code == 403
    assert calls == []
    response = client.post(url, json={})
    assert response.status_code == 200, response.text
    assert calls == [
        ("synthetic-batch", {"confirm_not_created": False} if action == "reconcile" else {})
    ]


def test_runtime_reconcile_never_ignores_supplied_remote_id(client, prepared, monkeypatch):
    _, job = runtime_job(client, prepared)
    calls = []
    monkeypatch.setattr(
        runtime_batches, "reconcile", lambda *args, **kwargs: calls.append(args), raising=False
    )
    for url in (
        f"/api/jobs/{job['id']}/reconcile",
        f"/api/runtime-batches/{job['params']['runtime_batch_id']}/reconcile",
    ):
        response = client.post(url, json={"remote_id": "unrelated-remote-id"})
        assert response.status_code == 400
        assert "exact saved run name" in response.json()["detail"]
    assert calls == []


def test_corrupt_runtime_membership_cannot_address_another_owner(client, prepared, monkeypatch):
    store, _, _ = prepared
    _, job = runtime_job(client, prepared)
    job["params"]["runtime_batch_id"] = "another-owner"
    store.update_job(job["id"], params=job["params"])
    calls = []
    monkeypatch.setattr(runtime_batches, "cancel", lambda *args: calls.append(args), raising=False)
    response = client.post(f"/api/jobs/{job['id']}/cancel")
    assert response.status_code == 400
    assert calls == []


def test_owner_activity_is_independent_of_recent_member_limit(client, prepared, monkeypatch):
    store, _, _ = prepared
    recording, job = runtime_job(client, prepared)
    batch_id = job["params"]["runtime_batch_id"]
    owner = {
        "id": "runtime-batch-" + batch_id,
        "kind": "enhance",
        "recording_id": None,
        "state": "failed",
        "runtime_batch_id": batch_id,
        "runtime_batch": {"id": batch_id, "unresolved": True},
    }
    monkeypatch.setattr(runtime_batches, "activity", lambda store: [owner])
    rows = client.get("/api/jobs").json()
    assert any(r["id"] == job["id"] and r["runtime_batch_id"] == batch_id for r in rows)
    store.update_job(job["id"], state="completed")
    store.execute("UPDATE jobs SET created='2000-01-01T00:00:00' WHERE id=?", (job["id"],))
    with store.connection() as db:
        db.executemany(
            "INSERT INTO jobs(id,recording_id,kind,state,created,updated) "
            "VALUES (?,?,'media','completed',?,?)",
            [(new_id(), recording["id"], now(), now()) for _ in range(201)],
        )
    client.post("/api/activity/clear")
    rows = client.get("/api/jobs").json()
    assert [r for r in rows if r.get("runtime_batch")] == [owner]
    assert len([r for r in rows if not r.get("runtime_batch")]) == 200
    assert all("params" not in row and "source_snapshot" not in row for row in rows)


def test_real_owner_activity_cancel_before_post_and_clear_preserve_all_receipts(
    client, prepared, monkeypatch
):
    store, project, provider = prepared
    monkeypatch.setattr(runtime_batches, "activity", REAL_ACTIVITY)
    monkeypatch.setattr(runtime_batches, "cancel", REAL_CANCEL)
    monkeypatch.setattr(runtime_batches, "require_no_obligations", REAL_REQUIRE_NO_OBLIGATIONS)

    def refuse_remote_client(*args, **kwargs):
        pytest.fail("Cancelling a never-submitted batch must perform no UCloud request.")

    monkeypatch.setattr(runtime_batches, "_client", refuse_remote_client)
    recordings = [add_recording(store, project) for _ in range(2)]
    response = transcribe(client, [provider["id"]], [row["id"] for row in recordings])
    assert response.status_code == 202, response.text
    jobs = response.json()
    jobs = inspect_and_materialize(store, client.app.state.credentials, [job["id"] for job in jobs])
    job_ids = {row["id"] for row in jobs}
    batch_id = jobs[0]["params"]["runtime_batch_id"]
    rows = client.get("/api/jobs").json()
    assert len(rows) == 5
    assert {
        row["id"] for row in rows if row["kind"] == "enhance" and row["recording_id"]
    } == job_ids
    assert len([row for row in rows if row["kind"] == "source-inspect"]) == 2
    owners = [row for row in rows if row.get("runtime_batch")]
    assert len(owners) == 1
    owner = owners[0]
    assert owner["id"] == "runtime-batch-" + batch_id
    assert owner["runtime_batch_id"] == batch_id
    assert owner["runtime_batch"]["member_count"] == 2
    assert {row["job_id"] for row in owner["runtime_batch"]["members"]} == job_ids
    assert owner["runtime_batch"]["unresolved"] and owner["runtime_batch"]["can_cancel"]

    # A reservation alone never proves a POST happened; cancellation can release it locally.
    batch = runtime_store.get(store, batch_id)
    assert runtime_store.reserve_capacity(store, batch_id, epoch=batch["epoch"])
    response = client.post(f"/api/runtime-batches/{batch_id}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["runtime_batch_id"] == batch_id
    assert set(response.json()["affected_jobs"]) == job_ids
    batch = runtime_store.get(store, batch_id)
    assert batch["state"] == "cancelled" and batch["admission"] == "closed"
    assert batch["journal"]["stop_intent"] and batch["journal"]["no_post"]
    assert batch["journal"]["capacity_reserved"] is False
    assert not batch["journal"].get("submission_intent") and not batch["journal"].get("job_id")
    assert all(m["state"] == m["attempt_state"] == "cancelled" for m in batch["members"])
    assert all(store.job(job_id)["state"] == "cancelled" for job_id in job_ids)
    assert runtime_store.obligations(store) == []
    REAL_REQUIRE_NO_OBLIGATIONS(store, project_id=project["id"])

    cancelled = next(row for row in client.get("/api/jobs").json() if row.get("runtime_batch"))
    info = cancelled["runtime_batch"]
    assert not any(info[key] for key in ("unresolved", "can_cancel", "can_retry", "can_cleanup"))
    clear = client.post("/api/activity/clear")
    assert clear.status_code == 200, clear.text
    assert client.get("/api/activity").json()["cleared_at"] == clear.json()["cleared_at"]
    # Activity hides settled terminal owners by this cutoff; /jobs retains editor/history rows.
    assert cancelled["state"] == "cancelled"
    assert cancelled["updated"] <= clear.json()["cleared_at"]
    assert len(client.get("/api/jobs").json()) == 5
    assert runtime_store.get(store, batch_id) == batch
    assert len(store.all("SELECT id FROM runtime_batch_runs")) == 1
    assert len(store.all("SELECT job_id FROM runtime_batch_members")) == 2
    assert len(store.all("SELECT task_id FROM runtime_batch_attempts")) == 2
    assert len(store.all("SELECT id FROM jobs WHERE kind='enhance'")) == 2
    assert len(store.all("SELECT id FROM jobs WHERE kind='source-inspect'")) == 2


def test_runtime_owner_card_is_removed_from_activity_only_without_obligations(
    client, prepared, monkeypatch
):
    store, project, provider = prepared
    monkeypatch.setattr(runtime_batches, "cancel", REAL_CANCEL)

    def refuse_remote_client(*args, **kwargs):
        pytest.fail("Removing an Activity card must perform no UCloud request.")

    monkeypatch.setattr(runtime_batches, "_client", refuse_remote_client)
    recordings = [add_recording(store, project) for _ in range(2)]
    response = transcribe(client, [provider["id"]], [row["id"] for row in recordings])
    assert response.status_code == 202, response.text
    jobs = inspect_and_materialize(
        store, client.app.state.credentials, [job["id"] for job in response.json()]
    )
    batch_id = jobs[0]["params"]["runtime_batch_id"]
    card = "runtime-batch-" + batch_id

    def hide(identifier):
        return client.post("/api/activity/hide", json={"job_id": identifier})

    # The open owner (and any member) keeps its card while work or obligations remain.
    assert hide(card).status_code == 409
    assert hide(jobs[0]["id"]).status_code == 409
    assert client.post(f"/api/runtime-batches/{batch_id}/cancel").status_code == 200
    assert runtime_store.obligations(store) == []
    batch = runtime_store.get(store, batch_id)
    response = hide(card)
    assert response.status_code == 200, response.text
    assert response.json()["hidden"] == {card: batch["updated"]}
    assert runtime_store.get(store, batch_id) == batch
    assert len(store.all("SELECT job_id FROM runtime_batch_members")) == 2


def test_finished_members_block_storage_and_library_switch_but_delete_overrules(
    client, prepared, tmp_path
):
    store, project, _ = prepared
    recording, job = runtime_job(client, prepared)
    store.update_job(job["id"], state="completed")
    store.execute("UPDATE runtime_batch_members SET state='completed' WHERE job_id=?", (job["id"],))
    store.execute("UPDATE runtime_batch_attempts SET state='complete' WHERE job_id=?", (job["id"],))
    store.execute(
        "UPDATE runtime_batch_runs SET state='failed',journal=? WHERE id=?",
        ('{"cleanup_state":"pending"}', job["params"]["runtime_batch_id"]),
    )
    calls = []
    client.app.state.switch_library = calls.append
    other_library = Store(tmp_path / "other-library")
    other_database = other_library.db_path.read_bytes()
    requests = [
        ("post", "/api/libraries/open", {"path": str(other_library.root)}),
        ("post", f"/api/projects/{project['id']}/storage", {"storage_parent": str(tmp_path)}),
    ]
    for method, url, body in requests:
        response = client.request(method, url, json=body)
        assert response.status_code == 409, (url, response.text)
        assert "Runtime batch obligations" in response.json()["detail"]
    assert calls == []
    assert other_library.db_path.read_bytes() == other_database
    assert store.recording(recording["id"])["id"] == recording["id"]
    # Delete overrules the owner's obligation (ADR 0101); its journal outlives the rows.
    batch_id = job["params"]["runtime_batch_id"]
    response = client.delete(f"/api/projects/{project['id']}")
    assert response.status_code == 200, response.text
    assert not store.all("SELECT id FROM recordings WHERE id=?", (recording["id"],))
    retained = runtime_store.get(store, batch_id)
    assert retained["journal"]["cleanup_state"] == "pending"
    assert retained["journal"]["deleted_members"] == [job["id"]]
    assert [b["id"] for b in runtime_store.obligations(store)] == [batch_id]
    unrelated = store.create_project("Unrelated synthetic project")
    assert client.delete(f"/api/projects/{unrelated['id']}").status_code == 200


def test_capacity_settings_are_strict_persistent_and_same_origin(client):
    assert client.get("/api/runtime-batches/settings").json() == {"max_allocations": 2}
    for value in (0, 5, True, "2", 2.5):
        response = client.put("/api/runtime-batches/settings", json={"max_allocations": value})
        assert response.status_code == 422
    assert (
        client.put(
            "/api/runtime-batches/settings",
            json={"max_allocations": 3},
            headers={"Origin": "https://other.invalid"},
        ).status_code
        == 403
    )
    assert client.get("/api/runtime-batches/settings").json() == {"max_allocations": 2}
    assert client.put("/api/runtime-batches/settings", json={"max_allocations": 3}).json() == {
        "max_allocations": 3
    }
    store = Store(client.app.state.store.root)
    assert runtime_store.settings(store) == {"max_allocations": 3}
