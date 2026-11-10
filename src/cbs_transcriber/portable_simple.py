"""Resolve a user's existing UCloud connection for backup without choosing a model."""

from collections import Counter
from pathlib import PurePosixPath

from ucloud_workflow.resources import ManagementError
from ucloud_workflow.setup import list_writable_drives, prepare_storage_directory

from . import portable_cloud
from . import ucloud_native as native
from .providers import Settings, credential_scope, default_provider_id

BACKUP_FOLDER = "CBS-Transcriber-Backups"


def connection(store, credentials):
    """Resolve one saved identity; an existing target always retains its exact profile."""
    try:
        target, _, problem, revision = portable_cloud._load_target(store)
    except (
        portable_cloud.MissingBackupConnection,
        portable_cloud.UnavailableBackupConnection,
    ) as exc:
        target, revision = None, exc.storage_revision
        problem = (
            "The previous backup connection is unavailable. "
            "Review the new location before backing up."
        )
    rows = store.all("SELECT id,value FROM providers ORDER BY created,id LIMIT 501")
    if len(rows) > 500:
        raise ValueError("Too many saved connections. Review UCloud Settings first.")
    profiles = []
    for row in rows:
        settings = Settings.model_validate_json(row["value"])
        setup = settings.ucloud_transcriber
        if settings.provider_type == "ucloud" and setup and setup.project_id:
            profiles.append((row["id"], settings))
    identities = {(item.base_url, item.ucloud_transcriber.project_id) for _, item in profiles}
    if not identities:
        raise ValueError("Connect UCloud in Settings before choosing a backup location.")
    if len(identities) != 1:
        raise ValueError(
            "More than one UCloud project is configured. Resolve this in Settings first."
        )
    if target is not None:
        # Never replace the profile pinned by old backup/transfer journals.
        matches = [
            (identifier, item)
            for identifier, item in profiles
            if identifier == target["provider_id"]
        ]
        if len(matches) != 1:
            raise ValueError("The saved backup connection is unavailable. Review UCloud Settings.")
        selected, settings = matches[0]
    else:
        preferred = default_provider_id(store)
        selected, settings = next(
            ((identifier, item) for identifier, item in profiles if identifier == preferred),
            profiles[0],
        )
    key = credentials.get(credential_scope(settings))
    if not key:
        raise ValueError("Connect the UCloud token in Settings before choosing a backup location.")
    same_identity = [
        item
        for _, item in profiles
        if item.base_url == settings.base_url
        and item.ucloud_transcriber.project_id == settings.ucloud_transcriber.project_id
    ]
    return selected, settings, key, target, problem, revision, same_identity


def locations(store, credentials):
    """List project-drive locations; this performs only UCloud metadata reads."""
    selected, settings, key, target, problem, revision, profiles = connection(store, credentials)
    try:
        with native.client_for(settings, key) as client:
            drives = native.cloud_call(list_writable_drives, client)
    except ManagementError:
        raise ValueError(
            "UCloud backup locations could not be listed. Check UCloud Settings."
        ) from None
    if not isinstance(drives, list) or len(drives) > 1000:
        raise ValueError("UCloud returned an invalid backup location list.")
    choices, seen = [], set()
    for drive in drives:
        if not isinstance(drive, dict) or not isinstance(drive.get("id"), str):
            raise ValueError("UCloud returned an invalid backup location.")
        identifier = drive["id"]
        if (
            not identifier
            or len(identifier) > 200
            or any(ord(char) < 33 or ord(char) == 127 for char in identifier)
        ):
            raise ValueError("UCloud returned an invalid backup location.")
        path = f"/{identifier}/{BACKUP_FOLDER}"
        if PurePosixPath(path).parts != ("/", identifier, BACKUP_FOLDER) or identifier in seen:
            raise ValueError("UCloud returned an invalid or duplicate backup location.")
        native.NativeSetup.safe_path(path)
        seen.add(identifier)
        specification = drive.get("specification", {})
        title = specification.get("title") if isinstance(specification, dict) else None
        if (
            not isinstance(title, str)
            or not title.strip()
            or len(title) > 200
            or any(ord(char) < 32 for char in title)
        ):
            title = identifier
        choices.append({"id": path, "label": title})
    counts = Counter(item["label"] for item in choices)
    for item in choices:
        if counts[item["label"]] > 1:
            item["label"] += " (" + PurePosixPath(item["id"]).parts[1] + ")"
    selected_path = None
    if target is not None:
        selected_path = target["parent"]
        if all(choice["id"] != selected_path for choice in choices):
            choices.insert(0, {"id": selected_path, "label": "Current backup folder"})
    else:
        configured_drives = {
            item.ucloud_transcriber.storage_path.split("/")[1]
            for item in profiles
            if item.ucloud_transcriber.storage_path
        }
        if len(configured_drives) == 1:
            proposed = f"/{next(iter(configured_drives))}/{BACKUP_FOLDER}"
            if any(choice["id"] == proposed for choice in choices):
                selected_path = proposed
        elif len(choices) == 1:
            selected_path = choices[0]["id"]
    return {
        "connection": {
            "provider_id": selected,
            "server": settings.base_url,
            "project_id": settings.ucloud_transcriber.project_id,
        },
        "storage_revision": revision,
        "target": target,
        "problem": problem,
        "locations": choices,
        "selected": selected_path,
    }


