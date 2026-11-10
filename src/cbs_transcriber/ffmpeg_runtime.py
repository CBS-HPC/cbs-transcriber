"""Cancellable FFmpeg discovery/version probes; no media, models or settings writes."""

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from imageio_ffmpeg import _utils

PROBE_TIMEOUT = 10.0
MAX_VERSION_BYTES = 64 * 1024
_default_executable = None


class Unavailable(ValueError):
    """The selected decoder could not be observed safely."""


class _Rejected(Unavailable):
    """A missing/failed automatic candidate permits the existing next candidate."""


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        from .media import Cancelled

        raise Cancelled()


def _probe(executable, cancel=None):
    check_cancel(cancel)
    cancel = cancel if cancel is not None else threading.Event()
    try:
        process = subprocess.Popen(
            [str(executable), "-version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except OSError:
        raise _Rejected("The FFmpeg executable is unavailable.") from None
    output, eof, nonblocking = bytearray(), False, False
    try:
        try:
            os.set_blocking(process.stdout.fileno(), False)
        except OSError:
            raise Unavailable("The FFmpeg startup response cannot be observed safely.") from None
        nonblocking = True
        deadline = time.monotonic() + PROBE_TIMEOUT
        while True:
            check_cancel(cancel)
            if time.monotonic() >= deadline:
                raise Unavailable("The FFmpeg startup check exceeded its bounded time.")
            if not eof:
                try:
                    block = os.read(
                        process.stdout.fileno(), min(8192, MAX_VERSION_BYTES + 1 - len(output))
                    )
                except BlockingIOError:
                    block = None
                if block:
                    if len(output) + len(block) > MAX_VERSION_BYTES:
                        raise Unavailable("The FFmpeg version response exceeds its bounded limit.")
                    output.extend(block)
                    continue
                if block == b"":
                    eof = True
            if process.poll() is not None and eof:
                break
            if cancel.wait(0.05):
                from .media import Cancelled

                raise Cancelled()
    finally:
        # Guard exceptions, timeout and Cancel retain ownership through kill/wait/drain.
        # Nonblocking reads also bound a pipe inherited by an explicit wrapper's child.
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait()
            if nonblocking:
                settle = time.monotonic() + 1
                while not eof and time.monotonic() < settle:
                    try:
                        block = os.read(process.stdout.fileno(), 8192)
                    except BlockingIOError:
                        time.sleep(0.01)
                        continue
                    except OSError:
                        break
                    if not block:
                        eof = True
        finally:
            process.stdout.close()
    check_cancel(cancel)
    if process.returncode:
        raise _Rejected("The FFmpeg executable failed its startup check.")
    return bytes(output)


def _candidates():
    # These metadata-only helpers come from the locked imageio-ffmpeg package.
    # Calling its public locator would run an unbounded subprocess before our guard.
    platform = _utils.get_platform()
    bundled = Path(_utils._get_bin_dir()) / _utils.FNAME_PER_PLATFORM.get(platform, "")
    if bundled.is_file():
        yield str(bundled)
    conda = Path(sys.prefix) / (
        "Library/bin/ffmpeg.exe" if platform.startswith("win") else "bin/ffmpeg"
    )
    if conda.is_file():
        yield str(conda)
    yield "ffmpeg"


def resolve(cancel=None):
    """Keep explicit overrides and bundled→conda→system ordering, with bounded probes."""
    global _default_executable
    check_cancel(cancel)
    override = os.getenv("IMAGEIO_FFMPEG_EXE")
    if override:
        return override  # As before, explicit choices bypass automatic candidate validation.
    if _default_executable is not None:
        return _default_executable
    for executable in _candidates():
        check_cancel(cancel)
        try:
            _probe(executable, cancel)
        except _Rejected:
            continue
        # Timeout, output refusal and Cancel never silently select another decoder.
        _default_executable = executable
        return executable
    raise Unavailable(
        "FFmpeg could not be found. Repair the app installation or check IMAGEIO_FFMPEG_EXE."
    )


def for_identity(cancel=None):
    """Resolve one actual file for both original-v1 execution and its decoder receipt."""
    executable = resolve(cancel)
    path = Path(executable)
    if not path.is_file():
        found = shutil.which(executable)
        if found is None:
            raise Unavailable("The selected original audio decoder could not be found.")
        path = Path(found)
    check_cancel(cancel)
    return str(path.resolve(strict=True))


def version(executable, cancel=None):
    # Preserve imageio's version-token spelling while observing this exact executable.
    line = _probe(executable, cancel).split(b"\n", 1)[0].decode(errors="ignore").strip()
    return line.split("version", 1)[-1].lstrip().split(" ", 1)[0].strip()
