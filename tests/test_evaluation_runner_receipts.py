"""Public evaluation must score one exact machine output, never today's active view."""

import copy
import hashlib
import json
import runpy
from pathlib import Path

import httpx
import pytest
from admission_support import write_original_wav
from test_runtime_batch_api import add_profile

from cbs_transcriber.history import encode_payload
from cbs_transcriber.transcripts import normalize_segments

ROOT = Path(__file__).resolve().parents[1]
RUNNER = runpy.run_path(str(ROOT / "scripts/evaluate_fleurs.py"))


@pytest.fixture
def runtime_admission(client, credentials):
    store = client.app.state.store
    project = store.create_project("Synthetic evaluation admission")
    recording = store.create_recording(project["id"], "synthetic.wav")
    write_original_wav(store, recording)
    provider = add_profile(store, credentials, "whisperx/large-v3-turbo")
    with store.library_lock():
        response = client.post(
            "/api/transcriptions",
            json={"recording_ids": [recording["id"]], "provider_ids": [provider["id"]]},
        )
    assert response.status_code == 202, response.text
    return store, recording["id"], provider, response.json()


def test_current_runtime_admission_binds_frozen_choices_before_preparation(runtime_admission):
    store, rid, provider, jobs = runtime_admission
    assert jobs[0]["kind"] == "enhance"
    assert jobs[0]["params"]["input_intent"]["phase"] == "waiting"
    assert "ucloud_api" not in jobs[0]["params"]
    before = store.all("SELECT * FROM jobs ORDER BY id")
    accepted = RUNNER["accepted_jobs"](
        jobs, {"clip": rid}, {"id": provider["id"], **provider["settings"]}
    )
    receipt = accepted[rid]
    assert receipt["id"] == jobs[0]["id"]
    assert receipt["source_model"] == "large-v3-turbo"
    assert receipt["source_fields"] == {
        "provider_id": provider["id"],
        "model": "large-v3-turbo",
        "language": "da",
        "ucloud_server": "https://runtime.invalid",
        "ucloud_provider_id": provider["id"],
    }
    assert store.all("SELECT * FROM jobs ORDER BY id") == before


@pytest.mark.parametrize("damage", ["hash", "envelope", "settings", "source", "cancelled"])
def test_runtime_admission_cannot_substitute_frozen_identity_or_bounded_work(
    runtime_admission, damage
):
    from cbs_transcriber import input_admission

    store, rid, provider, jobs = runtime_admission
    params = jobs[0]["params"]
    intent = params["input_intent"]
    if damage == "hash":
        intent["accepted_sha256"] = "0" * 64
    elif damage == "envelope":
        params["enhancement"]["whisper_model"] = "large-v3"
    else:
        if damage == "settings":
            intent["accepted"]["provider"]["settings"]["base_url"] = "https://other.invalid"
        elif damage == "source":
            intent["source_snapshot"] = {}
        else:
            intent["cancel_requested"] = True
        intent["accepted_sha256"] = input_admission._digest(
            input_admission._accepted_payload(intent)
        )
        jobs[0]["params"] = input_admission._envelope(intent)
    before = store.all("SELECT * FROM jobs ORDER BY id")
    with pytest.raises(ValueError, match="selected provider or recordings"):
        RUNNER["accepted_jobs"](jobs, {"clip": rid}, {"id": provider["id"], **provider["settings"]})
    assert store.all("SELECT * FROM jobs ORDER BY id") == before


