"""Ctrl+C must not wait forever for a paused browser download such as <audio> (B480)."""

import socket
import threading
import time

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse

from cbs_transcriber.__main__ import SHUTDOWN_DRAIN_SECONDS, server_for


def test_ctrl_c_has_a_bounded_graceful_shutdown_like_a_library_switch():
    # The exact browser connection that held the 20 November preview open was not reproduced
    # synthetically; the bound itself is the fix, as for library switches (SWITCH_DRAIN_SECONDS).
    server = server_for(FastAPI(), free_port())
    assert server.config.timeout_graceful_shutdown == SHUTDOWN_DRAIN_SECONDS
    assert 0 < SHUTDOWN_DRAIN_SECONDS <= 30


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_stop_finishes_while_a_paused_client_holds_a_large_file_response(tmp_path):
    # Like playback media: a FileResponse far larger than the client reads before pausing,
    # so the server's write waits on full socket buffers and the request never completes.
    media = tmp_path / "audio.mp3"
    with media.open("wb") as stream:
        for _ in range(96):
            stream.write(b"\0" * (1 << 20))
    app = FastAPI()
    lifespan_done = threading.Event()

    @app.get("/media")
    def playback():
        return FileResponse(media)

    @app.on_event("shutdown")
    def finished():
        lifespan_done.set()

    port = free_port()
    server = server_for(app, port)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.05)
    with httpx.Client() as client:
        with client.stream("GET", f"http://127.0.0.1:{port}/media", timeout=60) as response:
            assert next(response.iter_raw(5))
            time.sleep(1)  # The paused client stops reading; the server fills the buffers.
            server.should_exit = True  # What Ctrl+C sets.
            thread.join(SHUTDOWN_DRAIN_SECONDS + 10)
            assert not thread.is_alive(), "shutdown still waited for the paused download"
    # The app's own shutdown (worker drain, library release) still ran.
    assert lifespan_done.is_set()
