"""Legacy capacity receipts against synthetic SQLite and a read-only remote client."""

import copy
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import providers
from cbs_transcriber import runtime_capacity as capacity
from cbs_transcriber.store import Store


class Client:
    def __init__(self, *states):
        self.states = list(states or ["RUNNING"])
        self.reads, self.stops, self.before_read = [], [], None

    def retrieve_job(self, identifier):
        self.reads.append(identifier)
        if self.before_read:
            self.before_read()
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if isinstance(state, Exception):
            raise state
        return {"status": {"state": state}}

    def terminate_job(self, identifier):
        self.stops.append(identifier)
        self.states = ["ABORTED"]


@pytest.fixture
def library(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Synthetic capacity study")
    credentials = SimpleNamespace(get=lambda scope: "synthetic-control-secret")
    return store, project, credentials


def job(library, *, state="running", remote_id="owned-remote", base_url="https://cloud.invalid"):
    store, project, _ = library
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    settings = providers.Settings(provider_type="ucloud", base_url=base_url, model="large-v3")
    row = store.add_job(
        recording["id"],
        "enhance",
        {
            "ucloud_api": {"settings": settings.model_dump()},
            "ucloud_run": {
                "job_id": remote_id,
                "remote_state": "RUNNING",
                "capacity_reserved": True,
                "uploaded": True,
                "owned_folder": "synthetic-owned-folder",
            },
        },
    )
    store.update_job(row["id"], state=state)
    return store.job(row["id"])


def checkpoint(store, identifier):
    def write(**values):
        current = store.job(identifier)
        current["params"]["ucloud_run"].update(values)
        store.update_job(identifier, params=current["params"])

    return write


def test_stop_collect_observes_terminal_proof_and_releases_legacy_slot(library):
    store, _, credentials = library
    row = job(library)
    client = Client("RUNNING")
    write = checkpoint(store, row["id"])
    api.stop(client, row["params"]["ucloud_run"], write)
    assert client.stops == ["owned-remote"]
    before = store.job(row["id"])["params"]["ucloud_run"]
    assert before["termination_requested"] and before["capacity_reserved"]
    fresh = capacity.drain(store, credentials, row, client=client, checkpoint=write)
    run = fresh["params"]["ucloud_run"]
    assert run["remote_state"] == "ABORTED" and run["capacity_reserved"] is False
    assert run["remote_observed_at"] and run["capacity_released_at"]
    assert run["uploaded"] and run["owned_folder"] == "synthetic-owned-folder"
    assert client.stops == ["owned-remote"]  # Refresh itself never terminates anything.


def test_uncertain_submission_without_remote_id_never_releases_or_calls_remote(library):
    store, _, credentials = library
    row = job(library, remote_id=None)
    row["params"]["ucloud_run"]["submission_intent"] = True
    store.update_job(row["id"], params=row["params"])
    client = Client("SUCCESS")
    fresh = capacity.refresh(store, credentials, row, client=client)
    assert fresh["params"]["ucloud_run"]["capacity_reserved"]
    assert client.reads == [] and client.stops == []


@pytest.mark.parametrize(
    "state",
    [
        httpx.ReadTimeout("private transcript synthetic-secret"),
        None,
        "private text",
        ValueError("synthetic-secret"),
    ],
)
def test_status_failures_keep_capacity_and_only_curated_diagnostics(library, state):
    store, _, credentials = library
    row = job(library)
    client = Client(state)
    fresh = capacity.refresh(store, credentials, row, client=client)
    run = fresh["params"]["ucloud_run"]
    assert run["remote_state"] == "RUNNING" and run["capacity_reserved"]
    assert run["capacity_refresh_error"] == "status_unavailable"
    assert "private" not in str(run) and "synthetic-secret" not in str(run)


def test_refresh_merges_receipts_written_while_remote_read_is_in_flight(library):
    store, _, credentials = library
    row = job(library)
    client = Client("SUCCESS")

    def newer_receipt():
        current = store.job(row["id"])
        current["params"]["ucloud_run"].update(cleanup_state="pending", reconciled_at="new receipt")
        current["params"]["new_frozen_note"] = "preserve"
        store.update_job(row["id"], params=current["params"])

    client.before_read = newer_receipt
    fresh = capacity.refresh(store, credentials, row, client=client)
    assert fresh["params"]["new_frozen_note"] == "preserve"
    run = fresh["params"]["ucloud_run"]
    assert run["cleanup_state"] == "pending" and run["reconciled_at"] == "new receipt"
    assert run["capacity_reserved"] is False


def test_remote_identity_change_during_read_cannot_release_another_owner(library):
    store, _, credentials = library
    row = job(library)
    client = Client("SUCCESS")
    client.before_read = lambda: checkpoint(store, row["id"])(job_id="different-owned-job")
    with pytest.raises(ValueError, match="allocation changed"):
        capacity.refresh(store, credentials, row, client=client)
    run = store.job(row["id"])["params"]["ucloud_run"]
    assert run["job_id"] == "different-owned-job" and run["capacity_reserved"]


def test_stale_running_response_cannot_reopen_concurrent_terminal_receipt(library):
    store, _, credentials = library
    row = job(library)
    client = Client("RUNNING")
    client.before_read = lambda: checkpoint(store, row["id"])(remote_state="ABORTED")
    run = capacity.refresh(store, credentials, row, client=client)["params"]["ucloud_run"]
    assert run["remote_state"] == "ABORTED" and run["capacity_reserved"] is False


def test_drain_is_bounded_and_cancel_aware_without_inference_from_stop_ack(library):
    store, _, credentials = library
    row = job(library)
    write = checkpoint(store, row["id"])
    write(termination_requested=True)
    client = Client("RUNNING")
    waits = []
    fresh = capacity.drain(
        store,
        credentials,
        row,
        client=client,
        checkpoint=write,
        wait=lambda seconds: waits.append(seconds),
    )
    assert len(client.reads) == 6 and waits == [1] * 5
    assert fresh["params"]["ucloud_run"]["capacity_reserved"]
    client.reads.clear()
    capacity.drain(store, credentials, fresh, client=client, checkpoint=write, wait=lambda _: True)
    assert len(client.reads) == 1


def test_drain_polls_until_exact_terminal_state(library):
    store, _, credentials = library
    row = job(library)
    client = Client("RUNNING", "RUNNING", "SUCCESS")
    fresh = capacity.drain(
        store,
        credentials,
        row,
        client=client,
        checkpoint=checkpoint(store, row["id"]),
        wait=lambda _: False,
    )
    assert len(client.reads) == 3 and client.stops == []
    assert fresh["params"]["ucloud_run"]["capacity_reserved"] is False


def test_blocker_refresh_finds_completed_legacy_job_outside_recent_history(library, monkeypatch):
    store, _, credentials = library
    row = job(library, state="completed")
    client = Client("SUCCESS")

    @contextmanager
    def client_for(settings, secret):
        assert secret == "synthetic-control-secret"
        yield client

    monkeypatch.setattr(capacity.native, "client_for", client_for)
    fresh = capacity.refresh_blocker(store, credentials)
    assert fresh["id"] == row["id"] and fresh["state"] == "completed"
    assert fresh["params"]["ucloud_run"]["capacity_reserved"] is False
    assert client.reads == ["owned-remote"] and client.stops == []
    assert capacity.refresh_blocker(store, credentials) is None


@pytest.mark.parametrize("different", ["runtime_batch_id", "native_batch_id", "ssh"])
def test_new_batch_native_and_ssh_owners_are_never_legacy_blockers(library, monkeypatch, different):
    store, _, credentials = library
    row = job(library)
    params = copy.deepcopy(row["params"])
    if different == "ssh":
        params.pop("ucloud_api")
        params["cloud_settings"] = {"server": "synthetic"}
    else:
        params[different] = "another-allocation"
    store.update_job(row["id"], params=params)
    monkeypatch.setattr(capacity.native, "client_for", lambda *_: pytest.fail("Unexpected network"))
    assert capacity.refresh_blocker(store, credentials) is None
    assert capacity.refresh(store, credentials, row)["params"]["ucloud_run"]["capacity_reserved"]


def test_missing_secret_is_skipped_and_does_not_starve_next_old_owner(library, monkeypatch):
    store, _, _ = library
    first = job(library, base_url="https://no-key.invalid")
    second = job(library, remote_id="second-remote")
    credentials = SimpleNamespace(
        get=lambda scope: None if "no-key.invalid" in scope else "synthetic"
    )
    client = Client("SUCCESS")

    @contextmanager
    def client_for(*_):
        yield client

    monkeypatch.setattr(capacity.native, "client_for", client_for)
    moment = [0]
    monkeypatch.setattr(capacity.time, "monotonic", lambda: moment[0])
    fresh = capacity.refresh_blocker(store, credentials)
    assert fresh["id"] == first["id"] and client.reads == []
    assert fresh["params"]["ucloud_run"]["capacity_refresh_error"] == "credentials_unavailable"
    moment[0] = 14
    assert capacity.refresh_blocker(store, credentials) is None
    moment[0] = 15
    fresh = capacity.refresh_blocker(store, credentials)
    assert fresh["id"] == second["id"] and client.reads == ["second-remote"]


def test_known_terminal_receipt_releases_without_credentials_or_remote_calls(library):
    store, _, _ = library
    row = job(library, state="completed")
    checkpoint(store, row["id"])(remote_state="SUCCESS")
    credentials = SimpleNamespace(get=lambda _: pytest.fail("Terminal proof needs no credential"))
    fresh = capacity.refresh(store, credentials, row)
    assert fresh["params"]["ucloud_run"]["capacity_reserved"] is False


def test_supplied_client_cannot_use_a_stale_frozen_owner(library):
    store, _, credentials = library
    row = job(library)
    checkpoint(store, row["id"])(job_id="another-owned-allocation")
    client = Client("SUCCESS")
    with pytest.raises(ValueError, match="changed before"):
        capacity.refresh(store, credentials, row, client=client)
    assert client.reads == []


def test_blocker_process_gate_never_waits_behind_another_refresh(library):
    store, _, credentials = library
    job(library)
    assert capacity._refresh_gate.acquire(blocking=False)
    try:
        assert capacity.refresh_blocker(store, credentials) is None
    finally:
        capacity._refresh_gate.release()
