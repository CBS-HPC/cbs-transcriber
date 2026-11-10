"""Retry only owned output reads, preserving saved siblings and all allocation authority."""

import json
import threading
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from test_native_batch import batch_remote as batch_remote
from test_native_batch import selection as selection
from test_native_batch_incremental import published as published
from test_native_batch_transfers import process, retry
from test_ucloud_native import PAYLOAD, REAL_CLIENT, SECRET
from test_ucloud_native import remote as remote
from ucloud_workflow import UCloudAPIError

from cbs_transcriber import native_batch, providers
from cbs_transcriber.media import Cancelled

FOLDER = "/drive/cbs/cbs-batch-" + "c" * 32 + "/out"
FILENAME = "a" * 32 + ".wav"
OUTPUT = FOLDER + "/" + "a" * 32 + "_large-v3_da.json"


@pytest.fixture
def output_http(monkeypatch):
    state = {"errors": [], "stage": "download", "calls": [], "waits": [], "guards": 0}
    payload = json.dumps(PAYLOAD).encode()

    def failure(stage):
        if stage == state["stage"] and state["errors"]:
            value = state["errors"].pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        return None

    class Control:
        def request(self, method, path, **kwargs):
            state["calls"].append("session")
            assert method == "POST" and path == "/api/files/download"
            assert kwargs["json"] == {"items": [{"id": OUTPUT}]}
            failure("session")
            return httpx.Response(
                200, json={"responses": [{"endpoint": "https://files.invalid/output"}]}
            )

        def browse_files(self, folder, **kwargs):
            state["calls"].append("metadata")
            assert folder == FOLDER
            failure("metadata")
            return {"items": [{"id": OUTPUT}]}

    class Partial(httpx.SyncByteStream):
        def __iter__(self):
            yield payload[:12]
            raise httpx.ReadError(SECRET)

    def handle(request):
        state["calls"].append("download")
        assert request.method == "GET" and request.url.host == "files.invalid"
        assert "authorization" not in request.headers
        value = failure("download")
        if value == "partial":
            return httpx.Response(200, stream=Partial())
        return httpx.Response(value or 200, content=SECRET.encode() if value else payload)

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *args, **kwargs: REAL_CLIENT(*args, **kwargs, transport=httpx.MockTransport(handle)),
    )
    monkeypatch.setattr(native_batch, "time", SimpleNamespace(sleep=state["waits"].append))

    def guard():
        state["guards"] += 1

    state["client"], state["guard"], state["payload"] = Control(), guard, payload
    state["read"] = lambda: native_batch._download(
        state["client"],
        "https://cloud.invalid",
        {OUTPUT},
        FOLDER,
        FILENAME,
        "large-v3",
        "da",
        guard=state["guard"],
    )
    return state


def test_transient_output_transfer_retries_the_same_read_without_submission(output_http):
    output_http["errors"] = [httpx.ReadTimeout(SECRET)]
    assert output_http["read"]() == output_http["payload"]
    assert output_http["calls"] == ["session", "download", "download"]
    assert output_http["waits"] == [2]


@pytest.mark.parametrize("stage", ["metadata", "session", "download"])
@pytest.mark.parametrize("status", [502, 503, 504])
def test_gateway_failures_retry_only_the_same_output_read(output_http, stage, status):
    output_http.update(
        stage=stage,
        errors=[status if stage == "download" else UCloudAPIError(SECRET, status_code=status)],
    )
    if stage == "metadata":
        assert native_batch._output_paths(output_http["client"], FOLDER) == {OUTPUT}
    else:
        assert output_http["read"]() == output_http["payload"]
    assert output_http["calls"].count(stage) == 2
    assert output_http["waits"] == [2]


def test_retry_drops_partial_transfer_bytes_instead_of_appending_them(output_http):
    output_http["errors"] = ["partial"]
    assert output_http["read"]() == output_http["payload"]
    assert output_http["calls"].count("download") == 2


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_auth_missing_and_nontransient_output_failures_are_not_retried(output_http, status):
    output_http["errors"] = [status]
    with pytest.raises(native_batch.OutputReadError) as caught:
        output_http["read"]()
    assert caught.value.http_status == status
    assert f"HTTP {status}" in str(caught.value) and SECRET not in str(caught.value)
    assert output_http["calls"].count("download") == 1 and not output_http["waits"]


def test_exhausted_download_reports_only_curated_stage_status_and_attempts(output_http):
    output_http["errors"] = [502] * 10
    with pytest.raises(native_batch.OutputReadError) as caught:
        output_http["read"]()
    assert caught.value.details == {"stage": "download", "http_status": 502, "attempts": 3}
    assert output_http["calls"].count("download") == 3
    assert output_http["waits"] == [2, 5]
    assert SECRET not in str(caught.value)
    assert "Choose Retry failed" in str(caught.value)


