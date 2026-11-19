"""Retain a useful preferred suggestion without exhausting a costly generator."""

import threading
from pathlib import Path

import pytest
from test_spelling_worker import service

from cbs_transcriber import spelling


@pytest.mark.parametrize("word,expected", [("langauge", "language"), ("interveiw", "interview")])
def test_preferred_suggestion_never_requests_the_slow_second_candidate(
    tmp_path, monkeypatch, word, expected
):
    value, recording = service(tmp_path, monkeypatch)
    worker_path = Path(spelling.__file__).with_name("spelling_worker.py")
    script = tmp_path / "spelling_worker.py"
    second_next = tmp_path / "second-next.txt"
    script.write_text(
        "import runpy,sys,types,time\n"
        "from pathlib import Path\n"
        "package=types.ModuleType('spylls');package.__path__=[]\n"
        "engine=types.ModuleType('spylls.hunspell')\n"
        "class Dictionary:\n"
        " @classmethod\n"
        " def from_files(cls,path):return cls()\n"
        " def suggest(self,word):\n"
        "  yield {'langauge':'language','interveiw':'interview'}[word]\n"
        f"  Path({str(second_next)!r}).write_text('second next requested')\n"
        "  time.sleep(30)\n"
        "engine.Dictionary=Dictionary\n"
        "sys.modules['spylls']=package;sys.modules['spylls.hunspell']=engine\n"
        f"runpy.run_path({str(worker_path)!r},run_name='__main__')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(spelling, "__file__", str(tmp_path / "spelling.py"))
    assert value.catalog["en"]["capabilities"]["max_suggestions"] == 5
    before = {
        str(path.relative_to(value.cache)): path.read_bytes()
        for path in value.cache.rglob("*")
        if path.is_file()
    }
    try:
        result = value.suggest(recording["id"], word, "preferred-one")
        assert result["status"] == "ready", result
        assert result["suggestions"] == [expected]
        assert not second_next.exists()
        process = value.worker.process
        # A second user action reuses the warm dictionary; no automatic retry is involved.
        again = value.suggest(recording["id"], word, "preferred-two")
        assert again["status"] == "ready" and again["suggestions"] == [expected]
        assert value.worker.process is process and not second_next.exists()
        assert {
            str(path.relative_to(value.cache)): path.read_bytes()
            for path in value.cache.rglob("*")
            if path.is_file()
        } == before
    finally:
        value.close()
    assert not [thread for thread in threading.enumerate() if thread.name == "spelling-reader"]
