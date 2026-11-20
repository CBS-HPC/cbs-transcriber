"""V214: exact recipe observations and preparation-only fences; no real cloud calls."""

import copy
import json
import subprocess
import threading
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import httpx
import pytest
from test_runtime_preparation_locking import bash_executable, functions, generated
from ucloud_workflow.client import UCloudAPIError, UCloudClient
from ucloud_workflow.settings import Settings as CloudSettings

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import providers, runtime_batches, runtime_preparation, runtime_store
from cbs_transcriber.enhancement import EnhancementOptions
from cbs_transcriber.runtime_compute import MIG_PRODUCT
from cbs_transcriber.store import Store

REAL_HTTPX_CLIENT = httpx.Client


def settings():
    return providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        ucloud_transcriber={"project_id": "synthetic", "storage_path": "/drive/synthetic"},
    )


def stamp_bytes():
    return ('{"requirements_sha256":"' + api.requirements_hash() + '"}').encode()


class Observation:
    def __init__(self, body, chosen=None):
        self.settings = chosen or settings()
        self.runtime = api.runtime_folder(self.settings)
        self.body = body
        self.metadata = {
            self.runtime: {"id": self.runtime, "status": {"type": "DIRECTORY"}},
            self.runtime + "/stamp.json": {
                "id": self.runtime + "/stamp.json",
                "status": {"type": "FILE", "sizeInBytes": len(body)},
            },
        }
        self.calls, self.transfers = [], []
        self._client = SimpleNamespace(stream=self.control_stream)

    def browse_files(self, path, **kwargs):
        return {
            "items": [
                value for key, value in self.metadata.items() if key.rsplit("/", 1)[0] == path
            ]
        }

    def request(self, method, path, *, params=None, json=None, timeout=None):
        self.calls.append((method, path, params, json, timeout))
        if method == "GET" and path == "/api/files/retrieve":
            if params["id"] not in self.metadata:
                raise UCloudAPIError("synthetic absent", status_code=404)
            return httpx.Response(200, json=self.metadata[params["id"]])
        assert (method, path) == ("POST", "/api/files/download")
        return httpx.Response(
            200, json={"responses": [{"endpoint": "https://cloud.invalid/stamp"}]}
        )

    def control_stream(self, method, path, *, headers, follow_redirects, **kwargs):
        assert headers == {"Accept-Encoding": "identity"} and follow_redirects is False
        reply = self.request(method, path, **kwargs)

        class Reply:
            status_code, is_success, headers = reply.status_code, reply.is_success, reply.headers

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def iter_raw(self):
                yield reply.content

        return Reply()

    def transport(self, **kwargs):
        owner = self
        owner.transfers.append(kwargs)

        class Stream:
            status_code, is_success = 200, True
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def iter_raw(self):
                yield owner.body

        class Transport:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def stream(self, method, endpoint, **kwargs):
                assert (method, endpoint) == ("GET", "https://cloud.invalid/stamp")
                assert not kwargs.get("headers", {}).get("Authorization")
                return Stream()

        return Transport()


def bodies():
    valid = stamp_bytes()
    return [
        (valid, True),
        (valid + b"\n", False),
        (valid.replace(b'":"', b'": "'), False),
        (valid[:-1] + b',"extra":true}', False),
        (b'{"note":' + valid + b"}", False),
        (b'{"requirements_sha256":"' + b"0" * 64 + b'"}', False),
        (valid[:-1], False),
        (valid + b" " * 65536, False),
    ]


@pytest.mark.parametrize(
    "body,ready",
    bodies(),
    ids=[
        "canonical",
        "newline",
        "space",
        "extra-field",
        "embedded",
        "wrong-digest",
        "truncated",
        "oversized",
    ],
)
def test_host_and_shell_require_identical_canonical_bytes(tmp_path, monkeypatch, body, ready):
    cloud = Observation(body)
    monkeypatch.setattr(api.httpx, "Client", cloud.transport)
    assert api.stamped(cloud, cloud.settings) is ready
    executable = bash_executable()
    if executable is None:
        pytest.skip("Bash not installed; host assertion ran, shell needs separate acceptance.")
    script, runtime, _ = generated(tmp_path)
    (runtime / "stamp.json").write_bytes(body)
    path = tmp_path / "stamp.sh"
    path.write_text(functions(script) + "\nprepared\nexit $?\n", encoding="utf-8", newline="\n")
    result = subprocess.run([executable, str(path)], capture_output=True, timeout=20)
    assert (result.returncode == 0) is ready


