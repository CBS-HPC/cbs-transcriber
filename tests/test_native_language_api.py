"""Per-model native language changes are scoped and never start cloud work."""

import copy

import pytest
from test_runtime_batch_api import add_profile

from cbs_transcriber import providers


def test_native_language_choice_is_versioned_and_preserves_other_profiles(client, credentials):
    store = client.app.state.store
    native = add_profile(store, credentials, "large-v3")
    runtime = add_profile(store, credentials, "whisperx/large-v3")
    before = providers.public_profile(store, credentials, native["id"])
    other = store.one("SELECT * FROM providers WHERE id=?", (runtime["id"],))
    keys = copy.deepcopy(credentials.keys)
    endpoint = f"/api/providers/{native['id']}/native-language"
    response = client.put(endpoint, json={"language": "en", "expected_version": before["version"]})
    assert response.status_code == 200, response.text
    assert response.json()["language"] == "en"
    assert store.one("SELECT * FROM providers WHERE id=?", (runtime["id"],)) == other
    assert credentials.keys == keys and not store.all("SELECT id FROM jobs")
    assert (
        client.put(
            endpoint, json={"language": "da", "expected_version": before["version"]}
        ).status_code
        == 409
    )
    current = providers.public_profile(store, credentials, native["id"])
    assert (
        client.put(endpoint, json={"language": "", "expected_version": current["version"]}).json()[
            "language"
        ]
        == ""
    )


@pytest.mark.parametrize(("language", "status"), [("auto", 400), (" AuTo ", 400), ("x" * 21, 422)])
def test_native_language_rejects_auto_and_overlong_values(client, credentials, language, status):
    store = client.app.state.store
    native = add_profile(store, credentials, "large-v3")
    saved = providers.public_profile(store, credentials, native["id"])
    response = client.put(
        f"/api/providers/{native['id']}/native-language",
        json={"language": language, "expected_version": saved["version"]},
    )
    assert response.status_code == status
    assert providers.public_profile(store, credentials, native["id"]) == saved


def test_native_language_accepts_other_code_without_assuming_provider_support(client, credentials):
    store = client.app.state.store
    native = add_profile(store, credentials, "large-v3")
    saved = providers.public_profile(store, credentials, native["id"])
    response = client.put(
        f"/api/providers/{native['id']}/native-language",
        json={"language": "fr", "expected_version": saved["version"]},
    )
    assert response.status_code == 200
    assert response.json()["language"] == "fr"
    response = client.put(
        f"/api/providers/{native['id']}/native-language",
        json={"language": " fr ", "expected_version": response.json()["version"]},
    )
    assert response.status_code == 200
    assert response.json()["language"] == " fr "


def test_native_language_route_refuses_runtime_profiles(client, credentials):
    store = client.app.state.store
    runtime = add_profile(store, credentials, "whisperx/large-v3")
    saved = providers.public_profile(store, credentials, runtime["id"])
    response = client.put(
        f"/api/providers/{runtime['id']}/native-language",
        json={"language": "en", "expected_version": saved["version"]},
    )
    assert response.status_code == 400
    assert providers.public_profile(store, credentials, runtime["id"]) == saved
