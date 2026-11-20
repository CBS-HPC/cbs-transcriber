"""Adversarial preparation observations through real durable owners and raw fake reads."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from test_runtime_preparation import enqueue, execute
from test_runtime_preparation_observation import assert_one_owner
from test_runtime_preparation_observation import observation as observation
from ucloud_workflow import UCloudAPIError

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import runtime_preparation as subject


def artifact(observation, owner, name):
    return api.run_folder(observation.settings, owner["id"]) + "/" + name + ".json"


@pytest.mark.parametrize("status", [401, 403, 404])
def test_permanent_state_refusal_never_retries_or_submits_a_second_owner(observation, status):
    f = observation.fixture
    owner = enqueue(f)
    observation.state_replies = [UCloudAPIError("synthetic refusal", status_code=status), "SUCCESS"]
    final, _ = execute(f)
    assert final["state"] == "failed"
    assert final["journal"].get("prepared") is not True
    assert observation.state_replies == ["SUCCESS"]
    assert_one_owner(f, owner["id"])


@pytest.mark.parametrize("status", [401, 403])
def test_artifact_authentication_refusal_is_not_publication_settling(observation, status):
    f = observation.fixture
    owner = enqueue(f)
    path = artifact(observation, owner, "supervisor")
    observation.files[path] = [UCloudAPIError("synthetic refusal", status_code=status)]
    final, _ = execute(f)
    assert final["state"] == "failed"
    assert final["journal"].get("prepared") is not True
    assert observation.reads.count(path) == 1
    assert_one_owner(f, owner["id"])


@pytest.mark.parametrize("payload", [[], {"phase": "finished", "generation": "another-owner"}])
def test_complete_invalid_supervisor_never_settles_into_readiness(observation, payload):
    f = observation.fixture
    owner = enqueue(f)
    path = artifact(observation, owner, "supervisor")
    observation.files[path] = [payload, {"phase": "finished", "generation": owner["id"]}]
    final, _ = execute(f)
    assert final["state"] == "failed"
    assert final["journal"].get("prepared") is not True
    assert observation.reads.count(path) == 1
    assert_one_owner(f, owner["id"])


def test_partial_supervisor_can_settle_without_duplicating_the_allocation(observation, monkeypatch):
    f = observation.fixture
    owner = enqueue(f)
    path = artifact(observation, owner, "supervisor")
    observation.files[path] = [b'{"phase":', {"phase": "finished", "generation": owner["id"]}]
    waits = []
    monkeypatch.setattr(subject.Run, "_wait", lambda self, delay: waits.append(delay))
    final, _ = execute(f)
    assert final["state"] == "completed", final["message"]
    assert observation.reads.count(path) == 2 and waits
    assert_one_owner(f, owner["id"])


def test_negative_doctor_cannot_be_retried_into_a_later_positive_doctor(observation):
    f = observation.fixture
    owner = enqueue(f)
    path = artifact(observation, owner, "doctor")
    negative = {**f.cloud.doctor, "ready": False}
    observation.files[path] = [negative, f.cloud.doctor]
    final, _ = execute(f)
    assert final["state"] == "failed"
    assert final["journal"].get("doctor_verified") is not True
    assert observation.reads.count(path) == 1
    assert_one_owner(f, owner["id"])


def test_wrong_stamp_is_invalid_not_temporarily_missing(observation):
    f = observation.fixture
    owner = enqueue(f)
    path = observation.runtime + "/stamp.json"
    observation.files[path] = [b"wrong recipe"]
    final, _ = execute(f)
    assert final["state"] == "failed"
    assert final["journal"].get("prepared") is not True
    metadata = [entry for entry in observation.controls if entry[2] == {"id": path}]
    assert len(metadata) == 1
    assert_one_owner(f, owner["id"])


def test_consecutive_transient_budget_resets_only_after_a_valid_observation(
    observation, monkeypatch
):
    f = observation.fixture
    owner = enqueue(f)
    observation.state_replies = [
        UCloudAPIError("synthetic gateway", status_code=503),
        "RUNNING",
        UCloudAPIError("synthetic gateway", status_code=503),
        "SUCCESS",
    ]
    monkeypatch.setattr(subject, "OBSERVATION_ATTEMPTS", 2)
    monkeypatch.setattr(subject.Run, "_wait", lambda self, delay: None)
    final, _ = execute(f)
    assert final["state"] == "completed", final["message"]
    assert observation.state_replies == []
    assert_one_owner(f, owner["id"])


def test_transient_exhaustion_keeps_the_same_owner_and_remote_evidence(observation, monkeypatch):
    f = observation.fixture
    owner = enqueue(f)
    observation.state_replies = [
        UCloudAPIError("synthetic gateway", status_code=503) for _ in range(8)
    ]
    waits = []
    monkeypatch.setattr(subject, "OBSERVATION_ATTEMPTS", 2)
    monkeypatch.setattr(subject.Run, "_wait", lambda self, delay: waits.append(delay))
    final, _ = execute(f)
    assert final["state"] == "failed"
    assert final["journal"].get("prepared") is not True
    assert len(observation.state_replies) >= 5 and len(waits) <= 2
    assert_one_owner(f, owner["id"])


def test_missing_terminal_artifact_has_a_finite_settling_budget(observation, monkeypatch):
    f = observation.fixture
    owner = enqueue(f)
    path = artifact(observation, owner, "supervisor")
    observation.files[path] = [None]
    monkeypatch.setattr(subject, "TERMINAL_SETTLE_ATTEMPTS", 2)
    monkeypatch.setattr(subject.Run, "_wait", lambda self, delay: None)
    final, _ = execute(f)
    assert final["state"] == "failed"
    assert final["journal"].get("prepared") is not True
    assert 1 <= observation.reads.count(path) <= 3
    assert_one_owner(f, owner["id"])


@pytest.mark.parametrize("waiting", ["transient", "publication"])
def test_actual_stop_during_observation_wait_uses_owned_stop_path(
    observation, monkeypatch, waiting
):
    f = observation.fixture
    owner = enqueue(f)
    if waiting == "transient":
        f.cloud.state = "RUNNING"
        observation.state_replies = [UCloudAPIError("synthetic gateway", status_code=503)]
    else:
        observation.files[artifact(observation, owner, "supervisor")] = [None]
    waits = []

    def stop_while_waiting(self, delay):
        waits.append(delay)
        subject.cancel(f.store, f.credentials, owner["id"])

    monkeypatch.setattr(subject.Run, "_wait", stop_while_waiting)
    final, _ = execute(f)
    assert waits
    assert final["state"] == "cancelled"
    assert final["journal"]["stop_intent"] is True
    assert final["journal"].get("prepared") is not True
    assert f.cloud.terminations == (["remote-preparation"] if waiting == "transient" else [])
    assert_one_owner(f, owner["id"])


def test_epoch_supersession_after_verification_cannot_publish_a_stale_ready(observation):
    f = observation.fixture
    owner = enqueue(f)
    replacement = []

    def supersede(path):
        if path.endswith("/doctor.json"):
            observation.after_read = None
            subject.recover(f.store)
            replacement.append(subject.claim(f.store))

    observation.after_read = supersede
    final, _ = execute(f)
    assert replacement and replacement[0]["id"] == owner["id"]
    assert final["epoch"] == replacement[0]["epoch"]
    assert final["state"] == "running"
    assert final["journal"].get("prepared") is not True
    assert_one_owner(f, owner["id"])


def test_stop_committed_while_completion_waits_for_writer_cannot_publish_ready(
    observation, monkeypatch
):
    f = observation.fixture
    owner = enqueue(f)
    doctor_seen, allow_doctor, stamp_seen, completion_writer = (threading.Event() for _ in range(4))
    runner_thread = []
    original_connection = f.store.connection

    class ObservedConnection:
        def __init__(self, database):
            self.database = database

        def execute(self, sql, parameters=()):
            if (
                sql == "BEGIN IMMEDIATE"
                and stamp_seen.is_set()
                and runner_thread == [threading.get_ident()]
            ):
                completion_writer.set()
            return self.database.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(self.database, name)

    @contextmanager
    def observe_connection():
        with original_connection() as database:
            yield ObservedConnection(database)

    def after_read(path):
        if path.endswith("/doctor.json"):
            runner_thread[:] = [threading.get_ident()]
            doctor_seen.set()
            assert allow_doctor.wait(8)
        elif path.endswith("/stamp.json"):
            stamp_seen.set()

    monkeypatch.setattr(f.store, "connection", observe_connection)
    observation.after_read = after_read
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(execute, f)
        try:
            assert doctor_seen.wait(8)
            with original_connection() as database:
                database.execute("BEGIN IMMEDIATE")
                allow_doctor.set()
                assert completion_writer.wait(8)
                row = database.execute(
                    "SELECT journal FROM runtime_preparations WHERE id=?", (owner["id"],)
                ).fetchone()
                journal = {**json.loads(row[0]), "stop_intent": True}
                database.execute(
                    "UPDATE runtime_preparations SET journal=? WHERE id=?",
                    (json.dumps(journal), owner["id"]),
                )
            final, _ = pending.result(timeout=10)
        finally:
            allow_doctor.set()
    assert final["state"] == "cancelled"
    assert final["journal"]["stop_intent"] is True
    assert final["journal"].get("prepared") is not True
    assert final["journal"].get("doctor_verified") is not True
    assert_one_owner(f, owner["id"])
