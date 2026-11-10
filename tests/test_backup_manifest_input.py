"""Malformed legacy inventory metadata never becomes HTML or blocks healthy inventories."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cbs_transcriber.backup import FORMAT


def manifest():
    return {
        "format": FORMAT,
        "created": "2026-10-31T00:00:00+00:00",
        "tables": {"recordings": 0},
        "originals": [],
        "missing_originals": [],
    }


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        "scalar",
        {"format": FORMAT},
        manifest() | {"tables": []},
        manifest() | {"originals": None},
        manifest() | {"missing_originals": {}},
        manifest() | {"created": "\ud800"},
        manifest() | {"tables": {"recordings": "<span data-untrusted>0</span>"}},
    ],
)
def test_malformed_inventory_is_skipped_without_blocking_listing_or_new_backup(client, bad):
    root = client.app.state.store.root / "backups"
    good, broken = root / "library-good", root / "library-z-broken"
    good.mkdir(parents=True)
    broken.mkdir()
    (good / "manifest.json").write_text(json.dumps(manifest()), encoding="utf-8")
    raw = json.dumps(bad).encode()
    (broken / "manifest.json").write_bytes(raw)
    safe = TestClient(client.app, headers={"X-CBS-Request": "1"}, raise_server_exceptions=False)
    try:
        listing = safe.get("/api/library/backups")
        assert listing.status_code == 200, listing.text
        assert [entry["folder"] for entry in listing.json()["backups"]] == [str(good)]
        created = safe.post("/api/library/backups", json={})
        assert created.status_code == 201, created.text
    finally:
        safe.close()
    assert (broken / "manifest.json").read_bytes() == raw


@pytest.mark.parametrize(
    "item",
    [
        None,
        [],
        {"recording_id": [], "size": 1, "mtime_ns": 2, "sha256": "a" * 64},
        {"recording_id": "a" * 32, "size": [], "mtime_ns": 2, "sha256": "a" * 64},
    ],
)
def test_malformed_cached_original_entry_does_not_abort_new_inventory(client, item):
    root = client.app.state.store.root / "backups" / "library-z-malformed-entry"
    root.mkdir(parents=True)
    payload = copy.deepcopy(manifest())
    payload["originals"] = [item]
    raw = json.dumps(payload).encode()
    (root / "manifest.json").write_bytes(raw)
    safe = TestClient(client.app, headers={"X-CBS-Request": "1"}, raise_server_exceptions=False)
    try:
        response = safe.post("/api/library/backups", json={})
        assert response.status_code == 201, response.text
    finally:
        safe.close()
    assert (root / "manifest.json").read_bytes() == raw


@pytest.mark.parametrize("digest", [{"unexpected": "metadata"}, "not-a-sha256"])
def test_malformed_cached_hash_is_recomputed_from_the_owned_original(client, digest):
    store = client.app.state.store
    project = store.create_project("Synthetic hash cache")
    recording = store.create_recording(project["id"], "synthetic.wav")
    original = store.directory(recording["id"])
    original.mkdir(parents=True)
    body = b"SYNTHETIC ORIGINAL FOR HASH VALIDATION"
    path = original / "original.wav"
    path.write_bytes(body)
    observed = path.stat()
    data = manifest()
    data["tables"]["recordings"] = 1
    data["originals"] = [
        {
            "recording_id": recording["id"],
            "size": len(body),
            "mtime_ns": observed.st_mtime_ns,
            "sha256": digest,
        }
    ]
    root = store.root / "backups" / "library-z-invalid-hash"
    root.mkdir(parents=True)
    raw = json.dumps(data).encode()
    (root / "manifest.json").write_bytes(raw)
    response = client.post("/api/library/backups", json={})
    assert response.status_code == 201, response.text
    created = json.loads((Path(response.json()["folder"]) / "manifest.json").read_bytes())
    assert created["originals"][0]["sha256"] == hashlib.sha256(body).hexdigest()
    assert (root / "manifest.json").read_bytes() == raw


def test_deeply_nested_legacy_json_does_not_break_listing_or_creation(client):
    root = client.app.state.store.root / "backups" / "library-z-deep"
    root.mkdir(parents=True)
    raw = b"[" * 1200 + b"0" + b"]" * 1200
    (root / "manifest.json").write_bytes(raw)
    assert client.get("/api/library/backups").status_code == 200
    assert client.post("/api/library/backups", json={}).status_code == 201
    assert (root / "manifest.json").read_bytes() == raw
