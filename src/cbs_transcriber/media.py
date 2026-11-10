"""FFmpeg conversion and bounded API audio, without a shell or resident media server."""

import json
import os
import re
import subprocess
import tempfile
import threading

from . import ffmpeg_runtime

MEDIA_SUFFIXES = {
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
    ".ogg",
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".mpeg",
    ".mpg",
    ".mpga",
    ".aac",
    ".wma",
    ".avi",
}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".mpeg", ".mpg", ".avi"}


class Cancelled(Exception):
    pass


def ffmpeg(args, cancel=None):
    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise Cancelled()
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            [ffmpeg_runtime.resolve(cancel), "-hide_banner", "-nostdin", "-nostats", "-y", *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        while process.poll() is None:
            if cancel.wait(0.15):
                process.kill()
                process.wait()
                raise Cancelled()
        log.seek(0)
        output = log.read(2_000_000).decode("utf-8", errors="replace")
        if process.returncode:
            raise ValueError(
                "Media decoding or conversion failed. Check that the file is playable, "
                "contains audio, and that the library has enough free space."
            )
        return output


def prepare_media(directory, filename, cancel):
    original = directory / ("original" + filename.suffix.lower())
    audio_temp = directory / "audio.pending.mp3"
    log = ffmpeg(
        [
            "-i",
            str(original),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(audio_temp),
        ],
        cancel,
    )
    audio_temp.replace(directory / "audio.mp3")
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", log)
    duration = (
        sum(float(match[i]) * factor for i, factor in [(1, 3600), (2, 60), (3, 1)])
        if match
        else None
    )
    video = filename.suffix.lower() in VIDEO_SUFFIXES and "Video:" in log
    if video:
        pending = directory / "video.pending.mp4"
        ffmpeg(
            [
                "-i",
                str(original),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-sn",
                "-dn",
                "-vf",
                "scale=w='min(1280,iw)':h=-2",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "25",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(pending),
            ],
            cancel,
        )
        pending.replace(directory / "video.mp4")
    return {"kind": "video" if video else "audio", "duration": duration}


def chunk_boundaries(duration, silence_ends, max_seconds=900):
    if duration is None or duration <= 0:
        raise ValueError("Cannot determine recording duration for transcription.")
    boundaries = [0.0]
    while duration - boundaries[-1] > max_seconds:
        target = boundaries[-1] + max_seconds
        nearby = [point for point in silence_ends if target - 30 <= point <= target]
        boundaries.append(max(nearby) if nearby else target)
    boundaries.append(duration)
    return list(zip(boundaries[:-1], boundaries[1:], strict=True))


def prepare_chunks(
    directory, job_id, duration, max_mb, cancel, *, audio_input=None, transport="mp3"
):
    if audio_input is not None:
        from .audio_inputs import prepare_chunks as prepare_original_chunks

        return prepare_original_chunks(
            directory,
            job_id,
            duration,
            max_mb,
            cancel,
            audio_input=audio_input,
            transport=transport,
        )
    folder = directory / "chunks" / job_id
    folder.mkdir(parents=True, exist_ok=True)
    plan_path = folder / "plan.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if all((folder / item["file"]).is_file() for item in plan):
            return folder, plan
    audio = directory / "audio.mp3"
    # 64 kbps gives a predictable upper bound; allow headroom for container overhead.
    max_seconds = min(900, max_mb * 1_000_000 * 0.8 / 8000)
    silence_ends = []
    if duration and duration > max_seconds:
        log = ffmpeg(
            [
                "-i",
                str(audio),
                "-af",
                "silencedetect=noise=-35dB:d=0.4",
                "-f",
                "null",
                "-",
            ],
            cancel,
        )
        silence_ends = [float(s) for s in re.findall(r"silence_end:\s*([\d.]+)", log)]
    plan = []
    for index, (start, end) in enumerate(chunk_boundaries(duration, silence_ends, max_seconds)):
        filename = f"{index:04d}.mp3"
        target = folder / filename
        ffmpeg(
            [
                "-ss",
                str(start),
                "-i",
                str(audio),
                "-t",
                str(end - start),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "64k",
                str(target),
            ],
            cancel,
        )
        if target.stat().st_size > max_mb * 1_000_000:
            raise ValueError("The prepared audio exceeds this provider's upload limit.")
        plan.append({"file": filename, "offset": start, "duration": end - start})
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return folder, plan
