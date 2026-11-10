"""Owned native logs are bounded historical observations, never processing authority."""

import json
from types import SimpleNamespace

import httpx
import pytest
from test_native_batch import batch_remote as batch_remote
from test_native_batch import enqueue, frozen_native_jobs
from test_native_batch import selection as selection
from test_ucloud_native import REAL_CLIENT, SECRET, SETUP
from test_ucloud_native import remote as remote

from cbs_transcriber import native_batch, providers
from cbs_transcriber.media import Cancelled

MEMBERS = [
    {"job_id": "a" * 32, "filename": "a" * 32 + ".m4a"},
    {"job_id": "b" * 32, "filename": "b" * 32 + ".wav"},
]
FOLDER = "/drive/cbs/cbs-batch-" + "c" * 32


def start(member, timestamp="2026-11-09 13:38:19,694", *, model="large-v3"):
    return (
        (timestamp + " - " if timestamp else "")
        + f"Starting {member['filename']} duration: 4544 seconds on device: cuda model: {model}\n"
    ).encode()


def processed(member):
    return (
        "2026-11-09 13:52:54,430 - Processed " + member["filename"] + " it took 00:14:34.645837\n"
    ).encode()


def test_processed_exact_uuid_is_historical_and_does_not_complete_the_next_file():
    data = start(MEMBERS[0]) + processed(MEMBERS[0]) + start(MEMBERS[1])
    parsed = native_batch._parse_log(data, MEMBERS, "large-v3")
    assert parsed["files"][MEMBERS[0]["job_id"]]["stage"] == "processed_reported"
    assert parsed["files"][MEMBERS[1]["job_id"]]["stage"] == "started"
    assert parsed["batch"] is None
    assert (
        native_batch._parse_log(processed({"filename": "d" * 32 + ".m4a"}), MEMBERS, "large-v3")[
            "files"
        ]
        == {}
    )


def test_parser_maps_exact_frozen_inputs_and_keeps_interleaved_loading_batch_only():
    data = (
        b"2026-11-09 13:38:18,000 - Threads initialised: 6\nProcessing #3..\n"
        + start(MEMBERS[0])
        + start(MEMBERS[1])
        + b"2026-11-09 13:38:20,001 - Loading large-v3 to 00:00:07.535745\n"
        + b"SUCCESS finished complete all files\n"
    )
    parsed = native_batch._parse_log(data, MEMBERS, "large-v3")
    assert set(parsed["files"]) == {member["job_id"] for member in MEMBERS}
    assert {item["stage"] for item in parsed["files"].values()} == {"started"}
    assert parsed["batch"]["stage"] == "model_load_reported"
    assert "model_load_reported" not in [v["stage"] for v in parsed["files"].values()]
    assert not any(word in json.dumps(parsed) for word in ("cuda", "duration", "SUCCESS", "m4a"))


@pytest.mark.parametrize(
    "line",
    [
        start({"filename": "d" * 32 + ".m4a"}),
        start({"filename": "../" + MEMBERS[0]["filename"]}),
        start({"filename": MEMBERS[0]["filename"] + ".bak"}),
        start(MEMBERS[0], model="large-v2"),
        start(MEMBERS[0], timestamp="2026-99-99 13:38:19,694"),
        b"Processing #1..\n",
        b"Threads initialised: 6\n",
        b"Loading large-v2 to 00:00:07\n",
        b"Authorization: Bearer " + SECRET.encode() + b"\n",
    ],
)
def test_parser_ignores_unowned_names_models_ordinals_and_unallowlisted_text(line):
    assert native_batch._parse_log(line, MEMBERS, "large-v3") == {"files": {}, "batch": None}


