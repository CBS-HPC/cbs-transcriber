"""Original-backed native work can finish while its playback reader remains active."""

import threading

from test_native_batch import batch_remote as batch_remote
from test_native_batch import enqueue
from test_native_batch import selection as selection
from test_ucloud_native import PAYLOAD
from test_ucloud_native import remote as remote

from cbs_transcriber import media


def test_native_inspection_upload_and_results_do_not_wait_for_active_playback_reader(
    client, batch_remote, selection, wav_bytes, monkeypatch
):
    provider, recordings = selection  # Retains the same-Store lease through both workers.
    store, queue = client.app.state.store, client.app.state.queue
    batch_remote.store = store
    recording = recordings[0]
    directory = store.directory(recording["id"])
    original = directory / "original.wav"
    store.execute("UPDATE recordings SET status='preparing' WHERE id=?", (recording["id"],))
    playback = store.add_job(recording["id"], "media")
    entered, release = threading.Event(), threading.Event()
    real_prepare = media.prepare_media

    def held_reader(folder, filename, cancel):
        assert folder == directory and store._lease_depth > 0
        with original.open("rb") as reader:
            assert reader.read(1) == wav_bytes[:1]
            entered.set()
            assert release.wait(60), "Native work did not finish while playback was held"
            assert reader.read() == wav_bytes[1:]
        return real_prepare(folder, filename, cancel)

    monkeypatch.setattr(media, "prepare_media", held_reader)
    worker = next(worker for worker in queue.workers if worker.kind == "media")
    worker._start()
    try:
        assert entered.wait(30), "Playback did not open its synthetic original"
        assert store.job(playback["id"])["state"] == "running"
        assert not (directory / "audio.mp3").exists()

        jobs = enqueue(client, provider, recordings)  # Real inspection and admission CAS.
        batch_id = jobs[0]["params"]["native_batch_id"]
        assert {job["params"]["native_batch_id"] for job in jobs} == {batch_id}
        claimed = store.claim_job("transcribe")
        assert claimed is not None
        queue.active_batch_id = batch_id
        queue.process(claimed["id"])

        assert not release.is_set() and worker.thread.is_alive()
        assert store.job(playback["id"])["state"] == "running"
        assert store.recording(recording["id"])["status"] == "preparing"
        assert not (directory / "audio.mp3").exists()
        assert len(batch_remote.submissions) == 1
        assert [bytes(upload) for upload in batch_remote.uploads] == [wav_bytes, wav_bytes]
        assert store.native_batch(batch_id)["state"] == "completed"
        assert {store.job(job["id"])["state"] for job in jobs} == {"completed"}
        for row in recordings:
            (result,) = store.results(row["id"])
            saved = store.result(row["id"], result["id"])
            assert saved["segments"][0]["text"] == PAYLOAD["segments"][0]["text"]
    finally:
        # Stop further claims without cancelling the accepted playback conversion.
        worker.stopping.set()
        release.set()
        worker.wakeup.set()
        worker.thread.join(30)
        assert not worker.thread.is_alive(), "Playback did not drain under its serving lease"

    assert store._lease_depth > 0
    assert store.job(playback["id"])["state"] == "completed"
    assert store.recording(recording["id"])["status"] == "ready"
    assert (directory / "audio.mp3").is_file()
    assert original.read_bytes() == wav_bytes
    assert all(len(store.results(row["id"])) == 1 for row in recordings)
