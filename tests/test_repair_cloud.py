"""API-only selected-clip repair; synthetic files and mocked execution only."""

import copy
import json
import os
import re
import shutil
import threading
from contextlib import nullcontext
from pathlib import PurePosixPath
from types import SimpleNamespace

import httpx
import pytest
import test_enhancement_api as api_fixtures
import test_repair_bundles as bundle_fixtures
from test_enhancement_api import SETUP

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import providers, repair_bundles, repair_cloud, repair_remote, runtime_compute
from cbs_transcriber.enhancement import EnhancementOptions
from cbs_transcriber.enhancement_worker import MODELS

REAL_READ_REMOTE = api.read_remote


class RepairUCloud(api_fixtures.FakeUCloud):
    def __init__(self, application, stage):
        super().__init__(application)
        self.stage = stage
        self.inference_calls = []
        self.pause_hook = None
        self.paused = False
        self.fail_index = None

    def step(self):
        if self.run is None or self.state != "RUNNING":
            return
        if self.paused:
            return
        if (
            self.phase() not in {"ready", "running"}
            or self.run + "/manifest.sha256" not in self.files
        ):
            return super().step()
        for line in self.files[self.run + "/manifest.sha256"].decode().splitlines():
            expected, name = line.split("  ")
            assert repair_remote.digest(self.files[self.run + "/" + name]) == expected
        for name, raw in self.files.items():
            if name.startswith(self.run + "/"):
                target = self.stage.joinpath(*PurePosixPath(name).relative_to(self.run).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)

        cancel = threading.Event()

        def align(request, audio):
            self.inference_calls.append(request["unit_index"])
            if request["unit_index"] == self.fail_index:
                raise ValueError("synthetic unit failure")
            if self.pause_hook is not None:
                self.pause_hook()
                cancel.set()
            return fake_alignment(request, audio)

        status = repair_remote.process(self.stage, self.generation, runner=align, cancel=cancel)
        for path in (self.stage / "out").rglob("*.json"):
            self.files[self.run + "/" + path.relative_to(self.stage).as_posix()] = path.read_bytes()
        self.files[self.run + "/remote-status.json"] = (
            self.stage / "remote-status.json"
        ).read_bytes()
        if status["unstarted"]:
            self.paused = True
        self.write(
            "supervisor.json",
            {"phase": "running" if self.paused else "finished", "generation": self.generation},
        )
        self.state = self.end_state


def test_closed_repair_protocol_is_separate_from_full_asr():
    assert repair_cloud.PROTOCOL == repair_remote.PROTOCOL == "cbs-repair-cloud-v1"


@pytest.fixture
def study(tmp_path):
    return bundle_fixtures.study.__wrapped__(tmp_path)


@pytest.fixture
def execution(study, tmp_path, monkeypatch):
    job, clips = bundle_fixtures.prepared_job(study)
    store, recording, *_ = study
    if not job["params"].get("realign"):
        # This older fixture adds the timing flag only after enqueue. Model it as a
        # legacy frozen job rather than a newly authorized result-producing job.
        store.execute("DELETE FROM working_job_authorities WHERE job_id=?", (job["id"],))
    code = {name: repair_cloud.code_bytes(path) for name, path in repair_cloud.code_files().items()}
    repo, revision = MODELS["da"]
    runtime = {
        "runtime_id": api.runtime_id(),
        "worker_sha256": repair_remote.digest(code["enhancement_worker.py"]),
        "language": "da",
        "alignment_model": {"repo": repo, "revision": revision},
    }
    settings = providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="large-v3",
        ucloud_transcriber=SETUP,
    ).model_dump()
    params = job["params"] | {
        "realign": True,
        "repair_runtime": runtime,
        "repair_cloud": {
            "protocol": repair_cloud.PROTOCOL,
            "bundle_generation": 1,
            "code": {name: repair_remote.receipt(data) for name, data in code.items()},
        },
        "ucloud_api": {
            "provider_id": "synthetic",
            "settings": settings,
            "runtime_id": api.runtime_id(),
            "prepare_runtime": False,
            "runtime_product": runtime_compute.MIG_PRODUCT,
            "runtime_compute_policy": runtime_compute.POLICY,
        },
    }
    params["enhancement"]["timing_policy"] = "retain-estimates"
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job["id"]))
    job = store.job(job["id"])
    bundle = repair_bundles.build_bundle(store, job["id"], clips, runtime)
    remote = tmp_path / "cloud"
    shutil.copytree(bundle["directory"], remote / "bundle")
    for name, data in code.items():
        (remote / name).write_bytes(data)
    control = {
        "protocol": repair_cloud.PROTOCOL,
        "generation": job["id"],
        "bundle_sha256": bundle["sha256"],
        "code": params["repair_cloud"]["code"],
    }
    (remote / "repair-run.json").write_bytes(repair_remote.encoded(control))
    worker = SimpleNamespace(
        store=store,
        credentials=SimpleNamespace(get=lambda *_: "synthetic-secret"),
        cancel_event=threading.Event(),
        stopping=threading.Event(),
    )
    controller = repair_cloud.Run(
        worker,
        job,
        store.recording(recording["id"]),
        bundle,
        EnhancementOptions.model_validate(params["enhancement"]),
    )
    controller.client = object()

    def download(client, server, path, limit=api.MAX_STATUS_BYTES):
        assert client is controller.client and server == "https://cloud.invalid"
        suffix = PurePosixPath(path).relative_to(controller.remote)
        candidate = remote.joinpath(*suffix.parts)
        if not candidate.exists():
            return None
        raw = candidate.read_bytes()
        if len(raw) > limit:
            raise ValueError("Oversized mocked download")
        return raw

    monkeypatch.setattr(api, "read_remote", download)
    return SimpleNamespace(
        store=store,
        job=job,
        recording=recording,
        worker=worker,
        clips=clips,
        bundle=bundle,
        remote=remote,
        control=control,
        controller=controller,
    )


