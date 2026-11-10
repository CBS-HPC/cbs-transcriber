"""Producer observations use synthetic files, no Store, provider, model or current runtime."""

import hashlib
import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from cbs_transcriber import provenance_artifacts as artifacts

PRODUCER = {
    "recording_id": "a" * 32,
    "job_id": "b" * 32,
    "request_sha256": "c" * 64,
    "stage": "transcription",
}
CREATION = {"result_id": "d" * 32, "version_id": "e" * 32, "payload_sha256": "f" * 64}
ENVIRONMENT = {
    "format": artifacts.ENVIRONMENT_FORMAT,
    "python": {"implementation": "cpython", "version": "3.13.7"},
    "platform": {"system": "Linux", "machine": "x86_64"},
    "packages": {"whisperx": "3.8.6", "torch": "2.8.0+cu128"},
    "device": {"type": "cuda", "index": 0, "memory_bytes": 23 * 1024**3},
    "code": {"application_sha256": None, "worker_sha256": "1" * 64, "lock_sha256": "2" * 64},
    "recipe": {"id": "whisperx-transcription-v1", "compute_type": "float16"},
}


@pytest.fixture
def owned(tmp_path):
    root = tmp_path / "recording-owned"
    root.mkdir()
    return root


def capture(root, *, role="output", data=b"Private synthetic words", format="json"):
    path = root / (role + ".private.json")
    path.write_bytes(data)
    return artifacts.capture_file(path, root, role=role, format=format), path


def bound(root):
    observed, path = capture(root)
    checkpoint = artifacts.make_checkpoint([observed], producer=PRODUCER)
    return artifacts.bind_creation(checkpoint, producer=PRODUCER, creation=CREATION), path


@pytest.mark.parametrize("role", ["request", "raw_response", "output", "environment"])
def test_v144_exact_bytes_and_public_projection(owned, role):
    data = (
        json.dumps(ENVIRONMENT, indent=2).encode()
        if role == "environment"
        else b'\xef\xbb\xbf{ "text": "Private synthetic words" }\r\n'
    )
    observed, path = capture(owned, role=role, data=data)
    producer = (
        {**PRODUCER, "request_sha256": hashlib.sha256(data).hexdigest()}
        if role == "request"
        else PRODUCER
    )
    checkpoint = artifacts.make_checkpoint([observed], producer=producer)
    expected = {
        "role": role,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "format": "json",
    }
    if role == "environment":
        expected["environment"] = ENVIRONMENT
    assert observed.receipt() == expected
    assert checkpoint["artifacts"][role] == expected
    assert all(
        value == "not_captured" for key, value in checkpoint["artifacts"].items() if key != role
    )
    receipt = artifacts.bind_creation(checkpoint, producer=producer, creation=CREATION)
    projected = artifacts.public_projection(receipt, producer=producer, creation=CREATION)
    encoded = json.dumps(projected)
    assert projected["retained_files"] == "not_checked"
    assert path.read_bytes() == data
    for private in (
        str(owned),
        path.name,
        "Private synthetic words",
        PRODUCER["recording_id"],
        PRODUCER["job_id"],
        CREATION["result_id"],
        CREATION["version_id"],
    ):
        assert private not in encoded
    assert "request_sha256" not in encoded
    assert "payload_sha256" not in encoded


def test_v144_request_receipt_must_match_exact_frozen_request_anchor(owned):
    observed, _ = capture(owned, role="request", data=b'{"language":"da"}')
    producer = {**PRODUCER, "request_sha256": observed.receipt()["sha256"]}
    checkpoint = artifacts.make_checkpoint([observed], producer=producer)
    assert artifacts.validate_checkpoint(checkpoint, producer=producer) == checkpoint
    with pytest.raises(ValueError, match="artifact evidence"):
        artifacts.make_checkpoint([observed], producer=PRODUCER)
    checkpoint["artifacts"]["request"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="artifact evidence"):
        artifacts.validate_checkpoint(checkpoint, producer=producer)


def test_v144_empty_opaque_response_is_observed_without_claiming_success(owned):
    observed, _ = capture(owned, role="raw_response", data=b"", format="bytes")
    assert observed.receipt()["bytes"] == 0
    assert observed.receipt()["sha256"] == hashlib.sha256(b"").hexdigest()
    assert "success" not in observed.receipt()


def test_v144_no_dictionary_capture_and_duplicate_role(owned):
    observed, _ = capture(owned)
    for supplied in ([], [observed.receipt()], [observed, observed]):
        with pytest.raises(ValueError, match="artifact evidence"):
            artifacts.make_checkpoint(supplied, producer=PRODUCER)


