"""The suite-wide app-folder guard (conftest.isolated_app_folder, B459).

A browser test once registered its temporary restore targets in this computer's real library
list, and Library dialog tests read the real registry and described the real library.
"""

import os
from pathlib import Path

from conftest import REAL_LOCAL_APP_DATA

from cbs_transcriber import libraries, library_deletion, storage
from cbs_transcriber.store import Store

# expanduser reads USERPROFILE/HOME, not the patched Path.home().
REAL_HOME = Path(os.path.expanduser("~")).resolve()


def outside_real_home(path):
    return not Path(path).resolve().is_relative_to(REAL_HOME / storage.APP_FOLDER)


def test_registry_and_defaults_never_use_the_real_app_folder(isolated_app_folder):
    for path in (
        storage.app_folder(),
        libraries.registry_path(),
        libraries.visibility_path(),
        libraries.default_new(),
        storage.default_library_root(),
        storage.default_project_parent(),
        library_deletion.journal_root(),
    ):
        assert outside_real_home(path), path
    assert libraries.registry_path().is_relative_to(isolated_app_folder)


def test_earlier_default_and_packaged_scan_are_isolated(isolated_app_folder):
    legacy = storage.legacy_library_root()
    assert legacy == libraries.legacy_library_root()
    # pytest's own temporary folder may sit inside LOCALAPPDATA; the real default may not.
    assert legacy.is_relative_to(isolated_app_folder)
    if REAL_LOCAL_APP_DATA:
        assert legacy != Path(REAL_LOCAL_APP_DATA) / storage.APP_FOLDER
    assert libraries.redirected_roots() == []


def test_registering_a_library_writes_only_the_temporary_registry(tmp_path):
    root = Store(tmp_path / "library").root
    libraries.register(root)
    assert libraries.load()["recent"] == [str(root)]
    assert outside_real_home(libraries.registry_path())


def test_a_test_owned_local_app_data_keeps_the_packaged_scan(tmp_path, monkeypatch):
    packaged = tmp_path / "local" / "Packages" / "Vendor.App_x" / "LocalCache/Local/CBS Transcriber"
    Store(packaged)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert libraries.redirected_roots() == [packaged]
