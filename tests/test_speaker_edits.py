import copy

import pytest

from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def speaker_result(client):
    store = client.app.state.store
    project = store.create_project("Speaker review")
    recording = store.create_recording(project["id"], "interview.wav")
    segments = normalize_segments(
        [
            {
                "text": "Hej Benny.",
                "start": 0,
                "end": 2,
                "speaker": "A",
                "words": [
                    {"word": "Hej", "start": 0, "end": 1},
                    {"word": "Benny", "start": 1, "end": 2},
                ],
            },
            {"text": "Hej igen.", "start": 2, "end": 3, "speaker": "B"},
            {"text": "Tak.", "start": 3, "end": 4, "speaker": "A"},
        ]
    )
    store.add_result(
        recording["id"],
        segments,
        {"A": "Interviewer", "B": "Benny", "C": "Previously assigned"},
        "Reviewed result",
        {"kind": "import"},
        activate=True,
        revision=0,
    )
    other = store.add_result(
        recording["id"],
        copy.deepcopy(segments),
        {"A": "Another result name"},
        "Independent result",
        {"kind": "import"},
    )
    return store.recording(recording["id"]), other


def assign(client, recording, **body):
    return client.patch(
        f"/api/recordings/{recording['id']}/segments/0/speaker",
        json={"revision": recording["revision"], **body},
    )


@pytest.mark.parametrize("speaker", ["B", "C"])
def test_assign_existing_speaker_changes_only_selected_passage(
    client, speaker_result, tmp_path, speaker
):
    before, other = speaker_result
    response = assign(client, before, speaker_id=speaker)
    assert response.status_code == 200, response.text
    current = response.json()
    expected = copy.deepcopy(before["segments"])
    expected[0]["speaker"] = speaker
    assert current["segments"] == expected
    assert current["speakers"] == before["speakers"]
    assert current["revision"] == before["revision"] + 1
    store = Store(tmp_path)
    active = store.result(current["id"], current["active_transcript_id"])
    assert active["segments"] == expected
    assert active["speakers"] == current["speakers"]
    assert store.result(current["id"], other["id"]) == other
    assert store.recording(current["id"])["segments"] == expected


def test_add_new_speaker_retains_names_words_originals_and_independent_results(
    client, speaker_result
):
    before, other = speaker_result
    response = assign(client, before, name="  Øyvind Æble  ")
    assert response.status_code == 200, response.text
    current = response.json()
    speaker = current["segments"][0]["speaker"]
    assert speaker.startswith("MANUAL_")
    assert speaker not in before["speakers"]
    assert current["speakers"] == {**before["speakers"], speaker: "Øyvind Æble"}
    expected = copy.deepcopy(before["segments"])
    expected[0]["speaker"] = speaker
    assert current["segments"] == expected
    assert client.app.state.store.result(current["id"], other["id"]) == other
    renamed = client.patch(
        f"/api/recordings/{current['id']}/speakers",
        json={"revision": current["revision"], "speaker": speaker, "name": "New display name"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["segments"] == expected
    assert renamed.json()["speakers"][speaker] == "New display name"


def test_stale_speaker_edit_cannot_change_a_newer_result(client, speaker_result):
    before, _ = speaker_result
    updated = assign(client, before, speaker_id="B").json()
    rejected = assign(client, before, name="Must not be saved")
    assert rejected.status_code == 409
    assert client.app.state.store.recording(before["id"]) == updated


@pytest.mark.parametrize(
    "body,status",
    [
        ({}, 400),
        ({"speaker_id": "A", "name": "Both supplied"}, 400),
        ({"speaker_id": "NOT_IN_THIS_RESULT"}, 400),
        ({"name": ""}, 400),
        ({"name": " \t\n "}, 400),
        ({"name": "x" * 201}, 422),
        ({"name": 123}, 422),
        ({"speaker_id": ""}, 422),
    ],
)
def test_invalid_speaker_requests_leave_the_result_unchanged(client, speaker_result, body, status):
    before, _ = speaker_result
    response = assign(client, before, **body)
    assert response.status_code == status, response.text
    assert client.app.state.store.recording(before["id"]) == before


def test_speaker_assignment_cannot_reach_another_recordings_passage(client, speaker_result):
    before, _ = speaker_result
    store = client.app.state.store
    project = store.create_project("Other project")
    foreign = store.create_recording(project["id"], "other.wav")
    foreign_segments = normalize_segments([{"text": "Another recording", "speaker": "FOREIGN"}])
    foreign_segments[0]["id"] = "foreign-only"
    store.save_transcript(foreign["id"], foreign_segments, {"FOREIGN": "Other person"}, 0)
    foreign_before = store.recording(foreign["id"])
    response = client.patch(
        f"/api/recordings/{before['id']}/segments/foreign-only/speaker",
        json={"revision": before["revision"], "name": "Wrong recording"},
    )
    assert response.status_code == 404
    assert assign(client, before, speaker_id="FOREIGN").status_code == 400
    assert store.recording(before["id"]) == before
    assert store.recording(foreign["id"]) == foreign_before
