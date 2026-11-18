"""ADR 0176 Stage 2 seams: registry promotion CAS, frozen roots and disabled reachability.

Pure in-memory SQLite, synthetic paths and Bash on temporary folders only.
"""

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_runtime_generations_stage1 import compute, reserve, selection, target
from test_runtime_generations_stage1 import db as db  # noqa: F401 - fixture
from test_runtime_generations_stage1 import generation as generation  # noqa: F401 - fixture
from test_runtime_generations_stage1 import settings as settings  # noqa: F401 - fixture
from test_runtime_preparation_locking import bash as bash  # noqa: F401 - fixture
from test_runtime_preparation_locking import execute as run_bash
from test_runtime_preparation_locking import functions, quoted
from ucloud_workflow import UCloudAPIError

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import runtime_compute
from cbs_transcriber import runtime_preparation as preparation
from cbs_transcriber import runtime_preparation_observation as observation

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "cbs_transcriber"
TOKEN = "c" * 32


def publish(generation, db, base, **changes):  # noqa: F811 - fixture name reuse
    values = {
        "owner_id": "b" * 32,
        "token": TOKEN,
        "operation_sha256": "d" * 64,
        "compute": compute(base["device"]),
        **changes,
    }
    return generation._publish_active(db, base, **values)


def enable(generation, monkeypatch):  # noqa: F811 - fixture name reuse
    monkeypatch.setattr(generation, "CANDIDATE_RESERVATION_ENABLED", True)
    monkeypatch.setattr(generation, "ACTIVE_POINTERS_ENABLED", True)


def test_active_pointers_stay_refused_while_promotion_is_disabled(
    generation,  # noqa: F811
    db,  # noqa: F811
    settings,  # noqa: F811
    monkeypatch,
):
    monkeypatch.setattr(generation, "CANDIDATE_RESERVATION_ENABLED", True)
    base = target(generation, settings)
    db.execute("BEGIN IMMEDIATE")
    reserve(generation, db, base)
    raw = db.execute("SELECT value FROM settings WHERE id=11").fetchone()[0]
    with pytest.raises(generation.Conflict, match="disabled"):
        publish(generation, db, base)
    db.commit()
    assert db.execute("SELECT value FROM settings WHERE id=11").fetchone()[0] == raw
    monkeypatch.setattr(generation, "ACTIVE_POINTERS_ENABLED", True)
    db.execute("BEGIN IMMEDIATE")
    published = publish(generation, db, base)
    db.commit()
    monkeypatch.setattr(generation, "ACTIVE_POINTERS_ENABLED", False)
    with pytest.raises(generation.Conflict):
        generation.snapshot(db, base)  # A well-formed pointer is still refused while off.
    assert published["active"]["promoted_revision"] == 1


@pytest.mark.parametrize("field", ["token", "owner_id", "operation_sha256", "compute"])
def test_promotion_requires_the_exact_reserved_candidate(
    generation,  # noqa: F811
    db,  # noqa: F811
    settings,  # noqa: F811
    monkeypatch,
    field,
):
    enable(generation, monkeypatch)
    base = target(generation, settings)
    db.execute("BEGIN IMMEDIATE")
    reserve(generation, db, base)
    before = db.execute("SELECT * FROM settings ORDER BY id").fetchall()
    changed = compute()
    changed["product"]["id"] = "another-structural-reference"
    wrong = {
        "token": "e" * 32,
        "owner_id": "e" * 32,
        "operation_sha256": "e" * 64,
        "compute": changed,
    }[field]
    with pytest.raises(generation.Conflict):
        publish(generation, db, base, **{field: wrong})
    assert db.execute("SELECT * FROM settings ORDER BY id").fetchall() == before
    db.rollback()


