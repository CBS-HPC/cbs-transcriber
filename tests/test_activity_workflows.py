"""Durable request cards survive source/producer/follow-up handoffs and history limits."""

import json
from uuid import uuid4

import pytest
from test_activity_remove import finished
from test_delete_cancellation import native_jobs
from test_input_admission import inspect, item, native_provider
from test_restored_activity import mark

from cbs_transcriber import (
    app as app_module,
)
from cbs_transcriber import (
    input_admission,
    jobs,
    native_batch,
    original_inspection,
    providers,
    remote_release,
    restored_owners,
)
from cbs_transcriber.store import now


def selection(client, credentials, *, models=1, count=2):
    store = client.app.state.store
    project = store.create_project("Synthetic Activity workflow")
    rows = [store.create_recording(project["id"], f"file-{i}.wav") for i in range(count)]
    profiles = []
    for index in range(models):
        value = providers.Settings(
            provider_type="custom",
            base_url="http://127.0.0.1:9000/v1",
            model=f"model-{index}",
            capability="segments",
        )
        saved = providers.save_profile(store, credentials, f"Model {index}", value, remember=False)
        profiles.append(providers.profile(store, saved["id"]))
    accepted = input_admission.accept(
        store, [item(profile, row) for row in rows for profile in profiles]
    )
    return store, project, rows, profiles, accepted


def transcriptions(client):
    return [row for row in client.get("/api/jobs").json() if row["kind"] == "transcribe"]


def test_request_exposes_only_its_current_verified_inspection_generation(
    client, credentials, wav_bytes
):
    store = client.app.state.store
    with store.library_lock():
        _, project, rows, _, accepted = selection(client, credentials)
        listed = {row["id"]: row for row in client.get("/api/jobs").json()}
        request = accepted[0]["params"]["input_intent"]["request_id"]
        for job, recording in zip(accepted, rows, strict=True):
            cache = original_inspection.read(store, recording["id"])
            assert listed[job["id"]]["activity_inspection"] == {
                "job_id": cache["inspection_job_id"],
                "generation": cache["generation"],
                "request_id": request,
                "shared": False,
            }
            assert listed[cache["inspection_job_id"]]["project_id"] == project["id"]
        inspect(store, rows[0], wav_bytes)
        token = input_admission.claim(store)
        input_admission.materialize(
            store, token, input_admission.prepare(store, credentials, token)
        )
        current = {row["id"]: row for row in client.get("/api/jobs").json()}
        assert current[accepted[0]["id"]]["activity_inspection"]["request_id"] == request
        params = store.job(accepted[0]["id"])["params"]
        invalid_audio = json.loads(json.dumps(params))
        invalid_audio["audio_input"]["sha256"] = "f" * 64
        store.execute(
            "UPDATE jobs SET params=? WHERE id=?", (json.dumps(invalid_audio), accepted[0]["id"])
        )
        corrupted = {row["id"]: row for row in client.get("/api/jobs").json()}
        assert "activity_inspection" not in corrupted[accepted[0]["id"]]
        # A malformed receipt does not bind an inspector to another activity.
        params["input_acceptance"]["generation"] = "f" * 32
        store.execute(
            "UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), accepted[0]["id"])
        )
        corrupted = {row["id"]: row for row in client.get("/api/jobs").json()}
        assert "activity_inspection" not in corrupted[accepted[0]["id"]]


def test_hidden_old_single_request_keeps_shared_inspection_independent(client, credentials):
    store = client.app.state.store
    with store.library_lock():
        _, project, recordings, _, accepted = selection(client, credentials)
        value = providers.Settings(
            provider_type="custom",
            base_url="http://127.0.0.1:9001/v1",
            model="other-model",
            capability="segments",
        )
        saved = providers.save_profile(store, credentials, "Other", value, remember=False)
        profile = providers.profile(store, saved["id"])
        other = input_admission.accept(store, [item(profile, recordings[0])])[0]
        input_admission.cancel(store, other["id"], epoch=1, phase="waiting")
        response = client.post("/api/activity/hide", json={"job_id": other["id"]})
        assert response.status_code == 200, response.text
        filler = store.create_recording(project["id"], "filler.wav")
        with store.connection() as db:
            db.execute(
                "UPDATE jobs SET created=? WHERE id=?", ("2000-01-01T00:00:00+00:00", other["id"])
            )
            db.executemany(
                "INSERT INTO jobs(id,recording_id,kind,state,params,created,updated) "
                "VALUES (?,?,'media','completed','{}',?,?)",
                [(uuid4().hex, filler["id"], now(), now()) for _ in range(201)],
            )
        listed = {row["id"]: row for row in client.get("/api/jobs").json()}
        assert other["id"] not in listed
        assert listed[accepted[0]["id"]]["activity_inspection"]["shared"] is True
        assert listed[accepted[1]["id"]]["activity_inspection"]["shared"] is False


