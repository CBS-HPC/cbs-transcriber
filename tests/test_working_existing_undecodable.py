"""Undecodable stored text in the ADR 0179 native fence is evidence, never an aborted pass.

Invalid UTF-8 or a lone-surrogate JSON escape used to stop the whole startup pass with
`aborted: OperationalError`. Failed-job settings that cannot be read prove nothing, so only
that recording is skipped; unreadable owner links or inventories refuse the remaining pass.
Synthetic temporary libraries only.
"""

import json

import pytest
from test_working_existing_schema19 import (
    assert_only_roles_written,
    job,
    owner,
    raw,
    role,
    settled_batch,
    sole,
    state,
)
from test_working_existing_schema19 import store as store

from cbs_transcriber import working_existing
from cbs_transcriber.store import new_id

INVALID = bytes.fromhex("ff")  # never valid UTF-8
SURROGATE = bytes.fromhex("eda080")  # an encoded lone surrogate, rejected by strict UTF-8


def as_text(db, sql, value, identifier):
    """Store bytes with TEXT affinity, as an old writer or damaged file could."""
    db.execute(sql.format("CAST(? AS TEXT)"), (value, identifier))


@pytest.mark.parametrize("bad", [INVALID, SURROGATE], ids=["invalid", "surrogate"])
def test_undecodable_failed_settings_skip_only_that_recording(store, bad):
    project = store.create_project("Synthetic")
    target = sole(store, project, "target.wav")
    healthy = sole(store, project, "healthy.wav")
    identifier = job(store, target, "failed", "{}")
    with raw(store) as db:
        settings = b'{"settings":{"provider_type":"custom' + bad + b'"}}'
        as_text(db, "UPDATE jobs SET params={} WHERE id=?", settings, identifier)
    before = state(store)

    summary = working_existing.designate_existing(store)

    assert summary["aborted"] is None and summary["refused"] is None, summary
    assert summary["skipped"] == {"native_recovery": 1}, summary
    assert role(store, target) == {"revision": 0, "working": None}
    assert_only_roles_written(before, state(store), [healthy])


def test_lone_surrogate_link_refuses_the_pass_without_writing(store):
    project = store.create_project("Synthetic")
    members = [sole(store, project, f"{n}.wav") for n in "ab"]
    unrelated = sole(store, project, "unrelated.wav")
    identifier, _ = settled_batch(store, project, members)
    with raw(store) as db:
        db.execute(
            "UPDATE jobs SET params=? WHERE json_extract(params,'$.native_batch_id')=?",
            ('{"native_batch_id":"\\ud800"}', identifier),
        )
    before = state(store)

    summary = working_existing.startup(store)

    assert summary["aborted"] is None, summary
    assert summary["refused"] == "native_evidence_unverifiable", summary
    assert summary["designated"] == 0 and state(store) == before
    assert role(store, unrelated) == {"revision": 0, "working": None}


@pytest.mark.parametrize("column", ["id", "members", "state"])
@pytest.mark.parametrize("bad", [INVALID, SURROGATE], ids=["invalid", "surrogate"])
def test_undecodable_owner_text_refuses_the_pass_without_writing(store, column, bad):
    project = store.create_project("Synthetic")
    members = [sole(store, project, f"{n}.wav") for n in "ab"]
    unrelated = sole(store, project, "unrelated.wav")
    identifier, manifest = settled_batch(store, project, members)
    # Each value would be eligible or consistent if it were readable; only decoding fails.
    exact = json.dumps(manifest).encode()
    value = {
        "id": identifier.encode() + bad,
        "members": exact.replace(b'.wav"', b".wav" + bad + b'"', 1),
        "state": b"completed" + bad,
    }[column]
    with raw(store) as db:
        as_text(db, f"UPDATE native_batch_runs SET {column}={{}} WHERE id=?", value, identifier)
    before = state(store)

    summary = working_existing.startup(store)

    assert summary["aborted"] is None, summary
    assert summary["refused"] == "native_evidence_unverifiable", summary
    assert summary["designated"] == 0 and state(store) == before
    assert role(store, unrelated) == {"revision": 0, "working": None}


def test_blob_owner_state_is_never_settled(store):
    """Reading owner text as bytes must not widen a non-text 'completed' into settled."""
    project = store.create_project("Synthetic")
    members = [sole(store, project, f"{n}.wav") for n in "ab"]
    healthy = sole(store, project, "healthy.wav")
    identifier, _ = settled_batch(store, project, members)
    with raw(store) as db:
        db.execute("UPDATE native_batch_runs SET state=? WHERE id=?", (b"completed", identifier))
    before = state(store)

    summary = working_existing.designate_existing(store)

    assert summary["aborted"] is None and summary["refused"] is None, summary
    assert summary["skipped"] == {"native_recovery": 2}, summary
    assert all(role(store, rid) == {"revision": 0, "working": None} for rid in members)
    assert_only_roles_written(before, state(store), [healthy])


def test_decodable_settled_batch_still_designates_its_members(store):
    """Control: the byte-level read keeps the exact settled shape eligible."""
    project = store.create_project("Synthetic")
    members = [sole(store, project, f"{n}.wav") for n in "ab"]
    settled_batch(store, project, members)
    unsettled = [sole(store, project, f"{n}.wav") for n in "cd"]
    owner(
        store,
        project["id"],
        "running",
        [{"job_id": new_id(), "recording_id": rid} for rid in unsettled],
    )
    before = state(store)

    summary = working_existing.designate_existing(store)

    assert summary["aborted"] is None and summary["refused"] is None, summary
    assert summary["skipped"] == {"native_recovery": 2}, summary
    assert_only_roles_written(before, state(store), members)
