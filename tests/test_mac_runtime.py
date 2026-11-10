import pytest

from cbs_transcriber import enhancement
from cbs_transcriber import enhancement_worker as worker


def test_intel_mac_doctor_reports_unsupported_pinned_runtime(monkeypatch):
    monkeypatch.setattr(worker.sys, "platform", "darwin")
    monkeypatch.setattr(worker.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(worker.importlib.metadata, "version", lambda name: worker.WHISPERX_VERSION)
    result = worker.doctor()
    assert result["unsupported_platform"] and not result["ready"]


def test_intel_mac_preflight_does_not_suggest_reinstall(monkeypatch):
    monkeypatch.setattr(
        enhancement,
        "check_runtime",
        lambda *a, **kw: {
            "unsupported_platform": True,
            "ready": False,
            "note": "Use UCloud enhancement on Intel macOS.",
        },
    )
    with pytest.raises(ValueError, match="UCloud enhancement") as error:
        enhancement.require_local_runtime(enhancement.EnhancementOptions())
    assert "uv sync" not in str(error.value)
