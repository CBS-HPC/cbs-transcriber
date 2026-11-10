import copy
import hashlib
import json
import math
import struct
import threading
import wave
from pathlib import Path

import pytest

from cbs_transcriber import audio_inputs
from cbs_transcriber.media import Cancelled, ffmpeg, prepare_media
from cbs_transcriber.providers import Settings


class AudioStore:
    def __init__(self, directory):
        self.path = directory

    def directory(self, recording_id):
        assert recording_id == self.path.name
        return self.path


@pytest.mark.parametrize(
    "prefix",
    [
        "Parsed_ashowinfo_0 @ 000001AB37F0",
        "Parsed_ashowinfo_17 @ 0x600003abc",
        "ashowinfo@source_pts @ 0x600003abc",
    ],
)
def test_filter_pts_diagnostics_accept_real_windows_and_posix_prefixes(prefix):
    row = f"[{prefix}] n:0 pts:0 pts_time:0 fmt:s16 channels:1 rate:16000 nb_samples:1024"
    assert audio_inputs._FRAME_PTS.search(row)[1] == "0"
    assert audio_inputs._FRAME_PTS.search("    title : " + row) is None
    assert audio_inputs._FRAME_PTS.search(row.replace("pts:0", "pts:NOPTS")) is None


@pytest.mark.parametrize("pointer", ["00000253bb468a40", "0x600003abc"])
def test_mp3_completeness_diagnostic_is_anchored_not_user_metadata(pointer):
    diagnostic = f"[mp3 @ {pointer}] filesize and duration do not match (growing file?)\r\n"
    assert audio_inputs._MP3_INCOMPLETE.fullmatch(diagnostic)
    assert not audio_inputs._MP3_INCOMPLETE.fullmatch("    title : " + diagnostic)
    assert not audio_inputs._MP3_INCOMPLETE.fullmatch(diagnostic.replace("[mp3 @", "[other @"))


@pytest.fixture
def original(tmp_path):
    directory = tmp_path / "recordings" / ("a" * 32)
    directory.mkdir(parents=True)
    with wave.open(str(directory / "original.wav"), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"".join(struct.pack("<h", (i % 400) * 40) for i in range(48000)))
    return AudioStore(directory), {
        "id": directory.name,
        "filename": "Synthetic.wav",
        "duration": 9.0,
    }


def test_freeze_measures_original_without_changing_container_observation(original):
    store, recording = original
    before = copy.deepcopy(recording)
    value = audio_inputs.freeze(store, recording)
    assert value["policy"] == "original-v1"
    assert value["audio_stream"] == "0:a:0"
    assert value["audio_frames"] == 48000
    assert value["duration"] == 3
    assert value["container_duration_observation"] == 9
    assert value["sha256"] == hashlib.sha256((store.path / "original.wav").read_bytes()).hexdigest()
    assert recording == before
    assert audio_inputs.effective_recording({"params": {}}, recording) == recording
    assert (
        audio_inputs.effective_recording({"params": {"audio_input": value}}, recording)["duration"]
        == 3
    )


def test_first_stream_wins_over_default_and_native_refuses_ambiguous_contract(original):
    store, recording = original
    ffmpeg(
        [
            "-i",
            str(store.path / "original.wav"),
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1300:sample_rate=16000:duration=5",
            "-map",
            "0:a:0",
            "-map",
            "1:a:0",
            "-c:a",
            "pcm_s16le",
            "-disposition:a:0",
            "0",
            "-disposition:a:1",
            "default",
            str(store.path / "original.mkv"),
        ]
    )
    recording["filename"] = "Synthetic.mkv"
    value = audio_inputs.freeze(store, recording)
    assert value["duration"] == 3
    assert value["audio_stream_count"] == 2
    with pytest.raises(ValueError, match="single audio stream"):
        audio_inputs.require_native(value)


@pytest.mark.parametrize("offset", [2.5, -0.5])
def test_unproven_nonzero_first_stream_pts_refuses(original, offset):
    store, recording = original
    ffmpeg(
        [
            "-itsoffset",
            str(offset),
            "-i",
            str(store.path / "original.wav"),
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=32x32:r=2:d=5",
            "-map",
            "0:a:0",
            "-map",
            "1:v:0",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            "-copyts",
            "-avoid_negative_ts",
            "disabled",
            "-metadata",
            "title=pts_time:0",
            str(store.path / "original.mkv"),
        ]
    )
    recording["filename"] = "Synthetic.mkv"
    with pytest.raises(ValueError, match="timeline"):
        audio_inputs.freeze(store, recording)