def test_promotion_is_atomic_idempotent_and_a_to_b_keeps_history(
    generation,  # noqa: F811
    db,  # noqa: F811
    settings,  # noqa: F811
    monkeypatch,
):
    enable(generation, monkeypatch)
    base = target(generation, settings)
    with pytest.raises(generation.Conflict, match="transaction"):
        publish(generation, db, base)
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(generation.Conflict):
        publish(generation, db, base)  # No candidate, no pointer.
    reserve(generation, db, base)
    first = publish(generation, db, base)
    raw = db.execute("SELECT value FROM settings WHERE id=11").fetchone()[0]
    assert publish(generation, db, base) == first
    assert db.execute("SELECT value FROM settings WHERE id=11").fetchone()[0] == raw
    assert first["revision"] == 1 and first["candidate"] is None
    with pytest.raises(generation.Conflict):
        reserve(generation, db, base, owner="e" * 32, expected=1)  # Active token reuse.
    reserve(generation, db, base, owner="e" * 32, token="f" * 32, expected=1)
    second = publish(generation, db, base, owner_id="e" * 32, token="f" * 32)
    db.commit()
    assert second["revision"] == 2 and second["active"]["owner_id"] == "e" * 32
    assert second["active"]["promoted_revision"] == 2
    assert db.execute("SELECT * FROM settings WHERE id IN (9,10) ORDER BY id").fetchall() == [
        (9, "keep-nine"),
        (10, "keep-ten"),
    ]


@pytest.mark.parametrize("damage", ["revision", "zero", "keys", "duplicate-token"])
def test_enabled_codec_still_rejects_malformed_or_ambiguous_pointers(
    generation,  # noqa: F811
    db,  # noqa: F811
    settings,  # noqa: F811
    monkeypatch,
    damage,
):
    enable(generation, monkeypatch)
    base = target(generation, settings)
    active = {
        "token": TOKEN,
        "owner_id": "b" * 32,
        "operation_sha256": "d" * 64,
        "compute": compute(),
        "promoted_revision": 1,
    }
    entry = {"target": base, "revision": 1, "active": active, "candidate": None}
    if damage == "revision":
        active["promoted_revision"] = 2
    elif damage == "zero":
        entry["revision"] = active["promoted_revision"] = 0
    elif damage == "keys":
        active["asserted_ready"] = True
    else:
        entry["candidate"] = {
            "token": TOKEN,
            "owner_id": "e" * 32,
            "expected_revision": 1,
            "operation_sha256": "d" * 64,
            "compute": compute(),
        }
    raw = json.dumps(
        {"policy": generation.REGISTRY_POLICY, "targets": {base["target_sha256"]: entry}}
    )
    db.execute("INSERT INTO settings VALUES (11,?)", (raw,))
    with pytest.raises(generation.Conflict):
        generation.snapshot(db, base)


def test_generation_root_matches_selection_paths_and_refuses_other_targets(
    generation,  # noqa: F811
    settings,  # noqa: F811
):
    base = target(generation, settings)
    root = generation.generation_root(settings, base, TOKEN)
    chosen = selection(generation, base, token=TOKEN)
    assert root == generation.runtime_path(
        settings, runtime_id=base["runtime_id"], selection=chosen
    )
    other = settings.model_copy(deep=True)
    other.ucloud_transcriber.storage_path = "/drive/other"
    for case in ((other, base, TOKEN), (settings, base, "C" * 32), (settings, base, "../x")):
        with pytest.raises(generation.Conflict):
            generation.generation_root(*case)


