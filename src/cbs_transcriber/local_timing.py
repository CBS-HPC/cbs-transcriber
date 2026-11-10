"""Atomic same-result timing successors and exact lost-reply receipts."""

import copy
import hashlib
import json
import re

from .history import decode_payload, encode_payload, validate_revision
from .transcript_merge import _index, _valid_timing
from .transcripts import MAX_TRANSCRIPT_BYTES

FORMAT = "cbs-local-timing-apply-v1"
MAP_FIELDS = ("text", "start", "end", "words", "word_timing", "words_current")
MAX_DOCUMENT_BYTES = 2 * MAX_TRANSCRIPT_BYTES + 1024 * 1024


def _digest(value):
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise ValueError("The frozen timing request exceeds the supported size.")
    return hashlib.sha256(encoded).hexdigest()


def assert_params_preserved(previous, proposed, *, authority=None, policy=None):
    if authority is not None:
        from . import timing_working

        timing_working.validate_authorized_params(authority, policy, previous)
        timing_working.validate_authorized_params(authority, policy, proposed)
        keys = (
            (timing_working.KEY, "realign_receipt", "realign_applied")
            if policy == "timing"
            else (timing_working.ROLE_KEY, timing_working.ROLE_POLICY_KEY)
            if authority["admission_sha256"] is None
            else ("input_acceptance",)
        )
        if any(
            (key in previous) != (key in proposed)
            or key in previous
            and _digest(previous[key]) != _digest(proposed[key])
            for key in keys
        ):
            raise ValueError("Frozen Working evidence and its source cannot be changed.")
        if (
            policy == "timing"
            and previous.get("realign_applied") == "applied"
            and _digest(previous.get("realign_output")) != _digest(proposed.get("realign_output"))
        ):
            raise ValueError("Applied timing output cannot be changed.")
    receipt = previous.get("realign_apply_receipt")
    if _digest(receipt) != _digest(proposed.get("realign_apply_receipt")):
        raise ValueError(
            "Timing application receipts are written only by the atomic timing writer."
        )
    if receipt is not None and any(
        _digest(previous.get(key)) != _digest(proposed.get(key))
        for key in (
            "realign_applied",
            "source_snapshot",
            "realign_output",
            "realign",
            "segment_id",
            "segment_ids",
            "timing_units",
            "enhancement",
        )
    ):
        raise ValueError("An applied timing request and its receipt cannot be changed.")


def _inputs(db, recording_id, result_id, params):
    source = params.get("source_snapshot")
    if not isinstance(source, dict) or source.get("id") != result_id:
        raise ValueError("Open the result this word timing belongs to.")
    segments, speakers = source.get("segments"), source.get("speakers")
    encode_payload(segments, speakers)
    frozen = _index({"segments": segments, "speakers": speakers})
    source_id = source.get("source_version_id")
    if params.get("timing_artifact_policy") is not None and source_id is None:
        raise ValueError("Captured timing requires an exact immutable source version.")
    if source_id is not None:
        if (
            not isinstance(source_id, str)
            or re.fullmatch(r"[a-f0-9]{32}", source_id) is None
            or not isinstance(source.get("source_payload_sha256"), str)
            or re.fullmatch(r"[a-f0-9]{64}", source["source_payload_sha256"]) is None
        ):
            raise ValueError("The exact frozen timing source reference is invalid.")
        size = db.execute(
            "SELECT length(CAST(payload AS BLOB)) FROM result_versions WHERE id=?", (source_id,)
        ).fetchone()
        if size is None or size[0] > MAX_TRANSCRIPT_BYTES:
            raise ValueError("The exact frozen timing source exceeds the supported size.")
        row = db.execute(
            "SELECT v.* FROM result_versions v JOIN transcript_results t ON t.id=v.result_id "
            "WHERE v.id=? AND v.result_id=? AND t.recording_id=?",
            (source_id, result_id, recording_id),
        ).fetchone()
        if row is None or row["payload_sha256"] != source.get("source_payload_sha256"):
            raise ValueError("The exact frozen timing source failed its ownership check.")
        payload = decode_payload(row["payload"], row["payload_sha256"])
        selected = {item["id"]: item for item in payload["segments"]}
        if payload["speakers"] != speakers or any(
            _digest(selected.get(key)) != _digest(value) for key, value in frozen.items()
        ):
            raise ValueError("The frozen timing projection differs from its exact source version.")
    output = params.get("realign_output")
    if not isinstance(output, dict):
        raise ValueError("There is no new word timing to apply.")
    outputs = output.get("passages") if "passages" in output else {params.get("segment_id"): output}
    if not isinstance(outputs, dict) or not outputs or any(key not in frozen for key in outputs):
        raise ValueError("The timing output does not match its frozen passages.")
    return frozen, outputs, _digest({"source": source, "output": output})


