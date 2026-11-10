"""Read-only, funded MIG selection for new CBS-runtime jobs; native compute stays separate."""

import math

import httpx
from ucloud_workflow import UCloudAPIError
from ucloud_workflow.resources import ManagementError

from . import providers
from . import ucloud_native as native
from .ucloud_discovery import _object, _wallets

# Exact public reference and slice metadata verified read-only on 26 October 2026.
MIG_PRODUCT = {
    "id": "gpu-nvidia-b200-1-mig.1g",
    "category": "gpu-nvidia-b200",
    "provider": "ucloud",
}
POLICY = "mig-v1"
# The pinned models run sequentially on bounded audio windows. This is a readiness floor,
# not a claim about host RAM or a substitute for the first live MIG acceptance run.
MINIMUM_GPU_MEMORY_GB = 8


def select(client, project_id, provider):
    """Require one exact compatible slice and its project's positive COMPUTE wallet."""
    funded = set()
    for wallet in _wallets(client):
        owner = _object(wallet.get("owner"))
        category = _object(wallet.get("paysFor"))
        balance = wallet.get("maxUsable")
        if (
            owner.get("projectId") == project_id
            and category.get("productType") == "COMPUTE"
            and type(balance) is int
            and balance > 0
        ):
            funded.add((category.get("name"), category.get("provider")))
    products = _object(_object(client.retrieve_compute_products()).get("productsByProvider"))
    entries = products.get(provider, [])
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("Malformed UCloud runtime compute metadata.")
    matches = []
    for entry in entries:
        product, support = _object(entry.get("product")), _object(entry.get("support"))
        reference = support.get("product")
        if reference != MIG_PRODUCT or reference["provider"] != provider:
            continue
        category = _object(product.get("category"))
        fraction = _object(product.get("fraction"))
        if (
            product.get("name") != reference["id"]
            or category.get("name") != reference["category"]
            or category.get("provider") != provider
            or category.get("productType") != "COMPUTE"
            or type(product.get("gpu")) is not int
            or product["gpu"] != 1
            or product.get("gpuModel") != "B200-mig.1g.23gb"
            or type(fraction.get("numerator")) is not int
            or type(fraction.get("denominator")) is not int
            or fraction != {"numerator": 1, "denominator": 7}
            or _object(support.get("docker", {})).get("enabled") is not True
            or support.get("queueStatus") not in {"AVAILABLE", "BUSY"}
            or (reference["category"], provider) not in funded
        ):
            continue
        matches.append(dict(reference))
    if len(matches) != 1:
        raise ValueError(
            "CBS runtime requires one verified, funded B200 MIG slice in this UCloud project. "
            + (
                "The compute metadata is ambiguous. "
                if matches
                else "The required slice is unavailable or has no usable project allocation. "
            )
            + "Check the connection and project allocation; no full GPU was selected."
        )
    return matches[0]


def freeze(settings, credentials):
    """Read metadata only, leaving the native connection's product and policy unchanged."""
    setup = settings.ucloud_transcriber
    key = credentials.get(providers.credential_scope(settings))
    try:
        with native.client_for(settings, key) as client:
            product = select(client, setup.project_id, setup.product["provider"])
    except (UCloudAPIError, httpx.HTTPError, ManagementError):
        raise ValueError(
            "Could not verify the CBS runtime's funded MIG allocation. Check the UCloud "
            "connection and retry; no full GPU was selected."
        ) from None
    return {"runtime_product": product, "runtime_compute_policy": POLICY}


def frozen_product(params):
    """Only pre-policy snapshots inherit native compute; a partial new identity is invalid."""
    if "runtime_product" not in params and "runtime_compute_policy" not in params:
        return None
    if (
        params.get("runtime_compute_policy") != POLICY
        or params.get("runtime_product") != MIG_PRODUCT
    ):
        raise ValueError(
            "This job's frozen CBS runtime compute identity is invalid. Start a new job."
        )
    return dict(params["runtime_product"])


def require_gpu_memory(doctor):
    """The worker uses the first CUDA device; never sum devices or infer VRAM from host RAM."""
    gpu = doctor.get("gpu")
    gpu = gpu if isinstance(gpu, dict) else {}
    devices = gpu.get("devices")
    memory = (
        devices[0].get("memory_gb")
        if isinstance(devices, list) and devices and isinstance(devices[0], dict)
        else None
    )
    if (
        gpu.get("available") is not True
        or type(memory) not in {int, float}
        or not math.isfinite(memory)
        or memory < MINIMUM_GPU_MEMORY_GB
    ):
        raise ValueError(
            f"The UCloud job must report at least {MINIMUM_GPU_MEMORY_GB} GiB of GPU memory "
            "on its first CUDA device. No recording was uploaded."
        )
