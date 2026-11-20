"""Contained Spylls loading, credential isolation and hard timeout cleanup."""

import threading
import time

from test_spelling import tiny_catalog

from cbs_transcriber import spelling
from cbs_transcriber.store import Store


def service(tmp_path, monkeypatch):
    catalog, bodies = tiny_catalog()
    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic", default_language="en")
    recording = store.create_recording(project["id"], "synthetic.wav")
    value = spelling.SpellingService(store, tmp_path / "cache", catalog=catalog)

    def retrieve(entry, target, *, guard=None):
        if guard:
            guard()
        target.write_bytes(
            next(
                body
                for body in bodies.values()
                if spelling.hashlib.sha256(body).hexdigest() == entry["sha256"]
            )
        )

    monkeypatch.setattr(spelling, "_download_file", retrieve)
    value.save_settings(0, True, True)
    value.install(recording["id"], 1)
    return value, recording


def test_real_tiny_spylls_child_is_reused_without_credentials_or_private_files(
    tmp_path, monkeypatch
):
    value, recording = service(tmp_path, monkeypatch)
    monkeypatch.setenv("SYNTHETIC_PRIVATE_TOKEN", "do-not-inherit-this-canary")
    original, calls = spelling.subprocess.Popen, []

    def start(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(spelling.subprocess, "Popen", start)
    cached = {str(path): path.read_bytes() for path in value.cache.rglob("*") if path.is_file()}
    try:
        answer = value.check(recording["id"], "hello wrng PrivatePerson", "check1")
        assert answer["status"] == "ready"
        assert [item["word"] for item in answer["flags"]] == ["wrng", "PrivatePerson"]
        process = value.worker.process
        assert value.suggest(recording["id"], "helo", "suggest1")["suggestions"] == ["hello"]
        assert value.worker.process is process and len(calls) == 1
        environment = calls[0][1]["env"]
        assert set(environment) <= {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
        assert "SYNTHETIC_PRIVATE_TOKEN" not in environment
        assert all(word not in repr(calls) for word in ("PrivatePerson", "wrng", "do-not-inherit"))
        assert {
            str(path): path.read_bytes() for path in value.cache.rglob("*") if path.is_file()
        } == cached
    finally:
        value.close()
    assert process.poll() is not None and value.worker.process is None
    assert not [thread for thread in threading.enumerate() if thread.name == "spelling-reader"]


def test_hung_lookup_is_killed_reaped_and_reader_drained(tmp_path, monkeypatch):
    value, _ = service(tmp_path, monkeypatch)
    script = tmp_path / "spelling_worker.py"
    script.write_text(
        "import json,sys,time\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line)\n"
        " if r['command']!='load': time.sleep(60)\n"
        " print(json.dumps({'id':r['id'],'ready':True}),flush=True)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(spelling, "__file__", str(tmp_path / "spelling.py"))
    entry = value.catalog["en"]
    original, accepted = spelling.subprocess.Popen, []

    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        accepted.append(process)
        return process

    monkeypatch.setattr(spelling.subprocess, "Popen", start)
    started = time.monotonic()
    try:
        result = value.worker.request(entry, value._folder(entry), "check", words=["private"])
        assert result["status"] == "timeout"
        assert time.monotonic() - started < 6
        assert len(accepted) == 1 and accepted[0].poll() is not None
        assert value.worker.process is None
        assert not [thread for thread in threading.enumerate() if thread.name == "spelling-reader"]
    finally:
        value.close()


def test_busy_request_never_spawns_or_queues_private_words(tmp_path, monkeypatch):
    value, _ = service(tmp_path, monkeypatch)
    entry = value.catalog["en"]
    value.worker.lock.acquire()
    try:
        assert value.worker.request(entry, value._folder(entry), "check", words=["Private"]) == {
            "status": "busy"
        }
        assert value.worker.process is None
    finally:
        value.worker.lock.release()
        value.close()