def _candidate(passage, timing):
    if (
        not isinstance(timing, dict)
        or timing.keys() - {"words", "word_timing", "start", "end"}
        or not isinstance(timing.get("words"), list)
        or type(timing.get("word_timing")) is not bool
        or ("start" in timing) != ("end" in timing)
    ):
        raise ValueError("The completed timing output is invalid. Preserve its artifacts.")
    if any(
        not isinstance(word, dict) or "manual_timing" in word or "alignment_hints_stale" in word
        for word in timing["words"]
    ):
        raise ValueError("The completed timing output cannot assert manual timing evidence.")
    updated = copy.deepcopy(passage)
    updated.update(
        words=copy.deepcopy(timing["words"]), word_timing=timing["word_timing"], words_current=True
    )
    if "start" in timing and (passage.get("start") is None or passage.get("end") is None):
        updated.update(start=timing["start"], end=timing["end"])
    valid, timed = _valid_timing(updated, passage)
    if not valid or not timed:
        raise ValueError("The completed timing has no valid mapping for its frozen wording.")
    source_words = passage.get("words", [])
    if isinstance(source_words, list) and any(
        key in word and word[key] is not True
        for word in source_words
        if isinstance(word, dict)
        for key in ("manual_timing", "alignment_hints_stale")
    ):
        raise ValueError("The frozen manual timing evidence is invalid.")
    if isinstance(source_words, list) and any(
        word.get("manual_timing") is True or word.get("alignment_hints_stale") is True
        for word in source_words
        if isinstance(word, dict)
    ):
        # The saved result retains its original hint document. New model timings
        # cannot make that document evidence for the new intervals.
        for word in updated["words"]:
            word["alignment_hints_stale"] = True
    if "start" in timing and (passage.get("start") is None or passage.get("end") is None):
        if (
            not timing["word_timing"]
            or timing["start"] != timing["words"][0]["start"]
            or timing["end"] != timing["words"][-1]["end"]
        ):
            raise ValueError(
                "Unbounded passage timing must use only its own completely timed words."
            )
    return updated


def _successor(segments, frozen, outputs):
    changed = copy.deepcopy(segments)
    applied, skipped = [], []
    for index, passage in enumerate(segments):
        identifier = passage["id"]
        if identifier not in outputs:
            continue
        # Validate producer output independently, including targets which have since changed.
        _candidate(frozen[identifier], outputs[identifier])
        if _digest({field: passage[field] for field in MAP_FIELDS if field in passage}) != _digest(
            {
                field: frozen[identifier][field]
                for field in MAP_FIELDS
                if field in frozen[identifier]
            }
        ):
            skipped.append(identifier)
            continue
        changed[index] = _candidate(passage, outputs[identifier])
        applied.append(identifier)
    skipped.extend(
        identifier for identifier in outputs if identifier not in {s["id"] for s in segments}
    )
    return changed, applied, skipped


