"""Explicit synthetic source/admission steps; callers visibly hold the Store lease.

These functions do not accept HTTP requests, acquire a lease, mutate frozen receipts,
fake source proof, or run an executable transcription/enhancement job.
"""

import wave

from cbs_transcriber import input_admission, original_inspection


def checked_connection(store, credentials, provider_id=None, *, models=None):
    """Explicit successful synthetic Settings check; no transport or credential changes.

    Call only where the test's configured provider is meant to accept fresh work. Keep
    raw/frozen settings exact and use real server-issued proof validation/persistence.
    """
    from cbs_transcriber import provider_connection, providers

    selected = providers.profile(store, provider_id or providers.default_provider_id(store))
    settings = providers.Settings.model_validate(selected["settings"])
    key = credentials.get(providers.credential_scope(settings))
    providers.require_key(settings, key)
    checks = provider_connection.Checks()
    proof = checks.issue(
        settings,
        key,
        checks.begin(settings),
        models=[settings.model] if models is None else models,
    )
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        checks.save(db, proof, settings, key)


def write_original_wav(store, recording):
    """Create a real three-second silent WAV before a fresh synthetic request."""
    assert recording["filename"].endswith(".wav")
    directory = store.directory(recording["id"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "original.wav"
    assert not path.exists(), "Test originals remain immutable"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x00\x00" * 48000)
    return path


def inspect_and_materialize(store, credentials, identifiers):
    """Run the real source inspector/preflight/CAS for exactly the accepted test jobs."""
    assert store._lease_depth > 0, "The test must hold the same-Store lease through processing"
    identifiers = list(identifiers)
    wanted = set(identifiers)
    assert len(wanted) == len(identifiers) and wanted
    for recording_id in {store.job(identifier)["recording_id"] for identifier in identifiers}:
        cache = original_inspection.read(store, recording_id)
        assert cache is not None
        if cache["state"] != "ready":
            claim = original_inspection.claim(store, cache["inspection_job_id"])
            assert claim is not None
            cache = original_inspection.run(store, claim)
        assert cache["state"] == "ready", cache.get("error")
    remaining = set(wanted)
    while remaining:
        token = input_admission.claim(store)
        assert token is not None, "Every supplied source is ready for explicit admission"
        members = {member["id"] for member in token["jobs"]}
        assert members <= remaining, "Do not implicitly drain another request"
        outcomes = input_admission.prepare(store, credentials, token)
        observed = input_admission.materialize(store, token, outcomes)
        assert {job["id"] for job in observed} == members
        remaining -= members
    return [store.job(identifier) for identifier in identifiers]
