"""Explicit private-run disposition uses synthetic originals and an in-memory remote drive."""

import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from test_runtime_store import enqueue
from test_runtime_store import library as library

from cbs_transcriber import runtime_batches as batches
from cbs_transcriber import runtime_discard as discard
from cbs_transcriber import runtime_store as storage
from cbs_transcriber.store import new_id
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def retained(library, credentials, monkeypatch):
    store, _, _ = library
    job = enqueue(library)
    batch = storage.for_job(store, job)
    recording = store.recording(job["recording_id"])
    base = store.directory(recording["id"])
    folder = base / "enhancements" / job["id"]
    folder.mkdir(parents=True)
    (base / "original.wav").write_bytes(b"immutable synthetic audio")
    (folder / "audio.wav").write_bytes(b"prepared synthetic audio")
    (folder / "request.json").write_bytes(b'{"synthetic":true}')
    member = batch["members"][0]
    expected_folder = batches.remote_folder(batches._settings(batch), batch["id"])
    journal = {
        "job_id": "owned-synthetic-job",
        "run_name": "CBS Runtime batch " + batch["id"],
        "folder_created": True,
        "owned_folder": expected_folder,
        "remote_state": "FAILURE",
        "stop_intent": True,
        "cleanup_state": "retained",
    }
    store.execute(
        "UPDATE runtime_batch_runs SET state='failed',admission='closed',journal=? WHERE id=?",
        (json.dumps(journal), batch["id"]),
    )
    store.execute(
        "UPDATE runtime_batch_members SET state='interrupted' WHERE job_id=?", (job["id"],)
    )
    store.execute(
        "UPDATE runtime_batch_attempts SET state='interrupted' WHERE job_id=?", (job["id"],)
    )
    store.execute("UPDATE jobs SET state='interrupted' WHERE id=?", (job["id"],))
    remote = {}
    client = SimpleNamespace()
    client.remote = {
        "id": journal["job_id"],
        "status": {"state": "FAILURE"},
        "specification": {
            "name": journal["run_name"],
            "application": batch["identity"]["application"],
            "product": batch["identity"]["runtime_product"],
            "resources": [{"type": "file", "path": expected_folder, "readOnly": False}],
        },
    }
    client.retrieve_job = lambda identifier: copy.deepcopy(client.remote)
    calls = []

    @contextmanager
    def connection(*args):
        yield client

    monkeypatch.setattr(batches, "_client", connection)
    monkeypatch.setattr(
        discard.api, "read_remote", lambda client, server, path, limit=65536: remote.get(path)
    )
    monkeypatch.setattr(
        discard.native, "trash_owned_folder", lambda client, path: calls.append(path)
    )
    monkeypatch.setattr(discard.native, "folder_listed", lambda client, path: not bool(calls))
    env = SimpleNamespace(
        store=store,
        job=job,
        batch_id=batch["id"],
        member=member,
        folder=folder,
        original=base / "original.wav",
        remote=remote,
        remote_folder=expected_folder,
        client=client,
        credentials=credentials,
        trashed=calls,
    )

    def publish(task=None, attempt=1, state="failed", outputs=None):
        task = task or member["task_id"]
        files = {n: batches.hash_file(folder / n) for n in ("request.json", "audio.wav")}
        binding = {
            "protocol": storage.PROTOCOL,
            "batch_id": batch["id"],
            "identity_hash": batch["compatibility_hash"],
            "task_id": task,
            "job_id": job["id"],
            "attempt": attempt,
        }
        inputs = batches.json_bytes(
            {
                **binding,
                "files": files,
                "expected_outputs": sorted(storage._required_stages(batch["identity"])),
            }
        )
        accepted = {
            **binding,
            "inputs_sha256": discard._hash(inputs)["sha256"],
            "inputs_size": len(inputs),
        }
        receipts = {}
        task_folder = expected_folder + "/tasks/" + task
        for name, raw in (outputs or {}).items():
            remote[task_folder + "/" + name] = raw
            receipts[name] = discard._hash(raw)
        remote[task_folder + "/inputs.json"] = inputs
        remote[task_folder + "/accepted.json"] = batches.json_bytes(accepted)
        remote[task_folder + "/request.json"] = (folder / "request.json").read_bytes()
        if state:
            remote[task_folder + "/outcome.json"] = batches.json_bytes(
                {
                    **accepted,
                    "state": state,
                    "error_code": None if state == "complete" else "processing",
                    "outputs": receipts,
                }
            )
        input_receipt = {
            "publish_intent": True,
            "published": True,
            "files": files,
            "manifest_sha256": accepted["inputs_sha256"],
        }
        store.execute(
            "UPDATE runtime_batch_attempts SET input_receipt=? WHERE job_id=? AND attempt=?",
            (json.dumps(input_receipt), job["id"], attempt),
        )
        return task, receipts

    env.publish = publish
    return env