def _existing(db, recording_id, result_id, job, params, input_hash):
    receipt = params.get("realign_apply_receipt")
    if (
        not isinstance(receipt, dict)
        or set(receipt) - {"working_choice", "working_follow_warning"}
        != {
            "format",
            "version",
            "previous",
            "base_revision",
            "applied_revision",
            "input_sha256",
            "applied_ids",
            "skipped_ids",
        }
        or receipt.get("format") != FORMAT
        or receipt.get("input_sha256") != input_hash
        or any(
            not isinstance(receipt[key], dict) or set(receipt[key]) != {"id", "payload_sha256"}
            for key in ("version", "previous")
        )
        or any(
            not isinstance(receipt[key][field], str)
            or re.fullmatch(
                r"[a-f0-9]{32}" if field == "id" else r"[a-f0-9]{64}", receipt[key][field]
            )
            is None
            for key in ("version", "previous")
            for field in ("id", "payload_sha256")
        )
        or type(receipt["base_revision"]) is not int
        or receipt["base_revision"] < 0
        or type(receipt["applied_revision"]) is not int
        or receipt["applied_revision"] != receipt["base_revision"] + 1
    ):
        raise ValueError("The applied timing receipt is invalid. Preserve its history.")
    version = db.execute(
        "SELECT * FROM result_versions WHERE id=? AND result_id=?",
        (receipt["version"]["id"], result_id),
    ).fetchone()
    previous = db.execute(
        "SELECT * FROM result_versions WHERE id=? AND result_id=?",
        (receipt["previous"]["id"], result_id),
    ).fetchone()
    if version is None or previous is None:
        raise ValueError("The applied timing version is missing. Preserve its history.")
    from . import timing_working

    if receipt.get("working_follow_warning") is not None and (
        receipt["working_follow_warning"] != timing_working.FOLLOW_WARNING
    ):
        raise ValueError("The applied timing Working warning is invalid.")
    timing_working.validate_choice_receipt(db, recording_id, receipt, previous["id"], version)
    context, previous_context = json.loads(version["context"]), json.loads(previous["context"])
    if not isinstance(context, dict) or not isinstance(previous_context, dict):
        raise ValueError("The applied timing context is invalid. Preserve its history.")
    if (
        version is None
        or previous is None
        or version["payload_sha256"] != receipt["version"]["payload_sha256"]
        or previous["payload_sha256"] != receipt["previous"]["payload_sha256"]
        or version["predecessor_id"] != previous["id"]
        or version["sequence"] != previous["sequence"] + 1
        or version["source_version_id"] != previous["source_version_id"]
        or context.get("job_id") != job["id"]
        or version["action"] not in {"timing_realign", "save_no_change"}
    ):
        raise ValueError("The applied timing version failed its integrity check.")
    payload = decode_payload(previous["payload"], previous["payload_sha256"])
    frozen = {item["id"]: item for item in params["source_snapshot"]["segments"]}
    output = params["realign_output"]
    outputs = output.get("passages") if "passages" in output else {params.get("segment_id"): output}
    successor, applied, skipped = _successor(payload["segments"], frozen, outputs)
    if (
        encode_payload(successor, payload["speakers"])[0] != version["payload"]
        or applied != receipt.get("applied_ids")
        or skipped != receipt.get("skipped_ids")
    ):
        raise ValueError("The applied timing receipt does not reconstruct its exact successor.")
    decode_payload(version["payload"], version["payload_sha256"])
    if context.get("producer_artifacts") != previous_context.get("producer_artifacts"):
        raise ValueError("The timing successor changed its original producer evidence.")
    if params.get("timing_artifact_policy") is not None:
        from . import timing_capture

        evidence = timing_capture.for_application(job, recording_id, result_id, applied)
        expected = timing_capture.bind(
            evidence,
            creation={
                "result_id": result_id,
                "version_id": version["id"],
                "payload_sha256": version["payload_sha256"],
            },
        )
        if context.get("timing_artifacts") != expected:
            raise ValueError("The applied timing artifact receipt changed. Preserve its history.")
    return receipt


