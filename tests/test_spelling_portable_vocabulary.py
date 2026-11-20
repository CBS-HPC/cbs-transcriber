"""Private spelling words survive isolated restore without restoring operational settings."""

import copy
import io
import json
import sqlite3
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi import HTTPException
from test_portable_restore import destination

from cbs_transcriber import (
    exports,
    portable_capture,
    portable_restore,
    research_reports,
    spelling,
)
from cbs_transcriber import portable_semantics_worker as worker
from cbs_transcriber.backup import create_backup
from cbs_transcriber.deletions import Deletions
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments

PROJECT = "a" * 32
# v2 rows 10/11 belong to checked-provider receipts and runtime generations (B464).
ROW, PREFERENCES = spelling.VOCABULARY_ID, spelling.SETTINGS_ID
assert (ROW, PREFERENCES) == (worker.SPELLING_VOCABULARY, 12)


def vocabulary(project_id=PROJECT):
    return {
        "schema": 1,
        "personal": {"revision": 2, "words": ["Björk", "cafe\u0301", "O\u2019Neill"]},
        "projects": {project_id: {"revision": 4, "words": ["Anne-Marie", "Ærø", "D\u2011vitamin"]}},
    }


class Observed:
    """The contained reader supplies bounded text, strict JSON and fixed owner lookups."""

    def __init__(self, value, projects=(PROJECT,)):
        self.raw = value if isinstance(value, str) or value is None else json.dumps(value)
        self.projects = set(projects)
        self.guards = 0

    def one(self, sql, parameters=()):
        if sql == f"SELECT id FROM settings WHERE id={ROW}":
            return None if self.raw is None else (ROW,)
        assert sql == "SELECT id FROM projects WHERE id=?"
        return (parameters[0],) if parameters[0] in self.projects else None

    def text(self, table, column, key, value, *, maximum):
        assert (table, column, key, value, maximum) == ("settings", "value", "id", ROW, 4 * 1024**2)
        if len(self.raw.encode("utf-8")) > maximum:
            worker.fail("resource")
        return self.raw

    def json(self, raw):
        return json.loads(raw, object_pairs_hook=worker.pairs)

    def guard(self):
        self.guards += 1


def test_standalone_validator_keeps_exact_literal_order_bytes_and_revisions():
    value = vocabulary()
    value["projects"][PROJECT]["words"].append("Björk")  # Scopes remain independent.
    raw = json.dumps(value, ensure_ascii=False, indent=2)
    observed = Observed(raw)
    assert worker._spelling_vocabulary(observed) == (ROW, raw)
    assert observed.guards >= 9
    assert worker._spelling_vocabulary(Observed(None)) is None


@pytest.mark.parametrize(
    "bad_word",
    [
        None,
        "",
        "A" * 129,
        "\ud800",
        "9names",
        "two words",
        "Name\n",
        "a/b",
        "a@b",
        "-name",
        "name-",
        "name--word",
        "a\u2019\u2019b",
        "\u0301name",
        "a_thing",
        "a\u200db",
    ],
)
def test_standalone_validator_refuses_invalid_private_terms(bad_word):
    value = vocabulary()
    value["personal"]["words"] = [bad_word]
    with pytest.raises(worker.Refused):
        worker._spelling_vocabulary(Observed(value))


@pytest.mark.parametrize(
    "damage",
    [
        "schema_bool",
        "schema_future",
        "extra_root",
        "extra_scope",
        "revision_bool",
        "revision_negative",
        "revision_large",
        "revision_float",
        "words_not_list",
        "too_many_words",
        "case_duplicate",
        "unicode_duplicate",
        "bad_owner",
        "orphan_owner",
        "too_many_projects",
        "null_scope",
    ],
)
def test_standalone_validator_refuses_unknown_shapes_limits_or_owners(damage):
    value = vocabulary()
    personal = value["personal"]
    if damage == "schema_bool":
        value["schema"] = True
    elif damage == "schema_future":
        value["schema"] = 2
    elif damage == "extra_root":
        value["dictionary_url"] = "https://private.invalid/never-used"
    elif damage == "extra_scope":
        personal["path"] = "C:/private"
    elif damage.startswith("revision_"):
        personal["revision"] = {
            "revision_bool": True,
            "revision_negative": -1,
            "revision_large": 2**63,
            "revision_float": 1.0,
        }[damage]
    elif damage == "words_not_list":
        personal["words"] = "Name"
    elif damage == "too_many_words":
        personal["words"] = ["Name"] * 5001
    elif damage == "case_duplicate":
        personal["words"] = ["Name", "NAME"]
    elif damage == "unicode_duplicate":
        personal["words"] = ["café", "cafe\u0301"]
    elif damage == "bad_owner":
        value["projects"] = {"../outside": {"revision": 0, "words": []}}
    elif damage == "orphan_owner":
        value["projects"] = {"b" * 32: {"revision": 0, "words": []}}
    elif damage == "too_many_projects":
        value["projects"] = {f"{index:032x}": {"revision": 0, "words": []} for index in range(1025)}
    else:
        value["personal"] = None
    with pytest.raises(worker.Refused):
        worker._spelling_vocabulary(Observed(value))