def preview(env):
    return discard.preview(env.store, env.credentials, env.batch_id)


def confirm(env, plan):
    return discard.confirm(
        env.store, env.credentials, env.batch_id, fingerprint=plan["fingerprint"], confirmed=True
    )


def test_private_discard_preserves_partial_outputs_and_records_decision_before_trash(
    retained, monkeypatch
):
    env = retained
    raw = b'{"segments":[{"text":"Synthetic partial."}]}'
    task, _ = env.publish(outputs={"transcription.json": raw})
    plan = preview(env)
    assert plan["count"] == 1 and plan["members"][0]["partial_stages"] == 1
    assert plan["missing_results"] == [env.job["id"]] and not plan["already_confirmed"]
    assert not env.trashed
    before = {
        table: env.store.all("SELECT * FROM " + table)
        for table in (
            "jobs",
            "runtime_batch_members",
            "runtime_batch_attempts",
            "transcript_results",
            "result_versions",
        )
    }

    def trash(client, path):
        batch = storage.get(env.store, env.batch_id)
        decision = batch["journal"]["discard_decision"]
        assert decision["fingerprint"] == plan["fingerprint"]
        assert batch["journal"]["discard_confirmed"] and batch["epoch"] == 1
        assert path == env.remote_folder
        archive = env.store.root / "runtime-batches" / env.batch_id / "discard-evidence" / task
        assert (archive / "transcription.json").read_bytes() == raw
        env.trashed.append(path)

    monkeypatch.setattr(discard.native, "trash_owned_folder", trash)
    assert confirm(env, plan)["cleanup_state"] == "trashed"
    assert {t: env.store.all("SELECT * FROM " + t) for t in before} == before
    assert env.original.read_bytes() == b"immutable synthetic audio"
    assert not storage.obligations(env.store)
    assert preview(env)["already_confirmed"]
    assert confirm(env, plan)["cleanup_state"] == "trashed"
    assert env.trashed == [env.remote_folder]


@pytest.mark.parametrize(
    "change", ["running", "unknown", "folder", "application", "product", "name", "resource"]
)
def test_private_discard_requires_fresh_terminal_exact_remote_identity(retained, change):
    env = retained
    env.publish()
    if change == "running":
        env.client.remote["status"]["state"] = "RUNNING"
    elif change == "unknown":
        env.client.remote["id"] = "another-job"
    elif change == "folder":
        batch = storage.get(env.store, env.batch_id)
        env.store.execute(
            "UPDATE runtime_batch_runs SET journal=? WHERE id=?",
            (json.dumps({**batch["journal"], "owned_folder": "/another"}), env.batch_id),
        )
    elif change == "resource":
        env.client.remote["specification"]["resources"][0]["readOnly"] = True
    else:
        env.client.remote["specification"][change] = "different"
    with pytest.raises(ValueError):
        preview(env)
    assert not env.trashed


@pytest.mark.parametrize("what", ["original", "audio", "missing_input", "source", "result"])
def test_private_discard_rejects_missing_or_changed_local_preservation(retained, what):
    env = retained
    env.publish()
    if what == "original":
        env.original.unlink()
    elif what == "audio":
        (env.folder / "audio.wav").write_bytes(b"damaged")
    elif what == "missing_input":
        env.remote[env.remote_folder + "/tasks/" + env.member["task_id"] + "/inputs.json"] = b"[]"
    elif what == "source":
        env.store.execute(
            "UPDATE runtime_batch_members SET source_identity='{}' WHERE job_id=?", (env.job["id"],)
        )
    else:
        env.store.execute(
            "UPDATE runtime_batch_members SET result_id='unknown' WHERE job_id=?", (env.job["id"],)
        )
    with pytest.raises(ValueError):
        preview(env)
    assert not env.trashed


