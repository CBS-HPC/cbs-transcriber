"""Research reports expose bounded, aliased original-audio observations only."""

import copy
import io
import json
from contextlib import contextmanager
from zipfile import ZipFile

import pytest
from report_support import selected_view_report

from cbs_transcriber import audio_inputs, research_reports
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


def source_snapshot(recording_id):
    return {
        "policy": "original-v1",
        "recording_id": recording_id,
        "format": "wav",
        "sha256": "a" * 64,
        "bytes": 96044,
        "audio_stream": "0:a:0",
        "audio_stream_count": 1,
        "timebase": "first-audio-zero-v1",
        "recipe": "mono-16000-pcm16-v1",
        "decoder": {"version": "PRIVATE_DECODER_VERSION", "sha256": "b" * 64},
        "audio_frames": 48000,
        "duration": 3,
        "duration_basis": "decoded_pcm_frames",
        "container_duration_observation": 3.1,
        "pcm_sha256": "c" * 64,
        "source_origin": {
            "basis": "decoded_frame",
            "first_pts": 0,
            "normalized_first_pts": 0,
            "has_video": False,
        },
    }


def preparation(snapshot, *, start=0, end=48000, format="wav"):
    return {
        "schema": 1,
        "policy": "original-v1",
        "source": copy.deepcopy(snapshot),
        "window": {"start_frame": start, "end_frame": end, "timebase": "first-audio-zero-v1"},
        "prepared": {
            "sha256": "d" * 64,
            "bytes": (end - start) * 2 + 44,
            "format": format,
            "frames": end - start,
            "pcm_sha256": snapshot["pcm_sha256"]
            if format == "wav" and start == 0 and end == snapshot["audio_frames"]
            else "e" * 64,
            "sample_rate": 16000,
            "channels": 1,
            "sample_width": 2,
            "sample_basis": "mono-16000-pcm16-v1",
        },
    }


def seeded(tmp_path, *, prepared=True, legacy=False):
    store = Store(tmp_path)
    project = store.create_project("PRIVATE_PROJECT")
    recording = store.create_recording(project["id"], "PRIVATE_RECORDING.wav")
    snapshot = source_snapshot(recording["id"])
    params = {} if legacy else {"audio_input": snapshot}
    if prepared and not legacy:
        params["audio_preparations"] = [preparation(snapshot)]
    job = store.add_job(recording["id"], "transcribe", params)
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "PRIVATE_WORDS", "start": 0, "end": 3}]),
        {},
        "PRIVATE_LABEL",
        {"kind": "provider", "provider_type": "custom", "model": "synthetic"},
        job_id=job["id"],
    )
    version = store.result_versions(recording["id"], result["id"])[0]
    return store, project, recording, result, snapshot, version


def report(store, project, selections=None):
    with ZipFile(io.BytesIO(selected_view_report(store, project["id"], selections))) as archive:
        return json.loads(archive.read("manifest.json")), archive.read("methods.md").decode()


def update_context(store, version, context):
    store.execute(
        "UPDATE result_versions SET context=? WHERE id=?", (json.dumps(context), version["id"])
    )


def test_context_budget_refuses_before_fetching_or_decoding_version_metadata(tmp_path, monkeypatch):
    store, project, _, _, _, version = seeded(tmp_path, legacy=True)
    update_context(store, version, {"PRIVATE_TOKEN": "æ" * 100})
    monkeypatch.setattr(research_reports, "MAX_CONTEXT_BYTES", 128, raising=False)
    connect = store.connection

    class Guarded:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, params=()):
            if "SELECT v.*" in sql:
                pytest.fail("Oversized context was fetched before its SQL byte preflight.")
            return self.db.execute(sql, params)

    @contextmanager
    def guarded():
        with connect() as db:
            yield Guarded(db)

    monkeypatch.setattr(store, "connection", guarded)
    with pytest.raises(ValueError, match="metadata.*limit"):
        report(store, project)


