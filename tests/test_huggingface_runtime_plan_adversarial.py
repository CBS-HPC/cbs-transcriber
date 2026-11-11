"""HF preparation admission against synthetic storage, without external work."""

import copy
import hashlib
import threading
from contextlib import contextmanager

import pytest
from admission_support import checked_connection
from test_runtime_preparation import enqueue, execute, prepared

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import huggingface_tasks, providers, runtime_store
from cbs_transcriber import runtime_preparation as subject
from cbs_transcriber.enhancement import HF_SCOPE


@pytest.fixture
def configured(client, credentials, monkeypatch):
    fixture = prepared.__wrapped__(client, credentials, monkeypatch)
    saved = providers.profile(fixture.store, fixture.profile["id"])
    runtime = providers.save_profile(
        fixture.store,
        credentials,
        "Synthetic configured Runtime",
        providers.Settings.model_validate(saved["settings"]).model_copy(
            update={"model": "whisperx/large-v3", "enabled": True}
        ),
        remember=False,
    )
    checked_connection(fixture.store, credentials, runtime["id"])
    credentials.set(HF_SCOPE, "hf_SYNTHETIC_PARENT_ACCEPTANCE", False)
    body = b"synthetic pinned speaker file"
    monkeypatch.setattr(
        api, "SPEAKER_FILES", {"config.yaml": (len(body), hashlib.sha256(body).hexdigest())}
    )
    folder = api.snapshot_folder("speakers", api._local_hub_cache())
    folder.mkdir(parents=True)
    (folder / "config.yaml").write_bytes(body)
    fixture.plan = subject.huggingface_plan(fixture.store, credentials)
    assert fixture.plan["status"] == "ready", fixture.plan
    return fixture


def request(fixture, *, plan=None, guard=lambda: None):
    return subject.request_huggingface(
        fixture.store, fixture.credentials, fixture.plan if plan is None else plan, guard
    )


def assert_no_remote_writes(fixture):
    assert not fixture.cloud.submissions
    assert not fixture.cloud.uploads
    assert not fixture.cloud.terminations
    assert not fixture.cloud.trashed


@pytest.mark.parametrize("change", ["missing", "null", "unknown-policy", "changed-pin"])
def test_a_changed_plan_cannot_accept_a_durable_owner(configured, change):
    fixture = configured
    plan = copy.deepcopy(fixture.plan)
    if change == "missing":
        del plan["model_plan"]
    elif change == "null":
        plan["model_plan"] = None
    elif change == "unknown-policy":
        plan["model_plan"]["policy"] = "future-model-plan-v99"
    else:
        name = plan["model_plan"]["models"][0]
        plan["model_plan"]["model_revisions"][name][1] = "f" * 40
        # A self-consistent caller hash still cannot replace the saved configured plan.
        plan["connection_fingerprint"] = subject.fingerprint(
            plan["chosen"], model_plan=plan["model_plan"]
        )
    before = fixture.store.all("SELECT * FROM runtime_preparations")
    with pytest.raises(subject.Conflict):
        request(fixture, plan=plan)
    assert fixture.store.all("SELECT * FROM runtime_preparations") == before
    assert_no_remote_writes(fixture)


@pytest.mark.parametrize("change", ["null", "unknown-policy"])
def test_malformed_stored_plan_refuses_even_with_consistent_outer_hash(configured, change):
    fixture = configured
    accepted = request(fixture)
    identifier = accepted["operation"]["runtime_preparation_id"]
    frozen = subject.get(fixture.store, identifier)["frozen"]
    if change == "null":
        frozen["model_plan"] = None
    else:
        frozen["model_plan"]["policy"] = "future-model-plan-v99"
    raw = runtime_store.canonical(frozen)
    fixture.store.execute(
        "UPDATE runtime_preparations SET frozen=?,connection_fingerprint=? WHERE id=?",
        (
            raw,
            runtime_store._digest(
                {key: value for key, value in frozen.items() if key != "retry_of"}
            ),
            identifier,
        ),
    )
    with pytest.raises(subject.Conflict):
        subject.get(fixture.store, identifier)
    assert (
        fixture.store.one("SELECT frozen FROM runtime_preparations WHERE id=?", (identifier,))[
            "frozen"
        ]
        == raw
    )
    assert_no_remote_writes(fixture)


@pytest.mark.parametrize("state", ["queued", "uncertain"])
def test_legacy_owner_cannot_be_attached_as_sufficient_or_replaced(configured, state):
    fixture = configured
    legacy = enqueue(fixture)
    assert "model_plan" not in legacy["frozen"]
    assert legacy["connection_fingerprint"] == fixture.fingerprint
    if state == "uncertain":
        subject.checkpoint(
            fixture.store,
            legacy["id"],
            legacy["epoch"],
            state="failed",
            journal={"submission_intent": True},
        )
    before = fixture.store.all("SELECT * FROM runtime_preparations")
    with pytest.raises(subject.Conflict):
        request(fixture)
    assert fixture.store.all("SELECT * FROM runtime_preparations") == before
    assert_no_remote_writes(fixture)


