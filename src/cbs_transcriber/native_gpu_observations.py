"""Bounded, supplied-data observations for ADR 0184; deliberately unintegrated.

These helpers do no I/O and confer no submission, termination, consent or ownership
permission. The caller must obtain fresh responses in the frozen credential/project
context, prove complete job history and retain all existing owner/control fences.
Funding stays with the existing bounded ucloud_funding observer, outside this helper.
Capacity is an observation, never a reservation or a free-instance count. Live UCloud
metadata and the queued cancellation protocol still need separate acceptance.
"""

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime

MIG_PRODUCT = {
    "id": "gpu-nvidia-b200-1-mig.1g",
    "category": "gpu-nvidia-b200",
    "provider": "ucloud",
}
FULL_PRODUCT = {**MIG_PRODUCT, "id": "gpu-nvidia-b200-1-gpu"}
RATIO = 7
READ_SPACING = 30
READ_BYTES = 1024 * 1024
TOTAL_BYTES = 4 * READ_BYTES
MAX_PRODUCTS = 1000
MAX_UPDATES = 1000
MAX_INPUTS = 1000
FREE = frozenset({"AVAILABLE", "BUSY"})
STATUSES = FREE | {"FULL"}
TERMINAL = frozenset({"SUCCESS", "FAILURE", "EXPIRED", "ABORTED", "CANCELLED", "SUSPENDED"})
_SHA = re.compile(r"[a-f0-9]{64}")


@dataclass(frozen=True)
class CapacityObservation:
    context_sha256: str
    observed_at: str
    monotonic_seconds: float
    mig: str
    full: str


@dataclass(frozen=True)
class NeverStartedObservation:
    remote_id: str
    terminal_state: str
    update_states: tuple[str, ...]
    input_count: int


def _text(value, limit=200):
    return (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and not any(ord(char) < 32 for char in value)
    )


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key")
        value[key] = item
    return value


