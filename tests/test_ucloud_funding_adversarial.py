"""Independent adversarial tests for the funding observer (ADR 0178, T146 / V221).

Written apart from the builder's suite to find what is wrong rather than to confirm it.
Nothing contacts UCloud: replies come from ``httpx.MockTransport`` or from a plain-TCP
HTTP/1.1 peer on 127.0.0.1 started by the test. Real-socket tests keep the SDK's own
``httpx.Client``, ``HTTPTransport``, httpcore pool and h11 parser with real per-read socket
timeouts; only the TLS handshake is skipped. A guarded resolver answers numeric loopback and
names a test registers (always with 127.0.0.1) and refuses every other name. Proxy and CA
variables are cleared unless a test sets them. Keys and projects are synthetic.

Strict xfail tests record defects or hardening gaps found by this review.
"""

import gzip
import importlib.util
import json
import socket
import ssl
import struct
import sys
import threading
import time
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import httpcore
import httpx
import pytest
import ucloud_workflow.client as sdk_client

from cbs_transcriber import ucloud_funding as funding

SERVER = "https://ucloud.example.test"
PROJECT = "1f2e3d4c-5b6a-4789-8abc-def012345678"
OTHER_PROJECT = "9e8d7c6b-5a49-4382-9fed-cba987654321"
KEY = "synthetic-adversarial-key-0123456789"
NOW = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
MIB = 1024 * 1024
GRACE = 0.75  # Scheduling slack for wall-clock assertions on a busy workstation.
ENVIRONMENT = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "UCLOUD_SERVER",
    "UCLOUD_TOKEN",
    "UCLOUD_PROJECT",
)
MISSING = object()


# Wallet payloads (upstream WalletV2/ProductCategory shape at the ADR 0178 pin).


def unit(name, plural=None, *, floating=False, suffix=True):
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
    accounting=None,
    frequency="PERIODIC_MINUTE",
    product_type="COMPUTE",
    provider="ucloud",
    owner=None,
):
    return {
        "owner": owner or {"type": "project", "projectId": PROJECT},
        "paysFor": {
            "name": name,
            "provider": provider,
            "productType": product_type,
            "accountingUnit": accounting or unit("GPU"),
            "accountingFrequency": frequency,
            "freeToUse": False,
            "allowSubAllocations": True,
        },
        "allocationGroups": [],
        # Upstream WalletsBrowse sets Children to nil when includeChildren is false.
        "children": None,
        "totalUsage": 0,
        "localUsage": 0,
        "maxUsable": balance,
        "quota": 0,
        "totalAllocated": 0,
        "activeUsage": 0,
        "activeQuota": 0,
        "lastSignificantUpdateAt": 1763366400000,
    }


def gpu(balance=600, **kwargs):
    kwargs.setdefault("accounting", unit("GPU"))
    return wallet(funding.GPU_CATEGORY, balance=balance, **kwargs)


def cpu(balance=40, **kwargs):
    kwargs.setdefault("accounting", unit("Core"))
    kwargs.setdefault("frequency", "PERIODIC_HOUR")
    return wallet(funding.CPU_CATEGORY, balance=balance, **kwargs)


def storage(**kwargs):
    kwargs.setdefault("accounting", unit("GB", suffix=False))
    return wallet("u1-cephfs", product_type="STORAGE", frequency="ONCE", **kwargs)


def compact(name):
    return {
        "owner": {"type": "project", "projectId": PROJECT},
        "paysFor": {"name": name, "provider": "ucloud", "productType": "STORAGE"},
    }


def page(items, cursor=None):
    # Upstream normalizes Items to a non-nil slice and reports itemsPerPage = len(items).
    return {"items": items, "next": cursor, "itemsPerPage": len(items)}


def encode(payload):
    return payload if isinstance(payload, bytes) else json.dumps(payload).encode()


def padded(payload, size):
    data = encode(payload)
    assert len(data) <= size
    return data + b" " * (size - len(data))


def split(data, size):
    return [data[i : i + size] for i in range(0, len(data), size)] or [b""]


def with_raw(row, key, raw):
    """Serialize ``row`` and append one member whose JSON text is given verbatim."""
    return json.dumps(row)[:-1] + f', "{key}": {raw}}}'


def raw_page(*rows, cursor="null"):
    items = ", ".join(row if isinstance(row, str) else json.dumps(row) for row in rows)
    return f'{{"items": [{items}], "next": {cursor}, "itemsPerPage": {len(rows)}}}'.encode()


def balance_page(raw):
    row = gpu()
    del row["maxUsable"]
    return raw_page(with_raw(row, "maxUsable", raw), cpu(1))


# MockTransport service.


class Body(httpx.SyncByteStream):
    def __init__(self, chunks, *, fail=None):
        self.chunks, self.fail = list(chunks), fail
        self.served, self.closed = 0, False

    def __iter__(self):
        for chunk in self.chunks:
            self.served += 1
            yield chunk
        if self.fail is not None:
            raise self.fail

    def close(self):
        self.closed = True


def reply(payload, *, status=200, headers=None, length=True, chunks=None, fail=None):
    """``payload`` is the declared body; ``chunks`` (if given) is what actually streams."""
    data = encode(payload)
    base = {"Content-Type": "application/json"}
    if length:
        base["Content-Length"] = str(len(data))
    stream = Body([data] if chunks is None else chunks, fail=fail)
    return httpx.Response(status, headers={**base, **(headers or {})}, stream=stream)


class Service(httpx.MockTransport):
    def __init__(self, *replies):
        self.replies, self.requests, self.bodies, self.closed = list(replies), [], [], False
        super().__init__(self._handle)

    def _handle(self, request):
        self.requests.append(request)
        result = self.replies.pop(0)
        if isinstance(result.stream, Body):
            self.bodies.append(result.stream)
        return result

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def run(clock=None, server=SERVER):
    return funding.observe(server, PROJECT, KEY, monotonic=clock or Clock(), now=lambda: NOW)


def timed(server):
    started = time.monotonic()
    observation = funding.observe(server, PROJECT, KEY, now=lambda: NOW)
    return observation, time.monotonic() - started


def assert_unknown(observation, reason):
    assert (observation.complete, observation.reason) == (False, reason)
    for category in (observation.gpu, observation.cpu):
        assert (category.state, category.balance, category.unit, category.frequency) == (
            "unknown",
            None,
            None,
            None,
        )
    assert (observation.affordability, observation.reservation) == ("not_checked", False)
    text = repr(observation) + json.dumps(observation.as_dict())
    assert KEY not in text and "Bearer" not in text


@pytest.fixture(autouse=True)
def guarded_network(monkeypatch):
    """No real DNS, proxy, CA or UCloud environment; tests register loopback names."""
    for name in ENVIRONMENT:
        for variant in {name, name.lower()}:
            monkeypatch.delenv(variant, raising=False)
    # A non-empty proxy environment stops urllib's Windows-registry/macOS proxy fallback.
    monkeypatch.setenv("NO_PROXY", "*")
    names = {}
    original = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        if isinstance(host, bytes):
            host = host.decode("ascii", "replace")
        if host == "127.0.0.1":
            return original(host, port, *args, **kwargs)
        if host in names:
            time.sleep(names[host])
            return original("127.0.0.1", port, *args, **kwargs)
        raise socket.gaierror(socket.EAI_NONAME, "test resolver refuses real names")

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    return names


@pytest.fixture
def mock_sdk(monkeypatch):
    """The SDK's own UCloudClient builds its httpx.Client; only the transport is mocked."""

    def install(*replies, adjust=None):
        service = Service(*replies)

        def factory(**kwargs):
            if adjust is not None:
                adjust(kwargs)
            return httpx.Client(transport=service, **kwargs)

        monkeypatch.setattr(sdk_client, "httpx", SimpleNamespace(Client=factory))
        return service

    return install


