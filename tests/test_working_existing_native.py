"""T147/ADR 0179 builder checks: genuine schema-19 designation and the native recovery fence.

Basic synthetic cases only. Independent adversarial schema-19, native-dependency and
contained capture/restore suites are separate.
"""

import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from test_working_existing import CommitHook

from cbs_transcriber import passage_review, result_roles, working_existing
from cbs_transcriber import store as store_module
from cbs_transcriber.app import create_app
from cbs_transcriber.store import new_id, now
from cbs_transcriber.transcripts import normalize_segments

NATIVE = {"provider_type": "ucloud", "model": "large-v3"}
TEXTS = ("First passage.", "Second passage.", "Third passage.")
# Python's parser, as used by native admission, keeps the last duplicate; SQLite the first.
DUPLICATE_TYPE = '{"settings":{"provider_type":"custom","provider_type":"ucloud"}}'
DUPLICATE_MEMBER = (
    '[{"job_id": "j", "job_id": "k", "recording_id": "r"}, {"job_id": "l", "recording_id": "s"}]'
)


@pytest.fixture
def store(tmp_path):
    current = store_module.Store(tmp_path / "synthetic-library")
    assert_genuine_schema19(current)
    return current


def assert_genuine_schema19(store):
    assert store_module.SCHEMA_VERSION == 19
    with store.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 19
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='passage_reviews'"
        ).fetchone()


def sole(store, project, name="synthetic.wav"):
    rid = store.create_recording(project["id"], name)["id"]
    segments = [{"start": n, "end": n + 1, "text": text} for n, text in enumerate(TEXTS)]
    store.add_result(
        rid, normalize_segments(segments), {"SPEAKER_00": "Synthetic voice"}, "Synthetic", {}
    )
    return rid


def job(store, recording_id, state, params, kind="transcribe"):
    identifier = new_id()
    store.execute(
        "INSERT INTO jobs(id,recording_id,kind,state,params,created,updated) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            identifier,
            recording_id,
            kind,
            state,
            params if isinstance(params, str) else json.dumps(params),
            now(),
            now(),
        ),
    )
    return identifier


def owner(store, project_id, state, members, identifier=None):
    identifier = identifier or new_id()
    store.execute(
        "INSERT INTO native_batch_runs(id,project_id,state,params,members,created,updated) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            identifier,
            project_id,
            state,
            json.dumps({"settings": NATIVE, "ucloud_run": {}}),
            members if isinstance(members, str) else json.dumps(members),
            now(),
            now(),
        ),
    )
    return identifier


def batch(store, project, state, recordings, job_state="completed"):
    """A native owner whose members' jobs link back exactly."""
    identifier, manifest = new_id(), []
    for rid in recordings:
        params = {"settings": NATIVE, "native_batch_id": identifier}
        job_id = job(store, rid, job_state, params)
        manifest.append({"job_id": job_id, "recording_id": rid, "filename": job_id + ".wav"})
    return owner(store, project["id"], state, manifest, identifier), manifest


def role(store, recording_id):
    with store.connection() as db:
        return result_roles.read(db, store, recording_id, verify_tail=True)


def designated(store, recording_id):
    working = role(store, recording_id)["working"]
    return working and working["version_id"]


def events(store):
    return store.all("SELECT * FROM result_role_events ORDER BY recording_id,sequence")


def reviews(store):
    return store.all("SELECT * FROM passage_reviews ORDER BY result_id,segment_id")


def mark_every_status(store, recording_id):
    """Each review status plus a stale mark and one for a passage that no longer exists."""
    result_id = store.recording(recording_id)["active_transcript_id"]
    view = passage_review.read(store, recording_id, result_id)
    statuses = ("checked", "needs_review", "not_checked")
    for passage, status in zip(view["passages"], statuses, strict=False):
        passage_review.save(
            store,
            recording_id,
            passage["segment_id"],
            result_id=result_id,
            revision=view["revision"],
            status=status,
            review_revision=0,
            passage_sha256=passage["passage_sha256"],
        )
    stale = view["passages"][0]["segment_id"]
    store.execute(
        "UPDATE passage_reviews SET passage_sha256=? WHERE result_id=? AND segment_id=?",
        ("a" * 64, result_id, stale),
    )
    store.execute(
        "INSERT INTO passage_reviews VALUES (?,?,?,?,?,?)",
        (result_id, "removed-passage", "b" * 64, "checked", 3, "2026-11-01T00:00:00+00:00"),
    )
    return result_id


