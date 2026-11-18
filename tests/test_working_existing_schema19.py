"""Independent adversarial tests for T147/V222 (ADR 0179 on top of ADR 0141).

The startup pass may designate an untouched sole existing result as Working on genuine
schema-19 libraries. Passage review marks are neither consent nor a veto, and a negative-only
native-recovery fence (ADR 0174 dependency) must skip or refuse every recording that native
startup admission could still change. Synthetic temporary libraries only: no provider,
network, real library or running app is touched.
"""

import json
import sqlite3
import threading
import time
from collections import Counter
from contextlib import closing, contextmanager
from types import SimpleNamespace

import pytest

from cbs_transcriber import passage_review, result_roles, working_existing
from cbs_transcriber import store as store_module
from cbs_transcriber.store import new_id, now
from cbs_transcriber.transcripts import normalize_segments

TEXTS = ("Første passage, æøå.", "Second passage.", "Third passage.")
NATIVE = {"provider_type": "ucloud", "model": "large-v3"}
CUSTOM = {"provider_type": "custom", "base_url": "https://asr.example.invalid/v1"}
STAMP = "2026-11-01T00:00:00+00:00"
ROLE_TABLES = ("recording_result_roles", "result_role_events")


# --------------------------------------------------------------------------- helpers


@pytest.fixture
def store(tmp_path):
    current = store_module.Store(tmp_path / "synthetic-library")
    assert_genuine_schema19(current)
    return current


def assert_genuine_schema19(store):
    assert store_module.SCHEMA_VERSION == 19
    with closing(sqlite3.connect(store.db_path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 19
        assert db.execute(
            "SELECT type FROM sqlite_master WHERE name='passage_reviews'"
        ).fetchone() == ("table",)


@contextmanager
def raw(store):
    """A plain foreign connection: no foreign keys, so crafted damaged rows can be written."""
    with closing(sqlite3.connect(store.db_path, timeout=15)) as db:
        with db:
            yield db


def sole(store, project, name="synthetic.wav"):
    rid = store.create_recording(project["id"], name)["id"]
    segments = [{"start": n, "end": n + 1, "text": text} for n, text in enumerate(TEXTS)]
    store.add_result(
        rid, normalize_segments(segments), {"SPEAKER_00": "Synthetic voice"}, "Synthetic", {}
    )
    return rid


def bare_recording(db, project_id, identifier=None):
    """A recording row without any result: never a candidate itself."""
    identifier = identifier or new_id()
    db.execute(
        "INSERT INTO recordings(id,project_id,name,filename,created) VALUES (?,?,?,?,?)",
        (identifier, project_id, "bare", "bare.wav", now()),
    )
    return identifier


def job(store, recording_id, state, params, kind="transcribe", identifier=None):
    identifier = new_id() if identifier is None else identifier
    if isinstance(params, dict | list):
        params = json.dumps(params)
    with raw(store) as db:
        db.execute(
            "INSERT INTO jobs(id,recording_id,kind,state,params,created,updated) "
            "VALUES (?,?,?,?,?,?,?)",
            (identifier, recording_id, kind, state, params, now(), now()),
        )
    return identifier


def owner(store, project_id, state, members, identifier=None):
    identifier = new_id() if identifier is None else identifier
    if isinstance(members, list):
        members = json.dumps(members)
    with raw(store) as db:
        db.execute(
            "INSERT INTO native_batch_runs(id,project_id,state,params,members,created,updated) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                identifier,
                project_id,
                state,
                json.dumps({"settings": NATIVE, "ucloud_run": {"job_id": "remote"}}),
                members,
                now(),
                now(),
            ),
        )
    return identifier


def settled_batch(store, project, recordings, state="completed"):
    """A native owner whose member jobs link back exactly (the only settled shape)."""
    identifier, manifest = new_id(), []
    for rid in recordings:
        job_id = job(store, rid, "completed", {"settings": NATIVE, "native_batch_id": identifier})
        manifest.append({"job_id": job_id, "recording_id": rid, "filename": job_id + ".wav"})
    owner(store, project["id"], state, manifest, identifier)
    return identifier, manifest


def role(store, recording_id):
    with store.connection() as db:
        return result_roles.read(db, store, recording_id, verify_tail=True)


def designated(store, recording_id):
    working = role(store, recording_id)["working"]
    return working and working["version_id"]


def events(store, recording_id=None):
    with closing(sqlite3.connect(store.db_path)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT * FROM result_role_events "
            + ("WHERE recording_id=? " if recording_id else "")
            + "ORDER BY recording_id,sequence",
            (recording_id,) if recording_id else (),
        ).fetchall()
    return [dict(row) for row in rows]


def creation(store, recording_id):
    result_id = store.recording(recording_id)["active_transcript_id"]
    return store.result_versions(recording_id, result_id)[-1]


def state(store):
    """Schema, every raw row (bytes, so damaged text is compared too) and review rowids."""
    with closing(sqlite3.connect(store.db_path)) as db:
        db.text_factory = bytes
        schema = db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall()
        rows = Counter()
        for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall():
            table = name.decode()
            for row in db.execute(f'SELECT * FROM "{table}"'):
                rows[table, row] += 1
        reviews = (
            db.execute("SELECT rowid,* FROM passage_reviews ORDER BY rowid").fetchall()
            if any(item[:2] == (b"table", b"passage_reviews") for item in schema)
            else None
        )
    return schema, rows, reviews


def assert_only_roles_written(before, after, recordings):
    """Nothing removed or changed; only one role row and one event per designation."""
    assert before[0] == after[0]  # schema
    assert not before[1] - after[1], list((before[1] - after[1]).items())[:3]
    added = list((after[1] - before[1]).elements())
    assert before[2] == after[2]  # passage_reviews: rows, revisions, timestamps, rowids
    assert all(table in ROLE_TABLES for table, _ in added), added[:3]
    assert len(added) == 2 * len(recordings), added
    for rid in recordings:
        assert sum(rid.encode() in row for _, row in added) == 2, rid


def review(store, recording_id, statuses):
    """Save each status through the real API against the current saved passages."""
    result_id = store.recording(recording_id)["active_transcript_id"]
    view = passage_review.read(store, recording_id, result_id)
    for passage, status in zip(view["passages"], statuses, strict=False):
        if status is not None:
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
    return result_id


def stale_and_removed(store, result_id, segment_id):
    """A stale fingerprint and a mark for a passage that no longer exists."""
    with raw(store) as db:
        db.execute(
            "UPDATE passage_reviews SET passage_sha256=? WHERE result_id=? AND segment_id=?",
            ("a" * 64, result_id, segment_id),
        )
        db.execute(
            "INSERT INTO passage_reviews VALUES (?,?,?,?,?,?)",
            (result_id, "removed-passage", "b" * 64, "checked", 2**63 - 1, STAMP),
        )


def mark(store, recording_id, version_id):
    current = store.recording(recording_id)
    roles = role(store, recording_id)
    plan = result_roles.preview(
        store, recording_id, version_id, current["revision"], roles["revision"]
    )
    return result_roles.apply(
        store, recording_id, version_id, current["revision"], roles["revision"], plan["fingerprint"]
    )


class CommitHook:
    """Delegate to the pass's real connection; run a foreign writer after each page commit."""

    def __init__(self, db, after_commit):
        self._db, self._after = db, after_commit

    def __getattr__(self, name):
        return getattr(self._db, name)

    def commit(self):
        self._db.commit()
        self._after()


def hook_pass(monkeypatch, store, after_commit):
    """Only the pass's own (first) connection is hooked; foreign writers use plain ones."""
    original, opened = store.connection, []

    @contextmanager
    def hooked():
        with original() as db:
            if opened:
                yield db
            else:
                opened.append(None)
                yield CommitHook(db, after_commit)

    monkeypatch.setattr(store, "connection", hooked)
    return lambda: monkeypatch.setattr(store, "connection", original)


# --------------------------------------------------------------------------- schema pairs


@pytest.mark.parametrize(
    "created,runtime,relabel",
    [
        (18, 18, None),  # reviewed pair
        (19, 19, None),  # reviewed pair
        (18, 19, None),  # mismatch
        (17, 17, None),  # unreviewed
        (19, 20, None),  # future runtime against a schema-19 file
        (19, 20, 20),  # unreviewed future pair
    ],
)
def test_only_reviewed_schema_pairs_designate(tmp_path, monkeypatch, created, runtime, relabel):
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", created)
    current = store_module.Store(tmp_path / f"schema-{created}-{runtime}")
    project = current.create_project("Synthetic")
    rid = sole(current, project)
    with closing(sqlite3.connect(current.db_path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == created
        has_reviews = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='passage_reviews'"
        ).fetchone()
    assert bool(has_reviews) is (created >= 19)  # Genuine libraries, never relabelled fixtures.
    if relabel:
        with raw(current) as db:
            db.execute(f"PRAGMA user_version={relabel}")
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", runtime)
    before = state(current)

    summary = working_existing.startup(current)

    if (created, runtime) in {(18, 18), (19, 19)}:
        assert summary["designated"] == 1 and summary["refused"] is None
        assert designated(current, rid) == creation(current, rid)["id"]
        assert_only_roles_written(before, state(current), [rid])
    else:
        assert summary["refused"] == "unsupported_schema"
        assert summary["designated"] == summary["pages"] == summary["job_scans"] == 0
        assert state(current) == before


def test_newer_library_than_runtime_aborts_without_writing(store, monkeypatch):
    project = store.create_project("Synthetic")
    rid = sole(store, project)
    before = state(store)
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 18)  # 19 library, 18 runtime

    summary = working_existing.startup(store)

    assert summary["designated"] == 0 and (summary["refused"] or summary["aborted"])
    assert state(store) == before
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 19)
    assert role(store, rid) == {"revision": 0, "working": None}