@pytest.mark.parametrize(
    "target,kind", [("runtime", "FILE"), ("stamp", "DIRECTORY"), ("stamp", "SOFT_LINK")]
)
def test_reported_wrong_kind_is_definitely_unprepared(monkeypatch, target, kind):
    cloud = Observation(stamp_bytes())
    name = cloud.runtime if target == "runtime" else cloud.runtime + "/stamp.json"
    cloud.metadata[name]["status"]["type"] = kind
    monkeypatch.setattr(api.httpx, "Client", cloud.transport)
    assert api.stamped(cloud, cloud.settings) is False
    assert not cloud.transfers


@pytest.mark.parametrize("flag", ["isSymlink", "isSymbolicLink", "symlink", "symbolicLink"])
def test_explicit_link_flag_refuses_without_claiming_physical_metadata_proof(monkeypatch, flag):
    cloud = Observation(stamp_bytes())
    cloud.metadata[cloud.runtime + "/stamp.json"]["status"][flag] = True
    monkeypatch.setattr(api.httpx, "Client", cloud.transport)
    assert api.stamped(cloud, cloud.settings) is False
    assert not cloud.transfers


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_unknown_metadata_is_not_missing_or_preparation_permission(monkeypatch, status):
    cloud = Observation(stamp_bytes())

    def unavailable(*args, **kwargs):
        raise UCloudAPIError("synthetic-private-body-never-diagnostic", status_code=status)

    cloud.request = unavailable
    monkeypatch.setattr(api.httpx, "Client", cloud.transport)
    with pytest.raises(api.Transient) as caught:
        api.stamped(cloud, cloud.settings)
    assert "synthetic-private-body" not in str(caught.value)
    assert not cloud.transfers


@pytest.mark.parametrize("ready", [False, True])
def test_known_legacy_reader_blocks_preparation_independently_of_stamp(
    tmp_path, monkeypatch, ready
):
    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic legacy reader")
    row = store.create_recording(project["id"], "synthetic.wav")
    chosen = settings()
    job = store.add_job(
        row["id"],
        "enhance",
        {
            "ucloud_api": {"settings": chosen.model_dump(), "runtime_id": api.runtime_id()},
            "ucloud_run": {"job_id": "legacy", "remote_state": "RUNNING"},
        },
    )
    monkeypatch.setattr(api, "stamped", lambda *_: ready)
    before = copy.deepcopy(store.job(job["id"]))
    with pytest.raises(ValueError, match="older|legacy"):
        runtime_batches.require_preparation_idle(store, chosen, object())
    assert store.job(job["id"]) == before


@pytest.mark.parametrize(
    "damage", ["unknown-kind", "conflicting-kind", "missing-id", "wrong-id", "malformed-flag"]
)
def test_uncertain_metadata_cannot_become_unprepared_permission(monkeypatch, damage):
    cloud = Observation(stamp_bytes())
    item = cloud.metadata[cloud.runtime + "/stamp.json"]
    if damage == "unknown-kind":
        item["status"]["type"] = "UNKNOWN"
    elif damage == "conflicting-kind":
        item["type"] = "DIRECTORY"
    elif damage == "missing-id":
        item.pop("id")
    elif damage == "wrong-id":
        item["id"] = "/another/stamp.json"
    else:
        item["status"]["isSymlink"] = "false"
    monkeypatch.setattr(api.httpx, "Client", cloud.transport)
    with pytest.raises(api.Transient):
        api.stamped(cloud, cloud.settings)
    assert not cloud.transfers


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads, self.closed = 0, False

    def __iter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    def close(self):
        self.closed = True


def sdk_transport(monkeypatch, special):
    """Use the released SDK's actual default HTTPX construction, not buffered request."""
    cloud = Observation(stamp_bytes())
    seen = []

    def handler(request):
        seen.append(request)
        assert 0 < request.extensions["timeout"]["read"] <= api.RUNTIME_REQUEST_SECONDS
        if request.url.path == "/stamp":
            assert "Authorization" not in request.headers and "Project" not in request.headers
            body = stamp_bytes()
        else:
            assert request.headers["Authorization"] == "Bearer synthetic-control-secret"
            assert request.headers["Project"] == "synthetic"
            if request.url.path == "/api/files/retrieve":
                body = json.dumps(cloud.metadata[request.url.params["id"]]).encode()
            else:
                assert request.url.path == "/api/files/download"
                body = b'{"responses":[{"endpoint":"https://cloud.invalid/stamp"}]}'
        stream = special(request, body) if special else Chunks([body])
        return httpx.Response(200, stream=stream)

    monkeypatch.setattr(
        api.httpx,
        "Client",
        lambda **kwargs: REAL_HTTPX_CLIENT(**kwargs, transport=httpx.MockTransport(handler)),
    )
    client = UCloudClient(
        CloudSettings(
            server="https://cloud.invalid", token="synthetic-control-secret", project="synthetic"
        )
    )
    return client, cloud, seen