def test_wav_receipt_binds_actual_window_and_reuses_exact_bytes(original):
    store, recording = original
    snapshot = audio_inputs.freeze(store, recording)
    folder = store.path / "enhancements" / ("b" * 32)
    receipt = audio_inputs.prepare_wav(
        store.path,
        recording,
        snapshot,
        folder,
        offset=0.12343,
        duration=0.442232,
        cancel=threading.Event(),
    )
    expected_start = round(0.12343 * 16000)
    expected_end = round((0.12343 + 0.442232) * 16000)
    assert receipt["window"] == {
        "start_frame": expected_start,
        "end_frame": expected_end,
        "timebase": "first-audio-zero-v1",
    }
    assert receipt["prepared"]["frames"] == expected_end - expected_start
    wav = folder / "audio.wav"
    before = wav.read_bytes(), wav.stat().st_mtime_ns
    assert (
        audio_inputs.prepare_wav(
            store.path,
            recording,
            snapshot,
            folder,
            offset=0.12343,
            duration=0.442232,
            cancel=threading.Event(),
        )
        == receipt
    )
    assert (wav.read_bytes(), wav.stat().st_mtime_ns) == before
    assert audio_inputs.prepared_evidence(folder, snapshot) == receipt


def test_changed_source_or_prepared_bytes_refuse(original):
    store, recording = original
    snapshot = audio_inputs.freeze(store, recording)
    folder = store.path / "enhancements" / ("b" * 32)
    audio_inputs.prepare_wav(
        store.path,
        recording,
        snapshot,
        folder,
        offset=0,
        duration=3,
        cancel=threading.Event(),
    )
    path = folder / "audio.wav"
    with path.open("r+b") as output:
        output.seek(-2, 2)
        output.write(b"\xff\x7f")
    with pytest.raises(ValueError, match="receipt"):
        audio_inputs.prepared_evidence(folder, snapshot)
    with (store.path / "original.wav").open("ab") as output:
        output.write(b"changed")
    with pytest.raises(ValueError, match="original.*changed"):
        audio_inputs.verify_original(store, recording, snapshot)


