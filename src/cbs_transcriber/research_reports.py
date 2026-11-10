"""Metadata-first research reports from one SQLite snapshot; no inference or file access."""

import hashlib
import io
import json
import math
import re
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from zipfile import ZIP_DEFLATED, ZipFile

from .exports import export_bytes
from .history import (
    EDIT_ACTIONS,
    MAX_OBSERVATION_BYTES,
    automatic_language_metadata,
    conflict_review_metadata,
    conversion_metadata,
    decode_payload,
    encode_payload,
    merge_metadata,
    text_hash,
    validate_import_artifact_evidence,
    validate_version_observation,
    wording_adoption_metadata,
)
from .transcripts import _passage_index, edit_segment, word_mapping_current

MAX_SELECTIONS = 100
MAX_VERSIONS = 1000
MAX_PAYLOAD_BYTES = 32 * 1024 * 1024
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_CONTEXT_BYTES = 32 * 1024 * 1024
FORMAT = "cbs-research-report-v1"
# v2: unnamed speakers render as Speaker_NN display names (V88) instead of raw labels.
RENDERER = "cbs-readable-text-v2"
# One shared vocabulary: every manual correction recorded in history counts as an edit.
ACTIONS = EDIT_ACTIONS | {
    "result_trashed",
    "result_restored",
    "imported_result",
    "provider_result",
    "enhancement_result",
    "legacy_observed",
    "manual_save",
    "save_no_change",
}


def _coverage(payload):
    segments = payload["segments"]
    total, timed = 0, 0
    for segment in segments:
        text = segment["text"]
        tokens = [token for token in re.finditer(r"\S+", text) if re.search(r"\w", token[0])]
        total += len(tokens)
        if not word_mapping_current(segment):
            continue
        covered = bytearray(len(text))
        previous_start, previous_end = -1, -1
        for word in segment.get("words", []):
            start, end = word.get("start"), word.get("end")
            left, right = word.get("char_start"), word.get("char_end")
            if (
                not all(
                    isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                    for v in (start, end)
                )
                or not 0 <= start < end
                or start < previous_start
                or end < previous_end
                or (segment.get("start") is not None and start < segment["start"])
                or (segment.get("end") is not None and end > segment["end"])
                or type(left) is not int
                or type(right) is not int
                or not 0 <= left < right <= len(text)
                or text[left:right] != word["word"]
            ):
                continue
            previous_start, previous_end = start, end
            covered[left:right] = b"\x01" * (right - left)
        # A provider may split one text token into several word units (can + 't).
        # Count current lexical tokens only when all their lexical characters are located.
        timed += sum(
            all(covered[token.start() + char.start()] for char in re.finditer(r"\w", token[0]))
            for token in tokens
        )
    return {
        "passages": len(segments),
        "complete_word_timing_passages": sum(bool(s.get("word_timing")) for s in segments),
        "lexical_tokens": total,
        "timed_lexical_tokens": timed,
        "speaker_labels": len({s["speaker"] for s in segments if s["speaker"] != "UNKNOWN"}),
        "speaker_identity_verified": False,
    }


def _processing(value):
    """Defence in depth: never serialize arbitrary saved context/settings into a report."""
    if not isinstance(value, dict):
        return {}
    output = {}
    enums = {
        "provider_type": {"openai", "custom", "ucloud-vllm", "ucloud", "whisperx"},
        "execution": {"local", "ucloud"},
        "engine": {"whisperx"},
        "capability": {"json", "segments", "words", "speakers", "words_speakers"},
        "scope": {
            "whole_result",
            "passage_repair",
            "passage_repairs",
            "passage_retranscription",
        },
    }
    for key, allowed in enums.items():
        if value.get(key) in allowed:
            output[key] = value[key]
    for key, pattern in {
        "model": r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}",
        "language": r"(?:auto|[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?)",
        "version": r"\d+\.\d+(?:\.\d+)?(?:[a-z0-9.+-]{0,30})?",
    }.items():
        item = value.get(key)
        if isinstance(item, str) and re.fullmatch(pattern, item) and ".." not in item:
            output[key] = item
    count = value.get("passage_count")
    if isinstance(count, int) and not isinstance(count, bool) and 0 < count <= 1000:
        output["passage_count"] = count
    options = value.get("options")
    if isinstance(options, dict):
        safe = {}
        for key in ("align", "diarize"):
            if type(options.get(key)) is bool:
                safe[key] = options[key]
        if options.get("device") in {"cpu", "cuda"}:
            safe["device"] = options["device"]
        language = options.get("language")
        if isinstance(language, str) and re.fullmatch(
            r"(?:auto|[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?)", language
        ):
            safe["language"] = language
        if options.get("execution") in {"local", "ucloud"}:
            safe["execution"] = options["execution"]
        score = options.get("min_score")
        if type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1:
            safe["min_score"] = score
        for key in ("min_speakers", "max_speakers"):
            item = options.get(key)
            if type(item) is int and 1 <= item <= 100:
                safe[key] = item
        if safe:
            output["options"] = safe
        if options.get("timing_policy") in ("legacy-score-filter", "retain-estimates"):
            output.setdefault("options", {})["timing_policy"] = options["timing_policy"]
    revisions = value.get("model_revisions")
    if isinstance(revisions, dict):
        safe = {}
        for key in (
            "da",
            "en",
            "coarse",
            "speakers",
            "whisper-medium",
            "whisper-large-v3-turbo",
            "whisper-large-v3",
            "edda-turbo",
            "edda-large",
            "roest-v3",
        ):
            item = revisions.get(key)
            if not isinstance(item, dict):
                continue
            repo, revision = item.get("repo"), item.get("revision")
            if (
                isinstance(repo, str)
                and re.fullmatch(r"[\w.-]+/[\w.-]+", repo)
                and ".." not in repo
                and isinstance(revision, str)
                and re.fullmatch(r"[a-fA-F0-9]{40}", revision)
            ):
                safe[key] = {"repo": repo, "revision": revision}
        if safe:
            output["model_revisions"] = safe
    evidence = value.get("language_evidence")
    if evidence is not None and "language_decision" not in value:
        from .enhancement_worker import validate_language_evidence

        try:
            output["language_evidence"] = [
                {key: field for key, field in receipt.items() if key != "passage_id"}
                for receipt in validate_language_evidence(evidence)
            ]
        except (ValueError, TypeError, KeyError, RecursionError):
            pass
    output.update(automatic_language_metadata(value))
    conversion = conversion_metadata(value).get("asr_conversion")
    if conversion is not None:
        # The validator accepts a closed bounded schema; publish only that explicit schema.
        output["asr_conversion"] = {
            key: conversion[key]
            for key in (
                "schema",
                "choice",
                "source",
                "recipe",
                "files",
                "artifact_sha256",
                "request_sha256",
                "input_sha256",
            )
        }
    return output


def _artifact_claim(value):
    if value == "not_captured":
        return None
    return validate_import_artifact_evidence(value)


