"""ADR 0176 Stage 2: disabled generation owner, exclusive folders and sole promotion.

Synthetic storage, an in-memory UCloud drive and mocked clients only. Nothing contacts
UCloud or Hugging Face, prepares a real folder, downloads a model or touches a real library.
"""

import copy
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from test_huggingface_runtime_plan import configured as configured
from test_runtime_preparation import execute
from ucloud_workflow import UCloudAPIError

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import providers, runtime_batches, runtime_compute, runtime_store
from cbs_transcriber import runtime_generation_preparation as subject
from cbs_transcriber import runtime_generations as generations
from cbs_transcriber import runtime_preparation as preparation
from cbs_transcriber import runtime_preparation_observation as observation

REAL = {name: getattr(observation, name) for name in ("state", "read_json", "stamp")}
GATES = (
    (subject, "ENABLED"),
    (generations, "CANDIDATE_RESERVATION_ENABLED"),
    (generations, "ACTIVE_POINTERS_ENABLED"),
)


class Drive:
    """Minimal UCloud project drive and job API: a directory is None, a file is bytes."""

    def __init__(self, f, monkeypatch):
        self.f, self.cloud = f, f.cloud
        self.items = {"/drive": None, "/drive/cbs": None, "/drive/cbs/cbs-runtime": None}
        self.kinds, self.touched, self.creates = {}, [], []
        self.create_modes, self.on_create, self.spec_override = {}, None, None
        self.complete, self.damage = True, None
        monkeypatch.setattr(observation, "control", self.control)
        monkeypatch.setattr(observation, "read_bytes", self.read_bytes)
        for name, function in REAL.items():
            monkeypatch.setattr(observation, name, function)
        monkeypatch.setattr(api, "ensure_folder", self.ensure_folder)
        monkeypatch.setattr(api.native, "upload", self.upload)
        self.cloud.create_folder = self.create_folder
        self.cloud.retrieve_file = self.retrieve_file
        submit = self.cloud.submit_job

        def submitted(specification):
            reply = submit(specification)
            if self.complete:
                self.finish(specification)
            return reply

        self.cloud.submit_job = submitted

        def legacy(*args, **kwargs):
            pytest.fail("A generation owner reached a legacy-root helper.")

        for module, name in (
            (api, "stamped"),
            (api, "runtime_metadata"),
            (api, "read_json"),
            (runtime_batches, "require_preparation_idle"),
            (runtime_batches, "ensure_use_lock"),
        ):
            monkeypatch.setattr(module, name, legacy)

    def mkdirs(self, path):
        parts = path.split("/")
        for depth in range(2, len(parts) + 1):
            self.items.setdefault("/".join(parts[:depth]), None)

    def put(self, path, data):
        self.mkdirs(path.rsplit("/", 1)[0])
        self.items[path] = data if isinstance(data, bytes) else json.dumps(data).encode()

    def describe(self, path):
        data = self.items[path]
        kind = self.kinds.get(path, "DIRECTORY" if data is None else "FILE")
        status = {"type": kind}
        if data is not None:
            status["sizeInBytes"] = len(data)
        return {"id": path, "status": status}

    def control(self, client, method, path, *, deadline=None, missing=False, **kwargs):
        params = kwargs.get("params") or {}
        if (method, path) == ("GET", "/api/jobs/retrieve"):
            if not self.cloud.submissions or params["id"] != "remote-preparation":
                raise observation.Refused()
            specification = copy.deepcopy(self.spec_override or self.cloud.submissions[-1])
            return {
                "id": params["id"],
                "specification": specification,
                "status": {"state": self.cloud.state},
            }
        if (method, path) == ("GET", "/api/files/retrieve"):
            self.touched.append(params["id"])
            if params["id"] not in self.items:
                raise observation.Pending() if missing else observation.Refused()
            return self.describe(params["id"])
        assert (method, path) == ("GET", "/api/files/browse")
        folder = params["path"]
        self.touched.append(folder)
        children = sorted(item for item in self.items if item.rsplit("/", 1)[0] == folder)
        return {"items": [self.describe(item) for item in children], "next": None}

    def read_bytes(self, client, settings, path, *, deadline=None):
        self.touched.append(path)
        if self.items.get(path) is None:
            raise observation.Pending()
        return self.items[path]

    def create_folder(self, path):
        self.touched.append(path)
        self.creates.append(path)
        if self.on_create:
            self.on_create(path)
        mode = self.create_modes.get(path)
        if mode == "transport":
            raise httpx.ConnectError("synthetic lost request")
        if path in self.items:
            raise UCloudAPIError("synthetic exists", status_code=409)
        if path.rsplit("/", 1)[0] not in self.items:
            raise UCloudAPIError("synthetic missing parent", status_code=400)
        self.items[path] = None
        if mode == "lost":
            raise httpx.ReadTimeout("synthetic lost reply")
        if mode == "malformed":
            return {"responses": [{"id": "/drive/elsewhere"}]}
        return {}

    def ensure_folder(self, client, path):
        self.touched.append(path)
        assert "-g-" in path, "Only a generation's own folders may be ensured."
        self.mkdirs(path)

    def retrieve_file(self, path):
        self.touched.append(path)
        if self.items.get(path) is None:
            raise UCloudAPIError("synthetic missing", status_code=404)
        return self.describe(path)

    def upload(self, client, server, path, target, guard, update, **kwargs):
        guard()
        self.touched.append(target)
        assert target.rsplit("/", 1)[0] in self.items
        self.items[target] = path.read_bytes()

    def finish(self, specification):
        run, root = (item["path"] for item in specification["resources"])
        owner = specification["name"].rsplit(" ", 1)[1]
        settings = self.f.settings
        for name in ("bin", "hf", "nltk", "python", "venv"):
            self.mkdirs(root + "/" + name)
        self.put(root + "/stamp.json", api.runtime_stamp_bytes())
        self.put(root + "/.in-use", b"")
        self.put(root + "/requirements.txt", api.requirements_bytes())
        plan = self.f.plan["model_plan"]
        for name in plan["models"]:
            if name != "speakers":
                self.put(api.model_folder(settings, name, root=root) + "/model.bin", b"w")
        for language in ("danish", "english"):
            for name in ("abbrev_types.txt", "collocations.tab", "ortho_context.tab"):
                self.put(f"{root}/nltk/tokenizers/punkt_tab/{language}/{name}", b"p")
            self.put(f"{root}/nltk/tokenizers/punkt_tab/{language}/sent_starters.txt", b"p")
        self.put(run + "/supervisor.json", {"phase": "finished", "generation": owner})
        self.put(run + "/doctor.json", self.cloud.doctor)
        self.put(
            run + "/cache.json",
            {"models": dict.fromkeys(plan["models"], True), "punkt_tab": True},
        )
        if self.damage:
            self.damage(self, run, root)