def test_complete_unsaved_result_blocks_discard_and_keeps_raw_success(retained):
    env = retained
    env.publish(
        state="complete",
        outputs={"transcription.json": b'{"segments":[]}', "alignment.json": b'{"passages":[]}'},
    )
    with pytest.raises(ValueError, match="Reconnect and save"):
        preview(env)
    assert not env.trashed
    assert not storage.get(env.store, env.batch_id)["journal"].get("discard_confirmed")


def test_failed_local_preservation_cannot_authorize_or_trash(retained, monkeypatch):
    env = retained
    env.publish(outputs={"transcription.json": b'{"segments":[]}'})

    def fail_write(*args):
        raise OSError("synthetic disk write refusal")

    monkeypatch.setattr(batches, "durable_write", fail_write)
    with pytest.raises(ValueError, match="disk access"):
        preview(env)
    journal = storage.get(env.store, env.batch_id)["journal"]
    assert not journal.get("discard_confirmed") and not journal.get("discard_decision")
    assert journal["cleanup_state"] == "retained"
    assert not env.trashed


def test_missing_outcome_still_preserves_available_unconfirmed_stage(retained):
    env = retained
    raw = b'{"segments":[{"text":"Recovered bytes without outcome."}]}'
    task, _ = env.publish(state=None, outputs={"transcription.json": raw})
    plan = preview(env)
    assert plan["members"][0]["partial_stages"] == 0
    assert plan["members"][0]["unverified_stages"] == 1
    confirm(env, plan)
    decision = storage.get(env.store, env.batch_id)["journal"]["discard_decision"]
    assert decision["evidence"]["attempts"][0]["outcome"] == "unconfirmed"
    path = (
        env.store.root
        / "runtime-batches"
        / env.batch_id
        / "discard-evidence"
        / task
        / "transcription.json"
    )
    assert path.read_bytes() == raw


def test_discard_inventories_prior_attempt_outputs_without_overwriting_current_files(retained):
    env = retained
    first, _ = env.publish(outputs={"transcription.json": b'{"segments":["first"]}'})
    second = new_id()
    env.store.execute(
        "UPDATE runtime_batch_members SET current_attempt=2 WHERE job_id=?", (env.job["id"],)
    )
    env.store.execute(
        "UPDATE runtime_batch_attempts SET state='failed' WHERE job_id=?", (env.job["id"],)
    )
    env.store.execute(
        "INSERT INTO runtime_batch_attempts(job_id,attempt,task_id,state,created,updated) "
        "VALUES (?,?,?,?,?,?)",
        (env.job["id"], 2, second, "interrupted", "synthetic", "synthetic"),
    )
    env.publish(second, 2, outputs={"transcription.json": b'{"segments":["second"]}'})
    current = env.folder / "transcription.json"
    current.write_bytes(b"current unrelated checkpoint remains unchanged")
    plan = preview(env)
    assert plan["members"][0]["partial_stages"] == 2
    confirm(env, plan)
    archived = env.store.root / "runtime-batches" / env.batch_id / "discard-evidence"
    assert (archived / first / "transcription.json").read_bytes() != (
        archived / second / "transcription.json"
    ).read_bytes()
    assert current.read_bytes() == b"current unrelated checkpoint remains unchanged"


