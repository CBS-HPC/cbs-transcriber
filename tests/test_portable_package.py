"""Same-call composition checks on tiny synthetic packages, never a real library."""

import copy
import hashlib
import json
import os
import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from cbs_transcriber import portable_database as reader
from cbs_transcriber import portable_package as package
from cbs_transcriber import portable_rows as rows
from cbs_transcriber import portable_transport as transport


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def seal(root, manifest):
    raw = canonical(manifest)
    (root / "manifest.json").write_bytes(raw)
    marker = {
        "format": "cbs-library-backup-commit-v2",
        "backup_id": "a" * 32,
        "manifest_size": len(raw),
        "manifest_sha256": sha(raw),
    }
    raw_marker = canonical(marker)
    (root / "COMMITTED.json").write_bytes(raw_marker)
    return sha(raw_marker)


@pytest.fixture
def sample(tmp_path):
    root = tmp_path / "PRIVATE_PACKAGE_PATH"
    (root / "objects").mkdir(parents=True)
    catalog = json.loads(
        reader.files("cbs_transcriber").joinpath("catalogs/portable_schema_12.json").read_bytes()
    )
    source = tmp_path / "synthetic.sqlite3"
    db = sqlite3.connect(source)
    try:
        for kind in ("table", "index"):
            for item in catalog["structure"]["objects"]:
                if item["type"] == kind and item["sql"] is not None:
                    db.execute(item["sql"])
        db.execute("PRAGMA user_version=12")
        db.execute("INSERT INTO settings VALUES (1, ?)", ("PRIVATE_BODY æøå 😀",))
        db.commit()
        claims = {}
        for name, table in catalog["structure"]["tables"].items():
            columns = [column["name"] for column in table["columns"]]
            keys = [
                column["name"]
                for column in sorted(table["columns"], key=lambda c: c["pk_position"])
                if column["pk_position"]
            ]
            # Identifiers come only from the trusted pinned fixture catalog.
            result = rows.digest_table(
                name,
                db.execute(
                    "SELECT " + ",".join(columns) + " FROM " + name + " ORDER BY " + ",".join(keys)
                ),
            )
            claims[name] = {key: result[key] for key in ("rows", "sha256")}
    finally:
        db.close()
    raw_db, raw_media = source.read_bytes(), b"synthetic media bytes only"
    db_hash, media_hash = sha(raw_db), sha(raw_media)
    (root / "objects" / db_hash).write_bytes(raw_db)
    (root / "objects" / media_hash).write_bytes(raw_media)
    manifest = {
        "format": "cbs-library-backup-v2",
        "backup_id": "a" * 32,
        "source_library_id": "b" * 32,
        "created_at": "2026-10-28T14:00:00Z",
        "scope": "whole_library",
        "inventory_policy": "portable-files-v1",
        "app_version": "0.1.0",
        "schema_version": 12,
        "database": {
            "object": db_hash,
            "size": len(raw_db),
            "schema_fingerprint": reader.SCHEMA_FINGERPRINT,
            "tables": claims,
        },
        "objects": sorted(
            [
                {"sha256": db_hash, "size": len(raw_db)},
                {"sha256": media_hash, "size": len(raw_media)},
            ],
            key=lambda o: o["sha256"],
        ),
        "files": [
            {
                "project_id": "c" * 32,
                "recording_id": "d" * 32,
                "role": "original",
                "relative_path": "PRIVATE_FILENAME.wav",
                "object": media_hash,
            }
        ],
        "legacy_gaps": [{"recording_id": "d" * 32, "job_id": None, "code": "unknown_semantics"}],
        "exclusions": "portable-exclusions-v1",
    }
    return root, manifest, seal(root, manifest)