@pytest.fixture
def env(configured, monkeypatch):  # noqa: F811 - pytest fixture injection
    f = configured
    for module, name in GATES:
        monkeypatch.setattr(module, name, True)
    monkeypatch.setattr(preparation, "TERMINAL_SETTLE_ATTEMPTS", 2)
    f.settings = providers.Settings.model_validate(
        api.connection(f.store, f.credentials, [])["settings"]
    )
    f.legacy = api.runtime_folder(f.settings)
    f.drive = Drive(f, monkeypatch)
    return f


def admit(f):
    confirmation = subject.preview(f.store, f.credentials)
    result = subject.admit(f.store, f.credentials, confirmation, lambda: None)
    return preparation.get(f.store, result["operation"]["runtime_preparation_id"]), confirmation


def registry(f, value):
    with f.store.connection() as db:
        return generations.snapshot(db, value["frozen"]["generation"]["target"])


def raw_settings(f):
    return f.store.all("SELECT id,value FROM settings ORDER BY id")


def roots(value, f):
    generation = value["frozen"]["generation"]
    return (
        generations.generation_root(f.settings, generation["target"], generation["token"]),
        api.run_folder(f.settings, value["id"]),
    )


def assert_legacy_untouched(f):
    assert all(path != f.legacy and not path.startswith(f.legacy + "/") for path in f.drive.touched)


