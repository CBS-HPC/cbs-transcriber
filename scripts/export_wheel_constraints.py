"""Convert one frozen uv runtime export to supported CPython 3.13 tool constraints.

Only Torch's index-sourced pins change. Supply an export from the same locked source;
this is not a resolver or a general export/lock consistency checker. Nothing is fetched.
"""

import argparse
import hashlib
import re
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

CPU_INDEX = "https://download.pytorch.org/whl/cpu"
CPU_HOSTS = {"download.pytorch.org", "download-r2.pytorch.org"}
PACKAGES = ("torch", "torchaudio", "torchvision")
SPLIT_MAC = (
    "(platform_machine == 'aarch64' and platform_python_implementation == 'CPython' "
    "and sys_platform == 'linux') or (platform_machine != 'x86_64' and sys_platform == 'darwin')"
)
SPLIT_CPU = (
    "(platform_machine != 'aarch64' and sys_platform == 'linux') or "
    "(platform_python_implementation != 'CPython' and sys_platform == 'linux') or "
    "(sys_platform != 'darwin' and sys_platform != 'linux')"
)
EXPORT_MARKERS = {
    "torch": {
        "macos": "platform_machine != 'x86_64' and sys_platform == 'darwin'",
        "windows": "sys_platform != 'darwin'",
    },
    "torchaudio": {"macos": SPLIT_MAC, "windows": SPLIT_CPU},
    "torchvision": {"macos": SPLIT_MAC, "windows": SPLIT_CPU},
}
PYTHON = "python_version == '3.13' and platform_python_implementation == 'CPython'"
OUTPUT_MARKERS = {
    "windows": (
        "sys_platform == 'win32' and (platform_machine == 'AMD64' or "
        f"platform_machine == 'x86_64') and {PYTHON}"
    ),
    "macos": f"sys_platform == 'darwin' and platform_machine == 'arm64' and {PYTHON}",
}
UNSUPPORTED = (
    "python_version != '3.13' or platform_python_implementation != 'CPython' or "
    "(sys_platform != 'win32' and sys_platform != 'darwin') or "
    "(sys_platform == 'win32' and platform_machine != 'AMD64' and "
    "platform_machine != 'x86_64') or (sys_platform == 'darwin' and "
    "platform_machine != 'arm64' and platform_machine != 'x86_64')"
)


def wheel_url(lock, name, version, target):
    """Select a unique normal-ABI cp313 CPU wheel and retain its exact locked URL."""
    records = [p for p in lock["package"] if p.get("name") == name and p.get("version") == version]
    if len(records) != 1 or records[0].get("source") != {"registry": CPU_INDEX}:
        raise ValueError(f"{name} {version}: expected one CPU-index lock record")
    wheels = records[0].get("wheels")
    if not isinstance(wheels, list) or not wheels:
        raise ValueError(f"{name} {version}: missing locked wheels")
    matches = []
    for wheel in wheels:
        if not isinstance(wheel, dict):
            raise ValueError(f"{name}: malformed locked wheel")
        url, digest = wheel.get("url"), wheel.get("hash")
        if not isinstance(url, str) or not isinstance(digest, str):
            raise ValueError(f"{name}: malformed locked wheel")
        parsed = urlsplit(url)
        path = unquote(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.netloc not in CPU_HOSTS
            or parsed.query
            or parsed.fragment
            or any(ord(c) < 33 or ord(c) > 126 for c in url)
            or not path.startswith("/whl/cpu/")
            or path.count("/") != 3
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
        ):
            raise ValueError(f"{name}: unsupported CPU wheel URL or SHA-256")
        filename = path.rsplit("/", 1)[1]
        parts = filename.removesuffix(".whl").split("-")
        if not filename.endswith(".whl") or len(parts) != 5 or parts[:2] != [name, version]:
            raise ValueError(f"{name}: wheel identity disagrees with the lock")
        interpreter, abi, platform = parts[2:]
        compatible = interpreter == "cp313" and abi == "cp313" and platform == "win_amd64"
        if target == "macos":
            mac = re.fullmatch(r"macosx_(\d+)_(\d+)_arm64", platform)
            compatible = (
                interpreter == "cp313"
                and abi in {"cp313", "none"}
                and mac is not None
                and (11, 0) <= (int(mac[1]), int(mac[2])) <= (14, 0)
            )
        if compatible:
            matches.append(url + "#sha256=" + digest.removeprefix("sha256:"))
    if len(matches) != 1:
        raise ValueError(f"{name} {version}: expected one {target} CPython 3.13 CPU wheel")
    return matches[0]


