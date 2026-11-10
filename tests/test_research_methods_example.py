"""A genuine report example, with independent content/privacy and failure oracles."""

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "docs/examples/research_methods_demo.py"
spec = importlib.util.spec_from_file_location("research_methods_demo", SCRIPT)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


@pytest.fixture
def generated(tmp_path):
    output = tmp_path / "example"
    manifest = demo.build(output)
    return output, manifest


def test_pack_is_complete_and_hashes_actual_bytes_without_distributing_database(generated):
    output, manifest = generated
    assert set(manifest["files"]) == {
        "metadata-report.zip",
        "selected-text-report.zip",
        "oracle.json",
        "START-HERE.md",
    }
    assert set(path.name for path in output.iterdir()) == set(manifest["files"]) | {
        "example-manifest.json"
    }
    for name, receipt in manifest["files"].items():
        data = (output / name).read_bytes()
        assert len(data) == receipt["bytes"]
        assert hashlib.sha256(data).hexdigest() == receipt["sha256"]
    assert json.loads((output / "example-manifest.json").read_bytes()) == manifest


def test_default_and_opt_in_reports_use_frozen_working_payload_and_explicit_gaps(generated):
    output, _ = generated
    oracle = json.loads((output / "oracle.json").read_bytes())
    metadata, metadata_files = demo.unpack((output / "metadata-report.zip").read_bytes())
    report, files = demo.unpack((output / "selected-text-report.zip").read_bytes())
    assert set(metadata_files) == {"manifest.json", "methods.md"}
    assert metadata["inclusion"]["selected_transcript_text"] is False
    assert report["selection"]["skipped_unassigned_recordings"] == 1
    [selected] = [version for version in report["versions"] if version["selected"]]
    assert selected["action"] == "enhancement_result" and selected["sequence"] == 1
    assert selected["working_designation"]["selected_version_is_working"] is True
    assert selected["payload_sha256"] == oracle["working_payload_sha256"]
    assert selected["payload_sha256"] != oracle["active_payload_sha256"]
    assert oracle["working_reselected_after_edit"] is True
    source = next(v for v in report["versions"] if v["id"] == selected["source_version"])
    assert source["sequence"] == 3 and source["action"] == "speaker_rename"
    assert source["payload_sha256"] == oracle["frozen_source_payload_sha256"]
    assert report["manual_change_counts"] == {"free_rewrite": 1, "speaker_rename": 1}
    [export] = report["exports"]
    text = files[export["path"]].decode("utf-8-sig")
    assert "Hej København 🧪.\r\nVi bevarer cafe\u0301 og café." in text
    assert "The reviewed English wording remains available." in text
    assert "Synthetic voice A" in text and "Synthetic voice B" in text
    assert selected["processing"]["model"] == "synthetic-model-not-executed"
    assert "producer_artifacts" not in selected and selected["provenance_gaps"]
    assert oracle["model_executed"] is False and oracle["producer_capture"] == "not_captured"
    for content in (*metadata_files.values(), *files.values()):
        assert b"PRIVATE-" not in content and b"UNSELECTED-" not in content
    assert b"Hej K" not in b"".join(metadata_files.values())


def test_existing_output_is_preserved_and_catalog_override_creates_nothing(tmp_path, monkeypatch):
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_bytes(b"existing caller data")
    with pytest.raises(FileExistsError):
        demo.build(output)
    assert list(output.iterdir()) == [sentinel] and sentinel.read_bytes() == b"existing caller data"
    monkeypatch.setenv("CBS_TRANSCRIBER_MODELS", "synthetic-unused-private-catalog")
    with pytest.raises(ValueError, match="CBS_TRANSCRIBER_MODELS"):
        demo.build(tmp_path / "refused")
    assert not (tmp_path / "refused").exists()


def test_wrong_export_bytes_prevent_a_completion_manifest(tmp_path, monkeypatch):
    from cbs_transcriber import research_reports

    original = research_reports.project_report

    def damaged(*args, **kwargs):
        raw = original(*args, **kwargs)
        if not kwargs.get("include_selected_transcripts"):
            return raw
        report, files = demo.unpack(raw)
        files[report["exports"][0]["path"]] = b"wrong synthetic export bytes"
        stream = io.BytesIO()
        with ZipFile(stream, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        return stream.getvalue()

    monkeypatch.setattr(research_reports, "project_report", damaged)
    output = tmp_path / "incomplete"
    with pytest.raises(AssertionError):
        demo.build(output)
    assert output.is_dir() and not (output / "example-manifest.json").exists()


def test_mismatched_report_module_is_refused_before_output_creation(tmp_path, monkeypatch):
    from cbs_transcriber import research_reports

    monkeypatch.setattr(
        research_reports, "__file__", str(tmp_path / "wrong-checkout/research_reports.py")
    )
    output = tmp_path / "mismatched"
    with pytest.raises(ValueError, match="this example's checkout"):
        demo.build(output)
    assert not output.exists()


def test_optimized_cli_cannot_emit_unchecked_example(tmp_path):
    output = tmp_path / "optimized"
    result = subprocess.run(
        [sys.executable, "-O", str(SCRIPT), str(output)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0 and "example checks are required" in result.stderr
    assert not output.exists()


def test_fresh_cli_builds_only_disposable_synthetic_material(tmp_path):
    output = tmp_path / "cli"
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), str(output)],
        env={**os.environ, "PYTHONPATH": str(SCRIPT.parents[2] / "src")},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "no real processing" in result.stdout
    assert (output / "example-manifest.json").is_file()
