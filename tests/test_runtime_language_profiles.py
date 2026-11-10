"""Runtime language edits stay model-scoped; native/OpenAI retain their own Auto protocols."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from admission_support import write_original_wav
from test_native_model_profiles import native_settings, raw_profile
from test_ucloud_native import PRODUCT, SECRET, SETUP, TEMPLATE
from test_ucloud_native import remote as remote

from cbs_transcriber import providers
from cbs_transcriber import ucloud_native as native
from cbs_transcriber.catalog import ucloud_models


def row(store, identifier):
    return store.one("SELECT * FROM providers WHERE id=?", (identifier,))


def version(store, identifier):
    saved = row(store, identifier)
    return providers.stored_version(saved["name"], saved["value"])


def legacy_native_job(client, credentials):
    """Seed the historical native contract; never alter a new accepted choice's receipt."""
    store = client.app.state.store
    settings = native_settings(
        base_url="https://cloud.invalid", language="", ucloud_transcriber=SETUP
    )
    provider = providers.save_profile(
        store, credentials, "UCloud native", settings, api_key=SECRET, remember=False
    )
    project = store.create_project("Synthetic legacy native")
    recording = store.create_recording(project["id"], "test.v2.wav")
    write_original_wav(store, recording)
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    job = store.add_job(
        recording["id"],
        "transcribe",
        {
            "provider_id": provider["id"],
            "provider_name": provider["name"],
            "settings": settings.model_dump(),
            "revision": recording["revision"],
        },
    )
    assert "input_acceptance" not in job["params"] and "audio_input" not in job["params"]
    return job


@pytest.mark.parametrize("language", ["", "da", "en", "auto"])
def test_v125_runtime_language_updates_only_one_raw_profile(
    client, credentials, monkeypatch, language
):
    store = client.app.state.store
    settings = native_settings(model="whisperx/large-v3", language="da", approved_for=[])
    settings.enabled = False
    identifier = raw_profile(store, settings, "Named Runtime")
    payload = json.loads(row(store, identifier)["value"])
    payload["future_setting"] = {"preserved": True}
    store.execute(
        "UPDATE providers SET value=? WHERE id=?", (json.dumps(payload, indent=2), identifier)
    )
    other = raw_profile(store, native_settings(model="small", language="en"))
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.execute("UPDATE recordings SET provider_id=? WHERE id=?", (identifier, recording["id"]))
    providers.set_default_provider(store, other)
    frozen = store.add_job(
        recording["id"],
        "transcribe",
        {"provider_id": identifier, "settings": settings.model_dump()},
    )
    before_rows = store.all("SELECT * FROM providers ORDER BY id")
    before_job = store.job(frozen["id"])
    before_default = providers.default_provider_id(store)
    before_keys = dict(credentials.keys)

    def refuse(*args, **kwargs):
        pytest.fail("A model-only language edit must not append models or write credentials")

    monkeypatch.setattr(providers, "_append_models", refuse)
    monkeypatch.setattr(credentials, "set", refuse)
    returned = providers.save_runtime_language(
        store, credentials, identifier, language, version(store, identifier)
    )
    updated = row(store, identifier)
    assert json.loads(updated["value"]) == payload | {"language": language}
    target_before = next(r for r in before_rows if r["id"] == identifier)
    assert updated["name"] == "Named Runtime" and updated["created"] == target_before["created"]
    assert {r["id"] for r in store.all("SELECT * FROM providers")} == {r["id"] for r in before_rows}
    for saved in before_rows:
        if saved["id"] != identifier:
            assert row(store, saved["id"]) == saved
    assert returned["id"] == identifier and returned["language"] == language
    assert returned["approved_for"] == [] and returned["enabled"] is False
    assert returned["version"] == version(store, identifier)
    assert store.job(frozen["id"]) == before_job
    assert store.recording(recording["id"])["provider_id"] == identifier
    assert providers.default_provider_id(store) == before_default
    assert credentials.keys == before_keys
    if language == "da":
        assert updated == next(r for r in before_rows if r["id"] == identifier)


@pytest.mark.parametrize("expected", [None, "", "x" * 16, "a" * 64])
def test_v125_runtime_language_requires_its_exact_version(client, credentials, expected):
    store = client.app.state.store
    identifier = raw_profile(store, native_settings(model="whisperx/large-v3-turbo"))
    before = row(store, identifier)
    with pytest.raises(providers.StaleProvider, match="another window"):
        providers.save_runtime_language(store, credentials, identifier, "auto", expected)
    assert row(store, identifier) == before