@pytest.mark.parametrize("changed", ["original", "job", "attempt", "artifact"])
def test_confirmation_rejects_stale_preview(retained, changed):
    env = retained
    task, _ = env.publish(outputs={"transcription.json": b'{"segments":[]}'})
    plan = preview(env)
    if changed == "original":
        env.original.write_bytes(b"changed original")
    elif changed == "job":
        env.store.execute("UPDATE jobs SET message='concurrent retry' WHERE id=?", (env.job["id"],))
    elif changed == "attempt":
        env.store.execute(
            "UPDATE runtime_batch_attempts SET error_code='concurrent' WHERE job_id=?",
            (env.job["id"],),
        )
    else:
        env.remote[env.remote_folder + "/tasks/" + task + "/transcription.json"] = (
            b"changed artifact"
        )
    with pytest.raises(ValueError):
        confirm(env, plan)
    assert not env.trashed


def test_explicit_confirmation_is_required_and_failed_trash_has_durable_retry(
    retained, monkeypatch
):
    env = retained
    env.publish()
    plan = preview(env)
    with pytest.raises(ValueError, match="explicitly confirm"):
        discard.confirm(
            env.store,
            env.credentials,
            env.batch_id,
            fingerprint=plan["fingerprint"],
            confirmed=False,
        )
    monkeypatch.setattr(
        discard.native,
        "trash_owned_folder",
        lambda *args: (_ for _ in ()).throw(ValueError("synthetic trash failure")),
    )
    assert confirm(env, plan)["cleanup_state"] == "pending"
    decided = storage.get(env.store, env.batch_id)
    assert decided["journal"]["discard_confirmed"] and decided["journal"]["cleanup_error"]
    assert storage.obligations(env.store)
    monkeypatch.setattr(
        discard.native, "trash_owned_folder", lambda client, path: env.trashed.append(path)
    )
    assert confirm(env, plan)["cleanup_state"] == "trashed"
    assert not storage.obligations(env.store)


def test_disposition_snapshot_cas_fences_recovery_and_late_results(retained, monkeypatch):
    env = retained
    env.publish()
    plan = preview(env)
    original = storage.confirm_discard

    def race(store, identifier, snapshot, evidence, fingerprint):
        store.execute(
            "UPDATE runtime_batch_runs SET coordinator_epoch=coordinator_epoch+1 WHERE id=?",
            (identifier,),
        )
        return original(store, identifier, snapshot, evidence, fingerprint)

    monkeypatch.setattr(storage, "confirm_discard", race)
    with pytest.raises(ValueError, match="changed"):
        confirm(env, plan)
    assert not env.trashed
    monkeypatch.setattr(storage, "confirm_discard", original)
    plan = preview(env)
    confirm(env, plan)
    batch = storage.get(env.store, env.batch_id)
    for method in (storage.reconnect, storage.queue_recovery):
        with pytest.raises(ValueError, match="abandoned"):
            method(env.store, env.batch_id, batch["epoch"], remote_status={})
    env.store.execute(
        "UPDATE runtime_batch_runs SET journal=json_set(journal,'$.recovery_requested',1,"
        "'$.recovery_epoch',coordinator_epoch) WHERE id=?",
        (env.batch_id,),
    )
    assert storage.claim(env.store) is None
    with pytest.raises(ValueError, match="abandoned"):
        env.store.add_result(
            env.job["recording_id"],
            normalize_segments([{"text": "late", "start": 0, "end": 1}]),
            {},
            "late",
            {},
            job_id=env.job["id"],
        )
    assert not env.store.all("SELECT id FROM transcript_results")


@pytest.mark.parametrize("corruption", ["remote_stage", "audio", "archive", "fresh_terminal"])
def test_pending_discard_rechecks_remote_and_local_evidence_before_retry(
    retained, monkeypatch, corruption
):
    env = retained
    task, _ = env.publish()
    plan = preview(env)
    monkeypatch.setattr(
        discard.native, "trash_owned_folder", lambda *a: (_ for _ in ()).throw(ValueError("failed"))
    )
    assert confirm(env, plan)["cleanup_state"] == "pending"
    if corruption == "remote_stage":
        env.remote[env.remote_folder + "/tasks/" + task + "/transcription.json"] = (
            b'{"segments":[]}'
        )
    elif corruption == "audio":
        (env.folder / "audio.wav").write_bytes(b"changed input")
    elif corruption == "archive":
        path = (
            env.store.root
            / "runtime-batches"
            / env.batch_id
            / "discard-evidence"
            / task
            / "accepted.json"
        )
        path.write_bytes(b"changed receipt")
    else:
        env.client.remote["status"]["state"] = "RUNNING"
    monkeypatch.setattr(
        discard.native, "trash_owned_folder", lambda client, path: env.trashed.append(path)
    )
    with pytest.raises(ValueError):
        confirm(env, plan)
    assert not env.trashed
    batch = storage.get(env.store, env.batch_id)
    assert batch["journal"]["cleanup_state"] == "pending"
    assert batch["journal"]["cleanup_stage"] == "discard_validation"
    assert "changed" not in batch["journal"]["cleanup_error"]


