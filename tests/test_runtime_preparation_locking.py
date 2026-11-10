"""Exercise supervisor decisions in Bash; actual flock concurrency also runs on Linux."""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import providers


def bash_executable():
    candidates = [shutil.which("bash")]
    if os.name == "nt":
        candidates.append(str(Path.home() / "AppData/Local/Programs/Git/bin/bash.exe"))
        candidates.append("C:/Program Files/Git/bin/bash.exe")
    return next(
        (
            path
            for path in candidates
            if path and "system32" not in path.lower() and Path(path).is_file()
        ),
        None,
    )


@pytest.fixture
def bash():
    executable = bash_executable()
    if executable is None:
        pytest.skip("A local Bash executable is not installed.")
    return executable


def settings():
    return providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        ucloud_transcriber={"storage_path": "/synthetic/runtime"},
    )


def quoted(path):
    return shlex.quote(Path(path).as_posix())


def generated(tmp_path, *, prepare=False, batch=False, stamp=True, lock=True, prepare_only=False):
    runtime, run = tmp_path / "runtime", tmp_path / "run"
    runtime.mkdir()
    run.mkdir()
    if stamp:
        (runtime / "stamp.json").write_text(
            '{"requirements_sha256":"' + api.requirements_hash() + '"}', encoding="utf-8"
        )
    if lock:
        (runtime / ".in-use").write_text("stable inode", encoding="utf-8")
    script = api.supervisor_script(
        settings(), "synthetic", prepare, batch=batch, prepare_only=prepare_only
    )
    script = re.sub(r"^RUN=.*$", "RUN=" + quoted(run), script, count=1, flags=re.M)
    script = re.sub(r"^RT=.*$", "RT=" + quoted(runtime), script, count=1, flags=re.M)
    return script, runtime, run


def functions(script):
    return script.split('[ -d "$RUN" ] || exit 1', 1)[0]