@pytest.mark.parametrize("variant", ["dropped", "view", "renamed", "case"])
def test_schema19_label_without_a_real_review_table_is_unsupported(store, variant):
    project = store.create_project("Synthetic")
    rid = sole(store, project)
    with raw(store) as db:
        if variant == "view":
            db.execute("ALTER TABLE passage_reviews RENAME TO passage_reviews_data")
            db.execute("CREATE VIEW passage_reviews AS SELECT * FROM passage_reviews_data")
        elif variant == "renamed":
            db.execute("ALTER TABLE passage_reviews RENAME TO passage_review")
        elif variant == "case":
            db.execute("ALTER TABLE passage_reviews RENAME TO tmp_reviews")
            db.execute('ALTER TABLE tmp_reviews RENAME TO "Passage_Reviews"')
        else:
            db.execute("DROP TABLE passage_reviews")
    folder = store.project_folder(project["id"])
    files = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    before = state(store)

    summary = working_existing.startup(store)

    assert summary["refused"] == "unsupported_schema" and summary["pages"] == 0
    assert state(store) == before
    assert {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()} == files
    assert not events(store, rid)


# --------------------------------------------------------------------------- review marks


def test_every_review_state_is_neither_consent_nor_veto_and_stays_byte_identical(store):
    project = store.create_project("Synthetic")
    rid = {name: sole(store, project, f"{name}.wav") for name in "ABCDEFGH"}
    # A: each status, one stale fingerprint and one removed passage at the revision limit.
    result_a = review(store, rid["A"], ("checked", "needs_review", "not_checked"))
    first = passage_review.read(store, rid["A"], result_a)["passages"][0]["segment_id"]
    stale_and_removed(store, result_a, first)
    review(store, rid["B"], ("checked",) * 3)  # Fully checked: no consent required.
    review(store, rid["C"], ("needs_review",) * 3)  # Needs review everywhere: no veto.
    # D: an edited sole result fully checked; marks never make it eligible.
    current = store.recording(rid["D"])
    store.save_transcript(
        rid["D"],
        normalize_segments([{"start": 0, "end": 1, "text": "Edited before the pass."}]),
        current["speakers"],
        current["revision"],
    )
    review(store, rid["D"], ("checked",))
    # E: a second live result, the active one fully checked.
    store.add_result(
        rid["E"],
        normalize_segments([{"start": 0, "end": 1, "text": "Alternative."}]),
        {},
        "Alternative",
        {},
    )
    review(store, rid["E"], ("checked",) * 3)
    # F: a failed native transcription; G: unsettled local processing. Both fully checked.
    job(store, rid["F"], "failed", {"settings": NATIVE, "ucloud_run": {"job_id": "remote"}})
    review(store, rid["F"], ("checked",) * 3)
    store.add_job(rid["G"], "media", {})
    review(store, rid["G"], ("checked",) * 3)
    # H: never reviewed.
    views = {
        name: passage_review.read(store, r, store.recording(r)["active_transcript_id"])
        for name, r in rid.items()
    }
    assert views["A"]["counts"] == {
        "checked": 0,
        "needs_review": 2,
        "not_checked": 1,
        "total": 3,
    }
    assert any(p["stale"] for p in views["A"]["passages"])
    recordings = {name: store.recording(r) for name, r in rid.items()}
    before = state(store)

    summary = working_existing.designate_existing(store)

    assert summary["designated"] == 4, summary
    assert summary["skipped"] == {
        "versions": 1,
        "results": 1,
        "native_recovery": 1,
        "pending_processing": 1,
    }
    chosen = [rid[name] for name in "ABCH"]
    for name, r in rid.items():
        assert bool(designated(store, r)) is (name in "ABCH"), name
    assert_only_roles_written(before, state(store), chosen)
    for name, r in rid.items():
        # Review views, recording revisions and projections are unchanged.
        after = store.recording(r)
        assert passage_review.read(store, r, after["active_transcript_id"]) == views[name]
        assert (after["revision"], after["segments"], after["speakers"]) == (
            recordings[name]["revision"],
            recordings[name]["segments"],
            recordings[name]["speakers"],
        )
    for r in chosen:
        [event] = events(store, r)
        version = creation(store, r)
        assert (event["action"], event["actor_kind"], event["sequence"]) == (
            "working_auto_first",
            "system",
            1,
        )
        assert (event["previous_version_id"], event["previous_payload_sha256"]) == (None, None)
        assert (event["next_version_id"], event["next_payload_sha256"]) == (
            version["id"],
            version["payload_sha256"],
        )
    # Snapshot reconciliation and an idempotent rerun leave marks alone as well.
    reviewed = state(store)
    working_existing.reconcile_snapshots(store)
    again = working_existing.designate_existing(store)
    assert again["designated"] == 0
    assert state(store) == reviewed