def test_standalone_validator_refuses_oversized_and_duplicate_key_json():
    with pytest.raises(worker.Refused, match="resource"):
        worker._spelling_vocabulary(Observed(" " * (4 * 1024**2 + 1)))
    raw = '{"schema":1,"schema":1,"personal":{"revision":0,"words":[]},"projects":{}}'
    with pytest.raises(worker.Refused):
        worker._spelling_vocabulary(Observed(raw))


@pytest.fixture
def source(tmp_path):
    store = Store(tmp_path / "source")
    project = store.create_project("Synthetic vocabulary", storage_parent=tmp_path / "projects")
    other = store.create_project("Unrelated project", storage_parent=tmp_path / "projects")
    return store, project, other


def capture(source, tmp_path, raw):
    store = source[0]
    # Preferences and unrecognized operational settings must never be reactivated.
    store.execute(
        f"INSERT INTO settings VALUES ({PREFERENCES},?)",
        (json.dumps({"revision": 3, "enabled": True, "auto_download": True}),),
    )
    store.execute("INSERT INTO settings VALUES (123,?)", ('{"private_url":"never-used"}',))
    # The preview's old vocabulary row number is now another v2 setting: never restored as
    # spelling vocabulary, even when it happens to hold vocabulary-shaped JSON (B464).
    store.execute(
        "INSERT OR REPLACE INTO settings VALUES (11,?)",
        (json.dumps({"schema": 1, "personal": {"revision": 0, "words": []}, "projects": {}}),),
    )
    if raw is not None:
        store.execute(f"INSERT INTO settings VALUES ({ROW},?)", (raw,))
    with store.library_lock():
        return portable_capture.capture_library(
            store, JobQueue(store, None), tmp_path / "packages", gate=portable_capture.CaptureGate()
        )


@pytest.mark.parametrize("present", [False, True])
def test_vocab_round_trip_preserves_only_validated_data_and_legacy_absence(
    source, tmp_path, present
):
    store, project, other = source
    value = vocabulary(project["id"])
    value["projects"][other["id"]] = {"revision": 0, "words": []}
    raw = json.dumps(value, ensure_ascii=False, indent=2) if present else None
    package = capture(source, tmp_path, raw)
    target = destination(tmp_path, "spelling-round-trip")
    result = portable_restore.restore_library(
        package["folder"],
        expected_commit_sha256=package["commit_sha256"],
        target=target,
        live_root=store.root,
    )
    assert result["state"] == "complete"
    restored = Store(target)
    rows = restored.all("SELECT * FROM settings ORDER BY id")
    assert [row["id"] for row in rows] == ([2, ROW] if present else [2])
    if present:
        assert rows[1]["value"] == raw
    assert not list(target.rglob("*.aff")) and not list(target.rglob("*.dic"))
    assert store.all(f"SELECT value FROM settings WHERE id={ROW}") == (
        [{"value": raw}] if present else []
    )


@pytest.mark.parametrize("damage", ["orphan", "duplicate_key", "oversized"])
def test_invalid_vocabulary_refuses_before_promotion_or_registration(source, tmp_path, damage):
    value = vocabulary(source[1]["id"])
    if damage == "orphan":
        value["projects"]["f" * 32] = {"revision": 0, "words": ["PrivateName"]}
        raw = json.dumps(value)
    elif damage == "duplicate_key":
        raw = '{"schema":1,"schema":1,"personal":{"revision":0,"words":[]},"projects":{}}'
    else:
        raw = " " * (4 * 1024**2 + 1)
    package = capture(source, tmp_path, raw)
    target = destination(tmp_path, "refused-vocabulary")
    registered = []
    before = copy.deepcopy(source[0].all("SELECT * FROM settings ORDER BY id"))
    with pytest.raises(ValueError):
        portable_restore.restore_library(
            package["folder"],
            expected_commit_sha256=package["commit_sha256"],
            target=target,
            live_root=source[0].root,
            register=registered.append,
        )
    assert not target.exists() and registered == []
    assert source[0].all("SELECT * FROM settings ORDER BY id") == before
    assert Path(package["folder"]).is_dir()


