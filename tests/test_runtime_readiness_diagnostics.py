"""Later readiness failures must not deny already transferred private inputs."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import remote_audio_input
from cbs_transcriber import runtime_batches as batches
from cbs_transcriber.enhancement import EnhancementOptions


@pytest.mark.parametrize("already_uploaded", [False, True], ids=["first", "later-member"])
@pytest.mark.parametrize("missing", ["model", "decoder"])
def test_v114_readiness_refusal_preserves_prior_upload_truth_and_blocks_next_transfer(
    monkeypatch, already_uploaded, missing
):
    owner = object.__new__(batches.Coordinator)
    owner.client, owner.server, owner.remote = None, "https://synthetic.invalid", "/drive/owned"
    owner.worker = SimpleNamespace()
    owner.options = EnhancementOptions(execution="ucloud", device="cuda", transcribe=True)
    owner.batch = {"identity": {}, "journal": {}}
    owner.id, owner.store = "synthetic", None
    owner.guard = lambda: None
    owner.fresh = lambda: owner.batch
    owner.project_status = lambda: None
    owner.checkpoint = lambda **values: owner.batch["journal"].update(values)
    transfers = []
    fail_readiness = False

    def read_json(client, server, path):
        if path.endswith("doctor.json"):
            return {
                "version": api.WHISPERX_VERSION,
                "ready": True,
                "gpu": {"available": True, "devices": [{"memory_gb": 23}]},
            }
        if path.endswith("decoder.json"):
            return None
        return {
            "models": {}
            if fail_readiness
            else dict.fromkeys(api.required_models(owner.options), True),
            "punkt_tab": True,
        }

    monkeypatch.setattr(batches, "_observe", lambda *args: (owner.batch, "RUNNING"))
    monkeypatch.setattr(batches, "_status", lambda *args: {"phase": "ready"})
    monkeypatch.setattr(api, "read_json", read_json)
    monkeypatch.setattr(
        batches.native,
        "upload",
        lambda client, server, path, target, *args: transfers.append(target),
    )
    monkeypatch.setattr(batches, "require_code", lambda *_: None)
    monkeypatch.setattr(remote_audio_input, "for_job", lambda *_: {"protocol": "synthetic"})
    source = Path("synthetic-audio.wav")  # The fake transfer never reads media from disk.
    if already_uploaded:
        owner.upload(source, owner.remote + "/tasks/first/audio.wav", private=True)
    previous = list(transfers)
    fail_readiness = True
    with pytest.raises(api.RuntimeNotReady, match="Check UCloud runtime preparation") as failure:
        if missing == "model":
            owner.upload(source, owner.remote + "/tasks/second/audio.wav", private=True)
        else:
            owner.publish(SimpleNamespace(job={"params": {}}, folder=Path("synthetic-member")), {})
    assert transfers == previous
    assert bool(transfers) is already_uploaded
    message = str(failure.value)
    assert "no recording was uploaded" not in message
    assert "no private recording was uploaded" not in message
    assert owner.remote not in message and owner.server not in message
