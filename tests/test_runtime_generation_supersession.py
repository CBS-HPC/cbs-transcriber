"""ADR 0176 Stage 2: disabled supersession of a settled failed Runtime generation candidate.

Synthetic storage, the in-memory UCloud drive and mocked clients only. Nothing contacts
UCloud or Hugging Face, prepares a real folder, downloads a model or touches a real library.
"""

import copy
import json
import threading
import time

import pytest
from test_huggingface_runtime_plan import configured as configured  # noqa: F401 - fixture
from test_runtime_generation_preparation import (
    GATES,
    _cache,
    _failure,
    admit,
    raw_settings,
    registry,
    roots,
)
from test_runtime_generation_preparation import env as env  # noqa: F401 - fixture
from test_runtime_preparation import execute

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import runtime_compute, runtime_store
from cbs_transcriber import runtime_generation_preparation as subject
from cbs_transcriber import runtime_generations as generations
from cbs_transcriber import runtime_preparation as preparation

SETTLED_MODES = (
    "disabled-worker",
    "environment-conflict",
    "environment-lost",
    "run-malformed",
    "remote-failure",
    "success-unverified",
    "stopped-before-post",
    "stopped-after-post",
)


def _rows(f):
    return f.store.all("SELECT * FROM runtime_preparations ORDER BY id")


def _row(f, identifier):
    return f.store.one("SELECT * FROM runtime_preparations WHERE id=?", (identifier,))


def _under(path, root):
    return path == root or path.startswith(root + "/")


def _store_journal(f, identifier, journal, **columns):
    sets = ",".join(["journal=?", *(key + "=?" for key in columns)])
    f.store.execute(
        "UPDATE runtime_preparations SET " + sets + " WHERE id=?",
        (runtime_store.canonical(journal), *columns.values(), identifier),
    )


def _interrupt_with_open_allocation(f, monkeypatch):
    """A recorded allocation still RUNNING when the worker shuts down (state interrupted)."""
    f.cloud.state, f.drive.complete = "RUNNING", False
    original = preparation.Run._wait

    def shutdown(self, delay):
        self.worker.stopping.set()

    monkeypatch.setattr(preparation.Run, "_wait", shutdown)
    value, _ = execute(f)
    monkeypatch.setattr(preparation.Run, "_wait", original)
    assert value["state"] == "interrupted" and value["journal"]["job_id"]
    assert value["journal"]["remote_state"] == "RUNNING"
    return value


def failed_candidate(f, monkeypatch, mode):
    """Admit one candidate and leave its owner settled in the given synthetic way."""
    value, _ = admit(f)
    root, run = roots(value, f)
    if mode == "disabled-worker":
        monkeypatch.setattr(subject, "ENABLED", False)
        execute(f)
        monkeypatch.setattr(subject, "ENABLED", True)
    elif mode == "environment-conflict":
        f.drive.mkdirs(root)
        execute(f)
    elif mode == "environment-lost":
        f.drive.create_modes[root] = "lost"
        execute(f)
    elif mode == "run-malformed":
        f.drive.create_modes[run] = "malformed"
        execute(f)
    elif mode == "remote-failure":
        f.drive.damage = _failure
        execute(f)
    elif mode == "success-unverified":
        f.drive.damage = _cache
        execute(f)
    elif mode == "stopped-before-post":

        def stop(path):
            if path == root:
                preparation.cancel(f.store, f.credentials, value["id"])

        f.drive.on_create = stop
        execute(f)
    else:
        assert mode == "stopped-after-post"
        _interrupt_with_open_allocation(f, monkeypatch)
        card = preparation.cancel(f.store, f.credentials, value["id"])
        assert card["state"] == "cancelled" and f.cloud.terminations == ["remote-preparation"]
    f.drive.damage = f.drive.on_create = None
    f.drive.complete, f.cloud.state = True, "SUCCESS"
    final = preparation.get(f.store, value["id"])
    assert final["state"] in subject.SETTLED, (mode, final["state"], final["message"])
    assert not runtime_store._reserved(final["journal"])
    assert registry(f, final)["candidate"]["owner_id"] == final["id"]
    return final


def _frozen_supersession(f, owner):
    """Bypass the preview's own check: a record a forged or stale client could send."""
    confirmation = subject._freeze(f.store, f.credentials)[0]
    candidate = registry(f, owner)["candidate"]
    return subject._supersession(confirmation, candidate, subject._evidence(owner))