def test_new_complete_outcome_after_pending_trash_blocks_abandonment(retained, monkeypatch):
    env = retained
    task, _ = env.publish(state=None)
    plan = preview(env)
    monkeypatch.setattr(
        discard.native, "trash_owned_folder", lambda *a: (_ for _ in ()).throw(ValueError("failed"))
    )
    assert confirm(env, plan)["cleanup_state"] == "pending"
    env.publish(
        state="complete",
        outputs={"transcription.json": b'{"segments":[]}', "alignment.json": b'{"passages":[]}'},
    )
    monkeypatch.setattr(
        discard.native, "trash_owned_folder", lambda client, path: env.trashed.append(path)
    )
    with pytest.raises(ValueError, match="explicit recovery review"):
        confirm(env, plan)
    assert not env.trashed
    archive = env.store.root / "runtime-batches" / env.batch_id / "discard-evidence" / task
    assert (archive / "transcription.json").is_file() and (archive / "alignment.json").is_file()


def test_lost_trash_reply_confirms_exact_parent_absence_without_second_trash(retained, monkeypatch):
    env = retained
    env.publish()
    plan = preview(env)

    def lost_reply(client, path):
        env.trashed.append(path)
        env.remote.clear()
        raise ValueError("lost reply after actual Trash")

    monkeypatch.setattr(discard.native, "trash_owned_folder", lost_reply)
    assert confirm(env, plan)["cleanup_state"] == "pending"
    assert confirm(env, plan)["cleanup_state"] == "trashed"
    assert env.trashed == [env.remote_folder]
    assert not storage.obligations(env.store)


def test_original_changed_during_remote_preservation_refuses_preview(retained, monkeypatch):
    env = retained
    env.publish(outputs={"transcription.json": b'{"segments":[]}'})
    read = discard.api.read_remote

    def changing(*args, **kwargs):
        result = read(*args, **kwargs)
        if args[2].endswith("/transcription.json"):
            env.original.write_bytes(b"original changed during download")
        return result

    monkeypatch.setattr(discard.api, "read_remote", changing)
    with pytest.raises(ValueError, match="changed during preservation"):
        preview(env)
    assert not env.trashed


def test_bound_undispatched_attempt_is_preserved_without_private_publication(retained):
    env = retained
    binding = {
        "protocol": storage.PROTOCOL,
        "batch_id": env.batch_id,
        "identity_hash": storage.get(env.store, env.batch_id)["compatibility_hash"],
        "task_id": env.member["task_id"],
        "job_id": env.job["id"],
        "attempt": 1,
        "inputs_sha256": None,
        "inputs_size": None,
        "state": "not_dispatched",
        "error_code": "not_dispatched",
        "outputs": {},
    }
    env.remote[env.remote_folder + "/tasks/" + env.member["task_id"] + "/outcome.json"] = (
        batches.json_bytes(binding)
    )
    plan = preview(env)
    assert not plan["members"][0]["partial_stages"]
    assert confirm(env, plan)["cleanup_state"] == "trashed"


