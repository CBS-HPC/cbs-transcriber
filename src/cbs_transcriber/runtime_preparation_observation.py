"""Typed, bounded reads for standalone preparation; legacy readers are unchanged."""

import json
import time

import httpx
from ucloud_workflow import UCloudAPIError

from . import enhancement_api as api

READ_SECONDS = 10


class Error(ValueError):
    pass


class Retryable(Error):
    def __init__(self):
        super().__init__("UCloud preparation observations are temporarily unavailable.")


class Pending(Error):
    def __init__(self):
        super().__init__("Waiting for UCloud preparation verification files to become available.")


class Refused(Error):
    def __init__(self):
        super().__init__(
            "UCloud preparation failed. Its evidence is unavailable or invalid. "
            "Its recorded allocation and files were retained; inspect Activity before retrying."
        )


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Retryable()
    return min(READ_SECONDS, remaining)


def _status(code, *, missing=False):
    if code == 429 or 500 <= code <= 599:
        raise Retryable()
    if missing and code in {400, 404}:
        raise Pending()
    if not 200 <= code <= 299:
        raise Refused()


def _raw(reply, deadline, limit):
    if reply.headers.get("Content-Encoding", "identity") != "identity":
        raise Refused()
    data = bytearray()
    _remaining(deadline)
    for block in reply.iter_raw():
        _remaining(deadline)
        if len(data) + len(block) > limit:
            raise Refused()
        data.extend(block)
    _remaining(deadline)
    return bytes(data)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise Refused()
        result[key] = value
    return result


def _json(raw, *, incomplete=False):
    try:
        text = raw.decode("utf-8")
        result = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(Refused()),
        )
    except UnicodeDecodeError as exc:
        if incomplete and exc.reason == "unexpected end of data" and exc.end == len(raw):
            raise Pending() from None
        raise Refused() from None
    except json.JSONDecodeError as exc:
        if incomplete and (
            exc.pos >= len(text.rstrip()) or exc.msg.startswith("Unterminated string")
        ):
            raise Pending() from None
        raise Refused() from None
    except (TypeError, RecursionError):
        raise Refused() from None
    if type(result) is not dict:
        raise Refused()
    return result


def control(client, method, path, *, deadline=None, missing=False, **kwargs):
    deadline = time.monotonic() + READ_SECONDS if deadline is None else deadline
    try:
        # Same pinned SDK transport as ADR0172; unlike its readiness adapter this seam
        # preserves authentication/invalid proof versus retryable transport failures.
        with client._client.stream(
            method,
            path,
            timeout=_remaining(deadline),
            follow_redirects=False,
            headers={"Accept-Encoding": "identity"},
            **kwargs,
        ) as reply:
            _status(reply.status_code, missing=missing)
            return _json(_raw(reply, deadline, api.RUNTIME_METADATA_BYTES))
    except UCloudAPIError as exc:
        _status(exc.status_code or 0, missing=missing)
        raise Refused() from None
    except (httpx.HTTPError, httpx.StreamError):
        raise Retryable() from None
    except (AttributeError, KeyError, TypeError):
        raise Refused() from None


def state(client, remote_id, specification, *, deadline=None):
    value = control(
        client,
        "GET",
        "/api/jobs/retrieve",
        deadline=deadline,
        params={
            "id": remote_id,
            "includeUpdates": "false",
            "includeApplication": "true",
            "includeParameters": "true",
        },
    )
    status, actual = value.get("status"), value.get("specification")
    if (
        value.get("id") != remote_id
        or type(status) is not dict
        or type(status.get("state")) is not str
        or status.get("state") not in {"IN_QUEUE", "RUNNING", *api.TERMINAL}
        or type(actual) is not dict
        or type(specification) is not dict
        or any(
            actual.get(key) != specification.get(key)
            for key in ("name", "application", "product", "parameters", "resources")
        )
    ):
        raise Refused()
    return status["state"]


def metadata(client, path, *, deadline=None):
    item = control(
        client, "GET", "/api/files/retrieve", deadline=deadline, missing=True, params={"id": path}
    )
    if item.get("id") != path:
        raise Refused()
    try:
        api._runtime_kind(item)
    except api.RuntimePreflightBlocked:
        raise Refused() from None
    return item


def read_bytes(client, settings, path, *, deadline=None):
    deadline = time.monotonic() + READ_SECONDS if deadline is None else deadline
    ticket = control(
        client,
        "POST",
        "/api/files/download",
        deadline=deadline,
        missing=True,
        json={"items": [{"id": path}]},
    )
    try:
        endpoint = api.native.transfer_url(
            settings.base_url, api.native.one_response(ticket)["endpoint"]
        )
        with httpx.Client(timeout=_remaining(deadline), follow_redirects=False) as transport:
            with transport.stream(
                "GET", endpoint, headers={"Accept-Encoding": "identity"}
            ) as reply:
                _status(reply.status_code, missing=True)
                return _raw(reply, deadline, api.MAX_STATUS_BYTES)
    except (httpx.HTTPError, httpx.StreamError):
        raise Retryable() from None
    except (KeyError, TypeError, AttributeError):
        raise Refused() from None
    except ValueError as exc:
        if isinstance(exc, Error):
            raise
        raise Refused() from None


def read_json(client, settings, path, *, deadline=None):
    return _json(read_bytes(client, settings, path, deadline=deadline), incomplete=True)


def stamp(client, settings, *, deadline=None):
    deadline = time.monotonic() + READ_SECONDS if deadline is None else deadline
    root = api.runtime_folder(settings)
    folder = metadata(client, root, deadline=deadline)
    item = metadata(client, root + "/stamp.json", deadline=deadline)
    expected = api.runtime_stamp_bytes()
    size = item.get("status", {}).get("sizeInBytes", item.get("sizeInBytes"))
    if (
        api._runtime_kind(folder) != "DIRECTORY"
        or api._runtime_kind(item) != "FILE"
        or size is not None
        and (type(size) is not int or size != len(expected))
        or read_bytes(client, settings, root + "/stamp.json", deadline=deadline) != expected
    ):
        raise Refused()