def test_review_save_waiting_on_the_pass_lands_afterwards_without_conflict(store, monkeypatch):
    project = store.create_project("Synthetic")
    rid = sole(store, project)
    result_id = store.recording(rid)["active_transcript_id"]
    view = passage_review.read(store, rid, result_id)  # Displayed before the pass.
    passage = view["passages"][1]
    inside, outcome = threading.Event(), {}
    original = working_existing.result_roles.move_automatic

    def slow(*args, **kwargs):
        written = original(*args, **kwargs)
        inside.set()
        time.sleep(0.3)  # The reviewer's save waits on the open writer transaction.
        return written

    def reviewer():
        inside.wait(5)
        try:
            outcome["saved"] = passage_review.save(
                store,
                rid,
                passage["segment_id"],
                result_id=result_id,
                revision=view["revision"],
                status="needs_review",
                review_revision=0,
                passage_sha256=passage["passage_sha256"],
            )
        except Exception as error:  # noqa: BLE001 - reported below
            outcome["error"] = repr(error)

    monkeypatch.setattr(working_existing.result_roles, "move_automatic", slow)
    thread = threading.Thread(target=reviewer)
    thread.start()
    assert working_existing.designate_existing(store)["designated"] == 1
    thread.join(10)
    assert "error" not in outcome, outcome
    assert outcome["saved"]["passage"]["review_revision"] == 1
    assert designated(store, rid) and len(events(store, rid)) == 1
    with closing(sqlite3.connect(store.db_path)) as db:
        assert db.execute("SELECT segment_id,status,revision FROM passage_reviews").fetchall() == [
            (passage["segment_id"], "needs_review", 1)
        ]


def test_review_mark_committed_between_pages_rescans_but_never_vetoes(store, monkeypatch):
    project = store.create_project("Synthetic")
    rids = sorted(sole(store, project, f"{n}.wav") for n in range(3))
    commits = []

    def foreign():
        commits.append(None)
        if len(commits) == 1:
            review(store, rids[2], ("needs_review", "checked", None))

    restore = hook_pass(monkeypatch, store, foreign)
    summary = working_existing.designate_existing(store, page=1)
    restore()

    assert summary["job_scans"] == 2  # A foreign commit invalidates cached negatives.
    assert summary["designated"] == 3 and all(designated(store, r) for r in rids)
    view = passage_review.read(store, rids[2], store.recording(rids[2])["active_transcript_id"])
    assert view["counts"]["needs_review"] == 1 and view["counts"]["checked"] == 1


# --------------------------------------------------------------------------- native: failed rows

DEEP = "[" * 50000 + "]" * 50000  # Beyond both parsers' nesting limits.
AMBIGUOUS = {
    # SQLite's JSON functions read the first duplicate; Python's parser keeps the last.
    "duplicate_settings_last_native": (
        '{"settings":{"provider_type":"custom"},"settings":{"provider_type":"ucloud"}}'
    ),
    "duplicate_type_last_native": (
        '{"settings":{"provider_type":"custom","provider_type":"ucloud"}}'
    ),
    "duplicate_type_first_native": (
        '{"settings":{"provider_type":"ucloud","provider_type":"custom"}}'
    ),
    "escaped_duplicate_type": (
        '{"settings":{"provider_type":"custom","provider\\u005ftype":"ucloud"}}'
    ),
    "nan_native": '{"settings":{"provider_type":"ucloud"},"chunk":NaN}',
    "infinity_unknown_type": '{"settings":{"provider_type":"UCloud"},"chunk":Infinity}',
    "json5_unquoted": '{settings:{provider_type:"custom"}}',
    "json5_trailing_comma": '{"settings":{"provider_type":"custom"},}',
    "json5_comment": '{"settings":{"provider_type":"custom"}} /* native */',
    "trailing_nul": '{"settings":{"provider_type":"custom"}}\x00',
    "list_type": '{"settings":{"provider_type":["ucloud"]}}',
    "boolean_type": '{"settings":{"provider_type":true}}',
    "empty_type": '{"settings":{"provider_type":""}}',
    "unknown_type": '{"settings":{"provider_type":"ucloud-native"}}',
    "case_variant_type": '{"settings":{"provider_type":"Custom"}}',
    "settings_text": '{"settings":"custom"}',
    "settings_null": '{"settings":null}',
    "settings_list": '{"settings":[{"provider_type":"custom"}]}',
    "settings_missing": '{"provider_name":"Legacy"}',
    "top_level_list": '[{"settings":{"provider_type":"custom"}}]',
    "empty_text": "",
    "deeply_nested": '{"settings":{"provider_type":"custom"},"x":' + DEEP + "}",
    "blob_params": json.dumps({"settings": {"provider_type": "custom"}}).encode(),
}


@pytest.mark.parametrize("case", sorted(AMBIGUOUS))
def test_ambiguous_failed_transcription_settings_skip_without_writing(store, case):
    project = store.create_project("Synthetic")
    target = sole(store, project, "target.wav")
    job(store, target, "failed", AMBIGUOUS[case])
    # Controls: a proven non-native failure and a non-transcription failure are not native.
    proven = sole(store, project, "proven.wav")
    job(store, proven, "failed", {"settings": CUSTOM, "ucloud_run": {"job_id": "remote"}})
    media = sole(store, project, "media.wav")
    job(store, media, "failed", "{not json", kind="media")
    before = state(store)

    summary = working_existing.designate_existing(store)

    assert summary["skipped"] == {"native_recovery": 1}, summary
    assert role(store, target) == {"revision": 0, "working": None}
    assert_only_roles_written(before, state(store), [proven, media])