@pytest.mark.parametrize(
    "error", [Cancelled(), ValueError("approval changed"), httpx.ReadError(SECRET)]
)
def test_guard_failures_are_propagated_exactly_without_retry(output_http, error):
    def guard():
        if output_http["calls"].count("download"):
            raise error

    output_http["guard"] = guard
    with pytest.raises(type(error)) as caught:
        output_http["read"]()
    assert caught.value is error
    assert output_http["calls"].count("download") == 1 and not output_http["waits"]


def test_stop_during_read_backoff_prevents_another_request(output_http, monkeypatch):
    stopped = []
    output_http["errors"] = [httpx.ReadTimeout(SECRET)]

    def guard():
        if stopped:
            raise Cancelled()

    output_http["guard"] = guard
    monkeypatch.setattr(native_batch.time, "sleep", lambda _: stopped.append(True))
    with pytest.raises(Cancelled):
        output_http["read"]()
    assert output_http["calls"].count("download") == 1


@pytest.mark.parametrize(
    "error",
    [
        {"stage": SECRET, "http_status": 502, "attempts": 3},
        {"stage": "download", "http_status": True, "attempts": 3},
        {"stage": "download", "http_status": 502, "attempts": True},
        {"stage": "download", "http_status": 502, "attempts": 4},
        {"stage": "download", "http_status": 502, "attempts": 3, "body": SECRET},
    ],
)
def test_malformed_diagnostics_cannot_enter_the_public_projection(error):
    member = {"job_id": "a" * 32}
    assert (
        native_batch._output_errors({"output_read_errors": {member["job_id"]: error}}, [member])
        == {}
    )


def test_diagnostic_maps_are_bounded_and_ignore_foreign_members():
    error = {"stage": "download", "http_status": 502, "attempts": 3}
    member = {"job_id": "a" * 32}
    assert native_batch._output_errors({"output_read_errors": {"foreign": error}}, [member]) == {}
    oversized = {str(index): error for index in range(native_batch.LOG_MEMBER_LIMIT + 1)}
    assert native_batch._output_errors({"output_read_errors": oversized}, [member]) == {}


def test_optional_publication_listing_never_softens_a_guard_failure(client, published, monkeypatch):
    p = published
    paths, approval = native_batch._output_paths, providers.require_approval
    fault = threading.local()

    def require(*args, **kwargs):
        if getattr(fault, "armed", False):
            fault.armed = False
            raise ValueError("Synthetic one-time original approval denial")
        return approval(*args, **kwargs)

    def listing(*args, **kwargs):
        if p.state["poll"] == 1:
            fault.armed = True
        return paths(*args, **kwargs)

    monkeypatch.setattr(providers, "require_approval", require)
    monkeypatch.setattr(native_batch, "_output_paths", listing)
    with pytest.raises(ValueError, match="one-time original approval denial"):
        process(client, p.jobs)
    assert not any(p.store.results(recording["id"]) for recording in p.recordings)


def test_a_later_parse_failure_does_not_project_an_older_read_error(client, published, monkeypatch):
    p = published
    p.state["states"], p.state["events"] = ["SUCCESS"], lambda: []
    target, original = p.jobs[0]["id"], p.remote.handle
    state = {"mode": "gateway", "reads": 0}

    def handle(request):
        if request.url.path == "/output" and request.url.params["member"] == target:
            state["reads"] += 1
            if state["mode"] == "gateway":
                return httpx.Response(503, text=SECRET)
            return httpx.Response(200, content=b'{"segments":[')
        return original(request)

    p.remote.handle = handle
    monkeypatch.setattr(p.queue.cancel_event, "wait", lambda _: False)
    with pytest.raises(ValueError, match="1 of 2"):
        process(client, p.jobs)
    assert (
        p.store.native_batch(p.identifier)["params"]["ucloud_run"]["output_read_errors"][target][
            "http_status"
        ]
        == 503
    )
    sibling = deepcopy(p.store.results(p.jobs[1]["recording_id"]))
    assert len(sibling) == 1
    sibling_error = {"stage": "metadata", "http_status": 502, "attempts": 2}
    read_errors = deepcopy(
        p.store.native_batch(p.identifier)["params"]["ucloud_run"]["output_read_errors"]
    )
    read_errors[p.jobs[1]["id"]] = sibling_error
    p.store.update_native_batch(p.identifier, run_update={"output_read_errors": read_errors})

    state["mode"] = "malformed-json"
    with pytest.raises(ValueError, match="1 of 2"):
        retry(client, p.jobs)
    assert state["reads"] == 4  # Three gateway attempts, then one invalid JSON; no parse retry.
    fields = native_batch.status_fields(p.store, p.store.job(target))
    failed = next(x for x in fields["ucloud_batch_members"] if x["job_id"] == target)
    assert "HTTP 503" not in failed["message"]
    assert target not in p.store.native_batch(p.identifier)["params"]["ucloud_run"].get(
        "output_read_errors", {}
    )
    assert (
        p.store.native_batch(p.identifier)["params"]["ucloud_run"]["output_read_errors"][
            p.jobs[1]["id"]
        ]
        == sibling_error
    )
    completed = next(x for x in fields["ucloud_batch_members"] if x["job_id"] == p.jobs[1]["id"])
    assert completed["state"] == "completed" and "HTTP 502" not in completed["message"]
    assert p.store.results(p.jobs[1]["recording_id"]) == sibling
    assert len(p.remote.submissions) == 1


