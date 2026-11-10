"""Passage word timing added in place, several passages at once (ADR 0056)."""

import json
import threading
import wave
from pathlib import Path

import pytest

from cbs_transcriber import enhancement, media
from cbs_transcriber.transcripts import normalize_segments


def synthetic_wav(arguments):
    duration = float(arguments[arguments.index("-t") + 1])
    with wave.open(str(Path(arguments[-1])), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * int(duration * 16000))


@pytest.fixture
def untimed(client, monkeypatch):
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    store = client.app.state.store
    project = store.create_project("Passage timing")
    recording = store.create_recording(project["id"], "a.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=30 WHERE id=?", (rid,))
    result = store.add_result(
        rid,
        normalize_segments(
            [
                {
                    "text": "Timed.",
                    "start": 0,
                    "end": 2,
                    "words": [{"word": "Timed.", "start": 0, "end": 2}],
                },
                {"text": "God morgen.", "start": 2, "end": 5},
                {"text": "Tak for nu.", "start": 5, "end": 9},
                {"text": "No bounds.", "start": None, "end": None},
            ]
        ),
        {},
        "Word model",
        {"capability": "words"},
    )
    return client, store, rid, result


def request(client, rid, result, ids, revision=None):
    revision = (
        revision if revision is not None else client.app.state.store.recording(rid)["revision"]
    )
    return client.post(
        f"/api/recordings/{rid}/passage-timing",
        json={"result_id": result["id"], "revision": revision, "segment_ids": ids},
    )


