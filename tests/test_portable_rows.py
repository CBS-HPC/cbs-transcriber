"""Independent byte vectors for the pure codec; no library, database or inference needed."""

import hashlib
import io
import json
import struct
from concurrent.futures import CancelledError
from decimal import Decimal
from importlib.resources import files

import pytest

from cbs_transcriber import portable_rows as portable

SCHEMA = "038150c5376a827bec4a8d9c3f61c6d19e95fda7cd8a401dd8b3a81c8a20ef42"
SETTINGS_HEADER = bytes.fromhex(
    "6362732d706f727461626c652d7461626c652d763100"
    "53" + SCHEMA + "54000000000000000873657474696e6773"
    "430000000000000002540000000000000002696454000000000000000576616c7565"
)
ID_ONE = bytes.fromhex("4900000000000000080000000000000001")
END_ONE = bytes.fromhex("450000000000000001")


@pytest.fixture(autouse=True)
def clear_catalog():
    portable._catalog.cache_clear()
    yield
    portable._catalog.cache_clear()


def expected_value(value_frame):
    raw = SETTINGS_HEADER + b"R" + ID_ONE + value_frame + END_ONE
    return hashlib.sha256(raw).hexdigest(), len(raw)


def observed(value):
    return portable.digest_table("settings", [(1, value)])


def test_v139_known_answer_uses_literal_domain_framing_and_null_vector():
    result = observed(None)
    assert result == {
        "policy": "cbs-portable-table-v1",
        "table": "settings",
        "schema_fingerprint": SCHEMA,
        "rows": 1,
        "sha256": "38259a66f45f553799b7ddb6ec1fa303f8491e21477c2ea5ba89b49597023abe",
        "encoded_bytes": 142,
    }
    assert not {"valid", "restore_ready", "backup", "source", "database"} & result.keys()


def test_v139_empty_stream_has_a_bound_zero_row_terminator():
    raw = SETTINGS_HEADER + bytes.fromhex("450000000000000000")
    result = portable.digest_table("settings", [])
    assert result["rows"] == 0
    assert result["sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["encoded_bytes"] == len(raw)


@pytest.mark.parametrize(
    "value,frame",
    [
        (None, b"N" + b"\0" * 8),
        (0, b"I" + b"\0" * 7 + b"\x08" + b"\0" * 8),
        (-1, b"I" + b"\0" * 7 + b"\x08" + b"\xff" * 8),
        (-(2**63), b"I" + b"\0" * 7 + b"\x08\x80" + b"\0" * 7),
        (2**63 - 1, b"I" + b"\0" * 7 + b"\x08\x7f" + b"\xff" * 7),
        ("", b"T" + b"\0" * 8),
        (b"", b"B" + b"\0" * 8),
        (b"\0\xff", b"B" + b"\0" * 7 + b"\x02\0\xff"),
        ("é", b"T" + b"\0" * 7 + b"\x02\xc3\xa9"),
        ("e\u0301", b"T" + b"\0" * 7 + b"\x03e\xcc\x81"),
        ("😀", b"T" + b"\0" * 7 + b"\x04\xf0\x9f\x98\x80"),
        ("\r\n\0", b"T" + b"\0" * 7 + b"\x03\r\n\0"),
    ],
)
def test_v139_typed_literal_values_preserve_exact_bytes(value, frame):
    digest, count = expected_value(frame)
    result = observed(value)
    assert result["sha256"] == digest and result["encoded_bytes"] == count


@pytest.mark.parametrize(
    "bits",
    [
        "0000000000000000",  # positive zero
        "8000000000000000",  # negative zero
        "4009000000000000",  # 3.125
        "7ff0000000000000",  # positive infinity
        "fff0000000000000",  # negative infinity
        "7ff8000000000123",  # observed quiet NaN payload
        "fff8000000000123",  # observed negative quiet NaN
    ],
)
def test_v139_observed_binary64_bits_are_encoded_without_json_numbers(bits):
    raw = bytes.fromhex(bits)
    value = struct.unpack(">d", raw)[0]
    digest, count = expected_value(b"R" + b"\0" * 7 + b"\x08" + raw)
    result = observed(value)
    assert result["sha256"] == digest and result["encoded_bytes"] == count


def test_v139_types_and_json_or_unicode_spellings_are_never_collapsed():
    values = [None, "", b"", 0, 0.0, -0.0, "é", "e\u0301", '{"a":1}', '{ "a": 1 }']
    assert len({observed(value)["sha256"] for value in values}) == len(values)


def test_v139_table_and_column_context_separate_identical_values():
    row = ("id", "one", "two", "three")
    assert (
        portable.digest_table("providers", [row])["sha256"]
        != portable.digest_table("result_hints", [("id", "one", "two")])["sha256"]
    )
    a = portable.digest_table("settings", [(1, "a"), (2, "bc")])
    b = portable.digest_table("settings", [(1, "ab"), (2, "c")])
    assert a["sha256"] != b["sha256"]


class IntSubclass(int):
    pass


class FloatSubclass(float):
    pass


class TextSubclass(str):
    pass


class BytesSubclass(bytes):
    pass


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        2**63,
        -(2**63) - 1,
        Decimal("1"),
        bytearray(b"x"),
        memoryview(b"x"),
        {},
        [],
        (),
        {1},
        object(),
        IntSubclass(1),
        FloatSubclass(1),
        TextSubclass("x"),
        BytesSubclass(b"x"),
        "PRIVATE_CANARY\ud800",
    ],
)
def test_v139_unsupported_native_values_refuse_without_exposing_content(value):
    with pytest.raises(ValueError) as error:
        observed(value)
    assert "PRIVATE_CANARY" not in str(error.value)