def evaluation_http(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "clip.wav").write_bytes(b"synthetic public clip; HTTP is mocked")
    manifest = {
        "dataset": "synthetic-public",
        "revision": "test",
        "clips": [{"id": "clip", "file": "clip.wav", "text": "one two"}],
    }
    settings = {
        "id": "provider-1",
        "base_url": "https://provider.invalid/v1",
        "model": "whisper-1",
        "language": "da",
        "capability": "words",
        "max_upload_mb": 24,
    }
    segments = normalize_segments([{"text": "one wrong", "start": 0, "end": 1}])
    payload, digest = encode_payload(segments, {})
    creation = {
        "id": "version-1",
        "result_id": "result-1",
        "recording_id": "recording-1",
        "sequence": 1,
        "predecessor_id": None,
        "source_version_id": None,
        "payload_sha256": digest,
        "actor_kind": "worker",
        "action": "provider_result",
        "context": {"producer_job_id": "transcribe-1", "processing_kind": "transcribe"},
        "payload": {**json.loads(payload), "speaker_defaults": {}},
    }
    accepted = {
        "id": "transcribe-1",
        "recording_id": "recording-1",
        "kind": "transcribe",
        "state": "queued",
        "params": {"provider_id": settings["id"], "settings": dict(settings)},
    }
    result = {
        "id": "result-1",
        "recording_id": "recording-1",
        "job_id": "transcribe-1",
        "segments": normalize_segments([{"text": "one two", "start": 0, "end": 1}]),
        "speakers": {},
        "source": {"kind": "provider", "provider_id": settings["id"], **settings},
    }
    state = {
        "accepted": [accepted],
        "completed": {
            "id": "transcribe-1",
            "recording_id": "recording-1",
            "kind": "transcribe",
            "state": "completed",
            "result_id": "result-1",
        },
        "result": result,
        "creation": creation,
        "settings": settings,
        "calls": [],
        "exports": [],
    }

    def respond(request):
        path = request.url.path
        state["calls"].append((request.method, path, dict(request.url.params)))
        if path == "/api/settings":
            return httpx.Response(200, json=state["settings"])
        if path == "/api/projects":
            return httpx.Response(201, json={"id": "project-1"})
        if path == "/api/recordings":
            return httpx.Response(
                201, json={"recording": {"id": "recording-1"}, "job": {"id": "media-1"}}
            )
        if path == "/api/transcriptions":
            state["enqueue_body"] = json.loads(request.content)
            return httpx.Response(202, json=state["accepted"])
        if path == "/api/jobs":
            return httpx.Response(
                200,
                json=[
                    {"id": "media-1", "state": "completed"},
                    state["completed"],
                ],
            )
        if path == "/api/recordings/recording-1":
            return httpx.Response(200, json={"segments": state["result"]["segments"]})
        if path == "/api/recordings/recording-1/transcripts/result-1":
            return httpx.Response(200, json=state["result"])
        if path == "/api/recordings/recording-1/transcripts/result-1/versions":
            return httpx.Response(
                200,
                json=[
                    {
                        key: value
                        for key, value in state["creation"].items()
                        if key not in {"payload", "context", "recording_id"}
                        and key not in state.get("metadata_omissions", set())
                    }
                ],
            )
        if path.endswith("/versions/version-1"):
            return httpx.Response(
                200,
                json={
                    key: value
                    for key, value in state["creation"].items()
                    if key not in state.get("version_omissions", set())
                },
            )
        if path.endswith("/export"):
            state["exports"].append(path)
            text = "one wrong" if "/versions/version-1/" in path else "one two"
            return httpx.Response(200, content=("INTERVIEW\n" + text + "\n").encode())
        if "/versions/" in path:
            return httpx.Response(404, json={"detail": "Unknown synthetic version"})
        raise AssertionError("Unexpected local API operation: " + path)

    return suite, manifest, state, httpx.MockTransport(respond)


def test_evaluation_uses_machine_creation_after_active_result_and_wording_change(tmp_path):
    suite, manifest, state, transport = evaluation_http(tmp_path)
    output = tmp_path / "run"
    with httpx.Client(base_url="http://127.0.0.1", transport=transport) as client:
        report = RUNNER["run_app_suite"](client, suite, manifest, output)
    assert report["wer"] == 0.5
    assert report["clips"][0]["prediction"] == "one wrong"
    assert (output / "clip.nvivo.txt").read_text() == "INTERVIEW\none wrong\n"
    assert state["enqueue_body"]["provider_ids"] == ["provider-1"]
    receipt = report["run"]["results"]["clip"]
    assert receipt["job_id"] == "transcribe-1"
    assert receipt["recording_id"] == "recording-1"
    assert receipt["result_id"] == "result-1"
    assert receipt["version_id"] == "version-1"
    assert receipt["payload_sha256"] == state["creation"]["payload_sha256"]
    assert receipt["export_format"] == "text"
    assert receipt["prediction_text_sha256"] == hashlib.sha256(b"one wrong").hexdigest()
    assert receipt["export_bytes"] == len((output / "clip.nvivo.txt").read_bytes())
    assert (
        receipt["export_sha256"]
        == hashlib.sha256((output / "clip.nvivo.txt").read_bytes()).hexdigest()
    )
    assert not any(path == "/api/recordings/recording-1" for _, path, _ in state["calls"])
    assert state["exports"] == [
        "/api/recordings/recording-1/transcripts/result-1/versions/version-1/export"
    ]