def _rehash(record):
    body = {key: item for key, item in record.items() if key != "supersession_sha256"}
    return {**body, "supersession_sha256": runtime_store._digest(body)}


def _successor(f, result):
    return preparation.get(f.store, result["operation"]["runtime_preparation_id"])


# ------------------------------------------------------------------- gates and reach


def test_supersession_entries_refuse_at_shipped_defaults(configured, monkeypatch):  # noqa: F811
    f = configured
    assert [getattr(module, name) for module, name in GATES] == [False, False, False]
    for module, name in ((api.native, "client_for"), (runtime_compute, "freeze")):
        monkeypatch.setattr(module, name, lambda *_, **__: pytest.fail("network while disabled"))
    before = raw_settings(f)
    for call in (
        lambda: subject.preview_supersession(f.store, f.credentials),
        lambda: subject.supersede(f.store, f.credentials, {}, lambda: None),
    ):
        with pytest.raises(subject.Conflict, match="disabled"):
            call()
    with f.store.connection() as db, pytest.raises(generations.Conflict, match="disabled"):
        db.execute("BEGIN IMMEDIATE")
        generations.supersede_candidate(
            db,
            {},
            superseded={},
            owner_id="a" * 32,
            token="b" * 32,
            operation_sha256="c" * 64,
            compute={},
        )
    assert not f.store.all("SELECT id FROM runtime_preparations")
    assert raw_settings(f) == before


@pytest.mark.parametrize("off", ["slice", "reservation", "pointers"])
def test_any_switch_off_refuses_supersession_before_any_effect(env, monkeypatch, off):  # noqa: F811
    f = env
    owner = failed_candidate(f, monkeypatch, "remote-failure")
    record = subject.preview_supersession(f.store, f.credentials)
    module, name = {
        "slice": (subject, "ENABLED"),
        "reservation": (generations, "CANDIDATE_RESERVATION_ENABLED"),
        "pointers": (generations, "ACTIVE_POINTERS_ENABLED"),
    }[off]
    monkeypatch.setattr(module, name, False)
    for item, attribute in ((api.native, "client_for"), (runtime_compute, "freeze")):
        monkeypatch.setattr(item, attribute, lambda *_, **__: pytest.fail("network"))
    rows, settings = _rows(f), raw_settings(f)
    for call in (
        lambda: subject.preview_supersession(f.store, f.credentials),
        lambda: subject.supersede(f.store, f.credentials, record, lambda: None),
    ):
        with pytest.raises(subject.Conflict, match="disabled"):
            call()
    assert _rows(f) == rows and raw_settings(f) == settings
    assert registry(f, owner)["candidate"]["owner_id"] == owner["id"]


# ---------------------------------------------------------------- settled failures


