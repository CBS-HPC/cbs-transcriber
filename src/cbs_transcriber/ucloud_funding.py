"""Read-only, bounded observation of one UCloud project's compute funding (ADR 0178, T146).

Standalone and unwired: no route, setting, job, routing decision or database uses it. One
call makes complete GET-only ``browseWallets`` reads for an explicitly supplied server,
project and API key through the released SDK's ``CloudSettings``/``UCloudClient``. It
reports what that traversal observed. It never estimates affordability, reserves funding or
selects compute. Unknown is never zero: any failed, partial, foreign, duplicate or
unsupported evidence makes the whole observation unknown.

The server, project and key come only from the caller: no ``.env`` file, ``UCLOUD_*``
variable or credential store is read. Like the app's other SDK clients, the SDK's HTTPX
client keeps httpx's default ``trust_env``, so HTTPS_PROXY/ALL_PROXY/NO_PROXY and
SSL_CERT_FILE/SSL_CERT_DIR in the process environment still apply to this connection.

Network reads run on a daemon worker thread. At the overall deadline the caller shuts down
every connected socket of this call through its own duplicate descriptor, then closes the
SDK client (and so its pooled sockets), waits at most ``JOIN_GRACE_SECONDS`` for the worker
and returns unknown/deadline_exceeded; a result the worker completes after that moment is
never accepted. Both wake-ups are needed: in loopback probes ``shutdown(SHUT_RDWR)`` woke a
blocked read or TLS handshake on Linux but not Windows, and closing from another thread woke
it on Windows but not Linux (macOS is expected to match Linux; unverified). The duplicate is
taken as soon as TCP connects, so shutdown also reaches a handshake whose TLS socket httpcore
has not returned yet; closing the client cannot, so on Windows a stalled handshake ends only
at its connect timeout. A connection completing after the deadline is shut down at once. An
operating-system name lookup or TCP connect cannot be interrupted: such a worker finishes on
its own, bounded by the resolver or its connect timeout, but once observe() has returned an
httpcore ``trace`` hook refuses to write its request headers, so the late connection is
closed without sending the key or project.

Interpreted units follow upstream UCloud commit 2c23d295318cabdb960a8baa69dc175e806254ac
(provider-integration/shared/pkg/accounting and k8s/shared/products.go): resource-paid
compute categories use ``Core``/``Core`` or ``GPU``/``GPU`` with ``floatingPoint=false``,
``displayFrequencySuffix=true`` and a PERIODIC_SECOND/MINUTE/HOUR/DAY frequency. ONCE,
currency (floating-point) and any other representation stay unsupported.
"""

import json
import re
import socket
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import httpx
from ucloud_workflow import Settings as CloudSettings
from ucloud_workflow import UCloudClient

SCHEMA = "ucloud-funding-observation-v1"
PATH = "/api/accounting/v2/browseWallets"
PROVIDER = "ucloud"
GPU_CATEGORY = "gpu-nvidia-b200"
CPU_CATEGORY = "cpu-amd-zen5"
_TARGETS = {(GPU_CATEGORY, PROVIDER): "gpu", (CPU_CATEGORY, PROVIDER): "cpu"}

# Local application limits (ADR 0178), not provider guarantees.
DEADLINE_SECONDS = 15.0
JOIN_GRACE_SECONDS = 0.5  # After the deadline, time allowed for the worker to release sockets.
MAX_PAGE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_PAGES = 32
MAX_ROWS = 3200
ITEMS_PER_PAGE = 100
MAX_CURSOR_CHARS = 1024

STATES = ("positive_current", "no_usable_balance", "absent", "unknown")
UNITS = ("GPU", "Core")
FREQUENCIES = ("PERIODIC_SECOND", "PERIODIC_MINUTE", "PERIODIC_HOUR", "PERIODIC_DAY")
_UNIT_KEYS = frozenset({"name", "namePlural", "floatingPoint", "displayFrequencySuffix"})
_INT64 = 2**63
REASONS = frozenset(
    {
        "transport_unsupported",
        "deadline_exceeded",
        "connection_failed",
        "redirect_refused",
        "unauthorized",
        "forbidden",
        "not_found",
        "rate_limited",
        "server_error",
        "http_status",
        "compressed_response",
        "unexpected_content_type",
        "page_too_large",
        "total_too_large",
        "too_many_pages",
        "too_many_rows",
        "cursor_invalid",
        "cursor_repeated",
        "malformed_json",
        "malformed_page",
        "malformed_wallet",
        "foreign_owner",
        "duplicate_target",
        "malformed_unit",
        "unsupported_unit",
        "unsupported_balance",
        "internal_error",
    }
)

