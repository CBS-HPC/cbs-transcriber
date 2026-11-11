"""Configured Runtime model preparation using tiny public fixtures and no real services."""

import copy
import hashlib
import json
import threading
from contextlib import contextmanager

import pytest
from admission_support import checked_connection
from test_runtime_preparation import execute, prepared
from test_runtime_readiness_guards import Observation

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import providers
from cbs_transcriber import runtime_preparation as subject

PUNKT = ["abbrev_types.txt", "collocations.tab", "ortho_context.tab", "sent_starters.txt"]


@pytest.fixture
def configured(client, credentials, monkeypatch):
    f = prepared.__wrapped__(client, credentials, monkeypatch)
    chosen = providers.Settings.model_validate(
        providers.profile(f.store, f.profile["id"])["settings"]
    )
    runtime = chosen.model_copy(update={"model": "whisperx/large-v3", "enabled": True})
    profiles = [
        providers.profile(f.store, row["id"]) for row in f.store.all("SELECT id FROM providers")
    ]
    existing = next((p for p in profiles if p["settings"]["model"] == runtime.model), None)
    f.runtime_profile = providers.save_profile(
        f.store,
        credentials,
        "Synthetic Runtime",
        runtime,
        identifier=existing["id"] if existing else None,
    )
    checked_connection(f.store, credentials, f.profile["id"])
    body = b"synthetic pinned public speaker file"
    monkeypatch.setattr(
        api, "SPEAKER_FILES", {"config.yaml": (len(body), hashlib.sha256(body).hexdigest())}
    )
    folder = api.snapshot_folder("speakers", api._local_hub_cache())
    folder.mkdir(parents=True, exist_ok=True)
    f.speaker = folder / "config.yaml"
    f.speaker.write_bytes(body)
    f.plan = subject.huggingface_plan(f.store, credentials)
    assert f.plan["status"] == "ready", f.plan
    return f


def request(f):
    return subject.request_huggingface(f.store, f.credentials, f.plan, lambda: None)


def evidence(plan):
    return {
        "snapshots": {
            name: [{"name": "model.bin", "kind": "FILE", "bytes": 1}]
            for name in plan["models"]
            if name != "speakers"
        },
        "speakers": [
            {"name": name, "kind": "FILE", "bytes": size}
            for name, (size, _) in plan["speaker_files"].items()
        ],
        "speaker_marker": True,
        "punkt": {
            language: [{"name": name, "kind": "FILE", "bytes": 1} for name in PUNKT]
            for language in ("danish", "english")
        },
        "conversions": {name: "a" * 64 for name in set(plan["models"]) & api.DANISH_CHOICES},
    }


def finish(f, monkeypatch, *, bad_cache=None):
    accepted = request(f)
    observed = evidence(f.plan["model_plan"])
    f.observed, f.scripts, f.seeded = observed, [], []
    monkeypatch.setattr(subject, "_model_evidence", lambda *_: copy.deepcopy(f.observed))
    read_json = api.read_json

    def read(client, server, path, *args):
        if path.endswith("/cache.json"):
            cache = {
                "models": dict.fromkeys(f.plan["model_plan"]["models"], True),
                "punkt_tab": True,
            }
            if bad_cache is not None:
                cache["models"]["whisper-large-v3"] = bad_cache
            return cache
        return read_json(client, server, path, *args)

    monkeypatch.setattr(api, "read_json", read)
    upload = api.native.upload

    def upload_public(client, server, path, *args, **kwargs):
        if path.name == "supervisor.sh":
            f.scripts.append(path.read_text(encoding="utf-8"))
        return upload(client, server, path, *args, **kwargs)

    monkeypatch.setattr(api.native, "upload", upload_public)
    monkeypatch.setattr(
        api,
        "seed_speakers",
        lambda _client, _server, _settings, files, guard, _marker: (
            guard(),
            f.seeded.extend(files),
        ),
    )
    submit = f.cloud.submit_job

    def submitted(spec):
        reply = submit(spec)
        f.cloud.ready = True
        return reply

    monkeypatch.setattr(f.cloud, "submit_job", submitted)
    get = f.credentials.get

    def no_hf(scope):
        assert scope != api.HF_SCOPE, "An accepted public-file child borrowed HF credentials"
        return get(scope)

    monkeypatch.setattr(f.credentials, "get", no_hf)
    monkeypatch.setattr(
        api, "prepare_local_speakers", lambda *_: pytest.fail("Accepted child fetched gated files")
    )
    result, _ = execute(f)
    assert result["id"] == accepted["operation"]["runtime_preparation_id"]
    return result


