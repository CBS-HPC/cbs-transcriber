import copy
import json

import pytest

from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


def legacy_result(tmp_path, *, scoped=True, bounded=True):
    store = Store(tmp_path)
    project = store.create_project("Synthetic replay")
    recording = store.create_recording(project["id"], "synthetic.wav")
    segments = normalize_segments(
        [
            {"text": "Earlier passage", "start": 0, "end": 1},
            {
                "text": "Known unknown known.",
                "start": 2 if bounded else None,
                "end": 5 if bounded else None,
            },
            {"text": "Later passage", "start": 6, "end": 7},
        ]
    )
    # An unbounded source must stay between its actual neighbours, as in a frozen result.
    segments.sort(key=lambda s: s["text"] == "Later passage")
    for index, segment in enumerate(segments):
        segment["id"] = str(index)
    source = store.add_result(recording["id"], segments, {}, "Source", {"kind": "api"})
    frozen = store.freeze_result(recording["id"], source["id"], 1)
    selected = "1"
    params = {"source_snapshot": frozen}
    if scoped:
        params["segment_id"] = selected
    job = store.add_job(recording["id"], "enhance", params)
    fragments = []
    for identifier, text, start, end in [
        ("1", "Known ", 2, 3),
        ("3", "unknown ", None, None),
        ("4", "known.", 4, 5),
    ]:
        part = normalize_segments([{"text": text, "start": start, "end": end}])[0]
        part["id"] = identifier
        fragments.append(part)
    output = [copy.deepcopy(segments[0]), *fragments, copy.deepcopy(segments[2])]
    result = store.add_result(
        recording["id"],
        output,
        {},
        "Legacy partial enhancement",
        {
            "kind": "enhancement",
            "source_result_id": source["id"],
            **({"segment_id": selected} if scoped else {}),
        },
        job_id=job["id"],
    )
    return store, recording, source, job, result


@pytest.mark.parametrize("scoped", [False, True])
def test_legacy_partial_replay_uses_exact_frozen_source_without_writing(tmp_path, scoped):
    store, recording, source, _, result = legacy_result(tmp_path, scoped=scoped)
    before = store.all("SELECT * FROM transcript_results ORDER BY id")
    expected = {
        "3": {"start": 2, "end": 5, "source_result_id": source["id"], "source_segment_id": "1"}
    }
    assert result["replay_windows"] == expected
    assert result["segments"][2]["start"] is None
    current = store.recording(recording["id"])
    active = store.activate_result(recording["id"], result["id"], current["revision"])
    assert active["replay_windows"] == expected
    assert store.result(recording["id"], result["id"])["replay_windows"] == expected
    assert store.all("SELECT * FROM transcript_results ORDER BY id") == before
    assert store.result(recording["id"], result["id"])["segments"][0] == source["segments"][0]


def test_legacy_replay_does_not_use_later_edited_source(tmp_path):
    store, recording, source, _, result = legacy_result(tmp_path)
    changed = copy.deepcopy(source["segments"])
    changed[1].update(start=99, end=100, text="Different text")
    store.execute(
        "UPDATE transcript_results SET segments=? WHERE id=?", (json.dumps(changed), source["id"])
    )
    assert store.result(recording["id"], result["id"])["replay_windows"]["3"]["start"] == 2


@pytest.mark.parametrize("damage", ["text", "order", "snapshot_id", "job_recording"])
def test_legacy_replay_requires_exact_owned_frozen_mapping(tmp_path, damage):
    store, recording, _, job, result = legacy_result(tmp_path)
    if damage in {"text", "order"}:
        changed = copy.deepcopy(result["segments"])
        if damage == "text":
            changed[2]["original_text"] = "Different baseline"
        else:
            changed[0], changed[-1] = changed[-1], changed[0]
        store.execute(
            "UPDATE transcript_results SET segments=? WHERE id=?",
            (json.dumps(changed), result["id"]),
        )
    elif damage == "snapshot_id":
        params = copy.deepcopy(job["params"])
        params["source_snapshot"]["id"] = "wrong-result"
        store.update_job(job["id"], params=params)
    else:
        other = store.create_recording(recording["project_id"], "other.wav")
        store.execute("UPDATE jobs SET recording_id=? WHERE id=?", (other["id"], job["id"]))
    assert store.result(recording["id"], result["id"])["replay_windows"] == {}


def test_legacy_unbounded_source_never_invents_a_replay_window(tmp_path):
    store, recording, _, _, result = legacy_result(tmp_path, bounded=False)
    assert store.result(recording["id"], result["id"])["replay_windows"] == {}


def test_source_and_other_recordings_do_not_inherit_legacy_replay_windows(tmp_path):
    store, recording, source, _, _ = legacy_result(tmp_path)
    assert store.result(recording["id"], source["id"])["replay_windows"] == {}
    assert store.recording(recording["id"])["replay_windows"] == {}
    other = store.create_recording(recording["project_id"], "other.wav")
    assert other["replay_windows"] == {}
