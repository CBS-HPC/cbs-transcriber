"""Local spelling privacy, vocabulary fences, Unicode ranges and pinned cache behavior."""

import hashlib
import json
import socket
import threading

import pytest

from cbs_transcriber import spelling
from cbs_transcriber.store import Store


def tiny_catalog():
    content = {
        "aff": b"SET UTF-8\nTRY esiarntolcdugmphbyfvkwzxjq\n",
        "dic": "6\nhello\nworld\ncolour\nsprog\narbejde\nmøller\n".encode(),
        "license": b"Synthetic test dictionary licence\n",
    }
    rows = []
    for language in ("da", "en"):
        rows.append(
            {
                "language": language,
                "name": language,
                "locale": language,
                "source": {"commit": "a" * 40},
                "files": {
                    kind: {
                        "name": kind,
                        "url": "https://raw.githubusercontent.com/test/dicts/"
                        + f"{'a' * 40}/{language}/{kind}",
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                    for kind, data in content.items()
                },
                "licence": {
                    "spdx_expression": "MIT",
                    "publisher_expression": "MIT",
                    "attribution_url": "https://example.invalid/test",
                    "verification_status": "synthetic",
                    "institutional_review": "not_assessed",
                },
                "capabilities": {
                    "check": True,
                    "suggest": language == "en",
                    "max_suggestions": 5 if language == "en" else 0,
                },
            }
        )
    return {"schema_version": 1, "dictionaries": rows}, content


class FakeWorker:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.before_return = lambda: None

    def request(self, entry, folder, command, **values):
        self.calls.append((entry["language"], command, values))
        self.before_return()
        if command == "check":
            return {
                "status": "ready",
                "correct": [
                    word.lower() in {"hello", "world", "sprog", "arbejde", "møller"}
                    for word in values["words"]
                ],
            }
        return {"status": "ready", "suggestions": ["hello"]}

    def close(self):
        self.closed = True

    def reset(self):
        pass


@pytest.fixture
def local_spelling(tmp_path, monkeypatch):
    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic", default_language="en")
    recording = store.create_recording(project["id"], "synthetic.wav")
    catalog, bodies = tiny_catalog()
    worker = FakeWorker()
    service = spelling.SpellingService(store, tmp_path / "cache", catalog=catalog, worker=worker)
    downloads = []

    def retrieve(entry, target, *, guard=None):
        if guard:
            guard()
        downloads.append(entry["url"])
        target.write_bytes(
            next(
                body
                for body in bodies.values()
                if hashlib.sha256(body).hexdigest() == entry["sha256"]
            )
        )

    monkeypatch.setattr(spelling, "_download_file", retrieve)
    yield service, store, project, recording, worker, downloads
    service.close()


def enable(service, *, auto=True):
    current = service.settings()
    return service.save_settings(current["revision"], True, auto)


def install(service, recording):
    return service.install(recording["id"], service.settings()["revision"], consent=True)


def test_defaults_status_and_check_never_create_cache_or_download(local_spelling):
    service, _, _, recording, worker, downloads = local_spelling
    assert service.settings() == {"revision": 0, "enabled": False, "auto_download": True}
    assert all(not row["ready"] for row in service.dictionaries()["dictionaries"])
    assert service.check(recording["id"], "Private name", "draft1")["status"] == "disabled"
    enable(service)
    assert service.check(recording["id"], "Private name", "draft1")["status"] == "not_installed"
    assert service.suggest(recording["id"], "Privatename", "draft1")["status"] == "not_installed"
    assert not service.cache.exists() and not downloads and not worker.calls


def test_install_consent_cas_exact_hashes_and_marker_last(local_spelling, monkeypatch):
    service, _, _, recording, _, downloads = local_spelling
    preferences = enable(service, auto=False)
    with pytest.raises(spelling.Conflict, match="Confirm"):
        service.install(recording["id"], preferences["revision"])
    with pytest.raises(spelling.Conflict, match="preferences changed"):
        service.install(recording["id"], 0, consent=True)
    original = spelling._download_file

    def retrieve(entry, target, **kwargs):
        assert not (target.parent / "installed.json").exists()
        original(entry, target, **kwargs)

    monkeypatch.setattr(spelling, "_download_file", retrieve)
    assert install(service, recording)["ready"]
    assert len(downloads) == 3
    assert install(service, recording)["ready"] and len(downloads) == 3
    assert service.settings()["auto_download"] is False


def test_check_and_suggest_are_socket_free_and_leave_transcript_history_unchanged(
    local_spelling, monkeypatch
):
    service, store, _, recording, _, downloads = local_spelling
    enable(service)
    install(service, recording)
    before = store.all("SELECT * FROM recordings")

    def offline(*args, **kwargs):
        raise AssertionError("Spelling attempted outbound networking")

    monkeypatch.setattr(socket, "socket", offline)
    monkeypatch.setattr(socket, "create_connection", offline)
    monkeypatch.setattr(spelling, "_download_file", offline)
    result = service.check(recording["id"], "🧪 hello wrng e\u0301", "draft1")
    assert result["offset_unit"] == "utf16"
    assert result["flags"][0] == {"start": 9, "end": 13, "word": "wrng"}
    assert service.suggest(recording["id"], "helo", "draft2")["suggestions"] == ["hello"]
    assert len(downloads) == 3 and store.all("SELECT * FROM recordings") == before
    assert store.all("SELECT * FROM result_versions") == []


def test_tokenizer_preserves_utf16_and_ignores_annotations_addresses_numbers_labels():
    text = (
        "🧪 Ærø e\u0301 can't co-operate [inaudible] 123 abc42 "
        "https://example.invalid/a\nSpeaker_01: hello a@b.dk"
    )
    words = spelling.tokens(text)
    assert [word["word"] for word in words] == ["Ærø", "e\u0301", "can't", "co-operate", "hello"]
    encoded = text.encode("utf-16-le")
    assert all(
        encoded[word["start"] * 2 : word["end"] * 2].decode("utf-16-le") == word["word"]
        for word in words
    )
    assert spelling.tokens("x " * (spelling.MAX_TOKENS + 1)) is None
    assert spelling.tokens("x" * (spelling.MAX_TEXT + 1)) is None


def test_effective_recording_language_overrides_project_and_da_is_check_only(local_spelling):
    service, store, project, recording, worker, _ = local_spelling
    enable(service)
    store.execute(
        "UPDATE recordings SET language_override='da',language_revision=language_revision+1 "
        "WHERE id=?",
        (recording["id"],),
    )
    assert install(service, recording)["language"] == "da"
    result = service.check(recording["id"], "sprog wrng", "one")
    assert result["language"] == "da" and worker.calls[-1][0] == "da"
    count = len(worker.calls)
    result = service.suggest(recording["id"], "sproggg", "two")
    assert result["status"] == "check_only" and len(worker.calls) == count
    store.execute("UPDATE recordings SET language_override='fr' WHERE id=?", (recording["id"],))
    assert service.check(recording["id"], "hello", "three")["status"] == "unsupported"
    assert (
        store.one("SELECT default_language FROM projects WHERE id=?", (project["id"],))[
            "default_language"
        ]
        == "en"
    )


@pytest.mark.parametrize("change", ["language", "vocabulary", "preferences", "project_owner"])
def test_late_worker_response_refuses_newer_scope(local_spelling, change):
    service, store, _, recording, worker, _ = local_spelling
    enable(service)
    install(service, recording)

    def changed():
        if change == "language":
            store.execute(
                "UPDATE recordings SET language_override='da',language_revision=1 WHERE id=?",
                (recording["id"],),
            )
        elif change == "vocabulary":
            service.save_vocabulary(0, ["wrng"])
        elif change == "project_owner":
            other = store.create_project(
                "Other project with the same language", default_language="en"
            )
            store.execute(
                "UPDATE recordings SET project_id=? WHERE id=?", (other["id"], recording["id"])
            )
        else:
            service.save_settings(service.settings()["revision"], False, False)

    worker.before_return = changed
    with pytest.raises(spelling.Conflict, match="changed"):
        service.check(recording["id"], "wrng", "one")


def test_private_vocabulary_cas_layers_and_delete_helper(local_spelling):
    service, store, project, recording, _, _ = local_spelling
    enable(service)
    install(service, recording)
    assert service.save_vocabulary(0, ["Privateperson"])["revision"] == 1
    assert service.save_vocabulary(0, ["Researchname"], project["id"])["revision"] == 1
    with pytest.raises(spelling.Conflict):
        service.save_vocabulary(0, ["Otherperson"])
    result = service.check(recording["id"], "PRIVATEPERSON Researchname wrng", "one")
    assert [flag["word"] for flag in result["flags"]] == ["wrng"]
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        assert spelling.project_vocabulary_revision(db, project["id"]) == 1
        spelling.remove_project_vocabulary(db, project["id"])
        db.execute("DELETE FROM projects WHERE id=?", (project["id"],))
    with pytest.raises(KeyError):
        service.save_vocabulary(1, ["Researchname"], project["id"])
    assert service.vocabulary()["words"] == ["Privateperson"]
    assert (
        json.loads(store.one("SELECT value FROM settings WHERE id=13")["value"])["projects"] == {}
    )


@pytest.mark.parametrize(
    "words", [["x", "X"], ["two words"], ["name42"], ["-name"], ["x\ud800"], ["x" * 129]]
)
def test_vocabulary_invalid_terms_leave_state_unchanged(local_spelling, words):
    service, store, _, _, _, _ = local_spelling
    before = store.all("SELECT * FROM settings")
    with pytest.raises(ValueError):
        service.save_vocabulary(0, words)
    assert store.all("SELECT * FROM settings") == before


def test_bad_cache_never_loads_and_failed_download_never_publishes_marker(
    local_spelling, monkeypatch
):
    service, _, _, recording, worker, _ = local_spelling
    enable(service)

    def bad(entry, target, **kwargs):
        target.write_bytes(b"unverified")

    monkeypatch.setattr(spelling, "_download_file", bad)
    with pytest.raises(ValueError, match="size check"):
        install(service, recording)
    assert not list(service.cache.rglob("installed.json"))
    assert service.check(recording["id"], "wrng", "one")["status"] == "not_installed"
    assert not worker.calls


def test_remove_after_close_is_refused(local_spelling):
    service, _, _, recording, _, _ = local_spelling
    install(service, recording)
    service.close()
    with pytest.raises(ValueError, match="closing"):
        service.remove("en", 0)
    assert service.dictionaries()["dictionaries"][1]["ready"]


def test_close_cancels_and_drains_public_download(local_spelling, monkeypatch):
    service, _, _, recording, worker, _ = local_spelling
    entered, cancelled, outcome = threading.Event(), threading.Event(), []

    def slow(entry, target, *, guard):
        entered.set()
        while not cancelled.wait(0.005):
            try:
                guard()
            except ValueError:
                cancelled.set()
                raise

    def retrieve():
        try:
            install(service, recording)
        except ValueError:
            outcome.append("cancelled")

    monkeypatch.setattr(spelling, "_download_file", slow)
    thread = threading.Thread(target=retrieve)
    thread.start()
    assert entered.wait(2)
    service.close()
    thread.join(2)
    assert not thread.is_alive() and outcome == ["cancelled"] and worker.closed
    assert not list(service.cache.rglob("installed.json"))


def test_missing_engine_reports_unavailable_without_worker_or_download(local_spelling, monkeypatch):
    service, _, _, recording, worker, downloads = local_spelling
    enable(service)
    install(service, recording)
    before = list(downloads)
    find_spec = spelling.importlib.util.find_spec
    monkeypatch.setattr(
        spelling.importlib.util,
        "find_spec",
        lambda name, *args, **kwargs: (
            None if name == "spylls" else find_spec(name, *args, **kwargs)
        ),
    )
    assert service.check(recording["id"], "wrng", "missing-check")["status"] == "engine_missing"
    assert service.suggest(recording["id"], "wrng", "missing-suggest")["status"] == "engine_missing"
    assert not worker.calls and downloads == before
