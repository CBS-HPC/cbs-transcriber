"""Native model identity checks use synthetic metadata without any UCloud calls."""

from copy import deepcopy

import pytest

from cbs_transcriber import ucloud_native as native
from cbs_transcriber.providers import Settings

CANONICAL = "nvidia/parakeet-tdt-0.6b-v3"
VERSIONED = "parakeet-tdt-0.6b-v3"


def specification(options, directory):
    settings = Settings(
        provider_type="ucloud",
        base_url="https://synthetic.invalid",
        model=CANONICAL,
        language="da",
        ucloud_transcriber={
            "project_id": "synthetic",
            "application": {"name": "transcriber", "version": "1.12"},
            "product": {
                "id": native.COMPUTE_ID,
                "category": "gpu-nvidia-b200",
                "provider": "synthetic",
            },
            "storage_path": "/synthetic-drive/cbs",
        },
    )
    parameters = {
        "input_file": {"type": "input_file"},
        "input_dir": {"type": "input_directory"},
        "output_dir": {"type": "input_directory"},
        "model": {"type": "enumeration", "options": options},
        "language": {"type": "text"},
        "output_format": {"type": "enumeration", "options": [{"value": "json"}]},
    }
    template = {
        "application": settings.ucloud_transcriber.application,
        "product": settings.ucloud_transcriber.product,
        "replicas": 1,
        "parameters": {},
        "resources": [],
    }
    frozen = deepcopy((settings.model_dump(), template, parameters))
    try:
        if directory:
            result, _ = native.build_directory_specification(
                settings, template, parameters, "/synthetic-drive/owned", "synthetic-batch"
            )
        else:
            result = native.build_specification(
                settings, template, parameters, "/synthetic-drive/owned", ".wav", "synthetic-job"
            )
    finally:
        assert (settings.model_dump(), template, parameters) == frozen
    return result["parameters"]["model"]["value"]


@pytest.mark.parametrize("directory", [False, True], ids=["single", "shared"])
@pytest.mark.parametrize("label", ["nvidia/parakeet-tdt-0.6b-v2", "Parakeet", None])
def test_v26_generic_parakeet_requires_a_version_proving_label(directory, label):
    with pytest.raises(ValueError, match="not offered"):
        specification([{"name": label, "value": "parakeet"}], directory)


@pytest.mark.parametrize("directory", [False, True], ids=["single", "shared"])
@pytest.mark.parametrize("label", [CANONICAL, VERSIONED])
def test_v26_current_parakeet_declared_identity_is_preserved(directory, label):
    assert specification([{"name": label, "value": "parakeet"}], directory) == "parakeet"


@pytest.mark.parametrize("directory", [False, True], ids=["single", "shared"])
@pytest.mark.parametrize("value", [CANONICAL, VERSIONED])
def test_v26_versioned_parakeet_values_stay_compatible(directory, value):
    assert specification([{"value": value}], directory) == value


def test_v26_conflicting_generic_labels_do_not_prove_canonical_identity():
    with pytest.raises(ValueError, match="not offered"):
        specification(
            [
                {"name": CANONICAL, "value": "parakeet"},
                {"name": "nvidia/parakeet-tdt-0.6b-v2", "value": "parakeet"},
            ],
            False,
        )


def test_v26_exact_canonical_value_takes_precedence_over_generic_fallback():
    assert (
        specification(
            [
                {"name": "nvidia/parakeet-tdt-0.6b-v2", "value": "parakeet"},
                {"name": CANONICAL, "value": CANONICAL},
            ],
            False,
        )
        == CANONICAL
    )
