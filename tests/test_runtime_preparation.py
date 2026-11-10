"""Standalone Runtime preparation against synthetic storage and in-memory UCloud only."""

import copy
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import (
    providers,
    runtime_compute,
    runtime_store,
)
from cbs_transcriber import (
    runtime_preparation as subject,
)
from cbs_transcriber.store import SCHEMA_VERSION, Store, now

SECRET = "synthetic-preparation-key-never-uploaded"


@pytest.fixture
def prepared(client, credentials, monkeypatch):
    store = client.app.state.store
    profile = providers.save_profile(
        store,
        credentials,
        "Synthetic UCloud",
        providers.Settings(
            provider_type="ucloud",
            base_url="https://cloud.invalid",
            model="large-v3",
            ucloud_transcriber={
                "project_id": "synthetic-project",
                "storage_path": "/drive/cbs",
                "application": {"name": "transcriber", "version": "1.12"},
                "product": {
                    "id": "gpu-nvidia-b200-1-gpu",
                    "category": "gpu-nvidia-b200",
                    "provider": "ucloud",
                },
            },
        ),
        api_key=SECRET,
        remember=False,
    )
    providers.set_default_provider(store, profile["id"])

    class Cloud:
        ready = False
        state = "SUCCESS"
        upload_error = False
        submit_error = False
        retrieval_hook = None
        doctor = {
            "ready": True,
            "version": api.WHISPERX_VERSION,
            "gpu": {"available": True, "devices": [{"memory_gb": 23}]},
        }

        def __init__(self):
            self.submissions, self.uploads, self.terminations, self.trashed = [], [], [], []
            self.folders, self.matches = set(), []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def create_folder(self, path):
            if path in self.folders:
                raise ValueError("synthetic conflict")
            self.folders.add(path)

        def submit_job(self, specification):
            # The durable POST intent and shared slot must already exist.
            owner = store.one("SELECT id FROM runtime_preparations WHERE state='running'")
            journal = subject.get(store, owner["id"])["journal"]
            assert journal["submission_intent"] and journal["capacity_reserved"]
            self.submissions.append(copy.deepcopy(specification))
            if self.submit_error:
                raise ValueError("synthetic unknown reply " + SECRET)
            return {"responses": [{"id": "remote-preparation"}]}

        def retrieve_job(self, identifier):
            assert identifier == "remote-preparation"
            if self.retrieval_hook:
                self.retrieval_hook()
            return {
                "id": identifier,
                "specification": self.submissions[-1],
                "status": {"state": self.state},
            }

        def terminate_job(self, identifier):
            self.terminations.append(identifier)
            self.state = "CANCELLED"

        def browse_jobs(self, **kwargs):
            return {"items": copy.deepcopy(self.matches), "next": None}

    cloud = Cloud()
    monkeypatch.setattr(api.native, "client_for", lambda *args, **kwargs: cloud)
    monkeypatch.setattr(api, "stamped", lambda *args: cloud.ready)
    monkeypatch.setattr(api, "ensure_folder", lambda _, path: cloud.folders.add(path))
    monkeypatch.setattr(
        api, "script_application", lambda _: {"name": "terminal-ubuntu", "version": "synthetic"}
    )
    monkeypatch.setattr(
        runtime_compute,
        "freeze",
        lambda *_: {
            "runtime_product": dict(runtime_compute.MIG_PRODUCT),
            "runtime_compute_policy": runtime_compute.POLICY,
        },
    )
    monkeypatch.setattr("cbs_transcriber.runtime_batches.ensure_use_lock", lambda *args: None)
    monkeypatch.setattr(api, "POLL_SECONDS", 0)

    def upload(_, server, path, target, guard, checkpoint):
        guard()
        assert server == "https://cloud.invalid"
        assert path.name not in {"audio.wav", "manifest.sha256", "request.json"}
        assert SECRET.encode() not in path.read_bytes()
        cloud.uploads.append(target)
        if cloud.upload_error:
            raise OSError("synthetic interrupted public upload")

    def remote_json(_, server, path, *args):
        if path.endswith("/supervisor.json"):
            identifier = path.split("cbs-enhancement-", 1)[1].split("/", 1)[0]
            return {"phase": "finished", "generation": identifier}
        assert path.endswith("/doctor.json")
        return copy.deepcopy(cloud.doctor)

    monkeypatch.setattr(api.native, "upload", upload)
    monkeypatch.setattr(api, "read_json", remote_json)
    monkeypatch.setattr(
        api.native, "trash_owned_folder", lambda _, path: cloud.trashed.append(path)
    )
    chosen = api.connection(store, credentials, [])
    return SimpleNamespace(
        store=store,
        credentials=credentials,
        client=client,
        cloud=cloud,
        fingerprint=subject.fingerprint(chosen),
        profile=profile,
    )


def enqueue(fixture):
    response = fixture.client.post(
        "/api/enhancement-api/prepare", json={"connection_fingerprint": fixture.fingerprint}
    )
    assert response.status_code == 202, response.text
    return subject.get(fixture.store, response.json()["operation"]["runtime_preparation_id"])


def execute(fixture):
    value = subject.claim(fixture.store)
    assert value is not None
    worker = SimpleNamespace(
        store=fixture.store, credentials=fixture.credentials, stopping=threading.Event()
    )
    subject.Run(worker, value).execute()
    return subject.get(fixture.store, value["id"]), worker