@pytest.mark.parametrize(
    "kind,params",
    [
        (b"transcribe", {"settings": CUSTOM}),
        (b"transcribe", {"settings": NATIVE}),
        (b"Transcribe", {"settings": NATIVE}),
    ],
)
def test_non_text_job_kind_proves_nothing(store, kind, params):
    project = store.create_project("Synthetic")
    rid = sole(store, project)
    job(store, rid, "failed", params, kind=kind)
    before = state(store)
    summary = working_existing.designate_existing(store)
    assert summary["skipped"] == {"native_recovery": 1} and state(store) == before


@pytest.mark.parametrize("value", ["FAILED", "failed ", b"failed", "interrupted", "queued"])
def test_failed_like_and_resumable_states_never_designate(store, value):
    project = store.create_project("Synthetic")
    rid = sole(store, project)
    job(store, rid, value, {"settings": NATIVE, "ucloud_run": {"job_id": "remote"}})
    before = state(store)
    summary = working_existing.designate_existing(store)
    assert summary["designated"] == 0 and sum(summary["skipped"].values()) == 1
    assert state(store) == before


@pytest.mark.parametrize("offset,eligible", [("bytes", True), ("bytes-1", False), ("chars", False)])
def test_failed_parameter_bound_counts_utf8_bytes(store, monkeypatch, offset, eligible):
    project = store.create_project("Synthetic")
    rid = sole(store, project)
    params = json.dumps({"settings": CUSTOM, "label": "æøå" * 40}, ensure_ascii=False)
    size, chars = len(params.encode("utf-8")), len(params)
    assert size > chars
    job(store, rid, "failed", params)
    bound = {"bytes": size, "bytes-1": size - 1, "chars": chars}[offset]
    monkeypatch.setattr(working_existing, "NATIVE_PARAMS_BYTES", bound)
    summary = working_existing.designate_existing(store)
    assert bool(designated(store, rid)) is eligible, summary
    if not eligible:
        assert summary["skipped"] == {"native_recovery": 1}


# --------------------------------------------------------------------------- native: owners/links


@pytest.mark.parametrize(
    "owner_state",
    [
        "failed",
        "interrupted",
        "queued",
        "running",
        "Completed",
        "completed ",
        "cancelled\n",
        "",
        "stopping",
        b"completed",
        7,
    ],
)
def test_unsettled_or_unknown_owner_blocks_every_member_in_both_forms(store, owner_state):
    project = store.create_project("Synthetic")
    completed, failed, claimed = (sole(store, project, f"{n}.wav") for n in "abc")
    control = sole(store, project, "control.wav")
    identifier, manifest = settled_batch(store, project, [completed], state="completed")
    failed_job = job(store, failed, "failed", {"settings": CUSTOM, "native_batch_id": identifier})
    manifest += [
        {"job_id": failed_job, "recording_id": failed, "filename": "f.wav"},
        {"job_id": new_id(), "recording_id": claimed, "filename": "missing.wav"},
    ]
    with raw(store) as db:
        db.execute(
            "UPDATE native_batch_runs SET state=?,members=? WHERE id=?",
            (owner_state, json.dumps(manifest), identifier),
        )
    before = state(store)

    summary = working_existing.designate_existing(store)

    assert summary["skipped"] == {"native_recovery": 3}, summary
    assert_only_roles_written(before, state(store), [control])


def test_link_conflicts_in_both_directions_block_without_writing(store):
    project = store.create_project("Synthetic")
    rid = {n: sole(store, project, f"{n}.wav") for n in range(1, 15)}
    settled, manifest = settled_batch(store, project, [rid[1], rid[2]])  # exact: designated
    # 3: listed under a different claimed recording (4) than its actual one.
    moved = job(store, rid[3], "completed", {"settings": NATIVE, "native_batch_id": settled})
    manifest.append({"job_id": moved, "recording_id": rid[4], "filename": "m.wav"})
    # 5..8: owner identifiers that cannot be the listed owner.
    job(store, rid[5], "completed", {"settings": NATIVE, "native_batch_id": settled.upper()})
    job(store, rid[6], "completed", {"settings": NATIVE, "native_batch_id": ""})
    job(store, rid[7], "completed", {"settings": NATIVE, "native_batch_id": None})
    job(store, rid[8], "completed", {"settings": NATIVE, "native_batch_id": {"id": settled}})
    # 9: a duplicate key whose first (SQLite) value names an unsettled owner.
    unsettled = owner(
        store,
        project["id"],
        "failed",
        [
            {"job_id": new_id(), "recording_id": rid[14], "filename": "x.wav"},
            {"job_id": new_id(), "recording_id": rid[14], "filename": "y.wav"},
        ],
    )
    job(
        store,
        rid[9],
        "completed",
        f'{{"native_batch_id":"{unsettled}","native_batch_id":"{settled}"}}',
    )
    # 10/11: one job listed twice under two different claimed recordings.
    twice = job(store, rid[10], "completed", {"settings": NATIVE, "native_batch_id": settled})
    manifest += [
        {"job_id": twice, "recording_id": rid[10], "filename": "t.wav"},
        {"job_id": twice, "recording_id": rid[11], "filename": "t.wav"},
    ]
    # 12: claimed by the settled owner, but its job does not exist.
    manifest.append({"job_id": new_id(), "recording_id": rid[12], "filename": "g.wav"})
    # 13: a job without an identifier cannot be a listed member.
    job(store, rid[13], "completed", {"native_batch_id": settled}, identifier=None)
    with raw(store) as db:
        db.execute("UPDATE jobs SET id=NULL WHERE recording_id=?", (rid[13],))
        db.execute(
            "UPDATE native_batch_runs SET members=? WHERE id=?", (json.dumps(manifest), settled)
        )
    before = state(store)

    summary = working_existing.designate_existing(store)

    blocked = set(range(3, 15))
    assert summary["skipped"] == {"native_recovery": len(blocked)}, summary
    for n, r in rid.items():
        assert bool(designated(store, r)) is (n not in blocked), n
    assert_only_roles_written(before, state(store), [rid[1], rid[2]])


