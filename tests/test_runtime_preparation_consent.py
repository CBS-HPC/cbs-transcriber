"""Ask once before acceptance, or use standing consent, for UCloud runtime preparation.

ADR 0098. Every readiness probe is mocked: no UCloud, Hugging Face or paid call is made."""

import copy
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from admission_support import inspect_and_materialize
from test_runtime_batch_api import add_profile, add_recording, assert_queue_unlocked, plan

from cbs_transcriber import enhancement_api, input_admission, providers, routing
from cbs_transcriber.enhancement import EnhancementOptions, ProcessingSettings
from cbs_transcriber.runtime_compute import MIG_PRODUCT

# Captured at import, before the autouse conftest fixture replaces it for each test.
REAL_STATES = enhancement_api.preparation_states
NOT_READY = {"ready": False, "speakers": None}


@pytest.fixture
def runtime(client, credentials, monkeypatch):
    store, queue = client.app.state.store, client.app.state.queue
    project = store.create_project("Synthetic preparation consent")
    provider = add_profile(store, credentials, "whisperx/roest-v3")
    rows = [add_recording(store, project, f"synthetic-{index}.wav") for index in range(3)]
    probes, observed = [], {"state": copy.deepcopy(NOT_READY)}

    def states(credentials, targets, **_):
        assert_queue_unlocked(queue)
        probes.append(copy.deepcopy(targets))
        return {key: copy.deepcopy(observed["state"]) for key in targets}

    def freeze_profile(store, credentials, selected, project_ids, options):
        return options.model_copy(update={"device": "cuda", "allow_downloads": False}), {
            "ucloud_api": {
                "provider_id": selected["id"],
                "settings": copy.deepcopy(selected["settings"]),
                "runtime_id": "synthetic-runtime",
                "runtime_product": dict(MIG_PRODUCT),
                "runtime_compute_policy": "mig-v1",
                "prepare_runtime": options.prepare_runtime,
                "batch_plan": plan(),
            }
        }

    monkeypatch.setattr(enhancement_api, "preparation_states", states)
    monkeypatch.setattr(enhancement_api, "freeze_profile", freeze_profile)
    monkeypatch.setattr(enhancement_api, "whisperx_ready", lambda *a: False)
    with store.library_lock():
        yield SimpleNamespace(
            store=store,
            project=project,
            provider=provider,
            rows=rows,
            probes=probes,
            observed=observed,
        )


def transcribe(client, runtime, **extra):
    return client.post(
        "/api/transcriptions",
        json={
            "recording_ids": [row["id"] for row in runtime.rows],
            "provider_ids": [runtime.provider["id"]],
            **extra,
        },
    )


def standing(client, enabled):
    saved = client.get("/api/enhancement-settings").json()
    response = client.put(
        "/api/enhancement-settings", json={**saved, "prepare_ucloud_models": enabled}
    )
    assert response.status_code == 200, response.text
    assert response.json()["prepare_ucloud_models"] is enabled


def accepted(job):
    return job["params"]["input_intent"]["accepted"]


def test_v0098_unprepared_runtime_asks_once_and_accepts_nothing(client, runtime):
    standing(client, False)
    before = runtime.store.all("SELECT * FROM jobs ORDER BY id")
    response = transcribe(client, runtime)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "prepare_runtime" and detail["recordings"] == 3
    assert "UCloud Røst v3 (CBS Runtime)" in detail["message"]
    assert "3 recordings" in detail["message"]
    assert "UCloud charges" in detail["message"] and "Settings → UCloud" in detail["message"]
    # One probe for the whole selection; no intent, inspector or cache row was created.
    assert len(runtime.probes) == 1 and len(runtime.probes[0]) == 1
    assert runtime.store.all("SELECT * FROM jobs ORDER BY id") == before
    assert not runtime.store.all("SELECT id FROM recordings WHERE original_input IS NOT NULL")


def test_v0098_confirmed_request_freezes_consent_and_continues_without_stopping(
    client, runtime, credentials, monkeypatch
):
    monkeypatch.setattr(
        enhancement_api, "whisperx_ready", lambda *a: pytest.fail("Consent skips this check")
    )
    response = transcribe(client, runtime, prepare_runtime=True)
    assert response.status_code == 202, response.text
    jobs = response.json()
    assert runtime.probes == []  # The confirmed request needs no readiness answer.
    for job in jobs:
        intent = job["params"]["input_intent"]
        assert intent["version"] == input_admission.VERSION == 2
        assert accepted(job)["prepare_consent"] == "request"
        assert accepted(job)["prepare_runtime"] is True
        assert accepted(job)["options"]["prepare_runtime"] is True
        assert accepted(job)["options"]["whisper_model"] == "roest-v3"
    done = inspect_and_materialize(runtime.store, credentials, [job["id"] for job in jobs])
    assert [job["state"] for job in done] == ["queued"] * 3
    assert all("input_intent" not in job["params"] for job in done)
    assert all(job["params"]["ucloud_api"]["prepare_runtime"] is True for job in done)
    # Accepted together, compatible files share one Runtime allocation (ADR 0065).
    assert len({job["params"]["runtime_batch_id"] for job in done}) == 1