def test_check_and_settings_reads_never_schedule_preparation(prepared):
    f = prepared
    assert (
        f.client.get("/api/enhancement-api/status").json()["connection_fingerprint"]
        == f.fingerprint
    )
    assert f.client.post("/api/enhancement-api/check", json={}).json()["prepared"] is False
    f.client.get("/api/enhancement-settings")
    f.client.put("/api/enhancement-settings", json={"prepare_ucloud_models": True})
    assert f.store.all("SELECT * FROM runtime_preparations") == []
    assert f.cloud.submissions == [] and f.cloud.uploads == []


@pytest.mark.parametrize("problem", ["disabled", "stale", "absent", "extra"])
def test_explicit_prepare_requires_saved_consent_exact_connection_and_strict_body(
    prepared, problem
):
    f = prepared
    body = {"connection_fingerprint": f.fingerprint}
    if problem == "disabled":
        f.client.put("/api/enhancement-settings", json={"prepare_ucloud_models": False})
    elif problem == "stale":
        body["connection_fingerprint"] = "0" * 64
    elif problem == "absent":
        body = {}
    else:
        body["prepare"] = True
    response = f.client.post("/api/enhancement-api/prepare", json=body)
    assert response.status_code in {409, 422}, response.text
    assert not f.store.all("SELECT * FROM runtime_preparations")
    assert not f.cloud.submissions


def test_concurrent_explicit_clicks_share_one_durable_owner_without_recording(prepared):
    f = prepared
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(
            executor.map(lambda _: subject.request(f.store, f.credentials, f.fingerprint), range(2))
        )
    ids = {reply["operation"]["runtime_preparation_id"] for reply in replies}
    assert len(ids) == 1 and sorted(reply["deduplicated"] for reply in replies) == [False, True]
    assert f.store.all("SELECT * FROM projects") == []
    assert f.store.all("SELECT * FROM recordings") == [] and f.store.all("SELECT * FROM jobs") == []
    assert len(f.client.get("/api/jobs").json()) == 1


def test_ready_check_returns_no_owner_or_remote_writes(prepared):
    f = prepared
    f.cloud.ready = True
    response = f.client.post(
        "/api/enhancement-api/prepare", json={"connection_fingerprint": f.fingerprint}
    )
    assert response.status_code == 200 and response.json()["operation"] is None
    assert not f.store.all("SELECT * FROM runtime_preparations") and not f.cloud.uploads


def test_success_uses_mig_public_scripts_only_and_preserves_cleanup_activity(prepared, monkeypatch):
    f = prepared
    initial = enqueue(f)
    original_submit = f.cloud.submit_job

    def submit(specification):
        result = original_submit(specification)
        f.cloud.ready = True
        return result

    monkeypatch.setattr(f.cloud, "submit_job", submit)
    value, _ = execute(f)
    assert value["state"] == "completed", value["message"]
    assert value["journal"]["doctor_verified"] and not value["journal"]["capacity_reserved"]
    spec = f.cloud.submissions[0]
    assert spec["product"] == runtime_compute.MIG_PRODUCT
    assert spec["application"] == {"name": "terminal-ubuntu", "version": "synthetic"}
    assert spec["name"] == initial["journal"]["run_name"]
    assert set(spec["parameters"]) == {"batchScript"}
    assert len(spec["resources"]) == 2 and all(not item["readOnly"] for item in spec["resources"])
    script = (f.store.root / "runtime-preparations" / value["id"] / "supervisor.sh").read_text()
    assert "PREPARE_ONLY=1" in script and 'MODELS=""' in script
    assert script.index('if [ "$PREPARE_ONLY" = 1 ]') < script.index(
        'while [ ! -f "$RUN/manifest.sha256" ]'
    )
    assert len(f.cloud.submissions) == 1
    assert f.client.post("/api/activity/clear", json={}).status_code == 200
    assert any(
        row.get("runtime_preparation_id") == value["id"] for row in f.client.get("/api/jobs").json()
    )
    response = f.client.post(f"/api/runtime-preparations/{value['id']}/cleanup", json={})
    assert response.status_code == 200, response.text
    assert f.cloud.trashed == [
        api.run_folder(providers.Settings.model_validate(value["frozen"]["settings"]), value["id"])
    ]
    assert f.client.post(f"/api/runtime-preparations/{value['id']}/hide", json={}).json() == {
        "hidden": True
    }
    assert not f.client.get("/api/jobs").json()


@pytest.mark.parametrize("code", ["runtime-busy", "runtime-prepare", "runtime"])
def test_terminal_supervisor_failure_preserves_safe_reason(prepared, monkeypatch, code):
    f = prepared
    initial = enqueue(f)
    monkeypatch.setattr(
        api,
        "read_json",
        lambda _client, _server, path, *args: {
            "phase": "failed",
            "generation": initial["id"],
            "code": code,
            "detail": SECRET,
        },
    )
    value, _ = execute(f)
    assert value["state"] == "failed"
    assert api.FAILURES[code] in value["message"]
    assert SECRET not in value["message"]
    assert value["journal"]["job_id"] == "remote-preparation"
    assert value["journal"]["remote_state"] == "SUCCESS"


