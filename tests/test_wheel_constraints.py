"""Offline, exact wheel-source mapping for the selected uv tool-install constraints."""

import hashlib
import itertools
import runpy
import tomllib
from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
MODULE = runpy.run_path(str(ROOT / "scripts" / "export_wheel_constraints.py"))
generate = MODULE["generate_constraints"]
main = MODULE["main"]
PIN_LINES = [
    "torch==2.8.0 ; platform_machine != 'x86_64' and sys_platform == 'darwin'",
    "torch==2.8.0+cpu ; sys_platform != 'darwin'",
    "torchaudio==2.8.0 ; (platform_machine == 'aarch64' and "
    "platform_python_implementation == 'CPython' and sys_platform == 'linux') or "
    "(platform_machine != 'x86_64' and sys_platform == 'darwin')",
    "torchaudio==2.8.0+cpu ; (platform_machine != 'aarch64' and sys_platform == 'linux') or "
    "(platform_python_implementation != 'CPython' and sys_platform == 'linux') or "
    "(sys_platform != 'darwin' and sys_platform != 'linux')",
    "torchvision==0.23.0 ; (platform_machine == 'aarch64' and "
    "platform_python_implementation == 'CPython' and sys_platform == 'linux') or "
    "(platform_machine != 'x86_64' and sys_platform == 'darwin')",
    "torchvision==0.23.0+cpu ; (platform_machine != 'aarch64' and sys_platform == 'linux') or "
    "(platform_python_implementation != 'CPython' and sys_platform == 'linux') or "
    "(sys_platform != 'darwin' and sys_platform != 'linux')",
]
OPAQUE = (
    "# uv export --locked --offline --no-dev --no-emit-project --no-hashes\n"
    "fastapi==0.120.0\n"
    "numpy==2.5.4 ; platform_machine != 'x86_64' or sys_platform != 'darwin'\n"
    "torchmetrics==1.9.0 ; platform_machine != 'x86_64' or sys_platform != 'darwin'\n"
    "    # via scientific stack\n"
    "ucloud-workflow @ https://github.com/CBS-HPC/ucloud-api/releases/download/"
    "v0.1.9/ucloud_workflow-0.1.9-py3-none-any.whl\n"
)
EXPORT = OPAQUE + "\n".join(PIN_LINES) + "\n"


@pytest.fixture
def lock():
    with (ROOT / "uv.lock").open("rb") as source:
        return tomllib.load(source)