def assert_unpromoted(f, value):
    entry = registry(f, value)
    assert entry["active"] is None and entry["revision"] == 0
    assert entry["candidate"]["owner_id"] == value["id"]


def test_every_gate_defaults_off_and_refuses_before_any_effect(configured, monkeypatch):
    f = configured
    assert [getattr(module, name) for module, name in GATES] == [False, False, False]
    for module, name in ((api.native, "client_for"), (runtime_compute, "freeze")):
        monkeypatch.setattr(module, name, lambda *_, **__: pytest.fail("network while disabled"))
    before = raw_settings(f)
    calls = (
        lambda: subject.preview(f.store, f.credentials),
        lambda: subject.admit(f.store, f.credentials, {}, lambda: None),
        lambda: subject.verify_and_promote(f.store, "a" * 32, 1, None, time.monotonic() + 9),
        lambda: subject.active(f.store, {}),
    )
    for call in calls:
        with pytest.raises(subject.Conflict, match="disabled"):
            call()
    assert not f.store.all("SELECT id FROM runtime_preparations")
    assert raw_settings(f) == before


def test_disabled_worker_fails_a_generation_owner_without_credentials_or_cloud(env, monkeypatch):
    f = env
    value, _ = admit(f)
    monkeypatch.setattr(subject, "ENABLED", False)
    monkeypatch.setattr(api.native, "client_for", lambda *_, **__: pytest.fail("cloud"))
    monkeypatch.setattr(f.credentials, "get", lambda *_: pytest.fail("credentials"))
    final, _ = execute(f)
    assert final["id"] == value["id"] and final["state"] == "failed"
    assert final["message"] == subject.DISABLED and not f.drive.touched
    assert registry(f, value)["candidate"]["owner_id"] == value["id"]


def test_admission_inserts_owner_and_candidate_in_one_writer_and_keeps_other_slots(env):
    f = env
    before = raw_settings(f)
    assert 11 not in {row["id"] for row in before}
    value, confirmation = admit(f)
    generation = value["frozen"]["generation"]
    entry = registry(f, value)
    assert entry["revision"] == 0 and entry["active"] is None
    assert entry["candidate"] == {
        "token": generation["token"],
        "owner_id": value["id"],
        "expected_revision": 0,
        "operation_sha256": confirmation["operation_sha256"],
        "compute": confirmation["operation"]["compute"],
    }
    assert value["connection_fingerprint"] == f.plan["connection_fingerprint"]
    assert value["frozen"]["model_plan"] == f.plan["model_plan"]
    assert value["journal"] == {"run_name": "CBS runtime generation " + value["id"]}
    assert confirmation["operation"]["compute"]["device"] == "cuda"
    assert [row for row in raw_settings(f) if row["id"] != 11] == before
    assert not f.drive.touched and not f.cloud.submissions
    card = preparation.card(value)
    assert card["kind"] == "runtime-preparation" and card["state"] == "queued"


def test_duplicate_confirmation_returns_the_same_owner_and_stale_ones_refuse(env, monkeypatch):
    f = env
    confirmation = subject.preview(f.store, f.credentials)
    first = subject.admit(f.store, f.credentials, confirmation, lambda: None)
    again = subject.admit(f.store, f.credentials, confirmation, lambda: None)
    assert again["deduplicated"] and again["operation"]["id"] == first["operation"]["id"]
    with pytest.raises(subject.Conflict, match="unresolved"):
        subject.preview(f.store, f.credentials)
    monkeypatch.setattr(
        api, "script_application", lambda _: {"name": "terminal-debian", "version": "other"}
    )
    with pytest.raises(subject.Conflict, match="changed"):
        subject.admit(f.store, f.credentials, confirmation, lambda: None)
    forged = {**confirmation, "expected_revision": 1}
    with pytest.raises(subject.Conflict):
        subject.admit(f.store, f.credentials, forged, lambda: None)
    assert len(f.store.all("SELECT id FROM runtime_preparations")) == 1


