"""Recording vocabulary (ADR 0186): inherits project words, own CAS, live owner and Delete fence.

Synthetic libraries, a fake worker and mocked downloads only; nothing leaves this computer.
"""

import json

import pytest
import test_spelling
from fastapi import HTTPException
from test_spelling import enable, install

from cbs_transcriber import spelling
from cbs_transcriber.deletions import Deletions
from cbs_transcriber.jobs import JobQueue

ROW = spelling.VOCABULARY_ID
local_spelling = test_spelling.local_spelling


def stored(store):
    row = store.one(f"SELECT value FROM settings WHERE id={ROW}")
    return None if row is None else json.loads(row["value"])


def test_recording_words_inherit_project_words_and_schema_two_only_when_needed(local_spelling):
    service, store, project, recording, worker, _ = local_spelling
    enable(service)
    install(service, recording)
    rid = recording["id"]
    service.save_vocabulary(0, ["Alpha"], project["id"])
    # No recording words yet: an earlier v2 build still reads this row (schema 1).
    assert stored(store) == {
        "schema": 1,
        "personal": {"revision": 0, "words": []},
        "projects": {project["id"]: {"revision": 1, "words": ["Alpha"]}},
    }
    assert service.vocabulary(recording_id=rid) == {
        "revision": 0,
        "words": [],
        "project": {"id": project["id"], "revision": 1, "words": ["Alpha"]},
    }
    assert service.save_vocabulary(0, ["Beta"], recording_id=rid) == {
        "revision": 1,
        "words": ["Beta"],
    }
    value = stored(store)
    assert value["schema"] == 2 and value["recordings"] == {rid: {"revision": 1, "words": ["Beta"]}}
    # Checking uses the union of library, project and recording words.
    service.save_vocabulary(0, ["Delta"])
    result = service.check(rid, "Alpha BETA Delta Gamma", "one")
    assert [flag["word"] for flag in result["flags"]] == ["Gamma"]
    assert worker.calls[-1] == ("en", "check", {"words": ["Gamma"]})
    assert result["vocabulary_revision"] == {"personal": 1, "project": 1, "recording": 1}
    # Another recording of the same project inherits only the project's words.
    other = store.create_recording(project["id"], "other.wav")["id"]
    flagged = service.check(other, "Alpha Beta", "two")["flags"]
    assert [flag["word"] for flag in flagged] == ["Beta"]
    # Emptying the words keeps the entry and its revision for the next CAS.
    assert service.save_vocabulary(1, [], recording_id=rid) == {"revision": 2, "words": []}
    assert stored(store)["recordings"] == {rid: {"revision": 2, "words": []}}


def test_recording_vocabulary_cas_live_owner_and_hidden_additions(local_spelling):
    service, store, project, recording, _, _ = local_spelling
    rid = recording["id"]
    service.save_vocabulary(0, ["Kept", "Removed"], recording_id=rid)
    with pytest.raises(spelling.Conflict):
        service.save_vocabulary(0, ["Other"], recording_id=rid)
    with pytest.raises(KeyError):
        service.save_vocabulary(0, ["Other"], recording_id="f" * 32)
    with pytest.raises(KeyError):
        service.vocabulary(recording_id="f" * 32)
    with pytest.raises(ValueError):
        service.save_vocabulary(1, ["Other"], project["id"], rid)
    before = stored(store)
    with pytest.raises(spelling.AdditionsDisabled):
        service.save_vocabulary(1, ["Kept", "Added"], recording_id=rid, additions=False)
    assert stored(store) == before
    assert service.save_vocabulary(1, ["kept"], recording_id=rid, additions=False) == {
        "revision": 2,
        "words": ["kept"],
    }


def test_recording_vocabulary_change_refuses_a_late_check(local_spelling):
    service, _, _, recording, worker, _ = local_spelling
    enable(service)
    install(service, recording)
    worker.before_return = lambda: service.save_vocabulary(
        0, ["wrng"], recording_id=recording["id"]
    )
    with pytest.raises(spelling.Conflict, match="changed"):
        service.check(recording["id"], "wrng", "one")


