"""Frozen native allocation execution; synthetic libraries and mocked UCloud only."""

import copy
import json

import pytest
from test_native_batch import batch_remote as batch_remote
from test_native_batch import selection as selection
from test_ucloud_native import PRODUCT
from test_ucloud_native import remote as remote

from cbs_transcriber import (
    audio_inputs,
    deletions,
    native_allocation,
    native_batch,
    native_capture,
    portable_capture,
    providers,
)
from cbs_transcriber.providers import Settings

MIG = {"id": "gpu-nvidia-b200-1-mig.1g", "category": "gpu-nvidia-b200", "provider": "ucloud"}


def policy(product=MIG):
    return {"policy": "native-allocation-v1", "product": copy.deepcopy(product)}


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"policy": "unknown", "product": MIG},
        {"policy": "native-allocation-v1"},
        {**policy(), "extra": True},
        policy({**MIG, "provider": "foreign"}),
        policy({**MIG, "category": "foreign"}),
        policy({**MIG, "id": "other-gpu"}),
        policy({**MIG, "provider": ""}),
        policy({**MIG, "provider": "bad\nprovider"}),
        policy({**MIG, "extra": True}),
    ],
)
def test_present_invalid_policy_never_becomes_legacy(value):
    with pytest.raises(ValueError, match="frozen native allocation"):
        native_allocation.validate({"native_allocation": value})


@pytest.mark.parametrize("target", [MIG, PRODUCT])
def test_effective_settings_change_only_frozen_product(target):
    base = Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="large-v3",
        ucloud_transcriber={"product": PRODUCT},
    ).model_dump()
    frozen = copy.deepcopy(base)
    params = {"native_allocation": policy(target)}
    actual = native_allocation.effective(base, params)
    assert actual["ucloud_transcriber"]["product"] == target
    actual["ucloud_transcriber"]["product"] = PRODUCT
    assert actual == base == frozen
    assert params == {"native_allocation": policy(target)}
    assert native_allocation.effective(base, {}) == base
    assert native_allocation.validate({}) is None


def add_native(store, provider, recordings, target, *, captured=True):
    base = Settings.model_validate(provider).model_dump()
    items = []
    for recording in recordings:
        params = {
            "settings": base,
            "provider_id": provider["id"],
            "provider_name": provider["name"],
            "revision": store.recording(recording["id"])["revision"],
            "native_allocation": policy(target),
        }
        if captured:
            params.update(
                audio_input=audio_inputs.freeze(store, recording), audio_transport="original"
            )
            params = native_capture.freeze(params)
        items.append({"recording_id": recording["id"], "params": params})
    return store.add_transcription_jobs(items)


@pytest.mark.parametrize("target", [MIG, PRODUCT], ids=["mig", "full"])
@pytest.mark.parametrize("count", [1, 2], ids=["single", "batch"])
@pytest.mark.parametrize("classification", ["standard", "protected"])
def test_submission_records_actual_product_without_replacing_approval_settings(
    client, batch_remote, selection, monkeypatch, tmp_path, target, count, classification
):
    provider, recordings = selection
    store = client.app.state.store
    if classification == "protected":
        provider["approved_for"] = ["protected"]
        response = client.put("/api/providers/" + provider["id"], json=provider)
        assert response.status_code == 200
        provider = response.json()
        store.execute(
            "UPDATE projects SET classification='protected' WHERE id=?",
            (recordings[0]["project_id"],),
        )
    batch_remote.store = store
    batch_remote.template["specification"]["product"] = target
    if count == 1:
        batch_remote.handle = type(batch_remote).handle.__get__(batch_remote)
    jobs = add_native(store, provider, recordings[:count], target)
    base = [copy.deepcopy(job["params"]["settings"]) for job in jobs]
    checked = []
    require_approval = providers.require_approval

    def approve(*args):
        checked.append(copy.deepcopy(args[-1].ucloud_transcriber.product))
        return require_approval(*args)

    monkeypatch.setattr(providers, "require_approval", approve)
    queue = client.app.state.queue
    claimed = store.claim_job("transcribe")
    queue.active_batch_id = claimed["params"].get("native_batch_id")
    queue.process(claimed["id"])
    assert batch_remote.submissions[0]["product"] == target
    assert len(batch_remote.submissions) == 1
    assert checked and all(product == PRODUCT for product in checked)
    assert [store.job(job["id"])["params"]["settings"] for job in jobs] == base
    for job in jobs:
        saved = store.job(job["id"])
        capture = saved["params"]["native_artifact_capture"]
        [submission] = capture["submissions"]
        body = submission["descriptor"]
        assert body["native_allocation"] == policy(target)
        assert body["submission"]["specification"]["product"] == target
        assert native_capture.validate(capture, saved) == capture
        [result] = store.results(job["recording_id"])
        assert result["source"]["native_allocation"] == policy(target)
        assert result["source"]["ucloud_transcriber"]["product"] == PRODUCT
        version = store.one(
            "SELECT context FROM result_versions WHERE result_id=? AND sequence=1", (result["id"],)
        )
        bound = json.loads(version["context"])["native_artifacts"]
        assert (
            native_capture.unbound(
                bound,
                recording_id=job["recording_id"],
                job_id=job["id"],
                creation=bound["creation"],
            )
            == capture
        )
        altered = copy.deepcopy(capture)
        altered["submissions"][0]["descriptor"]["submission"]["specification"]["product"] = {
            **target,
            "provider": "foreign",
        }
        with pytest.raises(ValueError):
            native_capture.validate(altered, saved)
        stripped = copy.deepcopy(capture)
        del stripped["submissions"][0]["descriptor"]["native_allocation"]
        with pytest.raises(ValueError):
            native_capture.validate(stripped, saved)
    if count == 2:
        queue.process(claimed["id"])
        assert len(batch_remote.submissions) == 1
    else:
        assert store.claim_job("transcribe") is None
    if count == 2:
        owner = store.native_batch(jobs[0]["params"]["native_batch_id"])
        assert owner["params"]["native_allocation"] == policy(target)
    if count == 2 and target == MIG:
        queue.active_batch_id = None
        package = portable_capture.capture_library(
            store, queue, tmp_path / "packages", gate=portable_capture.CaptureGate()
        )
        assert package["folder"]


