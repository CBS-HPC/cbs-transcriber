"""Selected audio bundles are inert, bounded, immutable inputs; no provider is contacted."""

import copy
import hashlib
import json
import os
import shutil
import struct
import threading
import wave
from pathlib import Path

import pytest

from cbs_transcriber import audio_inputs, repair_bundles, repair_sources
from cbs_transcriber.enhancement import EnhancementOptions
from cbs_transcriber.media import Cancelled
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_wav(path, pcm, *, comment=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, audio_inputs.RATE, 0, "NONE", "not compressed"))
        output.writeframes(pcm)
    if comment is not None:
        body = path.read_bytes()
        payload = b"INFOICMT" + struct.pack("<I", len(comment)) + comment
        payload += b"\0" * (len(payload) % 2)
        info = b"LIST" + struct.pack("<I", len(payload)) + payload
        body = body[:36] + info + body[36:]
        path.write_bytes(body[:4] + struct.pack("<I", len(body) - 8) + body[8:])


@pytest.fixture
def study(tmp_path):
    store = Store(tmp_path / "library")
    project = store.create_project("PRIVATE_PROJECT")
    recording = store.create_recording(project["id"], "PRIVATE_FILE.wav")
    store.execute("UPDATE recordings SET status='ready',duration=20 WHERE id=?", (recording["id"],))
    result = store.add_result(
        recording["id"],
        [
            normalize_segments([row])[0] | {"id": str(index)}
            for index, row in enumerate(
                [
                    {"text": "PRIVATE_UNSELECTED", "start": 0, "end": 2},
                    {"text": "God morgen.", "start": 2, "end": 5},
                    {"text": "Tak fordi", "start": None, "end": None},
                    {"text": "du kom.", "start": None, "end": None},
                    {"text": "Farvel igen.", "start": 9, "end": 12},
                ]
            )
        ],
        {"UNKNOWN": "PRIVATE_SPEAKER"},
        "PRIVATE_RESULT",
        {"kind": "import"},
    )
    pcm = b"".join(struct.pack("<h", ((i * 37) % 65536) - 32768) for i in range(20 * 16000))
    snapshot = audio_inputs.validate(
        {
            "policy": audio_inputs.POLICY,
            "recording_id": recording["id"],
            "format": "wav",
            "sha256": "a" * 64,
            "bytes": len(pcm) + 44,
            "audio_stream": "0:a:0",
            "audio_stream_count": 1,
            "timebase": audio_inputs.TIMEBASE,
            "recipe": audio_inputs.RECIPE,
            "decoder": {"version": "PRIVATE_DECODER_VERSION", "sha256": "b" * 64},
            "audio_frames": len(pcm) // 2,
            "duration": 20,
            "duration_basis": "decoded_pcm_frames",
            "pcm_sha256": digest(pcm),
            "container_duration_observation": 20,
            "source_origin": {
                "basis": "decoded_frame",
                "first_pts": 0,
                "normalized_first_pts": 0,
                "has_video": False,
            },
        }
    )
    runtime = {
        "runtime_id": "synthetic-runtime-v1",
        "worker_sha256": "c" * 64,
        "language": "da",
        "alignment_model": {"repo": "synthetic/aligner", "revision": "d" * 40},
    }
    return store, recording, result, pcm, snapshot, runtime


def prepared_job(study, *, group=False, comment=None):
    store, recording, result, pcm, snapshot, runtime = study
    options = EnhancementOptions(execution="ucloud", device="cuda").model_dump()
    params = {
        "enhancement": options,
        "audio_input": snapshot,
        "repair_runtime": runtime,
        "segment_ids": ["2", "3"] if group else ["1", "4"],
    }
    if group:
        source = store.freeze_result(
            recording["id"], result["id"], store.recording(recording["id"])["revision"]
        )
        params |= {
            "source_snapshot": source | {"segments": source["segments"][2:4]},
            "realign": True,
            "passage_timing": True,
            "timing_units": [{"ids": ["2", "3"], "start": 5, "end": 9}],
        }
        job = store.add_job(recording["id"], "enhance", params)
        windows = [(5, 9)]
    else:
        job = store.enqueue_enhancement(
            recording["id"], result["id"], store.recording(recording["id"])["revision"], params
        )
        windows = [(2, 5), (9, 12)]
    clips = []
    for index, (start, end) in enumerate(windows):
        folder = (
            store.directory(recording["id"]) / "enhancements" / job["id"] / "prepared" / str(index)
        )
        path = folder / "audio.wav"
        clip = pcm[start * 16000 * 2 : end * 16000 * 2]
        write_wav(path, clip, comment=comment)
        receipt = {
            "schema": 1,
            "policy": audio_inputs.POLICY,
            "source": snapshot,
            "window": {
                "start_frame": start * 16000,
                "end_frame": end * 16000,
                "timebase": audio_inputs.TIMEBASE,
            },
            "prepared": {
                "sha256": digest(path.read_bytes()),
                "bytes": path.stat().st_size,
                "format": "wav",
                "frames": len(clip) // 2,
                "pcm_sha256": digest(clip),
                "sample_rate": 16000,
                "channels": 1,
                "sample_width": 2,
                "sample_basis": audio_inputs.RECIPE,
            },
        }
        (folder / "audio.input.json").write_text(json.dumps(receipt), encoding="utf-8")
        store.record_audio_preparation(job["id"], receipt)
        clips.append(folder)
    return store.job(job["id"]), clips


