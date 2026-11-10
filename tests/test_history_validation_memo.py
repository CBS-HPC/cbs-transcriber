"""A warm validation memo must never accept changed or noncanonical stored bytes."""

import json

import pytest

from cbs_transcriber import history, result_roles
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture(autouse=True)
def clear_validation_memo():
    with history._VERIFIED_PAYLOADS_LOCK:
        history._VERIFIED_PAYLOADS.clear()
    yield
    with history._VERIFIED_PAYLOADS_LOCK:
        history._VERIFIED_PAYLOADS.clear()


def test_unchanged_bytes_skip_redecoding_but_changed_bytes_are_still_checked(monkeypatch):
    payload, digest = history.encode_payload([{"text": "Ældre café"}], {"a": "Speaker"})
    history.verify_payload(payload, digest)
    original_decode = history.decode_payload
    calls = []

    def decode(*args):
        calls.append(args)
        return original_decode(*args)

    monkeypatch.setattr(history, "decode_payload", decode)
    history.verify_payload(payload, digest)
    assert calls == []
    with pytest.raises(ValueError, match="integrity check"):
        history.verify_payload(payload.replace("café", "name"), digest)
    noncanonical = json.dumps(json.loads(payload), ensure_ascii=False)
    with pytest.raises(ValueError, match="integrity check"):
        history.verify_payload(noncanonical, history.text_hash(noncanonical))
    assert len(calls) == 1


@pytest.mark.parametrize("payload,digest", [(None, "a"), ("\ud800", "a"), ("{}", [])])
def test_invalid_payloads_keep_the_existing_integrity_error(payload, digest):
    with pytest.raises(ValueError, match="integrity check"):
        history.verify_payload(payload, digest)


def test_evicted_entry_is_fully_validated_again(monkeypatch):
    monkeypatch.setattr(history, "_VERIFIED_PAYLOADS_LIMIT", 1)
    first = history.encode_payload([{"text": "first"}], {})
    second = history.encode_payload([{"text": "second"}], {})
    history.verify_payload(*first)
    history.verify_payload(*second)
    assert list(history._VERIFIED_PAYLOADS) == [second[1]]
    original_decode = history.decode_payload
    calls = []

    def decode(*args):
        calls.append(args)
        return original_decode(*args)

    monkeypatch.setattr(history, "decode_payload", decode)
    history.verify_payload(*first)
    assert calls == [first]


def test_warm_payload_does_not_memoize_version_metadata_or_ownership(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Synthetic validation")
    recording = store.create_recording(project["id"], "synthetic.wav")
    other = store.create_recording(project["id"], "other.wav")
    result = store.add_result(
        recording["id"], normalize_segments([{"text": "Exact source"}]), {}, "Test", {}
    )
    saved = store.result_versions(recording["id"], result["id"])[0]
    with store.connection() as db:
        result_roles.version(db, recording["id"], saved["id"])
        with pytest.raises(ValueError, match="belong"):
            result_roles.version(db, other["id"], saved["id"])
    store.execute("UPDATE result_versions SET observed_at='invalid' WHERE id=?", (saved["id"],))
    with store.connection() as db, pytest.raises(ValueError, match="metadata"):
        result_roles.version(db, recording["id"], saved["id"])
