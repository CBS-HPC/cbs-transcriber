"""Source provenance is checked against captured proof, without filling legacy gaps."""

import json
from contextlib import contextmanager

import pytest
from report_support import selected_view_report
from test_research_reports import unpack

from cbs_transcriber import research_reports
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


def seeded(tmp_path, *, legacy=False, text="Captured source."):
    store = Store(tmp_path)
    project = store.create_project("PRIVATE_SOURCE_PROJECT")
    recording = store.create_recording(project["id"], "PRIVATE_SOURCE_RECORDING.wav")
    rid = recording["id"]
    base = store.add_result(rid, normalize_segments([{"text": text}]), {}, "Source", {})
    frozen = store.freeze_result(rid, base["id"], store.recording(rid)["revision"])
    if legacy:
        frozen = {key: frozen[key] for key in ("id", "segments", "speakers")}
    job = store.add_job(rid, "enhance", {"source_snapshot": frozen})
    derived = store.add_result(
        rid,
        normalize_segments([{"text": "Derived wording."}]),
        {},
        "Derived",
        {"kind": "enhancement", "source_result_id": base["id"]},
        job_id=job["id"],
    )
    creation = store.result_versions(rid, derived["id"])[0]
    return store, project, rid, base, frozen, job, derived, creation


def report(store, project, rid, result, version):
    return unpack(
        selected_view_report(
            store,
            project["id"],
            [{"recording_id": rid, "result_id": result["id"], "version_id": version["id"]}],
        )
    )


@pytest.mark.parametrize("damage", ["self", "cross_result"])
def test_source_cycles_are_refused_even_without_additional_source_receipts(tmp_path, damage):
    store = Store(tmp_path)
    project = store.create_project("Source graph")
    recording = store.create_recording(project["id"], "source.wav")
    rid = recording["id"]
    first = store.add_result(rid, normalize_segments([{"text": "First."}]), {}, "First", {})
    version = store.result_versions(rid, first["id"])[0]
    source_id = version["id"]
    if damage == "cross_result":
        second = store.add_result(rid, normalize_segments([{"text": "Second."}]), {}, "Second", {})
        source_id = store.result_versions(rid, second["id"])[0]["id"]
        store.execute(
            "UPDATE result_versions SET source_version_id=? WHERE id=?", (version["id"], source_id)
        )
    store.execute(
        "UPDATE result_versions SET source_version_id=? WHERE id=?", (source_id, version["id"])
    )
    with pytest.raises(ValueError, match="source"):
        report(store, project, rid, first, version)


@pytest.mark.parametrize(
    "damage",
    [
        "pointer",
        "missing_pointer",
        "context_result",
        "context_hash",
        "context_unknown",
        "context_producer",
        "job_version",
        "job_result",
        "job_hash",
        "job_body",
    ],
)
def test_exact_source_claims_must_agree_with_frozen_producer_evidence(tmp_path, damage):
    store, project, rid, _, _, job, derived, creation = seeded(tmp_path)
    unrelated = store.add_result(
        rid, normalize_segments([{"text": "Unrelated source."}]), {}, "Other", {}
    )
    other_version = store.result_versions(rid, unrelated["id"])[0]
    if damage in {"pointer", "missing_pointer"}:
        store.execute(
            "UPDATE result_versions SET source_version_id=? WHERE id=?",
            (other_version["id"] if damage == "pointer" else None, creation["id"]),
        )
    elif damage.startswith("context"):
        context = dict(creation["context"])
        key, value = {
            "context_result": ("source_result_id", unrelated["id"]),
            "context_hash": ("source_payload_sha256", "0" * 64),
            "context_unknown": ("source_version_unknown", True),
            "context_producer": ("producer_job_id", "0" * 32),
        }[damage]
        context[key] = value
        store.execute(
            "UPDATE result_versions SET context=? WHERE id=?", (json.dumps(context), creation["id"])
        )
    else:
        params = store.job(job["id"])["params"]
        if damage == "job_version":
            params["source_snapshot"]["source_version_id"] = other_version["id"]
        elif damage == "job_result":
            params["source_snapshot"]["id"] = unrelated["id"]
        elif damage == "job_hash":
            params["source_snapshot"]["source_payload_sha256"] = "0" * 64
        else:
            params["source_snapshot"]["segments"][0]["text"] = "Changed frozen bytes."
        store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job["id"]))
    before = store.all("SELECT * FROM result_versions ORDER BY id")
    with pytest.raises(ValueError, match="source"):
        report(store, project, rid, derived, creation)
    assert store.all("SELECT * FROM result_versions ORDER BY id") == before


def test_legacy_unknown_snapshot_stays_unknown_after_current_source_changes(tmp_path):
    store, project, rid, base, _, _, derived, creation = seeded(tmp_path, legacy=True)
    store.activate_result(rid, base["id"], store.recording(rid)["revision"])
    store.save_transcript(
        rid,
        normalize_segments([{"text": "New current source."}]),
        {},
        store.recording(rid)["revision"],
    )
    manifest, _ = report(store, project, rid, derived, creation)
    assert len(manifest["versions"]) == 1  # Do not find a historical version from a matching hash.
    chosen = manifest["versions"][0]
    assert chosen["source_version"] is None
    assert any("historical version is unknown" in gap for gap in chosen["provenance_gaps"])


