"""V152: a timing successor and its applied job receipt share one writer transaction."""

import copy
import json
import sqlite3

import pytest

from cbs_transcriber.transcripts import normalize_segments


def ready(client, *, mark_first=False):
    store = client.app.state.store
    project = store.create_project("Atomic local timing")
    recording = store.create_recording(project["id"], "synthetic.wav")
    result = store.add_result(
        recording["id"],
        normalize_segments(
            [
                {"text": "Hej verden.", "start": 0, "end": 3},
                {"text": "Untouched æøå.", "start": 3, "end": 5},
            ]
        ),
        {"A": "Anna"},
        "Source",
        {},
    )
    current = store.recording(recording["id"])
    if mark_first:
        from cbs_transcriber import result_roles

        version = store.result_versions(recording["id"], result["id"])[0]
        role = current["result_roles"]
        plan = result_roles.preview(
            store, recording["id"], version["id"], current["revision"], role["revision"]
        )
        result_roles.apply(
            store,
            recording["id"],
            version["id"],
            current["revision"],
            role["revision"],
            plan["fingerprint"],
        )
        current = store.recording(recording["id"])
    job = store.enqueue_enhancement(
        recording["id"],
        result["id"],
        current["revision"],
        {"realign": True, "segment_id": "0", "enhancement": {"execution": "local"}},
    )
    params = job["params"]
    params["realign_output"] = {
        "passages": {
            "0": {
                "words": [
                    {"word": "Hej", "start": 0.1, "end": 1, "char_start": 0, "char_end": 3},
                    {"word": "verden.", "start": 1.1, "end": 2.8, "char_start": 4, "char_end": 11},
                ],
                "word_timing": True,
            }
        }
    }
    store.update_job(job["id"], params=params, state="completed")
    return store, recording["id"], result["id"], job["id"]


def test_v152_job_receipt_failure_rolls_back_timing_version_and_projection(client):
    store, rid, result_id, job_id = ready(client)
    before = store.recording(rid)
    versions = store.result_versions(rid, result_id)
    store.execute(
        "CREATE TRIGGER refuse_timing_receipt BEFORE UPDATE OF params ON jobs "
        "WHEN json_extract(NEW.params,'$.realign_applied')='applied' "
        "BEGIN SELECT RAISE(ABORT,'synthetic receipt failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic receipt"):
        client.post(
            f"/api/recordings/{rid}/realign/{job_id}", json={"revision": before["revision"]}
        )
    assert store.recording(rid) == before
    assert store.result_versions(rid, result_id) == versions
    assert not store.job(job_id)["params"].get("realign_applied")


def test_v152_lost_reply_retry_verifies_one_existing_successor(client):
    store, rid, result_id, job_id = ready(client)
    before = store.recording(rid)
    body = {"revision": before["revision"], "result_id": result_id}
    first = client.post(f"/api/recordings/{rid}/realign/{job_id}", json=body)
    assert first.status_code == 200, first.text
    versions = store.result_versions(rid, result_id)
    again = client.post(f"/api/recordings/{rid}/realign/{job_id}", json=body)
    assert again.status_code == 200, again.text
    assert store.result_versions(rid, result_id) == versions
    assert store.recording(rid)["segments"][1] == before["segments"][1]


def test_timing_follows_exact_frozen_working_once_and_retry_is_idempotent(client):
    store, rid, result_id, job_id = ready(client, mark_first=True)
    before = store.recording(rid)
    frozen = store.job(job_id)["params"]["timing_working_policy"]
    assert frozen["expected_role"] == before["result_roles"]
    first = store.apply_local_timing(rid, result_id, job_id, before["revision"])
    head = store.result_versions(rid, result_id)[0]
    assert first["result_roles"]["working"]["version_id"] == head["id"]
    assert first["result_roles"]["revision"] == before["result_roles"]["revision"] + 1
    again = store.apply_local_timing(rid, result_id, job_id, before["revision"])
    assert again["result_roles"] == first["result_roles"]
    with pytest.raises(ValueError, match="already applied"):
        store.apply_local_timing(rid, result_id, job_id, before["revision"], working_choice={})