def test_selected_bundle_has_only_canonical_clips_and_selected_text(study, monkeypatch):
    job, clips = prepared_job(study, comment=b"PRIVATE_CONTAINER_COMMENT")
    store, recording, _, pcm, _, runtime = study
    monkeypatch.setattr(
        audio_inputs, "freeze", lambda *a, **k: pytest.fail("must not inspect original")
    )
    built = repair_bundles.build_bundle(store, job["id"], clips, runtime)
    checked = repair_bundles.validate_bundle(store, job["id"])
    assert checked["sha256"] == built["sha256"]
    assert checked["manifest"]["source"]["segment_ids"] == ["1", "4"]
    assert checked["manifest"]["transfer_frames"] == 6 * 16000
    directory = built["directory"]
    for index, (start, end) in enumerate(((2, 5), (9, 12))):
        path = directory / "units" / f"{index:05d}" / "audio.wav"
        with wave.open(str(path), "rb") as source:
            assert source.readframes(source.getnframes()) == pcm[start * 32000 : end * 32000]
        assert path.stat().st_size == 44 + (end - start) * 32000
    all_bytes = b"".join(p.read_bytes() for p in directory.rglob("*") if p.is_file())
    for private in (b"PRIVATE_", str(store.root).encode(), b"api_key", b"runtime_python"):
        assert private not in all_bytes
    assert not store.all("SELECT id FROM runtime_batch_runs")
    assert store.job(job["id"])["state"] == "queued"
    assert store.recording(recording["id"])["active_transcript_id"] == study[2]["id"]


def test_untimed_group_uses_search_window_without_inventing_passage_bounds(study):
    job, clips = prepared_job(study, group=True)
    built = repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
    request = json.loads((built["directory"] / "units/00000/request.json").read_text())
    unit = built["manifest"]["units"][0]
    assert request["text"] == "Tak fordi du kom."
    assert [item["id"] for item in unit["passages"]] == ["2", "3"]
    assert all(item["start"] is None and item["end"] is None for item in unit["passages"])
    assert unit["window"]["start_frame"] == 5 * 16000
    assert unit["window"]["end_frame"] == 9 * 16000


def test_bundle_reuse_is_exact_and_ignores_later_progress_fields(study):
    job, clips = prepared_job(study)
    store, *_, runtime = study
    first = repair_bundles.build_bundle(store, job["id"], clips, runtime)
    old = {
        str(p.relative_to(first["directory"])): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in first["directory"].rglob("*")
        if p.is_file()
    }
    params = store.job(job["id"])["params"] | {"progress_observation": "later"}
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job["id"]))
    second = repair_bundles.build_bundle(store, job["id"], clips, runtime)
    assert second["sha256"] == first["sha256"]
    assert old == {
        str(p.relative_to(first["directory"])): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in first["directory"].rglob("*")
        if p.is_file()
    }


def test_source_read_change_refuses_before_bundle_publication(study, monkeypatch):
    job, clips = prepared_job(study)
    store = study[0]
    inspect = repair_sources.inspect_repair_source

    def race(*args):
        proof = inspect(*args)
        params = store.job(job["id"])["params"] | {"late": True}
        store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job["id"]))
        return proof

    monkeypatch.setattr(repair_sources, "inspect_repair_source", race)
    with pytest.raises(ValueError, match="changed"):
        repair_bundles.build_bundle(store, job["id"], clips, study[-1])
    assert not list(store.directory(study[1]["id"]).rglob("COMMITTED.json"))