_HOST = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
_SERVER = re.compile(rf"https://({_HOST})(?::([1-9][0-9]{{0,4}}))?\Z")
_PROJECT = re.compile(r"[!-~]{1,200}\Z")
_KEY = re.compile(r"[!-~]{1,4096}\Z")


class _Unknown(Exception):
    """Internal refusal carrying only a curated reason, never upstream text."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class _Deadline:
    """Overall budget shared by observe()'s watchdog and its worker thread."""

    def __init__(self, monotonic, seconds):
        self._monotonic = monotonic
        self._end = monotonic() + seconds
        self._lock = threading.Lock()
        self._cancelled = False
        self._abandoned = False
        self._client = None
        self._handles = []  # Our duplicate descriptors of this call's connected sockets.

    def remaining(self):
        left = self._end - self._monotonic()
        if self._cancelled or left <= 0:
            raise _Unknown("deadline_exceeded")
        return left

    def check(self):
        self.remaining()

    def attach(self, http):
        """Register the SDK client before its first request, unless already cancelled."""
        with self._lock:
            self.check()
            self._client = http

    def cancel(self):
        """Watchdog side: shut down every connected socket, then close the client.

        shutdown() wakes a blocked read or TLS handshake where a close from another thread
        does not (Linux); closing the client's own pooled sockets wakes it where shutdown
        does not (Windows). The worker's outcome is discarded either way.
        """
        with self._lock:
            self._cancelled = True
            http, self._client = self._client, None
            handles, self._handles = self._handles, []
        for handle in handles:
            _shut(handle)
        if http is not None:
            try:
                http.close()
            except Exception:  # The worker's own outcome is discarded after cancellation.
                pass

    def release(self):
        """Worker side, after its client has closed: drop the remaining duplicates."""
        with self._lock:
            handles, self._handles = self._handles, []
        for handle in handles:
            try:
                handle.close()
            except OSError:
                pass

    def abandon(self):
        """observe() is returning without this worker; it must never send a request again."""
        self._abandoned = True

    def trace(self, event, info):
        """httpcore trace hook: track connected sockets; refuse headers set up too late.

        Closing the client cannot reach a connection still resolving or connecting, so a
        late connection reaches this check before any request byte is written.
        """
        if event.endswith(".connect_tcp.complete"):
            self._track(info.get("return_value"))
        elif self._abandoned and event.endswith(".send_request_headers.started"):
            raise _Unknown("deadline_exceeded")

    def _track(self, stream):
        """Keep our own duplicate of a newly connected socket for cancel() to shut down.

        A duplicate descriptor cannot be closed or reused by httpcore, and it still refers
        to the connection after a TLS wrapper detaches the plain socket object. Without one
        (an unexpected stream or no descriptor left) only closing the client remains.
        """
        getter = getattr(stream, "get_extra_info", None)
        sock = getter("socket") if callable(getter) else None
        if not isinstance(sock, socket.socket):
            return
        try:
            handle = sock.dup()
        except (OSError, NotImplementedError):  # No descriptor left, or an SSLSocket.
            return
        with self._lock:
            late = self._cancelled
            if not late:
                self._handles.append(handle)
        if late:  # Connected after the deadline: never usable for a request.
            _shut(handle)


def _shut(handle):
    """Shut the connection down for both directions, then close our duplicate descriptor."""
    try:
        handle.shutdown(socket.SHUT_RDWR)
    except OSError:  # Already disconnected or reset by the peer.
        pass
    try:
        handle.close()
    except OSError:
        pass


def _canonical_server(value):
    """Exact lowercase https origin; the default port, a slash or a path is not canonical."""
    match = _SERVER.fullmatch(value) if type(value) is str else None
    port = match and match.group(2)
    return bool(match and len(match.group(1)) <= 253 and (not port or 443 != int(port) <= 65535))


