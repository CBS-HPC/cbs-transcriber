"""V242/B481: advisory progress survives Windows readers holding both names."""

import json
import os

import pytest

from cbs_transcriber import enhancement_worker


@pytest.mark.skipif(os.name != "nt", reason="Windows readers deny replacement and unlink")
def test_busy_pending_progress_file_never_fails_the_worker(tmp_path):
    progress = tmp_path / "progress.json"
    pending = progress.with_suffix(".pending")
    enhancement_worker.write_json(progress, {"stage": "loading", "fraction": 0.05})
    pending.write_text("{}", encoding="utf-8")
    # Ordinary readers allow writing but deny replacing/deleting these Windows names.
    # The old fallback caught the replace refusal then leaked the cleanup refusal.
    with progress.open("rb"), pending.open("rb"):
        assert not enhancement_worker.write_progress(tmp_path, "aligning", 0.3, attempts=1, pause=0)
        assert json.loads(progress.read_text(encoding="utf-8"))["stage"] == "loading"
        assert pending.is_file()
        # The mandatory JSON writer still propagates the same real sharing violation.
        with pytest.raises(PermissionError):
            enhancement_worker.write_json(progress, {"stage": "complete", "fraction": 1})
    # After readers close, a later observation replaces the stale advisory file normally.
    assert enhancement_worker.write_progress(tmp_path, "aligning", 0.3, attempts=1, pause=0)
    assert json.loads(progress.read_text(encoding="utf-8"))["stage"] == "aligning"
    assert not pending.exists()