def execute(bash, tmp_path, script):
    path = tmp_path / "exercise.sh"
    path.write_text(script, encoding="utf-8", newline="\n")
    result = subprocess.run([bash, str(path)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result


def test_runtime_supervisor_variants_have_valid_bash_and_no_placeholders(bash, tmp_path):
    for batch in (False, True):
        script = api.supervisor_script(settings(), "synthetic", True, ["da"], batch=batch)
        assert not re.search(r"__[A-Z0-9_]+__", script)
        assert "\r" not in script
        assert f"BATCH={int(batch)}" in script
        run = "cbs-runtime-batch-synthetic" if batch else "cbs-enhancement-synthetic"
        assert f"RUN=/work/{run}" in script
        path = tmp_path / f"supervisor-{batch}.sh"
        path.write_text(script, encoding="utf-8", newline="\n")
        result = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_preparation_rechecks_stamp_after_claim_and_preserves_lock_inode(bash, tmp_path):
    script, runtime, run = generated(tmp_path, prepare=True, stamp=False)
    original = (runtime / ".in-use").stat()
    # A competing preparation completes after our first stamp check but before our claim.
    execute(
        bash,
        tmp_path,
        functions(script)
        + """
flock() { return 0; }
take_lock() {
  mkdir "$LOCK" || return 1
  HOLDING_LOCK=1
  printf '%s' '{"requirements_sha256":"DIGEST"}' > "$RT/stamp.json"
}
install_runtime() { touch "$RUN/destructive-install"; }
fetch_models() { touch "$RUN/models-fetched"; }
prepare || exit 1
""".replace("DIGEST", api.requirements_hash()),
    )
    assert (run / "models-fetched").is_file()
    assert not (run / "destructive-install").exists()
    assert (runtime / ".in-use").read_text(encoding="utf-8") == "stable inode"
    assert (runtime / ".in-use").stat().st_ino == original.st_ino
    assert not (runtime / ".prepare-lock").exists()


def test_destructive_preparation_refuses_busy_reader_without_removing_runtime(bash, tmp_path):
    script, runtime, run = generated(tmp_path, prepare=True, stamp=False)
    sentinel = runtime / "venv"
    sentinel.mkdir()
    execute(
        bash,
        tmp_path,
        functions(script)
        + """
flock() { [ "$1" != '-xn' ]; }
install_runtime() { rm -rf "$RT/venv"; touch "$RUN/destructive-install"; }
prepare
[ "$?" = 2 ] || exit 1
""",
    )
    assert sentinel.is_dir()
    assert not (run / "destructive-install").exists()


def test_preparation_never_steals_an_old_lock(bash, tmp_path):
    script, runtime, run = generated(tmp_path, prepare=True, stamp=False)
    lock = runtime / ".prepare-lock"
    lock.mkdir()
    (lock / "owner").write_text("other-generation", encoding="utf-8")
    os.utime(lock, (1, 1))
    execute(
        bash,
        tmp_path,
        functions(script)
        + """
install_runtime() { touch "$RUN/destructive-install"; }
prepare
[ "$?" = 2 ] || exit 1
""",
    )
    assert (lock / "owner").read_text(encoding="utf-8") == "other-generation"
    assert not (run / "destructive-install").exists()


def test_standalone_preparation_reports_busy_without_touching_unowned_lock(bash, tmp_path):
    script, runtime, run = generated(tmp_path, prepare=True, prepare_only=True, stamp=False)
    lock = runtime / ".prepare-lock"
    lock.mkdir()
    (lock / "owner").write_text("other-generation", encoding="utf-8")
    execute(bash, tmp_path, script)
    assert json.loads((run / "supervisor.json").read_text(encoding="utf-8")) == {
        "phase": "failed",
        "code": "runtime-busy",
        "generation": "synthetic",
    }
    assert (lock / "owner").read_text(encoding="utf-8") == "other-generation"
    assert not (run / "prepare.log").exists()
    assert not (runtime / "stamp.json").exists()


def test_prepared_readonly_runtime_requires_existing_lock(bash, tmp_path):
    script, runtime, run = generated(tmp_path, lock=False)
    execute(bash, tmp_path, script)
    assert json.loads((run / "supervisor.json").read_text(encoding="utf-8")) == {
        "phase": "failed",
        "code": "runtime-lock-missing",
        "generation": "synthetic",
    }
    assert not (runtime / ".in-use").exists()


def test_explicit_preparation_creates_missing_lock_without_reinstalling_valid_runtime(
    bash, tmp_path
):
    script, runtime, run = generated(tmp_path, prepare=True, lock=False)
    execute(
        bash,
        tmp_path,
        functions(script)
        + """
flock() { return 0; }
install_runtime() { touch "$RUN/destructive-install"; }
fetch_models() { touch "$RUN/models-fetched"; }
prepare || exit 1
""",
    )
    assert (runtime / ".in-use").is_file()
    assert (runtime / ".in-use").read_bytes() == b""
    assert (run / "models-fetched").is_file()
    assert not (run / "destructive-install").exists()


@pytest.mark.parametrize("batch", [False, True])
def test_ready_supervisor_dispatches_matching_controller(bash, tmp_path, batch):
    script, runtime, run = generated(tmp_path, batch=batch)
    python = runtime / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text(
        """#!/usr/bin/env bash
case "$*" in
  *--doctor*) printf '%s' '{"ready":true}' ;;
  *--cache-status*) printf '%s' '{"models":{}}' ;;
  *capability*) printf '%s' '{"synthetic":"decoder-capability"}' ;;
  *)
    [ "$CBS_RUNTIME_LOCK_FD" = 9 ] || exit 1
    [ -r /dev/fd/9 ] || exit 1
    printf '%s\\n' "$@" > "${4}/invocation"
    ;;
esac
""",
        encoding="utf-8",
        newline="\n",
    )
    python.chmod(0o755)
    (run / "manifest.sha256").write_text("", encoding="utf-8")
    # MINGW has Bash but lacks Linux flock. Only control flow is mocked in this check.
    script = script.replace("set -u\n", "set -u\nflock() { return 0; }\n", 1)
    # An empty manifest is enough for the singleton dispatch check, with no input files.
    script = script.replace(
        '(cd "$RUN" && sha256sum -c --status manifest.sha256) || fail manifest', ":"
    )
    execute(bash, tmp_path, script)
    invocation = (run / "invocation").read_text(encoding="utf-8").splitlines()
    controller = "runtime_batch_remote.py" if batch else "enhancement_remote.py"
    assert invocation[0] == "-I"
    assert invocation[1].endswith("/" + controller)
    assert invocation[2] == "run"
    assert invocation[3] == run.as_posix()
    assert invocation[4:] == ([] if batch else ["synthetic"])
    phase = json.loads((run / "supervisor.json").read_text(encoding="utf-8"))["phase"]
    assert phase == ("ready" if batch else "finished")


def test_preparation_only_supervisor_exits_before_private_manifest_or_controller(bash, tmp_path):
    script, runtime, run = generated(tmp_path, prepare_only=True)
    python = runtime / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text(
        """#!/usr/bin/env bash
case "$*" in
  *--doctor*) printf '%s' '{"ready":true}' ;;
  *--cache-status*) printf '%s' '{"models":{}}' ;;
  *) printf '%s' 'unexpected inference or decoder invocation' >&2; exit 9 ;;
esac
""",
        encoding="utf-8",
        newline="\n",
    )
    python.chmod(0o755)
    script = script.replace("set -u\n", "set -u\nflock() { return 0; }\n", 1)
    execute(bash, tmp_path, script)
    status = json.loads((run / "supervisor.json").read_text())
    assert status == {"phase": "finished", "code": "", "generation": "synthetic"}
    assert not (run / "manifest.sha256").exists() and not (run / "invocation").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Actual flock semantics require Linux.")
def test_linux_shared_reader_blocks_rebuild_until_it_exits(bash, tmp_path):
    if not shutil.which("flock"):
        pytest.skip("Linux util-linux flock is not installed.")
    script, runtime, run = generated(tmp_path)
    (runtime / ".in-use").chmod(0o444)
    reader = tmp_path / "reader.sh"
    reader.write_text(
        functions(script)
        + """
hold_runtime || exit 1
touch "$RUN/reader-ready"
while [ ! -f "$RUN/reader-release" ]; do sleep 0.02; done
""",
        encoding="utf-8",
    )
    process = subprocess.Popen([bash, str(reader)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not (run / "reader-ready").exists() and process.poll() is None:
            assert time.monotonic() < deadline, "Shared reader did not reach its barrier."
            time.sleep(0.01)
        assert (run / "reader-ready").is_file()
        (runtime / "stamp.json").unlink()
        writer = (
            functions(script)
            + """
install_runtime() { touch "$RUN/destructive-install"; }
prepare
[ "$?" = EXPECTED ] || exit 1
"""
        )
        execute(bash, tmp_path, writer.replace("EXPECTED", "2"))
        assert not (run / "destructive-install").exists()
        (run / "reader-release").touch()
        assert process.wait(timeout=5) == 0
        execute(bash, tmp_path, writer.replace("EXPECTED", "0"))
        assert (run / "destructive-install").is_file()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