def test_v0098_standing_consent_freezes_its_source_without_asking(client, runtime, credentials):
    assert ProcessingSettings().prepare_ucloud_models is True
    response = transcribe(client, runtime)
    assert response.status_code == 202, response.text
    jobs = response.json()
    assert len(runtime.probes) == 1
    assert {accepted(job)["prepare_consent"] for job in jobs} == {"standing"}
    assert all(accepted(job)["options"]["prepare_runtime"] for job in jobs)
    done = inspect_and_materialize(runtime.store, credentials, [job["id"] for job in jobs])
    assert all(job["state"] == "queued" for job in done)
    assert all(job["params"]["ucloud_api"]["prepare_runtime"] is True for job in done)


@pytest.mark.parametrize("enabled", [False, True])
def test_v0098_prepared_runtime_never_asks_or_freezes_consent(
    client, runtime, credentials, monkeypatch, enabled
):
    standing(client, enabled)
    runtime.observed["state"] = {"ready": True, "speakers": None}
    monkeypatch.setattr(enhancement_api, "whisperx_ready", lambda *a: True)
    response = transcribe(client, runtime)
    assert response.status_code == 202, response.text
    jobs = response.json()
    assert {accepted(job)["prepare_consent"] for job in jobs} == {None}
    assert not any(accepted(job)["prepare_runtime"] for job in jobs)
    done = inspect_and_materialize(runtime.store, credentials, [job["id"] for job in jobs])
    assert all(job["params"]["ucloud_api"]["prepare_runtime"] is False for job in done)


def test_v0098_unknown_readiness_accepts_and_keeps_one_confirmation_fallback(
    client, runtime, credentials
):
    standing(client, False)
    runtime.observed["state"] = None  # Offline or timed out: no readiness claim.
    response = transcribe(client, runtime)
    assert response.status_code == 202, response.text
    jobs = response.json()
    assert {accepted(job)["prepare_consent"] for job in jobs} == {None}
    stopped = inspect_and_materialize(runtime.store, credentials, [job["id"] for job in jobs])
    assert {job["params"]["input_intent"]["error"]["code"] for job in stopped} == {
        "needs_confirmation"
    }
    projections = [input_admission.projection(runtime.store, job["id"]) for job in stopped]
    previews = [projection["prepare_preview"] for projection in projections]
    # Every card offers the same one confirmation for the whole selection.
    assert {preview["fingerprint"] for preview in previews} == {previews[0]["fingerprint"]}
    assert previews[0]["scope"] == "request_selection" and previews[0]["count"] == 3
    intent = stopped[1]["params"]["input_intent"]
    confirmed = client.post(
        f"/api/input-intents/{stopped[1]['id']}/prepare",
        json={
            "epoch": intent["epoch"],
            "phase": intent["phase"],
            "fingerprint": previews[0]["fingerprint"],
            "confirmed": True,
        },
    )
    assert confirmed.status_code == 202, confirmed.text
    successors = confirmed.json()["jobs"]
    assert {job["recording_id"] for job in successors} == {row["id"] for row in runtime.rows}
    assert {accepted(job)["prepare_consent"] for job in successors} == {"request"}
    for job in stopped:
        continuation = input_admission.projection(runtime.store, job["id"])["continuation"]
        assert continuation["request_id"] == confirmed.json()["request_id"]


