"""Opt-in realignment of edited passages as timing-only versions of the same result (T69)."""

import json
import threading
import wave
from pathlib import Path

import pytest

from cbs_transcriber import enhancement, media
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def edited(client, monkeypatch):
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    store = client.app.state.store
    project = store.create_project("Realign")
    recording = store.create_recording(project["id"], "a.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=300 WHERE id=?", (rid,))
    result = store.add_result(
        rid,
        normalize_segments(
            [
                {
                    "text": "Keep this.",
                    "start": 0,
                    "end": 2,
                    "words": [
                        {"word": "Keep", "start": 0, "end": 1},
                        {"word": "this.", "start": 1, "end": 2},
                    ],
                },
                {
                    "text": "Hej verden.",
                    "start": 10,
                    "end": 14,
                    "words": [
                        {"word": "Hej", "start": 10, "end": 11},
                        {"word": "verden.", "start": 11, "end": 14},
                    ],
                },
                {"text": "No bounds.", "start": None, "end": None},
            ]
        ),
        {},
        "Word model",
        {"capability": "words"},
    )
    return client, store, rid, result


def edit(client, store, rid, segment_id, text):
    revision = store.recording(rid)["revision"]
    response = client.patch(
        f"/api/recordings/{rid}/segments/{segment_id}",
        json={"revision": revision, "mode": "free", "text": text},
    )
    assert response.status_code == 200, response.text
    return response.json()


def realign_jobs(store, state=None):
    rows = store.all(
        "SELECT id,state FROM jobs WHERE kind='enhance' AND json_extract(params,'$.realign')=1"
    )
    return [row for row in rows if state is None or row["state"] == state]


def run_worker_for(client, store, job_id, monkeypatch):
    def convert(args, cancel):
        duration = float(args[args.index("-t") + 1])
        with wave.open(str(Path(args[-1])), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\0\0" * int(duration * 16000))

    def process(folder, options, token, cancel, progress, **kw):
        request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
        assert request["passages"] == [
            {"id": "1", "text": "Hej hele verden.", "start": 0, "end": 4}
        ]
        assert not options.diarize and not options.allow_downloads
        enhancement.write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": "1",
                        "words": [
                            {"word": "Hej", "start": 0.1, "end": 0.9, "score": 0.9},
                            {"word": "hele", "start": 1.0, "end": 1.8, "score": 0.9},
                            {"word": "verden.", "start": 2.0, "end": 3.5, "score": 0.9},
                        ],
                    }
                ]
            },
        )

    monkeypatch.setattr(media, "ffmpeg", convert)
    monkeypatch.setattr(enhancement, "run_worker", process)
    queue = client.app.state.queue
    queue.cancel_event = threading.Event()
    store.update_job(job_id, state="running")
    queue.process(job_id)
    store.update_job(job_id, state="completed")


def test_edits_queue_nothing_until_realignment_is_enabled(edited):
    client, store, rid, _ = edited
    assert client.get("/api/editing-settings").json() == {"realign_edits": False}
    edit(client, store, rid, "1", "Hej hele verden.")
    assert realign_jobs(store) == []
    assert client.put("/api/editing-settings", json={"realign_edits": "yes"}).status_code == 422
    assert client.put("/api/editing-settings", json={"realign_edits": True}).json() == {
        "realign_edits": True
    }


def test_per_save_realign_choice_is_not_changed_by_another_tabs_setting(edited):
    client, store, rid, _ = edited

    def save(text, choice=...):
        body = {"revision": store.recording(rid)["revision"], "mode": "free", "text": text}
        if choice is not ...:
            body["realign"] = choice
        suffix = "/edit-v1" if choice is not ... else ""
        response = client.patch(f"/api/recordings/{rid}/segments/1{suffix}", json=body)
        assert response.status_code == 200, response.text
        return response.json()

    # A different tab turns the library default on before this tab saves an
    # explicitly unchecked passage: no job should be queued or cancelled.
    client.put("/api/editing-settings", json={"realign_edits": True}).raise_for_status()
    save("First manual edit.", False)
    assert realign_jobs(store) == []
    # A different tab turns the default off before an explicitly checked save.
    client.put("/api/editing-settings", json={"realign_edits": False}).raise_for_status()
    save("Second manual edit.", True)
    assert [job["state"] for job in realign_jobs(store)] == ["queued"]
    save("Third manual edit.", False)
    assert [job["state"] for job in realign_jobs(store)] == ["queued"]
    # Omitted field retains old clients' saved library-wide preference.
    save("Fourth manual edit.")
    assert [job["state"] for job in realign_jobs(store)] == ["queued"]
    client.put("/api/editing-settings", json={"realign_edits": True}).raise_for_status()
    save("Fifth manual edit.")
    assert [job["state"] for job in realign_jobs(store)] == ["cancelled", "queued"]
    revision = store.recording(rid)["revision"]
    invalid = client.patch(
        f"/api/recordings/{rid}/segments/1/edit-v1",
        json={"revision": revision, "mode": "free", "text": "Invalid", "realign": "true"},
    )
    assert invalid.status_code == 422
    assert store.recording(rid)["revision"] == revision
    # Arbitrary legacy extras remain ignored, including names used by the new route.
    client.put("/api/editing-settings", json={"realign_edits": False}).raise_for_status()
    legacy = client.patch(
        f"/api/recordings/{rid}/segments/1",
        json={
            "revision": revision,
            "mode": "free",
            "text": "Legacy",
            "realign": True,
            "cbs_edit_realign_v1": True,
        },
    )
    assert legacy.status_code == 200, legacy.text
    assert [job["state"] for job in realign_jobs(store)] == ["cancelled", "queued"]