def test_fresh_timing_can_promote_selected_result_without_an_existing_working(client):
    store, rid, result_id, job_id = ready(client)
    before = store.recording(rid)
    assert before["result_roles"]["working"] is None
    saved = store.apply_local_timing(rid, result_id, job_id, before["revision"])
    assert (
        saved["result_roles"]["working"]["version_id"]
        == store.result_versions(rid, result_id)[0]["id"]
    )


def test_timing_selected_alternative_promotes_only_its_saved_successor(client):
    store, rid, _, first_job = ready(client, mark_first=True)
    first = store.recording(rid)
    alternative = store.add_result(
        rid, copy.deepcopy(first["segments"]), first["speakers"], "Alternative", {}
    )
    current = store.recording(rid)
    store.activate_result(rid, alternative["id"], current["revision"])
    current = store.recording(rid)
    assert current["active_transcript_id"] == alternative["id"]
    assert current["result_roles"] == first["result_roles"]
    job = store.enqueue_enhancement(
        rid,
        alternative["id"],
        current["revision"],
        {"realign": True, "segment_id": "0", "enhancement": {"execution": "local"}},
    )
    params = job["params"]
    params["realign_output"] = store.job(first_job)["params"]["realign_output"]
    store.update_job(job["id"], params=params, state="completed")
    saved = store.apply_local_timing(rid, alternative["id"], job["id"], current["revision"])
    version = store.result_versions(rid, alternative["id"])[0]
    assert saved["result_roles"]["working"]["version_id"] == version["id"]


def test_timing_does_not_override_a_later_working_choice(client):
    from cbs_transcriber import result_roles

    store, rid, result_id, job_id = ready(client, mark_first=True)
    before = store.recording(rid)
    role = before["result_roles"]
    plan = result_roles.preview(store, rid, None, before["revision"], role["revision"])
    result_roles.apply(store, rid, None, before["revision"], role["revision"], plan["fingerprint"])
    cleared = store.recording(rid)
    applied = store.apply_local_timing(rid, result_id, job_id, cleared["revision"])
    assert applied["result_roles"] == cleared["result_roles"]
    assert applied["segments"][0]["word_timing"] is True


def two_disjoint_timing_jobs(client):
    store, rid, result_id, first_job = ready(client, mark_first=True)
    before = store.recording(rid)
    second = store.enqueue_enhancement(
        rid,
        result_id,
        before["revision"],
        {"realign": True, "segment_id": "1", "enhancement": {"execution": "local"}},
    )
    params = second["params"]
    params["realign_output"] = {
        "passages": {
            "1": {
                "words": [
                    {
                        "word": "Untouched",
                        "start": 3.1,
                        "end": 3.9,
                        "char_start": 0,
                        "char_end": 9,
                    },
                    {"word": "æøå.", "start": 4, "end": 4.8, "char_start": 10, "char_end": 14},
                ],
                "word_timing": True,
            }
        }
    }
    store.update_job(second["id"], params=params, state="completed")
    first = store.apply_local_timing(rid, result_id, first_job, before["revision"])
    return store, rid, result_id, first_job, second["id"], before, first


def test_disjoint_timing_jobs_follow_verified_automatic_successor_chain(client):
    store, rid, result_id, _, second, _, first = two_disjoint_timing_jobs(client)
    saved = store.apply_local_timing(rid, result_id, second, first["revision"])
    assert saved["segments"][0]["word_timing"] is True
    assert saved["segments"][1]["word_timing"] is True
    assert (
        saved["result_roles"]["working"]["version_id"]
        == store.result_versions(rid, result_id)[0]["id"]
    )
    assert "working_follow_warning" not in saved


def test_disjoint_timing_never_overrides_an_intervening_explicit_choice(client):
    from cbs_transcriber import result_roles

    store, rid, result_id, _, second, before, first = two_disjoint_timing_jobs(client)
    original_id = before["result_roles"]["working"]["version_id"]
    role = first["result_roles"]
    plan = result_roles.preview(store, rid, original_id, first["revision"], role["revision"])
    result_roles.apply(
        store, rid, original_id, first["revision"], role["revision"], plan["fingerprint"]
    )
    chosen = store.recording(rid)
    saved = store.apply_local_timing(rid, result_id, second, chosen["revision"])
    assert saved["result_roles"] == chosen["result_roles"]
    assert saved["segments"][1]["word_timing"] is True
    assert saved["working_follow_warning"]