def test_ambiguous_upload_ownership_does_not_join_request(client, credentials):
    store = client.app.state.store
    with store.library_lock():
        _, _, recordings, _, accepted = selection(client, credentials)
        marker = uuid4().hex
        finished(store, recordings[0]["id"], kind="media", params={"upload_batch": marker})
        finished(store, recordings[0]["id"], kind="media")
        listed = {row["id"]: row for row in client.get("/api/jobs").json()}
        assert all("activity_inspection" not in listed[job["id"]] for job in accepted)


def test_batch_expands_verified_inspections_past_recent_history_limit(
    client, credentials, wav_bytes
):
    store = client.app.state.store
    with store.library_lock():
        _, project, recordings, _, accepted = selection(client, credentials)
        filler = store.create_recording(project["id"], "filler.wav")
        inspections = []
        for recording in recordings:
            cache = inspect(store, recording, wav_bytes)
            inspections.append(cache["inspection_job_id"])
            token = input_admission.claim(store)
            input_admission.materialize(
                store, token, input_admission.prepare(store, credentials, token)
            )
        with store.connection() as db:
            db.executemany(
                "UPDATE jobs SET created=? WHERE id=?",
                [("2000-01-01T00:00:00+00:00", identifier) for identifier in inspections],
            )
            db.executemany(
                "INSERT INTO jobs(id,recording_id,kind,state,params,created,updated) "
                "VALUES (?,?,'media','completed','{}',?,?)",
                [(uuid4().hex, filler["id"], now(), now()) for _ in range(201)],
            )
        listed = {row["id"]: row for row in client.get("/api/jobs").json()}
        assert set(inspections) <= listed.keys()
        assert {listed[job["id"]]["activity_inspection"]["job_id"] for job in accepted} == set(
            inspections
        )


def test_request_identity_survives_partial_materialization_and_exact_followups(
    client, credentials, wav_bytes
):
    store = client.app.state.store
    with store.library_lock():
        store, _, rows, _, accepted = selection(client, credentials)
        request = accepted[0]["params"]["input_intent"]["request_id"]
        assert {row["activity_request_id"] for row in transcriptions(client)} == {request}
        for row in rows:
            inspect(store, row, wav_bytes)
            token = input_admission.claim(store)
            input_admission.materialize(
                store, token, input_admission.prepare(store, credentials, token)
            )
            public = transcriptions(client)
            assert len(public) == 2
            assert {row["activity_request_id"] for row in public} == {request}
            assert all(row["activity_membership_complete"] for row in public)
        children = []
        for job in accepted:
            child_id = uuid4().hex
            params = store.job(job["id"])["params"]
            params["followup"] = {"id": child_id, "params": {"enhancement": {"execution": "local"}}}
            store.update_job(job["id"], params=params, state="running")
            store.add_result(
                job["recording_id"],
                [{"id": "p1", "start": 0, "end": 1, "text": "Synthetic", "speaker": "A"}],
                {},
                "Synthetic",
                {"kind": "provider"},
                job_id=job["id"],
            )
            children.append(child_id)
        public = [row for row in client.get("/api/jobs").json() if row["id"] in children]
        assert len(public) == 2
        assert {row["activity_request_id"] for row in public} == {request}
        assert {row["activity_parent_job_id"] for row in public} == {job["id"] for job in accepted}
        assert (
            client.post("/api/activity/hide", json={"job_id": accepted[0]["id"]}).status_code == 409
        )
        for identifier in children:
            store.update_job(identifier, state="completed")
        hidden = client.post("/api/activity/hide", json={"job_id": children[0]})
        assert hidden.status_code == 200, hidden.text
        assert set(hidden.json()["hidden"]) == set(children) | {job["id"] for job in accepted}
        # An arbitrary parent ID without the exact producer/result binding grants no grouping.
        forged = store.add_job(
            rows[0]["id"],
            "enhance",
            {
                "parent_transcription_id": accepted[1]["id"],
                "source_snapshot": {"id": "f" * 32},
            },
        )
        listed = next(row for row in client.get("/api/jobs").json() if row["id"] == forged["id"])
        assert "activity_request_id" not in listed


