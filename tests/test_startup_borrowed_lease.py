"""V167: no-worker startup borrows a caller-held same-Store serving lease."""

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from cbs_transcriber.app import create_app


def test_v167_no_worker_startup_borrows_same_store_lease(tmp_path, credentials):
    app = create_app(tmp_path, start_worker=False, credentials=credentials)
    store = app.state.store

    def serve():
        with TestClient(app, headers={"X-CBS-Request": "1"}) as client:
            assert client.get("/api/health").status_code == 200
            assert store._lease_depth == 1

    # TestClient runs lifespan on another thread. Re-entering the caller's RLock
    # would block until this context exits, instead of borrowing its serving lease.
    with ThreadPoolExecutor(max_workers=1) as requests:
        with store.library_lock():
            opened = requests.submit(serve)
            opened.result(timeout=3)
        assert store._lease_depth == 0


def test_v167_no_worker_startup_takes_transient_lease_when_absent(
    tmp_path, credentials, monkeypatch
):
    app = create_app(tmp_path, start_worker=False, credentials=credentials)
    store = app.state.store
    observed = []
    recover = app.state.deletions.recover_files

    def recover_under_lease():
        observed.append(store._lease_depth)
        recover()

    monkeypatch.setattr(app.state.deletions, "recover_files", recover_under_lease)
    with TestClient(app, headers={"X-CBS-Request": "1"}) as client:
        assert client.get("/api/health").status_code == 200
        assert store._lease_depth == 0
    assert observed == [1] and store._lease_depth == 0