@pytest.mark.parametrize("mode", SETTLED_MODES)
def test_a_settled_failed_candidate_is_superseded_and_the_successor_promotes(
    env,  # noqa: F811
    monkeypatch,
    mode,
):
    f = env
    old = failed_candidate(f, monkeypatch, mode)
    old_root, old_run = roots(old, f)
    old_items = {k: v for k, v in f.drive.items.items() if _under(k, old_root)}
    submissions, trashed = len(f.cloud.submissions), list(f.cloud.trashed)
    settings = [row for row in raw_settings(f) if row["id"] != 11]
    entry_before = registry(f, old)
    f.drive.touched.clear()
    f.drive.creates.clear()

    record = subject.preview_supersession(f.store, f.credentials)
    assert record["superseded"] == entry_before["candidate"]
    evidence = record["evidence"]
    assert evidence["owner_id"] == old["id"] and evidence["state"] == old["state"]
    assert evidence["folders"] == {
        kind: entry["outcome"]
        for kind, entry in (old["journal"].get("generation_folders") or {}).items()
    }
    result = subject.supersede(f.store, f.credentials, record, lambda: None)

    # Nothing remote: no read, create, upload, POST or trash for preview or acceptance.
    assert f.drive.touched == [] and f.drive.creates == []
    assert len(f.cloud.submissions) == submissions and f.cloud.trashed == trashed
    assert [row for row in raw_settings(f) if row["id"] != 11] == settings
    assert not result["deduplicated"]
    new = _successor(f, result)
    assert new["id"] != old["id"] and new["state"] == "queued"
    generation = new["frozen"]["generation"]
    assert generation["token"] != old["frozen"]["generation"]["token"]
    assert generation["expected_revision"] == entry_before["revision"]
    assert generation["confirmation_sha256"] == record["confirmation"]["confirmation_sha256"]
    entry = registry(f, new)
    assert entry["revision"] == entry_before["revision"] and entry["active"] is None
    assert entry["candidate"] == {
        "token": generation["token"],
        "owner_id": new["id"],
        "expected_revision": entry_before["revision"],
        "operation_sha256": record["confirmation"]["operation_sha256"],
        "compute": record["confirmation"]["operation"]["compute"],
    }

    retired = preparation.get(f.store, old["id"])
    receipt = retired["journal"]["generation_supersession"]
    assert receipt == {
        "format": subject.SUPERSESSION_FORMAT,
        "successor_owner_id": new["id"],
        "successor_token": generation["token"],
        "supersession_sha256": record["supersession_sha256"],
        "observed_sha256": evidence["observed_sha256"],
        "registry_revision": entry_before["revision"],
    }
    # Every earlier record is retained exactly; only the receipt, epoch and message change.
    assert {k: v for k, v in retired["journal"].items() if k != "generation_supersession"} == (
        old["journal"]
    )
    assert retired["frozen"] == old["frozen"] and retired["state"] == old["state"]
    assert retired["epoch"] == old["epoch"] + 1 and "Superseded" in retired["message"]
    card = preparation.card(retired)
    assert not card["can_retry"] and not card["can_cancel"] and not card["can_reconcile"]
    assert result["superseded"]["runtime_preparation_id"] == old["id"]
    if mode in {"environment-lost", "run-malformed"}:
        # The uncertain intent stays a visible obligation on the old owner only.
        assert old["id"] in {item["id"] for item in preparation.obligations(f.store)}

    retired_row = _row(f, old["id"])
    final, _ = execute(f)
    assert final["id"] == new["id"] and final["state"] == "completed", final["message"]
    new_root, new_run = roots(final, f)
    assert {new_root, new_run}.isdisjoint({old_root, old_run})
    assert f.drive.creates == [new_root, new_run]
    assert not [p for p in f.drive.touched if _under(p, old_root) or _under(p, old_run)]
    assert {k: v for k, v in f.drive.items.items() if _under(k, old_root)} == old_items
    assert _row(f, old["id"]) == retired_row
    entry = registry(f, final)
    assert entry["candidate"] is None and entry["revision"] == entry_before["revision"] + 1
    assert entry["active"]["owner_id"] == new["id"]
    assert subject.active(f.store, generation["target"])["root"] == new_root


def test_supersession_with_an_active_generation_keeps_it_until_the_successor_promotes(
    env,  # noqa: F811
    monkeypatch,
):
    f = env
    admit(f)
    a, _ = execute(f)
    assert a["state"] == "completed", a["message"]
    root_a, run_a = roots(a, f)
    a_items = {k: v for k, v in f.drive.items.items() if _under(k, root_a)}
    a_row = _row(f, a["id"])
    b = failed_candidate(f, monkeypatch, "remote-failure")
    root_b, _ = roots(b, f)
    b_items = {k: v for k, v in f.drive.items.items() if _under(k, root_b)}
    record = subject.preview_supersession(f.store, f.credentials)
    assert record["confirmation"]["expected_revision"] == 1
    result = subject.supersede(f.store, f.credentials, record, lambda: None)
    entry = registry(f, b)
    assert entry["revision"] == 1 and entry["active"]["owner_id"] == a["id"]
    assert subject.active(f.store, a["frozen"]["generation"]["target"])["root"] == root_a
    c, _ = execute(f)
    assert c["id"] == result["operation"]["runtime_preparation_id"]
    assert c["state"] == "completed", c["message"]
    entry = registry(f, c)
    assert entry["revision"] == 2 and entry["active"]["owner_id"] == c["id"]
    assert entry["candidate"] is None
    assert _row(f, a["id"]) == a_row
    assert {k: v for k, v in f.drive.items.items() if _under(k, root_a)} == a_items
    assert {k: v for k, v in f.drive.items.items() if _under(k, root_b)} == b_items
    assert c["journal"]["generation_promotion"]["registry_revision"] == 2


