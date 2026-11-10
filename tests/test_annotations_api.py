"""Local annotation API boundaries; synthetic evidence only, with no provider execution."""

from copy import deepcopy

import pytest

from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def annotation_items(client):
    store = client.app.state.store
    projects = [store.create_project(name, "protected") for name in ("Study", "Other study")]
    recordings = [store.create_recording(project["id"], "synthetic.wav") for project in projects]
    segments = normalize_segments(
        [
            {
                "text": "😀 Jeg kan lide fleksibiliteten, men systemet er langsomt.",
                "start": 1,
                "end": 5,
                "speaker": "A",
            },
            {"text": "Ingen timing her. e\u0301", "speaker": "B"},
        ]
    )
    results = [
        store.add_result(
            recording["id"],
            deepcopy(segments),
            {"A": "Researcher", "B": "Participant"},
            "Synthetic",
            {"kind": "import"},
        )
        for recording in recordings
    ]
    return store, projects, recordings, results, segments


def create_scope(client, items, index=0):
    _, projects, recordings, results, _ = items
    project, recording, result = projects[index], recordings[index], results[index]
    codebooks = f"/api/projects/{project['id']}/codebook/versions"
    response = client.post(
        codebooks,
        json={
            "revision": 0,
            "payload": {
                "format": "cbs-topic-codebook-v1",
                "approved": True,
                "topics": [
                    {
                        "id": "flexibility",
                        "name": "Fleksibilitet",
                        "definition": "Evaluation of flexibility",
                    },
                    {
                        "id": "performance",
                        "name": "Performance",
                        "definition": "Evaluation of speed",
                    },
                ],
            },
        },
    )
    assert response.status_code == 201, response.text
    codebook = response.json()
    versions = client.get(
        f"/api/recordings/{recording['id']}/transcripts/{result['id']}/versions"
    ).json()
    runs = f"/api/recordings/{recording['id']}/annotation-runs"
    response = client.post(
        runs,
        json={
            "result_id": result["id"],
            "source_version_id": versions[0]["id"],
            "codebook_version_id": codebook["id"],
            "request_id": "new-run",
        },
    )
    assert response.status_code == 201, response.text
    return codebooks, codebook, runs, response.json(), versions[0]


def evidence(items, topic="flexibility", polarity="positive"):
    text = items[4][0]["text"]
    quote = "kan lide fleksibiliteten"
    start = text.index(quote)
    return {
        "topic_id": topic,
        "polarity": polarity,
        "attribution": "self",
        "evidence": [
            {
                "passage_id": "0",
                "start_char": start,
                "end_char": start + len(quote),
                "exact_text": quote,
            }
        ],
    }