def _number(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _constant(value):
    raise ValueError("Non-finite JSON number")


def _decode(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= READ_BYTES:
        raise ValueError("Unbounded observation")
    value = json.loads(
        raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_number
    )
    if not isinstance(value, dict):
        raise ValueError("Expected an object")
    return value


def _timestamp(value):
    if not _text(value, 40):
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except (ValueError, OverflowError):
        return False


def _reference(value):
    return (
        isinstance(value, dict)
        and set(value) == {"id", "category", "provider"}
        and all(_text(item) for item in value.values())
    )


def _fraction(value, denominator):
    return (
        isinstance(value, dict)
        and set(value) == {"numerator", "denominator"}
        and all(type(item) is int for item in value.values())
        and value == {"numerator": 1, "denominator": denominator}
    )


def _identity(entry, expected, backend):
    product, support = entry.get("product"), entry.get("support")
    if not isinstance(product, dict) or not isinstance(support, dict):
        return False
    category, enabled = product.get("category"), support.get(backend)
    if (
        support.get("product") != expected
        or product.get("name") != expected["id"]
        or not isinstance(category, dict)
        or category.get("name") != expected["category"]
        or category.get("provider") != expected["provider"]
        or category.get("productType") != "COMPUTE"
        or type(product.get("gpu")) is not int
        or product["gpu"] != 1
        or not isinstance(enabled, dict)
        or enabled.get("enabled") is not True
    ):
        return False
    if expected == MIG_PRODUCT:
        return product.get("gpuModel") == "B200-mig.1g.23gb" and _fraction(
            product.get("fraction"), RATIO
        )
    return product.get("gpuModel") in (None, "B200") and (
        product.get("fraction") is None or _fraction(product["fraction"], 1)
    )


def _prices(mig, full):
    left, right = mig["product"], full["product"]
    for key in ("accountingUnit", "accountingFrequency", "freeToUse"):
        a, b = left["category"], right["category"]
        if (key in a) != (key in b) or a.get(key) != b.get(key):
            return False
    for category in (left["category"], right["category"]):
        if category.get("freeToUse", False) is not False:
            return False
        if "accountingUnit" in category or "accountingFrequency" in category:
            # Same GPU/periodic unit contract as ucloud_funding; price metadata only.
            unit = category.get("accountingUnit")
            if (
                not isinstance(unit, dict)
                or set(unit) != {"name", "namePlural", "floatingPoint", "displayFrequencySuffix"}
                or unit.get("name") != "GPU"
                or unit.get("namePlural") != "GPU"
                or unit.get("floatingPoint") is not False
                or unit.get("displayFrequencySuffix") is not True
                or category.get("accountingFrequency")
                not in {"PERIODIC_SECOND", "PERIODIC_MINUTE", "PERIODIC_HOUR", "PERIODIC_DAY"}
            ):
                return False
    prices = [product.get("price") for product in (left, right)]
    return prices == [None, None] or (
        all(type(price) is int and price > 0 for price in prices) and prices[1] == RATIO * prices[0]
    )


def _finite_clock(value):
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def capacity(raw, *, backend, context_sha256, observed_at, monotonic_seconds):
    """Return two exact, verified product statuses, or None for any unusable read.

    `backend` must come from verified frozen application metadata. `context_sha256`
    identifies the same endpoint/project/application observation context; never hash a
    credential into a durable/public receipt. The caller supplies its monotonic clock.
    """
    if (
        not isinstance(backend, str)
        or backend not in {"docker", "singularity", "native"}
        or not isinstance(context_sha256, str)
        or _SHA.fullmatch(context_sha256) is None
        or not _timestamp(observed_at)
        or type(monotonic_seconds) not in {int, float}
        or not _finite_clock(monotonic_seconds)
        or monotonic_seconds < 0
    ):
        return None
    try:
        providers = _decode(raw).get("productsByProvider")
        entries = providers.get(MIG_PRODUCT["provider"]) if isinstance(providers, dict) else None
        if not isinstance(entries, list) or len(entries) > MAX_PRODUCTS:
            return None
        found = {}
        for entry in entries:
            support = entry.get("support") if isinstance(entry, dict) else None
            ref = support.get("product") if isinstance(support, dict) else None
            if not _reference(ref) or not isinstance(entry.get("product"), dict):
                return None
            key = tuple(ref[field] for field in ("id", "category", "provider"))
            if key in found:
                return None
            found[key] = entry
        exact = [
            found.get(tuple(ref[field] for field in ("id", "category", "provider")))
            for ref in (MIG_PRODUCT, FULL_PRODUCT)
        ]
        if any(entry is None for entry in exact):
            return None
        mig, full = exact
        if not all(
            _identity(entry, ref, backend)
            for entry, ref in zip(exact, (MIG_PRODUCT, FULL_PRODUCT), strict=True)
        ):
            return None
        states = [entry["support"].get("queueStatus") for entry in exact]
        if any(state not in STATUSES for state in states) or not _prices(mig, full):
            return None
        return CapacityObservation(context_sha256, observed_at, monotonic_seconds, *states)
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        return None


def agree(earlier, later):
    """Two verified FULL observations, >=30 seconds apart, with a full B200 free now.

    A caller resets its earlier observation after every failed/unknown read or intervening
    MIG status change. These values are process-local; they are not restart authority.
    """
    return (
        isinstance(earlier, CapacityObservation)
        and isinstance(later, CapacityObservation)
        and earlier.context_sha256 == later.context_sha256
        and earlier.mig == later.mig == "FULL"
        and later.full in FREE
        and later.monotonic_seconds - earlier.monotonic_seconds >= READ_SPACING
    )


class CapacityWindow:
    """One owner's process-local consecutive reads; failed reads always reset it."""

    def __init__(self):
        self.previous = None

    def add(self, observation):
        candidate = agree(self.previous, observation)
        self.previous = observation if isinstance(observation, CapacityObservation) else None
        return candidate


def never_started(raw_job, raw_outputs, *, remote_id, updates_complete, expected_inputs, inputs):
    """Observe terminal queued history, an empty complete out/ listing and exact input sizes.

    `updates_complete is True` must come from a separately verified retrieval contract,
    never from an empty/missing list. `expected_inputs` maps exact previously owned remote
    input paths to recorded byte sizes; `inputs` contains `(requested_path, raw_response)`.
    This checks only supplied observations, not live history completeness, ownership,
    cancellation success, quiescence, a current owner generation or permission to submit.
    """
    if (
        not _text(remote_id)
        or updates_complete is not True
        or not isinstance(expected_inputs, dict)
        or not 0 < len(expected_inputs) <= MAX_INPUTS
        or not isinstance(inputs, (list, tuple))
        or len(inputs) != len(expected_inputs)
        or any(
            not _text(path, 4000) or type(size) is not int or size < 0
            for path, size in expected_inputs.items()
        )
    ):
        return None
    try:
        remote, output = _decode(raw_job), _decode(raw_outputs)
        total = len(raw_job) + len(raw_outputs)
        status, updates = remote.get("status"), remote.get("updates")
        if (
            remote.get("id") != remote_id
            or not isinstance(status, dict)
            or status.get("state") not in TERMINAL
            or status.get("startedAt") is not None
            or not isinstance(updates, list)
            or len(updates) > MAX_UPDATES
            or "next" not in output
            or output.get("items") != []
            or output.get("next") not in (None, "")
        ):
            return None
        states = []
        for update in updates:
            state = update.get("state") if isinstance(update, dict) else "invalid"
            if state is not None and state not in {"IN_QUEUE", status["state"]}:
                return None
            if state is not None and state not in states:
                states.append(state)
        if "IN_QUEUE" not in states:
            return None
        seen = set()
        for supplied in inputs:
            if not isinstance(supplied, (list, tuple)) or len(supplied) != 2:
                return None
            path, raw = supplied
            if not isinstance(path, str) or path not in expected_inputs or path in seen:
                return None
            seen.add(path)
            item = _decode(raw)
            total += len(raw)
            details = item.get("status")
            size = details.get("sizeInBytes") if isinstance(details, dict) else None
            if (
                total > TOTAL_BYTES
                or type(size) is not int
                or size != expected_inputs[path]
                or ("id" in item and item["id"] != path)
            ):
                return None
        return NeverStartedObservation(remote_id, status["state"], tuple(states), len(seen))
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        return None