# -------------------------------------------------------------- unsettled candidates


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("queued", "still active"),
        ("running", "still active"),
        ("uncertain-post", "uncertain"),
        ("allocation-open", "allocation or capacity"),
        ("stop-pending", "still active"),
    ],
)
def test_an_unsettled_candidate_is_never_superseded(env, monkeypatch, mode, message):  # noqa: F811
    f = env
    value, _ = admit(f)
    if mode == "running":
        value = preparation.claim(f.store)
    elif mode == "uncertain-post":
        f.cloud.submit_error = True
        execute(f)
    elif mode in {"allocation-open", "stop-pending"}:
        _interrupt_with_open_allocation(f, monkeypatch)
        if mode == "stop-pending":
            monkeypatch.setattr(f.cloud, "terminate_job", lambda _: None)  # Never confirms.
            assert preparation.cancel(f.store, f.credentials, value["id"])["state"] == "queued"
    owner = preparation.get(f.store, value["id"])
    rows, settings = _rows(f), raw_settings(f)
    with pytest.raises(subject.Conflict, match=message):
        subject.preview_supersession(f.store, f.credentials)
    forged = _frozen_supersession(f, owner)
    with pytest.raises(subject.Conflict, match=message):
        subject.supersede(f.store, f.credentials, forged, lambda: None)
    assert _rows(f) == rows and raw_settings(f) == settings
    assert registry(f, owner)["candidate"]["owner_id"] == owner["id"]


def test_without_a_candidate_there_is_nothing_to_supersede(env):  # noqa: F811
    f = env
    with pytest.raises(subject.Conflict, match="No Runtime generation candidate"):
        subject.preview_supersession(f.store, f.credentials)
    admit(f)
    promoted, _ = execute(f)
    assert promoted["state"] == "completed"
    with pytest.raises(subject.Conflict, match="No Runtime generation candidate"):
        subject.preview_supersession(f.store, f.credentials)


def _rewrite_candidate(f, change):
    raw = f.store.one("SELECT value FROM settings WHERE id=11")["value"]
    value = json.loads(raw)
    (entry,) = value["targets"].values()
    change(entry["candidate"])
    f.store.execute("UPDATE settings SET value=? WHERE id=11", (generations._encoded(value),))


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("missing-owner", "no owner record"),
        ("token", "does not match"),
        ("operation", "does not match"),
        ("archived", "historical or unverified"),
        ("unverified", "historical or unverified"),
    ],
)
def test_a_candidate_without_its_exact_owner_refuses(env, monkeypatch, damage, message):  # noqa: F811
    f = env
    owner = failed_candidate(f, monkeypatch, "remote-failure")
    if damage == "missing-owner":
        _rewrite_candidate(f, lambda candidate: candidate.update(owner_id="e" * 32))
    elif damage == "token":
        _rewrite_candidate(f, lambda candidate: candidate.update(token="f" * 32))
    elif damage == "operation":
        _rewrite_candidate(f, lambda candidate: candidate.update(operation_sha256="d" * 64))
    elif damage == "archived":
        f.store.execute("UPDATE runtime_preparations SET archived=1 WHERE id=?", (owner["id"],))
    else:
        f.store.execute("UPDATE runtime_preparations SET journal='{}' WHERE id=?", (owner["id"],))
    rows, settings = _rows(f), raw_settings(f)
    with pytest.raises(subject.Conflict, match=message):
        subject.preview_supersession(f.store, f.credentials)
    assert _rows(f) == rows and raw_settings(f) == settings


# ------------------------------------------------------- stale or forged confirmations


def _missing(record, f, owner, monkeypatch):
    del record["evidence"]
    return record


def _extra(record, f, owner, monkeypatch):
    record["note"] = "forged"
    return record


def _owner_shape(record, f, owner, monkeypatch):
    record["superseded"]["owner_id"] = "NOT-HEX"
    return record


def _hash(record, f, owner, monkeypatch):
    record["supersession_sha256"] = "0" * 64
    return record


def _retyped(record, f, owner, monkeypatch):
    record["superseded"]["expected_revision"] = float(record["superseded"]["expected_revision"])
    return _rehash(record)


def _evidence_edit(record, f, owner, monkeypatch):
    record["evidence"]["state"] = "cancelled" if owner["state"] != "cancelled" else "failed"
    return _rehash(record)


def _other_owner(record, f, owner, monkeypatch):
    record["superseded"]["owner_id"] = "e" * 32
    return _rehash(record)


def _stale_owner(record, f, owner, monkeypatch):
    # Explicit cleanup after the preview changes the observed owner.
    preparation.cleanup(f.store, f.credentials, owner["id"])
    return record


