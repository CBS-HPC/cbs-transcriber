"""Independent adversarial review of ADR 0176 Stage 2 (T144 / V219), builder commit e9dd23d.

Synthetic temporary libraries, an in-memory UCloud drive and mocked clients only. Nothing
contacts UCloud or Hugging Face, downloads a model, opens a real library or binds a port.
Known defects are strict xfails raising ``DefectReproduced`` so that a setup failure is
reported as an ordinary failure instead of being mistaken for the defect.
"""

import ast
import copy
import importlib.util
import inspect
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from test_huggingface_runtime_plan import configured as configured  # noqa: F401 - fixture
from test_huggingface_runtime_plan import evidence
from test_runtime_generation_preparation import (
    _cache,
    admit,
    assert_legacy_untouched,
    registry,
    roots,
)
from test_runtime_generation_preparation import env as env  # noqa: F401 - fixture
from test_runtime_preparation import enqueue, execute
from test_runtime_preparation import prepared as prepared  # noqa: F401 - fixture

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import runtime_compute, runtime_store
from cbs_transcriber import runtime_generation_preparation as subject
from cbs_transcriber import runtime_generations as generations
from cbs_transcriber import runtime_preparation as preparation
from cbs_transcriber import runtime_preparation_observation as observation

BASELINE = "e76399f"  # Parent of the reviewed builder commit e9dd23d.
PACKAGE = Path(subject.__file__).resolve().parent
REPOSITORY = PACKAGE.parents[1]
GATES = (
    (subject, "ENABLED"),
    (generations, "CANDIDATE_RESERVATION_ENABLED"),
    (generations, "ACTIVE_POINTERS_ENABLED"),
)
CPU = {"id": "cpu-amd-zen5-8-vcpu", "category": "cpu-amd-zen5", "provider": "ucloud"}
FULL_GPU = {"id": "gpu-nvidia-b200-1-gpu", "category": "gpu-nvidia-b200", "provider": "ucloud"}


class DefectReproduced(Exception):
    """Raised only at the exact point a reviewed defect is demonstrated."""


@pytest.fixture
def stage(env):  # noqa: F811 - all three gates on, synthetic drive installed
    return env


@pytest.fixture
def plain(configured):  # noqa: F811 - gates at their shipped defaults
    return configured


@pytest.fixture
def legacy_app(prepared):  # noqa: F811 - legacy preparation fixture only
    return prepared


# --------------------------------------------------------------------------- helpers


def _rows(f):
    return f.store.all("SELECT * FROM runtime_preparations ORDER BY id")


def _settings(f):
    return f.store.all("SELECT id,value FROM settings ORDER BY id")


def _row(f, identifier):
    return f.store.one("SELECT * FROM runtime_preparations WHERE id=?", (identifier,))


def _good_cache(f, value):
    _, run = roots(value, f)
    f.drive.put(
        run + "/cache.json",
        {"models": dict.fromkeys(f.plan["model_plan"]["models"], True), "punkt_tab": True},
    )


def _awaiting(f):
    """A claimed owner whose recorded allocation SUCCEEDED and awaits the sole promotion."""
    value, _ = admit(f)
    f.drive.damage = _cache
    failed, _ = execute(f)
    assert failed["state"] == "failed", failed["message"]
    assert failed["journal"]["remote_state"] == "SUCCESS"
    f.drive.damage = None
    _good_cache(f, value)
    preparation.retry(f.store, f.credentials, value["id"])
    claimed = preparation.claim(f.store)
    assert claimed["id"] == value["id"] and claimed["state"] == "running"
    return claimed


def _legacy(f, *, plan=True):
    chosen = f.plan["chosen"]
    model_plan = f.plan["model_plan"] if plan else preparation._ABSENT
    identity = preparation._identity(chosen, model_plan=model_plan)
    with f.store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        return preparation._insert(
            db, identity, preparation.fingerprint(chosen, model_plan=model_plan)
        )


def _fingerprint(frozen):
    keys = ["policy", "provider_id", "settings", "runtime_id", "recipe_sha256"]
    keys += [key for key in ("connection_policy", "model_plan") if key in frozen]
    return runtime_store._digest({key: frozen.get(key) for key in keys})


def _reseal(f, value, mutate):
    """Forge a fully self-consistent generation birth (every hash recomputed)."""
    frozen = copy.deepcopy(value["frozen"])
    generation = frozen["generation"]
    mutate(f, frozen, generation)
    fingerprint = _fingerprint(frozen)
    operation = generation["operation"]
    operation["connection_fingerprint"] = fingerprint
    generation["operation_sha256"] = runtime_store._digest(operation)
    generation["confirmation_sha256"] = subject._confirmation(
        fingerprint,
        generation["target"],
        operation,
        generation["operation_sha256"],
        generation["expected_revision"],
    )["confirmation_sha256"]
    f.store.execute(
        "UPDATE runtime_preparations SET frozen=?,connection_fingerprint=? WHERE id=?",
        (runtime_store.canonical(frozen), fingerprint, value["id"]),
    )


def _rewrite_registry(f, change):
    raw = f.store.one("SELECT value FROM settings WHERE id=11")["value"]
    value = json.loads(raw)
    (entry,) = value["targets"].values()
    change(entry)
    f.store.execute("UPDATE settings SET value=? WHERE id=11", (generations._encoded(value),))


def _under(path, root):
    return path == root or path.startswith(root + "/")


# ------------------------------------------------------------ 1. disabled-ness / reach


def test_gates_are_literal_false_and_never_reassigned_by_production_code():
    names = {gate for _, gate in GATES}
    definitions, writes = [], []
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets = [node.target]
            for target in targets:
                name = getattr(target, "id", None) or getattr(target, "attr", None)
                if name not in names:
                    continue
                literal = (
                    isinstance(node, ast.Assign)
                    and node in tree.body
                    and isinstance(target, ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and node.value.value is False
                )
                (definitions if literal else writes).append((path.name, name))
            if isinstance(node, ast.Global) and names & set(node.names):
                writes.append((path.name, "global"))
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "setattr"
                and any(isinstance(a, ast.Constant) and a.value in names for a in node.args)
            ):
                writes.append((path.name, "setattr"))
    # ``ENABLED`` is a common word; only the slice's own module may bind it at all.
    assert sorted(definitions) == [
        ("runtime_generation_preparation.py", "ENABLED"),
        ("runtime_generations.py", "ACTIVE_POINTERS_ENABLED"),
        ("runtime_generations.py", "CANDIDATE_RESERVATION_ENABLED"),
    ]
    assert writes == []
    for module in ("runtime_generation_preparation.py", "runtime_generations.py"):
        tree = ast.parse((PACKAGE / module).read_text(encoding="utf-8"))
        assert not [
            node
            for node in ast.walk(tree)
            if (getattr(node, "attr", None) or getattr(node, "id", None))
            in {"environ", "getenv", "environb"}
        ], module
    assert [getattr(module, gate) for module, gate in GATES] == [False, False, False]


