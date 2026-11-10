"""V211 adversarial boundaries: preserve evidence, deny forged authority and replay exactly."""

import copy
import io
import json
import sqlite3
from contextlib import closing
from zipfile import ZipFile

import pytest

from cbs_transcriber import (
    research_reports,
    result_conflicts,
    result_merges,
    result_roles,
    review_hints,
    timing_capture,
    timing_working,
)
from cbs_transcriber.history import conflict_review_metadata, merge_metadata
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import merge_passages, normalize_segments, split_passage

POLICY_KEY = "timing_apply_policy"
POLICY = "invalidate-source-alignment-fit-v1"
MARKER = "alignment_hints_stale"


def study(tmp_path, *, marked=False, kind="alignment"):
    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic timing-policy boundaries")
    recording = store.create_recording(project["id"], "synthetic.wav")
    segments = normalize_segments(
        [
            {
                "text": "Hej igen",
                "start": index,
                "end": index + 1,
                "speaker": "VOICE",
                "words": [
                    {"word": "Hej", "start": index + 0.1, "end": index + 0.3},
                    {"word": "igen", "start": index + 0.5, "end": index + 0.9},
                ],
            }
            for index in range(3)
        ]
    )
    for index, passage in enumerate(segments):
        passage["opaque"] = {"literal": ["æ\u0301 🧪", None, False, index]}
        passage["_hints"] = {
            "word_kind": kind,
            "words": [[0, 3, 0.11], [4, 8, 0.22]],
            "no_speech_prob": 0.07,
        }
    if marked:
        segments[0]["words"][0]["manual_timing"] = True
    result = store.add_result(
        recording["id"], segments, {"VOICE": "Reviewed voice"}, "Source", {}, auto_first=True
    )
    return store, recording["id"], result["id"]


def queue_timing(store, rid, result_id, *, fresh=True, targets=("0",), capture=False):
    current = store.recording(rid)
    params = {"realign": True, "segment_id": targets[0], "enhancement": {"execution": "local"}}
    if capture:
        params["enhancement"].update(
            align=True, diarize=False, transcribe=False, retranscribe=False
        )
    if len(targets) > 1:
        params.pop("segment_id")
        params["segment_ids"] = list(targets)
    if fresh:
        return store.enqueue_enhancement(rid, result_id, current["revision"], params)
    params["source_snapshot"] = store.freeze_result(rid, result_id, current["revision"])
    if capture:
        params = timing_capture.freeze(params)
    return store.add_job(rid, "enhance", params)


def completed(store, job, *, partial=False, targets=("0",)):
    params = copy.deepcopy(job["params"])
    passages = {}
    for identifier in targets:
        offset = int(identifier)
        words = [
            {
                "word": "Hej",
                "start": offset + 0.2,
                "end": offset + 0.4,
                "char_start": 0,
                "char_end": 3,
            },
            {
                "word": "igen",
                "start": offset + 0.6,
                "end": offset + 0.95,
                "char_start": 4,
                "char_end": 8,
            },
        ]
        passages[identifier] = {
            "words": words[:1] if partial else words,
            "word_timing": not partial,
        }
    params["realign_output"] = {"passages": passages}
    if "timing_artifact_policy" in params:
        store.update_job(job["id"], state="running")
        timing_capture.publish_output(
            store, store.job(job["id"]), params["realign_output"], "Synthetic unreceipted output"
        )
        store.update_job(job["id"], state="completed")
    else:
        store.update_job(job["id"], params=params, state="completed")
    return store.job(job["id"])