def test_disjoint_timing_does_not_absorb_intervening_manual_edit(client):
    store, rid, result_id, _, second, _, first = two_disjoint_timing_jobs(client)
    segments = copy.deepcopy(first["segments"])
    segments[0].update(text="Reviewed wording.", words=[], word_timing=False)
    store.save_transcript(rid, segments, first["speakers"], first["revision"], result_id=result_id)
    reviewed = store.recording(rid)
    saved = store.apply_local_timing(rid, result_id, second, reviewed["revision"])
    assert saved["result_roles"] == reviewed["result_roles"]
    assert saved["segments"][0]["text"] == "Reviewed wording."
    assert saved["segments"][1]["word_timing"] is True
    assert saved["working_follow_warning"]


@pytest.mark.parametrize("case", ["receipt", "budget", "quarantine"])
def test_disjoint_timing_keeps_working_if_chain_cannot_be_proved(client, monkeypatch, case):
    from cbs_transcriber import timing_working

    store, rid, result_id, first_job, second, _, first = two_disjoint_timing_jobs(client)
    if case == "receipt":
        params = store.job(first_job)["params"]
        params["realign_apply_receipt"]["input_sha256"] = "0" * 64
        store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), first_job))
    elif case == "budget":
        monkeypatch.setattr(timing_working, "MAX_CHAIN_BYTES", 1)
    else:
        store.execute(
            "INSERT INTO restored_owners(kind,owner_id,restore_id,source_backup_id) "
            "VALUES(?,?,?,?)",
            ("job", first_job, "synthetic-restore", "synthetic-backup"),
        )
    saved = store.apply_local_timing(rid, result_id, second, first["revision"])
    assert saved["result_roles"] == first["result_roles"]
    assert saved["segments"][1]["word_timing"] is True
    assert saved["working_follow_warning"]


def test_timing_does_not_promote_when_head_advanced_after_acceptance(client):
    store, rid, result_id, job_id = ready(client, mark_first=True)
    before = store.recording(rid)
    edited = copy.deepcopy(before["segments"])
    edited[1]["text"] = "Later independent wording."
    store.save_transcript(rid, edited, before["speakers"], before["revision"], result_id=result_id)
    current = store.recording(rid)
    applied = store.apply_local_timing(rid, result_id, job_id, current["revision"])
    assert applied["result_roles"] == current["result_roles"]
    assert applied["segments"][1]["text"] == edited[1]["text"]
    assert applied["segments"][0]["word_timing"] is True


def test_timing_working_change_rolls_back_with_job_receipt_failure(client):
    store, rid, result_id, job_id = ready(client, mark_first=True)
    before = store.recording(rid)
    versions = store.result_versions(rid, result_id)
    store.execute(
        "CREATE TRIGGER refuse_working_successor BEFORE INSERT ON result_role_events "
        "WHEN NEW.action='working_auto_successor' BEGIN SELECT RAISE(ABORT, 'blocked'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.apply_local_timing(rid, result_id, job_id, before["revision"])
    assert store.recording(rid) == before
    assert store.result_versions(rid, result_id) == versions
    assert "realign_applied" not in store.job(job_id)["params"]


def test_frozen_timing_working_and_source_cannot_be_replaced(client):
    store, _, _, job_id = ready(client, mark_first=True)
    original = store.job(job_id)["params"]
    for mutation in ("policy", "source"):
        proposed = copy.deepcopy(original)
        if mutation == "policy":
            proposed["timing_working_policy"]["expected_role"]["revision"] += 1
        else:
            proposed["source_snapshot"]["source_payload_sha256"] = "0" * 64
        with pytest.raises(ValueError, match="Working"):
            store.update_job(job_id, params=proposed)
    assert store.job(job_id)["params"] == original


def test_timing_authority_rejects_altered_valid_source_projection(client):
    store, rid, result_id, job_id = ready(client, mark_first=True)
    before = store.recording(rid)
    params = store.job(job_id)["params"]
    # Every passage still belongs to the exact source, but the accepted projection changed.
    params["source_snapshot"]["segments"].reverse()
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job_id))
    with pytest.raises(ValueError, match="accepted receipt"):
        store.apply_local_timing(rid, result_id, job_id, before["revision"])
    assert store.recording(rid) == before


