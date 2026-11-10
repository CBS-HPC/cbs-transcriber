"""Bound configured-language provenance, without models, inference or network."""

import copy
import hashlib
import io
import json
import sys
import threading
import wave
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from test_working_reports import mark_active_latest

from cbs_transcriber import enhancement, history, research_reports
from cbs_transcriber import enhancement_worker as worker
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import edit_segment, normalize_segments


def stage_files(folder, options, selected=None, pin=None):
    folder.mkdir(parents=True, exist_ok=True)
    with wave.open(str(folder / "audio.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 48000)
    request = {
        "passages": [
            {key: passage[key] for key in ("id", "start", "end")} | {"text": ""}
            for passage in selected["segments"]
        ]
        if selected
        else [],
        "options": options.model_dump(exclude={"runtime_python", "prepare_runtime"})
        | {"duration": 3.0, "whole_recording": False},
    }
    worker.write_json(folder / "request.json", request)
    repo, revision = pin or worker.MODELS[worker.WHISPER_MODELS[options.whisper_model]]
    # Construct independently of the binding/validation helper under test.
    receipts = []
    windows = (
        [
            {
                "scope": "passage",
                "passage_id": passage["id"],
                "window": {
                    "start": int(max(0.0, passage["start"] - 0.5) * 16000) / 16000,
                    "end": min(48000, int(min(3.0, passage["end"] + 0.5) * 16000)) / 16000,
                    "timebase": "prepared_audio",
                },
            }
            for passage in selected["segments"]
        ]
        if selected
        else [
            {
                "scope": "recording",
                "window": {"start": 0.0, "end": 3.0, "timebase": "prepared_audio"},
            }
        ]
    )
    for window in windows:
        receipts.append(
            {
                "schema": 1,
                "requested": options.language,
                "effective": options.language,
                "origin": "configured",
                "probability": None,
                "reported_language": options.language,
                **window,
                "input_sha256": hashlib.sha256((folder / "audio.wav").read_bytes()).hexdigest(),
                "request_sha256": hashlib.sha256(
                    (folder / "request.json").read_bytes()
                ).hexdigest(),
                "model": {"choice": options.whisper_model, "repo": repo, "revision": revision},
                "engine": {"name": "faster-whisper", "version": "1.2.1"},
            }
        )
    output = (
        {
            "passages": [
                {"id": passage["id"], "text": "Hej verden."} for passage in selected["segments"]
            ]
        }
        if selected
        else {"segments": [{"text": "Hej verden.", "start": 0.75, "end": 1.5}]}
    )
    output["language_evidence"] = receipts
    filename = "retranscription.json" if options.retranscribe else "transcription.json"
    worker.write_json(folder / filename, output)
    return request, receipts, filename


def options_for(retranscribe=False):
    return enhancement.EnhancementOptions(
        device="cpu", whisper_model="small", transcribe=not retranscribe, retranscribe=retranscribe
    )


def selected_passages():
    return {
        "segments": [
            {"id": "PRIVATE_PASSAGE_ID", "start": 0.75004, "end": 1.50004, "text": "Private"}
        ]
    }


@pytest.mark.parametrize("retranscribe", [False, True])
def test_optional_receipts_bind_exact_request_audio_options_and_sample_windows(
    tmp_path, retranscribe
):
    options = options_for(retranscribe)
    selected = selected_passages() if retranscribe else None
    request, receipts, filename = stage_files(tmp_path, options, selected)
    unchanged = {name: (tmp_path / name).read_bytes() for name in (filename, "request.json")}
    captured = enhancement.asr_language_evidence(tmp_path, options, selected=selected, duration=3.0)
    assert captured == {"language_evidence": receipts}
    assert {name: (tmp_path / name).read_bytes() for name in unchanged} == unchanged
    assert json.loads((tmp_path / "request.json").read_bytes()) == request
    assert captured["language_evidence"][0]["probability"] is None
    if retranscribe:
        assert receipts[0]["window"]["start"] == 0.25
        assert receipts[0]["window"]["end"] == 2.0


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("input_sha256",), "a" * 64),
        (("request_sha256",), "b" * 64),
        (("request_sha256",), "not-a-hash"),
        (("probability",), 1.0),
        (("requested",), "en"),
        (("effective",), "en"),
        (("origin",), "detected"),
        (("model", "choice"), "medium"),
        (("model", "repo"), "C:/PRIVATE_MODEL"),
        (("model", "revision"), "main"),
        (("window", "start"), 0.5),
        (("window", "end"), float("inf")),
        (("window", "timebase"), "original_media"),
        (("window", "private_path"), "C:/PRIVATE_AUDIO"),
        (("engine", "api_key"), "PRIVATE_TOKEN"),
    ],
)
def test_malformed_or_unbound_receipts_cannot_be_attached(tmp_path, path, value):
    options = options_for()
    _, receipts, filename = stage_files(tmp_path, options)
    receipt = receipts[0]
    for key in path[:-1]:
        receipt = receipt[key]
    receipt[path[-1]] = value
    output = json.loads((tmp_path / filename).read_bytes())
    output["language_evidence"] = receipts
    # Deliberately permit nonfinite JSON here to exercise the reader's rejection too.
    (tmp_path / filename).write_text(json.dumps(output), encoding="utf-8")
    with pytest.raises(ValueError):
        enhancement.asr_language_evidence(tmp_path, options, duration=3.0)