def _scope(node, parents):
    names = []
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
    return ".".join(reversed(names)) or "<module>"


def _call_sites():
    sites = []
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                keywords = {item.arg: item.value for item in node.keywords if item.arg}
                sites.append((path.name, _scope(node, parents), called, keywords, node))
    return sites


def test_every_privileged_writer_and_frozen_root_has_an_exact_reviewed_call_site():
    sites = _call_sites()

    def where(predicate):
        return sorted(
            (name, scope, called)
            for name, scope, called, kw, node in sites
            if predicate(called, kw, node)
        )

    slice_ = "runtime_generation_preparation.py"
    assert where(lambda c, kw, n: c == "_publish_active") == [
        (slice_, "verify_and_promote", "_publish_active")
    ]
    assert where(lambda c, kw, n: c == "reserve_candidate") == [
        (slice_, "admit", "reserve_candidate")
    ]
    assert where(lambda c, kw, n: c == "supersede_candidate") == [
        (slice_, "supersede", "supersede_candidate")
    ]
    assert where(lambda c, kw, n: c == "_insert" and "identifier" in kw) == [
        (slice_, "admit", "_insert"),
        (slice_, "supersede", "_insert"),
    ]
    assert where(lambda c, kw, n: c == "validate_owner") == [
        ("runtime_preparation.py", "_decode", "validate_owner")
    ]
    assert where(lambda c, kw, n: c == "GenerationRun") == [
        ("runtime_preparation.py", "Run.execute", "GenerationRun")
    ]
    assert where(lambda c, kw, n: c == "verify_and_promote") == [
        (slice_, "GenerationRun._settle_success", "verify_and_promote")
    ]
    assert where(lambda c, kw, n: "_generation_writer" in kw) == [
        (slice_, "_write", "_update"),
        (slice_, "supersede", "_update"),
        (slice_, "verify_and_promote", "_update"),
        ("runtime_preparation.py", "begin_submission", "_update"),
    ]
    flags = {
        scope: ast.unparse(kw["_generation_writer"])
        for _, scope, _, kw, _ in sites
        if "_generation_writer" in kw
    }
    assert flags["begin_submission"] == "require is not None"
    assert where(lambda c, kw, n: c == "begin_submission" and "require" in kw) == [
        (slice_, "GenerationRun.submit", "begin_submission")
    ]
    # Legacy execution, batches and repairs never pass a frozen generation root.
    rooted = {
        "build_script_specification": "runtime",
        "supervisor_script": "runtime",
        "seed_speakers": "root",
        "remote_speaker_gaps": "root",
        "model_folder": "root",
        "stamp": "root",
        "_model_evidence": "root",
    }
    assert where(lambda c, kw, n: rooted.get(c) in kw) == [
        ("enhancement_api.py", "remote_speaker_gaps", "model_folder"),
        ("enhancement_api.py", "seed_speakers", "remote_speaker_gaps"),
        ("enhancement_api.py", "seed_speakers", "remote_speaker_gaps"),
        (slice_, "GenerationRun.submit", "build_script_specification"),
        (slice_, "GenerationRun.submit", "seed_speakers"),
        (slice_, "GenerationRun.submit", "supervisor_script"),
        (slice_, "verify_and_promote", "_model_evidence"),
        (slice_, "verify_and_promote", "stamp"),
        ("runtime_preparation.py", "_model_evidence", "model_folder"),
    ]
    assert where(lambda c, kw, n: c == "frozen_runtime_root") == [
        ("enhancement_api.py", "build_script_specification", "frozen_runtime_root"),
        ("enhancement_api.py", "model_folder", "frozen_runtime_root"),
        ("enhancement_api.py", "seed_speakers", "frozen_runtime_root"),
        ("enhancement_api.py", "supervisor_script", "frozen_runtime_root"),
        ("runtime_preparation.py", "_model_evidence", "frozen_runtime_root"),
        ("runtime_preparation_observation.py", "stamp", "frozen_runtime_root"),
    ]
    dynamic = [
        (name, scope)
        for name, scope, called, kw, node in sites
        if called in {"import_module", "__import__"}
        and any(
            isinstance(arg, ast.Constant) and "runtime_generation" in str(arg.value)
            for arg in node.args
        )
    ]
    assert dynamic == []
    for function in (
        subject.verify_and_promote,
        generations._publish_active,
        generations.supersede_candidate,
        subject.admit,
        subject.supersede,
    ):
        kinds = {item.kind for item in inspect.signature(function).parameters.values()}
        assert not kinds & {inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL}
    assert list(inspect.signature(subject.verify_and_promote).parameters) == [
        "store",
        "owner",
        "epoch",
        "client",
        "deadline",
        "stopping",
    ]


def test_no_route_or_static_asset_names_the_slice(plain):
    paths = [getattr(route, "path", "") for route in plain.client.app.routes]
    assert paths and not [path for path in paths if "generation" in path.lower()]
    tokens = (
        "runtime_generation",
        "runtime-generation",
        "generation_promotion",
        "generation_folders",
        "generation_submission",
        "generation_supersession",
        "preview_supersession",
        "CBS runtime generation",
        "verify_and_promote",
    )
    for path in (PACKAGE / "static").rglob("*"):
        if path.suffix in {".js", ".html", ".css"}:
            text = path.read_text(encoding="utf-8")
            assert not [token for token in tokens if token in text], path.name


