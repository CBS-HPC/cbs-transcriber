"""Native folder selection; the browser never needs filesystem permissions."""

import hashlib
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def _move_node(path):
    from .deletion_files import _redirect

    info = path.lstat()
    if _redirect(info) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise ValueError("Project move files changed or contain filesystem links.")
    return info


def _move_signature(info):
    # Windows path/descriptor ctime may differ on an unchanged file; hashes bind its bytes.
    identity = info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)
    return (
        identity
        if stat.S_ISDIR(info.st_mode)
        else identity + (info.st_size, info.st_mtime_ns, info.st_nlink)
    )


def _move_tree(root):
    nodes, pending = {}, [root]
    while pending:
        path = pending.pop()
        info = nodes[path] = _move_node(path)
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as entries:
                pending.extend(path / entry.name for entry in entries)
    return nodes


def _move_digest(path, expected):
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(os.open(path, flags), "rb") as incoming:
        if _move_signature(os.fstat(incoming.fileno())) != _move_signature(expected):
            raise ValueError("Project move file changed while opening it.")
        digest, count = hashlib.sha256(), 0
        while block := incoming.read(min(4 * 1024 * 1024, expected.st_size - count + 1)):
            count += len(block)
            if count > expected.st_size:
                raise ValueError("Project move file grew while reading it.")
            digest.update(block)
        if count != expected.st_size or _move_signature(
            os.fstat(incoming.fileno())
        ) != _move_signature(expected):
            raise ValueError("Project move file changed while reading it.")
    if _move_signature(_move_node(path)) != _move_signature(expected):
        raise ValueError("Project move file path changed while reading it.")
    return digest.hexdigest()


def _verify_move_tree(root, nodes, hashes=None):
    expected = {p: info for p, info in nodes.items() if p.is_relative_to(root)}
    current = _move_tree(root)
    if current.keys() != expected.keys():
        raise ValueError("Project move file membership changed. Original files are retained.")
    for path, info in current.items():
        if _move_signature(info) != _move_signature(expected[path]):
            raise ValueError("Project move file identity changed. Original files are retained.")
        if hashes is not None and stat.S_ISREG(info.st_mode):
            if path not in hashes or _move_digest(path, info) != hashes[path]:
                raise ValueError(
                    "Project move copied content changed. Original files are retained."
                )