@pytest.mark.parametrize("code", [SECRET, [SECRET], None, "manifest", "input_preparation"])
def test_unknown_supervisor_failure_does_not_expose_remote_data(prepared, monkeypatch, code):
    f = prepared
    initial = enqueue(f)
    monkeypatch.setattr(
        api,
        "read_json",
        lambda _client, _server, path, *args: {
            "phase": "failed",
            "generation": initial["id"],
            "code": code,
            "detail": SECRET,
        },
    )
    value, _ = execute(f)
    assert value["state"] == "failed"
    assert "UCloud preparation failed" in value["message"]
    assert SECRET not in value["message"]
    assert value["journal"]["job_id"] == "remote-preparation"
    assert value["journal"]["remote_state"] == "SUCCESS"


def test_upload_failure_is_durable_and_retry_creates_one_frozen_successor(prepared):
    f = prepared
    initial = enqueue(f)
    f.cloud.upload_error = True
    value, _ = execute(f)
    assert value["state"] == "failed" and not f.cloud.submissions
    assert not value["journal"].get("submission_intent")
    assert subject.card(value)["can_retry"] and subject.card(value)["can_cleanup"]
    child = subject.retry(f.store, f.credentials, value["id"])
    again = subject.retry(f.store, f.credentials, value["id"])
    assert child["runtime_preparation_id"] == again["runtime_preparation_id"] != initial["id"]
    assert (
        subject.get(f.store, child["runtime_preparation_id"])["frozen"]["settings"]
        == initial["frozen"]["settings"]
    )
    assert f.store.all("SELECT * FROM jobs") == []


def test_uncertain_post_cannot_retry_and_absence_needs_fresh_confirmation(prepared):
    f = prepared
    enqueue(f)
    f.cloud.submit_error = True
    value, _ = execute(f)
    assert value["state"] == "failed" and len(f.cloud.submissions) == 1
    assert subject.card(value)["ucloud_submission_uncertain"]
    assert SECRET not in json.dumps(f.client.get("/api/jobs").json())
    with pytest.raises(subject.Conflict, match="Resolve"):
        subject.retry(f.store, f.credentials, value["id"])
    subject.recover(f.store)
    assert subject.claim(f.store) is None
    preview = subject.reconcile(f.store, f.credentials, value["id"])
    assert preview["requires_confirmation"] and preview["status"] == "not-found"
    with pytest.raises(subject.Conflict, match="stale"):
        subject.reconcile(
            f.store, f.credentials, value["id"], confirm_not_created=True, fingerprint="0" * 64
        )
    confirmed = subject.reconcile(
        f.store,
        f.credentials,
        value["id"],
        confirm_not_created=True,
        fingerprint=preview["fingerprint"],
    )
    assert not confirmed["operation"]["ucloud_submission_uncertain"]
    assert not subject.get(f.store, value["id"])["journal"]["capacity_reserved"]
    assert len(f.cloud.submissions) == 1


def test_uncertain_exact_remote_is_adopted_and_reconnect_never_submits(prepared):
    f = prepared
    enqueue(f)
    f.cloud.submit_error = True
    value, _ = execute(f)
    f.cloud.matches = [{"id": "remote-preparation", "specification": f.cloud.submissions[0]}]
    result = subject.reconcile(f.store, f.credentials, value["id"])
    assert result["status"] == "found" and result["operation"]["state"] == "queued"
    f.cloud.ready = True
    final, _ = execute(f)
    assert final["state"] == "completed" and len(f.cloud.submissions) == 1


def test_queued_stop_is_inert_and_terminal_stale_stop_refuses(prepared):
    f = prepared
    value = enqueue(f)
    result = subject.cancel(f.store, f.credentials, value["id"])
    assert result["state"] == "cancelled" and subject.claim(f.store) is None
    assert not f.cloud.submissions and not f.cloud.uploads
    assert subject.cancel(f.store, f.credentials, value["id"])["state"] == "cancelled"
    f.store.execute(
        "UPDATE runtime_preparations SET state='completed',journal=? WHERE id=?",
        (json.dumps({"run_name": value["journal"]["run_name"]}), value["id"]),
    )
    with pytest.raises(subject.Conflict, match="no longer active"):
        subject.cancel(f.store, f.credentials, value["id"])


def test_shutdown_preserves_recorded_remote_and_restart_only_observes_same_id(prepared):
    f = prepared
    enqueue(f)
    value = subject.claim(f.store)
    worker = SimpleNamespace(store=f.store, credentials=f.credentials, stopping=threading.Event())
    f.cloud.state = "RUNNING"
    f.cloud.retrieval_hook = worker.stopping.set
    subject.Run(worker, value).execute()
    assert subject.get(f.store, value["id"])["state"] == "interrupted"
    assert not f.cloud.terminations and len(f.cloud.submissions) == 1
    subject.recover(f.store)
    # A clean shutdown leaves interrupted; explicit Retry reconnects the same allocation.
    result = subject.retry(f.store, f.credentials, value["id"])
    assert result["runtime_preparation_id"] == value["id"]
    f.cloud.retrieval_hook = None
    subject.cancel(f.store, f.credentials, value["id"])
    final = subject.get(f.store, value["id"])
    assert final["state"] == "cancelled" and f.cloud.terminations == ["remote-preparation"]
    assert len(f.cloud.submissions) == 1


