"""V218: bounded preparation observations; every provider/transfer is synthetic."""

import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import quote, unquote

import httpx
import pytest
import test_runtime_preparation as preparation_fixtures
from test_runtime_preparation import enqueue, execute
from ucloud_workflow import UCloudAPIError

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import providers
from cbs_transcriber import runtime_preparation as subject
from cbs_transcriber import runtime_preparation_observation as reader

REAL_HTTPX_CLIENT = httpx.Client
REAL_READ_JSON = api.read_json
REAL_STATE, REAL_JSON, REAL_STAMP = reader.state, reader.read_json, reader.stamp


class ObservationHarness:
    def __init__(self, fixture, monkeypatch):
        self.fixture, self.cloud = fixture, fixture.cloud
        self.settings = providers.Settings.model_validate(
            api.connection(fixture.store, fixture.credentials, [])["settings"]
        )
        self.runtime = api.runtime_folder(self.settings)
        self.files, self.reads, self.controls = {}, [], []
        self.state_replies = []
        self.after_read = None
        self.original_retrieve = self.cloud.retrieve_job
        self.cloud.retrieve_job = self.retrieve_job
        self.cloud.request = self.request
        self.cloud._client = SimpleNamespace(stream=self.stream)
        monkeypatch.setattr(api, "read_json", REAL_READ_JSON)
        monkeypatch.setattr(api.httpx, "Client", self.transport)
        monkeypatch.setattr(reader, "state", REAL_STATE)
        monkeypatch.setattr(reader, "read_json", REAL_JSON)
        monkeypatch.setattr(reader, "stamp", REAL_STAMP)

    def retrieve_job(self, identifier):
        if self.state_replies:
            reply = self.state_replies.pop(0)
            if isinstance(reply, BaseException):
                raise reply
            self.cloud.state = reply
        self.cloud.ready = True  # The allocation publishes its stamp after submission.
        return self.original_retrieve(identifier)

    def content(self, path):
        if path in self.files:
            entries = self.files[path]
            value = entries.pop(0) if len(entries) > 1 else entries[0]
        elif path.endswith("/stamp.json"):
            value = api.runtime_stamp_bytes()
        elif path.endswith("/supervisor.json"):
            value = {
                "phase": "finished",
                "generation": path.split("cbs-enhancement-", 1)[1].split("/", 1)[0],
            }
        elif path.endswith("/doctor.json"):
            value = copy.deepcopy(self.cloud.doctor)
        else:
            value = None
        return json.dumps(value).encode() if isinstance(value, (dict, list)) else value

    def request(self, method, path, *, params=None, json=None, **_):
        self.controls.append((method, path, params, json))
        if (method, path) == ("GET", "/api/jobs/retrieve"):
            value = copy.deepcopy(self.retrieve_job(params["id"]))
            if params.get("includeApplication") != "true":
                value["specification"].pop("application", None)
            if params.get("includeParameters") != "true":
                value["specification"].pop("parameters", None)
            return httpx.Response(200, json=value)
        if (method, path) == ("GET", "/api/files/retrieve"):
            target = params["id"]
            if target == self.runtime:
                return httpx.Response(200, json={"id": target, "status": {"type": "DIRECTORY"}})
            if target.endswith("/stamp.json"):
                raw = self.content(target)
                if raw is not None:
                    return httpx.Response(
                        200,
                        json={"id": target, "status": {"type": "FILE", "sizeInBytes": len(raw)}},
                    )
            raise UCloudAPIError("synthetic missing", status_code=404)
        assert (method, path) == ("POST", "/api/files/download")
        target = json["items"][0]["id"]
        self.reads.append(target)
        raw = self.content(target)
        if raw is None:
            raise UCloudAPIError("synthetic missing", status_code=404)
        if isinstance(raw, BaseException):
            raise raw
        endpoint = "https://cloud.invalid/preparation-read/" + quote(target, safe="")
        self.pending = raw
        return httpx.Response(200, json={"responses": [{"endpoint": endpoint}]})

    @contextmanager
    def stream(self, method, path, **kwargs):
        reply = self.request(method, path, **kwargs)

        class Reply:
            status_code, is_success, headers = reply.status_code, reply.is_success, reply.headers

            def iter_raw(self):
                yield reply.content

        yield Reply()

    def transport(self, **kwargs):
        def response(request):
            target = unquote(request.url.path.split("/preparation-read/", 1)[1])
            raw = self.pending
            if self.after_read:
                self.after_read(target)
            return httpx.Response(200, stream=httpx.ByteStream(raw))

        return REAL_HTTPX_CLIENT(transport=httpx.MockTransport(response), **kwargs)


@pytest.fixture
def observation(client, credentials, monkeypatch):
    fixture = preparation_fixtures.prepared.__wrapped__(client, credentials, monkeypatch)
    return ObservationHarness(fixture, monkeypatch)


