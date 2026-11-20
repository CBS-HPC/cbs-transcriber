"""Explicit synthetic native checks for the Settings browser regression cases."""

from cbs_transcriber.ucloud_native import MIG_PRODUCT


def mock_native_check(monkeypatch, *, key=None):
    calls = []

    def discover(settings, token, *, compute_id=None):
        if key is not None:
            assert token == key
        setup = settings.ucloud_transcriber.model_dump()
        existing = setup.get("product")
        # Recheck the saved allocation exactly; only fresh setup discovers the fixed MIG.
        assert compute_id == (existing["id"] if existing else MIG_PRODUCT["id"])
        setup.update(
            project_id=setup["project_id"] or "synthetic-project",
            application=setup["application"] or {"name": "transcriber", "version": "1.12"},
            product=existing or dict(MIG_PRODUCT),
            storage_path=setup["storage_path"] or "/drive/CBS-Transcriber",
        )
        calls.append((settings.model, token))
        return {
            "ready": True,
            "setup": setup,
            "choices": {},
            "note": "Synthetic connection checked.",
        }

    monkeypatch.setattr("cbs_transcriber.ucloud_discovery.discover", discover)
    return calls
