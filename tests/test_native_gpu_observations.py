"""ADR 0184 helper gate: supplied synthetic bytes only, no allocation or live evidence."""

import copy
import json

import pytest

from cbs_transcriber import native_gpu_observations as gpu

CONTEXT = "a" * 64
STAMP = "2026-11-20T16:00:00+00:00"
REMOTE = "synthetic-remote-mig"
INPUT = "/owned/cbs-batch-synthetic/input/synthetic.wav"


def raw(value):
    return json.dumps(value).encode()


def products():
    category = {"name": "gpu-nvidia-b200", "provider": "ucloud", "productType": "COMPUTE"}
    entries = []
    for ref, denominator, status in (
        (gpu.MIG_PRODUCT, 7, "FULL"),
        (gpu.FULL_PRODUCT, 1, "AVAILABLE"),
    ):
        entries.append(
            {
                "product": {
                    "name": ref["id"],
                    "category": copy.deepcopy(category),
                    "gpu": 1,
                    "gpuModel": "B200-mig.1g.23gb" if denominator == 7 else "B200",
                    "fraction": {"numerator": 1, "denominator": denominator},
                },
                "support": {
                    "product": copy.deepcopy(ref),
                    "docker": {"enabled": True},
                    "queueStatus": status,
                },
            }
        )
    return {"productsByProvider": {"ucloud": entries}}


def observe(payload=None, seconds=0, **options):
    arguments = {
        "backend": "docker",
        "context_sha256": CONTEXT,
        "observed_at": STAMP,
        "monotonic_seconds": seconds,
    }
    arguments.update(options)
    return gpu.capacity(raw(products() if payload is None else payload), **arguments)


def job():
    return {
        "id": REMOTE,
        "status": {"state": "SUCCESS", "startedAt": None},
        "updates": [
            {"state": "IN_QUEUE", "timestamp": 1},
            {"status": "waiting", "timestamp": 2},
            {"state": "SUCCESS", "timestamp": 3},
        ],
    }


def proof(remote=None, outputs=None, **options):
    arguments = {
        "remote_id": REMOTE,
        "updates_complete": True,
        "expected_inputs": {INPUT: 12},
        "inputs": [(INPUT, raw({"id": INPUT, "status": {"sizeInBytes": 12}}))],
    }
    arguments.update(options)
    return gpu.never_started(
        raw(job() if remote is None else remote),
        raw({"items": [], "next": None} if outputs is None else outputs),
        **arguments,
    )


def test_two_verified_full_reads_wait_the_entire_30_seconds():
    first = observe()
    assert first is not None
    for elapsed in (0, 1, 27, 29.999):
        assert not gpu.agree(first, observe(seconds=elapsed))
    assert gpu.agree(first, observe(seconds=30))
    assert gpu.agree(first, observe(seconds=40))
    assert not gpu.agree(first, observe(seconds=30, context_sha256="b" * 64))


def test_failed_read_and_flapping_reset_consecutive_evidence():
    window = gpu.CapacityWindow()
    assert not window.add(observe())
    assert not window.add(None)
    assert not window.add(observe(seconds=30))
    busy = products()
    busy["productsByProvider"]["ucloud"][0]["support"]["queueStatus"] = "BUSY"
    assert not window.add(observe(busy, seconds=60))
    assert not window.add(observe(seconds=90))
    assert window.add(observe(seconds=120))