@pytest.mark.parametrize("changed", ["request", "audio", "options", "duration", "passage"])
def test_mutated_request_input_and_frozen_context_rejected(tmp_path, changed):
    options = options_for(True)
    selected = selected_passages()
    request, _, _ = stage_files(tmp_path, options, selected)
    duration = 3.0
    if changed == "request":
        request["options"]["language"] = "en"
        worker.write_json(tmp_path / "request.json", request)
    elif changed == "audio":
        with (tmp_path / "audio.wav").open("r+b") as stream:
            stream.seek(-2, 2)
            stream.write(b"\x01\x00")
    elif changed == "options":
        options = options.model_copy(update={"min_score": 0.8})
    elif changed == "duration":
        duration = 2.9
    else:
        selected["segments"][0]["id"] = "other-passage"
    with pytest.raises(ValueError):
        enhancement.asr_language_evidence(tmp_path, options, selected=selected, duration=duration)


def test_frozen_batch_model_revisions_override_current_registry(tmp_path, monkeypatch):
    options = options_for()
    pin = ("historical/faster-whisper-small", "a" * 40)
    _, receipts, _ = stage_files(tmp_path, options, pin=pin)
    job = {
        "params": {
            "ucloud_api": {
                "batch_plan": {"identity": {"model_revisions": {"whisper/small": list(pin)}}}
            }
        }
    }
    monkeypatch.setitem(worker.MODELS, "coarse", ("current/faster-whisper-small", "b" * 40))
    captured = enhancement.asr_language_evidence(tmp_path, options, job=job, duration=3.0)
    assert captured["language_evidence"] == receipts
    assert enhancement.asr_model_revisions(["coarse", "da"], captured)["coarse"] == {
        "repo": pin[0],
        "revision": pin[1],
    }
    # Older local stages have no frozen model map; their choice and exact hashes still bind.
    assert enhancement.asr_language_evidence(tmp_path, options) == captured
    job["params"]["ucloud_api"]["batch_plan"]["identity"]["model_revisions"]["coarse"] = [
        "current/faster-whisper-small",
        "b" * 40,
    ]
    with pytest.raises(ValueError, match="inconsistent"):
        enhancement.asr_language_evidence(tmp_path, options, job=job)


@pytest.mark.parametrize("revisions", [{}, {"coarse": ["another/model", "c" * 40]}])
def test_missing_or_mismatched_frozen_pin_does_not_relabel_cached_asr(tmp_path, revisions):
    options = options_for()
    stage_files(tmp_path, options)
    job = {"params": {"ucloud_api": {"batch_plan": {"identity": {"model_revisions": revisions}}}}}
    with pytest.raises(ValueError):
        enhancement.asr_language_evidence(tmp_path, options, job=job)


