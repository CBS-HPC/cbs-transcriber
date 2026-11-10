"""Stale browser tabs cannot act on the next serving library instance."""

import re

from fastapi.testclient import TestClient

from cbs_transcriber.app import create_app, create_chooser_app


def token(client):
    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    match = re.search(r'data-cbs-instance="([a-f0-9]{32})"', page.text)
    assert match
    return match.group(1)


def test_browser_instance_fences_reads_writes_and_direct_resources(tmp_path, credentials):
    first = create_app(tmp_path / "first", start_worker=False, credentials=credentials)
    second = create_app(tmp_path / "second", start_worker=False, credentials=credentials)
    with TestClient(first) as old, TestClient(second) as new:
        old_token, new_token = token(old), token(new)
        assert old_token != new_token
        for path in ("/static/index.html", "/static/library.html"):
            redirect = new.get(path, follow_redirects=False)
            assert redirect.status_code == 303
            assert redirect.headers["location"] == "/"
            assert redirect.headers["cache-control"] == "no-store"
        browser = {"Sec-Fetch-Site": "same-origin"}
        stale = {**browser, "X-CBS-Instance": old_token}
        fresh = {**browser, "X-CBS-Instance": new_token}
        assert new.get("/api/projects", headers=fresh).json() == []
        assert new.get("/api/projects", headers=stale).status_code == 409
        assert new.get("/api/projects", headers=browser).status_code == 409
        response = new.post(
            "/api/projects",
            json={"name": "Synthetic"},
            headers={**stale, "X-CBS-Request": "1"},
        )
        assert response.status_code == 409
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["code"] == "library_instance_changed"
        assert new.get("/api/projects", headers=fresh).json() == []
        # Even a matching recording ID cannot disclose media or an export from the next library.
        for path in (
            "/api/recordings/same-id/media/original",
            "/api/recordings/same-id/media/audio",
            "/api/recordings/same-id/export?format=text",
        ):
            separator = "&" if "?" in path else "?"
            assert (
                new.get(f"{path}{separator}cbs_instance={old_token}", headers=browser).status_code
                == 409
            )
        # Supplying a fresh header cannot override an old media URL.
        response = new.get(
            f"/api/recordings/same-id/media/original?cbs_instance={old_token}", headers=fresh
        )
        assert response.status_code == 409
        # Unmarked local integrations remain compatible, with no fencing claim. A client
        # that sends browser-identifying headers needs an instance even if it is not a browser.
        assert new.get("/api/projects").status_code == 200
        assert new.get("/api/projects", headers={"Origin": "http://localhost"}).status_code == 409
        # The initiating page may only ask whether the accepted target has opened.
        target = str(second.state.store.root)
        assert new.get(
            "/api/library/switch-ready", params={"expected": target}, headers=stale
        ).json() == {"ready": True}
        assert new.get(
            "/api/library/switch-ready", params={"expected": "different"}, headers=stale
        ).json() == {"ready": False}


def test_startup_chooser_has_its_own_server_instance(tmp_path, credentials):
    chooser = create_chooser_app()
    library = create_app(tmp_path / "library", start_worker=False, credentials=credentials)
    with TestClient(chooser) as initial, TestClient(library) as opened:
        assert token(initial) != token(opened)
        response = opened.get(
            "/api/libraries",
            headers={"Sec-Fetch-Site": "same-origin", "X-CBS-Instance": token(initial)},
        )
        assert response.status_code == 409
