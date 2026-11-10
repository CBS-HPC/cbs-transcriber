"""Keep owned allocation journals recoverable after local failures and uncertain POSTs."""

from . import enhancement_cloud, providers, ucloud_native
from .store import now

TERMINAL = {"SUCCESS", "FAILURE", "EXPIRED", "ABORTED", "CANCELLED", "SUSPENDED"}


def uncertain(run):
    return bool(
        not run.get("job_id")
        and (run.get("submission_attempted") or run.get("submission_intent"))
        and not run.get("reconciled_not_created")
    )


def unresolved(job):
    run = job["params"].get("ucloud_run", {})
    return (
        run.get("cleanup_state") == "pending"
        or uncertain(run)
        or bool(
            run.get("job_id")
            and job["state"] != "completed"
            and run.get("remote_state") not in TERMINAL
            and not run.get("termination_requested")
            and not run.get("cancellation_requested")
        )
    )


def unresolved_for(store, job):
    """A shared native allocation is resolved only through its batch journal."""
    from . import restored_owners

    with store.connection() as db:
        if restored_owners.is_restored(db, "job", job["id"]):
            return False
    if job["params"].get("runtime_batch_id"):
        from . import runtime_store

        batch = runtime_store.for_job(store, job)
        return any(
            item["id"] == batch["id"]
            for item in runtime_store.obligations(store, project_id=batch["project_id"])
        )
    if job["params"].get("native_batch_id"):
        from . import native_batch

        return native_batch.unresolved(native_batch.batch_for_job(store, job))
    return unresolved(job)


def reconcile_batch(store, credentials, job, remote_id, not_created):
    from . import restored_owners

    restored_owners.require_job(store, job["id"])
    from . import native_batch

    batch = native_batch.batch_for_job(store, job)
    return reconcile_native_owner(store, credentials, batch["id"], remote_id, not_created)


def reconcile_native_owner(store, credentials, batch_id, remote_id, not_created):
    """Batch-level reconciliation also serves allocations whose recordings were deleted."""
    from . import restored_owners

    restored_owners.require_owner(store, "native_batch", batch_id)
    batch = store.native_batch(batch_id)
    run = batch["params"].get("ucloud_run", {})
    if batch["state"] not in {"failed", "interrupted", "cancelled"} or not uncertain(run):
        raise ValueError("Only an inactive, uncertain UCloud submission can be reconciled.")
    if bool(remote_id) == bool(not_created):
        raise ValueError(
            "Provide the owned remote job ID or explicitly confirm no job was created."
        )
    if not_created:
        receipt = {"reconciled_not_created": True, "reconciled_at": now()}
    else:
        settings = providers.Settings.model_validate(batch["params"]["settings"])
        with ucloud_native.client_for(
            settings, credentials.get(providers.credential_scope(settings))
        ) as client:
            remote = ucloud_native.cloud_call(client.retrieve_job, remote_id)
        if remote.get("specification", {}).get("name") != "CBS Transcriber batch " + batch["id"]:
            raise ValueError("That remote job's unique name does not match this local batch.")
        receipt = {
            "job_id": remote_id,
            "remote_state": remote.get("status", {}).get("state"),
            "reconciled_at": now(),
        }
    store.update_native_batch(
        batch["id"], run_update=receipt, message="UCloud submission reconciled."
    )


def reconcile(store, credentials, job, remote_id, not_created):
    from . import restored_owners

    restored_owners.require_job(store, job["id"])
    if job["params"].get("runtime_batch_id"):
        from . import runtime_batches

        if remote_id:
            raise ValueError("Runtime batches reconnect by their exact unique run name.")
        return runtime_batches.reconcile(
            store, credentials, job["params"]["runtime_batch_id"], confirm_not_created=not_created
        )
    if job["params"].get("native_batch_id"):
        return reconcile_batch(store, credentials, job, remote_id, not_created)
    run = job["params"].get("ucloud_run", {})
    if job["state"] not in {"failed", "interrupted", "cancelled"} or not uncertain(run):
        raise ValueError("Only an inactive, uncertain UCloud submission can be reconciled.")
    if bool(remote_id) == bool(not_created):
        raise ValueError(
            "Provide the owned remote job ID or explicitly confirm no job was created."
        )
    if not_created:
        run.update(reconciled_not_created=True, reconciled_at=now())
    else:
        if job["kind"] == "enhance" and "ucloud_api" in job["params"]:
            settings = providers.Settings.model_validate(job["params"]["ucloud_api"]["settings"])
            context = ucloud_native.client_for(
                settings, credentials.get(providers.credential_scope(settings))
            )
            expected = "CBS enhancement " + job["id"]
        elif job["kind"] == "enhance":
            settings = enhancement_cloud.CloudEnhancementSettings.model_validate(
                job["params"]["cloud_settings"]
            )
            context = enhancement_cloud.client_for(settings, credentials)
            expected = "CBS enhancement " + job["id"]
        else:
            settings = providers.Settings.model_validate(job["params"]["settings"])
            if settings.provider_type != "ucloud":
                raise ValueError("This job is not a native UCloud transcription.")
            context = ucloud_native.client_for(
                settings, credentials.get(providers.credential_scope(settings))
            )
            expected = "CBS Transcriber " + job["id"]
        with context as client:
            remote = ucloud_native.cloud_call(client.retrieve_job, remote_id)
        if remote.get("specification", {}).get("name") != expected:
            raise ValueError("That remote job's unique name does not match this local run.")
        run.update(
            job_id=remote_id,
            remote_state=remote.get("status", {}).get("state"),
            reconciled_at=now(),
        )
    store.update_job(job["id"], params=job["params"], message="UCloud submission reconciled.")
    if "ucloud_api" in job["params"] and run.get("capacity_reserved"):
        from . import runtime_store

        if not_created or run.get("remote_state") in TERMINAL:
            runtime_store.release_capacity(store, job["id"], legacy=True)