@pytest.mark.parametrize("retranscribe", [False, True])
def test_legacy_stage_has_no_invented_evidence_or_new_input_requirements(
    tmp_path, monkeypatch, retranscribe
):
    options = options_for(retranscribe)
    name = "retranscription.json" if retranscribe else "transcription.json"
    raw = b'{"passages": []}' if retranscribe else b'{"segments": []}'
    (tmp_path / name).write_bytes(raw)

    def refuse(*args, **kwargs):
        pytest.fail("Legacy finalization must not inspect an input/runtime/model")

    monkeypatch.setattr(worker, "language_binding", refuse)
    monkeypatch.setattr(enhancement, "check_runtime", refuse)
    monkeypatch.setattr(worker.importlib.metadata, "version", refuse)
    assert enhancement.asr_language_evidence(tmp_path, options) == {}
    assert (tmp_path / name).read_bytes() == raw
    assert history.processing_metadata({"engine": "whisperx"}) == {"engine": "whisperx"}


@pytest.mark.parametrize("retranscribe", [False, True])
@pytest.mark.parametrize("valid_receipt", [False, True])
def test_producers_preserve_reviewed_input_lineage_and_add_only_validated_evidence(
    tmp_path, monkeypatch, retranscribe, valid_receipt
):
    store = Store(tmp_path)
    project = store.create_project("PRIVATE_PROJECT")
    recording = store.create_recording(project["id"], "PRIVATE_AUDIO.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET duration=3,status='ready' WHERE id=?", (rid,))
    initial = store.add_result(
        rid,
        normalize_segments(
            [
                {"text": "Keep original.", "start": 0, "end": 0.5, "speaker": "A"},
                {"text": "Review this.", "start": 0.75, "end": 1.5, "speaker": "B"},
                {"text": "Keep final.", "start": 2, "end": 3, "speaker": "A"},
            ]
        ),
        {"B": "PRIVATE_SPEAKER_NAME"},
        "Reviewed",
        {"kind": "import"},
    )
    reviewed = copy.deepcopy(initial["segments"])
    reviewed[1] = edit_segment(reviewed[1], "free", "Exact reviewed wording æøå.")
    recording = store.save_transcript(rid, reviewed, initial["speakers"], 1)
    head = store.result_versions(rid, initial["id"])[0]
    options = options_for(retranscribe)
    params = {"enhancement": options.model_dump()}
    if retranscribe:
        job = store.enqueue_enhancement(
            rid,
            initial["id"],
            recording["revision"],
            params | {"retranscription": True, "segment_ids": ["1"]},
        )
    else:
        job = store.add_job(rid, "enhance", params | {"transcription": True})
    frozen_params = copy.deepcopy(job["params"])
    saved_receipts = []
    snapshots = {}
    # A retained older local receipt remains usable if package pins were updated later.
    pin = ("historical/faster-whisper-small", "e" * 40)

    def complete(queue, frozen_job, media, settings, selected, folder, *, merged=None):
        _, receipts, name = stage_files(folder, settings, selected, pin=pin)
        if not valid_receipt:
            output = json.loads((folder / name).read_bytes())
            output["language_evidence"][0]["input_sha256"] = "0" * 64
            worker.write_json(folder / name, output)
        saved_receipts.extend(receipts)
        snapshots.update(
            {path: path.read_bytes() for path in (folder / "request.json", folder / name)}
        )
        worker.write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": "1" if selected else "0",
                        "words": [
                            {"word": "Hej", "start": 0.75, "end": 1.0, "score": 0.9},
                            {"word": "verden.", "start": 1.0, "end": 1.5, "score": 0.9},
                        ],
                    }
                ]
            },
        )
        return merged()

    monkeypatch.setattr(enhancement, "enhance_window", complete)
    queue = SimpleNamespace(store=store, cancel_event=threading.Event())
    run = enhancement.run_retranscription if retranscribe else enhancement.run_transcription
    if not valid_receipt:
        with pytest.raises(ValueError, match="another prepared input"):
            run(queue, job, recording, options)
        assert len(store.results(rid)) == 1
        assert store.result_versions(rid, initial["id"])[0] == head
        assert store.result(rid, initial["id"])["segments"] == reviewed
        assert store.job(job["id"])["params"] == frozen_params
        return
    run(queue, job, recording, options)
    new_id = next(result["id"] for result in store.results(rid) if result["id"] != initial["id"])
    new = store.result(rid, new_id)
    assert new["source"]["language_evidence"] == saved_receipts
    assert new["source"]["model_revisions"]["coarse"] == {"repo": pin[0], "revision": pin[1]}
    version = store.result_versions(rid, new_id)[0]
    assert version["context"]["processing"]["language_evidence"] == saved_receipts
    assert store.result(rid, initial["id"])["segments"] == reviewed
    assert store.recording(rid)["active_transcript_id"] == initial["id"]
    assert store.job(job["id"])["params"] == frozen_params
    assert job["params"] == frozen_params
    assert options == options_for(retranscribe)
    assert all(path.read_bytes() == content for path, content in snapshots.items())
    if retranscribe:
        assert version["source_version_id"] == head["id"]
        assert version["context"]["source_payload_sha256"] == head["payload_sha256"]
        assert new["segments"][0] == reviewed[0] and new["segments"][2] == reviewed[2]
        assert new["segments"][1]["text"] == "Hej verden."
        assert new["segments"][1]["speaker"] == "B"
        assert new["speakers"] == initial["speakers"]
        assert new["source"]["segment_ids"] == ["1"]
    else:
        assert version["source_version_id"] is None


