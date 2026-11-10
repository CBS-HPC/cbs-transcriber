"""V138: inert, fenced original inspection in temporary libraries only."""

import hashlib
import json
import sqlite3
import threading
import wave
from contextlib import contextmanager
from pathlib import Path

import pytest
from prior_schema import remove_later_additions, without_added_languages

from cbs_transcriber import audio_inputs
from cbs_transcriber import original_inspection as inspection
from cbs_transcriber.media import Cancelled
from cbs_transcriber.store import SCHEMA_VERSION, Store


@pytest.fixture
def source(tmp_path):
    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic inspection")
    recording = store.create_recording(project["id"], "synthetic.wav")
    directory = store.directory(recording["id"])
    directory.mkdir(parents=True)
    with wave.open(str(directory / "original.wav"), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\x01\x00" * 1600)
    store.execute(
        "UPDATE recordings SET duration=7,status='preparing' WHERE id=?", (recording["id"],)
    )
    return store, store.recording(recording["id"]), directory


def test_v138_null_cache_and_reserved_generic_boundaries(source):
    store, recording, _ = source
    assert inspection.read(store, recording["id"]) is None
    with pytest.raises(ValueError, match="inspection"):
        store.add_job(recording["id"], inspection.KIND)
    with pytest.raises(ValueError, match="inspection"):
        store.claim_job(inspection.KIND)
    with store.library_lock():
        cache = inspection.ensure(store, recording["id"])
        with pytest.raises(ValueError, match="inspection"):
            store.update_job(cache["inspection_job_id"], state="completed")
    assert "original_input" not in store.recording(recording["id"])


def test_v138_manual_inspection_anchors_before_decode_and_preserves_recording(source, monkeypatch):
    store, recording, directory = source
    before = store.recording(recording["id"])
    original_run = audio_inputs._run
    observed = []

    def decode(*args, **kwargs):
        cache = inspection.read(store, recording["id"])
        assert cache["anchor"] == {
            "sha256": hashlib.sha256((directory / "original.wav").read_bytes()).hexdigest(),
            "bytes": (directory / "original.wav").stat().st_size,
            "format": "wav",
        }
        observed.append(cache["state"])
        return original_run(*args, **kwargs)

    monkeypatch.setattr(audio_inputs, "_run", decode)
    with store.library_lock():
        pending = inspection.ensure(store, recording["id"])
        assert inspection.ensure(store, recording["id"]) == pending
        claim = inspection.claim(store)
        assert inspection.claim(store) is None
        ready = inspection.run(store, claim)
    assert observed and set(observed) == {"running"}
    assert ready["state"] == "ready" and ready["receipt"]["audio_frames"] == 1600
    assert ready["receipt"]["container_duration_observation"] == 7
    assert store.job(pending["inspection_job_id"])["state"] == "completed"
    assert store.recording(recording["id"]) == before


def test_v138_queued_cancel_retry_preserves_id_and_generation(source):
    store, recording, _ = source
    with store.library_lock():
        pending = inspection.ensure(store, recording["id"])
        cancelled = inspection.cancel(
            store,
            pending["inspection_job_id"],
            generation=pending["generation"],
            epoch=pending["epoch"],
        )
        assert cancelled["state"] == "error" and cancelled["error"]["code"] == "cancelled"
        assert cancelled["anchor"] is None
        retried = inspection.retry(
            store,
            pending["inspection_job_id"],
            generation=cancelled["generation"],
            epoch=cancelled["epoch"],
        )
        assert retried["inspection_job_id"] == pending["inspection_job_id"]
        assert retried["generation"] == pending["generation"]
        assert retried["epoch"] > cancelled["epoch"]


def test_v138_lease_is_required_without_reentering_thread_owner(source):
    store, recording, _ = source
    with pytest.raises(RuntimeError, match="lease"):
        inspection.ensure(store, recording["id"])
    results = []
    with store.library_lock():
        inspection.ensure(store, recording["id"])
        worker = threading.Thread(
            target=lambda: results.append(inspection.run(store, inspection.claim(store)))
        )
        worker.start()
        worker.join(5)
        assert not worker.is_alive(), "Inspector re-entered the lifespan-owned RLock"
        with pytest.raises(RuntimeError, match="already open"):
            with Store(store.root).library_lock():
                pass
    assert results[0]["state"] == "ready"


def test_v138_corrupt_cache_is_refused_without_reset(source):
    store, recording, _ = source
    raw = '{"version":1,"version":1}'
    store.execute("UPDATE recordings SET original_input=? WHERE id=?", (raw, recording["id"]))
    with store.library_lock():
        for action in (inspection.read, inspection.ensure):
            with pytest.raises(inspection.InspectionError):
                action(store, recording["id"])
    assert (
        store.one("SELECT original_input FROM recordings WHERE id=?", (recording["id"],))[
            "original_input"
        ]
        == raw
    )
    assert not store.all("SELECT id FROM jobs")


def test_v138_schema12_migration_is_additive_and_backed_up(source, monkeypatch):
    store, recording, directory = source
    store.execute("ALTER TABLE recordings DROP COLUMN original_input")
    with store.connection() as db:
        remove_later_additions(db, 12)
    store.execute("PRAGMA user_version=12")
    before = store.recording(recording["id"])
    monkeypatch.setattr(
        audio_inputs, "freeze", lambda *a, **k: pytest.fail("Migration inspected audio")
    )
    original = (directory / "original.wav").read_bytes()
    upgraded = Store(store.root)
    assert upgraded.one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION
    [after] = without_added_languages("recordings", [upgraded.recording(recording["id"])])
    assert after == before
    assert inspection.read(upgraded, recording["id"]) is None
    backups = list((store.root / "backups").glob("schema-12-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 12
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    Store(store.root)
    assert len(list((store.root / "backups").glob("schema-12-*.sqlite3"))) == 1
    assert (directory / "original.wav").read_bytes() == original


def test_v138_hash_cancel_observer_does_not_claim_incomplete_observation(source):
    store, recording, _ = source
    cancelled = threading.Event()
    cancelled.set()
    observed = []
    with pytest.raises(Cancelled):
        audio_inputs.freeze(store, recording, cancelled, identity_observer=observed.append)
    assert observed == []


def test_v138_failed_decode_retains_anchor_and_retry_rejects_replacement(source, monkeypatch):
    store, recording, directory = source
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise ValueError("Sensitive decoder output /private/synthetic.wav")

    monkeypatch.setattr(audio_inputs, "_run", fail)
    with store.library_lock():
        inspection.ensure(store, recording["id"])
        failed = inspection.run(store, inspection.claim(store))
        assert failed["error"]["code"] == "decode_failed"
        anchor = failed["anchor"]
        assert anchor is not None and "private" not in json.dumps(failed)
        original = directory / "original.wav"
        data = bytearray(original.read_bytes())
        data[-1] ^= 1
        original.write_bytes(data)
        inspection.retry(
            store,
            failed["inspection_job_id"],
            generation=failed["generation"],
            epoch=failed["epoch"],
        )
        refused = inspection.run(store, inspection.claim(store))
    assert refused["error"]["code"] == "source_changed"
    assert refused["anchor"] == anchor
    assert len(calls) == 1, "Replacement was decoded before checking the retained anchor"


def test_transient_read_error_is_retryable_and_not_called_unsupported(source, monkeypatch):
    # B280: a busy machine once failed an ordinary WAV as "outside supported limits".
    store, recording, _ = source
    real_run = audio_inputs._run

    def unreadable(*args, **kwargs):
        raise PermissionError("Synthetic sharing violation on /private/synthetic.wav")

    monkeypatch.setattr(audio_inputs, "_run", unreadable)
    with store.library_lock():
        inspection.ensure(store, recording["id"])
        failed = inspection.run(store, inspection.claim(store))
        assert failed["error"]["code"] == "read_failed"
        assert "supported limits" not in failed["error"]["message"]
        assert "private" not in json.dumps(failed)
        monkeypatch.setattr(audio_inputs, "_run", real_run)
        inspection.retry(
            store,
            failed["inspection_job_id"],
            generation=failed["generation"],
            epoch=failed["epoch"],
        )
        ready = inspection.run(store, inspection.claim(store))
    assert ready["state"] == "ready" and ready["error"] is None


def test_v138_running_cancel_retains_busy_ownership_until_io_drains(source, monkeypatch):
    store, recording, directory = source
    started, saw_cancel, drain = threading.Event(), threading.Event(), threading.Event()
    outcomes = []

    def blocked(_store, _recording, cancel, *, identity_observer):
        identity_observer(
            {**audio_inputs._hash(directory / "original.wav", directory), "format": "wav"}
        )
        started.set()
        while not cancel.wait(0.01):
            pass
        saw_cancel.set()
        assert drain.wait(5)
        raise Cancelled()

    monkeypatch.setattr(audio_inputs, "freeze", blocked)
    with store.library_lock():
        inspection.ensure(store, recording["id"])
        claim = inspection.claim(store)
        thread = threading.Thread(target=lambda: outcomes.append(inspection.run(store, claim)))
        thread.start()
        try:
            assert started.wait(5)
            running = inspection.cancel(
                store, claim["job_id"], generation=claim["generation"], epoch=claim["epoch"]
            )
            assert saw_cancel.wait(5)
            assert running["state"] == "running" and running["cancel_requested"]
            assert store.job(claim["job_id"])["state"] == "running"
            assert inspection.read(store, recording["id"])["anchor"] is not None
            with pytest.raises(RuntimeError, match="already open"):
                with Store(store.root).library_lock():
                    pass
        finally:
            drain.set()
            thread.join(5)
        assert not thread.is_alive()
    assert outcomes[0]["error"]["code"] == "cancelled"
    assert outcomes[0]["anchor"] == running["anchor"]
    assert store.job(claim["job_id"])["state"] == "cancelled"


@pytest.mark.parametrize("cancel_requested", [False, True])
def test_v138_explicit_recovery_preserves_anchor_and_fences_old_worker(
    source, monkeypatch, cancel_requested
):
    store, recording, directory = source
    identity = {**audio_inputs._hash(directory / "original.wav", directory), "format": "wav"}

    def crash(_store, _recording, cancel, *, identity_observer):
        identity_observer(identity)
        raise RuntimeError("Simulated process exit")

    monkeypatch.setattr(audio_inputs, "freeze", crash)
    with store.library_lock():
        inspection.ensure(store, recording["id"])
        old_claim = inspection.claim(store)
        with pytest.raises(RuntimeError, match="Simulated"):
            inspection.run(store, old_claim)
        if cancel_requested:
            inspection.cancel(
                store,
                old_claim["job_id"],
                generation=old_claim["generation"],
                epoch=old_claim["epoch"],
            )
        before = inspection.read(store, recording["id"])
        store.recover_jobs()
        assert inspection.read(store, recording["id"]) == before
        assert inspection.recover(store) == 1
        recovered = inspection.read(store, recording["id"])
        assert recovered["epoch"] > before["epoch"] and recovered["anchor"] == identity
        assert recovered["state"] == ("error" if cancel_requested else "pending")
        assert inspection.run(store, old_claim) is None
        assert inspection.read(store, recording["id"]) == recovered
    with pytest.raises(RuntimeError, match="lease"):
        inspection.recover(store)


def test_v138_concurrent_ensure_and_claim_are_unique(source):
    store, recording, _ = source
    barrier = threading.Barrier(3)
    ensured, claimed = [], []

    def ensure():
        barrier.wait()
        ensured.append(inspection.ensure(store, recording["id"]))

    def claim():
        barrier.wait()
        claimed.append(inspection.claim(store))

    with store.library_lock():
        for action, output in ((ensure, ensured), (claim, claimed)):
            threads = [threading.Thread(target=action) for _ in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(5)
                assert not thread.is_alive()
            assert len(output) == 2
    assert ensured[0] == ensured[1]
    assert len(store.all("SELECT id FROM jobs WHERE kind=?", (inspection.KIND,))) == 1
    assert sum(item is not None for item in claimed) == 1


@pytest.mark.parametrize(
    "damage", ["scope", "hash", "recording", "unknown", "state", "bool_epoch", "oversized", "job"]
)
def test_v138_ready_cache_cannot_be_forged_or_reset(source, damage):
    store, recording, _ = source
    with store.library_lock():
        inspection.ensure(store, recording["id"])
        ready = inspection.run(store, inspection.claim(store))
        cache = json.loads(json.dumps(ready))
        if damage == "scope":
            receipt = cache["receipt"]
            receipt.update(
                duration_basis="container_observation",
                duration=7,
                audio_frames=None,
                pcm_sha256=None,
            )
            receipt["source_origin"].update(
                basis="encoded_packet", first_pts=0, normalized_first_pts=None
            )
            audio_inputs.validate(
                receipt
            )  # Valid scoped evidence is insufficient for whole readiness.
            cache["receipt_sha256"] = inspection._digest(receipt)
        elif damage == "hash":
            cache["receipt_sha256"] = "0" * 64
        elif damage == "recording":
            cache["receipt"]["recording_id"] = "b" * 32
            cache["receipt_sha256"] = inspection._digest(cache["receipt"])
        elif damage == "unknown":
            cache["unexpected"] = "no"
        elif damage == "state":
            cache["state"] = []
        elif damage == "bool_epoch":
            cache["epoch"] = True
        elif damage == "job":
            store.execute(
                "UPDATE jobs SET params=? WHERE id=?",
                ('{"inspection":{}}', cache["inspection_job_id"]),
            )
        raw = json.dumps(cache) if damage != "oversized" else " " * (inspection.MAX_CACHE_BYTES + 1)
        store.execute("UPDATE recordings SET original_input=? WHERE id=?", (raw, recording["id"]))
        for operation in (inspection.read, inspection.ensure):
            with pytest.raises(inspection.InspectionError):
                operation(store, recording["id"])
        assert (
            store.one("SELECT original_input FROM recordings WHERE id=?", (recording["id"],))[
                "original_input"
            ]
            == raw
        )


def test_v138_snapshot_excludes_raw_cache_and_ready_read_needs_no_media(source, monkeypatch):
    store, recording, directory = source
    with store.library_lock():
        inspection.ensure(store, recording["id"])
        ready = inspection.run(store, inspection.claim(store))
    (directory / "original.wav").unlink()
    monkeypatch.setattr(audio_inputs, "freeze", lambda *a, **k: pytest.fail("Read inspected media"))
    monkeypatch.setattr(audio_inputs, "_decoder", lambda: pytest.fail("Read probed decoder"))
    assert inspection.read(store, recording["id"]) == ready
    store.snapshot_project(recording["project_id"])
    snapshot = json.loads(
        (store.project_folder(recording["project_id"]) / "project.json").read_text(encoding="utf-8")
    )
    assert all("original_input" not in row for row in snapshot["recordings"])
    with store.library_lock(), pytest.raises(inspection.InspectionError):
        inspection.retry(
            store, ready["inspection_job_id"], generation=ready["generation"], epoch=ready["epoch"]
        )


def test_v138_json_depth_refused_before_deserializing(source, monkeypatch):
    store, recording, _ = source
    raw = '{"deep":' + "[" * 20 + "0" + "]" * 20 + "}"
    store.execute("UPDATE recordings SET original_input=? WHERE id=?", (raw, recording["id"]))
    monkeypatch.setattr(inspection.json, "loads", lambda *a, **k: pytest.fail("Deep JSON parsed"))
    with pytest.raises(inspection.InspectionError):
        inspection.read(store, recording["id"])


def test_v138_epoch_exhaustion_cannot_persist_invalid_cache(source):
    store, recording, _ = source
    with store.library_lock():
        cache = inspection.ensure(store, recording["id"])
        cache["epoch"] = 2**53
        raw = json.dumps(cache)
        store.execute("UPDATE recordings SET original_input=? WHERE id=?", (raw, recording["id"]))
        store.execute(
            "UPDATE jobs SET params=? WHERE id=?",
            (json.dumps(inspection._params(cache)), cache["inspection_job_id"]),
        )
        before = store.job(cache["inspection_job_id"])
        with pytest.raises(inspection.InspectionError):
            inspection.claim(store)
        assert (
            store.one("SELECT original_input FROM recordings WHERE id=?", (recording["id"],))[
                "original_input"
            ]
            == raw
        )
        assert store.job(cache["inspection_job_id"]) == before


def test_v138_cancel_after_completed_hash_retains_anchor_before_decoder(source, monkeypatch):
    store, recording, directory = source
    event = threading.Event()
    original_hash = audio_inputs._hash
    identity = {**original_hash(directory / "original.wav", directory), "format": "wav"}

    def hash_then_cancel(*args, **kwargs):
        result = original_hash(*args, **kwargs)
        event.set()
        return result

    monkeypatch.setattr(audio_inputs, "_hash", hash_then_cancel)
    monkeypatch.setattr(audio_inputs, "_run", lambda *a, **k: pytest.fail("Cancelled work decoded"))
    with store.library_lock():
        inspection.ensure(store, recording["id"])
        cancelled = inspection.run(store, inspection.claim(store), event)
    assert cancelled["error"]["code"] == "cancelled"
    assert cancelled["anchor"] == identity


@pytest.mark.parametrize("damage", ["cancel", "grow", "replace"])
def test_v138_hash_bounded_reads_cancel_growth_and_replacement(source, monkeypatch, damage):
    _, _, directory = source
    path = directory / "original.wav"
    size = path.stat().st_size
    data = path.read_bytes()
    event, reads = threading.Event(), []
    original_open = Path.open

    class Reader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, count):
            reads.append(count)
            result = self.stream.read(count)
            if len(reads) == 1:
                if damage == "cancel":
                    event.set()
                elif damage == "grow":
                    with original_open(path, "ab") as output:
                        output.write(b"extra bytes")
                else:
                    replacement = directory / "replacement.wav"
                    replacement.write_bytes(data)
                    replacement.replace(path)
            return result

    def guarded_open(target, mode="r", *args, **kwargs):
        stream = original_open(target, mode, *args, **kwargs)
        return Reader(stream) if target == path and mode == "rb" else stream

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(Cancelled if damage == "cancel" else ValueError):
        audio_inputs._hash(path, directory, event, max_bytes=audio_inputs.MAX_BYTES)
    assert reads and max(reads) <= min(1024 * 1024, size + 1)
    if damage == "grow":
        assert reads == [size + 1, 1]


def test_v138_hash_cap_precedes_source_open_and_default_digest_is_unchanged(source, monkeypatch):
    _, _, directory = source
    path = directory / "original.wav"
    data = path.read_bytes()
    assert audio_inputs._hash(path, directory) == {
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("Oversized source opened"))
    with pytest.raises(ValueError):
        audio_inputs._hash(path, directory, max_bytes=len(data) - 1)


def test_v138_incomplete_first_hash_keeps_anchor_unobserved(source, monkeypatch):
    store, recording, _ = source

    def incomplete(*args, **kwargs):
        raise Cancelled()

    monkeypatch.setattr(audio_inputs, "_hash", incomplete)
    with store.library_lock():
        inspection.ensure(store, recording["id"])
        outcome = inspection.run(store, inspection.claim(store))
    assert outcome["anchor"] is None and outcome["error"]["code"] == "cancelled"


def test_v138_source_replaced_during_decode_cannot_publish_ready(source, monkeypatch):
    store, recording, directory = source
    original_run = audio_inputs._run
    path = directory / "original.wav"
    first_hash = audio_inputs._hash(path, directory)
    calls = []

    def replace_then_decode(*args, **kwargs):
        if not calls:
            data = bytearray(path.read_bytes())
            data[-1] ^= 1
            replacement = directory / "replacement.wav"
            replacement.write_bytes(data)
            replacement.replace(path)
        calls.append(1)
        return original_run(*args, **kwargs)

    monkeypatch.setattr(audio_inputs, "_run", replace_then_decode)
    with store.library_lock():
        inspection.ensure(store, recording["id"])
        result = inspection.run(store, inspection.claim(store))
    assert result["state"] == "error" and result["receipt"] is None
    assert result["anchor"] == {**first_hash, "format": "wav"}
    assert store.job(result["inspection_job_id"])["state"] == "failed"


def test_v138_decode_holds_neither_database_nor_snapshot_lock(source, monkeypatch):
    store, recording, _ = source
    started, release = threading.Event(), threading.Event()
    original_run = audio_inputs._run
    outcomes = []

    def blocked_decode(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original_run(*args, **kwargs)

    monkeypatch.setattr(audio_inputs, "_run", blocked_decode)
    with store.library_lock():
        inspection.ensure(store, recording["id"])
        token = inspection.claim(store)
        worker = threading.Thread(target=lambda: outcomes.append(inspection.run(store, token)))
        worker.start()
        try:
            assert started.wait(5)
            with store.connection() as db:
                db.execute("PRAGMA busy_timeout=100")
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE recordings SET name=name WHERE id=?", (recording["id"],))
            assert store.snapshot_lock.acquire(timeout=1)
            store.snapshot_lock.release()
        finally:
            release.set()
            worker.join(5)
        assert not worker.is_alive()
    assert outcomes[0]["state"] == "ready"


def test_v138_migration_requires_lease_before_backup_or_column_write(source):
    store, recording, _ = source
    store.execute("ALTER TABLE recordings DROP COLUMN original_input")
    store.execute("PRAGMA user_version=12")
    with store.library_lock(), pytest.raises(RuntimeError, match="already open"):
        Store(store.root)
    assert not list((store.root / "backups").glob("schema-12-*.sqlite3"))
    assert store.one("PRAGMA user_version")["user_version"] == 12
    assert "original_input" not in {
        row["name"] for row in store.all("PRAGMA table_info(recordings)")
    }
    assert store.recording(recording["id"])["duration"] == 7


def test_v138_migration_transaction_rolls_back_before_version_publication(source, monkeypatch):
    store, recording, _ = source
    store.execute("ALTER TABLE recordings DROP COLUMN original_input")
    store.execute("PRAGMA user_version=12")
    before = store.recording(recording["id"])
    original_connection = Store.connection

    @contextmanager
    def fail_commit(owner):
        with original_connection(owner) as db:
            yield db
            if db.execute("PRAGMA user_version").fetchone()[0] == 13:
                raise OSError("Synthetic write failure")

    monkeypatch.setattr(Store, "connection", fail_commit)
    with pytest.raises(OSError, match="Synthetic"):
        Store(store.root)
    monkeypatch.setattr(Store, "connection", original_connection)
    assert store.one("PRAGMA user_version")["user_version"] == 12
    assert "original_input" not in {
        row["name"] for row in store.all("PRAGMA table_info(recordings)")
    }
    assert store.recording(recording["id"]) == before


@pytest.mark.parametrize("field,value", [("version", True), ("epoch", True), ("epoch", 1.0)])
def test_v138_job_params_require_exact_numeric_types(source, field, value):
    store, recording, _ = source
    with store.library_lock():
        pending = inspection.ensure(store, recording["id"])
        params = inspection._params(pending)
        params["inspection"][field] = value
        store.execute(
            "UPDATE jobs SET params=? WHERE id=?",
            (json.dumps(params), pending["inspection_job_id"]),
        )
        with pytest.raises(inspection.InspectionError):
            inspection.read(store, recording["id"])


def test_v138_missing_cache_cannot_erase_existing_inspector_generation(source):
    store, recording, _ = source
    with store.library_lock():
        pending = inspection.ensure(store, recording["id"])
        store.execute("UPDATE recordings SET original_input=NULL WHERE id=?", (recording["id"],))
        with pytest.raises(inspection.InspectionError):
            inspection.ensure(store, recording["id"])
    assert [row["id"] for row in store.all("SELECT id FROM jobs")] == [pending["inspection_job_id"]]


@pytest.mark.parametrize(
    "error",
    [
        inspection.StaleInspection,
        inspection.InspectionError,
        sqlite3.OperationalError,
        RuntimeError,
    ],
)
def test_v138_guard_exception_kills_reaps_and_drains_decoder(monkeypatch, error):
    killed, pcm_drained, log_drained = threading.Event(), threading.Event(), threading.Event()

    class Pipe:
        def __init__(self, drained):
            self.drained = drained

        def read(self, size):
            assert killed.wait(2)
            self.drained.set()
            return b""

        readline = read

    class Process:
        def __init__(self):
            self.kill_calls, self.wait_calls = 0, 0
            self.stdout, self.stderr = Pipe(pcm_drained), Pipe(log_drained)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def poll(self):
            return None

        def kill(self):
            self.kill_calls += 1
            killed.set()

        def wait(self):
            self.wait_calls += 1

    failure = error("Synthetic inspection guard failure")

    class Guard:
        def is_set(self):
            return False

        def wait(self, timeout):
            raise failure

    process = Process()
    monkeypatch.setattr(audio_inputs.subprocess, "Popen", lambda *a, **k: process)
    try:
        with pytest.raises(error) as caught:
            audio_inputs._run([], Guard(), pcm=True, timeline=True, executable="synthetic-ffmpeg")
        assert caught.value is failure
        assert process.kill_calls == process.wait_calls == 1
        assert pcm_drained.is_set() and log_drained.is_set()
    finally:
        killed.set()


@pytest.mark.parametrize("operation", ["ensure", "claim"])
def test_v138_lifecycle_job_write_failure_rolls_back_cache_and_job_together(source, operation):
    store, recording, _ = source
    with store.library_lock():
        if operation == "claim":
            inspection.ensure(store, recording["id"])
        before = (
            store.one("SELECT * FROM recordings WHERE id=?", (recording["id"],)),
            store.all("SELECT * FROM jobs"),
        )
        store.execute(
            "CREATE TRIGGER refuse_inspection_update BEFORE UPDATE ON jobs "
            "WHEN NEW.kind='source-inspect' BEGIN SELECT RAISE(ABORT,'Synthetic job failure'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="Synthetic"):
            if operation == "ensure":
                inspection.ensure(store, recording["id"])
            else:
                inspection.claim(store)
        after = (
            store.one("SELECT * FROM recordings WHERE id=?", (recording["id"],)),
            store.all("SELECT * FROM jobs"),
        )
        assert after == before