@pytest.mark.parametrize("failure", ["reservation", "occupied-slot", "guard"])
def test_failed_reservation_or_fence_leaves_no_owner_or_candidate(env, monkeypatch, failure):
    f = env
    confirmation = subject.preview(f.store, f.credentials)
    guard = lambda: None  # noqa: E731
    if failure == "reservation":

        def refuse(*args, **kwargs):
            raise generations.Conflict("synthetic refused reservation")

        monkeypatch.setattr(generations, "reserve_candidate", refuse)
    elif failure == "occupied-slot":
        f.store.execute("INSERT INTO settings(id,value) VALUES (11,'{}')")
    else:
        calls = []

        def guard():
            calls.append(1)
            if len(calls) == 3:  # Fails only after BEGIN, at the acceptance point.
                raise subject.Conflict("synthetic HF access revoked")

    before = raw_settings(f)
    with pytest.raises(ValueError):
        subject.admit(f.store, f.credentials, confirmation, guard)
    assert not f.store.all("SELECT id FROM runtime_preparations")
    assert raw_settings(f) == before


@pytest.mark.parametrize("product", ["cpu", "full-gpu"])
def test_cpu_or_unverified_compute_refuses_before_reservation(env, monkeypatch, product):
    f = env
    chosen = {
        "cpu": {"id": "cpu-amd-zen5-8-vcpu", "category": "cpu-amd-zen5", "provider": "ucloud"},
        "full-gpu": {
            "id": "gpu-nvidia-b200-1-gpu",
            "category": "gpu-nvidia-b200",
            "provider": "ucloud",
        },
    }[product]
    monkeypatch.setattr(
        runtime_compute,
        "freeze",
        lambda *_: {"runtime_product": chosen, "runtime_compute_policy": runtime_compute.POLICY},
    )
    with pytest.raises(subject.Conflict, match="CPU preparation"):
        subject.preview(f.store, f.credentials)
    cpu = {"product": chosen, "device": "cpu", "flavor": "cpu"}
    with pytest.raises(ValueError):
        subject._cuda(cpu)
    assert not f.store.all("SELECT id FROM runtime_preparations")


def test_success_uses_only_its_own_folders_and_promotes_owner_and_pointer_atomically(env):
    f = env
    value, confirmation = admit(f)
    root, run = roots(value, f)
    final, _ = execute(f)
    assert final["state"] == "completed", final["message"]
    journal = final["journal"]
    folders = journal["generation_folders"]
    assert [folders[kind]["path"] for kind in subject.FOLDERS] == [root, run]
    assert {entry["outcome"] for entry in folders.values()} == {"acknowledged"}
    assert journal["generation_submission"]["epoch"] == folders["run"]["epoch"]
    assert f.drive.creates == [root, run] and journal["owned_folder"] == run
    assert root == f.legacy + "-g-" + value["frozen"]["generation"]["token"]
    spec = f.cloud.submissions[0]
    assert spec["name"] == "CBS runtime generation " + value["id"]
    assert spec["product"] == runtime_compute.MIG_PRODUCT
    assert spec["resources"] == [
        {"type": "file", "path": run, "readOnly": False},
        {"type": "file", "path": root, "readOnly": False},
    ]
    script = f.drive.items[run + "/supervisor.sh"].decode()
    assert "RT=/work/" + root.rsplit("/", 1)[1] + "\n" in script
    assert "PREPARE=1" in script and "PREPARE_ONLY=1" in script
    assert 'MODELS="coarse,da,en,whisper-large-v3"' in script
    assert_legacy_untouched(f)
    entry = registry(f, final)
    generation = final["frozen"]["generation"]
    assert entry["candidate"] is None and entry["revision"] == 1
    assert entry["active"] == {
        "token": generation["token"],
        "owner_id": final["id"],
        "operation_sha256": confirmation["operation_sha256"],
        "compute": confirmation["operation"]["compute"],
        "promoted_revision": 1,
    }
    assert journal["generation_promotion"]["registry_revision"] == 1
    assert journal["model_plan_receipt"]["evidence"]["speaker_marker"] is True
    assert not journal["capacity_reserved"] and len(f.cloud.submissions) == 1
    assert subject.active(f.store, generation["target"])["root"] == root
    rows, settings = f.store.all("SELECT * FROM runtime_preparations"), raw_settings(f)
    deadline = time.monotonic() + 30
    assert subject.verify_and_promote(f.store, final["id"], final["epoch"], f.cloud, deadline)
    assert f.store.all("SELECT * FROM runtime_preparations") == rows
    assert raw_settings(f) == settings