def test_absent_root_keeps_exact_legacy_bytes_and_present_root_must_be_a_generation(
    settings,  # noqa: F811
):
    legacy = api.runtime_folder(settings)
    root = legacy + "-g-" + TOKEN
    assert api.frozen_runtime_root(settings) == legacy
    assert api.model_folder(settings, "da") == api.model_folder(settings, "da", root=None)
    assert api.model_folder(settings, "da").startswith(legacy + "/hf/hub/")
    assert api.model_folder(settings, "da", root=root).startswith(root + "/hf/hub/")
    old = api.supervisor_script(settings, "synthetic", True, ["da"], prepare_only=True)
    new = api.supervisor_script(
        settings, "synthetic", True, ["da"], prepare_only=True, runtime=root
    )
    changed = [
        pair for pair in zip(old.splitlines(), new.splitlines(), strict=True) if pair[0] != pair[1]
    ]
    assert changed == [
        ("RT=/work/" + legacy.rsplit("/", 1)[1], "RT=/work/" + root.rsplit("/", 1)[1])
    ]
    options = type("Options", (), {"timeout_minutes": 1})()
    application = {"name": "terminal-ubuntu", "version": "synthetic"}
    mig = {"runtime_product": runtime_compute.MIG_PRODUCT}
    spec = api.build_script_specification(settings, application, "synthetic", options, True, **mig)
    moved = api.build_script_specification(
        settings, application, "synthetic", options, True, runtime=root, **mig
    )
    assert spec["resources"][1]["path"] == legacy
    assert moved == {
        **spec,
        "resources": [spec["resources"][0], {**spec["resources"][1], "path": root}],
    }
    for bad in (
        legacy,
        legacy + "-g-" + "C" * 32,
        legacy + "-g-" + "c" * 31,
        legacy + "-g-" + TOKEN + "/..",
        legacy.replace("cu128", "cpu") + "-g-" + TOKEN,
        "/drive/elsewhere-g-" + TOKEN,
        123,
    ):
        with pytest.raises(ValueError):
            api.frozen_runtime_root(settings, bad)


def test_seeding_and_stamp_reads_use_only_the_frozen_root(
    settings,  # noqa: F811
    monkeypatch,
    tmp_path,
):
    legacy = api.runtime_folder(settings)
    root = legacy + "-g-" + TOKEN
    body = b"synthetic speaker"
    monkeypatch.setattr(
        api, "SPEAKER_FILES", {"config.yaml": (len(body), hashlib.sha256(body).hexdigest())}
    )
    local = tmp_path / "config.yaml"
    local.write_bytes(body)
    touched, stored = [], {}

    class Client:
        def retrieve_file(self, path):
            touched.append(path)
            if path not in stored:
                raise UCloudAPIError("missing", status_code=404)
            return {"status": {"sizeInBytes": len(stored[path])}}

    def upload(client, server, path, target, guard, update, **kwargs):
        touched.append(target)
        stored[target] = path.read_bytes()

    monkeypatch.setattr(api, "ensure_folder", lambda client, path: touched.append(path))
    monkeypatch.setattr(api.native, "upload", upload)
    api.seed_speakers(
        Client(),
        "https://cloud.invalid",
        settings,
        [(local, "config.yaml")],
        lambda: None,
        tmp_path / "marker",
        root=root,
    )
    assert touched and all(path.startswith(root + "/") for path in touched)
    reads = []

    def control(client, method, path, *, deadline=None, missing=False, **kwargs):
        reads.append(kwargs["params"]["id"])
        kind = "FILE" if kwargs["params"]["id"].endswith("stamp.json") else "DIRECTORY"
        size = len(api.runtime_stamp_bytes())
        return {"id": kwargs["params"]["id"], "status": {"type": kind, "sizeInBytes": size}}

    def read_bytes(client, settings, path, *, deadline=None):
        reads.append(path)
        return api.runtime_stamp_bytes()

    monkeypatch.setattr(observation, "control", control)
    monkeypatch.setattr(observation, "read_bytes", read_bytes)
    observation.stamp(None, settings, root=root)
    assert reads == [root, root + "/stamp.json", root + "/stamp.json"]
    reads.clear()
    observation.stamp(None, settings)
    assert reads == [legacy, legacy + "/stamp.json", legacy + "/stamp.json"]