def test_released_sdk_streams_control_and_keeps_transfer_credentials_separate(monkeypatch):
    client, cloud, seen = sdk_transport(monkeypatch, None)
    with client:
        assert api.stamped(client, cloud.settings) is True
    assert [request.method for request in seen] == ["GET", "GET", "POST", "GET"]


def test_control_budget_stops_raw_stream_before_parsing_or_reading_another_chunk(monkeypatch):
    stream = Chunks([b"x" * (api.RUNTIME_METADATA_BYTES + 1), b"must-not-be-read"])
    client, cloud, seen = sdk_transport(monkeypatch, lambda *_: stream)
    with client, pytest.raises(api.Transient):
        api.stamped(client, cloud.settings)
    assert stream.reads == 1 and stream.closed
    assert len(seen) == 1


def missing_hierarchy_client(monkeypatch, browse):
    seen = []

    def handler(request):
        assert request.headers["Authorization"] == "Bearer synthetic-control-secret"
        assert request.headers["Project"] == "synthetic"
        seen.append(request)
        if request.url.path == "/api/files/retrieve":
            return httpx.Response(400)
        assert request.method == "GET" and request.url.path == "/api/files/browse"
        status, payload = browse(request)
        return httpx.Response(status, stream=Chunks([json.dumps(payload).encode()]))

    monkeypatch.setattr(
        api.httpx,
        "Client",
        lambda **kwargs: REAL_HTTPX_CLIENT(**kwargs, transport=httpx.MockTransport(handler)),
    )
    return (
        UCloudClient(
            CloudSettings(
                server="https://cloud.invalid",
                token="synthetic-control-secret",
                project="synthetic",
            )
        ),
        seen,
    )


@pytest.mark.parametrize("suffix", ["", "/stamp.json", "/.prepare-lock"])
def test_missing_hierarchy_is_proved_from_nearest_existing_ancestor(monkeypatch, suffix):
    chosen = settings()
    root = api.runtime_folder(chosen)
    existing = chosen.ucloud_transcriber.storage_path

    def browse(request):
        return (200, {"items": []}) if request.url.params["path"] == existing else (400, {})

    client, seen = missing_hierarchy_client(monkeypatch, browse)
    with client:
        assert api.runtime_metadata(client, root + suffix) is None
        assert api.stamped(client, chosen) is False
    parents = [request.url.params["path"] for request in seen if "path" in request.url.params]
    assert existing in parents
    assert all(request.method == "GET" for request in seen)
    assert len(seen) <= 9


@pytest.mark.parametrize(
    "failure",
    ["present-branch", "authorization", "cross-parent", "alias-id", "cursor-cycle", "page-cap"],
)
def test_ancestor_probe_cannot_turn_ambiguous_missing_response_into_permission(
    monkeypatch, failure
):
    target = api.runtime_folder(settings()) + "/.prepare-lock"
    calls = []

    def browse(request):
        parent = request.url.params["path"]
        calls.append(parent)
        if failure == "authorization":
            return 403, {}
        if failure == "present-branch":
            if len(calls) == 1:
                return 400, {}
            return 200, {"items": [{"id": target.rsplit("/", 1)[0]}]}
        if failure in {"cross-parent", "alias-id"}:
            identifier = "/another/child" if failure == "cross-parent" else parent + "/./child"
            return 200, {"items": [{"id": identifier}]}
        return 200, {
            "items": [],
            "next": "same" if failure == "cursor-cycle" else str(len(calls)),
        }

    client, seen = missing_hierarchy_client(monkeypatch, browse)
    with client, pytest.raises(api.RuntimePreflightBlocked) as caught:
        api.runtime_metadata(client, target)
    assert caught.value.code == "runtime-readiness-unknown"
    assert len(calls) <= 16
    if failure == "page-cap":
        assert len(calls) == 16
    assert all(request.method == "GET" for request in seen)