def test_stop_control_reserves_epoch_and_id_before_rpc_and_excludes_claim(prepared, monkeypatch):
    f = prepared
    first = enqueue(f)
    claimed = subject.claim(f.store)
    assert subject.begin_submission(f.store, first["id"], claimed["epoch"])
    subject.checkpoint(
        f.store,
        first["id"],
        claimed["epoch"],
        state="failed",
        journal={"job_id": "remote-preparation", "remote_state": "RUNNING"},
    )
    f.cloud.state = "RUNNING"
    f.cloud.submissions.append({"name": first["journal"]["run_name"]})
    entered, release = threading.Event(), threading.Event()
    terminate = f.cloud.terminate_job

    def held_termination(identifier):
        receipt = subject.get(f.store, first["id"])
        assert receipt["epoch"] == claimed["epoch"]
        assert receipt["journal"]["termination_epoch"] == claimed["epoch"]
        assert receipt["journal"]["termination_job_id"] == identifier
        assert receipt["journal"]["termination_response"] == "unknown"
        entered.set()
        assert release.wait(8)
        terminate(identifier)

    monkeypatch.setattr(f.cloud, "terminate_job", held_termination)
    with f.store.library_lock(), ThreadPoolExecutor(max_workers=1) as pool:
        control = pool.submit(
            f.client.post, f"/api/runtime-preparations/{first['id']}/cancel", json={}
        )
        try:
            assert entered.wait(3)
            assert subject.claim(f.store) is None
            duplicate = f.client.post(f"/api/runtime-preparations/{first['id']}/cancel", json={})
            assert duplicate.status_code == 409
            assert subject.get(f.store, first["id"])["epoch"] == claimed["epoch"]
        finally:
            release.set()
        assert control.result(timeout=3).json()["state"] == "cancelled"
    assert f.cloud.terminations == ["remote-preparation"]
    final = subject.get(f.store, first["id"])
    subject.checkpoint(f.store, first["id"], final["epoch"], journal={"remote_state": "RUNNING"})
    assert subject.get(f.store, first["id"])["journal"]["remote_state"] == "CANCELLED"


def test_unknown_termination_is_observed_after_restart_and_only_retried_explicitly(
    prepared, monkeypatch
):
    f = prepared
    first = enqueue(f)
    claimed = subject.claim(f.store)
    assert subject.begin_submission(f.store, first["id"], claimed["epoch"])
    subject.checkpoint(
        f.store,
        first["id"],
        claimed["epoch"],
        state="failed",
        journal={"job_id": "remote-preparation", "remote_state": "RUNNING"},
    )
    f.cloud.state = "RUNNING"
    f.cloud.submissions.append({"name": first["journal"]["run_name"]})
    terminate = f.cloud.terminate_job

    def lost_reply(identifier):
        f.cloud.terminations.append(identifier)
        raise ValueError("synthetic lost control reply " + SECRET)

    monkeypatch.setattr(f.cloud, "terminate_job", lost_reply)
    response = f.client.post(f"/api/runtime-preparations/{first['id']}/cancel", json={})
    assert response.status_code == 202 and response.json()["ucloud_stop_pending"]
    pending = subject.get(f.store, first["id"])
    assert pending["journal"]["termination_response"] == "unknown"
    assert pending["journal"]["capacity_reserved"] and SECRET not in json.dumps(response.json())
    # An automatic/restarted observer may read this same ID, but does not repeat the RPC.
    observer = subject.claim(f.store)
    worker = SimpleNamespace(store=f.store, credentials=f.credentials, stopping=threading.Event())
    f.cloud.retrieval_hook = worker.stopping.set
    subject.Run(worker, observer).execute()
    assert f.cloud.terminations == ["remote-preparation"]
    assert subject.get(f.store, first["id"])["journal"]["capacity_reserved"]
    f.cloud.retrieval_hook = None
    monkeypatch.setattr(f.cloud, "terminate_job", terminate)
    response = f.client.post(f"/api/runtime-preparations/{first['id']}/cancel", json={})
    assert response.json()["state"] == "cancelled"
    final = subject.get(f.store, first["id"])
    assert final["journal"]["termination_attempts"] == 2
    assert final["journal"]["termination_uncertain_seen"] is True
    assert final["journal"]["capacity_reserved"] is False
    assert f.cloud.terminations == ["remote-preparation", "remote-preparation"]


@pytest.mark.parametrize("intent", [False, True])
def test_crashed_prepost_owner_requires_explicit_retry_or_reconcile(prepared, intent):
    f = prepared
    enqueue(f)
    value = subject.claim(f.store)
    if intent:
        assert subject.begin_submission(f.store, value["id"], value["epoch"])
    subject.recover(f.store)
    assert subject.get(f.store, value["id"])["state"] == "interrupted"
    assert subject.claim(f.store) is None and not f.cloud.submissions
    assert subject.card(subject.get(f.store, value["id"]))["can_retry"] is (not intent)


def test_shared_capacity_reservation_counts_preparation_and_uncertainty(prepared):
    f = prepared
    runtime_store.save_settings(f.store, 1)
    enqueue(f)
    value = subject.claim(f.store)
    assert subject.begin_submission(f.store, value["id"], value["epoch"])
    identifier = "b" * 32
    f.store.execute(
        "INSERT INTO runtime_batch_runs(id,project_id,compatibility_hash,identity,created,updated) "
        "VALUES (?,?,?,?,?,?)",
        (identifier, "synthetic-project", runtime_store._digest({}), "{}", now(), now()),
    )
    assert runtime_store.reserve_capacity(f.store, identifier, epoch=0) is False
    with f.store.connection() as db:
        assert runtime_store._capacity(db) == 1
    f.store.execute("UPDATE runtime_preparations SET archived=1 WHERE id=?", (value["id"],))
    assert runtime_store.reserve_capacity(f.store, identifier, epoch=0) is True


