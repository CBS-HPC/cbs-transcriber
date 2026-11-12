"""Basic mocked tests for the standalone funding observer (ADR 0178, T146).

The released SDK's own ``UCloudClient`` constructs its HTTPX client; only its transport is
replaced by ``httpx.MockTransport``. No UCloud contact, real token or credential store.
"""

import dataclasses
import functools
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
import ucloud_workflow.client as sdk_client

from cbs_transcriber import ucloud_funding as funding

SERVER = "https://ucloud.example.test"
PROJECT = "11111111-2222-3333-4444-555555555555"
OTHER_PROJECT = "99999999-8888-7777-6666-555555555555"
KEY = "synthetic-funding-test-key-0123456789"
NOW = datetime(2026, 11, 12, 12, 0, 0, 123456, tzinfo=UTC)


def accounting_unit(name, plural=None, *, floating=False, suffix=True):
    return {
        "name": name,
        "namePlural": name if plural is None else plural,
        "floatingPoint": floating,
        "displayFrequencySuffix": suffix,
    }


def wallet(
    name,
    *,
    balance=0,
    unit=None,
    frequency="PERIODIC_MINUTE",
    product_type="COMPUTE",
    provider="ucloud",
    owner=None,
    quota=0,
):
    """Shape follows upstream WalletV2/ProductCategory at the ADR 0178 pin."""
    category = {
        "name": name,
        "provider": provider,
        "productType": product_type,
        "accountingUnit": unit or accounting_unit("GPU"),
        "accountingFrequency": frequency,
        "freeToUse": False,
        "allowSubAllocations": True,
    }
    return {
        "owner": owner or {"type": "project", "username": "", "projectId": PROJECT},
        "paysFor": category,
        "allocationGroups": [],
        "children": [],
        "totalUsage": 0,
        "localUsage": 0,
        "maxUsable": balance,
        "quota": quota,
        "totalAllocated": 0,
        "activeUsage": 0,
        "activeQuota": quota,
        "lastSignificantUpdateAt": 1762905600000,
    }


def gpu(balance=600, **kwargs):
    kwargs.setdefault("unit", accounting_unit("GPU"))
    return wallet("gpu-nvidia-b200", balance=balance, **kwargs)


def cpu(balance=0, **kwargs):
    kwargs.setdefault("unit", accounting_unit("Core"))
    kwargs.setdefault("frequency", "PERIODIC_HOUR")
    return wallet("cpu-amd-zen5", balance=balance, **kwargs)


def storage(**kwargs):
    # Unrelated wallets are ignored after identity checks, so this unit is never interpreted.
    return wallet(
        "u1-cephfs",
        product_type="STORAGE",
        unit=accounting_unit("GB", suffix=False),
        frequency="ONCE",
        balance=-5,
        **kwargs,
    )


def money_compute():
    return wallet(
        "u1-standard",
        unit=accounting_unit("DKK", floating=True, suffix=False),
        balance=10**15,
    )


def page(items, cursor=None):
    return {"items": items, "next": cursor, "itemsPerPage": 100}


class Body(httpx.SyncByteStream):
    def __init__(self, chunks, tick=None, after=None):
        self.chunks, self.tick, self.after = chunks, tick, after
        self.closed, self.served = False, 0

    def __iter__(self):
        for chunk in self.chunks:
            if self.tick:
                self.tick()
            self.served += 1
            yield chunk
        if self.after:
            self.after()

    def close(self):
        self.closed = True


def reply(payload, *, status=200, headers=None, length=True, **stream):
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    base = {"Content-Type": "application/json"}
    if length:
        base["Content-Length"] = str(len(data))
    return httpx.Response(
        status, headers={**base, **(headers or {})}, stream=Body([data], **stream)
    )


class Clock:
    def __init__(self):
        self.value = 5000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class Service(httpx.MockTransport):
    """Scripted replies; records requests, served bodies and transport closure."""

    def __init__(self, *replies):
        self.replies, self.requests, self.bodies, self.closed = list(replies), [], [], False
        super().__init__(self._handle)

    def _handle(self, request):
        self.requests.append(request)
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        result = result(request) if callable(result) else result
        if isinstance(result.stream, Body):
            self.bodies.append(result.stream)
        return result

    def close(self):
        self.closed = True


