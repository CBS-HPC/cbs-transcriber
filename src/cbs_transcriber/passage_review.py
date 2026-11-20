"""Passage review progress is independent of transcript content and Working roles.

Stored rows are the researcher's own marks (ADR 0148). Automatic Needs review for word
timing (ADR 0191) is derived at read time from the saved content and the timing versions'
own evidence; it is never stored as a review row and never outranks a mark bound to the
passage's exact current content.
"""

import hashlib
import json
import re
import sqlite3
import threading
from collections import OrderedDict

from .history import decode_payload, encode_payload, validate_revision
from .store import new_id, now
from .transcripts import MAX_TRANSCRIPT_BYTES

MAX_PASSAGES = 10000
STATUSES = frozenset({"not_checked", "checked", "needs_review"})
# ADR 0191: a timing successor records which passages it timed and their resulting content.
TIMING_KEY = "timing_added"
MAX_TIMING_VERSIONS = 1000
MAX_TIMING_ENTRIES = 20000
MAX_TIMING_BYTES = 4 * 1024 * 1024
_LEXICAL = re.compile(r"[^\W]")

# Pure completed content checks only: keys hash actual current bytes, values retain passage
# IDs/fingerprints and content-derived timing booleans, never transcript text, speaker
# names, mark states or mutation authority.
_CONTEXTS = OrderedDict()
_CONTEXT_LOCK = threading.Lock()
_CONTEXT_LIMIT = 8
_CONTEXT_PASSAGES = 20000


class Conflict(ValueError):
    """The displayed transcript or independent review decision changed."""