@pytest.mark.parametrize("mig", ["AVAILABLE", "BUSY", "FULL"])
@pytest.mark.parametrize("full", ["AVAILABLE", "BUSY", "FULL"])
def test_statuses_are_observations_never_a_count(mig, full):
    payload = products()
    entries = payload["productsByProvider"]["ucloud"]
    entries[0]["support"]["queueStatus"], entries[1]["support"]["queueStatus"] = mig, full
    first, later = observe(payload), observe(payload, seconds=30)
    assert first.mig == mig and first.full == full
    assert gpu.agree(first, later) == (mig == "FULL" and full in gpu.FREE)


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "duplicate",
        "provider",
        "category",
        "type",
        "gpu-bool",
        "gpu-count",
        "model",
        "fraction",
        "fraction-bool",
        "backend",
        "support-ref",
        "unknown",
        "malformed-status",
    ],
)
def test_unknown_malformed_or_unverified_product_never_enters_the_window(index, problem):
    payload = products()
    entries = payload["productsByProvider"]["ucloud"]
    target, product = entries[index], entries[index]["product"]
    if problem == "missing":
        entries.pop(index)
    elif problem == "duplicate":
        entries.append(copy.deepcopy(target))
    elif problem in {"provider", "category", "type"}:
        key = {"provider": "provider", "category": "name", "type": "productType"}[problem]
        product["category"][key] = "foreign"
    elif problem in {"gpu-bool", "gpu-count"}:
        product["gpu"] = True if problem == "gpu-bool" else 2
    elif problem == "model":
        product["gpuModel"] = "V100"
    elif problem in {"fraction", "fraction-bool"}:
        product["fraction"]["numerator"] = 2 if problem == "fraction" else True
    elif problem == "backend":
        target["support"]["docker"]["enabled"] = 1
    elif problem == "support-ref":
        target["support"].pop("product")
    else:
        target["support"]["queueStatus"] = "UNKNOWN" if problem == "unknown" else []
    assert observe(payload) is None
    assert not gpu.agree(observe(payload), observe(seconds=30))


@pytest.mark.parametrize(
    "problem",
    [
        "wrong-price",
        "one-price",
        "bool-price",
        "zero-price",
        "unit",
        "frequency",
        "free",
        "one-unit",
    ],
)
def test_inconsistent_live_credit_relation_withholds_the_question(problem):
    payload = products()
    mig, full = [entry["product"] for entry in payload["productsByProvider"]["ucloud"]]
    mig["price"], full["price"] = 1, 7
    if problem == "wrong-price":
        full["price"] = 8
    elif problem == "one-price":
        full.pop("price")
    elif problem == "bool-price":
        mig["price"] = True
    elif problem == "zero-price":
        mig["price"], full["price"] = 0, 0
    elif problem in {"unit", "frequency", "one-unit"}:
        key = "accountingFrequency" if problem == "frequency" else "accountingUnit"
        mig["category"][key] = "one"
        if problem != "one-unit":
            full["category"][key] = "two"
    else:
        mig["category"]["freeToUse"] = full["category"]["freeToUse"] = True
    assert observe(payload) is None


def test_positive_exact_price_relation_and_absent_full_fraction():
    payload = products()
    mig, full = [entry["product"] for entry in payload["productsByProvider"]["ucloud"]]
    mig["price"], full["price"] = 3, 21
    full.pop("fraction")
    assert observe(payload) is not None


@pytest.mark.parametrize(
    "options",
    [
        {"backend": []},
        {"backend": "unknown"},
        {"context_sha256": ""},
        {"observed_at": "2026-11-20T16:00:00"},
        {"monotonic_seconds": True},
        {"monotonic_seconds": -1},
        {"monotonic_seconds": float("inf")},
        {"monotonic_seconds": float("nan")},
        {"monotonic_seconds": 10**400},
    ],
)
def test_bad_observation_context_refuses(options):
    assert observe(**options) is None


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"[]",
        b"null",
        b"{",
        b"\xff",
        b'{"productsByProvider":{},"productsByProvider":{}}',
        b'{"productsByProvider":NaN}',
        b" " * (gpu.READ_BYTES + 1),
        b"[" * 1100,
    ],
    ids=["empty", "array", "null", "broken", "utf8", "duplicate-key", "nan", "oversized", "depth"],
)
def test_bounded_strict_json_cannot_become_capacity_evidence(payload):
    assert (
        gpu.capacity(
            payload,
            backend="docker",
            context_sha256=CONTEXT,
            observed_at=STAMP,
            monotonic_seconds=0,
        )
        is None
    )


