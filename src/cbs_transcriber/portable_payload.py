"""Pure supplied snapshot-envelope observations, not historical/domain validation (ADR 0091).

Budgets precede JSON graph creation. They define a supported subset, not a heap/RSS limit or
evidence that larger retained data is corrupt. No database, filesystem or runtime is opened.
"""

import hashlib
import json
import math
import re

POLICY = "cbs-portable-result-payload-v1"
FORMAT = "cbs-result-snapshot-v1"
MAX_BYTES = 8 * 1024**2
MAX_TOKENS = 500_000
MAX_DEPTH = 24
MAX_STRING_BYTES = 2 * 1024**2
MAX_ATOM_BYTES = 128
CHECK_BYTES = 64 * 1024
UTF8_CHARS = 16 * 1024
NOT_CHECKED = (
    "database_and_package_binding",
    "passage_word_and_speaker_semantics",
    "history_sequences_heads_and_mirrors",
    "source_and_owner_lineage",
    "artifact_and_capture_completeness",
    "reference_custody_and_authentication",
    "credential_exclusion_and_quarantine",
    "restore_and_activation",
    "private_data_security_baseline",
    "runtime_memory_and_security_acceptance",
)


def _invalid():
    raise ValueError("The result payload is invalid or noncanonical.")


def _resource():
    raise ValueError("The result payload exceeds the supported resource budget.")


def _guard(guard):
    if guard is not None:
        guard()


def _hash(raw, guard):
    failed = False
    try:
        digest, view = hashlib.sha256(), memoryview(raw)
    except (MemoryError, RecursionError):
        failed = True
    if failed:
        _resource()
    for position in range(0, len(raw), CHECK_BYTES):
        _guard(guard)
        try:
            digest.update(view[position : position + CHECK_BYTES])
        except (MemoryError, RecursionError):
            failed = True
        if failed:
            _resource()
    try:
        result = digest.hexdigest()
    except (MemoryError, RecursionError):
        failed = True
    if failed:
        _resource()
    return result


def _tokens(count):
    count += 1
    if count > MAX_TOKENS:
        _resource()
    return count


def _scan(raw):
    """Yield bounded-work checkpoints, then return lexical counts; no caller code runs here."""
    index, count, maximum, checkpoint = 0, 0, 0, 0
    stack = []
    while index < len(raw):
        if index >= checkpoint:
            yield
            checkpoint = index + CHECK_BYTES
        byte = raw[index]
        if byte in b" \t\r\n,:":
            index += 1
        elif byte in b"{[":
            count = _tokens(count)
            stack.append(byte)
            if len(stack) > MAX_DEPTH:
                _resource()
            maximum = max(maximum, len(stack))
            index += 1
        elif byte in b"}]":
            if not stack or stack[-1] != (123 if byte == 125 else 91):
                _invalid()
            stack.pop()
            index += 1
        elif byte == 34:
            count = _tokens(count)
            start, index, escaped = index + 1, index + 1, False
            while index < len(raw):
                if index >= checkpoint:
                    yield
                    checkpoint = index + CHECK_BYTES
                if index - start > MAX_STRING_BYTES:
                    _resource()
                byte = raw[index]
                if escaped:
                    escaped = False
                elif byte == 34:
                    break
                elif byte == 92:
                    escaped = True
                elif byte < 32:
                    _invalid()
                index += 1
            if index == len(raw):
                _invalid()
            if index - start > MAX_STRING_BYTES:
                _resource()
            index += 1
        else:
            count, start = _tokens(count), index
            while index < len(raw) and raw[index] not in b' \t\r\n,:{}[]"':
                index += 1
                if index - start > MAX_ATOM_BYTES:
                    _resource()
                if index >= checkpoint:
                    yield
                    checkpoint = index + CHECK_BYTES
    if stack:
        _invalid()
    return count, maximum


def _preflight(raw, guard):
    failed = False
    try:
        scanner = _scan(raw)
    except (MemoryError, RecursionError):
        failed = True
    if failed:
        _resource()
    while True:
        done, failed, result = False, False, None
        try:
            next(scanner)
        except StopIteration as stop:
            done, result = True, stop.value
        except (MemoryError, RecursionError):
            failed = True
        if failed:
            _resource()
        if done:
            return result
        _guard(guard)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite JSON number.")
    return result


def _constant(value):
    raise ValueError("Nonfinite JSON constant.")


def _decode(raw):
    failure = None
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_float=_float,
            parse_constant=_constant,
        )
        if (
            type(value) is not dict
            or len(value) != 3
            or set(value) != {"format", "segments", "speakers"}
            or type(value["format"]) is not str
            or value["format"] != FORMAT
            or type(value["segments"]) is not list
            or type(value["speakers"]) is not dict
        ):
            failure = "invalid"
    except (MemoryError, RecursionError):
        failure = "resource"
    except (ValueError, TypeError, OverflowError):
        failure = "invalid"
    if failure == "resource":
        _resource()
    if failure:
        _invalid()
    return value


def _encoder(value):
    failure = None
    try:
        result = json.JSONEncoder(
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).iterencode(value, _one_shot=False)
    except (MemoryError, RecursionError):
        failure = "resource"
    except (ValueError, TypeError, OverflowError):
        failure = "invalid"
    if failure == "resource":
        _resource()
    if failure:
        _invalid()
    return result


def _piece(part, position):
    failure = None
    try:
        result = part[position : position + UTF8_CHARS].encode("utf-8")
    except (MemoryError, RecursionError):
        failure = "resource"
    except (UnicodeError, TypeError):
        failure = "invalid"
    if failure == "resource":
        _resource()
    if failure:
        _invalid()
    return result


def _compare(raw, value, guard):
    iterator, position = _encoder(value), 0
    while True:
        _guard(guard)
        done, failure, part = False, None, None
        try:
            part = next(iterator)
        except StopIteration:
            done = True
        except (MemoryError, RecursionError):
            failure = "resource"
        except (ValueError, TypeError, OverflowError):
            failure = "invalid"
        if failure == "resource":
            _resource()
        if failure:
            _invalid()
        if done:
            break
        if type(part) is not str:
            _invalid()
        for offset in range(0, len(part), UTF8_CHARS):
            _guard(guard)
            data = _piece(part, offset)
            if not raw.startswith(data, position):
                _invalid()
            position += len(data)
        part = None
    if position != len(raw):
        _invalid()


def inspect_result_payload(raw, *, expected_sha256, guard=None):
    """Observe a supplied canonical envelope; counts never assert valid passages or voices."""
    if (
        type(raw) is not bytes
        or type(expected_sha256) is not str
        or len(expected_sha256) != 64
        or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None
        or (guard is not None and not callable(guard))
    ):
        raise ValueError("The supplied result payload reference is invalid.")
    if len(raw) > MAX_BYTES:
        _resource()
    _guard(guard)
    digest = _hash(raw, guard)
    if digest != expected_sha256:
        raise ValueError("The supplied result payload hash does not match.")
    token_count, depth = _preflight(raw, guard)
    _guard(guard)
    value = _decode(raw)
    _guard(guard)
    _compare(raw, value, guard)
    _guard(guard)
    return {
        "level": "result_payload_envelope_only",
        "reference": "matched_supplied_payload",
        "policy": POLICY,
        "format": FORMAT,
        "sha256": digest,
        "bytes": len(raw),
        "segment_entries": len(value["segments"]),
        "speaker_entries": len(value["speakers"]),
        "lexical_tokens": token_count,
        "max_depth": depth,
        "restore_ready": False,
        "not_checked": list(NOT_CHECKED),
    }