@pytest.fixture
def connect(monkeypatch):
    def install(*replies, **client_options):
        service = Service(*replies)
        factory = functools.partial(httpx.Client, transport=service, **client_options)
        monkeypatch.setattr(sdk_client, "httpx", SimpleNamespace(Client=factory))
        return service

    return install


def run(clock=None):
    return funding.observe(SERVER, PROJECT, KEY, monotonic=clock or Clock(), now=lambda: NOW)


def assert_unknown(observation, reason):
    assert observation.complete is False
    assert observation.reason == reason
    for category in (observation.gpu, observation.cpu):
        assert (category.state, category.balance, category.unit, category.frequency) == (
            "unknown",
            None,
            None,
            None,
        )
    assert observation.affordability == "not_checked"
    assert observation.reservation is False


def assert_secret_free(observation):
    text = repr(observation) + json.dumps(observation.as_dict())
    assert KEY not in text and "Bearer" not in text


def test_complete_observation_reports_only_bounded_scalars(connect):
    service = connect(reply(page([storage(), gpu(600), money_compute(), cpu(0, quota=1000)])))

    observation = run()

    assert observation.as_dict() == {
        "server": SERVER,
        "project": PROJECT,
        "started_at": "2026-11-12T12:00:00.123Z",
        "finished_at": "2026-11-12T12:00:00.123Z",
        "complete": True,
        "gpu": {
            "category": "gpu-nvidia-b200",
            "provider": "ucloud",
            "state": "positive_current",
            "balance": 600,
            "unit": "GPU",
            "frequency": "PERIODIC_MINUTE",
        },
        "cpu": {
            "category": "cpu-amd-zen5",
            "provider": "ucloud",
            "state": "no_usable_balance",
            "balance": 0,
            "unit": "Core",
            "frequency": "PERIODIC_HOUR",
        },
        "reason": None,
        "schema": "ucloud-funding-observation-v1",
        "affordability": "not_checked",
        "reservation": False,
    }
    assert all(body.closed for body in service.bodies) and service.closed
    with pytest.raises(dataclasses.FrozenInstanceError):
        observation.reservation = True


def test_requests_are_get_only_with_exact_origin_headers_and_pagination(connect):
    service = connect(reply(page([storage()], "cursor-1")), reply(page([gpu(42)])))

    observation = run()

    assert observation.complete is True
    assert (observation.gpu.state, observation.gpu.balance) == ("positive_current", 42)
    assert (observation.cpu.state, observation.cpu.balance) == ("absent", None)
    assert len(service.requests) == 2
    for index, request in enumerate(service.requests):
        assert request.method == "GET" and request.content == b""
        assert (request.url.scheme, request.url.host, request.url.port) == (
            "https",
            "ucloud.example.test",
            None,
        )
        assert request.url.path == "/api/accounting/v2/browseWallets"
        expected = {"includeChildren": "false", "itemsPerPage": "100"}
        if index:
            expected["next"] = "cursor-1"
        assert dict(request.url.params) == expected
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        assert request.headers["Project"] == PROJECT
        assert request.headers["Accept"] == "application/json"
        assert request.headers["Accept-Encoding"] == "identity"
    assert all(body.closed for body in service.bodies) and service.closed


def test_absence_needs_a_complete_validated_traversal(connect):
    connect(reply(page([storage(), money_compute()])))

    observation = run()

    assert observation.complete is True and observation.reason is None
    assert (observation.gpu.state, observation.cpu.state) == ("absent", "absent")


def test_hierarchy_zero_is_reported_without_explaining_exhaustion(connect):
    connect(reply(page([gpu(0, quota=10_000), cpu(7)])))

    observation = run()

    assert (observation.gpu.state, observation.gpu.balance) == ("no_usable_balance", 0)
    assert (observation.cpu.state, observation.cpu.balance) == ("positive_current", 7)


def test_later_error_page_discards_earlier_positive_matches(connect):
    service = connect(
        reply(page([gpu(600), cpu(50)], "cursor-1")),
        reply({"error": "unavailable"}, status=503),
    )

    observation = run()

    assert_unknown(observation, "server_error")
    assert all(body.closed for body in service.bodies) and service.closed


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (400, "http_status"),
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "not_found"),
        (429, "rate_limited"),
        (500, "server_error"),
    ],
)
def test_http_errors_are_unknown_with_curated_reasons(connect, status, reason):
    connect(reply({"message": f"rejected {KEY}"}, status=status))

    observation = run()

    assert_unknown(observation, reason)
    assert_secret_free(observation)