def test_history_and_report_preserve_historical_evidence_without_runtime_queries(
    tmp_path, monkeypatch
):
    options = options_for(True)
    _, receipts, _ = stage_files(
        tmp_path / "stage", options, selected_passages(), pin=("historical/asr", "f" * 40)
    )
    receipts[0]["engine"]["version"] = "0.10.0"

    def refuse(*args, **kwargs):
        pytest.fail("History/report validation must not query the installed inference runtime")

    monkeypatch.setattr(worker.importlib.metadata, "version", refuse)
    monkeypatch.setitem(worker.MODELS, "coarse", ("current/different-model", "d" * 40))
    source = {
        "kind": "provider",
        "provider_type": "whisperx",
        "engine": "whisperx",
        "language": "da",
        "operation": "retranscription",
        "segment_ids": ["PRIVATE_PASSAGE_ID"],
        "language_evidence": receipts,
        "api_key": "PRIVATE_TOKEN",
        "ucloud_server": "https://PRIVATE_ENDPOINT",
        "runtime_python": "C:/PRIVATE_PATH/python.exe",
    }
    metadata = history.processing_metadata(source)
    assert metadata["language_evidence"] == receipts
    assert metadata["language_evidence"] is not receipts
    metadata["language_evidence"][0]["engine"]["version"] = None
    assert receipts[0]["engine"]["version"] == "0.10.0"
    store = Store(tmp_path / "library")
    project = store.create_project("PRIVATE_PROJECT_NAME")
    recording = store.create_recording(project["id"], "PRIVATE_RECORDING.wav")
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "PRIVATE_TEXT", "start": 0.75, "end": 1.5}]),
        {},
        "PRIVATE_LABEL",
        source,
    )
    before = store.all("SELECT * FROM result_versions")
    mark_active_latest(store, project["id"])
    with ZipFile(io.BytesIO(research_reports.project_report(store, project["id"]))) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files["manifest.json"])
    captured = manifest["versions"][0]["processing"]["language_evidence"]
    assert captured == [{key: value for key, value in receipts[0].items() if key != "passage_id"}]
    assert captured[0]["probability"] is None
    assert captured[0]["engine"]["version"] == "0.10.0"
    methods = files["methods.md"].decode()
    assert "configured decoding, not measured spoken-language detection" in methods
    assert "ASR model=historical/asr@" + "f" * 40 in methods
    assert "observed package version=0.10.0" in methods
    combined = b"\n".join(files.values()).decode()
    for private in ("PRIVATE_", project["id"], recording["id"], result["id"]):
        assert private not in combined
    assert store.all("SELECT * FROM result_versions") == before


@pytest.mark.parametrize("nested", ["model", "engine", "window"])
def test_history_and_report_allowlists_reject_unknown_nested_receipt_fields(tmp_path, nested):
    _, receipts, _ = stage_files(tmp_path, options_for())
    receipts[0][nested]["api_key"] = "PRIVATE_TOKEN"
    source = {"engine": "whisperx", "language_evidence": receipts}
    assert "language_evidence" not in history.processing_metadata(source)
    # Revalidate at the export boundary too, including unvalidated legacy context.
    assert "language_evidence" not in research_reports._processing(source)
    assert "PRIVATE_TOKEN" not in json.dumps(history.processing_metadata(source))
    assert source["language_evidence"][0][nested]["api_key"] == "PRIVATE_TOKEN"