@pytest.mark.parametrize(
    "members,owner_state,outcome",
    [
        (1, "completed", "refused"),
        (2, "completed", "designated"),
        (1000, "completed", "designated"),
        (1000, "failed", "skipped"),
        (1001, "completed", "refused"),
        (1001, "failed", "refused"),
    ],
)
def test_manifest_member_count_bounds(store, members, owner_state, outcome):
    project = store.create_project("Synthetic")
    target = sole(store, project, "target.wav")
    other = sole(store, project, "other.wav")
    identifier, manifest = new_id(), []
    with raw(store) as db:
        for n in range(members):
            rid = target if n == 0 else bare_recording(db, project["id"])
            job_id = new_id()
            db.execute(
                "INSERT INTO jobs(id,recording_id,kind,state,params,created,updated) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    job_id,
                    rid,
                    "transcribe",
                    "completed",
                    json.dumps({"settings": NATIVE, "native_batch_id": identifier}),
                    now(),
                    now(),
                ),
            )
            manifest.append({"job_id": job_id, "recording_id": rid, "filename": job_id + ".wav"})
    owner(store, project["id"], owner_state, manifest, identifier)
    before = state(store)

    summary = working_existing.designate_existing(store)

    if outcome == "refused":
        assert summary["refused"] == "native_evidence_unverifiable", summary
        assert summary["designated"] == 0 and state(store) == before
    elif outcome == "skipped":
        assert summary["skipped"] == {"native_recovery": 1}
        assert_only_roles_written(before, state(store), [other])
    else:
        assert summary["designated"] == 2
        assert_only_roles_written(before, state(store), [target, other])


UNREADABLE = {
    "blob_manifest": lambda db, i: db.execute(
        "UPDATE native_batch_runs SET members=CAST(members AS BLOB) WHERE id=?", (i,)
    ),
    "invalid_utf8_manifest": lambda db, i: db.execute(
        "UPDATE native_batch_runs SET members=CAST(X'5BFF5D' AS TEXT) WHERE id=?", (i,)
    ),
    "null_owner_id": lambda db, i: db.execute(
        "UPDATE native_batch_runs SET id=NULL WHERE id=?", (i,)
    ),
    "blob_owner_id": lambda db, i: db.execute(
        "UPDATE native_batch_runs SET id=CAST(id AS BLOB) WHERE id=?", (i,)
    ),
    "nan_member": lambda db, i: db.execute(
        "UPDATE native_batch_runs SET members=? WHERE id=?",
        ('[{"job_id":NaN,"recording_id":"r"},{"job_id":"j","recording_id":"s"}]', i),
    ),
    "member_without_recording": lambda db, i: db.execute(
        "UPDATE native_batch_runs SET members=? WHERE id=?",
        ('[{"job_id":"j"},{"job_id":"k","recording_id":"s"}]', i),
    ),
    "overlong_identifier": lambda db, i: db.execute(
        "UPDATE native_batch_runs SET members=? WHERE id=?",
        (json.dumps([{"job_id": "j" * 201, "recording_id": "r"}] * 2), i),
    ),
    "member_duplicate_key": lambda db, i: db.execute(
        "UPDATE native_batch_runs SET members=? WHERE id=?",
        (
            '[{"job_id":"j","recording_id":"r","recording_id":"s"},'
            '{"job_id":"k","recording_id":"t"}]',
            i,
        ),
    ),
    "invalid_utf8_link": lambda db, i: db.execute(
        "UPDATE jobs SET params=? WHERE json_extract(params,'$.native_batch_id')=?",
        ('{"native_batch_id":"\\ud800"}', i),
    ),
}


@pytest.mark.parametrize("damage", sorted(UNREADABLE))
@pytest.mark.parametrize("owner_state", ["completed", "failed"])
def test_unreadable_owner_inventory_writes_nothing_for_anyone(store, damage, owner_state):
    project = store.create_project("Synthetic")
    members = [sole(store, project, f"{n}.wav") for n in "ab"]
    unrelated = sole(store, project, "unrelated.wav")
    identifier, _ = settled_batch(store, project, members, state=owner_state)
    with raw(store) as db:
        UNREADABLE[damage](db, identifier)
    before = state(store)

    summary = working_existing.startup(store)

    # Opaque evidence refuses (or, for undecodable text, aborts) the whole pass.
    assert summary["designated"] == 0, summary
    assert summary["refused"] in {"native_evidence_unverifiable", "native_evidence_incomplete"} or (
        summary["aborted"] is not None
    )
    assert state(store) == before
    assert role(store, unrelated) == {"revision": 0, "working": None}


# --------------------------------------------------------------------------- native: bounds


def inventory(store, project, owners, per_owner, state="completed"):
    """Exactly linked settled owners; returns (owners, members, links, manifest bytes)."""
    total = 0
    with raw(store) as db:
        anchor = bare_recording(db, project["id"])
        jobs, runs = [], []
        for _ in range(owners):
            identifier, manifest = new_id(), []
            for _ in range(per_owner):
                job_id = new_id()
                params = json.dumps({"native_batch_id": identifier})
                jobs.append((job_id, anchor, "transcribe", "completed", params, now(), now()))
                manifest.append({"job_id": job_id, "recording_id": anchor})
            members = json.dumps(manifest)
            total += len(members.encode("utf-8"))
            runs.append((identifier, project["id"], state, "{}", members, now(), now()))
        db.executemany(
            "INSERT INTO jobs(id,recording_id,kind,state,params,created,updated) "
            "VALUES (?,?,?,?,?,?,?)",
            jobs,
        )
        db.executemany(
            "INSERT INTO native_batch_runs(id,project_id,state,params,members,created,updated) "
            "VALUES (?,?,?,?,?,?,?)",
            runs,
        )
    return owners, owners * per_owner, owners * per_owner, total


@pytest.mark.parametrize(
    "bound", ["NATIVE_OWNERS", "NATIVE_LINKS", "NATIVE_MEMBERS", "NATIVE_INVENTORY_BYTES"]
)
@pytest.mark.parametrize("delta,refused", [(0, False), (-1, True)])
def test_every_inventory_bound_is_exact_and_exhaustion_refuses(
    store, monkeypatch, bound, delta, refused
):
    project = store.create_project("Synthetic")
    rid = sole(store, project)
    owners, members, links, size = inventory(store, project, 3, 2)
    exact = {
        "NATIVE_OWNERS": owners,
        "NATIVE_LINKS": links,
        "NATIVE_MEMBERS": members,
        "NATIVE_INVENTORY_BYTES": size,
    }[bound]
    monkeypatch.setattr(working_existing, bound, exact + delta)
    before = state(store)

    summary = working_existing.designate_existing(store)

    if refused:
        assert summary["refused"] == "native_evidence_incomplete", summary
        assert state(store) == before
    else:
        assert summary["refused"] is None and summary["designated"] == 1
        assert_only_roles_written(before, state(store), [rid])