def _stale_request(record, f, owner, monkeypatch):
    monkeypatch.setattr(
        api, "script_application", lambda _: {"name": "terminal-debian", "version": "other"}
    )
    return record


@pytest.mark.parametrize(
    ("forge", "message"),
    [
        (_missing, "confirm the Runtime generation supersession again"),
        (_extra, "confirm the Runtime generation supersession again"),
        (_owner_shape, "confirm the Runtime generation supersession again"),
        (_hash, "superseded Runtime generation changed"),
        (_retyped, "candidate changed"),
        (_evidence_edit, "superseded Runtime generation changed"),
        (_other_owner, "candidate changed"),
        (_stale_owner, "superseded Runtime generation changed"),
        (_stale_request, "request changed"),
    ],
    ids=lambda item: getattr(item, "__name__", "").strip("_") or None,
)
def test_a_stale_or_forged_supersession_refuses_and_changes_nothing(
    env,  # noqa: F811
    monkeypatch,
    forge,
    message,
):
    f = env
    owner = failed_candidate(f, monkeypatch, "success-unverified")
    record = forge(subject.preview_supersession(f.store, f.credentials), f, owner, monkeypatch)
    rows, settings = _rows(f), raw_settings(f)
    with pytest.raises(subject.Conflict, match=message):
        subject.supersede(f.store, f.credentials, record, lambda: None)
    assert _rows(f) == rows and raw_settings(f) == settings
    assert registry(f, owner)["candidate"]["owner_id"] == owner["id"]


def test_a_repeated_supersession_returns_its_recorded_successor_while_current(
    env,  # noqa: F811
    monkeypatch,
):
    f = env
    old = failed_candidate(f, monkeypatch, "remote-failure")
    record = subject.preview_supersession(f.store, f.credentials)
    first = subject.supersede(f.store, f.credentials, record, lambda: None)
    rows, settings = _rows(f), raw_settings(f)
    again = subject.supersede(f.store, f.credentials, copy.deepcopy(record), lambda: None)
    assert again["deduplicated"] and again["operation"]["id"] == first["operation"]["id"]
    assert again["superseded"]["runtime_preparation_id"] == old["id"]
    assert _rows(f) == rows and raw_settings(f) == settings
    # A different record for the same old candidate never creates a second successor.
    other = _evidence_edit(copy.deepcopy(record), f, old, monkeypatch)
    with pytest.raises(subject.Conflict, match="candidate changed"):
        subject.supersede(f.store, f.credentials, other, lambda: None)
    promoted, _ = execute(f)
    assert promoted["state"] == "completed"
    # Once the successor is promoted the frozen request is stale: review again, never a
    # second successor (deduplication holds only while the confirmation is current).
    rows, settings = _rows(f), raw_settings(f)
    with pytest.raises(subject.Conflict, match="request changed"):
        subject.supersede(f.store, f.credentials, copy.deepcopy(record), lambda: None)
    assert _rows(f) == rows and raw_settings(f) == settings and len(rows) == 2


def test_concurrent_identical_supersessions_create_exactly_one_successor(env, monkeypatch):  # noqa: F811
    f = env
    failed_candidate(f, monkeypatch, "remote-failure")
    record = subject.preview_supersession(f.store, f.credentials)
    barrier, results, errors = threading.Barrier(2), [], []

    def run():
        try:
            barrier.wait(30)
            results.append(subject.supersede(f.store, f.credentials, record, lambda: None))
        except BaseException as exc:  # noqa: B036 - reported to the main thread
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
    # The old owner's control slot may turn the loser away; it never makes a second owner.
    assert all(isinstance(error, preparation.ControlBusy) for error in errors), errors
    assert results and len({item["operation"]["id"] for item in results}) == 1
    assert len(_rows(f)) == 2


def test_a_concurrent_control_on_the_old_owner_excludes_supersession(env, monkeypatch):  # noqa: F811
    f = env
    owner = failed_candidate(f, monkeypatch, "success-unverified")
    record = subject.preview_supersession(f.store, f.credentials)
    rows, settings = _rows(f), raw_settings(f)
    with preparation._control(owner["id"]), pytest.raises(preparation.ControlBusy):
        subject.supersede(f.store, f.credentials, record, lambda: None)
    assert _rows(f) == rows and raw_settings(f) == settings


# ------------------------------------------------------------------------ atomicity