def _clean(value, maximum):
    return (
        type(value) is str
        and 0 < len(value) <= maximum
        and not any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)
    )


def _valid_balance(state, balance):
    if state == "positive_current":
        return type(balance) is int and 0 < balance < _INT64
    return type(balance) is int and balance == 0


@dataclass(frozen=True, slots=True)
class CategoryFunding:
    category: str
    provider: str
    state: str
    balance: int | None = None
    unit: str | None = None
    frequency: str | None = None

    def __post_init__(self):
        observed = self.state in {"positive_current", "no_usable_balance"}
        if (
            self.category not in (GPU_CATEGORY, CPU_CATEGORY)
            or self.provider != PROVIDER
            or self.state not in STATES
            or (
                observed
                and not (
                    _valid_balance(self.state, self.balance)
                    and self.unit in UNITS
                    and self.frequency in FREQUENCIES
                )
            )
            or (not observed and (self.balance, self.unit, self.frequency) != (None, None, None))
        ):
            raise ValueError("Invalid funding category observation.")


@dataclass(frozen=True, slots=True)
class FundingObservation:
    """Immutable bounded scalars. Positive is current eligibility, never whole-run funding."""

    server: str
    project: str
    started_at: str
    finished_at: str
    complete: bool
    gpu: CategoryFunding
    cpu: CategoryFunding
    reason: str | None = None
    schema: str = SCHEMA
    affordability: str = "not_checked"
    reservation: bool = False

    def __post_init__(self):
        if type(self.gpu) is not CategoryFunding or type(self.cpu) is not CategoryFunding:
            raise ValueError("Invalid funding observation.")
        states = (self.gpu.state, self.cpu.state)
        if (
            self.schema != SCHEMA
            or self.affordability != "not_checked"
            or self.reservation is not False
            or not _canonical_server(self.server)
            or type(self.project) is not str
            or not _PROJECT.fullmatch(self.project)
            or type(self.started_at) is not str
            or type(self.finished_at) is not str
            or type(self.complete) is not bool
            or self.gpu.category != GPU_CATEGORY
            or self.cpu.category != CPU_CATEGORY
            or (self.complete and (self.reason is not None or "unknown" in states))
            or (
                not self.complete
                and (
                    type(self.reason) is not str
                    or self.reason not in REASONS
                    or states != ("unknown", "unknown")
                )
            )
        ):
            raise ValueError("Invalid funding observation.")

    def as_dict(self):
        return asdict(self)


def _timestamp(now):
    moment = now()
    if type(moment) is not datetime or moment.utcoffset() is None:
        raise ValueError("The observation clock must return an aware datetime.")
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _status_reason(status):
    if 300 <= status < 400:
        return "redirect_refused"
    reason = {401: "unauthorized", 403: "forbidden", 404: "not_found", 429: "rate_limited"}
    return reason.get(status, "server_error" if 500 <= status < 600 else "http_status")


def _read_page(http, params, deadline, used):
    """One streamed GET page; the worker checks every bound through EOF and closure.

    Per-operation HTTPX timeouts restart on each socket read, so they cannot bound a drip
    or a slow name lookup; the caller's watchdog enforces the overall deadline instead.
    """
    chunks, size = [], 0
    with http.stream(
        "GET",
        PATH,
        params=params,
        headers={"Accept-Encoding": "identity"},
        timeout=httpx.Timeout(deadline.remaining()),
        follow_redirects=False,
        extensions={"trace": deadline.trace},
    ) as response:
        deadline.check()
        if response.status_code != 200:
            raise _Unknown(_status_reason(response.status_code))
        encodings = response.headers.get_list("content-encoding", split_commas=True)
        if any(value.strip().lower() not in {"", "identity"} for value in encodings):
            raise _Unknown("compressed_response")
        media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media != "application/json":
            raise _Unknown("unexpected_content_type")
        declared = response.headers.get("content-length")
        if declared is not None:
            if not (declared.isascii() and declared.isdigit()):
                raise _Unknown("malformed_page")
            if int(declared) > MAX_PAGE_BYTES:
                raise _Unknown("page_too_large")
            if used + int(declared) > MAX_TOTAL_BYTES:
                raise _Unknown("total_too_large")
        # No chunk_size: httpx would buffer small reads and delay these per-read checks.
        for chunk in response.iter_raw():
            size += len(chunk)
            if size > MAX_PAGE_BYTES:
                raise _Unknown("page_too_large")
            if used + size > MAX_TOTAL_BYTES:
                raise _Unknown("total_too_large")
            deadline.check()
            chunks.append(chunk)
        deadline.check()
    deadline.check()
    return b"".join(chunks)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _refuse_constant(_):
    raise ValueError("non-finite number")


