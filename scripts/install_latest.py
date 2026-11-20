# /// script
# requires-python = "==3.13.*"
# dependencies = []
# ///
"""Install one verified stable CBS Transcriber release into its uv tool environment."""

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

REPOSITORY = "https://github.com/CBS-HPC/cbs-transcriber"
LATEST_API = "https://api.github.com/repos/CBS-HPC/cbs-transcriber/releases/latest"
ASSET_HOSTS = {
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
}
MAX_METADATA = 2 * 1024 * 1024
MAX_WHEEL = 32 * 1024 * 1024
MAX_CONSTRAINTS = 2 * 1024 * 1024


class ReleaseRedirects(urllib.request.HTTPRedirectHandler):
    """Allow GitHub's HTTPS asset CDN redirects, without credentials or foreign hosts."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if (
            urlsplit(request.full_url).hostname == "api.github.com"
            or target.scheme != "https"
            or target.netloc not in ASSET_HOSTS
            or (
                target.netloc == "github.com"
                and not target.path.startswith("/CBS-HPC/cbs-transcriber/releases/download/")
            )
            or target.fragment
            or any(ord(char) < 33 for char in newurl)
        ):
            raise ValueError("Unexpected release download redirect; nothing was installed.")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def read_url(url, maximum):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "CBS-Transcriber-installer",
        },
    )
    opener = urllib.request.build_opener(ReleaseRedirects())
    with opener.open(request, timeout=30) as response:
        content = response.read(maximum + 1)
    if len(content) > maximum:
        raise ValueError("Release download exceeds its size limit; nothing was installed.")
    return content


def release_assets(release):
    """Choose one valid version-named pair from one observed public release."""
    if (
        not isinstance(release, dict)
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or not isinstance(release.get("tag_name"), str)
    ):
        raise ValueError("Expected a published stable release; nothing was installed.")
    tag = release["tag_name"]
    match = re.fullmatch(r"v([0-9]+\.[0-9]+\.[0-9]+)", tag)
    if match is None or release.get("html_url") != f"{REPOSITORY}/releases/tag/{tag}":
        raise ValueError("Unexpected release version or repository; nothing was installed.")
    version = match[1]
    assets = release.get("assets")
    if not isinstance(assets, list) or not all(isinstance(asset, dict) for asset in assets):
        raise ValueError("Malformed release asset list; nothing was installed.")
    selected = []
    for name, maximum in (
        (f"cbs_transcriber-{version}-py3-none-any.whl", MAX_WHEEL),
        (f"cbs-transcriber-{version}-constraints.txt", MAX_CONSTRAINTS),
    ):
        candidates = [asset for asset in assets if asset.get("name") == name]
        if len(candidates) != 1:
            raise ValueError(f"Expected exactly one {name}; nothing was installed.")
        asset = candidates[0]
        if (
            asset.get("state") != "uploaded"
            or asset.get("browser_download_url") != f"{REPOSITORY}/releases/download/{tag}/{name}"
            or type(asset.get("size")) is not int
            or not 0 < asset["size"] <= maximum
            or not isinstance(asset.get("digest"), str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", asset["digest"])
        ):
            raise ValueError(f"Unverified release asset {name}; nothing was installed.")
        selected.append(asset)
    return tag, selected


@contextmanager
def stopped_default_app():
    # Hold the default port through installation so an app cannot start there mid-update.
    # Custom-port processes still require the user's explicit stop before updating.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as guard:
        if sys.platform == "win32":
            guard.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            guard.bind(("127.0.0.1", 8765))
        except OSError as exc:
            raise ValueError(
                "Port 8765 is in use or cannot be reserved. Save your work and stop CBS "
                "Transcriber with Ctrl+C before installing. Nothing was installed."
            ) from exc
        yield


def uv_executable():
    # uv >=0.9.29 sets UV for child scripts, including an explicitly selected PATH-shadowed uv.
    executable = os.environ.get("UV") or shutil.which("uv")
    if not executable:
        raise ValueError("uv was not found. Install uv 0.9.29 or later first.")
    result = subprocess.run([executable, "--version"], check=True, capture_output=True, text=True)
    match = re.match(r"uv (\d+)\.(\d+)\.(\d+)\b", result.stdout)
    if match is None or tuple(map(int, match.groups())) < (0, 9, 29):
        raise ValueError("uv 0.9.29 or later is required; nothing was installed.")
    return executable


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="Replace the stopped app with the latest stable release.",
    )
    args = parser.parse_args(argv)
    try:
        if sys.version_info[:2] != (3, 13):
            raise ValueError("Run this script with uv run --python 3.13.")
        with stopped_default_app():
            executable = uv_executable()
            tag, assets = release_assets(json.loads(read_url(LATEST_API, MAX_METADATA)))
            with tempfile.TemporaryDirectory(prefix="cbs-transcriber-install-") as temporary:
                paths = []
                for asset in assets:
                    content = read_url(asset["browser_download_url"], asset["size"])
                    if (
                        len(content) != asset["size"]
                        or "sha256:" + hashlib.sha256(content).hexdigest() != asset["digest"]
                    ):
                        raise ValueError(
                            f"Size or SHA-256 mismatch for {asset['name']}; nothing was installed."
                        )
                    path = Path(temporary) / asset["name"]
                    path.write_bytes(content)
                    paths.append(path)
                command = [
                    executable,
                    "tool",
                    "install",
                    "--python",
                    "3.13",
                    "--no-config",
                    "--link-mode",
                    "copy",
                    "--constraints",
                    str(paths[1]),
                ]
                if args.update:
                    command.extend(("--reinstall-package", "cbs-transcriber"))
                command.append(str(paths[0]))
                print(f"Installing verified release {tag} with uv.", flush=True)
                subprocess.run(command, check=True)
        print("Installation completed. Run cbs-transcriber to start the app.")
    except subprocess.CalledProcessError as exc:
        parser.exit(
            2, f"Install latest: uv exited with status {exc.returncode}; review its output.\n"
        )
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Install latest: {exc}\n")


if __name__ == "__main__":
    main()
