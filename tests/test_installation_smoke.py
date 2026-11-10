"""Offline installation-helper evidence uses synthetic libraries, never a user's library."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def helper():
    source = Path(__file__).parents[1] / "scripts" / "check_installation.py"
    spec = importlib.util.spec_from_file_location("installation_smoke_helper", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_standalone_smoke_uses_production_loopback_without_pytest_overrides(tmp_path):
    source = Path(__file__).parents[1] / "scripts" / "check_installation.py"
    # A child has no conftest TestClient override; the actual CLI must pass on its own.
    env = dict(os.environ)
    for key in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME", "TMP", "TEMP"):
        env[key] = str(tmp_path)
    env["CBS_TRANSCRIBER_PROFILE"] = "full"
    result = subprocess.run(
        [sys.executable, "-B", str(source)],
        cwd=source.parents[1],
        env=env,
        capture_output=True,
        timeout=120,
        check=False,
    )
    report = json.loads(result.stdout.decode("utf-8"))
    assert result.returncode == 0, report
    assert report["core_status"] == "pass"
    assert all(value == "pass" for value in report["checks"].values())
    assert str(tmp_path) not in json.dumps(report)


def test_synthetic_smoke_is_offline_redacted_and_restores_environment(
    helper, tmp_path, monkeypatch
):
    from cbs_transcriber import app, ucloud_native

    private = tmp_path / "private-user-library"
    monkeypatch.setenv("CBS_TRANSCRIBER_DATA_DIR", str(private))
    monkeypatch.setenv("CBS_TRANSCRIBER_MODELS", str(private / "private-models.json"))
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", str(private / "unowned-ffmpeg"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "original-value")

    def forbidden(*args, **kwargs):
        pytest.fail("The smoke check reached credentials or a network transport")

    monkeypatch.setattr(app, "Credentials", forbidden)
    monkeypatch.setattr(ucloud_native, "client_for", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    old_temp, old_bytecode = tempfile.tempdir, sys.dont_write_bytecode
    report = helper.run_checks(temp_parent=tmp_path)
    assert report["core_status"] == "pass", report
    assert all(value == "pass" for value in report["checks"].values())
    assert report["ffmpeg"]["source"] == "bundled"
    assert report["local_inference"] == "not_tested" and report["gpu"] == "not_checked"
    assert report["browser_playback"] == report["background_scheduling"] == "not_tested"
    assert not private.exists() and not list(tmp_path.iterdir())
    raw = json.dumps(report)
    assert str(tmp_path) not in raw and "private-user-library" not in raw
    assert helper.os.environ["CBS_TRANSCRIBER_DATA_DIR"] == str(private)
    assert helper.os.environ["CBS_TRANSCRIBER_MODELS"] == str(private / "private-models.json")
    assert helper.os.environ["IMAGEIO_FFMPEG_EXE"] == str(private / "unowned-ffmpeg")
    assert helper.os.environ["HF_HUB_OFFLINE"] == "original-value"
    assert tempfile.tempdir == old_temp and sys.dont_write_bytecode == old_bytecode


@pytest.mark.parametrize(
    "machine,release,expected",
    [
        ("x86_64", "13.6.1", "intel_mac_core_only"),
        ("arm64", "14.0", "cpu_local_packages_expected"),
        ("arm64", "13.6", "macos_14_or_later_required"),
    ],
)
def test_mac_policy_is_metadata_only(helper, monkeypatch, machine, release, expected):
    monkeypatch.setattr(helper.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(helper.platform, "machine", lambda: machine)
    monkeypatch.setattr(helper.platform, "mac_ver", lambda: (release, (), machine))
    assert helper.platform_status()["local_package_policy"] == expected


def test_failed_probe_uses_fixed_error_code_and_retains_no_paths(helper, tmp_path, monkeypatch):
    def fail():
        raise OSError("C:/Users/private-user/private-token")

    monkeypatch.setattr(helper, "bundled_ffmpeg", fail)
    report = helper.run_checks(temp_parent=tmp_path)
    assert report["core_status"] == "fail" and report["error"] == "ffmpeg_failed"
    assert "private" not in json.dumps(report)
    assert not list(tmp_path.iterdir())


def test_version_metadata_omits_local_labels(helper, monkeypatch):
    monkeypatch.setattr(helper.metadata, "version", lambda name: "2.8.0+private-user")
    assert helper.package_status("torch") == {"installed": True, "version": "2.8.0"}


def test_report_is_utf8_exclusive_and_existing_file_refuses_before_smoke(
    helper, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(helper, "run_checks", lambda: {"core_status": "pass", "marker": "æøå"})
    target = tmp_path / "report.json"
    assert helper.main(["--report", str(target)]) == 0
    original = target.read_bytes()
    assert json.loads(original.decode("utf-8"))["marker"] == "æøå"
    capsys.readouterr()
    monkeypatch.setattr(
        helper, "run_checks", lambda: pytest.fail("An existing file must refuse first")
    )
    assert helper.main(["--report", str(target)]) == 2
    assert target.read_bytes() == original
    assert str(tmp_path) not in capsys.readouterr().err


def test_missing_core_package_is_not_a_pass(helper, tmp_path, monkeypatch):
    real = helper.package_status
    monkeypatch.setattr(
        helper,
        "package_status",
        lambda name: {"installed": False, "version": None} if name == "uvicorn" else real(name),
    )
    monkeypatch.setattr(helper, "bundled_ffmpeg", lambda: {"status": "pass"})
    monkeypatch.setattr(
        helper, "core_smoke", lambda directory, checks: checks.update(dict.fromkeys(checks, "pass"))
    )
    assert helper.run_checks(temp_parent=tmp_path)["core_status"] == "fail"


def test_intel_mac_core_policy_does_not_require_local_packages(helper, tmp_path, monkeypatch):
    monkeypatch.setattr(helper.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(helper.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(helper.platform, "mac_ver", lambda: ("13.6", (), "x86_64"))
    monkeypatch.setattr(
        helper,
        "package_status",
        lambda name: {
            "installed": name in helper.CORE,
            "version": "1.0" if name in helper.CORE else None,
        },
    )
    monkeypatch.setattr(helper, "bundled_ffmpeg", lambda: {"status": "pass"})
    monkeypatch.setattr(
        helper, "core_smoke", lambda directory, checks: checks.update(dict.fromkeys(checks, "pass"))
    )
    report = helper.run_checks(temp_parent=tmp_path)
    assert report["core_status"] == "pass"
    assert report["platform"]["local_package_policy"] == "intel_mac_core_only"
    assert not any(report["packages"][name]["installed"] for name in helper.LOCAL)
    assert report["real_device_acceptance"] == "not_established"


def test_unexpected_collection_failure_has_no_exception_text(helper, monkeypatch, capsys):
    def fail():
        raise RuntimeError("/Users/private-user/private-key")

    monkeypatch.setattr(helper, "run_checks", fail)
    assert helper.main([]) == 1
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["error"] == "report_collection_failed"
    assert "private" not in output.out and not output.err


def test_bundled_probe_never_calls_locator_or_system_fallback(helper, monkeypatch):
    import imageio_ffmpeg
    from imageio_ffmpeg import _definitions, _utils

    def forbidden(*args, **kwargs):
        pytest.fail("Bundled selection must not execute imageio's locator or fallback probes")

    monkeypatch.setattr(imageio_ffmpeg, "get_ffmpeg_exe", forbidden)
    monkeypatch.setattr(_utils, "_get_ffmpeg_exe", forbidden)
    calls = []

    def bounded(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, b"ffmpeg version 7.1", b"")

    monkeypatch.setattr(helper.subprocess, "run", bounded)
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "unowned-private-override")
    assert helper.bundled_ffmpeg() == {"status": "pass", "source": "bundled", "version": "7.1"}
    expected = (
        Path(imageio_ffmpeg.__file__).resolve().parent
        / "binaries"
        / (_definitions.FNAME_PER_PLATFORM[_definitions.get_platform()])
    )
    assert len(calls) == 1 and calls[0][0] == [str(expected), "-version"]
    assert calls[0][1]["timeout"] == 10


def test_absent_bundle_refuses_before_any_probe_or_fallback(helper, tmp_path, monkeypatch):
    import imageio_ffmpeg
    from imageio_ffmpeg import _utils

    monkeypatch.setattr(imageio_ffmpeg, "__file__", str(tmp_path / "package" / "__init__.py"))

    def forbidden(*args, **kwargs):
        pytest.fail("A missing bundled binary must not start a candidate, conda or PATH probe")

    monkeypatch.setattr(imageio_ffmpeg, "get_ffmpeg_exe", forbidden)
    monkeypatch.setattr(_utils, "_get_ffmpeg_exe", forbidden)
    monkeypatch.setattr(helper.subprocess, "run", forbidden)
    report = helper.run_checks(temp_parent=tmp_path)
    assert report["core_status"] == "fail" and report["error"] == "ffmpeg_failed"
    assert report["ffmpeg"] == {"status": "fail"}


def test_bundled_probe_timeout_is_bounded_and_redacted(helper, tmp_path, monkeypatch):
    import imageio_ffmpeg

    calls = []

    def forbidden(*args, **kwargs):
        pytest.fail("A timeout must not retry through the imageio locator")

    def timeout(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.TimeoutExpired("/Users/private-user/private-key", kwargs["timeout"])

    monkeypatch.setattr(helper.subprocess, "run", timeout)
    monkeypatch.setattr(imageio_ffmpeg, "get_ffmpeg_exe", forbidden)
    report = helper.run_checks(temp_parent=tmp_path)
    assert len(calls) == 1 and calls[0][1]["timeout"] == 10
    assert report["core_status"] == "fail" and report["error"] == "ffmpeg_failed"
    assert "private" not in json.dumps(report) and str(tmp_path) not in json.dumps(report)
