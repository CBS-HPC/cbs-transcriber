"""Synthetic requests freeze recording/project language without changing legacy fallbacks."""

import threading

import pytest
from test_passage_alignment import passage_source as passage_source
from test_passage_alignment import submit as submit_repair
from test_passage_timing import process as process_timing
from test_passage_timing import request as request_timing
from test_passage_timing import untimed as untimed
from test_realign import edited as edited
from test_realign import realign_jobs
from test_retranscription import request as request_retranscription
from test_retranscription import reviewed as reviewed

from cbs_transcriber import (
    enhancement,
    language_preferences,
    providers,
    repair_cloud,
    repair_remote,
    routing,
)


def select(store, rid, *, project="da", recording="en"):
    row = store.recording(rid)
    language_preferences.save(store, "project", row["project_id"], project, 0)
    language_preferences.save(store, "recording", rid, recording, 0)


def test_passage_timing_uses_recording_language_and_freezes_it(untimed):
    client, store, rid, result = untimed
    select(store, rid)
    response = request_timing(client, rid, result, ["1"])
    assert response.status_code == 202, response.text
    params = store.job(response.json()["id"])["params"]
    assert params["enhancement"]["language"] == "en"
    assert params["language_preference"] == {"scope": "recording", "language": "en"}
    language_preferences.save(store, "recording", rid, "da", 1)
    assert store.job(response.json()["id"])["params"]["enhancement"]["language"] == "en"


def test_recording_language_reaches_timing_worker_and_applies(untimed, monkeypatch):
    client, store, rid, result = untimed
    select(store, rid)
    response = request_timing(client, rid, result, ["1"])
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    process_timing(client, store, identifier, monkeypatch)
    applied = client.post(f"/api/recordings/{rid}/realign/{identifier}", json={"revision": 1})
    assert applied.status_code == 200, applied.text
    assert store.job(identifier)["params"]["enhancement"]["language"] == "en"
    assert store.recording(rid)["segments"][1]["words"]


def test_passage_timing_rejects_stale_choice_after_preflight(untimed, monkeypatch):
    client, store, rid, result = untimed
    select(store, rid)

    def change_during_check(_store, _credentials, _recording, _source, _units, options, *, cloud):
        assert cloud is True
        language_preferences.save(store, "recording", rid, "da", 1)
        return options, {}

    monkeypatch.setattr(routing, "repair_route", change_during_check)
    response = request_timing(client, rid, result, ["1"])
    assert response.status_code == 409, response.text
    assert not store.all("SELECT id FROM jobs WHERE kind='enhance'")


def test_retranscription_uses_project_default_over_request_language(reviewed):
    client, store, rid, result = reviewed
    project = store.recording(rid)["project_id"]
    language_preferences.save(store, "project", project, "en", 0)
    response = request_retranscription(client, rid, result, ["1"], language="da")
    assert response.status_code == 202, response.text
    params = response.json()["params"]
    assert params["enhancement"]["language"] == "en"
    assert params["language_preference"] == {"scope": "project", "language": "en"}


def test_selected_repair_uses_recording_language(client, passage_source):
    rid, _ = passage_source
    store = client.app.state.store
    select(store, rid)
    response = submit_repair(client, passage_source, language="da")
    assert response.status_code == 202, response.text
    params = response.json()["params"]
    assert params["enhancement"]["language"] == "en"
    assert params["language_preference"] == {"scope": "recording", "language": "en"}


def test_whole_enhancement_freezes_preference_on_durable_admission(untimed):
    client, store, rid, result = untimed
    select(store, rid)
    with store.library_lock():
        response = client.post(
            f"/api/recordings/{rid}/enhancements",
            json={"result_id": result["id"], "revision": 1, "language": "da"},
        )
    assert response.status_code == 202, response.text
    accepted = response.json()["params"]["input_intent"]["accepted"]
    assert accepted["options"]["language"] == "en"
    assert accepted["language_preference"] == {"scope": "recording", "language": "en"}


def test_realign_uses_preference_and_unsupported_edit_saves_with_warning(edited):
    client, store, rid, _ = edited
    project = store.recording(rid)["project_id"]
    language_preferences.save(store, "project", project, "en", 0)
    assert client.put("/api/editing-settings", json={"realign_edits": True}).status_code == 200
    revision = store.recording(rid)["revision"]
    response = client.patch(
        f"/api/recordings/{rid}/segments/1",
        json={"revision": revision, "mode": "free", "text": "An English edit."},
    )
    assert response.status_code == 200, response.text
    [job] = realign_jobs(store, "queued")
    params = store.job(job["id"])["params"]
    assert params["enhancement"]["language"] == "en"
    assert params["language_preference"] == {"scope": "project", "language": "en"}
    language_preferences.save(store, "recording", rid, "fr", 0)
    revision = store.recording(rid)["revision"]
    response = client.patch(
        f"/api/recordings/{rid}/segments/1",
        json={"revision": revision, "mode": "free", "text": "Un texte français."},
    )
    assert response.status_code == 200, response.text
    assert "Danish and English only" in response.json()["realign_warning"]
    assert store.recording(rid)["segments"][1]["text"] == "Un texte français."
    assert len(realign_jobs(store, "queued")) == 1