def test_offline_plan_freezes_only_enabled_supported_runtime_profiles(configured, monkeypatch):
    f = configured
    monkeypatch.setattr(
        api.native, "client_for", lambda *_: pytest.fail("Offline plan contacted UCloud")
    )
    monkeypatch.setattr(
        api, "local_speaker_files", lambda: pytest.fail("Offline plan scanned weights")
    )
    plan = subject.huggingface_plan(f.store, f.credentials)
    assert plan["model_plan"]["models"] == ["coarse", "da", "en", "speakers", "whisper-large-v3"]
    assert [p["model"] for p in plan["model_plan"]["profiles"]] == ["whisperx/large-v3"]
    assert plan["model_plan"]["model_revisions"]["whisper-large-v3"] == list(
        api.MODELS["whisper-large-v3"]
    )
    assert "token" not in json.dumps(plan)


def test_missing_local_seed_refuses_before_durable_acceptance(configured):
    f = configured
    f.speaker.unlink()
    with pytest.raises(api.RuntimeNotReady):
        request(f)
    assert not f.store.all("SELECT id FROM runtime_preparations")
    assert f.cloud.submissions == f.cloud.uploads == []


def test_successful_owner_binds_all_caches_and_repeat_save_uses_fresh_evidence(
    configured, monkeypatch
):
    f = configured
    result = finish(f, monkeypatch)
    assert result["state"] == "completed", result["message"]
    receipt = result["journal"]["model_plan_receipt"]
    assert receipt["model_plan_sha256"] == subject.runtime_store._digest(f.plan["model_plan"])
    assert receipt["evidence"] == f.observed
    assert len(f.seeded) == 1 and len(f.cloud.submissions) == 1
    assert 'MODELS="coarse,da,en,whisper-large-v3"' in f.scripts[0]
    assert "hf_" not in f.scripts[0]
    repeated = request(f)
    assert repeated["prepared"] and repeated["operation"] is None
    assert len(f.store.all("SELECT id FROM runtime_preparations")) == 1
    assert len(f.cloud.submissions) == 1


@pytest.mark.parametrize("damage", ["missing", "changed", "unknown"])
def test_repeat_save_never_blindly_trusts_an_old_receipt(configured, monkeypatch, damage):
    f = configured
    result = finish(f, monkeypatch)
    assert result["state"] == "completed"
    if damage == "missing":
        f.observed = None
    elif damage == "changed":
        f.observed["snapshots"]["coarse"][0]["bytes"] = 2
    else:
        monkeypatch.setattr(
            subject,
            "_model_evidence",
            lambda *_: (_ for _ in ()).throw(
                api.RuntimePreflightBlocked("runtime-readiness-unknown")
            ),
        )
    if damage == "unknown":
        with pytest.raises(api.RuntimePreflightBlocked):
            request(f)
        assert len(f.store.all("SELECT id FROM runtime_preparations")) == 1
    else:
        repeated = request(f)
        assert not repeated["prepared"] and repeated["operation"]["state"] == "queued"
        assert len(f.store.all("SELECT id FROM runtime_preparations")) == 2
    assert len(f.cloud.submissions) == 1


@pytest.mark.parametrize("bad_cache", [False, "true", 1])
def test_base_success_without_every_requested_cache_cannot_publish_receipt(
    configured, monkeypatch, bad_cache
):
    result = finish(configured, monkeypatch, bad_cache=bad_cache)
    assert result["state"] == "failed"
    assert "model_plan_receipt" not in result["journal"]
    assert result["journal"]["job_id"] == "remote-preparation"
    assert len(configured.cloud.submissions) == 1


def test_accepted_child_refuses_drift_without_hf_fallback(configured, monkeypatch):
    f = configured
    accepted = request(f)
    f.speaker.write_bytes(b"changed")
    monkeypatch.setattr(
        api,
        "prepare_local_speakers",
        lambda *_: pytest.fail("Accepted child fetched a replacement"),
    )
    result, _ = execute(f)
    assert result["id"] == accepted["operation"]["runtime_preparation_id"]
    assert result["state"] == "failed" and not f.cloud.uploads and not f.cloud.submissions


@pytest.mark.parametrize(
    "body,expected",
    [(b"small public receipt", b"small public receipt"), (b"x" * 65537, None)],
    ids=["small", "oversized"],
)
def test_small_model_receipts_use_bounded_separate_transfer(monkeypatch, body, expected):
    cloud = Observation(body)
    monkeypatch.setattr(api.httpx, "Client", cloud.transport)
    assert (
        subject._model_bytes(
            cloud, cloud.settings, cloud.runtime + "/stamp.json", api.time.monotonic() + 8
        )
        == expected
    )
    assert cloud.transfers and cloud.transfers[0]["follow_redirects"] is False