def fake_observation(manifest):
    database = manifest["database"]
    return {
        "level": "database_claims_only",
        "reference": "matched_supplied_database",
        "database_sha256": database["object"],
        "database_bytes": database["size"],
        "schema_version": 12,
        "schema_fingerprint": reader.SCHEMA_FINGERPRINT,
        "table_policy": "cbs-portable-table-v1",
        "tables": {
            name: dict(claim, encoded_bytes=100, private="PRIVATE_EXTRA")
            for name, claim in database["tables"].items()
        },
        "sqlite_version": "3.45.3",
        "heap_cap_bytes": reader.HEAP_BYTES,
        "heap_sentry": "allocation_refused_before_open",
        "worker_sha256_lf": reader.WORKER_SHA256_LF,
        "codec_sha256_lf": reader.CODEC_SHA256_LF,
        "catalog_sha256": reader.CATALOG_SHA256,
        "not_checked": ["sealed_package_binding"],
        "restore_ready": False,
        "private_path": "PRIVATE_EXTRA",
    }


def inspect(sample, **kwargs):
    return package.inspect_package(sample[0], expected_commit_sha256=sample[2], **kwargs)


def test_v146_derived_database_reference_is_bound_and_output_is_allowlisted(sample, monkeypatch):
    root, manifest, expected = sample
    wanted = copy.deepcopy(manifest)
    calls = []

    def checked(path, **kwargs):
        calls.append(path)
        assert path == root / "objects" / wanted["database"]["object"]
        assert kwargs["expected_sha256"] == wanted["database"]["object"]
        assert kwargs["expected_size"] == wanted["database"]["size"]
        assert kwargs["expected_claims"] == {
            "schema_version": 12,
            "schema_fingerprint": wanted["database"]["schema_fingerprint"],
            "tables": wanted["database"]["tables"],
        }
        return fake_observation(wanted)

    monkeypatch.setattr(reader, "inspect_database_object", checked)
    result = inspect(sample)
    assert len(calls) == 1
    assert result["level"] == "package_database_claims_only"
    assert result["reference"] == "matched_supplied_commit" and result["commit_sha256"] == expected
    assert result["restore_ready"] is False
    assert result["object_count"] == 2 and result["file_mapping_count"] == 1
    assert result["legacy_gap_count"] == 1
    assert "sealed_package_binding" not in result["not_checked"]
    assert {
        "supplied_reference_custody_and_authentication",
        "filesystem_atomicity_and_future_availability",
        "owner_and_project_membership",
        "role_resolver_and_capture_completeness",
        "legacy_gap_semantics",
        "history_payloads_and_active_mirrors",
    } <= set(result["not_checked"])
    encoded = json.dumps(result, ensure_ascii=False)
    for private in [str(root), "PRIVATE_", "b" * 32, "c" * 32, "d" * 32]:
        assert private not in encoded
    assert all(
        set(item) == {"rows", "sha256", "encoded_bytes"}
        for item in result["database_claims"]["tables"].values()
    )


class HashSubclass(str):
    pass


@pytest.mark.parametrize("reference", [None, True, "", "A" * 64, "x" * 64, HashSubclass("a" * 64)])
def test_v146_native_commit_reference_fails_before_capture_or_guard(sample, monkeypatch, reference):
    monkeypatch.setattr(
        transport, "_capture_transport", lambda *a, **k: pytest.fail("Unexpected capture")
    )
    with pytest.raises(ValueError):
        package.inspect_package(
            sample[0],
            expected_commit_sha256=reference,
            guard=lambda: pytest.fail("Unexpected guard"),
        )


@pytest.mark.parametrize(
    "extra", [{"capture": {}}, {"manifest": {}}, {"expected_claims": {}}, {"cache": {}}]
)
def test_v146_no_public_capture_or_claims_authority_parameter(sample, extra):
    with pytest.raises(TypeError):
        package.inspect_package(sample[0], expected_commit_sha256=sample[2], **extra)