def test_foreign_capacity_blocks_preparation_before_its_post_intent(prepared):
    f = prepared
    runtime_store.save_settings(f.store, 1)
    identifier = "b" * 32
    f.store.execute(
        "INSERT INTO runtime_batch_runs(id,project_id,compatibility_hash,identity,journal,"
        "created,updated) VALUES (?,?,?,?,?,?,?)",
        (
            identifier,
            "synthetic-project",
            runtime_store._digest({}),
            "{}",
            '{"submission_intent":true}',
            now(),
            now(),
        ),
    )
    enqueue(f)
    value = subject.claim(f.store)
    assert subject.begin_submission(f.store, value["id"], value["epoch"]) is False
    assert not subject.get(f.store, value["id"])["journal"].get("submission_intent")


@pytest.mark.parametrize("action", ["cancel", "retry", "cleanup", "reconcile"])
def test_archived_owners_are_inert_before_credentials_or_cloud(prepared, monkeypatch, action):
    f = prepared
    value = enqueue(f)
    f.store.execute("UPDATE runtime_preparations SET archived=1 WHERE id=?", (value["id"],))
    monkeypatch.setattr(
        f.credentials, "get", lambda *args: pytest.fail("archived owner accessed credentials")
    )
    with pytest.raises(subject.Conflict):
        if action == "cancel":
            subject.cancel(f.store, f.credentials, value["id"])
        else:
            getattr(subject, action)(f.store, f.credentials, value["id"])
    assert subject.claim(f.store) is None and subject.obligations(f.store) == []
    assert subject.card(subject.get(f.store, value["id"]))["can_remove"]


