"""Durable, explicitly configured UCloud SSH execution of the optional enhancement worker."""

import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from pydantic import BaseModel, Field, field_validator
from ucloud_workflow import Settings as CloudSettings
from ucloud_workflow import UCloudClient
from ucloud_workflow.jobs import (
    build_job_specification,
    extract_job_id,
    extract_ssh_command,
    parse_ssh_command,
)
from ucloud_workflow.transfer import SSH_TRANSPORT_OPTIONS

from .enhancement import (
    ERRORS,
    STAGES,
    WHISPERX_VERSION,
    child_environment,
    merge_output,
    read_output,
    stop_process,
)
from .media import Cancelled
from .providers import UCloudSetup
from .store import new_id
from .ucloud_native import cloud_call

HELPER = Path(__file__).with_name("enhancement_remote.py")
WORKER = Path(__file__).with_name("enhancement_worker.py")
TERMINAL = {"SUCCESS", "FAILURE", "EXPIRED", "ABORTED", "CANCELLED", "SUSPENDED"}


class CloudEnhancementSettings(BaseModel):
    server: str = "https://cloud.sdu.dk"
    project_id: str = Field(default="", max_length=200)
    template_job_id: str = Field(default="", max_length=200)
    remote_python: str = Field(default="/opt/whisperx/bin/python", max_length=2000)
    work_root: str = Field(default="/work/cbs-transcriber", max_length=1500)
    ssh_config: str = Field(default="", max_length=2000)
    ssh_identity: str = Field(default="", max_length=2000)
    approved_for_protected: bool = True

    @field_validator("server")
    @classmethod
    def server_root(cls, value):
        return UCloudSetup.validate_server(value)

    @field_validator("remote_python", "work_root")
    @classmethod
    def remote_path(cls, value):
        if (
            not re.fullmatch(r"/[A-Za-z0-9_./-]+", value)
            or value == "/"
            or any(p in {"", ".", ".."} for p in value.split("/")[1:])
        ):
            raise ValueError("Use an absolute Linux path without spaces or traversal.")
        return value

    @field_validator("ssh_config", "ssh_identity")
    @classmethod
    def local_path(cls, value):
        if value and (not Path(value).is_absolute() or "\n" in value or '"' in value):
            raise ValueError("Use an absolute local SSH file path.")
        return value


def load_cloud_settings(store):
    rows = store.all("SELECT value FROM settings WHERE id=4")
    return (
        CloudEnhancementSettings.model_validate_json(rows[0]["value"])
        if rows
        else CloudEnhancementSettings()
    )


def save_cloud_settings(store, settings):
    store.execute(
        "INSERT INTO settings(id,value) VALUES (4,?) "
        "ON CONFLICT(id) DO UPDATE SET value=excluded.value",
        (settings.model_dump_json(),),
    )


def token_scope(settings):
    return "ucloud-control:" + settings.server


def require_setup(settings, credentials):
    if not settings.project_id or not settings.template_job_id:
        raise ValueError("Set the UCloud enhancement project and SSH template first.")
    if not credentials.get(token_scope(settings)):
        raise ValueError("Save the UCloud API token for this enhancement server.")
    for value in (settings.ssh_config, settings.ssh_identity):
        if value and not Path(value).is_file():
            raise ValueError("The configured local SSH file does not exist.")


def require_approval(store, project_id, frozen):
    current = load_cloud_settings(store)
    if (
        store.one("SELECT classification FROM projects WHERE id=?", (project_id,))["classification"]
        == "standard"
    ):
        return
    if not current.approved_for_protected or current.model_dump(
        exclude={"approved_for_protected"}
    ) != frozen.model_dump(exclude={"approved_for_protected"}):
        raise ValueError(
            "This Protected project requires the current UCloud enhancement "
            "configuration to be approved."
        )


def client_for(settings, credentials):
    return UCloudClient(
        CloudSettings(
            server=settings.server,
            project=settings.project_id,
            token=credentials.get(token_scope(settings)),
        ),
        timeout=30,
    )


def template_specification(client, settings, hours, identifier):
    template = cloud_call(
        client.retrieve_job,
        settings.template_job_id,
        include_updates=False,
        include_application=True,
    )
    spec = template.get("specification", {})
    reference = spec.get("application", {})
    metadata = template.get("status", {}).get("resolvedApplication", {}).get("metadata", {})
    if (
        not reference.get("name")
        or "transcrib" in reference["name"].lower()
        or any(reference.get(k) != metadata.get(k) for k in ("name", "version"))
    ):
        raise ValueError("Choose a prepared SSH/Python template, separate from native Transcriber.")
    return build_job_specification(
        spec, hours=hours, name="CBS enhancement " + identifier, ssh_enabled=True
    )


