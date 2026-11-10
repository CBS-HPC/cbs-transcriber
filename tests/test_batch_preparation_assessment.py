"""Bounded offline assessment arithmetic and real synthetic multi-file decoding."""

import hashlib
import runpy
from pathlib import Path

import pytest

RUNNER = runpy.run_path(str(Path(__file__).parents[1] / "scripts/evaluate_batch_preparation.py"))


def test_transfer_counterfactual_keeps_signed_payload_penalty():
    transfer = RUNNER["transfer_estimates"]
    [small] = transfer(1_000_000, 2_000_000, [5])
    assert small["original_payload_seconds"] == 1.6
    assert small["wav_payload_seconds"] == 3.2
    assert small["wav_minus_original_seconds"] == 1.6
    [large] = transfer(2_000_000, 1_000_000, [5])
    assert large["wav_minus_original_seconds"] == -1.6


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), 10_001])
def test_transfer_rate_must_be_explicit_finite_and_positive(value):
    with pytest.raises(ValueError):
        RUNNER["transfer_estimates"](100, 200, [value])


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_payload_byte_counts_are_nonnegative_integers(value):
    with pytest.raises(ValueError):
        RUNNER["transfer_estimates"](value, 200, [5])


def test_pcm_qualification_requires_exact_samples_and_geometry():
    reference = {
        "channels": 1,
        "sample_width": 2,
        "sample_rate": 16000,
        "frames": 48000,
        "pcm_sha256": "a" * 64,
    }
    exact = RUNNER["exact_pcm"]
    assert exact(dict(reference), reference)
    assert not exact(reference | {"frames": 48001}, reference)
    assert not exact(reference | {"pcm_sha256": "b" * 64}, reference)
    assert not exact(reference | {"channels": 2}, reference)


def test_existing_output_is_never_overwritten(tmp_path):
    marker = tmp_path / "report.json"
    marker.write_text("preserve existing assessment")
    with pytest.raises(FileExistsError):
        RUNNER["run_assessment"](tmp_path)
    assert marker.read_text() == "preserve existing assessment"


@pytest.mark.parametrize("changes", [{"counts": (100,)}, {"workers": (8,)}, {"seconds": 600}])
def test_benchmark_limits_are_checked_before_any_output(tmp_path, changes):
    target = tmp_path / "not-created"
    with pytest.raises(ValueError):
        RUNNER["run_assessment"](target, **changes)
    assert not target.exists()


def test_real_four_file_parallel_decode_preserves_pcm(tmp_path, wav_bytes):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "prepared").mkdir()
    files = []
    for index in range(4):
        identifier = f"file-{index + 1:02d}"
        filename = f"inputs/{identifier}.wav"
        (tmp_path / filename).write_bytes(wav_bytes)
        files.append({"id": identifier, "file": filename, "profile": "wav"})
    result = RUNNER["prepare_batch"](RUNNER["Experiment"](tmp_path), files, 2, "test")
    assert result["file_count"] == 4 and result["local_workers"] == 2
    assert result["batch_wall_seconds"] > 0
    assert result["python_process_cpu_seconds"] >= 0
    assert result["ffmpeg_cpu_seconds_sum"] is None or result["ffmpeg_cpu_seconds_sum"] >= 0
    expected = hashlib.sha256(b"\x00\x00" * 48000).hexdigest()
    assert [item["pcm"]["frames"] for item in result["files"]] == [48000] * 4
    assert {item["pcm"]["pcm_sha256"] for item in result["files"]} == {expected}
