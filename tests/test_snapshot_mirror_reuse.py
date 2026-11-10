import json
import threading
from pathlib import Path

import pytest

from cbs_transcriber import store as store_module
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


def _fixture(tmp_path):
    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "synthetic.wav")
    segments = normalize_segments(
        [{"text": "Møller 🧪 e\u0301", "start": 0.123456789012345, "end": 2.75}]
    )
    result = store.add_result(recording["id"], segments, {"Speaker_01": "Åse"}, "First", {})
    return store, project, recording, result


def test_snapshot_reuses_identical_active_mirror_and_repairs_outputs(tmp_path, monkeypatch):
    store, project, recording, result = _fixture(tmp_path)
    store.add_result(
        recording["id"], normalize_segments([{"text": "Another result"}]), {}, "Other", {}
    )
    with store.connection() as db:
        raw = db.execute(
            "SELECT segments FROM transcript_results WHERE id=?", (result["id"],)
        ).fetchone()[0]
        assert (
            db.execute("SELECT segments FROM recordings WHERE id=?", (recording["id"],)).fetchone()[
                0
            ]
            == raw
        )

    output = (
        store.project_folder(project["id"]) / "exports" / recording["id"] / f"{result['id']}.txt"
    )
    output.write_text("synthetic corruption", encoding="utf-8")
    original_loads = json.loads
    calls = []

    def counting_loads(value, *args, **kwargs):
        if value == raw:
            calls.append(value)
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(store_module.json, "loads", counting_loads)
    store.snapshot_project(project["id"])
    assert len(calls) == 1
    snapshot = original_loads(
        (store.project_folder(project["id"]) / "project.json").read_text("utf-8")
    )
    saved = snapshot["recordings"][0]
    active = next(item for item in saved["transcripts"] if item["id"] == result["id"])
    assert saved["segments"] == active["segments"]
    assert saved["speakers"] == active["speakers"]
    assert "Møller 🧪 e\u0301" in output.read_text("utf-8")
    assert store.snapshot_errors.get(project["id"]) is None


def test_snapshot_exact_bytes_and_write_failure_with_different_mirror_encodings(
    tmp_path, monkeypatch
):
    store, project, recording, result = _fixture(tmp_path)
    with store.connection() as db:
        raw_segments, raw_speakers = db.execute(
            "SELECT segments,speakers FROM transcript_results WHERE id=?", (result["id"],)
        ).fetchone()
    separate_segments = json.dumps(
        json.loads(raw_segments), ensure_ascii=True, separators=(",", ":")
    )
    separate_speakers = json.dumps(
        json.loads(raw_speakers), ensure_ascii=True, separators=(",", ":")
    )
    assert separate_segments != raw_segments and separate_speakers != raw_speakers
    store.execute(
        "UPDATE recordings SET segments=?,speakers=? WHERE id=?",
        (separate_segments, separate_speakers, recording["id"]),
    )
    store.snapshot_project(project["id"])
    folder = store.project_folder(project["id"])

    def published():
        return {
            path.relative_to(folder): path.read_bytes()
            for path in folder.rglob("*")
            if path.is_file()
        }

    independent_decodes = published()
    store.execute(
        "UPDATE recordings SET segments=?,speakers=? WHERE id=?",
        (raw_segments, raw_speakers, recording["id"]),
    )
    store.snapshot_project(project["id"])
    assert published() == independent_decodes

    original_write = Path.write_text

    def fail_pending(path, *args, **kwargs):
        if path.name == "project.pending":
            raise OSError("synthetic snapshot write failure")
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_pending)
    store.snapshot_project(project["id"])
    assert "database" in store.snapshot_errors[project["id"]]
    assert (folder / "project.json").read_bytes() == independent_decodes[Path("project.json")]
    monkeypatch.setattr(Path, "write_text", original_write)
    store.snapshot_project(project["id"])
    assert project["id"] not in store.snapshot_errors
    assert published() == independent_decodes


def test_snapshot_preserves_nonidentical_mirror_and_rejects_invalid_json(tmp_path):
    store, project, recording, result = _fixture(tmp_path)
    different = normalize_segments([{"text": "Separat spejl"}])
    store.execute(
        "UPDATE recordings SET segments=? WHERE id=?", (json.dumps(different), recording["id"])
    )
    store.snapshot_project(project["id"])
    folder = store.project_folder(project["id"])
    snapshot = json.loads((folder / "project.json").read_text("utf-8"))["recordings"][0]
    assert snapshot["segments"] == different
    assert snapshot["transcripts"][0]["segments"][0]["text"] == "Møller 🧪 e\u0301"
    assert "Møller 🧪 e\u0301" in (
        folder / "exports" / recording["id"] / f"{result['id']}.txt"
    ).read_text("utf-8")
    previous = (folder / "project.json").read_bytes()
    store.execute("UPDATE recordings SET segments=? WHERE id=?", ("[invalid", recording["id"]))
    with pytest.raises(json.JSONDecodeError):
        store.snapshot_project(project["id"])
    assert (folder / "project.json").read_bytes() == previous