@pytest.mark.parametrize("failure", ["registry", "receipt", "guard"])
def test_any_failure_inside_the_writer_leaves_nothing(env, monkeypatch, failure):  # noqa: F811
    f = env
    owner = failed_candidate(f, monkeypatch, "remote-failure")
    record = subject.preview_supersession(f.store, f.credentials)
    guard = lambda: None  # noqa: E731
    if failure == "registry":

        def refuse(*args, **kwargs):
            raise generations.Conflict("synthetic refused registry swap")

        monkeypatch.setattr(generations, "supersede_candidate", refuse)
    elif failure == "receipt":
        real = preparation._update

        def refuse_receipt(db, value, **kwargs):
            if "generation_supersession" in (kwargs.get("journal") or {}):
                raise RuntimeError("synthetic failure after inserting the successor")
            return real(db, value, **kwargs)

        monkeypatch.setattr(preparation, "_update", refuse_receipt)
    else:
        calls = []

        def guard():
            calls.append(1)
            if len(calls) == 3:  # Only after BEGIN, at the acceptance point.
                raise subject.Conflict("synthetic HF access revoked")

    rows, settings = _rows(f), raw_settings(f)
    with pytest.raises((ValueError, RuntimeError)):
        subject.supersede(f.store, f.credentials, record, guard)
    assert _rows(f) == rows and raw_settings(f) == settings
    assert registry(f, owner)["candidate"]["owner_id"] == owner["id"]


# ------------------------------------------------- the superseded owner stays settled


def test_a_superseded_owner_is_never_retried_promoted_requeued_or_reserved(env, monkeypatch):  # noqa: F811
    f = env
    old = failed_candidate(f, monkeypatch, "success-unverified")
    assert preparation.card(old)["can_retry"]  # Before: known-ID re-verification was offered.
    record = subject.preview_supersession(f.store, f.credentials)
    subject.supersede(f.store, f.credentials, record, lambda: None)
    retired = preparation.get(f.store, old["id"])
    row = _row(f, old["id"])
    with pytest.raises(preparation.Conflict):
        preparation.retry(f.store, f.credentials, old["id"])
    with pytest.raises(subject.Conflict, match="never promoted"):
        subject.verify_and_promote(
            f.store, old["id"], retired["epoch"], f.cloud, time.monotonic() + 30
        )
    receipt = retired["journal"]["generation_supersession"]
    with pytest.raises(preparation.Interrupted):
        preparation.checkpoint(f.store, old["id"], old["epoch"], message="stale writer")
    altered = {**receipt, "successor_owner_id": "e" * 32}
    for journal in ({"generation_supersession": altered}, {"generation_supersession": None}):
        with f.store.connection() as db, pytest.raises(preparation.Conflict, match="permanent"):
            db.execute("BEGIN IMMEDIATE")
            value = preparation._get(db, old["id"])
            preparation._update(db, value, journal=journal, _generation_writer=True)
    assert _row(f, old["id"]) == row


@pytest.mark.parametrize(
    "change",
    [
        {"state": "queued"},
        {"state": "running"},
        {"journal": {"capacity_reserved": True}},
        {"journal": {"job_id": "remote-other"}},
        {"journal": {"generation_supersession": None}},
    ],
    ids=["requeue", "run", "reserve", "rebind-job", "drop-receipt"],
)
def test_a_generic_write_cannot_unsettle_a_superseded_owner(env, monkeypatch, change):  # noqa: F811
    f = env
    old = failed_candidate(f, monkeypatch, "success-unverified")
    subject.supersede(
        f.store, f.credentials, subject.preview_supersession(f.store, f.credentials), lambda: None
    )
    retired = preparation.get(f.store, old["id"])
    row = _row(f, old["id"])
    with pytest.raises(preparation.Conflict):
        preparation.checkpoint(f.store, old["id"], retired["epoch"], **change)
    assert _row(f, old["id"]) == row


def test_cleanup_of_the_superseded_run_folder_spares_both_generation_roots(env, monkeypatch):  # noqa: F811
    f = env
    old = failed_candidate(f, monkeypatch, "success-unverified")
    old_root, old_run = roots(old, f)
    assert old["journal"]["cleanup_state"] == "pending"
    result = subject.supersede(
        f.store, f.credentials, subject.preview_supersession(f.store, f.credentials), lambda: None
    )
    new_root, _ = roots(_successor(f, result), f)
    card = preparation.cleanup(f.store, f.credentials, old["id"])
    assert f.cloud.trashed == [old_run]
    assert card["ucloud_cleanup_state"] == "trashed" and card["can_remove"]
    retired = preparation.get(f.store, old["id"])
    assert (
        retired["journal"]["generation_supersession"]["successor_owner_id"]
        == result["operation"]["runtime_preparation_id"]
    )
    assert old_root not in f.cloud.trashed and new_root not in f.cloud.trashed


