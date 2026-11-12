"""Disabled generation metadata cannot claim readiness or mutate other settings."""

import copy
import importlib
import json
import sqlite3

import pytest

from cbs_transcriber import enhancement_api, providers


@pytest.fixture
def generation():
    return importlib.import_module("cbs_transcriber.runtime_generations")


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    connection.executemany(
        "INSERT INTO settings VALUES (?,?)", [(9, "keep-nine"), (10, "keep-ten")]
    )
    yield connection
    connection.close()


@pytest.fixture
def settings():
    return providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.sdu.dk",
        model="whisperx-large-v3",
        language="da",
        capability="words_speakers",
        ucloud_transcriber={"project_id": "synthetic-project", "storage_path": "/drive/app"},
    )


def compute(device="cuda"):
    return {
        "product": {
            "id": "gpu-nvidia-b200-1-mig.1g" if device == "cuda" else "cpu-amd-zen5-8-vcpu",
            "category": "gpu-nvidia-b200" if device == "cuda" else "cpu-amd-zen5",
            "provider": "ucloud",
        },
        "device": device,
        "flavor": "cuda-cu128" if device == "cuda" else "cpu",
    }


def target(generation, settings, device="cuda", digest="a" * 64):
    flavor = "cu128" if device == "cuda" else "cpu"
    return generation.base_target(
        settings,
        runtime_id=f"whisperx-3.8.6-{flavor}-{digest[:12]}",
        requirements_sha256=digest,
        compute=compute(device),
    )


def selection(generation, base, device="cuda", token=None):
    return {
        "policy": generation.SELECTION_POLICY,
        "target": base,
        "compute": compute(device),
        "revision": 0 if token is None else 1,
        "generation": None
        if token is None
        else {"policy": generation.GENERATION_POLICY, "token": token, "owner_id": "b" * 32},
    }


def reserve(generation, db, base, *, owner="b" * 32, token="c" * 32, expected=0):
    return generation.reserve_candidate(
        db,
        base,
        expected_revision=expected,
        owner_id=owner,
        token=token,
        operation_sha256="d" * 64,
        compute=compute(base["device"]),
    )


def test_stage1_is_disabled_and_has_no_promotion_or_capture_authority(generation, db, settings):
    base = target(generation, settings)
    assert generation.CANDIDATE_RESERVATION_ENABLED is False
    assert not hasattr(generation, "promote")
    assert not hasattr(generation, "capture_active")
    before = db.execute("SELECT * FROM settings ORDER BY id").fetchall()
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(generation.Conflict, match="disabled"):
        reserve(generation, db, base)
    db.rollback()
    assert db.execute("SELECT * FROM settings ORDER BY id").fetchall() == before


def test_cpu_cuda_targets_are_distinct_and_wallets_are_not_target_identity(generation, settings):
    gpu = target(generation, settings)
    cpu = target(generation, settings, "cpu")
    assert gpu["target_sha256"] != cpu["target_sha256"]
    changed = compute()
    changed["product"]["id"] = "structural-reference-only"
    same = generation.base_target(
        settings,
        runtime_id=gpu["runtime_id"],
        requirements_sha256=gpu["requirements_sha256"],
        compute=changed,
    )
    assert same == gpu  # Products bind operations; this codec grants no funding/eligibility.
    with pytest.raises(generation.Conflict):
        generation.validate_compute({**compute(), "wallet_checked_at": "not-target-data"})


@pytest.mark.parametrize("field", ["server", "project_id", "storage_path", "requirements_sha256"])
def test_target_hash_binds_connection_and_base_recipe(generation, settings, field):
    base = target(generation, settings)
    changed = copy.deepcopy(base)
    changed[field] = {
        "server": "https://other.invalid",
        "project_id": "another-project",
        "storage_path": "/drive/other",
        "requirements_sha256": "f" * 64,
    }[field]
    with pytest.raises(generation.Conflict):
        generation.validate_target(changed)


@pytest.mark.parametrize("bad", [None, {}, {"policy": None}, {"policy": "future-v99"}])
def test_unknown_null_selection_refuses(generation, settings, bad):
    with pytest.raises(generation.Conflict):
        generation.validate_selection(bad)
    with pytest.raises(generation.Conflict):
        generation.runtime_path(settings, runtime_id=enhancement_api.runtime_id(), selection=bad)


def test_explicit_legacy_and_selected_generation_paths_are_pure(generation, settings, monkeypatch):
    base = target(generation, settings)
    legacy = selection(generation, base)
    selected = selection(generation, base, token="c" * 32)
    expected = settings.ucloud_transcriber.storage_path + "/cbs-runtime/" + base["runtime_id"]
    assert generation.runtime_path(settings, runtime_id=base["runtime_id"]) == expected
    assert generation.runtime_path(
        settings, runtime_id=enhancement_api.runtime_id()
    ) == enhancement_api.runtime_folder(settings)
    assert (
        generation.runtime_path(settings, runtime_id=base["runtime_id"], selection=legacy)
        == expected
    )
    assert (
        generation.runtime_path(settings, runtime_id=base["runtime_id"], selection=selected)
        == expected + "-g-" + "c" * 32
    )
    assert generation.validate_selection(selected) == selected
    mutated = generation.validate_selection(selected)
    mutated["compute"]["product"]["id"] = "different"
    assert selected["compute"] == compute()
    with pytest.raises(generation.Conflict):
        generation.validate_selection(selected, compute=compute("cpu"))