# Loopback peer and plain-TCP backend.


class PlainStream(httpcore.NetworkStream):
    """The real TCP stream; ``start_tls`` is skipped so an https URL speaks HTTP/1.1."""

    def __init__(self, inner):
        self._inner = inner

    def read(self, max_bytes, timeout=None):
        return self._inner.read(max_bytes, timeout)

    def write(self, buffer, timeout=None):
        self._inner.write(buffer, timeout)

    def close(self):
        self._inner.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return self

    def get_extra_info(self, info):
        return self._inner.get_extra_info(info)


class PlainBackend(httpcore.NetworkBackend):
    sockets = []  # Every client socket opened through this backend (cleared per test).

    def __init__(self):
        self._inner = httpcore.SyncBackend()

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        stream = self._inner.connect_tcp(host, port, timeout, local_address, socket_options)
        PlainBackend.sockets.append(stream._sock)
        return PlainStream(stream)

    def sleep(self, seconds):
        time.sleep(seconds)


def plain_tcp_client(**kwargs):
    """The SDK's arguments, a real HTTPTransport and httpcore pool, minus the TLS handshake.

    An empty, never-used SSL context avoids two CA-bundle loads (about 0.9 s each on this
    workstation) that would otherwise consume most of a shortened test deadline.
    """
    unused = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    transport = httpx.HTTPTransport(verify=unused)
    transport._pool = httpcore.ConnectionPool(ssl_context=unused, network_backend=PlainBackend())
    return httpx.Client(transport=transport, **kwargs)