def legacy_timing_authority(store, rid, job):
    """Build the exact plain-role authority stored before the application policy existed."""
    params = copy.deepcopy(job["params"])
    role = store.recording(rid)["result_roles"]
    params["timing_working_policy"] = {
        "format": "cbs-timing-working-v1",
        "expected_role": role,
    }
    source = params["source_snapshot"]
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job["id"]))
        db.execute(
            "INSERT INTO working_job_authorities "
            "(job_id,recording_id,policy,role_json,source_version_id,source_payload_sha256,"
            "source_snapshot_sha256,admission_sha256) VALUES (?,?,?,?,?,?,?,?)",
            (
                job["id"],
                rid,
                "timing",
                json.dumps(role, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                source["source_version_id"],
                source["source_payload_sha256"],
                result_roles.source_snapshot_hash(source),
                None,
            ),
        )
        authority = result_roles.job_authority(db, job["id"], rid, "timing")
        timing_working.validate_authorized_params(authority, "timing", params)
    return store.job(job["id"])


@pytest.mark.parametrize("marked,partial", [(False, False), (True, True)])
def test_fresh_apply_suppresses_every_creation_alignment_span(tmp_path, marked, partial):
    store, rid, result_id = study(tmp_path, marked=marked)
    creation_hints = store.result_hints(rid, result_id)
    before = store.recording(rid)
    job = completed(store, queue_timing(store, rid, result_id), partial=partial)
    saved = store.apply_local_timing(rid, result_id, job["id"], before["revision"])
    assert review_hints.current_hints(store, rid, result_id)["passages"]["0"]["words"] == []
    assert saved["segments"][0][MARKER] is True
    assert saved["segments"][1:] == before["segments"][1:]
    assert store.result_hints(rid, result_id) == creation_hints
    assert (
        review_hints.current_hints(store, rid, result_id)["passages"]["0"]["no_speech_prob"] == 0.07
    )


@pytest.mark.parametrize("marked,partial", [(False, False), (True, True)])
def test_absent_policy_replays_legacy_hint_behavior_without_upgrading(tmp_path, marked, partial):
    store, rid, result_id = study(tmp_path, marked=marked)
    before = store.recording(rid)
    job = completed(store, queue_timing(store, rid, result_id, fresh=False), partial=partial)
    assert POLICY_KEY not in job["params"]
    saved = store.apply_local_timing(rid, result_id, job["id"], before["revision"])
    expected = [[4, 8, 0.22]] if partial else [[0, 3, 0.11], [4, 8, 0.22]]
    assert review_hints.current_hints(store, rid, result_id)["passages"]["0"]["words"] == expected
    assert MARKER not in saved["segments"][0]
    expected_segments = copy.deepcopy(before["segments"])
    expected_segments[0].update(
        copy.deepcopy(job["params"]["realign_output"]["passages"]["0"]), words_current=True
    )
    if marked:
        for word in expected_segments[0]["words"]:
            word[MARKER] = True
    assert saved["segments"] == expected_segments
    assert (
        store.job(job["id"])["params"]["realign_apply_receipt"]["format"]
        == "cbs-local-timing-apply-v1"
    )


@pytest.mark.parametrize("value", [None, False, POLICY, "unknown-policy", {}, []])
def test_fresh_and_generic_creation_refuse_caller_policy_claims(tmp_path, value):
    store, rid, result_id = study(tmp_path)
    current = store.recording(rid)
    params = {
        "realign": True,
        "segment_id": "0",
        "enhancement": {"execution": "local"},
        POLICY_KEY: value,
    }
    for invoke in (
        lambda: store.enqueue_enhancement(rid, result_id, current["revision"], params),
        lambda: store.add_job(rid, "enhance", params),
        lambda: store.add_job(
            rid,
            "enhance",
            params | {"source_snapshot": store.freeze_result(rid, result_id, current["revision"])},
            capture_timing_role=True,
        ),
    ):
        before = store.all("SELECT id,params FROM jobs ORDER BY id")
        with pytest.raises(ValueError):
            invoke()
        assert store.all("SELECT id,params FROM jobs ORDER BY id") == before


@pytest.mark.parametrize("value", [None, False, "unknown-policy", {}, []])
def test_present_malformed_policy_on_legacy_job_refuses_before_any_write(tmp_path, value):
    store, rid, result_id = study(tmp_path)
    job = completed(store, queue_timing(store, rid, result_id, fresh=False))
    params = copy.deepcopy(job["params"])
    params[POLICY_KEY] = value
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job["id"]))
    before = store.recording(rid)
    versions = store.result_versions(rid, result_id)
    with pytest.raises(ValueError):
        store.apply_local_timing(rid, result_id, job["id"], before["revision"])
    assert store.recording(rid) == before
    assert store.result_versions(rid, result_id) == versions