def test_v125_runtime_language_stale_permission_and_concurrent_writes_cannot_be_lost(
    client, credentials
):
    store = client.app.state.store
    identifier = raw_profile(store, native_settings(model="whisperx/large-v3-turbo"))
    stale = version(store, identifier)
    payload = json.loads(row(store, identifier)["value"])
    payload["approved_for"] = []
    store.execute("UPDATE providers SET value=? WHERE id=?", (json.dumps(payload), identifier))
    before = row(store, identifier)
    with pytest.raises(providers.StaleProvider):
        providers.save_runtime_language(store, credentials, identifier, "auto", stale)
    assert row(store, identifier) == before
    fresh = version(store, identifier)

    def change(language):
        try:
            return providers.save_runtime_language(store, credentials, identifier, language, fresh)
        except providers.StaleProvider:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(change, ["auto", "en"]))
    assert sum(result is not None for result in results) == 1
    assert json.loads(row(store, identifier)["value"])["approved_for"] == []


def test_v125_public_profile_version_belongs_to_the_same_displayed_snapshot(
    client, credentials, monkeypatch
):
    store = client.app.state.store
    identifier = raw_profile(store, native_settings(model="whisperx/large-v3", language="da"))
    loaded_version = version(store, identifier)
    newer = json.loads(row(store, identifier)["value"])
    newer.update(language="en", approved_for=[])
    original_one = store.one
    changed = False

    def read_then_change(sql, params=()):
        nonlocal changed
        found = original_one(sql, params)
        if not changed and "FROM providers" in sql:
            changed = True
            store.execute(
                "UPDATE providers SET value=? WHERE id=?", (json.dumps(newer), identifier)
            )
        return found

    monkeypatch.setattr(store, "one", read_then_change)
    shown = providers.public_profile(store, credentials, identifier)
    assert shown["language"] == "da" and shown["approved_for"] == ["protected"]
    assert shown["version"] == loaded_version
    with pytest.raises(providers.StaleProvider):
        providers.save_runtime_language(store, credentials, identifier, "auto", shown["version"])
    assert json.loads(row(store, identifier)["value"]) == newer


@pytest.mark.parametrize("model", ["large-v3", "whisperx/not-in-catalog"])
def test_v125_runtime_language_rejects_non_runtime_profiles_and_missing_ids(
    client, credentials, model
):
    store = client.app.state.store
    identifier = raw_profile(store, native_settings(model=model))
    before = row(store, identifier)
    with pytest.raises(ValueError, match="CBS Runtime"):
        providers.save_runtime_language(
            store, credentials, identifier, "auto", version(store, identifier)
        )
    assert row(store, identifier) == before
    with pytest.raises(KeyError):
        providers.save_runtime_language(store, credentials, "missing", "auto", "a" * 16)


@pytest.mark.parametrize("language", ["AUTO", " auto ", "fr", None, False, [], {}])
def test_v125_runtime_language_choices_are_canonical(client, credentials, language):
    store = client.app.state.store
    identifier = raw_profile(store, native_settings(model="whisperx/large-v3-turbo"))
    before = row(store, identifier)
    with pytest.raises(ValueError, match="Runtime language"):
        providers.save_runtime_language(
            store, credentials, identifier, language, version(store, identifier)
        )
    assert row(store, identifier) == before


@pytest.mark.parametrize("language", ["auto", " AUTO "])
def test_v125_auto_runtime_base_never_fans_out_auto_to_missing_models(
    client, credentials, language
):
    store = client.app.state.store
    explicit = raw_profile(store, native_settings(model="small", language="en", approved_for=[]))
    explicit_runtime = raw_profile(
        store, native_settings(model="whisperx/large-v3-turbo", language="da", enabled=False)
    )
    preserved = [row(store, identifier) for identifier in (explicit, explicit_runtime)]
    saved = providers.save_profile(
        store,
        credentials,
        "Automatic Runtime",
        native_settings(model="whisperx/large-v3", language=language),
    )
    rows = [
        r
        for r in store.all("SELECT * FROM providers")
        if json.loads(r["value"]).get("provider_type") == "ucloud"
    ]
    assert len(rows) == len(ucloud_models())
    assert saved["language"] == language
    expected_languages = {
        model["model"]: model.get("fixed_language", "") for model in ucloud_models()
    }
    for previous in preserved:
        assert row(store, previous["id"]) == previous
    for configured in rows:
        if configured["id"] not in {explicit, explicit_runtime, saved["id"]}:
            payload = json.loads(configured["value"])
            assert payload["language"] == expected_languages[payload["model"]]