def test_full_wav_pcm_proof_is_rechecked_on_pure_validation_and_retry(original):
    store, recording = original
    snapshot = audio_inputs.freeze(store, recording)
    folder = store.path / "enhancements" / ("b" * 32)
    receipt = audio_inputs.prepare_wav(
        store.path,
        recording,
        snapshot,
        folder,
        offset=0,
        duration=3,
        cancel=threading.Event(),
    )
    corrupted = copy.deepcopy(receipt)
    corrupted["prepared"]["pcm_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="receipt"):
        audio_inputs.validate_evidence(corrupted, snapshot)
    path = folder / "audio.wav"
    with path.open("r+b") as output:
        output.seek(-2, 2)
        output.write(b"\x00\x00")
    receipt["prepared"] = audio_inputs._sample_info(path, store.path)
    (folder / "audio.input.json").write_text(json.dumps(receipt), encoding="utf-8")
    changed = path.read_bytes()
    with pytest.raises(ValueError, match="receipt"):
        audio_inputs.prepare_wav(
            store.path,
            recording,
            snapshot,
            folder,
            offset=0,
            duration=3,
            cancel=threading.Event(),
        )
    assert path.read_bytes() == changed


def test_partial_failure_never_publishes_or_adopts_audio(original, monkeypatch):
    store, recording = original
    snapshot = audio_inputs.freeze(store, recording)
    folder = store.path / "enhancements" / ("b" * 32)

    def fail(args, cancel=None, **kwargs):
        Path(args[-1]).write_bytes(b"partial output")
        raise ValueError("Media decoding failed.")

    monkeypatch.setattr(audio_inputs, "_run", fail)
    with pytest.raises(ValueError, match="decoding"):
        audio_inputs.prepare_wav(
            store.path,
            recording,
            snapshot,
            folder,
            offset=0,
            duration=3,
            cancel=threading.Event(),
        )
    assert not (folder / "audio.wav").exists()
    assert not (folder / "audio.input.json").exists()
    (folder / "audio.wav").write_bytes(b"orphan")
    with pytest.raises(ValueError, match="receipt"):
        audio_inputs.prepare_wav(
            store.path,
            recording,
            snapshot,
            folder,
            offset=0,
            duration=3,
            cancel=threading.Event(),
        )


def test_cancelled_preparation_never_publishes(original):
    store, recording = original
    snapshot = audio_inputs.freeze(store, recording)
    cancel = threading.Event()
    cancel.set()
    folder = store.path / "enhancements" / ("b" * 32)
    with pytest.raises(Cancelled):
        audio_inputs.prepare_wav(
            store.path,
            recording,
            snapshot,
            folder,
            offset=0,
            duration=3,
            cancel=cancel,
        )
    assert not (folder / "audio.input.json").exists()


def test_chunk_receipts_use_original_and_validate_reuse(original):
    store, recording = original
    snapshot = audio_inputs.freeze(store, recording)
    (store.path / "audio.mp3").write_bytes(b"a deliberately unusable playback derivative")
    folder, chunks = audio_inputs.prepare_chunks(
        store.path,
        "b" * 32,
        3,
        20,
        threading.Event(),
        audio_input=snapshot,
    )
    assert len(chunks) == 1
    assert chunks[0]["offset"] == 0
    assert chunks[0]["duration"] == 3
    evidence = audio_inputs.prepared_evidence(folder, snapshot)
    assert evidence["parts"][0]["prepared"]["frames"] == 48000
    before = (folder / chunks[0]["file"]).read_bytes()
    assert audio_inputs.prepare_chunks(
        store.path,
        "b" * 32,
        3,
        20,
        threading.Event(),
        audio_input=snapshot,
    ) == (folder, chunks)
    assert (folder / chunks[0]["file"]).read_bytes() == before
    plan = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
    plan["parts"][0]["window"]["end_frame"] -= 1
    (folder / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt"):
        audio_inputs.prepared_evidence(folder, snapshot)


def test_chunk_path_rechecks_only_bound_selected_part(original, monkeypatch):
    store, recording = original
    snapshot = audio_inputs.freeze(store, recording)
    folder, chunks = audio_inputs.prepare_chunks(
        store.path,
        "f" * 32,
        3,
        20,
        threading.Event(),
        audio_input=snapshot,
    )
    monkeypatch.setattr(
        audio_inputs,
        "_verify",
        lambda *args: pytest.fail("converted part rehashed entire original"),
    )
    path = audio_inputs.chunk_path(store.path, folder, chunks[0], snapshot)
    with path.open("ab") as output:
        output.write(b"changed")
    with pytest.raises(ValueError, match="receipt"):
        audio_inputs.chunk_path(store.path, folder, chunks[0], snapshot)
    with pytest.raises(ValueError, match="receipt"):
        audio_inputs.chunk_path(store.path, folder, {**chunks[0], "file": "0001.mp3"}, snapshot)


def test_new_compressed_parts_reuse_silence_aware_boundaries_from_original(original, monkeypatch):
    store, recording = original
    snapshot = audio_inputs.freeze(store, recording)
    run = audio_inputs._run

    def silence(args, *remaining, **kwargs):
        if any("silencedetect" in str(arg) for arg in args):
            assert args[args.index("-i") + 1] == str(store.path / "original.wav")
            assert args[args.index("-map") + 1] == "0:a:0"
            return (
                "[silencedetect @ 0x123] silence_end: 0.9 | silence_duration: 0.5\n"
                "[silencedetect @ 0x123] silence_end: 1.8 | silence_duration: 0.5\n",
                0,
                "",
            )
        return run(args, *remaining, **kwargs)

    monkeypatch.setattr(audio_inputs, "_run", silence)
    _, parts = audio_inputs.prepare_chunks(
        store.path,
        "f" * 32,
        3,
        0.012,
        threading.Event(),
        audio_input=snapshot,
    )
    assert [(part["offset"], part["duration"]) for part in parts] == [
        (0, 0.9),
        (0.9, 0.9),
        (1.8, 1.2),
    ]


def test_managed_original_symlink_refuses(original, tmp_path):
    store, recording = original
    path = store.path / "original.wav"
    target = tmp_path / "other.wav"
    path.replace(target)
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this Windows account.")
    with pytest.raises(ValueError, match="owned regular"):
        audio_inputs.freeze(store, recording)


@pytest.mark.parametrize(
    "field,value",
    [("duration", math.nan), ("audio_frames", True), ("audio_stream", "0:a:1"), ("bytes", -1)],
)
def test_invalid_frozen_contract_refuses(original, field, value):
    snapshot = audio_inputs.freeze(*original)
    snapshot[field] = value
    with pytest.raises(ValueError, match="input"):
        audio_inputs.validate(snapshot)


def test_truncated_aac_fails_strict_full_decode(original):
    store, recording = original
    ffmpeg(
        [
            "-i",
            str(store.path / "original.wav"),
            "-map",
            "0:a:0",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            str(store.path / "original.m4a"),
        ]
    )
    path = store.path / "original.m4a"
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])
    recording["filename"] = "Synthetic.m4a"
    with pytest.raises(ValueError, match="decoding"):
        audio_inputs.freeze(store, recording)