def test_never_started_requires_terminal_complete_queued_history_and_all_input_sizes():
    observed = proof()
    assert observed == gpu.NeverStartedObservation(REMOTE, "SUCCESS", ("IN_QUEUE", "SUCCESS"), 1)
    assert proof(updates_complete=False) is None
    assert proof(updates_complete=1) is None
    assert proof(outputs={"items": []}) is None
    assert proof(outputs={"items": [], "next": "more"}) is None
    assert proof(outputs={"items": [{"id": "/owned/out/transcribe.log"}]}) is None


@pytest.mark.parametrize(
    "problem",
    [
        "id",
        "running",
        "started",
        "missing-history",
        "bad-history",
        "too-much-history",
        "running-update",
        "unknown-update",
        "nonobject-update",
        "wrong-terminal-update",
        "canceling",
        "updates-type",
    ],
)
def test_no_started_or_malformed_history_can_authorize_a_second_allocation(problem):
    value = job()
    if problem == "id":
        value["id"] = "another-allocation"
    elif problem == "running":
        value["status"]["state"] = "RUNNING"
    elif problem == "started":
        value["status"]["startedAt"] = 123
    elif problem == "missing-history":
        value.pop("updates")
    elif problem == "bad-history":
        value["updates"] = None
    elif problem == "too-much-history":
        value["updates"] = [{"state": "IN_QUEUE"}] * (gpu.MAX_UPDATES + 1)
    elif problem == "running-update":
        value["updates"].append({"state": "RUNNING"})
    elif problem == "unknown-update":
        value["updates"].append({"state": "UNKNOWN"})
    elif problem == "nonobject-update":
        value["updates"].append(None)
    elif problem == "wrong-terminal-update":
        value["updates"].append({"state": "FAILURE", "timestamp": 4})
    elif problem == "canceling":
        value["status"]["state"] = "CANCELING"
    else:
        value["updates"].append({"state": []})
    assert proof(remote=value) is None


@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "duplicate",
        "foreign-path",
        "foreign-id",
        "resized",
        "bool-size",
        "malformed",
        "missing-size",
    ],
)
def test_every_recorded_owned_input_must_be_observed_once_with_its_exact_size(problem):
    item = {"id": INPUT, "status": {"sizeInBytes": 12}}
    inputs = [(INPUT, raw(item))]
    if problem == "missing":
        inputs.clear()
    elif problem == "duplicate":
        inputs.append(inputs[0])
    elif problem == "foreign-path":
        inputs = [("/other/input", raw(item))]
    elif problem == "foreign-id":
        item["id"] = "/other/input"
        inputs = [(INPUT, raw(item))]
    elif problem in {"resized", "bool-size"}:
        item["status"]["sizeInBytes"] = 13 if problem == "resized" else True
        inputs = [(INPUT, raw(item))]
    elif problem == "malformed":
        inputs = [(INPUT, b"bad-json")]
    else:
        inputs = [(INPUT, raw({"status": {}}))]
    assert proof(inputs=inputs) is None


def test_helpers_have_no_file_database_network_or_mutation_access(monkeypatch):
    import builtins
    import socket
    import sqlite3

    import httpx

    payload, expected = products(), {INPUT: 12}
    before = copy.deepcopy(payload)

    def forbidden(*args, **kwargs):
        pytest.fail("A supplied-data observation attempted I/O")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(socket, "socket", forbidden)
        patch.setattr(sqlite3, "connect", forbidden)
        patch.setattr(httpx, "Client", forbidden)
        assert observe(payload) is not None
        assert proof(expected_inputs=expected) is not None
    assert payload == before and expected == {INPUT: 12}


@pytest.mark.parametrize(
    "replacement",
    [None, True, 1, "bad", [], {}],
    ids=["null", "bool", "number", "text", "array", "object"],
)
@pytest.mark.parametrize(
    "path",
    [
        ("productsByProvider",),
        ("productsByProvider", "ucloud"),
        ("productsByProvider", "ucloud", 0),
        ("productsByProvider", "ucloud", 0, "product"),
        ("productsByProvider", "ucloud", 0, "support"),
        ("productsByProvider", "ucloud", 0, "product", "category"),
        ("productsByProvider", "ucloud", 0, "support", "product"),
        ("productsByProvider", "ucloud", 0, "support", "docker"),
    ],
    ids=["providers", "entries", "entry", "product", "support", "category", "reference", "backend"],
)
def test_required_metadata_shapes_fail_closed(path, replacement):
    payload = products()
    cursor = payload
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = replacement
    assert observe(payload) is None