def test_local_backup_contains_private_words_but_exports_and_reports_do_not(source, tmp_path):
    store, project, _ = source
    value = vocabulary(project["id"])
    value["personal"]["words"] = ["PrivatePersonalSentinel"]
    value["projects"][project["id"]]["words"] = ["PrivateProjectSentinel"]
    raw = json.dumps(value)
    store.execute(f"INSERT INTO settings VALUES ({ROW},?)", (raw,))
    recording = store.create_recording(project["id"], "public.wav")
    store.add_result(
        recording["id"],
        normalize_segments([{"text": "Public synthetic transcript"}]),
        {},
        "Public result",
        {},
        auto_first=True,
    )
    receipt = create_backup(store, tmp_path / "inventories")
    with sqlite3.connect(Path(receipt["folder"]) / "library.sqlite3") as db:
        assert db.execute(f"SELECT value FROM settings WHERE id={ROW}").fetchone()[0] == raw
    archive = exports.project_archive(
        store, project["id"], recording_ids=[recording["id"]], formats=["json"]
    )
    try:
        with ZipFile(archive) as exported:
            exported_bytes = b"\n".join(exported.read(name) for name in exported.namelist())
    finally:
        archive.close()
    with ZipFile(io.BytesIO(research_reports.project_report(store, project["id"]))) as report:
        report_bytes = b"\n".join(report.read(name) for name in report.namelist())
    for private in (b"PrivatePersonalSentinel", b"PrivateProjectSentinel"):
        assert private not in exported_bytes and private not in report_bytes


def test_project_vocabulary_changed_after_delete_preview_is_preserved(source):
    store, project, _ = source
    value = vocabulary(project["id"])
    store.execute(f"INSERT INTO settings VALUES ({ROW},?)", (json.dumps(value),))
    deletes = Deletions(store, JobQueue(store, None), acquire_file_lease=True)
    preview = deletes.preview("project", project["id"])
    value["projects"][project["id"]] = {"revision": 5, "words": ["NewPrivateName"]}
    raw = json.dumps(value)
    store.execute(f"UPDATE settings SET value=? WHERE id={ROW}", (raw,))
    with pytest.raises(HTTPException) as caught:
        deletes.request("project", project["id"], operation_id=preview["operation_id"])
    assert caught.value.status_code == 409
    assert store.one(f"SELECT value FROM settings WHERE id={ROW}")["value"] == raw
    assert store.one("SELECT id FROM projects WHERE id=?", (project["id"],))


@pytest.mark.parametrize("changed_scope", ["personal", "other_project"])
def test_project_delete_keeps_unrelated_vocabulary_and_its_revisions(source, changed_scope):
    store, project, other = source
    value = vocabulary(project["id"])
    value["projects"][other["id"]] = {"revision": 7, "words": ["OtherPrivateName"]}
    store.execute(f"INSERT INTO settings VALUES ({ROW},?)", (json.dumps(value),))
    deletes = Deletions(store, JobQueue(store, None), acquire_file_lease=True)
    preview = deletes.preview("project", project["id"])
    changed = value["personal"] if changed_scope == "personal" else value["projects"][other["id"]]
    changed["words"].append("LaterPrivateTerm")
    changed["revision"] += 1
    store.execute(f"UPDATE settings SET value=? WHERE id={ROW}", (json.dumps(value),))
    status, response = deletes.request(
        "project", project["id"], operation_id=preview["operation_id"]
    )
    assert status == 200 and response["ok"] is True
    expected = copy.deepcopy(value)
    expected["projects"].pop(project["id"])
    assert json.loads(store.one(f"SELECT value FROM settings WHERE id={ROW}")["value"]) == expected
    assert not store.all("SELECT id FROM projects WHERE id=?", (project["id"],))
    assert store.one("SELECT id FROM projects WHERE id=?", (other["id"],))