class Peer:
    """Plain-TCP HTTP/1.1 peer on 127.0.0.1 replaying one scripted step list per request.

    Steps: ``bytes`` are sent, numbers are pauses, ``"close"`` closes and ``"reset"`` aborts.
    ``"eof"`` records the client's FIN, then keeps this side open (it never wakes the client).
    """

    def __init__(self, scripts, *, hangup=False):
        self.scripts, self.hangup = list(scripts), hangup
        self.heads, self.connections, self.eofs = [], 0, []
        self.stop = threading.Event()
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.listener.settimeout(0.05)
        self.port = self.listener.getsockname()[1]
        self.threads = [threading.Thread(target=self._accept, daemon=True)]
        self.threads[0].start()

    @property
    def origin(self):
        return f"https://127.0.0.1:{self.port}"

    def _accept(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.connections += 1
            if self.hangup:
                connection.close()
                continue
            thread = threading.Thread(target=self._serve, args=(connection,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def _serve(self, connection):
        try:
            connection.settimeout(10)
            buffer = b""
            while not self.stop.is_set():
                while b"\r\n\r\n" not in buffer:
                    data = connection.recv(65536)
                    if not data:
                        return
                    buffer += data
                head, buffer = buffer.split(b"\r\n\r\n", 1)
                self.heads.append(head.decode("latin-1"))
                if not self.scripts:
                    return
                for step in self.scripts.pop(0):
                    if isinstance(step, bytes):
                        connection.sendall(step)
                    elif step == "close":
                        return
                    elif step == "reset":
                        linger = struct.pack("HH" if sys.platform == "win32" else "ii", 1, 0)
                        connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
                        return
                    elif step == "eof":
                        while connection.recv(65536):
                            pass
                        self.eofs.append(time.monotonic())
                        self.stop.wait(30)
                        return
                    elif self.stop.wait(step):
                        return
        except OSError:
            return
        finally:
            connection.close()

    def shutdown(self):
        self.stop.set()
        self.listener.close()
        for thread in self.threads:
            thread.join(timeout=5)


@pytest.fixture
def peers():
    started = []

    def start(*scripts, hangup=False):
        peer = Peer(scripts, hangup=hangup)
        started.append(peer)
        return peer

    yield start
    for peer in started:
        peer.shutdown()


@pytest.fixture
def loopback(peers, monkeypatch):
    PlainBackend.sockets.clear()

    def start(*scripts):
        peer = peers(*scripts)
        monkeypatch.setattr(sdk_client, "httpx", SimpleNamespace(Client=plain_tcp_client))
        return peer

    return start


def response(body, *, status="200 OK", headers=(), length=True, content_type="application/json"):
    fields = list(headers)
    if content_type is not None:
        fields.append(("Content-Type", content_type))
    if length:
        fields.append(("Content-Length", str(len(body))))
    head = "".join(f"{name}: {value}\r\n" for name, value in fields)
    return f"HTTP/1.1 {status}\r\n{head}\r\n".encode("latin-1") + body


def chunked(*parts):
    return b"".join(f"{len(part):x}\r\n".encode() + part + b"\r\n" for part in parts) + (
        b"0\r\n\r\n"
    )


def drip(data, interval):
    steps = []
    for value in data:
        steps += [interval, bytes([value])]
    return steps


def header_drip_peer(loopback, count, interval):
    full = response(encode(page([gpu(600)])), headers=[("X-Drip", "d" * 64)])
    start = full.index(b"\r\n") + 2
    return loopback(
        [full[:start], *drip(full[start : start + count], interval), full[start + count :]]
    )


BODY_DRIP_DEADLINE = 2.0
BODY_DRIP_INTERVAL = 1.5  # Below the 2 s per-read timeout, so no single socket read times out.


def body_drip_peer(loopback):
    data = encode(page([gpu(600)]))
    head = response(data)[: -len(data)]
    return loopback([head, *drip(data[:6], BODY_DRIP_INTERVAL), "close"])


def wait_for(condition, timeout=5.0):
    end = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= end:
            return False
        time.sleep(0.01)
    return True


# 1. Real-socket timing.


def test_real_socket_round_trip_carries_only_the_explicit_context(loopback):
    peer = loopback([response(encode(page([gpu(600), cpu(0), storage()])))])

    observation, _ = timed(peer.origin)

    assert observation.complete is True
    assert (observation.gpu.balance, observation.cpu.state) == (600, "no_usable_balance")
    (head,) = peer.heads
    request_line, *fields = head.split("\r\n")
    assert request_line == (
        "GET /api/accounting/v2/browseWallets?includeChildren=false&itemsPerPage=100 HTTP/1.1"
    )
    headers = dict(field.split(": ", 1) for field in fields)
    headers = {name.lower(): value for name, value in headers.items()}
    assert headers["authorization"] == f"Bearer {KEY}"
    assert headers["project"] == PROJECT
    assert headers["accept-encoding"] == "identity"
    assert headers["host"] == f"127.0.0.1:{peer.port}"


def test_headers_delayed_past_the_deadline_end_at_the_deadline(loopback, monkeypatch):
    monkeypatch.setattr(funding, "DEADLINE_SECONDS", 1.0)
    peer = loopback([3.0, response(encode(page([gpu()])))])

    observation, elapsed = timed(peer.origin)

    assert_unknown(observation, "deadline_exceeded")
    assert elapsed <= 1.0 + GRACE


def test_headers_just_inside_the_deadline_are_accepted(loopback, monkeypatch):
    monkeypatch.setattr(funding, "DEADLINE_SECONDS", 1.5)
    peer = loopback([0.9, response(encode(page([gpu(7)])))])

    observation, _ = timed(peer.origin)

    assert observation.complete is True and observation.gpu.balance == 7


def test_real_socket_deadline_spans_pages(loopback, monkeypatch):
    monkeypatch.setattr(funding, "DEADLINE_SECONDS", 2.0)
    peer = loopback(
        [1.2, response(encode(page([], "cursor-1")))],
        [1.2, response(encode(page([gpu()])))],
    )

    observation, elapsed = timed(peer.origin)

    assert_unknown(observation, "deadline_exceeded")
    assert len(peer.heads) == 2 and elapsed <= 2.0 + GRACE


def test_close_delimited_body_left_open_ends_at_the_deadline(loopback, monkeypatch):
    monkeypatch.setattr(funding, "DEADLINE_SECONDS", 1.0)
    peer = loopback([response(encode(page([gpu()])), length=False), 5.0, "close"])

    observation, elapsed = timed(peer.origin)

    # The complete JSON already arrived; without EOF it is never accepted.
    assert_unknown(observation, "deadline_exceeded")
    assert elapsed <= 1.0 + GRACE


def test_body_drip_never_yields_a_result_after_the_deadline(loopback, monkeypatch):
    monkeypatch.setattr(funding, "DEADLINE_SECONDS", BODY_DRIP_DEADLINE)

    observation, elapsed = timed(body_drip_peer(loopback).origin)

    assert_unknown(observation, "deadline_exceeded")
    # Present bound: the deadline plus at most one per-read timeout (the page's remaining time).
    assert elapsed <= 2 * BODY_DRIP_DEADLINE + GRACE


def test_body_drip_below_the_read_timeout_is_cut_at_the_overall_deadline(loopback, monkeypatch):
    monkeypatch.setattr(funding, "DEADLINE_SECONDS", BODY_DRIP_DEADLINE)

    observation, elapsed = timed(body_drip_peer(loopback).origin)

    assert observation.reason == "deadline_exceeded"
    assert elapsed <= BODY_DRIP_DEADLINE + GRACE, f"wall {elapsed:.2f}s for a 2.0s bound"


def test_header_drip_never_yields_a_result_after_the_deadline(loopback, monkeypatch):
    monkeypatch.setattr(funding, "DEADLINE_SECONDS", 1.0)

    observation, _ = timed(header_drip_peer(loopback, count=4, interval=0.4).origin)

    assert_unknown(observation, "deadline_exceeded")


def test_header_drip_is_cut_at_the_overall_deadline(loopback, monkeypatch):
    monkeypatch.setattr(funding, "DEADLINE_SECONDS", 1.0)

    observation, elapsed = timed(header_drip_peer(loopback, count=10, interval=0.3).origin)

    assert observation.reason == "deadline_exceeded"
    assert elapsed <= 1.0 + GRACE, f"wall {elapsed:.2f}s for a 1.0s bound"


def test_slow_name_resolution_never_yields_a_result_after_the_deadline(
    loopback, guarded_network, monkeypatch
):
    monkeypatch.setattr(funding, "DEADLINE_SECONDS", 1.0)
    peer = loopback([response(encode(page([gpu()])))])
    guarded_network["funding.example.test"] = 1.3

    observation, _ = timed(f"https://funding.example.test:{peer.port}")

    assert_unknown(observation, "deadline_exceeded")
    assert drain_workers(5) == []
    assert wait_for(lambda: peer.connections == 1)
    # The lookup ended inside the join grace and TCP connected after the deadline: that
    # connection was shut down at once, so not even the request reached the peer.
    assert peer.heads == []


def test_slow_name_resolution_is_cut_at_the_overall_deadline(
    loopback, guarded_network, monkeypatch
):
    monkeypatch.setattr(funding, "DEADLINE_SECONDS", 1.0)
    peer = loopback([response(encode(page([gpu()])))])
    guarded_network["funding.example.test"] = 2.5

    observation, elapsed = timed(f"https://funding.example.test:{peer.port}")

    assert observation.reason == "deadline_exceeded"
    assert elapsed <= 1.0 + GRACE, f"wall {elapsed:.2f}s for a 1.0s bound"


# 3. Transport encoding, framing and redirects over a real socket.


@pytest.mark.parametrize(
    "encoding", ["gzip", "GZip", "x-gzip", "deflate", "br", "identity, gzip", "Identity,GZIP"]
)
def test_real_socket_compression_is_refused_despite_the_identity_request(loopback, encoding):
    body = gzip.compress(encode(page([gpu()])))
    peer = loopback([response(body, headers=[("Content-Encoding", encoding)])])

    observation, _ = timed(peer.origin)

    assert_unknown(observation, "compressed_response")
    assert "\r\naccept-encoding: identity" in peer.heads[0].lower()


@pytest.mark.parametrize("encoding", ["", "Identity", "IDENTITY", "identity, identity"])
def test_real_socket_identity_spellings_and_empty_encoding_are_accepted(loopback, encoding):
    peer = loopback([response(encode(page([gpu(3)])), headers=[("Content-Encoding", encoding)])])

    observation, _ = timed(peer.origin)

    assert observation.complete is True and observation.gpu.balance == 3


def test_real_socket_unlabelled_gzip_body_is_malformed_json(loopback):
    peer = loopback([response(gzip.compress(encode(page([gpu()]))))])

    assert_unknown(timed(peer.origin)[0], "malformed_json")


def test_real_socket_duplicate_equal_content_length_is_collapsed_by_h11(loopback):
    data = encode(page([gpu(5)]))
    peer = loopback([response(data, headers=[("Content-Length", str(len(data)))])])

    observation, _ = timed(peer.origin)

    assert observation.complete is True and observation.gpu.balance == 5


@pytest.mark.parametrize("values", [("{n}", "{m}"), ("+{n}",), ("-1",), ("1e3",), ("{n}, {m}",)])
def test_real_socket_conflicting_or_invalid_content_length_fails_closed(loopback, values):
    data = encode(page([gpu(5)]))
    fields = [("Content-Length", v.format(n=len(data), m=len(data) + 1)) for v in values]
    peer = loopback([response(data, headers=fields, length=False), "close"])

    assert_unknown(timed(peer.origin)[0], "connection_failed")


def test_real_socket_truncated_body_with_a_valid_json_prefix_is_not_accepted(loopback):
    data = encode(page([gpu(600)]))
    head = response(data + b" " * 16)[: -(len(data) + 16)]
    peer = loopback([head + data, "close"])

    assert_unknown(timed(peer.origin)[0], "connection_failed")


def test_real_socket_chunked_body_is_accepted(loopback):
    data = encode(page([gpu(9)]))
    head = response(b"", headers=[("Transfer-Encoding", "chunked")], length=False)
    peer = loopback([head + chunked(*split(data, 7))])

    observation, _ = timed(peer.origin)

    assert observation.complete is True and observation.gpu.balance == 9


@pytest.mark.parametrize("ending", ["close", "reset"])
def test_real_socket_chunked_body_failing_midway_fails_closed(loopback, ending):
    data = encode(page([gpu(9)]))
    head = response(b"", headers=[("Transfer-Encoding", "chunked")], length=False)
    partial = chunked(data)[: len(data) // 2]
    peer = loopback([head + partial, 0.1, ending])

    assert_unknown(timed(peer.origin)[0], "connection_failed")


def test_real_socket_unsupported_transfer_encoding_fails_closed(loopback):
    data = gzip.compress(encode(page([gpu(9)])))
    head = response(b"", headers=[("Transfer-Encoding", "gzip, chunked")], length=False)
    peer = loopback([head + chunked(data), "close"])

    assert_unknown(timed(peer.origin)[0], "connection_failed")


@pytest.mark.parametrize(
    "status",
    [
        "301 Moved Permanently",
        "302 Found",
        "303 See Other",
        "307 Temporary Redirect",
        "308 Permanent Redirect",
    ],
)
def test_real_socket_redirects_are_refused_and_never_followed(loopback, status):
    location = [("Location", "/api/accounting/v2/browseWallets?includeChildren=true")]
    peer = loopback(
        [response(b"", status=status, headers=location)],
        [response(encode(page([gpu(1)])))],
    )

    assert_unknown(timed(peer.origin)[0], "redirect_refused")
    assert len(peer.heads) == 1


def test_real_socket_page_of_exactly_one_mib_is_accepted(loopback):
    peer = loopback([response(padded(page([gpu(3)]), MIB))])

    observation, _ = timed(peer.origin)

    assert observation.complete is True and observation.gpu.balance == 3


@pytest.mark.parametrize("framing", ["declared", "chunked", "close"])
def test_real_socket_page_one_byte_over_one_mib_is_refused(loopback, framing):
    data = padded(page([gpu(3)]), MIB + 1)
    if framing == "declared":
        script = [response(data)]
    elif framing == "chunked":
        head = response(b"", headers=[("Transfer-Encoding", "chunked")], length=False)
        script = [head + chunked(*split(data, 64 * 1024))]
    else:
        script = [response(data, length=False), "close"]
    peer = loopback(script)

    assert_unknown(timed(peer.origin)[0], "page_too_large")


# 3b. Proxy and CA environment reach the SDK's default client (trust_env=True).


def test_https_proxy_environment_reaches_the_sdk_client(peers, monkeypatch):
    proxy = peers([b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n"])
    monkeypatch.delenv("NO_PROXY")
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{proxy.port}")

    observation, _ = timed("https://funding.example.test:8443")

    assert_unknown(observation, "connection_failed")
    (head,) = proxy.heads
    assert head.startswith("CONNECT funding.example.test:8443 HTTP/1.1\r\n")
    # The tunnel request itself carries neither the key nor the project.
    assert KEY not in head and "bearer" not in head.lower() and PROJECT not in head


def test_no_proxy_entry_bypasses_the_environment_proxy(peers, monkeypatch):
    proxy = peers([b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n"])
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{proxy.port}")
    monkeypatch.setenv("NO_PROXY", "funding.example.test")

    observation, _ = timed("https://funding.example.test:8443")

    assert_unknown(observation, "connection_failed")  # Direct: the guarded resolver refuses.
    assert proxy.connections == 0


def test_http_proxy_alone_does_not_carry_https(peers, monkeypatch):
    proxy = peers([b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n"])
    monkeypatch.delenv("NO_PROXY")
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy.port}")

    assert_unknown(timed("https://funding.example.test:8443")[0], "connection_failed")
    assert proxy.connections == 0


def test_socks_all_proxy_environment_changes_every_observation(monkeypatch):
    monkeypatch.delenv("NO_PROXY")
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:9")
    socks = importlib.util.find_spec("socksio") is not None

    observation, _ = timed("https://funding.example.test:8443")

    # Without socksio the SDK cannot even build its client: every observation is unknown.
    assert_unknown(observation, "connection_failed" if socks else "internal_error")


@pytest.mark.parametrize("ca_file", [False, True])
def test_ssl_cert_file_environment_reaches_the_sdk_transport(peers, monkeypatch, tmp_path, ca_file):
    peer = peers(hangup=True)
    if ca_file:
        monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "absent-ca.pem"))

    observation, _ = timed(peer.origin)

    if ca_file:
        assert_unknown(observation, "internal_error")
        assert peer.connections == 0
    else:
        assert_unknown(observation, "connection_failed")
        assert peer.connections == 1


# 2. Boundaries (MockTransport).


def test_exactly_32_pages_and_3200_rows_complete(mock_sdk):
    replies = []
    for number in range(funding.MAX_PAGES):
        rows = [compact(f"s{number}-{row}") for row in range(funding.ITEMS_PER_PAGE)]
        last = number == funding.MAX_PAGES - 1
        if last:
            rows[-1] = gpu(9)
        replies.append(reply(page(rows, None if last else f"cursor-{number}")))
    service = mock_sdk(*replies)

    observation = run()

    assert observation.complete is True and observation.gpu.balance == 9
    assert len(service.requests) == funding.MAX_PAGES == 32
    assert all(body.closed for body in service.bodies) and service.closed


def test_one_row_beyond_3200_across_pages_is_unknown(mock_sdk):
    replies = [
        reply(page([compact(f"s{n}-{r}") for r in range(100)], f"cursor-{n}")) for n in range(31)
    ]
    replies.append(reply(page([compact(f"last-{r}") for r in range(101)])))
    mock_sdk(*replies)

    assert_unknown(run(), "too_many_rows")


@pytest.mark.parametrize("declared", [True, False])
def test_page_of_exactly_one_mib_is_accepted(mock_sdk, declared):
    data = padded(page([gpu(4)]), MIB)
    mock_sdk(reply(data, length=declared, chunks=split(data, 64 * 1024)))

    observation = run()

    assert observation.complete is True and observation.gpu.balance == 4


@pytest.mark.parametrize("declared", [True, False])
def test_page_one_byte_over_one_mib_is_refused(mock_sdk, declared):
    data = padded(page([gpu(4)]), MIB + 1)
    service = mock_sdk(reply(data, length=declared, chunks=split(data, 64 * 1024)))

    assert_unknown(run(), "page_too_large")
    assert service.bodies[0].closed and service.closed
    assert service.bodies[0].served == (0 if declared else 17)


def four_mib_pages(last_cursor):
    pages = []
    for number in range(4):
        final = number == 3
        cursor = last_cursor if final else f"cursor-{number}"
        pages.append(padded(page([gpu(8)] if final else [], cursor), MIB))
    return pages


@pytest.mark.parametrize("declared", [True, False])
def test_aggregate_of_exactly_four_mib_is_accepted(mock_sdk, declared):
    pages = four_mib_pages(None)
    service = mock_sdk(*(reply(d, length=declared, chunks=split(d, 256 * 1024)) for d in pages))

    observation = run()

    assert observation.complete is True and observation.gpu.balance == 8
    assert len(service.requests) == 4


@pytest.mark.parametrize("declared", [True, False])
def test_aggregate_one_byte_over_four_mib_is_refused(mock_sdk, declared):
    pages = four_mib_pages("cursor-3")
    replies = [reply(d, length=declared, chunks=split(d, 256 * 1024)) for d in pages]
    service = mock_sdk(*replies, reply(b" ", length=declared))

    assert_unknown(run(), "total_too_large")
    assert len(service.requests) == 5
    assert service.bodies[-1].served == (0 if declared else 1)
    assert all(body.closed for body in service.bodies) and service.closed


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (httpx.ReadError("reset"), "connection_failed"),
        (httpx.RemoteProtocolError("incomplete body"), "connection_failed"),
        (httpx.ReadTimeout("slow"), "deadline_exceeded"),
        (OSError(f"disk with {KEY}"), "internal_error"),
    ],
)
def test_body_failing_midway_discards_earlier_pages_and_closes(mock_sdk, failure, reason):
    data = encode(page([gpu(600)]))
    service = mock_sdk(
        reply(page([cpu(5)], "cursor-1")),
        reply(data, chunks=[data[:10]], fail=failure),
    )

    assert_unknown(run(), reason)
    assert [body.served for body in service.bodies] == [1, 1]
    assert all(body.closed for body in service.bodies) and service.closed


def test_local_parsing_that_crosses_the_deadline_is_refused(mock_sdk, monkeypatch):
    clock = Clock()
    decode = funding._decode

    def slow_decode(body):
        clock.advance(funding.DEADLINE_SECONDS)
        return decode(body)

    monkeypatch.setattr(funding, "_decode", slow_decode)
    mock_sdk(reply(page([gpu()])))

    assert_unknown(run(clock), "deadline_exceeded")


# 4. Identity.


@pytest.mark.parametrize(
    "project_id",
    [PROJECT.upper(), f" {PROJECT}", f"{PROJECT} ", f"{PROJECT} ", f"{PROJECT}​"],
)
def test_project_id_variants_are_foreign(mock_sdk, project_id):
    row = storage(owner={"type": "project", "projectId": project_id})
    mock_sdk(reply(page([gpu(600), cpu(5), row])))

    assert_unknown(run(), "foreign_owner")


def test_user_wallet_carrying_the_selected_project_id_is_foreign(mock_sdk):
    owner = {"type": "user", "username": "someone", "projectId": PROJECT}
    mock_sdk(reply(page([gpu(600), cpu(5, owner=owner)])))

    assert_unknown(run(), "foreign_owner")


@pytest.mark.parametrize(
    "owner",
    [
        {"type": "Project", "projectId": PROJECT},
        {"type": "PROJECT", "projectId": PROJECT},
        {"projectId": PROJECT},
        {"type": "project"},
        {"type": "project", "projectId": None},
        {"type": "project", "projectId": ""},
    ],
)
def test_owner_type_or_id_variants_are_malformed(mock_sdk, owner):
    mock_sdk(reply(page([gpu(600), storage(owner=owner)])))

    assert_unknown(run(), "malformed_wallet")


@pytest.mark.parametrize(
    ("name", "provider"),
    [
        ("GPU-NVIDIA-B200", "ucloud"),
        ("gpu-nvidia-b200 ", "ucloud"),
        (" gpu-nvidia-b200", "ucloud"),
        ("gpu‑nvidia-b200", "ucloud"),
        ("gpu-nvidia-b200", "UCloud"),
        ("gpu-nvidia-b200", "ucloud "),
    ],
)
def test_near_miss_target_identities_are_ignored_as_unrelated(mock_sdk, name, provider):
    """Documents exact identity: a near-miss is another category, so GPU reports absent."""
    mock_sdk(reply(page([wallet(name, provider=provider, balance=600), cpu(5)])))

    observation = run()

    assert observation.complete is True
    assert (observation.gpu.state, observation.cpu.balance) == ("absent", 5)


@pytest.mark.parametrize(
    "product_type", [MISSING, None, "", "compute", "Compute", "COMPUTE ", "STORAGE"]
)
def test_target_product_type_must_be_exactly_compute(mock_sdk, product_type):
    row = gpu(600)
    if product_type is MISSING:
        del row["paysFor"]["productType"]
    else:
        row["paysFor"]["productType"] = product_type
    mock_sdk(reply(page([row, cpu(5)])))

    assert_unknown(run(), "malformed_wallet")


def _owner_surrogate(row):
    row["owner"]["projectId"] = PROJECT + "\ud800"


def _name_surrogate(row):
    row["paysFor"]["name"] = funding.GPU_CATEGORY + "\ud800"


def _provider_surrogate(row):
    row["paysFor"]["provider"] = "ucloud\udfff"


def _unit_surrogate(row):
    row["paysFor"]["accountingUnit"] = unit("GPU\udc00", "GPU\udc00")


def _frequency_surrogate(row):
    row["paysFor"]["accountingFrequency"] = "PERIODIC_MINUTE\ud800"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (_owner_surrogate, "malformed_wallet"),
        (_name_surrogate, "malformed_wallet"),
        (_provider_surrogate, "malformed_wallet"),
        (_unit_surrogate, "unsupported_unit"),
        (_frequency_surrogate, "unsupported_unit"),
    ],
)
def test_lone_surrogates_in_identity_or_unit_are_unknown(mock_sdk, mutate, reason):
    row = gpu(600)
    mutate(row)
    mock_sdk(reply(page([row, cpu(5)])))

    assert_unknown(run(), reason)


def test_lone_surrogate_cursor_is_invalid(mock_sdk):
    service = mock_sdk(reply(page([gpu(1)], "cursor\ud800")))

    assert_unknown(run(), "cursor_invalid")
    assert len(service.requests) == 1


def test_lone_surrogate_in_an_ignored_field_never_reaches_the_result(mock_sdk):
    row = storage()
    row["note"] = "\ud800 ignored"
    mock_sdk(reply(page([row, gpu(2)])))

    observation = run()

    assert observation.complete is True and observation.gpu.balance == 2
    assert "\ud800" not in repr(observation.as_dict())


@pytest.mark.parametrize(
    "rows",
    [
        [gpu(300), gpu(300)],
        [gpu(0), gpu(5)],
        [cpu(1), storage(), cpu(1)],
        [gpu(5), gpu(5, accounting=unit("Core"), frequency="PERIODIC_HOUR")],
    ],
)
def test_duplicate_targets_on_one_page_are_unknown(mock_sdk, rows):
    mock_sdk(reply(page(rows)))

    assert_unknown(run(), "duplicate_target")


# 5. Numbers and JSON.


@pytest.mark.parametrize(
    ("raw", "state", "balance"),
    [
        (str(2**63 - 1), "positive_current", 2**63 - 1),
        ("1", "positive_current", 1),
        ("0", "no_usable_balance", 0),
        ("-0", "no_usable_balance", 0),
    ],
)
def test_integer_balance_spellings(mock_sdk, raw, state, balance):
    mock_sdk(reply(balance_page(raw)))

    observation = run()

    assert (observation.gpu.state, observation.gpu.balance) == (state, balance)
    assert type(observation.gpu.balance) is int


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (str(-(2**63)), "unsupported_balance"),
        (str(-(2**63) - 1), "malformed_wallet"),
        (str(2**63), "malformed_wallet"),
        ("1e2", "malformed_wallet"),
        ("100.0", "malformed_wallet"),
        ("-0.0", "malformed_wallet"),
        ("1E400", "malformed_wallet"),
        ("true", "malformed_wallet"),
        ('"600"', "malformed_wallet"),
        ("null", "malformed_wallet"),
        ("9" * 4301, "malformed_json"),
        ("NaN", "malformed_json"),
        ("Infinity", "malformed_json"),
        ("-Infinity", "malformed_json"),
    ],
    ids=lambda value: value if len(value) < 40 else f"{len(value)}-chars",
)
def test_non_integer_or_out_of_range_balances_are_unknown(mock_sdk, raw, reason):
    mock_sdk(reply(balance_page(raw)))

    assert_unknown(run(), reason)


def test_disabled_digit_limit_still_rejects_an_oversized_balance(mock_sdk):
    previous = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(0)
    try:
        mock_sdk(reply(balance_page("9" * 5000)))
        observation = run()
    finally:
        sys.set_int_max_str_digits(previous)

    assert_unknown(observation, "malformed_wallet")


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("9" * 4301, "malformed_json"),
        ("[" * 100_000 + "]" * 100_000, "malformed_json"),
        ('{"a": ' * 50_000 + "1" + "}" * 50_000, "malformed_json"),
        ("NaN", "malformed_json"),
        ("-Infinity", "malformed_json"),
        ('{"k": 1, "k": 1}', "malformed_json"),
        ("1E400", None),
        ("-0", None),
        ("[" * 500 + "]" * 500, None),
    ],
    ids=[
        "4301-digits",
        "array-depth-100000",
        "object-depth-50000",
        "nan",
        "minus-infinity",
        "duplicate-key",
        "float-overflow-accepted",
        "minus-zero-accepted",
        "array-depth-500-accepted",
    ],
)
def test_ignored_fields_still_parse_strictly(mock_sdk, raw, reason):
    body = raw_page(with_raw(storage(), "extra", raw), gpu(3))
    assert len(body) <= MIB
    mock_sdk(reply(body))

    observation = run()

    if reason is None:
        assert observation.complete is True and observation.gpu.balance == 3
    else:
        assert_unknown(observation, reason)