def check_setup(settings, credentials):
    require_setup(settings, credentials)
    with client_for(settings, credentials) as client:
        spec = template_specification(client, settings, 1, "check")
    return {
        "application": spec["application"],
        "product": spec["product"],
        "note": "Template metadata only. SSH, the prepared runtime and cached models "
        "need run validation. No job was started.",
    }


def endpoint(command):
    try:
        user, host, port = parse_ssh_command(command)
        if (
            not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", user)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host)
            or not 1 <= int(port) <= 65535
        ):
            raise ValueError()
        return user + "@" + host, port
    except (ValueError, TypeError):
        raise ValueError("UCloud did not provide a valid SSH endpoint.") from None


def transport_arguments(settings, address, port, known_hosts, *, scp=False):
    args = [
        "scp" if scp else "ssh",
        *SSH_TRANSPORT_OPTIONS,
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f'UserKnownHostsFile="{known_hosts.as_posix()}"',
        "-P" if scp else "-p",
        port,
    ]
    if settings.ssh_config:
        args.extend(["-F", settings.ssh_config])
    if settings.ssh_identity:
        args.extend(["-i", settings.ssh_identity])
    return args if scp else [*args, address]


def transport(args, cancel, *, timeout=120, output=None, limit=32768):
    """No raw command/output in exceptions; bounded captures remain in the managed run folder."""
    stream = output.open("wb") if output else None
    process = None
    try:
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=stream if stream else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_environment()
            | (
                {"SSH_AUTH_SOCK": os.environ["SSH_AUTH_SOCK"]}
                if "SSH_AUTH_SOCK" in os.environ
                else {}
            ),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if cancel.wait(0.2):
                raise Cancelled()
            if time.monotonic() > deadline:
                raise ValueError(
                    "UCloud SSH transport timed out. Retry to reconnect to the owned job."
                )
            if output and output.stat().st_size > limit:
                raise ValueError("UCloud returned an oversized response.")
        if process.returncode:
            raise ValueError(
                "UCloud SSH transport failed. Check the configured key, SSH access "
                "and remote runtime."
            )
        if output and output.stat().st_size > limit:
            raise ValueError("UCloud returned an oversized response.")
        if cancel.is_set():
            raise Cancelled()
    finally:
        if process:
            stop_process(process)
        if stream:
            stream.close()


def terminate_owned(settings, credentials, remote_id):
    with client_for(settings, credentials) as client:
        stop_owned(client, remote_id)


def stop_owned(client, remote_id):
    remote = cloud_call(client.retrieve_job, remote_id)
    if remote.get("status", {}).get("state") not in TERMINAL:
        cloud_call(client.terminate_job, remote_id)


