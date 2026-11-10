"""Synthetic, entirely offline topic evidence and immutable review regressions."""

import copy
import sqlite3

import pytest
from prior_schema import remove_later_additions, without_added_languages

from cbs_transcriber import annotations
from cbs_transcriber.store import SCHEMA_VERSION, Store
from cbs_transcriber.transcripts import normalize_segments

TABLES = ("annotation_review_events", "annotation_items", "annotation_runs", "codebook_versions")


def definition(*, approved=True):
    return {
        "approved": approved,
        "topics": [
            {
                "id": "flexibility",
                "name": "Flexibility",
                "definition": "Evaluations of flexibility",
            },
            {"id": "speed", "name": "Speed", "definition": "Evaluations of performance"},
        ],
    }


@pytest.fixture
def study(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Synthetic study", "protected")
    recording = store.create_recording(project["id"], "never-export-this-name.wav")
    segments = normalize_segments(
        [
            {
                "text": "😀 Jeg kan lide fleksibilitet, men systemet er langsomt. hej hej",
                "speaker": "A",
                "start": 0,
                "end": 4,
            },
            {"text": "Ingen timing. e\u0301 is exact.", "speaker": "B"},
            {"text": "The system is slow.", "speaker": "A", "start": 5, "end": 6},
        ]
    )
    # Keep explicit identity across the normalizer's chronological ordering of untimed gaps.
    for passage in segments:
        passage["id"] = (
            "0"
            if passage["text"].startswith("😀")
            else "1"
            if passage["text"].startswith("Ingen")
            else "2"
        )
    result = store.add_result(
        recording["id"],
        segments,
        {"A": "Private Name", "B": "Other Voice"},
        "Synthetic",
        {"kind": "import"},
    )
    recording = store.recording(recording["id"])
    frozen = store.freeze_result(recording["id"], result["id"], recording["revision"])
    book = store.save_codebook(project["id"], definition(), 0, reason="Private codebook reason")
    run = store.create_annotation_run(
        recording["id"],
        result["id"],
        frozen["source_version_id"],
        book["id"],
        request_id="manual-run",
    )
    return store, project, recording, result, book, run


def candidate(
    study,
    quote="kan lide fleksibilitet",
    *,
    passage_id="0",
    topic="flexibility",
    polarity="positive",
    attribution="self",
):
    passage = next(s for s in study[3]["segments"] if s["id"] == passage_id)
    start = passage["text"].index(quote)
    return {
        "topic_id": topic,
        "polarity": polarity,
        "attribution": attribution,
        "evidence": [
            {
                "passage_id": passage_id,
                "start_char": start,
                "end_char": start + len(quote),
                "exact_text": quote,
            }
        ],
    }


def bundle(study, payload=None):
    run = study[5]
    return {
        "format": annotations.IMPORT_FORMAT,
        **{
            key: run[key]
            for key in (
                "result_id",
                "source_version_id",
                "source_payload_sha256",
                "codebook_version_id",
                "codebook_payload_sha256",
            )
        },
        "passage_ids": run["passage_ids"],
        "suggestions": [{"id": "external-1", "annotation": payload or candidate(study)}],
        "producer": {"model": "UNVERIFIED-PRIVATE-PRODUCER", "revision": "unknown"},
    }


def test_schema6_backup_preserves_every_prior_row_and_history_and_migrates_once(study):
    store = study[0]
    with store.connection() as db:
        db.execute("DROP TABLE IF EXISTS result_hints")
        db.execute("DROP TABLE IF EXISTS native_batch_runs")
        db.execute("DROP TABLE IF EXISTS result_trash")
        for table in TABLES:
            db.execute(f"DROP TABLE {table}")
        remove_later_additions(db, 6)
        db.execute("PRAGMA user_version=6")
        old_tables = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        before = {
            table: [dict(row) for row in db.execute(f"SELECT * FROM {table}")]
            for table in old_tables
        }
    migrated = Store(store.root)
    with migrated.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        for table, rows in before.items():
            current = [dict(row) for row in db.execute(f"SELECT * FROM {table}")]
            assert without_added_languages(table, current) == rows
        assert all(
            db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0 for table in TABLES
        )
    backups = list((store.root / "backups").glob("schema-6-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        db.row_factory = sqlite3.Row
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
        for table, rows in before.items():
            assert [dict(row) for row in db.execute(f"SELECT * FROM {table}")] == rows
    Store(store.root)
    assert len(list((store.root / "backups").glob("schema-6-*.sqlite3"))) == 1


def test_schema7_migration_failure_rolls_back_all_created_tables(study):
    store = study[0]
    with store.connection() as db:
        db.execute("DROP TABLE IF EXISTS result_hints")
        db.execute("DROP TABLE IF EXISTS native_batch_runs")
        db.execute("DROP TABLE IF EXISTS result_trash")
        for table in TABLES:
            db.execute(f"DROP TABLE {table}")
        db.execute("PRAGMA user_version=6")
        db.execute("CREATE TABLE annotation_items (preexisting TEXT)")
    before = store.result_versions(study[2]["id"], study[3]["id"])
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        Store(store.root)
    with store.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
        assert {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE name IN "
                "('codebook_versions','annotation_runs','annotation_review_events')"
            )
        } == set()
    assert store.result_versions(study[2]["id"], study[3]["id"]) == before
    assert len(list((store.root / "backups").glob("schema-6-*.sqlite3"))) == 1


def test_codebook_approval_revision_retirement_hash_and_scope(study):
    store, project, recording, result, book, run = study
    before = store.recording(recording["id"])
    revised = definition()
    revised["topics"][0]["retired"] = True
    revised["topics"][1]["definition"] = "New explicit definition"
    next_book = store.save_codebook(project["id"], revised, 1)
    assert next_book["sequence"] == 2 and next_book["predecessor_id"] == book["id"]
    assert next_book["payload_sha256"] == annotations.canonical(next_book["payload"])[1]
    assert (
        store.codebook_version(project["id"], book["id"])["payload"]["topics"][0]["retired"]
        is False
    )
    assert store.annotation_run(recording["id"], run["id"])["codebook_stale"]
    removed = copy.deepcopy(revised)
    removed["topics"].pop(0)
    with pytest.raises(ValueError, match="retire"):
        store.save_codebook(project["id"], removed, 2)
    with pytest.raises(ValueError, match="another window"):
        store.save_codebook(project["id"], revised, 1)
    unapproved = store.save_codebook(project["id"], definition(approved=False), 2)
    with pytest.raises(ValueError, match="approve"):
        store.create_annotation_run(
            recording["id"],
            result["id"],
            run["source_version_id"],
            unapproved["id"],
            request_id="not-approved",
        )
    other = store.create_project("Other")
    with pytest.raises(KeyError):
        store.codebook_version(other["id"], book["id"])
    listed = store.codebook_versions(project["id"])
    assert all("payload" not in item and "reason" not in item for item in listed)
    assert store.recording(recording["id"]) == before


def test_manual_unicode_exact_offsets_missing_timing_multiple_topics_and_restart(study):
    store, _, recording, _, _, run = study
    before = store.recording(recording["id"])
    first = store.add_manual_annotation(
        recording["id"], run["id"], candidate(study), request_id="first"
    )
    assert first["revision"] == 1 and first["review_state"] == "accepted"
    assert first["suggestion"] is None and first["current"]["polarity"] == "positive"
    assert first["source_speakers"]["0"] == {"speaker_id": "A", "speaker_name": "Private Name"}
    negative = store.add_manual_annotation(
        recording["id"],
        run["id"],
        candidate(study, "systemet er langsomt", topic="speed", polarity="negative"),
        request_id="second",
    )
    untimed = store.add_manual_annotation(
        recording["id"],
        run["id"],
        candidate(study, "e\u0301", passage_id="1", polarity="neutral"),
        request_id="untimed",
    )
    assert untimed["current"]["evidence"][0]["exact_text"] == "e\u0301"
    listed = store.annotations(recording["id"], run["id"])
    assert [item["sequence"] for item in listed] == [3, 2, 1]
    assert all(
        not {"current", "suggestion", "payload", "reason", "evidence"} & item.keys()
        for item in listed
    )
    assert (
        store.annotations(recording["id"], run["id"], polarity="negative")[0]["id"]
        == negative["id"]
    )
    reopened = Store(store.root)
    assert reopened.annotation_item(recording["id"], run["id"], first["id"]) == first
    assert reopened.recording(recording["id"]) == before


@pytest.mark.parametrize(
    "damage",
    [
        "boolean",
        "utf16",
        "wrong_quote",
        "missing_offset",
        "empty",
        "bounds",
        "unknown_passage",
        "unknown_topic",
        "unknown_field",
        "derived_speaker",
        "uncertain",
        "normalization",
    ],
)
def test_malformed_evidence_never_commits(study, damage):
    store, _, recording, _, _, run = study
    payload = candidate(study)
    span = payload["evidence"][0]
    if damage == "boolean":
        span["start_char"] = True
    elif damage == "utf16":
        span["start_char"] += 1
        span["end_char"] += 1
    elif damage == "wrong_quote":
        span["exact_text"] = "invented quote"
    elif damage == "missing_offset":
        span.pop("start_char")
    elif damage == "empty":
        span["end_char"] = span["start_char"]
        span["exact_text"] = ""
    elif damage == "bounds":
        span["end_char"] = 100000
    elif damage == "unknown_passage":
        span["passage_id"] = "999"
    elif damage == "unknown_topic":
        payload["topic_id"] = "unsupported"
    elif damage == "unknown_field":
        payload["api_key"] = "not allowed"
    elif damage == "derived_speaker":
        span["source_speaker_id"] = "A"
    elif damage == "uncertain":
        payload["polarity"] = "uncertain"
    else:
        payload = candidate(study, "e\u0301", passage_id="1")
        payload["evidence"][0]["exact_text"] = "é"
    with pytest.raises(ValueError):
        store.add_manual_annotation(recording["id"], run["id"], payload, request_id="invalid")
    assert store.annotations(recording["id"], run["id"]) == []


def test_repeated_cross_passage_and_multispeaker_evidence_has_no_fuzzy_alignment(study):
    store, _, recording, _, _, run = study
    payload = candidate(study, "hej")
    first = payload["evidence"][0]
    second = {**first, "start_char": first["start_char"] + 4, "end_char": first["end_char"] + 4}
    payload["evidence"].append(second)
    payload["polarity"] = "uncertain"
    payload["uncertainty_reason"] = "Quotation alone does not establish evaluation."
    item = store.add_manual_annotation(recording["id"], run["id"], payload, request_id="repeat")
    assert len(item["current"]["evidence"]) == 2
    payload["evidence"].extend(candidate(study, "The system is slow.", passage_id="2")["evidence"])
    store.add_manual_annotation(recording["id"], run["id"], payload, request_id="same-voice")
    payload["evidence"].extend(candidate(study, "Ingen timing.", passage_id="1")["evidence"])
    with pytest.raises(ValueError, match="Multi-speaker"):
        store.add_manual_annotation(recording["id"], run["id"], payload, request_id="mixed-voices")
    payload["attribution"] = "unclear"
    item = store.add_manual_annotation(
        recording["id"], run["id"], payload, request_id="unclear-voices"
    )
    assert item["source_speakers"]["1"]["speaker_id"] == "B"


def test_import_review_accept_corrected_retry_staleness_export_and_isolation(study):
    store, project, recording, result, book, _ = study
    imported = store.import_annotation_run(recording["id"], bundle(study), request_id="import")
    item = store.annotations(recording["id"], imported["id"])[0]
    exact = store.annotation_item(recording["id"], imported["id"], item["id"])
    assert exact["revision"] == 0 and exact["current"] is None
    changed = candidate(study, polarity="negative")
    current = store.review_annotation(
        recording["id"],
        imported["id"],
        item["id"],
        0,
        "change",
        payload=changed,
        request_id="change",
        reason="PRIVATE-REVIEW-REASON",
    )
    current = store.review_annotation(
        recording["id"], imported["id"], item["id"], 1, "accept", request_id="accept"
    )
    assert current["current"]["polarity"] == "negative"
    assert current["suggestion"]["polarity"] == "positive"
    store.import_annotation_run(recording["id"], bundle(study), request_id="import")
    retry = store.review_annotation(
        recording["id"],
        imported["id"],
        item["id"],
        0,
        "change",
        payload=changed,
        request_id="change",
        reason="PRIVATE-REVIEW-REASON",
    )
    assert retry["revision"] == 2
    with pytest.raises(ValueError, match="different values"):
        store.review_annotation(
            recording["id"], imported["id"], item["id"], 0, "accept", request_id="change"
        )
    with pytest.raises(ValueError, match="another window"):
        store.review_annotation(
            recording["id"], imported["id"], item["id"], 0, "reject", request_id="stale"
        )
    before = store.recording(recording["id"])
    store.save_transcript(
        recording["id"], normalize_segments([{"text": "New source"}]), {}, before["revision"]
    )
    updated_book = definition()
    updated_book["topics"][0]["definition"] = "Changed meaning"
    store.save_codebook(project["id"], updated_book, book["sequence"])
    assert store.annotation_run(recording["id"], imported["id"])["source_stale"]
    assert store.annotation_run(recording["id"], imported["id"])["codebook_stale"]
    exported = annotations.export_bytes(
        store.export_annotation_run(recording["id"], imported["id"])
    )
    assert b'"negative"' in exported and b'"positive"' not in exported
    for excluded in (
        b"PRIVATE-REVIEW-REASON",
        b"UNVERIFIED-PRIVATE-PRODUCER",
        b"Private Name",
        b"never-export-this-name",
        b"original_text",
        b"original_words",
        b"New source",
    ):
        assert excluded not in exported
    assert b"unverified" in exported
    # Export does not include unreviewed proposal wording.
    pending = store.import_annotation_run(recording["id"], bundle(study), request_id="pending")
    assert b"kan lide" not in annotations.export_bytes(
        store.export_annotation_run(recording["id"], pending["id"])
    )
    assert store.result_version(recording["id"], result["id"], imported["source_version_id"])[
        "payload"
    ]["segments"][0]["text"].startswith("😀")


def test_scopes_and_frozen_hash_corruption_reject_writes(study):
    store, _, recording, result, book, run = study
    other_project = store.create_project("Other")
    other_recording = store.create_recording(other_project["id"], "other.wav")
    other_book = store.save_codebook(other_project["id"], definition(), 0)
    with pytest.raises(KeyError):
        store.create_annotation_run(
            recording["id"],
            result["id"],
            run["source_version_id"],
            other_book["id"],
            request_id="cross-book",
        )
    with pytest.raises(KeyError):
        store.annotation_run(other_recording["id"], run["id"])
    for key in ("source_payload_sha256", "codebook_payload_sha256"):
        bad = bundle(study)
        bad[key] = "0" * 64
        with pytest.raises(ValueError, match="hashes"):
            store.import_annotation_run(recording["id"], bad, request_id=key)
    store.execute(
        "UPDATE codebook_versions SET payload_sha256=? WHERE id=?", ("0" * 64, book["id"])
    )
    with pytest.raises(ValueError, match="integrity"):
        store.add_manual_annotation(
            recording["id"], run["id"], candidate(study), request_id="corrupt"
        )


def test_item_review_and_import_failures_are_atomic(study):
    store, _, recording, _, _, run = study
    store.execute(
        "CREATE TRIGGER reject_review BEFORE INSERT ON annotation_review_events "
        "BEGIN SELECT RAISE(FAIL,'injected'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        store.add_manual_annotation(
            recording["id"], run["id"], candidate(study), request_id="manual-failure"
        )
    assert store.annotations(recording["id"], run["id"]) == []
    store.execute("DROP TRIGGER reject_review")
    item = store.add_manual_annotation(
        recording["id"], run["id"], candidate(study), request_id="manual-success"
    )
    store.execute(
        "CREATE TRIGGER reject_review BEFORE INSERT ON annotation_review_events "
        "BEGIN SELECT RAISE(FAIL,'injected'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.review_annotation(
            recording["id"], run["id"], item["id"], 1, "reject", request_id="review-failure"
        )
    assert store.annotation_item(recording["id"], run["id"], item["id"])["revision"] == 1
    before_runs = store.annotation_runs(recording["id"])
    bad = bundle(study)
    bad["suggestions"].append({"id": "bad-later", "annotation": candidate(study)})
    bad["suggestions"][1]["annotation"]["evidence"][0]["exact_text"] = "invented"
    with pytest.raises(ValueError, match="Suggestion 2"):
        store.import_annotation_run(recording["id"], bad, request_id="failed-import")
    assert store.annotation_runs(recording["id"]) == before_runs


def test_historical_review_integrity_pagination_and_owner_deletion(study):
    store, project, recording, _, _, run = study
    item = store.add_manual_annotation(
        recording["id"], run["id"], candidate(study), request_id="item"
    )
    store.review_annotation(
        recording["id"], run["id"], item["id"], 1, "reject", request_id="reject"
    )
    events = store.annotation_review_events(recording["id"], run["id"], item["id"], limit=1)
    assert events[0]["revision"] == 2 and "payload" not in events[0] and "reason" not in events[0]
    older = store.annotation_review_events(
        recording["id"], run["id"], item["id"], before_revision=2
    )[0]
    event = store.annotation_review_event(recording["id"], run["id"], item["id"], older["id"])
    assert event["payload"]["polarity"] == "positive"
    store.execute(
        "UPDATE annotation_review_events SET payload_sha256=? WHERE id=?", ("0" * 64, older["id"])
    )
    with pytest.raises(ValueError, match="integrity"):
        store.annotation_review_event(recording["id"], run["id"], item["id"], older["id"])
    store.execute("DELETE FROM projects WHERE id=?", (project["id"],))
    assert all(store.all(f"SELECT * FROM {table}") == [] for table in TABLES)


def test_import_unknown_fields_and_producer_identity_are_not_executed(study):
    store, _, recording, _, _, _ = study
    for key in ("api_key", "endpoint", "runtime_python", "chain_of_thought"):
        bad = bundle(study)
        bad["producer"][key] = "do-not-execute-or-echo"
        with pytest.raises(ValueError):
            store.import_annotation_run(recording["id"], bad, request_id=key)
    assert all(run["kind"] == "manual" for run in store.annotation_runs(recording["id"]))


def test_uncertain_evaluation_explanation_is_exported_but_private_review_rationale_is_not(study):
    store, _, recording, _, _, run = study
    payload = candidate(study, polarity="uncertain")
    payload["uncertainty_reason"] = "The evaluation could be ironic; more context is required."
    store.add_manual_annotation(
        recording["id"],
        run["id"],
        payload,
        request_id="uncertain",
        reason="PRIVATE REVIEW RATIONALE MUST STAY LOCAL",
    )
    exported = store.export_annotation_run(recording["id"], run["id"])
    encoded = annotations.export_bytes(exported)
    assert b"The evaluation could be ironic; more context is required." in encoded
    assert b"PRIVATE REVIEW RATIONALE" not in encoded
    assert "review_reasons" in exported["excludes"]
    assert "reasons" not in exported["excludes"]