def test_v144_observations_and_validators_return_independent_values(owned):
    observed, _ = capture(owned)
    first = observed.receipt()
    first["sha256"] = "0" * 64
    assert observed.receipt() != first
    with pytest.raises(AttributeError):
        observed._json = b"forged"
    checkpoint = artifacts.make_checkpoint([observed], producer=PRODUCER)
    checked = artifacts.validate_checkpoint(checkpoint, producer=PRODUCER)
    checked["artifacts"]["output"]["sha256"] = "0" * 64
    assert checked != checkpoint


@pytest.mark.parametrize("where", ["producer", "creation"])
@pytest.mark.parametrize("change", ["identity", "hash", "unknown", "bool"])
def test_v144_exact_reference_binding(owned, where, change):
    receipt, _ = bound(owned)
    expected = deepcopy(PRODUCER if where == "producer" else CREATION)
    if change == "identity":
        expected["job_id" if where == "producer" else "version_id"] = "0" * 32
    elif change == "hash":
        expected["request_sha256" if where == "producer" else "payload_sha256"] = "0" * 64
    elif change == "unknown":
        expected["private_path"] = "private"
    else:
        expected["job_id" if where == "producer" else "version_id"] = True
    with pytest.raises(ValueError, match="artifact evidence"):
        artifacts.validate_bound(
            receipt,
            producer=expected if where == "producer" else PRODUCER,
            creation=expected if where == "creation" else CREATION,
        )


@pytest.mark.parametrize(
    "change", ["extra", "digest", "size", "format", "role", "missing", "empty"]
)
def test_v144_malformed_checkpoint_rejected(owned, change):
    receipt, _ = bound(owned)
    output = receipt["artifacts"]["output"]
    if change == "extra":
        output["path"] = "private"
    elif change == "digest":
        output["sha256"] = "INVALID"
    elif change == "size":
        output["bytes"] = True
    elif change == "format":
        output["format"] = "pickle"
    elif change == "role":
        output["role"] = "request"
    elif change == "missing":
        receipt["artifacts"].pop("request")
    else:
        receipt["artifacts"]["output"] = "not_captured"
    with pytest.raises(ValueError, match="artifact evidence"):
        artifacts.validate_bound(receipt, producer=PRODUCER, creation=CREATION)


def test_v144_historical_validation_never_reads_files_or_runtime(owned, monkeypatch):
    receipt, path = bound(owned)
    path.unlink()

    def prohibited(*args, **kwargs):
        pytest.fail("Historical validation attempted filesystem or runtime lookup")

    monkeypatch.setattr(Path, "open", prohibited)
    monkeypatch.setattr(os, "open", prohibited)
    monkeypatch.setattr(Path, "stat", prohibited)
    monkeypatch.setattr(Path, "lstat", prohibited)
    monkeypatch.setattr(os, "environ", {})
    assert artifacts.validate_bound(receipt, producer=PRODUCER, creation=CREATION) == receipt
    assert (
        artifacts.public_projection(receipt, producer=PRODUCER, creation=CREATION)["retained_files"]
        == "not_checked"
    )


@pytest.mark.parametrize(
    "change", ["private", "version", "local_version", "package", "bool", "path", "device", "recipe"]
)
def test_v144_environment_allowlist_is_strict(owned, change):
    environment = deepcopy(ENVIRONMENT)
    if change == "private":
        environment["environment_variables"] = {"HF_TOKEN": "synthetic-secret"}
    elif change == "version":
        environment["python"]["version"] = "python C:/private/python.exe"
    elif change == "local_version":
        environment["packages"]["torch"] = "2.8.0+hf_synthetic_secret"
    elif change == "package":
        environment["packages"]["private-customer-package"] = "1.0"
    elif change == "bool":
        environment["device"]["memory_bytes"] = True
    elif change == "path":
        environment["code"]["worker_sha256"] = "C:/private/worker.py"
    elif change == "device":
        environment["device"]["type"] = "auto"
    else:
        environment["recipe"]["id"] = "arbitrary private command"
    with pytest.raises(ValueError, match="artifact evidence"):
        capture(owned, role="environment", data=json.dumps(environment).encode())


