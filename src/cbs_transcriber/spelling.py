"""Local draft spelling assistance; retrieval is an explicit, separate operation."""

import hashlib
import importlib.util
import json
import os
import queue
import re
import stat
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from platformdirs import user_cache_path

from . import language_preferences

# Settings rows 10 and 11 hold checked-provider receipts and runtime generations in v2; the
# preview's rows moved to 12 (operational preferences) and 13 (private vocabulary, ADR 0185).
SETTINGS_ID, VOCABULARY_ID = 12, 13
MAX_VOCABULARY_BYTES = 4 * 1024 * 1024
MAX_WORDS, MAX_PROJECTS, MAX_WORD = 5000, 1024, 128
MAX_TEXT, MAX_TOKENS, MAX_FLAGS = 20_000, 2048, 100
MAX_DICTIONARY_BYTES = 16 * 1024 * 1024
CONNECTORS = frozenset("'’-‐‑")
_PROJECT = re.compile(r"[0-9a-f]{32}\Z")
_SKIP = re.compile(
    r"https?://[^\s]+|www\.[^\s]+|[^\s@]{1,256}@[^\s@.]{1,128}(?:\.[^\s@.]{1,64})+|\[[^\]\r\n]*\]"
    r"|(?im:^[ \t]*speaker[ _-]*\d+[ \t]*:)"
)


class Conflict(ValueError):
    """A spelling setting, vocabulary or effective language changed."""


class AdditionsDisabled(Exception):
    """A hidden spelling feature may remove private words but never add new ones."""


def default_cache_dir():
    """Disposable public dictionaries, outside every library (tests redirect this)."""
    return user_cache_path("CBS Transcriber") / "Spelling"


def _revision(value):
    if type(value) is not int or not 0 <= value < 2**63:
        raise ValueError("Use a valid spelling revision.")
    return value


def _key(word):
    return unicodedata.normalize("NFC", word).casefold()


def _letter(char):
    return unicodedata.category(char).startswith("L")


def _mark(char):
    return unicodedata.category(char).startswith("M")


def _word(word):
    if not isinstance(word, str) or not 1 <= len(word) <= MAX_WORD:
        raise ValueError("Vocabulary entries must be individual words of at most 128 characters.")
    word.encode("utf-8")
    if not _letter(word[0]) or not (_letter(word[-1]) or _mark(word[-1])):
        raise ValueError("Vocabulary entries must be individual words.")
    for index, char in enumerate(word):
        if _letter(char) or _mark(char):
            continue
        if not (
            char in CONNECTORS
            and 0 < index < len(word) - 1
            and (_letter(word[index - 1]) or _mark(word[index - 1]))
            and _letter(word[index + 1])
        ):
            raise ValueError("Vocabulary entries must be individual words.")
    return word


def _scope(value):
    if type(value) is not dict or set(value) != {"revision", "words"}:
        raise ValueError("The saved spelling vocabulary is invalid.")
    _revision(value["revision"])
    words = value["words"]
    if type(words) is not list or len(words) > MAX_WORDS:
        raise ValueError("A spelling vocabulary may contain at most 5000 words.")
    seen = set()
    for word in words:
        key = _key(_word(word))
        if key in seen:
            raise ValueError("The spelling vocabulary contains a duplicate word.")
        seen.add(key)
    return value


def _setting(db, identifier, limit):
    row = db.execute(
        "SELECT length(CAST(value AS BLOB)) AS bytes,"
        "CASE WHEN length(CAST(value AS BLOB))<=? THEN value ELSE NULL END "
        "FROM settings WHERE id=?",
        (limit, identifier),
    ).fetchone()
    if row is None:
        return None
    if type(row[0]) is not int or row[0] > limit or not isinstance(row[1], str):
        raise ValueError("The saved spelling settings exceed their supported size.")
    return json.loads(row[1])


def _preferences(db):
    value = _setting(db, SETTINGS_ID, 1024)
    if value is None:
        return {"revision": 0, "enabled": False, "auto_download": True}
    if type(value) is not dict or set(value) != {"revision", "enabled", "auto_download"}:
        raise ValueError("The saved spelling preferences are invalid.")
    _revision(value["revision"])
    if type(value["enabled"]) is not bool or type(value["auto_download"]) is not bool:
        raise ValueError("The saved spelling preferences are invalid.")
    return value