def test_active_upload_retains_aged_failed_sibling_and_project_scope(client):
    store = client.app.state.store
    projects = [store.create_project(f"Synthetic {i}") for i in range(2)]
    rows = [store.create_recording(project["id"], "file.wav") for project in projects]
    marker = uuid4().hex
    old = finished(store, rows[0]["id"], "failed", {"upload_batch": marker}, "media")
    active = store.add_job(rows[0]["id"], "media", {"upload_batch": marker})
    foreign = store.add_job(rows[1]["id"], "media", {"upload_batch": marker})
    for _ in range(201):
        finished(store, rows[0]["id"], kind="media")
    listed = {row["id"]: row for row in client.get("/api/jobs").json()}
    assert {old, active["id"], foreign["id"]} <= listed.keys()
    store.update_job(active["id"], state="completed")
    hidden = client.post("/api/activity/hide", json={"job_id": old})
    assert hidden.status_code == 200, hidden.text
    assert set(hidden.json()["hidden"]) == {old, active["id"]}


def test_materialization_between_raw_rows_and_projection_keeps_frozen_intent_token(
    client, credentials, wav_bytes, monkeypatch
):
    store = client.app.state.store
    with store.library_lock():
        _, _, rows, _, accepted = selection(client, credentials, count=1)
        inspect(store, rows[0], wav_bytes)
        token = input_admission.claim(store)
        outcomes = input_admission.prepare(store, credentials, token)
        original, changed = input_admission.projection, []

        def transition(current_store, identifier, **kwargs):
            if identifier == accepted[0]["id"] and not changed:
                input_admission.materialize(store, token, outcomes)
                changed.append(identifier)
            return original(current_store, identifier, **kwargs)

        monkeypatch.setattr(input_admission, "projection", transition)
        row = transcriptions(client)[0]
        assert changed and "input_acceptance" in store.job(accepted[0]["id"])["params"]
        assert row["input_preparation"]["kind"] == "intent"
        assert row["input_preparation"]["phase"] == "materializing"
        assert row["input_preparation"]["epoch"] == token["jobs"][0]["epoch"]


def test_atomic_child_created_during_expansion_never_appears_without_request_identity(
    client, credentials, wav_bytes, monkeypatch
):
    store = client.app.state.store
    with store.library_lock():
        _, _, rows, _, accepted = selection(client, credentials, count=1)
        inspect(store, rows[0], wav_bytes)
        token = input_admission.claim(store)
        input_admission.materialize(
            store, token, input_admission.prepare(store, credentials, token)
        )
        parent, child = accepted[0], uuid4().hex
        params = store.job(parent["id"])["params"]
        params["followup"] = {"id": child, "params": {"enhancement": {"execution": "local"}}}
        store.update_job(parent["id"], params=params, state="running")
        marker = uuid4().hex
        old = finished(store, rows[0]["id"], "failed", {"upload_batch": marker}, "media")
        store.add_job(rows[0]["id"], "media", {"upload_batch": marker})
        for _ in range(201):
            finished(store, rows[0]["id"], kind="media")
        original, changed = app_module.activity_listing, []

        def transition(current_store, selected, **kwargs):
            result = original(current_store, selected, **kwargs)
            if not changed:
                assert old in result[0]  # Forces the expansion re-read after this commit.
                store.add_result(
                    parent["recording_id"],
                    [{"id": "p1", "start": 0, "end": 1, "text": "Synthetic", "speaker": "A"}],
                    {},
                    "Synthetic",
                    {"kind": "provider"},
                    job_id=parent["id"],
                )
                changed.append(child)
            return result

        monkeypatch.setattr(app_module, "activity_listing", transition)
        first = client.get("/api/jobs").json()
        assert changed and all(row["id"] != child for row in first)
        second = next(row for row in client.get("/api/jobs").json() if row["id"] == child)
        assert second["activity_request_id"] == params["input_acceptance"]["request_id"]