def test_v125_offline_backfill_retains_auto_only_on_its_existing_runtime_row(client, credentials):
    store = client.app.state.store
    identifier = raw_profile(store, native_settings(model="whisperx/large-v3", language=" AUTO "))
    before = row(store, identifier)
    added = providers.backfill_native_profiles(store)
    assert added == len(ucloud_models()) - 1
    assert row(store, identifier) == before
    expected_languages = {
        model["model"]: model.get("fixed_language", "") for model in ucloud_models()
    }
    for saved in store.all("SELECT * FROM providers"):
        payload = json.loads(saved["value"])
        if saved["id"] != identifier and payload.get("provider_type") == "ucloud":
            assert payload["language"] == expected_languages[payload["model"]]
    assert providers.backfill_native_profiles(store) == 0


@pytest.mark.parametrize("kind", ["openai", "native"])
@pytest.mark.parametrize("language", ["auto", " AuTo\t"])
def test_v125_new_saves_reject_literal_auto_but_legacy_settings_still_load(
    client, credentials, kind, language
):
    store = client.app.state.store
    settings = (
        native_settings(language=language)
        if kind == "native"
        else providers.Settings(language=language)
    )
    snapshot = settings.model_dump_json()
    before = store.all("SELECT * FROM providers ORDER BY id")
    with pytest.raises(ValueError, match="auto"):
        providers.save_profile(
            store, credentials, "Unsupported hint", settings, api_key="synthetic"
        )
    assert store.all("SELECT * FROM providers ORDER BY id") == before
    assert not credentials.keys
    # A stored/frozen legacy value remains parseable; new-save validation is not a reader migration.
    assert providers.Settings.model_validate_json(snapshot).language == language


@pytest.mark.parametrize("kind", ["custom", "ucloud-vllm"])
def test_v125_explicit_compatible_backends_keep_their_literal_auto_setting(
    client, credentials, kind
):
    settings = providers.Settings(
        provider_type=kind,
        base_url="https://speech.invalid/v1",
        model="owner/model",
        language="auto",
        capability="json",
    )
    result = providers.save_profile(client.app.state.store, credentials, "Compatible", settings)
    assert result["language"] == "auto" and result["capability"] == "json"


@pytest.mark.parametrize("language", ["auto", " AUTO "])
def test_v125_openai_literal_auto_refuses_before_key_file_or_client(monkeypatch, language):
    def refuse(*args, **kwargs):
        pytest.fail("No credential/client/file/request work is allowed for a literal Auto hint")

    class Unopened:
        open = refuse

    monkeypatch.setattr(providers, "require_key", refuse)
    monkeypatch.setattr(providers, "OpenAI", refuse)
    monkeypatch.setattr(providers, "DefaultHttpxClient", refuse)
    with pytest.raises(ValueError, match="Leave the OpenAI language hint blank"):
        providers.transcribe_file(Unopened(), providers.Settings(language=language), "")


@pytest.mark.parametrize(
    "base_url", ["https://api.openai.com/v1", "https://API.OpenAI.Com/alternate"]
)
def test_v125_custom_profile_cannot_send_literal_auto_to_the_official_host(
    client, credentials, monkeypatch, base_url
):
    store = client.app.state.store
    settings = providers.Settings(
        provider_type="custom", base_url=base_url, model="compatible-model", language=" AUTO "
    )
    before = store.all("SELECT * FROM providers ORDER BY id")

    def refuse(*args, **kwargs):
        pytest.fail("An official-host Auto hint must fail before credential/file/client work")

    class Unopened:
        open = refuse

    monkeypatch.setattr(credentials, "set", refuse)
    monkeypatch.setattr(providers, "OpenAI", refuse)
    monkeypatch.setattr(providers, "DefaultHttpxClient", refuse)
    monkeypatch.setattr(providers, "require_key", refuse)
    with pytest.raises(ValueError, match="Leave the OpenAI language hint blank"):
        providers.save_profile(store, credentials, "Official custom", settings, api_key="synthetic")
    assert store.all("SELECT * FROM providers ORDER BY id") == before
    with pytest.raises(ValueError, match="Leave the OpenAI language hint blank"):
        providers.transcribe_file(Unopened(), settings, "")
    assert providers.Settings.model_validate_json(settings.model_dump_json()).language == " AUTO "


def test_v125_runtime_language_on_the_official_host_does_not_bypass_the_hint_guard(
    client, credentials
):
    store = client.app.state.store
    identifier = raw_profile(
        store, native_settings(model="whisperx/large-v3", base_url="https://api.openai.com")
    )
    before = row(store, identifier)
    with pytest.raises(ValueError, match="Leave the OpenAI language hint blank"):
        providers.save_runtime_language(
            store, credentials, identifier, "auto", version(store, identifier)
        )
    assert row(store, identifier) == before