def test_v146_private_capture_contains_only_immutable_observations(sample):
    capture = transport._capture_transport(sample[0], expected_commit_sha256=sample[2])
    assert type(capture.manifest_bytes) is bytes
    assert type(capture.object_names) is tuple and type(capture.witnesses) is tuple
    assert type(capture.counts) is tuple
    assert all(
        type(name) is str and type(witness) is os.stat_result for name, witness in capture.witnesses
    )
    with pytest.raises(FrozenInstanceError):
        capture.manifest_bytes += b"changed"
    with pytest.raises(TypeError):
        capture.object_names[0] = "changed"
    with pytest.raises(AttributeError):
        capture.root_witness.st_size = 0
    assert not hasattr(capture, "__dict__")


def test_v146_manifest_is_not_reopened_for_database_claims(sample, monkeypatch):
    root, manifest, _ = sample
    original, old = transport._capture_transport, copy.deepcopy(manifest)
    seen = []

    def changed_after_capture(*args, **kwargs):
        capture = original(*args, **kwargs)
        changed = copy.deepcopy(manifest)
        changed["database"]["tables"]["settings"]["sha256"] = "0" * 64
        seal(root, changed)
        return capture

    def reader_from_capture(path, **kwargs):
        seen.append(kwargs["expected_claims"])
        assert kwargs["expected_claims"]["tables"] == old["database"]["tables"]
        return fake_observation(old)

    monkeypatch.setattr(transport, "_capture_transport", changed_after_capture)
    monkeypatch.setattr(reader, "inspect_database_object", reader_from_capture)
    with pytest.raises(ValueError):
        inspect(sample)
    assert seen


def test_v146_no_callback_intervenes_before_reader_snapshots_derived_claims(sample, monkeypatch):
    capture_done, reader_started = False, False
    original = transport._capture_transport

    def capture(*args, **kwargs):
        nonlocal capture_done
        result = original(*args, **kwargs)
        capture_done = True
        return result

    def checked(path, **kwargs):
        nonlocal reader_started
        frozen = reader._claims(kwargs["expected_claims"])
        reader_started = True
        kwargs["guard"]()
        assert frozen["tables"] == sample[1]["database"]["tables"]
        return fake_observation(sample[1])

    def guard():
        assert not capture_done or reader_started

    monkeypatch.setattr(transport, "_capture_transport", capture)
    monkeypatch.setattr(reader, "inspect_database_object", checked)
    assert inspect(sample, guard=guard)["restore_ready"] is False