def test_history_and_report_omit_unbounded_integer_window_metadata(tmp_path):
    _, receipts, _ = stage_files(tmp_path, options_for())
    receipts[0]["window"]["end"] = 10**400
    source = {"engine": "whisperx", "language_evidence": receipts}
    assert "language_evidence" not in history.processing_metadata(source)
    assert "language_evidence" not in research_reports._processing(source)


@pytest.mark.parametrize("asr", [False, True])
def test_missing_language_evidence_gap_applies_only_to_asr(tmp_path, asr):
    store = Store(tmp_path)
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.add_result(
        recording["id"],
        normalize_segments([{"text": "Hej.", "start": 0, "end": 1}]),
        {},
        "Synthetic",
        {"kind": "provider", "engine": "whisperx"} | ({"provider_type": "whisperx"} if asr else {}),
    )
    mark_active_latest(store, project["id"])
    with ZipFile(io.BytesIO(research_reports.project_report(store, project["id"]))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    gap = "Bound language processing evidence was not captured."
    assert (gap in manifest["versions"][0]["provenance_gaps"]) is asr


def test_actual_worker_receipt_survives_result_history_and_metadata_report(tmp_path, monkeypatch):
    """Exercise the writer/producer/export contract with real decoded samples and stub ASR."""
    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.execute("UPDATE recordings SET duration=3 WHERE id=?", (recording["id"],))
    recording = store.recording(recording["id"])
    options = options_for()
    job = store.add_job(
        recording["id"], "enhance", {"enhancement": options.model_dump(), "transcription": True}
    )
    cache = tmp_path / "model"
    cache.mkdir()
    (cache / "tokenizer.json").write_text("{}", encoding="utf-8")
    calls, captured = [], []

    class StubModel:
        def __init__(self, location, **kwargs):
            assert location == str(cache) and kwargs["local_files_only"] is True

        def transcribe(self, audio, **kwargs):
            calls.append((len(audio), kwargs["language"]))
            return iter(
                [SimpleNamespace(text="Hej verden.", start=0.75, end=1.5)]
            ), SimpleNamespace(language="da", language_probability=1.0)

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=StubModel))
    monkeypatch.setattr(worker.importlib.metadata, "version", lambda _: "1.2.1")

    def complete(queue, frozen_job, media, settings, selected, folder, *, merged):
        # Reuse the synthetic input builder, then let the real worker produce the stage.
        request, _, name = stage_files(folder, settings)
        (folder / name).unlink()
        audio = worker.audio_data(folder)
        try:
            rows = worker.transcribe_audio(
                audio, request["options"], folder, lambda _: str(cache), lambda *args: None
            )
        finally:
            del audio
        captured.extend(json.loads((folder / name).read_bytes())["language_evidence"])
        worker.write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": "0",
                        "words": [
                            {"word": "Hej", "start": 0.75, "end": 1.0, "score": 0.9},
                            {"word": "verden.", "start": 1.0, "end": 1.5, "score": 0.9},
                        ],
                    }
                ]
            },
        )
        assert rows[0]["text"] == "Hej verden."
        return merged()

    monkeypatch.setattr(enhancement, "enhance_window", complete)
    queue = SimpleNamespace(store=store, cancel_event=threading.Event())
    enhancement.run_transcription(queue, job, recording, options)
    result = store.result(recording["id"], store.results(recording["id"])[0]["id"])
    assert calls == [(48000, "da")]
    assert result["source"]["language_evidence"] == captured
    version = store.result_versions(recording["id"], result["id"])[0]
    assert version["context"]["processing"]["language_evidence"] == captured
    mark_active_latest(store, project["id"])
    with ZipFile(io.BytesIO(research_reports.project_report(store, project["id"]))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["versions"][0]["processing"]["language_evidence"] == captured
        assert (
            "configured decoding, not measured spoken-language detection"
            in archive.read("methods.md").decode()
        )
    assert captured[0]["origin"] == "configured" and captured[0]["probability"] is None
    assert result["segments"][0]["text"] == "Hej verden."
