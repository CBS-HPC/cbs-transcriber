"""The suite-wide guard keeps every test on loopback (AGENTS.md: tests never call a service)."""

import socket
import threading

import httpx
import pytest


@pytest.mark.parametrize("host", ["cloud.sdu.dk", "api.openai.com", "example.invalid", b"x.y"])
def test_real_host_names_are_never_resolved(host):
    with pytest.raises(socket.gaierror, match="Tests never reach a real host"):
        socket.getaddrinfo(host, 443)
    if isinstance(host, str):
        with pytest.raises(socket.gaierror, match="Tests never reach a real host"):
            socket.gethostbyname(host)


@pytest.mark.parametrize("address", ["192.0.2.1", "2001:db8::1", "10.0.0.1"])
def test_non_loopback_addresses_are_never_connected(address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    with socket.socket(family) as probe:
        with pytest.raises(ConnectionRefusedError, match="Tests never reach a real host"):
            probe.connect((address, 443))


def test_http_client_to_a_real_host_fails_before_any_request():
    with pytest.raises(httpx.ConnectError):
        httpx.get("https://cloud.sdu.dk/api/projects", timeout=2)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_stays_reachable(host):
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    try:
        server = socket.socket(family)
        server.bind((host if host != "localhost" else "127.0.0.1", 0))
    except OSError:
        pytest.skip(f"{host} is not available on this computer")
    with server:
        server.listen(1)
        port = server.getsockname()[1]
        accepted = threading.Thread(target=lambda: server.accept()[0].close())
        accepted.start()
        with socket.create_connection((host, port), timeout=5):
            pass
        accepted.join(5)