def apply(store, recording_id, result_id, job_id, revision, *, working_choice=None):
    from .result_actions import append_edit
    from .store import now

    validate_revision(revision)
    stale, warning = False, None
    with store.snapshot_lock:
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            from . import restored_owners

            restored_owners.require_owned(db, "job", job_id)
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None or job["recording_id"] != recording_id or job["kind"] != "enhance":
                raise KeyError(job_id)
            if len(job["params"].encode("utf-8")) > MAX_DOCUMENT_BYTES:
                raise ValueError("The frozen timing request exceeds the supported size.")
            params = json.loads(job["params"])
            if not isinstance(params, dict):
                raise ValueError("The frozen timing request is invalid.")
            from . import result_roles, timing_working

            authority = result_roles.job_authority(db, job_id, recording_id, "timing")
            if authority is not None:
                timing_working.validate_authorized_params(authority, "timing", params)
            if job["state"] != "completed" or params.get("realign") is not True:
                raise ValueError("There is no new word timing to apply.")
            frozen, outputs, input_hash = _inputs(db, recording_id, result_id, params)
            current = db.execute("SELECT * FROM recordings WHERE id=?", (recording_id,)).fetchone()
            result = db.execute(
                "SELECT * FROM transcript_results WHERE id=? AND recording_id=?",
                (result_id, recording_id),
            ).fetchone()
            if current is None or result is None:
                raise ValueError("The saved timing result no longer belongs to this recording.")
            if params.get("realign_applied") == "applied":
                if working_choice is not None and (
                    not isinstance(params.get("realign_apply_receipt"), dict)
                    or params["realign_apply_receipt"].get("working_choice") != working_choice
                ):
                    raise ValueError("This timing was already applied; choose Working separately.")
                receipt = _existing(db, recording_id, result_id, job, params, input_hash)
                warning = receipt.get("working_follow_warning")
                if revision not in {receipt["base_revision"], receipt["applied_revision"]}:
                    raise ValueError("This transcript changed. Reload before applying timing.")
            elif params.get("realign_applied"):
                raise ValueError("These passages changed. Start a new timing request.")
            else:
                if (
                    current["active_transcript_id"] != result_id
                    or db.execute(
                        "SELECT 1 FROM result_trash WHERE result_id=?", (result_id,)
                    ).fetchone()
                ):
                    raise ValueError("Open the result this word timing belongs to.")
                head = store._head_version(db, result)
                payload = decode_payload(head["payload"], head["payload_sha256"])
                if (
                    encode_payload(
                        json.loads(current["segments"]), json.loads(current["speakers"])
                    )[0]
                    != head["payload"]
                ):
                    raise ValueError(
                        "The recording and active result disagree. Preserve the library."
                    )
                if current["revision"] != revision:
                    raise ValueError("This transcript changed. Reload before applying timing.")
                segments, applied, skipped = _successor(payload["segments"], frozen, outputs)
                if applied:
                    evidence = None
                    if params.get("timing_artifact_policy") is not None:
                        from . import timing_capture

                        evidence = timing_capture.for_application(
                            job, recording_id, result_id, applied
                        )
                    version = append_edit(
                        store,
                        db,
                        current,
                        result_id,
                        head,
                        segments,
                        payload["speakers"],
                        action="timing_realign",
                        context={"job_id": job_id}
                        | ({"segment_id": applied[0]} if len(outputs) == 1 else {}),
                        reason=None,
                        timing_artifacts=evidence,
                        **(
                            {"working_choice": working_choice} if working_choice is not None else {}
                        ),
                    )
                    if working_choice is None:
                        from . import timing_working

                        followed = timing_working.follow(
                            store, db, recording_id, job_id, params, head, version
                        )
                        if authority is not None and not followed:
                            warning = timing_working.FOLLOW_WARNING
                    params["realign_applied"] = "applied"
                    params["realign_apply_receipt"] = {
                        "format": FORMAT,
                        "version": version,
                        "previous": {"id": head["id"], "payload_sha256": head["payload_sha256"]},
                        "base_revision": revision,
                        "applied_revision": revision + 1,
                        "input_sha256": input_hash,
                        "applied_ids": applied,
                        "skipped_ids": skipped,
                        **({"working_follow_warning": warning} if warning else {}),
                        **(
                            {"working_choice": working_choice} if working_choice is not None else {}
                        ),
                    }
                else:
                    params["realign_applied"], stale = "stale", True
                db.execute(
                    "UPDATE jobs SET params=?,updated=? WHERE id=?",
                    (json.dumps(params, ensure_ascii=False, allow_nan=False), now(), job_id),
                )
        if stale:
            raise ValueError(
                "The passages changed again; their newer text and timing were preserved."
            )
        store.snapshot_project(current["project_id"])
        saved = store.recording(recording_id)
        if warning:
            saved["working_follow_warning"] = warning
        return saved
