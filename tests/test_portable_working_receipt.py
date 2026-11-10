"""Schema-18 capture and receipt plumbing with contained semantic validation mocked."""

import hashlib
import sqlite3
from contextlib import closing

import pytest
from test_portable_routes import capture, recording_with_original
from test_portable_routes import served as served

from cbs_transcriber import portable_semantics
from cbs_transcriber.transcripts import normalize_segments


def test_source_resource_normalizes_crlf_before_enforcing_byte_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(portable_semantics, "files", lambda _: tmp_path)
    resource = tmp_path / "source.py"
    canonical = b"x\n" * 100
    resource.write_bytes(canonical.replace(b"\n", b"\r\n"))
    digest = hashlib.sha256(canonical).hexdigest()
    assert (
        portable_semantics._resource("source.py", digest, len(canonical), source=True) == canonical
    )
    with pytest.raises(ValueError):
        portable_semantics._resource("source.py", digest, len(canonical) - 1, source=True)
    resource.write_bytes(b"y\n" * 100)
    with pytest.raises(ValueError):
        portable_semantics._resource("source.py", digest, len(canonical), source=True)
    resource.write_bytes(b"z" * (len(canonical) * 2 + 1))
    with pytest.raises(ValueError):
        portable_semantics._resource("source.py", digest, len(canonical), source=True)
    with pytest.raises(ValueError):
        portable_semantics._resource("source.py", digest, len(canonical))


def test_working_role_backup_receipt_keeps_schema18_bytes(served, monkeypatch):
    store = served.app.state.store
    recording = recording_with_original(store)
    store.add_result(
        recording["id"],
        normalize_segments([{"text": "Synthetic Working transcript"}]),
        {},
        "Synthetic",
        {},
        auto_first=True,
    )
    observations = []

    def synthetic_semantic_check(path, **kwargs):
        with closing(sqlite3.connect(path)) as db:
            observations.append(
                (
                    db.execute("PRAGMA user_version").fetchone()[0],
                    db.execute("SELECT action FROM result_role_events").fetchall(),
                    kwargs["manifest"]["schema_version"],
                )
            )

    # The subprocess's semantic checks are not exercised by this in-process test.
    monkeypatch.setattr(portable_semantics, "inspect_database", synthetic_semantic_check)
    result = capture(served)
    assert observations == [(18, [("working_auto_first",)], 18)]
    recovered = served.get(f"/api/library/portable-backups/{result['backup_id']}/verify")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["commit_sha256"] == result["commit_sha256"]
    assert recovered.json()["verification"] == "transport_integrity_only"