@pytest.mark.parametrize("kind", subject.FOLDERS)
def test_existing_folder_is_never_adopted_and_nothing_is_submitted(env, kind):
    f = env
    value, _ = admit(f)
    root, run = roots(value, f)
    f.drive.mkdirs(root if kind == "environment" else run)
    final, _ = execute(f)
    folders = final["journal"]["generation_folders"]
    assert final["state"] == "failed" and folders[kind]["outcome"] == "conflict"
    assert f.drive.creates == ([root] if kind == "environment" else [root, run])
    assert "owned_folder" not in final["journal"] and not f.cloud.submissions
    card = preparation.card(final)
    assert not card["can_retry"] and not card["can_reconcile"]
    with pytest.raises(preparation.Conflict):
        preparation.retry(f.store, f.credentials, final["id"])
    with pytest.raises(preparation.Conflict, match="never reconciled"):
        preparation.reconcile(f.store, f.credentials, final["id"])
    assert_unpromoted(f, final)


@pytest.mark.parametrize(
    ("mode", "kind"),
    [("lost", "environment"), ("transport", "environment"), ("malformed", "run"), ("lost", "run")],
)
def test_unacknowledged_create_stays_an_unresolved_intent_without_retry(env, mode, kind):
    f = env
    value, _ = admit(f)
    root, run = roots(value, f)
    f.drive.create_modes[root if kind == "environment" else run] = mode
    final, _ = execute(f)
    assert final["state"] == "failed"
    assert final["journal"]["generation_folders"][kind]["outcome"] == "uncertain"
    assert preparation.unresolved(final) and preparation.card(final)["needs_attention"]
    assert final["id"] in {item["id"] for item in preparation.obligations(f.store)}
    assert len(f.drive.creates) == (1 if kind == "environment" else 2)
    assert not f.cloud.submissions and "owned_folder" not in final["journal"]
    with pytest.raises(preparation.Conflict):
        preparation.retry(f.store, f.credentials, final["id"])
    assert_unpromoted(f, final)


def test_generic_writer_cannot_forge_a_decoder_valid_folder_acknowledgment(env):
    f = env
    value, _ = admit(f)
    root, _run = roots(value, f)
    f.drive.create_modes[root] = "lost"
    failed, _ = execute(f)
    folders = copy.deepcopy(failed["journal"]["generation_folders"])
    assert folders["environment"]["outcome"] == "uncertain"
    folders["environment"]["outcome"] = "acknowledged"  # Shape-valid, so decoding accepts it.
    before = f.store.all("SELECT * FROM runtime_preparations")
    with pytest.raises(preparation.Conflict, match="verified writers"):
        preparation.checkpoint(
            f.store, value["id"], failed["epoch"], journal={"generation_folders": folders}
        )
    assert f.store.all("SELECT * FROM runtime_preparations") == before


def test_stop_during_create_drains_its_reply_and_prevents_every_later_post(env):
    f = env
    value, _ = admit(f)
    root, _run = roots(value, f)

    def stop(path):
        if path == root:
            assert preparation.cancel(f.store, f.credentials, value["id"])["state"] == "running"

    f.drive.on_create = stop
    final, _ = execute(f)
    assert final["state"] == "cancelled" and final["journal"]["stop_intent"]
    assert final["journal"]["generation_folders"]["environment"]["outcome"] == "acknowledged"
    assert set(final["journal"]["generation_folders"]) == {"environment"}
    assert f.drive.creates == [root] and not f.cloud.submissions
    assert not any(path.endswith(".sh") for path in f.drive.touched)