def test_schema19_designation_preserves_review_marks_and_ignores_them(store):
    project = store.create_project("Synthetic")
    reviewed = sole(store, project, "reviewed.wav")
    result_id = mark_every_status(store, reviewed)
    unreviewed = sole(store, project, "unreviewed.wav")
    edited = sole(store, project, "edited.wav")
    current = store.recording(edited)
    segments = normalize_segments([{"start": 0, "end": 1, "text": "Edited before."}])
    store.save_transcript(edited, segments, current["speakers"], current["revision"])
    mark_every_status(store, edited)
    marks, view = reviews(store), passage_review.read(store, reviewed, result_id)
    assert view["counts"] == {"checked": 0, "needs_review": 2, "not_checked": 1, "total": 3}
    recording_before = store.recording(reviewed)

    summary = working_existing.designate_existing(store)

    # Marks are neither consent nor a veto: Needs review and unreviewed results are both
    # designated, and marks never make an edited result eligible.
    assert summary["designated"] == 2 and summary["skipped"] == {"versions": 1}
    assert designated(store, reviewed) and designated(store, unreviewed)
    assert role(store, edited) == {"revision": 0, "working": None}
    assert [(e["action"], e["actor_kind"]) for e in events(store)] == [
        ("working_auto_first", "system")
    ] * 2
    # Exact rows, revisions, timestamps and stale entries are untouched.
    assert reviews(store) == marks
    assert passage_review.read(store, reviewed, result_id) == view
    after = store.recording(reviewed)
    assert (after["revision"], after["segments"]) == (
        recording_before["revision"],
        recording_before["segments"],
    )


def test_schema19_label_without_review_table_is_refused(store):
    rid = sole(store, store.create_project("Synthetic"))
    store.execute("DROP TABLE passage_reviews")
    summary = working_existing.designate_existing(store)
    assert summary["refused"] == "unsupported_schema" and summary["pages"] == 0
    assert not events(store) and not designated(store, rid)


def test_failed_native_or_unprovable_failures_skip_and_proven_failures_do_not(store):
    project = store.create_project("Synthetic")
    cases = {}

    def case(name, state, params, kind="transcribe"):
        cases[name] = sole(store, project, f"{name}.wav")
        job(store, cases[name], state, params, kind)

    case("native_failed", "failed", {"settings": NATIVE, "ucloud_run": {"job_id": "remote"}})
    case("missing_settings", "failed", {"provider_name": "Legacy"})
    case("malformed_params", "failed", "{not json")
    case("duplicate_type", "failed", DUPLICATE_TYPE)
    case("non_text_kind", "failed", {"settings": {"provider_type": "custom"}}, b"transcribe")
    case("custom_failed", "failed", {"settings": {"provider_type": "custom"}})
    case("vllm_failed", "failed", {"settings": {"provider_type": "ucloud-vllm"}})
    case("inferred_failed", "failed", {"settings": {"model": "whisper-1"}})
    case("media_failed", "failed", "{not json", kind="media")
    case("native_cancelled", "cancelled", {"settings": NATIVE})
    case("native_completed", "completed", {"settings": NATIVE})

    summary = working_existing.designate_existing(store)

    assert summary["skipped"] == {"native_recovery": 5}, summary
    blocked = {"native_failed", "missing_settings", "malformed_params"}
    blocked |= {"duplicate_type", "non_text_kind"}
    for name, rid in cases.items():
        assert bool(designated(store, rid)) is (name not in blocked), name


@pytest.mark.parametrize("state", ["failed", "interrupted", "queued", "running", "unknown"])
def test_unsettled_owner_blocks_completed_members_and_claims_without_jobs(store, state):
    project = store.create_project("Synthetic")
    completed_member, claimed_only = sole(store, project, "a.wav"), sole(store, project, "b.wav")
    _, manifest = batch(store, project, state, [completed_member])
    manifest.append({"job_id": new_id(), "recording_id": claimed_only, "filename": "x.wav"})
    store.execute("UPDATE native_batch_runs SET members=?", (json.dumps(manifest),))
    settled = [sole(store, project, f"s{n}.wav") for n in range(4)]
    batch(store, project, "completed", settled[:2])
    batch(store, project, "cancelled", settled[2:])

    summary = working_existing.designate_existing(store)

    assert summary["skipped"] == {"native_recovery": 2}, summary
    assert not designated(store, completed_member) and not designated(store, claimed_only)
    assert all(designated(store, rid) for rid in settled)


def test_missing_or_conflicting_owner_links_block_both_directions(store):
    project = store.create_project("Synthetic")
    rid = {name: sole(store, project, f"{name}.wav") for name in "abcdefgh"}
    # Job -> owner: the named owner is missing, or is settled but does not list the job.
    job(store, rid["a"], "completed", {"settings": NATIVE, "native_batch_id": new_id()})
    listed, _ = batch(store, project, "completed", [rid["c"], rid["d"]])
    job(store, rid["b"], "completed", {"settings": NATIVE, "native_batch_id": listed})
    # Owner -> job: a settled manifest names a job that does not link back, and a job whose
    # actual recording differs from the claimed one.
    unlinked = job(store, rid["e"], "completed", {"settings": NATIVE})
    elsewhere = new_id()
    moved = job(store, rid["h"], "completed", {"settings": NATIVE, "native_batch_id": elsewhere})
    linked = job(store, rid["f"], "completed", {"settings": NATIVE, "native_batch_id": elsewhere})
    owner(
        store,
        project["id"],
        "completed",
        [
            {"job_id": unlinked, "recording_id": rid["e"]},
            {"job_id": linked, "recording_id": rid["f"]},
        ],
    )
    owner(
        store,
        project["id"],
        "completed",
        [
            {"job_id": moved, "recording_id": rid["g"]},
            {"job_id": linked, "recording_id": rid["f"]},
        ],
        elsewhere,
    )

    summary = working_existing.designate_existing(store)

    blocked = {"a", "b", "e", "g", "h", "f"}  # f is listed by a second, non-linked owner too.
    assert summary["skipped"] == {"native_recovery": len(blocked)}, summary
    for name, recording_id in rid.items():
        assert bool(designated(store, recording_id)) is (name not in blocked), name


