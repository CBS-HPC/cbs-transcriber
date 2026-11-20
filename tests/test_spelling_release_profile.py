"""Hidden spelling in the MVP profile (ADR 0145/0185): no new work, existing private data stays.

Synthetic dictionaries and a mocked downloader only; nothing is retrieved from the network.
"""

import json
from hashlib import sha256
from pathlib import Path

import pytest
from conftest import MemoryCredentials
from fastapi.testclient import TestClient

from cbs_transcriber import release_profile, spelling
from cbs_transcriber.app import create_app


@pytest.fixture
def synthetic_dictionaries(monkeypatch):
    original = spelling._catalog
    catalog = json.loads(
        (Path(spelling.__file__).parent / "catalogs/spelling_dictionaries.json").read_bytes()
    )
    bodies = {}
    for dictionary in catalog["dictionaries"]:
        data = {"aff": b"SET UTF-8\n", "dic": b"1\nhello\n", "license": b"Synthetic licence\n"}
        for kind, content in data.items():
            entry = dictionary["files"][kind]
            entry.update(bytes=len(content), sha256=sha256(content).hexdigest())
            bodies[entry["url"]] = content
    monkeypatch.setattr(
        spelling, "_catalog", lambda value=None: original(catalog if value is None else value)
    )
    downloads = []

    def download(entry, target, *, guard=None):
        downloads.append(entry["url"])
        target.write_bytes(bodies[entry["url"]])

    monkeypatch.setattr(spelling, "_download_file", download)
    return downloads


def client(root, release, cache):
    app = create_app(
        root,
        start_worker=False,
        credentials=MemoryCredentials(),
        release=release,
        spelling_cache_dir=cache,
    )
    return TestClient(app, headers={"X-CBS-Request": "1"})


def refused(response):
    assert response.status_code == 403, response.text
    assert response.json() == {
        "code": "feature_disabled",
        "feature": "spelling",
        "detail": f"{release_profile.FEATURES['spelling']} is not available in this version of "
        "CBS Transcriber.",
    }


def test_mvp_refuses_new_spelling_work_but_keeps_existing_private_data(
    tmp_path, synthetic_dictionaries
):
    root, cache = tmp_path / "library", tmp_path / "cache"
    with client(root, "full", cache) as full:
        store = full.app.state.store
        project = store.create_project("Synthetic spelling", default_language="en")
        rid = store.create_recording(project["id"], "synthetic.wav")["id"]
        assert full.put(
            "/api/spelling/settings", json={"revision": 0, "enabled": True, "auto_download": True}
        ).json() == {"revision": 1, "enabled": True, "auto_download": True}
        installed = full.post(
            f"/api/recordings/{rid}/spelling/dictionary", json={"settings_revision": 1}
        )
        assert installed.status_code == 200 and installed.json()["ready"], installed.text
        assert full.put(
            "/api/spelling/vocabulary", json={"revision": 0, "words": ["Alpha", "Beta"]}
        ).json() == {"revision": 1, "words": ["Alpha", "Beta"]}
        project_path = f"/api/projects/{project['id']}/spelling/vocabulary"
        assert full.put(project_path, json={"revision": 0, "words": ["Gamma"]}).status_code == 200
    downloads = list(synthetic_dictionaries)
    assert len(downloads) == 3

    with client(root, "mvp", cache) as mvp:
        assert "spelling" in mvp.get("/api/features").json()["disabled"]
        assert 'data-off="' in mvp.get("/").text and "spelling" in mvp.get("/").text
        # Reads of existing private data and the public cache stay available.
        assert mvp.get("/api/spelling/settings").json() == {
            "revision": 1,
            "enabled": True,
            "auto_download": True,
        }
        listed = {
            row["language"]: row
            for row in mvp.get("/api/spelling/dictionaries").json()["dictionaries"]
        }
        assert listed["en"]["ready"] is True
        assert mvp.get("/api/spelling/vocabulary").json()["words"] == ["Alpha", "Beta"]
        assert mvp.get(project_path).json() == {"revision": 1, "words": ["Gamma"]}
        # No new work: preferences, checks, suggestions and retrieval are refused before
        # their handlers, so no worker starts and nothing is downloaded.
        refused(
            mvp.put(
                "/api/spelling/settings",
                json={"revision": 1, "enabled": False, "auto_download": False},
            )
        )
        refused(
            mvp.post(
                f"/api/recordings/{rid}/spelling/check", json={"text": "helo", "request_id": "a"}
            )
        )
        refused(
            mvp.post(
                f"/api/recordings/{rid}/spelling/suggest", json={"word": "helo", "request_id": "b"}
            )
        )
        refused(
            mvp.post(
                f"/api/recordings/{rid}/spelling/dictionary",
                json={"settings_revision": 1, "consent": True},
            )
        )
        assert mvp.app.state.spelling.worker.process is None
        # Adding a private word is new work; removing existing words is cleanup.
        refused(
            mvp.put("/api/spelling/vocabulary", json={"revision": 1, "words": ["Alpha", "Delta"]})
        )
        refused(mvp.put(project_path, json={"revision": 1, "words": ["Gamma", "Epsilon"]}))
        assert mvp.get("/api/spelling/vocabulary").json() == {
            "revision": 1,
            "words": ["Alpha", "Beta"],
        }
        removed = mvp.put("/api/spelling/vocabulary", json={"revision": 1, "words": ["Alpha"]})
        assert removed.json() == {"revision": 2, "words": ["Alpha"]}, removed.text
        assert mvp.put(project_path, json={"revision": 1, "words": []}).json() == {
            "revision": 2,
            "words": [],
        }
        # A stale removal still meets the ordinary revision fence.
        assert (
            mvp.put("/api/spelling/vocabulary", json={"revision": 1, "words": []}).status_code
            == 409
        )
        # The disposable public dictionary cache can still be removed.
        gone = mvp.request("DELETE", "/api/spelling/dictionaries/en", json={"settings_revision": 1})
        assert gone.status_code == 200, gone.text
        assert gone.json()["ready"] is False
        assert not list(cache.rglob("installed.json"))
    assert synthetic_dictionaries == downloads

    with client(root, "full", cache) as full:
        # The full profile is unchanged: the same addition succeeds.
        added = full.put(
            "/api/spelling/vocabulary", json={"revision": 2, "words": ["Alpha", "Delta"]}
        )
        assert added.json() == {"revision": 3, "words": ["Alpha", "Delta"]}, added.text


def test_mvp_status_reads_create_no_dictionary_storage(tmp_path, synthetic_dictionaries):
    cache = tmp_path / "cache"
    with client(tmp_path / "library", "mvp", cache) as mvp:
        store = mvp.app.state.store
        project = store.create_project("Synthetic", default_language="da")
        rid = store.create_recording(project["id"], "synthetic.wav")["id"]
        assert mvp.get("/api/spelling/settings").json()["enabled"] is False
        assert all(
            not row["installed"]
            for row in mvp.get("/api/spelling/dictionaries").json()["dictionaries"]
        )
        refused(
            mvp.post(
                f"/api/recordings/{rid}/spelling/check", json={"text": "sprog", "request_id": "a"}
            )
        )
    assert not cache.exists() and not synthetic_dictionaries