@pytest.mark.parametrize("problem", ["missing", "soft-link", "file"])
def test_namespace_must_already_be_a_verified_directory(env, problem):
    f = env
    admit(f)
    namespace = "/drive/cbs/cbs-runtime"
    if problem == "missing":
        del f.drive.items[namespace]
    elif problem == "soft-link":
        f.drive.kinds[namespace] = "SOFT_LINK"
    else:
        f.drive.items[namespace] = b"not a folder"
    final, _ = execute(f)
    assert final["state"] == "failed" and "generation_folders" not in final["journal"]
    assert not f.drive.creates and not f.cloud.submissions


def _spec(drive, run, root):
    drive.spec_override = copy.deepcopy(drive.cloud.submissions[-1])
    drive.spec_override["resources"][1]["path"] = drive.f.legacy


def _supervisor(drive, run, root):
    drive.put(run + "/supervisor.json", {"phase": "finished", "generation": "b" * 32})


def _doctor(drive, run, root):
    doctor = copy.deepcopy(drive.cloud.doctor)
    doctor["gpu"]["devices"][0]["memory_gb"] = 4
    drive.put(run + "/doctor.json", doctor)


def _stamp(drive, run, root):
    drive.put(root + "/stamp.json", b'{"requirements_sha256":"' + b"0" * 64 + b'"}')


def _lock(drive, run, root):
    drive.mkdirs(root + "/.prepare-lock")


def _link(drive, run, root):
    drive.kinds[root + "/.in-use"] = "SOFT_LINK"


def _extra(drive, run, root):
    drive.put(root + "/stamp.pending", b"{}")


def _cache(drive, run, root):
    drive.put(run + "/cache.json", {"models": {"coarse": True}, "punkt_tab": True})


def _model(drive, run, root):
    folder = api.model_folder(drive.f.settings, "whisper-large-v3", root=root)
    del drive.items[folder + "/model.bin"]


def _speaker(drive, run, root):
    folder = api.model_folder(drive.f.settings, "speakers", root=root)
    drive.put(folder + "/.cbs-complete", b"someone/else@" + b"0" * 40 + b"\n")


def _failure(drive, run, root):
    drive.cloud.state = "FAILURE"


@pytest.mark.parametrize(
    "damage",
    [_spec, _supervisor, _doctor, _stamp, _lock, _link, _extra, _cache, _model, _speaker, _failure],
    ids=lambda item: item.__name__.strip("_"),
)
def test_promotion_refuses_wrong_or_incomplete_evidence(env, damage):
    f = env
    value, _ = admit(f)
    f.drive.damage = damage
    final, _ = execute(f)
    assert final["state"] == "failed", final["message"]
    assert "generation_promotion" not in final["journal"]
    assert final["journal"].get("prepared") is not True
    assert_unpromoted(f, final)
    assert len(f.cloud.submissions) == 1 and len(f.drive.creates) == 2
    assert_legacy_untouched(f)


def test_stop_recorded_during_verification_wins_before_the_writer(env, monkeypatch):
    f = env
    value, _ = admit(f)
    stamp = REAL["stamp"]

    def stop_then_stamp(*args, **kwargs):
        fresh = preparation.get(f.store, value["id"])
        preparation.checkpoint(f.store, value["id"], fresh["epoch"], journal={"stop_intent": True})
        return stamp(*args, **kwargs)

    monkeypatch.setattr(observation, "stamp", stop_then_stamp)
    final, _ = execute(f)
    assert final["state"] == "cancelled" and "generation_promotion" not in final["journal"]
    assert f.cloud.terminations in ([], ["remote-preparation"])
    assert_unpromoted(f, final)


