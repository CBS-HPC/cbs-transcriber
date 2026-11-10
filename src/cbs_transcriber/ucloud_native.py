"""Native batch Transcriber jobs using the released UCloud Workflow client."""

import json
import os
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, Field, field_validator, model_validator
from ucloud_workflow import Settings as CloudSettings
from ucloud_workflow import UCloudAPIError, UCloudClient
from ucloud_workflow.jobs import build_job_specification, extract_job_id
from ucloud_workflow.resources import ManagementError
from ucloud_workflow.setup import prepare_storage_directory
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from .catalog import provider_catalog, whisperx_model
from .media import Cancelled
from .transcripts import MAX_TRANSCRIPT_BYTES, parse_transcript

COMPUTE_ID = "gpu-nvidia-b200-1-gpu"
MIG_PRODUCT = {
    "id": "gpu-nvidia-b200-1-mig.1g",
    "category": "gpu-nvidia-b200",
    "provider": "ucloud",
}
MIG_APPLICATION = {"name": "transcriber", "version": "1.12"}
MIG_MODEL = "large-v3-turbo"
DEFAULT_NATIVE_JOB_HOURS = 24
NATIVE_AUTO_LANGUAGE_ERROR = (
    "Native UCloud Transcriber cannot use the literal language 'auto'. Leave the override "
    "blank to retain the template/application choice, or select an explicit language."
)


def require_compute(settings):
    """Fresh native requests use an explicit qualified allocation; frozen runs stay exact."""
    setup = settings.ucloud_transcriber
    if setup and setup.product:
        if setup.product["id"] == COMPUTE_ID:
            return
        if (
            setup.product == MIG_PRODUCT
            and not whisperx_model(settings)
            and setup.application
            and not setup.template_job_id
        ):
            return
    raise ValueError(
        f"Connect UCloud to select the full {COMPUTE_ID} allocation. "
        "Or connect an exact eligible native 1g MIG allocation without a template."
    )


def require_connection(settings):
    """Runtime validates its own allocation; this is only the shared connection reference."""
    setup = settings.ucloud_transcriber
    if (
        setup
        and setup.product
        and (setup.product["id"] == COMPUTE_ID or setup.product == MIG_PRODUCT)
    ):
        return
    raise ValueError("Reconnect UCloud to resolve an exact supported connection product.")


