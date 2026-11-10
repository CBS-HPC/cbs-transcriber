"""CBS-runtime MIG selection uses read-only metadata and never changes native compute."""

import copy

import pytest

from cbs_transcriber import runtime_compute


@pytest.fixture
def compute():
    class Compute:
        def __init__(self):
            self.entry = {
                "product": {
                    "name": runtime_compute.MIG_PRODUCT["id"],
                    "category": {
                        "name": "gpu-nvidia-b200",
                        "provider": "ucloud",
                        "productType": "COMPUTE",
                    },
                    "gpu": 1,
                    "gpuModel": "B200-mig.1g.23gb",
                    "fraction": {"numerator": 1, "denominator": 7},
                    "memoryInGigs": 36,
                    # Real product responses leave these zero even with a funded wallet.
                    "balance": 0,
                    "maxUsableBalance": 0,
                },
                "support": {
                    "product": dict(runtime_compute.MIG_PRODUCT),
                    "docker": {"enabled": True},
                    "queueStatus": "AVAILABLE",
                },
            }
            self.entries = [self.entry]
            self.wallet = {
                "owner": {"projectId": "synthetic-project"},
                "paysFor": {
                    "name": "gpu-nvidia-b200",
                    "provider": "ucloud",
                    "productType": "COMPUTE",
                },
                "maxUsable": 100,
            }
            self.wallets = [self.wallet]

        def retrieve_compute_products(self):
            return {"productsByProvider": {"ucloud": self.entries}}

        def browse_wallets(self, *, include_children):
            assert include_children is False
            return {"items": self.wallets, "next": None}

    return Compute()


def select(compute):
    return runtime_compute.select(compute, "synthetic-project", "ucloud")


def test_one_verified_funded_slice_is_selected_independently_of_product_balance(compute):
    assert select(compute) == runtime_compute.MIG_PRODUCT


@pytest.mark.parametrize("kind", ["full-gpu", "two-slices", "missing"])
def test_no_full_gpu_or_other_slice_count_fallback(compute, kind):
    if kind == "missing":
        compute.entries = []
    else:
        identifier = "gpu-nvidia-b200-1-gpu" if kind == "full-gpu" else "gpu-nvidia-b200-2-mig.1g"
        compute.entry["product"]["name"] = identifier
        compute.entry["support"]["product"]["id"] = identifier
    with pytest.raises(ValueError, match="no full GPU was selected"):
        select(compute)


@pytest.mark.parametrize("problem", ["unfunded", "owner", "category", "provider", "storage"])
def test_only_this_projects_compute_wallet_funds_the_slice(compute, problem):
    if problem == "unfunded":
        compute.wallet["maxUsable"] = 0
    elif problem == "owner":
        compute.wallet["owner"]["projectId"] = "another-project"
    elif problem == "category":
        compute.wallet["paysFor"]["name"] = "another-category"
    elif problem == "provider":
        compute.wallet["paysFor"]["provider"] = "another-provider"
    else:
        compute.wallet["paysFor"]["productType"] = "STORAGE"
    with pytest.raises(ValueError, match="no usable project allocation"):
        select(compute)


@pytest.mark.parametrize(
    "problem", ["docker", "gpu", "model", "fraction", "category", "provider", "queue"]
)
def test_slice_requires_matching_compatible_metadata(compute, problem):
    product, support = compute.entry["product"], compute.entry["support"]
    if problem == "docker":
        support["docker"]["enabled"] = False
    elif problem == "gpu":
        product["gpu"] = 2
    elif problem == "model":
        product["gpuModel"] = "a-different-GPU"
    elif problem == "fraction":
        product["fraction"] = {"numerator": 1, "denominator": 1}
    elif problem == "category":
        product["category"]["name"] = "another-category"
    elif problem == "provider":
        product["category"]["provider"] = "another-provider"
    else:
        support["queueStatus"] = "UNAVAILABLE"
    with pytest.raises(ValueError, match="required slice is unavailable"):
        select(compute)


def test_duplicate_eligible_slice_metadata_is_not_silently_selected(compute):
    compute.entries.append(copy.deepcopy(compute.entry))
    with pytest.raises(ValueError, match="ambiguous"):
        select(compute)


def test_busy_slice_queues_normally_without_a_full_gpu_fallback(compute):
    compute.entry["support"]["queueStatus"] = "BUSY"
    assert select(compute) == runtime_compute.MIG_PRODUCT


@pytest.mark.parametrize("memory", [None, True, 0, 7.9, float("inf"), float("nan"), "23"])
def test_gpu_memory_refuses_missing_invalid_or_insufficient_reports(memory):
    with pytest.raises(ValueError, match="No recording was uploaded"):
        runtime_compute.require_gpu_memory(
            {
                "gpu": {"available": True, "devices": [{"memory_gb": memory}]},
                "memoryInGigs": 1000,
            }
        )


def test_gpu_memory_uses_first_device_and_never_sums_devices():
    with pytest.raises(ValueError, match="first CUDA device"):
        runtime_compute.require_gpu_memory(
            {"gpu": {"available": True, "devices": [{"memory_gb": 4}, {"memory_gb": 80}]}}
        )
    runtime_compute.require_gpu_memory({"gpu": {"available": True, "devices": [{"memory_gb": 23}]}})


@pytest.mark.parametrize(
    "params",
    [
        {"runtime_compute_policy": "mig-v1"},
        {"runtime_product": runtime_compute.MIG_PRODUCT},
        {"runtime_compute_policy": "unknown", "runtime_product": runtime_compute.MIG_PRODUCT},
    ],
)
def test_partial_or_unknown_new_policy_never_inherits_the_full_gpu(params):
    with pytest.raises(ValueError, match="compute identity is invalid"):
        runtime_compute.frozen_product(params)
    assert runtime_compute.frozen_product({"settings": {"legacy": True}}) is None