def test_redirect_is_refused_and_never_followed(connect):
    service = connect(
        reply(b"", status=302, headers={"Location": "https://elsewhere.example.test/x"}),
        reply(page([gpu(1)])),
    )

    observation = run()

    assert_unknown(observation, "redirect_refused")
    assert len(service.requests) == 1


def test_sdk_client_that_follows_redirects_is_refused_before_any_request(connect):
    service = connect(reply(page([gpu(1)])), follow_redirects=True)

    assert_unknown(run(), "transport_unsupported")
    assert service.requests == [] and service.closed


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (
            storage(owner={"type": "project", "username": "", "projectId": OTHER_PROJECT}),
            "foreign_owner",
        ),
        (gpu(5, owner={"type": "user", "username": "someone", "projectId": ""}), "foreign_owner"),
        (gpu(5, owner={"type": "group", "username": "", "projectId": PROJECT}), "malformed_wallet"),
        ({"paysFor": gpu()["paysFor"], "maxUsable": 5}, "malformed_wallet"),
        (wallet("u1-cephfs", product_type=""), "malformed_wallet"),
        (gpu(5, product_type="STORAGE"), "malformed_wallet"),
        ("not-a-wallet", "malformed_wallet"),
    ],
)
def test_foreign_or_malformed_identity_makes_everything_unknown(connect, row, reason):
    connect(reply(page([gpu(600), cpu(40), row])))

    assert_unknown(run(), reason)


def test_duplicate_target_wallets_are_unknown_never_summed(connect):
    connect(reply(page([gpu(300)], "cursor-1")), reply(page([gpu(300)])))

    assert_unknown(run(), "duplicate_target")


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"frequency": "ONCE"}, "unsupported_unit"),
        ({"frequency": "PERIODIC_WEEK"}, "unsupported_unit"),
        ({"unit": accounting_unit("DKK", floating=True, suffix=False)}, "unsupported_unit"),
        ({"unit": accounting_unit("GB")}, "unsupported_unit"),
        ({"unit": accounting_unit("GPU", "GPUs")}, "unsupported_unit"),
        ({"unit": accounting_unit("gpu")}, "unsupported_unit"),
        ({"unit": accounting_unit("GPU", floating=True)}, "unsupported_unit"),
        ({"unit": accounting_unit("GPU", suffix=False)}, "unsupported_unit"),
        ({"unit": {**accounting_unit("GPU"), "extra": 1}}, "malformed_unit"),
        ({"unit": {**accounting_unit("GPU"), "floatingPoint": "false"}}, "malformed_unit"),
        ({"unit": {"name": "GPU", "namePlural": "GPU"}}, "malformed_unit"),
        ({"frequency": None}, "malformed_unit"),
    ],
)
def test_target_units_outside_the_pin_are_unknown(connect, changes, reason):
    row = gpu(600, **changes)
    if changes.get("frequency", "") is None:
        del row["paysFor"]["accountingFrequency"]
    connect(reply(page([row, cpu(9)])))

    assert_unknown(run(), reason)


@pytest.mark.parametrize(
    ("balance", "reason"),
    [
        (600.0, "malformed_wallet"),
        (True, "malformed_wallet"),
        ("600", "malformed_wallet"),
        (None, "malformed_wallet"),
        (2**63, "malformed_wallet"),
        (-1, "unsupported_balance"),
    ],
)
def test_target_balances_require_exact_supported_integers(connect, balance, reason):
    connect(reply(page([cpu(balance)])))

    assert_unknown(run(), reason)


def test_declared_oversized_page_is_refused_before_reading(connect):
    service = connect(reply(b" " * (funding.MAX_PAGE_BYTES + 1)))

    assert_unknown(run(), "page_too_large")
    assert service.bodies[0].closed and service.bodies[0].served == 0


def test_streamed_oversized_page_without_length_is_refused(connect):
    chunk = b" " * (600 * 1024)
    stream = Body([chunk, chunk, chunk])
    connect(httpx.Response(200, headers={"Content-Type": "application/json"}, stream=stream))

    assert_unknown(run(), "page_too_large")
    assert stream.closed and stream.served == 2