@pytest.mark.parametrize("delta,refused", [(0, False), (-1, True)])
def test_single_manifest_byte_bound_is_exact(store, monkeypatch, delta, refused):
    project = store.create_project("Synthetic")
    rid = sole(store, project)
    *_, size = inventory(store, project, 1, 2)
    monkeypatch.setattr(working_existing, "NATIVE_MANIFEST_BYTES", size + delta)
    summary = working_existing.designate_existing(store)
    assert (summary["refused"] == "native_evidence_unverifiable") is refused, summary
    assert bool(designated(store, rid)) is not refused


def test_real_owner_and_link_bounds_refuse_one_past_the_limit(store):
    project = store.create_project("Synthetic")
    rid = sole(store, project)
    # 4,096 owners and 65,536 linked jobs are accepted at their real constants.
    inventory(store, project, working_existing.NATIVE_OWNERS, 16)
    assert working_existing.NATIVE_OWNERS * 16 == working_existing.NATIVE_LINKS
    before = state(store)
    started = time.monotonic()
    summary = working_existing.designate_existing(store, seconds=working_existing.STARTUP_SECONDS)
    elapsed = time.monotonic() - started
    assert summary["refused"] is None and summary["designated"] == 1, (summary, elapsed)
    assert elapsed < working_existing.STARTUP_SECONDS + 2
    assert_only_roles_written(before, state(store), [rid])
    # One more linked job (to an existing settled owner) is not a complete scan.
    other = sole(store, project, "other.wav")
    with raw(store) as db:
        some_owner = db.execute("SELECT id FROM native_batch_runs LIMIT 1").fetchone()[0]
    job(store, other, "completed", {"native_batch_id": some_owner})
    before = state(store)
    summary = working_existing.designate_existing(store)
    assert summary["refused"] == "native_evidence_incomplete" and state(store) == before
    # Likewise one more owner row.
    with raw(store) as db:
        db.execute("DELETE FROM jobs WHERE recording_id=?", (other,))
    owner(
        store,
        project["id"],
        "completed",
        [{"job_id": "x", "recording_id": "y"}, {"job_id": "z", "recording_id": "y"}],
    )
    before = state(store)
    summary = working_existing.designate_existing(store)
    assert summary["refused"] == "native_evidence_incomplete" and state(store) == before


def test_real_sixteen_mebibyte_inventory_bound_is_exact(store):
    project = store.create_project("Synthetic")
    rid = sole(store, project)
    limit = working_existing.NATIVE_MANIFEST_BYTES
    count = working_existing.NATIVE_INVENTORY_BYTES // limit
    assert count * limit == working_existing.NATIVE_INVENTORY_BYTES
    with raw(store) as db:
        claimed = bare_recording(db, project["id"])
        for _ in range(count):
            manifest = [
                {"job_id": new_id(), "recording_id": claimed, "filename": ""},
                {"job_id": new_id(), "recording_id": claimed},
            ]
            pad = limit - len(json.dumps(manifest))
            manifest[0]["filename"] = "p" * pad
            text = json.dumps(manifest)
            assert len(text.encode("utf-8")) == limit
            db.execute(
                "INSERT INTO native_batch_runs(id,project_id,state,params,members,created,"
                "updated) VALUES (?,?,'completed','{}',?,?,?)",
                (new_id(), project["id"], text, now(), now()),
            )
    summary = working_existing.designate_existing(store)
    assert summary["refused"] is None and designated(store, rid), summary
    # One more byte anywhere in the inventory refuses the next pass.
    later = sole(store, project, "later.wav")
    owner(
        store,
        project["id"],
        "completed",
        [{"job_id": "a", "recording_id": claimed}, {"job_id": "b", "recording_id": claimed}],
    )
    before = state(store)
    summary = working_existing.designate_existing(store)
    assert summary["refused"] == "native_evidence_incomplete", summary
    assert state(store) == before and not designated(store, later)


# --------------------------------------------------------------------------- races


def three_with_settled_member(store):
    project = store.create_project("Synthetic")
    rids = sorted(sole(store, project, f"{n}.wav") for n in range(3))
    with raw(store) as db:
        partner = bare_recording(db, project["id"])
    identifier, manifest = settled_batch(store, project, [rids[2], partner])
    return project, rids, identifier, manifest


def late(store, project, rids, identifier, manifest, mutation):
    target = rids[2]
    if mutation == "owner_unsettled":
        with raw(store) as db:
            db.execute("UPDATE native_batch_runs SET state='failed' WHERE id=?", (identifier,))
    elif mutation == "claim_added":
        owner(
            store,
            project["id"],
            "interrupted",
            [
                {"job_id": new_id(), "recording_id": target, "filename": "a.wav"},
                {"job_id": new_id(), "recording_id": manifest[1]["recording_id"]},
            ],
        )
    elif mutation == "link_broken":
        with raw(store) as db:
            db.execute(
                "UPDATE jobs SET params=? WHERE id=?",
                (
                    json.dumps({"settings": NATIVE, "native_batch_id": new_id()}),
                    manifest[0]["job_id"],
                ),
            )
    elif mutation == "failed_native":
        job(store, target, "failed", {"settings": NATIVE})
    elif mutation == "role_event":
        mark(store, target, creation(store, target)["id"])
    elif mutation == "edit":
        current = store.recording(target)
        store.save_transcript(
            target,
            normalize_segments([{"start": 0, "end": 1, "text": "Late edit."}]),
            current["speakers"],
            current["revision"],
        )
    elif mutation == "trash":
        current = store.recording(target)
        store.set_result_trashed(
            target, current["active_transcript_id"], current["revision"], trashed=True
        )
    elif mutation == "new_result":
        store.add_result(
            target, normalize_segments([{"start": 0, "end": 1, "text": "Late."}]), {}, "Late", {}
        )
    elif mutation == "review_mark":
        review(store, target, ("checked", None, "needs_review"))


LATE = {
    "none": ("designated", 1),
    "review_mark": ("designated", 2),
    "owner_unsettled": ({"native_recovery": 1}, 2),
    "claim_added": ({"native_recovery": 1}, 2),
    "link_broken": ({"native_recovery": 1}, 2),
    "failed_native": ({"native_recovery": 1}, 2),
    "role_event": ({}, 2),
    "edit": ({"versions": 1}, 2),
    "trash": ({}, 2),
    "new_result": ({"results": 1}, 2),
}