def test_swapped_clip_refuses_and_never_commits(study):
    job, clips = prepared_job(study)
    with pytest.raises(ValueError, match="clip|receipt|window"):
        repair_bundles.build_bundle(study[0], job["id"], clips[::-1], study[-1])
    assert not list(study[0].directory(study[1]["id"]).rglob("COMMITTED.json"))


def test_map_alignment_offsets_once_and_preserves_nulls(study):
    job, clips = prepared_job(study)
    built = repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
    output = {
        "format": repair_bundles.OUTPUT_FORMAT,
        "bundle_sha256": built["sha256"],
        "unit_index": 0,
        "coordinate_space": "clip",
        "words": [
            {
                "word": "God",
                "start": 0.25,
                "end": 0.75,
                "score": 0.9,
                "char_start": 0,
                "char_end": 3,
            },
            {
                "word": "morgen.",
                "start": None,
                "end": 1.5,
                "score": None,
                "char_start": 4,
                "char_end": 11,
            },
        ],
    }
    before = copy.deepcopy(output)
    mapped = repair_bundles.map_alignment_output(built, output)
    assert output == before
    assert mapped["coordinate_space"] == "recording"
    assert mapped["words"][0]["start"] == 2.25
    assert mapped["words"][1]["start"] is None and mapped["words"][1]["end"] == 3.5
    assert repair_bundles.map_alignment_output(built, output) == mapped
    with pytest.raises(ValueError, match="coordinate|clip"):
        repair_bundles.map_alignment_output(built, mapped)


def change_params(store, job, change):
    params = store.job(job["id"])["params"]
    change(params)
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job["id"]))


def generation_path(study, job, generation=1):
    return repair_bundles._directory(
        study[0], {"recording_id": study[1]["id"], "job_id": job["id"]}, generation
    )[0]


@pytest.mark.parametrize("field", ["manifest", "request", "audio", "extra", "missing", "marker"])
def test_committed_inventory_tampering_refuses_exact_reuse(study, field):
    job, clips = prepared_job(study)
    built = repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
    directory = built["directory"]
    if field == "manifest":
        target = directory / "manifest.json"
        target.write_bytes(target.read_bytes() + b" ")
    elif field == "request":
        target = directory / "units/00000/request.json"
        target.write_bytes(target.read_bytes().replace(b"God", b"Bad"))
    elif field == "audio":
        target = directory / "units/00000/audio.wav"
        data = target.read_bytes()
        target.write_bytes(data[:44] + bytes([data[44] ^ 1]) + data[45:])
    elif field == "extra":
        (directory / "units/00000/private.txt").write_text("private")
    elif field == "missing":
        (directory / "units/00001/request.json").unlink()
    else:
        (directory / "COMMITTED.json").write_text('{"format": "forged"}')
    with pytest.raises(ValueError, match="bundle"):
        repair_bundles.validate_bundle(study[0], job["id"])
    with pytest.raises(ValueError, match="bundle"):
        repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])


def test_reuse_needs_no_live_clip_cache_or_original_decode(study, monkeypatch):
    job, clips = prepared_job(study)
    first = repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
    for path in clips:
        assert path.resolve().is_relative_to(study[0].directory(study[1]["id"]).resolve())
        shutil.rmtree(path)
    monkeypatch.setattr(
        audio_inputs, "prepared_evidence", lambda *args: pytest.fail("reuse must use committed PCM")
    )
    second = repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
    assert first == second


def test_cancelled_generation_is_retained_and_requires_explicit_new_generation(study, monkeypatch):
    job, clips = prepared_job(study)
    cancel = threading.Event()
    copy_audio = repair_bundles._copy_audio

    def copy_then_cancel(*args):
        copy_audio(*args)
        cancel.set()

    monkeypatch.setattr(repair_bundles, "_copy_audio", copy_then_cancel)
    with pytest.raises(Cancelled):
        repair_bundles.build_bundle(study[0], job["id"], clips, study[-1], cancel=cancel)
    directory = generation_path(study, job)
    assert (directory / "units/00000/audio.wav").is_file()
    assert not (directory / "COMMITTED.json").exists()
    before = (directory / "units/00000/audio.wav").read_bytes()
    with pytest.raises(ValueError, match="bundle"):
        repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
    monkeypatch.setattr(repair_bundles, "_copy_audio", copy_audio)
    built = repair_bundles.build_bundle(study[0], job["id"], clips, study[-1], generation=2)
    assert built["manifest"]["local_generation"] == 2
    assert (directory / "units/00000/audio.wav").read_bytes() == before
    assert study[0].job(job["id"])["state"] == "queued"