@pytest.mark.parametrize("revision", [True, 1.0])
def test_timing_authority_rejects_json_numeric_type_substitution(client, revision):
    store, rid, result_id, job_id = ready(client, mark_first=True)
    before = store.recording(rid)
    assert before["result_roles"]["revision"] == 1
    params = store.job(job_id)["params"]
    params["timing_working_policy"]["expected_role"]["revision"] = revision
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job_id))
    with pytest.raises(ValueError, match="Working policy"):
        store.apply_local_timing(rid, result_id, job_id, before["revision"])
    assert store.recording(rid) == before


def test_corrupted_authority_policy_cannot_bypass_job_update_guards(client):
    store, _, _, job_id = ready(client, mark_first=True)
    params = store.job(job_id)["params"]
    with store.connection() as db:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.execute("UPDATE working_job_authorities SET policy='unknown' WHERE job_id=?", (job_id,))
    with pytest.raises(ValueError, match="Working"):
        store.update_job(job_id, params=params)


def test_processing_role_cannot_be_forged_removed_or_changed_by_generic_writers(client):
    store, rid, result_id, _ = ready(client, mark_first=True)
    current = store.recording(rid)
    job = store.enqueue_enhancement(
        rid, result_id, current["revision"], {"enhancement": {"execution": "local"}}
    )
    original = job["params"]
    assert original["working_role"] == current["result_roles"]
    assert original["working_role_policy"] == "cbs-working-role-v1"
    for action in ("remove", "change", "remove_policy"):
        proposed = copy.deepcopy(original)
        if action == "remove":
            del proposed["working_role"]
        elif action == "remove_policy":
            del proposed["working_role_policy"]
        else:
            proposed["working_role"]["revision"] += 1
        with pytest.raises(ValueError, match="Working"):
            store.update_job(job["id"], params=proposed)
    legacy = store.add_job(rid, "enhance", {"enhancement": {"execution": "local"}})
    # Matching legacy custom fields remain inert without a separate authority receipt.
    store.update_job(
        legacy["id"],
        params=legacy["params"]
        | {
            "working_role": original["working_role"],
            "working_role_policy": original["working_role_policy"],
        },
    )
    with pytest.raises(ValueError, match="Working evidence"):
        store.add_job(rid, "enhance", {"working_role": original["working_role"]})

    # Existing custom jobs may already have an unrelated unversioned field by this name.
    old_params = legacy["params"] | {"working_role": {"custom": True}}
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(old_params), legacy["id"]))
    store.update_job(legacy["id"], params=old_params | {"custom_progress": 1})
    assert store.job(legacy["id"])["params"]["working_role"] == {"custom": True}


def test_legacy_timing_without_working_policy_never_promotes(client):
    store, rid, result_id, job_id = ready(client, mark_first=True)
    before = store.recording(rid)
    params = store.job(job_id)["params"]
    del params["timing_working_policy"]
    # Simulate a durable job accepted by an older version of the application.
    store.execute("DELETE FROM working_job_authorities WHERE job_id=?", (job_id,))
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job_id))
    applied = store.apply_local_timing(rid, result_id, job_id, before["revision"])
    assert applied["result_roles"] == before["result_roles"]


def test_legacy_timing_with_coincidental_policy_never_promotes(client):
    from cbs_transcriber import repair_cloud

    store, rid, result_id, job_id = ready(client, mark_first=True)
    before = store.recording(rid)
    params = store.job(job_id)["params"]
    # Old custom jobs could use this formerly unreserved key, even with identical value.
    store.execute("DELETE FROM working_job_authorities WHERE job_id=?", (job_id,))
    assert repair_cloud.identity(params) == repair_cloud.identity(
        {key: value for key, value in params.items() if key != "timing_working_policy"}
    )
    applied = store.apply_local_timing(rid, result_id, job_id, before["revision"])
    assert applied["result_roles"] == before["result_roles"]


