import copy
import json
import threading

import pytest

from cbs_transcriber import enhancement, media
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def passage_source(client, monkeypatch):
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    store = client.app.state.store
    project = store.create_project("Passage repairs", classification="protected")
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
                    {"word": "earlier", "start": 1, "end": 2},
                ],
            },
            {"text": "🙂 Hej  København.", "start": 199, "end": 203, "speaker": "B"},
            {"text": "Keep later.", "start": 205, "end": 208, "speaker": "A"},
        ]
    )
    segments[0]["original_text"] = "Earlier original."
    result = store.add_result(
        recording["id"], segments, {"B": "Benny"}, "Reviewed", {"capability": "words"}
    )
    return recording["id"], result


def submit(client, source, **extra):
    rid, result = source
    return client.post(
        f"/api/recordings/{rid}/enhancements",
        json={
            "result_id": result["id"],
            "revision": 1,
            "segment_id": "1",
            **extra,
        },
    )


def test_v50_repairs_only_selected_audio_and_preserves_other_passages(
    client, passage_source, monkeypatch
):
    rid, source = passage_source
    response = submit(client, passage_source)
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["params"]["segment_id"] == "1"
    store = client.app.state.store
    original = copy.deepcopy(store.result(rid, source["id"]))
    conversion = []

    def convert(args, cancel):
        conversion.append(args)
        from pathlib import Path

        Path(args[-1]).write_bytes(b"synthetic")

    def process(folder, options, token, cancel, progress, **kw):
        request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
        assert len(request["passages"]) == 1
        assert request["passages"][0] == {
            "id": "1",
            "text": "🙂 Hej  København.",
            "start": 0,
            "end": 4,
        }
        assert request["options"]["duration"] == 4
        enhancement.write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": "1",
                        "words": [
                            {"word": "🙂", "start": None, "end": None, "score": None},
                            {"word": "Hej", "start": 0.5, "end": 1.5, "score": 0.9},
                            {"word": "København.", "start": 1.5, "end": 3.5, "score": 0.9},
                        ],
                    }
                ]
            },
        )

    monkeypatch.setattr(media, "ffmpeg", convert)
    monkeypatch.setattr(enhancement, "run_worker", process)
    queue = client.app.state.queue
    queue.cancel_event = threading.Event()
    queue.process(job["id"])
    queue.process(job["id"])
    results = store.results(rid)
    assert len(results) == 2
    repaired = store.result(rid, results[-1]["id"])
    assert repaired["segments"][0] == original["segments"][0]
    assert repaired["segments"][-1] == original["segments"][-1]
    assert repaired["segments"][1]["text"] == original["segments"][1]["text"]
    assert repaired["segments"][1]["words"][0]["start"] == 199.5
    assert repaired["speakers"] == original["speakers"]
    assert store.result(rid, source["id"]) == original
    assert store.recording(rid)["active_transcript_id"] == source["id"]
    assert len(conversion) == 1
    assert conversion[0][conversion[0].index("-ss") + 1] == "199.0"
    assert conversion[0][conversion[0].index("-t") + 1] == "4.0"


@pytest.mark.parametrize(
    "extra",
    [
        {"segment_id": "missing"},
        {"segment_id": "0"},
        {"diarize": True},
        {"execution": "ucloud"},
        {"allow_downloads": True},
    ],
)
def test_v50_invalid_passage_repair_creates_no_job(client, passage_source, extra):
    response = submit(client, passage_source, **extra)
    assert response.status_code == 400, response.text
    assert client.app.state.store.all("SELECT id FROM jobs") == []


@pytest.mark.parametrize(
    "values",
    [
        {"start": None},
        {"end": None},
        {"end": 199},
        {"end": 270},
        {"text": "x" * 1201},
        {"text": "word " * 121},
    ],
)
def test_v50_unbounded_passages_are_not_aligned(client, passage_source, values):
    _, source = passage_source
    frozen = {"segments": copy.deepcopy(source["segments"]), "speakers": {}}
    frozen["segments"][1].update(values)
    with pytest.raises(ValueError):
        enhancement.passage_input(frozen, "1", enhancement.EnhancementOptions(), 300)


def test_v50_checkpoint_outside_selected_interval_does_not_create_result(client, passage_source):
    rid, source = passage_source
    response = submit(client, passage_source)
    job = response.json()
    store = client.app.state.store
    folder = store.directory(rid) / "enhancements" / job["id"]
    folder.mkdir(parents=True)
    enhancement.write_json(
        folder / "alignment.json",
        {
            "passages": [
                {
                    "id": "1",
                    "words": [
                        {"word": "🙂", "start": None, "end": None, "score": None},
                        {"word": "Hej", "start": -1, "end": 1, "score": 0.9},
                        {"word": "København.", "start": 1, "end": 3, "score": 0.9},
                    ],
                }
            ]
        },
    )
    with pytest.raises(ValueError, match="outside its passage window"):
        client.app.state.queue.process(job["id"])
    assert len(store.results(rid)) == 1
    assert store.result(rid, source["id"]) == source