class NativeSetup(BaseModel):
    project_id: str = Field(default="", max_length=200)
    template_job_id: str = Field(default="", max_length=200)
    storage_path: str = Field(default="", max_length=2000)
    application: dict[str, str] | None = None
    product: dict[str, str] | None = None
    cleanup_after_success: bool = False  # Preserve legacy configurations and queued runs.
    # Frozen legacy settings without hours must retain their original four-hour limit.
    hours: int = Field(default=4, ge=1, le=168)
    input_parameter: str = Field(default="input_file", max_length=200)
    output_parameter: str = Field(default="output_dir", max_length=200)
    model_parameter: str = Field(default="model", max_length=200)
    language_parameter: str = Field(default="language", max_length=200)
    format_parameter: str = Field(default="output_format", max_length=200)

    @field_validator("application", "product")
    @classmethod
    def safe_reference(cls, value, info):
        expected = (
            {"name", "version"}
            if info.field_name == "application"
            else {"id", "category", "provider"}
        )
        if value is not None and (
            set(value) != expected
            or any(
                not v.strip() or len(v) > 200 or any(ord(c) < 32 for c in v) for v in value.values()
            )
        ):
            raise ValueError("Use the exact application or compute reference returned by UCloud.")
        return value

    @field_validator("storage_path")
    @classmethod
    def safe_path(cls, value):
        if value and (
            not value.startswith("/")
            or len(value.split("/")) < 3
            or any(p in {"", ".", ".."} for p in value.split("/")[1:])
            or "\\" in value
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("Use an existing UCloud folder such as /drive-id/transcriber.")
        return value

    @model_validator(mode="after")
    def distinct_parameters(self):
        names = [
            getattr(self, field)
            for field in (
                "input_parameter",
                "output_parameter",
                "model_parameter",
                "language_parameter",
                "format_parameter",
            )
            if getattr(self, field)
        ]
        if len(names) != len(set(names)):
            raise ValueError("Each Transcriber parameter must have a different name.")
        return self


def require_setup(settings, key):
    setup = settings.ucloud_transcriber
    if not key:
        raise ValueError("Add the UCloud API token in Settings.")
    if not setup or not all(
        (
            setup.project_id,
            setup.template_job_id or (setup.application and setup.product),
            setup.storage_path,
            setup.input_parameter,
            setup.output_parameter,
            setup.model_parameter,
        )
    ):
        raise ValueError(
            "Connect the UCloud token to discover a project, Transcriber Batch app and storage."
        )


def client_for(settings, key, *, timeout=30):
    # Construct directly: from_env() would read another environment and choose CPU defaults.
    return UCloudClient(
        CloudSettings(
            server=settings.base_url,
            token=key,
            project=settings.ucloud_transcriber.project_id,
        ),
        timeout=timeout,
    )


def cloud_call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except UCloudAPIError as exc:
        raise ValueError(
            f"UCloud request failed (HTTP {exc.status_code}). Check token, project, "
            "template permissions and provider availability."
        ) from None
    except httpx.HTTPError:
        raise ValueError(
            "UCloud connection failed. Check the server and retry explicitly."
        ) from None


def application_parameters(application):
    invocation = application.get("invocation")
    if not isinstance(invocation, dict):
        raise ValueError("Malformed Transcriber invocation metadata.")
    parameters = invocation.get("parameters")
    if parameters is None:
        parameters = []
    if not isinstance(parameters, list) or any(
        not isinstance(p, dict) or not isinstance(p.get("name"), str) or not p["name"]
        for p in parameters
    ):
        raise ValueError("Malformed Transcriber application parameters.")
    names = {p["name"]: p for p in parameters}
    if len(names) != len(parameters):
        raise ValueError("Ambiguous Transcriber application parameter names.")
    return names


def batch_overrides(application, parameters):
    """Accept genuine Batch apps or the verified Transcriber Batch web-capable flavor."""
    metadata = application.get("metadata", {})
    kind = application.get("invocation", {}).get("applicationType")
    if (
        kind in {"WEB", "BATCH"}
        and metadata.get("name") == "transcriber"
        and metadata.get("flavorName") == "Batch"
        and parameters.get("jupyterLab", {}).get("type") == "boolean"
    ):
        return {"jupyterLab": {"type": "boolean", "value": False}}
    if kind == "BATCH":
        return {}
    raise ValueError("Choose a verified noninteractive Transcriber Batch application.")


def template_info(client, settings):
    setup = settings.ucloud_transcriber
    if setup.template_job_id:
        job = cloud_call(
            client.retrieve_job,
            setup.template_job_id,
            include_updates=False,
            include_application=True,
        )
        specification = job.get("specification", {})
        application = job.get("status", {}).get("resolvedApplication", {})
    else:
        application = cloud_call(
            client.request,
            "GET",
            "/api/hpc/apps/byNameAndVersion",
            params={
                "appName": setup.application["name"],
                "appVersion": setup.application["version"],
            },
        ).json()
        specification = {
            "application": setup.application,
            "product": setup.product,
            "replicas": 1,
            "parameters": {},
            "resources": [],
        }
    reference, metadata = specification.get("application", {}), application.get("metadata", {})
    if (
        not reference.get("name")
        or "transcrib" not in reference["name"].lower()
        or any(reference.get(k) != metadata.get(k) for k in ("name", "version"))
    ):
        raise ValueError("Choose a Transcriber Batch template with matching application metadata.")
    parameters = application_parameters(application)
    overrides = batch_overrides(application, parameters)
    specification = deepcopy(specification)
    if (
        setup.template_job_id
        and setup.product
        and setup.product["id"] in {COMPUTE_ID, MIG_PRODUCT["id"]}
        and specification.get("product") != setup.product
    ):
        raise ValueError("The template's compute no longer matches the configured B200 allocation.")
    if not setup.template_job_id:
        for name, parameter in parameters.items():
            default = parameter.get("defaultValue")
            if isinstance(default, dict):
                default = default.get("value")
            kind = parameter.get("type")
            types = {
                "boolean": "boolean",
                "text": "text",
                "textarea": "text",
                "enumeration": "text",
                "integer": "integer",
                "floating_point": "floating_point",
            }
            if default is not None and kind in types:
                specification["parameters"][name] = {"type": types[kind], "value": default}
    specification.setdefault("parameters", {}).update(overrides)
    return specification, parameters


def parameter_value(parameters, name, value, *, file=False):
    parameter = parameters.get(name, {})
    kind = parameter.get("type")
    if file:
        if kind not in {"input_file", "input_directory"}:
            raise ValueError(f"UCloud parameter '{name}' must be a declared file/directory input.")
        return {"type": "file", "path": value}
    if kind not in {"text", "textarea", "enumeration"}:
        raise ValueError(f"UCloud parameter '{name}' must be declared as text or enumeration.")
    if kind == "enumeration" and value not in {
        p.get("value") for p in parameter.get("options", [])
    }:
        raise ValueError(f"The selected value is not offered by UCloud parameter '{name}'.")
    return {"type": "text", "value": value}


def build_specification(settings, template, parameters, folder, suffix, identifier):
    if settings.language.strip().casefold() == "auto":
        raise ValueError(NATIVE_AUTO_LANGUAGE_ERROR)
    setup = settings.ucloud_transcriber
    specification = build_job_specification(
        template,
        hours=setup.hours,
        name="CBS Transcriber " + identifier,
        ssh_enabled=False,
    )
    values = specification.setdefault("parameters", {})
    values[setup.input_parameter] = parameter_value(
        parameters,
        setup.input_parameter,
        folder + "/recording" + suffix,
        file=True,
    )
    if parameters[setup.input_parameter]["type"] != "input_file":
        raise ValueError("The configured input parameter must accept a single file.")
    values[setup.output_parameter] = parameter_value(
        parameters,
        setup.output_parameter,
        folder + "/out",
        file=True,
    )
    model_value = settings.model
    model_field = parameters.get(setup.model_parameter, {})
    if model_field.get("type") == "enumeration":
        offered = {p.get("value") for p in model_field.get("options", [])}
        entry = next(
            (
                m
                for m in provider_catalog()["ucloud_transcriber"]["models"]
                if m["model"] == settings.model
            ),
            {},
        )
        for candidate in [settings.model, *entry.get("aliases", [])]:
            if candidate not in offered:
                continue
            if candidate == "parakeet" and candidate != settings.model:
                # The unversioned token is app-specific; its label must prove v3.
                labels = {
                    option.get("name")
                    for option in model_field.get("options", [])
                    if option.get("value") == candidate
                }
                if settings.model != "nvidia/parakeet-tdt-0.6b-v3" or not labels <= {
                    settings.model,
                    "parakeet-tdt-0.6b-v3",
                }:
                    continue
            model_value = candidate
            break
    values[setup.model_parameter] = parameter_value(parameters, setup.model_parameter, model_value)
    if settings.language:
        values[setup.language_parameter] = parameter_value(
            parameters,
            setup.language_parameter,
            settings.language,
        )
    language = values.get(setup.language_parameter)
    language = language.get("value") if isinstance(language, dict) else language
    if isinstance(language, str) and language.strip().casefold() == "auto":
        raise ValueError(NATIVE_AUTO_LANGUAGE_ERROR)
    if setup.format_parameter:
        values[setup.format_parameter] = parameter_value(parameters, setup.format_parameter, "json")
    # Do not run inherited initialization scripts or a second input directory accidentally.
    for name, parameter in parameters.items():
        if name in {setup.input_parameter, setup.output_parameter}:
            continue
        if parameter.get("type") in {"input_file", "input_directory"} and name in values:
            raise ValueError(
                "Remove extra input/initialization parameters from the batch template."
            )
        if not parameter.get("optional", True) and name not in values:
            raise ValueError(
                "The Batch application needs an additional parameter. Use Advanced settings."
            )
    return specification


def build_directory_specification(
    settings, template, parameters, folder, identifier, directory_parameter=None
):
    """Use the declared multi-file Batch input without changing frozen single-file jobs."""
    setup = settings.ucloud_transcriber
    if directory_parameter is None:
        candidates = [
            name
            for name in ("input_dir", "inputDir")
            if parameters.get(name, {}).get("type") == "input_directory"
            and name != setup.output_parameter
        ]
        if len(candidates) != 1:
            raise ValueError(
                "This Transcriber Batch application needs one unambiguous input directory. "
                "Reconnect UCloud to use multi-file transcription."
            )
        directory_parameter = candidates[0]
    if parameters.get(directory_parameter, {}).get(
        "type"
    ) != "input_directory" or directory_parameter in {
        setup.input_parameter,
        setup.output_parameter,
    }:
        raise ValueError("The frozen Transcriber Batch input directory is no longer available.")
    clean_template = deepcopy(template)
    inherited = clean_template.setdefault("parameters", {})
    # Both are exclusively owned input selections. All other inherited inputs/scripts still fail.
    inherited.pop(directory_parameter, None)
    inherited.pop(setup.input_parameter, None)
    specification = build_specification(
        settings, clean_template, parameters, folder, ".wav", identifier
    )
    values = specification["parameters"]
    values.pop(setup.input_parameter)
    values[directory_parameter] = parameter_value(
        parameters, directory_parameter, folder + "/input", file=True
    )
    specification["name"] = "CBS Transcriber batch " + identifier
    return specification, directory_parameter


def check_setup(settings, key):
    require_setup(settings, key)
    with client_for(settings, key) as client:
        template, parameters = template_info(client, settings)
        # Validate all overrides and existing storage without creating folders or jobs.
        issue = ""
        try:
            require_compute(settings)
            build_specification(
                settings,
                template,
                parameters,
                settings.ucloud_transcriber.storage_path + "/check",
                ".wav",
                "check",
            )
        except ValueError as exc:
            issue = str(exc)
        verify_storage(
            client,
            settings.ucloud_transcriber.storage_path,
            allow_missing=not settings.ucloud_transcriber.template_job_id,
        )
        return {
            "ready": not issue,
            "issue": issue,
            "application": template["application"],
            "product": template["product"],
            "parameters": [
                {"name": p["name"], "type": p["type"], "options": p.get("options", [])}
                for p in parameters.values()
            ],
            "note": "Application and storage are accessible. No audio uploaded or job started. "
            "A live recording is still needed to validate execution and output.",
        }


def verify_storage(client, path, *, create=False, allow_missing=False):
    try:
        if prepare_storage_directory(client, path, create=create) and not (create or allow_missing):
            raise ValueError("Create the chosen UCloud storage folder before transcribing.")
    except ManagementError:
        raise ValueError(
            "UCloud storage is not a verified writable directory. Check access."
        ) from None


# "deleted" is a legacy receipt; current providers only support moving folders to Trash.
CLEANED = {"deleted", "trashed"}
TRASH_CONFIRM_DELAYS = (0, 0.5, 1, 2, 4, 8)


class CleanupError(ValueError):
    """A local, credential-free explanation of a failed cleanup stage."""

    def __init__(self, message, stage, code, http_status=None):
        super().__init__(message)
        self.stage, self.code, self.http_status = stage, code, http_status


def cleanup_diagnostic(exc, stage):
    """Persist only our messages and safe status codes, never upstream exception bodies."""
    status = None
    if isinstance(exc, CleanupError):
        stage, code, status, message = exc.stage, exc.code, exc.http_status, str(exc)
    elif isinstance(exc, UCloudAPIError):
        status, code = exc.status_code, "http_error"
        message = f"UCloud cleanup failed during {stage} (HTTP {status}). Check access and retry."
    elif isinstance(exc, httpx.TimeoutException):
        code, message = (
            "connection_timeout",
            "UCloud cleanup connection timed out. Retry explicitly.",
        )
    elif isinstance(exc, httpx.HTTPError):
        code, message = (
            "connection_failed",
            "UCloud cleanup connection failed. Check access and retry.",
        )
    elif isinstance(exc, OSError):
        code = "local_file_error"
        message = (
            "The saved local response could not be read or flushed. Check disk access and retry."
        )
    else:
        code = "validation_failed"
        message = {
            "setup": "UCloud cleanup setup is incomplete. Reconnect in Settings.",
            "confirmation": "Remote folder removal could not be confirmed. Check UCloud and retry.",
        }.get(
            stage,
            "Remote cleanup requirements could not be verified. Check this run before retrying.",
        )
    return {
        # Refusing an unauthorized/unowned cleanup must not manufacture an obligation.
        # Omitting the field preserves both a retained receipt and an existing pending one.
        **({"cleanup_state": "pending"} if stage != "preconditions" else {}),
        "cleanup_stage": stage,
        "cleanup_error_code": code,
        "cleanup_http_status": status,
        "cleanup_error": message,
    }


def folder_listed(client, folder):
    """Confirm by listing the parent: a missing path returns HTTP 400, not 404."""
    parent, seen, token = str(PurePosixPath(folder).parent), set(), None
    for _ in range(200):
        page = client.browse_files(parent, items_per_page=250, next_page=token)
        items = page.get("items")
        if not isinstance(items, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]
            for item in items
        ):
            raise ValueError("UCloud returned malformed folder metadata.")
        if any(item["id"] == folder for item in items):
            return True
        token = page.get("next")
        if not token:
            return False
        if not isinstance(token, str) or token in seen:
            raise ValueError("UCloud returned invalid folder pagination.")
        seen.add(token)
    raise ValueError("The UCloud storage folder is too large to confirm cleanup.")