@pytest.mark.parametrize("row", [None, {}, "ab", iter([1, None]), (), (1,), (1, None, 3)])
def test_v139_exact_native_row_shape_is_required(row):
    with pytest.raises(ValueError):
        portable.digest_table("settings", [row])


@pytest.mark.parametrize("table", [None, True, "", "unknown", TextSubclass("settings")])
def test_v139_only_pinned_native_table_names_are_supported(table):
    with pytest.raises(ValueError):
        portable.digest_table(table, [])


@pytest.mark.parametrize("keys", [[None], [True], [1.0], ["1"], [1, 1], [2, 1]])
def test_v139_primary_keys_are_typed_nonnull_unique_and_ordered(keys):
    with pytest.raises(ValueError):
        portable.digest_table("settings", [(key, None) for key in keys])


def test_v139_text_keys_use_case_sensitive_utf8_binary_order():
    keys = ["\0", "A", "a", "e\u0301", "z", "é", "😀"]
    assert sorted(keys, key=lambda value: value.encode("utf-8")) == keys
    result = portable.digest_table("providers", [(key, "n", "v", "c") for key in keys])
    assert result["rows"] == len(keys)
    with pytest.raises(ValueError):
        portable.digest_table("providers", [(key, "n", "v", "c") for key in reversed(keys)])


def attempt(job, number):
    return job, number, "task", "failed", "{}", "{}", None, "created", "updated"


def test_v139_composite_keys_are_ordered_by_the_declared_key_tuple():
    rows = [attempt("a", -1), attempt("a", 2), attempt("b", 1)]
    assert portable.digest_table("runtime_batch_attempts", rows)["rows"] == 3


@pytest.mark.parametrize(
    "rows",
    [
        [attempt("a", 1), attempt("a", 1)],
        [attempt("a", 2), attempt("a", 1)],
        [attempt("b", 1), attempt("a", 2)],
        [attempt("a", None)],
        [attempt("a", True)],
        [attempt(None, 1)],
    ],
)
def test_v139_invalid_composite_keys_refuse(rows):
    with pytest.raises(ValueError):
        portable.digest_table("runtime_batch_attempts", rows)


@pytest.mark.parametrize("value", ["a" * 12, "😀" * 3, b"x" * 12])
def test_v139_value_cap_is_exact_utf8_or_blob_bytes(monkeypatch, value):
    monkeypatch.setitem(portable.LIMITS, "value_bytes", 12)
    assert observed(value)["rows"] == 1


@pytest.mark.parametrize("value", ["a" * 13, "😀" * 4, b"x" * 13])
def test_v139_value_cap_refuses_one_extra_byte_or_multibyte_overflow(monkeypatch, value):
    monkeypatch.setitem(portable.LIMITS, "value_bytes", 12)
    with pytest.raises(ValueError):
        observed(value)


def test_v139_row_cap_includes_tags_lengths_and_all_values(monkeypatch):
    # R + (I length8 payload8) + (T length8 payload12) = 39 bytes.
    monkeypatch.setitem(portable.LIMITS, "row_bytes", 39)
    assert observed("a" * 12)["rows"] == 1
    monkeypatch.setitem(portable.LIMITS, "row_bytes", 38)
    with pytest.raises(ValueError):
        observed("a" * 12)


def test_v139_total_cap_includes_header_and_final_count(monkeypatch):
    monkeypatch.setitem(portable.LIMITS, "total_bytes", 142)
    assert observed(None)["encoded_bytes"] == 142
    monkeypatch.setitem(portable.LIMITS, "total_bytes", 141)
    with pytest.raises(ValueError):
        observed(None)


def test_v139_rows_are_consumed_once_and_stop_at_first_excess_row(monkeypatch):
    monkeypatch.setitem(portable.LIMITS, "rows", 2)
    seen = []

    def rows():
        for key in range(5):
            seen.append(key)
            yield key, None

    assert portable.digest_table("settings", [(1, None), (2, None)])["rows"] == 2
    with pytest.raises(ValueError):
        portable.digest_table("settings", rows())
    assert seen == [0, 1, 2]


def test_v139_large_text_is_counted_and_hashed_in_guarded_bounded_chunks(monkeypatch):
    monkeypatch.setattr(portable, "CHUNK_BYTES", 8)
    original, encoded_sizes, guard_calls = portable._text, [], 0

    def watched(value):
        result = original(value)
        encoded_sizes.append(len(result))
        return result

    def guard():
        nonlocal guard_calls
        guard_calls += 1

    monkeypatch.setattr(portable, "_text", watched)
    result = portable.digest_table("settings", [(1, "😀" * 5)], guard=guard)
    assert result["rows"] == 1
    # Static metadata fits8; two passes over5emoji yield8,8,4 each, never20.
    assert max(encoded_sizes) <= 8
    assert encoded_sizes[-6:] == [8, 8, 4, 8, 8, 4]
    assert guard_calls >= 8


