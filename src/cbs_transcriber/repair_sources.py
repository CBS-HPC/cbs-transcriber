"""Read-only frozen passage-source proof; never authorizes audio extraction or execution."""

import hashlib
import json
import math
import re

from .history import decode_payload, encode_payload

MAX_RECORD_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_TARGETS = 1000
FORMAT = "cbs-repair-source-v1"


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("The frozen repair source has an invalid identity.")
    return value


def _passage_id(value):
    if not isinstance(value, str) or not 0 < len(value) <= 100:
        raise ValueError("The frozen repair selection has an invalid passage identity.")
    return value


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("The frozen repair source has an invalid content hash.")
    return value


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _size(value, used):
    if type(value) is not int or not 0 < value <= MAX_RECORD_BYTES:
        raise ValueError("The frozen repair source exceeds its record byte limit.")
    if used + value > MAX_TOTAL_BYTES:
        raise ValueError("The frozen repair source exceeds its total byte limit.")
    return used + value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("The frozen repair parameters contain duplicate keys.")
        result[key] = value
    return result


def _finite(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("The frozen repair parameters contain a nonfinite number.")
    return number


def _read_params(text, size):
    if not isinstance(text, str) or len(text.encode("utf-8")) != size:
        raise ValueError("The frozen repair parameters failed their byte check.")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_float=_finite,
            parse_constant=lambda _: _finite("nan"),
        )
        if not isinstance(value, dict):
            raise ValueError
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("The frozen repair parameters are invalid.") from exc
    return value


def _selection(params):
    if ("segment_id" in params) == ("segment_ids" in params):
        raise ValueError("The frozen repair must declare one passage selector.")
    ids = [params["segment_id"]] if "segment_id" in params else params["segment_ids"]
    if not isinstance(ids, list) or not 0 < len(ids) <= MAX_TARGETS:
        raise ValueError("The frozen repair selection is invalid.")
    ids = [_passage_id(identifier) for identifier in ids]
    if len(set(ids)) != len(ids):
        raise ValueError("The frozen repair selection repeats a passage.")
    return ids


def _operation(params):
    options = params.get("enhancement")
    if not isinstance(options, dict) or any(
        type(options.get(key)) is not bool
        for key in ("align", "diarize", "transcribe", "retranscribe")
    ):
        raise ValueError("The frozen repair operation is invalid.")
    for key in ("realign", "passage_timing", "retranscription", "transcription"):
        if key in params and type(params[key]) is not bool:
            raise ValueError("The frozen repair flags must be literal booleans.")
    if (
        params.get("transcription") is True
        or not options["align"]
        or options["diarize"]
        or options["transcribe"]
    ):
        raise ValueError("This job does not declare a supported passage repair.")
    if params.get("passage_timing") is True:
        if (
            params.get("realign") is not True
            or options["retranscribe"]
            or params.get("retranscription") is True
        ):
            raise ValueError("The frozen timing operation is contradictory.")
        return "timing", "selected"
    if "timing_units" in params:
        raise ValueError("Frozen timing groups require the passage-timing operation.")
    if params.get("retranscription") is True:
        if not options["retranscribe"] or params.get("realign") is True:
            raise ValueError("The frozen re-transcription operation is contradictory.")
        return "retranscription", "full"
    if options["retranscribe"]:
        raise ValueError("The frozen re-transcription marker is missing.")
    return "timing" if params.get("realign") is True else "alignment", "full"


def _passages(segments):
    positions = {}
    for index, passage in enumerate(segments):
        if not isinstance(passage, dict) or not {"id", "text", "start", "end"} <= passage.keys():
            raise ValueError("The preserved repair passages are invalid.")
        identifier = _passage_id(passage["id"])
        if identifier in positions or not isinstance(passage["text"], str):
            raise ValueError("The preserved repair passages are ambiguous.")
        for key in ("start", "end"):
            value = passage[key]
            if value is not None and (not _number(value) or value < 0):
                raise ValueError("The preserved repair bounds are invalid.")
        positions[identifier] = index
    return positions


