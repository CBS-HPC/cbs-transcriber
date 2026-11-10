"""Scoped read-only history and explicit report download boundaries."""

from copy import deepcopy

import pytest

from cbs_transcriber.exports import export_bytes
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def history_items(client):
    store = client.app.state.store
    projects = [store.create_project(name) for name in ("Private study", "Other study")]
    recordings = [store.create_recording(project["id"], "private.wav") for project in projects]
    segments = normalize_segments(
        [
            {
                "text": "Hej København.",
                "start": 0,
                "end": 2,
                "speaker": "A",
                "words": [
                    {"word": "Hej", "start": 0, "end": 1},
                    {"word": "København.", "start": 1, "end": 2},
                ],
            }
        ]
    )
    results = [
        store.add_result(
            recording["id"], deepcopy(segments), {"A": "Søren"}, "Private label", {"kind": "import"}
        )
        for recording in recordings
    ]
    return store, projects, recordings, results, segments


def version_url(recording, result):
    return f"/api/recordings/{recording['id']}/transcripts/{result['id']}/versions"


def test_history_is_scoped_paginated_and_exact_export_survives_edits(client, history_items):
    store, _, recordings, results, segments = history_items
    url = version_url(recordings[0], results[0])
    first = client.get(url).json()[0]
    before = store.recording(recordings[0]["id"])
    changed = client.patch(
        f"/api/recordings/{recordings[0]['id']}/segments/0",
        json={
            "revision": before["revision"],
            "mode": "free",
            "text": '<img onerror="x"> Ændret.',
            "reason": "Private reason",
        },
    )
    assert changed.status_code == 200, changed.text
    latest = client.get(url, params={"limit": 1}).json()
    assert latest[0]["sequence"] == first["sequence"] + 1
    assert latest[0]["action"] == "free_rewrite"
    assert not {"payload", "segments", "speakers", "reason", "context"} & latest[0].keys()
    earlier = client.get(url, params={"before_sequence": latest[0]["sequence"], "limit": 1}).json()
    assert earlier == [first]
    exact = client.get(url + "/" + first["id"])
    assert exact.status_code == 200
    assert exact.json()["payload"]["segments"] == segments
    historical = client.get(url + "/" + first["id"] + "/export")
    assert historical.content == export_bytes(segments, {"A": "Søren"}, "speaker", "text")
    assert "<img" not in historical.text
    assert store.recording(recordings[0]["id"]) == changed.json()
    assert len(client.get(url).json()) == 2
    foreign = version_url(recordings[1], results[0])
    for suffix in ("", "/" + first["id"], "/" + first["id"] + "/export"):
        assert client.get(foreign + suffix).status_code == 404
    assert client.get(version_url(recordings[0], results[1])).status_code == 404


@pytest.mark.parametrize("query", [{"limit": 0}, {"limit": 101}, {"before_sequence": 0}])
def test_history_pagination_is_bounded(client, history_items, query):
    _, _, recordings, results, _ = history_items
    assert client.get(version_url(recordings[0], results[0]), params=query).status_code == 422


@pytest.mark.parametrize("scope,index", [("projects", 1), ("recordings", 2)])
def test_note_history_omits_text_and_reason_and_preserves_revision_domains(
    client, history_items, scope, index
):
    store = history_items[0]
    item = history_items[index][0]
    recording_before = store.recording(history_items[2][0]["id"])
    url = f"/api/{scope}/{item['id']}/notes"
    response = client.patch(
        url, json={"text": "Private research note", "revision": 0, "reason": "Private note reason"}
    )
    assert response.status_code == 200
    versions = client.get(url + "/versions", params={"limit": 1}).json()
    assert len(versions) == 1 and versions[0]["notes_revision"] == 1
    assert not {"text", "notes", "reason", "context"} & versions[0].keys()
    assert "Private" not in str(versions)
    assert client.get(url + "/versions?before_revision=1").json()[0]["notes_revision"] == 0
    assert client.get(url + "/versions?before_revision=0").status_code == 422
    recording_after = store.recording(history_items[2][0]["id"])
    assert recording_after["revision"] == recording_before["revision"]
    assert client.get(f"/api/{scope}/missing/notes/versions").status_code == 404