def test_parent_listing_waits_for_all_pages_before_claiming_absence(monkeypatch):
    target = api.runtime_folder(settings()) + "/.prepare-lock"

    def browse(request):
        if "next" not in request.url.params:
            return 200, {"items": [], "next": "later"}
        return 200, {"items": [{"id": target}]}

    client, seen = missing_hierarchy_client(monkeypatch, browse)
    with client, pytest.raises(api.RuntimePreflightBlocked):
        api.runtime_metadata(client, target)
    assert len(seen) == 3


@pytest.mark.parametrize("phase", ["metadata", "ticket", "stamp", "stamp-eof"])
def test_raw_drips_and_final_read_cannot_hide_the_overall_deadline(monkeypatch, phase):
    clock, observed = [0], []

    class Drips(Chunks):
        def __iter__(self):
            for byte in b" " * 20:
                clock[0] += 2
                self.reads += 1
                yield bytes([byte])

    class LateEOF(Chunks):
        def __iter__(self):
            yield stamp_bytes()
            clock[0] = api.RUNTIME_CHECK_SECONDS + 1

    def special(request, body):
        match = (
            (phase == "metadata" and request.url.path == "/api/files/retrieve")
            or (phase == "ticket" and request.url.path == "/api/files/download")
            or (phase in {"stamp", "stamp-eof"} and request.url.path == "/stamp")
        )
        stream = (LateEOF([]) if phase == "stamp-eof" else Drips([])) if match else Chunks([body])
        observed.append(stream)
        return stream

    monkeypatch.setattr(api.time, "monotonic", lambda: clock[0])
    client, cloud, _ = sdk_transport(monkeypatch, special)
    with client, pytest.raises(api.Transient):
        api.stamped(client, cloud.settings)
    assert all(stream.closed for stream in observed)
    if phase != "stamp-eof":
        assert observed[-1].reads == 4


def singleton_owner(client, credentials, tmp_path, prepare):
    store = client.app.state.store
    project = store.create_project("Synthetic preallocation")
    recording = store.create_recording(project["id"], "synthetic.wav")
    chosen = settings()
    profile = providers.save_profile(
        store, credentials, "Synthetic", chosen, api_key="synthetic", remember=False
    )
    job = store.add_job(
        recording["id"],
        "enhance",
        {
            "ucloud_api": {
                "provider_id": profile["id"],
                "settings": chosen.model_dump(),
                "runtime_id": api.runtime_id(),
                "runtime_product": MIG_PRODUCT,
                "runtime_compute_policy": "mig-v1",
                "prepare_runtime": prepare,
            }
        },
    )
    worker = SimpleNamespace(
        store=store,
        credentials=credentials,
        stopping=threading.Event(),
        cancel_event=threading.Event(),
    )
    return api.Run(
        worker,
        job,
        store.recording(recording["id"]),
        tmp_path / "unused-staging",
        EnhancementOptions(execution="ucloud"),
        lambda: None,
    )


