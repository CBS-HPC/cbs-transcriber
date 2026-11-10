"""Same-result repair application is revision guarded and commits its job receipt once."""

import copy

import pytest
import test_repair_bundles as bundle_fixtures
import test_repair_cloud as cloud_fixtures

from cbs_transcriber import repair_cloud, repair_remote


@pytest.fixture
def case(tmp_path, monkeypatch):
    study = bundle_fixtures.study.__wrapped__(tmp_path)
    value = cloud_fixtures.execution.__wrapped__(study, tmp_path, monkeypatch)
    repair_remote.process(value.remote, value.job["id"], runner=cloud_fixtures.fake_alignment)
    cloud_fixtures.terminal(value)
    value.controller.harvest()
    value.controller.publish()
    value.store.update_job(value.job["id"], state="failed")
    return value


def apply(case, *, revision=None, result_id=None):
    current = case.store.recording(case.recording["id"])
    proof = repair_cloud.validate_local_outputs(case.store, case.store.job(case.job["id"]))
    return case.store.apply_repair_timing(
        case.recording["id"],
        result_id or current["active_transcript_id"],
        case.job["id"],
        current["revision"] if revision is None else revision,
        proof["fingerprint"],
    )


def test_apply_keeps_unselected_dictionaries_and_commits_history_and_receipt_once(case):
    before = case.store.recording(case.recording["id"])
    result = apply(case)
    assert result["active_transcript_id"] == before["active_transcript_id"]
    assert result["speakers"] == before["speakers"]
    for index in (0, 2, 3):
        assert result["segments"][index] == before["segments"][index]
    assert result["segments"][1]["word_timing"] is True
    assert result["segments"][1]["original_words"] == before["segments"][1]["original_words"]
    versions = case.store.result_versions(case.recording["id"], before["active_transcript_id"])
    assert versions[0]["action"] == "timing_realign"
    job = case.store.job(case.job["id"])
    assert job["params"]["realign_applied"] == "applied"
    assert job["params"]["realign_receipt"]["version"]["id"] == versions[0]["id"]


@pytest.mark.parametrize("clear_before_apply", [False, True])
def test_cloud_timing_follows_only_unchanged_frozen_working(
    tmp_path, monkeypatch, clear_before_apply
):
    from cbs_transcriber import result_roles

    study = bundle_fixtures.study.__wrapped__(tmp_path)
    store, recording, result, *_ = study
    rid = recording["id"]
    current = store.recording(rid)
    version = store.result_versions(rid, result["id"])[0]
    role = current["result_roles"]
    plan = result_roles.preview(store, rid, version["id"], current["revision"], role["revision"])
    result_roles.apply(
        store, rid, version["id"], current["revision"], role["revision"], plan["fingerprint"]
    )
    enqueue = store.enqueue_enhancement

    def enqueue_timing(recording_id, result_id, revision, params):
        return enqueue(recording_id, result_id, revision, params | {"realign": True})

    # The legacy fixture otherwise adds the realign flag only after accepting its job.
    monkeypatch.setattr(store, "enqueue_enhancement", enqueue_timing)
    value = cloud_fixtures.execution.__wrapped__(study, tmp_path, monkeypatch)
    repair_remote.process(value.remote, value.job["id"], runner=cloud_fixtures.fake_alignment)
    cloud_fixtures.terminal(value)
    value.controller.harvest()
    value.controller.publish()
    value.store.update_job(value.job["id"], state="failed")
    before = store.recording(rid)
    if clear_before_apply:
        role = before["result_roles"]
        plan = result_roles.preview(store, rid, None, before["revision"], role["revision"])
        result_roles.apply(
            store, rid, None, before["revision"], role["revision"], plan["fingerprint"]
        )
        before = store.recording(rid)
    saved = apply(value)
    persisted = store.job(value.job["id"])["params"]
    tampered = copy.deepcopy(persisted)
    tampered["realign_receipt"]["fingerprint"] = "0" * 64
    with pytest.raises(ValueError, match="Working evidence"):
        store.update_job(value.job["id"], params=tampered)
    if clear_before_apply:
        assert saved["result_roles"] == before["result_roles"]
    else:
        head = store.result_versions(rid, result["id"])[0]
        assert saved["result_roles"]["working"]["version_id"] == head["id"]
        assert saved["result_roles"]["revision"] == before["result_roles"]["revision"] + 1