@pytest.mark.parametrize("old_authority", [False, True], ids=["no-authority", "old-v1-authority"])
def test_known_policy_without_host_birth_seal_cannot_upgrade_a_legacy_job(tmp_path, old_authority):
    store, rid, result_id = study(tmp_path)
    job = completed(store, queue_timing(store, rid, result_id, fresh=False), partial=True)
    assert not store.all("SELECT * FROM working_job_authorities WHERE job_id=?", (job["id"],))
    if old_authority:
        job = legacy_timing_authority(store, rid, job)
    params = copy.deepcopy(job["params"])
    params[POLICY_KEY] = POLICY
    # Model a pre-policy frozen row carrying arbitrary producer parameters, rather
    # than granting a genuine fresh acceptance through today's guarded insertion.
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job["id"]))
    before = store.recording(rid)
    versions = store.result_versions(rid, result_id)
    with pytest.raises(ValueError):
        store.apply_local_timing(rid, result_id, job["id"], before["revision"])
    assert store.recording(rid) == before
    assert store.result_versions(rid, result_id) == versions


def test_birth_sealed_policy_cannot_downgrade_to_legacy_when_key_is_missing(tmp_path):
    store, rid, result_id = study(tmp_path)
    job = completed(store, queue_timing(store, rid, result_id), partial=True)
    params = copy.deepcopy(job["params"])
    assert params.pop(POLICY_KEY) == POLICY
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job["id"]))
    before = store.recording(rid)
    versions = store.result_versions(rid, result_id)
    hints = review_hints.current_hints(store, rid, result_id)
    with pytest.raises(ValueError):
        store.apply_local_timing(rid, result_id, job["id"], before["revision"])
    assert store.recording(rid) == before
    assert store.result_versions(rid, result_id) == versions
    assert review_hints.current_hints(store, rid, result_id) == hints


@pytest.mark.parametrize("fresh", [False, True])
@pytest.mark.parametrize("change", ["remove", None, False, "unknown-policy", POLICY])
def test_generic_mutation_cannot_change_policy_presence_or_value_before_receipt(
    tmp_path, fresh, change
):
    store, rid, result_id = study(tmp_path)
    job = queue_timing(store, rid, result_id, fresh=fresh)
    assert (POLICY_KEY in job["params"]) is fresh
    params = copy.deepcopy(job["params"])
    if change == "remove":
        params.pop(POLICY_KEY, None)
    else:
        params[POLICY_KEY] = change
    if params == job["params"]:
        store.update_job(job["id"], params=params, message="Unchanged policy checkpoint")
        assert store.job(job["id"])["params"] == job["params"]
        return
    with pytest.raises(ValueError):
        store.update_job(job["id"], params=params)
    assert store.job(job["id"]) == job


def test_partial_apply_preserves_skipped_and_unselected_full_dictionaries(tmp_path):
    store, rid, result_id = study(tmp_path)
    job = completed(
        store,
        queue_timing(store, rid, result_id, targets=("0", "1")),
        partial=True,
        targets=("0", "1"),
    )
    current = store.recording(rid)
    edited = copy.deepcopy(current["segments"])
    edited[1]["words"][0]["start"] = 1.15
    edited[1]["opaque"]["newer_review"] = ["kept", None]
    store.save_transcript(
        rid, edited, current["speakers"], current["revision"], result_id=result_id
    )
    before = store.recording(rid)
    hints = store.result_hints(rid, result_id)
    saved = store.apply_local_timing(rid, result_id, job["id"], before["revision"])
    assert saved["segments"][1:] == before["segments"][1:]
    assert MARKER not in saved["segments"][1] and MARKER not in saved["segments"][2]
    assert saved["segments"][0]["opaque"] == before["segments"][0]["opaque"]
    assert saved["segments"][0]["original_words"] == before["segments"][0]["original_words"]
    receipt = store.job(job["id"])["params"]["realign_apply_receipt"]
    assert receipt["applied_ids"] == ["0"] and receipt["skipped_ids"] == ["1"]
    assert (
        review_hints.current_hints(store, rid, result_id)["passages"]["1"]["words"]
        == hints["passages"]["1"]["words"]
    )
    assert store.result_hints(rid, result_id) == hints