def padded(payload, size):
    data = json.dumps(payload).encode()
    return data + b" " * (size - len(data))


def test_aggregate_byte_limit_spans_pages(connect):
    replies = [reply(padded(page([], f"cursor-{n}"), 900 * 1024)) for n in range(5)]
    service = connect(*replies)

    assert_unknown(run(), "total_too_large")
    assert len(service.requests) == 5


def test_page_count_limit(connect):
    replies = [reply(page([], f"cursor-{n}")) for n in range(funding.MAX_PAGES + 1)]
    service = connect(*replies)

    assert_unknown(run(), "too_many_pages")
    assert len(service.requests) == funding.MAX_PAGES


def compact_row(name):
    return {
        "owner": {"type": "project", "projectId": PROJECT},
        "paysFor": {"name": name, "provider": "ucloud", "productType": "STORAGE"},
    }


def test_row_limit_boundary(connect):
    connect(reply(page([compact_row(f"s{n}") for n in range(funding.MAX_ROWS)])))
    assert run().complete is True

    connect(reply(page([compact_row(f"s{n}") for n in range(funding.MAX_ROWS + 1)])))
    assert_unknown(run(), "too_many_rows")


@pytest.mark.parametrize(
    ("cursor", "reason"),
    [
        ("c" * (funding.MAX_CURSOR_CHARS + 1), "cursor_invalid"),
        ("", "cursor_invalid"),
        (5, "cursor_invalid"),
        ("bad\ncursor", "cursor_invalid"),
    ],
)
def test_invalid_cursors_are_unknown(connect, cursor, reason):
    connect(reply(page([gpu(1)], cursor)))

    assert_unknown(run(), reason)


def test_longest_allowed_cursor_is_followed(connect):
    cursor = "c" * funding.MAX_CURSOR_CHARS
    service = connect(reply(page([], cursor)), reply(page([gpu(1)])))

    assert run().gpu.state == "positive_current"
    assert service.requests[1].url.params["next"] == cursor


def test_repeated_cursor_is_unknown(connect):
    connect(reply(page([], "same")), reply(page([], "same")))

    assert_unknown(run(), "cursor_repeated")


def test_slow_drip_body_hits_the_overall_deadline(connect):
    clock = Clock()
    data = json.dumps(page([gpu(1)])).encode()
    pieces = [data[i : i + 8] for i in range(0, len(data), 8)]
    stream = Body(pieces, tick=lambda: clock.advance(4))
    service = connect(
        httpx.Response(200, headers={"Content-Type": "application/json"}, stream=stream)
    )

    assert_unknown(run(clock), "deadline_exceeded")
    # Small network reads are checked as they arrive, not after buffering to EOF.
    assert stream.served == 4 < len(pieces)
    assert stream.closed and service.closed


def test_deadline_is_checked_at_eof(connect):
    clock = Clock()
    service = connect(reply(page([gpu(1)]), after=lambda: clock.advance(16)))

    assert_unknown(run(clock), "deadline_exceeded")
    assert service.bodies[0].closed


def test_deadline_spans_pages(connect):
    clock = Clock()
    service = connect(
        reply(page([], "cursor-1"), tick=lambda: clock.advance(14.5)),
        reply(page([gpu(1)]), tick=lambda: clock.advance(1)),
    )

    assert_unknown(run(clock), "deadline_exceeded")
    assert len(service.requests) == 2


def test_transport_timeout_is_deadline_unknown(connect):
    connect(httpx.ReadTimeout("slow"))

    assert_unknown(run(), "deadline_exceeded")


@pytest.mark.parametrize("encoding", ["gzip", "br", "identity, gzip"])
def test_compressed_responses_are_refused(connect, encoding):
    service = connect(reply(page([gpu(1)]), headers={"Content-Encoding": encoding}))

    assert_unknown(run(), "compressed_response")
    assert service.requests[0].headers["Accept-Encoding"] == "identity"
    assert service.bodies[0].closed


def test_explicit_identity_encoding_is_accepted(connect):
    connect(reply(page([gpu(1)]), headers={"Content-Encoding": "identity"}))

    assert run().complete is True