def test_manual_review_export_restart_and_transcript_isolation(client, annotation_items):
    store, _, recordings, results, _ = annotation_items
    before = store.recording(recordings[0]["id"])
    versions_before = store.result_versions(recordings[0]["id"], results[0]["id"])
    _, _, runs, run, _ = create_scope(client, annotation_items)
    url = runs + "/" + run["id"]
    response = client.post(
        url + "/items",
        json={
            "payload": evidence(annotation_items),
            "request_id": "manual-item",
            "reason": "Private rationale",
        },
    )
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["revision"] == 1 and item["review_state"] == "accepted"
    metadata = client.get(url + "/items").json()
    assert len(metadata) == 1
    assert not {"payload", "current", "suggestion", "reason", "evidence"} & metadata[0].keys()
    assert "Private rationale" not in str(metadata)
    changed = evidence(annotation_items, polarity="mixed")
    response = client.patch(
        url + "/items/" + item["id"],
        json={
            "revision": 1,
            "action": "change",
            "payload": changed,
            "request_id": "review-change",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2
    response = client.patch(
        url + "/items/" + item["id"],
        json={
            "revision": 2,
            "action": "accept",
            "request_id": "review-accept",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["polarity"] == "mixed"
    events = client.get(url + "/items/" + item["id"] + "/reviews").json()
    assert len(events) == 3
    assert all("payload" not in event and "reason" not in event for event in events)
    exported = client.get(url + "/export")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("application/json")
    assert "attachment" in exported.headers["content-disposition"]
    assert "kan lide fleksibiliteten" in exported.text
    assert "Private rationale" not in exported.text
    assert "Ingen timing her" not in exported.text
    assert "synthetic.wav" not in exported.text
    assert store.recording(recordings[0]["id"]) == before
    assert store.result_versions(recordings[0]["id"], results[0]["id"]) == versions_before
    from cbs_transcriber.store import Store

    reopened = Store(store.root)
    assert reopened.annotation_item(recordings[0]["id"], run["id"], item["id"])["revision"] == 3


def test_annotation_and_codebook_access_are_owner_scoped(client, annotation_items):
    _, projects, recordings, _, _ = annotation_items
    _, codebook, runs, run, _ = create_scope(client, annotation_items)
    url = runs + "/" + run["id"]
    item = client.post(
        url + "/items",
        json={
            "payload": evidence(annotation_items),
            "request_id": "item",
        },
    ).json()
    reviews = client.get(url + "/items/" + item["id"] + "/reviews").json()
    wrong = f"/api/recordings/{recordings[1]['id']}/annotation-runs/{run['id']}"
    for suffix in (
        "",
        "/items",
        "/export",
        "/items/" + item["id"],
        "/items/" + item["id"] + "/reviews",
        "/items/" + item["id"] + "/reviews/" + reviews[0]["id"],
    ):
        assert client.get(wrong + suffix).status_code == 404
    assert (
        client.post(
            wrong + "/items",
            json={
                "payload": evidence(annotation_items),
                "request_id": "bad-owner",
            },
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/projects/{projects[1]['id']}/codebook/versions/{codebook['id']}"
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "body",
    [
        {"revision": True, "action": "accept", "request_id": "a"},
        {"revision": 1, "action": "automatic", "request_id": "a"},
        {"revision": 1, "action": "accept", "request_id": "a", "api_key": "SECRET"},
        {"revision": 1, "action": "accept", "request_id": ""},
    ],
)
def test_invalid_annotation_fields_do_not_echo_input(client, annotation_items, body):
    _, _, runs, run, _ = create_scope(client, annotation_items)
    response = client.patch(runs + "/" + run["id"] + "/items/missing", json=body)
    assert response.status_code == 422
    assert "SECRET" not in response.text


@pytest.mark.parametrize(
    "data,status",
    [
        (b'{"bundle":', 422),
        (b'{"bundle":{},"request_id":"a","request_id":"b"}', 422),
        (b'{"bundle":{"value":NaN},"request_id":"a"}', 422),
        (b'{"bundle":{},"request_id":true}', 422),
        (b" " * (2 * 1024 * 1024 + 1), 413),
    ],
    ids=["malformed", "duplicate-key", "nonfinite", "boolean-id", "oversized"],
)
def test_suggestion_json_is_bounded_and_strict_before_parsing(
    client, annotation_items, data, status
):
    rid = annotation_items[2][0]["id"]
    response = client.post(
        f"/api/recordings/{rid}/annotation-runs/import",
        content=data,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == status, response.text
    assert "NaN" not in response.text


def test_annotation_mutations_preserve_local_origin_boundary(client, annotation_items):
    pid = annotation_items[1][0]["id"]
    response = client.post(
        f"/api/projects/{pid}/codebook/versions",
        json={"payload": {}, "revision": 0},
        headers={"Origin": "https://other.example"},
    )
    assert response.status_code == 403


def test_imported_suggestions_are_unverified_unreviewed_and_retries_preserve_review(
    client, annotation_items
):
    _, _, runs, run, version = create_scope(client, annotation_items)
    bundle = {
        "format": "cbs-topic-suggestions-v1",
        "result_id": run["result_id"],
        "source_version_id": version["id"],
        "source_payload_sha256": version["payload_sha256"],
        "codebook_version_id": run["codebook_version_id"],
        "codebook_payload_sha256": run["codebook_payload_sha256"],
        "suggestions": [{"id": "candidate-1", "annotation": evidence(annotation_items)}],
        "producer": {"model": "Declared model, unverified", "revision": "unverified revision"},
    }
    request = {"bundle": bundle, "request_id": "import-1"}
    response = client.post(runs + "/import", json=request)
    assert response.status_code == 201, response.text
    imported = response.json()
    url = runs + "/" + imported["id"]
    item = client.get(url + "/items").json()[0]
    assert item["review_state"] == "unreviewed" and item["revision"] == 0
    exact = client.get(url + "/items/" + item["id"]).json()
    assert exact["current"] is None and exact["suggestion"]["polarity"] == "positive"
    assert client.get(url + "/items/" + item["id"] + "/reviews").json() == []
    changed = evidence(annotation_items, polarity="negative")
    response = client.patch(
        url + "/items/" + item["id"],
        json={
            "revision": 0,
            "action": "change",
            "payload": changed,
            "request_id": "change-import",
        },
    )
    assert response.status_code == 200, response.text
    retry = client.post(runs + "/import", json=request)
    assert retry.status_code == 201 and retry.json()["id"] == imported["id"]
    exact = client.get(url + "/items/" + item["id"]).json()
    assert exact["current"]["polarity"] == "negative"
    assert exact["suggestion"]["polarity"] == "positive"
    conflicting = deepcopy(request)
    conflicting["bundle"]["suggestions"][0]["annotation"]["polarity"] = "mixed"
    response = client.post(runs + "/import", json=conflicting)
    assert response.status_code == 400
    exported = client.get(url + "/export")
    assert "unverified" in exported.text
    assert "Declared model" not in exported.text
    assert '"negative"' in exported.text


def test_frozen_annotation_run_is_marked_older_after_text_or_codebook_changes(
    client, annotation_items
):
    _, _, recordings, _, _ = annotation_items
    books_url, book, runs, run, _ = create_scope(client, annotation_items)
    rid = recordings[0]["id"]
    current = client.get(f"/api/recordings/{rid}").json()
    response = client.patch(
        f"/api/recordings/{rid}/segments/0",
        json={
            "revision": current["revision"],
            "mode": "free",
            "text": "New reviewed wording.",
        },
    )
    assert response.status_code == 200, response.text
    payload = client.get(books_url + "/" + book["id"]).json()["payload"]
    payload["topics"][0]["definition"] = "A changed definition"
    response = client.post(books_url, json={"revision": book["sequence"], "payload": payload})
    assert response.status_code == 201, response.text
    response = client.get(runs + "/" + run["id"])
    assert response.json()["source_stale"] is True
    assert response.json()["codebook_stale"] is True
    # The same original evidence remains valid for this older, explicitly bound run.
    response = client.post(
        runs + "/" + run["id"] + "/items",
        json={
            "payload": evidence(annotation_items),
            "request_id": "older-evidence",
        },
    )
    assert response.status_code == 201, response.text
    assert (
        client.get(f"/api/recordings/{rid}").json()["segments"][0]["text"]
        == "New reviewed wording."
    )


@pytest.mark.parametrize("fault", ["hash", "quote", "boolean_offset", "unknown_topic"])
def test_invalid_suggestion_batch_is_atomic_and_errors_do_not_quote_evidence(
    client, annotation_items, fault
):
    _, _, runs, run, version = create_scope(client, annotation_items)
    valid = evidence(annotation_items)
    invalid = deepcopy(valid)
    bundle = {
        "format": "cbs-topic-suggestions-v1",
        "result_id": run["result_id"],
        "source_version_id": version["id"],
        "source_payload_sha256": version["payload_sha256"],
        "codebook_version_id": run["codebook_version_id"],
        "codebook_payload_sha256": run["codebook_payload_sha256"],
        "suggestions": [
            {"id": "valid", "annotation": valid},
            {"id": "invalid", "annotation": invalid},
        ],
    }
    if fault == "hash":
        bundle["source_payload_sha256"] = "0" * 64
    elif fault == "quote":
        invalid["evidence"][0]["exact_text"] = "PRIVATE-INVENTED-EVIDENCE"
    elif fault == "boolean_offset":
        invalid["evidence"][0]["start_char"] = True
    else:
        invalid["topic_id"] = "not-in-codebook"
    before = client.get(runs).json()
    response = client.post(runs + "/import", json={"bundle": bundle, "request_id": "invalid-batch"})
    assert response.status_code == 400, response.text
    assert "PRIVATE-INVENTED-EVIDENCE" not in response.text
    assert "kan lide fleksibiliteten" not in response.text
    assert client.get(runs).json() == before