def test_policy_is_host_frozen_without_mutating_the_callers_request(tmp_path):
    store, rid, result_id = study(tmp_path)
    current = store.recording(rid)
    params = {"realign": True, "segment_id": "0", "enhancement": {"execution": "local"}}
    original = copy.deepcopy(params)
    job = store.enqueue_enhancement(rid, result_id, current["revision"], params)
    assert params == original
    assert job["params"][POLICY_KEY] == POLICY
    params["enhancement"]["execution"] = "ucloud"
    assert store.job(job["id"])["params"]["enhancement"]["execution"] == "local"


def test_explicit_timing_capture_mints_policy_only_after_exact_source_verification(tmp_path):
    store, rid, result_id = study(tmp_path)
    current = store.recording(rid)
    params = {
        "realign": True,
        "segment_id": "0",
        "enhancement": {"execution": "local"},
        "source_snapshot": store.freeze_result(rid, result_id, current["revision"]),
    }
    caller = copy.deepcopy(params)
    accepted = store.add_job(rid, "enhance", params, capture_timing_role=True)
    assert accepted["params"][POLICY_KEY] == POLICY
    assert params == caller
    stale = copy.deepcopy(caller)
    stale["source_snapshot"]["source_payload_sha256"] = "0" * 64
    jobs = store.all("SELECT id,params FROM jobs ORDER BY id")
    with pytest.raises(ValueError):
        store.add_job(rid, "enhance", stale, capture_timing_role=True)
    assert store.all("SELECT id,params FROM jobs ORDER BY id") == jobs
    assert POLICY_KEY not in stale


def test_snapshot_failure_then_reopen_retries_one_v2_successor_and_working_event(
    tmp_path, monkeypatch
):
    store, rid, result_id = study(tmp_path)
    job = completed(store, queue_timing(store, rid, result_id), partial=True)
    before = store.recording(rid)

    def lost_reply(*_):
        raise OSError("Synthetic post-commit snapshot failure")

    monkeypatch.setattr(store, "snapshot_project", lost_reply)
    with pytest.raises(OSError, match="post-commit"):
        store.apply_local_timing(rid, result_id, job["id"], before["revision"])
    committed = store.recording(rid)
    versions = store.result_versions(rid, result_id)
    params = store.job(job["id"])["params"]
    assert params["realign_apply_receipt"]["format"] == "cbs-local-timing-apply-v2"
    assert committed["result_roles"]["working"]["version_id"] == versions[0]["id"]
    reopened = Store(store.root)
    again = reopened.apply_local_timing(rid, result_id, job["id"], before["revision"])
    assert again == committed
    assert reopened.result_versions(rid, result_id) == versions
    assert reopened.job(job["id"])["params"] == params


def test_v1_receipt_stays_exact_after_a_v2_successor_and_old_retry(tmp_path):
    store, rid, result_id = study(tmp_path)
    before = store.recording(rid)
    legacy = completed(store, queue_timing(store, rid, result_id, fresh=False))
    old = store.apply_local_timing(rid, result_id, legacy["id"], before["revision"])
    legacy_params = copy.deepcopy(store.job(legacy["id"])["params"])
    legacy_version = copy.deepcopy(store.result_versions(rid, result_id)[0])
    fresh = completed(store, queue_timing(store, rid, result_id), partial=True)
    saved = store.apply_local_timing(rid, result_id, fresh["id"], old["revision"])
    versions = store.result_versions(rid, result_id)
    again = store.apply_local_timing(rid, result_id, legacy["id"], before["revision"])
    assert again == saved
    assert store.result_versions(rid, result_id) == versions
    assert versions[1] == legacy_version
    assert store.job(legacy["id"])["params"] == legacy_params
    assert legacy_params["realign_apply_receipt"]["format"] == "cbs-local-timing-apply-v1"
    assert (
        store.job(fresh["id"])["params"]["realign_apply_receipt"]["format"]
        == "cbs-local-timing-apply-v2"
    )