def test_commit_failure_never_publishes_marker_or_overwrites_generation(study, monkeypatch):
    job, clips = prepared_job(study)
    write = repair_bundles._write

    def fail_marker(path, data, root):
        if path.name == "COMMITTED.pending":
            raise OSError("PRIVATE_PATH_SECRET")
        return write(path, data, root)

    monkeypatch.setattr(repair_bundles, "_write", fail_marker)
    with pytest.raises(ValueError, match="could not be written") as error:
        repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
    assert "PRIVATE_" not in str(error.value)
    assert (generation_path(study, job) / "manifest.json").is_file()
    assert not (generation_path(study, job) / "COMMITTED.json").exists()
    with pytest.raises(ValueError, match="bundle"):
        repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])


@pytest.mark.parametrize("bound", ["MAX_REQUEST_BYTES", "MAX_MANIFEST_BYTES", "MAX_BUNDLE_BYTES"])
def test_byte_limits_are_checked_before_creating_generation(study, monkeypatch, bound):
    job, clips = prepared_job(study)
    monkeypatch.setattr(repair_bundles, bound, 1)
    with pytest.raises(ValueError, match="byte limit"):
        repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
    assert not generation_path(study, job).exists()


def test_oversized_recorded_container_is_refused_before_opening_clip(study, monkeypatch):
    job, clips = prepared_job(study)
    change_params(
        study[0],
        job,
        lambda p: p["audio_preparations"][0]["prepared"].update(bytes=2**30),
    )
    monkeypatch.setattr(
        audio_inputs, "prepared_evidence", lambda *args: pytest.fail("must reject before clip I/O")
    )
    with pytest.raises(ValueError, match="byte limit"):
        repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
    assert not generation_path(study, job).exists()


def test_actual_clip_byte_size_is_checked_before_shared_preparation_hashing(study, monkeypatch):
    job, clips = prepared_job(study)
    with (clips[0] / "audio.wav").open("ab") as target:
        target.write(b"unexpected data")
    monkeypatch.setattr(
        audio_inputs,
        "prepared_evidence",
        lambda *args: pytest.fail("must reject actual size before hashing"),
    )
    with pytest.raises(ValueError, match="bundle|byte"):
        repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
    assert not (generation_path(study, job) / "COMMITTED.json").exists()


def test_clip_growth_during_copy_keeps_container_reads_bounded(study, monkeypatch):
    job, clips = prepared_job(study)
    target = clips[0] / "audio.wav"
    expected = target.stat().st_size
    read_frames, fdopen = wave.Wave_read.readframes, os.fdopen
    observed = []

    class Meter:
        def __init__(self, stream):
            self.stream, self.read_bytes = stream, 0

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size=-1):
            assert 0 <= size <= 64 * 1024
            block = self.stream.read(size)
            self.read_bytes += len(block)
            return block

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

    def measured(*args, **kwargs):
        meter = Meter(fdopen(*args, **kwargs))
        observed.append(meter)
        return meter

    changed = False

    def grow(audio, frames):
        nonlocal changed
        block = read_frames(audio, frames)
        if block and not changed:
            changed = True
            with target.open("ab") as output:
                output.write(b"unselected" * 10000)
        return block

    monkeypatch.setattr(os, "fdopen", measured)
    monkeypatch.setattr(wave.Wave_read, "readframes", grow)
    with pytest.raises(ValueError, match="bundle"):
        repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
    assert len(observed) == 1 and observed[0].read_bytes <= 2 * expected + 1
    assert not (generation_path(study, job) / "COMMITTED.json").exists()


