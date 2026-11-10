"""Durable metadata acceptance and background-only source/producer admission (V142)."""

import copy
import json
import threading

import pytest

from cbs_transcriber import (
    enhancement_api,
    input_admission,
    original_inspection,
    providers,
    runtime_store,
)
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.runtime_compute import MIG_PRODUCT
from cbs_transcriber.store import Store, new_id, now


@pytest.fixture
def library(tmp_path, credentials):
    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic background admission")
    settings = providers.Settings(
        provider_type="custom",
        base_url="http://127.0.0.1:9000/v1",
        model="synthetic",
        capability="segments",
    )
    provider = providers.save_profile(store, credentials, "Synthetic", settings, remember=False)
    provider = providers.profile(store, provider["id"])
    rows = [store.create_recording(project["id"], name) for name in ("one.wav", "two.wav")]
    return store, project, provider, rows


def item(provider, recording):
    return {"recording_id": recording["id"], "choice": input_admission.choice(provider=provider)}


def test_v142_acceptance_is_metadata_only_and_atomic(library, monkeypatch):
    store, _, provider, rows = library
    monkeypatch.setattr(
        "cbs_transcriber.audio_inputs.freeze", lambda *a, **k: pytest.fail("HTTP inspected source")
    )
    with store.library_lock():
        jobs = input_admission.accept(store, [item(provider, row) for row in rows])
        assert len(jobs) == 2
        assert [job["recording_id"] for job in jobs] == [row["id"] for row in rows]
        assert all(job["state"] == "queued" and "input_intent" in job["params"] for job in jobs)
        assert store.claim_job("transcribe") is None
        assert input_admission.claim(store) is None
    assert len(store.all("SELECT id FROM jobs WHERE kind='source-inspect'")) == 2
    assert not store.all("SELECT id FROM native_batch_runs")
    assert not store.all("SELECT id FROM runtime_batch_runs")


def test_v142_acceptance_requires_lease_and_failed_transaction_leaves_no_inspector(library):
    store, _, provider, rows = library
    with pytest.raises(RuntimeError, match="lease"):
        input_admission.accept(store, [item(provider, rows[0])])
    with store.library_lock(), pytest.raises(KeyError):
        input_admission.accept(
            store,
            [
                item(provider, rows[0]),
                {"recording_id": "a" * 32, "choice": input_admission.choice(provider=provider)},
            ],
        )
    assert not store.all("SELECT id FROM jobs")
    assert all(
        row["original_input"] is None for row in store.all("SELECT original_input FROM recordings")
    )


def test_v142_pending_cancel_is_epoch_bound_and_keeps_shared_inspection(library):
    store, _, provider, rows = library
    with store.library_lock():
        job = input_admission.accept(store, [item(provider, rows[0])])[0]
        before = job["params"]["input_intent"]
        result = input_admission.cancel(
            store, job["id"], epoch=before["epoch"], phase=before["phase"]
        )
        assert result["state"] == "cancelled"
        with pytest.raises(input_admission.StaleInput):
            input_admission.cancel(store, job["id"], epoch=before["epoch"], phase=before["phase"])
    inspectors = store.all("SELECT state FROM jobs WHERE kind='source-inspect'")
    assert inspectors == [{"state": "queued"}]


def test_v142_two_requests_share_one_inspector_but_reject_provider_duplicate(library):
    store, _, provider, rows = library
    barrier = threading.Barrier(3)
    outcomes = []

    def accept():
        barrier.wait()
        try:
            outcomes.append(input_admission.accept(store, [item(provider, rows[0])]))
        except ValueError as error:
            outcomes.append(error)

    with store.library_lock():
        threads = [threading.Thread(target=accept) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
    assert sum(isinstance(outcome, list) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, ValueError) for outcome in outcomes) == 1
    assert len(store.all("SELECT id FROM jobs WHERE kind='source-inspect'")) == 1