def test_v0098_mixed_unknown_and_unprepared_targets_still_ask(
    client, runtime, credentials, monkeypatch
):
    standing(client, False)
    other = add_profile(runtime.store, credentials, "whisperx/large-v3")
    answers = iter([None, NOT_READY])

    def states(credentials, targets, **_):
        assert len(targets) == 2
        return {key: next(answers) for key in sorted(targets)}

    monkeypatch.setattr(enhancement_api, "preparation_states", states)
    response = client.post(
        "/api/transcriptions",
        json={
            "recording_ids": [runtime.rows[0]["id"]],
            "provider_ids": [runtime.provider["id"], other["id"]],
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["recordings"] == 1


def test_v0098_whole_ucloud_enhancement_asks_before_accepting(client, runtime, credentials):
    standing(client, False)
    add_profile(runtime.store, credentials, "large-v3")
    row = add_recording(runtime.store, runtime.project, "reviewed.wav", reviewed=True)
    body = {
        "result_id": row["active_transcript_id"],
        "revision": row["revision"],
        "execution": "ucloud",
    }
    before = runtime.store.all("SELECT * FROM jobs ORDER BY id")
    refused = client.post(f"/api/recordings/{row['id']}/enhancements", json=body)
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "prepare_runtime" and detail["recordings"] == 1
    assert "UCloud WhisperX enhancement (CBS Runtime)" in detail["message"]
    assert "for 1 recording." in detail["message"]
    assert runtime.store.all("SELECT * FROM jobs ORDER BY id") == before
    response = client.post(
        f"/api/recordings/{row['id']}/enhancements", json={**body, "prepare_runtime": True}
    )
    assert response.status_code == 202, response.text
    assert accepted(response.json())["prepare_consent"] == "request"
    assert accepted(response.json())["options"]["prepare_runtime"] is True
    assert len(runtime.probes) == 1


def test_v0098_retry_never_grants_preparation_consent(client, runtime, credentials):
    standing(client, False)
    runtime.observed["state"] = None
    job = transcribe(client, runtime).json()[0]
    stopped = inspect_and_materialize(runtime.store, credentials, [job["id"]])[0]
    before = runtime.store.one("SELECT * FROM jobs WHERE id=?", (stopped["id"],))
    # A later enabled Processing choice never changes the stopped intent or grants Retry consent.
    standing(client, True)
    intent = stopped["params"]["input_intent"]
    response = client.post(
        f"/api/input-intents/{stopped['id']}/retry",
        json={"epoch": intent["epoch"], "phase": intent["phase"]},
    )
    assert response.status_code == 400
    assert "preparation consent" in response.json()["detail"]
    assert runtime.store.one("SELECT * FROM jobs WHERE id=?", (stopped["id"],)) == before


def test_v0098_missing_speaker_model_without_token_is_refused_not_asked(
    client, runtime, credentials
):
    runtime.observed["state"] = {"ready": False, "speakers": False}
    before = runtime.store.all("SELECT * FROM jobs ORDER BY id")
    response = transcribe(client, runtime, requested_features={"speaker_labels": True})
    assert response.status_code == 400
    assert response.json()["detail"] == enhancement_api.speaker_refusal(credentials)
    assert runtime.store.all("SELECT * FROM jobs ORDER BY id") == before


@pytest.mark.parametrize("enabled", [False, True])
def test_v0098_standing_setting_persists_and_omitted_field_keeps_saved_choice(client, enabled):
    assert client.get("/api/enhancement-settings").json()["prepare_ucloud_models"] is True
    standing(client, enabled)
    saved = client.get("/api/enhancement-settings").json()
    older_client = {key: saved[key] for key in ("runtime_python", "device", "language")}
    response = client.put("/api/enhancement-settings", json=older_client | {"device": "cpu"})
    assert response.status_code == 200
    assert response.json()["prepare_ucloud_models"] is enabled
    assert response.json()["device"] == "cpu"
    # The standing choice never enters job options or frozen enhancement identities.
    assert "prepare_ucloud_models" not in EnhancementOptions.model_fields


def legacy(intent):
    """The exact version-1 intent shape written before ADR 0098."""
    old = copy.deepcopy(intent)
    del old["accepted"]["prepare_consent"]
    old["version"] = 1
    old["accepted_sha256"] = input_admission._digest(input_admission._accepted_payload(old))
    return old


def test_v0098_legacy_intents_validate_and_versions_stay_strict(client, runtime, credentials):
    standing(client, False)
    runtime.observed["state"] = None
    job = transcribe(client, runtime).json()[0]
    intent = job["params"]["input_intent"]
    old = legacy(intent)
    runtime.store.execute(
        "UPDATE jobs SET params=? WHERE id=?",
        (input_admission._canonical(input_admission._envelope(old)), job["id"]),
    )
    stopped = inspect_and_materialize(runtime.store, credentials, [job["id"]])[0]
    stopped_intent = stopped["params"]["input_intent"]
    assert stopped_intent["version"] == 1 and "prepare_consent" not in stopped_intent["accepted"]
    preview = input_admission.projection(runtime.store, stopped["id"])["prepare_preview"]
    result = input_admission.confirm_preparation(
        runtime.store,
        stopped["id"],
        epoch=stopped_intent["epoch"],
        phase=stopped_intent["phase"],
        fingerprint=preview["fingerprint"],
        confirmed=True,
    )
    [successor] = [job for job in result["jobs"] if job["recording_id"] == stopped["recording_id"]]
    assert successor["params"]["input_intent"]["version"] == 2
    assert accepted(successor)["prepare_consent"] == "request"
    # Each version accepts exactly its own field set and consistent provenance.
    for changed in (
        {**old, "version": 2},
        {**intent, "version": 1},
        {**intent, "accepted": {**intent["accepted"], "prepare_consent": "standing"}},
        {**intent, "accepted": {**intent["accepted"], "prepare_consent": "automatic"}},
    ):
        changed = copy.deepcopy(changed)
        changed["accepted_sha256"] = input_admission._digest(
            input_admission._accepted_payload(changed)
        )
        with pytest.raises(input_admission.AdmissionError):
            input_admission._validate(changed)


def choice(provider, **values):
    return input_admission.choice(
        provider=provider,
        routing_snapshot={"rules": routing.Rules().model_dump(), "connection": provider},
        **values,
    )


def test_v0098_targets_cover_explicit_ucloud_only(runtime, credentials):
    native = add_profile(runtime.store, credentials, "large-v3")
    full = choice(runtime.provider)
    [(chosen, options, label)] = input_admission.preparation_targets(full)
    assert chosen["id"] == runtime.provider["id"] and options.transcribe
    assert label == "UCloud Røst v3 (CBS Runtime) (Danish)"
    assert input_admission.preparation_targets(choice(runtime.provider, prepare_runtime=True)) == []
    followup = choice(
        native, requested_features={"speaker_labels": True}, enhancement={"execution": "ucloud"}
    )
    [(_, options, _)] = input_admission.preparation_targets(followup)
    assert options.diarize and not options.transcribe
    for execution in ("auto", "local"):
        assert (
            input_admission.preparation_targets(
                choice(
                    native,
                    requested_features={"speaker_labels": True},
                    enhancement={"execution": execution},
                )
            )
            == []
        )
    whole = input_admission.choice(
        options={"execution": "ucloud"},
        routing_snapshot={"rules": routing.Rules().model_dump(), "connection": native},
        source={"result_id": "0" * 32, "revision": 0},
    )
    [(chosen, options, _)] = input_admission.preparation_targets(whole)
    assert chosen["id"] == native["id"] and options.align


class Listing:
    """A read-only synthetic UCloud drive; any write is a test failure."""

    def __init__(self, folders, *, fail=False):
        self.folders, self.fail, self.timeouts = set(folders), fail, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def browse_files(self, path, items_per_page=None):
        if self.fail:
            raise OSError("Synthetic offline drive")
        prefix = path.rstrip("/") + "/"
        items = [{"id": folder} for folder in self.folders if folder.startswith(prefix)]
        return {"items": items, "next": None}


@contextmanager
def drive(monkeypatch, listing):
    def client_for(settings, key, *, timeout=30):
        listing.timeouts.append(timeout)
        return listing

    monkeypatch.setattr(enhancement_api.native, "client_for", client_for)
    monkeypatch.setattr(
        enhancement_api.native,
        "folder_listed",
        lambda client, path: path in client.folders,
    )
    yield listing


def test_v0098_probe_is_tri_state_and_bounded(runtime, monkeypatch, credentials):
    monkeypatch.setattr(enhancement_api, "preparation_states", REAL_STATES)
    settings = providers.Settings.model_validate(runtime.provider["settings"])
    options = EnhancementOptions(
        execution="ucloud", transcribe=True, align=True, whisper_model="large-v3", device="cuda"
    )
    root = enhancement_api.runtime_folder(settings)
    complete = {root, root + "/stamp.json"} | {
        enhancement_api.model_folder(settings, name)
        for name in enhancement_api.prefetch_models(options)
    }
    target = {"one": (runtime.provider, options)}
    with drive(monkeypatch, Listing(complete)) as listing:
        assert enhancement_api.preparation_states(credentials, target) == {
            "one": {"ready": True, "speakers": None}
        }
        assert set(listing.timeouts) == {enhancement_api.PROBE_REQUEST_SECONDS}
    with drive(monkeypatch, Listing({root})):
        assert enhancement_api.preparation_states(credentials, target)["one"]["ready"] is False
    with drive(monkeypatch, Listing(complete, fail=True)):
        assert enhancement_api.preparation_states(credentials, target) == {"one": None}
    release = threading.Event()

    def slow(*args):
        release.wait(5)
        return {"ready": False, "speakers": None}

    monkeypatch.setattr(enhancement_api, "preparation_state", slow)
    started = time.monotonic()
    try:
        assert enhancement_api.preparation_states(credentials, target, seconds=0.2) == {"one": None}
    finally:
        release.set()
    assert time.monotonic() - started < 2
