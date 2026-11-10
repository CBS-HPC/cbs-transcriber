"""Standalone Linux wrapper for a prepared UCloud runtime. No control-plane credentials."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def read_json(path, default):
    if not path.exists():
        return default
    if path.stat().st_size > 8192:
        raise ValueError("Oversized status")
    return json.loads(path.read_text(encoding="utf-8"))


def write_status(directory, state, generation):
    path = directory / "remote-status.pending"
    path.write_text(json.dumps({"state": state, "generation": generation}), encoding="utf-8")
    path.replace(directory / "remote-status.json")


def worker_environment():
    allowed = {
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "LD_LIBRARY_PATH",
        "CUDA_VISIBLE_DEVICES",
        "OMP_NUM_THREADS",
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "TORCH_HOME",
        "NLTK_DATA",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CBS_RUNTIME_LOCK_FD",
    }
    return {k: v for k, v in os.environ.items() if k in allowed}


def inherited_runtime_fds():
    """Only the supervisor's fixed shared-use descriptor may reach an inference child."""
    marker = os.environ.get("CBS_RUNTIME_LOCK_FD")
    if marker is None:
        return ()  # Already-frozen legacy supervisors have no runtime-use descriptor.
    if marker != "9":
        raise ValueError("Invalid runtime lock descriptor.")
    os.fstat(9)  # A missing inherited lock fails before inference, never silently ignored.
    return (9,)


def run(directory, generation):
    import fcntl

    with (directory / "remote-worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # An uncertain SSH launch must not duplicate active inference.
        write_status(directory, "running", generation)
        result = subprocess.run(
            [sys.executable, "-I", str(directory / "enhancement_worker.py"), str(directory)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=worker_environment(),
            # Inference retains both allocation and runtime locks if either parent dies.
            pass_fds=(lock.fileno(), *inherited_runtime_fds()),
        )
        write_status(directory, "complete" if result.returncode == 0 else "failed", generation)


def status(directory, required=None):
    result = read_json(directory / "remote-status.json", {"state": "pending"})
    if required:
        stages = required.split(",")
        if not set(stages) <= {"alignment", "diarization"}:
            raise ValueError("Unsupported stages")
        if all((directory / (name + ".json")).is_file() for name in stages):
            result["state"] = "complete"  # Recover outputs if the wrapper exited before status.
    if result.get("state") == "running":
        import fcntl

        with (directory / "remote-worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                result["state"] = "failed"  # A dead worker must not appear active indefinitely.
    progress = read_json(directory / "progress.json", {})
    error = read_json(directory / "error.json", {})
    # No text, arbitrary runtime output, arguments or environment in status responses.
    return {
        "state": result.get("state"),
        "generation": result.get("generation"),
        "stage": progress.get("stage"),
        "code": error.get("code"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["start", "run", "status", "read-stage"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.operation == "run":
        run(directory, args.value)
    elif args.operation == "start":
        subprocess.Popen(
            [
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "run",
                str(directory),
                args.value,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    elif args.operation == "status":
        print(json.dumps(status(directory, args.value)))
    else:
        if args.value not in {"alignment", "diarization"}:
            raise ValueError("Unsupported stage")
        path = directory / (args.value + ".json")
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Oversized stage")
        with path.open("rb") as stream:
            while data := stream.read(65536):
                sys.stdout.buffer.write(data)


if __name__ == "__main__":
    main()
