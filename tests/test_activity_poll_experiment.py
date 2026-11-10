"""Small harness/oracle checks; large wall-time measurements are explicitly manual."""

import importlib.util
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/benchmark_activity_poll.py"
spec = importlib.util.spec_from_file_location("activity_poll_experiment", SCRIPT)
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


@pytest.mark.parametrize("values", [(0, 10, 1), (200, 51, 1), (10, 10, 1), (200, 10, 10)])
def test_experiment_refuses_unbounded_or_non_scale_configuration(values):
    with pytest.raises(ValueError):
        experiment.bounds(*values)


def test_exact_membership_oracle_refuses_duplicates_foreign_and_private_content():
    expected = {"a": {"project_id": "p", "recording_id": "r", "upload_batch": "u"}}
    row = {
        "id": "a",
        **expected["a"],
        "kind": "media",
        "state": "completed",
        "progress": 1,
        "activity_membership_complete": True,
        "activity_group_limit": 1000,
    }
    experiment.verify([row], expected)
    for bad in (
        [],
        [row, row],
        [row | {"project_id": "foreign"}],
        [row | {"message": experiment.CANARY}],
        [row | {"activity_membership_complete": False}],
    ):
        with pytest.raises(AssertionError):
            experiment.verify(bad, expected)


def test_inherited_private_catalog_is_refused_before_app_or_fixture(monkeypatch):
    monkeypatch.setenv("CBS_TRANSCRIBER_MODELS", "synthetic-unused-canary")
    with pytest.raises(ValueError, match="CBS_TRANSCRIBER_MODELS"):
        experiment.run(samples=1)


def test_optimized_python_refuses_before_emitting_a_success_report():
    result = subprocess.run(
        [sys.executable, "-O", str(SCRIPT)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode != 0 and result.stdout == ""
    assert "correctness assertions are required" in result.stderr


def test_wrong_imported_checkout_is_refused_before_opening_a_library(monkeypatch, tmp_path):
    from cbs_transcriber import app

    monkeypatch.setattr(app, "__file__", str(tmp_path / "wrong-checkout/app.py"))

    def unopened(*args, **kwargs):
        raise AssertionError("The mismatched application must not open a library")

    monkeypatch.setattr(app, "create_app", unopened)
    with pytest.raises(ValueError, match="matching benchmark checkout"):
        experiment.run(samples=1)


@pytest.mark.parametrize("operation", ["dns", "datagram"])
def test_network_tripwires_refuse_before_the_underlying_operation(monkeypatch, operation):
    from cbs_transcriber import app

    def unguarded(*args, **kwargs):
        raise AssertionError("Underlying synthetic canary reached; no network operation was made")

    # The fallback canary also refuses: this regression can never perform a real lookup/send.
    monkeypatch.setattr(socket, "getaddrinfo", unguarded)
    monkeypatch.setattr(socket.socket, "sendto", unguarded)

    def probe(*args, **kwargs):
        if operation == "dns":
            socket.getaddrinfo("synthetic.invalid", 443)
        else:
            socket.socket.sendto(None, b"SYNTHETIC", ("192.0.2.1", 9))

    monkeypatch.setattr(app, "create_app", probe)
    with pytest.raises(AssertionError, match="Activity experiment must not"):
        experiment.run(samples=1)


def test_small_real_endpoint_smoke_expands_aged_members_and_preserves_database():
    observed = experiment.run(groups=10, members=21, samples=1)
    assert observed["history_rows"] == 211
    assert observed["samples"][0]["returned_rows"] == 210
    assert observed["exact_membership_and_project_scope"] is True
    assert observed["database_sql_dump_unchanged"] is True
    assert observed["sql_statements_in_separate_poll"]["WITH"] >= 1


def test_fresh_cli_smoke_imports_and_polls_without_children_or_cached_platform_state():
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--groups", "10", "--members", "21", "--samples", "1"],
        env={**os.environ, "PYTHONPATH": str(SCRIPT.parents[1] / "src")},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["history_rows"] == 211
    assert observed["samples"][0]["returned_rows"] == 210
    assert observed["environment"]["os"] == experiment.SYSTEM
    assert observed["database_sql_dump_unchanged"] is True