@pytest.mark.parametrize("value", ["😀" * 8, b"x" * 32])
def test_v139_guard_exceptions_inside_value_chunks_are_unchanged(monkeypatch, value):
    monkeypatch.setattr(portable, "CHUNK_BYTES", 8)
    failure, calls = CancelledError("caller cancellation"), 0

    def stop():
        nonlocal calls
        calls += 1
        if calls == 4:
            raise failure

    with pytest.raises(CancelledError) as error:
        portable.digest_table("settings", [(1, value)], guard=stop)
    assert error.value is failure


def test_v139_iterator_and_guard_oserrors_are_not_curated_as_codec_errors():
    failure = OSError("caller-owned iterator failure")

    def rows():
        yield 1, None
        raise failure

    with pytest.raises(OSError) as error:
        portable.digest_table("settings", rows())
    assert error.value is failure

    def guard():
        raise failure

    with pytest.raises(OSError) as error:
        portable.digest_table("settings", [], guard=guard)
    assert error.value is failure


@pytest.mark.parametrize("change", ["append", "shrink", "replace"])
def test_v139_each_supplied_list_row_is_snapshotted_before_later_guards(change):
    row = ["id", "name", "value", "created"]
    baseline = portable.digest_table("providers", [tuple(row)])
    calls = 0

    def mutate():
        nonlocal calls
        calls += 1
        if calls == 3:
            if change == "append":
                row.append("extra")
            elif change == "shrink":
                row.pop()
            else:
                row[2] = "changed"

    assert portable.digest_table("providers", [row], guard=mutate) == baseline


def test_v139_pinned_catalog_is_canonical_complete_and_provenance_separate(monkeypatch):
    monkeypatch.setenv("CBS_TRANSCRIBER_MODELS", "PRIVATE_CANARY")
    resource = files("cbs_transcriber").joinpath("catalogs/portable_schema_12.json")
    raw = resource.read_bytes()
    catalog = json.loads(raw)

    def canonical(value):
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")

    assert raw == canonical(catalog) and not raw.endswith(b"\n")
    assert hashlib.sha256(raw).hexdigest() == portable.CATALOG_SHA256
    assert len(catalog["structure"]["tables"]) == 19
    assert len(catalog["structure"]["indexes"]) == 36
    assert (
        sum(
            item["type"] == "index" and item["sql"] is not None
            for item in catalog["structure"]["objects"]
        )
        == 7
    )
    assert catalog["structure"]["schema_version"] == 12
    assert catalog["structure"]["encoding"] == "UTF-8"
    assert catalog["provenance"]["commit"].startswith("e96303d")
    assert catalog["provenance"]["sources"] == [
        {
            "path": "src/cbs_transcriber/runtime_store.py",
            "sha256_lf": "58ba4377a1936ee0480fd17421a34cbab00586ab472243c3d1754fa40fb57a63",
        },
        {
            "path": "src/cbs_transcriber/store.py",
            "sha256_lf": "9801e0e122b9af3963ca874ecb7d707771932b6d82be0dd52702b29491c7f498",
        },
    ]
    before = hashlib.sha256(b"cbs-portable-schema-v1\0" + canonical(catalog["structure"]))
    catalog["provenance"]["sqlite_version"] = "different-observer"
    assert before.hexdigest() == SCHEMA
    assert (
        before.hexdigest()
        == hashlib.sha256(b"cbs-portable-schema-v1\0" + canonical(catalog["structure"])).hexdigest()
    )
    catalog["structure"]["tables"]["settings"]["columns"][1]["name"] = "changed"
    assert (
        before.hexdigest()
        != hashlib.sha256(b"cbs-portable-schema-v1\0" + canonical(catalog["structure"])).hexdigest()
    )


@pytest.mark.parametrize("damage", ["changed", "oversized"])
def test_v139_resource_changes_or_oversize_refuse_before_json_parse(monkeypatch, damage):
    raw = b"changed" if damage == "changed" else b"x" * (portable.LIMITS["catalog_bytes"] + 1)
    streams = []

    class Resource:
        def joinpath(self, name):
            assert name == "catalogs/portable_schema_12.json"
            return self

        def open(self, mode):
            assert mode == "rb"
            stream = io.BytesIO(raw)
            streams.append(stream)
            return stream

    monkeypatch.setattr(portable, "files", lambda name: Resource())
    monkeypatch.setattr(portable.json, "loads", lambda value: pytest.fail("Unexpected parser"))
    with pytest.raises(ValueError):
        observed(None)
    assert streams and all(stream.closed for stream in streams)


def test_v139_supplied_private_text_remains_only_in_the_callers_rows():
    result = observed("PRIVATE_CANARY interview body")
    assert "PRIVATE_CANARY" not in json.dumps(result)
