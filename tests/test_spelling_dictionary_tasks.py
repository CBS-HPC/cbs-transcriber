"""Automatic public dictionary downloads as Activity tasks (ADR 0187).

Synthetic dictionaries and mocked retrieval only: no test downloads a real dictionary.
"""

import hashlib
import threading
import time

import pytest
import test_spelling_release_profile
from test_spelling import FakeWorker, tiny_catalog
from test_spelling_release_profile import client

from cbs_transcriber import spelling, spelling_tasks
from cbs_transcriber.store import Store

synthetic_dictionaries = test_spelling_release_profile.synthetic_dictionaries


def cards(api):
    return [job for job in api.get("/api/jobs").json() if job["kind"] == "spelling_dictionary"]


def save_preferences(api, enabled, auto_download):
    revision = api.get("/api/spelling/settings").json()["revision"]
    response = api.put(
        "/api/spelling/settings",
        json={"revision": revision, "enabled": enabled, "auto_download": auto_download},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_downloads_follow_enabling_and_language_saves_but_never_startup(
    tmp_path, synthetic_dictionaries
):
    root, cache = tmp_path / "library", tmp_path / "cache"
    with client(root, "full", cache) as api:
        store = api.app.state.store
        project = store.create_project("Synthetic", default_language="da")
        rid = store.create_recording(project["id"], "synthetic.wav")["id"]
        other = store.create_recording(project["id"], "other.wav")["id"]
        # Saved without the HTTP trigger: checking and downloads are on before restart.
        api.app.state.spelling.save_settings(0, True, True)
        assert cards(api) == []
    # Startup alone downloads nothing.
    with client(root, "full", cache) as api:
        tasks = api.app.state.spelling_downloads
        assert cards(api) == [] and not synthetic_dictionaries
        save_preferences(api, True, True)
        queued = cards(api)
        assert [(card["state"], card["spelling_dictionary"]["language"]) for card in queued] == [
            ("queued", "da")
        ]
        assert queued[0]["id"] == "spelling-dictionary:" + queued[0]["spelling_dictionary"]["id"]
        assert queued[0]["recording_id"] is None and "transcript" not in str(queued[0]).lower()
        tasks.run_pending()
        assert [card["state"] for card in cards(api)] == ["completed"]
        assert len(synthetic_dictionaries) == 3
        # A recording override needs English; a repeated save queues nothing new.
        language = api.get(f"/api/recordings/{rid}").json()
        response = api.put(
            f"/api/recordings/{rid}/language",
            json={"language": "en", "revision": language["language_revision"]},
        )
        assert response.status_code == 200, response.text
        assert [card["spelling_dictionary"]["language"] for card in cards(api)] == ["da", "en"]
        response = api.put(
            f"/api/projects/{project['id']}/recording-languages",
            json={"language": "en", "recordings": [{"id": other, "revision": 0}]},
        )
        assert response.status_code == 200, response.text
        assert len(cards(api)) == 2
        tasks.run_pending()
        assert len(synthetic_dictionaries) == 6
        # Unsupported languages and automatic downloads switched off queue nothing.
        save_preferences(api, True, False)
        response = api.put(
            f"/api/projects/{project['id']}/language", json={"language": "fr", "revision": 0}
        )
        assert response.status_code == 200, response.text
        assert len(cards(api)) == 2


def test_explicit_consent_editor_request_and_revision_fence(tmp_path, synthetic_dictionaries):
    with client(tmp_path / "library", "full", tmp_path / "cache") as api:
        store = api.app.state.store
        project = store.create_project("Synthetic", default_language="en")
        rid = store.create_recording(project["id"], "synthetic.wav")["id"]
        preferences = save_preferences(api, True, False)
        body = {"settings_revision": preferences["revision"], "recording_id": rid}
        assert api.post("/api/spelling/dictionary-tasks", json=body).status_code == 409
        stale = {**body, "settings_revision": 0, "consent": True}
        assert api.post("/api/spelling/dictionary-tasks", json=stale).status_code == 409
        both = {**body, "consent": True, "language": "en"}
        assert api.post("/api/spelling/dictionary-tasks", json=both).status_code == 400
        accepted = api.post("/api/spelling/dictionary-tasks", json={**body, "consent": True})
        assert accepted.status_code == 202, accepted.text
        assert [task["state"] for task in accepted.json()["tasks"]] == ["queued"]
        assert accepted.json()["ready"] == []
        # A second request while it waits does not queue a duplicate.
        again = api.post("/api/spelling/dictionary-tasks", json={**body, "consent": True})
        assert len(again.json()["tasks"]) == 1 and len(cards(api)) == 1
        api.app.state.spelling_downloads.run_pending()
        done = api.post("/api/spelling/dictionary-tasks", json={**body, "consent": True})
        assert done.json()["ready"] == ["en"] and len(cards(api)) == 1
        # The editor's first use with automatic downloads on asks for its own language.
        preferences = save_preferences(api, True, True)
        unsupported = store.create_recording(project["id"], "french.wav")["id"]
        store.execute("UPDATE recordings SET language_override='fr' WHERE id=?", (unsupported,))
        refused = api.post(
            "/api/spelling/dictionary-tasks",
            json={"settings_revision": preferences["revision"], "recording_id": unsupported},
        )
        assert refused.status_code == 400
    assert len(synthetic_dictionaries) == 3


def test_failure_waits_for_retry_and_finished_cards_can_be_dismissed(
    tmp_path, synthetic_dictionaries, monkeypatch
):
    original = spelling._download_file

    def offline(entry, target, *, guard=None):
        raise OSError(r"C:\private\path unreachable")

    monkeypatch.setattr(spelling, "_download_file", offline)
    with client(tmp_path / "library", "full", tmp_path / "cache") as api:
        store = api.app.state.store
        tasks = api.app.state.spelling_downloads
        store.create_project("Synthetic", default_language="en")
        save_preferences(api, True, True)
        tasks.run_pending()
        [failed] = cards(api)
        assert failed["state"] == "failed"
        assert failed["spelling_dictionary"]["can_retry"] is True
        assert "private" not in failed["message"] and "Retry" in failed["message"]
        assert not list((tmp_path / "cache").rglob("installed.json"))
        # Automatic triggers never restart a failed language by themselves.
        save_preferences(api, True, True)
        assert [card["state"] for card in cards(api)] == ["failed"]
        identifier = failed["spelling_dictionary"]["id"]
        monkeypatch.setattr(spelling, "_download_file", original)
        retried = api.post(f"/api/spelling/dictionary-tasks/{identifier}/retry")
        assert retried.status_code == 202, retried.text
        assert retried.json()["state"] == "queued"
        successor = retried.json()["spelling_dictionary"]["id"]
        assert successor != identifier
        assert api.post(f"/api/spelling/dictionary-tasks/{successor}/retry").status_code == 409
        assert api.delete(f"/api/spelling/dictionary-tasks/{successor}").status_code == 409
        tasks.run_pending()
        assert [card["state"] for card in cards(api)] == ["completed"]
        assert api.delete(f"/api/spelling/dictionary-tasks/{successor}").json() == {"ok": True}
        assert cards(api) == []
        assert api.delete(f"/api/spelling/dictionary-tasks/{successor}").status_code == 404
    assert len(synthetic_dictionaries) == 3


@pytest.fixture
def service(tmp_path, monkeypatch):
    store = Store(tmp_path / "library")
    for language in ("da", "en"):
        store.create_project(f"Synthetic {language}", default_language=language)
    catalog, bodies = tiny_catalog()
    service = spelling.SpellingService(
        store, tmp_path / "cache", catalog=catalog, worker=FakeWorker()
    )
    service.save_settings(0, True, True)
    gate, active, peak = threading.Event(), [], []

    def slow(entry, target, *, guard=None):
        active.append(entry["url"])
        peak.append(len(active))
        try:
            while not gate.wait(0.02):
                guard()
            target.write_bytes(
                next(
                    body
                    for body in bodies.values()
                    if hashlib.sha256(body).hexdigest() == entry["sha256"]
                )
            )
        finally:
            active.remove(entry["url"])

    monkeypatch.setattr(spelling, "_download_file", slow)
    yield service, gate, peak
    gate.set()
    service.close()


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "The synthetic download state never arrived."
        time.sleep(0.01)


def states(tasks):
    return [(card["spelling_dictionary"]["language"], card["state"]) for card in tasks.activity()]


def test_one_download_at_a_time_and_shutdown_interrupts_without_publishing(service):
    service, gate, peak = service
    tasks = spelling_tasks.DictionaryTasks(service)
    assert len(tasks.automatic()) == 2
    wait_for(lambda: states(tasks) == [("da", "running"), ("en", "queued")])
    time.sleep(0.1)
    assert max(peak) == 1
    tasks.close()
    assert states(tasks) == [("da", "interrupted"), ("en", "interrupted")]
    assert all("Retry" in card["message"] for card in tasks.activity())
    assert not list(service.cache.rglob("installed.json"))
    assert not list(service.cache.rglob("*.partial"))
    assert tasks.automatic() == []
    with pytest.raises(spelling_tasks.Conflict):
        tasks.request(1, consent=True, language="da")


def test_turning_spelling_off_stops_automatic_but_not_consented_downloads(service):
    service, gate, _ = service
    tasks = spelling_tasks.DictionaryTasks(service)
    tasks.automatic()
    wait_for(lambda: states(tasks) == [("da", "running"), ("en", "queued")])
    service.save_settings(1, False, True)
    tasks.preferences_changed()
    wait_for(lambda: states(tasks) == [("da", "cancelled"), ("en", "cancelled")])
    assert not list(service.cache.rglob("installed.json"))
    # Explicit consent downloads even while checking is off (as the earlier explicit route).
    tasks.request(2, consent=True, language="en")
    wait_for(lambda: states(tasks)[-1] == ("en", "running"))
    gate.set()
    wait_for(lambda: states(tasks)[-1] == ("en", "completed"))
    assert service.ready("en") and not service.ready("da")
    tasks.close()


def test_hidden_feature_never_starts_downloads(service):
    service, gate, _ = service
    tasks = spelling_tasks.DictionaryTasks(service, enabled=False)
    assert tasks.automatic() == [] and tasks.preferences_changed() == []
    with pytest.raises(spelling.AdditionsDisabled):
        tasks.request(1, consent=True, language="en")
    assert tasks.activity() == [] and not service.cache.exists()
    tasks.close()