@pytest.mark.parametrize("stage", ["freeze", "prepare"])
def test_complete_frame_truncated_mp3_with_retained_info_count_never_publishes(original, stage):
    store, recording = original
    path = store.path / "original.mp3"
    ffmpeg(
        [
            "-i",
            str(store.path / "original.wav"),
            "-map",
            "0:a:0",
            "-ar",
            "16000",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "64k",
            str(path),
        ]
    )
    recording["filename"] = "Synthetic.mp3"
    complete = audio_inputs.freeze(store, recording)
    data = path.read_bytes()
    pos = (
        10 + sum((data[6 + i] & 127) << (7 * (3 - i)) for i in range(4))
        if data.startswith(b"ID3")
        else 0
    )
    starts = []
    bitrates = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
    while pos + 4 <= len(data):
        header = int.from_bytes(data[pos : pos + 4], "big")
        assert header >> 21 == 2047
        assert (header >> 19) & 3 == 2  # MPEG2
        assert (header >> 17) & 3 == 1  # Layer III
        rate = [44100, 48000, 32000][(header >> 10) & 3] // 2
        size = 72 * bitrates[(header >> 12) & 15] * 1000 // rate + ((header >> 9) & 1)
        assert size > 4 and pos + size <= len(data)
        starts.append(pos)
        pos += size
    assert pos == len(data) and len(starts) > 50
    info = data.find(b"Info", starts[0], starts[1])
    if info < 0:
        info = data.find(b"Xing", starts[0], starts[1])
    assert info >= 0 and int.from_bytes(data[info + 4 : info + 8], "big") & 1
    declared_frames = int.from_bytes(data[info + 8 : info + 12], "big")
    retained_frames = len(starts) // 2
    assert declared_frames > retained_frames
    # Keep every retained MPEG frame complete, together with the original Info/Xing count.
    path.write_bytes(data[: starts[retained_frames]])
    folder = store.path / "enhancements" / ("e" * 32)
    if stage == "freeze":
        with pytest.raises(ValueError, match="complete MP3"):
            audio_inputs.freeze(store, recording)
    else:
        # Simulate a prior decoder-only inspection receipt, to prove preparation independently
        # refuses incomplete source even when its exact shortened bytes/samples were observed.
        observed = store.path / "observed-truncated.wav"
        ffmpeg(
            [
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(observed),
            ]
        )
        measured = audio_inputs._sample_info(observed, store.path)
        cached = {
            **complete,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
            "audio_frames": measured["frames"],
            "duration": measured["frames"] / 16000,
            "pcm_sha256": measured["pcm_sha256"],
        }
        with pytest.raises(ValueError, match="complete MP3"):
            audio_inputs.prepare_wav(
                store.path,
                recording,
                cached,
                folder,
                offset=0,
                duration=cached["duration"],
                cancel=threading.Event(),
            )
    assert not (folder / "audio.wav").exists()
    assert not (folder / "audio.input.json").exists()


def test_complete_mp3_warning_text_in_metadata_does_not_refuse(original):
    store, recording = original
    ffmpeg(
        [
            "-i",
            str(store.path / "original.wav"),
            "-map",
            "0:a:0",
            "-c:a",
            "libmp3lame",
            "-metadata",
            "title=[mp3 @ 0x1] filesize and duration do not match (growing file?)",
            str(store.path / "original.mp3"),
        ]
    )
    recording["filename"] = "Synthetic.mp3"
    snapshot = audio_inputs.freeze(store, recording)
    folder = store.path / "enhancements" / ("e" * 32)
    receipt = audio_inputs.prepare_wav(
        store.path,
        recording,
        snapshot,
        folder,
        offset=0,
        duration=snapshot["duration"],
        cancel=threading.Event(),
    )
    assert receipt["prepared"]["frames"] == snapshot["audio_frames"]
    assert receipt["prepared"]["pcm_sha256"] == snapshot["pcm_sha256"]


