"""Offline qualification plans are not native MIG execution/eligibility evidence."""

import importlib.util
import json
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from cbs_transcriber import providers
from cbs_transcriber import ucloud_native as native
from cbs_transcriber.runtime_compute import MIG_PRODUCT
from cbs_transcriber.store import Store

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "native_transcriber_mig.py"
_spec = importlib.util.spec_from_file_location("native_transcriber_mig", SCRIPT)
adapter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(adapter)
FULL = {"id": native.COMPUTE_ID, "category": "gpu-nvidia-b200", "provider": "ucloud"}


@pytest.fixture(autouse=True)
def no_remote_or_credentials(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("The qualification planner must remain offline and credential-free")

    monkeypatch.setattr(native, "client_for", refuse)
    monkeypatch.setattr(native, "UCloudClient", refuse)
    monkeypatch.setattr(native, "upload", refuse)
    monkeypatch.setattr(native, "download_output", refuse)
    monkeypatch.setattr(native, "trash_owned_folder", refuse)
    monkeypatch.setattr(providers.Credentials, "get", refuse)
    monkeypatch.setattr(httpx, "Client", refuse)
    monkeypatch.delenv("CBS_TRANSCRIBER_MODELS", raising=False)


@pytest.fixture
def application(current_transcriber_application):
    value = deepcopy(current_transcriber_application)
    value["support"] = {"funded": True, "mig_supported": True}
    value["metadata"]["token"] = "synthetic-credential-must-not-be-copied"
    names = native.application_parameters(value)
    names["model"]["options"] = [{"name": "whisper/base", "value": "base"}]
    return value


@pytest.fixture
def planned(tmp_path, application):
    directory = tmp_path / "qualification"
    plan = adapter.create_plan(directory, application, FULL, MIG_PRODUCT)
    return directory, plan


def reseal(plan):
    plan["sha256"] = adapter.digest({k: v for k, v in plan.items() if k != "sha256"})


def outputs(plan, lane="mig"):
    return [
        {
            "job_id": member["job_id"],
            "recording_id": member["recording_id"],
            "input_filename": member["filename"],
            "output_name": Path(member["filename"]).stem + "_base_da.json",
            "data": json.dumps({"segments": [{"start": 0, "end": 1, "text": "Hej CBS."}]}).encode(),
        }
        for member in plan["runs"][lane]["members"]
    ]


def cleanup_claim(plan, lane="mig"):
    run = plan["runs"][lane]
    return {
        "batch_id": run["batch_id"],
        "folder": run["settings"]["ucloud_transcriber"]["storage_path"]
        + "/cbs-batch-"
        + run["batch_id"],
        "job_ids": sorted(member["job_id"] for member in run["members"]),
    }


def test_exact_native_pair_uses_real_builders_and_new_inert_store(planned, application):
    directory, plan = planned
    assert adapter.validate_plan(plan) == plan
    assert json.loads((directory / "plan.json").read_bytes()) == plan
    assert plan["scopes"] == adapter.SCOPES
    assert "synthetic-credential" not in (directory / "plan.json").read_text()
    assert "support" not in plan["application"]
    assert plan["fixtures"] == adapter.fixture_inputs()[0]
    assert plan["fixture_source"] == adapter.FIXTURE_SOURCE
    assert plan["fixture_source"]["license"] == "CC-BY-4.0"
    assert len(plan["fixtures"]) == 2
    assert sum(item["frames"] for item in plan["fixtures"]) / 16000 == 23.34
    store = Store(directory / "library")
    assert len(store.all("SELECT id FROM jobs")) == 4
    assert not store.all("SELECT id FROM transcript_results")
    assert store.claim_job("transcribe") is None
    for lane, run in plan["runs"].items():
        settings = providers.Settings.model_validate(run["settings"])
        assert settings.base_url == "https://offline.invalid"
        assert settings.ucloud_transcriber.template_job_id == ""
        assert run["specification"]["product"] == (FULL if lane == "b200" else MIG_PRODUCT)
        assert run["specification"]["parameters"]["jupyterLab"]["value"] is False
        assert run["specification"]["parameters"]["model"]["value"] == "base"
        assert "inputFile" not in run["specification"]["parameters"]
        assert run["specification"]["parameters"]["inputDir"]["path"].endswith(
            "/cbs-batch-" + run["batch_id"] + "/input"
        )
        batch = store.native_batch(run["batch_id"])
        assert batch["state"] == "cancelled"
        assert batch["params"]["settings"] == run["settings"]
        for member in run["members"]:
            assert store.job(member["job_id"])["state"] == "cancelled"
            assert store.job(member["job_id"])["params"]["qualification_plan_only"] is True
            raw = (store.directory(member["recording_id"]) / "original.wav").read_bytes()
            assert adapter.hashlib.sha256(raw).hexdigest() == adapter.FIXTURES[member["fixture_id"]]
        # Compute-shape validation now accepts the fixed native MIG. The cancelled
        # planner jobs still carry no public admission or execution authority.
        native.require_compute(settings)
    assert application["metadata"]["token"] == "synthetic-credential-must-not-be-copied"


@pytest.mark.parametrize(
    "choice", ["wrong_model", "wrong_flavor", "wrong_switch", "extra_input", "free_text"]
)
def test_invalid_metadata_is_refused_before_output_or_store(tmp_path, application, choice):
    names = native.application_parameters(application)
    if choice == "wrong_model":
        names["model"]["options"] = [{"value": "other"}]
    elif choice == "wrong_flavor":
        application["metadata"]["flavorName"] = "Interactive"
    elif choice == "wrong_switch":
        names["jupyterLab"]["type"] = "text"
    elif choice == "extra_input":
        names["initScript"]["optional"] = False
    else:
        names["archivePassword"]["defaultValue"] = "unowned text"
    directory = tmp_path / "refused"
    with pytest.raises(ValueError):
        adapter.create_plan(directory, application, FULL, MIG_PRODUCT)
    assert not directory.exists()


@pytest.mark.parametrize("label", ["nvidia/parakeet-tdt-0.6b-v3", "nvidia/parakeet-tdt-0.6b-v2"])
def test_parakeet_version_identity_is_preserved_or_refused(tmp_path, application, label):
    native.application_parameters(application)["model"]["options"] = [
        {"name": label, "value": "parakeet"}
    ]
    canonical = "nvidia/parakeet-tdt-0.6b-v3"
    if label == canonical:
        plan = adapter.create_plan(
            tmp_path / "parakeet", application, FULL, MIG_PRODUCT, model=canonical
        )
        assert plan["model"] == canonical
        assert plan["runs"]["mig"]["selector"] == {"model": "parakeet", "language": "da"}
    else:
        with pytest.raises(ValueError, match="not offered"):
            adapter.create_plan(
                tmp_path / "parakeet", application, FULL, MIG_PRODUCT, model=canonical
            )


@pytest.mark.parametrize("label", [None, "nvidia/parakeet-tdt-0.6b-v2"])
def test_bare_parakeet_never_bypasses_canonical_version_identity(tmp_path, application, label):
    option = {"value": "parakeet"}
    if label is not None:
        option["name"] = label
    native.application_parameters(application)["model"]["options"] = [option]
    with pytest.raises(ValueError, match="canonical native catalog"):
        adapter.settings_for(application, MIG_PRODUCT, "parakeet")
    settings = adapter.settings_for(application, MIG_PRODUCT, "base").model_copy(
        update={"model": "parakeet"}
    )
    with pytest.raises(ValueError, match="canonical native catalog"):
        adapter.specification(application, settings, "0" * 32)
    directory = tmp_path / "refused"
    with pytest.raises(ValueError, match="canonical native catalog"):
        adapter.create_plan(directory, application, FULL, MIG_PRODUCT, model="parakeet")
    assert not directory.exists()


@pytest.mark.parametrize(
    "model", ["whisper/base", "whisperx/large-v3", "gpt-transcribe", "unknown"]
)
def test_alias_runtime_and_unknown_models_are_not_native_canonical_choices(
    tmp_path, application, model
):
    native.application_parameters(application)["model"]["options"] = [{"value": model}]
    with pytest.raises(ValueError, match="canonical native catalog"):
        adapter.create_plan(tmp_path / "refused", application, FULL, MIG_PRODUCT, model=model)
    assert not (tmp_path / "refused").exists()


@pytest.mark.parametrize(
    "model",
    ["base", "small", "medium", "large-v3", "large-v3-turbo", "nvidia/parakeet-tdt-0.6b-v3"],
)
def test_packaged_canonical_native_models_remain_available(application, model):
    native.application_parameters(application)["model"]["options"] = [
        {"name": model, "value": model}
    ]
    settings = adapter.settings_for(application, MIG_PRODUCT, model)
    specification, selector = adapter.specification(application, settings, "0" * 32)
    assert settings.model == model
    assert selector["model"] == model
    assert specification["parameters"]["model"]["value"] == model


def test_private_catalog_override_is_refused_before_catalog_access(
    tmp_path, application, monkeypatch
):
    private = tmp_path / "private-catalog.json"
    monkeypatch.setenv("CBS_TRANSCRIBER_MODELS", str(private))
    monkeypatch.setattr(
        native, "provider_catalog", lambda: pytest.fail("must not read private catalog")
    )
    with pytest.raises(ValueError, match="packaged native catalog"):
        adapter.create_plan(tmp_path / "refused", application, FULL, MIG_PRODUCT)
    assert not (tmp_path / "refused").exists()


def test_cached_foreign_catalog_cannot_supply_aliases(tmp_path, application, monkeypatch):
    foreign = deepcopy(native.provider_catalog())
    foreign["ucloud_transcriber"]["models"][0]["aliases"] = ["parakeet"]
    monkeypatch.setattr(native, "provider_catalog", lambda: foreign)
    with pytest.raises(ValueError, match="packaged native catalog"):
        adapter.create_plan(tmp_path / "refused", application, FULL, MIG_PRODUCT)
    assert not (tmp_path / "refused").exists()


def test_fresh_import_refuses_override_before_any_application_catalog_read(tmp_path):
    child = """
import json, os, runpy, sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
canary = Path(sys.argv[2])
read_text = Path.read_text
attempted = []
def guarded(path, *args, **kwargs):
    if path == canary:
        attempted.append(True)
        raise AssertionError('Private catalog access attempted')
    return read_text(path, *args, **kwargs)
Path.read_text = guarded
os.environ['CBS_TRANSCRIBER_MODELS'] = str(canary)
try:
    runpy.run_path(sys.argv[1], run_name='qualification_import')
except ValueError as error:
    assert 'packaged native catalog' in str(error)
else:
    raise AssertionError('Inherited catalog override was accepted')
assert not any(name.startswith('cbs_transcriber') for name in sys.modules)
print(json.dumps({'override_refused': True, 'catalog_read_attempted': bool(attempted)}))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            child,
            str(SCRIPT),
            str(tmp_path / "private-canary.json"),
            str(SCRIPT.parents[1] / "src"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"override_refused": True, "catalog_read_attempted": False}


@pytest.mark.parametrize(
    "field,value", [("id", native.COMPUTE_ID), ("category", "other"), ("provider", "other")]
)
def test_wrong_candidate_product_cannot_become_mig_plan(tmp_path, application, field, value):
    candidate = {**MIG_PRODUCT, field: value}
    with pytest.raises(ValueError):
        adapter.create_plan(tmp_path / "refused", application, FULL, candidate)
    assert not (tmp_path / "refused").exists()


def test_existing_directory_is_never_opened_as_store(tmp_path, application, monkeypatch):
    sentinel = tmp_path / "user-data.txt"
    sentinel.write_text("keep")
    monkeypatch.setattr(adapter, "Store", lambda *_: pytest.fail("must not open existing storage"))
    with pytest.raises(FileExistsError):
        adapter.create_plan(tmp_path, application, FULL, MIG_PRODUCT)
    assert sentinel.read_text() == "keep"


def test_changed_public_source_bytes_are_refused_before_store(tmp_path, application, monkeypatch):
    source = tmp_path / "fixtures"
    source.mkdir()
    for filename in ["manifest.json", "da_dk_0000.wav", "da_dk_0001.wav"]:
        shutil.copyfile(adapter.SUITE / filename, source / filename)
    with (source / "da_dk_0001.wav").open("ab") as stream:
        stream.write(b"changed")
    monkeypatch.setattr(adapter, "SUITE", source)
    with pytest.raises(ValueError, match="public fixture bytes"):
        adapter.create_plan(tmp_path / "refused", application, FULL, MIG_PRODUCT)
    assert not (tmp_path / "refused").exists()


def test_other_sdk_version_is_refused_without_installation_or_store(
    tmp_path, application, monkeypatch
):
    monkeypatch.setattr(adapter, "version", lambda _: "0.2.0")
    with pytest.raises(ValueError, match="released ucloud-workflow 0.1.9"):
        adapter.create_plan(tmp_path / "refused", application, FULL, MIG_PRODUCT)
    assert not (tmp_path / "refused").exists()


def test_mistyped_default_is_not_frozen_into_a_native_spec(tmp_path, application):
    native.application_parameters(application)["mergeSpeakers"]["defaultValue"] = 1
    with pytest.raises(ValueError, match="default does not match"):
        adapter.create_plan(tmp_path / "refused", application, FULL, MIG_PRODUCT)
    assert not (tmp_path / "refused").exists()


@pytest.mark.parametrize(
    "change", ["product", "input", "interactive", "extra_resource", "application"]
)
def test_supplied_specification_must_match_exact_owner(planned, change):
    _, plan = planned
    supplied = deepcopy(plan["runs"]["mig"]["specification"])
    if change == "product":
        supplied["product"] = FULL
    elif change == "input":
        supplied["parameters"]["inputDir"]["path"] += "/other"
    elif change == "interactive":
        supplied["parameters"]["jupyterLab"]["value"] = True
    elif change == "extra_resource":
        supplied["resources"].append({"type": "file", "path": "/other/data", "readOnly": False})
    else:
        supplied["application"]["version"] = "other"
    with pytest.raises(ValueError, match="specification"):
        adapter.validate_specification(plan, "mig", supplied)


def test_semantic_plan_changes_fail_even_with_new_checksum(planned):
    _, plan = planned
    changed = deepcopy(plan)
    changed["runs"]["mig"]["specification"]["product"] = FULL
    reseal(changed)
    with pytest.raises(ValueError, match="specification/settings"):
        adapter.validate_plan(changed)
    changed = deepcopy(plan)
    changed["scopes"]["live_execution"] = "passed"
    reseal(changed)
    with pytest.raises(ValueError, match="plan changed"):
        adapter.validate_plan(changed)
    assert plan["scopes"] == adapter.SCOPES


def test_supplied_output_bytes_and_cleanup_only_verify_identity(planned):
    _, plan = planned
    assert (
        adapter.validate_specification(plan, "mig", plan["runs"]["mig"]["specification"])[
            "live_execution"
        ]
        == "not_checked"
    )
    result = adapter.validate_results(plan, "mig", outputs(plan))
    assert len(result["outputs"]) == 2
    assert result["local_result_commit"] == "not_checked"
    assert result["gpu_execution"] == "not_checked"
    cleanup = adapter.validate_cleanup_identity(plan, "mig", cleanup_claim(plan))
    assert cleanup["cleanup_identity"] == "matches"
    assert cleanup["cleanup_authority"] == "not_granted"


@pytest.mark.parametrize("change", [None, "version", "input", "path"])
def test_parakeet_suffixless_output_keeps_frozen_identity(tmp_path, application, change):
    canonical = "nvidia/parakeet-tdt-0.6b-v3"
    native.application_parameters(application)["model"]["options"] = [
        {"name": canonical, "value": "parakeet"}
    ]
    plan = adapter.create_plan(
        tmp_path / "parakeet", application, FULL, MIG_PRODUCT, model=canonical
    )
    data = outputs(plan)
    for item in data:
        item["output_name"] = Path(item["input_filename"]).stem + "_parakeet.json"
    if change == "version":
        data[0]["output_name"] = data[0]["output_name"].replace("_parakeet", "_parakeet-v2")
    elif change == "input":
        data[0]["output_name"] = data[1]["output_name"]
    elif change == "path":
        data[0]["output_name"] = "../" + data[0]["output_name"]
    if change:
        with pytest.raises(ValueError):
            adapter.validate_results(plan, "mig", data)
    else:
        result = adapter.validate_results(plan, "mig", data)
        assert len(result["outputs"]) == 2
        assert result["local_result_commit"] == result["gpu_execution"] == "not_checked"
        assert plan["runs"]["mig"]["selector"]["model"] == "parakeet"
        assert plan["runs"]["mig"]["settings"]["model"] == canonical


@pytest.mark.parametrize(
    "change", ["duplicate", "missing", "recording", "input", "model", "path", "json", "empty"]
)
def test_missing_or_foreign_outputs_are_refused(planned, change):
    _, plan = planned
    data = outputs(plan)
    if change == "duplicate":
        data[1] = deepcopy(data[0])
    elif change == "missing":
        data.pop()
    elif change == "recording":
        data[0]["recording_id"] = data[1]["recording_id"]
    elif change == "input":
        data[0]["input_filename"] = data[1]["input_filename"]
    elif change == "model":
        data[0]["output_name"] = data[0]["output_name"].replace("_base_da", "_other_da")
    elif change == "path":
        data[0]["output_name"] = "../" + data[0]["output_name"]
    elif change == "json":
        data[0]["data"] = b"not JSON"
    else:
        data[0]["data"] = b'{"segments": []}'
    with pytest.raises(ValueError):
        adapter.validate_results(plan, "mig", data)


@pytest.mark.parametrize("change", ["other_lane", "shared_models", "member", "duplicate"])
def test_cleanup_identity_cannot_expand_to_another_owner(planned, change):
    _, plan = planned
    claim = cleanup_claim(plan)
    if change == "other_lane":
        claim = cleanup_claim(plan, "b200")
    elif change == "shared_models":
        claim["folder"] = "/offline-native-mig/qualification/cbs-runtime/models"
    elif change == "member":
        claim["job_ids"][0] = plan["runs"]["b200"]["members"][0]["job_id"]
    else:
        claim["job_ids"].append(claim["job_ids"][0])
    with pytest.raises(ValueError, match="Cleanup identity"):
        adapter.validate_cleanup_identity(plan, "mig", claim)


@pytest.mark.parametrize(
    "raw",
    [b'{"a":1,"a":2}', b'{"a":NaN}', b"{" * 2000, b"x" * (adapter.MAX_JSON_BYTES + 1)],
    ids=["duplicate-key", "nonfinite", "deep-json", "oversized"],
)
def test_supplied_json_is_strict_and_bounded(tmp_path, raw):
    path = tmp_path / "metadata.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        adapter.read_json(path)


def test_cli_has_no_live_execution_switch(tmp_path, application, capsys):
    args = cli_arguments(tmp_path, application, tmp_path / "planned")
    adapter.main(args)
    assert json.loads(capsys.readouterr().out)["live_execution"] == "not_checked"
    with pytest.raises(SystemExit) as failure:
        adapter.main([*args, "--execute"])
    assert failure.value.code == 2


def cli_arguments(tmp_path, application, output):
    paths = {}
    for name, value in {"application": application, "full": FULL, "mig": MIG_PRODUCT}.items():
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value))
        paths[name] = path
    args = [
        "--application-json",
        str(paths["application"]),
        "--b200-product-json",
        str(paths["full"]),
        "--mig-product-json",
        str(paths["mig"]),
        "--output",
        str(output),
    ]
    return args


@pytest.mark.parametrize("label", [None, "nvidia/parakeet-tdt-0.6b-v2"])
def test_cli_rejects_bare_parakeet_without_output_or_success_evidence(
    tmp_path, application, capsys, label
):
    option = {"value": "parakeet"}
    if label is not None:
        option["name"] = label
    native.application_parameters(application)["model"]["options"] = [option]
    output = tmp_path / "refused"
    args = [*cli_arguments(tmp_path, application, output), "--model", "parakeet"]
    with pytest.raises(ValueError, match="canonical native catalog"):
        adapter.main(args)
    assert not output.exists()
    assert capsys.readouterr().out == ""
