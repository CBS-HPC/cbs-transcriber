"""Fresh API producer observations; synthetic audio and mocked providers only (ADR 0097)."""

import copy
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from admission_support import inspect_and_materialize
from conftest import finish_media
from report_support import selected_view_report

from cbs_transcriber import media, portable_capture, provider_capture, providers
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.providers import OPENAI_URL, Settings
from cbs_transcriber.transcripts import edit_segment

SECRET = "SYNTHETIC_PROVIDER_SECRET_CANARY"
OUTPUT = {
    "text": "Synthetic words.",
    "segments": [{"text": "Synthetic words.", "start": 0, "end": 1}],
}


@pytest.fixture
def library_lease(client):
    with client.app.state.store.library_lock():
        yield


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fresh_api_job(client, wav_bytes):
    project = client.post("/api/projects", json={"name": "Synthetic provider capture"}).json()
    response = client.post(
        "/api/recordings",
        data={"project_id": project["id"]},
        files={"file": ("synthetic.wav", wav_bytes, "audio/wav")},
    )
    assert response.status_code == 201, response.text
    recording = finish_media(client, response.json())
    response = client.put(
        "/api/settings",
        json={
            "provider_type": "custom",
            "base_url": "http://127.0.0.1:9000/v1",
            "model": "synthetic",
            "capability": "segments",
            "api_key": SECRET,
        },
    )
    assert response.status_code == 200, response.text
    response = client.post("/api/transcriptions", json={"recording_ids": [recording["id"]]})
    assert response.status_code == 202, response.text
    store = client.app.state.store
    [job] = inspect_and_materialize(store, client.app.state.credentials, [response.json()[0]["id"]])
    return store, recording, job


def run(client, job):
    client.app.state.store.update_job(job["id"], state="running")
    client.app.state.queue.process(job["id"])


def creation(store, recording):
    [result] = store.results(recording["id"])
    row = store.one(
        "SELECT id,payload_sha256,context FROM result_versions WHERE result_id=? AND sequence=1",
        (result["id"],),
    )
    return result, row, json.loads(row["context"])


def test_fresh_api_job_binds_exact_request_and_output_observations(
    client, wav_bytes, monkeypatch, library_lease
):
    store, recording, job = fresh_api_job(client, wav_bytes)
    assert job["params"]["provider_artifact_policy"] == provider_capture.POLICY
    assert "provider_artifact_capture" not in job["params"]
    keys = []

    def transcribe(path, settings, key):
        keys.append(key)
        return copy.deepcopy(OUTPUT)

    monkeypatch.setattr(providers, "transcribe_file", transcribe)
    run(client, job)
    assert keys == [SECRET]
    saved = store.job(job["id"])
    capture = saved["params"]["provider_artifact_capture"]
    [attempt] = capture["units"]["0000"]["attempts"]
    folder = store.directory(recording["id"]) / "chunks" / job["id"]
    request_raw = (folder / "request-0000-0000.json").read_bytes()
    response_raw = (folder / "response-0000.json").read_bytes()
    descriptor = json.loads(request_raw)
    assert descriptor == attempt["descriptor"]
    assert descriptor["parameters"] == {"model": "synthetic", "response_format": "verbose_json"}
    prepared = saved["params"]["audio_preparations"][0]["parts"][0]["prepared"]
    assert descriptor["input"]["scope"] == "prepared_part"  # Custom endpoints get MP3 parts.
    assert (descriptor["input"]["sha256"], descriptor["input"]["bytes"]) == (
        prepared["sha256"],
        prepared["bytes"],
    )
    assert attempt["request"]["artifacts"]["request"]["sha256"] == sha(request_raw)
    output = attempt["response"]["artifacts"]["output"]
    assert (output["sha256"], output["bytes"]) == (sha(response_raw), len(response_raw))
    assert attempt["response"]["artifacts"]["raw_response"] == "not_captured"
    assert attempt["part_sha256"] == provider_capture.part_hash(saved["chunks"][0])

    result, version, context = creation(store, recording)
    bound = context["provider_artifacts"]
    reference = {
        "result_id": result["id"],
        "version_id": version["id"],
        "payload_sha256": version["payload_sha256"],
    }
    assert bound["creation"] == reference
    assert bound["assembly_sha256"] == version["payload_sha256"] == capture["assembly_sha256"]
    transport = provider_capture.unbound(
        bound, recording_id=recording["id"], job_id=job["id"], creation=reference
    )
    assert transport == capture
    # Credentials never reach captured bytes, job observations or the version history.
    for blob in (
        request_raw,
        response_raw,
        json.dumps(saved["params"]).encode(),
        version["context"].encode(),
    ):
        assert SECRET.encode() not in blob
        assert b"Authorization" not in blob and b"Bearer" not in blob

    # Later edits inherit the exact original observation; nothing is rebound.
    current = store.recording(recording["id"])
    store.save_transcript(
        recording["id"],
        [edit_segment(current["segments"][0], "free", "Edited synthetic words.")],
        current["speakers"],
        current["revision"],
        action="free_rewrite",
        context={"segment_id": "0"},
    )
    head = store.one(
        "SELECT context FROM result_versions WHERE result_id=? ORDER BY sequence DESC LIMIT 1",
        (result["id"],),
    )
    assert json.loads(head["context"])["provider_artifacts"] == bound

    # A completed retry performs no request and re-observes nothing.
    monkeypatch.setattr(
        providers, "transcribe_file", lambda *a, **k: pytest.fail("no second request")
    )
    monkeypatch.setattr(
        provider_capture, "observe_response", lambda *a: pytest.fail("no re-observation")
    )
    run(client, job)
    assert store.job(job["id"])["params"] == saved["params"]
    assert len(store.results(recording["id"])) == 1