def trash_owned_folder(client, folder, wait=time.sleep):
    """Move one owned run folder to its drive's Trash; the user empties Trash in UCloud.

    UCloud storage providers implement no permanent per-file DELETE, so the app never purges.
    """
    try:
        client.request("POST", "/api/files/trash", json={"items": [{"id": folder}]})
        refused = None
    except UCloudAPIError as exc:
        # A previous attempt may already have moved it; absence below settles that case.
        refused = exc.status_code
    for delay in TRASH_CONFIRM_DELAYS:
        wait(delay)
        try:
            listed = folder_listed(client, folder)
        except (ValueError, UCloudAPIError, httpx.HTTPError) as exc:
            diagnostic = cleanup_diagnostic(exc, "confirmation")
            raise CleanupError(
                diagnostic["cleanup_error"],
                diagnostic["cleanup_stage"],
                diagnostic["cleanup_error_code"],
                diagnostic["cleanup_http_status"],
            ) from None
        if not listed:
            return
        if refused is not None:
            break
    if refused is not None:
        raise CleanupError(
            f"UCloud refused to move the run folder to Trash (HTTP {refused}).",
            "trash",
            "http_error",
            refused,
        )
    raise CleanupError(
        "The run folder is still listed in UCloud after moving it to Trash.",
        "confirmation",
        "folder_still_listed",
    )