@pytest.mark.parametrize(
    ("original", "duplicated"),
    [
        (f'"projectId": "{PROJECT}"', f'"projectId": "{OTHER_PROJECT}", "projectId": "{PROJECT}"'),
        ('"name": "gpu-nvidia-b200"', '"name": "u1-cephfs", "name": "gpu-nvidia-b200"'),
        ('"namePlural": "GPU"', '"namePlural": "GPUs", "namePlural": "GPU"'),
        ('"maxUsable": 600', '"maxUsable": -1, "maxUsable": 600'),
        ('"next": null', '"next": "cursor-1", "next": null'),
    ],
    ids=["owner-project", "category-name", "unit-plural", "balance", "page-next"],
)
def test_duplicate_keys_at_any_depth_are_malformed_json(mock_sdk, original, duplicated):
    text = json.dumps(page([gpu(600)]))
    assert text.count(original) == 1
    mock_sdk(reply(text.replace(original, duplicated).encode()))

    assert_unknown(run(), "malformed_json")


VALID = json.dumps(page([storage(), gpu(3)]))


@pytest.mark.parametrize(
    "body",
    [
        b"\xef\xbb\xbf" + VALID.encode(),
        VALID.encode("utf-16"),
        VALID.encode("utf-16-le"),
        VALID.encode().replace(b"u1-cephfs", b"u1-\xc3\x28"),
        VALID.encode().replace(b"u1-cephfs", b"u1-\xc0\xaf"),
        VALID.encode().replace(b"u1-cephfs", b"u1-\xed\xa0\x80"),
        VALID.encode().replace(b"u1-cephfs", b"u1-\x00"),
        VALID.encode() + b" x",
        VALID.encode() + VALID.encode(),
        b"",
    ],
    ids=[
        "utf8-bom",
        "utf16-bom",
        "utf16-le",
        "invalid-utf8",
        "overlong-utf8",
        "encoded-surrogate",
        "raw-nul-in-string",
        "trailing-garbage",
        "two-documents",
        "empty",
    ],
)
def test_encoding_and_framing_errors_in_the_json_are_malformed(mock_sdk, body):
    mock_sdk(reply(body))

    assert_unknown(run(), "malformed_json")