def process(client, store, job_id, monkeypatch, seen=None):
    """Align each requested passage: every word gets a time inside its own window."""

    def convert(args, cancel):
        synthetic_wav(args)

    def align(folder, options, token, cancel, progress, **kw):
        assert options.align and not options.diarize and not options.allow_downloads
        passages = json.loads((folder / "request.json").read_text(encoding="utf-8"))["passages"]
        if seen is not None:
            seen.extend(p["id"] for p in passages)
        output = []
        for passage in passages:
            words = passage["text"].split()
            output.append(
                {
                    "id": passage["id"],
                    "words": [
                        {"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4, "score": 0.9}
                        for i, w in enumerate(words)
                    ],
                }
            )
        enhancement.write_json(folder / "alignment.json", {"passages": output})

    monkeypatch.setattr(media, "ffmpeg", convert)
    monkeypatch.setattr(enhancement, "run_worker", align)
    queue = client.app.state.queue
    queue.cancel_event = threading.Event()
    store.update_job(job_id, state="running")
    queue.process(job_id)
    store.update_job(job_id, state="completed")


def test_one_passage_is_timed_alone_and_added_to_the_same_result(untimed, monkeypatch):
    client, store, rid, result = untimed
    response = request(client, rid, result, ["1"])
    assert response.status_code == 202, response.text
    job = store.job(response.json()["id"])
    params = job["params"]
    assert params["realign"] is True and params["segment_id"] == "1"
    # Only the chosen passage is the job's input.
    assert [s["id"] for s in params["source_snapshot"]["segments"]] == ["1"]
    seen = []
    process(client, store, job["id"], monkeypatch, seen)
    assert seen == ["1"]
    listed = next(j for j in client.get("/api/jobs").json() if j["id"] == job["id"])
    assert listed["realign"] and listed["realign_ready"] and listed["segment_id"] == "1"
    revision = store.recording(rid)["revision"]
    saved = client.post(f"/api/recordings/{rid}/realign/{job['id']}", json={"revision": revision})
    assert saved.status_code == 200, saved.text
    current = store.recording(rid)
    assert current["active_transcript_id"] == result["id"]
    assert len(store.results(rid)) == 1  # No new result to review.
    assert [w["word"] for w in current["segments"][1]["words"]] == ["God", "morgen."]
    assert current["segments"][1]["word_timing"] is True
    assert current["segments"][1]["text"] == "God morgen."
    assert not current["segments"][2]["words"]  # Unselected passages are untouched.
    latest = store.result_versions(rid, result["id"])[0]
    assert latest["action"] == "timing_realign" and latest["context"]["segment_id"] == "1"


def test_another_passage_can_start_while_one_is_queued(untimed):
    client, store, rid, result = untimed
    assert request(client, rid, result, ["1"]).status_code == 202
    assert request(client, rid, result, ["2"]).status_code == 202
    # The same passage is not queued twice.
    again = request(client, rid, result, ["1"])
    assert again.status_code == 409 and "already on its way" in again.text
    rows = store.all("SELECT params FROM jobs WHERE kind='enhance'")
    assert sorted(json.loads(r["params"])["segment_id"] for r in rows) == ["1", "2"]


def test_repair_all_is_one_job_and_one_version_keeping_later_edits(untimed, monkeypatch):
    client, store, rid, result = untimed
    response = request(client, rid, result, ["2", "1"])
    assert response.status_code == 202, response.text
    job = store.job(response.json()["id"])
    assert job["params"]["segment_ids"] == ["1", "2"]  # Frozen in transcript order.
    process(client, store, job["id"], monkeypatch)
    # Passage 2 is edited before the timing arrives: only passage 1 is updated.
    revision = store.recording(rid)["revision"]
    edited = client.patch(
        f"/api/recordings/{rid}/segments/2",
        json={"revision": revision, "mode": "free", "text": "Tak for i dag."},
    )
    assert edited.status_code == 200, edited.text
    before = len(store.result_versions(rid, result["id"]))
    saved = client.post(
        f"/api/recordings/{rid}/realign/{job['id']}",
        json={"revision": store.recording(rid)["revision"]},
    )
    assert saved.status_code == 200, saved.text
    current = store.recording(rid)
    assert current["segments"][1]["word_timing"] is True
    assert current["segments"][2]["text"] == "Tak for i dag."
    assert not current["segments"][2]["words"]
    versions = store.result_versions(rid, result["id"])
    assert len(versions) == before + 1
    assert versions[0]["context"]["job_id"] == job["id"]
    assert "segment_id" not in versions[0]["context"]


def test_untimed_passages_are_searched_only_between_their_timed_neighbours(client, monkeypatch):
    """ADR 0058: adjacent untimed fragments are aligned together inside the gap; the gap is
    never stored as their timing, only bounds from their own complete words are."""
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    store = client.app.state.store
    project = store.create_project("Fragments")
    recording = store.create_recording(project["id"], "a.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=120 WHERE id=?", (rid,))
    rows = [
        {
            "text": "Mit CV.",
            "start": 0,
            "end": 2,
            "words": [
                {"word": "Mit", "start": 0, "end": 1},
                {"word": "CV.", "start": 1, "end": 2},
            ],
        },
        {"text": "Jeg  har faktisk", "start": None, "end": None},
        {"text": "skrevet nogle", "start": None, "end": None},
        {"text": "Bøger.", "start": 6, "end": 8},
        {"text": "Sidst.", "start": None, "end": None},
    ]
    # In transcript order, as alignment leaves its untimed fragments (normalizing would sort).
    segments = [normalize_segments([row])[0] | {"id": str(i)} for i, row in enumerate(rows)]
    result = store.add_result(rid, segments, {}, "Word model", {"capability": "words"})
    response = request(client, rid, result, ["2"])
    assert response.status_code == 202, response.text
    job = store.job(response.json()["id"])
    # Both fragments in the gap go together, searched only between 2 s and 6 s.
    assert job["params"]["timing_units"] == [{"ids": ["1", "2"], "start": 2, "end": 6}]
    assert job["params"]["segment_ids"] == ["1", "2"]
    seen = []

    def convert(args, cancel):
        seen.append(args)
        synthetic_wav(args)

    def align(folder, options, token, cancel, progress, **kw):
        [passage] = json.loads((folder / "request.json").read_text(encoding="utf-8"))["passages"]
        assert passage["text"] == "Jeg  har faktisk skrevet nogle"
        assert (passage["start"], passage["end"]) == (0, 4)  # Relative to the 2-6 s clip.
        times = {"Jeg": (0.1, 0.4), "har": (0.5, 0.8), "faktisk": (0.9, 1.5), "skrevet": None}
        enhancement.write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": passage["id"],
                        "words": [
                            {"word": w, "start": t[0], "end": t[1], "score": 0.9}
                            if (t := times.get(w, (2.0, 2.6)))
                            else {"word": w}
                            for w in passage["text"].split()
                        ],
                    }
                ]
            },
        )

    monkeypatch.setattr(media, "ffmpeg", convert)
    monkeypatch.setattr(enhancement, "run_worker", align)
    queue = client.app.state.queue
    queue.cancel_event = threading.Event()
    store.update_job(job["id"], state="running")
    queue.process(job["id"])
    store.update_job(job["id"], state="completed")
    assert seen and seen[0][:2] == ["-ss", "2.0"]  # Only the gap is decoded.
    saved = client.post(
        f"/api/recordings/{rid}/realign/{job['id']}",
        json={"revision": store.recording(rid)["revision"]},
    )
    assert saved.status_code == 200, saved.text
    first, second = store.recording(rid)["segments"][1:3]
    # Every word of the first fragment was found: bounds from its own words, never the gap.
    assert first["word_timing"] is True and (first["start"], first["end"]) == (2.1, 3.5)
    assert first["text"] == "Jeg  har faktisk"
    # "skrevet" stayed uncertain: timed words highlight, the passage keeps no bounds.
    assert second["word_timing"] is False and second["start"] is None
    assert [w["start"] for w in second["words"]] == [None, 4.0]
    # The last passage is searched between 8 s and the recording's end at 120 s: too long.
    refused = request(client, rid, result, ["4"])
    assert refused.status_code == 400 and "60 seconds" in refused.text
    # Repair all leaves it out rather than refusing the passages that can be timed.
    # The fragment still untimed is now searched only after its newly timed neighbour.
    bulk = request(client, rid, result, ["2", "4"])
    assert bulk.status_code == 202, bulk.text
    assert store.job(bulk.json()["id"])["params"]["timing_units"] == [
        {"ids": ["2"], "start": 3.5, "end": 6}
    ]
    # An interrupted job can be retried: its frozen units are checked, not neighbours.
    store.update_job(bulk.json()["id"], state="interrupted")
    retried = client.post(f"/api/jobs/{bulk.json()['id']}/retry", json={"replace": True})
    assert retried.status_code < 300, retried.text


def test_stale_revisions_and_other_results_are_refused(untimed):
    client, store, rid, result = untimed
    stale = request(client, rid, result, ["1"], revision=store.recording(rid)["revision"] + 1)
    assert stale.status_code == 409
    other = store.add_result(
        rid, normalize_segments([{"text": "Other.", "start": 0, "end": 2}]), {}, "Other", {}
    )
    assert request(client, rid, other, ["0"]).status_code == 409
    assert not store.all("SELECT id FROM jobs WHERE kind='enhance'")