def run_cloud(worker, job, recording, folder, options):
    from . import restored_owners

    restored_owners.require_job(worker.store, job["id"])
    settings = CloudEnhancementSettings.model_validate(job["params"]["cloud_settings"])
    require_setup(settings, worker.credentials)
    run = job["params"].setdefault("ucloud_run", {})

    def checkpoint(**values):
        run.update(values)
        worker.store.update_job(job["id"], params=job["params"])

    def guard():
        if worker.cancel_event.is_set():
            raise Cancelled()
        require_approval(worker.store, recording["project_id"], settings)

    def message(text):
        worker.store.update_job(job["id"], message=text, progress=0.5)

    guard()
    with client_for(settings, worker.credentials) as client:
        if not run.get("job_id"):
            if run.get("submission_intent") and not run.get("reconciled_not_created"):
                raise ValueError(
                    "UCloud allocation outcome is uncertain. Reconcile it in UCloud "
                    "before starting another job."
                )
            spec = template_specification(
                client, settings, max(1, (options.timeout_minutes + 59) // 60), job["id"]
            )
            guard()
            checkpoint(submission_intent=True, reconciled_not_created=False)
            remote_id = extract_job_id(cloud_call(client.submit_job, spec))
            checkpoint(job_id=remote_id)
        remote_id = run["job_id"]
        deadline = time.monotonic() + options.timeout_minutes * 60
        try:
            message("Waiting for UCloud SSH…")
            while True:
                guard()
                remote = cloud_call(client.retrieve_job, remote_id, include_updates=True)
                state = remote.get("status", {}).get("state")
                if state != run.get("remote_state"):
                    checkpoint(remote_state=state)
                if state in TERMINAL:
                    raise ValueError(
                        "The owned UCloud allocation ended. Start a new enhancement; "
                        "the source result is preserved."
                    )
                command = extract_ssh_command(remote)
                if state == "RUNNING" and command:
                    break
                if time.monotonic() >= deadline:
                    raise ValueError(
                        "UCloud did not become ready within the time limit. Check the owned job."
                    )
                worker.cancel_event.wait(2)
            address, port = endpoint(command)
            known_hosts = worker.store.root / "ucloud-enhancement-known-hosts"
            ssh = transport_arguments(settings, address, port, known_hosts)
            scp = transport_arguments(settings, address, port, known_hosts, scp=True)
            remote_dir = settings.work_root + "/" + job["id"]

            def call(*argv, output=None, timeout=120, limit=32768):
                guard()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("UCloud monitoring limit reached. Retry to reconnect.")
                transport(
                    [*ssh, shlex.join(argv)],
                    worker.cancel_event,
                    output=output,
                    timeout=min(timeout, remaining),
                    limit=limit,
                )

            if not run.get("uploaded"):
                # UCloud can publish SSH before the endpoint accepts connections.
                for attempt_number in range(6):
                    guard()
                    try:
                        transport(
                            [*ssh, "true"],
                            worker.cancel_event,
                            timeout=min(25, max(0.1, deadline - time.monotonic())),
                        )
                        break
                    except ValueError:
                        if attempt_number == 5 or time.monotonic() >= deadline:
                            raise ValueError(
                                "UCloud SSH is not ready. Check the key and retry to reconnect."
                            ) from None
                        worker.cancel_event.wait(2)
                call("mkdir", "-p", "-m", "700", remote_dir)
                call("chmod", "700", remote_dir)
                for path in (WORKER, HELPER):
                    guard()
                    transport(
                        [*scp, str(path), address + ":" + remote_dir + "/" + path.name],
                        worker.cancel_event,
                    )
                doctor = folder / "cloud-doctor.json"
                call(
                    settings.remote_python,
                    "-I",
                    remote_dir + "/enhancement_worker.py",
                    "--doctor",
                    output=doctor,
                )
                if read_output(doctor).get("version") != WHISPERX_VERSION or not read_output(
                    doctor
                ).get("ready"):
                    raise ValueError(
                        "The UCloud runtime needs the supported WhisperX version. "
                        "No recording was uploaded."
                    )
                message("Uploading enhancement audio and frozen text to UCloud…")
                for name in ("request.json", "audio.wav"):
                    guard()
                    transport(
                        [
                            *scp,
                            str(folder / name),
                            address + ":" + remote_dir + "/" + name + ".pending",
                        ],
                        worker.cancel_event,
                        timeout=900,
                    )
                    call("mv", remote_dir + "/" + name + ".pending", remote_dir + "/" + name)
                checkpoint(uploaded=True)
            attempt = new_id()
            call(
                settings.remote_python,
                "-I",
                remote_dir + "/enhancement_remote.py",
                "start",
                remote_dir,
                attempt,
            )
            started = time.monotonic()
            while True:
                if time.monotonic() >= deadline:
                    raise ValueError(
                        "UCloud enhancement exceeded its monitoring limit. "
                        "Retry to reconnect to the owned job."
                    )
                path = folder / "cloud-status.json"
                call(
                    settings.remote_python,
                    "-I",
                    remote_dir + "/enhancement_remote.py",
                    "status",
                    remote_dir,
                    ",".join(
                        name
                        for name, enabled in (
                            ("alignment", options.align),
                            ("diarization", options.diarize),
                        )
                        if enabled
                    ),
                    output=path,
                )
                status = read_output(path)
                if status.get("state") == "complete":
                    break
                if status.get("state") == "pending" and time.monotonic() - started > 30:
                    raise ValueError(
                        "The remote worker did not start. Check the prepared runtime and retry."
                    )
                if status.get("state") == "failed" and (
                    status.get("generation") == attempt or time.monotonic() - started > 20
                ):
                    raise ValueError(
                        ERRORS.get(status.get("code"), ERRORS["processing"]).replace(
                            "Local", "UCloud"
                        )
                    )
                message(STAGES.get(status.get("stage"), "Running UCloud enhancement…"))
                worker.cancel_event.wait(2)
            for name, enabled in (("alignment", options.align), ("diarization", options.diarize)):
                if enabled:
                    output = folder / (name + ".cloud.pending")
                    call(
                        settings.remote_python,
                        "-I",
                        remote_dir + "/enhancement_remote.py",
                        "read-stage",
                        remote_dir,
                        name,
                        output=output,
                        limit=32 * 1024 * 1024,
                    )
                    read_output(output)
                    output.replace(folder / (name + ".json"))
            try:
                merge_output(
                    job["params"]["source_snapshot"],
                    read_output(folder / "alignment.json") if options.align else None,
                    read_output(folder / "diarization.json") if options.diarize else None,
                    options,
                    recording["duration"],
                    job["id"][:8],
                )
            except ValueError:
                for name in ("alignment", "diarization"):
                    path = folder / (name + ".json")
                    if path.exists():
                        path.replace(folder / (name + ".invalid.json"))
                raise
            # Keep remote stage folders; only stop the allocation this run created.
            stop_owned(client, remote_id)
            checkpoint(termination_requested=True)
        except Cancelled:
            if not worker.stopping.is_set():
                stop_owned(client, remote_id)
                checkpoint(termination_requested=True)
            raise