# 6. Contract choices (pinned as current behaviour; views are in the review report).


def test_items_null_discards_an_earlier_positive_page(mock_sdk):
    mock_sdk(
        reply(page([gpu(600), cpu(5)], "cursor-1")),
        reply(b'{"items": null, "next": null, "itemsPerPage": 0}'),
    )

    assert_unknown(run(), "malformed_page")


def test_upstream_empty_project_page_is_complete_absence(mock_sdk):
    """Upstream WalletsBrowse normalizes Items with NonNilSlice, so no wallets is []."""
    mock_sdk(reply(page([])))

    observation = run()

    assert observation.complete is True
    assert (observation.gpu.state, observation.cpu.state) == ("absent", "absent")


def test_cross_category_units_are_reported_as_observed(mock_sdk):
    gpu_in_cores = gpu(5, accounting=unit("Core"), frequency="PERIODIC_HOUR")
    cpu_in_gpus = cpu(6, accounting=unit("GPU"), frequency="PERIODIC_MINUTE")
    mock_sdk(reply(page([gpu_in_cores, cpu_in_gpus])))

    observation = run()

    assert (observation.gpu.unit, observation.gpu.frequency) == ("Core", "PERIODIC_HOUR")
    assert (observation.cpu.unit, observation.cpu.frequency) == ("GPU", "PERIODIC_MINUTE")