def test_malformed_recorded_clip_fails_with_curated_error(study):
    job, clips = prepared_job(study)
    store = study[0]
    target = clips[0] / "audio.wav"
    target.write_bytes(b"NOPE" + target.read_bytes()[4:])
    change_params(
        store,
        job,
        lambda p: p["audio_preparations"][0]["prepared"].update(sha256=digest(target.read_bytes())),
    )
    receipt = store.job(job["id"])["params"]["audio_preparations"][0]
    (clips[0] / "audio.input.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="bundle"):
        repair_bundles.build_bundle(store, job["id"], clips, study[-1])
    assert not (generation_path(study, job) / "COMMITTED.json").exists()


@pytest.mark.parametrize("failure", ["auto", "runtime", "policy", "retranscription", "full_asr"])
def test_unadmitted_operation_or_missing_frozen_evidence_refuses_before_write(study, failure):
    job, clips = prepared_job(study)

    def change(params):
        if failure == "auto":
            params["enhancement"]["language"] = "auto"
        elif failure == "runtime":
            del params["repair_runtime"]
        elif failure == "policy":
            del params["audio_input"]
        elif failure == "retranscription":
            params["retranscription"] = True
        else:
            params["transcription"] = True

    change_params(study[0], job, change)
    with pytest.raises(ValueError):
        repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
    assert not generation_path(study, job).exists()


def test_outside_recording_clip_and_linked_inventory_are_refused(study, tmp_path):
    job, clips = prepared_job(study)
    external = tmp_path / "external"
    shutil.copytree(clips[0], external)
    with pytest.raises(ValueError, match="owned"):
        repair_bundles.build_bundle(study[0], job["id"], [external, clips[1]], study[-1])
    built = repair_bundles.build_bundle(study[0], job["id"], clips, study[-1], generation=2)
    link = built["directory"] / "units/00000/linked.wav"
    try:
        os.symlink(external / "audio.wav", link)
    except OSError:
        if os.name == "nt":
            # Directory junctions require no Windows symlink privilege.
            import subprocess

            link = built["directory"] / "extra"
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link).removeprefix("\\\\?\\"), str(external)],
                capture_output=True,
                check=False,
            )
            if result.returncode:
                pytest.skip("This machine cannot create test links or junctions")
        else:
            raise
    with pytest.raises(ValueError, match="bundle|owned"):
        repair_bundles.validate_bundle(study[0], job["id"], generation=2)


@pytest.mark.parametrize(
    "update",
    [
        {"start": None, "end": -0.1},
        {"start": 3.1, "end": None},
        {"start": True},
        {"start": 0.5, "end": 0.5},
        {"start": float("nan")},
        {"end": float("inf")},
        {"score": False},
        {"score": 1.1},
        {"word": "forged"},
        {"char_start": True},
        {"unexpected": {"deep": []}},
    ],
)
def test_output_validates_each_present_endpoint_score_and_literal_range(study, update):
    job, clips = prepared_job(study)
    built = repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
    output = {
        "format": repair_bundles.OUTPUT_FORMAT,
        "bundle_sha256": built["sha256"],
        "unit_index": 0,
        "coordinate_space": "clip",
        "words": [
            {
                "word": "God",
                "start": 0.25,
                "end": 0.75,
                "score": 0.9,
                "char_start": 0,
                "char_end": 3,
            }
            | update
        ],
    }
    with pytest.raises(ValueError):
        repair_bundles.map_alignment_output(built, output)


@pytest.mark.parametrize("alteration", ["binding", "unit", "coordinates", "manifest"])
def test_output_cannot_change_binding_unit_or_coordinate_system(study, alteration):
    job, clips = prepared_job(study)
    built = repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
    output = {
        "format": repair_bundles.OUTPUT_FORMAT,
        "bundle_sha256": built["sha256"],
        "unit_index": 0,
        "coordinate_space": "clip",
        "words": [],
    }
    if alteration == "binding":
        output["bundle_sha256"] = "e" * 64
    elif alteration == "unit":
        output["unit_index"] = True
    elif alteration == "coordinates":
        output["coordinate_space"] = "recording"
    else:
        built["manifest"]["units"][0]["window"]["start_frame"] += 1
    with pytest.raises(ValueError):
        repair_bundles.map_alignment_output(built, output)


@pytest.mark.parametrize("data", [b'{"a":1,"a":1}', b'{"a":NaN}', b"[" * 25 + b"]" * 25])
def test_canonical_json_parser_bounds_depth_and_refuses_ambiguous_values(data):
    with pytest.raises(ValueError):
        repair_bundles._decode(data, 1000)