PROBE = r"""
import json, socket, sys
from pathlib import Path

_connect = socket.socket.connect


def guarded(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise OSError("reviewer probe refused a non-loopback connection")
    return _connect(self, address)


socket.socket.connect = guarded
from fastapi.testclient import TestClient

from cbs_transcriber import runtime_preparation as preparation
from cbs_transcriber.app import create_app


class Credentials:
    def get(self, base_url):
        return ""

    def set(self, base_url, value, persist):
        pass


app = create_app(Path(sys.argv[1]), start_worker=False, credentials=Credentials())
statuses = []
with TestClient(app, base_url="http://127.0.0.1", headers={"X-CBS-Request": "1"}) as client:
    statuses.append(client.get("/api/activity").status_code)
    statuses.append(client.get("/api/enhancement-api/status").status_code)
    statuses.append(client.post("/api/runtime-preparations/" + "a" * 32 + "/retry").status_code)
    store = app.state.store
    preparation.recover(store)
    preparation.claim(store)
    preparation.obligations(store)
    preparation.activity(store)
prefix = "cbs_transcriber.runtime_generation"
loaded = sorted(name for name in sys.modules if name.startswith(prefix))
print(json.dumps({"loaded": loaded, "statuses": statuses}))
"""


def test_a_fresh_process_serving_requests_and_recovery_never_loads_the_slice(tmp_path):
    folders = {name: tmp_path / name for name in ("home", "appdata", "local", "hf", "library")}
    for folder in folders.values():
        folder.mkdir()
    environment = {
        **os.environ,
        "PYTHONPATH": str(PACKAGE.parent),
        "USERPROFILE": str(folders["home"]),
        "HOME": str(folders["home"]),
        "APPDATA": str(folders["appdata"]),
        "LOCALAPPDATA": str(folders["local"]),
        "HF_HOME": str(folders["hf"]),
        "CBS_TRANSCRIBER_NO_STARTUP_CHECK": "1",
        "CBS_TRANSCRIBER_NO_REMOTE_RELEASE": "1",
        "CBS_TRANSCRIBER_PROFILE": "full",
    }
    result = subprocess.run(
        [sys.executable, "-B", "-s", "-c", PROBE, str(folders["library"])],
        capture_output=True,
        text=True,
        timeout=300,
        env=environment,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["loaded"] == []
    assert all(status < 500 for status in report["statuses"]), report


@pytest.mark.parametrize("gates", ["off", "on"])
@pytest.mark.parametrize("path", ["http-prepare", "huggingface"])
def test_legacy_preparation_entries_never_create_a_generation_row(plain, monkeypatch, gates, path):
    f = plain
    for module, name in GATES:
        monkeypatch.setattr(module, name, gates == "on")
    if path == "http-prepare":
        value = enqueue(f)
    else:
        result = preparation.request_huggingface(f.store, f.credentials, f.plan, lambda: None)
        value = preparation.get(f.store, result["operation"]["runtime_preparation_id"])
    for row in _rows(f):
        frozen, journal = json.loads(row["frozen"]), json.loads(row["journal"])
        assert "generation" not in frozen
        assert journal["run_name"] == "CBS runtime preparation " + row["id"]
        assert not preparation.GENERATION_ONLY & set(journal)
    assert value["journal"]["run_name"].startswith("CBS runtime preparation ")
    assert 11 not in {row["id"] for row in _settings(f)}


@pytest.mark.parametrize("candidate", [False, True])
@pytest.mark.parametrize("active", [False, True])
def test_every_entry_refuses_while_the_slice_gate_is_off_whatever_the_stage1_gates(
    plain, monkeypatch, candidate, active
):
    f = plain
    monkeypatch.setattr(generations, "CANDIDATE_RESERVATION_ENABLED", candidate)
    monkeypatch.setattr(generations, "ACTIVE_POINTERS_ENABLED", active)
    for module, name in (
        (api.native, "client_for"),
        (runtime_compute, "freeze"),
        (api, "script_application"),
        (preparation, "huggingface_plan"),
    ):
        monkeypatch.setattr(
            module, name, lambda *_, _name=name, **__: pytest.fail("reached " + _name)
        )
    rows, settings = _rows(f), _settings(f)
    confirmation = {key: None for key in subject._CONFIRMATION_KEYS}
    calls = (
        lambda: subject.preview(f.store, f.credentials),
        lambda: subject.admit(f.store, f.credentials, confirmation, lambda: None),
        lambda: subject.verify_and_promote(f.store, "a" * 32, 0, object(), time.monotonic()),
        lambda: subject.active(f.store, {}),
    )
    for call in calls:
        with pytest.raises(subject.Conflict, match="disabled"):
            call()
    assert _rows(f) == rows and _settings(f) == settings


def test_slice_on_but_reservation_off_admits_nothing(stage, monkeypatch):
    f = stage
    confirmation = subject.preview(f.store, f.credentials)
    monkeypatch.setattr(generations, "CANDIDATE_RESERVATION_ENABLED", False)
    rows, settings = _rows(f), _settings(f)
    # The slice gate itself now requires all three switches, so admission refuses before
    # freezing anything rather than at the registry's own reservation gate.
    with pytest.raises(subject.Conflict, match="disabled"):
        subject.admit(f.store, f.credentials, confirmation, lambda: None)
    assert _rows(f) == rows and _settings(f) == settings
    assert not f.drive.touched and not f.cloud.submissions


# ------------------------------------------------------------------- 2. writer fences


D1 = (
    "D1: runtime_preparation._birth compares GENERATION_RESERVED proof with Python ==, so a "
    "generic writer may re-type it (False->0, 1->1.0) and the decoder's loose comparisons "
    "accept it. Before submission this rebinds the specification: the promotion receipt then "
    "names a specification digest that was never POSTed. After submission the receipt's "
    "epoch can still be re-typed."
)


def test_generic_writer_cannot_retype_the_specification_before_submission(stage):
    f = stage
    value, _ = admit(f)
    root, _run = roots(value, f)
    accepted = []

    def retype(path):
        if path != root:
            return
        current = preparation.get(f.store, value["id"])
        written = current["journal"]["specification"]
        retyped = copy.deepcopy(written)
        retyped["resources"][0]["readOnly"] = 0
        assert retyped == written
        assert runtime_store.canonical(retyped) != runtime_store.canonical(written)
        try:
            preparation.checkpoint(
                f.store, value["id"], current["epoch"], journal={"specification": retyped}
            )
        except preparation.Conflict:
            return
        accepted.append(True)

    f.drive.on_create = retype
    final, _ = execute(f)
    assert final["state"] == "completed", final["message"]
    if not accepted:
        return
    bound = final["journal"]["generation_promotion"]["specification_sha256"]
    posted = runtime_store._digest(f.cloud.submissions[0])
    assert len(f.cloud.submissions) == 1
    raise DefectReproduced(f"promotion binds {bound[:12]}; the POSTed spec is {posted[:12]}")


def test_generic_writer_cannot_retype_the_submission_receipt(stage):
    f = stage
    value, _ = admit(f)
    f.drive.damage = _cache
    failed, _ = execute(f)
    journal = failed["journal"]
    assert failed["state"] == "failed" and "generation_submission" in journal
    retyped = {**journal["generation_submission"]}
    retyped["epoch"] = float(retyped["epoch"])
    assert retyped == journal["generation_submission"]
    before = _rows(f)
    try:
        preparation.checkpoint(
            f.store, value["id"], failed["epoch"], journal={"generation_submission": retyped}
        )
    except preparation.Conflict:
        assert _rows(f) == before
        return
    stored = json.loads(_row(f, value["id"])["journal"])["generation_submission"]
    raise DefectReproduced("generic checkpoint stored " + runtime_store.canonical(stored)[:60])


def test_generic_writer_cannot_forge_an_absence_receipt_for_an_uncertain_post(stage):
    f = stage
    value, _ = admit(f)
    f.cloud.submit_error = True
    final, _ = execute(f)
    journal = final["journal"]
    assert journal["submission_intent"] and "job_id" not in journal
    card = preparation.card(final)
    assert card["ucloud_submission_uncertain"] and not card["can_cleanup"]
    try:
        forged = preparation.checkpoint(
            f.store,
            value["id"],
            final["epoch"],
            journal={"reconciled_not_created": True, "no_post": True},
        )
    except preparation.Conflict:
        return
    card = preparation.card(forged)
    raise DefectReproduced(
        "uncertain="
        + str(card["ucloud_submission_uncertain"])
        + " can_cleanup="
        + str(card["can_cleanup"])
    )


def test_birth_anchor_cannot_be_renamed_in_either_direction(stage):
    f = stage
    value, _ = admit(f)
    legacy = _legacy(f)
    before = _rows(f)
    for row, name in (
        (value, "CBS runtime preparation " + value["id"]),
        (legacy, "CBS runtime generation " + legacy["id"]),
    ):
        with pytest.raises(preparation.Conflict):
            preparation.checkpoint(f.store, row["id"], row["epoch"], journal={"run_name": name})
    assert _rows(f) == before


def test_trusted_channels_cannot_graft_generation_evidence_onto_a_legacy_row(stage):
    f = stage
    legacy = _legacy(f)
    claimed = preparation.claim(f.store)
    assert claimed["id"] == legacy["id"]
    before = _rows(f)
    folders = {
        "environment": {
            "path": "/drive/x",
            "nonce": "a" * 32,
            "epoch": claimed["epoch"],
            "outcome": "acknowledged",
        }
    }
    with pytest.raises(subject.Conflict, match="Only a Runtime generation owner"):
        subject._write(
            f.store, legacy["id"], claimed["epoch"], lambda _: {"generation_folders": folders}
        )
    with pytest.raises(preparation.Conflict, match="verified receipts"):
        preparation.begin_submission(f.store, legacy["id"], claimed["epoch"], require=dict)
    grafted = copy.deepcopy(claimed)
    grafted["frozen"]["generation"] = {"policy": subject.POLICY}
    for value, journal in (
        (claimed, {"generation_promotion": {"format": subject.PROMOTION_FORMAT}}),
        (grafted, {"stop_intent": False}),
    ):
        with f.store.connection() as db, pytest.raises(preparation.Conflict):
            db.execute("BEGIN IMMEDIATE")
            preparation._update(db, copy.deepcopy(value), journal=journal, _generation_writer=True)
    assert _rows(f) == before


@pytest.mark.parametrize(
    "injected",
    [
        {"prepared": True, "doctor_verified": True},
        {"job_id": "remote-preparation"},
        {"generation_promotion": {"format": subject.PROMOTION_FORMAT}},
        {"generation_submission": {"epoch": 1, "specification_sha256": "a", "folders_sha256": "b"}},
        {"owned_folder": "/drive/cbs/cbs-enhancement-" + "a" * 32},
    ],
    ids=["ready", "job-id", "promotion", "receipt-without-folders", "owned-folder"],
)
def test_submission_receipt_hook_cannot_publish_ready_or_unproved_receipts(stage, injected):
    f = stage
    admit(f)
    value = preparation.claim(f.store)
    before, settings = _rows(f), _settings(f)
    with pytest.raises(preparation.Conflict):
        preparation.begin_submission(
            f.store, value["id"], value["epoch"], require=lambda _: copy.deepcopy(injected)
        )
    assert _rows(f) == before and _settings(f) == settings


# --------------------------------------------------- 3. verify_and_promote / registry CAS


def _race_epoch(db, identifier):
    db.execute("UPDATE runtime_preparations SET epoch=epoch+1 WHERE id=?", (identifier,))


def _race_stop(db, identifier):
    db.execute(
        "UPDATE runtime_preparations SET journal=json_set(journal,'$.stop_intent',json('true')) "
        "WHERE id=?",
        (identifier,),
    )


def _race_archived(db, identifier):
    db.execute("UPDATE runtime_preparations SET archived=1 WHERE id=?", (identifier,))


def _race_state(db, identifier):
    db.execute("UPDATE runtime_preparations SET state='failed' WHERE id=?", (identifier,))


def _race_candidate(db, identifier):
    value = json.loads(db.execute("SELECT value FROM settings WHERE id=11").fetchone()[0])
    (entry,) = value["targets"].values()
    entry["candidate"]["operation_sha256"] = "e" * 64
    db.execute("UPDATE settings SET value=? WHERE id=11", (generations._encoded(value),))


def _race_registry(db, identifier):
    db.execute("DELETE FROM settings WHERE id=11")


RACES = {
    "none": (None, None, True),
    "epoch": (_race_epoch, preparation.Interrupted, None),
    "stop": (_race_stop, None, False),
    "archived": (_race_archived, preparation.Conflict, None),
    "not-running": (_race_state, observation.Refused, None),
    "candidate": (_race_candidate, generations.Conflict, None),
    "registry": (_race_registry, generations.Conflict, None),
}


@pytest.mark.parametrize("race", sorted(RACES))
def test_promotion_writer_rereads_everything_after_waiting_for_the_lock(stage, monkeypatch, race):
    f = stage
    claimed = _awaiting(f)
    identifier = claimed["id"]
    change, error, expected = RACES[race]
    blocker = sqlite3.connect(
        f.store.db_path, timeout=15, isolation_level=None, check_same_thread=False
    )
    locked = threading.Event()
    real = preparation._model_evidence

    def evidence_then_lock(*args, **kwargs):
        result = real(*args, **kwargs)  # Last remote read before the writer.
        blocker.execute("BEGIN IMMEDIATE")
        locked.set()
        return result

    monkeypatch.setattr(preparation, "_model_evidence", evidence_then_lock)
    outcome = {}

    def promote():
        try:
            outcome["value"] = subject.verify_and_promote(
                f.store, identifier, claimed["epoch"], f.cloud, time.monotonic() + 120
            )
        except BaseException as exc:  # noqa: B036 - reported to the main thread
            outcome["error"] = exc

    worker = threading.Thread(target=promote)
    worker.start()
    try:
        assert locked.wait(60)
        time.sleep(0.5)  # The promotion is now waiting in BEGIN IMMEDIATE.
        if change:
            change(blocker, identifier)
        blocker.execute("COMMIT")
        worker.join(60)
        assert not worker.is_alive()
    finally:
        if blocker.in_transaction:
            blocker.execute("ROLLBACK")
        blocker.close()
    if error is None:
        assert "error" not in outcome, repr(outcome.get("error"))
        assert outcome["value"] is expected
    else:
        assert isinstance(outcome.get("error"), error), repr(outcome)
    raw = _row(f, identifier)
    journal = json.loads(raw["journal"])
    if race == "none":
        assert raw["state"] == "completed" and "generation_promotion" in journal
        assert registry(f, claimed)["active"]["owner_id"] == identifier
        return
    assert raw["state"] != "completed"
    assert not {"generation_promotion", "prepared", "model_plan_receipt"} & set(journal)
    if race == "registry":
        assert not f.store.all("SELECT id FROM settings WHERE id=11")
        return
    entry = registry(f, claimed)
    assert entry["active"] is None and entry["revision"] == 0
    assert entry["candidate"]["owner_id"] == identifier


def test_owner_completion_failure_rolls_back_the_already_published_pointer(stage, monkeypatch):
    f = stage
    claimed = _awaiting(f)
    real = preparation._update
    published = []
    publish = generations._publish_active

    def record(*args, **kwargs):
        published.append(publish(*args, **kwargs))
        return published[-1]

    def refuse_completion(db, value, **kwargs):
        if kwargs.get("state") == "completed":
            raise RuntimeError("synthetic failure after the registry CAS")
        return real(db, value, **kwargs)

    monkeypatch.setattr(generations, "_publish_active", record)
    monkeypatch.setattr(preparation, "_update", refuse_completion)
    settings = _settings(f)
    with pytest.raises(RuntimeError, match="after the registry CAS"):
        subject.verify_and_promote(
            f.store, claimed["id"], claimed["epoch"], f.cloud, time.monotonic() + 60
        )
    assert published and published[0]["active"]["owner_id"] == claimed["id"]
    assert _settings(f) == settings
    assert registry(f, claimed)["active"] is None
    assert _row(f, claimed["id"])["state"] == "running"


def test_expired_wall_deadline_records_stop_instead_of_promoting(stage):
    f = stage
    claimed = _awaiting(f)
    preparation.checkpoint(
        f.store, claimed["id"], claimed["epoch"], journal={"deadline_at": time.time() - 1}
    )
    settings = _settings(f)
    assert (
        subject.verify_and_promote(
            f.store, claimed["id"], claimed["epoch"], f.cloud, time.monotonic() + 60
        )
        is False
    )
    value = preparation.get(f.store, claimed["id"])
    assert value["journal"]["stop_intent"] is True and value["state"] != "completed"
    assert _settings(f) == settings


def test_a_to_b_keeps_a_and_the_legacy_lock_untouched_and_old_acks_never_revert(stage):
    f = stage
    legacy = f.legacy
    f.drive.put(legacy + "/stamp.json", api.runtime_stamp_bytes())
    f.drive.put(legacy + "/.in-use", b"")
    f.drive.mkdirs(legacy + "/.prepare-lock")
    f.drive.put(legacy + "/hf/hub/models--x/blob", b"legacy weights")
    legacy_items = {k: v for k, v in f.drive.items.items() if _under(k, legacy)}
    first, _ = admit(f)
    a, _ = execute(f)
    assert a["state"] == "completed", a["message"]
    root_a, run_a = roots(a, f)
    a_items = {k: v for k, v in f.drive.items.items() if _under(k, root_a)}
    a_row = _row(f, a["id"])
    f.drive.touched.clear()
    f.drive.creates.clear()
    confirmation = subject.preview(f.store, f.credentials)
    assert confirmation["expected_revision"] == 1
    second = subject.admit(f.store, f.credentials, confirmation, lambda: None)
    b, _ = execute(f)
    assert b["id"] == second["operation"]["runtime_preparation_id"] != a["id"]
    assert b["state"] == "completed", b["message"]
    root_b, run_b = roots(b, f)
    assert f.drive.creates == [root_b, run_b]
    assert not [p for p in f.drive.touched if _under(p, root_a) or _under(p, run_a)]
    assert {k: v for k, v in f.drive.items.items() if _under(k, root_a)} == a_items
    assert {k: v for k, v in f.drive.items.items() if _under(k, legacy)} == legacy_items
    assert_legacy_untouched(f)
    assert _row(f, a["id"]) == a_row
    target = b["frozen"]["generation"]["target"]
    assert target == a["frozen"]["generation"]["target"]
    entry = registry(f, b)
    assert entry["revision"] == 2 and entry["candidate"] is None
    assert entry["active"]["owner_id"] == b["id"]
    for owner, revision in ((a, 1), (b, 2)):
        journal = preparation.get(f.store, owner["id"])["journal"]
        promotion = journal["generation_promotion"]
        assert promotion["registry_revision"] == revision
        assert promotion["job_id"] == journal["job_id"]
        assert promotion["folders_sha256"] == runtime_store._digest(journal["generation_folders"])
        assert promotion["specification_sha256"] == runtime_store._digest(journal["specification"])
    assert subject.active(f.store, target)["root"] == root_b
    settings = _settings(f)
    assert subject.verify_and_promote(f.store, a["id"], a["epoch"], f.cloud, time.monotonic() + 30)
    assert _settings(f) == settings and _row(f, a["id"]) == a_row
    assert first["id"] == a["id"]


@pytest.mark.parametrize("damage", ["owner", "revision", "token", "operation", "archived"])
def test_active_reader_refuses_a_pointer_its_promotion_receipt_does_not_match(stage, damage):
    f = stage
    admit(f)
    value, _ = execute(f)
    assert value["state"] == "completed", value["message"]
    target = value["frozen"]["generation"]["target"]
    assert subject.active(f.store, target)["owner_id"] == value["id"]
    if damage == "archived":
        f.store.execute("UPDATE runtime_preparations SET archived=1 WHERE id=?", (value["id"],))
    else:

        def change(entry):
            active = entry["active"]
            if damage == "owner":
                active["owner_id"] = "e" * 32
            elif damage == "revision":
                entry["revision"] = active["promoted_revision"] = 2
            elif damage == "token":
                active["token"] = "f" * 32
            else:
                active["operation_sha256"] = "e" * 64

        _rewrite_registry(f, change)
    with pytest.raises(subject.Conflict, match="no matching promoted owner"):
        subject.active(f.store, target)


@pytest.mark.parametrize("race", ["candidate", "revision"])
def test_admission_rechecks_the_registry_inside_its_writer(stage, race):
    f = stage
    confirmation = subject.preview(f.store, f.credentials)
    target = confirmation["target"]
    calls = []

    def guard():
        calls.append(1)
        if len(calls) != 2:
            return
        # After the read-only refreeze and before BEGIN IMMEDIATE: another writer wins.
        with f.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if race == "candidate":
                generations.reserve_candidate(
                    db,
                    target,
                    expected_revision=0,
                    owner_id="e" * 32,
                    token="f" * 32,
                    operation_sha256="d" * 64,
                    compute=confirmation["operation"]["compute"],
                )
            else:
                active = {
                    "token": "f" * 32,
                    "owner_id": "e" * 32,
                    "operation_sha256": "d" * 64,
                    "compute": confirmation["operation"]["compute"],
                    "promoted_revision": 1,
                }
                value = {
                    "policy": generations.REGISTRY_POLICY,
                    "targets": {
                        target["target_sha256"]: {
                            "target": target,
                            "revision": 1,
                            "active": active,
                            "candidate": None,
                        }
                    },
                }
                db.execute(
                    "INSERT INTO settings(id,value) VALUES (11,?)", (generations._encoded(value),)
                )

    with pytest.raises(subject.Conflict):
        subject.admit(f.store, f.credentials, confirmation, guard)
    assert len(calls) == 3 and _rows(f) == []
    with f.store.connection() as db:
        entry = generations.snapshot(db, target)
    assert (entry["candidate"] or entry["active"])["owner_id"] == "e" * 32


def test_concurrent_identical_confirmations_create_exactly_one_owner(stage):
    f = stage
    confirmation = subject.preview(f.store, f.credentials)
    barrier, results, errors = threading.Barrier(2), [], []

    def run():
        try:
            barrier.wait(30)
            results.append(subject.admit(f.store, f.credentials, confirmation, lambda: None))
        except BaseException as exc:  # noqa: B036 - reported to the main thread
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
    assert not errors, errors
    assert len({item["operation"]["runtime_preparation_id"] for item in results}) == 1
    assert sorted(item["deduplicated"] for item in results) == [False, True]
    assert len(_rows(f)) == 1


# ------------------------------------------------------------------- 4. folders / POST


def test_intent_precedes_each_create_and_the_post_needs_both_acks_from_one_epoch(stage):
    f = stage
    value, _ = admit(f)
    root, run = roots(value, f)
    seen, posts = [], []

    def on_create(path):
        current = preparation.get(f.store, value["id"])
        kind = "environment" if path == root else "run"
        entry = current["journal"]["generation_folders"][kind]
        seen.append(
            (kind, entry["outcome"], entry["path"] == path, entry["epoch"] == current["epoch"])
        )

    f.drive.on_create = on_create
    submit = f.cloud.submit_job

    def checked(specification):
        current = preparation.get(f.store, value["id"])
        journal = current["journal"]
        folders, receipt = journal["generation_folders"], journal["generation_submission"]
        posts.append(
            {
                "acks": [folders[kind]["outcome"] for kind in subject.FOLDERS],
                "epochs": {folders[kind]["epoch"] for kind in subject.FOLDERS}
                | {receipt["epoch"], current["epoch"]},
                "specification": runtime_store._digest(specification)
                == receipt["specification_sha256"],
                "folders": runtime_store._digest(folders) == receipt["folders_sha256"],
                "intent": journal["submission_intent"] is True and "job_id" not in journal,
            }
        )
        return submit(specification)

    f.cloud.submit_job = checked
    final, _ = execute(f)
    assert final["state"] == "completed", final["message"]
    assert seen == [("environment", "pending", True, True), ("run", "pending", True, True)]
    assert posts == [
        {
            "acks": ["acknowledged", "acknowledged"],
            "epochs": {final["epoch"]},
            "specification": True,
            "folders": True,
            "intent": True,
        }
    ]
    assert f.drive.creates == [root, run]


def test_a_reply_drained_after_an_epoch_change_stays_pending_and_never_authorizes_work(stage):
    f = stage
    value, _ = admit(f)
    root, _run = roots(value, f)

    def bump(path):
        if path == root:  # A new observation epoch begins while the create is in flight.
            f.store.execute(
                "UPDATE runtime_preparations SET epoch=epoch+1 WHERE id=?", (value["id"],)
            )

    f.drive.on_create = bump
    execute(f)
    current = preparation.get(f.store, value["id"])
    folders = current["journal"]["generation_folders"]
    assert set(folders) == {"environment"} and folders["environment"]["outcome"] == "pending"
    assert root in f.drive.items  # The folder exists; its outcome is still unproved.
    preparation.recover(f.store)
    current = preparation.get(f.store, value["id"])
    assert current["state"] == "interrupted" and preparation.unresolved(current)
    card = preparation.card(current)
    assert not card["can_retry"] and not card["can_reconcile"] and not card["can_remove"]
    for action in (preparation.retry, preparation.reconcile):
        with pytest.raises(preparation.Conflict):
            action(f.store, f.credentials, value["id"])
    # Even a worker handed the row again (e.g. a future recovery) creates and posts nothing.
    f.store.execute("UPDATE runtime_preparations SET state='queued' WHERE id=?", (value["id"],))
    again, _ = execute(f)
    assert again["state"] == "failed"
    assert again["journal"]["generation_folders"] == folders
    assert f.drive.creates == [root] and not f.cloud.submissions
    assert registry(f, again)["candidate"]["owner_id"] == value["id"]


def test_stop_after_both_acks_prevents_every_upload_and_post_and_cleanup_spares_the_root(
    stage, monkeypatch
):
    f = stage
    value, _ = admit(f)
    root, run = roots(value, f)
    drive_upload, stopped = api.native.upload, []

    def upload(client, server, path, target, guard, update, **kwargs):
        if not stopped:
            stopped.append(target)
            preparation.cancel(f.store, f.credentials, value["id"])
        return drive_upload(client, server, path, target, guard, update, **kwargs)

    monkeypatch.setattr(api.native, "upload", upload)
    final, _ = execute(f)
    journal = final["journal"]
    assert final["state"] == "cancelled" and journal["stop_intent"] is True
    assert {entry["outcome"] for entry in journal["generation_folders"].values()} == {
        "acknowledged"
    }
    assert stopped and not f.cloud.submissions
    assert not {"submission_intent", "generation_submission", "job_id"} & set(journal)
    assert not [path for path in f.drive.touched if path.endswith((".sh", ".py", ".txt"))]
    assert preparation.card(final)["can_cleanup"]
    preparation.cleanup(f.store, f.credentials, value["id"])
    assert f.cloud.trashed == [run] and root in f.drive.items


def test_create_reply_classification_rejects_shapes_and_foreign_paths():
    path = "/drive/cbs/cbs-runtime/whisperx-x-cu128-000000000000-g-" + "a" * 32
    accepted = [{}, {"responses": [None]}, {"responses": [{"id": path}]}]
    refused = [
        None,
        [],
        "created",
        {"responses": []},
        {"responses": [None, None]},
        {"responses": {"id": path}},
        {"responses": [path]},
        {"responses": [{"id": path + "/"}]},
        {"responses": [{"id": path + "(1)"}]},
        {"responses": [{"id": None}]},
        {"responses": [{"id": path}], "next": None},
    ]
    assert [subject._acknowledged(reply, path) for reply in accepted] == [True] * len(accepted)
    assert [subject._acknowledged(reply, path) for reply in refused] == [False] * len(refused)


# --------------------------------------------------------------------------- 5. decoder


@pytest.mark.parametrize(
    ("module", "name", "value"),
    [
        pytest.param(runtime_compute, "POLICY", "mig-v2", id="compute-policy"),
        pytest.param(
            api,
            "SCRIPT_FLAVOURS",
            ("terminal-debian", "terminal-almalinux"),
            id="terminal-flavours",
        ),
        pytest.param(api, "SCRIPT_PARAMETER", "batchScriptV2", id="script-parameter"),
        pytest.param(runtime_compute, "MIG_PRODUCT", dict(CPU), id="mig-product"),
        pytest.param(api, "WHISPERX_VERSION", "9.9.9", id="whisperx-version"),
        pytest.param(
            api, "runtime_id", lambda: "whisperx-9.9.9-cu128-" + "0" * 12, id="runtime-id"
        ),
        pytest.param(preparation, "recipe_hash", lambda: "0" * 64, id="recipe"),
        pytest.param(api, "PREPARE_SECONDS", 9 * 3600, id="prepare-duration"),
    ],
)
def test_a_promoted_generation_stays_decodable_after_later_version_changes(
    stage, monkeypatch, module, name, value
):
    f = stage
    admit(f)
    promoted, _ = execute(f)
    assert promoted["state"] == "completed", promoted["message"]
    monkeypatch.setattr(module, name, value)
    try:
        current = preparation.get(f.store, promoted["id"])
    except preparation.Conflict as exc:
        raise DefectReproduced(f"{name} change orphaned the promoted owner: {exc}") from None
    assert current["state"] == "completed"
    assert preparation.card(current)["state"] == "completed"


def _cpu(f, frozen, generation):
    target = generation["target"]
    runtime_id = target["runtime_id"].replace("-cu128-", "-cpu-")
    compute = {"product": dict(CPU), "device": "cpu", "flavor": "cpu"}
    base = generations.base_target(
        f.settings,
        runtime_id=runtime_id,
        requirements_sha256=target["requirements_sha256"],
        compute=compute,
    )
    frozen["runtime_id"] = runtime_id
    generation["target"] = base
    generation["operation"].update(
        target_sha256=base["target_sha256"], runtime_id=runtime_id, compute=compute
    )


def _cpu_compute_only(f, frozen, generation):
    generation["operation"]["compute"] = {"product": dict(CPU), "device": "cpu", "flavor": "cpu"}


def _other_cuda_runtime(f, frozen, generation):
    digest = "f" * 64
    runtime_id = "whisperx-9.9.9-cu128-" + digest[:12]
    compute = generation["operation"]["compute"]
    base = generations.base_target(
        f.settings, runtime_id=runtime_id, requirements_sha256=digest, compute=compute
    )
    frozen["runtime_id"] = runtime_id
    generation["target"] = base
    generation["operation"].update(target_sha256=base["target_sha256"], runtime_id=runtime_id)


@pytest.mark.parametrize(
    ("mutate", "decodes"),
    [
        (lambda *_: None, True),
        (_other_cuda_runtime, True),
        (_cpu, False),
        (_cpu_compute_only, False),
    ],
    ids=["resealed-control", "other-cuda-runtime", "cpu-target", "cpu-compute"],
)
def test_resealed_rows_decode_data_only_but_cpu_births_are_refused(stage, mutate, decodes):
    f = stage
    value, _ = admit(f)
    _reseal(f, value, mutate)
    if decodes:
        assert preparation.get(f.store, value["id"])["frozen"]["generation"]
    else:
        with pytest.raises(preparation.Conflict):
            preparation.get(f.store, value["id"])


@pytest.mark.parametrize(
    "case", ["current-cpu", "current-full-gpu", "current-policy", "frozen-full-gpu"]
)
def test_worker_refuses_non_mig_compute_before_any_folder_or_post(stage, monkeypatch, case):
    f = stage
    value, _ = admit(f)
    current = {
        "runtime_product": dict(runtime_compute.MIG_PRODUCT),
        "runtime_compute_policy": runtime_compute.POLICY,
    }
    if case == "current-cpu":
        current["runtime_product"] = dict(CPU)
    elif case == "current-full-gpu":
        current["runtime_product"] = dict(FULL_GPU)
    elif case == "current-policy":
        current["runtime_compute_policy"] = "mig-v0"
    else:

        def full(f, frozen, generation):
            generation["operation"]["compute"]["product"] = dict(FULL_GPU)

        _reseal(f, value, full)
        assert preparation.get(f.store, value["id"])  # Data-only: history stays readable.
    monkeypatch.setattr(runtime_compute, "freeze", lambda *_: copy.deepcopy(current))
    settings = _settings(f)
    final, _ = execute(f)
    assert final["state"] == "failed"
    assert not {"specification", "generation_folders", "submission_intent"} & set(final["journal"])
    assert not f.drive.creates and not f.drive.touched and not f.cloud.submissions
    assert _settings(f) == settings


@pytest.mark.parametrize("key", sorted(preparation.GENERATION_ONLY) + ["frozen-generation"])
def test_legacy_rows_carrying_any_generation_key_stay_refused(plain, key):
    f = plain
    value = _legacy(f)
    frozen, journal = dict(value["frozen"]), dict(value["journal"])
    if key == "frozen-generation":
        frozen["generation"] = {"policy": subject.POLICY}
    else:
        journal[key] = {}
    f.store.execute(
        "UPDATE runtime_preparations SET frozen=?,journal=? WHERE id=?",
        (runtime_store.canonical(frozen), runtime_store.canonical(journal), value["id"]),
    )
    with pytest.raises(preparation.Conflict):
        preparation.get(f.store, value["id"])


def _baseline_preparation(tmp_path):
    try:
        source = subprocess.run(
            [
                "git",
                "-C",
                str(REPOSITORY),
                "show",
                BASELINE + ":src/cbs_transcriber/runtime_preparation.py",
            ],
            capture_output=True,
            timeout=60,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("The reviewed baseline commit is unavailable in this checkout.")
    path = tmp_path / "runtime_preparation_baseline.py"
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location(
        "cbs_transcriber._reviewer_baseline_runtime_preparation", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_variants(f, value):
    identifier, frozen = value["id"], value["frozen"]
    run = value["journal"]["run_name"]
    folder = "/drive/cbs/cbs-enhancement-" + identifier
    compute = {
        "runtime_product": dict(runtime_compute.MIG_PRODUCT),
        "runtime_compute_policy": runtime_compute.POLICY,
    }
    variants = [
        ("queued", {"run_name": run}),
        (
            "running",
            {
                "run_name": run,
                "capacity_reserved": True,
                "submission_intent": True,
                "submission_attempted_at": "2026-11-17T00:00:00Z",
                "reconciled_not_created": False,
                "no_post": False,
                "specification": {"name": run},
                **compute,
            },
        ),
        (
            "failed",
            {
                "run_name": run,
                "job_id": "remote-1",
                "remote_state": "RUNNING",
                "stop_intent": True,
                "owned_folder": folder,
                "cleanup_state": "pending",
            },
        ),
        (
            "cancelled",
            {
                "run_name": run,
                "submission_intent": True,
                "reconciled_not_created": True,
                "no_post": True,
            },
        ),
        (
            "interrupted",
            {"run_name": run, "job_id": "remote-2", "remote_state": "SUCCESS"},
        ),
    ]
    if "model_plan" in frozen:
        plan = frozen["model_plan"]
        receipt = {
            "format": preparation.MODEL_RECEIPT,
            "model_plan_sha256": runtime_store._digest(plan),
            "runtime_id": frozen["runtime_id"],
            "recipe_sha256": frozen["recipe_sha256"],
            "evidence": evidence(plan),
        }
        variants.append(
            (
                "completed",
                {
                    "run_name": run,
                    "job_id": "remote-3",
                    "remote_state": "SUCCESS",
                    "prepared": True,
                    "doctor_verified": True,
                    "model_plan_receipt": receipt,
                    "owned_folder": folder,
                    "cleanup_state": "pending",
                },
            )
        )
    return variants


@pytest.mark.parametrize("plan", [True, False], ids=["model-plan", "base-only"])
def test_existing_legacy_owners_decode_card_and_write_exactly_as_the_baseline(
    plain, tmp_path, plan
):
    f = plain
    old = _baseline_preparation(tmp_path)
    value = _legacy(f, plan=plan)
    chosen = f.plan["chosen"]
    # Each module has its own absent sentinel, so base-only omits the keyword entirely.
    kwargs = {"model_plan": f.plan["model_plan"]} if plan else {}
    assert old._identity(chosen, **kwargs) == preparation._identity(chosen, **kwargs)
    assert old.fingerprint(chosen, **kwargs) == preparation.fingerprint(chosen, **kwargs)
    variants = _legacy_variants(f, value)
    assert len(variants) == (6 if plan else 5)
    for state, journal in variants:
        f.store.execute(
            "UPDATE runtime_preparations SET state=?,journal=? WHERE id=?",
            (state, runtime_store.canonical(journal), value["id"]),
        )
        raw = _row(f, value["id"])
        new_value, old_value = preparation._decode(dict(raw)), old._decode(dict(raw))
        assert new_value == old_value, state
        assert preparation.card(new_value) == old.card(old_value), state
        assert preparation.unresolved(new_value) == old.unresolved(old_value), state
        assert preparation._target(new_value["frozen"]) == old._target(old_value["frozen"])
        if state in {"completed", "cancelled"}:
            continue
        written = []
        for module in (preparation, old):
            with f.store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                fresh = module._get(db, value["id"])
                result = module._update(
                    db,
                    fresh,
                    journal={"remote_state": "RUNNING", "termination_attempts": 1},
                    message="synthetic legacy write",
                    progress=0.5,
                )
                written.append({k: v for k, v in result.items() if k != "updated"})
                db.execute("ROLLBACK")
        assert written[0] == written[1], state
