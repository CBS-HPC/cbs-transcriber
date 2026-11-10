"""Pure typed row fingerprints for one pinned catalog; no database or package inspection.

Rows are caller-owned observations. This codec neither acquires nor validates their origin,
and cannot bound work performed by a caller's iterator. No stored SQL is executed here.
"""

import hashlib
import json
import struct
from functools import cache
from importlib.resources import files

POLICY = "cbs-portable-table-v1"
DOMAIN = b"cbs-portable-table-v1\0"
SCHEMA_DOMAIN = b"cbs-portable-schema-v1\0"
SCHEMA_FINGERPRINT = "038150c5376a827bec4a8d9c3f61c6d19e95fda7cd8a401dd8b3a81c8a20ef42"
CATALOG_SHA256 = "c971fcad9df182f484e6978f85c9b8fc290994219afd01e04391b036c1a03330"
LIMITS = {
    "catalog_bytes": 128 * 1024,
    "value_bytes": 32 * 1024**2,
    "row_bytes": 32 * 1024**2,
    "rows": 1_000_000,
    "total_bytes": 8 * 1024**3,
}
CHUNK_BYTES = 64 * 1024


def _invalid(part):
    raise ValueError(f"The portable row {part} is invalid or exceeds the codec limit.")


def _guard(guard):
    if guard is not None:
        guard()


@cache
def _catalog():
    resource = files("cbs_transcriber").joinpath("catalogs/portable_schema_12.json")
    with resource.open("rb") as stream:
        raw = stream.read(LIMITS["catalog_bytes"] + 1)
    if len(raw) > LIMITS["catalog_bytes"] or hashlib.sha256(raw).hexdigest() != CATALOG_SHA256:
        _invalid("catalog resource")
    catalog = json.loads(raw)
    structure = catalog["structure"]
    canonical = json.dumps(
        structure, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if hashlib.sha256(SCHEMA_DOMAIN + canonical).hexdigest() != SCHEMA_FINGERPRINT:
        _invalid("catalog structure")
    return structure["tables"]


def _length(value):
    return struct.pack(">Q", value)


def _text(value):
    try:
        return value.encode("utf-8")
    except UnicodeError:
        _invalid("Unicode value")


def _text_chunks(value):
    # At most four UTF-8 bytes per scalar; never encode a whole large string at once.
    characters = max(1, CHUNK_BYTES // 4)
    for position in range(0, len(value), characters):
        yield value[position : position + characters]


def _parts(value, guard):
    kind = type(value)
    if kind is str:
        if len(value) > LIMITS["value_bytes"]:
            _invalid("value byte count")
        count = 0
        for chunk in _text_chunks(value):
            _guard(guard)
            count += len(_text(chunk))
            if count > LIMITS["value_bytes"]:
                _invalid("value byte count")
        yield b"T" + _length(count)
        for chunk in _text_chunks(value):
            _guard(guard)
            yield _text(chunk)
        return
    if kind is bytes:
        if len(value) > LIMITS["value_bytes"]:
            _invalid("value byte count")
        yield b"B" + _length(len(value))
        for position in range(0, len(value), CHUNK_BYTES):
            _guard(guard)
            yield memoryview(value)[position : position + CHUNK_BYTES]
        return
    if value is None:
        tag, raw = b"N", b""
    elif kind is int and -(2**63) <= value < 2**63:
        tag, raw = b"I", struct.pack(">q", value)
    elif kind is float:
        # These are observed Python binary64 bits, including infinities and NaNs.
        tag, raw = b"R", struct.pack(">d", value)
    else:
        _invalid("native value type")
    if len(raw) > LIMITS["value_bytes"]:
        _invalid("value byte count")
    yield tag + _length(len(raw))
    yield raw


def digest_table(table, rows, *, guard=None):
    """Fingerprint one ordered stream of exact native tuple/list rows (ADR 0083).

    Key validation describes only these supplied rows, never an actual database or its schema.
    Caller guards and iterator exceptions propagate unchanged. No successful partial digest
    is returned after failure. Resource limits are codec limits, not library capacity claims.
    """
    if type(table) is not str or (guard is not None and not callable(guard)):
        _invalid("table or guard")
    _guard(guard)
    catalog = _catalog()
    if table not in catalog:
        _invalid("table")
    columns = catalog[table]["columns"]
    keys = sorted(
        (column for column in columns if column["pk_position"]),
        key=lambda column: column["pk_position"],
    )
    digest, encoded, row_bytes = hashlib.sha256(), 0, 0

    def emit(data, *, row=False):
        nonlocal encoded, row_bytes
        count = len(data)
        if encoded + count > LIMITS["total_bytes"]:
            _invalid("total byte count")
        if row and row_bytes + count > LIMITS["row_bytes"]:
            _invalid("row byte count")
        digest.update(data)
        encoded += count
        if row:
            row_bytes += count

    emit(DOMAIN)
    emit(b"S" + bytes.fromhex(SCHEMA_FINGERPRINT))
    raw = _text(table)
    emit(b"T" + _length(len(raw)) + raw)
    emit(b"C" + _length(len(columns)))
    for column in columns:
        raw = _text(column["name"])
        emit(b"T" + _length(len(raw)) + raw)
    count, previous = 0, None
    for row in rows:
        _guard(guard)
        if count >= LIMITS["rows"]:
            _invalid("row count")
        if type(row) not in (tuple, list) or len(row) != len(columns):
            _invalid("row shape")
        row = tuple(row)
        key = tuple(row[column["position"]] for column in keys)
        if any(
            type(value) is not (int if column["declared_type"] == "INTEGER" else str)
            for column, value in zip(keys, key, strict=True)
        ):
            _invalid("key type")
        # Valid Unicode scalar order is also lexicographic UTF-8 BINARY order.
        if previous is not None and key <= previous:
            _invalid("key order")
        previous = key
        row_bytes = 0
        emit(b"R", row=True)
        for value in row:
            for part in _parts(value, guard):
                emit(part, row=True)
        count += 1
    _guard(guard)
    emit(b"E" + _length(count))
    return {
        "policy": POLICY,
        "table": table,
        "schema_fingerprint": SCHEMA_FINGERPRINT,
        "rows": count,
        "sha256": digest.hexdigest(),
        "encoded_bytes": encoded,
    }