def test_fresh_generation_shell_leaves_only_the_promotable_root_inventory(
    bash,  # noqa: F811
    settings,  # noqa: F811
    tmp_path,
):
    from cbs_transcriber import runtime_generation_preparation as subject

    root = api.runtime_folder(settings) + "-g-" + TOKEN
    script = api.supervisor_script(
        settings, "synthetic", True, ["da"], prepare_only=True, runtime=root
    )
    assert "RT=/work/" + root.rsplit("/", 1)[1] + "\n" in script
    runtime, run = tmp_path / "generation", tmp_path / "run"
    run.mkdir()
    runtime.mkdir()  # The exclusive create made the empty root; mkdir -p must tolerate it.
    (run / "requirements.txt").write_bytes(api.requirements_bytes())
    script = re.sub(r"^RUN=.*$", "RUN=" + quoted(run), script, count=1, flags=re.M)
    script = re.sub(r"^RT=.*$", "RT=" + quoted(runtime), script, count=1, flags=re.M)
    # Only flock and the pinned install are stubbed; lock, .in-use and stamp logic are real.
    stubs = """
flock() { return 0; }
install_runtime() {
  mkdir -p "$RT/bin" "$RT/hf" "$RT/nltk" "$RT/python" "$RT/venv"
  cp "$RUN/requirements.txt" "$RT/requirements.txt"
  printf '{"requirements_sha256":"%s"}' 'DIGEST' > "$RT/stamp.pending"
  mv -f "$RT/stamp.pending" "$RT/stamp.json"
}
prepare || exit 1
"""
    run_bash(bash, tmp_path, functions(script) + stubs.replace("DIGEST", api.requirements_hash()))
    names = {path.name for path in runtime.iterdir()}
    allowed = set(subject.ROOT_REQUIRED) | set(subject.ROOT_OPTIONAL)
    assert set(subject.ROOT_REQUIRED) <= names <= allowed
    assert (runtime / ".in-use").is_file() and not (runtime / ".prepare-lock").exists()
    assert (runtime / "stamp.json").read_bytes() == api.runtime_stamp_bytes()


def _guarded_imports(path, module):
    """Every import of ``module`` must sit under an ``"generation" in ...`` branch."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(item.name == module for item in node.names):
            guard, current = False, node
            while current in parents:
                current = parents[current]
                if (
                    isinstance(current, ast.If)
                    and isinstance(current.test, ast.Compare)
                    and isinstance(current.test.left, ast.Constant)
                    and current.test.left.value == "generation"
                    and isinstance(current.test.ops[0], ast.In)
                ):
                    guard = True
                if isinstance(current, ast.Module) and node in current.body:
                    guard = False  # Never a module-level import.
            found.append(guard)
    return found


def _imported(path):
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            names.add((node.module or "").rsplit(".", 1)[-1])
            names.update(item.name for item in node.names)
        elif isinstance(node, ast.Import):
            names.update(item.name.rsplit(".", 1)[-1] for item in node.names)
    return names


def test_no_production_module_imports_or_reaches_the_slice_unguarded():
    references = {}
    for path in sorted(PACKAGE.glob("*.py")):
        for module in ("runtime_generation_preparation", "runtime_generations"):
            if module in _imported(path):
                references.setdefault(module, set()).add(path.name)
    assert references == {
        "runtime_generation_preparation": {"runtime_preparation.py"},
        "runtime_generations": {"runtime_generation_preparation.py"},
    }
    guards = _guarded_imports(PACKAGE / "runtime_preparation.py", "runtime_generation_preparation")
    assert guards == [True, True]
    probe = (
        "import sys\n"
        "import cbs_transcriber.app, cbs_transcriber.jobs, cbs_transcriber.runtime_preparation\n"
        "prefix = 'cbs_transcriber.runtime_generation'\n"
        "print(sorted(m for m in sys.modules if m.startswith(prefix)))\n"
    )
    environment = {**os.environ, "PYTHONPATH": str(PACKAGE.parent)}
    result = subprocess.run(
        [sys.executable, "-B", "-c", probe],
        capture_output=True,
        text=True,
        timeout=120,
        env=environment,
        cwd=PACKAGE.parent,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_stage_one_metadata_still_has_no_public_promotion_or_capture(generation):  # noqa: F811
    from cbs_transcriber import runtime_generation_preparation as subject

    public = {name for name in dir(generation) if not name.startswith("_")}
    assert not {"promote", "capture_active", "publish_active"} & public
    assert generation.ACTIVE_POINTERS_ENABLED is False and subject.ENABLED is False
    assert preparation.GENERATION_ONLY < preparation.GENERATION_RESERVED