def assert_one_owner(fixture, identifier):
    assert len(fixture.cloud.submissions) == 1
    assert fixture.store.all("SELECT id FROM runtime_preparations") == [{"id": identifier}]
    assert subject.get(fixture.store, identifier)["journal"]["job_id"] == "remote-preparation"


def test_strict_state_explicitly_requests_identity_proof_fields(observation):
    specification = {
        "name": "Synthetic preparation",
        "application": {"name": "terminal", "version": "pinned"},
        "product": {"id": "synthetic-MIG"},
        "parameters": {"batchScript": {"type": "file", "path": "/drive/run/supervisor.sh"}},
        "resources": [{"type": "file", "path": observation.runtime, "readOnly": False}],
    }
    observation.original_retrieve = lambda identifier: {
        "id": identifier,
        "specification": specification,
        "status": {"state": "IN_QUEUE"},
    }
    assert reader.state(observation.cloud, "remote-preparation", specification) == "IN_QUEUE"
    assert observation.controls[-1][2] == {
        "id": "remote-preparation",
        "includeUpdates": "false",
        "includeApplication": "true",
        "includeParameters": "true",
    }


def test_transient_status_read_reconnects_same_preparation(observation):
    f = observation.fixture
    initial = enqueue(f)
    observation.state_replies = [
        UCloudAPIError("synthetic gateway", status_code=502),
        "SUCCESS",
    ]
    final, _ = execute(f)
    assert final["state"] == "completed", final["message"]
    assert final["journal"]["prepared"] is True
    assert_one_owner(f, initial["id"])


def test_terminal_success_waits_for_supervisor_publication(observation):
    f = observation.fixture
    initial = enqueue(f)
    path = api.run_folder(observation.settings, initial["id"]) + "/supervisor.json"
    observation.files[path] = [None, {"phase": "finished", "generation": initial["id"]}]
    final, _ = execute(f)
    assert final["state"] == "completed", final["message"]
    assert observation.reads.count(path) == 2
    assert_one_owner(f, initial["id"])


def test_stop_during_positive_verification_cannot_publish_prepared(observation):
    f = observation.fixture
    initial = enqueue(f)

    def stop_after_read(path):
        if path.endswith("/doctor.json"):
            value = subject.get(f.store, initial["id"])
            subject.checkpoint(
                f.store,
                initial["id"],
                value["epoch"],
                state="queued",
                journal={"stop_intent": True},
            )
            observation.after_read = None

    observation.after_read = stop_after_read
    final, _ = execute(f)
    assert final["state"] == "cancelled", final["message"]
    assert final["journal"].get("prepared") is not True
    assert_one_owner(f, initial["id"])


@pytest.mark.parametrize("changed", ["id", "application", "parameters", "resources"])
def test_state_refuses_changed_allocation_proof(observation, changed):
    expected = {
        "name": "Synthetic preparation",
        "application": {"name": "terminal", "version": "pinned"},
        "product": {"id": "synthetic-MIG"},
        "parameters": {},
        "resources": [{"type": "file", "path": observation.runtime, "readOnly": False}],
    }
    actual = copy.deepcopy(expected)
    if changed != "id":
        actual[changed] = {} if changed != "resources" else []
        if changed == "parameters":
            actual[changed] = {"unexpected": True}
    observation.original_retrieve = lambda _: {
        "id": "different" if changed == "id" else "remote-preparation",
        "specification": actual,
        "status": {"state": "SUCCESS"},
    }
    with pytest.raises(reader.Refused):
        reader.state(observation.cloud, "remote-preparation", expected)
    assert not observation.cloud.submissions


def test_raw_control_read_stops_at_its_byte_budget(monkeypatch):
    chunks = []

    class Reply:
        status_code, headers = 200, {}

        def iter_raw(self):
            chunks.append(1)
            yield b"x" * 11
            pytest.fail("The oversized reply must not be read further.")

    @contextmanager
    def stream(*args, **kwargs):
        yield Reply()

    monkeypatch.setattr(api, "RUNTIME_METADATA_BYTES", 10)
    with pytest.raises(reader.Refused):
        reader.control(SimpleNamespace(_client=SimpleNamespace(stream=stream)), "GET", "/read")
    assert chunks == [1]


def test_raw_control_read_stops_after_its_elapsed_budget(monkeypatch):
    ticks = iter((0, 0, 0, 11))

    class Reply:
        status_code, headers = 200, {}

        def iter_raw(self):
            yield b"{}"
            pytest.fail("An expired reply must not be read further.")

    @contextmanager
    def stream(*args, **kwargs):
        assert kwargs["timeout"] <= reader.READ_SECONDS
        yield Reply()

    monkeypatch.setattr(reader, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    with pytest.raises(reader.Retryable):
        reader.control(SimpleNamespace(_client=SimpleNamespace(stream=stream)), "GET", "/read")