def test_v144_environment_gaps_are_explicit_and_do_not_use_local_machine(owned):
    environment = {key: None for key in ENVIRONMENT if key != "format"}
    environment["format"] = artifacts.ENVIRONMENT_FORMAT
    observed, _ = capture(owned, role="environment", data=json.dumps(environment).encode())
    assert observed.receipt()["environment"] == environment


@pytest.mark.parametrize(
    "section,key",
    [
        ("python", "implementation"),
        ("platform", "system"),
        ("platform", "machine"),
        ("device", "type"),
        ("recipe", "id"),
        ("recipe", "compute_type"),
    ],
)
def test_v144_unhashable_environment_fields_have_safe_validation_errors(section, key):
    environment = deepcopy(ENVIRONMENT)
    environment[section][key] = {"private": "synthetic-secret"}
    with pytest.raises(ValueError, match="artifact evidence"):
        artifacts.validate_environment(environment)


@pytest.mark.parametrize(
    "data", [b"", b"not JSON", b'{"format":null,"format":null}', b"\xff", b"[" * 1500]
)
def test_v144_invalid_environment_json_is_refused(owned, data):
    with pytest.raises(ValueError, match="artifact evidence"):
        capture(owned, role="environment", data=data)


@pytest.mark.parametrize("kind", ["outside", "traversal", "directory", "missing", "hardlink"])
def test_v144_capture_requires_owned_regular_single_link_file(owned, tmp_path, kind):
    target = owned / "output.json"
    target.write_bytes(b"{}")
    if kind == "outside":
        target = tmp_path / "outside.json"
        target.write_bytes(b"{}")
    elif kind == "traversal":
        target = owned / ".." / owned.name / "output.json"
    elif kind == "directory":
        target = owned
    elif kind == "missing":
        target.unlink()
    else:
        os.link(target, owned / "other-hardlink")
    with pytest.raises(ValueError, match="artifact evidence") as error:
        artifacts.capture_file(target, owned, role="output", format="json")
    assert str(target) not in str(error.value)


@pytest.mark.parametrize("location", ["root", "directory", "file"])
def test_v144_capture_refuses_symlinks(owned, tmp_path, location):
    real = tmp_path / "real"
    real.mkdir()
    target = real / "output.json"
    target.write_bytes(b"{}")
    root = owned
    try:
        if location == "root":
            root = tmp_path / "root-link"
            root.symlink_to(real, target_is_directory=True)
            path = root / "output.json"
        elif location == "directory":
            (owned / "child").symlink_to(real, target_is_directory=True)
            path = owned / "child" / "output.json"
        else:
            path = owned / "output.json"
            path.symlink_to(target)
    except OSError:
        pytest.skip("This account cannot create a temporary symlink")
    with pytest.raises(ValueError, match="artifact evidence"):
        artifacts.capture_file(path, root, role="output", format="json")


@pytest.mark.parametrize("location", ["root", "directory"])
def test_v144_capture_refuses_windows_junctions(owned, tmp_path, location):
    if os.name != "nt":
        pytest.skip("Windows junction acceptance")
    real = tmp_path / "real"
    real.mkdir()
    (real / "output.json").write_bytes(b"{}")
    link = tmp_path / "root-link" if location == "root" else owned / "child"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(real)], capture_output=True
    )
    if completed.returncode:
        pytest.skip("This account cannot create a temporary junction")
    root = link if location == "root" else owned
    with pytest.raises(ValueError, match="artifact evidence"):
        artifacts.capture_file(link / "output.json", root, role="output", format="json")


def test_v144_oversize_file_is_rejected_before_opening(owned, monkeypatch):
    target = owned / "output.json"
    target.write_bytes(b"123456")
    monkeypatch.setitem(artifacts.LIMITS, "output", 5)

    def prohibited(*args, **kwargs):
        pytest.fail("An oversized artifact was opened")

    monkeypatch.setattr(Path, "open", prohibited)
    monkeypatch.setattr(os, "open", prohibited)
    with pytest.raises(ValueError, match="artifact evidence"):
        artifacts.capture_file(target, owned, role="output", format="json")


def test_v144_read_growth_stops_at_original_size_plus_sentinel(owned, monkeypatch):
    target = owned / "output.json"
    target.write_bytes(b"Original")
    original_fdopen = os.fdopen
    reads = []

    class Growing:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, amount):
            reads.append(amount)
            with target.open("ab") as writer:
                writer.write(b"x" * 100)
            return self.stream.read(amount)

    monkeypatch.setattr(
        artifacts.os, "fdopen", lambda *args, **kwargs: Growing(original_fdopen(*args, **kwargs))
    )
    with pytest.raises(ValueError, match="artifact evidence"):
        artifacts.capture_file(target, owned, role="output", format="json")
    assert reads == [len(b"Original") + 1]