def test_json_float_overflow_is_not_silently_accepted_even_in_an_unused_field():
    encoded = raw(products())
    encoded = b'{"unused":1e999,' + encoded[1:]
    assert (
        gpu.capacity(
            encoded,
            backend="docker",
            context_sha256=CONTEXT,
            observed_at=STAMP,
            monotonic_seconds=0,
        )
        is None
    )


@pytest.mark.parametrize(
    "problem",
    ["integer-flags", "currency", "core-unit", "nonperiodic", "malformed", "extra-unit-field"],
)
def test_equal_but_unsupported_units_cannot_support_the_credit_claim(problem):
    payload = products()
    unit = {
        "name": "GPU",
        "namePlural": "GPU",
        "floatingPoint": False,
        "displayFrequencySuffix": True,
    }
    frequency = "PERIODIC_MINUTE"
    if problem == "integer-flags":
        unit["floatingPoint"], unit["displayFrequencySuffix"] = 0, 1
    elif problem == "currency":
        unit["floatingPoint"] = True
    elif problem == "core-unit":
        unit["name"] = unit["namePlural"] = "Core"
    elif problem == "nonperiodic":
        frequency = "ONCE"
    elif problem == "malformed":
        unit = "GPU"
    else:
        unit["foreign"] = True
    for entry in payload["productsByProvider"]["ucloud"]:
        entry["product"]["category"].update(accountingUnit=unit, accountingFrequency=frequency)
    assert observe(payload) is None


def test_matching_gpu_periodic_accounting_metadata_is_retained_only_as_evidence():
    payload = products()
    for entry in payload["productsByProvider"]["ucloud"]:
        entry["product"]["category"].update(
            accountingUnit={
                "name": "GPU",
                "namePlural": "GPU",
                "floatingPoint": False,
                "displayFrequencySuffix": True,
            },
            accountingFrequency="PERIODIC_MINUTE",
            freeToUse=False,
        )
    assert observe(payload) is not None


@pytest.mark.parametrize("updates", [[], [{"state": "SUCCESS"}], [{"status": "unknown history"}]])
def test_terminal_status_alone_is_not_a_recorded_queued_history(updates):
    value = job()
    value["updates"] = updates
    assert proof(remote=value) is None


def test_all_limits_are_enforced_before_observations_are_accepted(monkeypatch):
    monkeypatch.setattr(gpu, "MAX_PRODUCTS", 1)
    assert observe() is None
    monkeypatch.setattr(gpu, "MAX_UPDATES", 2)
    assert proof() is None
    monkeypatch.setattr(gpu, "MAX_UPDATES", 1000)
    monkeypatch.setattr(gpu, "MAX_INPUTS", 0)
    assert proof() is None
    monkeypatch.setattr(gpu, "MAX_INPUTS", 1000)
    monkeypatch.setattr(gpu, "TOTAL_BYTES", 1)
    assert proof() is None


def test_multiple_inputs_require_the_same_complete_exact_set():
    another = INPUT.replace("synthetic.wav", "another.wav")
    expected = {INPUT: 12, another: 19}
    first = (INPUT, raw({"status": {"sizeInBytes": 12}}))
    second = (another, raw({"status": {"sizeInBytes": 19}}))
    assert proof(expected_inputs=expected, inputs=[second, first]).input_count == 2
    assert proof(expected_inputs=expected, inputs=[first, first]) is None
    assert proof(expected_inputs=expected, inputs=[first]) is None