def generate_constraints(requirements, lock):
    """Preserve every non-Torch line; reject unfamiliar special cases before any write."""
    if (
        not isinstance(lock, dict)
        or type(lock.get("version")) is not int
        or lock["version"] != 1
        or lock.get("requires-python") != "==3.13.*"
        or not isinstance(lock.get("package"), list)
        or not all(isinstance(p, dict) for p in lock["package"])
    ):
        raise ValueError("Expected a Python 3.13 uv lock (schema 1)")
    output, seen, versions = [], set(), {}
    for line in requirements.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        stripped = content.strip()
        if not stripped or stripped.startswith("#"):
            output.append(line)
            continue
        if stripped.startswith("-") or stripped.endswith("\\"):
            raise ValueError("Export options and line continuations are not supported")
        identity = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", stripped)
        name = re.sub(r"[-_.]+", "-", identity[1]).lower() if identity else None
        if name not in PACKAGES:
            output.append(line)
            continue
        pin = re.fullmatch(
            r"(torch|torchaudio|torchvision)==(\d+\.\d+\.\d+(?:\+cpu)?) ; (.+)", content
        )
        if pin is None:
            raise ValueError(f"{name}: expected a canonical marked uv version pin")
        version, marker = pin[2], pin[3]
        target = "windows" if version.endswith("+cpu") else "macos"
        if marker != EXPORT_MARKERS[name][target] or (name, target) in seen:
            raise ValueError(f"{name}: ambiguous or unrecognized export marker")
        seen.add((name, target))
        versions[name, target] = version.removesuffix("+cpu")
        ending = "\r\n" if line.endswith("\r\n") else "\n"
        output.append(
            f"{name} @ {wheel_url(lock, name, version, target)} ; {OUTPUT_MARKERS[target]}{ending}"
        )
    if seen != {(name, target) for name in PACKAGES for target in OUTPUT_MARKERS}:
        raise ValueError(
            "Export must contain both supported version branches for all three Torch pins"
        )
    if any(versions[name, "windows"] != versions[name, "macos"] for name in PACKAGES):
        raise ValueError("Torch CPU/Mac versions in the export disagree")
    expected = {
        (name, version + ("+cpu" if target == "windows" else ""))
        for (name, target), version in versions.items()
    }
    locked = [
        (p.get("name"), p.get("version")) for p in lock["package"] if p.get("name") in PACKAGES
    ]
    if len(locked) != len(expected) or set(locked) != expected:
        raise ValueError("Lock contains unexpected Torch package versions or variants")
    body = "".join(output)
    if body and not body.endswith(("\n", "\r")):
        body += "\n"
    return (
        "# Scope: CPython 3.13; Windows x64 CPU; macOS ARM64 (14+) CPU; Intel Mac core-only.\n"
        + body
        + "\n# Refuse unsupported targets instead of falling back to unpinned local wheels.\n"
        f"cbs-transcriber<0 ; {UNSUPPORTED}\n"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        requirements, lock_bytes = args.requirements.read_bytes(), args.lock.read_bytes()
        result = generate_constraints(
            requirements.decode("utf-8"), tomllib.loads(lock_bytes.decode("utf-8"))
        )
        provenance = (
            f"# uv.lock SHA-256: {hashlib.sha256(lock_bytes).hexdigest()}\n"
            f"# uv export SHA-256: {hashlib.sha256(requirements).hexdigest()}\n"
        )
        with args.output.open("x", encoding="utf-8", newline="") as destination:
            destination.write(provenance + result)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Wheel constraints: {exc}\n")


if __name__ == "__main__":
    main()
