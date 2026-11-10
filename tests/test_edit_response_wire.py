"""Completed manual edits retain the existing JSON wire contract without a second walk."""

from copy import deepcopy

import fastapi.routing
import pytest

from cbs_transcriber import app as app_module
from cbs_transcriber import passage_undo
from cbs_transcriber.transcripts import normalize_segments

OPERATIONS = (
    "word",
    "mapped_word",
    "timed_word",
    "restore",
    "free",
    "free-v1",
    "free-v2",
    "rename",
    "assign",
    "merge",
    "split",
    "delete",
    "undo-merge",
    "undo-split",
)


@pytest.fixture
def native_recording(client):
    store = client.app.state.store
    project = store.create_project("Synthetic wire comparison")
    recording = store.create_recording(project["id"], "synthetic.wav")
    segments = normalize_segments(
        [
            {
                "text": "Hej København igen.",
                "start": 0,
                "end": 2,
                "speaker": "A",
                "words": [
                    {"word": "Hej", "start": 0, "end": 0.4},
                    {"word": "København", "start": 0.5, "end": 1.2},
                    {"word": "igen.", "start": 1.3, "end": 1.8},
                ],
            },
            {"text": "Andet afsnit.", "start": 3, "end": 4, "speaker": "A"},
            {"text": "Third passage.", "start": 5, "end": 6, "speaker": "B"},
        ]
    )
    segments[-1]["literal_metadata"] = {
        "unicode": 'æøå café 👩‍🔬 <literal>\n"quoted"',
        "values": [None, True, False, -0.0, 0, 1.23456789012345, 2**70],
    }
    store.add_result(
        recording["id"], segments, {"A": "Åse", "B": "Benny"}, "Saved", {}, auto_first=True
    )
    payload = store.recording(recording["id"])
    payload["snapshot_error"] = "Synthetic snapshot warning · æøå"
    return store, recording["id"], payload


def request_for(operation, rid, current):
    prefix = f"/api/recordings/{rid}"
    body = {"revision": current["revision"]}
    if operation in {"merge", "split", "delete", "undo-merge", "undo-split"}:
        body["result_id"] = current["active_transcript_id"]
        if operation == "merge":
            body["direction"] = "down"
        if operation == "split":
            body["cursor"] = 3
        return "POST", prefix + "/segments/0/" + operation, body
    if operation == "rename":
        return "PATCH", prefix + "/speakers", {**body, "speaker": "A", "name": "Åse Øst"}
    if operation == "assign":
        return "PATCH", prefix + "/segments/0/speaker", {**body, "speaker_id": "B"}
    suffix = ""
    if operation in {"free-v1", "free-v2"}:
        suffix = "/edit-v" + operation[-1]
    mode = "free" if operation.startswith("free") else operation
    body["mode"] = mode
    if mode == "free":
        body["text"] = current["segments"][0]["text"]
        if operation == "free-v1":
            body["realign"] = False
    elif mode != "restore":
        body.update(word_index=0, text="Goddag")
        if mode == "timed_word":
            body.update(start=0.05, end=0.45)
    return "PATCH", prefix + "/segments/0" + suffix, body


def mock_completed_writes(monkeypatch, store, payload):
    def saved(*args, **kwargs):
        return deepcopy(payload)

    monkeypatch.setattr(store, "save_transcript", saved)
    monkeypatch.setattr(passage_undo, "apply", saved)
    monkeypatch.setattr(passage_undo, "apply_split", saved)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_manual_mutation_uses_the_completed_native_projection_directly(
    client, native_recording, monkeypatch, operation
):
    store, rid, payload = native_recording
    mock_completed_writes(monkeypatch, store, payload)
    original, calls = fastapi.routing.serialize_response, []

    async def counted(*args, **kwargs):
        calls.append(kwargs.get("response_content"))
        return await original(*args, **kwargs)

    monkeypatch.setattr(fastapi.routing, "serialize_response", counted)
    method, url, body = request_for(operation, rid, payload)
    response = client.request(method, url, json=body)
    assert response.status_code == 200, response.text
    assert response.json() == payload
    assert calls == []  # No second recursive FastAPI encoder walk of the full recording.


@pytest.mark.parametrize("operation", OPERATIONS)
def test_manual_mutation_body_status_and_security_headers_match_the_legacy_adapter(
    client, native_recording, monkeypatch, operation
):
    store, rid, payload = native_recording
    mock_completed_writes(monkeypatch, store, payload)
    direct = app_module.json_response
    method, url, body = request_for(operation, rid, payload)
    monkeypatch.setattr(app_module, "json_response", lambda value: value)
    legacy = client.request(method, url, json=body)
    monkeypatch.setattr(app_module, "json_response", direct)
    current = client.request(method, url, json=body)
    assert current.status_code == legacy.status_code == 200
    assert current.content == legacy.content
    assert dict(current.headers) == dict(legacy.headers)
    assert current.headers["cache-control"] == "no-store"
    assert current.headers["content-type"] == "application/json"