@pytest.mark.parametrize("changed", ["segments", "speakers", "no_active"])
def test_snapshot_reuses_each_matching_field_only_for_the_active_result(
    tmp_path, monkeypatch, changed
):
    store, project, recording, result = _fixture(tmp_path)
    with store.connection() as db:
        raw_segments, raw_speakers = db.execute(
            "SELECT segments,speakers FROM transcript_results WHERE id=?", (result["id"],)
        ).fetchone()
    if changed == "segments":
        value = json.dumps(normalize_segments([{"text": "A different passage"}]))
        store.execute("UPDATE recordings SET segments=? WHERE id=?", (value, recording["id"]))
    elif changed == "speakers":
        value = json.dumps({"Speaker_01": "A different name"})
        store.execute("UPDATE recordings SET speakers=? WHERE id=?", (value, recording["id"]))
    else:
        store.execute(
            "UPDATE recordings SET active_transcript_id=NULL WHERE id=?", (recording["id"],)
        )
    original_loads = json.loads
    calls = []

    def counting_loads(value, *args, **kwargs):
        if value in (raw_segments, raw_speakers):
            calls.append(value)
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(store_module.json, "loads", counting_loads)
    store.snapshot_project(project["id"])
    assert calls.count(raw_segments) == (2 if changed == "no_active" else 1)
    assert calls.count(raw_speakers) == (2 if changed == "no_active" else 1)
    snapshot = original_loads(
        (store.project_folder(project["id"]) / "project.json").read_text("utf-8")
    )["recordings"][0]
    if changed == "segments":
        assert snapshot["segments"][0]["text"] == "A different passage"
    elif changed == "speakers":
        assert snapshot["speakers"]["Speaker_01"] == "A different name"
    else:
        assert snapshot["active_transcript_id"] is None


def test_snapshot_rejects_identical_corrupt_mirror_and_result(tmp_path):
    store, project, recording, result = _fixture(tmp_path)
    path = store.project_folder(project["id"]) / "project.json"
    previous = path.read_bytes()
    store.execute("UPDATE recordings SET segments=? WHERE id=?", ("[invalid", recording["id"]))
    store.execute("UPDATE transcript_results SET segments=? WHERE id=?", ("[invalid", result["id"]))
    with pytest.raises(json.JSONDecodeError):
        store.snapshot_project(project["id"])
    assert path.read_bytes() == previous


def test_snapshot_keeps_one_database_view_during_concurrent_update(tmp_path, monkeypatch):
    store, project, recording, result = _fixture(tmp_path)
    with store.connection() as db:
        raw = db.execute(
            "SELECT segments FROM transcript_results WHERE id=?", (result["id"],)
        ).fetchone()[0]
    replacement = json.dumps(normalize_segments([{"text": "Nyt indhold"}]), ensure_ascii=False)
    started, updated = threading.Event(), threading.Event()
    original_loads = json.loads
    pause_once = [True]

    def paused_loads(value, *args, **kwargs):
        if value == raw and pause_once[0]:
            pause_once[0] = False
            started.set()
            assert updated.wait(10), "synthetic writer did not finish"
        return original_loads(value, *args, **kwargs)

    def writer():
        assert started.wait(10), "snapshot did not reach the read"
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE recordings SET segments=? WHERE id=?", (replacement, recording["id"])
            )
            db.execute(
                "UPDATE transcript_results SET segments=? WHERE id=?", (replacement, result["id"])
            )
        updated.set()

    monkeypatch.setattr(store_module.json, "loads", paused_loads)
    thread = threading.Thread(target=writer)
    thread.start()
    store.snapshot_project(project["id"])
    thread.join(10)
    assert not thread.is_alive()
    folder = store.project_folder(project["id"])
    old = original_loads((folder / "project.json").read_text("utf-8"))["recordings"][0]
    assert (
        old["segments"][0]["text"]
        == old["transcripts"][0]["segments"][0]["text"]
        == "Møller 🧪 e\u0301"
    )
    store.snapshot_project(project["id"])
    new = original_loads((folder / "project.json").read_text("utf-8"))["recordings"][0]
    assert (
        new["segments"][0]["text"] == new["transcripts"][0]["segments"][0]["text"] == "Nyt indhold"
    )
