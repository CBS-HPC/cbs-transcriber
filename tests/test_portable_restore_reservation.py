"""A restore target becomes visible only with its bound incomplete-restore marker."""

import pytest
from test_portable_restore import apply, destination
from test_portable_restore import package as package

from cbs_transcriber import deletion_files
from cbs_transcriber import portable_restore as restore
from cbs_transcriber.store import Store


@pytest.mark.parametrize("written", [None, 0, 7, 32])
def test_marker_failure_stays_in_staging_and_resumes(package, tmp_path, monkeypatch, written):
    target = destination(tmp_path, "marker-failure")
    operation_id = "4" * 32
    durable, failed = restore.durable, False

    def interrupt(path, raw):
        nonlocal failed
        if path.name == "restore-pending" and not failed:
            failed = True
            if written is not None:
                with path.open("xb") as stream:
                    stream.write(raw[:written])
            raise OSError("synthetic marker interruption")
        return durable(path, raw)

    monkeypatch.setattr(restore, "durable", interrupt)
    with pytest.raises(OSError, match="synthetic"):
        apply(package, target, operation_id=operation_id)
    assert not target.exists(), "An unmarked incomplete target became visible"
    staged = target.parent / (".restore-" + operation_id + ".partial")
    assert (staged / "library" / "library.sqlite3").is_file()
    resumed = apply(package, target, operation_id=operation_id)
    assert resumed["state"] == "complete"
    assert Store(target).recording(package[2]["id"])["status"] == "preparing"
    assert not (target / "restore-pending").exists()


@pytest.mark.parametrize("published", [False, True])
def test_reservation_publish_interruption_preserves_binding_and_resumes(
    package, tmp_path, monkeypatch, published
):
    target = destination(tmp_path, "publish-interruption")
    operation_id = "5" * 32
    rename, failed = deletion_files._rename, False

    def interrupt(source, destination):
        nonlocal failed
        if source.name == "reservation" and not failed:
            failed = True
            if published:
                rename(source, destination)
            raise OSError("synthetic publication interruption")
        return rename(source, destination)

    monkeypatch.setattr(deletion_files, "_rename", interrupt)
    with pytest.raises(OSError, match="synthetic"):
        apply(package, target, operation_id=operation_id)
    assert target.exists() is published
    if published:
        assert (target / "restore-pending").read_bytes() == operation_id.encode("ascii")
        with pytest.raises(RuntimeError, match="incomplete"):
            Store(target)
    result = apply(package, target, operation_id=operation_id)
    assert result["state"] == "complete"


@pytest.mark.parametrize("existing", [None, "unrelated.txt", "restore-pending", "exact-marker"])
def test_reservation_never_replaces_a_racing_destination(package, tmp_path, monkeypatch, existing):
    target = destination(tmp_path, "racing-owner")
    operation_id = "6" * 32
    rename = deletion_files._rename
    filename = "restore-pending" if existing == "exact-marker" else existing
    body = (
        operation_id.encode("ascii")
        if existing == "exact-marker"
        else b""
        if existing == "restore-pending"
        else b"keep this owner"
    )

    def race(source, destination):
        if source.name == "reservation":
            destination.mkdir()
            if filename:
                (destination / filename).write_bytes(body)
        return rename(source, destination)

    monkeypatch.setattr(deletion_files, "_rename", race)
    with pytest.raises(OSError):
        apply(package, target, operation_id=operation_id)
    assert {path.name for path in target.iterdir()} == ({filename} if filename else set())
    staged = target.parent / (".restore-" + operation_id + ".partial")
    assert (staged / "reservation" / "restore-pending").is_file()
    assert (staged / "library" / "library.sqlite3").is_file()
    # The retained reservation proves this operation did not publish the destination.
    # Even the exact known marker cannot grant ownership of that pre-existing target.
    with pytest.raises(ValueError):
        apply(package, target, operation_id=operation_id)
    if filename:
        assert (target / filename).read_bytes() == body
    assert {path.name for path in target.iterdir()} == ({filename} if filename else set())
    assert (staged / "reservation" / "restore-pending").read_bytes() == operation_id.encode("ascii")
    assert (staged / "library" / "library.sqlite3").is_file()


@pytest.mark.parametrize("contents", [b"wrong", b"f" * 32, b"4" * 33])
def test_unpublished_reservation_refuses_foreign_marker(tmp_path, contents):
    staging, target = tmp_path / (".restore-" + "4" * 32 + ".partial"), tmp_path / "target"
    reservation = staging / "reservation"
    reservation.mkdir(parents=True)
    marker = reservation / "restore-pending"
    marker.write_bytes(contents)
    with pytest.raises(ValueError):
        restore._reserve_target(staging, target, "4" * 32, None)
    assert marker.read_bytes() == contents
    assert not target.exists()


def test_unpublished_reservation_refuses_unexpected_files(tmp_path):
    staging, target = tmp_path / (".restore-" + "4" * 32 + ".partial"), tmp_path / "target"
    reservation = staging / "reservation"
    reservation.mkdir(parents=True)
    (reservation / "unrelated.txt").write_text("retain", encoding="utf-8")
    with pytest.raises(ValueError):
        restore._reserve_target(staging, target, "4" * 32, None)
    assert (reservation / "unrelated.txt").read_text(encoding="utf-8") == "retain"
    assert not target.exists()


def test_unpublished_reservation_refuses_link(tmp_path):
    staging = tmp_path / (".restore-" + "4" * 32 + ".partial")
    staging.mkdir()
    target, outsider = tmp_path / "target", tmp_path / "outsider"
    outsider.mkdir()
    try:
        (staging / "reservation").symlink_to(outsider, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks needs platform permissions.")
    with pytest.raises(ValueError):
        restore._reserve_target(staging, target, "4" * 32, None)
    assert list(outsider.iterdir()) == []
    assert not target.exists()


def test_reservation_cannot_repair_another_operation_staging(tmp_path):
    staging, target = tmp_path / (".restore-" + "5" * 32 + ".partial"), tmp_path / "target"
    reservation = staging / "reservation"
    reservation.mkdir(parents=True)
    marker = reservation / "restore-pending"
    marker.write_bytes(b"4")
    with pytest.raises(ValueError, match="another operation"):
        restore._reserve_target(staging, target, "4" * 32, None)
    assert marker.read_bytes() == b"4"
    assert not target.exists()