@pytest.mark.parametrize(
    "path,ready",
    [
        ("singleton", False),
        ("singleton", True),
        ("batch", False),
        ("batch", True),
        ("standalone", False),
    ],
)
@pytest.mark.parametrize(
    "obstacle,code",
    [
        ("lock", "runtime-preparation-lock"),
        ("legacy", "runtime-legacy-reader"),
        ("unknown", "runtime-readiness-unknown"),
    ],
)
def test_actual_prepare_paths_refuse_before_any_setup_side_effect(
    client, credentials, tmp_path, monkeypatch, path, ready, obstacle, code
):
    from test_runtime_batches import environment
    from test_runtime_preparation import enqueue, prepared

    side_effects = []

    def forbidden(*args, **kwargs):
        side_effects.append("setup-or-job-submission")
        raise AssertionError("Retained lock reached a setup side effect")

    context = (
        contextmanager(environment.__wrapped__)(tmp_path, monkeypatch, credentials)
        if path == "batch"
        else nullcontext()
    )
    with context as env:
        if path == "batch":
            env.params["ucloud_api"]["prepare_runtime"] = True
            env.enqueue()
            owner = runtime_batches.Coordinator(env.worker, runtime_store.claim(env.store))
        elif path == "standalone":
            fixture = prepared.__wrapped__(client, credentials, monkeypatch)
            enqueue(fixture)
            value = runtime_preparation.claim(fixture.store)
            worker = SimpleNamespace(
                store=fixture.store, credentials=credentials, stopping=threading.Event()
            )
            owner = runtime_preparation.Run(worker, value)
            owner.settings = providers.Settings.model_validate(value["frozen"]["settings"])
        else:
            owner = singleton_owner(client, credentials, tmp_path, True)
        cloud = Observation(stamp_bytes(), owner.settings)
        if obstacle == "lock":
            lock = cloud.runtime + "/.prepare-lock"
            cloud.metadata[lock] = {"id": lock, "status": {"type": "DIRECTORY"}}
        elif obstacle == "legacy":
            project = owner.store.create_project("Known synthetic older reader")
            row = owner.store.create_recording(project["id"], "legacy.wav")
            owner.store.add_job(
                row["id"],
                "enhance",
                {
                    "ucloud_api": {
                        "settings": owner.settings.model_dump(),
                        "runtime_id": api.runtime_id(),
                    },
                    "ucloud_run": {"job_id": "older-runtime", "remote_state": "RUNNING"},
                },
            )
        else:

            def unknown(*args, **kwargs):
                raise UCloudAPIError("synthetic-private-body", status_code=403)

            cloud.request = unknown
        owner.client = cloud
        cloud.create_folder = cloud.submit_job = forbidden
        owner.prepare_speakers = forbidden
        monkeypatch.setattr(api, "stamped", lambda *_: ready)
        monkeypatch.setattr(
            api, "script_application", lambda *_: {"name": "terminal", "version": "synthetic"}
        )
        monkeypatch.setattr(
            api,
            "build_script_specification",
            lambda *_args, **_kwargs: {"parameters": {}, "resources": []},
        )
        monkeypatch.setattr(api, "ensure_folder", forbidden)
        monkeypatch.setattr(api.native, "upload", forbidden)
        monkeypatch.setattr(runtime_batches, "ensure_use_lock", forbidden)
        with pytest.raises(api.RuntimePreflightBlocked) as caught:
            owner.submit()
        assert caught.value.code == code
        assert side_effects == []
        assert all(call[1] != "/api/jobs" for call in cloud.calls)


@pytest.mark.parametrize(
    "code", ["runtime-preparation-lock", "runtime-legacy-reader", "runtime-readiness-unknown"]
)
def test_standalone_activity_keeps_curated_preflight_failure_without_submission(
    client, credentials, monkeypatch, code
):
    from test_runtime_preparation import enqueue, execute, prepared

    fixture = prepared.__wrapped__(client, credentials, monkeypatch)
    value = enqueue(fixture)

    def blocked(*args):
        raise api.RuntimePreflightBlocked(code)

    monkeypatch.setattr(api, "stamped", blocked)
    result, _ = execute(fixture)
    assert result["id"] == value["id"] and result["state"] == "failed"
    assert result["journal"]["preflight_failure"] == code
    assert result["message"] == str(api.RuntimePreflightBlocked(code))
    assert fixture.cloud.submissions == fixture.cloud.uploads == []
    assert not result["journal"].get("submission_intent")


def test_cached_singleton_and_existing_allocation_recovery_skip_preparation_guards(
    client, credentials, tmp_path, monkeypatch
):
    owner = singleton_owner(client, credentials, tmp_path, False)
    reached = []
    owner.client = Observation(stamp_bytes(), owner.settings)
    monkeypatch.setattr(
        runtime_batches,
        "require_preparation_idle",
        lambda *_: pytest.fail("PREPARE=0 reached preparation guard"),
    )
    monkeypatch.setattr(api, "stamped", lambda *_: True)
    monkeypatch.setattr(api, "script_application", lambda *_: {})
    monkeypatch.setattr(api, "build_script_specification", lambda *_args, **_kwargs: {})

    class PassedPreflight(Exception):
        pass

    def later_setup():
        reached.append("cached-reader")
        raise PassedPreflight

    owner.prepare_speakers = later_setup
    with pytest.raises(PassedPreflight):
        owner.submit()
    owner.run["job_id"] = "existing-owned"
    owner.submit = lambda: pytest.fail("Recovery attempted new submission")
    owner.poll = lambda: reached.append("same-allocation-poll")
    owner.collect = lambda: reached.append("same-allocation-output")
    monkeypatch.setattr(api.native, "require_setup", lambda *_: None)
    monkeypatch.setattr(api.native, "client_for", lambda *_: nullcontext(owner.client))
    owner.execute()
    assert reached == ["cached-reader", "same-allocation-poll", "same-allocation-output"]
