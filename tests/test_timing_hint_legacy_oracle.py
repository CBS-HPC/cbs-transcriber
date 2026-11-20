"""Stored v1 history retains the canonical output of the actual prior public writers."""

import copy
import json

import pytest
from test_timing_hint_policy_adversarial import (
    completed,
    derived_result,
    legacy_timing_authority,
    queue_timing,
    study,
)

from cbs_transcriber import research_reports, result_conflicts, result_merges
from cbs_transcriber.history import (
    conflict_review_metadata,
    decode_payload,
    encode_payload,
    merge_metadata,
)
from cbs_transcriber.result_actions import append_edit
from cbs_transcriber.transcript_merge import merge_payload

# Actual public conflict/merge/local-timing writers in the unchanged parent package
# at 67068dc0993c995ff8e6419ccd4bf63fc6ec1b8a produced this exact synthetic payload.
# Its runtime source matches the child base 7cf165c15f7748ed3242e1cad300d2ccf5c53d91.
# The independent two-process oracle stays private; no old code/database is required.
PARENT_V1_PAYLOAD_SHA256 = "ce4abc887482ae162f1fd63b2aa9a39c92b94270897415285d4ea0e87aa22a61"


def captured_history(store):
    history = {}
    for raw in store.all(
        "SELECT v.*,t.recording_id FROM result_versions v "
        "JOIN transcript_results t ON t.id=v.result_id"
    ):
        row = dict(raw)
        row["payload"] = decode_payload(row["payload"], row["payload_sha256"])
        row["context"] = json.loads(row["context"])
        history[row["id"]] = row
    return history


@pytest.mark.parametrize("mode", ["conflict", "merge"])
def test_stored_v1_adoption_matches_actual_parent_writer_oracle(tmp_path, mode):
    store, rid, result_id = study(tmp_path)
    incoming = derived_result(store, rid, result_id)
    current = store.recording(rid)
    choices = [{"passage_id": "0", "group": "timing", "choice": "incoming"}]
    with store.snapshot_lock, store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        lineage = result_merges._plan(
            store, db, rid, result_id, incoming["id"], current["revision"]
        )
        snapshots = lineage["snapshots"]
        arguments = tuple(snapshots[name] for name in ("base", "current", "incoming", "creation"))
        if mode == "conflict":
            output = result_conflicts.review_payload(
                *arguments, choices, policy="cbs-transcript-merge-v1"
            )
            evidence = conflict_review_metadata(
                {
                    "format": "cbs-result-conflict-review-v1",
                    **lineage["references"],
                    "decisions": choices,
                    "summary": output["summary"],
                }
            )
            action, key = "result_conflict_review", "conflict_review"
        else:
            output = merge_payload(*arguments, policy="cbs-transcript-merge-v1")
            evidence = merge_metadata(
                {
                    "format": "cbs-enhancement-merge-v1",
                    **lineage["references"],
                    "summary": {
                        name: output["summary"][name] for name in result_merges.SUMMARY_FIELDS
                    },
                }
            )
            action, key = "result_merge", "merge"
        # Trusted synthetic old-history construction; fresh public writers use v2.
        version = append_edit(
            store,
            db,
            lineage["current"],
            result_id,
            lineage["ours_head"],
            output["segments"],
            output["speakers"],
            action=action,
            context={key: evidence},
            reason=None,
        )
    history = captured_history(store)
    stored = history[version["id"]]
    assert stored["payload_sha256"] == PARENT_V1_PAYLOAD_SHA256
    assert "alignment_hints_stale" not in stored["payload"]["segments"][0]
    assert "timing_adoption_policy" not in stored["context"][key]
    if mode == "conflict":
        result_conflicts.verify_historical(stored, history)
        aliases = {identifier: f"version-{index}" for index, identifier in enumerate(history)}
        assert (
            result_conflicts.public_projection(stored, aliases)["format"]
            == "cbs-result-conflict-review-v1"
        )
        tampered = copy.deepcopy(history)
        changed = tampered[version["id"]]
        changed["payload"]["segments"][0]["alignment_hints_stale"] = True
        changed["payload_sha256"] = encode_payload(
            changed["payload"]["segments"], changed["payload"]["speakers"]
        )[1]
        with pytest.raises(ValueError, match="ancestry"):
            result_conflicts.verify_historical(changed, tampered)
    selected = [{"recording_id": rid, "result_id": result_id, "version_id": version["id"]}]
    checked = research_reports._capture(store, current["project_id"], selected)[1]
    assert checked[version["id"]]["context"][key] == evidence
    # Ordinary merge reports retain parent-only checking; the frozen payload oracle
    # independently covers its v1 transform without claiming report reconstruction.


def test_plain_v1_timing_authority_receipt_and_retry_match_parent_writer_oracle(tmp_path):
    store, rid, result_id = study(tmp_path)
    job = legacy_timing_authority(store, rid, queue_timing(store, rid, result_id, fresh=False))
    job = completed(store, job, partial=True)
    before = store.recording(rid)
    saved = store.apply_local_timing(rid, result_id, job["id"], before["revision"])
    versions = store.result_versions(rid, result_id)
    assert versions[0]["payload_sha256"] == PARENT_V1_PAYLOAD_SHA256
    params = store.job(job["id"])["params"]
    assert "timing_apply_policy" not in params
    assert params["realign_apply_receipt"]["format"] == "cbs-local-timing-apply-v1"
    assert "timing_apply_policy" not in versions[0]["context"]
    role = json.loads(
        store.one("SELECT role_json FROM working_job_authorities WHERE job_id=?", (job["id"],))[
            "role_json"
        ]
    )
    assert "format" not in role
    assert store.apply_local_timing(rid, result_id, job["id"], before["revision"]) == saved
    assert store.result_versions(rid, result_id) == versions
    assert store.job(job["id"])["params"] == params