@pytest.mark.parametrize("operation", ["free-v2", "rename", "merge", "undo-merge", "undo-split"])
@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_response_values_keep_the_existing_global_error_boundary(
    client, native_recording, monkeypatch, operation, number
):
    store, rid, payload = native_recording
    payload["transcripts"][0]["source"]["legacy_nonfinite_metadata"] = number
    mock_completed_writes(monkeypatch, store, payload)
    direct = app_module.json_response
    method, url, body = request_for(operation, rid, payload)
    monkeypatch.setattr(app_module, "json_response", lambda value: value)
    legacy = client.request(method, url, json=body)
    monkeypatch.setattr(app_module, "json_response", direct)
    current = client.request(method, url, json=body)
    assert current.status_code == legacy.status_code == 400
    assert current.content == legacy.content
    assert dict(current.headers) == dict(legacy.headers)
    assert "Out of range float" in current.json()["detail"]


def test_warning_is_added_before_the_completed_projection_is_encoded(
    client, native_recording, monkeypatch
):
    store, rid, payload = native_recording
    warning = "Synthetic retained timing warning · æøå"
    mock_completed_writes(monkeypatch, store, payload)
    monkeypatch.setattr(
        app_module,
        "edit_text_retaining_timing",
        lambda segment, text: (deepcopy(segment), warning),
    )
    direct = app_module.json_response
    method, url, body = request_for("free-v2", rid, payload)
    monkeypatch.setattr(app_module, "json_response", lambda value: value)
    legacy = client.request(method, url, json=body)
    monkeypatch.setattr(app_module, "json_response", direct)
    current = client.request(method, url, json=body)
    assert current.status_code == legacy.status_code == 200
    assert current.content == legacy.content
    assert current.json()["timing_retention_warning"] == warning


@pytest.mark.parametrize(
    "hidden_number",
    [None, float("nan"), float("inf"), -float("inf"), "\ud800", object()],
    ids=["ordinary", "nan", "inf", "minus-inf", "surrogate", "unencodable-object"],
)
def test_json_native_metadata_keeps_the_legacy_nested_sa_key_filter(
    client, native_recording, monkeypatch, hidden_number
):
    store, rid, payload = native_recording
    payload["transcripts"][0]["source"]["metadata"] = {
        "_sa_internal": {"not_public": "Synthetic hidden metadata", "number": hidden_number},
        "visible": "_sa is a literal value, not a hidden key",
        "_SA_visible": "Case-sensitive literal metadata",
        "nested": [{"_sailing": "Also omitted by the legacy prefix rule", "kept": True}],
    }
    mock_completed_writes(monkeypatch, store, payload)
    direct = app_module.json_response
    method, url, body = request_for("free-v2", rid, payload)
    monkeypatch.setattr(app_module, "json_response", lambda value: value)
    legacy = client.request(method, url, json=body)
    monkeypatch.setattr(app_module, "json_response", direct)
    current = client.request(method, url, json=body)
    assert current.status_code == legacy.status_code == 200
    assert current.content == legacy.content
    assert dict(current.headers) == dict(legacy.headers)
    metadata = current.json()["transcripts"][0]["source"]["metadata"]
    assert "_sa_internal" not in metadata and metadata["nested"] == [{"kept": True}]
    assert metadata["visible"].startswith("_sa") and "_SA_visible" in metadata


def test_deep_hidden_legacy_metadata_is_omitted_even_when_native_rendering_succeeds():
    from fastapi.encoders import jsonable_encoder
    from starlette.responses import JSONResponse

    nested = {}
    for _ in range(1200):
        nested = {"child": nested}
    hidden = {"_sa_hidden": nested, "kept": [True, None, "æøå"]}
    assert app_module.json_response(hidden).body == JSONResponse(jsonable_encoder(hidden)).body


def test_initial_render_depth_failure_uses_the_legacy_path_and_does_not_swallow_errors(monkeypatch):
    from fastapi.encoders import jsonable_encoder
    from starlette.responses import JSONResponse

    def native_render(value):
        if "_sa_hidden" in value or "visible" in value:
            raise RecursionError("Synthetic native-render depth limit")
        return JSONResponse(value)

    monkeypatch.setattr(app_module, "JSONResponse", native_render)
    hidden = {"_sa_hidden": {"deep": "synthetic"}, "kept": [True, None, "æøå"]}
    assert app_module.json_response(hidden).body == JSONResponse(jsonable_encoder(hidden)).body
    with pytest.raises(RecursionError):
        app_module.json_response({"visible": {"deep": "synthetic"}})


def test_real_writer_output_and_stale_failure_keep_the_legacy_wire_contract(
    client, native_recording, monkeypatch
):
    from fastapi.encoders import jsonable_encoder
    from starlette.responses import JSONResponse

    store, rid, payload = native_recording
    original, written = store.save_transcript, []

    def capture(*args, **kwargs):
        value = original(*args, **kwargs)
        written.append(value)
        return value

    monkeypatch.setattr(store, "save_transcript", capture)
    method, url, body = request_for("free-v2", rid, payload)
    body["text"] = "Goddag København igen."
    response = client.request(method, url, json=body)
    assert response.status_code == 200, response.text
    assert response.content == JSONResponse(jsonable_encoder(written[-1])).body
    current = store.recording(rid)
    assert response.json() == current
    direct = app_module.json_response
    monkeypatch.setattr(app_module, "json_response", lambda value: value)
    legacy = client.request(method, url, json=body)
    monkeypatch.setattr(app_module, "json_response", direct)
    stale = client.request(method, url, json=body)
    assert stale.status_code == legacy.status_code == 409
    assert stale.content == legacy.content
    assert dict(stale.headers) == dict(legacy.headers)