def test_repeated_or_older_rotated_events_do_not_churn_the_durable_observation(monkeypatch):
    times = iter(["2026-11-09T14:00:00+00:00", "2026-11-09T14:01:00+00:00"])
    monkeypatch.setattr(native_batch, "now", lambda: next(times))
    parsed = native_batch._parse_log(start(MEMBERS[0]), MEMBERS, "large-v3")
    previous = native_batch._merge_log_progress({"files": {}, "batch": None}, parsed)
    assert native_batch._merge_log_progress(previous, parsed) == previous
    older = native_batch._parse_log(
        start(MEMBERS[0], "2026-11-08 13:38:19,694"), MEMBERS, "large-v3"
    )
    assert native_batch._merge_log_progress(previous, older) == previous
    undated = native_batch._parse_log(start(MEMBERS[0], ""), MEMBERS, "large-v3")
    assert native_batch._merge_log_progress(previous, undated) == previous
    later = native_batch._parse_log(
        start(MEMBERS[0], "2026-11-09 14:01:00,000"), MEMBERS, "large-v3"
    )
    assert native_batch._merge_log_progress(previous, later)["files"][MEMBERS[0]["job_id"]][
        "observed_at"
    ].endswith("14:01:00+00:00")


def test_observations_remain_bounded_to_latest_64_exact_members():
    members = [{"job_id": f"{i:032x}", "filename": f"{i:032x}.wav"} for i in range(100)]
    parsed = native_batch._parse_log(
        b"".join(start(member) for member in members), members, "large-v3"
    )
    assert list(parsed["files"]) == [member["job_id"] for member in members[-64:]]


@pytest.fixture
def log_http(monkeypatch):
    state = {"data": start(MEMBERS[0]), "mode": "ok", "calls": [], "streams": [], "clock": None}
    settings = providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="large-v3",
        ucloud_transcriber=SETUP,
    )

    class Stream(httpx.SyncByteStream):
        def __init__(self, data, stage):
            self.parts = [data]
            self.stage, self.delivered, self.closed = stage, 0, False
            if state["mode"] == "slow-" + stage:
                self.parts = [bytes([byte]) for byte in data]
            state["streams"].append(self)

        def __iter__(self):
            for part in self.parts:
                self.delivered += 1
                if state["mode"] == "slow-" + self.stage:
                    state["clock"][0] += 0.5
                if state.get("on_block") is not None:
                    state["on_block"](self.stage)
                yield part

        def close(self):
            self.closed = True

    def response(status, data, stage, headers=None):
        return httpx.Response(status, stream=Stream(data, stage), headers=headers)

    def handle(request):
        state["calls"].append(request)
        mode, data = state["mode"], state["data"]
        if request.url.host == "cloud.invalid":
            assert request.headers["Authorization"] == "Bearer " + SECRET
            assert request.headers["Project"] == "project"
            assert request.headers["Accept-Encoding"] == "identity"
            if request.url.path == "/api/files/retrieve":
                assert (
                    request.method == "GET"
                    and request.url.params["id"] == FOLDER + "/out/transcribe.log"
                )
                metadata = {
                    "id": FOLDER + "/out/transcribe.log",
                    "status": {"type": "FILE", "sizeInBytes": len(data)},
                }
                if mode == "missing":
                    return response(404, SECRET.encode(), "metadata")
                if mode == "status-list":
                    metadata["status"] = []
                if mode == "wrong-id":
                    metadata["id"] = "/private/other.log"
                if mode == "directory":
                    metadata["status"]["type"] = "DIRECTORY"
                if mode == "boolean-size":
                    metadata["status"]["sizeInBytes"] = True
                if mode == "large-json":
                    metadata["extra"] = "x" * native_batch.LOG_JSON_BYTES
                if mode == "consumed":
                    return httpx.Response(200, json=metadata)
                return response(200, json.dumps(metadata).encode(), "metadata")
            assert request.url.path == "/api/files/download" and request.method == "POST"
            assert json.loads(request.content) == {
                "items": [{"id": FOLDER + "/out/transcribe.log"}]
            }
            endpoint = "https://files.invalid/log?temporary-session=public-test"
            if mode == "http-endpoint":
                endpoint = "http://files.invalid/log"
            if mode == "nul-endpoint":
                endpoint = "https://files.invalid/\0"
            return response(
                200, json.dumps({"responses": [{"endpoint": endpoint}]}).encode(), "session"
            )
        assert request.url.host == "files.invalid" and request.url.path == "/log"
        assert request.method == "GET" and "authorization" not in request.headers
        assert "project" not in request.headers and SECRET not in str(request.headers)
        offset = max(0, len(data) - native_batch.LOG_BYTES)
        assert request.headers["Range"] == f"bytes={offset}-{len(data) - 1}"
        headers = {"Content-Range": f"bytes {offset}-{len(data) - 1}/{len(data)}"}
        body = data[offset:]
        if mode == "ignored-range":
            return response(200, data, "tail")
        if mode == "changed-size":
            headers["Content-Range"] += "0"
        if mode == "wrong-range":
            headers["Content-Range"] = "bytes 0-1/2"
        if mode == "short":
            body = body[:-1]
        if mode == "oversize":
            body += b"x"
        if mode == "encoded":
            headers["Content-Encoding"] = "gzip"
        return response(206, body, "tail", headers)

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: REAL_CLIENT(*a, **kw, transport=httpx.MockTransport(handle)),
    )
    state["read"] = lambda guard=lambda: None: native_batch._read_log(
        settings, SECRET, FOLDER, guard
    )
    return state