def _decode(body):
    try:
        return json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_refuse_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise _Unknown("malformed_json") from None


def _page_parts(page):
    # Upstream PageV2 always serializes ``next`` (an absent Option is null) and ``items``.
    if type(page) is not dict or type(page.get("items")) is not list or "next" not in page:
        raise _Unknown("malformed_page")
    cursor = page["next"]
    if cursor is not None and not _clean(cursor, MAX_CURSOR_CHARS):
        raise _Unknown("cursor_invalid")
    return page["items"], cursor


def _unit(category):
    unit = category.get("accountingUnit")
    if (
        type(unit) is not dict
        or set(unit) != _UNIT_KEYS
        or type(unit["name"]) is not str
        or type(unit["namePlural"]) is not str
        or type(unit["floatingPoint"]) is not bool
        or type(unit["displayFrequencySuffix"]) is not bool
        or type(category.get("accountingFrequency")) is not str
    ):
        raise _Unknown("malformed_unit")
    frequency = category["accountingFrequency"]
    if (
        unit["name"] not in UNITS
        or unit["namePlural"] != unit["name"]
        or unit["floatingPoint"] is not False
        or unit["displayFrequencySuffix"] is not True
        or frequency not in FREQUENCIES
    ):
        raise _Unknown("unsupported_unit")
    return unit["name"], frequency


def _classify(item, project, found):
    """Identity first; valid unrelated selected-project wallets are then ignored."""
    owner = item.get("owner") if type(item) is dict else None
    if type(owner) is not dict:
        raise _Unknown("malformed_wallet")
    if owner.get("type") == "user":
        raise _Unknown("foreign_owner")
    if owner.get("type") != "project" or not _clean(owner.get("projectId"), 256):
        raise _Unknown("malformed_wallet")
    if owner["projectId"] != project:
        raise _Unknown("foreign_owner")
    category = item.get("paysFor")
    if type(category) is not dict or not all(
        _clean(category.get(field), 256) for field in ("name", "provider", "productType")
    ):
        raise _Unknown("malformed_wallet")
    target = _TARGETS.get((category["name"], category["provider"]))
    if target is None:
        return
    if category["productType"] != "COMPUTE":
        raise _Unknown("malformed_wallet")
    if target in found:
        raise _Unknown("duplicate_target")
    unit, frequency = _unit(category)
    balance = item.get("maxUsable")
    if type(balance) is not int or not -_INT64 <= balance < _INT64:
        raise _Unknown("malformed_wallet")
    if balance < 0:
        raise _Unknown("unsupported_balance")
    found[target] = (balance, unit, frequency)


def _traverse(server, project, api_key, deadline):
    # Construct explicitly: from_env() would read .env files and UCLOUD_* variables. The
    # SDK's HTTPX client still honours proxy/CA environment variables (trust_env).
    settings = CloudSettings(server=server, token=api_key, project=project)
    with UCloudClient(settings, timeout=DEADLINE_SECONDS) as sdk:
        http = getattr(sdk, "_client", None)  # The pinned SDK's own HTTPX transport.
        if (
            not isinstance(http, httpx.Client)
            or http.follow_redirects is not False
            or str(http.base_url).rstrip("/") != server
            # The exact selected Project/auth context, never a personal-workspace default.
            or http.headers.get_list("project") != [project]
            or http.headers.get_list("authorization") != [f"Bearer {api_key}"]
        ):
            raise _Unknown("transport_unsupported")
        deadline.attach(http)
        found, seen, cursor, used, rows = {}, set(), None, 0, 0
        for _ in range(MAX_PAGES):
            params = {"includeChildren": "false", "itemsPerPage": ITEMS_PER_PAGE}
            if cursor is not None:
                params["next"] = cursor
            body = _read_page(http, params, deadline, used)
            used += len(body)
            items, cursor = _page_parts(_decode(body))
            rows += len(items)
            if rows > MAX_ROWS:
                raise _Unknown("too_many_rows")
            for item in items:
                _classify(item, project, found)
            if cursor is None:
                break
            if cursor in seen:
                raise _Unknown("cursor_repeated")
            seen.add(cursor)
        else:
            raise _Unknown("too_many_pages")
    deadline.check()
    return found