@pytest.mark.parametrize("mutation", sorted(LATE))
def test_late_foreign_commits_between_pages_are_observed(store, monkeypatch, mutation):
    project, rids, identifier, manifest = three_with_settled_member(store)
    commits = []

    def foreign():
        commits.append(None)
        if len(commits) == 1 and mutation != "none":
            late(store, project, rids, identifier, manifest, mutation)

    restore = hook_pass(monkeypatch, store, foreign)
    summary = working_existing.designate_existing(store, page=1)
    restore()

    expected, scans = LATE[mutation]
    assert summary["job_scans"] == scans, summary
    assert designated(store, rids[0]) and designated(store, rids[1])
    automatic = [e for e in events(store, rids[2]) if e["actor_kind"] == "system"]
    if expected == "designated":
        assert summary["skipped"] == {} and designated(store, rids[2])
        assert len(automatic) == 1
    else:
        assert summary["skipped"] == expected, summary
        assert automatic == []
    if mutation == "role_event":
        assert [e["action"] for e in events(store, rids[2])] == ["working_marked"]


def test_native_scope_is_scanned_once_per_pass_without_foreign_commits(store, monkeypatch):
    project = store.create_project("Synthetic")
    rids = sorted(sole(store, project, f"{n}.wav") for n in range(5))
    settled_batch(store, project, rids[3:])
    job(store, rids[1], "failed", {"settings": NATIVE})
    calls = []
    real = working_existing._native_scope

    def counted(db, deadline):
        calls.append(None)
        return real(db, deadline)

    monkeypatch.setattr(working_existing, "_native_scope", counted)
    summary = working_existing.designate_existing(store, page=1)
    assert len(calls) == summary["job_scans"] == 1 and summary["pages"] == 5
    assert summary["designated"] == 4 and summary["skipped"] == {"native_recovery": 1}


def test_raw_foreign_connection_commit_also_invalidates_cached_negatives(store, monkeypatch):
    project, rids, identifier, _ = three_with_settled_member(store)
    commits = []

    def foreign():
        commits.append(None)
        if len(commits) == 1:
            other = sqlite3.connect(store.db_path, isolation_level=None)
            try:
                other.execute("BEGIN IMMEDIATE")
                other.execute(
                    "UPDATE native_batch_runs SET state='queued' WHERE id=?", (identifier,)
                )
                other.execute("COMMIT")
            finally:
                other.close()

    restore = hook_pass(monkeypatch, store, foreign)
    summary = working_existing.designate_existing(store, page=1)
    restore()
    assert summary["job_scans"] == 2 and summary["skipped"] == {"native_recovery": 1}
    assert not designated(store, rids[2])


def test_concurrent_trash_during_the_page_applies_after_it_consistently(store, monkeypatch):
    project = store.create_project("Synthetic")
    rid = sole(store, project)
    review(store, rid, ("checked", "needs_review", None))
    current = store.recording(rid)  # The Trash request was prepared before the pass.
    inside, outcome = threading.Event(), {}
    original = working_existing.result_roles.move_automatic

    def slow(*args, **kwargs):
        written = original(*args, **kwargs)
        inside.set()
        time.sleep(0.3)
        return written

    def trash():
        inside.wait(5)
        try:
            store.set_result_trashed(
                rid, current["active_transcript_id"], current["revision"], trashed=True
            )
            outcome["trashed"] = True
        except Exception as error:  # noqa: BLE001 - reported below
            outcome["error"] = repr(error)

    monkeypatch.setattr(working_existing.result_roles, "move_automatic", slow)
    thread = threading.Thread(target=trash)
    thread.start()
    assert working_existing.designate_existing(store)["designated"] == 1
    thread.join(10)
    assert outcome == {"trashed": True}
    # The later Trash clears the role in its own transaction; the tail stays consistent.
    assert [(e["action"], e["actor_kind"]) for e in events(store, rid)] == [
        ("working_auto_first", "system"),
        ("working_trashed", "local_user"),
    ]
    assert role(store, rid) == {"revision": 2, "working": None}
    with closing(sqlite3.connect(store.db_path)) as db:
        assert db.execute("SELECT count(*) FROM passage_reviews").fetchone()[0] == 2
    # Restoring later never re-designates: an earlier role event excludes it forever.
    restored = store.recording(rid)
    store.set_result_trashed(
        rid, current["active_transcript_id"], restored["revision"], trashed=False
    )
    assert working_existing.designate_existing(store)["designated"] == 0
    assert role(store, rid)["working"] is None


def test_late_inventory_exhaustion_reports_partial_progress_only(store, monkeypatch):
    project = store.create_project("Synthetic")
    rids = sorted(sole(store, project, f"{n}.wav") for n in range(3))
    monkeypatch.setattr(working_existing, "NATIVE_OWNERS", 1)
    commits = []

    def foreign():
        commits.append(None)
        if len(commits) == 1:
            for _ in range(2):
                owner(
                    store,
                    project["id"],
                    "completed",
                    [{"job_id": "j", "recording_id": "r"}, {"job_id": "k", "recording_id": "s"}],
                )

    restore = hook_pass(monkeypatch, store, foreign)
    summary = working_existing.designate_existing(store, page=1)
    restore()
    assert summary["refused"] == "native_evidence_incomplete" and summary["incomplete"]
    assert summary["designated"] == 1 and summary["pages"] == 1
    assert [bool(designated(store, r)) for r in rids] == [True, False, False]
    assert len(events(store)) == 1


