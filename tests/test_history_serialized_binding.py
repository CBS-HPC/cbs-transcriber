"""Warm serialization facts never replace fresh bytes, owner, revision or head checks."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from test_store_edit_hotpath import save, seeded, state

from cbs_transcriber import history
from cbs_transcriber.transcripts import edit_text_retaining_timing


@pytest.fixture(autouse=True)
def isolated_binding_memo():
    with history._VERIFIED_PAYLOADS_LOCK:
        history._VERIFIED_PAYLOADS.clear()
        bindings = getattr(history, "_SERIALIZED_PAYLOAD_BINDINGS", {})
        bindings.clear()
    yield
    with history._VERIFIED_PAYLOADS_LOCK:
        history._VERIFIED_PAYLOADS.clear()
        bindings = getattr(history, "_SERIALIZED_PAYLOAD_BINDINGS", {})
        bindings.clear()


def test_warm_store_save_encodes_only_authoritative_successor(tmp_path, monkeypatch):
    store, _, current = seeded(tmp_path)
    current["segments"][0] = edit_text_retaining_timing(
        current["segments"][0], current["segments"][0]["text"] + " checked"
    )[0]
    original, calls = history.encode_payload, []

    def encoded(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(history, "encode_payload", encoded)
    after = save(store, current)
    assert len(calls) == 1
    assert after["segments"] == current["segments"]
    assert after["result_roles"]["working"]["version_id"] == after["active_version"]["id"]


def test_every_binding_hit_hashes_both_raw_strings_and_actual_head(monkeypatch):
    segments, speakers = '[{"text":"æ e\\u0301 🧪","v":-0.0}]', '{"S0":"Åse"}'
    payload, digest = history.encode_serialized_payload(segments, speakers)
    original, calls = history.text_hash, []

    def hashed(raw):
        calls.append(raw)
        return original(raw)

    def refuse_encode(*args):
        raise AssertionError("Verified byte binding was unnecessarily re-encoded")

    monkeypatch.setattr(history, "text_hash", hashed)
    monkeypatch.setattr(history, "encode_payload", refuse_encode)
    assert history.serialized_payload_matches(segments, speakers, payload, digest)
    assert sorted(calls) == sorted([segments, speakers, payload])
    calls.clear()
    assert history.serialized_payload_matches(segments, speakers, payload, digest)
    assert sorted(calls) == sorted([segments, speakers, payload])


@pytest.mark.parametrize("field", ["segments", "speakers", "payload", "hash"])
def test_changed_actual_bytes_or_stored_hash_do_not_match_a_warm_binding(field):
    raw_segments, raw_speakers = '[{"text":"æ e\\u0301","v":-0.0}]', '{"S0":"Åse"}'
    payload, digest = history.encode_serialized_payload(raw_segments, raw_speakers)
    if field == "segments":
        raw_segments = raw_segments.replace("-0.0", "0.0")
    elif field == "speakers":
        raw_speakers = raw_speakers.replace("Åse", "Else")
    elif field == "payload":
        payload = payload.replace("e\u0301", "é")
    else:
        digest = "0" * 64
    assert not history.serialized_payload_matches(raw_segments, raw_speakers, payload, digest)


def test_self_consistent_noncanonical_head_still_cannot_match():
    segments, speakers = '[{"text":"one"}]', "{}"
    payload, _ = history.encode_serialized_payload(segments, speakers)
    noncanonical = json.dumps(json.loads(payload), ensure_ascii=False, indent=2)
    assert not history.serialized_payload_matches(
        segments, speakers, noncanonical, history.text_hash(noncanonical)
    )


def test_legacy_different_raw_format_uses_exact_canonical_fallback(monkeypatch):
    segments, speakers = '[{"text":"æ"}]', '{"S0":"Åse"}'
    payload, digest = history.encode_serialized_payload(segments, speakers)
    legacy_segments = json.dumps(json.loads(segments), ensure_ascii=True, indent=3)
    legacy_speakers = json.dumps(json.loads(speakers), ensure_ascii=True, indent=3)
    original, calls = history.encode_payload, []

    def encoded(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(history, "encode_payload", encoded)
    assert history.serialized_payload_matches(legacy_segments, legacy_speakers, payload, digest)
    assert len(calls) == 1
    assert history.serialized_payload_matches(legacy_segments, legacy_speakers, payload, digest)
    assert len(calls) == 1


def test_binding_facts_are_bounded_digests_and_eviction_only_costs_reencoding(monkeypatch):
    monkeypatch.setattr(history, "_SERIALIZED_PAYLOAD_BINDINGS_LIMIT", 2)
    first_raw = '[{"text":"first"}]'
    first = history.encode_serialized_payload(first_raw, "{}")
    history.encode_serialized_payload('[{"text":"second"}]', "{}")
    history.encode_serialized_payload('[{"text":"third"}]', "{}")
    assert len(history._SERIALIZED_PAYLOAD_BINDINGS) == 2
    assert all(
        len(key) == 3 and all(len(value) == 64 for value in key) and fact is True
        for key, fact in history._SERIALIZED_PAYLOAD_BINDINGS.items()
    )
    original, calls = history.encode_payload, []

    def encoded(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(history, "encode_payload", encoded)
    assert history.serialized_payload_matches(first_raw, "{}", *first)
    assert len(calls) == 1
    assert len(history._SERIALIZED_PAYLOAD_BINDINGS) == 2


def test_concurrent_binding_warmup_keeps_exact_outcomes_and_fresh_decoded_objects():
    raw = '[{"text":"æ 🧪","nested":{"value":1.0}}]'
    expected = history.encode_payload(json.loads(raw), {})

    def check(_):
        generated = history.encode_serialized_payload(raw, "{}")
        assert history.serialized_payload_matches(raw, "{}", *expected)
        decoded = history.decode_payload(*generated)
        decoded["segments"][0]["nested"]["value"] = "caller mutation"
        return generated

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(check, range(12))) == [expected] * 12
    assert history.decode_payload(*expected)["segments"][0]["nested"]["value"] == 1.0
    assert len(history._SERIALIZED_PAYLOAD_BINDINGS) == 1


def test_warm_binding_cannot_bypass_current_revision_or_working_choice(tmp_path):
    store, project, current = seeded(tmp_path)
    after = save(store, deepcopy(current))
    before = state(store, project)
    with pytest.raises(ValueError, match="another window"):
        save(store, current)
    assert state(store, project) == before
    assert store.recording(current["id"])["active_version"] == after["active_version"]


def test_equal_payload_from_foreign_result_does_not_bypass_current_owned_result(tmp_path):
    store, project, current = seeded(tmp_path)
    other = store.create_recording(project["id"], "other.wav")
    result = store.add_result(other["id"], current["segments"], current["speakers"], "Other", {})
    store.execute(
        "UPDATE recordings SET active_transcript_id=? WHERE id=?", (result["id"], current["id"])
    )
    before = state(store, project)
    with pytest.raises(RuntimeError, match="missing from this recording"):
        save(store, current)
    assert state(store, project) == before


@pytest.mark.parametrize("payload", [None, 3, b"{}", "\ud800"])
def test_invalid_head_preserves_canonical_mismatch_refusal(payload):
    assert not history.serialized_payload_matches("[]", "{}", payload, "0" * 64)


def test_unhashable_raw_utf8_preserves_the_original_json_parse_error():
    payload, digest = history.encode_payload([], {})
    with pytest.raises(json.JSONDecodeError):
        history.serialized_payload_matches("[\ud800]", "{}", payload, digest)
    assert not history._SERIALIZED_PAYLOAD_BINDINGS


def test_discarded_duplicate_surrogate_can_encode_but_never_seed_a_raw_binding():
    segments = '[{"text":"\ud800","text":"valid"}]'
    expected = history.encode_payload(json.loads(segments), {})
    assert history.encode_serialized_payload(segments, "{}") == expected
    assert history.serialized_payload_matches(segments, "{}", *expected)
    assert not history._SERIALIZED_PAYLOAD_BINDINGS
    assert list(history._VERIFIED_PAYLOADS.items()) == [(expected[1], True)]