def test_mixed_published_failure_and_never_dispatched_member_share_explicit_disposition(
    retained, library
):
    env = retained
    env.publish(outputs={"transcription.json": b'{"segments":[]}'})
    other = enqueue(library)
    owner = storage.for_job(env.store, other)
    member = owner["members"][0]
    params = dict(other["params"], runtime_batch_id=env.batch_id)
    env.store.execute(
        "UPDATE jobs SET params=?,state='interrupted' WHERE id=?", (json.dumps(params), other["id"])
    )
    env.store.execute(
        "UPDATE runtime_batch_members SET batch_id=?,ordinal=2,state='interrupted' WHERE job_id=?",
        (env.batch_id, other["id"]),
    )
    env.store.execute("DELETE FROM runtime_batch_runs WHERE id=?", (owner["id"],))
    base = env.store.directory(other["recording_id"])
    base.mkdir(parents=True, exist_ok=True)
    (base / "original.wav").write_bytes(b"second synthetic original")
    binding = {
        "protocol": storage.PROTOCOL,
        "batch_id": env.batch_id,
        "identity_hash": storage.get(env.store, env.batch_id)["compatibility_hash"],
        "task_id": member["task_id"],
        "job_id": other["id"],
        "attempt": 1,
        "inputs_sha256": None,
        "inputs_size": None,
        "state": "not_dispatched",
        "error_code": "not_dispatched",
        "outputs": {},
    }
    env.remote[env.remote_folder + "/tasks/" + member["task_id"] + "/outcome.json"] = (
        batches.json_bytes(binding)
    )
    plan = preview(env)
    assert plan["count"] == 2
    assert len(plan["missing_results"]) == 2
    assert [m["partial_stages"] for m in plan["members"]] == [1, 0]
    assert confirm(env, plan)["cleanup_state"] == "trashed"
    assert not storage.obligations(env.store)


@pytest.mark.parametrize(
    "invalid", ["accepted", "output_receipt", "output_bytes", "manifest_shape"]
)
def test_malformed_control_and_output_receipts_refuse_discard(retained, invalid):
    env = retained
    task, _ = env.publish(outputs={"transcription.json": b'{"segments":[]}'})
    folder = env.remote_folder + "/tasks/" + task
    if invalid == "accepted":
        value = json.loads(env.remote[folder + "/accepted.json"])
        value["job_id"] = new_id()
        env.remote[folder + "/accepted.json"] = batches.json_bytes(value)
    elif invalid == "output_receipt":
        value = json.loads(env.remote[folder + "/outcome.json"])
        value["outputs"]["transcription.json"] = None
        env.remote[folder + "/outcome.json"] = batches.json_bytes(value)
    elif invalid == "output_bytes":
        env.remote[folder + "/transcription.json"] = b"damaged stage"
    else:
        value = json.loads(env.remote[folder + "/inputs.json"])
        value["expected_outputs"] = {"unexpected": True}
        env.remote[folder + "/inputs.json"] = batches.json_bytes(value)
    with pytest.raises(ValueError):
        preview(env)
    assert not env.trashed
    assert not storage.get(env.store, env.batch_id)["journal"].get("discard_confirmed")


def test_private_discard_keeps_successful_sibling_in_result_trash(retained):
    env = retained
    # This saved result models a successfully collected member; visibility is independent.
    _, files = env.publish(
        state="complete",
        outputs={"transcription.json": b'{"segments":[]}', "alignment.json": b'{"passages":[]}'},
    )
    for name in files:
        (env.folder / name).write_bytes(
            env.remote[env.remote_folder + "/tasks/" + env.member["task_id"] + "/" + name]
        )
    receipt = {
        "batch_id": env.batch_id,
        "task_id": env.member["task_id"],
        "attempt": 1,
        "epoch": 0,
        "files": files,
    }
    result = env.store.add_result(
        env.job["recording_id"],
        normalize_segments([{"text": "saved", "start": 0, "end": 1}]),
        {},
        "saved",
        {},
        job_id=env.job["id"],
        runtime_receipt=receipt,
    )
    env.store.set_result_trashed(
        env.job["recording_id"],
        result["id"],
        env.store.recording(env.job["recording_id"])["revision"],
        trashed=True,
    )
    before = {
        t: env.store.all("SELECT * FROM " + t)
        for t in ("transcript_results", "result_versions", "result_trash")
    }
    plan = preview(env)
    assert plan["members"][0]["result_saved"] is True and not plan["missing_results"]
    assert confirm(env, plan)["cleanup_state"] == "trashed"
    assert {t: env.store.all("SELECT * FROM " + t) for t in before} == before
