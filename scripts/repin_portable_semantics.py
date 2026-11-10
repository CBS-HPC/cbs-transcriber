"""Re-pin the portable semantics worker to the integrated capture/inventory/timing sources.

Pins are SHA-256 of LF-normalised source. Run from the repository root.
"""

import hashlib
import sys
from pathlib import Path

PACKAGE = Path("src/cbs_transcriber")
SOURCES = {
    "CAPTURE_SHA256": "local_asr_capture.py",
    "INVENTORY_SHA256": "portable_inventory.py",
    "TIMING_SHA256": "timing_capture.py",
    "RUNTIME_SHA256": "runtime_capture.py",
    "ARTIFACTS_SHA256": "provenance_artifacts.py",
    "TRANSCRIPTS_SHA256": "transcripts.py",
    "PAYLOAD_SHA256": "portable_payload.py",
    "PROVIDER_SHA256": "provider_capture.py",
    "NATIVE_SHA256": "native_capture.py",
}


def lf_hash(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def repin(path, names):
    raw = path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.replace(b"\r\n", b"\n").decode("utf-8")
    lines = text.split("\n")
    changed = []
    for index, line in enumerate(lines):
        for name, source in names.items():
            if line.startswith(name + ' = "'):
                new = f'{name} = "{lf_hash(PACKAGE / source)}"'
                if line != new:
                    changed.append(name)
                    lines[index] = new
    if changed:  # Leave untouched files (and their checkout line endings) alone.
        path.write_bytes(newline.join(lines).encode("utf-8"))
    return changed


worker = PACKAGE / "portable_semantics_worker.py"
print("worker:", repin(worker, SOURCES))
semantics = PACKAGE / "portable_semantics.py"
print(
    "semantics:",
    repin(semantics, {**SOURCES, "WORKER_SHA256": "portable_semantics_worker.py"}),
)
sys.exit(0)
