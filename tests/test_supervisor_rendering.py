"""Render the packaged supervisor using the current singleton and batch contracts."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import providers


@pytest.mark.parametrize("batch", [False, True], ids=["singleton", "batch"])
@pytest.mark.parametrize("prepare", [False, True], ids=["prepared", "prepare"])
def test_packaged_supervisor_renders_every_token_for_both_protocols(tmp_path, batch, prepare):
    settings = providers.Settings(
        provider_type="ucloud",
        base_url="https://synthetic.invalid",
        ucloud_transcriber={"storage_path": "/drive/synthetic", "project_id": "synthetic"},
    )
    identifier = "0123456789abcdef0123456789abcdef"
    script = api.supervisor_script(settings, identifier, prepare, ["coarse", "da"], batch=batch)
    assert not re.search(r"__[A-Z0-9_]+__", script)
    assert "\r" not in script
    assert f"BATCH={int(batch)}\n" in script
    assert f"PREPARE={int(prepare)}\n" in script
    assert f"GENERATION={identifier}\n" in script
    name = "cbs-runtime-batch-" if batch else "cbs-enhancement-"
    assert f"RUN=/work/{name}{identifier}\n" in script
    assert f"RT=/work/{api.runtime_id()}\n" in script
    assert 'MODELS="coarse,da"' in script
    assert api.UV_SHA256 in script and api.requirements_hash() in script
    assert "unset HF_TOKEN" in script

    bash = shutil.which("bash")
    if not bash or "system32" in bash.lower():
        candidates = (
            Path.home() / "AppData/Local/Programs/Git/bin/bash.exe",
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe",
        )
        bash = next((str(candidate) for candidate in candidates if candidate.is_file()), None)
    if bash:
        path = tmp_path / "supervisor.sh"
        path.write_text(script, encoding="utf-8", newline="\n")
        result = subprocess.run([bash, "-n", path.as_posix()], capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def test_genuinely_unknown_packaged_template_token_still_fails_closed(tmp_path, monkeypatch):
    template = tmp_path / "supervisor.sh"
    template.write_text(
        api.SUPERVISOR.read_text(encoding="utf-8") + "\n__UNKNOWN_TEMPLATE_FIELD__\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(api, "SUPERVISOR", template)
    settings = providers.Settings(provider_type="ucloud", base_url="https://synthetic.invalid")
    with pytest.raises(ValueError, match="unknown placeholder"):
        api.supervisor_script(settings, "0123456789abcdef0123456789abcdef", False, batch=True)
