"""Published native batch outputs can save early; all remote operations are synthetic."""

import hashlib
import json
import threading
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_native_batch import batch_remote as batch_remote
from test_native_batch import enqueue
from test_native_batch import selection as selection
from test_native_batch_logs import processed
from test_native_batch_transfers import four_recordings, process, retry
from test_native_capture import creation
from test_ucloud_native import PAYLOAD, trash_fake
from test_ucloud_native import remote as remote

from cbs_transcriber import native_batch, native_capture, portable_capture
from cbs_transcriber.media import Cancelled


@pytest.fixture
def published(client, batch_remote, selection, monkeypatch, request, wav_bytes):
    provider, recordings = selection
    store, queue = client.app.state.store, client.app.state.queue
    batch_remote.store = store
    options = getattr(request, "param", 2)
    options = options if isinstance(options, dict) else {"count": options}
    four_recordings(store, recordings, wav_bytes, count=options.get("count", 2))
    if options.get("cleanup"):
        provider["ucloud_transcriber"]["cleanup_after_success"] = True
        response = client.put("/api/providers/" + provider["id"], json=provider)
        assert response.status_code == 200, response.text
    jobs = enqueue(client, provider, recordings)
    identifier = jobs[0]["params"]["native_batch_id"]
    members = store.native_batch(identifier)["members"]
    clock = [0.0]
    state = {
        "poll": 0,
        "states": ["RUNNING", "RUNNING", "RUNNING", "SUCCESS"],
        "events": lambda: [members[0]],
        "names": lambda member: [member["job_id"] + "_large-v3_da.json"],
        "data": lambda member: json.dumps(PAYLOAD).encode(),
        "before_poll": lambda: None,
        "downloads": [],
        "log_reads": [],
        "snapshots": [],
    }
    original = batch_remote.handle

    def handle(request):
        if request.url.host == "cloud.invalid":
            path = request.url.path
            if path == "/api/jobs/retrieve" and request.url.params["id"] == "remote-job":
                state["poll"] += 1
                state["before_poll"]()
                remote_state = state["states"][min(state["poll"] - 1, len(state["states"]) - 1)]
                batch_remote.calls.append(request)
                return httpx.Response(200, json={"status": {"state": remote_state}})
            if path == "/api/files/browse":
                folder = request.url.params["path"]
                batch_remote.calls.append(request)
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {"id": folder + "/" + name}
                            for member in members
                            for name in state["names"](member)
                        ]
                    },
                )
            if path == "/api/files/download":
                target = json.loads(request.content)["items"][0]["id"]
                name = target.rsplit("/", 1)[-1]
                batch_remote.calls.append(request)
                state["downloads"].append((state["poll"], name))
                return httpx.Response(
                    200,
                    json={
                        "responses": [
                            {"endpoint": "https://files.invalid/output?member=" + name[:32]}
                        ]
                    },
                )
        elif request.url.path == "/output":
            assert "authorization" not in request.headers
            member = next(m for m in members if m["job_id"] == request.url.params["member"])
            data = state["data"](member)
            if isinstance(data, Exception):
                raise data
            return httpx.Response(200, content=data)
        return original(request)

    def read_log(settings, key, folder, guard):
        guard()
        assert folder == native_batch.folder_for(store.native_batch(identifier))
        state["log_reads"].append(state["poll"])
        events = state["events"]()
        return None if events is None else b"".join(processed(member) for member in events)

    def wait(seconds):
        state["snapshots"].append([store.job(job["id"])["state"] for job in jobs])
        clock[0] += max(seconds, native_batch.LOG_POLL_SECONDS)

    batch_remote.handle = handle
    monkeypatch.setattr(native_batch, "_read_log", read_log)
    monkeypatch.setattr(native_batch, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(queue.cancel_event, "wait", wait)
    return SimpleNamespace(
        state=state,
        store=store,
        queue=queue,
        remote=batch_remote,
        jobs=jobs,
        members=members,
        recordings=recordings,
        identifier=identifier,
        provider=provider,
    )


def test_first_file_saves_while_running_with_exact_capture_and_no_early_cleanup(
    client, published, tmp_path
):
    p = published

    def check():
        if p.state["poll"] != 3:
            return
        first = p.store.job(p.jobs[0]["id"])
        assert first["state"] == "completed"
        assert p.store.job(p.jobs[1]["id"])["state"] == "running"
        run = p.store.native_batch(p.identifier)["params"]["ucloud_run"]
        assert run["remote_state"] == "RUNNING" and run.get("cleanup_state") != "trashed"
        assert not [c for c in p.remote.calls if c.url.path == "/api/files/trash"]
        result, version, context = creation(p.store, p.recordings[0]["id"])
        captured = first["params"]["native_artifact_capture"]
        raw = native_batch._raw_path(p.store, p.members[0]).read_bytes()
        assert captured["output"]["match"]["remote_name"] == (
            p.members[0]["job_id"] + "_large-v3_da.json"
        )
        assert run["raw_hashes"][first["id"]] == hashlib.sha256(raw).hexdigest()
        assert context["native_artifacts"]["creation"] == {
            "result_id": result["id"],
            "version_id": version["id"],
            "payload_sha256": version["payload_sha256"],
        }
        assert (
            native_capture.unbound(
                context["native_artifacts"],
                recording_id=p.recordings[0]["id"],
                job_id=first["id"],
                creation=context["native_artifacts"]["creation"],
            )
            == captured
        )

    p.state["before_poll"] = check
    process(client, p.jobs)
    assert p.state["snapshots"][:2] == [["running", "running"], ["completed", "running"]]
    assert len(p.remote.submissions) == 1 and len(p.remote.uploads) == 2
    first_downloads = [
        poll for poll, name in p.state["downloads"] if name.startswith(p.jobs[0]["id"])
    ]
    assert first_downloads == [1, 2]
    # The retained capture is accepted by the existing portable package collector too.
    p.queue.active_batch_id = None  # The synthetic worker has returned and released its slot.
    result = portable_capture.capture_library(
        p.store, p.queue, tmp_path / "packages", gate=portable_capture.CaptureGate()
    )
    manifest = json.loads((Path(result["folder"]) / "manifest.json").read_text(encoding="utf-8"))
    assert not [
        gap for gap in manifest["legacy_gaps"] if gap["job_id"] in {j["id"] for j in p.jobs}
    ]


def test_all_processed_markers_still_leave_one_member_until_terminal(client, published):
    p = published
    p.state["events"] = lambda: p.members
    process(client, p.jobs)
    assert all(snapshot.count("completed") <= 1 for snapshot in p.state["snapshots"])
    assert p.state["snapshots"][1].count("completed") == 1
    assert all(p.store.job(job["id"])["state"] == "completed" for job in p.jobs)
    assert any(poll == 4 for poll, _ in p.state["downloads"])


@pytest.mark.parametrize("published", [6], indirect=True)
def test_parallel_ready_outputs_save_all_but_final_pending_member(client, published):
    p = published
    p.state["events"] = lambda: p.members
    process(client, p.jobs)
    assert p.state["snapshots"][0] == ["running"] * 6
    assert p.state["snapshots"][1].count("completed") == 5
    assert all(snapshot.count("completed") <= 5 for snapshot in p.state["snapshots"])
    assert sum(poll == 4 for poll, _ in p.state["downloads"]) == 1
    assert len(p.remote.submissions) == 1 and len(p.remote.uploads) == 6


@pytest.mark.parametrize("published", [{"cleanup": True}], indirect=True)
def test_cleanup_waits_for_terminal_state_and_every_local_result(client, published):
    p = published
    folder = native_batch.folder_for(p.store.native_batch(p.identifier))
    trash = trash_fake(p.remote, folder)

    def check():
        if p.state["poll"] == 3:
            assert p.store.job(p.jobs[0]["id"])["state"] == "completed"
            assert p.store.job(p.jobs[1]["id"])["state"] == "running"
            assert not trash["requests"]
            assert (
                p.store.native_batch(p.identifier)["params"]["ucloud_run"]["cleanup_state"]
                == "pending"
            )

    p.state["before_poll"] = check
    process(client, p.jobs)
    assert trash["requests"] == [{"items": [{"id": folder}]}]
    run = p.store.native_batch(p.identifier)["params"]["ucloud_run"]
    assert run["cleanup_state"] == "trashed" and run["remote_state"] == "SUCCESS"
    assert all(p.store.results(recording["id"]) for recording in p.recordings)


@pytest.mark.parametrize("problem", ["truncated", "changed", "timeout", "absent"])
def test_running_partial_or_changing_outputs_are_deferred_until_stably_published(
    client, published, problem
):
    p = published
    p.state["states"] = ["RUNNING"] * 4 + ["SUCCESS"]
    valid = json.dumps(PAYLOAD).encode()
    changed = json.dumps({"segments": [{"text": "Changed", "start": 0, "end": 1}]}).encode()
    before_poll = {}

    def observe():
        before_poll[p.state["poll"]] = [p.store.job(job["id"])["state"] for job in p.jobs]

    p.state["before_poll"] = observe

    def data(member):
        if p.state["poll"] == 1:
            if problem == "truncated":
                return b'{"segments":['
            if problem == "changed":
                return changed
            if problem == "timeout":
                return httpx.ReadTimeout("synthetic interrupted output")
        return valid

    p.state["data"] = data
    if problem == "absent":
        p.state["names"] = lambda member: (
            [] if p.state["poll"] == 1 else [member["job_id"] + "_large-v3_da.json"]
        )
    process(client, p.jobs)
    # Read-retry backoffs also call Event.wait; observe actual monitor polls instead.
    # The invalid/unavailable first read cannot count toward publication. Valid reads
    # at polls 2 and 3 must both finish before the first result can appear at poll 4.
    assert before_poll[2] == before_poll[3] == ["running", "running"]
    assert before_poll[4] == ["completed", "running"]
    raw = native_batch._raw_path(p.store, p.members[0]).read_bytes()
    assert raw == valid and len(p.store.results(p.recordings[0]["id"])) == 1


@pytest.mark.parametrize(
    "problem",
    [
        "cached-log",
        "foreign-log",
        "converted-log",
        "legacy-name",
        "ambiguous",
        "wrong-model",
        "wrong-language",
        "unknown-app",
        "legacy-app-gap",
        "publisher-version-only",
        "synthetic-batch-alias",
    ],
)
def test_unproven_publication_never_saves_before_terminal(client, published, problem):
    p = published
    names = p.state["names"]
    if problem == "cached-log":
        observed = native_batch._parse_log(processed(p.members[0]), p.members, "large-v3")
        cached = native_batch._merge_log_progress({"files": {}, "batch": None}, observed)
        p.store.update_native_batch(p.identifier, run_update={"log_progress": cached})
        p.state["events"] = lambda: None
    elif problem == "foreign-log":
        p.state["events"] = lambda: [{"filename": "f" * 32 + ".wav"}]
    elif problem == "converted-log":
        p.state["events"] = lambda: [{**p.members[0], "filename": p.jobs[0]["id"] + ".mp3"}]
    elif problem in {
        "unknown-app",
        "legacy-app-gap",
        "publisher-version-only",
        "synthetic-batch-alias",
    }:
        p.state["before_poll"] = lambda: p.store.update_native_batch(
            p.identifier,
            run_update={
                "output_application": {
                    "unknown-app": {"name": "transcriber", "version": "2.0"},
                    "legacy-app-gap": None,
                    "publisher-version-only": {"name": "transcriber", "version": "1.22"},
                    "synthetic-batch-alias": {"name": "transcriber-batch", "version": "1.12"},
                }[problem]
            },
        )
    else:

        def uncertain(member):
            if p.state["poll"] == 4:
                return names(member)
            base = member["job_id"]
            return {
                "legacy-name": [base + ".json"],
                "ambiguous": [*names(member), base + ".json"],
                "wrong-model": [base + "_large-v2_da.json"],
                "wrong-language": [base + "_large-v3_en.json"],
            }[problem]

        p.state["names"] = uncertain
    process(client, p.jobs)
    assert all(snapshot == ["running", "running"] for snapshot in p.state["snapshots"])
    assert all(poll == 4 for poll, _ in p.state["downloads"])
    assert all(len(p.store.results(recording["id"])) == 1 for recording in p.recordings)


@pytest.mark.parametrize("shutdown", [False, True], ids=["stop", "shutdown"])
def test_stop_or_shutdown_keeps_early_result_and_reconnects_without_repeating_work(
    client, published, shutdown
):
    p = published
    p.state["states"] = ["RUNNING"] * 3 + ["SUCCESS"]
    saved = []

    def stop():
        if p.state["poll"] == 3:
            assert p.store.job(p.jobs[0]["id"])["state"] == "completed"
            saved.append(deepcopy(p.store.results(p.recordings[0]["id"])[0]))
            getattr(p.queue, "stopping" if shutdown else "cancel_event").set()

    p.state["before_poll"] = stop
    with pytest.raises(Cancelled):
        process(client, p.jobs)
    assert p.store.job(p.jobs[0]["id"])["state"] == "completed"
    assert p.store.job(p.jobs[1]["id"])["state"] in {"cancelled", "interrupted"}
    assert len([c for c in p.remote.calls if c.url.path == "/api/jobs/terminate"]) == (not shutdown)
    p.queue.stopping.clear()
    p.state["before_poll"] = lambda: None
    retry(client, p.jobs)
    assert p.store.results(p.recordings[0]["id"]) == saved
    assert len(p.remote.submissions) == 1 and len(p.remote.uploads) == 2
    assert [poll for poll, name in p.state["downloads"] if name.startswith(p.jobs[0]["id"])] == [
        1,
        2,
    ]
    assert all(p.store.job(job["id"])["state"] == "completed" for job in p.jobs)


def test_early_result_manual_edits_survive_terminal_import_of_other_members(client, published):
    p = published
    edited = []

    def edit():
        if p.state["poll"] != 3:
            return
        recording = p.store.recording(p.recordings[0]["id"])
        segment = recording["segments"][0]
        response = client.patch(
            f"/api/recordings/{recording['id']}/segments/{segment['id']}",
            json={
                "revision": recording["revision"],
                "mode": "free",
                "text": "Reviewed wording stays here.",
            },
        )
        assert response.status_code == 200, response.text
        edited.append(deepcopy(p.store.recording(recording["id"])["segments"]))

    p.state["before_poll"] = edit
    process(client, p.jobs)
    assert p.store.recording(p.recordings[0]["id"])["segments"] == edited[0]
    assert len(p.store.results(p.recordings[0]["id"])) == 1
    assert [poll for poll, name in p.state["downloads"] if name.startswith(p.jobs[0]["id"])] == [
        1,
        2,
    ]


def test_terminal_invalid_remaining_output_keeps_early_result_and_strict_failure(client, published):
    p = published
    p.state["states"] = ["RUNNING", "RUNNING", "FAILURE"]
    p.state["data"] = lambda member: (
        b'{"segments":[' if member == p.members[1] else json.dumps(PAYLOAD).encode()
    )
    with pytest.raises(ValueError, match="1 of 2 batch outputs"):
        process(client, p.jobs)
    assert p.store.job(p.jobs[0]["id"])["state"] == "completed"
    assert p.store.job(p.jobs[1]["id"])["state"] == "failed"
    assert len(p.store.results(p.recordings[0]["id"])) == 1
    assert not p.store.results(p.recordings[1]["id"])
    assert p.store.native_batch(p.identifier)["state"] == "failed"


def test_early_transfer_never_softens_a_guard_failure(client, published, monkeypatch):
    p = published
    original, approval = native_batch._download, native_batch.providers.require_approval
    guard_failure = threading.local()

    def require(*args, **kwargs):
        if getattr(guard_failure, "armed", False):
            guard_failure.armed = False  # Even a one-time worker guard failure must propagate.
            raise ValueError("Synthetic frozen source approval changed")
        return approval(*args, **kwargs)

    def download(*args, **kwargs):
        if kwargs.get("published") and p.state["poll"] == 2:
            guard_failure.armed = True
            kwargs["guard"]()
        return original(*args, **kwargs)

    monkeypatch.setattr(native_batch.providers, "require_approval", require)
    monkeypatch.setattr(native_batch, "_download", download)
    with pytest.raises(ValueError, match="frozen source approval changed"):
        process(client, p.jobs)
    assert not any(p.store.results(recording["id"]) for recording in p.recordings)
    assert len(p.remote.submissions) == 1


def test_persistent_approval_denial_after_capture_prevents_early_commit(
    client, published, monkeypatch
):
    p = published
    original_write = native_capture.write_output
    approval = native_batch.providers.require_approval
    denied = [False]

    def write(*args, **kwargs):
        original_write(*args, **kwargs)
        denied[0] = True

    def require(*args, **kwargs):
        if denied[0]:
            raise ValueError("Synthetic persistent approval denial")
        return approval(*args, **kwargs)

    monkeypatch.setattr(native_capture, "write_output", write)
    monkeypatch.setattr(native_batch.providers, "require_approval", require)
    with pytest.raises(ValueError, match="persistent approval denial"):
        process(client, p.jobs)
    assert not any(p.store.results(recording["id"]) for recording in p.recordings)
    assert native_batch._raw_path(p.store, p.members[0]).is_file()
    capture = p.store.job(p.jobs[0]["id"])["params"]["native_artifact_capture"]
    assert isinstance(capture["output"], dict)


def test_retry_keeps_previously_captured_raw_bytes_when_remote_output_changes(
    client, published, monkeypatch
):
    p = published
    original = p.store.add_result

    def interrupt(*args, **kwargs):
        raise ValueError("Synthetic failure after raw capture and before result commit")

    monkeypatch.setattr(p.store, "add_result", interrupt)
    with pytest.raises(ValueError, match="after raw capture"):
        process(client, p.jobs)
    raw = native_batch._raw_path(p.store, p.members[0])
    accepted = raw.read_bytes()
    capture = deepcopy(p.store.job(p.jobs[0]["id"])["params"]["native_artifact_capture"])
    assert isinstance(capture["output"], dict) and not p.store.results(p.recordings[0]["id"])
    monkeypatch.setattr(p.store, "add_result", original)
    p.state["states"] = ["RUNNING"] * 5 + ["SUCCESS"]
    p.state["data"] = lambda member: (
        json.dumps({"segments": [{"text": "Changed remote B", "start": 0, "end": 1}]}).encode()
        if member == p.members[0]
        else json.dumps(PAYLOAD).encode()
    )

    def check():
        if p.state["poll"] >= 3:
            assert raw.read_bytes() == accepted
            assert p.store.job(p.jobs[0]["id"])["params"]["native_artifact_capture"] == capture

    p.state["before_poll"] = check
    retry(client, p.jobs)
    assert p.state["poll"] == 6
    assert raw.read_bytes() == accepted
    assert p.store.job(p.jobs[0]["id"])["params"]["native_artifact_capture"] == capture
    assert p.store.recording(p.recordings[0]["id"])["segments"][0]["text"] == "Hej CBS."
    assert [poll for poll, name in p.state["downloads"] if name.startswith(p.jobs[0]["id"])] == [
        1,
        2,
    ]
    assert len(p.remote.submissions) == 1 and len(p.remote.uploads) == 2