def _valid_receipt(owner):
    return {
        "format": subject.SUPERSESSION_FORMAT,
        "successor_owner_id": "e" * 32,
        "successor_token": "f" * 32,
        "supersession_sha256": "a" * 64,
        "observed_sha256": "b" * 64,
        "registry_revision": owner["frozen"]["generation"]["expected_revision"],
    }


@pytest.mark.parametrize(
    "forge",
    [
        "control",
        "format",
        "successor-self",
        "token-self",
        "revision-retyped",
        "revision-other",
        "extra-key",
        "null",
        "state-queued",
        "reserved",
    ],
)
def test_decoder_refuses_a_forged_supersession_receipt(env, monkeypatch, forge):  # noqa: F811
    f = env
    owner = failed_candidate(f, monkeypatch, "remote-failure")
    receipt, columns = _valid_receipt(owner), {}
    journal = dict(owner["journal"])
    if forge == "format":
        receipt["format"] = "cbs-runtime-generation-supersession-receipt-v2"
    elif forge == "successor-self":
        receipt["successor_owner_id"] = owner["id"]
    elif forge == "token-self":
        receipt["successor_token"] = owner["frozen"]["generation"]["token"]
    elif forge == "revision-retyped":
        receipt["registry_revision"] = float(receipt["registry_revision"])
    elif forge == "revision-other":
        receipt["registry_revision"] += 1
    elif forge == "extra-key":
        receipt["note"] = "forged"
    elif forge == "null":
        receipt = None
    elif forge == "state-queued":
        columns["state"] = "queued"
    elif forge == "reserved":
        journal["capacity_reserved"] = True
    journal["generation_supersession"] = receipt
    _store_journal(f, owner["id"], journal, **columns)
    if forge == "control":
        decoded = preparation.get(f.store, owner["id"])
        assert decoded["journal"]["generation_supersession"] == receipt
        assert not preparation.card(decoded)["can_retry"]
    else:
        with pytest.raises(preparation.Conflict):
            preparation.get(f.store, owner["id"])


# ---------------------------------------------------------------- registry writer


def test_registry_writer_swaps_only_the_exact_candidate_inside_a_writer(env, monkeypatch):  # noqa: F811
    f = env
    owner = failed_candidate(f, monkeypatch, "remote-failure")
    entry = registry(f, owner)
    candidate, target = entry["candidate"], owner["frozen"]["generation"]["target"]
    fresh = {
        "owner_id": "e" * 32,
        "token": "f" * 32,
        "operation_sha256": candidate["operation_sha256"],
        "compute": candidate["compute"],
    }
    settings = raw_settings(f)
    with f.store.connection() as db, pytest.raises(generations.Conflict, match="transaction"):
        generations.supersede_candidate(db, target, superseded=candidate, **fresh)
    retyped = {**candidate, "expected_revision": False}
    for superseded, replacement in (
        (retyped, fresh),
        ({**candidate, "token": "d" * 32}, fresh),
        (candidate, {**fresh, "token": candidate["token"]}),
        (candidate, {**fresh, "owner_id": candidate["owner_id"]}),
        (candidate, {**fresh, "token": "NOT-HEX"}),
    ):
        with f.store.connection() as db, pytest.raises(generations.Conflict):
            db.execute("BEGIN IMMEDIATE")
            generations.supersede_candidate(db, target, superseded=superseded, **replacement)
    assert raw_settings(f) == settings
    with f.store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        swapped = generations.supersede_candidate(db, target, superseded=candidate, **fresh)
    assert swapped["revision"] == entry["revision"] and swapped["active"] is None
    assert swapped["candidate"] == {**fresh, "expected_revision": entry["revision"]}
    with f.store.connection() as db, pytest.raises(generations.Conflict):
        db.execute("BEGIN IMMEDIATE")  # The old expectation is now stale.
        generations.supersede_candidate(
            db, target, superseded=candidate, **{**fresh, "owner_id": "c" * 32, "token": "b" * 32}
        )