def _vocabularies(db):
    value = _setting(db, VOCABULARY_ID, MAX_VOCABULARY_BYTES)
    if value is None:
        return {"schema": 1, "personal": {"revision": 0, "words": []}, "projects": {}}
    if (
        type(value) is not dict
        or set(value) != {"schema", "personal", "projects"}
        or type(value["schema"]) is not int
        or value["schema"] != 1
        or type(value["projects"]) is not dict
        or len(value["projects"]) > MAX_PROJECTS
    ):
        raise ValueError("The saved spelling vocabulary is invalid.")
    _scope(value["personal"])
    for identifier, scope in value["projects"].items():
        if not isinstance(identifier, str) or not _PROJECT.fullmatch(identifier):
            raise ValueError("The saved project vocabulary is invalid.")
        _scope(scope)
    if value["projects"]:
        placeholders = ",".join("?" for _ in value["projects"])
        live = {
            row[0]
            for row in db.execute(
                f"SELECT id FROM projects WHERE id IN ({placeholders})", tuple(value["projects"])
            )
        }
        if live != set(value["projects"]):
            raise ValueError("A saved spelling vocabulary has no owning project.")
    return value


def _write(db, identifier, value):
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(body.encode("utf-8")) > MAX_VOCABULARY_BYTES:
        raise ValueError("The library spelling vocabulary exceeds its supported size.")
    db.execute(
        "INSERT INTO settings(id,value) VALUES (?,?) "
        "ON CONFLICT(id) DO UPDATE SET value=excluded.value",
        (identifier, body),
    )


def project_vocabulary_revision(db, project_id):
    return _vocabularies(db)["projects"].get(project_id, {"revision": 0})["revision"]


def remove_project_vocabulary(db, project_id):
    """Call inside the same writer transaction as logical project deletion."""
    value = _vocabularies(db)
    if value["projects"].pop(project_id, None) is not None:
        _write(db, VOCABULARY_ID, value)


def tokens(text):
    """Unicode words, with exact JS UTF-16 ranges; no normalization of draft offsets."""
    if not isinstance(text, str):
        raise ValueError("Supply passage text.")
    if len(text) > MAX_TEXT:
        return None
    text.encode("utf-8")
    excluded = [(match.start(), match.end()) for match in _SKIP.finditer(text)]
    offsets, units = [0], 0
    for char in text:
        units += 2 if ord(char) > 0xFFFF else 1
        offsets.append(units)
    result, index, skip = [], 0, 0
    while index < len(text):
        while skip < len(excluded) and excluded[skip][1] <= index:
            skip += 1
        if skip < len(excluded) and excluded[skip][0] <= index < excluded[skip][1]:
            index = excluded[skip][1]
            continue
        if not _letter(text[index]):
            index += 1
            continue
        start = index
        index += 1
        while index < len(text):
            char = text[index]
            if _letter(char) or _mark(char):
                index += 1
            elif char in CONNECTORS and index + 1 < len(text) and _letter(text[index + 1]):
                index += 1
            else:
                break
        # Ignore alphanumeric identifiers/numbers, including their word-like prefix/suffix.
        if (start and text[start - 1].isalnum()) or (index < len(text) and text[index].isalnum()):
            while index < len(text) and text[index].isalnum():
                index += 1
            continue
        word = text[start:index]
        if len(word) <= MAX_WORD:
            result.append({"start": offsets[start], "end": offsets[index], "word": word})
        if len(result) > MAX_TOKENS:
            return None
    return result


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _plain(path, *, directory=False):
    for parent in (*reversed(path.parents), path):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Spelling dictionary storage must not use filesystem links.")
        if parent != path or directory:
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("Spelling dictionary storage is not a directory.")
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Spelling dictionaries must be regular, unshared files.")
    return info