def migrate(store):
    """Schema 19 adds review metadata without observing or changing transcript content."""
    with store.library_lock(), store.connection() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] >= 19:
            return
        if store.initial_version == 18:
            (store.root / "backups").mkdir(exist_ok=True)
            backup = sqlite3.connect(store.root / "backups" / f"schema-18-{new_id()}.sqlite3")
            try:
                db.backup(backup)
            finally:
                backup.close()
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE passage_reviews (
            result_id TEXT NOT NULL REFERENCES transcript_results(id) ON DELETE CASCADE,
            segment_id TEXT NOT NULL,
            passage_sha256 TEXT NOT NULL CHECK(length(passage_sha256)=64),
            status TEXT NOT NULL CHECK(status IN ('not_checked','checked','needs_review')),
            revision INTEGER NOT NULL CHECK(revision>0),
            updated TEXT NOT NULL,
            PRIMARY KEY(result_id,segment_id)
        )""")
        db.execute("PRAGMA user_version=19")


def fingerprint(passage):
    raw = json.dumps(
        passage, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def timing_added(segments, applied):
    """Evidence for a timing successor: each timed passage's exact resulting fingerprint.

    The writer records it with the version in one transaction, so a later read can tell
    that a passage still holds exactly the content this timing produced (ADR 0191).
    """
    by_id = {passage["id"]: passage for passage in segments}
    return {
        identifier: fingerprint(json.loads(json.dumps(by_id[identifier], allow_nan=False)))
        for identifier in applied
    }


def _timing_flags(passage):
    """Content-derived booleans only: (lacks complete word timing, carries timing evidence)."""
    text = passage.get("text")
    missing = (
        passage.get("word_timing") is not True
        and isinstance(text, str)
        and _LEXICAL.search(text) is not None
    )
    evidence = bool(
        passage.get("words") or passage.get("original_words") or passage.get("word_timing")
    )
    return missing, evidence


def _context(db, recording_id, result_id, revision=None):
    recording = db.execute(
        "SELECT revision,active_transcript_id,"
        "CASE WHEN length(CAST(segments AS BLOB))+length(CAST(speakers AS BLOB))<=? "
        "THEN segments END AS segments,"
        "CASE WHEN length(CAST(segments AS BLOB))+length(CAST(speakers AS BLOB))<=? "
        "THEN speakers END AS speakers FROM recordings WHERE id=?",
        (MAX_TRANSCRIPT_BYTES, MAX_TRANSCRIPT_BYTES, recording_id),
    ).fetchone()
    if recording is None:
        raise KeyError(recording_id)
    if recording["segments"] is None or recording["speakers"] is None:
        raise ValueError("This transcript is too large to mark safely.")
    if recording["active_transcript_id"] != result_id or (
        revision is not None and recording["revision"] != revision
    ):
        raise Conflict("The saved transcript changed. Reload it before marking passages.")
    result = db.execute(
        "SELECT id,segments,speakers,"
        "CASE WHEN json_valid(source) AND json_type(source,'$.capability')='text' "
        "AND length(CAST(json_extract(source,'$.capability') AS BLOB))<=40 "
        "THEN json_extract(source,'$.capability') END AS capability,"
        "CASE WHEN json_valid(source) THEN json_type(source,'$.options.align') END AS align "
        "FROM transcript_results WHERE id=? AND recording_id=? "
        "AND id NOT IN (SELECT result_id FROM result_trash) "
        "AND length(CAST(segments AS BLOB))+length(CAST(speakers AS BLOB))<=?",
        (result_id, recording_id, MAX_TRANSCRIPT_BYTES),
    ).fetchone()
    if result is None:
        raise Conflict("The saved transcript is unavailable or too large to mark safely.")
    head = db.execute(
        "SELECT id,payload,payload_sha256 FROM (SELECT id,payload,payload_sha256 "
        "FROM result_versions WHERE result_id=? ORDER BY sequence DESC LIMIT 1) "
        "WHERE length(CAST(payload AS BLOB))<=?",
        (result_id, MAX_TRANSCRIPT_BYTES),
    ).fetchone()
    if head is None:
        raise ValueError("The saved transcript has no bounded research version.")
    result_pair = result["segments"], result["speakers"]
    recording_pair = recording["segments"], recording["speakers"]
    if any(not isinstance(value, str) for value in (*result_pair, *recording_pair)):
        raise ValueError("The saved transcript and its history disagree. Preserve the library.")
    try:
        head_hash = hashlib.sha256(head["payload"].encode("utf-8")).digest()
        if head_hash.hex() != head["payload_sha256"]:
            raise ValueError
        result_hashes = tuple(
            hashlib.sha256(value.encode("utf-8")).digest() for value in result_pair
        )
        mirror_hashes = (
            result_hashes
            if recording_pair == result_pair
            else tuple(hashlib.sha256(value.encode("utf-8")).digest() for value in recording_pair)
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("The saved research version failed its integrity check.") from exc
    key = (head_hash, *result_hashes, *mirror_hashes)
    # Mirrors expectsWordTiming() in the editor, together with the passages' own timing
    # evidence: speakers-only or untimed-by-design results get no automatic marks (ADR 0191).
    expects = result["capability"] in {"words", "words_speakers"} or result["align"] == "true"
    with _CONTEXT_LOCK:
        passages = _CONTEXTS.get(key)
        if passages is not None:
            _CONTEXTS.move_to_end(key)
    if passages is not None:
        if len(passages) > MAX_PASSAGES:
            raise ValueError("This transcript has too many passages to mark safely.")
        return recording["revision"], head["id"], passages, expects
    payload = decode_payload(head["payload"], head["payload_sha256"])
    if encode_payload(json.loads(result["segments"]), json.loads(result["speakers"]))[0] != head[
        "payload"
    ] or (
        recording_pair != result_pair
        and encode_payload(json.loads(recording["segments"]), json.loads(recording["speakers"]))[0]
        != head["payload"]
    ):
        raise ValueError("The saved transcript and its history disagree. Preserve the library.")
    passages = payload["segments"]
    if len(passages) > MAX_PASSAGES:
        raise ValueError("This transcript has too many passages to mark safely.")
    ids = [item.get("id") if isinstance(item, dict) else None for item in passages]
    if any(
        not isinstance(item, str) or not 1 <= len(item) <= 200 or "\x00" in item for item in ids
    ) or (len(set(ids)) != len(ids)):
        raise ValueError("The saved transcript has ambiguous passage identifiers.")
    passages = tuple((item["id"], fingerprint(item), *_timing_flags(item)) for item in passages)
    with _CONTEXT_LOCK:
        _CONTEXTS[key] = passages
        _CONTEXTS.move_to_end(key)
        while (
            len(_CONTEXTS) > _CONTEXT_LIMIT or sum(map(len, _CONTEXTS.values())) > _CONTEXT_PASSAGES
        ):
            _CONTEXTS.popitem(last=False)
    return recording["revision"], head["id"], passages, expects


def _rows(db, result_id, passages):
    rows = db.execute(
        "SELECT CASE WHEN length(CAST(segment_id AS BLOB))<=800 THEN segment_id END "
        "AS segment_id,CASE WHEN length(CAST(passage_sha256 AS BLOB))=64 "
        "THEN passage_sha256 END AS passage_sha256,"
        "CASE WHEN length(CAST(status AS BLOB))<=12 THEN status END AS status,revision "
        "FROM passage_reviews "
        "WHERE result_id=? AND segment_id IN (SELECT value FROM json_each(?)) LIMIT ?",
        (
            result_id,
            json.dumps([item[0] for item in passages], ensure_ascii=False),
            MAX_PASSAGES + 1,
        ),
    ).fetchall()
    if len(rows) > MAX_PASSAGES:
        raise ValueError("This transcript has too many retained review marks.")
    result = {}
    for row in rows:
        if (
            not isinstance(row["segment_id"], str)
            or not 1 <= len(row["segment_id"]) <= 200
            or "\x00" in row["segment_id"]
            or row["status"] not in STATUSES
            or type(row["revision"]) is not int
            or not 1 <= row["revision"] < 2**63
            or not isinstance(row["passage_sha256"], str)
            or re.fullmatch(r"[a-f0-9]{64}", row["passage_sha256"]) is None
        ):
            raise ValueError("The saved review marks are invalid. Preserve the library.")
        result[row["segment_id"]] = row
    return result


def _timed(db, result_id):
    """Passage ID -> fingerprints that a recorded timing successor of this result produced.

    Only real timing_realign versions count (a no-change save adds no timing). Reads the
    newest bounded versions' own evidence only; never payloads, jobs or files.
    """
    path = "$." + TIMING_KEY
    rows = db.execute(
        "SELECT CASE WHEN json_valid(context) THEN json_type(context,?) END AS kind,"
        "CASE WHEN json_valid(context) AND json_type(context,?)='object' "
        "AND length(CAST(json_extract(context,?) AS BLOB))<=? "
        "THEN json_extract(context,?) END AS added "
        "FROM result_versions WHERE result_id=? AND action='timing_realign' "
        "ORDER BY sequence DESC LIMIT ?",
        (path, path, path, MAX_TIMING_BYTES, path, result_id, MAX_TIMING_VERSIONS),
    ).fetchall()
    timed, entries, size = {}, 0, 0
    for row in rows:
        if row["kind"] is None:
            continue  # Recorded before ADR 0191: no evidence, so no automatic mark.
        try:
            added = json.loads(row["added"])
        except (TypeError, ValueError, RecursionError):
            added = None
        if (
            not isinstance(added, dict)
            or not added
            or any(
                not isinstance(key, str)
                or not 1 <= len(key) <= 200
                or "\x00" in key
                or not isinstance(value, str)
                or re.fullmatch(r"[a-f0-9]{64}", value) is None
                for key, value in added.items()
            )
        ):
            raise ValueError("The saved word-timing review evidence is invalid. Preserve it.")
        entries += len(added)
        size += len(row["added"].encode("utf-8"))
        if entries > MAX_TIMING_ENTRIES or size > MAX_TIMING_BYTES:
            break  # Older timing beyond these bounds raises no automatic marks.
        for key, value in added.items():
            timed.setdefault(key, set()).add(value)
    return timed


def _automatic(passage, expects, timed):
    identifier, digest, missing, _ = passage
    if digest in timed.get(identifier, ()):
        return "timing_added"
    return "word_timing_missing" if expects and missing else None


def _view(segment_id, digest, row, automatic=None):
    stale = row is not None and row["passage_sha256"] != digest
    status = row["status"] if row is not None else "not_checked"
    if stale and status == "checked":
        status = "needs_review"
    if row is not None and not stale:
        automatic = None  # A mark bound to this exact content is the researcher's decision.
    elif automatic is not None:
        status = "needs_review"
    return {
        "segment_id": segment_id,
        "passage_sha256": digest,
        "status": status,
        "review_revision": row["revision"] if row is not None else 0,
        "stale": stale,
        "automatic": automatic,
    }


def _response(result_id, revision, version_id, passages, rows, expects, timed):
    expects = expects or any(item[3] for item in passages)
    views = [
        _view(item[0], item[1], rows.get(item[0]), _automatic(item, expects, timed))
        for item in passages
    ]
    counts = {status: sum(item["status"] == status for item in views) for status in STATUSES}
    counts["total"] = len(views)
    return {
        "result_id": result_id,
        "revision": revision,
        "version_id": version_id,
        "read_only": False,
        "counts": counts,
        "passages": views,
    }


def read(store, recording_id, result_id):
    with store.connection() as db:
        db.execute("BEGIN")
        revision, version_id, passages, expects = _context(db, recording_id, result_id)
        return _response(
            result_id,
            revision,
            version_id,
            passages,
            _rows(db, result_id, passages),
            expects,
            _timed(db, result_id),
        )


def save(
    store, recording_id, segment_id, *, result_id, revision, status, review_revision, passage_sha256
):
    validate_revision(revision)
    validate_revision(review_revision)
    if (
        not isinstance(status, str)
        or status not in STATUSES
        or not isinstance(passage_sha256, str)
        or (re.fullmatch(r"[a-f0-9]{64}", passage_sha256) is None)
    ):
        raise ValueError("Choose a valid passage review status and fingerprint.")
    with store.snapshot_lock, store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        revision, version_id, passages, expects = _context(db, recording_id, result_id, revision)
        selected = next((item[1] for item in passages if item[0] == segment_id), None)
        if selected is None or selected != passage_sha256:
            raise Conflict("The passage changed. Reload it before marking it.")
        rows = _rows(db, result_id, passages)
        previous = rows.get(segment_id)
        actual_revision = previous["revision"] if previous is not None else 0
        if review_revision != actual_revision:
            raise Conflict("This passage's review changed in another window. Reload it.")
        if actual_revision >= 2**63 - 1:
            raise ValueError("The passage review revision limit has been reached.")
        db.execute(
            "INSERT INTO passage_reviews(result_id,segment_id,passage_sha256,"
            "status,revision,updated) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(result_id,segment_id) DO UPDATE SET "
            "passage_sha256=excluded.passage_sha256,status=excluded.status,"
            "revision=excluded.revision,updated=excluded.updated",
            (result_id, segment_id, passage_sha256, status, actual_revision + 1, now()),
        )
        response = _response(
            result_id,
            revision,
            version_id,
            passages,
            _rows(db, result_id, passages),
            expects,
            _timed(db, result_id),
        )
        response["passage"] = next(
            item for item in response.pop("passages") if item["segment_id"] == segment_id
        )
        return response
