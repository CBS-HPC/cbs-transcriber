"""Pure envelope policy checks; fixtures are synthetic and the reference codec is committed."""

import hashlib
import json
import types
from pathlib import Path

import pytest

from cbs_transcriber import portable_payload as payload


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(segments, speakers=None):
    return json.dumps(
        {"format": payload.FORMAT, "segments": segments, "speakers": speakers or {}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def inspect(raw, **kwargs):
    return payload.inspect_result_payload(raw, expected_sha256=sha(raw), **kwargs)


def private_free(error):
    assert error.__context__ is None and error.__cause__ is None
    assert not hasattr(error, "doc") and not hasattr(error, "object")
    assert "PRIVATE_CANARY" not in str(error)


@pytest.fixture(scope="module")
def committed_codec():
    # Frozen encode/decode/text_hash meaning from b21bfd5 history.py (unchanged vs1a710ca).
    # Full committed LF source SHA256:
    # 0f19cefcd1c37e03189108b13991f9a6b258f69c03f7ef219f86c02050ff487e
    # Routine tests need neither a Git checkout nor the evolving working history module.
    format_name = "cbs-result-snapshot-v1"

    def encode_payload(segments, speakers):
        if not isinstance(segments, list) or not isinstance(speakers, dict):
            raise ValueError("A result snapshot requires passages and speaker labels.")
        text = json.dumps(
            {"format": format_name, "segments": segments, "speakers": speakers},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return text, hashlib.sha256(text.encode("utf-8")).hexdigest()

    def decode_payload(text, digest):
        try:
            if (
                not isinstance(text, str)
                or hashlib.sha256(text.encode("utf-8")).hexdigest() != digest
            ):
                raise ValueError
            decoded = json.loads(text)
            if not isinstance(decoded, dict) or decoded.get("format") != format_name:
                raise ValueError
            if encode_payload(decoded["segments"], decoded["speakers"])[0] != text:
                raise ValueError
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("The saved research version failed its integrity check.") from error
        return decoded

    return types.SimpleNamespace(
        FORMAT=format_name, encode_payload=encode_payload, decode_payload=decode_payload
    )


def test_v147_empty_payload_is_metadata_only():
    raw = b'{"format":"cbs-result-snapshot-v1","segments":[],"speakers":{}}'
    result = inspect(raw)
    assert result == {
        "level": "result_payload_envelope_only",
        "reference": "matched_supplied_payload",
        "policy": "cbs-portable-result-payload-v1",
        "format": payload.FORMAT,
        "sha256": sha(raw),
        "bytes": len(raw),
        "segment_entries": 0,
        "speaker_entries": 0,
        "lexical_tokens": 7,
        "max_depth": 2,
        "restore_ready": False,
        "not_checked": list(payload.NOT_CHECKED),
    }
    assert not {"segments", "speakers", "payload", "valid", "complete"} & result.keys()


@pytest.mark.parametrize(
    "segments,speakers",
    [
        ([], {}),
        ([None, True, False, 1, 1.0, -0.0, "text", [], {}], {}),
        ([{"opaque": {"deep": [None, {"unrecognised": "æøå 😀 e\u0301"}]}}], {"voice": None}),
        (
            [{"text": "PRIVATE_CANARY", "extensions": [True, 1.0]}],
            {"not_a_label": {"data": [1, False]}},
        ),
        ([{"nul": "\0", "quotes": '\\"[]{}'}], {"é": "e\u0301"}),
        ([10**127, -(10**126), 1.7976931348623157e308, 5e-324], {}),
    ],
)
def test_v147_opaque_inner_values_match_committed_writer(committed_codec, segments, speakers):
    text, digest = committed_codec.encode_payload(segments, speakers)
    raw = text.encode("utf-8")
    assert committed_codec.decode_payload(text, digest)["segments"] == segments
    result = payload.inspect_result_payload(raw, expected_sha256=digest)
    assert result["segment_entries"] == len(segments)
    assert result["speaker_entries"] == len(speakers)
    assert "PRIVATE_CANARY" not in json.dumps(result)


@pytest.mark.parametrize("value", [True, 1, 1.0, 0, 0.0, -0.0, "é", "e\u0301"])
def test_v147_numeric_and_unicode_observations_preserve_distinct_hashes(value, committed_codec):
    text, digest = committed_codec.encode_payload([value], {})
    assert inspect(text.encode())["sha256"] == digest


class BytesSubclass(bytes):
    pass


class TextSubclass(str):
    pass


@pytest.mark.parametrize(
    "raw", [None, "text", bytearray(b"{}"), memoryview(b"{}"), BytesSubclass(b"{}")]
)
def test_v147_raw_argument_must_be_native_bytes_before_guard(raw):
    with pytest.raises(ValueError):
        payload.inspect_result_payload(
            raw, expected_sha256="0" * 64, guard=lambda: pytest.fail("Unexpected guard")
        )


@pytest.mark.parametrize("reference", [None, True, "", "A" * 64, "x" * 64, TextSubclass("a" * 64)])
def test_v147_reference_must_be_native_lowercase_sha_before_guard(reference):
    with pytest.raises(ValueError):
        payload.inspect_result_payload(
            b"{}", expected_sha256=reference, guard=lambda: pytest.fail("Unexpected guard")
        )


def test_v147_wrong_hash_refuses_before_scan(monkeypatch):
    monkeypatch.setattr(payload, "_preflight", lambda *a: pytest.fail("Unexpected scan"))
    with pytest.raises(ValueError):
        payload.inspect_result_payload(b"PRIVATE_CANARY", expected_sha256="0" * 64)


def no_parse(monkeypatch):
    monkeypatch.setattr(
        payload.json, "loads", lambda *a, **k: pytest.fail("Unexpected JSON allocation")
    )


@pytest.mark.parametrize(
    "case", ["bytes", "tokens", "depth", "string", "escaped_string", "atom", "unknown_atom"]
)
def test_v147_over_budget_refuses_before_json_materialization(monkeypatch, case):
    if case == "bytes":
        raw = b" " * (8 * 1024**2 + 1)
    elif case == "tokens":
        raw = encoded([None] * (500001 - 7))
    elif case == "depth":
        raw = (
            b'{"format":"cbs-result-snapshot-v1","segments":'
            + b"[" * 24
            + b"null"
            + b"]" * 24
            + b',"speakers":{}}'
        )
    elif case == "string":
        raw = encoded(["a" * (2 * 1024**2 - 3) + "😀"])
    elif case == "escaped_string":
        raw = encoded(["\0" * ((2 * 1024**2) // 6) + "aaa"])
    elif case == "atom":
        raw = b'{"format":"cbs-result-snapshot-v1","segments":[' + b"1" * 129 + b'],"speakers":{}}'
    else:
        raw = b'{"format":"cbs-result-snapshot-v1","segments":[' + b"x" * 129 + b'],"speakers":{}}'
    no_parse(monkeypatch)
    with pytest.raises(ValueError, match="supported resource") as error:
        inspect(raw)
    private_free(error.value)


@pytest.mark.parametrize("case", ["tokens", "depth", "unicode_string", "escaped_string", "atom"])
def test_v147_exact_lexical_boundaries_accept(case):
    if case == "tokens":
        raw = encoded([None] * (500000 - 7))
    elif case == "depth":
        raw = (
            b'{"format":"cbs-result-snapshot-v1","segments":'
            + b"[" * 23
            + b"null"
            + b"]" * 23
            + b',"speakers":{}}'
        )
    elif case == "unicode_string":
        raw = encoded(["a" * (2 * 1024**2 - 4) + "😀"])
    elif case == "escaped_string":
        raw = encoded(["\0" * ((2 * 1024**2) // 6) + "aa"])
    else:
        raw = b'{"format":"cbs-result-snapshot-v1","segments":[' + b"1" * 128 + b'],"speakers":{}}'
    result = inspect(raw)
    if case == "tokens":
        assert result["lexical_tokens"] == 500000
    if case == "depth":
        assert result["max_depth"] == 24
    assert result["restore_ready"] is False


def test_v147_exact_byte_limit_with_individually_bounded_unicode_values():
    limit, span = 8 * 1024**2, 2 * 1024**2
    overhead = len(encoded([{key: "" for key in "abcd"}]))
    sizes = [span, span, span, span - overhead]
    raw = encoded([{key: "a" * (size - 4) + "😀" for key, size in zip("abcd", sizes, strict=True)}])
    assert len(raw) == limit
    assert inspect(raw)["bytes"] == limit


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{}",
        b"[]",
        b"{",
        b"}",
        b"[}",
        b'{"segments":}',
        b'{"format":"wrong","segments":[],"speakers":{}}',
        b'{"format":"cbs-result-snapshot-v1","segments":{},"speakers":{}}',
        b'{"format":"cbs-result-snapshot-v1","segments":[],"speakers":[]}',
        b'{"extra":null,"format":"cbs-result-snapshot-v1","segments":[],"speakers":{}}',
        b'{"format":"cbs-result-snapshot-v1","segments":[{"a":1,"a":1}],"speakers":{}}',
        b'{"format":"cbs-result-snapshot-v1","segments":[{"a":1,"\\u0061":2}],"speakers":{}}',
        b'{"format":"cbs-result-snapshot-v1","segments":[NaN],"speakers":{}}',
        b'{"format":"cbs-result-snapshot-v1","segments":[Infinity],"speakers":{}}',
        b'{"format":"cbs-result-snapshot-v1","segments":[-Infinity],"speakers":{}}',
        b'{"format":"cbs-result-snapshot-v1","segments":[1e309],"speakers":{}}',
        b'{"format":"cbs-result-snapshot-v1","segments":[1e15],"speakers":{}}',
        b'{"format":"cbs-result-snapshot-v1","segments":[-0],"speakers":{}}',
        b'{"format":"cbs-result-snapshot-v1","segments":["\\ud800"],"speakers":{}}',
        b'{"format":"cbs-result-snapshot-v1","segments":["\\ud83d\\ude00"],"speakers":{}}',
        b'{"format":"cbs-result-snapshot-v1","segments":["PRIVATE_CANARY\x00"],"speakers":{}}',
        b'\xef\xbb\xbf{"format":"cbs-result-snapshot-v1","segments":[],"speakers":{}}',
        b'{"format":"cbs-result-snapshot-v1","segments":["PRIVATE_CANARY\xff"],"speakers":{}}',
        b'{"format":"cbs-result-snapshot-v1","segments":[],"speakers":{}}\n',
        b'{ "format":"cbs-result-snapshot-v1","segments":[],"speakers":{}}',
    ],
)
def test_v147_invalid_or_noncanonical_bytes_match_committed_refusal(raw, committed_codec):
    with pytest.raises((ValueError, UnicodeError, KeyError, TypeError)):
        committed_codec.decode_payload(raw.decode("utf-8"), sha(raw))
    with pytest.raises(ValueError) as error:
        inspect(raw)
    private_free(error.value)


def test_v147_string_brackets_and_escapes_do_not_consume_structural_depth():
    raw = encoded(["[]{}" * 200 + '\\"' + "\n"])
    assert inspect(raw)["max_depth"] == 2


def test_v147_encoder_streams_and_does_not_materialize_expanding_suffix(monkeypatch):
    raw = b'{"format":"cbs-result-snapshot-v1","segments":[1e15],"speakers":{}}'

    class Encoder:
        def __init__(self, **kwargs):
            assert kwargs == dict(
                ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )

        def iterencode(self, value, *, _one_shot):
            assert _one_shot is False
            yield '{"format":"cbs-result-snapshot-v1","segments":['
            yield "1000000000000000.0"
            pytest.fail("Expanding suffix must not be consumed")

    monkeypatch.setattr(payload.json, "JSONEncoder", Encoder)
    monkeypatch.setattr(payload.json, "dumps", lambda *a, **k: pytest.fail("Unexpected whole dump"))
    with pytest.raises(ValueError):
        inspect(raw)


def test_v147_utf8_pieces_are_bounded(monkeypatch):
    raw = encoded(["a" * 100000 + "😀"])
    original, sizes = payload._piece, []

    def watched(part, position):
        result = original(part, position)
        sizes.append(len(result))
        return result

    monkeypatch.setattr(payload, "_piece", watched)
    inspect(raw)
    assert len(sizes) > 6 and max(sizes) <= 64 * 1024


class StopPayload(BaseException):
    pass


@pytest.mark.parametrize("stage", ["hash", "scan", "decoded", "stream"])
@pytest.mark.parametrize(
    "failure",
    [
        StopPayload("caller"),
        MemoryError("caller"),
        RecursionError("caller"),
        PermissionError("caller"),
        StopIteration("caller"),
    ],
)
def test_v147_guards_escape_unchanged_at_all_stages(monkeypatch, stage, failure):
    raw, current = encoded(["a" * 100]), ["before"]
    if stage == "hash":
        original = payload._hash

        def wrapped(*args):
            current[0] = "hash"
            return original(*args)

        monkeypatch.setattr(payload, "_hash", wrapped)
    elif stage == "scan":
        original = payload._scan

        def wrapped(*args):
            current[0] = "scan"
            yield from original(*args)

        monkeypatch.setattr(payload, "_scan", wrapped)
    elif stage == "decoded":
        original = payload._decode

        def wrapped(*args):
            result = original(*args)
            current[0] = "decoded"
            return result

        monkeypatch.setattr(payload, "_decode", wrapped)
    else:
        original = payload._encoder

        def wrapped(*args):
            for part in original(*args):
                current[0] = "stream"
                yield part

        monkeypatch.setattr(payload, "_encoder", wrapped)

    def guard():
        if current[0] == stage:
            raise failure

    with pytest.raises(type(failure)) as error:
        inspect(raw, guard=guard)
    assert error.value is failure


@pytest.mark.parametrize(
    "stage", ["hash", "scan_factory", "scan_next", "parse", "encoder_factory", "encoder_next"]
)
@pytest.mark.parametrize("kind", [MemoryError, RecursionError])
def test_v147_internal_memory_or_recursion_failure_is_unsupported_without_context(
    monkeypatch, stage, kind
):
    raw = encoded(["PRIVATE_CANARY"])

    def broken(*args, **kwargs):
        raise kind("PRIVATE_CANARY")

    if stage == "hash":
        monkeypatch.setattr(payload.hashlib, "sha256", broken)
    elif stage == "scan_factory":
        monkeypatch.setattr(payload, "_scan", broken)
    elif stage == "scan_next":

        def iterator(*args):
            raise kind("PRIVATE_CANARY")
            yield

        monkeypatch.setattr(payload, "_scan", iterator)
    elif stage == "parse":
        monkeypatch.setattr(payload.json, "loads", broken)
    elif stage == "encoder_factory":
        monkeypatch.setattr(payload.json, "JSONEncoder", broken)
    else:

        class Encoder:
            def __init__(self, **kwargs):
                pass

            def iterencode(self, *args, **kwargs):
                raise kind("PRIVATE_CANARY")
                yield

        monkeypatch.setattr(payload.json, "JSONEncoder", Encoder)
    # Keep reference hashing independent when the module's shared hashlib is faulted.
    reference = "0" * 64 if stage == "hash" else sha(raw)
    with pytest.raises(ValueError, match="supported resource") as error:
        payload.inspect_result_payload(raw, expected_sha256=reference)
    private_free(error.value)


def test_v147_decoder_error_object_is_not_retained_by_refusal(monkeypatch):
    raw = encoded(["PRIVATE_CANARY"])

    def broken(*args, **kwargs):
        raise json.JSONDecodeError("PRIVATE_CANARY", "PRIVATE_CANARY", 0)

    monkeypatch.setattr(payload.json, "loads", broken)
    with pytest.raises(ValueError) as error:
        inspect(raw)
    private_free(error.value)


def test_v147_source_imports_are_pure_standard_library():
    import ast

    source = Path(payload.__file__).read_bytes()
    tree = ast.parse(source)
    assert {node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)} == {
        "hashlib",
        "json",
        "math",
        "re",
    }
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