def test_known_id_recovery_observes_the_same_owner_without_new_folders_or_posts(env, monkeypatch):
    f = env
    value, _ = admit(f)
    f.cloud.state, f.drive.complete = "RUNNING", False
    original_wait = preparation.Run._wait

    def shutdown(self, delay):
        self.worker.stopping.set()

    monkeypatch.setattr(preparation.Run, "_wait", shutdown)
    interrupted, _ = execute(f)
    monkeypatch.setattr(preparation.Run, "_wait", original_wait)
    assert interrupted["state"] == "interrupted"
    assert interrupted["journal"]["job_id"] == "remote-preparation"
    # A process crash leaves "running"; startup recovery requeues only a recorded ID.
    f.store.execute("UPDATE runtime_preparations SET state='running' WHERE id=?", (value["id"],))
    preparation.recover(f.store)
    assert preparation.get(f.store, value["id"])["state"] == "queued"
    f.drive.finish(f.cloud.submissions[-1])
    f.cloud.state = "SUCCESS"
    claimed = preparation.claim(f.store)
    with pytest.raises(preparation.Interrupted):
        subject.verify_and_promote(
            f.store, value["id"], claimed["epoch"] - 1, f.cloud, time.monotonic() + 30
        )
    worker = SimpleNamespace(store=f.store, credentials=f.credentials, stopping=threading.Event())
    preparation.Run(worker, claimed).execute()
    final = preparation.get(f.store, value["id"])
    assert final["state"] == "completed", final["message"]
    assert len(f.cloud.submissions) == 1 and len(f.drive.creates) == 2
    assert registry(f, final)["active"]["owner_id"] == value["id"]


def test_terminal_success_awaiting_verification_retries_only_the_same_owner(env):
    f = env
    value, _ = admit(f)
    f.drive.damage = _cache
    failed, _ = execute(f)
    assert failed["state"] == "failed" and failed["journal"]["remote_state"] == "SUCCESS"
    assert preparation.card(failed)["can_retry"]
    f.drive.put(
        api.run_folder(f.settings, value["id"]) + "/cache.json",
        {"models": dict.fromkeys(f.plan["model_plan"]["models"], True), "punkt_tab": True},
    )
    card = preparation.retry(f.store, f.credentials, value["id"])
    assert card["runtime_preparation_id"] == value["id"] and card["state"] == "queued"
    final, _ = execute(f)
    assert final["id"] == value["id"] and final["state"] == "completed", final["message"]
    assert len(f.store.all("SELECT id FROM runtime_preparations")) == 1
    assert len(f.cloud.submissions) == 1 and len(f.drive.creates) == 2


def test_uncertain_post_is_never_retried_reconciled_or_replaced(env):
    f = env
    value, _ = admit(f)
    f.cloud.submit_error = True
    final, _ = execute(f)
    journal = final["journal"]
    assert final["state"] == "failed" and journal["submission_intent"] and "job_id" not in journal
    card = preparation.card(final)
    assert card["ucloud_submission_uncertain"] and not card["can_retry"]
    assert not card["can_reconcile"]
    for action in (preparation.retry, preparation.reconcile):
        with pytest.raises(preparation.Conflict):
            action(f.store, f.credentials, value["id"])
    assert len(f.cloud.submissions) == 1
    assert len(f.store.all("SELECT id FROM runtime_preparations")) == 1
    assert_unpromoted(f, final)


@pytest.mark.parametrize("key", sorted(preparation.GENERATION_RESERVED))
def test_generic_writers_cannot_inject_or_alter_generation_proof(env, key):
    f = env
    value, _ = admit(f)
    before = f.store.all("SELECT * FROM runtime_preparations")
    with pytest.raises(preparation.Conflict):
        preparation.checkpoint(f.store, value["id"], value["epoch"], journal={key: True})
    assert f.store.all("SELECT * FROM runtime_preparations") == before