def test_creation_binding_accepts_real_public_history_contract(client):
    from cbs_transcriber.providers import public_settings

    store = client.app.state.store
    settings = public_settings(store, client.app.state.credentials)
    project = store.create_project("Synthetic evaluation")
    recording = store.create_recording(project["id"], "public.wav")
    accepted = store.add_job(
        recording["id"],
        "transcribe",
        {"provider_id": settings["id"], "settings": settings},
    )
    store.update_job(accepted["id"], state="running")
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "one wrong", "start": 0, "end": 1}]),
        {},
        "Synthetic provider",
        {"kind": "provider", "provider_id": settings["id"], **settings},
        job_id=accepted["id"],
    )
    current = store.recording(recording["id"])
    changed = normalize_segments([{"text": "one two", "start": 0, "end": 1}])
    store.save_transcript(recording["id"], changed, {}, current["revision"], action="free_rewrite")
    selected = RUNNER["accepted_jobs"]([accepted], {"clip": recording["id"]}, settings)
    completed = RUNNER["wait_for_jobs"](client, [accepted["id"]], 1, 0)
    payload, path, receipt = RUNNER["creation_version"](
        client, selected[recording["id"]], completed[accepted["id"]]
    )
    assert payload["segments"][0]["text"] == "one wrong"
    assert receipt["result_id"] == result["id"]
    assert receipt["version_id"] in path
    assert client.get(path + "/export", params={"format": "text"}).status_code == 200


@pytest.mark.parametrize(
    "damage",
    [
        "accepted_recording",
        "accepted_provider",
        "accepted_model",
        "accepted_duplicate",
        "accepted_missing",
        "generic_enhancement",
        "completed_recording",
        "completed_kind",
        "missing_result",
        "result_recording",
        "result_producer",
        "missing_creation",
        "version_result",
        "version_recording",
        "version_sequence",
        "version_actor",
        "version_action",
        "version_predecessor",
        "version_source",
        "version_producer",
        "version_hash",
        "version_text",
        "version_context",
        "malformed_result",
    ],
)
def test_evaluation_refuses_unbound_or_changed_receipts_without_active_fallback(tmp_path, damage):
    suite, manifest, state, transport = evaluation_http(tmp_path)
    accepted, completed, result, creation = (
        state["accepted"][0],
        state["completed"],
        state["result"],
        state["creation"],
    )
    if damage == "accepted_recording":
        accepted["recording_id"] = "other"
    elif damage == "accepted_provider":
        accepted["params"]["provider_id"] = "other"
    elif damage == "accepted_model":
        accepted["params"]["settings"]["model"] = "gpt-4o-transcribe"
    elif damage == "accepted_duplicate":
        state["accepted"].append(copy.deepcopy(accepted))
    elif damage == "accepted_missing":
        state["accepted"] = []
    elif damage == "generic_enhancement":
        accepted["kind"] = "enhance"
    elif damage == "completed_recording":
        completed["recording_id"] = "other"
    elif damage == "completed_kind":
        completed["kind"] = "media"
    elif damage == "missing_result":
        completed["result_id"] = None  # Public API hides a result already in Trash.
    elif damage == "result_recording":
        result["recording_id"] = "other"
    elif damage == "result_producer":
        result["job_id"] = "other"
    elif damage == "missing_creation":
        creation["id"] = "wrong-version"
    elif damage == "version_result":
        creation["result_id"] = "other"
    elif damage == "version_recording":
        creation["recording_id"] = "other"
    elif damage == "version_sequence":
        creation["sequence"] = 2
    elif damage == "version_actor":
        creation["actor_kind"] = "local_user"
    elif damage == "version_action":
        creation["action"] = "version_restore"
    elif damage == "version_predecessor":
        creation["predecessor_id"] = "other"
    elif damage == "version_source":
        creation["source_version_id"] = "other"
    elif damage == "version_producer":
        creation["context"]["producer_job_id"] = "other"
    elif damage == "version_hash":
        creation["payload_sha256"] = "0" * 64
    elif damage == "version_text":
        creation["payload"]["segments"][0]["text"] = "changed"
    elif damage == "version_context":
        creation["context"] = None
    elif damage == "malformed_result":
        result["source"] = None
    output = tmp_path / "run"
    with httpx.Client(base_url="http://127.0.0.1", transport=transport) as client:
        with pytest.raises(ValueError):
            RUNNER["run_app_suite"](client, suite, manifest, output)
    assert json.loads((output / "run.json").read_text())["status"] == "incomplete"
    assert not (output / "report.json").exists()
    assert not (output / "predictions.json").exists()
    assert not state["exports"]
    assert not any(path == "/api/recordings/recording-1" for _, path, _ in state["calls"])