def cleanup_owned(store, credentials, job):
    """Trash only an owned successful run after its result and raw data are durable."""
    from . import restored_owners

    restored_owners.require_job(store, job["id"])
    from .providers import Settings, credential_scope
    from .store import now

    params, identifier = job["params"], job["id"]
    run = params.get("ucloud_run", {})
    if run.get("cleanup_state") in CLEANED:
        return run["cleanup_state"]
    params["ucloud_run"] = run
    stage = "preconditions"
    run["cleanup_attempted_at"] = now()
    try:
        settings = Settings.model_validate(params["settings"])
        folder = settings.ucloud_transcriber.storage_path + "/cbs-" + identifier
        if (
            settings.provider_type != "ucloud"
            or not settings.ucloud_transcriber.cleanup_after_success
            or run.get("remote_state") != "SUCCESS"
            or not run.get("job_id")
            or not run.get("folders_created")
            or run.get("owned_folder") != folder
        ):
            raise CleanupError(
                "Remote cleanup requires a successful run and its exact owned folder.",
                stage,
                "ownership_or_success_missing",
            )
        stage = "local_validation"
        raw_path = store.directory(job["recording_id"]) / ("ucloud-" + identifier + ".json")
        if not store.all("SELECT id FROM transcript_results WHERE job_id=?", (identifier,)):
            raise CleanupError(
                "Save the local transcript before remote cleanup.", stage, "result_missing"
            )
        if not raw_path.is_file():
            raise CleanupError(
                "The saved local response is missing. Remote files were not moved.",
                stage,
                "local_response_missing",
            )
        try:
            if raw_path.stat().st_size > MAX_TRANSCRIPT_BYTES:
                raise ValueError()
            parse_transcript(raw_path.read_bytes(), "transcript.json")
        except ValueError:
            raise CleanupError(
                "The saved local response is invalid. Remote files were not moved.",
                stage,
                "local_response_invalid",
            ) from None
        run["cleanup_state"] = "pending"
        store.update_job(identifier, params=params)
        stage = "local_flush"
        # Flush validated bytes and their rename before removing the remote recovery copy.
        with raw_path.open("r+b") as saved:
            os.fsync(saved.fileno())
        if os.name != "nt":
            descriptor = os.open(raw_path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        stage = "setup"
        key = credentials.get(credential_scope(settings))
        require_setup(settings, key)
        with client_for(settings, key) as client:
            stage = "trash"
            trash_owned_folder(client, folder)
    except (OSError, ValueError, UCloudAPIError, httpx.HTTPError) as exc:
        run.update(cleanup_diagnostic(exc, stage))
        store.update_job(identifier, params=params)
        if stage in {"preconditions", "local_validation"}:
            raise
        return "pending"
    run["cleanup_state"] = "trashed"
    run["cleanup_stage"] = "complete"
    for field in ("cleanup_error", "cleanup_error_code", "cleanup_http_status"):
        run.pop(field, None)
    store.update_job(identifier, params=params)
    return "trashed"


def transfer_url(server, endpoint):
    url = urljoin(server + "/", endpoint)
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("UCloud returned an unsupported file-transfer endpoint.")
    return url


def one_response(response):
    values = response.get("responses", [])
    if len(values) != 1 or not isinstance(values[0], dict):
        raise ValueError("UCloud returned a malformed file-transfer session.")
    return values[0]


def upload(
    client, server, path, target, guard, update, *, conflict_policy="REPLACE", endpoint_guard=None
):
    guard()
    session = one_response(
        cloud_call(
            client.request,
            "POST",
            "/api/files/upload",
            json={
                "items": [
                    {
                        "id": target,
                        "type": "FILE",
                        "supportedProtocols": ["WEBSOCKET_V2", "CHUNKED"],
                        "conflictPolicy": conflict_policy,
                    }
                ],
            },
        ).json()
    )
    if session.get("protocol") == "WEBSOCKET_V2":
        if endpoint_guard is not None:
            endpoint = session.get("endpoint", "")
            endpoint_guard(transfer_url(server, endpoint.replace("wss://", "https://", 1)))
        guard()
        websocket_upload(server, session, path, guard, update)
        guard()
        verify_upload(client, target, path.stat().st_size)
        return
    if session.get("protocol") != "CHUNKED" or not session.get("token"):
        raise ValueError("This UCloud provider does not offer a supported upload protocol.")
    endpoint = transfer_url(server, session["endpoint"])
    if endpoint_guard is not None:
        endpoint_guard(endpoint)
    total, offset = path.stat().st_size, 0
    # An independent client never forwards the control-plane Authorization header.
    with httpx.Client(timeout=120, follow_redirects=False) as transport, path.open("rb") as source:
        while data := source.read(8 * 1024 * 1024):
            guard()
            response = cloud_call(
                transport.post,
                endpoint,
                content=data,
                headers={
                    "Chunked-Upload-Token": session["token"],
                    "Chunked-Upload-Offset": str(offset),
                    "Chunked-Upload-Total-Size": str(total),
                    "Content-Type": "application/octet-stream",
                },
            )
            if not response.is_success:
                raise ValueError("UCloud audio upload failed. Retry explicitly.")
            offset += len(data)
            update(message="Uploading original to UCloud…", progress=0.05 + 0.2 * offset / total)
    guard()
    verify_upload(client, target, total)


def verify_upload(client, target, size):
    remote = cloud_call(client.retrieve_file, target)
    if remote.get("status", {}).get("sizeInBytes") != size:
        raise ValueError("UCloud uploaded file size could not be verified. No job was started.")


def websocket_upload(server, session, path, guard, update):
    # V2 authenticates through the issued session URL. Empty relative path denotes its file root.
    endpoint = session["endpoint"]
    if endpoint.startswith("wss://"):
        endpoint = "https://" + endpoint[len("wss://") :]
    endpoint = transfer_url(server, endpoint).replace("https://", "wss://", 1)
    stat = path.stat()
    listing = struct.pack(">BiqqI", 4, 0, stat.st_size, int(stat.st_mtime * 1000), 0)
    guard()
    try:
        with (
            ThreadPoolExecutor(max_workers=1) as sender,
            connect(
                endpoint,
                open_timeout=30,
                close_timeout=5,
                max_size=16384,
                ping_interval=20,
                ping_timeout=20,
            ) as socket,
        ):
            guard()
            socket.send(listing)
            deadline = time.monotonic() + 120
            sending = None

            def send_file():
                with path.open("rb") as source:
                    uploaded = 0
                    while data := source.read(1024 * 1024):
                        guard()
                        socket.send(struct.pack(">Bi", 2, 0) + data)
                        uploaded += len(data)
                        update(
                            message="Uploading original to UCloud…",
                            progress=0.05 + 0.2 * uploaded / stat.st_size,
                        )

            while time.monotonic() < deadline:
                guard()
                if sending is not None:
                    if sending.done():
                        sending.result()
                    else:
                        deadline = time.monotonic() + 120
                try:
                    frame = socket.recv(timeout=1)
                except TimeoutError:
                    continue
                if isinstance(frame, str):
                    continue  # Provider keepalive text carries no upload state.
                if len(frame) % 5:
                    raise ValueError("Malformed UCloud upload acknowledgment.")
                for offset in range(0, len(frame), 5):
                    opcode, value = struct.unpack_from(">Bi", frame, offset)
                    if opcode == 0 and value == 0 and sending is None:
                        # Receive acknowledgments while writing, avoiding transfer backpressure.
                        sending = sender.submit(send_file)
                    elif opcode == 5 and value == 1:
                        if sending is not None:
                            sending.result()
                        guard()
                        socket.send(bytes([6]))
                        return
                    elif opcode not in {0, 3, 5}:
                        raise ValueError("Unsupported UCloud upload acknowledgment.")
            raise ValueError("UCloud upload confirmation timed out. No job was started.")
    except (WebSocketException, OSError, TimeoutError):
        raise ValueError(
            "UCloud upload interrupted. No job was started; retry explicitly."
        ) from None


def download_output(client, server, folder, *, with_name=False):
    page = cloud_call(client.browse_files, folder, items_per_page=100)
    paths = [p.get("id", "") for p in page.get("items", [])]
    candidates = [
        p for p in paths if PurePosixPath(p).name in {"recording.json", "recording.wav.json"}
    ]
    if not candidates:
        candidates = [
            p for p in paths if p.endswith(".json") and "_merged" not in PurePosixPath(p).name
        ]
    if len(candidates) != 1 or not candidates[0].startswith(folder + "/") or page.get("next"):
        raise ValueError("UCloud output must contain one unambiguous original JSON transcript.")
    session = one_response(
        cloud_call(
            client.request, "POST", "/api/files/download", json={"items": [{"id": candidates[0]}]}
        ).json()
    )
    endpoint = transfer_url(server, session["endpoint"])
    try:
        with httpx.Client(timeout=120, follow_redirects=False) as transport:
            with transport.stream("GET", endpoint) as response:
                if not response.is_success:
                    raise ValueError("UCloud transcript download failed. Retry explicitly.")
                data = bytearray()
                for block in response.iter_bytes():
                    data.extend(block)
                    if len(data) > MAX_TRANSCRIPT_BYTES:
                        raise ValueError("UCloud transcript exceeds the supported import size.")
    except httpx.HTTPError:
        raise ValueError("UCloud transcript download interrupted. Retry explicitly.") from None
    return (bytes(data), PurePosixPath(candidates[0]).name) if with_name else bytes(data)


def capture_selector(settings, specification):
    """The frozen model/language actually submitted, used to match native outputs."""
    setup, values = settings.ucloud_transcriber, specification["parameters"]
    language = values.get(setup.language_parameter, {})
    language = language.get("value", "") if isinstance(language, dict) else ""
    return {"model": values[setup.model_parameter]["value"], "language": language or ""}


def capture_owned(settings, *extra):
    """Parameters this app sets itself; inherited free text is withheld from evidence."""
    setup = settings.ucloud_transcriber
    return {
        name
        for name in (
            setup.input_parameter,
            setup.output_parameter,
            setup.model_parameter,
            setup.language_parameter,
            setup.format_parameter,
            *extra,
        )
        if name
    }


def run_transcription(worker, job, recording, settings, key):
    from . import restored_owners

    restored_owners.require_job(worker.store, job["id"])
    from .providers import preserve_response, require_approval

    store, identifier = worker.store, job["id"]
    params = job["params"]
    from . import native_allocation

    execution = type(settings).model_validate(
        native_allocation.effective(settings.model_dump(), params)
    )
    run = params.setdefault("ucloud_run", {})
    directory = store.directory(recording["id"])
    suffix = PurePosixPath(recording["filename"]).suffix.lower()
    folder = settings.ucloud_transcriber.storage_path + "/cbs-" + identifier
    from . import native_capture

    # Only a policy frozen at fresh admission records evidence (ADR 0100); legacy runs keep
    # their exact journal, files and recovery behavior.
    capture = native_capture.enabled(job)

    def checkpoint(**values):
        run.update(values)
        store.update_job(identifier, params=params)

    def guard():
        if worker.cancel_event.is_set():
            raise Cancelled()
        require_approval(store, recording["project_id"], params.get("provider_id"), settings)

    require_setup(settings, key)
    try:
        with client_for(settings, key) as client:
            try:
                if not run.get("job_id"):
                    if run.get("submission_attempted") and not run.get("reconciled_not_created"):
                        raise ValueError(
                            "UCloud submission outcome is unknown. Check your UCloud "
                            "jobs before starting a new transcription; retry cannot allocate twice."
                        )
                    template, parameters = template_info(client, execution)
                    specification = build_specification(
                        execution,
                        template,
                        parameters,
                        folder,
                        suffix,
                        identifier,
                    )
                    verify_storage(
                        client,
                        settings.ucloud_transcriber.storage_path,
                        create=not settings.ucloud_transcriber.template_job_id,
                    )
                    guard()
                    if not run.get("folders_created"):
                        if not run.get("root_created"):
                            try:
                                client.create_folder(folder)
                            except UCloudAPIError as exc:
                                if exc.status_code == 409:
                                    raise ValueError(
                                        "The run folder exists without an ownership receipt. "
                                        "No upload started."
                                    ) from None
                                raise
                            checkpoint(owned_folder=folder, root_created=True)
                        try:
                            client.create_folder(folder + "/out")
                        except UCloudAPIError as exc:
                            if exc.status_code != 409:
                                raise
                        checkpoint(folders_created=True)
                    if not run.get("uploaded"):
                        original = directory / ("original" + suffix)
                        if "audio_input" in params:
                            from .audio_inputs import require_native, verify_original

                            require_native(params["audio_input"])
                            original = verify_original(store, recording, params["audio_input"])
                        upload(
                            client,
                            settings.base_url,
                            original,
                            folder + "/recording" + suffix,
                            guard,
                            lambda **fields: store.update_job(identifier, **fields),
                        )
                        if "audio_input" in params:
                            verify_original(store, recording, params["audio_input"])
                        checkpoint(uploaded=True)
                    guard()
                    if capture:
                        # Recorded before the POST; the dedicated writer owns these keys, so
                        # later whole-parameter checkpoints carry the exact stored copy.
                        params[native_capture.KEYS[1]] = native_capture.begin_submission(
                            store,
                            job,
                            submitted=native_capture.projection(
                                specification, parameters, capture_owned(settings)
                            ),
                            remote_name="recording" + suffix,
                            selector=capture_selector(settings, specification),
                        )
                        checkpoint(
                            artifact_submission_sha256=native_capture.submission_sha256(
                                params[native_capture.KEYS[1]]
                            )
                        )
                    checkpoint(submission_attempted=True, reconciled_not_created=False)
                    remote_id = extract_job_id(cloud_call(client.submit_job, specification))
                    checkpoint(job_id=remote_id)
                deadline = time.monotonic() + settings.ucloud_transcriber.hours * 3600 + 600
                while True:
                    guard()
                    remote = cloud_call(client.retrieve_job, run["job_id"])
                    state = remote.get("status", {}).get("state")
                    if state != run.get("remote_state"):
                        checkpoint(remote_state=state)
                    if state == "SUCCESS":
                        break
                    if state in {"FAILURE", "EXPIRED", "ABORTED", "CANCELLED", "SUSPENDED"}:
                        raise ValueError(f"UCloud job ended with {state}. Check the job in UCloud.")
                    if time.monotonic() >= deadline:
                        raise ValueError(
                            "UCloud monitoring timed out. Retry to check the same job."
                        )
                    store.update_job(
                        identifier, message="UCloud job · " + str(state or "waiting"), progress=0.4
                    )
                    worker.cancel_event.wait(5)
                guard()
                raw_path = directory / ("ucloud-" + identifier + ".json")
                observed = None
                if raw_path.exists():
                    # A preserved file without a published observation stays an explicit gap.
                    data = raw_path.read_bytes()
                elif capture:
                    data, name = download_output(
                        client, settings.base_url, folder + "/out", with_name=True
                    )
                    json.loads(data)
                    native_capture.write_output(raw_path, data)
                    observed = native_capture.observe_output(store, job, name)
                else:
                    data = download_output(client, settings.base_url, folder + "/out")
                    payload = json.loads(data)
                    preserve_response(raw_path, payload)
                segments = parse_transcript(data, "transcript.json")
                if capture:
                    params[native_capture.KEYS[1]] = native_capture.record_output(
                        store, job, observed, segments
                    )
                with worker.lock:
                    guard()
                    if (
                        settings.ucloud_transcriber.cleanup_after_success
                        and run.get("cleanup_state") not in CLEANED
                    ):
                        # Persist the obligation before the independent transcript transaction.
                        checkpoint(cleanup_state="pending")
                    store.add_result(
                        recording["id"],
                        segments,
                        {},
                        params["provider_name"],
                        {
                            "kind": "provider",
                            "provider_id": params.get("provider_id"),
                            **settings.model_dump(),
                            **(
                                {"native_allocation": params["native_allocation"]}
                                if "native_allocation" in params
                                else {}
                            ),
                            "ucloud_job_id": run["job_id"],
                        },
                        job_id=identifier,
                        native_artifacts=params[native_capture.KEYS[1]] if capture else None,
                    )
                if (
                    settings.ucloud_transcriber.cleanup_after_success
                    and run.get("cleanup_state") not in CLEANED
                ):
                    if not worker.cancel_event.is_set():
                        cleanup_owned(store, worker.credentials, store.job(identifier))
                elif not settings.ucloud_transcriber.cleanup_after_success:
                    checkpoint(cleanup_state="retained")
            except Cancelled:
                if run.get("job_id") and not worker.stopping.is_set():
                    cloud_call(client.terminate_job, run["job_id"])
                    checkpoint(cancellation_requested=True)
                raise
    except UCloudAPIError as exc:
        raise ValueError(
            f"UCloud operation failed (HTTP {exc.status_code}). Check setup."
        ) from None
    except (httpx.HTTPError, RuntimeError):
        raise ValueError("UCloud operation failed. Check the remote job before retrying.") from None


def terminate_owned(settings, credentials, remote_id):
    from .providers import credential_scope

    key = credentials.get(credential_scope(settings))
    require_setup(settings, key)
    with client_for(settings, key) as client:
        cloud_call(client.terminate_job, remote_id)