def test_realigned_timing_becomes_a_timing_only_version_of_the_same_result(edited, monkeypatch):
    client, store, rid, result = edited
    client.put("/api/editing-settings", json={"realign_edits": True})
    edit(client, store, rid, "1", "Hej verden igen.")
    edit(client, store, rid, "1", "Hej hele verden.")
    # The second edit supersedes the first, still queued realignment.
    assert [row["state"] for row in realign_jobs(store)] == ["cancelled", "queued"]
    edit(client, store, rid, "2", "Still no bounds.")  # Unbounded passages are not queued.
    job_id = realign_jobs(store, "queued")[0]["id"]
    listed = next(j for j in client.get("/api/jobs").json() if j["id"] == job_id)
    assert listed["realign"] is True and listed["realign_ready"] is False
    run_worker_for(client, store, job_id, monkeypatch)
    assert store.results(rid) == store.results(rid)[:1]  # No new result.
    listed = next(j for j in client.get("/api/jobs").json() if j["id"] == job_id)
    assert listed["realign_ready"] is True
    before = store.recording(rid)
    applied = client.post(
        f"/api/recordings/{rid}/realign/{job_id}", json={"revision": before["revision"]}
    )
    assert applied.status_code == 200, applied.text
    passage = applied.json()["segments"][1]
    assert passage["text"] == "Hej hele verden." and passage["edited"] is True
    assert passage["word_timing"] is True and passage["words_current"] is True
    assert [w["start"] for w in passage["words"]] == [10.1, 11.0, 12.0]
    assert passage["original_text"] == "Hej verden."  # The model's original is retained.
    assert applied.json()["segments"][0] == before["segments"][0]
    versions = store.result_versions(rid, result["id"])
    assert versions[0]["action"] == "timing_realign"
    assert versions[0]["context"]["segment_id"] == "1"
    assert versions[0]["context"]["job_id"] == job_id
    again = client.post(
        f"/api/recordings/{rid}/realign/{job_id}",
        json={"revision": applied.json()["revision"]},
    )
    assert again.status_code == 200, again.text
    assert store.result_versions(rid, result["id"]) == versions


def test_stale_or_inactive_timing_is_never_applied(edited, monkeypatch):
    client, store, rid, result = edited
    client.put("/api/editing-settings", json={"realign_edits": True})
    edit(client, store, rid, "1", "Hej hele verden.")
    job_id = realign_jobs(store, "queued")[0]["id"]
    run_worker_for(client, store, job_id, monkeypatch)
    other = store.add_result(
        rid, normalize_segments([{"text": "X.", "start": 0, "end": 1}]), {}, "O", {}
    )
    current = store.recording(rid)
    store.activate_result(rid, other["id"], current["revision"])
    current = store.recording(rid)
    inactive = client.post(
        f"/api/recordings/{rid}/realign/{job_id}", json={"revision": current["revision"]}
    )
    assert inactive.status_code == 409 and "Open the result" in inactive.json()["detail"]
    store.activate_result(rid, result["id"], current["revision"])
    client.put("/api/editing-settings", json={"realign_edits": False})
    edit(client, store, rid, "1", "Hej igen.")  # Edited again before timing was applied.
    current = store.recording(rid)
    stale = client.post(
        f"/api/recordings/{rid}/realign/{job_id}", json={"revision": current["revision"]}
    )
    assert stale.status_code == 409
    assert store.job(job_id)["params"]["realign_applied"] == "stale"
    assert store.recording(rid)["segments"][1]["text"] == "Hej igen."


def test_results_without_word_timing_are_not_realigned(client, monkeypatch):
    store = client.app.state.store
    project = store.create_project("Plain")
    recording = store.create_recording(project["id"], "a.wav")
    store.execute("UPDATE recordings SET status='ready',duration=30 WHERE id=?", (recording["id"],))
    store.add_result(
        recording["id"], normalize_segments([{"text": "Hej.", "start": 0, "end": 2}]), {}, "P", {}
    )
    client.put("/api/editing-settings", json={"realign_edits": True})
    edit(client, store, recording["id"], "0", "Hej igen.")
    assert realign_jobs(store) == []
