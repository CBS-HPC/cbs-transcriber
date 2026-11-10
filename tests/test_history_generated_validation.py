"""Only canonical bytes generated from immutable stored JSON seed format facts."""

import hashlib
import json
from copy import deepcopy

import pytest
from test_store_edit_hotpath import seeded, state

from cbs_transcriber import history
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import edit_text_retaining_timing, split_passage


@pytest.fixture(autouse=True)
def isolated_memo():
    with history._VERIFIED_PAYLOADS_LOCK:
        history._VERIFIED_PAYLOADS.clear()
        getattr(history, "_SERIALIZED_PAYLOAD_BINDINGS", {}).clear()
    yield
    with history._VERIFIED_PAYLOADS_LOCK:
        history._VERIFIED_PAYLOADS.clear()
        getattr(history, "_SERIALIZED_PAYLOAD_BINDINGS", {}).clear()


def generated(segments, speakers):
    return history.encode_serialized_payload(segments, speakers)


def test_generated_canonical_bytes_need_no_cold_decode_but_every_read_hashes(monkeypatch):
    parts = [{"text": "Møller 🧪 e\u0301", "opaque": [1.0, -0.0, 5e-324]}]
    voices = {"S0": "Åse"}
    expected = history.encode_payload(parts, voices)
    payload, digest = generated(json.dumps(parts), json.dumps(voices))
    assert (payload, digest) == expected
    assert list(history._VERIFIED_PAYLOADS) == [digest]
    checks = []
    original_hash = history.text_hash

    def hashed(raw):
        checks.append(raw)
        return original_hash(raw)

    def refuse_decode(*args):
        raise AssertionError("Freshly generated canonical bytes were redecoded")

    monkeypatch.setattr(history, "text_hash", hashed)
    monkeypatch.setattr(history, "decode_payload", refuse_decode)
    history.verify_payload(payload, digest)
    history.verify_payload(payload, digest)
    with pytest.raises(ValueError, match="integrity check"):
        history.verify_payload(payload.replace("Åse", "Else"), digest)
    assert checks == [payload, payload, payload.replace("Åse", "Else")]


def test_generic_numeric_dictionary_keys_cannot_seed_noncanonical_bytes():
    # Legacy encode accepts numeric keys, whose numeric order differs after JSON decoding.
    payload, digest = history.encode_payload([{2: "two", 10: "ten"}], {})
    assert digest not in history._VERIFIED_PAYLOADS
    with pytest.raises(ValueError, match="integrity check"):
        history.verify_payload(payload, digest)
    assert digest not in history._VERIFIED_PAYLOADS
    normalized = generated(json.dumps([{2: "two", 10: "ten"}]), "{}")
    history.verify_payload(*normalized)
    assert history.decode_payload(*normalized)["segments"] == [{"2": "two", "10": "ten"}]


@pytest.mark.parametrize(
    "parts,voices,error",
    [
        ("[", "{}", ValueError),
        ("[]", "{", ValueError),
        ("{}", "{}", ValueError),
        ("[]", "[]", ValueError),
        ('[{"opaque": NaN}]', "{}", ValueError),
        ('[{"opaque": Infinity}]', "{}", ValueError),
        ('[{"text": "\\ud800"}]', "{}", UnicodeEncodeError),
        ("[]", '{"\\udfff": "name"}', UnicodeEncodeError),
        ("[]", '{"S0": "\\ud800"}', UnicodeEncodeError),
    ],
)
def test_failed_generated_encoding_does_not_seed_any_fact(parts, voices, error):
    with pytest.raises(error):
        generated(parts, voices)
    assert not history._VERIFIED_PAYLOADS
    assert not getattr(history, "_SERIALIZED_PAYLOAD_BINDINGS", {})