def test_edit_realign_captures_only_current_exact_source_and_rejects_forged_policy(client):
    store, rid, result_id, _ = ready(client, mark_first=True)
    current = store.recording(rid)
    source = store.freeze_result(rid, result_id, current["revision"])
    params = {"realign": True, "source_snapshot": source, "enhancement": {"execution": "local"}}
    accepted = store.add_job(rid, "enhance", params, capture_timing_role=True)
    assert accepted["params"]["timing_working_policy"]["expected_role"] == current["result_roles"]
    with pytest.raises(ValueError, match="Working evidence"):
        store.add_job(rid, "enhance", accepted["params"])
    segments = copy.deepcopy(current["segments"])
    segments[1]["text"] = "Changed before job acceptance."
    store.save_transcript(
        rid, segments, current["speakers"], current["revision"], result_id=result_id
    )
    with pytest.raises(ValueError, match="timing source changed"):
        store.add_job(rid, "enhance", params, capture_timing_role=True)


@pytest.mark.parametrize("make_working", [False, True])
def test_explicit_timing_choice_is_atomic_and_never_retroactive(client, make_working):
    store, rid, result_id, job_id = ready(client, mark_first=True)
    before = store.recording(rid)
    head = store.result_versions(rid, result_id)[0]
    choice = {
        "version_id": head["id"],
        "role_revision": before["result_roles"]["revision"],
        "make_working": make_working,
    }
    body = {"revision": before["revision"], "result_id": result_id, "working_choice": choice}
    response = client.post(f"/api/recordings/{rid}/realign/{job_id}", json=body)
    assert response.status_code == 200, response.text
    saved = store.recording(rid)
    successor = store.result_versions(rid, result_id)[0]
    expected = successor["id"] if make_working else head["id"]
    assert saved["result_roles"]["working"]["version_id"] == expected
    replay = client.post(f"/api/recordings/{rid}/realign/{job_id}", json=body)
    assert replay.status_code == 200, replay.text
    body["working_choice"]["make_working"] = not make_working
    changed = client.post(f"/api/recordings/{rid}/realign/{job_id}", json=body)
    assert changed.status_code == 409
    assert store.recording(rid)["result_roles"] == saved["result_roles"]


def test_stale_explicit_timing_choice_leaves_timing_unapplied(client):
    store, rid, result_id, job_id = ready(client, mark_first=True)
    before = store.recording(rid)
    head = store.result_versions(rid, result_id)[0]
    body = {
        "revision": before["revision"],
        "result_id": result_id,
        "working_choice": {
            "version_id": head["id"],
            "role_revision": before["result_roles"]["revision"] + 1,
            "make_working": True,
        },
    }
    response = client.post(f"/api/recordings/{rid}/realign/{job_id}", json=body)
    assert response.status_code == 409
    assert store.recording(rid) == before
    assert "realign_applied" not in store.job(job_id)["params"]


def test_v152_newer_word_mapping_is_retained_and_not_marked_applied(client):
    store, rid, result_id, job_id = ready(client)
    current = store.recording(rid)
    segments = copy.deepcopy(current["segments"])
    segments[0].update(
        words=[{"word": "Hej", "start": 0.2, "end": 0.9, "char_start": 0, "char_end": 3}],
        word_timing=False,
        words_current=True,
    )
    store.save_transcript(
        rid, segments, current["speakers"], current["revision"], result_id=result_id
    )
    before = store.recording(rid)
    response = client.post(
        f"/api/recordings/{rid}/realign/{job_id}", json={"revision": before["revision"]}
    )
    assert response.status_code == 409
    assert store.recording(rid)["segments"] == before["segments"]
    assert store.job(job_id)["params"]["realign_applied"] == "stale"