@pytest.mark.parametrize("change", ["grow", "shrink", "replace", "mtime"])
def test_v144_post_read_drift_refuses_capture(owned, monkeypatch, change):
    target = owned / "output.json"
    target.write_bytes(b"Original")
    original_fstat = os.fstat
    calls = 0

    def change_file(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == "grow":
                target.write_bytes(b"Original and more")
            elif change == "shrink":
                target.write_bytes(b"Ori")
            elif change == "replace":
                # Replace after the read is closed on Windows in the lstat hook below.
                pass
            else:
                info = target.stat()
                os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000000))
        return original_fstat(descriptor)

    original_lstat = Path.lstat

    def replace_file(path, *args, **kwargs):
        if (
            change == "replace"
            and path == target
            and calls >= 2
            and target.read_bytes() == b"Original"
        ):
            replacement = owned / "replacement.json"
            replacement.write_bytes(b"Replaced")
            os.replace(replacement, target)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, "fstat", change_file)
    monkeypatch.setattr(Path, "lstat", replace_file)
    with pytest.raises(ValueError, match="artifact evidence"):
        artifacts.capture_file(target, owned, role="output", format="json")


def test_v144_path_and_descriptor_ctime_are_compared_separately(owned, monkeypatch):
    original_fstat = os.fstat

    def different_ctime(descriptor):
        info = original_fstat(descriptor)
        return SimpleNamespace(
            **{
                key: getattr(info, key)
                for key in ("st_mode", "st_nlink", "st_dev", "st_ino", "st_size", "st_mtime_ns")
            },
            st_ctime_ns=info.st_ctime_ns + 1000000000,
        )

    monkeypatch.setattr(artifacts.os, "fstat", different_ctime)
    observed, _ = capture(owned)
    assert observed.receipt()["bytes"] == len(b"Private synthetic words")


@pytest.mark.parametrize("stage", ["allocation", "read", "close"])
def test_v144_stream_wrapper_failure_closes_the_owned_descriptor(owned, monkeypatch, stage):
    target = owned / "output.json"
    target.write_bytes(b"{}")
    opened = []
    original_open = os.open
    original_fdopen = os.fdopen

    def remember(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    class FailedStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()
            if stage == "close":
                raise OSError("Synthetic private stream close failure")

        def fileno(self):
            return self.stream.fileno()

        def read(self, amount):
            if stage == "read":
                raise OSError("Synthetic private stream read failure")
            return self.stream.read(amount)

    def fail_wrapper(*args, **kwargs):
        if stage == "allocation":
            raise OSError("Synthetic private stream allocation failure")
        return FailedStream(original_fdopen(*args, **kwargs))

    monkeypatch.setattr(artifacts.os, "open", remember)
    monkeypatch.setattr(artifacts.os, "fdopen", fail_wrapper)
    try:
        with pytest.raises(ValueError, match="artifact evidence") as error:
            artifacts.capture_file(target, owned, role="output", format="json")
        assert "private" not in str(error.value)
        assert error.value.__suppress_context__
        assert len(opened) == 1
        with pytest.raises(OSError):
            os.fstat(opened[0])
    finally:
        for descriptor in opened:
            try:
                os.close(descriptor)
            except OSError:
                pass


def test_v144_parent_replacement_is_refused_even_when_file_is_retained(owned, monkeypatch):
    directory = owned / "stage"
    directory.mkdir()
    target = directory / "output.json"
    target.write_bytes(b"Retained unchanged bytes")
    original_lstat, original_fstat = Path.lstat, os.fstat
    reads_done, changed = False, False
    descriptor_checks = 0

    def counted(descriptor):
        nonlocal reads_done, descriptor_checks
        descriptor_checks += 1
        if descriptor_checks == 2:
            reads_done = True
        return original_fstat(descriptor)

    def replaced(path, *args, **kwargs):
        nonlocal changed
        if path == target and reads_done and not changed:
            changed = True
            old = owned / "retained-old-stage"
            directory.rename(old)
            directory.mkdir()
            (old / "output.json").rename(target)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, "fstat", counted)
    monkeypatch.setattr(Path, "lstat", replaced)
    with pytest.raises(ValueError, match="artifact evidence"):
        artifacts.capture_file(target, owned, role="output", format="json")
    assert target.read_bytes() == b"Retained unchanged bytes"
