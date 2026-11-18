"""Ready synthetic UCloud Transcriber connections for browser transcription journeys.

Direct OpenAI is disabled in this version (DIRECT_OPENAI_DISABLED), so journeys that need a
fresh transcription use a saved native UCloud connection with a recorded successful check.
Discovery and both native execution entry points (singleton and shared batch) run in
process: no UCloud request, upload, allocation or credential leaves the test. Results are
committed through the real Store producer path, under each accepted job's own receipt.
"""

from admission_support import checked_connection
from browser_provider_support import mock_native_check

from cbs_transcriber import native_batch, providers, ucloud_native

NATIVE_KEY = "synthetic-ucloud-key"
NATIVE_SERVER = "https://cloud.sdu.dk"


def native_settings(**changes):
    """A complete native Transcriber connection on the fixed MIG; changes override fields."""
    setup = {
        "project_id": "synthetic-project",
        "storage_path": "/drive/cbs",
        "application": dict(ucloud_native.MIG_APPLICATION),
        "product": dict(ucloud_native.MIG_PRODUCT),
        **changes.pop("ucloud_transcriber", {}),
    }
    values = {
        "provider_type": "ucloud",
        "base_url": NATIVE_SERVER,
        "model": "large-v3",
        "capability": "words_speakers",
        "ucloud_transcriber": setup,
        **changes,
    }
    return providers.Settings.model_validate(values)


def save_ready_native(
    store, credentials, name="UCloud Transcriber", *, enabled_models=None, **changes
):
    """Save one native connection (plus its catalog models) and record a successful check.

    The Settings UI always saves the catalog's words_speakers capability. A narrower
    `capability` here models an API-saved or legacy native profile; the server and the
    transcription controls honour it as the model's declared native features.
    """
    saved = providers.save_profile(
        store,
        credentials,
        name,
        native_settings(**changes),
        api_key=NATIVE_KEY,
        remember=False,
        enabled_models=enabled_models,
    )
    checked_connection(store, credentials, saved["id"])
    return saved


def restore_native_key(app, server=NATIVE_SERVER):
    """A restarted test server starts with empty in-memory credentials.

    This stands in for a key remembered in the OS credential store: the saved connection
    check stays bound to the same key, so its models remain ready after the restart.
    """
    app.state.credentials.set("ucloud-control:" + server, NATIVE_KEY, False)


def mock_native_runs(monkeypatch, respond):
    """Complete accepted native jobs in process with `respond(settings, recording)` payloads.

    The payload is an ordinary provider response ({"text", "segments", "words"}), normalized
    exactly like an imported provider result. Returns the (model, recording_id) calls.
    """
    calls = []
    mock_native_check(monkeypatch, key=NATIVE_KEY)

    def commit(store, job, recording, settings, **source):
        calls.append((settings.model, recording["id"]))
        store.add_result(
            recording["id"],
            providers.normalize_response(respond(settings, recording)),
            {},
            job["params"]["provider_name"],
            {
                "kind": "provider",
                "provider_id": job["params"].get("provider_id"),
                **settings.model_dump(),
                **source,
            },
            job_id=job["id"],
        )

    def run_transcription(worker, job, recording, settings, key, *, reconnect=None):
        assert key == NATIVE_KEY and reconnect is None
        assert "native_batch_id" not in job["params"]
        commit(worker.store, job, recording, settings)

    def run_batch(worker, job, *, reconnect=None):
        """Settle one shared allocation as a successful import of every member would."""
        assert reconnect is None
        store = worker.store
        batch = native_batch.batch_for_job(store, job)
        if worker.active_batch_id != batch["id"] and not store.claim_native_batch(batch["id"]):
            return
        settings = providers.Settings.model_validate(batch["params"]["settings"])
        assert worker.credentials.get(providers.credential_scope(settings)) == NATIVE_KEY
        for member in batch["members"]:
            child = store.job(member["job_id"])
            if child["state"] == "completed":
                continue
            recording = store.recording(member["recording_id"])
            commit(store, child, recording, settings, ucloud_batch_id=batch["id"])
            store.update_job(member["job_id"], state="completed", progress=1, message="Complete")
        store.finish_native_batch(batch["id"], "completed", "UCloud batch complete")

    monkeypatch.setattr(ucloud_native, "run_transcription", run_transcription)
    monkeypatch.setattr(native_batch, "run_batch", run_batch)
    return calls