@pytest.mark.parametrize(
    "location", ["source_audio", "source_receipt", "committed_audio", "marker"]
)
def test_hard_links_do_not_count_as_owned_regular_bundle_files(study, tmp_path, location):
    job, clips = prepared_job(study)
    if location.startswith("source"):
        target = clips[0] / ("audio.wav" if location == "source_audio" else "audio.input.json")
    else:
        built = repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
        target = built["directory"] / (
            "units/00000/audio.wav" if location == "committed_audio" else "COMMITTED.json"
        )
    external = tmp_path / "external_file"
    shutil.copyfile(target, external)
    target.unlink()
    os.link(external, target)
    with pytest.raises(ValueError, match="owned|bundle"):
        repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])


def test_historical_bundle_stays_frozen_after_head_edit_and_trash(study):
    job, clips = prepared_job(study)
    store, recording, result, *_, runtime = study
    first = repair_bundles.build_bundle(store, job["id"], clips, runtime)
    current = store.recording(recording["id"])
    changed = copy.deepcopy(current["segments"])
    changed[1]["text"] = "NEW_PRIVATE_REVIEW_TEXT"
    store.save_transcript(recording["id"], changed, current["speakers"], current["revision"])
    store.set_result_trashed(
        recording["id"], result["id"], store.recording(recording["id"])["revision"], trashed=True
    )
    again = repair_bundles.validate_bundle(store, job["id"])
    assert again == first
    request = (again["directory"] / "units/00000/request.json").read_text()
    assert "God morgen." in request and "NEW_PRIVATE_" not in request
    assert store.recording(recording["id"])["active_transcript_id"] is None


def test_marker_publication_never_overwrites_an_existing_marker(study, monkeypatch):
    job, clips = prepared_job(study)
    write = repair_bundles._write

    def insert_marker(path, data, root):
        write(path, data, root)
        if path.name == "COMMITTED.pending":
            (path.parent / "COMMITTED.json").write_bytes(b"EXISTING")

    monkeypatch.setattr(repair_bundles, "_write", insert_marker)
    with pytest.raises(ValueError):
        repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
    assert (generation_path(study, job) / "COMMITTED.json").read_bytes() == b"EXISTING"


def test_interrupted_marker_publication_cannot_be_reused(study, monkeypatch):
    job, clips = prepared_job(study)
    unlink = Path.unlink

    def interrupt(path, *args, **kwargs):
        if path.name == "COMMITTED.pending":
            raise OSError("synthetic interruption")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupt)
    with pytest.raises(ValueError, match="could not be written"):
        repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
    directory = generation_path(study, job)
    assert (directory / "COMMITTED.pending").is_file()
    assert (directory / "COMMITTED.json").stat().st_nlink == 2
    with pytest.raises(ValueError, match="bundle"):
        repair_bundles.validate_bundle(study[0], job["id"])
    with pytest.raises(ValueError, match="bundle"):
        repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])


@pytest.mark.parametrize("left,right", [(7, 12), (9, 10)])
def test_group_word_must_belong_to_one_passage_not_a_joined_boundary(study, left, right):
    job, clips = prepared_job(study, group=True)
    built = repair_bundles.build_bundle(study[0], job["id"], clips, study[-1])
    text = "Tak fordi du kom."
    output = {
        "format": repair_bundles.OUTPUT_FORMAT,
        "bundle_sha256": built["sha256"],
        "unit_index": 0,
        "coordinate_space": "clip",
        "words": [
            {
                "word": text[left:right],
                "start": 0.5,
                "end": 1,
                "score": 0.9,
                "char_start": left,
                "char_end": right,
            }
        ],
    }
    with pytest.raises(ValueError, match="bundle"):
        repair_bundles.map_alignment_output(built, output)


def test_output_ranges_use_literal_unicode_codepoints(study):
    store, recording, *_ = study
    current = store.recording(recording["id"])
    current["segments"][1]["text"] = "Æble går."
    store.save_transcript(
        recording["id"], current["segments"], current["speakers"], current["revision"]
    )
    job, clips = prepared_job(study)
    built = repair_bundles.build_bundle(store, job["id"], clips, study[-1])
    output = {
        "format": repair_bundles.OUTPUT_FORMAT,
        "bundle_sha256": built["sha256"],
        "unit_index": 0,
        "coordinate_space": "clip",
        "words": [
            {"word": "Æble", "start": 0.5, "end": 1, "score": 0.9, "char_start": 0, "char_end": 4}
        ],
    }
    assert repair_bundles.map_alignment_output(built, output)["words"][0]["word"] == "Æble"
