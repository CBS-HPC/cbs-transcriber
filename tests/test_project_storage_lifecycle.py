"""Synthetic move/switch and destination-loss cases, without background processing."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from cbs_transcriber import libraries, storage
from cbs_transcriber.store import Store


def recording(store):
    project = store.create_project("Synthetic movement")
    item = store.create_recording(project["id"], "synthetic.wav")
    source = store.directory(item["id"])
    source.mkdir(parents=True)
    (source / "original.wav").write_bytes(b"SYNTHETIC IMMUTABLE ORIGINAL")
    return project, item, source


def test_library_switch_waits_for_project_move_and_retains_committed_original(
    client, tmp_path, monkeypatch
):
    store = client.app.state.store
    project, item, source = recording(store)
    target = tmp_path / "other-library"
    Store(target)
    chosen = []
    client.app.state.switch_library = chosen.append
    copying, release, selecting = Event(), Event(), Event()
    copytree, select = storage.shutil.copytree, libraries.select

    def hold_copy(src, dst, **kwargs):
        if Path(src) == source:
            copying.set()
            assert release.wait(5)
        return copytree(src, dst, **kwargs)

    def observed_select(*args, **kwargs):
        result = select(*args, **kwargs)
        if result.path == target:
            selecting.set()
        return result

    monkeypatch.setattr(storage.shutil, "copytree", hold_copy)
    monkeypatch.setattr(libraries, "select", observed_select)
    with ThreadPoolExecutor(max_workers=2) as pool:
        moving = pool.submit(
            client.post,
            f"/api/projects/{project['id']}/storage",
            json={"storage_parent": str(tmp_path / "moved")},
        )
        try:
            assert copying.wait(5)
            switching = pool.submit(client.post, "/api/libraries/open", json={"path": str(target)})
            assert selecting.wait(5)
            assert not chosen
        finally:
            release.set()
        moved = moving.result(5)
        switched = switching.result(5)
    assert moved.status_code == 200, moved.text
    assert switched.status_code == 202, switched.text
    assert [entry.path for entry in chosen] == [target]
    assert (
        store.directory(item["id"]) / "original.wav"
    ).read_bytes() == b"SYNTHETIC IMMUTABLE ORIGINAL"
    assert not source.exists()
    assert store.root == tmp_path


@pytest.mark.parametrize("replacement", [False, True])
def test_destination_disappearing_after_copy_retains_original_storage(
    client, tmp_path, monkeypatch, replacement
):
    store = client.app.state.store
    project, item, source = recording(store)
    original = store.project_folder(project["id"])
    parent = tmp_path / "destination"
    target = parent / project["id"][:8]
    preserved = tmp_path / "detached-copy"
    copytree = storage.shutil.copytree

    def destination_disappears(src, dst, **kwargs):
        result = copytree(src, dst, **kwargs)
        if Path(src) == source:
            target.rename(preserved)  # Keep copied bytes; simulate lost destination after I/O.
            if replacement:
                target.mkdir()
                (target / "unrelated.txt").write_text("Unrelated synthetic replacement")
        return result

    monkeypatch.setattr(storage.shutil, "copytree", destination_disappears)
    response = client.post(
        f"/api/projects/{project['id']}/storage", json={"storage_parent": str(parent)}
    )
    assert source.exists(), {
        "status": response.status_code,
        "active_original_exists": (store.directory(item["id"]) / "original.wav").exists(),
        "preserved_copy_exists": (preserved / "recordings" / item["id"] / "original.wav").exists(),
    }
    assert response.status_code == 400, response.text
    assert store.project_folder(project["id"]) == original
    assert (source / "original.wav").read_bytes() == b"SYNTHETIC IMMUTABLE ORIGINAL"
    assert (
        preserved / "recordings" / item["id"] / "original.wav"
    ).read_bytes() == b"SYNTHETIC IMMUTABLE ORIGINAL"
    if replacement:
        assert [p.name for p in target.iterdir()] == ["unrelated.txt"]
        assert (target / "unrelated.txt").read_text() == "Unrelated synthetic replacement"


def test_destination_lost_after_commit_retains_originals_and_reports_committed_location(
    client, tmp_path, monkeypatch
):
    store = client.app.state.store
    project, item, source = recording(store)
    parent = tmp_path / "destination"
    target = parent / project["id"][:8]
    preserved = tmp_path / "detached-copy"
    execute = store.execute

    def commit_then_lose_destination(sql, args=()):
        result = execute(sql, args)
        if sql.startswith("UPDATE projects SET storage_dir"):
            target.rename(preserved)
        return result

    monkeypatch.setattr(store, "execute", commit_then_lose_destination)
    body = {"storage_parent": str(parent)}
    response = client.post(f"/api/projects/{project['id']}/storage", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["cleanup_warning"]
    assert store.project_folder(project["id"]) == target
    assert (source / "original.wav").read_bytes() == b"SYNTHETIC IMMUTABLE ORIGINAL"
    assert not target.exists()  # Snapshot writing must not recreate an empty destination.
    assert (
        preserved / "recordings" / item["id"] / "original.wav"
    ).read_bytes() == b"SYNTHETIC IMMUTABLE ORIGINAL"


def test_destination_permission_failure_preserves_originals(client, tmp_path, monkeypatch):
    store = client.app.state.store
    project, item, source = recording(store)
    original = store.project_folder(project["id"])
    parent = tmp_path / "denied"
    target = parent / project["id"][:8]
    mkdir = Path.mkdir

    def denied(path, *args, **kwargs):
        if path == target:
            raise PermissionError("Synthetic destination permission denied")
        return mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", denied)
    response = client.post(
        f"/api/projects/{project['id']}/storage", json={"storage_parent": str(parent)}
    )
    assert response.status_code == 400, response.text
    assert store.project_folder(project["id"]) == original
    assert store.directory(item["id"]) == source
    assert (source / "original.wav").read_bytes() == b"SYNTHETIC IMMUTABLE ORIGINAL"
    assert not target.exists()


def test_retry_after_uncertain_move_commit_does_not_move_or_remove_either_copy(
    client, tmp_path, monkeypatch
):
    store = client.app.state.store
    project, item, source = recording(store)
    parent = tmp_path / "destination"
    before = store.one("SELECT * FROM recordings WHERE id=?", (item["id"],))
    execute, uncertain = store.execute, False

    def committed_reply_lost(sql, args=()):
        nonlocal uncertain
        result = execute(sql, args)
        if sql.startswith("UPDATE projects SET storage_dir") and not uncertain:
            uncertain = True
            raise OSError("Synthetic lost commit acknowledgement")
        return result

    monkeypatch.setattr(store, "execute", committed_reply_lost)
    endpoint, body = f"/api/projects/{project['id']}/storage", {"storage_parent": str(parent)}
    first = client.post(endpoint, json=body)
    assert first.status_code == 200 and first.json()["cleanup_warning"]
    active = store.directory(item["id"])
    second = client.post(endpoint, json=body)
    assert second.status_code == 200, second.text
    assert store.directory(item["id"]) == active
    assert [path.name for path in parent.iterdir()] == [project["id"][:8]]
    assert (source / "original.wav").read_bytes() == b"SYNTHETIC IMMUTABLE ORIGINAL"
    assert (active / "original.wav").read_bytes() == b"SYNTHETIC IMMUTABLE ORIGINAL"
    assert store.one("SELECT * FROM recordings WHERE id=?", (item["id"],)) == before


@pytest.mark.parametrize(
    "kind",
    ["directory-missing", "directory-replaced", "file-missing", "file-replaced", "file-mutated"],
)
def test_copied_child_change_under_unchanged_target_retains_originals(
    client, tmp_path, monkeypatch, kind
):
    store = client.app.state.store
    project, item, source = recording(store)
    original = store.project_folder(project["id"])
    parent = tmp_path / "destination"
    target = parent / project["id"][:8]
    preserved = tmp_path / "preserved-copy"
    copytree = storage.shutil.copytree

    def child_changes(src, dst, **kwargs):
        result = copytree(src, dst, **kwargs)
        if Path(src) == source:
            path = Path(dst) if kind.startswith("directory") else Path(dst) / "original.wav"
            if kind == "file-mutated":
                before = path.stat()
                path.write_bytes(b"X" * before.st_size)
                storage.os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            else:
                path.rename(preserved)
                if kind.endswith("replaced"):
                    if kind.startswith("directory"):
                        path.mkdir()
                        (path / "unrelated.txt").write_bytes(b"FOREIGN")
                    else:
                        path.write_bytes(b"FOREIGN")
        return result

    monkeypatch.setattr(storage.shutil, "copytree", child_changes)
    response = client.post(
        f"/api/projects/{project['id']}/storage", json={"storage_parent": str(parent)}
    )
    assert response.status_code == 400, response.text
    assert store.project_folder(project["id"]) == original
    assert (source / "original.wav").read_bytes() == b"SYNTHETIC IMMUTABLE ORIGINAL"
    if kind.endswith("replaced"):
        foreign = target / "recordings" / item["id"]
        foreign = foreign / ("unrelated.txt" if kind.startswith("directory") else "original.wav")
        assert foreign.read_bytes() == b"FOREIGN"
    if kind != "file-mutated":
        saved = preserved / "original.wav" if kind.startswith("directory") else preserved
        assert saved.read_bytes() == b"SYNTHETIC IMMUTABLE ORIGINAL"


def test_file_inserted_before_copy_is_never_overwritten_or_cleaned(client, tmp_path, monkeypatch):
    store = client.app.state.store
    project, item, source = recording(store)
    parent = tmp_path / "destination"
    target = parent / project["id"][:8]
    copytree = storage.shutil.copytree

    def insert_foreign(src, dst, **kwargs):
        if Path(src) == source:
            (Path(dst) / "original.wav").write_bytes(b"FOREIGN MUST REMAIN")
        return copytree(src, dst, **kwargs)

    monkeypatch.setattr(storage.shutil, "copytree", insert_foreign)
    response = client.post(
        f"/api/projects/{project['id']}/storage", json={"storage_parent": str(parent)}
    )
    assert response.status_code == 400, response.text
    assert (
        target / "recordings" / item["id"] / "original.wav"
    ).read_bytes() == b"FOREIGN MUST REMAIN"
    assert (source / "original.wav").read_bytes() == b"SYNTHETIC IMMUTABLE ORIGINAL"


def test_first_copied_recording_lost_during_later_copy_retains_both_originals(
    client, tmp_path, monkeypatch
):
    store = client.app.state.store
    project, first, source = recording(store)
    second = store.create_recording(project["id"], "second.wav")
    other = store.directory(second["id"])
    other.mkdir(parents=True)
    (other / "original.wav").write_bytes(b"SECOND SYNTHETIC ORIGINAL")
    original = store.project_folder(project["id"])
    copied = []
    copytree = storage.shutil.copytree

    def later_copy(src, dst, **kwargs):
        result = copytree(src, dst, **kwargs)
        if Path(src) in {source, other}:
            copied.append(Path(dst))
            if len(copied) == 2:
                copied[0].rename(tmp_path / "preserved-first-copy")
        return result

    monkeypatch.setattr(storage.shutil, "copytree", later_copy)
    response = client.post(
        f"/api/projects/{project['id']}/storage",
        json={"storage_parent": str(tmp_path / "destination")},
    )
    assert response.status_code == 400, response.text
    assert len(copied) == 2
    assert store.project_folder(project["id"]) == original
    assert (source / "original.wav").read_bytes() == b"SYNTHETIC IMMUTABLE ORIGINAL"
    assert (other / "original.wav").read_bytes() == b"SECOND SYNTHETIC ORIGINAL"


@pytest.mark.parametrize("link", ["hard", "symbolic"])
def test_unowned_link_replacing_copied_file_is_retained_without_touching_its_body(
    client, tmp_path, monkeypatch, link
):
    store = client.app.state.store
    project, item, source = recording(store)
    foreign = tmp_path / "foreign.wav"
    foreign.write_bytes(b"UNRELATED SYNTHETIC BYTES")
    if link == "symbolic":
        probe = tmp_path / "symbolic-permission-probe"
        try:
            probe.symlink_to(foreign)
        except OSError:
            pytest.skip("Symbolic link creation requires platform permission")
        probe.unlink()
    parent = tmp_path / "destination"
    path = parent / project["id"][:8] / "recordings" / item["id"] / "original.wav"
    copytree = storage.shutil.copytree

    def replace_with_link(src, dst, **kwargs):
        result = copytree(src, dst, **kwargs)
        if Path(src) == source:
            path.rename(tmp_path / "preserved-original.wav")
            if link == "hard":
                storage.os.link(foreign, path)
            else:
                path.symlink_to(foreign)
        return result

    monkeypatch.setattr(storage.shutil, "copytree", replace_with_link)
    response = client.post(
        f"/api/projects/{project['id']}/storage", json={"storage_parent": str(parent)}
    )
    assert response.status_code == 400, response.text
    assert foreign.read_bytes() == b"UNRELATED SYNTHETIC BYTES"
    assert path.exists() or path.is_symlink()
    assert (source / "original.wav").read_bytes() == b"SYNTHETIC IMMUTABLE ORIGINAL"


def test_successful_move_preserves_nested_checkpoints_empty_directories_and_source_hardlinks(
    client, tmp_path
):
    store = client.app.state.store
    project, item, source = recording(store)
    deep = source / "enhancements" / "synthetic" / "passages"
    deep.mkdir(parents=True)
    (deep / "checkpoint.json").write_bytes(b'{"text":"synthetic"}')
    (deep / "empty").mkdir()
    storage.os.link(source / "original.wav", deep / "source-copy.wav")
    response = client.post(
        f"/api/projects/{project['id']}/storage",
        json={"storage_parent": str(tmp_path / "destination")},
    )
    assert response.status_code == 200, response.text
    assert not response.json()["cleanup_warning"]
    moved = store.directory(item["id"])
    target = moved / "enhancements" / "synthetic" / "passages"
    assert (target / "checkpoint.json").read_bytes() == b'{"text":"synthetic"}'
    assert (target / "source-copy.wav").read_bytes() == b"SYNTHETIC IMMUTABLE ORIGINAL"
    assert (target / "empty").is_dir()
    assert not source.exists()


def reparse_metadata(monkeypatch, paths, tag):
    lstat = Path.lstat

    def observed(path, *args, **kwargs):
        info = lstat(path, *args, **kwargs)
        if path in paths:
            attributes = {
                name: getattr(info, name)
                for name in (
                    "st_mode",
                    "st_dev",
                    "st_ino",
                    "st_size",
                    "st_mtime_ns",
                    "st_atime_ns",
                    "st_ctime_ns",
                    "st_nlink",
                )
            }
            return SimpleNamespace(**attributes, st_file_attributes=0x400, st_reparse_tag=tag)
        return info

    monkeypatch.setattr(Path, "lstat", observed)


@pytest.mark.parametrize("tag", [0x9000001A, 0x9000101A, 0x9000F01A])
def test_known_cloud_placeholders_remain_movable_with_verified_bytes(
    client, tmp_path, monkeypatch, tag
):
    store = client.app.state.store
    project, item, source = recording(store)
    reparse_metadata(monkeypatch, {source, source / "original.wav"}, tag)
    response = client.post(
        f"/api/projects/{project['id']}/storage",
        json={"storage_parent": str(tmp_path / "destination")},
    )
    assert response.status_code == 200, response.text
    assert not response.json()["cleanup_warning"]
    assert (
        store.directory(item["id"]) / "original.wav"
    ).read_bytes() == b"SYNTHETIC IMMUTABLE ORIGINAL"
    assert not source.exists()


@pytest.mark.parametrize("tag", [None, 0x9001001A, 0xA0000003, 0xA000000C, 0xB000001A])
def test_unknown_or_redirecting_reparse_tags_refuse_move_and_retain_originals(
    client, tmp_path, monkeypatch, tag
):
    store = client.app.state.store
    project, item, source = recording(store)
    original = store.project_folder(project["id"])
    reparse_metadata(monkeypatch, {source / "original.wav"}, tag)
    response = client.post(
        f"/api/projects/{project['id']}/storage",
        json={"storage_parent": str(tmp_path / "destination")},
    )
    assert response.status_code == 400, response.text
    assert store.project_folder(project["id"]) == original
    assert (source / "original.wav").read_bytes() == b"SYNTHETIC IMMUTABLE ORIGINAL"