def relocate_project(store, identifier, storage_parent):
    """Copy managed paths before switching SQLite; caller holds queue and snapshot locks."""
    project = store.one("SELECT * FROM projects WHERE id=?", (identifier,))
    parent = Path(storage_parent).expanduser()
    if not parent.is_absolute():
        raise ValueError("Choose an absolute project storage folder.")
    parent = parent.resolve()
    target = parent / identifier[:8]
    original = store.project_folder(identifier).resolve()
    original_node = _move_node(original)
    old_metadata = original / "project.json"
    old_metadata_node = _move_node(old_metadata) if old_metadata.exists() else None
    old_metadata_hash = _move_digest(old_metadata, old_metadata_node) if old_metadata_node else None
    if target == original:
        return {"path": str(original), "cleanup_warning": None}
    if project["storage_dir"] and target.is_relative_to(original):
        raise ValueError("Choose a location outside the project's current folder.")
    rows = store.all("SELECT id FROM recordings WHERE project_id=?", (identifier,))
    sources = []
    for row in rows:
        for path in (store.directory(row["id"]), original / "exports" / row["id"]):
            if not path.exists():
                if path.parent.name == "recordings":
                    raise ValueError(
                        "Recording files are missing. Restore them before moving storage."
                    )
                continue
            resolved = path.resolve()
            if resolved == original or not resolved.is_relative_to(original):
                raise ValueError("Project files must stay inside their managed storage.")
            if target.is_relative_to(resolved):
                raise ValueError("Choose a location outside the recording and export folders.")
            sources.append(path)

    try:
        target.mkdir(parents=True)
        created = target.lstat()
    except FileExistsError as exc:
        raise ValueError(
            "A project folder already exists there. Choose another parent folder."
        ) from exc
    except OSError as exc:
        raise ValueError(
            "Cannot create the destination. Check its location and permissions."
        ) from exc

    def target_owned():
        try:
            current = target.lstat()
            return (
                stat.S_ISDIR(current.st_mode)
                and not target.is_symlink()
                and not target.is_junction()
                and (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino)
            )
        except OSError:
            return False

    source_nodes, copied_nodes = {}, {target: created}
    source_trees, copied_trees, children = {}, {}, {}
    hashes = {}

    def verify_parents(path):
        while path != target.parent:
            if path not in copied_nodes or _move_signature(_move_node(path)) != _move_signature(
                copied_nodes[path]
            ):
                raise ValueError("The move destination directory changed.")
            path = path.parent

    def copy_file(src, dst):
        src, dst = Path(src), Path(dst)
        expected = source_nodes[src]
        verify_parents(dst.parent)
        if _move_signature(_move_node(src)) != _move_signature(expected):
            raise ValueError("Project move source file changed.")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        with os.fdopen(os.open(src, flags), "rb") as incoming, dst.open("xb") as output:
            if _move_signature(os.fstat(incoming.fileno())) != _move_signature(expected):
                raise ValueError("Project move source file changed while opening it.")
            copied_nodes[dst] = os.fstat(output.fileno())
            digest, count = hashlib.sha256(), 0
            while block := incoming.read(min(4 * 1024 * 1024, expected.st_size - count + 1)):
                count += len(block)
                if count > expected.st_size:
                    raise ValueError("Project move source file grew while copying it.")
                output.write(block)
                digest.update(block)
            if count != expected.st_size or _move_signature(
                os.fstat(incoming.fileno())
            ) != _move_signature(expected):
                raise ValueError("Project move source file changed while copying it.")
        verify_parents(dst.parent)
        expected_id = copied_nodes[dst].st_dev, copied_nodes[dst].st_ino
        actual = _move_node(dst)
        if (actual.st_dev, actual.st_ino) != expected_id:
            raise ValueError("Project move copied file was replaced.")
        if _move_signature(_move_node(src)) != _move_signature(expected):
            raise ValueError("Project move source path changed before metadata copy.")
        shutil.copystat(src, dst, follow_symlinks=False)
        actual = _move_node(dst)
        if (actual.st_dev, actual.st_ino) != expected_id:
            raise ValueError("Project move copied file was replaced during metadata copy.")
        copied_nodes[dst] = actual
        hashes[src] = hashes[dst] = digest.hexdigest()
        return str(dst)

    def reject_links(directory, names):
        directory = Path(directory)
        expected = children.get(directory, set())
        if set(names) != expected or _move_signature(_move_node(directory)) != _move_signature(
            source_nodes[directory]
        ):
            raise ValueError("Project move source directory changed.")
        verify_parents(target / directory.relative_to(original))
        return []

    try:
        for source in sources:
            source_trees[source] = _move_tree(source)
            source_nodes.update(source_trees[source])
        for path in source_nodes:
            children.setdefault(path.parent, set()).add(path.name)
        for source, info in source_nodes.items():
            if not stat.S_ISDIR(info.st_mode):
                continue
            destination = target / source.relative_to(original)
            for folder in reversed((destination, *destination.parents)):
                if folder == target or folder.is_relative_to(target):
                    if folder not in copied_nodes:
                        folder.mkdir()  # Exclusive: an unexpected directory is never adopted.
                        copied_nodes[folder] = _move_node(folder)
        for source in sources:
            if not target_owned():
                raise ValueError("The move destination changed. Original files are retained.")
            if source.is_symlink() or source.is_junction():
                raise ValueError("Remove filesystem links from the managed files before moving.")
            shutil.copytree(
                source,
                target / source.relative_to(original),
                ignore=reject_links,
                copy_function=copy_file,
                dirs_exist_ok=True,
                symlinks=True,
            )
        if not target_owned():
            raise ValueError("The move destination changed. Original files are retained.")
        for source in sources:
            _verify_move_tree(source, source_trees[source], hashes)
            copied_trees[source] = {
                target / path.relative_to(original): copied_nodes[
                    target / path.relative_to(original)
                ]
                for path in source_trees[source]
            }
        _verify_move_tree(target, copied_nodes, hashes)
        store.execute("UPDATE projects SET storage_dir=? WHERE id=?", (str(target), identifier))
    except Exception as exc:
        # If commit succeeded before a connection error, never remove the active copy.
        try:
            committed = store.project_folder(identifier).resolve() == target
        except Exception:
            raise ValueError("Storage could not be verified. Both locations are retained.") from exc
        if committed:
            return {
                "path": str(target),
                "cleanup_warning": (
                    "Storage changed, but finalization failed. "
                    "Both locations are retained; check project outputs."
                ),
            }
        # Only this newly created exclusive target may be removed; never its chosen parent.
        if target_owned() and target.resolve().parent == parent and target.name == identifier[:8]:
            try:
                _verify_move_tree(target, copied_nodes, hashes)
            except (OSError, ValueError):
                pass  # Retain unverified/replaced descendants; no recursive cleanup authority.
            else:
                shutil.rmtree(target)
        if isinstance(exc, ValueError):
            raise
        raise ValueError("Project move failed. Original files and storage are unchanged.") from exc

    try:
        if not target_owned():
            raise ValueError("The move destination changed.")
        for source in sources:
            _verify_move_tree(source, source_trees[source], hashes)
        _verify_move_tree(target, copied_nodes, hashes)
    except (OSError, ValueError):
        return {
            "path": str(target),
            "cleanup_warning": (
                "Storage changed, but its destination changed or is unavailable. "
                f"Original files remain at {original}; check both locations before continuing."
            ),
        }
    warning = None
    try:
        for source in sources:
            if not target_owned():
                raise ValueError("The move destination changed before cleanup.")
            if _move_signature(_move_node(original)) != _move_signature(original_node):
                raise ValueError("The original project directory changed before cleanup.")
            _verify_move_tree(source, source_trees[source])
            _verify_move_tree(target / source.relative_to(original), copied_trees[source])
            resolved = source.resolve()
            if resolved == original or not resolved.is_relative_to(original):
                raise ValueError("The previous location changed during the move.")
            shutil.rmtree(source)
        if project["storage_dir"]:
            if old_metadata_node is not None:
                if _move_signature(_move_node(old_metadata)) != _move_signature(old_metadata_node):
                    raise ValueError("The previous project snapshot changed before cleanup.")
                if _move_digest(old_metadata, old_metadata_node) != old_metadata_hash:
                    raise ValueError(
                        "The previous project snapshot content changed before cleanup."
                    )
                old_metadata.unlink()
            for folder in (original / "recordings", original / "exports", original):
                try:
                    folder.rmdir()  # Keep unrelated files and nonempty parent folders.
                except OSError:
                    pass
    except (OSError, ValueError):
        warning = (
            "Project moved, but some old copies remain. "
            "Check the previous folder before removing them."
        )
    try:
        _verify_move_tree(target, copied_nodes)
    except (OSError, ValueError):
        warning = (
            warning
            or "Storage changed, but its destination changed before finalization. "
            "Original copies are retained."
        )
    else:
        store.snapshot_project(identifier)
    return {
        "path": str(target),
        "cleanup_warning": warning or store.snapshot_errors.get(identifier),
    }