def test_matching_uncertain_owner_keeps_its_exact_link_without_resubmission(configured):
    fixture = configured
    accepted = request(fixture)
    identifier = accepted["operation"]["runtime_preparation_id"]
    value = subject.get(fixture.store, identifier)
    subject.checkpoint(
        fixture.store,
        identifier,
        value["epoch"],
        state="failed",
        journal={"submission_intent": True},
    )
    before = subject.get(fixture.store, identifier)
    repeated = request(fixture)
    assert repeated["deduplicated"] and not repeated["prepared"]
    assert repeated["operation"]["runtime_preparation_id"] == identifier
    after = subject.get(fixture.store, identifier)
    assert (after["frozen"], after["journal"], after["epoch"], after["state"]) == (
        before["frozen"],
        before["journal"],
        before["epoch"],
        "failed",
    )
    assert len(fixture.store.all("SELECT id FROM runtime_preparations")) == 1
    assert_no_remote_writes(fixture)


def test_stop_after_sqlite_acceptance_cannot_hide_the_accepted_child(configured, monkeypatch):
    fixture = configured
    inserted, stopped = threading.Event(), threading.Event()
    insert, connection = subject._insert, fixture.store.connection

    def insert_then_stop(*args, **kwargs):
        value = insert(*args, **kwargs)
        inserted.set()
        return value

    @contextmanager
    def connection_then_stop():
        with connection() as database:
            yield database
        # The real connection context has committed before the parent's Stop wins.
        if inserted.is_set():
            stopped.set()

    def guard():
        if stopped.is_set():
            raise huggingface_tasks.Stopped()

    monkeypatch.setattr(subject, "_insert", insert_then_stop)
    monkeypatch.setattr(fixture.store, "connection", connection_then_stop)
    accepted = request(fixture, guard=guard)
    assert inserted.is_set() and stopped.is_set()
    assert not accepted["deduplicated"] and not accepted["prepared"]
    identifier = accepted["operation"]["runtime_preparation_id"]
    assert subject.get(fixture.store, identifier)["state"] == "queued"
    # A retry after a lost parent reply still finds that same accepted durable child.
    repeated = request(fixture)
    assert repeated["deduplicated"]
    assert repeated["operation"]["runtime_preparation_id"] == identifier
    assert len(fixture.store.all("SELECT id FROM runtime_preparations")) == 1
    assert_no_remote_writes(fixture)


def test_archived_completed_receipt_cannot_be_current_readiness(configured, monkeypatch):
    fixture = configured
    observed = {
        "snapshots": {
            name: [{"name": "model.bin", "kind": "FILE", "bytes": 1}]
            for name in fixture.plan["model_plan"]["models"]
            if name != "speakers"
        },
        "speakers": [
            {
                "name": "config.yaml",
                "kind": "FILE",
                "bytes": fixture.plan["model_plan"]["speaker_files"]["config.yaml"][0],
            }
        ],
        "speaker_marker": True,
        "punkt": {
            language: [
                {"name": name, "kind": "FILE", "bytes": 1}
                for name in (
                    "abbrev_types.txt",
                    "collocations.tab",
                    "ortho_context.tab",
                    "sent_starters.txt",
                )
            ]
            for language in ("danish", "english")
        },
        "conversions": {},
    }
    monkeypatch.setattr(subject, "_model_evidence", lambda *_: copy.deepcopy(observed))
    monkeypatch.setattr(api, "seed_speakers", lambda *args, **kwargs: None)
    read_json = api.read_json

    def prepared_json(client, server, path, *args):
        if path.endswith("/cache.json"):
            return {
                "models": dict.fromkeys(fixture.plan["model_plan"]["models"], True),
                "punkt_tab": True,
            }
        return read_json(client, server, path, *args)

    monkeypatch.setattr(api, "read_json", prepared_json)
    submit = fixture.cloud.submit_job

    def submit_and_prepare(specification):
        reply = submit(specification)
        fixture.cloud.ready = True
        return reply

    monkeypatch.setattr(fixture.cloud, "submit_job", submit_and_prepare)
    accepted = request(fixture)
    completed, _ = execute(fixture)
    identifier = accepted["operation"]["runtime_preparation_id"]
    assert completed["id"] == identifier and completed["state"] == "completed"
    assert completed["journal"]["model_plan_receipt"]["evidence"] == observed
    # Positive control: this exact live receipt and fresh cache observation are sufficient.
    ready = request(fixture)
    assert ready["prepared"] and ready["operation"] is None
    fixture.store.execute("UPDATE runtime_preparations SET archived=1 WHERE id=?", (identifier,))
    archived = fixture.store.one("SELECT * FROM runtime_preparations WHERE id=?", (identifier,))
    replacement = request(fixture)
    assert not replacement["prepared"] and not replacement["deduplicated"]
    successor = replacement["operation"]["runtime_preparation_id"]
    assert successor != identifier and replacement["operation"]["state"] == "queued"
    assert (
        fixture.store.one("SELECT * FROM runtime_preparations WHERE id=?", (identifier,))
        == archived
    )
    assert len(fixture.store.all("SELECT id FROM runtime_preparations")) == 2
    assert len(fixture.cloud.submissions) == 1  # Admission has not allocated the new owner.
    assert subject.claim(fixture.store)["id"] == successor