def _work(server, project, api_key, deadline, outcome):
    """Worker thread: records only a found mapping or a curated reason, never text."""
    try:
        outcome["found"] = _traverse(server, project, api_key, deadline)
    except _Unknown as unknown:
        outcome["reason"] = unknown.reason
    except httpx.TimeoutException:
        outcome["reason"] = "deadline_exceeded"
    except httpx.HTTPError:
        outcome["reason"] = "connection_failed"
    except Exception:  # Unknown evidence never propagates upstream text or secrets.
        outcome["reason"] = "internal_error"
    finally:
        deadline.release()  # The SDK client (and its sockets) closed inside _traverse.


def _category(name, entry):
    if entry is None:
        return CategoryFunding(name, PROVIDER, "absent")
    balance, unit, frequency = entry
    state = "positive_current" if balance > 0 else "no_usable_balance"
    return CategoryFunding(name, PROVIDER, state, balance, unit, frequency)


def observe(server, project, api_key, *, monotonic=time.monotonic, now=None):
    """Observe the selected project's GPU/CPU wallets once; never raises on remote evidence.

    Returns within DEADLINE_SECONDS + JOIN_GRACE_SECONDS of wall time. Invalid caller-supplied
    arguments (or an injected naive clock) raise ValueError without echoing their values.
    ``monotonic`` and ``now`` exist for deterministic tests; the watchdog always uses real
    monotonic time.
    """
    if not _canonical_server(server):
        raise ValueError("Use the canonical HTTPS UCloud server origin.")
    if type(project) is not str or not _PROJECT.fullmatch(project):
        raise ValueError("Use the exact selected UCloud project ID.")
    if type(api_key) is not str or not _KEY.fullmatch(api_key):
        raise ValueError("Use a single-line UCloud API key.")
    now = now or (lambda: datetime.now(UTC))
    started = _timestamp(now)
    seconds = DEADLINE_SECONDS
    deadline = _Deadline(monotonic, seconds)
    watchdog_end = time.monotonic() + seconds
    outcome = {}
    worker = threading.Thread(
        target=_work,
        args=(server, project, api_key, deadline, outcome),
        name="cbs-funding-observer",
        daemon=True,  # A name lookup or TCP connect (or a Windows TLS handshake) may
        # outlive observe(); such a worker never sends request headers afterwards (trace).
    )
    try:
        worker.start()
    except RuntimeError:
        outcome["reason"] = "internal_error"
    else:
        worker.join(max(0.0, watchdog_end - time.monotonic()))
        if worker.is_alive():
            deadline.cancel()
            worker.join(JOIN_GRACE_SECONDS)
            deadline.abandon()
            outcome = {"reason": "deadline_exceeded"}  # Never a result after the deadline.
    if "found" in outcome:
        found = outcome["found"]
        return FundingObservation(
            server=server,
            project=project,
            started_at=started,
            finished_at=_timestamp(now),
            complete=True,
            gpu=_category(GPU_CATEGORY, found.get("gpu")),
            cpu=_category(CPU_CATEGORY, found.get("cpu")),
        )
    reason = outcome.get("reason")
    return FundingObservation(
        server=server,
        project=project,
        started_at=started,
        finished_at=_timestamp(now),
        complete=False,
        gpu=CategoryFunding(GPU_CATEGORY, PROVIDER, "unknown"),
        cpu=CategoryFunding(CPU_CATEGORY, PROVIDER, "unknown"),
        reason=reason if reason in REASONS else "internal_error",
    )
