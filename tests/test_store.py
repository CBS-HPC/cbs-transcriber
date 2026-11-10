import ntpath
import os

import pytest

from cbs_transcriber.store import Store

if os.name == "nt":
    import _winapi
from cbs_transcriber.transcripts import normalize_segments


def test_v3_projects_and_transcripts_persist_with_conflict_protection(tmp_path):
    store = Store(tmp_path)
    first, second = store.create_project("One"), store.create_project("Two")
    recording = store.create_recording(first["id"], "interview.v2.wav")
    segments = normalize_segments([{"start": 0, "end": 1, "text": "Øresund"}])
    store.save_transcript(recording["id"], segments, {}, 0)
    with pytest.raises(ValueError, match="another window"):
        store.save_transcript(recording["id"], [], {}, 0)
    reopened = Store(tmp_path)
    assert reopened.recording(recording["id"])["segments"][0]["text"] == "Øresund"
    store.execute("DELETE FROM projects WHERE id=?", (second["id"],))
    assert reopened.recording(recording["id"])["project_id"] == first["id"]
    store.execute("DELETE FROM projects WHERE id=?", (first["id"],))
    with pytest.raises(KeyError):
        store.recording(recording["id"])


def test_v6_restart_keeps_chunks_and_requires_explicit_retry(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("One")
    recording = store.create_recording(project["id"], "one.wav")
    job = store.add_job(recording["id"], "transcribe")
    store.update_job(job["id"], state="running", chunks=[{"offset": 0, "done": True}])
    Store(tmp_path).recover_jobs()
    result = store.job(job["id"])
    assert result["state"] == "interrupted"
    assert result["chunks"][0]["done"]


def test_v5_directory_rejects_arbitrary_paths(tmp_path):
    with pytest.raises(KeyError):
        Store(tmp_path).directory("../../outside")


@pytest.mark.skipif(os.name != "nt", reason="Windows realpath() keeps \\\\?\\ in this race")
def test_directory_containment_survives_sibling_creating_recordings(tmp_path, monkeypatch):
    """Two Runtime member threads prepare first files of one project concurrently."""
    store = Store(tmp_path)
    project = store.create_project("One")
    first, second = (store.create_recording(project["id"], name) for name in ("a.wav", "b.wav"))
    base = store.project_folder(project["id"])
    assert base.is_dir() and not (base / "recordings").exists()
    lookup, interleaved = ntpath._getfinalpathname, []

    def sibling_mkdir_between_lookups(path):
        try:
            return lookup(path)
        except OSError as exc:
            # ERROR_PATH_NOT_FOUND: base/recordings is missing for this first lookup only.
            if exc.winerror == 3 and not interleaved:
                interleaved.append(path)
                (base / "recordings" / first["id"] / "enhancements").mkdir(parents=True)
            raise

    monkeypatch.setattr(ntpath, "_getfinalpathname", sibling_mkdir_between_lookups)
    assert store.directory(second["id"]) == base / "recordings" / second["id"]
    assert interleaved
    monkeypatch.undo()

    outside = tmp_path / "outside"
    outside.mkdir()
    _winapi.CreateJunction(str(outside), str(base / "recordings" / second["id"]))
    with pytest.raises(ValueError, match="inside its project storage"):
        store.directory(second["id"])


def test_v9_second_server_cannot_claim_same_library(tmp_path):
    store = Store(tmp_path)
    with store.library_lock():
        with pytest.raises(RuntimeError, match="already open"):
            with Store(tmp_path).library_lock():
                pass
    with store.library_lock():
        pass
