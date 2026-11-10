import threading
from pathlib import Path

import pytest

from cbs_transcriber.media import ffmpeg, prepare_media


def test_v8_video_and_audio_derivatives(tmp_path):
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=0x183e37:s=320x240:r=15:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(tmp_path / "original.mp4"),
        ]
    )
    metadata = prepare_media(tmp_path, Path("interview.mp4"), threading.Event())
    assert metadata["kind"] == "video"
    assert 2.8 < metadata["duration"] < 3.3
    assert (tmp_path / "audio.mp3").stat().st_size > 1000
    assert (tmp_path / "video.mp4").stat().st_size > 1000


def test_v8_corrupt_media_has_actionable_error(tmp_path):
    (tmp_path / "original.mp3").write_bytes(b"not a recording")
    with pytest.raises(ValueError, match="Media decoding"):
        prepare_media(tmp_path, Path("broken.mp3"), threading.Event())