def choose_folder():
    if sys.platform == "darwin":
        script = """
try
    set folderPrompt to "Choose a parent folder for this CBS Transcriber project"
    return POSIX path of (choose folder with prompt folderPrompt)
on error number -128
    return ""
end try
"""
        command = ["osascript", "-e", script]
        flags = 0
    elif os.name == "nt":
        script = """
Add-Type -AssemblyName System.Windows.Forms
# An invisible top-most owner brings the chooser in front of the browser window.
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.StartPosition = 'CenterScreen'
$owner.Show()
$owner.Activate()
$picker = New-Object System.Windows.Forms.FolderBrowserDialog
$picker.Description = 'Choose a parent folder for this CBS Transcriber project'
$picker.ShowNewFolderButton = $true
if ($picker.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::Write($picker.SelectedPath)
}
$picker.Dispose()
$owner.Close()
"""
        command = [
            "powershell.exe",
            "-NoProfile",
            "-STA",
            "-WindowStyle",
            "Hidden",
            "-Command",
            script,
        ]
        flags = subprocess.CREATE_NO_WINDOW
    else:
        raise ValueError("Enter an absolute folder path on this platform.")
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            timeout=300,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(
            "Folder chooser unavailable. Enter the absolute folder path instead."
        ) from exc
    if result.returncode:
        raise ValueError("Folder chooser unavailable. Enter the absolute folder path instead.")
    path = result.stdout.rstrip("\r\n") if sys.platform == "darwin" else result.stdout.strip()
    return path or None


SYNC_MARKERS = ("onedrive", "dropbox", "google drive", "mobile documents", "icloud")


def synced(path):
    """Best-effort detection of cloud-synced folders (OneDrive known-folder redirect, iCloud)."""
    try:
        resolved = Path(path).resolve()
    except OSError:
        return True
    roots = [
        Path(os.environ[name])
        for name in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")
        if os.environ.get(name)
    ]
    # Whole folder names only: "OneDrive - CBS" is synced; "…-OneDrive-…" inside a name is not.
    return any(part.lower().startswith(SYNC_MARKERS) for part in resolved.parts) or any(
        resolved.is_relative_to(root) for root in roots
    )


APP_FOLDER = "CBS Transcriber"


def app_folder():
    """One visible folder in the home directory on Windows and macOS (ADR 0050).

    Not Documents/Desktop, which OneDrive or iCloud commonly sync, and not hidden app data
    (AppData, ~/Library) that researchers cannot find.
    """
    return Path.home() / APP_FOLDER


def legacy_library_root():
    """The earlier hidden default: %LOCALAPPDATA% or ~/Library/Application Support."""
    from platformdirs import user_data_path

    return user_data_path(APP_FOLDER, appauthor=False)


def default_library_root():
    """<home>/CBS Transcriber/Library, unless a library exists only at the earlier default.

    An existing library is used where it is; it is never moved automatically.
    """
    friendly = app_folder() / "Library"
    legacy = legacy_library_root()
    if not (friendly / "library.sqlite3").exists() and (legacy / "library.sqlite3").exists():
        return legacy
    return friendly


def default_project_parent():
    """<home>/CBS Transcriber/Projects: new projects each get their own subfolder here."""
    override = os.environ.get("CBS_TRANSCRIBER_PROJECTS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return app_folder() / "Projects"