def test_one_unsupported_target_unit_hides_the_valid_other_category(mock_sdk):
    # Upstream resource categories may legitimately be paid in GB (memory) units.
    mock_sdk(reply(page([gpu(5, accounting=unit("GB")), cpu(6)])))

    assert_unknown(run(), "unsupported_unit")


def test_naive_clock_raises_before_any_request(mock_sdk):
    service = mock_sdk(reply(page([gpu()])))

    with pytest.raises(ValueError):
        funding.observe(SERVER, PROJECT, KEY, now=lambda: datetime(2026, 11, 17))

    assert service.requests == []


def test_clock_turning_naive_after_the_traversal_raises_after_network_io(mock_sdk):
    moments = iter([NOW, datetime(2026, 11, 17)])
    service = mock_sdk(reply(page([gpu()])))

    with pytest.raises(ValueError):
        funding.observe(SERVER, PROJECT, KEY, monotonic=Clock(), now=lambda: next(moments))

    assert len(service.requests) == 1


def test_datetime_subclass_clock_is_refused(mock_sdk):
    class Moment(datetime):
        pass

    mock_sdk(reply(page([gpu()])))

    with pytest.raises(ValueError):
        funding.observe(SERVER, PROJECT, KEY, now=lambda: Moment(2026, 11, 17, tzinfo=UTC))


def test_offset_clock_is_normalised_to_utc(mock_sdk):
    copenhagen = timezone(timedelta(hours=1))
    mock_sdk(reply(page([gpu()])))

    observation = funding.observe(
        SERVER,
        PROJECT,
        KEY,
        monotonic=Clock(),
        now=lambda: datetime(2026, 11, 17, 10, 30, 0, 999999, tzinfo=copenhagen),
    )

    assert observation.started_at == observation.finished_at == "2026-11-17T09:30:00.999Z"


# 7. Coupling to the released SDK's private ``_client``.


def sdk_variant(monkeypatch, change):
    original = funding.UCloudClient

    class Variant(original):
        def __init__(self, settings, *, timeout=30.0):
            super().__init__(settings, timeout=timeout)
            self.real_http = self._client
            change(self)

        def close(self):
            self.real_http.close()

    monkeypatch.setattr(funding, "UCloudClient", Variant)


