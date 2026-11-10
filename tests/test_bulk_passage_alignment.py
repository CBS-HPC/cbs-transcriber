import copy
import json
import re
import threading
from pathlib import Path

import pytest

from cbs_transcriber import enhancement, media
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def bulk_source(client, monkeypatch):
    monkeypatch.setattr(enhancement, "check_runtime", lambda *args, **kwargs: {"ready": True})
    store = client.app.state.store
    project = store.create_project("Bulk repairs", classification="protected")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.execute(
        "UPDATE recordings SET status='ready',duration=300 WHERE id=?", (recording["id"],)
    )
    segments = normalize_segments(
        [
            {
                "text": "Keep earlier.",
                "start": 0,
                "end": 2,
                "speaker": "A",
                "words": [
                    {"word": "Keep", "start": 0, "end": 1},
                    {"word": "earlier.", "start": 1, "end": 2},
                ],
            },
            {"text": "🙂 Hej  København.", "start": 10, "end": 14, "speaker": "B"},
            {"text": "Keep middle.", "start": 25, "end": 28, "speaker": "A"},
            {"text": "Good morning.", "start": 50, "end": 54, "speaker": "B"},
            {"text": "Unlocated wording.", "start": None, "end": None, "speaker": "A"},
        ]
    )
    segments[0]["original_text"] = "Earlier imported original."
    segments[2].update(original_text="Middle original.", edited=True)
    result = store.add_result(
        recording["id"], segments, {"A": "Alice", "B": "Benny"}, "Reviewed", {"capability": "words"}
    )
    return recording["id"], result


def submit(client, source, **extra):
    recording_id, result = source
    return client.post(
        f"/api/recordings/{recording_id}/enhancements",
        json={
            "result_id": result["id"],
            "revision": 1,
            "segment_ids": ["3", "1"],
            **extra,
        },
    )


def write_alignment(folder):
    request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
    assert len(request["passages"]) == 1
    passage = request["passages"][0]
    assert (passage["start"], passage["end"]) == (0, 4)
    assert request["options"]["duration"] == 4
    enhancement.write_json(
        folder / "alignment.json",
        {
            "passages": [
                {
                    "id": passage["id"],
                    "words": [
                        {
                            "word": word,
                            "start": 0.5 + index,
                            "end": 1 + index,
                            "score": 0.9,
                        }
                        for index, word in enumerate(re.findall(r"\S+", passage["text"]))
                    ],
                }
            ]
        },
    )
    return passage["id"]


def fake_conversion(conversions):
    def convert(arguments, cancel):
        conversions.append(arguments)
        Path(arguments[-1]).write_bytes(b"synthetic cropped audio")

    return convert


def test_bulk_repairs_crop_only_selected_windows_and_append_one_combined_result(
    client, bulk_source, monkeypatch
):
    rid, source = bulk_source
    response = submit(client, bulk_source)
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["params"]["segment_ids"] == ["1", "3"]
    assert "segment_id" not in job["params"]
    conversions, calls, runtimes = [], [], []

    def process(folder, options, token, cancel, progress, **kwargs):
        calls.append(write_alignment(folder))
        runtimes.append(kwargs["runtime"])

    monkeypatch.setattr(media, "ffmpeg", fake_conversion(conversions))
    monkeypatch.setattr(enhancement, "run_worker", process)
    queue = client.app.state.queue
    queue.process(job["id"])
    queue.process(job["id"])
    store = client.app.state.store
    results = store.results(rid)
    assert len(results) == 2
    repaired = store.result(rid, results[-1]["id"])
    assert calls == ["1", "3"]
    assert runtimes[0] is runtimes[1]
    assert [(args[args.index("-ss") + 1], args[args.index("-t") + 1]) for args in conversions] == [
        ("10.0", "4.0"),
        ("50.0", "4.0"),
    ]
    for index in (0, 2, 4):
        assert repaired["segments"][index] == source["segments"][index]
    assert repaired["segments"][1]["words"][0]["start"] == 10.5
    assert repaired["segments"][3]["words"][0]["start"] == 50.5
    assert repaired["speakers"] == source["speakers"]
    assert repaired["source"]["segment_ids"] == ["1", "3"]
    assert store.result(rid, source["id"]) == source
    assert store.recording(rid)["active_transcript_id"] == source["id"]
    public_job = next(row for row in client.get("/api/jobs").json() if row["id"] == job["id"])
    assert public_job["segment_ids"] == ["1", "3"]
    folder = store.directory(rid) / "enhancements" / job["id"] / "passages"
    for index in range(2):
        cached = json.loads((folder / f"{index:05d}" / "alignment.json").read_text())
        assert cached["passages"][0]["words"][0]["start"] == 0.5