def test_same_payload_source_successor_cannot_replace_the_exact_frozen_version(tmp_path):
    store, project, rid, base, frozen, _, derived, creation = seeded(tmp_path)
    current = store.recording(rid)
    store.save_transcript(rid, base["segments"], base["speakers"], current["revision"])
    successor = store.result_versions(rid, base["id"])[0]
    assert successor["payload_sha256"] == frozen["source_payload_sha256"]
    assert successor["id"] != frozen["source_version_id"]
    store.execute(
        "UPDATE result_versions SET source_version_id=? WHERE id=?",
        (successor["id"], creation["id"]),
    )
    with pytest.raises(ValueError, match="frozen producer"):
        report(store, project, rid, derived, creation)


@pytest.mark.parametrize("damage", ["id", "body"])
def test_legacy_unpinned_source_still_checks_known_frozen_observations(tmp_path, damage):
    store, project, rid, _, _, job, derived, creation = seeded(tmp_path, legacy=True)
    params = store.job(job["id"])["params"]
    if damage == "id":
        params["source_snapshot"]["id"] = "0" * 32
    else:
        params["source_snapshot"]["segments"][0]["text"] = "Different legacy snapshot."
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job["id"]))
    with pytest.raises(ValueError, match="source"):
        report(store, project, rid, derived, creation)


def test_missing_legacy_job_and_context_do_not_invent_source_proof(tmp_path):
    store, project, rid, _, _, job, derived, creation = seeded(tmp_path)
    store.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
    store.execute("UPDATE result_versions SET context='{}' WHERE id=?", (creation["id"],))
    manifest, _ = report(store, project, rid, derived, creation)
    chosen = next(row for row in manifest["versions"] if row["selected"])
    assert chosen["source_version"] is not None  # A pointer is observed, without adding job proof.


def test_known_source_remains_exact_when_legacy_producer_job_is_unavailable(tmp_path):
    store, project, rid, _, frozen, job, derived, creation = seeded(tmp_path)
    store.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
    manifest, _ = report(store, project, rid, derived, creation)
    chosen = next(row for row in manifest["versions"] if row["selected"])
    parent = next(row for row in manifest["versions"] if row["id"] == chosen["source_version"])
    assert parent["payload_sha256"] == frozen["source_payload_sha256"]


def test_retained_source_job_checks_never_publish_private_snapshot_or_settings(tmp_path):
    store, project, rid, _, _, job, derived, creation = seeded(
        tmp_path, text="PRIVATE_FROZEN_SOURCE_TEXT"
    )
    params = store.job(job["id"])["params"]
    params["api_key"] = "PRIVATE_SOURCE_TOKEN"
    params["source_snapshot"]["label"] = "PRIVATE_FROZEN_SOURCE_LABEL"
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job["id"]))
    _, files = report(store, project, rid, derived, creation)
    assert all(b"PRIVATE_" not in body for body in files.values())


def test_source_job_is_bounded_and_observed_only_once_across_edit_history(tmp_path, monkeypatch):
    store, project, rid, _, _, job, derived, _ = seeded(tmp_path, text="Source " * 1000)
    store.activate_result(rid, derived["id"], store.recording(rid)["revision"])
    for index in range(6):
        store.save_transcript(
            rid,
            normalize_segments([{"text": f"Edit {index}."}]),
            {},
            store.recording(rid)["revision"],
        )
    head = store.result_versions(rid, derived["id"])[0]
    with store.connection() as db:
        cost = db.execute("SELECT length(CAST(params AS BLOB)) FROM jobs WHERE id=?", (job["id"],))
        budget = (
            cost.fetchone()[0]
            + db.execute(
                "SELECT SUM(length(CAST(context AS BLOB))) FROM result_versions"
            ).fetchone()[0]
        )
    connection, reads = store.connection, []

    class Counted:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, parameters=()):
            if "SELECT params FROM jobs" in sql:
                reads.append(parameters)
            return self.db.execute(sql, parameters)

    @contextmanager
    def counted():
        with connection() as db:
            yield Counted(db)

    monkeypatch.setattr(store, "connection", counted)
    monkeypatch.setattr(research_reports, "MAX_CONTEXT_BYTES", budget)
    manifest, _ = report(store, project, rid, derived, head)
    assert len(manifest["versions"]) == 8
    assert reads == [(job["id"],)]


def test_source_job_metadata_is_preflighted_before_loading(tmp_path, monkeypatch):
    store, project, rid, _, _, _, derived, creation = seeded(tmp_path, text="Source " * 2000)
    connection = store.connection

    class Guarded:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, parameters=()):
            assert "SELECT params FROM jobs" not in sql, "Source job was loaded before its budget"
            return self.db.execute(sql, parameters)

    @contextmanager
    def guarded():
        with connection() as db:
            yield Guarded(db)

    monkeypatch.setattr(store, "connection", guarded)
    monkeypatch.setattr(research_reports, "MAX_CONTEXT_BYTES", 5000)
    with pytest.raises(ValueError, match="source"):
        report(store, project, rid, derived, creation)


def test_source_graph_handles_the_entire_version_budget_without_recursion():
    graph = {
        str(index): {"predecessor_id": None, "source_version_id": str(index - 1) if index else None}
        for index in range(research_reports.MAX_VERSIONS)
    }
    research_reports._verify_source_graph(graph)
    graph["0"]["source_version_id"] = str(research_reports.MAX_VERSIONS - 1)
    with pytest.raises(ValueError, match="cycle"):
        research_reports._verify_source_graph(graph)