def test_lost_reply_is_idempotent_with_old_revision(case, monkeypatch):
    before = case.store.recording(case.recording["id"])
    snapshot = case.store.snapshot_project
    monkeypatch.setattr(
        case.store,
        "snapshot_project",
        lambda *_: (_ for _ in ()).throw(OSError("synthetic lost reply")),
    )
    with pytest.raises(OSError):
        apply(case, revision=before["revision"])
    count = len(case.store.result_versions(case.recording["id"], before["active_transcript_id"]))
    monkeypatch.setattr(case.store, "snapshot_project", snapshot)
    again = apply(case, revision=before["revision"])
    assert again["revision"] == before["revision"] + 1
    assert (
        len(case.store.result_versions(case.recording["id"], before["active_transcript_id"]))
        == count
    )


@pytest.mark.parametrize("make_working", [False, True])
def test_cloud_timing_explicit_choice_retries_only_the_same_decision(case, make_working):
    store, rid = case.store, case.recording["id"]
    before = store.recording(rid)
    result_id = before["active_transcript_id"]
    head = store.result_versions(rid, result_id)[0]
    choice = {
        "version_id": head["id"],
        "role_revision": before["result_roles"]["revision"],
        "make_working": make_working,
    }
    proof = repair_cloud.validate_local_outputs(store, store.job(case.job["id"]))
    args = (rid, result_id, case.job["id"], before["revision"], proof["fingerprint"])
    saved = store.apply_repair_timing(*args, working_choice=choice)
    expected = store.result_versions(rid, result_id)[0]["id"] if make_working else None
    role = saved["result_roles"]["working"]
    assert (role["version_id"] if role else None) == expected
    assert (
        store.apply_repair_timing(*args, working_choice=choice)["result_roles"]
        == saved["result_roles"]
    )
    with pytest.raises(ValueError, match="already applied"):
        store.apply_repair_timing(*args, working_choice=choice | {"make_working": not make_working})


def test_stale_revision_and_wrong_result_never_write(case):
    before = case.store.recording(case.recording["id"])
    with pytest.raises(ValueError):
        apply(case, revision=before["revision"] + 1)
    with pytest.raises((ValueError, KeyError)):
        apply(case, result_id="0" * 32)
    assert case.store.recording(case.recording["id"]) == before


def test_newer_selected_wording_is_skipped_and_unselected_edits_survive(case):
    before = case.store.recording(case.recording["id"])
    changed = copy.deepcopy(before["segments"])
    changed[0]["text"] = "New unselected review text."
    changed[1]["text"] = "New selected review text."
    case.store.save_transcript(
        case.recording["id"], changed, before["speakers"], before["revision"]
    )
    result = apply(case)
    assert result["segments"][0] == changed[0]
    assert result["segments"][1] == changed[1]
    assert result["segments"][4]["word_timing"] is True
    receipt = case.store.job(case.job["id"])["params"]["realign_receipt"]
    assert receipt["applied_ids"] == ["4"] and receipt["skipped_ids"] == ["1"]


def test_failure_between_version_and_receipt_rolls_back_everything(case, monkeypatch):
    before = case.store.recording(case.recording["id"])
    versions = case.store.result_versions(case.recording["id"], before["active_transcript_id"])
    append = case.store._append_version

    def interrupted(*args, **kwargs):
        append(*args, **kwargs)
        raise ValueError("synthetic transaction interruption")

    monkeypatch.setattr(case.store, "_append_version", interrupted)
    with pytest.raises(ValueError):
        apply(case)
    assert case.store.recording(case.recording["id"]) == before
    assert (
        case.store.result_versions(case.recording["id"], before["active_transcript_id"]) == versions
    )
    assert "realign_applied" not in case.store.job(case.job["id"])["params"]


def test_older_cloud_output_does_not_replace_a_newer_timing_version(case):
    current = case.store.recording(case.recording["id"])
    newer_job = case.store.enqueue_enhancement(
        case.recording["id"],
        current["active_transcript_id"],
        current["revision"],
        {"realign": True, "segment_id": "1", "enhancement": {"execution": "local"}},
    )
    changed = copy.deepcopy(current["segments"])
    changed[1].update(
        words=[
            {"word": "God", "start": 2.1, "end": 2.5, "char_start": 0, "char_end": 3},
            {"word": "morgen.", "start": 2.6, "end": 4.9, "char_start": 4, "char_end": 11},
        ],
        word_timing=True,
        words_current=True,
    )
    case.store.save_transcript(
        case.recording["id"],
        changed,
        current["speakers"],
        current["revision"],
        action="timing_realign",
        context={"job_id": newer_job["id"]},
    )
    result = apply(case)
    assert result["segments"][1] == changed[1]
    assert result["segments"][4]["word_timing"] is True
    assert case.store.job(case.job["id"])["params"]["realign_receipt"]["skipped_ids"] == ["1"]
