"""A selected repair keeps the complete combined result's named voices and exact siblings."""

import copy
import json
import threading
from types import SimpleNamespace

import pytest

from cbs_transcriber import enhancement
from cbs_transcriber.transcripts import normalize_segments


@pytest.mark.parametrize("selection", ["single", "bulk"])
@pytest.mark.parametrize("partial", [False, True])
def test_cached_selected_repair_preserves_all_names_and_unselected_passages(
    client, monkeypatch, selection, partial
):
    store = client.app.state.store
    project = store.create_project("Selected repair names")
    rid = store.create_recording(project["id"], "synthetic.wav")["id"]
    store.execute("UPDATE recordings SET status='ready',duration=6 WHERE id=?", (rid,))
    source = store.add_result(
        rid,
        normalize_segments(
            [
                {"text": "Unselected æøå.\r\n", "start": 0, "end": 2, "speaker": "B"},
                {"text": "Selected words.", "start": 2, "end": 4, "speaker": "A"},
                {"text": "Unselected last.", "start": 4, "end": 6, "speaker": "B"},
            ]
        ),
        {"A": "Interviewer", "B": "Named respondent", "C": "Another saved name"},
        "Reviewed",
        {},
    )
    before = store.recording(rid)
    versions = store.result_versions(rid, source["id"])
    monkeypatch.setattr("cbs_transcriber.app.require_local_runtime", lambda *_: None)
    response = client.post(
        f"/api/recordings/{rid}/enhancements",
        json={
            "result_id": source["id"],
            "revision": before["revision"],
            **({"segment_id": "1"} if selection == "single" else {"segment_ids": ["1"]}),
            "execution": "local",
            "device": "cpu",
            "language": "en",
            "align": True,
            "diarize": False,
            "allow_downloads": False,
        },
    )
    assert response.status_code == 202, response.text
    job = store.job(response.json()["id"])
    directory = store.directory(rid) / "enhancements" / job["id"]
    if selection == "bulk":
        directory /= "passages/00000"
    directory.mkdir(parents=True)
    words = [
        {"word": "Selected", "start": 0.2, "end": 0.6, "score": 0.9},
        {"word": "words.", "start": 0.7, "end": 1.2, "score": 0.8},
    ]
    if partial:
        words[-1].update(start=None, end=None, score=None)
    checkpoint = json.dumps({"passages": [{"id": "1", "words": words}]}).encode()
    (directory / "alignment.json").write_bytes(checkpoint)

    def forbidden(*args, **kwargs):
        raise AssertionError("Cached synthetic repair must not decode or load inference.")

    monkeypatch.setattr(enhancement, "require_local_runtime", forbidden)
    monkeypatch.setattr(enhancement, "run_worker", forbidden)
    monkeypatch.setattr(enhancement.media, "ffmpeg", forbidden)
    queue = SimpleNamespace(
        store=store, credentials=client.app.state.credentials, cancel_event=threading.Event()
    )
    enhancement.run_enhancement(queue, job, store.recording(rid))
    result = next(r for r in store.results(rid) if r["id"] != source["id"])
    saved = store.result(rid, result["id"])
    assert saved["segments"][0] == before["segments"][0]
    assert saved["segments"][2] == before["segments"][2]
    assert saved["speakers"] == before["speakers"]
    assert saved["segments"][1]["speaker"] == "A"
    assert saved["segments"][1]["text"] == before["segments"][1]["text"]
    assert saved["segments"][1]["word_timing"] is (not partial)
    assert saved["segments"][1]["words"][0]["start"] == 2.2
    if partial:
        assert saved["segments"][1]["words"][-1]["start"] is None
    assert store.recording(rid)["active_transcript_id"] == source["id"]
    assert store.result(rid, source["id"])["speakers"] == before["speakers"]
    assert store.result_versions(rid, source["id"]) == versions
    # Lost completion replies reuse the same result and exact clip-relative checkpoint.
    prior = copy.deepcopy(saved)
    enhancement.run_enhancement(queue, store.job(job["id"]), store.recording(rid))
    assert len(store.results(rid)) == 2
    assert store.result(rid, result["id"]) == prior
    assert (directory / "alignment.json").read_bytes() == checkpoint
