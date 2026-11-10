"""Synthetic decoder observations retain independent cancellation and identity."""

import hashlib
import threading
from pathlib import Path

import pytest

from cbs_transcriber import audio_inputs, ffmpeg_runtime, media


class WatchingCancel:
    def __init__(self):
        self.cancelled = threading.Event()
        self.waiting = threading.Event()
        self.checks = 0

    def is_set(self):
        self.checks += 1
        if self.checks >= 3:
            self.waiting.set()
        return self.cancelled.is_set()


def worker(path, cancel=None):
    result = []

    def work():
        try:
            result.append(audio_inputs._decoder(cancel, executable=str(path)))
        except BaseException as exc:
            result.append(exc)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread, result


def finish(thread):
    thread.join(10)
    assert not thread.is_alive()


@pytest.fixture(autouse=True)
def cold_cache(monkeypatch):
    monkeypatch.setattr(audio_inputs, "_decoder_cache", None)
    assert not audio_inputs._decoder_flights
    yield
    assert not audio_inputs._decoder_flights


def test_same_decoder_has_one_cold_observation(monkeypatch, tmp_path):
    path = tmp_path / "synthetic-decoder"
    path.write_bytes(b"decoder bytes")
    entered, release = threading.Event(), threading.Event()
    calls = []

    def version(executable, cancel):
        calls.append(executable)
        entered.set()
        assert release.wait(10)
        return "synthetic"

    monkeypatch.setattr(ffmpeg_runtime, "version", version)
    owner, first = worker(path)
    assert entered.wait(10)
    guard = WatchingCancel()
    waiter, second = worker(path, guard)
    try:
        assert guard.waiting.wait(10)
        assert calls == [str(path)]
    finally:
        release.set()
        finish(owner)
        finish(waiter)
    expected = {"version": "synthetic", "sha256": hashlib.sha256(b"decoder bytes").hexdigest()}
    assert first == second == [expected]
    assert calls == [str(path)]


def test_cancelled_waiter_does_not_stop_owner(monkeypatch, tmp_path):
    path = tmp_path / "decoder"
    path.write_bytes(b"decoder")
    entered, release = threading.Event(), threading.Event()

    def version(executable, cancel):
        entered.set()
        assert release.wait(10)
        return "synthetic"

    monkeypatch.setattr(ffmpeg_runtime, "version", version)
    owner, first = worker(path)
    assert entered.wait(10)
    guard = WatchingCancel()
    waiter, second = worker(path, guard)
    try:
        assert guard.waiting.wait(10)
        guard.cancelled.set()
        finish(waiter)
        assert isinstance(second[0], media.Cancelled)
        assert owner.is_alive()
    finally:
        release.set()
        finish(owner)
    assert first[0]["version"] == "synthetic"


def test_waiter_guard_failure_does_not_disturb_owner(monkeypatch, tmp_path):
    path = tmp_path / "decoder"
    path.write_bytes(b"decoder")
    entered, release = threading.Event(), threading.Event()
    failure = RuntimeError("synthetic durable guard failure")

    def version(executable, cancel):
        entered.set()
        assert release.wait(10)
        return "synthetic"

    class FailingGuard(WatchingCancel):
        def is_set(self):
            if super().is_set():
                return True
            if self.checks >= 3:
                raise failure
            return False

    monkeypatch.setattr(ffmpeg_runtime, "version", version)
    owner, first = worker(path)
    assert entered.wait(10)
    waiter, second = worker(path, FailingGuard())
    try:
        finish(waiter)
        assert second == [failure]
        assert owner.is_alive()
    finally:
        release.set()
        finish(owner)
    assert first[0]["version"] == "synthetic"


def test_cancelled_owner_wakes_waiter_to_observe_independently(monkeypatch, tmp_path):
    path = tmp_path / "decoder"
    path.write_bytes(b"decoder")
    entered = threading.Event()
    cancel_owner = threading.Event()
    calls = []

    def version(executable, cancel):
        calls.append(executable)
        if len(calls) == 1:
            entered.set()
            assert cancel_owner.wait(10)
            ffmpeg_runtime.check_cancel(cancel)
        return "synthetic"

    monkeypatch.setattr(ffmpeg_runtime, "version", version)
    owner, first = worker(path, cancel_owner)
    assert entered.wait(10)
    guard = WatchingCancel()
    waiter, second = worker(path, guard)
    try:
        assert guard.waiting.wait(10)
    finally:
        cancel_owner.set()
        finish(owner)
        finish(waiter)
    assert isinstance(first[0], media.Cancelled)
    assert second[0]["version"] == "synthetic"
    assert len(calls) == 2


def test_distinct_decoders_are_not_serialized(monkeypatch, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    entered = {str(first): threading.Event(), str(second): threading.Event()}
    release = threading.Event()

    def version(executable, cancel):
        entered[executable].set()
        assert release.wait(10)
        return Path(executable).name

    monkeypatch.setattr(ffmpeg_runtime, "version", version)
    one, first_result = worker(first)
    two, second_result = worker(second)
    try:
        assert all(event.wait(10) for event in entered.values())
    finally:
        release.set()
        finish(one)
        finish(two)
    assert first_result[0]["version"] == "first"
    assert second_result[0]["version"] == "second"


def test_changed_decoder_is_not_shared_with_waiter(monkeypatch, tmp_path):
    path = tmp_path / "decoder"
    path.write_bytes(b"before")
    entered, release = threading.Event(), threading.Event()
    calls = []

    def version(executable, cancel):
        calls.append(executable)
        if len(calls) == 1:
            entered.set()
            assert release.wait(10)
        return "synthetic"

    monkeypatch.setattr(ffmpeg_runtime, "version", version)
    owner, first = worker(path)
    assert entered.wait(10)
    guard = WatchingCancel()
    waiter, second = worker(path, guard)
    try:
        assert guard.waiting.wait(10)
        path.write_bytes(b"different decoder")
    finally:
        release.set()
        finish(owner)
        finish(waiter)
    assert isinstance(first[0], ValueError)
    assert "decoder changed" in str(first[0])
    assert second[0]["sha256"] == hashlib.sha256(b"different decoder").hexdigest()
    assert len(calls) == 2