class DuckClient:
    follow_redirects = False

    def __init__(self):
        self.base_url = httpx.URL(SERVER + "/")
        self.calls = 0

    def stream(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("A duck-typed transport must never be used.")

    def close(self):
        pass


def test_sdk_that_renamed_its_private_client_is_unsupported(mock_sdk, monkeypatch):
    def rename(sdk):
        sdk._http = sdk.__dict__.pop("_client")

    sdk_variant(monkeypatch, rename)
    service = mock_sdk(reply(page([gpu(1)])))

    assert_unknown(run(), "transport_unsupported")
    assert service.requests == [] and service.closed


def test_duck_typed_sdk_client_is_unsupported(mock_sdk, monkeypatch):
    duck = DuckClient()

    def swap(sdk):
        sdk._client = duck

    sdk_variant(monkeypatch, swap)
    service = mock_sdk(reply(page([gpu(1)])))

    assert_unknown(run(), "transport_unsupported")
    assert duck.calls == 0 and service.requests == [] and service.closed


@pytest.mark.parametrize(
    "base_url",
    [
        "http://ucloud.example.test",
        "https://ucloud.example.test/api",
        "https://other.example.test",
        "https://ucloud.example.test:8443",
    ],
)
def test_sdk_client_with_a_different_base_url_is_unsupported(mock_sdk, base_url):
    service = mock_sdk(
        reply(page([gpu(1)])), adjust=lambda kwargs: kwargs.update(base_url=base_url)
    )

    assert_unknown(run(), "transport_unsupported")
    assert service.requests == [] and service.closed


def test_sdk_client_base_url_host_case_is_normalised_by_httpx(mock_sdk):
    service = mock_sdk(
        reply(page([gpu(1)])),
        adjust=lambda kwargs: kwargs.update(base_url="https://UCLOUD.Example.test"),
    )

    assert run().complete is True
    assert service.requests[0].url.host == "ucloud.example.test"


@pytest.mark.parametrize("value", [1, "yes"])
def test_sdk_client_with_truthy_redirect_following_is_unsupported(mock_sdk, value):
    service = mock_sdk(
        reply(page([gpu(1)])), adjust=lambda kwargs: kwargs.update(follow_redirects=value)
    )

    assert_unknown(run(), "transport_unsupported")
    assert service.requests == []


def test_sdk_constructor_signature_change_is_internal_error(mock_sdk, monkeypatch):
    original = funding.UCloudClient

    class Positional(original):
        def __init__(self, settings):
            super().__init__(settings)

    monkeypatch.setattr(funding, "UCloudClient", Positional)
    service = mock_sdk(reply(page([gpu(1)])))

    assert_unknown(run(), "internal_error")
    assert service.requests == []


def test_sdk_environment_and_dotenv_never_replace_the_explicit_context(
    mock_sdk, monkeypatch, tmp_path
):
    (tmp_path / ".env").write_text(
        "UCLOUD_SERVER=https://dotenv.example.test\n"
        "UCLOUD_TOKEN=dotenv-token\n"
        f"UCLOUD_PROJECT={OTHER_PROJECT}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UCLOUD_SERVER", "https://env.example.test")
    monkeypatch.setenv("UCLOUD_TOKEN", "environment-token")
    monkeypatch.setenv("UCLOUD_PROJECT", OTHER_PROJECT)
    service = mock_sdk(reply(page([gpu(1)])))

    assert run().complete is True
    (request,) = service.requests
    assert request.url.host == "ucloud.example.test"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert request.headers["Project"] == PROJECT


def _drop_project_header(kwargs):
    kwargs["headers"] = {k: v for k, v in kwargs["headers"].items() if k != "Project"}


def test_sdk_client_without_the_selected_project_header_is_refused(mock_sdk):
    service = mock_sdk(reply(page([])), adjust=_drop_project_header)

    observation = run()

    for request in service.requests:
        assert "Project" not in request.headers  # The simulated drift really happened.
    assert observation.complete is False


# 8. Recheck of the watchdog (921fd0d): cancellation, worker lifetime and late effects.

WORKER = "cbs-funding-observer"


def observer_threads():
    return [thread for thread in threading.enumerate() if thread.name == WORKER]


def drain_workers(timeout):
    """Wait for every observer worker to end; returns those still alive afterwards."""
    end = time.monotonic() + timeout
    for thread in observer_threads():
        thread.join(max(0.0, end - time.monotonic()))
    return observer_threads()


class LaggingClock:
    """Injected worker clock that never runs out, so per-read socket timeouts stay long.

    Only the watchdog (real monotonic time) can then end a blocked read: this isolates the
    wake-up path from httpx's own timeouts. ``budget`` is that per-operation timeout.
    """

    def __init__(self, budget=1000.0):
        self.calls, self.budget = 0, budget

    def __call__(self):
        self.calls += 1
        return self.budget if self.calls == 1 else 0.0


@pytest.fixture
def thread_errors(monkeypatch):
    """Unhandled exceptions in any thread (a late worker must never raise past _work)."""
    errors = []
    monkeypatch.setattr(threading, "excepthook", lambda args: errors.append(args.exc_type))
    yield errors
    assert drain_workers(10) == [], "an observer worker outlived the test"


def cancel_bound():
    return funding.DEADLINE_SECONDS + funding.JOIN_GRACE_SECONDS + GRACE


@pytest.fixture
def duplicates(monkeypatch):
    """Every descriptor the observer duplicates (socket.socket.dup), in order."""
    taken = []
    original = socket.socket.dup

    def dup(self):
        handle = original(self)
        taken.append(handle)
        return handle

    monkeypatch.setattr(socket.socket, "dup", dup)
    return taken


# Runs on every platform: shutdown wakes the read where closing does not (Linux, expected on
# macOS) and closing wakes it where shutdown does not (Windows).
def test_back_to_back_cancellations_leave_no_workers_or_open_sockets(
    loopback, monkeypatch, thread_errors, duplicates
):
    monkeypatch.setattr(funding, "DEADLINE_SECONDS", 0.3)
    rounds = 12
    assert drain_workers(5) == []
    peer = loopback(*([60.0] for _ in range(rounds)))  # Accept, read, never answer.

    for _ in range(rounds):
        started = time.monotonic()
        observation = funding.observe(
            peer.origin, PROJECT, KEY, monotonic=LaggingClock(), now=lambda: NOW
        )
        elapsed = time.monotonic() - started

        assert_unknown(observation, "deadline_exceeded")
        assert elapsed <= cancel_bound()
        # Only the watchdog's wake-up could end this read (its own timeout is ~1000 s).
        assert observer_threads() == []

    assert len(peer.heads) == rounds
    assert len(PlainBackend.sockets) == rounds
    assert all(client.fileno() == -1 for client in PlainBackend.sockets)
    assert len(duplicates) == rounds and all(handle.fileno() == -1 for handle in duplicates)
    assert thread_errors == []


@pytest.mark.parametrize("construction", [0.5, 1.2])
def test_cancel_during_sdk_client_construction_never_sends(
    mock_sdk, monkeypatch, thread_errors, construction
):
    monkeypatch.setattr(funding, "DEADLINE_SECONDS", 0.3)
    service = mock_sdk(reply(page([gpu(600)])), adjust=lambda kwargs: time.sleep(construction))

    observation, elapsed = timed(SERVER)

    assert_unknown(observation, "deadline_exceeded")
    assert elapsed <= cancel_bound()
    assert drain_workers(5) == []
    # The late client was refused at attach, closed by the SDK context and never used.
    assert service.requests == [] and service.closed
    assert thread_errors == []


def test_repeated_slow_lookups_pile_up_workers_until_each_lookup_returns(
    loopback, guarded_network, monkeypatch, thread_errors
):
    monkeypatch.setattr(funding, "DEADLINE_SECONDS", 0.1)
    calls = 4
    peer = loopback(*([response(encode(page([gpu()])))] for _ in range(calls)))
    guarded_network["funding.example.test"] = 3.0
    assert drain_workers(5) == []

    for _ in range(calls):
        observation, elapsed = timed(f"https://funding.example.test:{peer.port}")
        assert_unknown(observation, "deadline_exceeded")
        assert elapsed <= cancel_bound()

    # Documented: an OS name lookup cannot be interrupted, so each call leaves one worker.
    assert len(observer_threads()) == calls
    assert drain_workers(10) == []
    assert thread_errors == []


def test_cancelled_worker_never_sends_its_request_after_a_late_lookup(
    loopback, guarded_network, monkeypatch, thread_errors
):
    monkeypatch.setattr(funding, "DEADLINE_SECONDS", 0.2)
    peer = loopback([response(encode(page([gpu()])))])
    guarded_network["funding.example.test"] = 1.2

    observation, _ = timed(f"https://funding.example.test:{peer.port}")

    assert_unknown(observation, "deadline_exceeded")
    assert drain_workers(10) == []
    assert peer.heads == [], "the cancelled worker still sent the key after observe() returned"


class HeldBody(Body):
    """A body whose read cannot be woken by closing the client (the unwoken-read case)."""

    def __init__(self, data, release):
        super().__init__([data])
        self.release = release

    def __iter__(self):
        self.release.wait(10)
        yield from super().__iter__()


def test_late_worker_cannot_change_the_returned_observation_or_a_later_call(
    mock_sdk, monkeypatch, thread_errors
):
    monkeypatch.setattr(funding, "DEADLINE_SECONDS", 0.2)
    release = threading.Event()
    late = HeldBody(encode(page([gpu(600), cpu(50)])), release)
    held = httpx.Response(200, headers={"Content-Type": "application/json"}, stream=late)
    service = mock_sdk(held, reply(page([gpu(7)])))

    first, elapsed = timed(SERVER)
    snapshot = first.as_dict()

    assert_unknown(first, "deadline_exceeded")
    assert elapsed <= cancel_bound()
    assert len(observer_threads()) == 1  # Still blocked in a read the close did not wake.

    monkeypatch.setattr(funding, "DEADLINE_SECONDS", 5.0)
    second = funding.observe(SERVER, PROJECT, KEY, now=lambda: NOW)
    second_snapshot = second.as_dict()
    release.set()

    assert drain_workers(5) == []
    # The late worker read a complete positive page and still changed nothing.
    assert late.served == 1 and late.closed
    assert first.as_dict() == snapshot and second.as_dict() == second_snapshot
    assert (second.complete, second.gpu.balance, second.cpu.state) == (True, 7, "absent")
    assert len(service.requests) == 2
    assert thread_errors == []
    with pytest.raises(AttributeError):
        first.complete = True


# 9. Cross-platform wake-up: shutdown through the observer's own duplicate descriptor.
#
# Loopback probes on 19 November: closing a socket from another thread woke a blocked read or
# TLS handshake on Windows 11 but not on Linux (WSL2 6.18); shutdown(SHUT_RDWR), also through
# a duplicate taken before TLS wrapping, woke it on Linux but not on Windows. The assertions
# below that differ per platform encode exactly that; macOS is expected to match Linux.

WAKES_BY_SHUTDOWN = sys.platform != "win32"


class ConnectedStream:
    """Stands in for httpcore's network stream at ``connect_tcp.complete``."""

    def __init__(self, sock):
        self.sock = sock

    def get_extra_info(self, info):
        return self.sock if info == "socket" else None


@pytest.fixture
def connected():
    listener = socket.create_server(("127.0.0.1", 0))
    client = socket.create_connection(listener.getsockname())
    server, _ = listener.accept()
    listener.close()
    server.settimeout(5)
    yield client, server
    client.close()
    server.close()


@pytest.mark.parametrize("event", ["connection.connect_tcp.complete", "socks.connect_tcp.complete"])
def test_cancel_shuts_down_tracked_connections_before_closing_the_client(
    connected, duplicates, monkeypatch, event
):
    client, server = connected
    events, shut = [], funding._shut
    monkeypatch.setattr(funding, "_shut", lambda handle: (events.append("shutdown"), shut(handle)))
    deadline = funding._Deadline(time.monotonic, 10)
    deadline.attach(SimpleNamespace(close=lambda: events.append("close")))
    deadline.trace(event, {"return_value": ConnectedStream(client)})

    deadline.cancel()

    assert events == ["shutdown", "close"]
    assert server.recv(1) == b""  # The connection itself was shut down: the peer saw FIN.
    with pytest.raises(OSError):
        client.sendall(b"GET / HTTP/1.1\r\n\r\n")
    assert client.fileno() != -1  # httpcore's own socket is still httpcore's to close.
    assert [handle.fileno() for handle in duplicates] == [-1]


def test_connection_completing_after_cancellation_is_shut_down_at_once(connected, duplicates):
    client, server = connected
    deadline = funding._Deadline(time.monotonic, 10)
    deadline.cancel()

    deadline.trace("connection.connect_tcp.complete", {"return_value": ConnectedStream(client)})

    assert server.recv(1) == b""
    with pytest.raises(OSError):
        client.sendall(b"GET / HTTP/1.1\r\n\r\n")
    assert [handle.fileno() for handle in duplicates] == [-1]


def test_release_closes_duplicates_without_disturbing_the_connection(connected, duplicates):
    client, server = connected
    deadline = funding._Deadline(time.monotonic, 10)
    deadline.trace("connection.connect_tcp.complete", {"return_value": ConnectedStream(client)})

    deadline.release()
    deadline.cancel()  # Nothing is left to shut down after the worker released.

    assert [handle.fileno() for handle in duplicates] == [-1]
    client.sendall(b"still open")
    assert server.recv(16) == b"still open"


@pytest.mark.parametrize(
    ("event", "stream"),
    [
        ("connection.connect_tcp.complete", None),
        ("connection.connect_tcp.complete", object()),
        ("connection.connect_tcp.complete", SimpleNamespace(get_extra_info=lambda info: None)),
        ("connection.start_tls.complete", "connected"),
        ("connection.connect_tcp.started", "connected"),
    ],
)
def test_only_connected_plain_sockets_are_tracked(connected, duplicates, event, stream):
    client, server = connected
    if stream == "connected":
        stream = ConnectedStream(client)
    deadline = funding._Deadline(time.monotonic, 10)

    deadline.trace(event, {"return_value": stream})
    deadline.cancel()

    assert duplicates == []
    client.sendall(b"untouched")
    assert server.recv(16) == b"untouched"


@pytest.mark.parametrize("failure", [OSError(24, "Too many open files"), NotImplementedError()])
def test_a_socket_that_cannot_be_duplicated_leaves_only_the_client_close(
    connected, monkeypatch, failure
):
    client, server = connected

    def refuse(self):
        raise failure

    monkeypatch.setattr(socket.socket, "dup", refuse)
    deadline = funding._Deadline(time.monotonic, 10)

    deadline.trace("connection.connect_tcp.complete", {"return_value": ConnectedStream(client)})
    deadline.cancel()

    client.sendall(b"untouched")
    assert server.recv(16) == b"untouched"


def test_shutdown_reaches_the_connection_even_where_closing_cannot_wake_the_read(
    loopback, monkeypatch, thread_errors, duplicates
):
    """Closing the client is disabled, as it is ineffective on Linux: shutdown must suffice."""
    monkeypatch.setattr(funding, "DEADLINE_SECONDS", 0.3)
    monkeypatch.setattr(PlainStream, "close", lambda self: None)
    peer = loopback(["eof"])  # Reads the request, never answers, records the client's FIN.

    try:
        started = time.monotonic()
        observation = funding.observe(
            peer.origin, PROJECT, KEY, monotonic=LaggingClock(), now=lambda: NOW
        )
        elapsed = time.monotonic() - started

        assert_unknown(observation, "deadline_exceeded")
        assert elapsed <= cancel_bound()
        assert len(peer.heads) == 1
        # The shutdown reached the real connection although no socket was closed.
        assert wait_for(lambda: len(peer.eofs) == 1, timeout=2.0)
        assert all(client.fileno() != -1 for client in PlainBackend.sockets)
        assert len(duplicates) == 1 and duplicates[0].fileno() == -1
        if WAKES_BY_SHUTDOWN:
            assert observer_threads() == []  # Woken by shutdown alone (read timeout ~1000 s).
        else:
            assert len(observer_threads()) == 1  # Windows needs the close as well.
    finally:
        for client in PlainBackend.sockets:
            client.close()  # On Windows this is the wake-up; elsewhere it only releases.
    assert thread_errors == []


def tls_client(**kwargs):
    """The SDK's arguments over a real TLS client handshake; no CA, as none ever completes."""
    return httpx.Client(
        transport=httpx.HTTPTransport(verify=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)), **kwargs
    )