@pytest.mark.parametrize(
    "encoded",
    [b"", b"null", b"[]", b"{", b'{"items":[],"items":[]}'],
    ids=["empty", "null", "array", "broken", "duplicate-key"],
)
def test_bad_supplied_output_listing_never_becomes_never_started_evidence(encoded):
    assert (
        gpu.never_started(
            raw(job()),
            encoded,
            remote_id=REMOTE,
            updates_complete=True,
            expected_inputs={INPUT: 12},
            inputs=[(INPUT, raw({"status": {"sizeInBytes": 12}}))],
        )
        is None
    )


@pytest.mark.parametrize("field", ["freeToUse", "floatingPoint", "displayFrequencySuffix"])
def test_each_products_accounting_flags_require_real_booleans(field):
    payload = products()
    for entry in payload["productsByProvider"]["ucloud"]:
        entry["product"]["category"].update(
            accountingUnit={
                "name": "GPU",
                "namePlural": "GPU",
                "floatingPoint": False,
                "displayFrequencySuffix": True,
            },
            accountingFrequency="PERIODIC_MINUTE",
            freeToUse=False,
        )
    category = payload["productsByProvider"]["ucloud"][1]["product"]["category"]
    if field == "freeToUse":
        category[field] = 0
    else:
        category["accountingUnit"][field] = 0 if field == "floatingPoint" else 1
    assert observe(payload) is None


@pytest.mark.parametrize("state", ["SUSPENDED", "ABORTED", "CANCELLED"])
def test_review_nonfinal_or_unsupported_states_are_not_never_started_candidates(state):
    value = job()
    value["status"]["state"] = state
    value["updates"][-1]["state"] = state
    assert proof(remote=value) is None


@pytest.mark.parametrize(
    "update",
    [
        {"state": None, "startedAt": 123},
        {"state": None, "startedAt": None},
        {"state": None, "foreign": "field"},
        {"state": None, "status": {}},
        {"state": None, "expectedState": "UNKNOWN"},
        {"state": None, "expectedDifferentState": 1},
        {"state": None, "allowRestart": 0},
        {"state": None, "newTimeAllocation": "60"},
        {"state": None, "outputFolder": []},
        {"state": None, "resourceList": {}},
    ],
    ids=[
        "started",
        "unknown-null",
        "unknown-field",
        "bad-status",
        "bad-expected",
        "integer-expected-flag",
        "integer-restart-flag",
        "bad-allocation",
        "bad-output",
        "bad-resources",
    ],
)
def test_review_malformed_or_contradictory_stateless_updates_are_refused(update):
    value = job()
    value["updates"].append({"timestamp": 4, **update})
    assert proof(remote=value) is None


@pytest.mark.parametrize(
    "timestamp",
    [None, True, 1.5, "4", [], 2**63, -(2**63) - 1],
    ids=["null", "bool", "float", "text", "array", "too-large", "too-small"],
)
def test_review_job_updates_require_protocol_integer_timestamps(timestamp):
    value = job()
    value["updates"].append({"state": None, "timestamp": timestamp})
    assert proof(remote=value) is None


def test_review_missing_timestamp_is_not_a_complete_update():
    value = job()
    value["updates"].append({"state": None})
    assert proof(remote=value) is None


@pytest.mark.parametrize("field", ["expectedDifferentState", "allowRestart"])
def test_review_valid_nullable_update_flags_are_not_actual_running_states(field):
    value = job()
    value["updates"].insert(
        1,
        {
            "timestamp": 1,
            "state": None,
            "expectedState": "RUNNING",
            field: True,
            "resourceList": [],
        },
    )
    assert proof(remote=value) is not None


def test_review_nonempty_resource_changes_remain_explicitly_unchecked():
    value = job()
    value["updates"].append(
        {"timestamp": 4, "state": None, "resourceList": [{"type": "file", "path": "/other"}]}
    )
    assert proof(remote=value) is None


def test_review_only_pinned_final_states_are_observed():
    for state in ("SUCCESS", "FAILURE", "EXPIRED"):
        value = job()
        value["status"]["state"] = value["updates"][-1]["state"] = state
        assert proof(remote=value).terminal_state == state