def _frozen_source(db, row, remaining):
    """Observe retained producer proof once; absent legacy jobs confer no authority."""
    job_id = row["producing_job_id"]
    if job_id is None:
        return None, 0
    parent = next(
        (
            row[key]
            for key in (
                "producer_artifact_parent",
                "provider_artifact_parent",
                "native_artifact_parent",
                "runtime_artifact_parent",
            )
            if key in row
        ),
        None,
    )
    cost = 0
    if parent is None:
        size = db.execute(
            "SELECT recording_id,length(CAST(params AS BLOB)) FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        if size is None:
            return None, 0
        if size[0] != row["recording_id"]:
            raise ValueError("The preserved source producer ownership is invalid.")
        cost = size[1]
        if type(cost) is not int or not 0 <= cost <= remaining:
            raise ValueError("Selected source job evidence exceeds the report's metadata limit.")
        raw = db.execute("SELECT params FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
    else:
        raw = parent["params"]  # Already bounded and counted by its artifact namespace.
    try:
        params = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        return None, cost  # An unreadable legacy job cannot prove an exact source.
    if not isinstance(params, dict) or params.get("source_snapshot") is None:
        return None, cost
    frozen = params["source_snapshot"]
    try:
        if not isinstance(frozen, dict):
            raise ValueError()
        digest = frozen.get("source_payload_sha256")
        if "segments" in frozen and "speakers" in frozen:
            _, observed = encode_payload(frozen["segments"], frozen["speakers"])
            if digest not in (None, observed):
                raise ValueError()
            digest = observed
        return {
            "result_id": frozen.get("id"),
            "version_id": frozen.get("source_version_id"),
            "payload_sha256": digest,
        }, cost
    except (KeyError, TypeError, ValueError, RecursionError):
        raise ValueError("The preserved frozen source evidence is invalid.") from None


def _verify_source(row, captured, frozen):
    context = row["context"]
    source_id = row["source_version_id"]
    parent = captured.get(source_id)
    if source_id is not None and (
        parent is None
        or context.get("source_version_unknown") is True
        or context.get("source_result_id") not in (None, parent["result_id"])
        or context.get("source_payload_sha256") not in (None, parent["payload_sha256"])
    ):
        raise ValueError("The preserved source version contradicts its captured identity.")
    if frozen is None:
        return
    if source_id != frozen["version_id"] or context.get("producer_job_id") not in (
        None,
        row["producing_job_id"],
    ):
        raise ValueError("The preserved source version differs from its frozen producer input.")
    for key, context_key in (
        ("result_id", "source_result_id"),
        ("payload_sha256", "source_payload_sha256"),
    ):
        observed = frozen[key]
        if observed is not None and (
            context.get(context_key) not in (None, observed)
            or (parent is not None and parent[key] != observed)
        ):
            raise ValueError(
                "The preserved source identity differs from its frozen producer input."
            )


def _verify_source_graph(captured):
    """Both recorded parent edges must form a DAG, without recursive depth limits."""
    parents, children = {}, {key: [] for key in captured}
    for key, row in captured.items():
        parents[key] = {row[name] for name in ("predecessor_id", "source_version_id")} - {None}
        for parent in parents[key]:
            if parent not in captured:
                raise ValueError("The preserved source ancestry is invalid. Preserve the library.")
            children[parent].append(key)
    ready = [key for key in captured if not parents[key]]
    visited = 0
    while ready:
        parent = ready.pop()
        visited += 1
        for child in children[parent]:
            parents[child].remove(parent)
            if not parents[child]:
                ready.append(child)
    if visited != len(captured):
        raise ValueError("The preserved source ancestry contains a cycle. Preserve the library.")


def _capture(store, project_id, selections, collection=None):
    """Copy bounded exact versions/lineage coherently, then release the read transaction.

    `collection` limits the default selection to one collection, or "unsorted" (ADR 0060)."""
    with store.connection() as db:
        db.execute("BEGIN")
        if not db.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone():
            raise KeyError(project_id)
        total_recordings = db.execute(
            "SELECT COUNT(*) FROM recordings WHERE project_id=?", (project_id,)
        ).fetchone()[0]
        skipped = []
        default_selection = selections is None
        if default_selection:
            scope, params = "", [project_id]
            if collection == "unsorted":
                scope = " AND r.collection_id IS NULL"
            elif collection is not None:
                if not db.execute(
                    "SELECT id FROM collections WHERE id=? AND project_id=?",
                    (collection, project_id),
                ).fetchone():
                    raise KeyError(collection)
                scope, params = " AND r.collection_id=?", [project_id, collection]
            scoped = [
                dict(row)
                for row in db.execute(
                    "SELECT r.id recording_id,t.id result_id,v.id version_id "
                    "FROM recordings r LEFT JOIN recording_result_roles roles "
                    "ON roles.recording_id=r.id "
                    "LEFT JOIN result_versions v ON v.id=roles.working_version_id "
                    "LEFT JOIN transcript_results t ON t.id=v.result_id AND t.recording_id=r.id "
                    "WHERE r.project_id=?" + scope + " ORDER BY r.created,r.id LIMIT ?",
                    (*params, MAX_SELECTIONS + 1),
                )
            ]
            if len(scoped) > MAX_SELECTIONS:
                raise ValueError(
                    "Choose a collection of at most 100 recordings for the research report."
                )
            selections = []
            for item in scoped:
                from . import result_roles

                result_roles.read(db, store, item["recording_id"])
                if item["version_id"] is None:
                    skipped.append(item["recording_id"])
                elif item["result_id"] is None:
                    raise ValueError(
                        "The Working version ownership is invalid. Preserve the library."
                    )
                else:
                    selections.append(item)
        if (
            not isinstance(selections, list)
            or not (0 if default_selection else 1) <= len(selections) <= MAX_SELECTIONS
        ):
            raise ValueError("Choose 1–100 saved transcript versions for the research report.")
        chosen, pending, seen = [], [], set()
        for item in selections:
            if (
                not isinstance(item, dict)
                or set(item) != {"recording_id", "result_id", "version_id"}
                or any(not isinstance(v, str) or not 0 < len(v) <= 100 for v in item.values())
            ):
                raise ValueError("Choose a recording, saved result and exact version.")
            key = (item["recording_id"], item["result_id"], item["version_id"])
            if key in seen:
                raise ValueError("Each selected version may appear only once in a report.")
            if db.execute(
                "SELECT 1 FROM result_trash x JOIN transcript_results t ON t.id=x.result_id "
                "WHERE t.id=? AND t.recording_id=?",
                (item["result_id"], item["recording_id"]),
            ).fetchone():
                raise ValueError("Restore this result from Trash before selecting it for a report.")
            seen.add(key)
            chosen.append(key)
            pending.append(key)
        captured, payload_bytes, context_bytes = {}, 0, 0
        sources, source_bytes = {}, 0
        frozen_sources = {}
        while pending:
            recording_id, result_id, version_id = pending.pop(0)
            if version_id in captured:
                existing = captured[version_id]
                if existing["recording_id"] != recording_id or (
                    result_id is not None and existing["result_id"] != result_id
                ):
                    raise KeyError(version_id)
                continue
            if len(captured) >= MAX_VERSIONS:
                raise ValueError("This lineage exceeds the report's 1,000-version limit.")
            scope = (
                "FROM result_versions v "
                "JOIN transcript_results t ON t.id=v.result_id "
                "JOIN recordings r ON r.id=t.recording_id "
                "WHERE v.id=? AND r.project_id=? AND r.id=? "
                "AND (? IS NULL OR t.id=?)"
            )
            params = (version_id, project_id, recording_id, result_id, result_id)
            size = db.execute(
                "SELECT length(CAST(v.payload AS BLOB)),length(CAST(v.context AS BLOB)),"
                "length(CAST(v.observed_at AS BLOB)),typeof(v.sequence) " + scope,
                params,
            ).fetchone()
            if size is None:
                raise KeyError(version_id)
            payload_bytes += size[0]
            if payload_bytes > MAX_PAYLOAD_BYTES:
                raise ValueError("Selected history exceeds the report's 32 MiB content limit.")
            if type(size[1]) is not int or size[1] < 0:
                raise ValueError("The preserved version context is invalid.")
            context_bytes += size[1]
            if context_bytes > MAX_CONTEXT_BYTES:
                raise ValueError("Selected history metadata exceeds the report's 32 MiB limit.")
            if (
                type(size[2]) is not int
                or not 1 <= size[2] <= MAX_OBSERVATION_BYTES
                or size[3] != "integer"
            ):
                raise ValueError("The saved research observation metadata is invalid.")
            row = db.execute(
                "SELECT v.*,r.id recording_id,t.job_id producing_job_id,"
                "EXISTS(SELECT 1 FROM result_trash x WHERE x.result_id=t.id) AS result_in_trash "
                + scope,
                params,
            ).fetchone()
            row = dict(row)
            validate_version_observation(row["sequence"], row["observed_at"])
            try:
                row["payload"] = decode_payload(row["payload"], row["payload_sha256"])
            except (ValueError, TypeError, RecursionError):
                raise ValueError(
                    "A preserved version failed its content integrity check."
                ) from None
            try:
                row["context"] = json.loads(row["context"])
            except (ValueError, TypeError, RecursionError):
                raise ValueError("The preserved version context is invalid.") from None
            if not isinstance(row["context"], dict):
                raise ValueError("The preserved version context is invalid.")
            if row["result_id"] not in sources:
                source_params = (row["result_id"], recording_id)
                source_size = db.execute(
                    "SELECT length(CAST(source AS BLOB)) FROM transcript_results "
                    "WHERE id=? AND recording_id=?",
                    source_params,
                ).fetchone()
                if source_size is None or type(source_size[0]) is not int or source_size[0] < 0:
                    raise ValueError("The saved result source is invalid.")
                source_bytes += source_size[0]
                if source_bytes > MAX_SOURCE_BYTES:
                    raise ValueError("Selected result sources exceed the report's 32 MiB limit.")
                encoded_source = db.execute(
                    "SELECT source FROM transcript_results WHERE id=? AND recording_id=?",
                    source_params,
                ).fetchone()[0]
                try:
                    source = json.loads(encoded_source)
                    if not isinstance(source, dict):
                        raise ValueError
                except (TypeError, ValueError, RecursionError) as exc:
                    raise ValueError("The saved result source is invalid.") from exc
                sources[row["result_id"]] = (
                    source.get("kind") == "import",
                    _artifact_claim(source.get("artifact_evidence", "not_captured")),
                )
                del source, encoded_source
            is_import, source_receipt = sources[row["result_id"]]
            receipt = _artifact_claim(row["context"].get("artifact_evidence", "not_captured"))
            if receipt != source_receipt or (receipt is not None and not is_import):
                raise ValueError(
                    "The imported transcript artifact evidence contradicts its source."
                )
            row["import_artifact_evidence"] = receipt
            row["audio_input_evidence"], row["audio_preparation_evidence"] = _audio_evidence(row)
            row["runtime_artifact_evidence"] = None
            runtime_proof = row["context"].get("runtime_artifacts")
            if runtime_proof is not None:
                from . import runtime_capture

                job_id = row["context"].get("producer_job_id")
                if not isinstance(job_id, str) or row["producing_job_id"] != job_id:
                    raise ValueError("The Runtime artifact producer ownership is invalid.")
                sizes = db.execute(
                    "SELECT length(CAST(j.params AS BLOB)),length(CAST(a.input_receipt AS BLOB)),"
                    "length(CAST(a.remote_receipt AS BLOB)),"
                    "length(CAST(b.identity AS BLOB)),m.result_id "
                    "FROM jobs j JOIN runtime_batch_members m ON m.job_id=j.id "
                    "JOIN runtime_batch_attempts a ON a.job_id=m.job_id "
                    "AND a.attempt=m.current_attempt JOIN runtime_batch_runs b ON b.id=m.batch_id "
                    "WHERE j.id=? AND j.recording_id=?",
                    (job_id, recording_id),
                ).fetchone()
                if (
                    sizes is None
                    or sizes[4] != row["result_id"]
                    or any(
                        type(size) is not int or not 0 <= size <= MAX_CONTEXT_BYTES
                        for size in sizes[:4]
                    )
                ):
                    raise ValueError(
                        "The Runtime artifact receipt ownership or bounded scope is invalid."
                    )
                context_bytes += sum(sizes[:4])
                if context_bytes > MAX_CONTEXT_BYTES:
                    raise ValueError("Selected Runtime evidence exceeds the report's 32 MiB limit.")
                parent = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                row["runtime_artifact_parent"] = dict(parent)
                creation = db.execute(
                    "SELECT id,payload_sha256 FROM result_versions "
                    "WHERE result_id=? AND sequence=1",
                    (row["result_id"],),
                ).fetchone()
                if creation is None:
                    raise ValueError("The exact Runtime creation version is missing.")
                reference = {
                    "result_id": row["result_id"],
                    "version_id": creation["id"],
                    "payload_sha256": creation["payload_sha256"],
                }
                unbound = runtime_capture.unbound(
                    runtime_proof, recording_id=recording_id, job_id=job_id, creation=reference
                )
                if (
                    row["remote_audio_preparation_evidence"] is not None
                    and row["remote_audio_preparation_evidence"]["request_sha256"]
                    != unbound["origin"]["request_checkpoint"]["producer"]["request_sha256"]
                ):
                    raise ValueError(
                        "The remote preparation and Runtime request evidence disagree."
                    )
                runtime_capture.for_creation(db, parent, recording_id, unbound)
                row["runtime_artifact_evidence"] = runtime_capture.public_projection(
                    runtime_proof, recording_id=recording_id, job_id=job_id, creation=reference
                )
                pending.append((recording_id, row["result_id"], creation["id"]))
            row["producer_artifact_evidence"] = None
            producer = row["context"].get("producer_artifacts")
            if producer is not None:
                job_id = row["context"].get("producer_job_id")
                if not isinstance(job_id, str) or row["producing_job_id"] != job_id:
                    raise ValueError("The producer artifact job ownership is invalid.")
                size = db.execute(
                    "SELECT length(CAST(params AS BLOB)) FROM jobs WHERE id=? AND recording_id=?",
                    (job_id, recording_id),
                ).fetchone()
                if size is None or type(size[0]) is not int or size[0] > MAX_CONTEXT_BYTES:
                    raise ValueError(
                        "The producer artifact job evidence exceeds its bounded scope."
                    )
                context_bytes += size[0]
                if context_bytes > MAX_CONTEXT_BYTES:
                    raise ValueError(
                        "Selected producer evidence exceeds the report's 32 MiB limit."
                    )
                parent = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                row["producer_artifact_parent"] = dict(parent)
                creation_id = db.execute(
                    "SELECT id FROM result_versions WHERE result_id=? AND sequence=1",
                    (row["result_id"],),
                ).fetchone()
                if creation_id is None:
                    raise ValueError("The producer artifact creation version is missing.")
                pending.append((recording_id, row["result_id"], creation_id[0]))
            row["provider_artifact_evidence"] = None
            if row["context"].get("provider_artifacts") is not None:
                job_id = row["context"].get("producer_job_id")
                if not isinstance(job_id, str) or row["producing_job_id"] != job_id:
                    raise ValueError("The provider artifact job ownership is invalid.")
                sizes = db.execute(
                    "SELECT length(CAST(params AS BLOB)),length(CAST(chunks AS BLOB)) "
                    "FROM jobs WHERE id=? AND recording_id=?",
                    (job_id, recording_id),
                ).fetchone()
                if sizes is None or any(
                    type(size) is not int or not 0 <= size <= MAX_CONTEXT_BYTES for size in sizes
                ):
                    raise ValueError(
                        "The provider artifact job evidence exceeds its bounded scope."
                    )
                context_bytes += sizes[0] + sizes[1]
                if context_bytes > MAX_CONTEXT_BYTES:
                    raise ValueError(
                        "Selected provider evidence exceeds the report's 32 MiB limit."
                    )
                parent = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                row["provider_artifact_parent"] = dict(parent)
                creation_id = db.execute(
                    "SELECT id FROM result_versions WHERE result_id=? AND sequence=1",
                    (row["result_id"],),
                ).fetchone()
                if creation_id is None:
                    raise ValueError("The provider artifact creation version is missing.")
                pending.append((recording_id, row["result_id"], creation_id[0]))
            row["native_artifact_evidence"] = None
            if row["context"].get("native_artifacts") is not None:
                job_id = row["context"].get("producer_job_id")
                if not isinstance(job_id, str) or row["producing_job_id"] != job_id:
                    raise ValueError("The native artifact job ownership is invalid.")
                sizes = db.execute(
                    "SELECT length(CAST(j.params AS BLOB)),"
                    "COALESCE((SELECT length(CAST(b.params AS BLOB))"
                    "+length(CAST(b.members AS BLOB)) FROM native_batch_runs b "
                    "WHERE b.id=json_extract(j.params,'$.native_batch_id')),0) "
                    "FROM jobs j WHERE j.id=? AND j.recording_id=?",
                    (job_id, recording_id),
                ).fetchone()
                if sizes is None or any(
                    type(size) is not int or not 0 <= size <= MAX_CONTEXT_BYTES for size in sizes
                ):
                    raise ValueError("The native artifact job evidence exceeds its bounded scope.")
                context_bytes += sizes[0] + sizes[1]
                if context_bytes > MAX_CONTEXT_BYTES:
                    raise ValueError("Selected native evidence exceeds the report's 32 MiB limit.")
                parent = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                row["native_artifact_parent"] = dict(parent)
                creation_id = db.execute(
                    "SELECT id FROM result_versions WHERE result_id=? AND sequence=1",
                    (row["result_id"],),
                ).fetchone()
                if creation_id is None:
                    raise ValueError("The native artifact creation version is missing.")
                pending.append((recording_id, row["result_id"], creation_id[0]))
            from . import result_roles

            role = result_roles.read(db, store, recording_id)
            row["working_designation"] = {
                "selected_version_is_working": bool(
                    role["working"] and role["working"]["version_id"] == version_id
                ),
                "revision": role["revision"],
            }
            timing = row["context"].get("timing_artifacts")
            if timing is not None:
                try:
                    job_id = timing["application_job_id"]
                    if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
                        raise ValueError()
                    size = db.execute(
                        "SELECT length(CAST(params AS BLOB)) FROM jobs "
                        "WHERE id=? AND recording_id=?",
                        (job_id, recording_id),
                    ).fetchone()
                    if size is None or type(size[0]) is not int or size[0] > MAX_CONTEXT_BYTES:
                        raise ValueError()
                    context_bytes += size[0]
                    if context_bytes > MAX_CONTEXT_BYTES:
                        raise ValueError()
                    parent = dict(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
                    row["timing_artifact_parent"] = parent
                    from .timing_capture import historical_references

                    for reference in historical_references(timing, parent):
                        pending.append((recording_id, row["result_id"], reference["version_id"]))
                except (KeyError, TypeError, ValueError, RecursionError):
                    raise ValueError(
                        "The saved timing artifact ownership or bounded evidence is invalid."
                    ) from None
            if row["result_id"] not in frozen_sources:
                frozen, cost = _frozen_source(db, row, MAX_CONTEXT_BYTES - context_bytes)
                frozen_sources[row["result_id"]] = frozen
                context_bytes += cost
            captured[version_id] = row
            if row["action"] == "result_merge":
                row["merge_evidence"] = _merge_evidence(db, row)
                for name in ("base", "incoming", "creation"):
                    reference = row["merge_evidence"][name]
                    pending.append((recording_id, reference["result_id"], reference["version_id"]))
            if row["action"] == "wording_adopt":
                evidence = wording_adoption_metadata(row["context"].get("wording_adoption"))
                if evidence["source"]["result_id"] == row["result_id"]:
                    raise ValueError("The saved wording adoption source ownership is invalid.")
                row["wording_adoption_evidence"] = evidence
                reference = evidence["source"]
                pending.append((recording_id, reference["result_id"], reference["version_id"]))
            if row["action"] == "result_conflict_review":
                evidence = conflict_review_metadata(row["context"].get("conflict_review"))
                row["conflict_review_evidence"] = evidence
                for name in ("base", "incoming", "creation"):
                    reference = evidence[name]
                    pending.append((recording_id, reference["result_id"], reference["version_id"]))
            for link, expected_result in (
                ("predecessor_id", row["result_id"]),
                ("source_version_id", None),
            ):
                if row[link]:
                    pending.append((recording_id, expected_result, row[link]))
        _verify_source_graph(captured)
        for row in captured.values():
            _verify_source(row, captured, frozen_sources[row["result_id"]])
            previous = captured.get(row["predecessor_id"])
            if (row["sequence"] == 1 and row["predecessor_id"] is not None) or (
                row["sequence"] > 1
                and (
                    previous is None
                    or previous["result_id"] != row["result_id"]
                    or previous["sequence"] + 1 != row["sequence"]
                    or previous["source_version_id"] != row["source_version_id"]
                )
            ):
                raise ValueError("The preserved version ancestry is invalid. Preserve the library.")
            if row["runtime_artifact_evidence"] is not None:
                creation = row["context"]["runtime_artifacts"]["creation"]
                original = captured[creation["version_id"]]
                if (
                    original["sequence"] != 1
                    or original["context"].get("runtime_artifacts")
                    != row["context"]["runtime_artifacts"]
                ):
                    raise ValueError(
                        "The inherited Runtime evidence differs from its exact creation."
                    )
            if row["action"] == "wording_adopt":
                _verify_wording_adoption(row, captured)
            if row["action"] == "result_conflict_review":
                from .result_conflicts import verify_historical

                verify_historical(row, captured)
            if row["context"].get("timing_artifacts") is not None:
                from .timing_capture import verify_historical as verify_timing

                verify_timing(row, captured)
            if row["context"].get("producer_artifacts") is not None:
                from . import local_asr_capture

                creation = next(
                    (
                        item
                        for item in captured.values()
                        if item["result_id"] == row["result_id"] and item["sequence"] == 1
                    ),
                    None,
                )
                if (
                    creation is None
                    or creation["context"].get("producer_artifacts")
                    != row["context"]["producer_artifacts"]
                ):
                    raise ValueError(
                        "The inherited producer evidence differs from its exact creation."
                    )
                reference = {
                    "result_id": creation["result_id"],
                    "version_id": creation["id"],
                    "payload_sha256": creation["payload_sha256"],
                }
                parent = row["producer_artifact_parent"]
                transport = local_asr_capture.unbound(
                    row["context"]["producer_artifacts"],
                    recording_id=row["recording_id"],
                    job_id=parent["id"],
                    creation=reference,
                )
                local_asr_capture.for_creation(parent, row["recording_id"], transport)
                row["producer_artifact_evidence"] = local_asr_capture.public_projection(
                    row["context"]["producer_artifacts"],
                    recording_id=row["recording_id"],
                    job_id=parent["id"],
                    creation=reference,
                )
            if row["context"].get("provider_artifacts") is not None:
                from . import provider_capture

                creation = next(
                    (
                        item
                        for item in captured.values()
                        if item["result_id"] == row["result_id"] and item["sequence"] == 1
                    ),
                    None,
                )
                if (
                    creation is None
                    or creation["context"].get("provider_artifacts")
                    != row["context"]["provider_artifacts"]
                ):
                    raise ValueError(
                        "The inherited provider evidence differs from its exact creation."
                    )
                # Stored job observations and normalized parts only; no current files,
                # provider, credential or network access.
                row["provider_artifact_evidence"] = provider_capture.public_projection(
                    row["context"]["provider_artifacts"],
                    parent=row["provider_artifact_parent"],
                    recording_id=row["recording_id"],
                    creation={
                        "result_id": creation["result_id"],
                        "version_id": creation["id"],
                        "payload_sha256": creation["payload_sha256"],
                    },
                )
            if row["context"].get("native_artifacts") is not None:
                from . import native_capture

                creation = next(
                    (
                        item
                        for item in captured.values()
                        if item["result_id"] == row["result_id"] and item["sequence"] == 1
                    ),
                    None,
                )
                if (
                    creation is None
                    or creation["context"].get("native_artifacts")
                    != row["context"]["native_artifacts"]
                ):
                    raise ValueError(
                        "The inherited native evidence differs from its exact creation."
                    )
                # Stored job/batch rows in this snapshot only; no files, UCloud or network.
                row["native_artifact_evidence"] = native_capture.public_projection(
                    db,
                    row["context"]["native_artifacts"],
                    parent=row["native_artifact_parent"],
                    recording_id=row["recording_id"],
                    creation={
                        "result_id": creation["result_id"],
                        "version_id": creation["id"],
                        "payload_sha256": creation["payload_sha256"],
                    },
                )
        from . import result_roles

        role_histories = {}
        role_budget = {"remaining": MAX_PAYLOAD_BYTES}
        for recording_id in dict.fromkeys([key[0] for key in chosen] + skipped):
            role_histories[recording_id] = result_roles.public_history(
                db,
                store,
                recording_id,
                f"recording-{len(role_histories) + 1:03}",
                payload_budget=role_budget,
                verified_versions=captured,
            )
        if len(json.dumps(role_histories, ensure_ascii=False).encode("utf-8")) > MAX_CONTEXT_BYTES:
            raise ValueError("The Working role observations exceed the report's 32 MiB limit.")
        return chosen, captured, total_recordings, skipped, role_histories


def _verify_wording_adoption(row, captured):
    """Reconstruct the complete literal free edit from budgeted, verified history."""
    evidence = row["wording_adoption_evidence"]
    reference = evidence["source"]
    try:
        source = captured[reference["version_id"]]
        previous = captured[row["predecessor_id"]]
        if (
            source["result_id"] != reference["result_id"]
            or source["payload_sha256"] != reference["payload_sha256"]
            or source["recording_id"] != row["recording_id"]
            or previous["recording_id"] != row["recording_id"]
            or previous["result_id"] != row["result_id"]
            or previous["sequence"] + 1 != row["sequence"]
        ):
            raise ValueError()
        segments = previous["payload"]["segments"]
        source_segments = source["payload"]["segments"]
        target_index = _passage_index(segments, evidence["target_passage_id"])
        source_index = _passage_index(source_segments, evidence["source_passage_id"])
        before, incoming = segments[target_index], source_segments[source_index]
        if (
            not incoming["text"].strip()
            or incoming["text"] == before["text"]
            or text_hash(before["text"]) != evidence["target_text_sha256"]
            or text_hash(incoming["text"]) != evidence["source_text_sha256"]
        ):
            raise ValueError()
        proposed = list(segments)
        proposed[target_index] = edit_segment(before, "free", incoming["text"])
        encoded, digest = encode_payload(proposed, previous["payload"]["speakers"])
        if digest != row["payload_sha256"] or json.loads(encoded) != row["payload"]:
            raise ValueError()
    except (KeyError, TypeError, AttributeError, ValueError, RecursionError):
        raise ValueError("The saved wording adoption failed its ancestry check.") from None
    row["wording_adoption_passages"] = (target_index + 1, source_index + 1)


def _merge_evidence(db, row):
    """Follow only verified, same-recording historical merge references."""
    evidence = merge_metadata(row["context"].get("merge"))
    if (
        evidence["base"]["result_id"] != row["result_id"]
        or evidence["incoming"]["result_id"] == row["result_id"]
        or evidence["creation"]["result_id"] != evidence["incoming"]["result_id"]
    ):
        raise ValueError("The saved merge parent ownership is invalid.")
    found = {}
    for name in ("base", "incoming", "creation"):
        ref = evidence[name]
        parent = db.execute(
            "SELECT v.id,v.sequence,v.source_version_id,v.payload_sha256 "
            "FROM result_versions v JOIN transcript_results t ON t.id=v.result_id "
            "WHERE v.id=? AND v.result_id=? AND t.recording_id=?",
            (ref["version_id"], ref["result_id"], row["recording_id"]),
        ).fetchone()
        if parent is None or parent["payload_sha256"] != ref["payload_sha256"]:
            raise ValueError("The saved merge parent failed its identity check.")
        found[name] = parent
    if (
        found["creation"]["sequence"] != 1
        or found["creation"]["source_version_id"] != evidence["base"]["version_id"]
        or found["incoming"]["sequence"] < found["creation"]["sequence"]
    ):
        raise ValueError("The saved merge mapping ancestry is invalid.")
    return evidence


def _audio_evidence(row):
    """Validate frozen observations only; no current media, decoder or provider is consulted."""
    from .audio_inputs import validate, validate_evidence

    context = row["context"]
    remote_projection = None
    if "remote_audio_preparation" in context:
        from .remote_audio_input import report_preparation

        remote_projection = report_preparation(context)
    row["remote_audio_preparation_evidence"] = remote_projection
    row["remote_preparation_projection_match"] = False
    if "audio_input" not in context:
        if "audio_preparations" in context:
            raise ValueError("The saved audio preparation has no bound original source.")
        if remote_projection is not None:
            raise ValueError("The saved remote preparation has no bound original source.")
        return None, []
    try:
        source = validate(context["audio_input"])
        if source["recording_id"] != row["recording_id"]:
            raise ValueError()
        preparations = context.get("audio_preparations", [])
        if not isinstance(preparations, list) or len(preparations) > 200:
            raise ValueError()
        preparations = [validate_evidence(receipt, source) for receipt in preparations]
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        raise ValueError("The saved original audio evidence failed its identity check.") from None
    projected_source = {
        key: source[key]
        for key in (
            "policy",
            "sha256",
            "bytes",
            "format",
            "audio_stream",
            "audio_stream_count",
            "timebase",
            "recipe",
            "duration",
            "duration_basis",
            "audio_frames",
            "pcm_sha256",
            "container_duration_observation",
        )
    }
    projected_source["decoder_sha256"] = source["decoder"]["sha256"]
    projected_source["source_origin"] = {
        key: source["source_origin"][key]
        for key in ("basis", "first_pts", "normalized_first_pts", "has_video")
    }
    projected_preparations = []
    for receipt in preparations:
        parts = receipt["parts"] if "parts" in receipt else [receipt]
        remote_match = bool(
            remote_projection is not None
            and len(parts) == 1
            and json.dumps(
                {key: parts[0][key] for key in ("window", "prepared")},
                sort_keys=True,
                allow_nan=False,
            )
            == json.dumps(
                {key: context["remote_audio_preparation"][key] for key in ("window", "prepared")},
                sort_keys=True,
                allow_nan=False,
            )
        )
        row["remote_preparation_projection_match"] |= remote_match
        projected_preparations.append(
            {
                "observation": "remote_decoded_pcm" if remote_match else "local_decoded_pcm",
                **({"remote_decoder": remote_projection["decoder"]} if remote_match else {}),
                "transport": receipt.get("transport", "wav"),
                "windows": [
                    {
                        "start_frame": part["window"]["start_frame"],
                        "end_frame": part["window"]["end_frame"],
                        "timebase": part["window"]["timebase"],
                        "output": {
                            key: part["prepared"][key]
                            for key in (
                                "sha256",
                                "bytes",
                                "format",
                                "frames",
                                "pcm_sha256",
                                "sample_rate",
                                "channels",
                                "sample_width",
                                "sample_basis",
                            )
                        },
                    }
                    for part in parts
                ],
            }
        )
    return projected_source, projected_preparations


def _artifact_methods(evidence):
    """Render only the already verified public byte/environment projection."""
    details = []
    request = evidence["request"]["artifacts"]["request"]
    details.append(f"captured request SHA-256={request['sha256']}; bytes={request['bytes']}")
    for stage, checkpoint in evidence["stages"].items():
        if checkpoint == "not_captured":
            continue
        output = checkpoint["artifacts"]["output"]
        details.append(f"{stage} output SHA-256={output['sha256']}; bytes={output['bytes']}")
        environment = checkpoint["artifacts"]["environment"]
        if environment == "not_captured":
            details.append(f"{stage} worker environment was not captured")
            continue
        facts = environment["environment"]
        details.append(
            f"{stage} environment SHA-256={environment['sha256']}; bytes={environment['bytes']}"
        )
        if facts["python"]:
            details.append(
                f"{stage} worker Python={facts['python']['implementation']} "
                f"{facts['python']['version']}"
            )
        if facts["platform"]:
            details.append(
                f"{stage} worker platform={facts['platform']['system']} "
                f"{facts['platform']['machine']}"
            )
        if facts["device"]:
            device = facts["device"]
            details.append(
                f"{stage} worker device={device['type']}; index={device['index']}; "
                f"memory_bytes={device['memory_bytes']}"
            )
        details.extend(
            f"{stage} worker package {name}={value}"
            for name, value in sorted((facts["packages"] or {}).items())
        )
        if facts["code"]:
            details.append(
                f"{stage} worker script SHA-256={facts['code']['worker_sha256']}; "
                f"application SHA-256={facts['code']['application_sha256']}; "
                f"lock SHA-256={facts['code']['lock_sha256']}"
            )
    details.append(
        "artifact retained-file availability=not_checked; "
        "allowlisted worker facts do not establish full reproducibility"
    )
    return details


def _runtime_artifact_methods(evidence):
    request = evidence["request"]["artifacts"]["request"]
    details = [
        f"Runtime request captured before publication: SHA-256={request['sha256']}; "
        f"bytes={request['bytes']}",
        f"accepted Runtime input manifest SHA-256={evidence['input_manifest_sha256']}",
    ]
    details += [
        f"Runtime {item['kind']} input SHA-256={item['sha256']}; bytes={item['bytes']}; "
        f"observation={item['observation']}"
        for item in evidence["input_observations"]
    ]
    for kind, checkpoint in evidence["outputs"].items():
        output = checkpoint["artifacts"]["output"]
        details.append(
            f"Runtime {kind} output SHA-256={output['sha256']}; bytes={output['bytes']}; "
            "boundary=validated_local_collection"
        )
    details.append(
        "Runtime worker environment and raw API responses were not captured; "
        "retained-file availability=not_checked"
    )
    return details


def _native_artifact_methods(evidence):
    """Render the verified native submission/output projection; no IDs, paths or names."""
    details = []
    for item in evidence["submissions"]:
        application = item["application"] or {}
        details.append(
            f"Native {evidence['scope']} submission attempt {item['attempt']}: "
            f"descriptor SHA-256={item['request']['sha256']}; bytes={item['request']['bytes']}; "
            f"application={application.get('name')} {application.get('version')}; "
            f"product={item['product_id']}; withheld parameters={len(item['withheld_parameters'])}"
        )
    output = evidence["output"]
    details.append(
        "native output was reused without an observation"
        if output == "not_captured"
        else f"native downloaded output SHA-256={output['sha256']}; bytes={output['bytes']}"
    )
    details.append(
        "native remote execution and environment were not captured; "
        "retained-file availability=not_checked"
    )
    return details


def _provider_artifact_methods(evidence):
    """Render the verified API request/output projection; no endpoint, IDs or paths."""
    details = [
        f"API provider type={evidence['provider_type']}; logical request fields="
        + json.dumps(evidence["parameters"], ensure_ascii=False, sort_keys=True)
        + "; assembled result SHA-256="
        + evidence["assembly_sha256"]
    ]
    for part in evidence["parts"]:
        for attempt in part["attempts"]:
            request, output = attempt["request"], attempt["output"]
            details.append(
                f"API {part['part']} attempt {attempt['attempt']}: input "
                f"{attempt['input']['scope']} SHA-256={attempt['input']['sha256']}; "
                f"request descriptor SHA-256={request['sha256']}; bytes={request['bytes']}; "
                + (
                    "normalized output was not observed"
                    if output == "outcome_not_observed"
                    else f"normalized output SHA-256={output['sha256']}; bytes={output['bytes']}"
                )
            )
        if part["output_observation"] == "not_captured":
            details.append(
                f"API {part['part']} output was reused from a preserved file without an "
                "observation; normalized part SHA-256=" + part["normalized_part_sha256"]
            )
    details.append(
        "API wire requests/responses, server processing and environment were not captured; "
        "retained-file availability=not_checked"
    )
    return details


def project_report(
    store, project_id, selections=None, include_selected_transcripts=False, collection=None
):
    """Build a local research ZIP; text inclusion never includes originals or review history."""
    if type(include_selected_transcripts) is not bool:
        raise ValueError("Choose whether to include the selected transcript text.")
    chosen, captured, total_recordings, skipped, role_histories = _capture(
        store, project_id, selections, collection
    )
    recording_aliases, result_aliases, artifact_aliases, audio_aliases = {}, {}, {}, {}
    version_aliases = {identifier: f"version-{i:03}" for i, identifier in enumerate(captured, 1)}
    versions, exports, contents = [], [], {}
    selected_ids = {key[2] for key in chosen}
    for identifier, row in captured.items():
        recording = recording_aliases.setdefault(
            row["recording_id"], f"recording-{len(recording_aliases) + 1:03}"
        )
        result = result_aliases.setdefault(row["result_id"], f"result-{len(result_aliases) + 1:03}")
        context = row["context"]
        audio = row["audio_input_evidence"]
        gaps = (
            [
                "Raw provider response hashes and the full processing environment "
                "are not yet captured."
            ]
            if audio is not None
            else [
                "Original media/raw response hashes and full processing environment "
                "are not yet captured."
            ]
        )
        if row["action"] == "legacy_observed":
            gaps.append("Review history before this observation is unknown.")
        processing_observation = context.get("processing_observation")
        if processing_observation not in {"legacy_unverified", "captured_at_creation"}:
            processing_observation = "unknown"
        if processing_observation == "legacy_unverified":
            gaps.append(
                "Legacy processing settings are observed source metadata, "
                "not verified run evidence."
            )
        if context.get("source_version_unknown"):
            gaps.append("The frozen processing input's historical version is unknown.")
        processing = _processing(context.get("processing"))
        if not processing:
            gaps.append("Processing settings were not captured at this version's observation.")
        elif (
            processing.get("provider_type") == "whisperx"
            or processing.get("scope") == "passage_retranscription"
        ) and not {"language_evidence", "language_decision"} & processing.keys():
            gaps.append("Bound language processing evidence was not captured.")
        if "language_decision" in processing:
            outcome = processing.get("alignment_outcome")
            revisions = processing.get("model_revisions", {})
            if outcome is None:
                gaps.append("The word-alignment outcome was not captured.")
            elif (
                outcome["state"] == "aligned"
                and processing["language_decision"]["effective"] not in revisions
            ):
                gaps.append("The alignment model revision was not captured.")
            if processing.get("options", {}).get("diarize") is True and "speakers" not in revisions:
                gaps.append("The speaker model revision was not captured.")
        actor = (
            row["actor_kind"]
            if row["actor_kind"] in {"local_user", "worker", "migration"}
            else "unknown"
        )
        item = {
            "id": version_aliases[identifier],
            "recording": recording,
            "result": result,
            "result_in_trash": bool(row["result_in_trash"]),
            "sequence": row["sequence"],
            "selected": identifier in selected_ids,
            "payload_sha256": row["payload_sha256"],
            "observed_at": row["observed_at"],
            "action": row["action"] if row["action"] in ACTIONS else "unknown",
            "actor_kind": actor,
            "actor_identity_verified": False,
            "predecessor": version_aliases.get(row["predecessor_id"]),
            "source_version": version_aliases.get(row["source_version_id"]),
            "processing": processing,
            "processing_observation": processing_observation,
            "coverage": _coverage(row["payload"]),
            "working_designation": row["working_designation"],
            "provenance_gaps": gaps,
        }
        artifact = row["import_artifact_evidence"]
        if row["producer_artifact_evidence"] is not None:
            item["producer_artifacts"] = row["producer_artifact_evidence"]
        if row["runtime_artifact_evidence"] is not None:
            item["runtime_artifacts"] = row["runtime_artifact_evidence"]
        native = row["native_artifact_evidence"]
        if native is not None:
            item["native_artifacts"] = native
            gaps.append(
                "Native UCloud execution, model weights and the remote environment were not "
                "captured; the submission evidence is a credential-free projection."
            )
            if native["output"] == "not_captured":
                gaps.append(
                    "The native output was reused from a preserved file without an observation."
                )
        provider = row["provider_artifact_evidence"]
        if provider is not None:
            item["provider_artifacts"] = provider
            gaps.append(
                "Provider wire-level request/response bytes, server-side processing and the "
                "processing environment were not captured; outputs are normalized SDK responses."
            )
            if any(
                part["output_observation"] == "not_captured"
                or any(attempt["output"] == "outcome_not_observed" for attempt in part["attempts"])
                for part in provider["parts"]
            ):
                gaps.append(
                    "Some provider part requests or preserved outputs have no published "
                    "observation; they remain explicit gaps."
                )
        if row["remote_audio_preparation_evidence"] is not None:
            item["remote_audio_preparation"] = row["remote_audio_preparation_evidence"]
            item["remote_preparation_projection_match"] = row["remote_preparation_projection_match"]
            if not row["remote_preparation_projection_match"]:
                gaps.append(
                    "The remote preparation receipt has no matching standard "
                    "audio preparation projection."
                )
        if row.get("conflict_review_evidence"):
            from .result_conflicts import public_projection

            item["conflict_review"] = public_projection(row, version_aliases)
        if row["context"].get("timing_artifacts") is not None:
            from .timing_capture import public_projection as project_timing

            item["timing_artifacts"] = project_timing(row, version_aliases)
        if audio is not None:
            identity = (row["recording_id"], audio["sha256"], audio["bytes"])
            alias = audio_aliases.setdefault(identity, f"audio-source-{len(audio_aliases) + 1:03}")
            item["audio_input"] = {"source": alias, **audio, "availability": "not_checked"}
            if row["audio_preparation_evidence"]:
                item["audio_preparations"] = row["audio_preparation_evidence"]
            else:
                gaps.append("Local audio preparation or transfer evidence was not captured.")
        if artifact is not None:
            aliases = artifact_aliases.setdefault(row["recording_id"], {})
            source_id = artifact["source_id"]
            if source_id not in aliases:
                aliases[source_id] = (f"source-{len(aliases) + 1:03}", artifact)
            alias, previous = aliases[source_id]
            if previous != artifact:
                raise ValueError("The imported transcript artifact identity is contradictory.")
            item["artifact_evidence"] = {
                "source": alias,
                **{key: artifact[key] for key in ("role", "sha256", "bytes", "format")},
                "parser_generation": artifact["schema"],
                "availability": "not_checked",
            }
        if row.get("merge_evidence"):
            evidence = row["merge_evidence"]
            item["merge"] = {
                "format": evidence["format"],
                **{
                    name + "_version": version_aliases[evidence[name]["version_id"]]
                    for name in ("base", "incoming", "creation")
                },
                "summary": evidence["summary"],
            }
        if row.get("wording_adoption_evidence"):
            evidence = row["wording_adoption_evidence"]
            target_ordinal, source_ordinal = row["wording_adoption_passages"]
            item["wording_adoption"] = {
                "source_version": version_aliases[evidence["source"]["version_id"]],
                "source_passage": f"passage-{source_ordinal:03}",
                "target_passage": f"passage-{target_ordinal:03}",
            }
        versions.append(item)
        if include_selected_transcripts and identifier in selected_ids:
            path = f"transcripts/{recording}_{result}_{item['id']}.txt"
            content = export_bytes(
                row["payload"]["segments"], row["payload"]["speakers"], "speaker", "text"
            )
            contents[path] = content
            exports.append(
                {
                    "path": path,
                    "version": item["id"],
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "renderer": RENDERER,
                }
            )
    try:
        app_version = version("cbs-transcriber")
    except PackageNotFoundError:
        app_version = "unknown"
    manifest = {
        "format": FORMAT,
        "generated_at": datetime.now(UTC).isoformat(),
        "report_generator": {"app_version": app_version, "text_renderer": RENDERER},
        "inclusion": {
            "selected_transcript_text": include_selected_transcripts,
            "speaker_names_in_selected_text": include_selected_transcripts,
            "project_recording_names": False,
            "unselected_history_text": False,
            "preserved_originals": False,
            "notes_reasons": False,
            "media_raw_responses": False,
        },
        "selection": {
            "project_recordings": total_recordings,
            "selected_versions": len(chosen),
            "selected_recordings": len({key[0] for key in chosen}),
            "default_scope": "working_versions" if selections is None else "explicit_versions",
            "skipped_unassigned_recordings": len(skipped),
        },
        "skipped": [
            {
                "recording": recording_aliases.setdefault(
                    identifier, f"recording-{len(recording_aliases) + 1:03}"
                ),
                "reason": "working_version_unassigned",
            }
            for identifier in skipped
        ],
        "versions": versions,
        "role_history": [
            {"recording": f"recording-{index:03}", **value}
            for index, value in enumerate(role_histories.values(), 1)
        ],
        "exports": exports,
        "manual_change_counts": dict(
            sorted(
                Counter(
                    item["action"] for item in versions if item["action"] in EDIT_ACTIONS
                ).items()
            )
        ),
        "limitations": [
            "Observation times are not verified authorship or original inference times.",
            "Hashes provide integrity checks, not anonymity or tamper-proof evidence.",
            "Opaque API model revisions and stochastic inference may prevent exact reruns.",
            "Report-generation software is not evidence of earlier processing environments.",
            "This methods draft requires researcher review; journal compliance is not certified.",
        ],
    }
    lines = [
        "# Research workflow evidence",
        "",
        "This methods draft requires researcher review.",
        "",
        "Default project reports select the exact explicitly marked Working versions. "
        f"Recordings skipped because no Working version is assigned: {len(skipped)}. "
        "Role observations describe local actions, not verified researcher identities.",
        "",
        "Imported transcript receipts describe bytes captured at import. Retained-file "
        "availability is not checked here. These receipts do not establish a complete backup "
        "or capture original media and API responses.",
        "",
        "Original audio references describe frozen local source observations; retained-media "
        "availability is not checked. Audio preparation receipts describe local decoded PCM "
        "observations, not provider-side decoding or internal processing proof.",
        "",
        f"Selected versions: {len(chosen)}. Preserved states in lineage: {len(versions)}.",
        "",
        "## Selected observations",
        "",
    ]
    for item in versions:
        if item["selected"]:
            lines += [
                f"- {item['recording']} / {item['result']} / {item['id']}: "
                f"{item['action']}; observed {item['observed_at']}. "
                f"{item['coverage']['timed_lexical_tokens']} of "
                f"{item['coverage']['lexical_tokens']} lexical tokens have usable timing."
            ]
    lines += ["", "## Processing and review observations", ""]
    for item in sorted(versions, key=lambda v: (v["observed_at"], v["id"])):
        processing = item["processing"]
        details = [
            f"{key}={processing[key]}"
            for key in (
                "provider_type",
                "model",
                "language",
                "execution",
                "engine",
                "version",
                "capability",
            )
            if key in processing
        ]
        if item.get("merge"):
            merge = item["merge"]
            details.append(
                f"explicit enhancement merge from {merge['incoming_version']} "
                f"with base {merge['base_version']}; "
                f"{merge['summary']['updated_passages']} passage(s) updated"
            )
        if item.get("wording_adoption"):
            adoption = item["wording_adoption"]
            details.append(
                f"explicit wording adoption from {adoption['source_version']} / "
                f"{adoption['source_passage']} into {adoption['target_passage']}; "
                "source timing and speaker were not copied"
            )
        if item.get("artifact_evidence"):
            artifact = item["artifact_evidence"]
            details.append(
                f"transcript import={item['recording']}/{artifact['source']}; "
                f"SHA-256={artifact['sha256']}; bytes={artifact['bytes']}; "
                f"format={artifact['format']}; retained-file availability not checked"
            )
        if item.get("producer_artifacts"):
            details += _artifact_methods(item["producer_artifacts"])
        if item.get("runtime_artifacts"):
            details += _runtime_artifact_methods(item["runtime_artifacts"])
        if item.get("provider_artifacts"):
            details += _provider_artifact_methods(item["provider_artifacts"])
        if item.get("native_artifacts"):
            details += _native_artifact_methods(item["native_artifacts"])
        if item.get("remote_audio_preparation"):
            prepared = item["remote_audio_preparation"]
            details.append(
                "remote audio preparation was verified separately from recognition; "
                "standard preparation projection "
                f"matched={item['remote_preparation_projection_match']}; "
                f"decoder={prepared['decoder']['version']}; "
                f"decoder SHA-256={prepared['decoder']['sha256']}"
            )
        if item.get("timing_artifacts"):
            timing = item["timing_artifacts"]
            observed = [
                unit["observation"]
                for unit in timing["units"]
                if unit["observation"] != "not_captured"
            ]
            aligned = sum(unit["stages"]["alignment"] != "not_captured" for unit in observed)
            details.append(
                f"local timing source={timing['source_version']}; "
                f"application={timing['application_version']}; "
                f"applied passages={timing['applied_passages']}; "
                f"captured requests={len(observed)}; "
                f"captured alignment units={aligned}; unit gaps={len(timing['units']) - aligned}; "
                f"normalized output SHA-256={timing['assembled_output_sha256']}"
            )
            for unit in observed:
                details += _artifact_methods(unit)
        if item.get("audio_input"):
            audio = item["audio_input"]
            details.append(
                f"original audio={audio['source']}; policy={audio['policy']}; "
                f"SHA-256={audio['sha256']}; bytes={audio['bytes']}; format={audio['format']}; "
                f"stream={audio['audio_stream']}; timebase={audio['timebase']}; "
                f"duration basis={audio['duration_basis']}; frames={audio['audio_frames']}; "
                f"decoder SHA-256={audio['decoder_sha256']}; recipe={audio['recipe']}"
            )
        details += [f"{key}={value}" for key, value in processing.get("options", {}).items()]
        if processing.get("options", {}).get("timing_policy") == "retain-estimates":
            details.append(
                "valid estimated word intervals were retained below the legacy alignment-score "
                "cutoff; alignment scores are review hints, not measured timing accuracy"
            )
        details += [
            f"{stage} model={value['repo']}@{value['revision']}"
            for stage, value in processing.get("model_revisions", {}).items()
        ]
        if processing.get("language_evidence"):
            details.append(
                "language receipts describe configured decoding, "
                "not measured spoken-language detection"
            )
            details += [
                f"ASR model={repo}@{revision}"
                for repo, revision in sorted(
                    {
                        (receipt["model"]["repo"], receipt["model"]["revision"])
                        for receipt in processing["language_evidence"]
                    }
                )
            ]
            details += [
                f"ASR engine={engine}; observed package version={version or 'not captured'}"
                for engine, version in sorted(
                    {
                        (receipt["engine"]["name"], receipt["engine"]["version"])
                        for receipt in processing["language_evidence"]
                    },
                    key=lambda entry: (entry[0], entry[1] or ""),
                )
            ]
        if conversion := processing.get("asr_conversion"):
            recipe, source = conversion["recipe"], conversion["source"]
            details += [
                "ASR used a captured CTranslate2 conversion of a pinned Danish Whisper source; "
                f"source={source['repo']}@{source['revision']}; "
                f"source manifest SHA-256={source['manifest_sha256']}",
                f"conversion recipe={recipe['id']}; quantization={recipe['quantization']}; "
                f"recipe SHA-256={recipe['sha256']}; "
                f"contract SHA-256={recipe['contract_sha256']}; "
                f"artifact SHA-256={conversion['artifact_sha256']}",
                "conversion software="
                + ", ".join(
                    f"{name} {recipe[name]}"
                    for name in ("ctranslate2", "transformers", "safetensors", "torch_build")
                ),
                "conversion hashes are captured provenance; this report does not check model "
                "files, establish current availability or measure model accuracy",
            ]
        decision = processing.get("language_decision")
        if decision:
            probability = decision["probability"]
            probability = probability if probability is not None else "not captured"
            details += [
                "Automatic ASR requested; "
                f"model-reported decoder language={decision['effective'] or 'not captured'}; "
                f"language outcome={decision['status']}",
                "language scope=full ASR input; detector source window is unknown",
                f"model-reported language probability={probability}; not measured accuracy",
                f"language policy={decision['policy']['id']}; experimental uncalibrated "
                f"support cutoff={decision['policy']['min_probability']}",
                f"ASR model={decision['model']['repo']}@{decision['model']['revision']}",
                f"ASR engine={decision['engine']['name']}; observed package version="
                f"{decision['engine']['version'] or 'not captured'}",
            ]
            if decision["status"] == "no_speech":
                details.append("ASR/VAD reported no speech after filtering; no passages retained")
            elif decision["status"] == "uncertain" and decision["segment_count"] == 0:
                details.append("ASR returned no usable passages; language remains uncertain")
            outcome = processing.get("alignment_outcome")
            if outcome:
                details.append(
                    {
                        "aligned": (
                            "word alignment stage ran; usable coverage is reported separately"
                        ),
                        "unavailable_language": (
                            "word alignment unavailable for language; "
                            "ASR output retained and no word timing added"
                        ),
                        "not_requested": "word alignment was not requested",
                    }[outcome["state"]]
                )
            else:
                details.append("word-alignment outcome was not captured")
        lineage = "; ".join(
            f"{key}={item[key]}" for key in ("predecessor", "source_version") if item[key]
        )
        lines += [
            f"- {item['id']} ({item['result']}): {item['action']}; "
            f"observed {item['observed_at']}; actor category {item['actor_kind']} "
            f"(identity unverified). Processing evidence: {item['processing_observation']}. "
            + ("; ".join(details) + ". " if details else "Settings unavailable. ")
            + (lineage + "." if lineage else "")
        ]
    lines += ["", "## Provenance gaps and limits", ""]
    lines += [
        f"- {gap}" for gap in sorted({gap for item in versions for gap in item["provenance_gaps"]})
    ]
    lines += [f"- {limitation}" for limitation in manifest["limitations"]]
    lines += [
        "",
        "## Inclusion",
        "",
        "Project/recording names, notes, reasons, paths, endpoint/account/job IDs, "
        "media, raw responses and discarded originals are excluded. "
        + (
            "Only the selected versions' visible transcript text and "
            "speaker labels/names are included."
            if include_selected_transcripts
            else "Transcript text and speaker names are excluded."
        ),
        "",
    ]
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest_bytes)
        archive.writestr("methods.md", "\n".join(lines).encode("utf-8"))
        for path, content in contents.items():
            archive.writestr(path, content)
    return stream.getvalue()