def test_scoped_freeze_does_not_decode_unselected_pcm(original, monkeypatch):
    store, recording = original
    run = audio_inputs._run
    calls = []

    def record(args, *args_after, **kwargs):
        calls.append((args, kwargs))
        return run(args, *args_after, **kwargs)

    monkeypatch.setattr(audio_inputs, "_run", record)
    snapshot = audio_inputs.freeze(store, recording, scoped=True)
    assert snapshot["duration_basis"] == "container_observation"
    assert snapshot["duration"] == 9
    assert snapshot["audio_frames"] is None
    assert snapshot["pcm_sha256"] is None
    assert len(calls) == 1
    assert calls[0][0][calls[0][0].index("-c:a") + 1] == "copy"
    assert not calls[0][1].get("pcm")
    folder = store.path / "enhancements" / ("c" * 32)
    value = audio_inputs.prepare_wav(
        store.path,
        recording,
        snapshot,
        folder,
        offset=0.5,
        duration=0.75,
        cancel=threading.Event(),
    )
    assert value["prepared"]["frames"] == 12000
    assert "-ss" in calls[1][0]
    assert calls[1][0][calls[1][0].index("-ss") + 1] == "0.5"
    assert "-t" in calls[1][0]


def test_original_transport_never_copies_or_claims_container_pcm_format(original):
    store, recording = original
    snapshot = audio_inputs.freeze(store, recording)
    folder, chunks = audio_inputs.prepare_chunks(
        store.path,
        "d" * 32,
        3,
        20,
        threading.Event(),
        audio_input=snapshot,
        transport="original",
    )
    assert list(folder.iterdir()) == [folder / "plan.json"]
    assert chunks == [{"file": "original.wav", "source": "original", "offset": 0, "duration": 3}]
    assert (
        audio_inputs.chunk_path(store.path, folder, chunks[0], snapshot)
        == store.path / "original.wav"
    )
    evidence = audio_inputs.prepared_evidence(folder, snapshot)
    # Sample fields describe the explicit normalized measurement, not original container geometry.
    assert evidence["parts"][0]["prepared"]["sample_basis"] == "mono-16000-pcm16-v1"
    assert audio_inputs.validate_evidence(evidence, snapshot) == evidence


@pytest.mark.parametrize(
    "provider_type,endpoint,expected",
    [
        ("openai", "https://api.openai.com/v1", "original"),
        ("openai", "https://example.invalid/v1", "mp3"),
        ("custom", "https://api.openai.com/v1", "mp3"),
    ],
)
def test_only_exact_known_official_transport_uses_original(
    original, provider_type, endpoint, expected
):
    snapshot = audio_inputs.freeze(*original)
    settings = Settings(provider_type=provider_type, base_url=endpoint)
    assert audio_inputs.transport_for(snapshot, settings) == expected


def test_zero_origin_audio_shorter_than_video_uses_real_audio_frames(original):
    store, recording = original
    ffmpeg(
        [
            "-i",
            str(store.path / "original.wav"),
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=32x32:r=2:d=5",
            "-map",
            "1:v:0",
            "-map",
            "0:a:0",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            str(store.path / "original.mkv"),
        ]
    )
    recording.update(filename="Synthetic.mkv", duration=5)
    snapshot = audio_inputs.freeze(store, recording)
    assert snapshot["duration"] == 3
    assert snapshot["container_duration_observation"] == 5
    assert recording["duration"] == 5
    scoped = audio_inputs.freeze(store, recording, scoped=True)
    folder = store.path / "enhancements" / ("e" * 32)
    with pytest.raises(ValueError, match="receipt|PCM"):
        audio_inputs.prepare_wav(
            store.path,
            recording,
            scoped,
            folder,
            offset=4,
            duration=1,
            cancel=threading.Event(),
        )
    assert not (folder / "audio.input.json").exists()


def test_discontinuous_audio_timeline_refuses_even_with_zero_first_frame(original):
    store, recording = original
    ffmpeg(
        [
            "-i",
            str(store.path / "original.wav"),
            "-map",
            "0:a:0",
            "-af",
            "aselect='not(between(t,0.5,1))'",
            "-c:a",
            "pcm_s16le",
            str(store.path / "original.mkv"),
        ]
    )
    recording["filename"] = "Synthetic.mkv"
    with pytest.raises(ValueError, match="timeline"):
        audio_inputs.freeze(store, recording)