def test_store_creation_requires_the_exact_persisted_observation(
    client, wav_bytes, monkeypatch, library_lease
):
    store, recording, job = fresh_api_job(client, wav_bytes)
    monkeypatch.setattr(providers, "transcribe_file", lambda *a: copy.deepcopy(OUTPUT))
    original = store.add_result
    monkeypatch.setattr(store, "add_result", lambda *a, **k: None)  # Stop before creation.
    run(client, job)
    monkeypatch.setattr(store, "add_result", original)
    parent = store.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
    stored = json.loads(parent["params"])["provider_artifact_capture"]
    assert stored["assembly_sha256"] is not None
    # Omitted, altered or source-asserted observations never create a result.
    with pytest.raises(ValueError, match="provider artifact"):
        provider_capture.for_creation(parent, recording["id"], None)
    altered = copy.deepcopy(stored)
    altered["units"]["0000"]["attempts"][0]["part_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="provider artifact"):
        provider_capture.for_creation(parent, recording["id"], altered)
    with pytest.raises(ValueError, match="cannot assert provider"):
        store.add_result(
            recording["id"], [], {}, "Forged", {"kind": "provider", "provider_artifacts": {}}
        )
    with pytest.raises(ValueError, match="provider artifact"):
        provider_capture.bind(
            stored,
            creation={"result_id": "a" * 32, "version_id": "b" * 32, "payload_sha256": "c" * 64},
        )
    # Generic job writes cannot erase or replace the frozen observations.
    params = store.job(job["id"])["params"]
    for proposed in (
        {key: value for key, value in params.items() if key != "provider_artifact_capture"},
        {**params, "audio_transport": "original"},
    ):
        with pytest.raises(ValueError, match="provider artifact"):
            store.update_job(job["id"], params=proposed)
    assert store.job(job["id"])["params"] == params


def test_failed_attempt_retry_records_a_new_descriptor_with_frozen_fields(
    client, wav_bytes, monkeypatch, library_lease
):
    store, recording, job = fresh_api_job(client, wav_bytes)
    calls = []

    def transcribe(path, settings, key, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ValueError("Synthetic provider failure")
        return copy.deepcopy(OUTPUT)

    monkeypatch.setattr(providers, "transcribe_file", transcribe)
    with pytest.raises(ValueError, match="Synthetic provider failure"):
        run(client, job)
    [failed] = store.job(job["id"])["params"]["provider_artifact_capture"]["units"]["0000"][
        "attempts"
    ]
    assert failed["response"] is None and failed["part_sha256"] is None
    run(client, job)
    capture = store.job(job["id"])["params"]["provider_artifact_capture"]
    first, second = capture["units"]["0000"]["attempts"]
    assert first == failed and second["response"] is not None
    assert second["descriptor"]["attempt"] == 1
    assert second["descriptor"]["generation"] != first["descriptor"]["generation"]
    assert second["descriptor"]["parameters"] == first["descriptor"]["parameters"]
    assert calls == [{}, {}]
    folder = store.directory(recording["id"]) / "chunks" / job["id"]
    assert (folder / "request-0000-0001.json").is_file()
    _, version, context = creation(store, recording)
    assert context["provider_artifacts"]["creation"]["version_id"] == version["id"]


def test_changed_catalog_replays_the_first_frozen_fields(
    client, wav_bytes, monkeypatch, library_lease
):
    store, recording, job = fresh_api_job(client, wav_bytes)
    calls = []

    def transcribe(path, settings, key, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ValueError("Synthetic provider failure")
        return copy.deepcopy(OUTPUT)

    monkeypatch.setattr(providers, "transcribe_file", transcribe)
    with pytest.raises(ValueError, match="Synthetic"):
        run(client, job)
    frozen = store.job(job["id"])["params"]["provider_artifact_capture"]
    fields = frozen["units"]["0000"]["attempts"][0]["descriptor"]["parameters"]
    # A restarted app whose catalog derives different fields must replay the frozen ones.
    monkeypatch.setattr(
        providers, "transcription_fields", lambda settings: {**fields, "language": "xx"}
    )
    run(client, job)
    assert calls[1] == {"request_fields": fields}
    attempts = store.job(job["id"])["params"]["provider_artifact_capture"]["units"]["0000"][
        "attempts"
    ]
    assert attempts[1]["descriptor"]["parameters"] == fields


def test_preserved_output_without_observation_stays_an_explicit_gap(
    client, wav_bytes, monkeypatch, library_lease
):
    store, recording, job = fresh_api_job(client, wav_bytes)
    calls = []

    def transcribe(path, settings, key):
        calls.append(path)
        return copy.deepcopy(OUTPUT)

    def crash(*args):
        raise OSError("Synthetic crash after the response was preserved")

    monkeypatch.setattr(providers, "transcribe_file", transcribe)
    monkeypatch.setattr(provider_capture, "observe_response", crash)
    with pytest.raises(OSError):
        run(client, job)
    folder = store.directory(recording["id"]) / "chunks" / job["id"]
    assert (folder / "response-0000.json").is_file()
    assert store.job(job["id"])["chunks"] == []
    monkeypatch.setattr(
        provider_capture, "observe_response", lambda *a: pytest.fail("never backfilled")
    )
    run(client, job)
    assert len(calls) == 1  # The preserved output is reused without a new request.
    unit = store.job(job["id"])["params"]["provider_artifact_capture"]["units"]["0000"]
    assert unit["attempts"][0]["response"] is None
    assert unit["cached_part_sha256"] == provider_capture.part_hash(
        store.job(job["id"])["chunks"][0]
    )
    result, version, context = creation(store, recording)
    required = provider_capture.inventory(
        context["provider_artifacts"],
        recording_id=recording["id"],
        job_id=job["id"],
        creation={
            "result_id": result["id"],
            "version_id": version["id"],
            "payload_sha256": version["payload_sha256"],
        },
    )
    assert [item["filename"] for item in required] == ["request-0000-0000.json"]


def test_legacy_and_unmarked_jobs_keep_exact_earlier_behavior(
    client, credentials, wav_bytes, monkeypatch, library_lease
):
    store = client.app.state.store
    project = store.create_project("Research")
    recording = store.create_recording(project["id"], "interview.wav")
    store.directory(recording["id"]).mkdir(parents=True)
    store.execute(
        "UPDATE recordings SET status='ready',duration=900 WHERE id=?", (recording["id"],)
    )
    credentials.keys[OPENAI_URL] = "synthetic-key"
    legacy = store.add_job(
        recording["id"], "transcribe", {"settings": Settings().model_dump(), "revision": 0}
    )
    folder = store.directory(recording["id"]) / "test-chunks"
    folder.mkdir()
    prepare = media.prepare_chunks
    monkeypatch.setattr(
        media, "prepare_chunks", lambda *args: (folder, [{"file": "a.mp3", "offset": 0}])
    )
    monkeypatch.setattr(providers, "transcribe_file", lambda *a: copy.deepcopy(OUTPUT))
    JobQueue(store, credentials).process(legacy["id"])
    monkeypatch.setattr(media, "prepare_chunks", prepare)
    params = store.job(legacy["id"])["params"]
    assert not {"provider_artifact_policy", "provider_artifact_capture"} & params.keys()
    _, _, context = creation(store, recording)
    assert "provider_artifacts" not in context
    assert not list(folder.glob("request-*"))

    # A fresh-shaped job whose params never received the policy is not marked later.
    store2, recording2, job = fresh_api_job(client, wav_bytes)
    unmarked = {k: v for k, v in job["params"].items() if k != "provider_artifact_policy"}
    store2.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(unmarked), job["id"]))
    run(client, job)
    assert "provider_artifact_capture" not in store2.job(job["id"])["params"]
    assert not list((store2.directory(recording2["id"]) / "chunks" / job["id"]).glob("request-*"))
    _, _, context = creation(store2, recording2)
    assert "provider_artifacts" not in context


def test_report_projects_metadata_only_aliases_and_explicit_gaps(
    client, wav_bytes, monkeypatch, library_lease
):
    store, recording, job = fresh_api_job(client, wav_bytes)
    monkeypatch.setattr(providers, "transcribe_file", lambda *a: copy.deepcopy(OUTPUT))
    run(client, job)
    result, version, context = creation(store, recording)
    data = selected_view_report(store, recording["project_id"])
    with ZipFile(io.BytesIO(data)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        methods = archive.read("methods.md").decode("utf-8")
    [item] = [entry for entry in manifest["versions"] if "provider_artifacts" in entry]
    evidence = item["provider_artifacts"]
    encoded = json.dumps(evidence)
    descriptor = context["provider_artifacts"]["units"]["0000"]["attempts"][0]["descriptor"]
    for private in (
        recording["id"],
        job["id"],
        result["id"],
        version["id"],
        descriptor["generation"],
        descriptor["endpoint_sha256"],
        "127.0.0.1",
        "Synthetic words",
        str(Path(store.directory(recording["id"]))),
    ):
        assert private not in encoded
    [part] = evidence["parts"]
    assert part["part"] == "part-0001" and part["output_observation"] == "captured"
    [attempt] = part["attempts"]
    request = store.directory(recording["id"]) / "chunks" / job["id"] / "request-0000-0000.json"
    assert attempt["request"]["sha256"] == sha(request.read_bytes())
    assert evidence["wire_response"] == evidence["environment"] == "not_captured"
    assert evidence["retained_files"] == "not_checked"
    assert evidence["assembly_sha256"] == version["payload_sha256"]
    assert "API part-0001 attempt 1" in methods
    assert any("normalized SDK responses" in gap for gap in item["provenance_gaps"])

    # The historical projection refuses a version whose evidence differs from its job row.
    tampered = copy.deepcopy(context["provider_artifacts"])
    tampered["units"]["0000"]["attempts"][0]["part_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        provider_capture.public_projection(
            tampered,
            parent=store.one("SELECT * FROM jobs WHERE id=?", (job["id"],)),
            recording_id=recording["id"],
            creation=tampered["creation"],
        )


@pytest.mark.parametrize("damage", [None, "request"])
def test_portable_capture_retains_and_verifies_api_observations(
    client, wav_bytes, monkeypatch, library_lease, tmp_path, damage
):
    store, recording, job = fresh_api_job(client, wav_bytes)
    monkeypatch.setattr(providers, "transcribe_file", lambda *a: copy.deepcopy(OUTPUT))
    run(client, job)
    store.execute("UPDATE jobs SET state='completed' WHERE state IN ('queued','running')")
    folder = store.directory(recording["id"]) / "chunks" / job["id"]
    if damage == "request":
        path = folder / "request-0000-0000.json"
        path.write_bytes(path.read_bytes().replace(b'"attempt":0', b'"attempt": 0'))
        with pytest.raises(ValueError):
            portable_capture.capture_library(
                store,
                client.app.state.queue,
                tmp_path / "packages",
                gate=portable_capture.CaptureGate(),
            )
        return
    result = portable_capture.capture_library(
        store, client.app.state.queue, tmp_path / "packages", gate=portable_capture.CaptureGate()
    )
    manifest = json.loads((Path(result["folder"]) / "manifest.json").read_text(encoding="utf-8"))
    roles = {
        item["relative_path"]: item["role"]
        for item in manifest["files"]
        if item["recording_id"] == recording["id"]
    }
    prefix = "chunks/" + job["id"] + "/"
    assert roles[prefix + "request-0000-0000.json"] == "processing_input"
    assert roles[prefix + "response-0000.json"] == "provider_response"
    assert not [gap for gap in manifest["legacy_gaps"] if gap["job_id"] == job["id"]]


def test_sdk_sends_exact_frozen_fields_and_refuses_another_model(tmp_path):
    import httpx

    path = tmp_path / "part.mp3"
    path.write_bytes(b"synthetic audio")
    settings = Settings(base_url="https://example.test/v1", model="synthetic", capability="json")
    frozen = {"model": "synthetic", "language": "da", "response_format": "json"}
    bodies = []

    def reply(request):
        bodies.append(request.read())
        return httpx.Response(200, json={"text": "Hej."})

    client = httpx.Client(transport=httpx.MockTransport(reply))
    providers.transcribe_file(path, settings, SECRET, client, request_fields=frozen)
    [body] = bodies
    assert b'name="language"\r\n\r\nda' in body and b"json" in body
    assert SECRET.encode() not in body  # The key travels only in the Authorization header.
    with pytest.raises(ValueError, match="model changed"):
        providers.transcribe_file(
            path, settings, SECRET, client, request_fields={**frozen, "model": "other"}
        )
    assert len(bodies) == 1