@pytest.mark.parametrize("reason", [42, True, ["bad"], "x" * 2001])
def test_optional_edit_reasons_are_strict_and_bounded(client, history_items, reason):
    _, projects, recordings, _, _ = history_items
    rid = recordings[0]["id"]
    revision = client.get(f"/api/recordings/{rid}").json()["revision"]
    requests = [
        (f"/api/projects/{projects[0]['id']}/notes", {"text": "", "revision": 0}),
        (f"/api/recordings/{rid}/notes", {"text": "", "revision": 0}),
        (
            f"/api/recordings/{rid}/segments/0",
            {"revision": revision, "mode": "free", "text": "New"},
        ),
        (f"/api/recordings/{rid}/segments/0/speaker", {"revision": revision, "name": "New"}),
        (f"/api/recordings/{rid}/speakers", {"revision": revision, "speaker": "A", "name": "New"}),
    ]
    for url, body in requests:
        assert client.patch(url, json={**body, "reason": reason}).status_code == 422


def test_api_actions_are_owned_by_handlers_and_stale_edits_append_nothing(client, history_items):
    store, _, recordings, results, _ = history_items
    rid = recordings[0]["id"]
    url = version_url(recordings[0], results[0])
    requests = [
        (
            f"/api/recordings/{rid}/segments/0",
            {"mode": "word", "word_index": 0, "text": "Goddag"},
            "word_correction",
        ),
        (f"/api/recordings/{rid}/segments/0", {"mode": "restore"}, "original_restore"),
        (f"/api/recordings/{rid}/segments/0/speaker", {"name": "Benny"}, "passage_speaker"),
        (f"/api/recordings/{rid}/speakers", {"speaker": "A", "name": "Asta"}, "speaker_rename"),
    ]
    for path, body, action in requests:
        # Rename the currently assigned speaker after the passage-speaker change.
        current = store.recording(rid)
        if "speaker" in body:
            body["speaker"] = current["segments"][0]["speaker"]
        response = client.patch(
            path,
            json={
                **body,
                "revision": current["revision"],
                "reason": "Reviewed wording",
                "action": "legacy_observed",
            },
        )
        assert response.status_code == 200, response.text
        assert client.get(url).json()[0]["action"] == action
        count = len(client.get(url).json())
        assert client.patch(path, json={**body, "revision": current["revision"]}).status_code == 409
        assert len(client.get(url).json()) == count


def test_report_route_passes_explicit_selection_and_strict_inclusion(
    client, history_items, monkeypatch
):
    from cbs_transcriber import research_reports

    _, projects, recordings, results, _ = history_items
    version = client.get(version_url(recordings[0], results[0])).json()[0]
    calls = []

    def report(store, identifier, selections=None, include_selected_transcripts=False):
        calls.append((identifier, selections, include_selected_transcripts))
        return b"synthetic zip"

    monkeypatch.setattr(research_reports, "project_report", report)
    url = f"/api/projects/{projects[0]['id']}/research-report"
    response = client.post(url, json={})
    assert response.status_code == 200 and response.content == b"synthetic zip"
    assert response.headers["content-type"] == "application/zip"
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="project-research-report.zip"'
    )
    assert calls[-1] == (projects[0]["id"], None, False)
    selection = {
        "recording_id": recordings[0]["id"],
        "result_id": results[0]["id"],
        "version_id": version["id"],
    }
    assert (
        client.post(
            url, json={"selections": [selection], "include_selected_transcripts": True}
        ).status_code
        == 200
    )
    assert calls[-1][1:] == ([selection], True)
    for body in (
        {"include_selected_transcripts": "true"},
        {"selections": []},
        {"selections": [selection] * 101},
    ):
        assert client.post(url, json=body).status_code == 422


def test_report_route_rejects_foreign_versions_and_leaves_history_unchanged(client, history_items):
    store, projects, recordings, results, _ = history_items
    other_version = client.get(version_url(recordings[1], results[1])).json()[0]
    url = f"/api/projects/{projects[0]['id']}/research-report"
    original = store.recording(recordings[0]["id"])
    versions = client.get(version_url(recordings[0], results[0])).json()
    foreign = {
        "recording_id": recordings[1]["id"],
        "result_id": results[1]["id"],
        "version_id": other_version["id"],
    }
    assert client.post(url, json={"selections": [foreign]}).status_code == 404
    foreign["recording_id"] = recordings[0]["id"]
    assert client.post(url, json={"selections": [foreign]}).status_code == 404
    foreign["result_id"] = results[0]["id"]
    assert client.post(url, json={"selections": [foreign]}).status_code == 404
    downloaded = client.post(url, json={})
    assert downloaded.status_code == 200
    assert store.recording(recordings[0]["id"]) == original
    assert client.get(version_url(recordings[0], results[0])).json() == versions