@pytest.mark.parametrize(
    "damage",
    ["empty_schema_two", "schema_three", "orphan", "bad_identifier", "recordings_in_one"],
)
def test_malformed_recording_vocabulary_is_refused(local_spelling, damage):
    service, store, _, recording, _, _ = local_spelling
    value = {
        "schema": 2,
        "personal": {"revision": 0, "words": []},
        "projects": {},
        "recordings": {recording["id"]: {"revision": 1, "words": ["Name"]}},
    }
    if damage == "empty_schema_two":
        value["recordings"] = {}
    elif damage == "schema_three":
        value["schema"] = 3
    elif damage == "orphan":
        value["recordings"]["f" * 32] = {"revision": 0, "words": []}
    elif damage == "bad_identifier":
        value["recordings"]["NOT-AN-ID"] = {"revision": 0, "words": []}
    else:
        value["schema"] = 1
    store.execute(f"INSERT INTO settings VALUES ({ROW},?)", (json.dumps(value),))
    with pytest.raises(ValueError):
        service.vocabulary(recording_id=recording["id"])


def deletes(store):
    return Deletions(store, JobQueue(store, None), acquire_file_lease=True)


def test_recording_delete_removes_only_its_words_and_returns_to_schema_one(local_spelling):
    service, store, project, recording, _, _ = local_spelling
    rid = recording["id"]
    other = store.create_recording(project["id"], "other.wav")["id"]
    service.save_vocabulary(0, ["Personal"])
    service.save_vocabulary(0, ["Project"], project["id"])
    service.save_vocabulary(0, ["Mine"], recording_id=rid)
    service.save_vocabulary(0, ["Theirs"], recording_id=other)
    manager = deletes(store)
    preview = manager.preview("recording", rid)
    status, response = manager.request("recording", rid, operation_id=preview["operation_id"])
    assert status == 200 and response["ok"] is True
    value = stored(store)
    assert value["recordings"] == {other: {"revision": 1, "words": ["Theirs"]}}
    assert value["projects"][project["id"]]["words"] == ["Project"]
    assert value["personal"]["words"] == ["Personal"]
    preview = manager.preview("recording", other)
    manager.request("recording", other, operation_id=preview["operation_id"])
    # The last recording words went: older v2 readers see schema 1 again.
    assert stored(store) == {
        "schema": 1,
        "personal": {"revision": 1, "words": ["Personal"]},
        "projects": {project["id"]: {"revision": 1, "words": ["Project"]}},
    }


def test_recording_words_changed_after_delete_preview_keep_the_recording(local_spelling):
    service, store, _, recording, _, _ = local_spelling
    rid = recording["id"]
    manager = deletes(store)
    preview = manager.preview("recording", rid)
    service.save_vocabulary(0, ["LaterName"], recording_id=rid)
    with pytest.raises(HTTPException) as caught:
        manager.request("recording", rid, operation_id=preview["operation_id"])
    assert caught.value.status_code == 409
    assert stored(store)["recordings"] == {rid: {"revision": 1, "words": ["LaterName"]}}
    assert store.one("SELECT id FROM recordings WHERE id=?", (rid,))


def test_unrelated_recording_words_changed_after_preview_do_not_block(local_spelling):
    service, store, project, recording, _, _ = local_spelling
    other = store.create_recording(project["id"], "other.wav")["id"]
    manager = deletes(store)
    preview = manager.preview("recording", recording["id"])
    service.save_vocabulary(0, ["Unrelated"], recording_id=other)
    status, _ = manager.request("recording", recording["id"], operation_id=preview["operation_id"])
    assert status == 200
    assert stored(store)["recordings"] == {other: {"revision": 1, "words": ["Unrelated"]}}


def test_project_delete_removes_its_recordings_words(local_spelling):
    service, store, project, recording, _, _ = local_spelling
    keep = store.create_project("Kept project", default_language="en")
    kept = store.create_recording(keep["id"], "kept.wav")["id"]
    service.save_vocabulary(0, ["Project"], project["id"])
    service.save_vocabulary(0, ["Gone"], recording_id=recording["id"])
    service.save_vocabulary(0, ["Kept"], recording_id=kept)
    manager = deletes(store)
    preview = manager.preview("project", project["id"])
    status, _ = manager.request("project", project["id"], operation_id=preview["operation_id"])
    assert status == 200
    assert stored(store) == {
        "schema": 2,
        "personal": {"revision": 0, "words": []},
        "projects": {},
        "recordings": {kept: {"revision": 1, "words": ["Kept"]}},
    }