def fake_alignment(request, audio):
    assert audio.is_file()
    duration = request["audio"]["frames"] / 16000
    tokens = list(re.finditer(r"\S+", request["text"]))
    return [
        {
            "word": token.group(),
            "start": duration * i / len(tokens),
            "end": duration * (i + 0.8) / len(tokens),
            "score": 0.9,
        }
        for i, token in enumerate(tokens)
    ]


def terminal(execution, state="SUCCESS"):
    execution.controller.checkpoint(
        job_id="synthetic-remote-job", owned_folder=execution.controller.remote, remote_state=state
    )


def test_remote_runs_selected_clips_once_and_reuses_every_output_receipt(execution):
    calls = []

    def align(request, audio):
        calls.append(request["unit_index"])
        return fake_alignment(request, audio)

    first = repair_remote.process(execution.remote, execution.job["id"], runner=align)
    before = {
        str(p.relative_to(execution.remote)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in (execution.remote / "out").rglob("*")
        if p.is_file()
    }
    second = repair_remote.process(execution.remote, execution.job["id"], runner=align)
    assert calls == [0, 1]
    assert first == second and first["completed"] == 2
    assert before == {
        str(p.relative_to(execution.remote)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in (execution.remote / "out").rglob("*")
        if p.is_file()
    }


def test_completed_controller_recovery_does_not_load_inference_runtime(execution, monkeypatch):
    repair_remote.process(execution.remote, execution.job["id"], runner=fake_alignment)
    monkeypatch.setattr(
        repair_remote,
        "load_worker",
        lambda *_: pytest.fail("checkpoint recovery must not load inference"),
    )
    assert repair_remote.process(execution.remote, execution.job["id"])["completed"] == 2


def test_remote_recovery_checks_the_exact_started_receipt(execution):
    repair_remote.process(execution.remote, execution.job["id"], runner=fake_alignment)
    (execution.remote / "out/00000/started.json").write_bytes(b'{"format":"forged"}')
    with pytest.raises(ValueError):
        repair_remote.process(
            execution.remote, execution.job["id"], runner=lambda *_: pytest.fail("must not rerun")
        )


def test_cleanup_requires_every_units_terminal_evidence(execution, monkeypatch):
    terminal(execution, "CANCELLED")
    job = execution.store.job(execution.job["id"])
    job["params"]["ucloud_run"]["repair"] = {
        "protocol": repair_cloud.PROTOCOL,
        "bundle_sha256": execution.bundle["sha256"],
        "terminal_harvest": True,
        "units": {},
    }
    monkeypatch.setattr(
        api, "retry_cleanup", lambda *_: pytest.fail("must not delete incomplete proof")
    )
    with pytest.raises(ValueError):
        repair_cloud.retry_cleanup(execution.store, execution.worker.credentials, job)


def test_terminal_collection_publishes_timing_once_without_changing_reviewed_result(execution):
    repair_remote.process(execution.remote, execution.job["id"], runner=fake_alignment)
    terminal(execution)
    before = execution.store.recording(execution.recording["id"])
    execution.controller.harvest()
    execution.controller.publish()
    first = execution.store.job(execution.job["id"])
    proof = repair_cloud.validate_local_outputs(execution.store, first)
    assert set(proof["passages"]) == {"1", "4"}
    assert proof["passages"]["1"]["words"][0]["start"] == 2
    assert proof["passages"]["4"]["words"][0]["start"] == 9
    assert execution.store.recording(execution.recording["id"]) == before
    execution.controller.harvest()
    execution.controller.publish()
    assert (
        execution.store.job(execution.job["id"])["params"]["realign_output"]
        == first["params"]["realign_output"]
    )


def test_sibling_failure_keeps_completed_clip_and_does_not_retry_inference(execution):
    calls = []

    def fail_one(request, audio):
        calls.append(request["unit_index"])
        if request["unit_index"] == 1:
            raise ValueError("PRIVATE_RUNTIME_TEXT")
        return fake_alignment(request, audio)

    status = repair_remote.process(execution.remote, execution.job["id"], runner=fail_one)
    assert status["completed"] == 1 and status["failed"] == 1
    repair_remote.process(execution.remote, execution.job["id"], runner=fail_one)
    assert calls == [0, 1]
    terminal(execution, "FAILURE")
    execution.controller.harvest()
    execution.controller.publish()
    job = execution.store.job(execution.job["id"])
    assert job["params"]["ucloud_run"]["repair"]["unresolved_ids"] == ["4"]
    assert set(repair_cloud.validate_local_outputs(execution.store, job)["passages"]) == {"1"}
    assert b"PRIVATE_RUNTIME_" not in b"".join(
        p.read_bytes() for p in (execution.remote / "out").rglob("*.json")
    )


def test_started_without_output_is_uncertain_and_never_replayed(execution):
    control, manifest = repair_remote.load(execution.remote, execution.job["id"])
    out = execution.remote / "out/00000"
    out.mkdir(parents=True)
    ack = repair_remote.accepted(manifest, 0)
    repair_remote.write(out / "accepted.json", ack, execution.remote)
    repair_remote.write(
        out / "started.json",
        {
            "format": repair_remote.STARTED_FORMAT,
            "accepted": repair_remote.receipt(repair_remote.encoded(ack)),
        },
        execution.remote,
    )
    calls = []

    def align(request, audio):
        calls.append(request["unit_index"])
        return fake_alignment(request, audio)

    status = repair_remote.process(execution.remote, execution.job["id"], runner=align)
    assert status["uncertain"] == 1 and calls == [1]
    terminal(execution, "CANCELLED")
    execution.controller.harvest()
    execution.controller.publish()
    with pytest.raises(ValueError, match="Recover"):
        repair_cloud.retry_cleanup(
            execution.store, execution.worker.credentials, execution.store.job(execution.job["id"])
        )


def test_valid_output_without_remote_receipt_is_recovered_without_inference(execution):
    repair_remote.process(execution.remote, execution.job["id"], runner=fake_alignment)
    (execution.remote / "out/00000/receipt.json").unlink()
    terminal(execution, "CANCELLED")
    execution.controller.harvest()
    execution.controller.publish()
    proof = json.loads(
        (execution.controller.folder / "repair-results/00000/local-receipt.json").read_bytes()
    )
    assert proof["origin"] == "terminal_recovery"
    assert repair_cloud.validate_local_outputs(
        execution.store, execution.store.job(execution.job["id"])
    )["published_units"] == [0, 1]


def test_complete_unpublished_output_is_preserved_and_recovered_after_stop(execution):
    repair_remote.process(execution.remote, execution.job["id"], runner=fake_alignment)
    directory = execution.remote / "out/00000"
    (directory / "output.json").rename(directory / "output.json.pending")
    (directory / "receipt.json").unlink()
    terminal(execution, "CANCELLED")
    execution.controller.harvest()
    execution.controller.publish()
    job = execution.store.job(execution.job["id"])
    assert repair_cloud.validate_local_outputs(execution.store, job)["published_units"] == [0, 1]
    proof = json.loads(
        (execution.controller.folder / "repair-results/00000/local-receipt.json").read_bytes()
    )
    assert proof["origin"] == "pending_recovery"


def test_partial_download_retry_reuses_durable_unit_without_inference(execution, monkeypatch):
    repair_remote.process(execution.remote, execution.job["id"], runner=fake_alignment)
    terminal(execution)
    download = api.read_remote
    calls = []

    def interrupted(client, server, path, limit):
        calls.append(path)
        if path.endswith("out/00001/accepted.json"):
            raise api.Transient("synthetic interruption")
        return download(client, server, path, limit)

    monkeypatch.setattr(api, "read_remote", interrupted)
    with pytest.raises(api.Transient):
        execution.controller.harvest()
    local = execution.controller.folder / "repair-results/00000/output.json"
    before = (local.read_bytes(), local.stat().st_mtime_ns)
    monkeypatch.setattr(api, "read_remote", download)
    execution.controller.harvest()
    execution.controller.publish()
    assert (local.read_bytes(), local.stat().st_mtime_ns) == before
    assert len([p for p in calls if p.endswith("out/00000/output.json")]) == 1


def test_cancel_between_units_preserves_complete_output_and_reports_unstarted(execution):
    cancel = threading.Event()

    def align(request, audio):
        cancel.set()
        return fake_alignment(request, audio)

    status = repair_remote.process(
        execution.remote, execution.job["id"], runner=align, cancel=cancel
    )
    assert status["completed"] == 1 and status["unstarted"] == 1
    assert not (execution.remote / "out/00001/started.json").exists()


@pytest.mark.parametrize("field", ["audio", "request", "code", "extra"])
def test_remote_rejects_tampered_inputs_before_any_unit_is_accepted(execution, field):
    if field == "audio":
        path = execution.remote / "bundle/units/00000/audio.wav"
        path.write_bytes(path.read_bytes() + b"unexpected")
    elif field == "request":
        path = execution.remote / "bundle/units/00000/request.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif field == "code":
        (execution.remote / "enhancement_worker.py").write_text("raise Exception('changed')")
    else:
        (execution.remote / "bundle/private.txt").write_text("PRIVATE_TEXT")
    with pytest.raises(ValueError):
        repair_remote.process(
            execution.remote, execution.job["id"], runner=lambda *_: pytest.fail("must not execute")
        )
    assert not (execution.remote / "out").exists()


def test_fresh_freeze_uses_prepared_connection_and_mig_without_batch_plan(execution, monkeypatch):
    chosen = {
        key: execution.job["params"]["ucloud_api"][key] for key in ("provider_id", "settings")
    }
    monkeypatch.setattr(api, "connection", lambda *_: chosen)
    monkeypatch.setattr(providers, "credential_scope", lambda *_: "synthetic")
    monkeypatch.setattr(repair_cloud.native, "client_for", lambda *_: nullcontext(object()))
    monkeypatch.setattr(api, "stamped", lambda *_: True)
    monkeypatch.setattr(
        runtime_compute,
        "freeze",
        lambda *_: {
            "runtime_product": runtime_compute.MIG_PRODUCT,
            "runtime_compute_policy": runtime_compute.POLICY,
        },
    )
    options, params = repair_cloud.freeze(
        execution.store,
        execution.worker.credentials,
        [],
        EnhancementOptions(execution="ucloud", language="da"),
    )
    assert (
        options.device == "cuda"
        and params["ucloud_api"]["runtime_product"] == runtime_compute.MIG_PRODUCT
    )
    assert "batch_plan" not in params["ucloud_api"]
    assert params["ucloud_api"]["prepare_runtime"] is False
    monkeypatch.setattr(api, "stamped", lambda *_: False)
    with pytest.raises(ValueError, match="Prepare"):
        repair_cloud.freeze(execution.store, execution.worker.credentials, [], options)


def test_local_apply_proof_rejects_modified_timings_and_nonterminal_owner(execution):
    repair_remote.process(execution.remote, execution.job["id"], runner=fake_alignment)
    terminal(execution)
    execution.controller.harvest()
    execution.controller.publish()
    job = execution.store.job(execution.job["id"])
    changed = copy.deepcopy(job)
    changed["params"]["realign_output"]["passages"]["1"]["words"][0]["start"] += 0.1
    with pytest.raises(ValueError, match="matches|match"):
        repair_cloud.validate_local_outputs(execution.store, changed)
    changed = copy.deepcopy(job)
    changed["params"]["ucloud_run"]["remote_state"] = "RUNNING"
    with pytest.raises(ValueError, match="terminal|Terminal"):
        repair_cloud.validate_local_outputs(execution.store, changed)


def api_execution(execution, credentials, current_transcriber_application, tmp_path, monkeypatch):
    fake = RepairUCloud(current_transcriber_application, tmp_path / "simulated-worker")
    api_fixtures.runtime_ready(fake)
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *args, **kwargs: api_fixtures.REAL_CLIENT(
            *args, **kwargs, transport=httpx.MockTransport(fake.handle)
        ),
    )
    monkeypatch.setattr(api, "read_remote", REAL_READ_REMOTE)
    monkeypatch.setattr(api, "POLL_SECONDS", 0)
    monkeypatch.setattr(repair_cloud.native, "TRASH_CONFIRM_DELAYS", (0,))
    settings = providers.Settings.model_validate(execution.job["params"]["ucloud_api"]["settings"])
    profile = providers.save_profile(
        execution.store,
        credentials,
        "Synthetic UCloud",
        settings,
        api_key=api_fixtures.SECRET,
        remember=False,
    )
    params = execution.store.job(execution.job["id"])["params"]
    params["ucloud_api"]["provider_id"] = profile["id"]
    params["ucloud_api"]["settings"] = providers.profile(execution.store, profile["id"])["settings"]
    execution.store.execute(
        "UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), execution.job["id"])
    )
    execution.worker.credentials = credentials
    return fake


def test_mocked_api_allocates_one_mig_uploads_only_selected_clips_and_cleans_after_save(
    execution, credentials, current_transcriber_application, tmp_path, monkeypatch
):
    fake = api_execution(
        execution, credentials, current_transcriber_application, tmp_path, monkeypatch
    )
    report = repair_cloud.run(
        execution.worker,
        execution.store.job(execution.job["id"]),
        execution.store.recording(execution.recording["id"]),
    )
    assert (
        len(fake.submissions) == 1 and fake.submissions[0]["product"] == runtime_compute.MIG_PRODUCT
    )
    assert fake.submissions[0]["sshEnabled"] is False
    assert fake.inference_calls == [0, 1]
    assert report["counts"]["completed"] == 2
    assert fake.phase_at_audio == "ready"
    assert fake.trashed == [fake.run]
    assert all("original." not in path for path in fake.uploaded)
    assert [path for path in fake.uploaded if path.endswith("/audio.wav")] == [
        fake.run + f"/bundle/units/{index:05d}/audio.wav" for index in range(2)
    ]
    saved = execution.store.job(execution.job["id"])
    assert repair_cloud.validate_local_outputs(execution.store, saved)["published_units"] == [0, 1]
    assert all(api_fixtures.SECRET.encode() not in data for data in fake.files.values())


@pytest.mark.parametrize("shutdown", [False, True])
def test_api_stop_harvests_partial_results_but_shutdown_preserves_remote_work(
    execution, credentials, current_transcriber_application, tmp_path, monkeypatch, shutdown
):
    from cbs_transcriber.media import Cancelled

    fake = api_execution(
        execution, credentials, current_transcriber_application, tmp_path, monkeypatch
    )

    def pause():
        if shutdown:
            execution.worker.stopping.set()
        execution.worker.cancel_event.set()

    fake.pause_hook = pause
    with pytest.raises(Cancelled):
        repair_cloud.run(
            execution.worker,
            execution.store.job(execution.job["id"]),
            execution.store.recording(execution.recording["id"]),
        )
    saved = execution.store.job(execution.job["id"])
    assert len(fake.submissions) == 1 and fake.inference_calls == [0]
    if shutdown:
        assert fake.terminations == [] and fake.trashed == []
        assert saved["params"]["ucloud_run"].get("repair", {}).get("published") is not True
        execution.worker.stopping.clear()
        execution.worker.cancel_event.clear()
        fake.pause_hook, fake.paused = None, False
        report = repair_cloud.run(
            execution.worker, saved, execution.store.recording(execution.recording["id"])
        )
        assert report["counts"]["completed"] == 2 and fake.inference_calls == [0, 1]
        assert len(fake.submissions) == 1
    else:
        assert fake.terminations == ["remote-enhancement"]
        assert saved["params"]["ucloud_run"]["repair"]["counts"]["unstarted"] == 1
        assert repair_cloud.validate_local_outputs(execution.store, saved)["published_units"] == [0]
        assert fake.trashed == [fake.run]


def test_partial_api_output_is_saved_but_never_reported_as_complete(
    execution, credentials, current_transcriber_application, tmp_path, monkeypatch
):
    fake = api_execution(
        execution, credentials, current_transcriber_application, tmp_path, monkeypatch
    )
    fake.fail_index = 1
    with pytest.raises(ValueError, match="saved|Saved"):
        repair_cloud.run(
            execution.worker,
            execution.store.job(execution.job["id"]),
            execution.store.recording(execution.recording["id"]),
        )
    job = execution.store.job(execution.job["id"])
    assert len(fake.submissions) == 1
    assert repair_cloud.validate_local_outputs(execution.store, job)["published_units"] == [0]
    assert job["params"]["ucloud_run"]["repair"]["counts"]["failed"] == 1


def test_publish_merges_current_remote_receipts_under_one_writer(execution, monkeypatch):
    repair_remote.process(execution.remote, execution.job["id"], runner=fake_alignment)
    terminal(execution)
    execution.controller.harvest()
    get_job = execution.store.job
    raced = False

    def race(identifier):
        nonlocal raced
        value = get_job(identifier)
        if not raced:
            raced = True
            params = copy.deepcopy(value["params"])
            params["ucloud_run"]["cleanup_error"] = "newer concurrent observation"
            execution.store.execute(
                "UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), identifier)
            )
        return value

    monkeypatch.setattr(execution.store, "job", race)
    execution.controller.publish()
    assert (
        get_job(execution.job["id"])["params"]["ucloud_run"]["cleanup_error"]
        == "newer concurrent observation"
    )


def test_checkpoint_fences_changed_owner_even_for_status_only_updates(execution):
    terminal(execution)
    params = execution.store.job(execution.job["id"])["params"]
    params["ucloud_run"]["job_id"] = "different-owned-allocation"
    execution.store.execute(
        "UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), execution.job["id"])
    )
    with pytest.raises(ValueError, match="owner"):
        execution.controller.checkpoint(remote_state="RUNNING")
    assert (
        execution.store.job(execution.job["id"])["params"]["ucloud_run"]["remote_state"]
        == "SUCCESS"
    )


def test_checkpoint_cannot_reopen_terminal_capacity_or_cleanup(execution):
    terminal(execution)
    execution.controller.checkpoint(
        capacity_reserved=False, capacity_released_at="known-terminal", cleanup_state="trashed"
    )
    execution.controller.checkpoint(
        remote_state="RUNNING", capacity_reserved=True, cleanup_state="pending"
    )
    run = execution.store.job(execution.job["id"])["params"]["ucloud_run"]
    assert run["remote_state"] == "SUCCESS" and run["capacity_reserved"] is False
    assert run["cleanup_state"] == "trashed"


def test_alignment_adapter_freezes_no_asr_and_retains_low_score_estimates(execution):
    _, manifest = repair_remote.load(execution.remote, execution.job["id"])
    calls = []

    def process(work):
        task = json.loads((work / "request.json").read_bytes())
        calls.append(task)
        words = fake_alignment(manifest["units"][0]["_request"], work / "audio.wav")
        for word in words:
            word["score"] = 0.001
        (work / "alignment.json").write_bytes(
            repair_remote.encoded({"passages": [{"id": "0", "words": words}]})
        )

    worker = SimpleNamespace(needs_coarse=lambda *_: False, process_directory=process)
    words = repair_remote.align_unit(execution.remote, manifest, 0, worker)
    output = repair_remote.output_value(manifest, 0, words)
    timing = repair_cloud.project_outputs(execution.bundle, {0: output})
    assert timing["1"]["word_timing"] is True
    assert calls[0]["options"]["timing_policy"] == "retain-estimates"
    assert all(
        calls[0]["options"][key] is False
        for key in ("transcribe", "retranscribe", "diarize", "allow_downloads")
    )


def test_alignment_adapter_never_falls_back_to_coarse_asr(execution):
    _, manifest = repair_remote.load(execution.remote, execution.job["id"])
    worker = SimpleNamespace(
        needs_coarse=lambda *_: True, process_directory=lambda *_: pytest.fail("must not run ASR")
    )
    with pytest.raises(ValueError):
        repair_remote.align_unit(execution.remote, manifest, 0, worker)


def test_cleanup_only_retry_preserves_repair_publication_and_uses_fenced_writer(
    execution, credentials, current_transcriber_application, tmp_path, monkeypatch
):
    fake = api_execution(
        execution, credentials, current_transcriber_application, tmp_path, monkeypatch
    )
    trash = repair_cloud.native.trash_owned_folder
    monkeypatch.setattr(
        repair_cloud.native,
        "trash_owned_folder",
        lambda *_: (_ for _ in ()).throw(ValueError("synthetic cleanup failure")),
    )
    repair_cloud.run(
        execution.worker,
        execution.store.job(execution.job["id"]),
        execution.store.recording(execution.recording["id"]),
    )
    job = execution.store.job(execution.job["id"])
    expected = copy.deepcopy(job["params"]["realign_output"])
    assert job["params"]["ucloud_run"]["cleanup_state"] == "pending"
    monkeypatch.setattr(repair_cloud.native, "trash_owned_folder", trash)
    monkeypatch.setattr(
        api,
        "checkpoint_job",
        lambda *_args, **_kwargs: pytest.fail("must not use generic read/replace"),
    )
    assert repair_cloud.retry_cleanup(execution.store, credentials, job) == "trashed"
    fresh = execution.store.job(execution.job["id"])
    assert fresh["params"]["realign_output"] == expected
    assert fresh["params"]["ucloud_run"]["repair"]["published"] is True
    assert fake.trashed == [fake.run]


def test_status_update_never_truncates_a_linked_leftover_pending_file(execution, tmp_path):
    external = tmp_path / "outside-status"
    external.write_bytes(b"PRESERVE_EXTERNAL_BYTES")
    os.link(external, execution.remote / "remote-status.json.pending")
    repair_remote.write(
        execution.remote / "remote-status.json",
        {"state": "running"},
        execution.remote,
        immutable=False,
    )
    assert external.read_bytes() == b"PRESERVE_EXTERNAL_BYTES"


def test_background_preparation_extracts_only_frozen_clips_and_reuses_committed_bundle(
    execution, monkeypatch
):
    from cbs_transcriber import audio_inputs

    # Retain an independent fixture copy; never overwrite an existing generation.
    saved = execution.bundle["directory"].with_name("fixture-generation")
    execution.bundle["directory"].rename(saved)
    observed = []

    def prepare(_directory, _recording, snapshot, folder, *, offset, duration, cancel):
        assert snapshot == execution.job["params"]["audio_input"]
        assert not cancel.is_set()
        observed.append((offset, duration))
        index = len(observed) - 1
        original = execution.clips[index]
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original / "audio.wav", folder / "audio.wav")
        shutil.copyfile(original / "audio.input.json", folder / "audio.input.json")
        return json.loads((folder / "audio.input.json").read_text(encoding="utf-8"))

    monkeypatch.setattr(audio_inputs, "prepare_wav", prepare)
    calls = []
    monkeypatch.setattr(repair_cloud, "run", lambda _w, job, _r, **_kw: calls.append(job["id"]))
    repair_cloud.prepare_and_run(execution.worker, execution.job, execution.recording)
    assert observed == [(2, 3), (9, 3)]
    assert calls == [execution.job["id"]]
    repair_cloud.prepare_and_run(
        execution.worker, execution.store.job(execution.job["id"]), execution.recording
    )
    assert observed == [(2, 3), (9, 3)]  # No extraction or mutation on recovery.
    assert calls == [execution.job["id"]] * 2


def test_cancellation_before_clip_preparation_starts_no_allocation(execution, monkeypatch):
    from cbs_transcriber import audio_inputs

    execution.bundle["directory"].rename(
        execution.bundle["directory"].with_name("fixture-generation")
    )
    execution.worker.cancel_event.set()
    monkeypatch.setattr(audio_inputs, "prepare_wav", lambda *_a, **_kw: pytest.fail("cancelled"))
    monkeypatch.setattr(repair_cloud, "run", lambda *_a, **_kw: pytest.fail("must not allocate"))
    with pytest.raises(api.Cancelled):
        repair_cloud.prepare_and_run(execution.worker, execution.job, execution.recording)