def inspect(store, row, wav_bytes):
    directory = store.directory(row["id"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "original.wav").write_bytes(wav_bytes)
    cache = original_inspection.ensure(store, row["id"])
    claim = original_inspection.claim(store, cache["inspection_job_id"])
    return original_inspection.run(store, claim)


def native_provider(store, credentials):
    value = providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="large-v3",
        language="da",
        capability="words",
        ucloud_transcriber={
            "project_id": "synthetic-project",
            "storage_path": "/synthetic-drive/inputs",
            "application": {"name": "transcriber/Batch", "version": "synthetic"},
            "product": {
                "id": "gpu-nvidia-b200-1-gpu",
                "category": "synthetic",
                "provider": "synthetic",
            },
            "input_parameter": "input_dir",
        },
    )
    identifier = new_id()
    store.execute(
        "INSERT INTO providers VALUES (?,?,?,?)",
        (identifier, "Synthetic native", value.model_dump_json(), now()),
    )
    credentials.set(providers.credential_scope(value), "synthetic-token", False)
    return providers.profile(store, identifier)


def test_v142_ready_api_materializes_same_id_while_other_source_is_pending(
    library, credentials, wav_bytes
):
    store, _, provider, rows = library
    with store.library_lock():
        accepted = input_admission.accept(store, [item(provider, row) for row in rows])
        receipt = inspect(store, rows[0], wav_bytes)["receipt"]
        token = input_admission.claim(store)
        assert [member["id"] for member in token["jobs"]] == [accepted[0]["id"]]
        result = input_admission.materialize(
            store, token, input_admission.prepare(store, credentials, token)
        )
        assert result[0]["id"] == accepted[0]["id"]
        assert result[0]["params"]["audio_input"] == receipt
        assert "input_intent" not in result[0]["params"]
        assert (
            result[0]["params"]["input_acceptance"]["request_id"]
            == accepted[0]["params"]["input_intent"]["request_id"]
        )
        assert store.claim_job("transcribe")["id"] == accepted[0]["id"]
        assert input_admission.claim(store) is None
    assert store.job(accepted[1]["id"])["params"]["input_intent"]["phase"] == "waiting"


def test_v142_native_selection_waits_then_materializes_one_all_selected_owner(
    library, credentials, wav_bytes
):
    store, _, _, rows = library
    provider = native_provider(store, credentials)
    with store.library_lock():
        accepted = input_admission.accept(store, [item(provider, row) for row in rows])
        inspect(store, rows[0], wav_bytes)
        assert input_admission.claim(store) is None
        assert not store.all("SELECT id FROM native_batch_runs")

        inspect(store, rows[1], wav_bytes)
        token = input_admission.claim(store)
        assert {member["id"] for member in token["jobs"]} == {job["id"] for job in accepted}
        result = input_admission.materialize(
            store, token, input_admission.prepare(store, credentials, token)
        )
    batches = store.all("SELECT * FROM native_batch_runs")
    assert len(batches) == 1
    assert {member["job_id"] for member in json.loads(batches[0]["members"])} == {
        job["id"] for job in accepted
    }
    assert {job["params"]["native_batch_id"] for job in result} == {batches[0]["id"]}


def test_v142_native_cancelled_member_does_not_require_its_uninspected_source(
    library, credentials, wav_bytes
):
    store, _, _, rows = library
    provider = native_provider(store, credentials)
    with store.library_lock():
        accepted = input_admission.accept(store, [item(provider, row) for row in rows])
        intent = accepted[1]["params"]["input_intent"]
        input_admission.cancel(
            store, accepted[1]["id"], epoch=intent["epoch"], phase=intent["phase"]
        )
        inspect(store, rows[0], wav_bytes)
        token = input_admission.claim(store)
        result = input_admission.materialize(
            store, token, input_admission.prepare(store, credentials, token)
        )
    assert {job["id"]: job["state"] for job in result} == {
        accepted[0]["id"]: "queued",
        accepted[1]["id"]: "cancelled",
    }
    assert not store.all(
        "SELECT id FROM native_batch_runs"
    )  # One survivor uses one singleton allocation.
    assert not input_admission.obligations(store)
    assert original_inspection.read(store, rows[1]["id"])["state"] == "pending"