def _verified(path, expected):
    before = _plain(path)
    if before.st_size != expected["bytes"]:
        raise ValueError("A spelling dictionary failed its size check.")
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as stream:
        if _signature(os.fstat(stream.fileno())) != _signature(before):
            raise ValueError("A spelling dictionary changed while opening.")
        while block := stream.read(64 * 1024):
            count += len(block)
            if count > expected["bytes"]:
                raise ValueError("A spelling dictionary exceeds its pinned size.")
            digest.update(block)
    if digest.hexdigest() != expected["sha256"] or _signature(_plain(path)) != _signature(before):
        raise ValueError("A spelling dictionary failed its integrity check.")


def _catalog(value=None):
    if value is None:
        value = json.loads(
            files("cbs_transcriber").joinpath("catalogs/spelling_dictionaries.json").read_bytes()
        )
    if type(value) is not dict or value.get("schema_version") != 1:
        raise ValueError("Unsupported spelling dictionary catalog.")
    rows = value.get("dictionaries")
    if type(rows) is not list or not 1 <= len(rows) <= 128:
        raise ValueError("Invalid spelling dictionary catalog.")
    result = {}
    for row in rows:
        language = row.get("language")
        if (
            not isinstance(language, str)
            or not re.fullmatch(r"[a-z]{2,8}(?:-[A-Za-z0-9]{2,8})*", language)
            or language in result
        ):
            raise ValueError("Invalid spelling dictionary language.")
        commit = row.get("source", {}).get("commit")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Spelling dictionaries must pin a publisher commit.")
        for kind in ("aff", "dic", "license"):
            entry = row.get("files", {}).get(kind, {})
            url = urlsplit(entry.get("url", ""))
            if (
                url.scheme != "https"
                or url.netloc != "raw.githubusercontent.com"
                or url.query
                or url.fragment
                or "/" + commit + "/" not in url.path
                or type(entry.get("bytes")) is not int
                or not 1 <= entry["bytes"] <= MAX_DICTIONARY_BYTES
                or not isinstance(entry.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
            ):
                raise ValueError("Invalid pinned spelling dictionary file.")
        capabilities = row.get("capabilities")
        if (
            type(capabilities) is not dict
            or set(capabilities) != {"check", "suggest", "max_suggestions"}
            or capabilities["check"] is not True
            or type(capabilities["suggest"]) is not bool
            or type(capabilities["max_suggestions"]) is not int
            or not 0 <= capabilities["max_suggestions"] <= 5
            or capabilities["suggest"] != (capabilities["max_suggestions"] > 0)
        ):
            raise ValueError("Invalid spelling dictionary capabilities.")
        body = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        if len(body) > 16 * 1024:
            raise ValueError("Spelling dictionary metadata exceeds its limit.")
        result[language] = {**row, "catalog_sha256": hashlib.sha256(body).hexdigest()}
    return result


def _download_file(entry, target, *, guard=None):
    """Public pinned bytes only: no caller URL, credentials, proxies, redirects or text."""
    digest, count, deadline = hashlib.sha256(), 0, time.monotonic() + 45

    def current():
        if guard is not None:
            guard()
        if time.monotonic() >= deadline:
            raise ValueError("The public spelling dictionary download took too long.")

    current()
    with httpx.Client(timeout=httpx.Timeout(10), follow_redirects=False, trust_env=False) as client:
        with client.stream(
            "GET", entry["url"], headers={"Accept-Encoding": "identity"}
        ) as response:
            if (
                response.status_code != 200
                or response.headers.get("content-encoding", "identity") != "identity"
            ):
                raise ValueError("The public spelling dictionary download was unavailable.")
            with target.open("xb") as output:
                for block in response.iter_raw():
                    current()
                    count += len(block)
                    if count > entry["bytes"]:
                        raise ValueError("The spelling dictionary exceeded its pinned size.")
                    digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
    if count != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
        raise ValueError("The downloaded spelling dictionary failed its integrity check.")


class _Worker:
    """One lazy child; one bounded request, one reader, and kill/reap on any timeout."""

    def __init__(self):
        self.lock = threading.Lock()
        self.process = self.reader = self.responses = self.key = None
        self.closed = False

    def _stop(self):
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)
        process.stdin.close()
        if self.reader is not None and self.reader.ident is not None:
            self.reader.join(timeout=2)
            if self.reader.is_alive():
                raise RuntimeError("The spelling reader has not stopped.")
        process.stdout.close()
        self.process = self.reader = self.responses = self.key = None

    def _start(self):
        environment = {
            name: os.environ[name]
            for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
            if name in os.environ
        }
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-X",
                "utf8",
                str(Path(__file__).with_name("spelling_worker.py")),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        responses = queue.Queue(maxsize=1)

        def read():
            while True:
                line = process.stdout.readline(512 * 1024 + 1)
                try:
                    responses.put_nowait(line)
                except queue.Full:
                    return
                if not line or len(line) > 512 * 1024:
                    return

        self.process, self.responses = process, responses
        try:
            self.reader = threading.Thread(target=read, name="spelling-reader", daemon=True)
            self.reader.start()
        except BaseException:
            self._stop()
            raise

    def _exchange(self, payload, timeout):
        payload = {"id": uuid.uuid4().hex, **payload}
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        if len(body) > 512 * 1024:
            raise ValueError("Spelling request exceeds its supported size.")
        process = self.process

        def expire():
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass

        timer = threading.Timer(timeout, expire)
        timer.start()
        try:
            process.stdin.write(body)
            process.stdin.flush()
            line = self.responses.get(timeout=timeout)
            if not line or len(line) > 512 * 1024 or not line.endswith(b"\n"):
                raise ValueError("Spelling worker unavailable.")
            result = json.loads(line)
            if type(result) is not dict or result.get("id") != payload["id"] or "error" in result:
                raise ValueError("Spelling worker unavailable.")
            return result
        finally:
            timer.cancel()
            timer.join()

    def request(self, entry, folder, command, **values):
        if not self.lock.acquire(blocking=False):
            return {"status": "busy"}
        try:
            if self.closed:
                return {"status": "unavailable"}
            key = entry["catalog_sha256"]
            if self.process is None or self.key != key:
                self._stop()
                self._start()
                self._exchange(
                    {"command": "load", "key": key, "folder": str(folder), "files": entry["files"]},
                    10,
                )
                self.key = key
            result = self._exchange(
                {"command": command, "key": key, **values}, 1 if command == "check" else 0.5
            )
            return {"status": "ready", **{k: v for k, v in result.items() if k != "id"}}
        except (ValueError, OSError, RuntimeError, queue.Empty, subprocess.TimeoutExpired):
            self._stop()
            return {"status": "timeout"}
        finally:
            self.lock.release()

    def close(self):
        with self.lock:
            self.closed = True
            self._stop()

    def reset(self):
        with self.lock:
            self._stop()