def test_v152_retry_refuses_changed_receipt_but_preserves_later_review_and_notes(client):
    store, rid, result_id, job_id = ready(client)
    before = store.recording(rid)
    response = client.post(
        f"/api/recordings/{rid}/realign/{job_id}", json={"revision": before["revision"]}
    )
    assert response.status_code == 200
    current = store.recording(rid)
    segments = copy.deepcopy(current["segments"])
    segments[1]["text"] = "Later independent wording."
    store.save_transcript(
        rid, segments, current["speakers"], current["revision"], result_id=result_id
    )
    store.save_notes("recordings", rid, "Independent notes", 0)
    current = store.recording(rid)
    replay = client.post(
        f"/api/recordings/{rid}/realign/{job_id}", json={"revision": before["revision"]}
    )
    assert replay.status_code == 200
    assert store.recording(rid) == current
    params = store.job(job_id)["params"]
    params["realign_apply_receipt"]["version"]["payload_sha256"] = "0" * 64
    # Directly corrupt the durable receipt: ordinary producers have no such authority.
    import json

    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job_id))
    refused = client.post(
        f"/api/recordings/{rid}/realign/{job_id}", json={"revision": before["revision"]}
    )
    assert refused.status_code == 409
    assert store.recording(rid) == current


def test_v152_invalid_timing_never_commits(client):
    store, rid, result_id, job_id = ready(client)
    current = store.recording(rid)
    params = store.job(job_id)["params"]
    params["realign_output"]["passages"]["0"]["words"][0]["char_end"] = 4
    store.update_job(job_id, params=params)
    response = client.post(
        f"/api/recordings/{rid}/realign/{job_id}", json={"revision": current["revision"]}
    )
    assert response.status_code == 409
    assert store.recording(rid) == current


def test_v152_lost_reply_after_review_switch_and_trash_does_not_restore_or_reactivate(client):
    store, rid, result_id, job_id = ready(client)
    before = store.recording(rid)
    response = client.post(
        f"/api/recordings/{rid}/realign/{job_id}", json={"revision": before["revision"]}
    )
    assert response.status_code == 200
    alternative = store.add_result(rid, normalize_segments([{"text": "Alternative"}]), {}, "B", {})
    current = store.activate_result(rid, alternative["id"], store.recording(rid)["revision"])
    store.set_result_trashed(rid, result_id, current["revision"], trashed=True)
    current = store.recording(rid)
    versions = store.result_versions(rid, result_id)
    replay = client.post(
        f"/api/recordings/{rid}/realign/{job_id}", json={"revision": before["revision"]}
    )
    assert replay.status_code == 200, replay.text
    assert store.recording(rid) == current
    assert store.result_versions(rid, result_id) == versions
    assert store.results(rid, trashed=True)[0]["id"] == result_id


def test_v152_source_projection_preserves_numeric_types(client):
    store, rid, _, job_id = ready(client)
    current = store.recording(rid)
    params = store.job(job_id)["params"]
    assert type(params["source_snapshot"]["segments"][0]["start"]) is float
    params["source_snapshot"]["segments"][0]["start"] = 0
    # Simulate on-disk corruption; ordinary updates reject frozen source mutation.
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job_id))
    response = client.post(
        f"/api/recordings/{rid}/realign/{job_id}", json={"revision": current["revision"]}
    )
    assert response.status_code == 409
    assert store.recording(rid) == current
    params["source_snapshot"]["segments"][0]["start"] = 0.0
    params["source_snapshot"]["source_payload_sha256"] = "0" * 64
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job_id))
    response = client.post(
        f"/api/recordings/{rid}/realign/{job_id}", json={"revision": current["revision"]}
    )
    assert response.status_code == 409
    assert store.recording(rid) == current


def test_v152_unbounded_passage_cannot_store_its_search_gap_as_timing():
    from cbs_transcriber.local_timing import _candidate

    passage = normalize_segments([{"text": "Hej"}])[0]
    timing = {
        "words": [{"word": "Hej", "start": 10, "end": 11, "char_start": 0, "char_end": 3}],
        "word_timing": True,
        "start": 0,
        "end": 60,
    }
    with pytest.raises(ValueError, match="own completely timed words"):
        _candidate(passage, timing)
    corrected = _candidate(passage, {**timing, "start": 10, "end": 11})
    assert (corrected["start"], corrected["end"]) == (10, 11)