@pytest.mark.parametrize("revision", [True, -1, 2**53, "1", 1.5])
def test_selection_and_candidate_revisions_are_strict(
    generation, db, settings, monkeypatch, revision
):
    base = target(generation, settings)
    chosen = selection(generation, base)
    chosen["revision"] = revision
    with pytest.raises(generation.Conflict):
        generation.validate_selection(chosen)
    monkeypatch.setattr(generation, "CANDIDATE_RESERVATION_ENABLED", True)
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(generation.Conflict):
        reserve(generation, db, base, expected=revision)
    assert db.execute("SELECT value FROM settings WHERE id=11").fetchone() is None
    db.rollback()


def test_selection_cannot_cross_cpu_cuda_or_connection(generation, settings):
    base = target(generation, settings)
    selected = selection(generation, base, token="c" * 32)
    mixed = copy.deepcopy(selected)
    mixed["compute"] = compute("cpu")
    with pytest.raises(generation.Conflict):
        generation.validate_selection(mixed)
    other = settings.model_copy(deep=True)
    other.ucloud_transcriber.storage_path = "/drive/another"
    with pytest.raises(generation.Conflict):
        generation.runtime_path(other, runtime_id=base["runtime_id"], selection=selected)


@pytest.mark.parametrize("token", ["../escape", "A" * 32, "c" * 31, "c" * 33])
def test_paths_refuse_invalid_generation_tokens(generation, settings, token):
    base = target(generation, settings)
    with pytest.raises(generation.Conflict):
        generation.runtime_path(
            settings,
            runtime_id=base["runtime_id"],
            selection=selection(generation, base, token=token),
        )


def test_snapshot_is_read_only_and_reservation_cas_preserves_slots(
    generation, db, settings, monkeypatch
):
    base = target(generation, settings)
    assert generation.snapshot(db, base) == {
        "target_sha256": base["target_sha256"],
        "revision": 0,
        "active": None,
        "candidate": None,
    }
    assert db.execute("SELECT value FROM settings WHERE id=11").fetchone() is None
    monkeypatch.setattr(generation, "CANDIDATE_RESERVATION_ENABLED", True)
    with pytest.raises(generation.Conflict, match="transaction"):
        reserve(generation, db, base)
    db.execute("BEGIN IMMEDIATE")
    first = reserve(generation, db, base)
    raw = db.execute("SELECT value FROM settings WHERE id=11").fetchone()[0]
    assert reserve(generation, db, base) == first
    assert db.execute("SELECT value FROM settings WHERE id=11").fetchone()[0] == raw
    with pytest.raises(generation.Conflict):
        reserve(generation, db, base, token="e" * 32)
    with pytest.raises(generation.Conflict):
        reserve(generation, db, base, expected=1)
    db.commit()
    assert generation.snapshot(db, base) == first
    assert db.execute("SELECT * FROM settings WHERE id IN (9,10) ORDER BY id").fetchall() == [
        (9, "keep-nine"),
        (10, "keep-ten"),
    ]


@pytest.mark.parametrize("raw", ["null", "{}", '{"policy":null,"targets":{}}', "[]", "{"])
def test_occupied_unknown_registry_refuses_without_overwrite(
    generation, db, settings, monkeypatch, raw
):
    db.execute("INSERT INTO settings VALUES (11,?)", (raw,))
    base = target(generation, settings)
    with pytest.raises(generation.Conflict):
        generation.snapshot(db, base)
    monkeypatch.setattr(generation, "CANDIDATE_RESERVATION_ENABLED", True)
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(generation.Conflict):
        reserve(generation, db, base)
    db.rollback()
    assert db.execute("SELECT value FROM settings WHERE id=11").fetchone()[0] == raw


def test_duplicate_keys_oversize_and_active_pointer_are_not_accepted(generation, db, settings):
    base = target(generation, settings)
    entries = [
        '{"policy":"x","policy":"y","targets":{}}',
        " " * (generation.MAX_BYTES + 1),
        json.dumps(
            {
                "policy": generation.REGISTRY_POLICY,
                "targets": {
                    base["target_sha256"]: {
                        "target": base,
                        "revision": 1,
                        "candidate": None,
                        "active": {"token": "c" * 32, "owner_id": "b" * 32},
                    }
                },
            }
        ),
    ]
    for raw in entries:
        db.execute("INSERT OR REPLACE INTO settings VALUES (11,?)", (raw,))
        with pytest.raises(generation.Conflict):
            generation.snapshot(db, base)


def test_candidate_limit_and_cross_target_token_reuse_refuse(generation, db, settings, monkeypatch):
    monkeypatch.setattr(generation, "CANDIDATE_RESERVATION_ENABLED", True)
    monkeypatch.setattr(generation, "MAX_TARGETS", 1)
    gpu, cpu = target(generation, settings), target(generation, settings, "cpu")
    db.execute("BEGIN IMMEDIATE")
    reserve(generation, db, gpu)
    before = db.execute("SELECT value FROM settings WHERE id=11").fetchone()[0]
    with pytest.raises(generation.Conflict):
        reserve(generation, db, cpu, owner="e" * 32, token="f" * 32)
    monkeypatch.setattr(generation, "MAX_TARGETS", 128)
    with pytest.raises(generation.Conflict):
        reserve(generation, db, cpu, owner="e" * 32)
    assert db.execute("SELECT value FROM settings WHERE id=11").fetchone()[0] == before
    db.rollback()
