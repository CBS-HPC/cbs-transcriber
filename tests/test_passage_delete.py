"""Deleting one passage is a reversible-in-history edit, independent of result Trash."""

from copy import deepcopy

import pytest

from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import delete_passage, edit_segment, normalize_segments


@pytest.fixture
def delete_segments():
    passages = normalize_segments(
        [
            {
                "text": "Hej København.",
                "start": 0,
                "end": 2,
                "speaker": "A",
                "words": [
                    {"word": "Hej", "start": 0, "end": 0.8},
                    {"word": "København", "start": 1, "end": 2},
                ],
            },
            {"text": "Et uafklaret ord.", "speaker": "B"},
            {"text": "Tak igen.", "start": 3, "end": 4, "speaker": "A"},
        ]
    )
    # Include independent current/original text, current map marker and unknown metadata.
    passages[0] = edit_segment(passages[0], "word", "Goddag", 0)
    passages[0]["words_current"] = True
    passages[0]["source_context"] = {"preserved": ["æøå", 2]}
    return passages


@pytest.mark.parametrize("index", [0, 1, 2])
def test_delete_retains_every_unselected_dictionary_and_identifier_exactly(delete_segments, index):
    before = deepcopy(delete_segments)
    selected = delete_segments[index]["id"]
    current = delete_passage(delete_segments, selected)
    expected = [passage for passage in delete_segments if passage["id"] != selected]
    assert current == expected
    assert all(actual is original for actual, original in zip(current, expected, strict=True))
    assert delete_segments == before


def test_delete_last_passage_is_an_empty_current_view(delete_segments):
    assert delete_passage(delete_segments[:1], delete_segments[0]["id"]) == []


def test_delete_requires_an_exact_unambiguous_passage(delete_segments):
    with pytest.raises(KeyError):
        delete_passage(delete_segments, "foreign-only")
    with pytest.raises(ValueError, match="ambiguous"):
        delete_passage([delete_segments[0], deepcopy(delete_segments[0])], delete_segments[0]["id"])


@pytest.fixture
def delete_result(client, delete_segments):
    store = client.app.state.store
    project = store.create_project("Passage deletion")
    recording = store.create_recording(project["id"], "synthetic.wav")
    speakers = {"A": "Anna", "B": "Benny"}
    active = store.add_result(
        recording["id"], delete_segments, speakers, "Reviewed", {"kind": "import"}
    )
    other = store.add_result(
        recording["id"], deepcopy(delete_segments), speakers, "Other result", {"kind": "import"}
    )
    foreign_project = store.create_project("Other study")
    foreign_recording = store.create_recording(foreign_project["id"], "other.wav")
    foreign = store.add_result(
        foreign_recording["id"], deepcopy(delete_segments), {}, "Foreign", {"kind": "import"}
    )
    return store.recording(recording["id"]), active, other, foreign


def remove(client, before, segment_id="0", **body):
    return client.post(
        f"/api/recordings/{before['id']}/segments/{segment_id}/delete",
        json={"revision": before["revision"], "result_id": before["active_transcript_id"], **body},
    )


def test_delete_api_preserves_exact_prior_version_and_atomic_result_projection(
    client, delete_result, tmp_path
):
    before, active, other, foreign = delete_result
    store = client.app.state.store
    previous = store.result_versions(before["id"], active["id"])[0]
    deleted_id = before["segments"][0]["id"]
    response = remove(client, before, deleted_id, reason="Exclude an off-topic passage.")
    assert response.status_code == 200, response.text
    current = response.json()
    assert current["segments"] == before["segments"][1:]
    assert current["speakers"] == before["speakers"]
    assert current["revision"] == before["revision"] + 1
    assert current["notes_revision"] == before["notes_revision"]
    assert current["active_transcript_id"] == active["id"]
    assert len(current["transcripts"]) == len(before["transcripts"])
    persisted = Store(tmp_path)
    assert persisted.recording(before["id"]) == current
    assert persisted.result(before["id"], active["id"])["segments"] == current["segments"]
    assert persisted.result(before["id"], other["id"]) == other
    assert persisted.result(foreign["recording_id"], foreign["id"]) == foreign
    versions = persisted.result_versions(before["id"], active["id"])
    assert len(versions) == 2
    assert versions[0]["action"] == "passage_delete"
    exact_prior = persisted.result_version(before["id"], active["id"], previous["id"])
    assert exact_prior["payload"]["segments"] == before["segments"]
    latest = persisted.result_version(before["id"], active["id"], versions[0]["id"])
    assert latest["context"]["segment_id"] == deleted_id
    assert latest["payload"]["segments"] == current["segments"]
    assert persisted.all("SELECT * FROM result_trash") == []