@pytest.mark.parametrize("published", [{"count": 12}], indirect=True)
def test_retry_recovers_one_failed_output_and_preserves_eleven_saved_results(
    client, published, monkeypatch
):
    p = published
    p.state["states"], p.state["events"] = ["SUCCESS"], lambda: []
    target = p.jobs[-1]["id"]
    original = p.remote.handle
    state = {"failures": 10, "reads": 0}

    def handle(request):
        if request.url.path == "/output" and request.url.params["member"] == target:
            state["reads"] += 1
            if state["failures"]:
                state["failures"] -= 1
                return httpx.Response(502, text=SECRET)
        return original(request)

    p.remote.handle = handle
    monkeypatch.setattr(p.queue.cancel_event, "wait", lambda _: False)
    with pytest.raises(ValueError, match="1 of 12"):
        process(client, p.jobs)
    assert state["reads"] == 3
    saved = {job["id"]: deepcopy(p.store.results(job["recording_id"])) for job in p.jobs[:-1]}
    assert all(len(result) == 1 for result in saved.values())
    batch = p.store.native_batch(p.identifier)
    assert batch["params"]["ucloud_run"]["job_id"] == "remote-job"
    assert batch["params"]["ucloud_run"]["output_read_errors"][target] == {
        "stage": "download",
        "http_status": 502,
        "attempts": 3,
    }
    # Even the generic queue finalizer cannot erase the per-file curated explanation.
    p.store.finish_native_batch(p.identifier, "failed", "Generic batch error")
    fields = native_batch.status_fields(p.store, p.store.job(target))
    failed = next(x for x in fields["ucloud_batch_members"] if x["job_id"] == target)
    assert "download" in failed["message"] and "HTTP 502" in failed["message"]
    assert SECRET not in json.dumps(fields)

    state["failures"] = 1
    retry(client, p.jobs)
    assert state["reads"] == 5
    assert len(p.remote.submissions) == 1 and len(p.remote.uploads) == 12
    assert p.store.native_batch(p.identifier)["state"] == "completed"
    assert target not in p.store.native_batch(p.identifier)["params"]["ucloud_run"].get(
        "output_read_errors", {}
    )
    for job in p.jobs[:-1]:
        assert p.store.results(job["recording_id"]) == saved[job["id"]]
    assert len(p.store.results(p.jobs[-1]["recording_id"])) == 1


@pytest.mark.parametrize("published", [{"count": 12}], indirect=True)
def test_streamed_download_checks_current_metadata_without_hydrating_transcripts(
    client, published, monkeypatch
):
    p = published
    p.state["states"], p.state["events"] = ["SUCCESS"], lambda: []
    active = threading.local()
    observed = {"blocks": 0}
    original_handle = p.remote.handle
    original_recording = p.store.recording

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            data = json.dumps(PAYLOAD).encode()
            active.streaming = True
            try:
                for block in (data[: len(data) // 2], data[len(data) // 2 :]):
                    observed["blocks"] += 1
                    yield block
            finally:
                active.streaming = False

    def handle(request):
        if request.url.path == "/output":
            return httpx.Response(200, stream=Body())
        return original_handle(request)

    def recording(identifier):
        assert not getattr(active, "streaming", False), (
            "Download safety checks must not hydrate unrelated transcript/Working payloads"
        )
        return original_recording(identifier)

    p.remote.handle = handle
    monkeypatch.setattr(p.store, "recording", recording)
    process(client, p.jobs)
    assert observed["blocks"] == 2 * len(p.jobs)
    assert all(p.store.job(job["id"])["state"] == "completed" for job in p.jobs)
    assert len(p.remote.submissions) == 1


def test_streamed_download_still_rejects_a_member_moved_to_another_project(client, published):
    p = published
    p.state["states"], p.state["events"] = ["SUCCESS"], lambda: []
    destination = p.store.create_project("Other synthetic project")
    original_handle = p.remote.handle
    changed = threading.Event()

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            if not changed.is_set():
                p.store.execute(
                    "UPDATE recordings SET project_id=? WHERE id=?",
                    (destination["id"], p.recordings[0]["id"]),
                )
                changed.set()
            yield json.dumps(PAYLOAD).encode()

    def handle(request):
        if request.url.path == "/output":
            return httpx.Response(200, stream=Body())
        return original_handle(request)

    p.remote.handle = handle
    with pytest.raises(ValueError, match="batch outputs could not be imported"):
        process(client, p.jobs)
    assert changed.is_set()
    assert not any(p.store.results(recording["id"]) for recording in p.recordings)
    assert len(p.remote.submissions) == 1