class SpellingService:
    """Library-private preferences/vocabulary with a separate public dictionary cache."""

    def __init__(self, store, cache_dir, *, catalog=None, worker=None):
        self.store = store
        self.cache = Path(cache_dir).absolute()
        if ".." in self.cache.parts:
            raise ValueError("Choose an absolute spelling cache folder.")
        self.catalog = _catalog(catalog)
        self.worker = worker or _Worker()
        self.cache_lock = threading.Lock()
        self.closed = False

    def close(self):
        self.closed = True
        with self.cache_lock:
            self.worker.close()

    def settings(self):
        with self.store.connection() as db:
            return _preferences(db)

    def save_settings(self, revision, enabled, auto_download):
        _revision(revision)
        if type(enabled) is not bool or type(auto_download) is not bool:
            raise ValueError("Choose valid spelling preferences.")
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if _preferences(db)["revision"] != revision:
                raise Conflict("Spelling preferences changed. Read them again.")
            value = {"revision": revision + 1, "enabled": enabled, "auto_download": auto_download}
            _revision(value["revision"])
            _write(db, SETTINGS_ID, value)
        return value

    def vocabulary(self, project_id=None):
        with self.store.connection() as db:
            db.execute("BEGIN")
            if (
                project_id is not None
                and not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone()
            ):
                raise KeyError(project_id)
            value = _vocabularies(db)
            return (
                value["personal"]
                if project_id is None
                else value["projects"].get(project_id, {"revision": 0, "words": []})
            )

    def save_vocabulary(self, revision, words, project_id=None, *, additions=True):
        """CAS-save one scope; ``additions=False`` (hidden feature) permits removals only."""
        _scope({"revision": revision, "words": words})
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                project_id is not None
                and not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone()
            ):
                raise KeyError(project_id)
            value = _vocabularies(db)
            current = (
                value["personal"]
                if project_id is None
                else value["projects"].get(project_id, {"revision": 0, "words": []})
            )
            if current["revision"] != revision:
                raise Conflict("Spelling vocabulary changed. Read it again.")
            # Checked against the exact saved words in the same writer transaction.
            if not additions and not {_key(word) for word in words} <= {
                _key(word) for word in current["words"]
            }:
                raise AdditionsDisabled()
            scope = {"revision": _revision(revision + 1), "words": sorted(words, key=_key)}
            if project_id is None:
                value["personal"] = scope
            else:
                value["projects"][project_id] = scope
                if len(value["projects"]) > MAX_PROJECTS:
                    raise ValueError("The project vocabulary count exceeds its supported limit.")
            _write(db, VOCABULARY_ID, value)
        return scope

    def _context(self, recording_id):
        with self.store.connection() as db:
            db.execute("BEGIN")
            row = db.execute(
                "SELECT r.id,r.project_id,r.language_override,r.language_revision,"
                "p.default_language,p.language_revision AS project_revision "
                "FROM recordings r JOIN projects p ON p.id=r.project_id WHERE r.id=?",
                (recording_id,),
            ).fetchone()
            if row is None:
                raise KeyError(recording_id)
            language_guard = {
                "project_language": row["default_language"],
                "project_revision": row["project_revision"],
                "recording_language": row["language_override"],
                "recording_revision": row["language_revision"],
            }
            selected = language_preferences.selected(language_guard)
            vocab = _vocabularies(db)
            personal = vocab["personal"]
            project = vocab["projects"].get(row["project_id"], {"revision": 0, "words": []})
            return {
                "recording_id": recording_id,
                "project_id": row["project_id"],
                "language": selected["language"] if selected else None,
                "language_guard": language_guard,
                "preferences": _preferences(db),
                "vocabulary_revision": {
                    "personal": personal["revision"],
                    "project": project["revision"],
                },
                "words": {_key(w) for w in [*personal["words"], *project["words"]]},
            }

    def _current(self, context):
        current = self._context(context["recording_id"])
        if any(
            current[key] != context[key]
            for key in ("project_id", "language_guard", "preferences", "vocabulary_revision")
        ):
            raise Conflict("The spelling language or vocabulary changed. Check the draft again.")

    def _folder(self, entry):
        return self.cache / entry["language"] / entry["catalog_sha256"]

    def _ready(self, entry):
        folder = self._folder(entry)
        try:
            _plain(folder, directory=True)
            marker = folder / "installed.json"
            if _plain(marker).st_size > 1024:
                return False
            if json.loads(marker.read_bytes()) != {
                "format": "cbs-spelling-dictionary-v1",
                "catalog_sha256": entry["catalog_sha256"],
            }:
                return False
            for kind, name in (
                ("aff", "dictionary.aff"),
                ("dic", "dictionary.dic"),
                ("license", "license.txt"),
            ):
                _verified(folder / name, entry["files"][kind])
            return True
        except (OSError, ValueError, TypeError):
            return False

    def _metadata(self, entry):
        ready = self._ready(entry)
        try:
            self._folder(entry).lstat()
            installed = True
        except FileNotFoundError:
            installed = False
        return {
            key: entry[key]
            for key in ("language", "name", "locale", "licence", "capabilities", "catalog_sha256")
        } | {
            "ready": ready,
            "installed": installed,
            "status": "ready" if ready else "not_installed",
            "download_bytes": sum(file["bytes"] for file in entry["files"].values()),
        }

    def dictionaries(self):
        return {"dictionaries": [self._metadata(entry) for entry in self.catalog.values()]}

    def install(self, recording_id, settings_revision, consent=False):
        context = self._context(recording_id)
        _revision(settings_revision)
        if type(consent) is not bool:
            raise ValueError("Choose whether to download this dictionary.")
        if context["preferences"]["revision"] != settings_revision:
            raise Conflict("Spelling preferences changed. Read them again.")
        if not context["preferences"]["auto_download"] and not consent:
            raise Conflict("Confirm downloading this public spelling dictionary first.")
        entry = self.catalog.get(context["language"])
        if entry is None:
            raise ValueError("No pinned dictionary is available for the recording language.")
        if not self.cache_lock.acquire(blocking=False):
            return {"status": "busy"}
        staging = None
        try:
            if self.closed:
                raise ValueError("The spelling service is closing.")
            if self._ready(entry):
                return self._metadata(entry)
            folder = self._folder(entry)
            if folder.exists():
                raise ValueError("Remove the unverified dictionary cache before installing again.")
            # Constructor/status/check never create storage; explicit retrieval alone does.
            for parent in (*reversed(self.cache.parents), self.cache):
                if parent.exists():
                    _plain(parent, directory=True)
            folder.parent.mkdir(parents=True, exist_ok=True)
            _plain(folder.parent, directory=True)
            staging = folder.parent / ("." + uuid.uuid4().hex + ".partial")
            staging.mkdir()

            def guard():
                if self.closed:
                    raise ValueError("The spelling service is closing.")

            for kind, name in (
                ("aff", "dictionary.aff"),
                ("dic", "dictionary.dic"),
                ("license", "license.txt"),
            ):
                guard()
                target = staging / name
                _download_file(entry["files"][kind], target, guard=guard)
                _verified(target, entry["files"][kind])
            self._current(context)
            guard()
            marker = staging / "installed.json"
            with marker.open("x", encoding="utf-8") as stream:
                json.dump(
                    {
                        "format": "cbs-spelling-dictionary-v1",
                        "catalog_sha256": entry["catalog_sha256"],
                    },
                    stream,
                )
                stream.flush()
                os.fsync(stream.fileno())
            _plain(staging, directory=True)
            if folder.exists():
                raise Conflict("The dictionary cache changed. Check it again.")
            staging.rename(folder)
            staging = None
            return self._metadata(entry)
        finally:
            if staging is not None:
                for name in ("dictionary.aff", "dictionary.dic", "license.txt", "installed.json"):
                    try:
                        (staging / name).unlink(missing_ok=True)
                    except OSError:
                        pass
                try:
                    staging.rmdir()
                except OSError:
                    pass
            self.cache_lock.release()

    def remove(self, language, settings_revision):
        _revision(settings_revision)
        if self.settings()["revision"] != settings_revision:
            raise Conflict("Spelling preferences changed. Read them again.")
        entry = self.catalog.get(language)
        if entry is None:
            raise ValueError("Choose an available spelling dictionary.")
        with self.cache_lock:
            if self.closed:
                raise ValueError("The spelling service is closing.")
            self.worker.reset()
            folder = self._folder(entry)
            if not folder.exists():
                return {"language": language, "status": "not_installed", "ready": False}
            owned = _plain(folder, directory=True)
            identity = owned.st_dev, owned.st_ino
            allowed = {"dictionary.aff", "dictionary.dic", "license.txt", "installed.json"}
            names = {path.name for path in folder.iterdir()}
            if "installed.json" not in names or not names <= allowed:
                raise ValueError("Dictionary storage contains unverified extra files.")
            marker = folder / "installed.json"
            if _plain(marker).st_size > 1024 or json.loads(marker.read_bytes()) != {
                "format": "cbs-spelling-dictionary-v1",
                "catalog_sha256": entry["catalog_sha256"],
            }:
                raise ValueError("The dictionary ownership marker is invalid.")
            identities = {name: _signature(_plain(folder / name)) for name in names}
            # Atomically detach this exact generation before unlinking. A late replacement
            # link moves as a link and then fails validation; its destination is never read.
            from .deletion_files import _rename

            staging = self.cache / (".removing-" + uuid.uuid4().hex)
            _plain(self.cache, directory=True)
            _rename(folder, staging)
            try:
                moved = _plain(staging, directory=True)
                if (moved.st_dev, moved.st_ino) != identity:
                    raise ValueError("Dictionary storage changed before removal.")
                for name in [*sorted(names - {"installed.json"}), "installed.json"]:
                    moved = _plain(staging, directory=True)
                    if (moved.st_dev, moved.st_ino) != identity or _signature(
                        _plain(staging / name)
                    ) != identities[name]:
                        raise ValueError("Dictionary storage changed during removal.")
                    (staging / name).unlink()
                moved = _plain(staging, directory=True)
                if (moved.st_dev, moved.st_ino) != identity:
                    raise ValueError("Dictionary storage changed during removal.")
                staging.rmdir()
            except (OSError, ValueError):
                # Restore the detached entry when possible; never recursively delete an
                # unknown replacement or follow it to finish a failed public-cache removal.
                if not folder.exists() and staging.exists():
                    _rename(staging, folder)
                raise
            return self._metadata(entry)

    def check(self, recording_id, text, request_id):
        context = self._context(recording_id)
        answer = {
            key: context[key]
            for key in ("recording_id", "language", "language_guard", "vocabulary_revision")
        }
        answer.update(request_id=request_id, offset_unit="utf16", flags=[], status="ready")
        if self.closed or not context["preferences"]["enabled"]:
            return {**answer, "status": "disabled"}
        found = tokens(text)
        if found is None:
            return {**answer, "status": "too_large"}
        if not context["language"]:
            return {**answer, "status": "no_language"}
        entry = self.catalog.get(context["language"])
        if entry is None:
            return {**answer, "status": "unsupported"}
        answer["dictionary"] = self._metadata(entry)
        if not answer["dictionary"]["ready"]:
            return {**answer, "status": "not_installed"}
        if importlib.util.find_spec("spylls") is None:
            return {**answer, "status": "engine_missing"}
        words = list(
            dict.fromkeys(
                unicodedata.normalize("NFC", item["word"])
                for item in found
                if _key(item["word"]) not in context["words"]
            )
        )
        if not words:
            self._current(context)
            return answer
        result = self.worker.request(entry, self._folder(entry), "check", words=words)
        self._current(context)
        if result["status"] != "ready":
            return {**answer, "status": result["status"]}
        correct = result.get("correct")
        if (
            type(correct) is not list
            or len(correct) != len(words)
            or any(type(value) is not bool for value in correct)
        ):
            return {**answer, "status": "unavailable"}
        bad = {word for word, okay in zip(words, correct, strict=True) if not okay}
        flags = [
            item
            for item in found
            if _key(item["word"]) not in context["words"]
            and unicodedata.normalize("NFC", item["word"]) in bad
        ]
        return {**answer, "flags": flags[:MAX_FLAGS], "truncated": len(flags) > MAX_FLAGS}

    def suggest(self, recording_id, word, request_id):
        _word(word)
        context = self._context(recording_id)
        answer = {
            key: context[key]
            for key in ("recording_id", "language", "language_guard", "vocabulary_revision")
        }
        answer.update(request_id=request_id, offset_unit="utf16", suggestions=[], status="ready")
        if self.closed or not context["preferences"]["enabled"]:
            return {**answer, "status": "disabled"}
        entry = self.catalog.get(context["language"])
        if entry is None:
            return {**answer, "status": "unsupported" if context["language"] else "no_language"}
        answer["dictionary"] = self._metadata(entry)
        if not entry["capabilities"]["suggest"]:
            return {**answer, "status": "check_only"}
        if not answer["dictionary"]["ready"]:
            return {**answer, "status": "not_installed"}
        if importlib.util.find_spec("spylls") is None:
            return {**answer, "status": "engine_missing"}
        result = self.worker.request(
            entry,
            self._folder(entry),
            "suggest",
            word=unicodedata.normalize("NFC", word),
            # The preferred result is fast; exhausting lower-ranked alternatives can
            # exceed the watchdog and discard that useful result. MVP requests one.
            count=1,
        )
        self._current(context)
        suggestions = result.get("suggestions", [])
        if (
            type(suggestions) is not list
            or len(suggestions) > 5
            or any(not isinstance(value, str) or len(value) > 256 for value in suggestions)
        ):
            return {**answer, "status": "unavailable"}
        return {**answer, "status": result["status"], "suggestions": suggestions}