@pytest.mark.parametrize("row", ["metadata", "version"])
@pytest.mark.parametrize("field", ["predecessor_id", "source_version_id"])
def test_creation_requires_explicit_null_ancestry_fields(tmp_path, row, field):
    suite, manifest, state, transport = evaluation_http(tmp_path)
    state[row + "_omissions"] = {field}
    output = tmp_path / "run"
    with httpx.Client(base_url="http://127.0.0.1", transport=transport) as client:
        with pytest.raises(ValueError, match="creation receipt"):
            RUNNER["run_app_suite"](client, suite, manifest, output)
    assert json.loads((output / "run.json").read_text())["status"] == "incomplete"
    assert not (output / "predictions.json").exists()
    assert not state["exports"]
    assert not any(path == "/api/recordings/recording-1" for _, path, _ in state["calls"])


def runtime_job(state):
    state["settings"].update(
        provider_type="ucloud", base_url="https://cloud.sdu.dk", model="whisperx/large-v3-turbo"
    )
    job = state["accepted"][0]
    job["kind"] = state["completed"]["kind"] = "enhance"
    state["creation"]["context"]["processing_kind"] = "enhance"
    state["result"]["source"] = {
        "kind": "provider",
        "provider_type": "whisperx",
        "provider_id": state["settings"]["id"],
        "model": "large-v3-turbo",
        "language": "da",
        "ucloud_server": state["settings"]["base_url"],
        "ucloud_provider_id": state["settings"]["id"],
    }
    job["params"] = {
        "provider_id": state["settings"]["id"],
        "transcription": True,
        "ucloud_api": {
            "provider_id": state["settings"]["id"],
            "settings": dict(state["settings"]),
        },
        "enhancement": {
            "transcribe": True,
            "retranscribe": False,
            "execution": "ucloud",
            "whisper_model": "large-v3-turbo",
            "language": "da",
        },
    }
    return job


@pytest.mark.parametrize("language", ["da", "en", "auto"])
def test_runtime_full_asr_receipts_use_frozen_asr_alias_with_explicit_language(tmp_path, language):
    suite, manifest, state, transport = evaluation_http(tmp_path)
    job = runtime_job(state)
    state["settings"]["language"] = language
    job["params"]["ucloud_api"]["settings"]["language"] = language
    job["params"]["enhancement"]["language"] = language
    state["result"]["source"]["language"] = language
    with httpx.Client(base_url="http://127.0.0.1", transport=transport) as client:
        report = RUNNER["run_app_suite"](client, suite, manifest, tmp_path / "run")
    assert report["wer"] == 0.5
    assert report["run"]["results"]["clip"]["result_id"] == "result-1"