def test_bulk_failure_retry_keeps_frozen_wording_and_completed_target_checkpoints(
    client, bulk_source, monkeypatch
):
    rid, source = bulk_source
    job = submit(client, bulk_source).json()
    conversions, calls = [], []
    fail = True

    def process(folder, options, token, cancel, progress, **kwargs):
        request = json.loads((folder / "request.json").read_text())
        identifier = request["passages"][0]["id"]
        calls.append(identifier)
        if identifier == "3" and fail:
            raise ValueError("Synthetic second-passage failure")
        write_alignment(folder)

    monkeypatch.setattr(media, "ffmpeg", fake_conversion(conversions))
    monkeypatch.setattr(enhancement, "run_worker", process)
    queue = client.app.state.queue
    store = queue.store
    with pytest.raises(ValueError, match="second-passage"):
        queue.process(job["id"])
    assert len(store.results(rid)) == 1
    assert store.result(rid, source["id"]) == source
    store.update_job(job["id"], state="failed")
    current = store.recording(rid)
    edited = copy.deepcopy(current["segments"])
    edited[1]["text"] = "A newer reviewed correction."
    store.save_transcript(rid, edited, current["speakers"], current["revision"])
    fail = False
    retry = client.post(f"/api/jobs/{job['id']}/retry", json={})
    assert retry.status_code == 200, retry.text
    # Retry keeps the frozen job; only the automatic device was recorded when it first ran.
    frozen = copy.deepcopy(job["params"])
    frozen["enhancement"]["device"] = "cpu"
    assert store.job(job["id"])["params"] == frozen
    queue.process(job["id"])
    queue.process(job["id"])
    results = store.results(rid)
    assert len(results) == 2
    repaired = store.result(rid, results[-1]["id"])
    assert repaired["segments"][1]["text"] == source["segments"][1]["text"]
    assert repaired["segments"][1]["words"][0]["start"] == 10.5
    assert repaired["segments"][3]["words"][0]["start"] == 50.5
    assert calls == ["1", "3", "3"]
    assert len(conversions) == 2
    assert store.recording(rid)["segments"][1]["text"] == "A newer reviewed correction."

    store.update_job(job["id"], state="failed")
    monkeypatch.setattr(
        enhancement,
        "check_runtime",
        lambda *args, **kwargs: pytest.fail("checked completed stages"),
    )
    assert client.post(f"/api/jobs/{job['id']}/retry", json={}).status_code == 200
    queue.process(job["id"])
    assert len(store.results(rid)) == 2


def test_bulk_partial_alignment_preserves_text_ids_and_actual_passage_bounds(
    client, bulk_source, monkeypatch
):
    rid, source = bulk_source
    job = submit(client, bulk_source).json()

    def process(folder, options, token, cancel, progress, **kwargs):
        write_alignment(folder)
        path = folder / "alignment.json"
        output = json.loads(path.read_text(encoding="utf-8"))
        output["passages"][0]["words"][-1].update(start=None, end=None, score=None)
        enhancement.write_json(path, output)

    monkeypatch.setattr(media, "ffmpeg", fake_conversion([]))
    monkeypatch.setattr(enhancement, "run_worker", process)
    queue = client.app.state.queue
    queue.process(job["id"])
    repaired = queue.store.result(rid, queue.store.results(rid)[-1]["id"])
    assert "".join(segment["text"] for segment in repaired["segments"]) == "".join(
        segment["text"] for segment in source["segments"]
    )
    identifiers = [segment["id"] for segment in repaired["segments"]]
    assert len(identifiers) == len(set(identifiers))
    assert identifiers == [segment["id"] for segment in source["segments"]]
    by_id = {segment["id"]: segment for segment in repaired["segments"]}
    for identifier in ("1", "3"):
        assert not by_id[identifier]["word_timing"]
        assert (by_id[identifier]["start"], by_id[identifier]["end"]) == (
            source["segments"][int(identifier)]["start"],
            source["segments"][int(identifier)]["end"],
        )
    for identifier in ("0", "2", "4"):
        assert by_id[identifier] == source["segments"][int(identifier)]


def test_cancelling_between_passages_retains_completed_work_without_partial_result(
    client, bulk_source, monkeypatch
):
    rid, source = bulk_source
    job = submit(client, bulk_source).json()
    conversions, calls = [], []
    queue = client.app.state.queue

    def process(folder, options, token, cancel, progress, **kwargs):
        calls.append(write_alignment(folder))
        if len(calls) == 1:
            cancel.set()

    monkeypatch.setattr(media, "ffmpeg", fake_conversion(conversions))
    monkeypatch.setattr(enhancement, "run_worker", process)
    with pytest.raises(media.Cancelled):
        queue.process(job["id"])
    assert len(queue.store.results(rid)) == 1
    assert len(conversions) == 1
    assert calls == ["1"]
    queue.store.update_job(job["id"], state="cancelled")
    queue.cancel_event = threading.Event()
    response = client.post(f"/api/jobs/{job['id']}/retry", json={})
    assert response.status_code == 200, response.text
    queue.process(job["id"])
    assert calls == ["1", "3"]
    assert len(conversions) == 2
    assert len(queue.store.results(rid)) == 2


@pytest.mark.parametrize(
    ("extra", "status"),
    [
        ({"segment_ids": []}, 422),
        ({"segment_ids": ["1"] * 1001}, 422),
        ({"segment_ids": ["1", "1"]}, 400),
        ({"segment_ids": ["1", "missing"]}, 400),
        ({"segment_ids": ["1", "0"]}, 400),
        ({"segment_ids": ["1", "4"]}, 400),
        ({"segment_id": "1"}, 400),
        ({"execution": "ucloud"}, 400),
        ({"diarize": True}, 400),
        ({"allow_downloads": True}, 400),
    ],
)
def test_invalid_bulk_selection_queues_nothing(client, bulk_source, extra, status):
    response = submit(client, bulk_source, **extra)
    assert response.status_code == status, response.text
    assert client.app.state.store.all("SELECT id FROM jobs") == []


def test_bulk_selection_rejects_more_than_one_hundred_thousand_words():
    source = {
        "segments": [
            {
                "id": str(index),
                "text": "word " * 120,
                "start": index,
                "end": index + 1,
                "word_timing": False,
            }
            for index in range(1000)
        ]
    }
    with pytest.raises(ValueError, match="100,000"):
        enhancement.passage_inputs(
            source,
            [segment["id"] for segment in source["segments"]],
            enhancement.EnhancementOptions(),
            1000,
        )