def test_generated_fact_is_digest_only_bounded_and_evicted_bytes_are_revalidated(monkeypatch):
    monkeypatch.setattr(history, "_VERIFIED_PAYLOADS_LIMIT", 2)
    first = generated('[{"text":"first"}]', "{}")
    second = generated('[{"text":"second"}]', "{}")
    third = generated('[{"text":"third"}]', "{}")
    assert list(history._VERIFIED_PAYLOADS.items()) == [(second[1], True), (third[1], True)]
    original, calls = history.decode_payload, []

    def decoded(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(history, "decode_payload", decoded)
    history.verify_payload(*first)
    assert calls == [first]
    assert list(history._VERIFIED_PAYLOADS) == [third[1], first[1]]


def test_generated_fact_keeps_exact_unicode_float_bytes_and_no_mutable_objects():
    raw = '[{"text":"æ 🧪 e\\u0301","nested":{"10":"ten","2":"two"},"v":[-0.0,1.0,5e-324]}]'
    voices = '{"S0":"e\\u0301"}'
    payload, digest = generated(raw, voices)
    assert (payload, digest) == history.encode_payload(json.loads(raw), json.loads(voices))
    assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == digest
    first = history.decode_payload(payload, digest)
    first["segments"][0]["nested"]["10"] = "poison"
    second = history.decode_payload(payload, digest)
    assert second["segments"][0]["nested"]["10"] == "ten"
    assert list(history._VERIFIED_PAYLOADS.items()) == [(digest, True)]
    noncanonical = json.dumps(second, ensure_ascii=False, indent=2)
    with pytest.raises(ValueError, match="integrity check"):
        history.verify_payload(noncanonical, history.text_hash(noncanonical))


@pytest.mark.parametrize("action", ["save", "split"])
def test_save_and_split_reuse_generated_facts_without_cold_decode_and_keep_snapshot(
    tmp_path, monkeypatch, action
):
    store, project, current = seeded(tmp_path)
    before = state(store, project)
    parts = deepcopy(current["segments"])
    if action == "save":
        parts[0] = edit_text_retaining_timing(parts[0], parts[0]["text"] + " checked")[0]
        edit_action, context = (
            "free_rewrite",
            {
                "segment_id": parts[0]["id"],
                "text_policy": "conservative-word-map-v1",
            },
        )
    else:
        cursor = parts[0]["text"].index(" ") + 1
        parts = split_passage(parts, parts[0]["id"], cursor)
        edit_action, context = "passage_split", {"segment_id": parts[0]["id"], "cursor": cursor}
    with history._VERIFIED_PAYLOADS_LOCK:
        history._VERIFIED_PAYLOADS.clear()
    original, calls = history.decode_payload, []

    def decoded(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(history, "decode_payload", decoded)
    saved = store.save_transcript(
        current["id"],
        parts,
        current["speakers"],
        current["revision"],
        result_id=current["active_transcript_id"],
        action=edit_action,
        context=context,
    )
    assert calls == []
    assert saved["segments"] == parts
    assert saved["revision"] == current["revision"] + 1
    assert saved["result_roles"]["working"]["version_id"] == saved["active_version"]["id"]
    versions = store.all("SELECT * FROM result_versions ORDER BY sequence")
    assert versions[:-1] == before["versions"]
    assert versions[-1]["predecessor_id"] == current["active_version"]["id"]
    assert versions[-1]["action"] == edit_action
    assert (versions[-1]["payload"], versions[-1]["payload_sha256"]) == history.encode_payload(
        parts, current["speakers"]
    )
    after = state(store, project)
    assert after["recordings"][0]["segments"] == json.dumps(
        parts, ensure_ascii=False, sort_keys=True
    )
    assert any(name == "project.json" for name in after["files"])
    snapshot = json.loads(after["files"]["project.json"])
    assert snapshot["recordings"][0]["segments"] == parts
    assert saved["snapshot_error"] is None


@pytest.mark.parametrize(
    "damage",
    [
        "head_hash",
        "head_payload",
        "head_noncanonical",
        "segments",
        "speakers",
        "both_segments",
        "both_speakers",
        "equal_malformed",
        "owner",
    ],
)
def test_seeded_format_fact_cannot_bypass_head_mirror_or_owner_checks(tmp_path, damage):
    store, project, current = seeded(tmp_path)
    head = store.one("SELECT * FROM result_versions WHERE id=?", (current["active_version"]["id"],))
    generated(json.dumps(current["segments"]), json.dumps(current["speakers"]))
    if damage == "head_noncanonical":
        payload = json.dumps(json.loads(head["payload"]), ensure_ascii=False, indent=2)
        store.execute(
            "UPDATE result_versions SET payload=?,payload_sha256=? WHERE id=?",
            (payload, history.text_hash(payload), head["id"]),
        )
    elif damage.startswith("head"):
        field, value = ("payload_sha256", "0" * 64) if damage == "head_hash" else ("payload", "{}")
        store.execute(f"UPDATE result_versions SET {field}=? WHERE id=?", (value, head["id"]))
    elif damage == "equal_malformed":
        store.execute("UPDATE recordings SET segments='[' WHERE id=?", (current["id"],))
        store.execute("UPDATE transcript_results SET segments='[' WHERE id=?", (head["result_id"],))
    elif damage == "owner":
        other = store.create_recording(project["id"], "other.wav")
        store.execute(
            "UPDATE transcript_results SET recording_id=? WHERE id=?",
            (other["id"], head["result_id"]),
        )
    else:
        field = damage.removeprefix("both_")
        value = deepcopy(current[field])
        if field == "segments":
            value[0]["text"] = "Unverified changed mirror"
        else:
            value["S0"] = "Unverified speaker"
        store.execute(
            f"UPDATE recordings SET {field}=? WHERE id=?", (json.dumps(value), current["id"])
        )
        if damage.startswith("both_"):
            store.execute(
                f"UPDATE transcript_results SET {field}=? WHERE id=?",
                (json.dumps(value), head["result_id"]),
            )
    before = state(store, project)
    with pytest.raises((ValueError, RuntimeError)):
        store.save_transcript(
            current["id"], current["segments"], current["speakers"], current["revision"]
        )
    assert state(store, project) == before


def test_same_format_fact_across_stores_does_not_grant_version_ownership(tmp_path):
    _, _, current = seeded(tmp_path / "first")
    second = Store(tmp_path / "second")
    project = second.create_project("Other library")
    recording = second.create_recording(project["id"], "other.wav")
    generated(json.dumps(current["segments"]), json.dumps(current["speakers"]))
    with pytest.raises(KeyError):
        second.result_version(
            recording["id"], current["active_transcript_id"], current["active_version"]["id"]
        )