@pytest.mark.parametrize("kind", ["alignment", "transcription"])
def test_only_alignment_words_are_hidden_and_hint_documents_stay_immutable(tmp_path, kind):
    store, rid, result_id = study(tmp_path, kind=kind)
    document = copy.deepcopy(store.result_hints(rid, result_id))
    job = completed(store, queue_timing(store, rid, result_id), partial=True)
    store.apply_local_timing(rid, result_id, job["id"], store.recording(rid)["revision"])
    projected = review_hints.current_hints(store, rid, result_id)["passages"]["0"]
    assert projected["words"] == ([] if kind == "alignment" else document["passages"]["0"]["words"])
    assert projected["no_speech_prob"] == document["passages"]["0"]["no_speech_prob"]
    assert store.result_hints(rid, result_id) == document


def derived_result(store, rid, result_id, *, marker_only=False):
    current = store.recording(rid)
    frozen = store.freeze_result(rid, result_id, current["revision"])
    job = store.add_job(rid, "enhance", {"source_snapshot": frozen})
    segments = copy.deepcopy(frozen["segments"])
    if marker_only:
        segments[0][MARKER] = True
        segments[0]["words"][0][MARKER] = True
    else:
        segments[0].update(
            words=[{"word": "Hej", "start": 0.2, "end": 0.4, "char_start": 0, "char_end": 3}],
            word_timing=False,
            words_current=True,
        )
    return store.add_result(
        rid,
        segments,
        frozen["speakers"],
        "Incoming partial timing without a marker",
        {"kind": "enhancement", "source_result_id": result_id},
        job_id=job["id"],
    )


@pytest.mark.parametrize("mode", ["safe", "conflict"])
def test_fresh_timing_adoption_invalidates_even_an_unmarked_partial_incoming(tmp_path, mode):
    store, rid, result_id = study(tmp_path)
    hints = store.result_hints(rid, result_id)
    incoming = derived_result(store, rid, result_id)
    before = store.recording(rid)
    if mode == "safe":
        preview = result_merges.preview(store, rid, result_id, incoming["id"], before["revision"])
        saved = result_merges.apply(
            store, rid, result_id, incoming["id"], before["revision"], preview["fingerprint"]
        )["recording"]
    else:
        choices = [{"passage_id": "0", "group": "timing", "choice": "incoming"}]
        preview = result_conflicts.preview(
            store, rid, result_id, incoming["id"], before["revision"], choices
        )
        saved = result_conflicts.apply(
            store,
            rid,
            result_id,
            incoming["id"],
            before["revision"],
            choices,
            preview["fingerprint"],
        )["recording"]
    assert saved["segments"][0][MARKER] is True
    assert saved["segments"][1:] == before["segments"][1:]
    assert saved["speakers"] == before["speakers"]
    assert review_hints.current_hints(store, rid, result_id)["passages"]["0"]["words"] == []
    assert store.result_hints(rid, result_id) == hints
    head = store.result_versions(rid, result_id)[0]
    key = "merge" if mode == "safe" else "conflict_review"
    expected = "cbs-enhancement-merge-v2" if mode == "safe" else "cbs-result-conflict-review-v2"
    assert head["context"][key]["format"] == expected