@pytest.mark.parametrize("content_type", ["text/html", "application/problem+json", ""])
def test_unexpected_content_type_is_unknown(connect, content_type):
    connect(reply(page([gpu(1)]), headers={"Content-Type": content_type}))

    assert_unknown(run(), "unexpected_content_type")


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"{", "malformed_json"),
        (b'{"items": [], "items": [], "next": null}', "malformed_json"),
        (b'{"items": [], "next": null, "x": NaN}', "malformed_json"),
        (b'\xef\xbb\xbf{"items": [], "next": null}', "malformed_json"),
        (b"\xff", "malformed_json"),
        (b"[]", "malformed_page"),
        (b'{"items": []}', "malformed_page"),
        (b'{"items": null, "next": null}', "malformed_page"),
    ],
)
def test_malformed_pages_are_unknown(connect, body, reason):
    connect(reply(body))

    assert_unknown(run(), reason)


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (httpx.ConnectError(f"cannot reach with {KEY}"), "connection_failed"),
        (RuntimeError(f"unexpected {KEY}"), "internal_error"),
    ],
)
def test_failures_never_expose_secrets_or_exception_text(connect, failure, reason):
    service = connect(failure)

    observation = run()

    assert_unknown(observation, reason)
    assert_secret_free(observation)
    assert "cannot reach" not in repr(observation) and service.closed


@pytest.mark.parametrize(
    ("server", "project", "key"),
    [
        ("http://ucloud.example.test", PROJECT, KEY),
        ("https://ucloud.example.test/", PROJECT, KEY),
        ("https://ucloud.example.test/api", PROJECT, KEY),
        ("https://UCloud.example.test", PROJECT, KEY),
        ("https://ucloud.example.test:443", PROJECT, KEY),
        ("https://user@ucloud.example.test", PROJECT, KEY),
        (SERVER, "", KEY),
        (SERVER, "has space", KEY),
        (SERVER, PROJECT, ""),
        (SERVER, PROJECT, KEY + "\n"),
        (SERVER, PROJECT, None),
    ],
)
def test_invalid_explicit_context_raises_without_echo(connect, server, project, key):
    service = connect(reply(page([gpu(1)])))

    with pytest.raises(ValueError) as error:
        funding.observe(server, project, key, now=lambda: NOW)

    assert KEY not in str(error.value) and PROJECT not in str(error.value)
    assert service.requests == []


def test_non_default_port_is_a_canonical_origin(connect):
    service = connect(reply(page([gpu(1)])))

    observation = funding.observe(
        "https://ucloud.example.test:8443", PROJECT, KEY, monotonic=Clock(), now=lambda: NOW
    )

    assert observation.complete is True
    assert service.requests[0].url.port == 8443


def test_observation_invariants_cannot_claim_affordability_or_mix_unknown():
    unknown = funding.CategoryFunding(funding.GPU_CATEGORY, "ucloud", "unknown")
    cpu_unknown = funding.CategoryFunding(funding.CPU_CATEGORY, "ucloud", "unknown")
    positive = funding.CategoryFunding(
        funding.CPU_CATEGORY, "ucloud", "positive_current", 1, "Core", "PERIODIC_HOUR"
    )
    common = {"server": SERVER, "project": PROJECT, "started_at": "t", "finished_at": "t"}
    for changes in (
        {
            "complete": False,
            "gpu": unknown,
            "cpu": cpu_unknown,
            "affordability": "checked",
            "reason": "internal_error",
        },
        {
            "complete": False,
            "gpu": unknown,
            "cpu": cpu_unknown,
            "reservation": True,
            "reason": "internal_error",
        },
        {"complete": False, "gpu": unknown, "cpu": positive, "reason": "internal_error"},
        {"complete": False, "gpu": unknown, "cpu": cpu_unknown, "reason": "free text"},
        {"complete": True, "gpu": unknown, "cpu": positive},
    ):
        with pytest.raises(ValueError):
            funding.FundingObservation(**common, **changes)
    for state, balance in (("positive_current", 0), ("no_usable_balance", 3), ("absent", 0)):
        with pytest.raises(ValueError):
            funding.CategoryFunding(
                funding.GPU_CATEGORY, "ucloud", state, balance, "GPU", "PERIODIC_MINUTE"
            )