def test_oversize_upload_is_explicit_and_cannot_hide_partial_membership(client, monkeypatch):
    monkeypatch.setattr(jobs, "ACTIVITY_GROUP_LIMIT", 2)
    store = client.app.state.store
    project = store.create_project("Synthetic bounded upload")
    row = store.create_recording(project["id"], "file.wav")
    marker = uuid4().hex
    identifiers = [
        finished(store, row["id"], params={"upload_batch": marker}, kind="media") for _ in range(3)
    ]
    listed = [row for row in client.get("/api/jobs").json() if row["id"] in identifiers]
    assert len(listed) == 3
    assert all(row["activity_membership_complete"] is False for row in listed)
    assert all(row["activity_group_limit"] == 2 for row in listed)
    response = client.post("/api/activity/hide", json={"job_id": identifiers[0]})
    assert response.status_code == 409 and "display limit" in response.json()["detail"]
    assert client.get("/api/activity").json()["hidden"] == {}


@pytest.mark.parametrize("survivors", [1, 2])
def test_native_handoff_retains_source_failure_outside_surviving_owner(
    client, credentials, wav_bytes, survivors
):
    store = client.app.state.store
    project = store.create_project("Synthetic partial native admission")
    rows = [store.create_recording(project["id"], f"file-{i}.wav") for i in range(survivors + 1)]
    profile = native_provider(store, credentials)
    with store.library_lock():
        accepted = input_admission.accept(store, [item(profile, row) for row in rows])
        for row in rows[:survivors]:
            inspect(store, row, wav_bytes)
        cache = original_inspection.read(store, rows[-1]["id"])
        original_inspection.run(store, original_inspection.claim(store, cache["inspection_job_id"]))
        token = input_admission.claim(store)
        input_admission.materialize(
            store, token, input_admission.prepare(store, credentials, token)
        )
        public = {row["id"]: row for row in transcriptions(client)}
        assert public[accepted[-1]["id"]]["input_preparation"]["error_code"] == "source_error"
        assert "activity_request_id" in public[accepted[-1]["id"]]
        owners = [public[job["id"]] for job in accepted[:survivors]]
        assert all("activity_request_id" not in row for row in owners)
        if survivors == 1:
            assert owners[0]["ucloud_batch_id"] is None
        else:
            assert len({row["ucloud_batch_id"] for row in owners}) == 1
            assert all(row["ucloud_batch_count"] == survivors for row in owners)


def test_restored_siblings_are_excluded_before_the_live_group_bound(client, monkeypatch):
    monkeypatch.setattr(jobs, "ACTIVITY_GROUP_LIMIT", 2)
    store = client.app.state.store
    project = store.create_project("Synthetic archival and live upload receipts")
    row = store.create_recording(project["id"], "file.wav")
    marker = uuid4().hex
    identifiers = [f"{i:032x}" for i in range(1, 6)]
    with store.connection() as db:
        db.execute(restored_owners.DDL)
        db.executemany(
            "INSERT INTO jobs(id,recording_id,kind,state,params,created,updated) "
            "VALUES (?,?,'media','completed',?,?,?)",
            [
                (identifier, row["id"], json.dumps({"upload_batch": marker}), now(), now())
                for identifier in identifiers
            ],
        )
    for identifier in identifiers[:3]:
        mark(store, "job", identifier)
    listed = {row["id"]: row for row in client.get("/api/jobs").json()}
    assert all(listed[identifier]["activity_membership_complete"] for identifier in identifiers[3:])
    assert all(
        "activity_membership_complete" not in listed[identifier] for identifier in identifiers[:3]
    )
    hidden = client.post("/api/activity/hide", json={"job_id": identifiers[3]})
    assert hidden.status_code == 200, hidden.text
    assert set(hidden.json()["hidden"]) == set(identifiers[3:])