def test_grouping_and_member_fence_include_frozen_allocation(client, selection):
    provider, recordings = selection
    store = client.app.state.store
    for index in range(2):
        recordings.append(store.create_recording(recordings[0]["project_id"], f"extra-{index}.wav"))
    settings = Settings.model_validate(provider).model_dump()
    items = [
        {
            "recording_id": recording["id"],
            "params": {
                "settings": settings,
                "provider_id": provider["id"],
                "provider_name": provider["name"],
                "native_allocation": policy(MIG if index < 2 else PRODUCT),
            },
        }
        for index, recording in enumerate(recordings)
    ]
    jobs = store.add_transcription_jobs(items)
    first, second = (store.native_batch(jobs[i]["params"]["native_batch_id"]) for i in (0, 2))
    assert first["id"] != second["id"]
    assert len(first["members"]) == len(second["members"]) == 2
    assert first["params"]["native_allocation"] == policy(MIG)
    assert second["params"]["native_allocation"] == policy(PRODUCT)
    altered = copy.deepcopy(jobs[0])
    altered["params"]["native_allocation"] = policy(PRODUCT)
    with pytest.raises(ValueError, match="frozen provider identity"):
        native_batch.batch_for_job(store, altered)
    del altered["params"]["native_allocation"]
    with pytest.raises(ValueError, match="frozen provider identity"):
        native_batch.batch_for_job(store, altered)


@pytest.mark.parametrize("old", [False, True], ids=["absent", "present"])
def test_generic_writes_cannot_add_remove_or_change_policy(client, selection, old):
    provider, recordings = selection
    store = client.app.state.store
    params = {"settings": Settings.model_validate(provider).model_dump()}
    if old:
        params["native_allocation"] = policy(MIG)
    job = store.add_job(recordings[0]["id"], "transcribe", params)
    for proposed in (
        {**params, "native_allocation": policy(PRODUCT)},
        {"settings": params["settings"]},
    ):
        if proposed == params:
            continue
        with pytest.raises(ValueError, match="native artifact"):
            store.update_job(job["id"], params=proposed)
    if old:
        changed = copy.deepcopy(params)
        changed["settings"]["ucloud_transcriber"]["product"] = MIG
        with pytest.raises(ValueError, match="native artifact"):
            store.update_job(job["id"], params=changed)
    store.update_job(job["id"], params={**params, "ucloud_run": {"remote_state": "SUCCESS"}})
    assert store.job(job["id"])["params"].get("native_allocation") == params.get(
        "native_allocation"
    )


def test_deletion_snapshot_binds_separate_execution_allocation():
    job = {
        "id": "a" * 32,
        "recording_id": "b" * 32,
        "kind": "transcribe",
        "created": "2026-11-02T12:00:00+00:00",
        "params": {"settings": {"provider_type": "ucloud"}},
    }
    legacy = deletions.Deletions._job_identity(job)
    job["params"]["native_allocation"] = policy(MIG)
    mig = deletions.Deletions._job_identity(job)
    job["params"]["native_allocation"] = policy(PRODUCT)
    full = deletions.Deletions._job_identity(job)
    assert len({legacy, mig, full}) == 3


@pytest.mark.parametrize("count", [1, 2], ids=["single", "batch"])
def test_withdrawn_protected_approval_blocks_mig_before_remote_calls(
    client, batch_remote, selection, count
):
    provider, recordings = selection
    store = client.app.state.store
    batch_remote.store = store
    provider["approved_for"] = ["protected"]
    response = client.put("/api/providers/" + provider["id"], json=provider)
    assert response.status_code == 200
    provider = response.json()
    store.execute(
        "UPDATE projects SET classification='protected' WHERE id=?",
        (recordings[0]["project_id"],),
    )
    jobs = add_native(store, provider, recordings[:count], MIG)
    provider["approved_for"] = []
    assert client.put("/api/providers/" + provider["id"], json=provider).status_code == 200
    claimed = store.claim_job("transcribe")
    with pytest.raises(providers.ApprovalDenied):
        client.app.state.queue.process(claimed["id"])
    assert not batch_remote.calls and not batch_remote.submissions and not batch_remote.uploaded
    assert all(
        store.job(job["id"])["params"]["settings"]["approved_for"] == ["protected"] for job in jobs
    )