def _groups(params, segments, ids, positions):
    units = params.get("timing_units")
    if not isinstance(units, list) or not 0 < len(units) <= MAX_TARGETS:
        raise ValueError("The frozen timing groups are invalid.")
    covered = []
    for unit in units:
        if not isinstance(unit, dict) or set(unit) != {"ids", "start", "end"}:
            raise ValueError("The frozen timing group shape is invalid.")
        members = unit["ids"]
        if not isinstance(members, list) or not members:
            raise ValueError("The frozen timing group has no passages.")
        members = [_passage_id(identifier) for identifier in members]
        if any(identifier not in positions for identifier in members):
            raise ValueError("A frozen timing passage is not in its source version.")
        first, last = positions[members[0]], positions[members[-1]]
        if members != [passage["id"] for passage in segments[first : last + 1]]:
            raise ValueError("The frozen timing group is not an ordered contiguous projection.")

        def unbounded(passage):
            return passage["start"] is None or passage["end"] is None

        if unbounded(segments[first]):
            if (
                any(not unbounded(passage) for passage in segments[first : last + 1])
                or (first > 0 and unbounded(segments[first - 1]))
                or (last + 1 < len(segments) and unbounded(segments[last + 1]))
            ):
                raise ValueError("The frozen unbounded group must retain all adjacent members.")
        elif len(members) != 1:
            raise ValueError("A bounded timing group must contain one passage.")
        start, end = unit["start"], unit["end"]
        if not _number(start) or not _number(end) or not 0 <= start < end or end - start > 60:
            raise ValueError("The frozen timing window shape is invalid.")
        # This validates membership, not the audio origin or neighbour-time correctness.
        covered.extend(members)
    if covered != ids:
        raise ValueError("The frozen timing groups do not exactly cover the selected projection.")


def inspect_repair_source(store, job_id):
    """Prove preserved source/projection identity without requiring today's active/live state.

    observed_params_sha256 hashes this mutable database observation, not an immutable request.
    The receipt grants no execution, provider, Protected-policy or audio/time-map approval.
    Older execution paths are unchanged; this new inspection refuses to manufacture missing
    exact-version evidence for historical jobs.
    """
    _identifier(job_id)
    with store.connection() as db:
        db.execute("BEGIN")
        job = db.execute(
            "SELECT recording_id,kind,length(CAST(params AS BLOB)) params_bytes "
            "FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        if job is None:
            raise KeyError(job_id)
        if job["kind"] != "enhance":
            raise ValueError("Choose a frozen passage-repair job.")
        _identifier(job["recording_id"])
        used = _size(job["params_bytes"], 0)
        text = db.execute("SELECT params FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
        params = _read_params(text, job["params_bytes"])
        ids = _selection(params)
        operation, scope = _operation(params)
        if operation == "retranscription" and len(ids) > 200:
            raise ValueError("The frozen re-transcription selection exceeds its passage limit.")
        source = params.get("source_snapshot")
        if not isinstance(source, dict) or set(source) != {
            "id",
            "segments",
            "speakers",
            "source_version_id",
            "source_payload_sha256",
        }:
            raise ValueError("This repair lacks an exact preserved source version.")
        result_id = _identifier(source["id"])
        version_id = _identifier(source["source_version_id"])
        full_hash = _digest(source["source_payload_sha256"])
        row = db.execute(
            "SELECT v.result_id,r.recording_id,v.payload_sha256,"
            "length(CAST(v.payload AS BLOB)) payload_bytes FROM result_versions v "
            "JOIN transcript_results r ON r.id=v.result_id WHERE v.id=?",
            (version_id,),
        ).fetchone()
        if (
            row is None
            or row["result_id"] != result_id
            or row["recording_id"] != job["recording_id"]
            or row["payload_sha256"] != full_hash
        ):
            raise ValueError("The preserved repair source ownership or hash disagrees.")
        _size(row["payload_bytes"], used)
        payload = db.execute(
            "SELECT payload FROM result_versions WHERE id=?", (version_id,)
        ).fetchone()[0]
        if not isinstance(payload, str) or len(payload.encode("utf-8")) != row["payload_bytes"]:
            raise ValueError("The preserved repair source failed its byte check.")
        try:
            full = decode_payload(payload, full_hash)
            positions = _passages(full["segments"])
            if any(identifier not in positions for identifier in ids):
                raise ValueError("The frozen selection does not belong to its source version.")
            ordered_ids = sorted(ids, key=positions.__getitem__)
            if scope == "selected" and ids != ordered_ids:
                raise ValueError("The frozen timing selection is not in its source order.")
            ids = ordered_ids
            selected = [full["segments"][positions[identifier]] for identifier in ids]
            expected = selected if scope == "selected" else full["segments"]
            if (
                encode_payload(source["segments"], source["speakers"])[0]
                != encode_payload(expected, full["speakers"])[0]
            ):
                raise ValueError("The frozen source is not the exact declared projection.")
            if scope == "selected":
                _groups(params, full["segments"], ids, positions)
            projection_hash = encode_payload(selected, full["speakers"])[1]
        except (TypeError, KeyError, RecursionError) as exc:
            raise ValueError("The frozen repair source projection is invalid.") from exc
        return {
            "format": FORMAT,
            "operation": operation,
            "job_id": job_id,
            "recording_id": job["recording_id"],
            "result_id": result_id,
            "source_version_id": version_id,
            "source_payload_sha256": full_hash,
            "projection_sha256": projection_hash,
            "observed_params_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "source_scope": scope,
            "segment_ids": ids,
        }