@pytest.fixture
def cache_observation(configured, monkeypatch):
    f = configured
    objects, pages = {}, {}

    def folder(path, names):
        objects[path] = {"id": path, "status": {"type": "DIRECTORY"}}
        pages[path] = {
            "items": [
                {"id": path + "/" + name, "status": {"type": "FILE", "sizeInBytes": 1}}
                for name in names
            ],
            "next": None,
        }

    for key in f.plan["model_plan"]["models"]:
        path = api.model_folder(
            providers.Settings.model_validate(f.plan["chosen"]["settings"]), key
        )
        if key != "speakers":
            folder(path, ["model.bin", "config.json"])
        else:
            for name, (size, _) in api.SPEAKER_FILES.items():
                objects[path + "/" + name] = {
                    "id": path + "/" + name,
                    "status": {"type": "FILE", "sizeInBytes": size},
                }
    settings = providers.Settings.model_validate(f.plan["chosen"]["settings"])
    root = api.runtime_folder(settings)
    for language in ("danish", "english"):
        folder(root + "/nltk/tokenizers/punkt_tab/" + language, PUNKT)
    monkeypatch.setattr(api, "runtime_metadata", lambda _client, path, **_: objects.get(path))
    monkeypatch.setattr(
        api,
        "_runtime_request",
        lambda _client, method, path, deadline, *, params: copy.deepcopy(pages[params["path"]]),
    )
    repo, revision = api.MODELS["speakers"]
    monkeypatch.setattr(subject, "_model_bytes", lambda *_: f"{repo}@{revision}\n".encode())
    return f, settings, objects, pages


def test_cache_evidence_covers_every_pinned_snapshot_and_tokenizer(cache_observation):
    f, settings, _, _ = cache_observation
    observed = subject._model_evidence(object(), settings, f.plan["model_plan"])
    assert set(observed["snapshots"]) == set(f.plan["model_plan"]["models"]) - {"speakers"}
    assert set(observed["punkt"]) == {"danish", "english"}
    assert observed["speakers"][0]["bytes"] == f.speaker.stat().st_size


@pytest.mark.parametrize(
    "missing", ["coarse", "da", "en", "whisper-large-v3", "speakers", "danish", "english"]
)
def test_partial_current_cache_never_proves_prepared(cache_observation, missing):
    f, settings, objects, _ = cache_observation
    if missing in {"danish", "english"}:
        path = api.runtime_folder(settings) + "/nltk/tokenizers/punkt_tab/" + missing
    else:
        path = api.model_folder(settings, missing)
        if missing == "speakers":
            path += "/config.yaml"
    objects.pop(path)
    assert subject._model_evidence(object(), settings, f.plan["model_plan"]) is None


@pytest.mark.parametrize(
    "bad", ["unknown-kind", "missing-size", "cross-parent", "next-page", "too-many"]
)
def test_unknown_or_unbounded_inventory_never_grants_preparation(cache_observation, bad):
    f, settings, _, pages = cache_observation
    page = pages[api.model_folder(settings, "coarse")]
    if bad == "unknown-kind":
        page["items"][0]["status"]["type"] = "UNKNOWN"
    elif bad == "missing-size":
        page["items"][0]["status"].pop("sizeInBytes")
    elif bad == "cross-parent":
        page["items"][0]["id"] = "/another/model.bin"
    elif bad == "next-page":
        page["next"] = "more"
    else:
        page["items"] *= 33
    with pytest.raises(api.RuntimePreflightBlocked):
        subject._model_evidence(object(), settings, f.plan["model_plan"])


def test_stop_while_waiting_for_sqlite_writer_refuses_before_acceptance(configured, monkeypatch):
    f = configured
    proof, writer_locked, waiting, stopped = (threading.Event() for _ in range(4))
    failures = []
    original_connection = f.store.connection

    def not_prepared(*args):
        proof.set()
        assert writer_locked.wait(5)
        return False

    monkeypatch.setattr(subject, "_model_prepared", not_prepared)

    @contextmanager
    def traced_connection():
        with original_connection() as db:

            class Connection:
                def __getattr__(self, name):
                    return getattr(db, name)

                def execute(self, sql, *args):
                    if (
                        sql == "BEGIN IMMEDIATE"
                        and threading.current_thread() is child
                        and proof.is_set()
                    ):
                        waiting.set()
                    return db.execute(sql, *args)

            yield Connection()

    monkeypatch.setattr(f.store, "connection", traced_connection)

    def guard():
        if stopped.is_set():
            raise RuntimeError("Synthetic Stop before acceptance")

    def run():
        try:
            subject.request_huggingface(f.store, f.credentials, f.plan, guard)
        except BaseException as exc:
            failures.append(exc)

    child = threading.Thread(target=run)
    child.start()
    try:
        assert proof.wait(5)
        with original_connection() as db:
            db.execute("BEGIN IMMEDIATE")
            writer_locked.set()
            assert waiting.wait(5)
            stopped.set()
        child.join(10)
        assert not child.is_alive()
    finally:
        writer_locked.set()
        stopped.set()
        child.join(10)
    assert len(failures) == 1 and str(failures[0]) == "Synthetic Stop before acceptance"
    assert f.store.all("SELECT id FROM runtime_preparations") == []
    assert f.cloud.submissions == f.cloud.uploads == []