def test_report_projects_exact_bound_evidence_without_private_fields_or_environment_reads(
    tmp_path, monkeypatch
):
    store, project, recording, result, snapshot, version = seeded(tmp_path)
    context = copy.deepcopy(version["context"])
    context.update(private_path="PRIVATE_PATH", reason="PRIVATE_REASON", token="PRIVATE_TOKEN")
    update_context(store, version, context)

    def forbidden(*args, **kwargs):
        pytest.fail("Reports must not inspect audio files or the current decoder.")

    monkeypatch.setattr(audio_inputs, "_decoder", forbidden)
    monkeypatch.setattr(audio_inputs, "_hash", forbidden)
    monkeypatch.setattr(audio_inputs, "freeze", forbidden)
    monkeypatch.setattr(audio_inputs, "_run", forbidden)
    monkeypatch.setattr(store, "directory", forbidden)
    manifest, methods = report(store, project)
    item = manifest["versions"][0]
    evidence = item["audio_input"]
    assert evidence["source"] == "audio-source-001"
    for key in (
        "policy",
        "sha256",
        "bytes",
        "format",
        "audio_stream",
        "audio_stream_count",
        "timebase",
        "recipe",
        "duration",
        "duration_basis",
        "audio_frames",
        "pcm_sha256",
        "source_origin",
    ):
        assert evidence[key] == snapshot[key]
    assert evidence["decoder_sha256"] == snapshot["decoder"]["sha256"]
    assert evidence["availability"] == "not_checked"
    prepared = item["audio_preparations"][0]
    assert prepared["observation"] == "local_decoded_pcm"
    assert prepared["windows"] == [
        {
            "start_frame": 0,
            "end_frame": 48000,
            "timebase": snapshot["timebase"],
            "output": preparation(snapshot)["prepared"],
        }
    ]
    assert "provider-side" in methods and "local" in methods
    serialized = json.dumps(manifest) + methods
    for private in (
        "PRIVATE_PROJECT",
        "PRIVATE_RECORDING",
        "PRIVATE_LABEL",
        "PRIVATE_WORDS",
        "PRIVATE_DECODER_VERSION",
        "PRIVATE_PATH",
        "PRIVATE_REASON",
        "PRIVATE_TOKEN",
        recording["id"],
        result["id"],
        "original.wav",
    ):
        assert private not in serialized


@pytest.mark.parametrize(
    "damage",
    [
        "wrong-recording",
        "prepared-source",
        "prepared-output",
        "one-sided",
        "source-extra",
        "receipt-extra",
        "prepared-shape",
        "full-pcm-mismatch",
    ],
)
def test_malformed_or_contradictory_audio_claims_fail_closed(tmp_path, damage):
    store, project, _, _, _, version = seeded(tmp_path)
    context = copy.deepcopy(version["context"])
    if damage == "wrong-recording":
        context["audio_input"]["recording_id"] = "f" * 32
    elif damage == "prepared-source":
        context["audio_preparations"][0]["source"]["sha256"] = "0" * 64
    elif damage == "prepared-output":
        context["audio_preparations"][0]["prepared"]["frames"] = 123
    elif damage == "one-sided":
        del context["audio_input"]
    elif damage == "source-extra":
        context["audio_input"]["token"] = "PRIVATE_TOKEN"
    elif damage == "receipt-extra":
        context["audio_preparations"][0]["path"] = "PRIVATE_PATH"
    elif damage == "full-pcm-mismatch":
        context["audio_preparations"][0]["prepared"]["pcm_sha256"] = "f" * 64
    else:
        context["audio_preparations"] = {"PRIVATE_TOKEN": "PRIVATE_PATH"}
    update_context(store, version, context)
    with pytest.raises(ValueError) as error:
        report(store, project)
    assert "PRIVATE" not in str(error.value)


def test_source_only_native_observation_keeps_missing_preparation_gap(tmp_path):
    store, project, _, _, _, _ = seeded(tmp_path, prepared=False)
    manifest, _ = report(store, project)
    item = manifest["versions"][0]
    assert "audio_input" in item and "audio_preparations" not in item
    assert any(
        "preparation" in gap.lower() and "not captured" in gap for gap in item["provenance_gaps"]
    )


def test_legacy_audio_evidence_remains_unknown_without_backfill(tmp_path, monkeypatch):
    store, project, _, _, _, _ = seeded(tmp_path, legacy=True)
    monkeypatch.setattr(audio_inputs, "validate", lambda *a: pytest.fail("No legacy backfill."))
    manifest, _ = report(store, project)
    assert "audio_input" not in manifest["versions"][0]
    assert "audio_preparations" not in manifest["versions"][0]
    assert any("Original media" in gap for gap in manifest["versions"][0]["provenance_gaps"])


def test_context_budget_is_cumulative_across_preserved_lineage(tmp_path, monkeypatch):
    store, project, recording, result, _, first = seeded(tmp_path, legacy=True)
    current = store.recording(recording["id"])
    store.save_transcript(recording["id"], result["segments"], {}, current["revision"])
    latest = store.result_versions(recording["id"], result["id"])[0]
    context = {"padding": "x" * 40}
    for version in (first, latest):
        update_context(store, version, context)
    size = len(json.dumps(context).encode())
    monkeypatch.setattr(research_reports, "MAX_CONTEXT_BYTES", size + 1)
    with pytest.raises(ValueError, match="metadata.*limit"):
        report(store, project)