def test_a_stalled_tls_handshake_is_shut_down_at_the_deadline(
    monkeypatch, thread_errors, duplicates
):
    monkeypatch.setattr(funding, "DEADLINE_SECONDS", 0.3)
    monkeypatch.setattr(sdk_client, "httpx", SimpleNamespace(Client=tls_client))
    # A silent peer: the kernel completes TCP from the backlog; nothing ever answers TLS.
    listener = socket.create_server(("127.0.0.1", 0))
    handshake_timeout = 3.0  # The worker's connect timeout, so a Windows worker ends anyway.

    try:
        started = time.monotonic()
        observation = funding.observe(
            f"https://127.0.0.1:{listener.getsockname()[1]}",
            PROJECT,
            KEY,
            monotonic=LaggingClock(handshake_timeout),
            now=lambda: NOW,
        )
        elapsed = time.monotonic() - started

        assert_unknown(observation, "deadline_exceeded")
        assert elapsed <= cancel_bound()
        assert len(duplicates) == 1 and duplicates[0].fileno() == -1  # TCP had connected.
        if WAKES_BY_SHUTDOWN:
            # httpcore had not returned the TLS socket yet, so only the duplicate reached it.
            assert observer_threads() == []
        else:
            # Documented Windows limit: closing the client cannot reach a handshake socket.
            assert len(observer_threads()) == 1
            assert drain_workers(handshake_timeout + 2) == []
    finally:
        listener.close()
    assert thread_errors == []