def test_schema15_migration_is_additive_and_backed_up(tmp_path):
    from cbs_transcriber import portable_semantics

    root = tmp_path / "synthetic-old"
    root.mkdir()
    with sqlite3.connect(root / "library.sqlite3") as db:
        for kind in ("table", "index"):
            for item in portable_semantics.catalog(15)["objects"]:
                if item["type"] == kind and item["sql"]:
                    db.execute(item["sql"])
        db.execute("PRAGMA user_version=15")
        db.execute("INSERT INTO settings VALUES (3,?)", ('{"prepare_ucloud_models":false}',))
    store = Store(root)
    # Schema 16 is followed by the later additive migrations up to the current schema.
    assert store.one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION >= 16
    assert (
        store.one("SELECT value FROM settings WHERE id=3")["value"]
        == '{"prepare_ucloud_models":false}'
    )
    assert store.all("SELECT * FROM runtime_preparations") == []
    backups = list((root / "backups").glob("schema-15-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 15


def test_portable_capture_refuses_active_preparation_and_restores_uncertainty_inert(
    prepared, tmp_path, monkeypatch
):
    from cbs_transcriber import portable_capture, portable_restore, portable_semantics

    f = prepared
    value = enqueue(f)
    queue = f.client.app.state.queue
    parent = tmp_path.parent / "pkg"
    with f.store.library_lock(), pytest.raises(ValueError, match="Stop active"):
        portable_capture.capture_library(
            f.store, queue, parent, gate=portable_capture.CaptureGate()
        )
    assert not list(parent.glob(".portable-*"))
    claimed = subject.claim(f.store)
    assert subject.begin_submission(f.store, claimed["id"], claimed["epoch"])
    subject.checkpoint(f.store, claimed["id"], claimed["epoch"], state="failed")
    before = f.store.one("SELECT * FROM runtime_preparations WHERE id=?", (value["id"],))
    with f.store.library_lock():
        captured = portable_capture.capture_library(
            f.store, queue, parent, gate=portable_capture.CaptureGate()
        )
    target = tmp_path.parent / "restored"
    result = portable_restore.restore_library(
        captured["folder"],
        expected_commit_sha256=captured["commit_sha256"],
        target=target,
        live_root=f.store.root,
    )
    assert result["state"] == "complete"
    restored = Store(target)
    after = restored.one("SELECT * FROM runtime_preparations WHERE id=?", (value["id"],))
    assert after == {**before, "archived": 1}
    assert f.store.one("SELECT * FROM runtime_preparations WHERE id=?", (value["id"],)) == before
    preserved = target / "preservation" / result["operation_id"] / "source.sqlite3"
    with sqlite3.connect(preserved) as db:
        assert db.execute(
            "SELECT archived,journal,frozen FROM runtime_preparations"
        ).fetchone() == (before["archived"], before["journal"], before["frozen"])
        assert portable_semantics.database_claims(db)["schema_version"] == SCHEMA_VERSION
    monkeypatch.setattr(
        f.credentials, "get", lambda *_: pytest.fail("restored control reached credentials")
    )
    assert subject.claim(restored) is None and subject.obligations(restored) == []
    for action in (subject.retry, subject.cleanup, subject.reconcile):
        with pytest.raises(subject.Conflict):
            action(restored, f.credentials, value["id"])
    assert not f.cloud.submissions


def test_completed_owner_with_missing_runtime_admits_explicit_successor(prepared):
    f = prepared
    value = enqueue(f)
    claimed = subject.claim(f.store)
    subject.checkpoint(f.store, value["id"], claimed["epoch"], state="completed", progress=1)
    before = f.store.one("SELECT * FROM runtime_preparations WHERE id=?", (value["id"],))
    reply = subject.request(f.store, f.credentials, f.fingerprint)
    assert reply["operation"]["state"] == "queued"
    assert reply["operation"]["runtime_preparation_id"] != value["id"]
    assert reply["deduplicated"] is False
    assert f.store.one("SELECT * FROM runtime_preparations WHERE id=?", (value["id"],)) == before


@pytest.mark.parametrize("hidden", [False, True])
def test_explicit_click_after_settled_hidden_owner_creates_visible_successor(prepared, hidden):
    f = prepared
    first = enqueue(f)
    subject.cancel(f.store, f.credentials, first["id"])
    if hidden:
        subject.hide(f.store, first["id"])
    else:
        f.client.post("/api/activity/clear", json={})
    assert not subject.activity(f.store)
    reply = subject.request(f.store, f.credentials, f.fingerprint)
    successor = reply["operation"]["runtime_preparation_id"]
    assert successor != first["id"] and reply["deduplicated"] is False
    assert subject.get(f.store, successor)["frozen"]["retry_of"] == first["id"]
    assert any(card["runtime_preparation_id"] == successor for card in subject.activity(f.store))


@pytest.mark.parametrize("hidden", [False, True])
def test_duplicate_retry_reveals_existing_settled_successor_without_requeueing(prepared, hidden):
    f = prepared
    parent = enqueue(f)
    subject.cancel(f.store, f.credentials, parent["id"])
    successor = subject.retry(f.store, f.credentials, parent["id"])["runtime_preparation_id"]
    subject.cancel(f.store, f.credentials, successor)
    if hidden:
        subject.hide(f.store, successor)
    else:
        f.client.post("/api/activity/clear", json={})
    assert not any(
        card["runtime_preparation_id"] == successor for card in subject.activity(f.store)
    )
    before = f.store.one("SELECT * FROM runtime_preparations WHERE id=?", (successor,))
    result = subject.retry(f.store, f.credentials, parent["id"])
    after = f.store.one("SELECT * FROM runtime_preparations WHERE id=?", (successor,))
    assert result["runtime_preparation_id"] == successor and result["state"] == "cancelled"
    assert {**after, "updated": before["updated"]} == before
    assert any(card["runtime_preparation_id"] == successor for card in subject.activity(f.store))
    assert subject.claim(f.store) is None and not f.cloud.submissions


def test_model_profile_change_does_not_bypass_uncertain_runtime_target(prepared):
    f = prepared
    value = enqueue(f)
    claimed = subject.claim(f.store)
    assert subject.begin_submission(f.store, value["id"], claimed["epoch"])
    subject.checkpoint(f.store, value["id"], claimed["epoch"], state="failed")
    settings = providers.Settings.model_validate(
        providers.profile(f.store, f.profile["id"])["settings"]
    )
    settings.model = "small"
    providers.save_profile(
        f.store, f.credentials, "Synthetic UCloud", settings, identifier=f.profile["id"]
    )
    changed = subject.fingerprint(api.connection(f.store, f.credentials, []))
    assert changed != f.fingerprint
    reply = subject.request(f.store, f.credentials, changed)
    assert reply["operation"]["runtime_preparation_id"] == value["id"]
    assert reply["deduplicated"] is True
    assert len(f.store.all("SELECT * FROM runtime_preparations")) == 1


@pytest.mark.parametrize("column,raw", [("frozen", "[]"), ("frozen", "{}"), ("journal", "{")])
def test_invalid_queued_owner_keeps_raw_evidence_without_starving_claim(prepared, column, raw):
    f = prepared
    first = enqueue(f)
    with f.store.connection() as db:
        second = subject._insert(db, first["frozen"], f.fingerprint)
    f.store.execute(f"UPDATE runtime_preparations SET {column}=? WHERE id=?", (raw, first["id"]))
    claimed = subject.claim(f.store)
    assert claimed["id"] == second["id"]
    bad = f.store.one("SELECT * FROM runtime_preparations WHERE id=?", (first["id"],))
    assert bad[column] == raw and bad["state"] == "failed"
    cards = subject.activity(f.store)
    unverified = next(item for item in cards if item["runtime_preparation_id"] == first["id"])
    assert unverified["needs_attention"] and not any(
        unverified.get(key)
        for key in ("can_cancel", "can_retry", "can_reconcile", "can_cleanup", "can_remove")
    )
    assert subject.obligations(f.store)
    if column == "journal":
        with f.store.connection() as db:
            assert runtime_store._capacity(db) == 1


def test_archived_malformed_owner_has_early_inert_activity_projection(prepared, monkeypatch):
    f = prepared
    value = enqueue(f)
    f.store.execute(
        "UPDATE runtime_preparations SET archived=1,frozen='[]',journal='{' WHERE id=?",
        (value["id"],),
    )
    monkeypatch.setattr(
        f.credentials, "get", lambda *_: pytest.fail("archival projection used credentials")
    )
    [card] = subject.activity(f.store)
    assert card["archived"] and card["can_remove"] and not card["needs_attention"]
    assert subject.claim(f.store) is None and subject.obligations(f.store) == []
    assert subject.hide(f.store, value["id"]) == {"hidden": True}
    assert not subject.activity(f.store)


def test_unrelated_invalid_frozen_refuses_new_retry_without_losing_raw_proof(prepared):
    f = prepared
    parent = enqueue(f)
    subject.cancel(f.store, f.credentials, parent["id"])
    with f.store.connection() as db:
        other = subject._insert(db, parent["frozen"], f.fingerprint)
    f.store.execute("UPDATE runtime_preparations SET frozen='{' WHERE id=?", (other["id"],))
    with pytest.raises(subject.Conflict, match="unverified"):
        subject.retry(f.store, f.credentials, parent["id"])
    assert len(f.store.all("SELECT * FROM runtime_preparations")) == 2
    assert (
        f.store.one("SELECT frozen FROM runtime_preparations WHERE id=?", (other["id"],))["frozen"]
        == "{"
    )
    assert not f.cloud.submissions
    f.store.execute("UPDATE runtime_preparations SET archived=1 WHERE id=?", (other["id"],))
    assert subject.retry(f.store, f.credentials, parent["id"])["state"] == "queued"


def test_slow_reconciliation_does_not_block_stop_of_another_owner(prepared, monkeypatch):
    f = prepared
    first = enqueue(f)
    claimed = subject.claim(f.store)
    assert subject.begin_submission(f.store, first["id"], claimed["epoch"])
    subject.checkpoint(f.store, first["id"], claimed["epoch"], state="failed")
    with f.store.connection() as db:
        second = subject._insert(db, first["frozen"], f.fingerprint)
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()

    def browse(**kwargs):
        entered.set()
        assert release.wait(5)
        return {"items": [], "next": None}

    def stop():
        result = subject.cancel(f.store, f.credentials, second["id"])
        stopped.set()
        return result

    monkeypatch.setattr(f.cloud, "browse_jobs", browse)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reconciliation = pool.submit(subject.reconcile, f.store, f.credentials, first["id"])
        try:
            assert entered.wait(2)
            cancellation = pool.submit(stop)
            assert stopped.wait(1), "Stop of another owner waited on reconciliation network I/O"
        finally:
            release.set()
        assert cancellation.result(timeout=2)["state"] == "cancelled"
        assert reconciliation.result(timeout=2)["requires_confirmation"]


def test_stop_during_accepted_post_drains_one_reply_and_terminates_only_recorded_id(
    prepared, monkeypatch
):
    f = prepared
    value = enqueue(f)
    original = f.cloud.submit_job
    f.cloud.state = "RUNNING"

    def submit(specification):
        reply = original(specification)
        assert subject.cancel(f.store, f.credentials, value["id"])["state"] == "running"
        return reply

    monkeypatch.setattr(f.cloud, "submit_job", submit)
    final, _ = execute(f)
    assert final["state"] == "cancelled"
    assert final["journal"]["stop_intent"] and not final["journal"]["capacity_reserved"]
    assert len(f.cloud.submissions) == 1 and f.cloud.terminations == ["remote-preparation"]


def test_stop_during_public_upload_prevents_submission(prepared, monkeypatch):
    f = prepared
    value = enqueue(f)
    upload = api.native.upload

    def stop_after_upload(*args, **kwargs):
        upload(*args, **kwargs)
        subject.cancel(f.store, f.credentials, value["id"])

    monkeypatch.setattr(api.native, "upload", stop_after_upload)
    final, _ = execute(f)
    assert final["state"] == "cancelled" and final["journal"]["cleanup_state"] == "pending"
    assert len(f.cloud.uploads) == 1 and not f.cloud.submissions


def test_native_full_gpu_identity_is_not_a_preparation_fallback(prepared, monkeypatch):
    f = prepared
    enqueue(f)
    monkeypatch.setattr(
        runtime_compute,
        "freeze",
        lambda *_: {
            "runtime_product": {
                "id": "gpu-nvidia-b200-1-gpu",
                "category": "gpu-nvidia-b200",
                "provider": "ucloud",
            },
            "runtime_compute_policy": runtime_compute.POLICY,
        },
    )
    final, _ = execute(f)
    assert final["state"] == "failed" and not f.cloud.submissions and not f.cloud.uploads


def test_real_queue_lane_drains_with_the_same_store_lease(prepared, monkeypatch):
    f = prepared
    queue = f.client.app.state.queue
    completed = threading.Event()
    checkpoint = subject.checkpoint
    submit = f.cloud.submit_job

    def ready_after_submit(specification):
        reply = submit(specification)
        f.cloud.ready = True
        return reply

    def observed_checkpoint(*args, **kwargs):
        value = checkpoint(*args, **kwargs)
        if value["state"] == "completed":
            completed.set()
        return value

    monkeypatch.setattr(f.cloud, "submit_job", ready_after_submit)
    monkeypatch.setattr(subject, "checkpoint", observed_checkpoint)
    enqueue(f)
    with f.store.library_lock():
        queue.start()
        try:
            queue.notify()
            assert completed.wait(8)
        finally:
            queue.stop()
    assert len(f.cloud.submissions) == 1
    assert all(not worker.thread.is_alive() and not worker.active_ids for worker in queue.workers)
    assert f.store.all("SELECT * FROM jobs") == []


def test_activity_bounds_recent_history_and_keeps_old_unresolved_and_unverified(prepared):
    f = prepared
    seed = enqueue(f)
    subject.cancel(f.store, f.credentials, seed["id"])
    with f.store.connection() as db:
        for index in range(1, 206):
            identifier = f"{index:032x}"
            stamp = f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}+00:00"
            journal = {"run_name": "CBS runtime preparation " + identifier}
            db.execute(
                "INSERT INTO runtime_preparations(id,connection_fingerprint,frozen,journal,state,"
                "created,updated) VALUES (?,?,?,?,?,?,?)",
                (
                    identifier,
                    f.fingerprint,
                    json.dumps(seed["frozen"]),
                    json.dumps(journal),
                    "cancelled",
                    stamp,
                    stamp,
                ),
            )
        db.execute("UPDATE runtime_preparations SET frozen='{' WHERE id=?", (f"{1:032x}",))
        db.execute(
            "UPDATE runtime_preparations SET journal=? WHERE id=?",
            (
                json.dumps(
                    {
                        "run_name": "CBS runtime preparation " + f"{2:032x}",
                        "submission_intent": True,
                    }
                ),
                f"{2:032x}",
            ),
        )
        db.execute(
            "UPDATE runtime_preparations SET archived=1,frozen='{' WHERE id=?", (f"{3:032x}",)
        )
    cards = subject.activity(f.store)
    assert len(cards) == 202
    ids = {card["runtime_preparation_id"] for card in cards}
    assert {f"{1:032x}", f"{2:032x}"} <= ids and f"{3:032x}" not in ids
    assert len(f.store.all("SELECT id FROM runtime_preparations")) == 206


def test_old_retry_reveal_survives_more_than_two_hundred_newer_settled_owners(prepared):
    f = prepared
    parent = enqueue(f)
    subject.cancel(f.store, f.credentials, parent["id"])
    child = subject.retry(f.store, f.credentials, parent["id"])["runtime_preparation_id"]
    subject.cancel(f.store, f.credentials, child)
    f.store.execute(
        "UPDATE runtime_preparations SET created='2025-01-01',updated='2025-01-01' WHERE id=?",
        (child,),
    )
    with f.store.connection() as db:
        for index in range(1000, 1205):
            identifier = f"{index:032x}"
            db.execute(
                "INSERT INTO runtime_preparations(id,connection_fingerprint,frozen,journal,state,"
                "created,updated) VALUES (?,?,?,?,?,?,?)",
                (
                    identifier,
                    f.fingerprint,
                    json.dumps(parent["frozen"]),
                    json.dumps({"run_name": "CBS runtime preparation " + identifier}),
                    "cancelled",
                    "2025-12-01",
                    "2025-12-01",
                ),
            )
    assert not any(card["runtime_preparation_id"] == child for card in subject.activity(f.store))
    reply = subject.retry(f.store, f.credentials, parent["id"])
    assert reply["runtime_preparation_id"] == child and reply["state"] == "cancelled"
    assert any(card["runtime_preparation_id"] == child for card in subject.activity(f.store))
    assert (
        len(f.store.all("SELECT id FROM runtime_preparations")) == 207 and not f.cloud.submissions
    )


def test_delayed_doctor_completion_does_not_overwrite_explicit_terminal_cancel(
    prepared, monkeypatch
):
    f = prepared
    first = enqueue(f)
    claimed = subject.claim(f.store)
    worker = SimpleNamespace(store=f.store, credentials=f.credentials, stopping=threading.Event())
    entered, release = threading.Event(), threading.Event()
    submit, read_json = f.cloud.submit_job, api.read_json

    def successful_submission(specification):
        reply = submit(specification)
        f.cloud.ready = True
        return reply

    def held_doctor(*args, **kwargs):
        if args[2].endswith("/doctor.json"):
            entered.set()
            assert release.wait(8)
        return read_json(*args, **kwargs)

    monkeypatch.setattr(f.cloud, "submit_job", successful_submission)
    monkeypatch.setattr(api, "read_json", held_doctor)
    with f.store.library_lock(), ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(subject.Run(worker, claimed).execute)
        try:
            assert entered.wait(5)
            reply = f.client.post(f"/api/runtime-preparations/{first['id']}/cancel", json={})
            assert reply.json()["state"] == "cancelled"
        finally:
            release.set()
        future.result(timeout=5)
    assert subject.get(f.store, first["id"])["state"] == "cancelled"
    assert len(f.cloud.submissions) == 1 and not f.cloud.terminations


def test_stop_of_recorded_remote_does_not_wait_for_another_preparation_lane(prepared, monkeypatch):
    f = prepared
    first = enqueue(f)
    claimed = subject.claim(f.store)
    assert subject.begin_submission(f.store, first["id"], claimed["epoch"])
    subject.checkpoint(
        f.store,
        first["id"],
        claimed["epoch"],
        state="failed",
        journal={"job_id": "remote-preparation", "remote_state": "RUNNING"},
    )
    f.cloud.state = "RUNNING"
    f.cloud.submissions.append({"name": first["journal"]["run_name"]})
    settings = providers.Settings.model_validate(
        providers.profile(f.store, f.profile["id"])["settings"]
    )
    settings.ucloud_transcriber.storage_path = "/drive/other"
    providers.save_profile(
        f.store, f.credentials, "Synthetic UCloud", settings, identifier=f.profile["id"]
    )
    f.fingerprint = subject.fingerprint(api.connection(f.store, f.credentials, []))
    second = enqueue(f)
    entered, release = threading.Event(), threading.Event()

    def held_run(run):
        assert run.identifier == second["id"]
        entered.set()
        assert release.wait(8)

    monkeypatch.setattr(subject.Run, "execute", held_run)
    from cbs_transcriber.jobs import JobQueue

    queue = JobQueue(f.store, f.credentials, kind="runtime-prepare")
    with f.store.library_lock():
        queue.start()
        try:
            assert entered.wait(3)
            response = f.client.post(f"/api/runtime-preparations/{first['id']}/cancel", json={})
            assert response.status_code == 202, response.text
            assert f.cloud.terminations == ["remote-preparation"], (
                "Stop waited behind another long preparation"
            )
        finally:
            release.set()
            queue.stop()