@pytest.mark.parametrize("cancel_first", [True, False])
def test_v142_cancel_materialization_race_never_escalates_to_owner_stop(
    library, credentials, wav_bytes, cancel_first
):
    store, _, provider, rows = library
    with store.library_lock():
        accepted = input_admission.accept(store, [item(provider, rows[0])])[0]
        inspect(store, rows[0], wav_bytes)
        token = input_admission.claim(store)
        phase = store.job(accepted["id"])["params"]["input_intent"]
        outcomes = input_admission.prepare(store, credentials, token)
        if cancel_first:
            cancelled = input_admission.cancel(
                store, accepted["id"], epoch=phase["epoch"], phase=phase["phase"]
            )
            assert cancelled["state"] == "running"
            result = input_admission.materialize(store, token, outcomes)[0]
            assert result["state"] == "cancelled" and "input_intent" in result["params"]
        else:
            before = input_admission.materialize(store, token, outcomes)[0]
            with pytest.raises(input_admission.StaleInput):
                input_admission.cancel(
                    store, accepted["id"], epoch=phase["epoch"], phase=phase["phase"]
                )
            assert store.job(accepted["id"]) == before


def test_v142_restart_fences_local_preflight_without_replaying_executable_job(
    library, credentials, wav_bytes
):
    store, _, provider, rows = library
    with store.library_lock():
        accepted = input_admission.accept(store, [item(provider, row) for row in rows])
        for row in rows:
            inspect(store, row, wav_bytes)
        first = input_admission.claim(store)
        input_admission.materialize(
            store, first, input_admission.prepare(store, credentials, first)
        )
        store.claim_job("transcribe")
        second = input_admission.claim(store)
        before = copy.deepcopy(store.job(accepted[1]["id"])["params"]["input_intent"]["accepted"])
        assert input_admission.recover(store) == 1
        store.recover_jobs()
        assert store.job(accepted[0]["id"])["state"] == "interrupted"
        assert store.job(accepted[1]["id"])["state"] == "queued"
        assert store.job(accepted[1]["id"])["params"]["input_intent"]["accepted"] == before
        with pytest.raises(input_admission.StaleInput):
            input_admission.materialize(store, second, {})


def test_v142_current_protected_revocation_blocks_commit_but_disable_only_retains_acceptance(
    library, credentials, wav_bytes
):
    store, project, provider, rows = library
    with store.library_lock():
        input_admission.accept(store, [item(provider, rows[0])])
        inspect(store, rows[0], wav_bytes)
        token = input_admission.claim(store)
        outcomes = input_admission.prepare(store, credentials, token)
        value = providers.Settings.model_validate(provider["settings"]).model_copy(
            update={"enabled": False}
        )
        store.execute(
            "UPDATE providers SET value=? WHERE id=?", (value.model_dump_json(), provider["id"])
        )
        result = input_admission.materialize(store, token, outcomes)[0]
        assert result["state"] == "queued" and result["params"]["settings"]["enabled"] is True
    # Policy revocation is checked again independently of the frozen approval/visibility flags.
    with store.library_lock():
        store.execute(
            "UPDATE providers SET value=? WHERE id=?",
            (
                providers.Settings.model_validate(provider["settings"]).model_dump_json(),
                provider["id"],
            ),
        )
        input_admission.accept(store, [item(provider, rows[1])])
        inspect(store, rows[1], wav_bytes)
        token = input_admission.claim(store)
        outcomes = input_admission.prepare(store, credentials, token)
        store.execute("UPDATE projects SET classification='protected' WHERE id=?", (project["id"],))
        result = input_admission.materialize(store, token, outcomes)[0]
    assert result["state"] == "failed"
    assert result["params"]["input_intent"]["error"]["code"] == "policy_denied"