def prepare(store, credentials, *, connection, revision, location, confirmed):
    """Accept an explicitly confirmed location against a freshly resolved identity."""
    if confirmed is not True:
        raise ValueError("Confirm that the entire library may be stored at this UCloud location.")
    observed = locations(store, credentials)
    if observed["connection"] != connection or observed["storage_revision"] != revision:
        raise portable_cloud.StaleBackupStorage(
            "UCloud backup settings changed. Reopen Library and review the location again."
        )
    if location not in {item["id"] for item in observed["locations"]}:
        raise ValueError("Choose an available UCloud backup location.")
    target = observed["target"]
    if target and target["parent"] == location and target["whole_library_approved"]:
        return {**target, "storage_revision": revision}
    origins = [portable_cloud._origin(connection["server"])]
    if target and not observed["problem"]:
        # Prior reviewed hosts are retained only for the same saved connection.
        origins = target["allowed_transfer_origins"]
    value = {
        "provider_id": connection["provider_id"],
        "parent": location,
        "allowed_transfer_origins": origins,
        "whole_library_approved": True,
    }
    target, new_revision = portable_cloud.save_target(
        store,
        value,
        expected_server=connection["server"],
        expected_project_id=connection["project_id"],
        expected_revision=revision,
    )
    return {**target, "storage_revision": new_revision}


def ensure_folder(store, credentials, *, revision, fingerprint):
    """Prepare only our dedicated folder; preserve custom saved destinations."""
    target = portable_cloud.checked_target(store, revision, fingerprint)
    if not target["whole_library_approved"]:
        raise ValueError("Confirm the whole-library backup before preparing this folder.")
    path = target["parent"]
    dedicated = PurePosixPath(path).name == BACKUP_FOLDER and len(PurePosixPath(path).parts) == 3

    def guard():
        current = portable_cloud.checked_target(store, revision, fingerprint)
        if not current["whole_library_approved"]:
            raise ValueError("Whole-library backup approval was stopped.")

    class GuardedClient:
        def __init__(self, client):
            self.client = client

        def retrieve_file(self, *args, **kwargs):
            guard()
            return self.client.retrieve_file(*args, **kwargs)

        def browse_files(self, *args, **kwargs):
            guard()
            return self.client.browse_files(*args, **kwargs)

        def create_folder(self, *args, **kwargs):
            guard()
            return self.client.create_folder(*args, **kwargs)

    try:
        with portable_cloud._connection(store, credentials, target, upload=True) as client:
            # Folder creation is allowed only for the named backup folder at a drive root.
            missing = native.cloud_call(
                prepare_storage_directory, GuardedClient(client), path, create=dedicated
            )
            if missing and not dedicated:
                raise ValueError(
                    "The saved UCloud backup folder is unavailable. Check it in UCloud."
                )
    except ManagementError:
        raise ValueError(
            "The UCloud backup folder could not be checked. Check UCloud and retry; "
            "an interrupted folder creation may already have completed."
        ) from None
    portable_cloud.checked_target(store, revision, fingerprint)
    return {"ready": True}


def revoke(store, *, revision, fingerprint):
    """Stop authorizing new transfer requests without discarding the journal."""
    target = portable_cloud.checked_target(store, revision, fingerprint)
    value = {key: target[key] for key in portable_cloud.TARGET_FIELDS}
    value["whole_library_approved"] = False
    target, next_revision = portable_cloud.save_target(
        store,
        value,
        expected_server=target["server"],
        expected_project_id=target["project_id"],
        expected_revision=revision,
    )
    return {**target, "storage_revision": next_revision}