def test_realign_stale_preference_keeps_edit_and_does_not_cancel_previous(edited, monkeypatch):
    client, store, rid, _ = edited
    select(store, rid)
    client.put("/api/editing-settings", json={"realign_edits": True})
    revision = store.recording(rid)["revision"]
    first = client.patch(
        f"/api/recordings/{rid}/segments/1",
        json={"revision": revision, "mode": "free", "text": "First edit."},
    )
    assert first.status_code == 200
    [previous] = realign_jobs(store, "queued")

    def stale(*args):
        raise language_preferences.Conflict("The language changed during admission.")

    monkeypatch.setattr(language_preferences, "require_current", stale)
    revision = store.recording(rid)["revision"]
    response = client.patch(
        f"/api/recordings/{rid}/segments/1",
        json={"revision": revision, "mode": "free", "text": "Second edit."},
    )
    assert response.status_code == 200, response.text
    assert "language changed" in response.json()["realign_warning"]
    assert store.recording(rid)["segments"][1]["text"] == "Second edit."
    assert realign_jobs(store, "queued") == [previous]


@pytest.mark.parametrize("operation", ["timing", "whole_enhancement"])
def test_unsupported_language_fails_before_timing_or_enhancement(untimed, operation):
    client, store, rid, result = untimed
    language_preferences.save(store, "recording", rid, "fr", 0)
    if operation == "timing":
        response = request_timing(client, rid, result, ["1"])
    else:
        with store.library_lock():
            response = client.post(
                f"/api/recordings/{rid}/enhancements",
                json={"result_id": result["id"], "revision": 1},
            )
    assert response.status_code == 400, response.text
    assert "Danish and English only" in response.json()["detail"]
    assert not store.all("SELECT id FROM jobs WHERE kind='enhance'")


def test_unset_preferences_keep_saved_local_language_and_hidden_writes_preserve_it(untimed):
    client, store, rid, result = untimed
    assert client.put("/api/enhancement-settings", json={"language": "en"}).status_code == 200
    response = client.put("/api/enhancement-settings", json={"device": "cpu"})
    assert response.json()["language"] == "en"
    response = request_timing(client, rid, result, ["1"])
    assert response.status_code == 202, response.text
    params = store.job(response.json()["id"])["params"]
    assert params["enhancement"]["language"] == "en"
    assert "language_preference" not in params


def test_concurrent_omitted_local_language_cannot_overwrite_explicit_update(client):
    store = client.app.state.store
    barrier = threading.Barrier(3)
    errors = []

    def save(settings, preserve=()):
        barrier.wait()
        try:
            enhancement.save_local_settings(store, settings, preserve_fields=preserve)
        except Exception as error:
            errors.append(error)

    writers = [
        threading.Thread(target=save, args=(enhancement.ProcessingSettings(language="en"),)),
        threading.Thread(
            target=save,
            args=(enhancement.ProcessingSettings(device="cpu"), {"language"}),
        ),
    ]
    for writer in writers:
        writer.start()
    barrier.wait()
    for writer in writers:
        writer.join(30)
        assert not writer.is_alive()
    assert not errors
    assert enhancement.local_settings(store).language == "en"


def test_concurrent_partial_processing_api_saves_keep_unrelated_fields(client):
    initial = client.put("/api/enhancement-settings", json={"language": "en"})
    assert initial.status_code == 200
    barrier = threading.Barrier(3)
    replies = []

    def save(payload):
        barrier.wait()
        replies.append(client.put("/api/enhancement-settings", json=payload))

    writers = [
        threading.Thread(target=save, args=({"device": "cpu"},)),
        threading.Thread(target=save, args=({"prepare_ucloud_models": False},)),
    ]
    for writer in writers:
        writer.start()
    barrier.wait()
    for writer in writers:
        writer.join(30)
        assert not writer.is_alive()
    assert len(replies) == 2 and all(reply.status_code == 200 for reply in replies)
    saved = client.get("/api/enhancement-settings").json()
    assert saved["language"] == "en"
    assert saved["device"] == "cpu"
    assert saved["prepare_ucloud_models"] is False


@pytest.mark.parametrize("explicit", [False, True])
def test_omitted_openai_language_preserves_saved_value(client, explicit):
    first = client.put("/api/settings", json={"language": "da"})
    assert first.status_code == 200, first.text
    payload = {
        "provider_type": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "whisper-1",
    }
    if explicit:
        payload["language"] = "en"
    updated = client.put("/api/settings", json=payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()["language"] == ("en" if explicit else "da")


def test_concurrent_omitted_provider_language_keeps_explicit_update(client, credentials):
    store = client.app.state.store
    identifier = providers.default_provider_id(store)
    profile = providers.profile(store, identifier)
    barrier = threading.Barrier(3)
    errors = []

    def save(settings):
        barrier.wait()
        try:
            providers.save_profile(
                store, credentials, profile["name"], settings, identifier=identifier
            )
        except Exception as error:
            errors.append(error)

    writers = [
        threading.Thread(
            target=save, args=(providers.Settings(provider_type="openai", language="en"),)
        ),
        threading.Thread(target=save, args=(providers.Settings(provider_type="openai"),)),
    ]
    for writer in writers:
        writer.start()
    barrier.wait()
    for writer in writers:
        writer.join(30)
        assert not writer.is_alive()
    assert not errors
    assert providers.profile(store, identifier)["settings"]["language"] == "en"


def test_repair_identity_binds_new_scope_without_changing_legacy_identity():
    params = {"enhancement": {"language": "en"}, "segment_id": "1"}
    legacy = repair_remote.digest(repair_remote.encoded(params))
    assert repair_cloud.identity(params) == legacy
    project = params | {"language_preference": {"scope": "project", "language": "en"}}
    recording = params | {"language_preference": {"scope": "recording", "language": "en"}}
    assert repair_cloud.identity(project) != legacy
    assert repair_cloud.identity(project) != repair_cloud.identity(recording)