def test_log_read_uses_exact_owned_path_and_separate_transient_transport(log_http):
    assert log_http["read"]() == log_http["data"]
    assert len(log_http["calls"]) == 3
    assert all(stream.closed for stream in log_http["streams"])


@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "status-list",
        "wrong-id",
        "directory",
        "boolean-size",
        "large-json",
        "consumed",
        "http-endpoint",
        "nul-endpoint",
        "changed-size",
        "wrong-range",
        "short",
        "oversize",
        "encoded",
    ],
)
def test_unavailable_or_malformed_log_is_a_soft_failure(log_http, mode):
    log_http["mode"] = mode
    assert log_http["read"]() is None
    assert all(stream.closed for stream in log_http["streams"])
    assert not [request for request in log_http["calls"] if request.url.path == "/api/jobs"]


def test_ignored_large_range_is_closed_without_reading_the_full_log(log_http):
    log_http["data"] = b"x" * (native_batch.LOG_BYTES + 100)
    log_http["mode"] = "ignored-range"
    assert log_http["read"]() is None
    assert [stream.delivered for stream in log_http["streams"] if stream.stage == "tail"] == [0]


def test_tail_cut_lines_do_not_manufacture_events(log_http):
    log_http["data"] = (
        b"x" * native_batch.LOG_BYTES + b"\n" + start(MEMBERS[0]) + start(MEMBERS[1]).rstrip(b"\n")
    )
    data = log_http["read"]()
    assert len(data) <= native_batch.LOG_BYTES
    parsed = native_batch._parse_log(data, MEMBERS, "large-v3")
    assert list(parsed["files"]) == [MEMBERS[0]["job_id"]]