def test_merge_parent_context_cannot_bypass_capture_preflight(tmp_path, monkeypatch):
    from test_merge_report_evidence import seeded_merge

    store, project, _, base, _, _, _ = seeded_merge(tmp_path)
    parent = store.result_versions(
        store.one("SELECT recording_id FROM transcript_results WHERE id=?", (base["id"],))[
            "recording_id"
        ],
        base["id"],
    )[-1]
    update_context(store, parent, {"PRIVATE": "x" * 4096})
    monkeypatch.setattr(research_reports, "MAX_CONTEXT_BYTES", 4096)
    connect = store.connection
    full_reads = []

    class Guarded:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, params=()):
            if "SELECT v.*" in sql:
                full_reads.append(sql)
                if len(full_reads) > 1:
                    pytest.fail("Unbounded merge-parent metadata was selected.")
            return self.db.execute(sql, params)

    @contextmanager
    def guarded():
        with connect() as db:
            yield Guarded(db)

    monkeypatch.setattr(store, "connection", guarded)
    with pytest.raises(ValueError, match="metadata.*limit"):
        report(store, project)
    assert len(full_reads) == 1


def test_audio_alias_binds_recording_hash_and_size_and_is_reused_through_edits(tmp_path):
    store, project, recording, result, snapshot, _ = seeded(tmp_path)
    current = store.recording(recording["id"])
    store.save_transcript(recording["id"], result["segments"], {}, current["revision"])
    other = store.create_recording(project["id"], "PRIVATE_OTHER.wav")
    selections = []
    for target, frozen in (
        (recording, snapshot | {"sha256": "f" * 64}),
        (other, source_snapshot(other["id"])),
    ):
        job = store.add_job(target["id"], "transcribe", {"audio_input": frozen})
        added = store.add_result(
            target["id"],
            normalize_segments([{"text": "PRIVATE"}]),
            {},
            "PRIVATE",
            {"kind": "provider"},
            job_id=job["id"],
        )
        head = store.result_versions(target["id"], added["id"])[0]
        selections.append(
            {"recording_id": target["id"], "result_id": added["id"], "version_id": head["id"]}
        )
    selections.append(
        {
            "recording_id": recording["id"],
            "result_id": result["id"],
            "version_id": store.result_versions(recording["id"], result["id"])[0]["id"],
        }
    )
    manifest, _ = report(store, project, selections)
    items = manifest["versions"]
    assert len({item["audio_input"]["source"] for item in items}) == 3
    original = [
        item
        for item in items
        if item["audio_input"]["sha256"] == "a" * 64 and item["recording"] == items[0]["recording"]
    ]
    assert len(original) == 2
    assert original[0]["audio_input"]["source"] == original[1]["audio_input"]["source"]


@pytest.mark.parametrize("transport", ["mp3", "original"])
def test_chunk_plan_projects_only_sample_windows_and_output_receipts(tmp_path, transport):
    store, project, _, _, snapshot, version = seeded(tmp_path)
    context = copy.deepcopy(version["context"])
    windows = [(0, 16000), (16000, 48000)] if transport == "mp3" else [(0, 48000)]
    parts, chunks = [], []
    for index, (start, end) in enumerate(windows):
        part = preparation(
            snapshot,
            start=start,
            end=end,
            format="mp3" if transport == "mp3" else snapshot["format"],
        )
        chunk = {
            "file": f"{index:04d}.mp3",
            "offset": start / 16000,
            "duration": (end - start) / 16000,
        }
        if transport == "original":
            part["prepared"].update(
                sha256=snapshot["sha256"],
                bytes=snapshot["bytes"],
                pcm_sha256=snapshot["pcm_sha256"],
            )
            chunk.update(file="original.wav", source="original")
        parts.append(part)
        chunks.append(chunk)
    context["audio_preparations"] = [
        {
            "schema": 1,
            "policy": "original-v1",
            "source": snapshot,
            "transport": transport,
            "chunks": chunks,
            "parts": parts,
        }
    ]
    update_context(store, version, context)
    manifest, methods = report(store, project)
    receipt = manifest["versions"][0]["audio_preparations"][0]
    assert receipt["transport"] == transport
    assert [
        (window["start_frame"], window["end_frame"]) for window in receipt["windows"]
    ] == windows
    assert receipt["observation"] == "local_decoded_pcm"
    serialized = json.dumps(manifest) + methods
    for chunk in chunks:
        assert chunk["file"] not in serialized
    assert "chunks" not in receipt and "parts" not in receipt