@pytest.mark.parametrize("generation", ["v1", "v2"])
def test_conflict_public_label_comes_from_stored_generation(generation):
    label = f"cbs-result-conflict-review-{generation}"
    row = {
        "conflict_review_evidence": {
            "format": label,
            "base": {"version_id": "base"},
            "incoming": {"version_id": "incoming"},
            "creation": {"version_id": "creation"},
            "decisions": [],
            "summary": {},
        },
        "conflict_review_ordinals": {},
    }
    assert (
        result_conflicts.public_projection(
            row, {key: key for key in ("base", "incoming", "creation")}
        )["format"]
        == label
    )


def test_report_keeps_cloud_and_ordinary_merge_transformation_scopes_unchecked(tmp_path):
    store, rid, result_id = study(tmp_path)
    project_id = store.recording(rid)["project_id"]
    job = completed(store, queue_timing(store, rid, result_id), partial=True)
    saved = store.apply_local_timing(rid, result_id, job["id"], store.recording(rid)["revision"])
    version = store.result_versions(rid, result_id)[0]
    assert saved["segments"][0][MARKER] is True
    assert "timing_artifacts" not in version["context"]
    selection = [{"recording_id": rid, "result_id": result_id, "version_id": version["id"]}]
    with ZipFile(
        io.BytesIO(research_reports.project_report(store, project_id, selection))
    ) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert {"cloud_timing_transformations", "ordinary_merge_transformations"} <= set(
        manifest["not_checked"]
    )


@pytest.mark.parametrize("skipped", [False, True])
@pytest.mark.parametrize("claimed", [None, False, True])
def test_producer_cannot_assert_host_marker_even_for_a_skipped_passage(tmp_path, skipped, claimed):
    store, rid, result_id = study(tmp_path)
    job = completed(store, queue_timing(store, rid, result_id), partial=True)
    params = copy.deepcopy(job["params"])
    params["realign_output"]["passages"]["0"][MARKER] = claimed
    store.update_job(job["id"], params=params)
    if skipped:
        current = store.recording(rid)
        segments = copy.deepcopy(current["segments"])
        segments[0]["words"][0]["start"] = 0.15
        store.save_transcript(rid, segments, current["speakers"], current["revision"])
    before = store.recording(rid)
    versions = store.result_versions(rid, result_id)
    with pytest.raises(ValueError):
        store.apply_local_timing(rid, result_id, job["id"], before["revision"])
    assert store.recording(rid) == before
    assert store.result_versions(rid, result_id) == versions


@pytest.mark.parametrize("mode", ["safe", "conflict"])
def test_marker_only_adoption_cannot_inflate_timing_update_counts(tmp_path, mode):
    store, rid, result_id = study(tmp_path)
    incoming = derived_result(store, rid, result_id, marker_only=True)
    current = store.recording(rid)
    module = result_merges if mode == "safe" else result_conflicts
    preview = module.preview(store, rid, result_id, incoming["id"], current["revision"])
    assert preview["summary"]["timing_updates"] == 0
    assert store.recording(rid) == current
    assert MARKER not in current["segments"][0]
    versions = store.result_versions(rid, result_id)
    with pytest.raises(ValueError):
        if mode == "safe":
            module.apply(
                store, rid, result_id, incoming["id"], current["revision"], preview["fingerprint"]
            )
        else:
            module.apply(
                store,
                rid,
                result_id,
                incoming["id"],
                current["revision"],
                [],
                preview["fingerprint"],
            )
    assert store.result_versions(rid, result_id) == versions
    assert store.recording(rid) == current