@pytest.mark.parametrize("stage", ["metadata", "tail"])
def test_slow_trickles_are_stopped_by_per_transport_block_budget(log_http, monkeypatch, stage):
    clock = [0.0]
    log_http.update(mode="slow-" + stage, clock=clock)
    monkeypatch.setattr(native_batch, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    assert log_http["read"]() is None
    slow = [stream for stream in log_http["streams"] if stream.stage == stage]
    assert len(slow) == 1 and slow[0].delivered <= 7
    assert all(stream.closed for stream in log_http["streams"])


def test_local_batch_guard_work_does_not_consume_the_log_io_budget(log_http, monkeypatch):
    # A 12-file live batch guard performs 49 local SQLite checks. Copied-library
    # measurements put one warm pass around 0.35 s; model that work without sleeps.
    # All transport requests/chunks remain instant and all guard calls still run.
    clock, checks = [0.0], []
    monkeypatch.setattr(native_batch, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def guard():
        checks.append(True)
        clock[0] += 0.4

    assert log_http["read"](guard) == log_http["data"]
    assert clock[0] > native_batch.LOG_READ_SECONDS
    assert len(checks) >= 2 * len(log_http["calls"])
    assert len(log_http["calls"]) == 3
    assert all(stream.closed for stream in log_http["streams"])


@pytest.mark.parametrize("stage", ["metadata", "session", "tail"])
def test_expensive_guards_do_not_extend_time_allowed_for_slow_log_io(log_http, monkeypatch, stage):
    clock = [0.0]
    log_http.update(mode="slow-" + stage, clock=clock)
    monkeypatch.setattr(native_batch, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def guard():
        clock[0] += 0.4

    assert log_http["read"](guard) is None
    slow = [stream for stream in log_http["streams"] if stream.stage == stage]
    assert len(slow) == 1 and 0 < slow[0].delivered <= 7
    assert all(stream.closed for stream in log_http["streams"])


def test_expensive_guard_failure_during_log_stream_propagates_exactly(log_http, monkeypatch):
    clock, accepted = [0.0], []
    denied = ValueError("Frozen approval changed during the accepted log read")
    monkeypatch.setattr(native_batch, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    log_http["on_block"] = lambda stage: accepted.append(stage)

    def guard():
        clock[0] += 0.4
        if accepted:
            raise denied

    with pytest.raises(ValueError) as caught:
        log_http["read"](guard)
    assert caught.value is denied
    assert all(stream.closed for stream in log_http["streams"])


def test_log_read_propagates_stop_instead_of_hiding_it(log_http):
    def guard():
        raise Cancelled()

    with pytest.raises(Cancelled):
        log_http["read"](guard)


def test_log_read_does_not_soften_original_identity_or_approval_guard_failure(log_http):
    def guard():
        raise ValueError("Frozen source identity or approval changed")

    with pytest.raises(ValueError, match="identity or approval changed"):
        log_http["read"](guard)
    assert not log_http["calls"]


@pytest.mark.parametrize("stage", ["metadata", "tail"])
def test_stop_during_an_accepted_log_stream_closes_every_context(log_http, stage):
    stopped = []
    log_http["on_block"] = lambda current: stopped.append(True) if current == stage else None

    def guard():
        if stopped:
            raise Cancelled()

    with pytest.raises(Cancelled):
        log_http["read"](guard)
    accepted = [stream for stream in log_http["streams"] if stream.stage == stage]
    assert len(accepted) == 1 and accepted[0].delivered == 1
    assert all(stream.closed for stream in log_http["streams"])
    assert not [r for r in log_http["calls"] if r.url.path == "/api/jobs"]


def test_monitor_rate_limits_and_persists_sanitized_historical_notes_without_completion(
    client, batch_remote, selection, monkeypatch
):
    provider, recordings = selection
    store, queue = client.app.state.store, client.app.state.queue
    batch_remote.store = store
    batch_remote.states = ["RUNNING"] * 5 + ["SUCCESS"]
    jobs = enqueue(client, provider, recordings)
    identifier = jobs[0]["params"]["native_batch_id"]
    members = store.native_batch(identifier)["members"]
    data = (
        start(members[0])
        + processed(members[0])
        + start(members[1], "2026-11-09 13:52:55,000")
        + b"Loading large-v3 to 00:00:07.535745\n"
        + SECRET.encode()
        + b"\n"
    )
    clock, reads, writes = [0.0], [], []
    monkeypatch.setattr(native_batch, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(native_batch, "_read_log", lambda *a: reads.append(clock[0]) or data)
    original = store.update_native_batch

    def update(identifier, **kwargs):
        if "log_progress" in (kwargs.get("run_update") or {}):
            writes.append(kwargs["run_update"]["log_progress"])
        return original(identifier, **kwargs)

    def wait(seconds):
        fields = native_batch.status_fields(store, store.job(jobs[0]["id"]))
        assert all(member["state"] == "running" for member in fields["ucloud_batch_members"])
        notes = {member["job_id"]: member["message"] for member in fields["ucloud_batch_members"]}
        assert "Log reported: processing finished" in notes[members[0]["job_id"]]
        assert "Log reported: processing started" in notes[members[1]["job_id"]]
        assert "Last log: model loading reported" in fields["ucloud_batch_message"]
        assert SECRET not in json.dumps(fields) and "duration:" not in json.dumps(fields)
        assert not any(store.results(recording["id"]) for recording in recordings)
        clock[0] += seconds

    monkeypatch.setattr(store, "update_native_batch", update)
    monkeypatch.setattr(queue.cancel_event, "wait", wait)
    claimed = store.claim_job("transcribe")
    queue.active_batch_id = identifier
    queue.process(claimed["id"])
    assert reads == [0, 15] and len(writes) == 1
    assert len(batch_remote.submissions) == 1
    assert all(store.job(job["id"])["message"] == "Complete" for job in jobs)
    assert all(
        isinstance(store.job(job["id"])["params"]["native_artifact_capture"]["output"], dict)
        for job in jobs
    )
    final = native_batch.status_fields(store, store.job(jobs[0]["id"]))
    assert all(member["message"] == "Complete" for member in final["ucloud_batch_members"])
    assert len(store.native_batch(identifier)["params"]["ucloud_run"]["log_progress"]["files"]) == 2


@pytest.mark.parametrize("state", ["failed", "queued", "interrupted", "completed"])
def test_historical_note_projection_preserves_specific_messages_and_validated_completion(
    client, selection, state
):
    provider, recordings = selection
    store = client.app.state.store
    jobs = frozen_native_jobs(client, provider, recordings)
    identifier = jobs[0]["params"]["native_batch_id"]
    members = store.native_batch(identifier)["members"]
    parsed = native_batch._parse_log(start(members[0]), members, "large-v3")
    progress = native_batch._merge_log_progress({"files": {}, "batch": None}, parsed)
    progress["files"][jobs[0]["id"]]["private"] = SECRET  # Never project arbitrary fields.
    store.update_native_batch(identifier, run_update={"log_progress": progress})
    store.update_job(jobs[0]["id"], state=state, message="Keep this specific recovery message")
    fields = native_batch.status_fields(store, store.job(jobs[0]["id"]))
    [member] = [
        member for member in fields["ucloud_batch_members"] if member["job_id"] == jobs[0]["id"]
    ]
    assert member["message"].startswith("Keep this specific recovery message")
    assert ("Log reported:" in member["message"]) == (state != "completed")
    assert SECRET not in json.dumps(fields)
    assert store.job(jobs[0]["id"])["message"] == "Keep this specific recovery message"


def test_explicit_retry_reads_terminal_runs_log_before_validated_results_without_resubmission(
    client, batch_remote, selection, monkeypatch
):
    provider, recordings = selection
    store, queue = client.app.state.store, client.app.state.queue
    batch_remote.store = store
    jobs = frozen_native_jobs(client, provider, recordings)
    identifier = jobs[0]["params"]["native_batch_id"]
    batch = store.native_batch(identifier)
    folder = native_batch.folder_for(batch)
    store.update_native_batch(
        identifier,
        run_update={
            "job_id": "remote-job",
            "remote_state": "RUNNING",
            "owned_folder": folder,
            "folders_created": True,
            "output_model": "large-v3",
        },
    )
    store.finish_native_batch(identifier, "interrupted", "Earlier app shutdown")
    reads = []

    def read(settings, key, owned, guard):
        guard()
        assert owned == folder
        assert not any(store.results(recording["id"]) for recording in recordings)
        reads.append(owned)
        return start(batch["members"][0])

    monkeypatch.setattr(native_batch, "_read_log", read)
    assert client.post("/api/jobs/" + jobs[0]["id"] + "/retry", json={}).status_code == 200
    claimed = store.claim_job("transcribe")
    queue.active_batch_id = identifier
    queue.process(claimed["id"])
    assert reads == [folder] and not batch_remote.submissions and not batch_remote.uploads
    saved = store.native_batch(identifier)
    assert saved["params"]["ucloud_run"]["job_id"] == "remote-job"
    assert saved["params"]["ucloud_run"]["log_progress"]["files"]
    assert saved["state"] == "completed"
    assert all(len(store.results(recording["id"])) == 1 for recording in recordings)