def test_v142_shutdown_after_completed_hash_keeps_anchor_before_restart(
    library, wav_bytes, monkeypatch
):
    store, _, provider, rows = library
    directory = store.directory(rows[0]["id"])
    directory.mkdir(parents=True)
    (directory / "original.wav").write_bytes(wav_bytes)
    hashing_finished = threading.Event()
    real_hash = original_inspection.audio_inputs._hash

    def hash_then_stop(*args, **kwargs):
        identity = real_hash(*args, **kwargs)
        hashing_finished.set()
        return identity

    class StopGuard:
        def is_set(self):
            if hashing_finished.is_set():
                raise input_admission.PreparationStopped()
            return False

    monkeypatch.setattr(original_inspection.audio_inputs, "_hash", hash_then_stop)
    with store.library_lock():
        input_admission.accept(store, [item(provider, rows[0])])
        claim = original_inspection.claim(store)
        with pytest.raises(input_admission.PreparationStopped):
            original_inspection.run(store, claim, StopGuard())
        cache = original_inspection.read(store, rows[0]["id"])
        assert cache["state"] == "running" and cache["anchor"] is not None


def runtime_plan():
    return {
        "identity": {
            "protocol": runtime_store.PROTOCOL,
            "application": {"name": "Terminal", "version": "synthetic"},
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


def test_v142_runtime_ready_members_late_attach_to_real_shared_owner(
    library, credentials, wav_bytes, monkeypatch
):
    store, _, _, rows = library
    provider = native_provider(store, credentials)
    settings = providers.Settings.model_validate(provider["settings"]).model_copy(
        update={"model": "whisperx/large-v3"}
    )
    store.execute(
        "UPDATE providers SET value=? WHERE id=?", (settings.model_dump_json(), provider["id"])
    )
    provider = providers.profile(store, provider["id"])

    def freeze(_store, _credentials, selected, projects, options):
        return options, {
            "ucloud_api": {
                "provider_id": selected["id"],
                "settings": selected["settings"],
                "runtime_id": "synthetic-runtime",
                "runtime_product": dict(MIG_PRODUCT),
                "runtime_compute_policy": "mig-v1",
                "prepare_runtime": options.prepare_runtime,
                "batch_plan": runtime_plan(),
            }
        }

    monkeypatch.setattr(enhancement_api, "freeze_profile", freeze)
    monkeypatch.setattr(enhancement_api, "whisperx_ready", lambda *a: True)
    with store.library_lock():
        accepted = input_admission.accept(store, [item(provider, row) for row in rows])
        owners = []
        for row in rows:
            inspect(store, row, wav_bytes)
            token = input_admission.claim(store)
            result = input_admission.materialize(
                store, token, input_admission.prepare(store, credentials, token)
            )[0]
            owners.append(result["params"]["runtime_batch_id"])
    assert owners[0] == owners[1]
    assert len(store.all("SELECT id FROM runtime_batch_runs")) == 1
    members = store.all("SELECT job_id,recording_id FROM runtime_batch_members")
    assert {member["job_id"] for member in members} == {job["id"] for job in accepted}
    assert {member["recording_id"] for member in members} == {row["id"] for row in rows}


def test_v142_native_sql_attachment_failure_rolls_back_every_selected_row(
    library, credentials, wav_bytes
):
    import sqlite3

    store, _, _, rows = library
    provider = native_provider(store, credentials)
    with store.library_lock():
        accepted = input_admission.accept(store, [item(provider, row) for row in rows])
        for row in rows:
            inspect(store, row, wav_bytes)
        token = input_admission.claim(store)
        outcomes = input_admission.prepare(store, credentials, token)
        before = {
            job["id"]: store.one("SELECT * FROM jobs WHERE id=?", (job["id"],)) for job in accepted
        }
        store.execute(
            "CREATE TRIGGER refuse_native BEFORE INSERT ON native_batch_runs "
            "BEGIN SELECT RAISE(ABORT,'Synthetic native insertion'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="Synthetic"):
            input_admission.materialize(store, token, outcomes)
        assert {
            job["id"]: store.one("SELECT * FROM jobs WHERE id=?", (job["id"],)) for job in accepted
        } == before
        assert not store.all("SELECT id FROM native_batch_runs")


def test_v142_failed_group_without_owner_can_retry_without_reviving_siblings(
    library, credentials, wav_bytes
):
    store, _, _, rows = library
    provider = native_provider(store, credentials)
    with store.library_lock():
        accepted = input_admission.accept(store, [item(provider, row) for row in rows])
        for row in rows:
            inspect(store, row, wav_bytes)
        token = input_admission.claim(store)
        failed = input_admission.materialize(
            store, token, {job["id"]: {"error": "preparation_failed"} for job in accepted}
        )
        first = failed[0]["params"]["input_intent"]
        retried = input_admission.retry(
            store, failed[0]["id"], epoch=first["epoch"], phase=first["phase"]
        )
        assert retried["state"] == "queued"
        assert store.job(failed[1]["id"])["state"] == "failed"
        token = input_admission.claim(store)
        result = input_admission.materialize(
            store, token, input_admission.prepare(store, credentials, token)
        )
    assert [job["state"] for job in result] == ["queued", "failed"]
    assert not store.all("SELECT id FROM native_batch_runs")


def test_v142_corrupt_inspector_does_not_starve_healthy_source_worker(
    library, credentials, wav_bytes, monkeypatch
):
    store, _, provider, rows = library
    complete = threading.Event()
    original_run = original_inspection.run
    with store.library_lock():
        input_admission.accept(store, [item(provider, row) for row in rows])
        directory = store.directory(rows[1]["id"])
        directory.mkdir(parents=True)
        (directory / "original.wav").write_bytes(wav_bytes)
        corrupt = original_inspection.read(store, rows[0]["id"])
        corrupt["version"] = 99
        raw = json.dumps(corrupt)
        store.execute("UPDATE recordings SET original_input=? WHERE id=?", (raw, rows[0]["id"]))

        def observed(*args, **kwargs):
            result = original_run(*args, **kwargs)
            if result and result["state"] == "ready":
                complete.set()
            return result

        monkeypatch.setattr(original_inspection, "run", observed)
        worker = JobQueue(store, credentials, kind="source-inspect")
        worker._start()
        try:
            assert complete.wait(10)
        finally:
            worker.stop()
    assert original_inspection.read(store, rows[1]["id"])["state"] == "ready"
    assert (
        store.one("SELECT original_input FROM recordings WHERE id=?", (rows[0]["id"],))[
            "original_input"
        ]
        == raw
    )


def test_v142_startup_recovers_healthy_inspector_while_retaining_corrupt_sibling(
    library, credentials, monkeypatch
):
    store, _, provider, rows = library
    with store.library_lock():
        input_admission.accept(store, [item(provider, row) for row in rows])
        for row in rows:
            original_inspection.claim(
                store, original_inspection.read(store, row["id"])["inspection_job_id"]
            )
        corrupt = original_inspection.read(store, rows[0]["id"])
        corrupt["version"] = 99
        raw = json.dumps(corrupt)
        store.execute("UPDATE recordings SET original_input=? WHERE id=?", (raw, rows[0]["id"]))
        before = store.job(corrupt["inspection_job_id"])
        monkeypatch.setattr(JobQueue, "_start", lambda self: None)
        JobQueue(store, credentials).start()
    assert original_inspection.read(store, rows[1]["id"])["state"] == "pending"
    assert store.job(corrupt["inspection_job_id"]) == before


def test_v142_malformed_inspector_json_does_not_block_ready_admission(
    library, credentials, wav_bytes
):
    store, _, provider, rows = library
    with store.library_lock():
        accepted = input_admission.accept(store, [item(provider, row) for row in rows])
        bad = original_inspection.read(store, rows[0]["id"])["inspection_job_id"]
        store.execute("UPDATE jobs SET params='{' WHERE id=?", (bad,))
        inspect(store, rows[1], wav_bytes)
        token = input_admission.claim(store)
        assert [member["id"] for member in token["jobs"]] == [accepted[1]["id"]]
        assert input_admission.obligations(store) == []
    assert store.one("SELECT params FROM jobs WHERE id=?", (bad,))["params"] == "{"


def test_v142_malformed_inspector_json_does_not_block_startup_recovery(
    library, credentials, monkeypatch
):
    store, _, provider, rows = library
    with store.library_lock():
        input_admission.accept(store, [item(provider, row) for row in rows])
        claims = [original_inspection.claim(store) for _ in rows]
        bad = claims[0]["job_id"]
        store.execute("UPDATE jobs SET params='{' WHERE id=?", (bad,))
        monkeypatch.setattr(JobQueue, "_start", lambda self: None)
        JobQueue(store, credentials).start()
    assert original_inspection.read(store, claims[1]["recording_id"])["state"] == "pending"
    assert store.one("SELECT params,state FROM jobs WHERE id=?", (bad,)) == {
        "params": "{",
        "state": "running",
    }


def test_v142_cancel_after_final_hash_validation_retains_completed_identity(
    library, wav_bytes, monkeypatch
):
    store, _, provider, rows = library
    directory = store.directory(rows[0]["id"])
    directory.mkdir(parents=True)
    (directory / "original.wav").write_bytes(wav_bytes)
    stopped, calls = threading.Event(), []
    owned = original_inspection.audio_inputs._owned

    def checked_then_stop(*args, **kwargs):
        result = owned(*args, **kwargs)
        calls.append(1)
        if len(calls) == 3:  # _source, hash open guard, final stable path guard.
            stopped.set()
        return result

    class StopGuard:
        def is_set(self):
            if stopped.is_set():
                raise input_admission.PreparationStopped()
            return False

    monkeypatch.setattr(original_inspection.audio_inputs, "_owned", checked_then_stop)
    with store.library_lock():
        input_admission.accept(store, [item(provider, rows[0])])
        with pytest.raises(input_admission.PreparationStopped):
            original_inspection.run(store, original_inspection.claim(store), StopGuard())
        cache = original_inspection.read(store, rows[0]["id"])
        assert cache["anchor"] is not None


def test_v142_unknown_active_producer_blocks_same_recording_without_blocking_other_files(library):
    store, _, provider, rows = library
    prior = store.add_job(rows[0]["id"], "transcribe", {"settings": provider["settings"]})
    store.execute("UPDATE jobs SET params='{' WHERE id=?", (prior["id"],))
    with store.library_lock():
        with pytest.raises(input_admission.AdmissionError, match="active"):
            input_admission.accept(store, [item(provider, rows[0])])
        healthy = input_admission.accept(store, [item(provider, rows[1])])
    assert len(healthy) == 1
    assert store.one("SELECT params FROM jobs WHERE id=?", (prior["id"],))["params"] == "{"


def test_v142_generic_checkpoint_cannot_drop_or_change_acceptance_receipt(
    library, credentials, wav_bytes
):
    store, _, provider, rows = library
    with store.library_lock():
        input_admission.accept(store, [item(provider, rows[0])])
        inspect(store, rows[0], wav_bytes)
        token = input_admission.claim(store)
        job = input_admission.materialize(
            store, token, input_admission.prepare(store, credentials, token)
        )[0]
    before = store.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
    # ADR 0133 Working authority refuses first; without it (a pre-schema-18 accepted job),
    # the acceptance-receipt guard itself still refuses the same changes.
    for messages in (
        ("Working admission differs from its receipt", "Frozen Working evidence"),
        ("acceptance receipt", "acceptance receipt"),
    ):
        for remove, message in zip((True, False), messages, strict=True):
            changed = copy.deepcopy(job["params"])
            if remove:
                changed.pop("input_acceptance")
            else:
                changed["input_acceptance"]["request_id"] = "a" * 32
            with pytest.raises(ValueError, match=message):
                store.update_job(job["id"], params=changed)
            assert store.one("SELECT * FROM jobs WHERE id=?", (job["id"],)) == before
        store.execute("DELETE FROM working_job_authorities WHERE job_id=?", (job["id"],))


@pytest.mark.parametrize("damage", ["missing_child", "changed_child", "bounded_flag"])
def test_v142_prepared_outcome_cannot_drop_features_or_change_whole_scope(
    library, credentials, wav_bytes, monkeypatch, damage
):
    from cbs_transcriber import enhancement

    store, _, provider, rows = library
    monkeypatch.setattr(enhancement, "require_local_runtime", lambda options: None)
    with store.library_lock():
        accepted = input_admission.accept(
            store,
            [
                {
                    "recording_id": rows[0]["id"],
                    "choice": input_admission.choice(
                        provider=provider,
                        requested_features={"word_timing": True},
                        enhancement={"execution": "local"},
                    ),
                }
            ],
        )[0]
        inspect(store, rows[0], wav_bytes)
        token = input_admission.claim(store)
        outcomes = input_admission.prepare(store, credentials, token)
        params = outcomes[accepted["id"]]["params"]
        if damage == "missing_child":
            params.pop("followup")
        elif damage == "changed_child":
            params["followup"]["params"]["enhancement"]["language"] = "en"
        else:
            params["segment_ids"] = [1]
        before = store.one("SELECT * FROM jobs WHERE id=?", (accepted["id"],))
        with pytest.raises(input_admission.AdmissionError):
            input_admission.materialize(store, token, outcomes)
        assert store.one("SELECT * FROM jobs WHERE id=?", (accepted["id"],)) == before
        assert not store.all("SELECT id FROM native_batch_runs")


@pytest.mark.parametrize("invalid", [[], None, "opaque"])
def test_v142_non_object_job_roots_stay_quarantined_and_healthy_claim_continues(library, invalid):
    store, _, provider, rows = library
    bad = store.add_job(rows[0]["id"], "transcribe", {"settings": provider["settings"]})
    raw = json.dumps(invalid)
    store.execute("UPDATE jobs SET params=? WHERE id=?", (raw, bad["id"]))
    healthy = store.add_job(rows[1]["id"], "transcribe", {"settings": provider["settings"]})
    with store.library_lock():
        assert store.claim_job("transcribe")["id"] == healthy["id"]
        with pytest.raises(input_admission.AdmissionError, match="active"):
            input_admission.accept(store, [item(provider, rows[0])])
        store.recover_jobs()
    assert store.one("SELECT params,state FROM jobs WHERE id=?", (bad["id"],)) == {
        "params": raw,
        "state": "queued",
    }


@pytest.mark.parametrize("failure", [ValueError("Missing connection"), KeyError("missing")])
def test_v142_explicit_routing_preserves_non_policy_failures(monkeypatch, failure):
    def refuse(*args):
        raise failure

    monkeypatch.setattr(enhancement_api, "connection", refuse)
    with pytest.raises(type(failure)) as caught:
        input_admission.snapshot_routing(
            None, None, "project", input_admission.fresh_options({"execution": "ucloud"})
        )
    assert caught.value is failure


def test_v142_typed_followup_denial_keeps_auto_fallback_and_explicit_policy_status(monkeypatch):
    def refuse(*args):
        raise providers.ApprovalDenied("Synthetic Protected denial")

    monkeypatch.setattr(enhancement_api, "connection", refuse)
    rules = input_admission.routing.Rules()
    monkeypatch.setattr(input_admission.routing, "rules", lambda _: rules)
    with pytest.raises(input_admission.PolicyDenied, match="Synthetic Protected denial"):
        input_admission.snapshot_routing(
            None, None, "project", input_admission.fresh_options({"execution": "ucloud"})
        )
    assert input_admission.snapshot_routing(
        None, None, "project", input_admission.fresh_options({"execution": "auto"})
    ) == {"rules": rules.model_dump(), "connection": None}