def test_generic_writers_refuse_completion_downgrade_and_legacy_helpers(env):
    f = env
    admit(f)
    value = preparation.claim(f.store)
    before = f.store.all("SELECT * FROM runtime_preparations")
    with pytest.raises(preparation.Conflict):
        preparation.checkpoint(f.store, value["id"], value["epoch"], state="completed")
    stripped = copy.deepcopy(value)
    del stripped["frozen"]["generation"]
    for change in (
        lambda db: preparation._update(db, stripped, message="downgrade"),
        lambda db: preparation._update(db, value, frozen="{}"),
        lambda db: preparation._update(db, value, connection_fingerprint="0" * 64),
    ):
        with f.store.connection() as db, pytest.raises(preparation.Conflict):
            db.execute("BEGIN IMMEDIATE")
            change(db)
    with pytest.raises(preparation.Conflict, match="verified receipts"):
        preparation.begin_submission(f.store, value["id"], value["epoch"])
    worker = SimpleNamespace(store=f.store, credentials=f.credentials, stopping=threading.Event())
    legacy = preparation.Run(worker, value)
    with pytest.raises(preparation.Conflict):
        legacy.submit()
    with pytest.raises(preparation.Conflict):
        legacy._complete_prepared({"prepared": True}, time.monotonic() + 30)
    assert f.store.all("SELECT * FROM runtime_preparations") == before
    assert not f.drive.touched and not f.cloud.submissions


def _legacy_owner(f):
    chosen = f.plan["chosen"]
    identity = preparation._identity(chosen, model_plan=f.plan["model_plan"])
    with f.store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        return preparation._insert(db, identity, f.plan["connection_fingerprint"])


@pytest.mark.parametrize(
    "forge",
    ["completed", "prepared", "legacy-mount", "stripped", "legacy-with-promotion", "intent"],
)
def test_decoder_refuses_forged_proof_and_legacy_rows_with_generation_keys(env, forge):
    f = env
    value = _legacy_owner(f) if forge == "legacy-with-promotion" else admit(f)[0]
    frozen, journal, state = dict(value["frozen"]), dict(value["journal"]), value["state"]
    if forge == "completed":
        state = "completed"
    elif forge == "prepared":
        journal["prepared"] = True
    elif forge == "legacy-mount":
        journal["specification"] = api.build_script_specification(
            f.settings,
            {"name": "terminal-ubuntu", "version": "synthetic"},
            value["id"],
            SimpleNamespace(timeout_minutes=1),
            True,
            runtime_product=runtime_compute.MIG_PRODUCT,
        )
    elif forge == "stripped":
        del frozen["generation"]
    elif forge == "legacy-with-promotion":
        journal["generation_promotion"] = {"format": subject.PROMOTION_FORMAT}
    else:
        journal["submission_intent"] = True
    f.store.execute(
        "UPDATE runtime_preparations SET frozen=?,journal=?,state=? WHERE id=?",
        (runtime_store.canonical(frozen), runtime_store.canonical(journal), state, value["id"]),
    )
    with pytest.raises(preparation.Conflict):
        preparation.get(f.store, value["id"])
    cards = [item for item in preparation.activity(f.store) if item["id"].endswith(value["id"])]
    assert cards and cards[0].get("preparation_unverified")


def test_legacy_paths_never_match_or_reuse_a_generation_owner(env, monkeypatch):
    f = env
    queued, _ = admit(f)
    identity = preparation._identity(f.plan["chosen"], model_plan=f.plan["model_plan"])
    assert preparation._target(queued["frozen"])[:4] == preparation._target(identity)
    assert preparation._target(queued["frozen"]) != preparation._target(identity)
    assert (
        preparation._admit(f.store, identity, f.plan["connection_fingerprint"], insert=False)
        is None
    )
    final, _ = execute(f)
    assert final["state"] == "completed"
    monkeypatch.setattr(api, "stamped", lambda *_: True)
    monkeypatch.setattr(
        preparation, "_model_evidence", lambda *_, **__: pytest.fail("legacy reused a receipt")
    )
    assert (
        preparation._model_prepared(f.store, f.cloud, f.settings, f.plan["model_plan"], identity)
        is False
    )
