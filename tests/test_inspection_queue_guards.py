"""Reserved inspection work cannot fall through public ASR/media lifecycle paths (V138)."""

import pytest

from cbs_transcriber.store import new_id, now


def inspection(client, state):
    store = client.app.state.store
    project = store.create_project("Synthetic inspection boundary")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.execute(
        "UPDATE recordings SET status='ready',duration=3,notes='Keep this note' WHERE id=?",
        (recording["id"],),
    )
    identifier = new_id()
    # Deliberate raw fixture: public/general insertion must never create this reserved kind.
    store.execute(
        "INSERT INTO jobs(id,recording_id,kind,state,created,updated) VALUES(?,?,?,?,?,?)",
        (identifier, recording["id"], "source-inspect", state, now(), now()),
    )
    return store, recording["id"], identifier


def unchanged(store, recording_id, job_id):
    return (
        store.one("SELECT * FROM recordings WHERE id=?", (recording_id,)),
        store.one("SELECT * FROM jobs WHERE id=?", (job_id,)),
    )


@pytest.mark.parametrize("state", ["queued", "running", "failed", "completed"])
@pytest.mark.parametrize("action", ["cancel", "retry"])
def test_v138_public_actions_refuse_inspection_before_any_generic_dispatch(
    client, monkeypatch, state, action
):
    store, recording_id, job_id = inspection(client, state)
    before = unchanged(store, recording_id, job_id)

    def refuse(*args, **kwargs):
        pytest.fail("Internal inspection reached generic batch dispatch")

    monkeypatch.setattr("cbs_transcriber.runtime_batches.job_batch", refuse)
    response = client.post(f"/api/jobs/{job_id}/{action}", json={})
    assert response.status_code == 409
    assert "internal inspection worker" in response.json()["detail"]
    assert unchanged(store, recording_id, job_id) == before


@pytest.mark.parametrize("state", ["queued", "running", "completed"])
def test_v138_direct_queue_cancel_cannot_bypass_inspection_cas(client, state):
    store, recording_id, job_id = inspection(client, state)
    before = unchanged(store, recording_id, job_id)
    with pytest.raises(ValueError, match="internal inspection worker"):
        client.app.state.queue.cancel(job_id)
    assert unchanged(store, recording_id, job_id) == before


@pytest.mark.parametrize("playback_status", ["preparing", "ready"])
def test_v138_direct_queue_processing_refuses_before_media_or_provider_access(
    client, monkeypatch, playback_status
):
    store, recording_id, job_id = inspection(client, "running")
    store.execute("UPDATE recordings SET status=? WHERE id=?", (playback_status, recording_id))
    before = unchanged(store, recording_id, job_id)

    def refuse(*args, **kwargs):
        pytest.fail("Internal inspection reached the generic recording/media path")

    monkeypatch.setattr(store, "recording", refuse)
    with pytest.raises(ValueError, match="internal inspection worker"):
        client.app.state.queue.process(job_id)
    assert unchanged(store, recording_id, job_id) == before