def test_deleted_native_cleanup_obligations_do_not_use_recent_history_cap(client, credentials):
    store = client.app.state.store
    project = store.create_project("Synthetic deleted native obligations")
    rows = [store.create_recording(project["id"], f"file-{i}.wav") for i in range(2)]
    profile = native_provider(store, credentials)
    accepted = store.add_transcription_jobs(
        [
            {
                "recording_id": row["id"],
                "params": {
                    "provider_id": profile["id"],
                    "provider_name": profile["name"],
                    "settings": profile["settings"],
                },
            }
            for row in rows
        ]
    )
    identifier = accepted[0]["params"]["native_batch_id"]
    store.finish_native_batch(identifier, "failed", "Synthetic retained cleanup")
    store.update_native_batch(
        identifier,
        run_update={
            "job_id": "synthetic",
            "remote_state": "FAILURE",
            "cleanup_state": "pending",
            "root_created": True,
        },
    )
    native_batch.confirm_release(
        store,
        identifier,
        target={"kind": "project", "name": "Synthetic"},
        deleted_job_ids=[job["id"] for job in accepted],
    )
    template = store.one("SELECT * FROM native_batch_runs WHERE id=?", (identifier,))
    with store.connection() as db:
        db.execute("DELETE FROM recordings WHERE project_id=?", (project["id"],))
        for _ in range(200):
            duplicate = {**template, "id": uuid4().hex, "updated": now()}
            db.execute(
                "INSERT INTO native_batch_runs("
                + ",".join(duplicate)
                + ") VALUES ("
                + ",".join("?" for _ in duplicate)
                + ")",
                list(duplicate.values()),
            )
    assert len(native_batch.retained_activity(store)) == 201
    assert len(client.get("/api/jobs").json()) == 201


@pytest.mark.parametrize("pending_cleanup", [False, True])
def test_native_http_delete_during_poll_preserves_captured_or_retained_owner(
    client, monkeypatch, pending_cleanup
):
    store = client.app.state.store
    recordings, accepted = native_jobs(client)
    identifier = accepted[0]["params"]["native_batch_id"]
    route = "/api/projects/" + recordings[0]["project_id"]
    parameters = {}
    if pending_cleanup:
        store.update_native_batch(
            identifier,
            run_update={
                "job_id": "synthetic-terminal",
                "remote_state": "FAILURE",
                "root_created": True,
                "owned_folder": native_batch.folder_for(store.native_batch(identifier)),
            },
        )

        def retained(current_store, credentials, batch_id):
            current_store.update_native_batch(
                batch_id,
                run_update={
                    "cleanup_state": "pending",
                    "cleanup_error": "Synthetic cleanup unavailable",
                },
            )
            return "pending"

        # Real HTTP Delete owns cancellation, confirmation, staging and logical commit;
        # only its remote transport outcome is simulated.
        monkeypatch.setattr(remote_release, "_finish_native", retained)
        preview = client.delete(route)
        assert preview.status_code == 409, preview.text
        detail = preview.json()["detail"]
        parameters = {"operation_id": detail["operation_id"], "fingerprint": detail["fingerprint"]}
    original, deleted = app_module.activity_listing, []

    def transition(current_store, rows, **kwargs):
        result = original(current_store, rows, **kwargs)
        if not deleted:
            reply = client.delete(route, params=parameters)
            assert reply.status_code == 200, reply.text
            deleted.append(reply.json())
        return result

    monkeypatch.setattr(app_module, "activity_listing", transition)
    response = client.get("/api/jobs")
    assert response.status_code == 200, response.text
    assert deleted and not store.all("SELECT id FROM jobs")
    cards = response.json()
    if pending_cleanup:
        assert len(cards) == 1
        assert cards[0]["remote_release"]["id"] == identifier
        assert cards[0]["ucloud_cleanup_state"] == "pending"
        assert cards[0]["id"] == "native-batch-" + identifier
        assert not any(row.get("ucloud_batch_id") == identifier for row in cards)
    else:
        assert {row["id"] for row in cards} == {job["id"] for job in accepted}
        assert all(row["ucloud_batch_count"] == 2 for row in cards)
    later = client.get("/api/jobs")
    assert later.status_code == 200, later.text
    assert len(later.json()) == int(pending_cleanup)


def test_native_poll_does_not_silently_drop_existing_invalid_owner(client):
    store = client.app.state.store
    _, accepted = native_jobs(client)
    identifier = accepted[0]["params"]["native_batch_id"]
    batch = store.native_batch(identifier)
    batch["params"]["settings"]["model"] = "synthetic-mismatched-model"
    store.execute(
        "UPDATE native_batch_runs SET params=? WHERE id=?",
        (json.dumps(batch["params"]), identifier),
    )
    response = client.get("/api/jobs")
    assert response.status_code == 400, response.text
    assert "frozen provider identity disagrees" in response.json()["detail"]
    assert len(store.all("SELECT id FROM jobs")) == 2
    assert store.native_batch(identifier)["state"] == "queued"
