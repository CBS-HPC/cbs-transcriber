"""Unsaved legacy private runs require recovery; ordinary cleanup is not disposition."""

from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace

import pytest

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import providers
from cbs_transcriber.store import Store


@pytest.fixture
def retained(tmp_path, monkeypatch):
    store = Store(tmp_path)
    project = store.create_project("Synthetic retained run")
    recording = store.create_recording(project["id"], "synthetic.wav")
    settings = providers.Settings(
        provider_type="ucloud",
        base_url="https://runtime.invalid",
        ucloud_transcriber={"cleanup_after_success": True},
    )
    job = store.add_job(
        recording["id"],
        "enhance",
        {
            "enhancement": {"align": True},
            "ucloud_api": {"settings": settings.model_dump()},
            "ucloud_run": {
                "job_id": "owned",
                "remote_state": "SUCCESS",
                "uploaded": True,
                "script_application": {"name": "terminal", "version": "pinned"},
            },
        },
    )
    folder = api.run_folder(settings, job["id"])
    job["params"]["ucloud_run"]["owned_folder"] = folder
    store.update_job(job["id"], state="failed", params=job["params"])
    client = SimpleNamespace(
        retrieve_job=lambda _: {
            "id": "owned",
            "status": {"state": "SUCCESS"},
            "specification": {
                "name": "CBS enhancement " + job["id"],
                "application": job["params"]["ucloud_run"]["script_application"],
                "product": settings.ucloud_transcriber.product,
                "resources": [{"type": "file", "path": folder, "readOnly": False}],
            },
        },
        browse_files=lambda path, **_: {
            "items": [{"id": folder + "/audio.wav"}, {"id": folder + "/alignment.json"}],
            "next": None,
        },
    )
    trashed = []

    @contextmanager
    def connection(*_):
        yield client

    monkeypatch.setattr(api.native, "client_for", connection)
    monkeypatch.setattr(api.native, "require_setup", lambda *_: None)
    monkeypatch.setattr(api.native, "trash_owned_folder", lambda _, path: trashed.append(path))
    return store, store.job(job["id"]), SimpleNamespace(get=lambda _: "synthetic"), client, trashed


def test_terminal_unsaved_private_run_is_not_ordinary_cleanup_authority(retained):
    store, job, credentials, _, trashed = retained
    before = store.job(job["id"])
    with pytest.raises(ValueError, match="unsaved|recover|Recover"):
        api.retry_cleanup(store, credentials, job)
    assert trashed == []
    assert store.job(job["id"])["params"]["ucloud_run"].get("cleanup_state") != "trashed"
    assert before["params"]["ucloud_run"]["job_id"] == "owned"


def test_public_setup_only_legacy_folder_can_be_cleaned(retained):
    store, job, credentials, client, trashed = retained
    client.browse_files = lambda path, **_: {
        "items": [{"id": path + "/supervisor.sh"}],
        "next": None,
    }
    assert api.retry_cleanup(store, credentials, job) == "trashed"
    assert trashed == [job["params"]["ucloud_run"]["owned_folder"]]


def test_retained_preview_is_metadata_only_and_does_not_mutate_or_download(retained):
    store, job, credentials, _, trashed = retained
    before = store.job(job["id"])
    preview = api.retained_preview(store, credentials, job)
    assert preview["remote_state"] == "SUCCESS" and preview["saved_results"] == 0
    assert preview["available_stage_names"] == ["alignment.json"]
    assert preview["private_files_present"] is True and preview["can_cleanup"] is False
    assert store.job(job["id"]) == before and not trashed


def test_mismatched_owned_allocation_blocks_preview_and_cleanup(retained):
    store, job, credentials, client, trashed = retained
    client.retrieve_job = lambda _: {
        "id": "another",
        "status": {"state": "SUCCESS"},
        "specification": {},
    }
    with pytest.raises(ValueError, match="owned|ownership"):
        api.retained_preview(store, credentials, job)
    with pytest.raises(ValueError, match="owned|ownership"):
        api.retry_cleanup(store, credentials, job)
    assert not trashed


def test_failure_settlement_keeps_unsaved_private_stages_and_stops_only_owned_allocation(retained):
    store, job, credentials, client, trashed = retained
    job["params"]["ucloud_run"]["cleanup_state"] = "pending"
    store.update_job(job["id"], params=job["params"])
    owner = SimpleNamespace(
        store=store,
        job=job,
        run=job["params"]["ucloud_run"],
        client=client,
        worker=SimpleNamespace(credentials=credentials, cancel_event=Event()),
        settings=providers.Settings.model_validate(job["params"]["ucloud_api"]["settings"]),
        remote=job["params"]["ucloud_run"]["owned_folder"],
        checkpoint=lambda **values: api.checkpoint_job(store, job, **values),
    )
    api.Run.settle_failure(owner)
    assert not trashed
    assert store.job(job["id"])["params"]["ucloud_run"]["cleanup_state"] == "pending"


def test_cached_unsaved_files_do_not_make_settle_cleanup_authority(retained):
    store, job, credentials, _, trashed = retained
    api.settle(store, credentials, job)
    assert not trashed
    assert store.job(job["id"])["params"]["ucloud_run"]["cleanup_state"] == "pending"


def test_cached_recovery_validates_before_cleanup_and_retains_failed_validation(retained):
    store, job, credentials, _, trashed = retained

    def invalid():
        assert not trashed
        raise ValueError("Synthetic invalid cached stage")

    api.settle(store, credentials, job, validate=invalid)
    assert not trashed
    validated = []
    api.settle(store, credentials, store.job(job["id"]), validate=lambda: validated.append(True))
    assert validated == [True]
    assert trashed == [job["params"]["ucloud_run"]["owned_folder"]]