def test_deadline_inside_a_later_native_rescan_rolls_back_only_that_page(store, monkeypatch):
    project = store.create_project("Synthetic")
    rids = sorted(sole(store, project, f"{n}.wav") for n in range(3))
    clock = [0.0]
    monkeypatch.setattr(working_existing, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    real_manifest = working_existing._manifest

    def slow_manifest(row):
        clock[0] = 1000.0  # The budget expires while the native inventory is being read.
        return real_manifest(row)

    monkeypatch.setattr(working_existing, "_manifest", slow_manifest)
    commits = []

    def foreign():
        commits.append(None)
        if len(commits) == 1:
            owner(
                store,
                project["id"],
                "completed",
                [
                    {"job_id": new_id(), "recording_id": rids[2]},
                    {"job_id": new_id(), "recording_id": rids[2]},
                ],
            )

    restore = hook_pass(monkeypatch, store, foreign)
    summary = working_existing.designate_existing(store, page=1, seconds=10)
    restore()
    assert summary["aborted"] == "OperationalError" and summary["incomplete"], summary
    assert summary["designated"] == 1 and summary["skipped"] == {}
    assert [bool(designated(store, r)) for r in rids] == [True, False, False]
    assert len(events(store)) == 1


# --------------------------------------------------------------------------- budget/scale


def test_large_schema19_library_respects_the_startup_budget(store):
    project = store.create_project("Synthetic")
    with raw(store) as db:
        db.execute("UPDATE projects SET storage_dir=NULL WHERE id=?", (project["id"],))
    eligible = [sole(store, project, f"{n}.wav") for n in range(150)]
    blocked = eligible[::10]
    for rid in blocked:
        job(store, rid, "failed", {"settings": NATIVE, "ucloud_run": {"job_id": "remote"}})
    for rid in eligible[1::10]:
        review(store, rid, ("checked", "needs_review", "not_checked"))
    # A large history: 50,000 completed jobs (~1.5 KB parameters) and a full native inventory.
    padding = "x" * 1500
    with raw(store) as db:
        db.executemany(
            "INSERT INTO jobs(id,recording_id,kind,state,params,created,updated) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                (
                    new_id(),
                    eligible[n % len(eligible)],
                    "media",
                    "completed",
                    json.dumps({"provider_name": "Synthetic", "pad": padding}),
                    now(),
                    now(),
                )
                for n in range(50_000)
            ],
        )
    inventory(store, project, working_existing.NATIVE_OWNERS, 8)
    reviews_before = state(store)[2]

    started = time.monotonic()
    summary = working_existing.startup(store)
    elapsed = time.monotonic() - started

    budget = working_existing.STARTUP_SECONDS + working_existing.SNAPSHOT_SECONDS
    assert elapsed < budget + 5, (elapsed, summary)
    assert summary["aborted"] is None and summary["refused"] is None, summary
    done = summary["designated"]
    while summary["incomplete"]:  # Remaining candidates continue at later starts.
        summary = working_existing.startup(store)
        done += summary["designated"]
    assert done == len(eligible) - len(blocked)
    assert all(not designated(store, rid) for rid in blocked)
    assert state(store)[2] == reviews_before


# --------------------------------------------------------------------------- recovery/restore


def test_post_commit_snapshot_loss_recovers_once_with_reviews_intact(store, monkeypatch):
    project = store.create_project("Synthetic")
    rid = sole(store, project)
    review(store, rid, ("checked", "needs_review", "not_checked"))

    def stopped(identifier):
        raise OSError("synthetic process stop after the database commit")

    monkeypatch.setattr(store, "snapshot_project", stopped)
    first = working_existing.startup(store)
    monkeypatch.delattr(store, "snapshot_project")
    assert first["designated"] == 1 and first["snapshots"]["failed"] == 1
    path = store.project_folder(project["id"]) / "project.json"
    [entry] = [r for r in json.loads(path.read_bytes())["recordings"] if r["id"] == rid]
    assert entry["result_roles"]["working"] is None  # The lost reply: snapshot is stale.
    marks = state(store)[2]

    reopened = store_module.Store(store.root)  # A later process start.
    second = working_existing.startup(reopened)
    assert second["designated"] == 0 and second["snapshots"]["refreshed"] == 1, second
    [entry] = [r for r in json.loads(path.read_bytes())["recordings"] if r["id"] == rid]
    assert entry["result_roles"] == role(reopened, rid)
    assert len(events(reopened, rid)) == 1 and state(reopened)[2] == marks
    third = working_existing.startup(store_module.Store(store.root))
    assert third["snapshots"]["refreshed"] == 0 and third["snapshots"]["current"] == 1


def test_contained_capture_restore_keeps_reviews_and_the_pass_event(client, tmp_path, wav_bytes):
    from test_portable_restore import destination

    from cbs_transcriber import portable_capture, portable_restore

    source = client.app.state.store
    project = source.create_project("Synthetic schema-19 round trip")
    uploaded = []
    for name in ("chosen.wav", "edited.wav"):
        response = client.post(
            "/api/recordings",
            data={"project_id": project["id"]},
            files={"file": (name, wav_bytes, "audio/wav")},
        )
        assert response.is_success, response.text
        uploaded.append(response.json()["recording"]["id"])
    chosen, edited = uploaded
    source.execute("UPDATE jobs SET state='completed'")
    source.execute("UPDATE recordings SET status='ready'")
    for rid in uploaded:
        segments = [
            {"start": n * 0.5, "end": n * 0.5 + 0.5, "text": t} for n, t in enumerate(TEXTS)
        ]
        source.add_result(rid, normalize_segments(segments), {}, "Synthetic", {})
    current = source.recording(edited)
    source.save_transcript(
        edited,
        normalize_segments([{"start": 0, "end": 1, "text": "Edited wording."}]),
        current["speakers"],
        current["revision"],
    )
    result_id = review(source, chosen, ("checked", "needs_review", "not_checked"))
    first = passage_review.read(source, chosen, result_id)["passages"][0]["segment_id"]
    stale_and_removed(source, result_id, first)
    review(source, edited, ("checked",))

    summary = working_existing.designate_existing(source)
    assert summary["designated"] == 1 and summary["skipped"] == {"versions": 1}, summary

    def rows(store, table):
        with closing(sqlite3.connect(store.db_path)) as db:
            return db.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()

    expected = {
        table: rows(source, table)
        for table in (
            "passage_reviews",
            "recording_result_roles",
            "result_role_events",
            "working_job_authorities",
        )
    }
    with source.library_lock():
        saved = portable_capture.capture_library(
            source,
            client.app.state.queue,
            tmp_path / "packages",
            gate=portable_capture.CaptureGate(),
        )
    target = destination(tmp_path, "schema19-working-restored")
    portable_restore.restore_library(
        saved["folder"],
        expected_commit_sha256=saved["commit_sha256"],
        target=target,
        live_root=source.root,
    )
    restored = store_module.Store(target)
    assert {table: rows(restored, table) for table in expected} == expected
    assert expected["working_job_authorities"] == []  # No invented consent or authority.
    [event] = events(restored, chosen)
    version = creation(restored, chosen)
    assert (event["action"], event["actor_kind"]) == ("working_auto_first", "system")
    assert (event["next_version_id"], event["next_payload_sha256"]) == (
        version["id"],
        version["payload_sha256"],
    )
    assert events(restored, edited) == []
    view = passage_review.read(restored, chosen, result_id)
    assert view == passage_review.read(source, chosen, result_id)
    assert any(p["stale"] for p in view["passages"])
    # The restored library stays inert for the pass: no designation of the edited one either.
    before = state(restored)
    again = working_existing.startup(restored)
    assert again["refused"] == "restored_library" and again["designated"] == 0
    assert state(restored) == before