def entries(text):
    return [
        Requirement(line)
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def cpu_record(lock, name="torch", version="2.8.0+cpu"):
    return next(p for p in lock["package"] if p["name"] == name and p["version"] == version)


def windows_wheel(lock):
    return next(
        w for w in cpu_record(lock)["wheels"] if w["url"].endswith("cp313-cp313-win_amd64.whl")
    )


def test_actual_lock_produces_six_exact_cpu_wheel_urls_with_locked_hashes(lock):
    output = generate(EXPORT, lock)
    assert OPAQUE in output
    requirements = entries(output)
    wheels = [r for r in requirements if r.name in {"torch", "torchaudio", "torchvision"}]
    assert len(wheels) == 6
    locked = {
        w["url"] + "#" + w["hash"].replace(":", "=", 1)
        for package in lock["package"]
        if package["name"] in {"torch", "torchaudio", "torchvision"}
        for w in package.get("wheels", [])
    }
    for requirement in wheels:
        assert requirement.url in locked
        parsed = urlsplit(requirement.url)
        filename = unquote(parsed.path.rsplit("/", 1)[1])
        assert "cp313t" not in filename and "-cp313-" in filename
        assert filename.endswith("win_amd64.whl") or filename.endswith("arm64.whl")
        assert parsed.fragment.startswith("sha256=") and len(parsed.fragment) == 71
    assert not any(line.lstrip().startswith("--") for line in output.splitlines())
    assert next(r for r in requirements if r.name == "ucloud-workflow").url == (
        "https://github.com/CBS-HPC/ucloud-api/releases/download/v0.1.9/"
        "ucloud_workflow-0.1.9-py3-none-any.whl"
    )


def test_non_torch_lines_are_preserved_byte_for_byte_including_crlf(lock):
    original = EXPORT.replace("\n", "\r\n")
    output = generate(original, lock)
    for line in OPAQUE.replace("\n", "\r\n").splitlines(keepends=True):
        assert line in output


def test_fail_closed_marker_is_exact_complement_of_supported_target_union(lock):
    requirements = entries(generate(EXPORT, lock))
    guard = next(r for r in requirements if r.name == "cbs-transcriber")
    assert not guard.specifier.contains("0.1.0")
    for system, machine, python, implementation in itertools.product(
        ["win32", "darwin", "linux", "aix"],
        ["AMD64", "x86_64", "arm64", "ARM64", "aarch64", "i686"],
        ["3.12", "3.13", "3.14"],
        ["CPython", "PyPy"],
    ):
        environment = {
            **default_environment(),
            "sys_platform": system,
            "platform_machine": machine,
            "python_version": python,
            "platform_python_implementation": implementation,
        }
        supported = (
            python == "3.13"
            and implementation == "CPython"
            and (
                system == "win32"
                and machine in {"AMD64", "x86_64"}
                or system == "darwin"
                and machine in {"arm64", "x86_64"}
            )
        )
        assert guard.marker.evaluate(environment) is not supported
        selected = [
            r
            for r in requirements
            if r.name in {"torch", "torchaudio", "torchvision"} and r.marker.evaluate(environment)
        ]
        local = supported and not (system == "darwin" and machine == "x86_64")
        assert len(selected) == (3 if local else 0)


@pytest.mark.parametrize(
    "damage",
    ["missing", "duplicate", "marker", "url", "extras", "version", "leading-space", "uppercase"],
)
def test_missing_duplicate_or_unrecognized_torch_export_refuses(lock, damage):
    lines = PIN_LINES.copy()
    if damage == "missing":
        lines.pop()
    elif damage == "duplicate":
        lines.append(lines[0])
    elif damage == "marker":
        lines[0] = lines[0].replace("!=", "==")
    elif damage == "url":
        lines[0] = "torch @ https://example.invalid/torch.whl"
    elif damage == "extras":
        lines[0] = lines[0].replace("torch==", "torch[extra]==")
    elif damage == "version":
        lines[0] = lines[0].replace("2.8.0", "2.9.0")
    elif damage == "leading-space":
        lines[0] = " " + lines[0]
    else:
        lines[0] = lines[0].replace("torch==", "TORCH==")
    with pytest.raises(ValueError):
        generate(OPAQUE + "\n".join(lines) + "\n", lock)


@pytest.mark.parametrize(
    "option",
    ["--extra-index-url https://bad.invalid", "-r other.txt", "--hash=sha256:abc", "other==1 \\"],
)
def test_export_options_or_continuations_cannot_introduce_source_policy(lock, option):
    with pytest.raises(ValueError, match="options and line continuations"):
        generate(EXPORT + option + "\n", lock)


@pytest.mark.parametrize(
    "damage",
    [
        "registry",
        "package-duplicate",
        "missing-wheel",
        "ambiguous-wheel",
        "free-threaded",
        "arm-windows",
        "wrong-name",
        "wrong-version",
        "http",
        "foreign-host",
        "userinfo",
        "query",
        "fragment",
        "encoded-slash",
        "hash",
    ],
)
def test_invalid_identity_source_tag_or_hash_never_gets_selected(lock, damage):
    package = cpu_record(lock)
    wheel = windows_wheel(lock)
    if damage == "registry":
        package["source"] = {"registry": "https://pypi.org/simple"}
    elif damage == "package-duplicate":
        lock["package"].append(deepcopy(package))
    elif damage == "missing-wheel":
        package["wheels"].remove(wheel)
    elif damage == "ambiguous-wheel":
        package["wheels"].append(deepcopy(wheel))
    elif damage == "free-threaded":
        wheel["url"] = wheel["url"].replace("cp313-cp313-win", "cp313-cp313t-win")
    elif damage == "arm-windows":
        wheel["url"] = wheel["url"].replace("win_amd64", "win_arm64")
    else:
        replacements = {
            "wrong-name": ("/torch-", "/other-"),
            "wrong-version": ("2.8.0%2Bcpu", "2.9.0%2Bcpu"),
            "http": ("https:", "http:"),
            "foreign-host": ("download-r2.pytorch.org", "download-r2.pytorch.org.example.invalid"),
            "userinfo": ("https://", "https://secret@"),
            "query": (".whl", ".whl?token=secret"),
            "fragment": (".whl", ".whl#other=secret"),
            "encoded-slash": ("/torch-", "/nested%2Ftorch-"),
        }
        if damage == "hash":
            wheel["hash"] = "sha256:" + "z" * 64
        else:
            wheel["url"] = wheel["url"].replace(*replacements[damage])
    with pytest.raises(ValueError):
        generate(EXPORT, lock)


def test_mac_rejects_free_threaded_only_or_duplicate_compatible_wheels(lock):
    package = cpu_record(lock, version="2.8.0")
    normal = next(w for w in package["wheels"] if "-cp313-none-" in w["url"])
    package["wheels"].remove(normal)
    with pytest.raises(ValueError, match="macos CPython 3.13"):
        generate(EXPORT, lock)
    package["wheels"].extend([normal, deepcopy(normal)])
    with pytest.raises(ValueError, match="macos CPython 3.13"):
        generate(EXPORT, lock)


def test_unexported_torch_lock_variant_is_not_silently_ignored(lock):
    extra = deepcopy(cpu_record(lock))
    extra["version"] = "9.9.9+cpu"
    lock["package"].append(extra)
    with pytest.raises(ValueError, match="unexpected Torch package versions"):
        generate(EXPORT, lock)


def test_cli_writes_only_exclusive_output_after_validation(tmp_path, lock):
    source = tmp_path / "requirements.txt"
    lock_path = ROOT / "uv.lock"
    output = tmp_path / "constraints.txt"
    source.write_text(EXPORT, encoding="utf-8")
    main(["--requirements", str(source), "--lock", str(lock_path), "--output", str(output)])
    data = output.read_text(encoding="utf-8")
    assert hashlib.sha256(lock_path.read_bytes()).hexdigest() in data
    assert hashlib.sha256(source.read_bytes()).hexdigest() in data
    before = output.read_bytes()
    with pytest.raises(SystemExit) as caught:
        main(["--requirements", str(source), "--lock", str(lock_path), "--output", str(output)])
    assert caught.value.code == 2 and output.read_bytes() == before
    source.write_text(EXPORT.replace("torch==2.8.0 ;", "torch==9.9.9 ;"), encoding="utf-8")
    refused = tmp_path / "must-not-exist.txt"
    with pytest.raises(SystemExit):
        main(["--requirements", str(source), "--lock", str(lock_path), "--output", str(refused)])
    assert not refused.exists()