def test_structural_join_keeps_invalidation_from_the_right_passage(tmp_path):
    store, rid, result_id = study(tmp_path)
    job = completed(store, queue_timing(store, rid, result_id, targets=("1",)), targets=("1",))
    saved = store.apply_local_timing(rid, result_id, job["id"], store.recording(rid)["revision"])
    assert MARKER not in saved["segments"][0]
    assert saved["segments"][1][MARKER] is True
    joined = merge_passages(saved["segments"], "0", "down")
    assert joined[0][MARKER] is True
    split = split_passage(joined, "0", len(saved["segments"][0]["text"]))
    assert split[0][MARKER] is True and split[1][MARKER] is True
    assert split[2] == saved["segments"][2]


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "remove-policy",
        "plain-authority",
        "oversized-authority",
        "context-policy",
        "context-null",
        "legacy",
    ],
)
def test_contained_timing_generation_binding_and_exact_legacy_authority(tmp_path, damage):
    from test_portable_semantics import inspect, manifest

    store, rid, result_id = study(tmp_path)
    fresh = damage != "legacy"
    job = queue_timing(store, rid, result_id, capture=True, fresh=fresh)
    if not fresh:
        job = legacy_timing_authority(store, rid, job)
    job = completed(store, job, partial=True)
    saved = store.apply_local_timing(rid, result_id, job["id"], store.recording(rid)["revision"])
    assert (MARKER in saved["segments"][0]) is fresh
    head = store.result_versions(rid, result_id)[0]
    context = head["context"]
    expected_format = "cbs-local-timing-capture-v2" if fresh else "cbs-local-timing-capture-v1"
    assert context["timing_artifacts"]["format"] == expected_format
    assert context["timing_artifacts"]["units"] == {"00000": "not_captured"}
    selected = [{"recording_id": rid, "result_id": result_id, "version_id": head["id"]}]
    with ZipFile(
        io.BytesIO(
            research_reports.project_report(Store(store.root), saved["project_id"], selected)
        )
    ) as archive:
        reported = json.loads(archive.read("manifest.json"))
    assert any(
        item.get("timing_artifacts", {}).get("format") == expected_format
        for item in reported["versions"]
    )
    path = tmp_path / "quiescent.sqlite3"
    with store.connection() as source, closing(sqlite3.connect(path)) as destination:
        source.backup(destination)
        destination.execute("PRAGMA journal_mode=DELETE")
    if damage == "remove-policy":
        params = store.job(job["id"])["params"]
        params.pop(POLICY_KEY)
        with closing(sqlite3.connect(path)) as db:
            db.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job["id"]))
            db.commit()
    elif damage in {"plain-authority", "oversized-authority"}:
        with closing(sqlite3.connect(path)) as db:
            row = db.execute(
                "SELECT role_json FROM working_job_authorities WHERE job_id=?", (job["id"],)
            ).fetchone()
            envelope = json.loads(row[0])
            assert envelope["format"] == "cbs-timing-job-authority-v2"
            replacement = (
                envelope["role"]
                if damage == "plain-authority"
                else envelope | {"padding": "x" * 4096}
            )
            db.execute(
                "UPDATE working_job_authorities SET role_json=? WHERE job_id=?",
                (
                    json.dumps(
                        replacement, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ),
                    job["id"],
                ),
            )
            db.commit()
    elif damage in {"context-policy", "context-null"}:
        changed = copy.deepcopy(context)
        assert changed.pop(POLICY_KEY) == POLICY
        if damage == "context-null":
            changed[POLICY_KEY] = None
        with closing(sqlite3.connect(path)) as db:
            db.execute(
                "UPDATE result_versions SET context=? WHERE id=?", (json.dumps(changed), head["id"])
            )
            db.commit()
    value = manifest(path)
    value["files"][0].update(project_id=saved["project_id"], recording_id=rid)
    if damage == "oversized-authority":
        with pytest.raises(ValueError, match="resource budget"):
            inspect(path, value)
    elif damage in {"remove-policy", "plain-authority", "context-policy", "context-null"}:
        with pytest.raises(ValueError, match=r"inspection \(semantics\)"):
            inspect(path, value)
    else:
        checked = inspect(path, value)
        assert checked["semantic_counts"]["result_versions"] == 2
        assert "domain_specific_edit_and_inference_transformations" in checked["not_checked"]


@pytest.mark.parametrize("value", [[], {}])
@pytest.mark.parametrize("validator", [merge_metadata, conflict_review_metadata])
def test_malformed_stored_generation_refuses_with_value_error(validator, value):
    with pytest.raises(ValueError):
        validator({"format": value})
