"""Transport-only checks use synthetic bytes, never a library, database or service."""

import copy
import hashlib
import json
import os
import sqlite3
import subprocess
from concurrent.futures import CancelledError
from pathlib import Path
from types import SimpleNamespace

import pytest

from cbs_transcriber import portable_transport as portable

BACKUP = "a" * 32
LIBRARY = "b" * 32
PROJECT = "c" * 32
RECORDING = "d" * 32


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def seal(root, manifest=None, *, raw=None, marker_changes=None):
    raw = canonical(manifest) if raw is None else raw
    (root / "manifest.json").write_bytes(raw)
    marker = {
        "format": "cbs-library-backup-commit-v2",
        "backup_id": BACKUP,
        "manifest_size": len(raw),
        "manifest_sha256": sha(raw),
    }
    marker.update(marker_changes or {})
    encoded = canonical(marker)
    (root / "COMMITTED.json").write_bytes(encoded)
    return sha(encoded)


@pytest.fixture
def package(tmp_path):
    root = tmp_path / "package"
    objects = root / "objects"
    objects.mkdir(parents=True)
    data = {sha(b"not a SQLite database"): b"not a SQLite database", sha(b"audio"): b"audio"}
    for name, blob in data.items():
        (objects / name).write_bytes(blob)
    database, audio = sha(b"not a SQLite database"), sha(b"audio")
    manifest = {
        "format": "cbs-library-backup-v2",
        "backup_id": BACKUP,
        "source_library_id": LIBRARY,
        "created_at": "2026-10-28T10:00:00Z",
        "scope": "whole_library",
        "inventory_policy": "portable-files-v1",
        "app_version": "0.1.0+test",
        "schema_version": 12,
        "database": {
            "object": database,
            "size": len(data[database]),
            "schema_fingerprint": sha(b"declared schema"),
            "tables": {"projects": {"rows": 0, "sha256": sha(b"declared table")}},
        },
        "objects": [{"sha256": name, "size": len(data[name])} for name in sorted(data)],
        "files": [
            {
                "project_id": PROJECT,
                "recording_id": RECORDING,
                "role": "original",
                "relative_path": "original.wav",
                "object": audio,
            }
        ],
        "legacy_gaps": [],
        "exclusions": "portable-exclusions-v1",
    }
    return root, manifest, seal(root, manifest)


def inspect(package, **kwargs):
    root, _, expected = package
    return portable.inspect_transport(root, expected_commit_sha256=expected, **kwargs)