@pytest.mark.parametrize(
    "change",
    [
        "root_extra",
        "object_extra",
        "manifest",
        "commit",
        "media",
        "database",
        "hardlink",
        "directory",
    ],
)
def test_v146_final_inventory_and_witness_checks_reject_late_drift(sample, monkeypatch, change):
    root, manifest, _ = sample
    media = root / "objects" / manifest["files"][0]["object"]

    def changed_reader(path, **kwargs):
        if change == "root_extra":
            (root / "extra").write_bytes(b"x")
        elif change == "object_extra":
            (root / "objects" / ("0" * 64)).write_bytes(b"x")
        elif change == "hardlink":
            os.link(media, root.parent / "alias")
        elif change == "directory":
            info = root.stat()
            os.utime(root, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
        else:
            target = {
                "manifest": root / "manifest.json",
                "commit": root / "COMMITTED.json",
                "media": media,
                "database": path,
            }[change]
            before = target.stat()
            raw = target.read_bytes()
            target.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        return fake_observation(manifest)

    monkeypatch.setattr(reader, "inspect_database_object", changed_reader)
    with pytest.raises(ValueError):
        inspect(sample)


def test_v146_reader_failure_never_returns_a_partial_observation(sample, monkeypatch):
    error = ValueError("The supplied database claims are invalid.")

    def failed(*args, **kwargs):
        raise error

    monkeypatch.setattr(reader, "inspect_database_object", failed)
    with pytest.raises(ValueError) as observed:
        inspect(sample)
    assert observed.value is error


@pytest.mark.parametrize("where", ["media", "manifest"])
def test_v146_transport_failure_refuses_before_database_reader(sample, monkeypatch, where):
    root, manifest, _ = sample
    target = (
        root / "manifest.json"
        if where == "manifest"
        else (root / "objects" / manifest["files"][0]["object"])
    )
    target.write_bytes(target.read_bytes() + b"changed")
    monkeypatch.setattr(
        reader, "inspect_database_object", lambda *a, **k: pytest.fail("Unexpected reader")
    )
    with pytest.raises(ValueError):
        inspect(sample)


def test_v146_actual_reader_rejects_wrong_claims_in_a_sealed_manifest(sample, monkeypatch):
    root, manifest, _ = sample
    manifest["database"]["tables"]["settings"]["sha256"] = "0" * 64
    reference = seal(root, manifest)
    children, popen = [], reader.subprocess.Popen

    def tracked(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(reader.subprocess, "Popen", tracked)
    with pytest.raises(ValueError):
        package.inspect_package(root, expected_commit_sha256=reference)
    assert len(children) == 1
    assert children[0].poll() is not None
    assert children[0].stdin.closed and children[0].stdout.closed


class StopPackage(BaseException):
    pass


def test_v146_guard_at_final_recheck_is_unchanged(sample, monkeypatch):
    finished, failure = False, StopPackage("caller cancellation")

    def checked(*args, **kwargs):
        nonlocal finished
        finished = True
        return fake_observation(sample[1])

    def guard():
        if finished:
            raise failure

    monkeypatch.setattr(reader, "inspect_database_object", checked)
    with pytest.raises(StopPackage) as error:
        inspect(sample, guard=guard)
    assert error.value is failure


def test_v146_actual_spawned_child_is_closed_before_guard_exception_escapes(sample, monkeypatch):
    children, popen, failure = [], reader.subprocess.Popen, StopPackage("caller cancellation")

    def tracked(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    def guard():
        if children:
            raise failure

    monkeypatch.setattr(reader.subprocess, "Popen", tracked)
    with pytest.raises(StopPackage) as error:
        inspect(sample, guard=guard)
    assert error.value is failure
    assert len(children) == 1 and children[0].poll() is not None
    assert children[0].stdin.closed and children[0].stdout.closed


def test_v146_actual_composition_hashes_media_once_and_rechecks_after_reaping(sample, monkeypatch):
    root, manifest, _ = sample
    hashes, children, rechecks = [], [], []
    original_read, original_popen, original_recheck = (
        transport._read,
        reader.subprocess.Popen,
        transport._recheck,
    )

    def watched(path, *args, **kwargs):
        hashes.append(Path(path).relative_to(root).as_posix())
        return original_read(path, *args, **kwargs)

    def popen(*args, **kwargs):
        result = original_popen(*args, **kwargs)
        children.append(result)
        return result

    def recheck(capture, guard=None):
        if children:
            assert all(
                child.poll() is not None and child.stdin.closed and child.stdout.closed
                for child in children
            )
        rechecks.append(bool(children))
        return original_recheck(capture, guard)

    before = {name: (root / name).read_bytes() for name in ["manifest.json", "COMMITTED.json"]}
    monkeypatch.setattr(transport, "_read", watched)
    monkeypatch.setattr(reader.subprocess, "Popen", popen)
    monkeypatch.setattr(transport, "_recheck", recheck)
    result = inspect(sample)
    assert result["database_claims"]["tables"]["settings"]["rows"] == 1
    assert hashes.count("objects/" + manifest["files"][0]["object"]) == 1
    assert hashes.count("objects/" + manifest["database"]["object"]) == 3
    assert hashes.count("manifest.json") == hashes.count("COMMITTED.json") == 1
    assert rechecks == [False, True]
    assert len(children) == 1 and result["restore_ready"] is False
    assert before == {name: (root / name).read_bytes() for name in before}
    assert "PRIVATE_" not in json.dumps(result, ensure_ascii=False)