def test_delete_last_api_keeps_saved_result_active_and_its_content_recoverable(client, tmp_path):
    store = client.app.state.store
    project = store.create_project("Single passage")
    recording = store.create_recording(project["id"], "single.wav")
    passages = normalize_segments([{"text": "Det sidste afsnit.", "start": 1, "end": 3}])
    result = store.add_result(
        recording["id"], passages, {"UNKNOWN": "Researcher"}, "Only result", {}
    )
    before = store.recording(recording["id"])
    previous = store.result_versions(recording["id"], result["id"])[0]
    response = remove(client, before)
    assert response.status_code == 200, response.text
    current = response.json()
    assert current["segments"] == []
    assert current["speakers"] == before["speakers"]
    assert current["active_transcript_id"] == result["id"]
    assert len(current["transcripts"]) == 1
    assert store.result(recording["id"], result["id"])["segments"] == []
    assert (
        store.result_version(recording["id"], result["id"], previous["id"])["payload"]["segments"]
        == passages
    )
    assert Store(tmp_path).recording(recording["id"])["active_transcript_id"] == result["id"]
    assert store.all("SELECT * FROM result_trash") == []
    # Retrying an old deletion neither creates a result nor appends another version.
    assert remove(client, before).status_code == 409
    assert len(store.result_versions(recording["id"], result["id"])) == 2


@pytest.mark.parametrize(
    "body,status",
    [
        ({"revision": True}, 422),
        ({"revision": -1}, 422),
        ({"revision": "1"}, 422),
        ({"result_id": ""}, 422),
        ({"result_id": 123}, 422),
        ({"result_id": "unknown-result"}, 409),
        ({"reason": "x" * 2001}, 422),
        ({"delete_everything": True}, 422),
    ],
)
def test_delete_invalid_or_stale_envelopes_cannot_change_the_saved_result(
    client, delete_result, body, status
):
    before, active, _, _ = delete_result
    response = remove(client, before, **body)
    assert response.status_code == status, response.text
    store = client.app.state.store
    assert store.recording(before["id"]) == before
    assert len(store.result_versions(before["id"], active["id"])) == 1


def test_delete_cannot_target_another_result_or_a_foreign_passage(client, delete_result):
    before, active, other, foreign = delete_result
    assert remove(client, before, result_id=other["id"]).status_code == 409
    assert remove(client, before, result_id=foreign["id"]).status_code == 409
    assert remove(client, before, "foreign-only").status_code == 404
    store = client.app.state.store
    assert store.recording(before["id"]) == before
    assert len(store.result_versions(before["id"], active["id"])) == 1


def test_delete_uses_active_revision_after_result_navigation(client, delete_result):
    before, _, other, _ = delete_result
    store = client.app.state.store
    updated = store.activate_result(before["id"], other["id"], before["revision"])
    assert remove(client, before).status_code == 409
    assert store.recording(before["id"]) == updated
    assert len(store.result_versions(before["id"], other["id"])) == 1


def test_delete_rolls_back_both_projections_when_exact_history_write_fails(
    client, delete_result, monkeypatch
):
    before, active, _, _ = delete_result
    store = client.app.state.store

    def fail_append(*args, **kwargs):
        raise RuntimeError("Synthetic exact-history failure")

    monkeypatch.setattr(store, "_append_version", fail_append)
    with pytest.raises(RuntimeError, match="Synthetic exact-history"):
        remove(client, before)
    assert store.recording(before["id"]) == before
    assert store.result(before["id"], active["id"])["segments"] == before["segments"]
    assert len(store.result_versions(before["id"], active["id"])) == 1