def test_v137_transport_level_accepts_opaque_non_sqlite_without_opening_it(package, monkeypatch):
    root, manifest, _ = package
    before = {
        p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }

    def forbidden(*args, **kwargs):
        pytest.fail("Transport inspection must never open SQLite or a network service")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr("socket.socket", forbidden)
    observed = inspect(package)
    assert observed["level"] == "transport_integrity_only"
    assert observed["reference"] == "matched_supplied_commit"
    assert observed["restore_ready"] is False
    assert observed["backup_id"] == BACKUP
    assert observed["object_count"] == 2 and observed["file_mapping_count"] == 1
    assert observed["unique_object_bytes"] == sum(item["size"] for item in manifest["objects"])
    assert observed["declared_mapping_bytes"] == observed["unique_object_bytes"]
    assert {
        "database_format_and_integrity",
        "schema_compatibility_and_table_claims",
        "role_resolver_and_capture_completeness",
        "credential_exclusion_and_confidential_contents",
        "restore_quarantine_and_activation",
    } <= set(observed["not_checked"])
    assert not {"manifest", "files", "database", "verified", "complete"} & observed.keys()
    assert str(root) not in json.dumps(observed)
    assert before == {
        p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


def test_v137_deduplication_and_unknown_semantics_remain_explicit(package):
    root, manifest, _ = package
    second = copy.deepcopy(manifest["files"][0])
    second["recording_id"] = "e" * 32
    manifest["files"].append(second)
    manifest["schema_version"] = 999
    manifest["database"]["tables"] = {"unrecognised_table": {"rows": 4, "sha256": "f" * 64}}
    manifest["legacy_gaps"] = [
        {"recording_id": RECORDING, "job_id": None, "code": "unknown_publisher_declaration"}
    ]
    observed = inspect((root, manifest, seal(root, manifest)))
    assert observed["file_mapping_count"] == 2 and observed["legacy_gap_count"] == 1
    assert observed["declared_mapping_bytes"] == observed["unique_object_bytes"] + len(b"audio")
    assert "legacy_gap_semantics" in observed["not_checked"]
    assert observed["restore_ready"] is False


def test_v137_library_evidence_namespace_is_syntax_only(package):
    root, manifest, _ = package
    manifest["files"][0].update(
        project_id=None,
        recording_id=None,
        role="processing_evidence",
        relative_path="runtime-batches/" + "e" * 32 + "/status.json",
    )
    observed = inspect((root, manifest, seal(root, manifest)))
    assert "owner_and_project_membership" in observed["not_checked"]


@pytest.mark.parametrize("expected", [None, "", "A" * 64, "x" * 64, True])
def test_v137_supplied_reference_is_required_before_filesystem_access(
    package, monkeypatch, expected
):
    monkeypatch.setattr(portable, "_entries", lambda *a, **kw: pytest.fail("Unexpected scan"))
    with pytest.raises(ValueError):
        portable.inspect_transport(package[0], expected_commit_sha256=expected)


def test_v137_wrong_reference_reads_no_manifest_or_objects(package, monkeypatch):
    read, calls = portable._read, []

    def watched(path, *args, **kwargs):
        calls.append(path.name)
        return read(path, *args, **kwargs)

    monkeypatch.setattr(portable, "_read", watched)
    with pytest.raises(ValueError):
        portable.inspect_transport(package[0], expected_commit_sha256="0" * 64)
    assert calls == ["COMMITTED.json"]


@pytest.mark.parametrize(
    "damage",
    [
        "unknown_top",
        "wrong_format",
        "wrong_backup",
        "uppercase_source_id",
        "scope",
        "inventory_policy",
        "exclusions",
        "app_prose",
        "schema_bool",
        "timestamp_offset",
        "timestamp_bad_date",
        "object_bool_size",
        "object_extra",
        "object_duplicate",
        "object_order",
        "database_ref",
        "database_size",
        "database_hash",
        "table_bool_rows",
        "table_extra",
        "file_ref",
        "partial_owner",
        "unknown_role",
        "file_extra",
        "gap_job",
        "gap_prose",
    ],
)
def test_v137_strict_manifest_records_reject_malformed_declarations(package, damage):
    root, value, _ = package
    if damage == "unknown_top":
        value["private_note"] = "PRIVATE_CANARY"
    elif damage == "wrong_format":
        value["format"] = "cbs-library-backup-v1"
    elif damage == "wrong_backup":
        value["backup_id"] = "f" * 32
    elif damage == "uppercase_source_id":
        value["source_library_id"] = "A" * 32
    elif damage in {"scope", "inventory_policy", "exclusions"}:
        value[damage] = "unapproved"
    elif damage == "app_prose":
        value["app_version"] = "PRIVATE_CANARY\nsecret"
    elif damage == "schema_bool":
        value["schema_version"] = True
    elif damage == "timestamp_offset":
        value["created_at"] = "2026-10-28T10:00:00+00:00"
    elif damage == "timestamp_bad_date":
        value["created_at"] = "2026-99-28T10:00:00Z"
    elif damage == "object_bool_size":
        value["objects"][0]["size"] = True
    elif damage == "object_extra":
        value["objects"][0]["path"] = "PRIVATE_CANARY"
    elif damage == "object_duplicate":
        value["objects"].append(copy.deepcopy(value["objects"][0]))
    elif damage == "object_order":
        value["objects"].reverse()
    elif damage == "database_ref":
        value["database"]["object"] = "0" * 64
    elif damage == "database_size":
        value["database"]["size"] += 1
    elif damage == "database_hash":
        value["database"]["schema_fingerprint"] = "x" * 64
    elif damage == "table_bool_rows":
        value["database"]["tables"]["projects"]["rows"] = False
    elif damage == "table_extra":
        value["database"]["tables"]["projects"]["sql"] = "PRIVATE_CANARY"
    elif damage == "file_ref":
        value["files"][0]["object"] = "0" * 64
    elif damage == "partial_owner":
        value["files"][0]["project_id"] = None
    elif damage == "unknown_role":
        value["files"][0]["role"] = "executable"
    elif damage == "file_extra":
        value["files"][0]["source_path"] = "PRIVATE_CANARY"
    elif damage in {"gap_job", "gap_prose"}:
        value["legacy_gaps"] = [{"recording_id": RECORDING, "job_id": None, "code": "unknown_gap"}]
        value["legacy_gaps"][0]["job_id" if damage == "gap_job" else "code"] = "PRIVATE_CANARY"
    expected = seal(root, value)
    with pytest.raises(ValueError) as error:
        inspect((root, value, expected))
    assert "PRIVATE_CANARY" not in str(error.value)


@pytest.mark.parametrize("part", ["commit", "manifest"])
@pytest.mark.parametrize("encoding", ["whitespace", "newline", "bom", "duplicate", "float", "nan"])
def test_v137_noncanonical_or_ambiguous_json_is_refused(package, part, encoding):
    root, value, _ = package
    source = (root / ("COMMITTED.json" if part == "commit" else "manifest.json")).read_bytes()
    if encoding == "whitespace":
        raw = source.replace(b":", b": ", 1)
    elif encoding == "newline":
        raw = source + b"\n"
    elif encoding == "bom":
        raw = b"\xef\xbb\xbf" + source
    elif encoding == "duplicate":
        raw = b'{"format":"duplicate",' + source[1:]
    elif encoding == "float":
        raw = source.replace(b'"format":', b'"bad":1.0,"format":', 1)
    else:
        raw = source.replace(b'"format":', b'"bad":NaN,"format":', 1)
    if part == "manifest":
        expected = seal(root, raw=raw)
    else:
        (root / "COMMITTED.json").write_bytes(raw)
        expected = sha(raw)
    with pytest.raises(ValueError):
        portable.inspect_transport(root, expected_commit_sha256=expected)


@pytest.mark.parametrize(
    "relative",
    [
        "../escape",
        "/absolute",
        "C:/drive",
        "folder\\file",
        "audio.wav:stream",
        "folder//file",
        "./file",
        "CON.txt",
        "COM¹.txt",
        "lpt².log",
        "LPT³",
        "NUL .txt",
        "tail.",
        "tail ",
        "bad\x00name",
        "bad\nname",
        "bad?name",
        "bad|name",
        "bad<name",
        "a" * 81,
    ],
)
def test_v137_portable_paths_are_declarations_not_native_paths(package, relative):
    root, value, _ = package
    value["files"][0]["relative_path"] = relative
    with pytest.raises(ValueError):
        inspect((root, value, seal(root, value)))


@pytest.mark.parametrize("names", [("Name.wav", "name.wav"), ("é.wav", "e\u0301.wav")])
def test_v137_case_and_unicode_destination_collisions_refuse(package, names):
    root, value, _ = package
    first = value["files"][0]
    first["relative_path"] = names[0]
    second = copy.deepcopy(first) | {"relative_path": names[1]}
    value["files"] = sorted([first, second], key=lambda item: item["relative_path"])
    with pytest.raises(ValueError):
        inspect((root, value, seal(root, value)))


def test_v137_manifest_relative_path_is_never_opened(package, monkeypatch):
    root, value, _ = package
    value["files"][0]["relative_path"] = "unresolved-role-syntax/declared.json"
    expected = seal(root, value)
    opened, original = [], portable.os.open

    def watched(path, *args, **kwargs):
        path = Path(path)
        opened.append(path.relative_to(root).as_posix())
        assert path.name in {"COMMITTED.json", "manifest.json"} or (
            path.parent == root / "objects" and len(path.name) == 64
        )
        return original(path, *args, **kwargs)

    monkeypatch.setattr(portable.os, "open", watched)
    result = inspect((root, value, expected))
    assert len(opened) == 4 and "role_resolver_and_capture_completeness" in result["not_checked"]


@pytest.mark.parametrize("where", ["root", "objects", "unused_declared"])
def test_v137_closed_inventory_refuses_extras(package, where):
    root, value, _ = package
    if where == "root":
        (root / "hidden-note.txt").write_bytes(b"private")
    elif where == "objects":
        (root / "objects" / ("0" * 64)).write_bytes(b"extra")
    else:
        value["objects"].append({"sha256": "0" * 64, "size": 0})
        value["objects"].sort(key=lambda item: item["sha256"])
        expected = seal(root, value)
        package = root, value, expected
    with pytest.raises(ValueError):
        inspect(package)


@pytest.mark.parametrize("damage", ["missing", "same_size", "wrong_size", "directory", "hardlink"])
def test_v137_object_bytes_and_regular_ownership_are_checked(package, damage):
    root, _, _ = package
    path = root / "objects" / sha(b"audio")
    if damage == "missing":
        path.unlink()
    elif damage == "same_size":
        path.write_bytes(b"wrong")
    elif damage == "wrong_size":
        path.write_bytes(b"larger")
    elif damage == "directory":
        path.unlink()
        path.mkdir()
    else:
        os.link(path, root.parent / "alias")
    with pytest.raises(ValueError):
        inspect(package)


@pytest.mark.parametrize("where", ["root", "objects", "manifest", "object"])
def test_v137_managed_symlinks_are_never_followed(package, where):
    root, _, expected = package
    path = {
        "root": root,
        "objects": root / "objects",
        "manifest": root / "manifest.json",
        "object": root / "objects" / sha(b"audio"),
    }[where]
    destination = root.parent / ("outside-" + where)
    path.rename(destination)
    try:
        path.symlink_to(destination, target_is_directory=destination.is_dir())
    except OSError:
        destination.rename(path)
        pytest.skip("The operating system denied symlink creation")
    with pytest.raises(ValueError):
        portable.inspect_transport(root, expected_commit_sha256=expected)


@pytest.mark.parametrize(
    "limit",
    [
        "commit_bytes",
        "manifest_bytes",
        "objects",
        "files",
        "gaps",
        "database_bytes",
        "object_bytes",
        "unique_bytes",
        "depth",
        "string_bytes",
        "path_bytes",
        "component_bytes",
    ],
)
def test_v137_tiny_policy_limits_bound_real_bytes_and_declarations(package, monkeypatch, limit):
    root, value, _ = package
    limits = dict(portable.LIMITS)
    if limit == "commit_bytes":
        limits[limit] = (root / "COMMITTED.json").stat().st_size - 1
    elif limit == "manifest_bytes":
        limits[limit] = (root / "manifest.json").stat().st_size - 1
    elif limit in {"objects", "files", "gaps"}:
        limits[limit] = 0
        if limit == "gaps":
            value["legacy_gaps"] = [{"recording_id": RECORDING, "job_id": None, "code": "gap"}]
    elif limit == "database_bytes":
        limits[limit] = value["database"]["size"] - 1
    elif limit == "object_bytes":
        limits[limit] = 1
    elif limit == "unique_bytes":
        limits[limit] = sum(item["size"] for item in value["objects"]) - 1
    elif limit == "depth":
        limits[limit] = 3
    elif limit == "string_bytes":
        limits[limit] = 63
    elif limit == "path_bytes":
        limits[limit] = len("original.wav") - 1
    else:
        limits[limit] = len("original.wav") - 1
    expected = seal(root, value)
    monkeypatch.setattr(portable, "LIMITS", limits)
    with pytest.raises(ValueError):
        inspect((root, value, expected))


def test_v137_byte_and_depth_preflight_precede_json_parser(package, monkeypatch):
    root, _, _ = package
    monkeypatch.setattr(portable, "_canonical", lambda data: pytest.fail("Unexpected parser"))
    monkeypatch.setitem(portable.LIMITS, "commit_bytes", 1)
    with pytest.raises(ValueError):
        inspect(package)
    # Structural nesting outside quoted strings is refused before json.loads too.
    monkeypatch.undo()
    monkeypatch.setattr(portable.json, "loads", lambda *a, **kw: pytest.fail("Unexpected decoder"))
    with pytest.raises(ValueError):
        portable._canonical(b"[" * 13 + b"0" + b"]" * 13)


def test_v137_ctime_is_compared_within_each_stat_api(package, monkeypatch):
    original = portable.os.fstat

    def different_meaning(descriptor):
        info = original(descriptor)
        fields = {
            name: getattr(info, name)
            for name in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_mode", "st_nlink")
        }
        return SimpleNamespace(**fields, st_ctime_ns=info.st_ctime_ns + 12345)

    monkeypatch.setattr(portable.os, "fstat", different_meaning)
    assert inspect(package)["restore_ready"] is False


@pytest.mark.parametrize("change", ["manifest", "object", "new_root_item", "new_object"])
def test_v137_late_changes_refuse_without_publishing_an_observation(package, monkeypatch, change):
    root, _, _ = package
    original, changed = portable._read, False

    def mutate_after_read(path, *args, **kwargs):
        nonlocal changed
        result = original(path, *args, **kwargs)
        if path.parent == root / "objects" and not changed:
            changed = True
            if change == "manifest":
                (root / "manifest.json").write_bytes((root / "manifest.json").read_bytes() + b" ")
            elif change == "object":
                data = path.read_bytes()
                path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
            elif change == "new_root_item":
                (root / "unexpected.txt").write_bytes(b"x")
            else:
                (root / "objects" / ("0" * 64)).write_bytes(b"x")
        return result

    monkeypatch.setattr(portable, "_read", mutate_after_read)
    with pytest.raises(ValueError):
        inspect(package)
    assert changed


def test_v137_guard_cancellation_closes_handles_without_writes(package, monkeypatch):
    root, _, _ = package
    original, handles = portable.os.fdopen, []

    def track(*args, **kwargs):
        stream = original(*args, **kwargs)
        handles.append(stream)
        return stream

    def cancel_in_reader():
        if any(not stream.closed for stream in handles):
            raise CancelledError

    before = {
        p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    monkeypatch.setattr(portable.os, "fdopen", track)
    with pytest.raises(CancelledError):
        inspect(package, guard=cancel_in_reader)
    assert handles and all(stream.closed for stream in handles)
    assert before == {
        p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


@pytest.mark.parametrize("failure", [MemoryError("injected"), OSError("PRIVATE_CANARY_PATH")])
def test_v137_fdopen_failure_closes_untransferred_descriptor(package, monkeypatch, failure):
    root, _, _ = package
    original, descriptors = portable.os.open, []

    def opened(*args, **kwargs):
        descriptor = original(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    def fail_factory(*args, **kwargs):
        raise failure

    monkeypatch.setattr(portable.os, "open", opened)
    monkeypatch.setattr(portable.os, "fdopen", fail_factory)
    with pytest.raises(ValueError if isinstance(failure, OSError) else MemoryError) as error:
        portable._read(root / "manifest.json", root, maximum=16 * 1024)
    assert "PRIVATE_CANARY_PATH" not in str(error.value)
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_v137_fdopen_failure_never_closes_a_reused_unrelated_descriptor(package, monkeypatch):
    root, _, _ = package
    unrelated = None

    def fail_after_factory_cleanup(descriptor, *args, **kwargs):
        nonlocal unrelated
        if kwargs.get("closefd", True):
            os.close(descriptor)
        unrelated = os.open(root / "manifest.json", os.O_RDONLY | getattr(os, "O_BINARY", 0))
        raise MemoryError("injected factory failure")

    monkeypatch.setattr(portable.os, "fdopen", fail_after_factory_cleanup)
    try:
        with pytest.raises(MemoryError):
            portable._read(root / "manifest.json", root, maximum=16 * 1024)
        assert unrelated is not None
        assert os.fstat(unrelated).st_size > 0
    finally:
        if unrelated is not None:
            try:
                os.close(unrelated)
            except OSError:
                pass


def test_v137_scandir_advance_error_is_curated_and_closes_iterator(package, monkeypatch):
    root, _, _ = package

    class BrokenIterator:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def __iter__(self):
            return self

        def __next__(self):
            raise OSError("PRIVATE_CANARY_PATH")

    iterator = BrokenIterator()
    monkeypatch.setattr(portable.os, "scandir", lambda path: iterator)
    with pytest.raises(ValueError) as error:
        portable._entries(root, root, maximum=3, guard=None)
    assert "PRIVATE_CANARY_PATH" not in str(error.value)
    assert iterator.closed


def test_v137_scandir_guard_oserror_propagates_unchanged(package):
    root, _, _ = package
    failure = OSError("caller-owned guard failure")

    def stop():
        raise failure

    with pytest.raises(OSError) as error:
        portable._entries(root, root, maximum=3, guard=stop)
    assert error.value is failure


def test_v137_growing_read_stops_at_declared_size_plus_one(package, monkeypatch):
    root, _, _ = package
    path = root / "objects" / sha(b"audio")
    original, streams, sizes = portable.os.fdopen, [], []

    class GrowingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            sizes.append(size)
            if len(sizes) == 1:
                with path.open("ab") as writer:
                    writer.write(b"growth")
            return self.stream.read(size)

    def wrap(*args, **kwargs):
        stream = GrowingStream(original(*args, **kwargs))
        streams.append(stream)
        return stream

    monkeypatch.setattr(portable.os, "fdopen", wrap)
    monkeypatch.setattr(portable, "CHUNK_BYTES", 2)
    with pytest.raises(ValueError):
        portable._read(path, root, maximum=100, size=5)
    assert sizes == [2, 2, 2]
    assert streams and all(stream.stream.closed for stream in streams)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
@pytest.mark.parametrize("where", ["root", "objects"])
def test_v137_managed_windows_junction_is_refused(package, where):
    root, _, expected = package
    path = root if where == "root" else root / "objects"
    destination = root.parent / ("junction-target-" + where)
    path.rename(destination)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(path), str(destination)],
        check=False,
        capture_output=True,
    )
    if result.returncode:
        destination.rename(path)
        pytest.skip("The operating system denied synthetic junction creation")
    try:
        with pytest.raises(ValueError):
            portable.inspect_transport(root, expected_commit_sha256=expected)
    finally:
        # Remove only the checked junction itself, never recurse into its target.
        assert path.is_junction() and path.resolve() == destination.resolve()
        path.rmdir()
        destination.rename(path)


def test_v137_existing_v1_inventory_is_not_promoted_to_v2(package):
    root, _, _ = package
    expected = seal(root, {"format": "cbs-library-backup-v1", "originals": []})
    with pytest.raises(ValueError):
        portable.inspect_transport(root, expected_commit_sha256=expected)