def test_stereo_48khz_original_whole_wav_matches_frozen_pcm_measurement(original):
    store, recording = original
    with wave.open(str(store.path / "original.wav"), "wb") as output:
        output.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        output.writeframes(b"".join(struct.pack("<hh", i % 800, -(i % 400)) for i in range(144000)))
    snapshot = audio_inputs.freeze(store, recording)
    folder = store.path / "enhancements" / ("e" * 32)
    receipt = audio_inputs.prepare_wav(
        store.path,
        recording,
        snapshot,
        folder,
        offset=0,
        duration=snapshot["duration"],
        cancel=threading.Event(),
    )
    assert receipt["prepared"]["pcm_sha256"] == snapshot["pcm_sha256"]
    assert receipt["prepared"]["frames"] == 48000


@pytest.mark.parametrize(
    "extension,encoder",
    [
        ("m4a", "aac"),
        ("mp3", "libmp3lame"),
        ("flac", "flac"),
        ("ogg", "libvorbis"),
        ("webm", "libopus"),
    ],
)
def test_physical_compressed_original_freezes_and_prepares_whole_audio(
    original, extension, encoder
):
    store, recording = original
    ffmpeg(
        [
            "-i",
            str(store.path / "original.wav"),
            "-map",
            "0:a:0",
            "-c:a",
            encoder,
            str(store.path / ("original." + extension)),
        ]
    )
    recording["filename"] = "Synthetic." + extension
    snapshot = audio_inputs.freeze(store, recording)
    folder = store.path / "enhancements" / ("e" * 32)
    receipt = audio_inputs.prepare_wav(
        store.path,
        recording,
        snapshot,
        folder,
        offset=0,
        duration=snapshot["duration"],
        cancel=threading.Event(),
    )
    assert receipt["prepared"]["frames"] == snapshot["audio_frames"]
    assert receipt["prepared"]["pcm_sha256"] == snapshot["pcm_sha256"]


@pytest.mark.parametrize(
    "extension,encoder",
    [
        ("m4a", "aac"),
        ("mp3", "libmp3lame"),
        ("webm", "libopus"),
    ],
)
def test_audio_only_priming_preserves_leading_silence_against_browser(original, extension, encoder):
    store, recording = original
    with wave.open(str(store.path / "original.wav"), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(
            b"".join(
                struct.pack(
                    "<h", 0 if i < 8000 else round(10000 * math.sin(2 * math.pi * 800 * i / 16000))
                )
                for i in range(48000)
            )
        )
    ffmpeg(
        [
            "-i",
            str(store.path / "original.wav"),
            "-map",
            "0:a:0",
            "-c:a",
            encoder,
            str(store.path / ("original." + extension)),
        ]
    )
    recording["filename"] = "Synthetic." + extension
    snapshot = audio_inputs.freeze(store, recording)
    folder = store.path / "enhancements" / ("e" * 32)
    audio_inputs.prepare_wav(
        store.path,
        recording,
        snapshot,
        folder,
        offset=0,
        duration=snapshot["duration"],
        cancel=threading.Event(),
    )
    prepare_media(store.path, Path(recording["filename"]), threading.Event())
    browser = store.path / "browser-decoded.wav"
    ffmpeg(
        [
            "-i",
            str(store.path / "audio.mp3"),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(browser),
        ]
    )

    def marker(path):
        with wave.open(str(path), "rb") as source:
            samples = struct.unpack(
                "<" + "h" * source.getnframes(), source.readframes(source.getnframes())
            )
        return next(
            index / RATE
            for index in range(0, len(samples), 160)
            if math.sqrt(sum(v * v for v in samples[index : index + 160]) / 160) > 500
        )

    RATE = 16000
    assert marker(folder / "audio.wav") == marker(browser) == 0.5
    assert snapshot["source_origin"]["normalized_first_pts"] == 0


def test_real_aac_later_window_uses_cached_full_inspection_and_exact_scope(original):
    store, recording = original
    ffmpeg(
        [
            "-i",
            str(store.path / "original.wav"),
            "-map",
            "0:a:0",
            "-c:a",
            "aac",
            str(store.path / "original.m4a"),
        ]
    )
    recording["filename"] = "Synthetic.m4a"
    snapshot = audio_inputs.freeze(store, recording)
    # A full job/cache may supply this inspection; fresh bounded admission is deferred.
    folder = store.path / "enhancements" / ("e" * 32)
    receipt = audio_inputs.prepare_wav(
        store.path,
        recording,
        snapshot,
        folder,
        offset=1.25,
        duration=0.5,
        cancel=threading.Event(),
    )
    assert receipt["window"] == {
        "start_frame": 20000,
        "end_frame": 28000,
        "timebase": "first-audio-zero-v1",
    }
    assert receipt["prepared"]["frames"] == 8000