def test_runtime_saved_processing_language_fallback_is_not_reconstructed(tmp_path):
    suite, manifest, state, transport = evaluation_http(tmp_path)
    job = runtime_job(state)
    state["settings"]["language"] = ""
    job["params"]["ucloud_api"]["settings"]["language"] = ""
    assert job["params"]["enhancement"]["language"] == "da"
    with httpx.Client(base_url="http://127.0.0.1", transport=transport) as client:
        report = RUNNER["run_app_suite"](client, suite, manifest, tmp_path / "run")
    assert report["run"]["provider"]["language"] == ""
    assert report["wer"] == 0.5


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_snapshot", {}),
        ("segment_id", "one"),
        ("segment_ids", []),
        ("timing_units", []),
        ("realign", True),
        ("retranscription", True),
        ("transcription", 1),
    ],
)
def test_runtime_bounded_or_contradictory_top_level_shapes_are_refused(tmp_path, field, value):
    _, _, state, _ = evaluation_http(tmp_path)
    job = runtime_job(state)
    job["params"][field] = value
    with pytest.raises(ValueError):
        RUNNER["accepted_jobs"]([job], {"clip": "recording-1"}, state["settings"])


@pytest.mark.parametrize(
    "field,value",
    [("transcribe", False), ("transcribe", 1), ("retranscribe", True), ("execution", "local")],
)
def test_runtime_internal_options_must_agree_with_full_asr_operation(tmp_path, field, value):
    _, _, state, _ = evaluation_http(tmp_path)
    job = runtime_job(state)
    job["params"]["enhancement"][field] = value
    with pytest.raises(ValueError):
        RUNNER["accepted_jobs"]([job], {"clip": "recording-1"}, state["settings"])


@pytest.mark.parametrize("field", ["model", "provider_id", "base_url", "language"])
def test_evaluation_refuses_source_contradicting_frozen_provider(tmp_path, field):
    suite, manifest, state, transport = evaluation_http(tmp_path)
    state["result"]["source"][field] = "other"
    with httpx.Client(base_url="http://127.0.0.1", transport=transport) as client:
        with pytest.raises(ValueError, match="producer"):
            RUNNER["run_app_suite"](client, suite, manifest, tmp_path / "run")
    assert not state["exports"]


@pytest.mark.parametrize("damage", ["missing", "duplicate", "invalid"])
def test_missing_or_ambiguous_job_status_is_never_completed(damage):
    row = {"id": "one", "state": "completed"}
    rows = [] if damage == "missing" else [row, row] if damage == "duplicate" else ["bad"]
    with httpx.Client(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=rows)),
    ) as client:
        with pytest.raises(ValueError):
            RUNNER["wait_for_jobs"](client, ["one"], 1, 0)


def test_receipt_excludes_unrelated_private_settings_context_and_source_fields(tmp_path):
    suite, manifest, state, transport = evaluation_http(tmp_path)
    secret = "SYNTHETIC_DO_NOT_PUBLISH"
    state["accepted"][0]["params"]["private_extra"] = secret
    state["creation"]["context"]["private_reason"] = secret
    state["result"]["source"]["private_path"] = secret
    with httpx.Client(base_url="http://127.0.0.1", transport=transport) as client:
        report = RUNNER["run_app_suite"](client, suite, manifest, tmp_path / "run")
    assert secret not in json.dumps(report)
    assert secret not in (tmp_path / "run/run.json").read_text()


@pytest.mark.parametrize("url", [None, "http://127.0.0.1:8899"])
def test_live_command_defaults_to_current_app_port_and_retains_explicit_override(
    tmp_path, monkeypatch, url
):
    captured = {}
    original_client = httpx.Client

    def client(**kwargs):
        captured.update(kwargs)
        return original_client(
            **kwargs,
            transport=httpx.MockTransport(lambda request: pytest.fail("No HTTP in this test")),
        )

    def local_run(client, suite, manifest, output, **kwargs):
        output.mkdir()
        return {"coverage": {"scored": 1}, "wer": 0.5, "cer": 0.5}

    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setitem(RUNNER["main"].__globals__, "run_app_suite", local_run)
    args = ["run", "--allow-api-calls", "--output", str(tmp_path / "run")]
    if url:
        args += ["--app-url", url]
    assert RUNNER["main"](args) == 0
    assert captured["base_url"] == (url or "http://127.0.0.1:8765")