def test_non_text_owner_link_blocks_its_recording(store):
    project = store.create_project("Synthetic")
    rid = sole(store, project)
    job(store, rid, "completed", {"settings": NATIVE, "native_batch_id": 7})
    assert working_existing.designate_existing(store)["skipped"] == {"native_recovery": 1}
    assert not designated(store, rid)


@pytest.mark.parametrize(
    "members",
    [
        "{not json",
        "[]",
        '{"members": []}',
        '[{"job_id": 1, "recording_id": "r"}, {"job_id": "j", "recording_id": "s"}]',
        DUPLICATE_MEMBER,
        '["j", "k"]',
        "oversized",
    ],
)
@pytest.mark.parametrize("state", ["completed", "failed"])
def test_opaque_owner_inventory_refuses_the_pass(store, monkeypatch, members, state):
    project = store.create_project("Synthetic")
    rid = sole(store, project)
    if members == "oversized":
        _, manifest = batch(store, project, state, [sole(store, project, f"{n}.wav") for n in "xy"])
        monkeypatch.setattr(working_existing, "NATIVE_MANIFEST_BYTES", 8)
    else:
        owner(store, project["id"], state, members)

    summary = working_existing.designate_existing(store)

    assert summary["refused"] == "native_evidence_unverifiable", summary
    assert summary["designated"] == summary["pages"] == 0 and not summary["incomplete"]
    assert not events(store) and not designated(store, rid)


@pytest.mark.parametrize("bound", ["NATIVE_OWNERS", "NATIVE_LINKS", "NATIVE_MEMBERS"])
def test_inventory_bound_exhaustion_is_not_a_complete_scan(store, monkeypatch, bound):
    project = store.create_project("Synthetic")
    rid = sole(store, project)
    batch(store, project, "completed", [sole(store, project, f"{n}.wav") for n in "ab"])
    batch(store, project, "completed", [sole(store, project, f"{n}.wav") for n in "cd"])
    monkeypatch.setattr(working_existing, bound, {"NATIVE_MEMBERS": 3}.get(bound, 1))

    summary = working_existing.designate_existing(store)

    assert summary["refused"] == "native_evidence_incomplete", summary
    assert not events(store) and not designated(store, rid)


def test_foreign_commits_refresh_native_negatives_and_late_opaque_evidence_stops(
    store, monkeypatch
):
    project = store.create_project("Synthetic")
    rids = sorted(sole(store, project, f"{n}.wav") for n in range(3))
    original = store.connection
    commits = []

    def foreign():
        commits.append(None)
        if len(commits) == 1:
            # Committed by another connection: cached negatives must not be reused.
            job(store, rids[1], "failed", {"settings": NATIVE})
        elif len(commits) == 2:
            owner(store, project["id"], "completed", "{opaque")

    @contextmanager
    def hooked():
        with original() as db:
            yield CommitHook(db, foreign)

    monkeypatch.setattr(store, "connection", hooked)
    summary = working_existing.designate_existing(store, page=1)
    monkeypatch.setattr(store, "connection", original)

    assert summary["job_scans"] == 2 and summary["pages"] == 2
    assert summary["skipped"] == {"native_recovery": 1}
    # The first page stays committed and is reported as partial progress, not rolled back.
    assert summary["designated"] == 1 and summary["incomplete"]
    assert summary["refused"] == "native_evidence_unverifiable"
    assert designated(store, rids[0])
    assert not designated(store, rids[1]) and not designated(store, rids[2])


def test_schema19_startup_designates_and_refreshes_the_snapshot(tmp_path):
    root = tmp_path / "library"
    store = store_module.Store(root)
    assert_genuine_schema19(store)
    project = store.create_project("Synthetic")
    rid = sole(store, project)
    mark_every_status(store, rid)
    marks = reviews(store)
    app = create_app(root, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1", headers={"X-CBS-Request": "1"}) as client:
        summary = app.state.working_existing
        assert summary["designated"] == 1 and summary["snapshots"]["refreshed"] == 1
        view = client.get(f"/api/recordings/{rid}").json()
        assert view["result_roles"]["working"]["version_id"] == designated(store, rid)
        assert view["snapshot_error"] is None
    snapshot = json.loads(
        (store.project_folder(project["id"]) / "project.json").read_text(encoding="utf-8")
    )
    [entry] = [item for item in snapshot["recordings"] if item["id"] == rid]
    assert entry["result_roles"] == role(store, rid)
    assert reviews(store) == marks
