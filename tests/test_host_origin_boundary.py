"""Actual shared main/chooser boundary, without sockets, remote calls or real libraries."""

import pytest
from fastapi.testclient import TestClient

from cbs_transcriber.app import create_app, create_chooser_app


@pytest.mark.parametrize(
    "filename", ["app.js", "background_exports.js", "deleted_passages.js", "manual_review.js"]
)
def test_unversioned_editor_modules_are_fresh_after_app_restart(tmp_path, credentials, filename):
    app = create_app(tmp_path, start_worker=False, credentials=credentials)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/static/" + filename)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


@pytest.fixture(params=["main", "chooser"])
def boundary(request, tmp_path, credentials, monkeypatch):
    app = (
        create_app(tmp_path, start_worker=False, credentials=credentials)
        if request.param == "main"
        else create_chooser_app()
    )
    mutations, admissions = [], []

    @app.post("/api/boundary-probe")
    def mutate():
        mutations.append(True)
        return {"ok": True}

    @app.get("/api/boundary-probe")
    def read():
        return {"ok": True}

    if request.param == "main":
        gate = app.state.portable_capture_gate
        enter = gate.enter_mutation

        def observed(*args, **kwargs):
            admissions.append(True)
            return enter(*args, **kwargs)

        monkeypatch.setattr(gate, "enter_mutation", observed)
    with TestClient(
        app,
        base_url="http://127.0.0.1",
        # Origin makes these browser requests; carry this app's current instance so
        # the switch guard does not mask the host/origin boundary under test.
        headers={"X-CBS-Request": "1", "X-CBS-Instance": app.state.browser_instance},
        raise_server_exceptions=False,
    ) as client:
        yield client, mutations, admissions


@pytest.mark.parametrize("origin", ["http://[", "http://[::1"])
def test_malformed_origin_is_refused_before_admission(boundary, origin):
    client, mutations, admissions = boundary
    response = client.post("/api/boundary-probe", headers={"Origin": origin})
    assert response.status_code == 403, response.text
    assert not mutations and not admissions


@pytest.mark.parametrize("host", ["[", "[::1"])
def test_malformed_host_is_refused_without_response_url_parsing(boundary, host):
    client, mutations, admissions = boundary
    response = client.get("/api/boundary-probe", headers={"Host": host})
    assert response.status_code == 400, response.text
    assert not mutations and not admissions


def test_testclient_hostname_is_not_a_production_trusted_host(boundary):
    client, mutations, admissions = boundary
    response = client.post(
        "/api/boundary-probe", headers={"Host": "testserver", "Origin": "http://testserver"}
    )
    assert response.status_code == 400, response.text
    assert not mutations and not admissions


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost:8765", "[::1]:8765"])
def test_loopback_origins_and_explicit_header_remain_accepted(boundary, host):
    client, mutations, _ = boundary
    response = client.post(
        "/api/boundary-probe", headers={"Host": host, "Origin": "http://" + host}
    )
    assert response.status_code == 200, response.text
    assert mutations == [True]


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://127.0.0.1:8765"])
def test_cross_origin_and_wrong_port_are_refused_before_admission(boundary, origin):
    client, mutations, admissions = boundary
    response = client.post("/api/boundary-probe", headers={"Origin": origin})
    assert response.status_code == 403
    assert not mutations and not admissions


def test_non_browser_headered_client_is_still_supported(boundary):
    client, mutations, _ = boundary
    assert client.post("/api/boundary-probe").status_code == 200
    assert mutations == [True]
    client.headers.pop("X-CBS-Request")
    assert client.post("/api/boundary-probe").status_code == 403
    assert mutations == [True]


def test_read_responses_keep_browser_headers_without_granting_cross_origin_access(boundary):
    client, mutations, admissions = boundary
    response = client.get("/api/boundary-probe", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    policy = response.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "frame-ancestors 'none'" in policy
    assert "unsafe-inline" not in policy
    assert "access-control-allow-origin" not in response.headers
    assert not mutations and not admissions
