import sys
from types import SimpleNamespace

import pytest

from cbs_transcriber.enhancement_worker import detect_gpu, doctor


@pytest.mark.parametrize("available", [False, True])
def test_gpu_detection_reports_runtime_access_and_device_memory(monkeypatch, available):
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: available,
                device_count=lambda: 1,
                get_device_name=lambda index: "Synthetic NVIDIA GPU",
                get_device_properties=lambda index: SimpleNamespace(total_memory=8 * 1024**3),
            )
        ),
    )
    gpu = detect_gpu()
    assert gpu["checked"] is True
    assert gpu["available"] is available
    assert gpu["devices"] == (
        [{"name": "Synthetic NVIDIA GPU", "memory_gb": 8.0}] if available else []
    )
    assert "gpu" not in doctor()


def test_missing_torch_is_unknown_hardware_without_crashing(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    assert detect_gpu() == {"checked": False, "available": False, "devices": []}
    assert doctor(hardware=True)["gpu"]["checked"] is False