@pytest.mark.parametrize(
    "base_url", ["https://speech.invalid/v1", "https://api.openai.com.custom.invalid/v1"]
)
def test_v125_genuine_custom_hosts_keep_their_explicit_auto_hint(tmp_path, base_url):
    path = tmp_path / "synthetic.mp3"
    path.write_bytes(b"synthetic audio")
    calls = []

    def reply(request):
        body = request.read()
        assert b'name="language"' in body and b"auto" in body
        calls.append(request.url.host)
        return httpx.Response(200, json={"text": "Synthetic."})

    providers.transcribe_file(
        path,
        providers.Settings(provider_type="custom", base_url=base_url, language="auto"),
        "synthetic-key",
        httpx.Client(transport=httpx.MockTransport(reply)),
    )
    assert len(calls) == 1 and calls[0] != "api.openai.com"


@pytest.mark.parametrize("model", ["whisper-1", "gpt-transcribe"])
def test_v125_blank_openai_hint_keeps_both_language_parameters_omitted(tmp_path, model):
    path = tmp_path / "synthetic.mp3"
    path.write_bytes(b"synthetic audio")
    calls = []

    def reply(request):
        body = request.read()
        assert b'name="language"' not in body and b'name="languages[]"' not in body
        calls.append(request.url.path)
        return httpx.Response(200, json={"text": "Synthetic."})

    providers.transcribe_file(
        path,
        providers.Settings(model=model, language=""),
        "synthetic-key",
        httpx.Client(transport=httpx.MockTransport(reply)),
    )
    assert calls == ["/v1/audio/transcriptions"]


@pytest.mark.parametrize("origin", ["direct", "inherited"])
def test_v125_native_literal_auto_fails_before_private_upload_or_submission(
    client, credentials, remote, origin
):
    with client.app.state.store.library_lock():
        store = client.app.state.store
        job = legacy_native_job(client, credentials)
        if origin == "direct":
            job["params"]["settings"]["language"] = " AuTo "
            store.update_job(job["id"], params=job["params"])
        else:
            remote.template["specification"]["parameters"]["language"]["value"] = " AUTO "
        template_before = copy.deepcopy(remote.template)
        with pytest.raises(ValueError, match="literal language 'auto'"):
            client.app.state.queue.process(job["id"])
        assert not remote.uploaded and not remote.submissions
        assert remote.template == template_before
        assert all(request.method == "GET" for request in remote.calls)
        assert not any(request.url.path == "/api/files/upload" for request in remote.calls)
        assert not any(request.url.path == "/api/jobs" for request in remote.calls)


@pytest.mark.parametrize("language", ["da", "en"])
def test_v125_explicit_native_override_resolves_inherited_auto_without_mutating_template(language):
    template = copy.deepcopy(TEMPLATE["specification"])
    template["parameters"]["language"]["value"] = "auto"
    before = copy.deepcopy(template)
    parameters = {
        value["name"]: value
        for value in TEMPLATE["status"]["resolvedApplication"]["invocation"]["parameters"]
    }
    settings = providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="large-v3",
        language=language,
        ucloud_transcriber=SETUP,
    )
    specification = native.build_specification(
        settings, template, parameters, "/drive/run", ".wav", "test"
    )
    assert specification["parameters"]["language"] == {"type": "text", "value": language}
    assert template == before and specification["product"] == PRODUCT


def test_v125_owned_legacy_native_run_recovers_without_rebuilding_or_resubmitting(
    client, credentials, remote, monkeypatch
):
    with client.app.state.store.library_lock():
        store = client.app.state.store
        job = legacy_native_job(client, credentials)
        job["params"]["settings"]["language"] = " AUTO "
        job["params"]["ucloud_run"] = {"job_id": "remote-job"}
        store.update_job(job["id"], params=job["params"])
        frozen = copy.deepcopy(job["params"]["settings"])

        def refuse(*args, **kwargs):
            pytest.fail("Owned native recovery must not build or allocate a new job")

        monkeypatch.setattr(native, "build_specification", refuse)
        store.update_job(
            job["id"], state="running"
        )  # The production worker claims before processing.
        client.app.state.queue.process(job["id"])
        assert store.job(job["id"])["state"] == "completed"
        assert store.job(job["id"])["params"]["settings"] == frozen
        assert not remote.uploaded and not remote.submissions
