"""Bounded project ZIP downloads built from one read-only database snapshot."""

import hashlib
import json
import os
import re
import shutil
import stat
import unicodedata
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import get_args
from zipfile import ZIP_DEFLATED, ZipFile

from .docx_export import docx_bytes
from .history import decode_payload
from .transcripts import ExportFormat, export_filename, export_transcript, resolved_speakers

MEDIA_TYPES = {
    "json": "application/json",
    "csv": "text/csv",
    "vtt": "text/vtt",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
NO_RESULTS_MESSAGE = (
    "No results can be exported in this format. "
    "Choose Readable text, CSV or JSON, or add transcripts first."
)


def media_type(export_format):
    return MEDIA_TYPES.get(export_format, "text/plain")


def export_bytes(segments, speakers, grouping, export_format):
    if export_format == "docx":
        if not segments:
            raise ValueError("There is no transcript to export.")
        return docx_bytes(segments, resolved_speakers(segments, speakers or {}))
    content = export_transcript(segments, speakers, grouping, export_format)
    encoding = "utf-8-sig" if export_format in {"nvivo-windows", "nvivo-mac", "csv"} else "utf-8"
    return content.encode(encoding)


def collection_folder(position, name):
    """A safe, unique ZIP folder for a collection; the name is the researcher's own text."""
    safe = re.sub(r"[^\w\- ]+", "_", name, flags=re.UNICODE).strip(" ._")[:60]
    return f"{position + 1:02d} {safe or 'Collection'}"


def _device_name(name):
    reserved = unicodedata.normalize("NFKC", name.split(".", 1)[0]).rstrip(" ").casefold()
    return reserved in {"con", "prn", "aux", "nul", "conin$", "conout$"} or bool(
        re.fullmatch(r"(?:com|lpt)[1-9]", reserved)
    )


def _folder(name, used, fallback="Recording"):
    """Readable portable components, unique even after Unicode/case normalization."""
    safe = unicodedata.normalize("NFC", name or "")
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", safe).strip(" ._") or fallback
    # Leave room for a collision suffix; UTF-8 also bounds Windows UTF-16 units.
    safe = safe.encode("utf-8")[:180].decode("utf-8", errors="ignore").rstrip(" .")
    # Truncation can expose a device name that was not reserved before shortening.
    if _device_name(safe):
        safe = "_" + safe
    candidate, number = safe, 1
    while unicodedata.normalize("NFKC", candidate).casefold() in used:
        number += 1
        candidate = f"{safe} ({number})"
    used.add(unicodedata.normalize("NFKC", candidate).casefold())
    return candidate


def _formats(export_format, formats, include_originals=False):
    values = [export_format] if formats is None else formats
    if (
        not isinstance(values, list)
        or not (0 if include_originals else 1) <= len(values) <= 8
        or any(
            not isinstance(value, str) or value not in get_args(ExportFormat) for value in values
        )
        or len(set(values)) != len(values)
    ):
        raise ValueError("Choose one or more different transcript formats.")
    return values


def _original_filename(filename):
    """Keep the original basename and extension wherever they are portable."""
    from .media import MEDIA_SUFFIXES

    basename = Path(filename.replace("\\", "/")).name
    extension = Path(basename).suffix
    if extension.lower() not in MEDIA_SUFFIXES:
        raise ValueError("The original recording has an unsupported filename extension.")
    stem = unicodedata.normalize("NFC", basename[: -len(extension)])
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", stem).strip(" .") or "original"
    stem = stem.encode("utf-8")[:180].decode("utf-8", errors="ignore").rstrip(" .")
    if _device_name(stem):
        stem = "_" + stem
    return stem + extension


def _original_open(store, recording_id, owner):
    """Fence current ownership and bind a regular no-link file descriptor, without reading."""
    from . import audio_inputs, deletion_files

    with store.snapshot_lock:
        with store.connection() as db:
            current = db.execute(
                "SELECT r.project_id,r.filename,p.storage_dir FROM recordings r "
                "JOIN projects p ON p.id=r.project_id WHERE r.id=?",
                (recording_id,),
            ).fetchone()
        if current is None or tuple(current) != owner:
            raise ValueError("The original recording's location changed during export.")
        deletion_files._identifier(recording_id)
        base = Path(owner[2]) if owner[2] else store.root
        directory = base / "recordings" / recording_id
        extension = Path(owner[1].replace("\\", "/")).suffix.lower()
        path = directory / ("original" + extension)
        deletion_files._safe(path)
        audio_inputs._owned(path, directory)
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("The original recording is not an owned regular file.")
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (
                audio_inputs._signature(opened) != audio_inputs._signature(before)
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
            ):
                raise ValueError("The original recording changed while opening it.")
        except BaseException:
            os.close(descriptor)
            raise
    return descriptor, path, directory, before, opened


def _stage_original(store, recording_id, owner, background=None):
    """Validate a bounded-memory staged copy before adding any media member to the ZIP."""
    from . import audio_inputs, deletion_files

    staged = SpooledTemporaryFile(max_size=2 * 1024 * 1024, mode="w+b")
    try:
        descriptor, path, directory, before, opened = _original_open(store, recording_id, owner)
        with os.fdopen(descriptor, "rb") as incoming:
            digest, count = hashlib.sha256(), 0
            while block := incoming.read(min(1024 * 1024, before.st_size - count + 1)):
                if background:
                    background.guard()
                count += len(block)
                if count > before.st_size:
                    raise ValueError("The original recording grew during export.")
                digest.update(block)
                staged.write(block)
                if background:
                    background.progress("reading", recording_id, count, before.st_size)
            final = os.fstat(incoming.fileno())
            if (
                count != before.st_size
                or final.st_nlink != 1
                or audio_inputs._signature(final) != audio_inputs._signature(opened)
                or final.st_ctime_ns != opened.st_ctime_ns
            ):
                raise ValueError("The original recording changed while exporting it.")
            with store.snapshot_lock:
                with store.connection() as db:
                    current = db.execute(
                        "SELECT r.project_id,r.filename,p.storage_dir FROM recordings r "
                        "JOIN projects p ON p.id=r.project_id WHERE r.id=?",
                        (recording_id,),
                    ).fetchone()
                deletion_files._safe(path)
                audio_inputs._owned(path, directory)
                after = path.lstat()
                if (
                    current is None
                    or tuple(current) != owner
                    or audio_inputs._signature(after) != audio_inputs._signature(before)
                    or after.st_ctime_ns != before.st_ctime_ns
                    or after.st_nlink != 1
                ):
                    raise ValueError("The original recording's location changed during export.")
        staged.seek(0)
        return staged, {"bytes": count, "sha256": digest.hexdigest()}
    except BaseException as error:
        staged.close()
        if isinstance(error, FileNotFoundError):
            raise ValueError("The original recording is missing.") from None
        if isinstance(error, (OSError, ValueError)):
            raise ValueError(
                "The original recording is unavailable, changed or contains filesystem links."
            ) from None
        raise


def _recording_ids(values):
    if values is not None and (
        not isinstance(values, list)
        or not 1 <= len(values) <= 10_000
        or any(not isinstance(value, str) or not 1 <= len(value) <= 100 for value in values)
        or len(set(values)) != len(values)
    ):
        raise ValueError("Choose one or more different recordings to export.")
    return values


def project_archive(
    store,
    identifier,
    export_format="text",
    grouping="speaker",
    results="working",
    collection=None,
    *,
    recording_ids=None,
    formats=None,
    include_originals=False,
    background=None,
):
    """Export a project or an exact subset; None retains the legacy whole-project scope."""
    return _archive(
        store,
        identifier,
        export_format,
        grouping,
        results,
        collection,
        recording_ids=recording_ids,
        formats=formats,
        include_originals=include_originals,
        background=background,
    )


def recording_archive(
    store,
    identifier,
    result_id,
    formats,
    grouping="speaker",
    *,
    include_originals=False,
    background=None,
):
    """Export one exact live saved result in several formats from the same snapshot."""
    if not (result_id is None and include_originals) and (
        not isinstance(result_id, str) or not 1 <= len(result_id) <= 100
    ):
        raise ValueError("Choose the saved result to export.")
    return _archive(
        store,
        None,
        None,
        grouping,
        "active",
        None,
        recording_ids=[identifier],
        formats=formats,
        result_id=result_id,
        recording_id=identifier,
        include_originals=include_originals,
        background=background,
    )


def _archive(
    store,
    identifier,
    export_format,
    grouping,
    results,
    collection,
    *,
    recording_ids,
    formats,
    result_id=None,
    recording_id=None,
    include_originals=False,
    background=None,
):
    if type(include_originals) is not bool:
        raise ValueError("Choose whether to include original recordings.")
    formats = _formats(export_format, formats, include_originals)
    recording_ids = _recording_ids(recording_ids)
    if grouping not in {"speaker", "timestamp"}:
        raise ValueError("Choose speaker or timestamp grouping.")
    if results not in {"working", "active", "all"}:
        raise ValueError("Choose Working versions, reviewed results or all saved results.")
    condition = (
        " AND t.id=?"
        if recording_id is not None
        else (
            " AND t.id=(SELECT v.result_id FROM recording_result_roles w "
            "JOIN result_versions v ON v.id=w.working_version_id WHERE w.recording_id=r.id)"
            if results == "working"
            else " AND t.id=r.active_transcript_id"
            if results == "active"
            else ""
        )
    )
    scope = ""
    if collection == "unsorted":
        scope = " AND r.collection_id IS NULL"
    elif collection is not None:
        scope = " AND r.collection_id=?"
    if recording_ids is not None:
        scope += " AND r.id IN (" + ",".join("?" for _ in recording_ids) + ")"
    content = (
        "NULL AS segments,NULL AS speakers," if results == "working" else "t.segments,t.speakers,"
    )
    query = (
        "SELECT r.id recording_id,r.name recording_name,r.filename,r.collection_id,"
        "t.id result_id,t.label,"
        + content
        + "(SELECT working_version_id FROM recording_result_roles w WHERE w.recording_id=r.id) "
        "AS working_version_id "
        "FROM recordings r LEFT JOIN transcript_results t ON t.recording_id=r.id"
        + condition
        + " AND t.id NOT IN (SELECT result_id FROM result_trash)"
        + " WHERE r.project_id=?"
        + scope
        + " ORDER BY r.created,r.id,t.created,t.id"
    )
    archive = (
        background.archive
        if background
        else SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    )
    entries, exported = [], 0
    try:
        with store.connection() as db, ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
            # One read snapshot: collections and recordings cannot drift apart mid-export.
            db.execute("BEGIN")
            if recording_id is not None:
                recording = db.execute(
                    "SELECT project_id,name FROM recordings WHERE id=?", (recording_ids[0],)
                ).fetchone()
                if recording is None or (
                    result_id is not None
                    and not db.execute(
                        "SELECT id FROM transcript_results WHERE id=? AND recording_id=? "
                        "AND id NOT IN (SELECT result_id FROM result_trash)",
                        (result_id, recording_ids[0]),
                    ).fetchone()
                ):
                    raise KeyError("The requested item no longer exists.")
                identifier = recording["project_id"]
            project = db.execute("SELECT name FROM projects WHERE id=?", (identifier,)).fetchone()
            if project is None:
                raise KeyError("The requested item no longer exists.")
            storage = (
                db.execute("SELECT storage_dir FROM projects WHERE id=?", (identifier,)).fetchone()[
                    "storage_dir"
                ]
                if include_originals
                else None
            )
            if (
                collection not in (None, "unsorted")
                and not db.execute(
                    "SELECT id FROM collections WHERE id=? AND project_id=?",
                    (collection, identifier),
                ).fetchone()
            ):
                raise KeyError("The requested item no longer exists.")
            params = [identifier]
            if collection not in (None, "unsorted"):
                params.append(collection)
            if recording_ids is not None:
                params.extend(recording_ids)
                count = db.execute(
                    "SELECT COUNT(*) n FROM recordings r WHERE r.project_id=?" + scope,
                    params,
                ).fetchone()["n"]
                if count != len(recording_ids):
                    raise KeyError(
                        "The selected recordings changed or are outside this project or collection."
                    )
            if recording_id is not None:
                params.insert(0, result_id)
            # One folder per collection when the project has any (ADR 0060).
            folders = {
                row["id"]: (collection_folder(position, row["name"]), row["name"])
                for position, row in enumerate(
                    db.execute(
                        "SELECT id,name FROM collections WHERE project_id=? "
                        "ORDER BY position,created",
                        (identifier,),
                    )
                )
            }
            recording_folders, used_recording_folders, result_folders = {}, {}, {}
            originals = set()
            if background:
                # The read snapshot and exact selection are established before the task
                # acknowledges admission. Subsequent edits cannot retarget this export.
                background.prepared(
                    recording["name"] if recording_id is not None else project["name"]
                )
            for row in db.execute(query, params):
                if background:
                    background.guard()
                    background.progress("preparing", row["recording_id"])
                entry = {
                    k: row[k]
                    for k in ("recording_id", "filename", "result_id", "label", "recording_name")
                }
                folder = ""
                if folders:
                    folder, name = folders.get(row["collection_id"], ("Unsorted", None))
                    entry["collection"] = name
                    folder += "/"
                if row["recording_id"] not in recording_folders:
                    recording_folders[row["recording_id"]] = _folder(
                        row["recording_name"], used_recording_folders.setdefault(folder, set())
                    )
                folder += recording_folders[row["recording_id"]] + "/"
                if include_originals and row["recording_id"] not in originals:
                    originals.add(row["recording_id"])
                    original = {
                        **entry,
                        "kind": "source_media",
                        "format": "original",
                        "result_id": None,
                        "label": "Original recording",
                    }
                    try:
                        original_name = _original_filename(row["filename"])
                        staged, identity = _stage_original(
                            store,
                            row["recording_id"],
                            (identifier, row["filename"], storage),
                            background,
                        )
                    except ValueError as error:
                        original.update(status="skipped", reason=str(error))
                    else:
                        path = "transcripts/" + folder + "original/" + original_name
                        with staged, bundle.open(path, "w", force_zip64=True) as output:
                            if not background:
                                shutil.copyfileobj(staged, output, length=1024 * 1024)
                            else:
                                count = 0
                                while block := staged.read(1024 * 1024):
                                    background.guard()
                                    output.write(block)
                                    count += len(block)
                                    background.progress(
                                        "archiving", row["recording_id"], count, identity["bytes"]
                                    )
                        original.update(status="exported", path=path, **identity)
                        exported += 1
                    entries.append(original)
                if not formats:
                    continue
                if results == "all" and row["result_id"]:
                    if include_originals:
                        result_folders.setdefault(folder, set()).add("original")
                    folder += (
                        _folder(row["label"], result_folders.setdefault(folder, set()), "Result")
                        + "/"
                    )
                try:
                    if results == "working":
                        from . import result_roles

                        roles = result_roles.read(db, store, row["recording_id"])
                        if roles["working"] and roles["working"]["result_id"] != row["result_id"]:
                            raise ValueError("The Working designation ownership is invalid.")
                    if not row["result_id"]:
                        raise ValueError(
                            "No Working version designated. "
                            "Mark an exact saved version in Versions."
                            if results == "working"
                            else "No saved transcript."
                        )
                    if results == "working":
                        saved = result_roles.version(
                            db, row["recording_id"], roles["working"]["version_id"]
                        )
                        payload = decode_payload(saved["payload"], saved["payload_sha256"])
                        segments, speakers = payload["segments"], payload["speakers"]
                        entry.update(
                            version_id=saved["id"],
                            version_sequence=saved["sequence"],
                            payload_sha256=saved["payload_sha256"],
                        )
                    else:
                        segments, speakers = (
                            json.loads(row["segments"]),
                            json.loads(row["speakers"]),
                        )
                except ValueError as exc:
                    for selected_format in formats:
                        entries.append(
                            {
                                **entry,
                                "format": selected_format,
                                "status": "skipped",
                                "reason": str(exc),
                            }
                        )
                    continue
                for selected_format in formats:
                    if background:
                        background.guard()
                    output = {**entry, "format": selected_format}
                    try:
                        data = export_bytes(segments, speakers, grouping, selected_format)
                        path = (
                            "transcripts/"
                            + folder
                            + export_filename(row["filename"], export_format=selected_format)
                        )
                        bundle.writestr(path, data)
                        output.update(status="exported", path=path)
                        exported += 1
                    except ValueError as exc:
                        output.update(status="skipped", reason=str(exc))
                    entries.append(output)
            if not exported and results != "working" and not include_originals:
                raise ValueError(NO_RESULTS_MESSAGE)
            report = {
                "project": project["name"],
                "format": formats[0]
                if len(formats) == 1
                else "multiple"
                if formats
                else "original",
                "formats": formats,
                "results": "selected" if recording_id is not None else results,
                "include_originals": include_originals,
                "collection": collection,
                "exported": exported,
                "skipped": len(entries) - exported,
                "entries": entries,
            }
            if recording_ids is not None:
                report["recording_ids"] = recording_ids
            bundle.writestr("manifest.json", json.dumps(report, ensure_ascii=False, indent=2))
            lines = [
                f"Project: {project['name']}",
                f"Exported: {exported}; skipped: {len(entries) - exported}.",
                "Original recordings are included only where a source_media entry is exported."
                if include_originals
                else "Original recordings are not included.",
                "See manifest.json for result and source-media identities.",
                "",
            ]
            lines.extend(
                f"{e['filename']} — {e['label'] or 'No result'} ({e['format']}): "
                + (e["path"] if e["status"] == "exported" else "SKIPPED — " + e["reason"])
                for e in entries
            )
            bundle.writestr("EXPORT REPORT.txt", "\n".join(lines).encode("utf-8"))
        archive.seek(0)
        return archive
    except BaseException:
        archive.close()
        raise
