"""FFmpeg startup probes belong to the caller's cancellation and drain boundary."""

import hashlib
import subprocess
import sys
import threading
import wave
from pathlib import Path

import pytest
from imageio_ffmpeg import _utils
from test_audio_inputs import original as original

from cbs_transcriber import audio_inputs, ffmpeg_runtime, media
from cbs_transcriber.jobs import JobQueue


@pytest.mark.parametrize("operation", ["media", "original", "version"])
def test_cancel_and_shutdown_reap_locator_and_version_probes(tmp_path, monkeypatch, operation):
    monkeypatch.delenv("IMAGEIO_FFMPEG_EXE", raising=False)
    _utils._get_ffmpeg_exe.cache_clear()
    if "cbs_transcriber.ffmpeg_runtime" in sys.modules:
        monkeypatch.setattr(
            sys.modules["cbs_transcriber.ffmpeg_runtime"], "_default_executable", None
        )
    monkeypatch.setattr(audio_inputs, "_decoder_cache", None)
    if operation == "version":
        # Reach the injected real child without first hashing the 80+ MB bundled decoder.
        # The complete freeze/selected-binary identity path is also exercised below.
        executable = tmp_path / "selected decoder.exe"
        executable.write_bytes(b"synthetic selected decoder")
        monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", str(executable))
    directory = tmp_path / ("a" * 32)
    directory.mkdir()
    with wave.open(str(directory / "original.wav"), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\0\0" * 1600)

    class Source:
        def directory(self, _):
            return directory

    queue, entered, errors, probes = JobQueue(None, None, kind="media"), threading.Event(), [], []
    popen = subprocess.Popen

    def observed(command, *args, **kwargs):
        if len(command) == 2 and command[1] == "-version" and not entered.is_set():
            process = popen(
                [sys._base_executable, "-I", "-S", "-c", "import time; time.sleep(60)"],
                *args,
                **kwargs,
            )
            probes.append(process)
            entered.set()
            return process
        return popen(command, *args, **kwargs)

    def work():
        try:
            if operation == "media":
                media.ffmpeg(["-version"], queue.cancel_event)
            elif operation == "original":
                audio_inputs._run(["-version"], queue.cancel_event)
            else:
                audio_inputs.freeze(
                    Source(), {"id": directory.name, "filename": "test.wav"}, queue.cancel_event
                )
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(subprocess, "Popen", observed)
    queue.thread = threading.Thread(target=work, daemon=True)
    stopper = None
    try:
        queue.thread.start()
        assert entered.wait(5), errors
        stopper = threading.Thread(target=queue.stop, daemon=True)
        stopper.start()
        assert queue.cancel_event.wait(1)
        stopper.join(2)
        assert not stopper.is_alive(), (
            "Stop cannot drain a worker blocked in an unmanaged FFmpeg probe"
        )
        assert not queue.thread.is_alive()
        assert probes[0].poll() is not None
        assert len(errors) == 1 and isinstance(errors[0], media.Cancelled), errors
    finally:
        # Startup can fail before entered is set; cancellation still owns every late child.
        queue.cancel_event.set()
        for process in probes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        queue.thread.join(5)
        if stopper:
            stopper.join(5)
        _utils._get_ffmpeg_exe.cache_clear()
        assert not queue.thread.is_alive()


def python_probes(monkeypatch, code):
    popen, calls, processes = subprocess.Popen, [], []

    def launch(command, *args, **kwargs):
        calls.append(command)
        source = code(command) if callable(code) else code
        process = popen([sys._base_executable, "-I", "-S", "-c", source], *args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", launch)
    return calls, processes


def test_default_order_warm_cache_and_explicit_override_are_preserved(monkeypatch, tmp_path):
    monkeypatch.delenv("IMAGEIO_FFMPEG_EXE", raising=False)
    monkeypatch.setattr(ffmpeg_runtime, "_default_executable", None)
    monkeypatch.setattr(
        ffmpeg_runtime, "_candidates", lambda: iter(("failed", "selected", "later"))
    )
    calls, processes = python_probes(
        monkeypatch,
        lambda command: (
            "raise SystemExit(2)" if command[0] == "failed" else "print('ffmpeg version audit-1')"
        ),
    )
    assert ffmpeg_runtime.resolve() == "selected"
    assert [command[0] for command in calls] == ["failed", "selected"]
    assert ffmpeg_runtime.resolve() == "selected" and len(calls) == 2
    override = str(tmp_path / "explicit choice.exe")
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", override)
    assert ffmpeg_runtime.resolve() == override and len(calls) == 2
    monkeypatch.delenv("IMAGEIO_FFMPEG_EXE")
    assert ffmpeg_runtime.resolve() == "selected" and len(calls) == 2
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(media.Cancelled):
        ffmpeg_runtime.resolve(cancel)
    assert len(calls) == 2
    assert all(process.poll() is not None for process in processes)


def test_packaged_conda_system_candidate_order_uses_metadata_only(monkeypatch, tmp_path):
    bundle, prefix = tmp_path / "bundle", tmp_path / "environment"
    bundle.mkdir()
    conda = prefix / "Library/bin/ffmpeg.exe"
    conda.parent.mkdir(parents=True)
    bundled = bundle / "packaged.exe"
    bundled.write_bytes(b"synthetic bundle")
    conda.write_bytes(b"synthetic conda")
    monkeypatch.setattr(_utils, "get_platform", lambda: "win-audit")
    monkeypatch.setitem(_utils.FNAME_PER_PLATFORM, "win-audit", "packaged.exe")
    monkeypatch.setattr(_utils, "_get_bin_dir", lambda: str(bundle))
    monkeypatch.setattr(ffmpeg_runtime.sys, "prefix", str(prefix))
    assert list(ffmpeg_runtime._candidates()) == [str(bundled), str(conda), "ffmpeg"]


@pytest.mark.parametrize("failure", ["missing", "failed"])
def test_missing_or_failed_candidates_leave_no_cached_selection(monkeypatch, failure):
    monkeypatch.delenv("IMAGEIO_FFMPEG_EXE", raising=False)
    monkeypatch.setattr(ffmpeg_runtime, "_default_executable", None)
    monkeypatch.setattr(ffmpeg_runtime, "_candidates", lambda: iter(("first", "second")))
    if failure == "missing":
        calls = []

        def missing(command, **kwargs):
            calls.append(command)
            raise FileNotFoundError("synthetic executable missing")

        monkeypatch.setattr(subprocess, "Popen", missing)
    else:
        calls, _ = python_probes(monkeypatch, "raise SystemExit(4)")
    with pytest.raises(ffmpeg_runtime.Unavailable, match="could not be found"):
        ffmpeg_runtime.resolve()
    assert [command[0] for command in calls] == ["first", "second"]
    assert ffmpeg_runtime._default_executable is None


@pytest.mark.parametrize("failure", ["timeout", "output"])
def test_probe_budget_refusal_kills_and_drains_without_fallback(monkeypatch, failure):
    monkeypatch.delenv("IMAGEIO_FFMPEG_EXE", raising=False)
    monkeypatch.setattr(ffmpeg_runtime, "_default_executable", None)
    monkeypatch.setattr(
        ffmpeg_runtime, "_candidates", lambda: iter(("selected", "forbidden-fallback"))
    )
    monkeypatch.setattr(ffmpeg_runtime, "PROBE_TIMEOUT", 0.2 if failure == "timeout" else 3)
    code = (
        "import time; time.sleep(60)"
        if failure == "timeout"
        else ("import os,time;os.write(1,b'x'*65537);time.sleep(60)")
    )
    calls, processes = python_probes(monkeypatch, code)
    message = "bounded time" if failure == "timeout" else "bounded limit"
    with pytest.raises(ffmpeg_runtime.Unavailable, match=message):
        ffmpeg_runtime.resolve()
    assert [command[0] for command in calls] == ["selected"]
    assert ffmpeg_runtime._default_executable is None
    assert processes[0].poll() is not None


def test_guard_exception_reaps_and_drains_probe_before_propagation(monkeypatch):
    _, processes = python_probes(monkeypatch, "import time;time.sleep(60)")
    failure = RuntimeError("Synthetic durable guard failure")

    class Guard:
        def is_set(self):
            return False

        def wait(self, timeout):
            raise failure

    with pytest.raises(RuntimeError) as caught:
        ffmpeg_runtime.version("selected", Guard())
    assert caught.value is failure
    assert processes[0].poll() is not None and processes[0].stdout.closed


def test_inherited_output_pipe_cannot_extend_probe_deadline(monkeypatch):
    popen = subprocess.Popen
    holder = popen(
        [sys._base_executable, "-I", "-S", "-c", "import time;time.sleep(60)"],
        stdout=subprocess.PIPE,
    )
    parents = []

    def inherited(command, *args, **kwargs):
        process = popen(
            [sys._base_executable, "-I", "-S", "-c", "import time;time.sleep(60)"],
            *args,
            **kwargs,
        )
        process.stdout.close()
        # Model a writer surviving the selected probe, while retaining both real
        # process handles so this synthetic test can reap its independent holder.
        process.stdout = holder.stdout
        parents.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", inherited)
    monkeypatch.setattr(ffmpeg_runtime, "PROBE_TIMEOUT", 0.1)
    try:
        with pytest.raises(ffmpeg_runtime.Unavailable, match="bounded time"):
            ffmpeg_runtime.version("selected")
        assert parents[0].poll() is not None and parents[0].stdout.closed
    finally:
        for process in [*parents, holder]:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def test_decoder_hash_version_and_warm_cache_use_exact_selected_file(monkeypatch, tmp_path):
    executable = tmp_path / "selected.exe"
    executable.write_bytes(b"synthetic selected decoder")
    monkeypatch.setattr(audio_inputs, "_decoder_cache", None)
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "must-not-be-reselected")
    calls, _ = python_probes(monkeypatch, "print('ffmpeg version audit-pinned extra details')")
    expected = {
        "version": "audit-pinned",
        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
    }
    assert audio_inputs._decoder(executable=str(executable)) == expected
    assert calls == [[str(executable), "-version"]]
    assert audio_inputs._decoder(executable=str(executable)) == expected
    assert len(calls) == 1
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(media.Cancelled):
        audio_inputs._decoder(cancel, executable=str(executable))
    assert len(calls) == 1


def test_decoder_rejects_executable_change_during_version_observation(monkeypatch, tmp_path):
    executable = tmp_path / "selected.exe"
    executable.write_bytes(b"original decoder")
    monkeypatch.setattr(audio_inputs, "_decoder_cache", None)

    def changed(command):
        executable.write_bytes(b"changed decoder bytes with a different size")
        return "print('ffmpeg version audit')"

    python_probes(monkeypatch, changed)
    with pytest.raises(ValueError, match="decoder changed"):
        audio_inputs._decoder(executable=str(executable))
    assert audio_inputs._decoder_cache is None


def test_decoder_guard_interleaving_cannot_return_another_binary_cache(monkeypatch, tmp_path):
    first, second = tmp_path / "first.exe", tmp_path / "second.exe"
    first.write_bytes(b"first decoder")
    second.write_bytes(b"second decoder")
    monkeypatch.setattr(ffmpeg_runtime, "version", lambda executable, cancel: Path(executable).stem)
    monkeypatch.setattr(audio_inputs, "_decoder_cache", None)
    other = audio_inputs._decoder(executable=str(second))
    other_cache = audio_inputs._decoder_cache
    audio_inputs._decoder_cache = None
    original, switched = ffmpeg_runtime.check_cancel, []

    def interleave(cancel):
        original(cancel)
        if audio_inputs._decoder_cache and audio_inputs._decoder_cache[0][0] == str(first):
            # Another source/admission worker completes its observation while this
            # caller polls its durable guard immediately before returning.
            audio_inputs._decoder_cache = other_cache
            switched.append(True)

    monkeypatch.setattr(ffmpeg_runtime, "check_cancel", interleave)
    value = audio_inputs._decoder(executable=str(first))
    assert switched and value != other
    assert value == {"version": "first", "sha256": hashlib.sha256(first.read_bytes()).hexdigest()}


def test_original_freeze_pins_one_binary_despite_override_change(original, monkeypatch):
    store, recording = original
    executable = str(Path(_utils._get_bin_dir()) / _utils.FNAME_PER_PLATFORM[_utils.get_platform()])
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", executable)
    run, observed = audio_inputs._run, []

    def changed(args, *remaining, **kwargs):
        observed.append(kwargs["executable"])
        result = run(args, *remaining, **kwargs)
        monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "must-not-retarget-this-operation")
        return result

    monkeypatch.setattr(audio_inputs, "_run", changed)
    frozen = audio_inputs.freeze(store, recording)
    assert len(observed) == 3 and set(observed) == {str(Path(executable).resolve())}
    assert set(frozen["decoder"]) == {"version", "sha256"}
    assert frozen["decoder"]["sha256"] == hashlib.sha256(Path(executable).read_bytes()).hexdigest()
