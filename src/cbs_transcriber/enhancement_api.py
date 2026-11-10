"""API-only UCloud enhancement on the Settings connection; no SSH, no uploaded tokens.

A per-run supervisor script starts through the Transcriber Batch initialization input. It reports
runtime/model readiness before any private audio is uploaded, verifies the uploaded inputs by
hash and runs the existing source-preserving worker. The app talks to it only through files in
its owned run folder, read and written with the UCloud file API.
"""

import hashlib
import json
import math
import re
import threading
import time
from pathlib import Path, PurePosixPath

import httpx
from ucloud_workflow import UCloudAPIError
from ucloud_workflow.jobs import extract_job_id

from . import providers, runtime_compute
from . import ucloud_native as native
from .enhancement import HF_SCOPE
from .enhancement_worker import (
    DANISH_CHOICES,
    MODELS,
    WHISPER_MODELS,
    WHISPERX_VERSION,
    danish_module,
    danish_scope,
    snapshot_folder,
)
from .media import Cancelled

HERE = Path(__file__).parent
REQUIREMENTS = HERE / "catalogs" / "ucloud_whisperx_runtime.txt"
SUPERVISOR = HERE / "ucloud_supervisor.sh"
WORKER_FILES = (
    HERE / "enhancement_worker.py",
    HERE / "enhancement_remote.py",
    HERE / "danish_models.py",
    HERE / "remote_audio_input.py",
    HERE / "catalogs" / "danish_whisper_models.json",
)
UV_URL = (
    "https://github.com/astral-sh/uv/releases/download/0.9.29/uv-x86_64-unknown-linux-gnu.tar.gz"
)
UV_SHA256 = "1ce5212f8f42dc7427a1bd3db4168d6d1abcf81b38d8c82a5b9d0ddc54ceebfc"
PYTHON_VERSION = "3.12"
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
MANIFEST_WAIT_SECONDS = 3600
PREPARE_SECONDS = 3600
POLL_SECONDS = 5
MAX_TRANSIENT = 12
MAX_READINESS_READS = 6
MAX_STATUS_BYTES = 64 * 1024
MAX_STAGE_BYTES = 32 * 1024 * 1024
SPEAKERS_COMPLETE = ".cbs-complete"
# What speaker detection loads from the pinned pyannote snapshot (MODELS["speakers"]): size
# and SHA-256 per file. A partial or damaged download is fetched again, never copied (ADR 0057).
SPEAKER_FILES = {
    "config.yaml": (444, "5ce2bfa9a938dc132cec1172592d65173cbb8f444ea1e4133f10f9391de155be"),
    "embedding/pytorch_model.bin": (
        26646242,
        "6f10ff60898a1d185fa22e1d11e0bfa8a92efec811f11bca48cb8cafebefd929",
    ),
    "plda/plda.npz": (133852, "9b77bcd840692710dd3496f62ecfeed8d8e5f002fd991b785079b244eab7d255"),
    "plda/xvec_transform.npz": (
        134376,
        "325f1ce8e48f7e55e9c8aa47e05d2766b7c48c4b25b8de8dd751e7a4cc5fbe8f",
    ),
    "segmentation/pytorch_model.bin": (
        5906507,
        "7ad24338d844fb95985486eb1a464e32d229f6d7a03c9abe60f978bacf3f816e",
    ),
}
FETCH_ATTEMPTS = 3
FETCH_RETRY_SECONDS = 2
SEED_ATTEMPTS = 3
SEED_RETRY_SECONDS = 2
TERMINAL = {"SUCCESS", "FAILURE", "EXPIRED", "ABORTED", "CANCELLED", "SUSPENDED"}
STAGES = {
    "preparation": "UCloud · preparing original audio…",
    "loading": "UCloud · loading models…",
    "transcribing": "UCloud · transcribing with WhisperX…",
    "locating": "UCloud · locating wording in long audio…",
    "aligning": "UCloud · aligning words…",
    "speakers": "UCloud · detecting speakers…",
    "complete": "UCloud · saving enhanced result…",
}
FAILURES = {
    "input_preparation": "Original audio preparation failed validation before inference. "
    "The frozen source and available receipts were kept.",
    "runtime-lock-missing": "This prepared UCloud runtime needs its shared-use lock before "
    "starting another job. Prepare the runtime while no older allocation is active.",
    "runtime-missing": "The UCloud runtime is not prepared. Start the job again and confirm "
    "preparing the runtime when asked (one time per app version).",
    "runtime-busy": "The UCloud runtime may be in use or have a leftover preparation lock. "
    "Check Activity for ongoing preparation; if none is running, the lock needs investigation.",
    "runtime-prepare": "Preparing the UCloud runtime failed. See prepare.log in the run folder "
    "on UCloud; no recording was uploaded.",
    "runtime": "The UCloud runtime could not start WhisperX. Prepare the runtime again.",
    "manifest": "Uploaded enhancement inputs did not match their checksums. "
    "Start a new enhancement.",
    "manifest-timeout": "The UCloud job stopped waiting for inputs. Start a new enhancement.",
}


class RuntimeNotReady(ValueError):
    """The prepared runtime cannot run this enhancement; waiting longer will not help."""


class Transient(ValueError):
    """A connection problem; the owned remote job may still be fine, so keep it for reconnect."""


def requirements_bytes():
    # Hash normalized text: Windows checkouts may convert line endings.
    return REQUIREMENTS.read_bytes().replace(b"\r\n", b"\n")


def requirements_hash():
    return hashlib.sha256(requirements_bytes()).hexdigest()


def runtime_id():
    return f"whisperx-{WHISPERX_VERSION}-cu128-{requirements_hash()[:12]}"


def runtime_folder(settings):
    return settings.ucloud_transcriber.storage_path + "/cbs-runtime/" + runtime_id()


def run_folder(settings, identifier):
    return settings.ucloud_transcriber.storage_path + "/cbs-enhancement-" + identifier


def container_path(path):
    # Mounted folder resources appear at /work/<basename> inside UCloud jobs.
    return "/work/" + PurePosixPath(path).name


def connection(store, credentials, project_ids):
    """Choose the saved UCloud connection allowed for every affected project."""
    rows = store.all("SELECT id FROM providers ORDER BY rowid")
    default = providers.default_provider_id(store)
    candidates = []
    for row in rows:
        current = providers.profile(store, row["id"])
        settings = providers.Settings.model_validate(current["settings"])
        if settings.provider_type != "ucloud":
            continue
        try:
            native.require_setup(settings, credentials.get(providers.credential_scope(settings)))
            native.require_connection(settings)
        except ValueError:
            continue
        candidates.append((current["id"] != default, current, settings))
    if not candidates:
        raise ValueError(
            "Connect UCloud under Settings first. UCloud enhancement uses that connection."
        )
    refusal = None
    for _, current, settings in sorted(candidates, key=lambda item: item[0]):
        try:
            for project_id in project_ids:
                providers.require_approval(store, project_id, current["id"], settings)
        except ValueError as exc:
            refusal = exc
            continue
        return {"provider_id": current["id"], "settings": settings.model_dump()}
    raise refusal


def runtime_prepared(store, credentials):
    chosen = connection(store, credentials, [])
    settings = providers.Settings.model_validate(chosen["settings"])
    key = credentials.get(providers.credential_scope(settings))
    with native.client_for(settings, key) as client:
        prepared = stamped(client, settings)
    return {"runtime_id": runtime_id(), "prepared": prepared}


PREPARE_QUESTION = (
    "The CBS WhisperX runtime in your UCloud project drive is not prepared for this app version "
    "yet{detail}. Prepare it as part of this job? One time: pinned public packages and model "
    "weights (several GB, 20+ minutes) go into the project drive. No recording is uploaded "
    "until the runtime is ready."
)


def runtime_stamped(credentials, chosen):
    """Read-only: True/False, or None when UCloud could not be asked (then do not ask)."""
    settings = providers.Settings.model_validate(chosen["settings"])
    key = credentials.get(providers.credential_scope(settings))
    try:
        with native.client_for(settings, key) as client:
            return stamped(client, settings)
    except (ValueError, UCloudAPIError, httpx.HTTPError):
        return None


def freeze(store, credentials, project_ids, options):
    """Freeze a new enhancement's API execution identity; credentials stay in the OS store."""
    if not danish_scope(options.model_dump()):
        raise ValueError("Edda and Røst support Danish full-recording transcription only.")
    if options.allow_downloads:
        raise ValueError(
            "UCloud enhancement uses the prepared runtime's cached models. "
            "Use 'Prepare the UCloud runtime' instead of per-run downloads."
        )
    chosen = connection(store, credentials, project_ids)
    if options.diarize and not speakers_obtainable(credentials, chosen, options):
        raise ValueError(speaker_refusal(credentials))
    compute = runtime_compute.freeze(
        providers.Settings.model_validate(chosen["settings"]), credentials
    )
    from .runtime_batches import freeze_plan

    plan = freeze_plan(providers.Settings.model_validate(chosen["settings"]), credentials)
    return options.model_copy(update={"device": "cuda"}), {
        "ucloud_api": {
            **chosen,
            "runtime_id": runtime_id(),
            "prepare_runtime": options.prepare_runtime,
            **compute,
            "batch_plan": plan,
        }
    }


def freeze_profile(store, credentials, provider, project_ids, options):
    """Freeze an explicit UCloud model profile (T70 WhisperX models) like `freeze` does."""
    if not danish_scope(options.model_dump()):
        raise ValueError("Edda and Røst support Danish full-recording transcription only.")
    settings = providers.Settings.model_validate(provider["settings"])
    native.require_setup(settings, credentials.get(providers.credential_scope(settings)))
    native.require_connection(settings)
    for project_id in project_ids:
        providers.require_approval(store, project_id, provider["id"], settings)
    chosen = {"provider_id": provider["id"], "settings": settings.model_dump()}
    if options.diarize and not speakers_obtainable(credentials, chosen, options):
        raise ValueError(speaker_refusal(credentials))
    compute = runtime_compute.freeze(settings, credentials)
    from .runtime_batches import freeze_plan

    plan = freeze_plan(settings, credentials)
    return options.model_copy(update={"device": "cuda", "allow_downloads": False}), {
        "ucloud_api": {
            **chosen,
            "runtime_id": runtime_id(),
            "prepare_runtime": options.prepare_runtime,
            **compute,
            "batch_plan": plan,
        }
    }


def model_folder(settings, key):
    if key in DANISH_CHOICES:
        return runtime_folder(settings) + "/hf/hub/" + danish_module().relative_folder(key)
    repo, revision = MODELS[key]
    return (
        runtime_folder(settings)
        + "/hf/hub/models--"
        + repo.replace("/", "--")
        + "/snapshots/"
        + revision
    )


def converted_model_ready(client, settings, choice):
    """Read-only marker/inventory check; the inference worker verifies every artifact hash."""
    helper = danish_module()
    folder = model_folder(settings, choice)
    marker = read_json(client, settings.base_url, folder + "/" + helper.MARKER)
    if marker is None:
        return False
    receipt = helper.validate_receipt(marker, choice=choice, model=MODELS[choice])
    source_files = helper.models()[choice]["source_files"]
    manifest = json.dumps(
        sorted(source_files, key=lambda item: item["name"]),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if (
        receipt["source"]["manifest_sha256"] != hashlib.sha256(manifest).hexdigest()
        or receipt["recipe"]["contract_sha256"] != helper.RECIPE_CONTRACT_SHA256
    ):
        return False
    page = client.browse_files(folder, items_per_page=32)
    if not isinstance(page, dict):
        return False
    items = page.get("items")
    expected = {folder + "/" + item["name"]: item["bytes"] for item in receipt["files"]}
    if (
        not isinstance(items, list)
        or page.get("next")
        or len(items) != len(expected) + 1
        or any(
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not isinstance(item.get("status", {}), dict)
            for item in items
        )
        or {item.get("id") for item in items} != set(expected) | {folder + "/" + helper.MARKER}
    ):
        return False
    return all(
        type((item.get("status") or {}).get("sizeInBytes")) is int
        and (item["status"]["sizeInBytes"] == expected[item["id"]])
        for item in items
        if item["id"] in expected
    )


def models_ready(client, settings, options):
    """Read-only: the runtime stamp and this job's pinned public model folders exist."""
    return stamped(client, settings) and all(
        converted_model_ready(client, settings, name)
        if name in DANISH_CHOICES
        else listed(client, model_folder(settings, name))
        for name in prefetch_models(options)
    )


def whisperx_ready(credentials, provider, options):
    """Read-only: the runtime is prepared and holds this job's pinned models (and speakers)."""
    settings = providers.Settings.model_validate(provider["settings"])
    key = credentials.get(providers.credential_scope(settings))
    try:
        with native.client_for(settings, key) as client:
            ready = models_ready(client, settings, options)
    except (ValueError, UCloudAPIError, httpx.HTTPError):
        return False
    # A missing speaker model is fetched only with the same explicit preparation consent.
    return ready and (not options.diarize or speaker_model_available(credentials, provider))


# Request-time readiness (ADR 0098) never holds up a request: every probe shares one short
# deadline, and each UCloud read has its own short timeout.
PROBE_SECONDS = 8
PROBE_REQUEST_SECONDS = 4


def preparation_state(credentials, chosen, options):
    """Read-only: {"ready": bool, "speakers": bool | None}; UCloud errors propagate.

    "speakers" is None when the options need no speaker model."""
    settings = providers.Settings.model_validate(chosen["settings"])
    key = credentials.get(providers.credential_scope(settings))
    speakers = None
    if options.diarize:
        try:
            local_speaker_files()
            speakers = True
        except RuntimeNotReady:
            speakers = False
    with native.client_for(settings, key, timeout=PROBE_REQUEST_SECONDS) as client:
        prepared = models_ready(client, settings, options)
        if speakers is False:
            speakers = bool(stamped(client, settings)) and remote_speakers_complete(
                client, settings
            )
    return {"ready": bool(prepared) and speakers is not False, "speakers": speakers}


def preparation_states(credentials, targets, *, seconds=None):
    """Probe {key: (chosen, options)} concurrently within one deadline.

    A target that fails or does not answer in time maps to None: no readiness claim, so the
    request is accepted as before and background preflight still decides. Probe threads are
    daemons; a late answer is discarded and never uploads anything."""
    results = {}

    def probe(key, chosen, options):
        try:
            results[key] = preparation_state(credentials, chosen, options)
        except Exception:
            # Any failure to read the runtime only means "unknown" at request time.
            results[key] = None

    threads = [
        threading.Thread(
            target=probe, args=(key, *target), name="runtime-readiness-probe", daemon=True
        )
        for key, target in targets.items()
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + (PROBE_SECONDS if seconds is None else seconds)
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return {key: results.get(key) for key in targets}


GATED_TERMS = (
    "Hugging Face has not granted your token access to the gated speaker model "
    "pyannote/speaker-diarization-community-1. Sign in to Hugging Face with the account of "
    "your saved token, open https://huggingface.co/pyannote/speaker-diarization-community-1 and "
    "accept its terms, then choose Check token under Settings → Hugging Face. "
    "No recording was uploaded."
)


def speakers_obtainable(credentials, chosen, options):
    """Speakers run on UCloud: the weights exist, or an explicit preparation may fetch them
    to this computer with the saved Hugging Face token and copy them (never the token)."""
    if speaker_model_available(credentials, chosen):
        return True
    token = credentials.get(HF_SCOPE)
    if not (options.prepare_runtime and token):
        return False
    # Checked before any GPU job starts: a denied gated model fails here, not mid-run.
    from .huggingface_access import check_token

    access = check_token(token).get("speaker_model_access")
    if access == "denied":
        raise ValueError(GATED_TERMS)
    return True


def speaker_refusal(credentials):
    if not credentials.get(HF_SCOPE):
        return (
            "Speaker labels run on UCloud but need the gated pyannote speaker model once. Save "
            "a Hugging Face read token (Settings → Hugging Face) and accept the "
            "model's terms; the app then fetches it with your consent and copies it to UCloud."
        )
    return (
        "Speaker labels run on UCloud. The pyannote speaker model is not on your UCloud runtime "
        "yet: check 'Prepare the UCloud runtime' (or confirm preparation) so the app fetches it "
        "to this computer once and copies it to UCloud. Your token never leaves this computer."
    )


TOKEN_REJECTED = (
    "Hugging Face rejected the saved token. Save a valid read token under Settings → "
    "Hugging Face and choose Check token. No recording was uploaded."
)


def hub_status(exc):
    return getattr(getattr(exc, "response", None), "status_code", None)


def hub_refusal(exc):
    """Hugging Face said no (gated, unknown or unauthorised); retrying cannot help.

    Rate limiting (429) and server errors are not refusals: they are retried."""
    names = {c.__name__ for c in type(exc).__mro__}
    status = hub_status(exc)
    return bool(
        names & {"GatedRepoError", "RepositoryNotFoundError", "RevisionNotFoundError"}
        or (status is not None and 400 <= status < 500 and status != 429)
    )


def fetch_speaker_model(token):
    """Download the pinned gated files into this computer's HF cache (token stays local).

    A dropped connection or a just-written file briefly held by antivirus or indexing on
    Windows is retried; finished files stay cached, so each attempt resumes (ADR 0057)."""
    from huggingface_hub import snapshot_download

    repo, revision = MODELS["speakers"]
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            return Path(
                snapshot_download(
                    repo,
                    revision=revision,
                    token=token,
                    cache_dir=_local_hub_cache(),
                    allow_patterns=sorted(SPEAKER_FILES),
                )
            )
        except OSError as exc:
            if hub_refusal(exc) or attempt == FETCH_ATTEMPTS:
                raise
            time.sleep(FETCH_RETRY_SECONDS * attempt)
    raise AssertionError("unreachable")


def fetch_failure(exc):
    """Name the real cause: Hub errors and locked files are all OSErrors to Python."""
    import requests

    if hub_status(exc) == 401 and type(exc).__name__ != "GatedRepoError":
        return TOKEN_REJECTED
    if hub_refusal(exc):
        return GATED_TERMS
    if isinstance(exc, requests.RequestException):
        return (
            f"The speaker model could not be downloaded from Hugging Face ({FETCH_ATTEMPTS} "
            "attempts). Check the internet connection and try again. No recording was uploaded."
        )
    reason = getattr(exc, "strerror", None) or type(exc).__name__
    return (
        f"The speaker model could not be saved on this computer ({reason}; {FETCH_ATTEMPTS} "
        "attempts). Check free disk space and that no other program holds the Hugging Face "
        "cache folder, then try again. No recording was uploaded."
    )


def prepare_local_speakers(token):
    """Verified speaker files on this computer: checked, else fetched once with the token."""
    try:
        return local_speaker_files()
    except RuntimeNotReady:
        if not token:
            raise
    from .huggingface_access import check_token

    checked = check_token(token)
    if checked.get("token_status") == "invalid":
        raise RuntimeNotReady(TOKEN_REJECTED)
    if checked.get("speaker_model_access") == "denied":
        raise RuntimeNotReady(GATED_TERMS)
    for attempt in (1, 2):
        # The Hub never re-downloads a file already in a pinned snapshot, so a damaged one
        # is removed first; a download that silently returned too little is tried once more.
        discard_damaged_speaker_files()
        try:
            location = fetch_speaker_model(token)
        except OSError as exc:
            raise RuntimeNotReady(fetch_failure(exc)) from None
        try:
            files = local_speaker_files()
            break
        except RuntimeNotReady:
            if attempt == 2:
                raise RuntimeNotReady(
                    "The speaker model download from Hugging Face was incomplete. Check the "
                    "internet connection, then choose Check token under Settings → "
                    "Hugging Face. No recording was uploaded."
                ) from None
    # The local worker's offline cache check reads this marker (enhancement_worker).
    (location / SPEAKERS_COMPLETE).write_text("complete\n", encoding="utf-8")
    return files


def discard_damaged_speaker_files():
    """Remove pinned files (and their cache blobs) that fail size/SHA-256 verification."""
    folder = snapshot_folder("speakers", _local_hub_cache())
    for relative, (size, digest) in SPEAKER_FILES.items():
        path = folder / relative
        if not (path.exists() or path.is_symlink()) or verified_file(path, size, digest):
            continue
        blob = path.resolve() if path.is_symlink() else None
        path.unlink(missing_ok=True)
        if blob is not None and blob.is_file():
            blob.unlink(missing_ok=True)


def local_speaker_status(token=None):
    """Settings: report (and, with an allowed token, prepare) the local copy."""
    try:
        (prepare_local_speakers(token) if token else local_speaker_files())
        return {"speaker_model_local": "verified", "speaker_model_error": None}
    except RuntimeNotReady as exc:
        missing = str(exc) in {SPEAKERS_MISSING, SPEAKERS_INCOMPLETE}
        return {
            "speaker_model_local": "missing" if missing and not token else "failed",
            "speaker_model_error": None if missing and not token else str(exc),
        }


def speaker_marker(settings):
    repo, revision = MODELS["speakers"]
    return (
        runtime_folder(settings)
        + "/hf/hub/models--"
        + repo.replace("/", "--")
        + "/snapshots/"
        + revision
        + "/"
        + SPEAKERS_COMPLETE
    )


def speaker_model_available(credentials, chosen):
    """Local cache first (no network); otherwise a read-only check for a completed copy."""
    try:
        local_speaker_files()
        return True
    except RuntimeNotReady:
        pass
    settings = providers.Settings.model_validate(chosen["settings"])
    key = credentials.get(providers.credential_scope(settings))
    try:
        with native.client_for(settings, key) as client:
            return remote_speakers_complete(client, settings)
    except (ValueError, UCloudAPIError, httpx.HTTPError):
        return False


def require_current(store, credentials, job, project_id):
    """Retry checks: frozen connection still approved, configured and on this app's recipe."""
    from . import restored_owners

    restored_owners.require_job(store, job["id"])
    params, settings = frozen(job)
    providers.require_approval(store, project_id, params["provider_id"], settings)
    native.require_setup(settings, credentials.get(providers.credential_scope(settings)))
    if params.get("runtime_id") != runtime_id():
        raise ValueError(
            "This app version uses a different UCloud runtime than when the enhancement was "
            "queued. Start a new enhancement."
        )


def frozen(job):
    params = job["params"]["ucloud_api"]
    return params, providers.Settings.model_validate(params["settings"])


def supervisor_script(settings, identifier, prepare, models=(), *, batch=False, prepare_only=False):
    models = ",".join(models)
    if not re.fullmatch(r"[a-z0-9,-]*", models):
        raise ValueError("Unexpected model identifiers for the UCloud runtime.")
    replacements = {
        "__MODELS__": models,
        "__RUN__": (
            "/work/cbs-runtime-batch-" + identifier
            if batch
            else container_path(run_folder(settings, identifier))
        ),
        "__RUNTIME__": container_path(runtime_folder(settings)),
        "__PREPARE__": "1" if prepare else "0",
        "__BATCH__": "1" if batch else "0",
        "__PREPARE_ONLY__": "1" if prepare_only else "0",
        "__GENERATION__": identifier,
        "__UV_URL__": UV_URL,
        "__UV_SHA256__": UV_SHA256,
        "__PYTHON__": PYTHON_VERSION,
        "__TORCH_INDEX__": TORCH_INDEX,
        "__REQ_SHA256__": requirements_hash(),
        "__WAIT_SECONDS__": str(MANIFEST_WAIT_SECONDS),
        "__PREPARE_SECONDS__": str(PREPARE_SECONDS),
    }
    # Normalize Windows checkouts: bash rejects carriage returns in scripts.
    script = SUPERVISOR.read_text(encoding="utf-8").replace("\r\n", "\n")
    for key, value in replacements.items():
        script = script.replace(key, value)
    if re.search(r"__[A-Z0-9_]+__", script):
        raise ValueError("The UCloud supervisor template has an unknown placeholder.")
    return script


# ADR 0055: the CBS runtime runs as a batch script in a UCloud Terminal app. The Transcriber
# app only offers an initScript before its own program, which ends the job without an input.
SCRIPT_FLAVOURS = ("terminal-ubuntu", "terminal-debian", "terminal-almalinux")
SCRIPT_PARAMETER = "batchScript"


def script_application(client):
    """The preferred available Terminal flavour with a batch-script input (read-only)."""
    found = native.cloud_call(
        client.request,
        "POST",
        "/api/hpc/apps/search",
        json={"query": "Terminal", "itemsPerPage": 50, "discovery": "AVAILABLE"},
    ).json()
    flavours = {}
    groups = set()
    for item in found.get("items") or []:
        metadata = item.get("metadata") or {}
        flavours.setdefault(metadata.get("name"), metadata.get("version"))
        group = metadata.get("groupId")
        if group is None:
            group = ((metadata.get("group") or {}).get("metadata") or {}).get("id")
        if isinstance(group, int) and group >= 0:
            groups.add(group)
    for group in sorted(groups):
        listing = native.cloud_call(
            client.request,
            "GET",
            "/api/hpc/apps/retrieveGroups",
            params={"id": group, "discovery": "AVAILABLE"},
        ).json()
        for flavour in (listing.get("status") or {}).get("applications") or []:
            metadata = flavour.get("metadata") or {}
            flavours.setdefault(metadata.get("name"), metadata.get("version"))
    for name in SCRIPT_FLAVOURS:
        version = flavours.get(name)
        if not isinstance(version, str) or not version:
            continue
        application = native.cloud_call(
            client.request,
            "GET",
            "/api/hpc/apps/byNameAndVersion",
            params={"appName": name, "appVersion": version},
        ).json()
        parameters = native.application_parameters(application)
        if parameters.get(SCRIPT_PARAMETER, {}).get("type") == "input_file":
            return {"name": name, "version": version}
    raise ValueError(
        "No UCloud Terminal app with batch processing is available to this project. "
        "No recording was uploaded."
    )


def build_script_specification(
    settings, application, identifier, options, prepare, *, runtime_product=None
):
    """Use separately frozen runtime compute; earlier jobs keep the native product exactly."""
    setup = settings.ucloud_transcriber
    folder = run_folder(settings, identifier)
    hours = math.ceil((options.timeout_minutes + (PREPARE_SECONDS // 60 if prepare else 0)) / 60)
    specification = native.build_job_specification(
        {
            "application": dict(application),
            "product": dict(
                setup.product
                if runtime_product is None
                else native.NativeSetup(product=runtime_product).product
            ),
            "replicas": 1,
            "parameters": {},
            "resources": [],
        },
        hours=max(1, hours),
        name="CBS enhancement " + identifier,
        ssh_enabled=False,
    )
    specification["parameters"] = {
        SCRIPT_PARAMETER: {"type": "file", "path": folder + "/supervisor.sh"}
    }
    specification["resources"] = [
        {"type": "file", "path": folder, "readOnly": False},
        {"type": "file", "path": runtime_folder(settings), "readOnly": not prepare},
    ]
    return specification


def listed(client, path):
    try:
        client.browse_files(str(PurePosixPath(path).parent), items_per_page=1)
    except UCloudAPIError as exc:
        if exc.status_code in {400, 404}:
            return False  # A missing parent cannot contain the folder.
        raise ValueError(f"UCloud storage check failed (HTTP {exc.status_code}).") from None
    return native.folder_listed(client, path)


def stamped(client, settings):
    return listed(client, runtime_folder(settings)) and listed(
        client, runtime_folder(settings) + "/stamp.json"
    )


def ensure_folder(client, path):
    try:
        client.create_folder(path)
    except UCloudAPIError as exc:
        if exc.status_code != 409:
            raise ValueError(f"UCloud folder creation failed (HTTP {exc.status_code}).") from None


def read_remote(client, server, path, limit=MAX_STATUS_BYTES):
    """Bounded download of one run-folder file; missing files return None."""
    try:
        response = client.request("POST", "/api/files/download", json={"items": [{"id": path}]})
    except UCloudAPIError as exc:
        if exc.status_code in {400, 404}:
            return None  # UCloud reports a missing path as 400.
        raise Transient(f"UCloud file read failed (HTTP {exc.status_code}).") from None
    except httpx.HTTPError:
        raise Transient("UCloud connection failed. Retry to reconnect.") from None
    endpoint = native.transfer_url(server, native.one_response(response.json())["endpoint"])
    data = bytearray()
    try:
        with httpx.Client(timeout=120, follow_redirects=False) as transport:
            with transport.stream("GET", endpoint) as stream:
                if stream.status_code in {400, 404}:
                    return None
                if not stream.is_success:
                    raise Transient("UCloud file download failed. Retry to reconnect.")
                for block in stream.iter_bytes():
                    data.extend(block)
                    if len(data) > limit:
                        raise ValueError("UCloud returned an oversized enhancement file.")
    except httpx.HTTPError:
        raise Transient("UCloud file download was interrupted. Retry to reconnect.") from None
    return bytes(data)


def read_json(client, server, path, limit=MAX_STATUS_BYTES):
    data = read_remote(client, server, path, limit)
    if data is None:
        return None
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None  # A partially replaced file is read again on the next poll.
    return value if isinstance(value, dict) else None


def remote_state(client, job_id):
    try:
        return client.retrieve_job(job_id).get("status", {}).get("state")
    except UCloudAPIError as exc:
        if exc.status_code == 429 or exc.status_code >= 500:
            raise Transient(f"UCloud job status unavailable (HTTP {exc.status_code}).") from None
        raise ValueError(f"UCloud job status request failed (HTTP {exc.status_code}).") from None
    except httpx.HTTPError:
        raise Transient("UCloud connection failed. Retry to reconnect.") from None


def required_models(options):
    names = []
    if options.transcribe or options.retranscribe:
        names.append(WHISPER_MODELS[options.whisper_model])
    if options.align:
        names += ["coarse", *(("da", "en") if options.language == "auto" else (options.language,))]
    if options.diarize:
        names.append("speakers")
    return list(dict.fromkeys(names))


def prefetch_models(options):
    """Public pinned models an explicit preparation may fetch; never the gated speakers."""
    return [name for name in required_models(options) if name != "speakers"]


SPEAKERS_MISSING = (
    "Speaker labels on UCloud need the pyannote speaker model on this computer first. Save a "
    "Hugging Face token, accept the model's terms and choose Check token (Settings → "
    "Hugging Face). No recording was uploaded."
)
SPEAKERS_INCOMPLETE = (
    "The speaker model on this computer is incomplete or damaged. Choose Check token under "
    "Settings → Hugging Face to download it again. No recording was uploaded."
)


def verified_file(path, size, digest):
    """Size and SHA-256 match. Always hashed (about 33 MB in all): a cache keyed by size and
    modification time missed a same-size rewrite within one timestamp tick (B131)."""
    try:
        if not path.is_file() or path.stat().st_size != size:
            return False
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
    except OSError:
        return False
    return hasher.hexdigest() == digest


def local_speaker_files():
    """The pinned, verified speaker files on this computer; the HF token never leaves it."""
    folder = snapshot_folder("speakers", _local_hub_cache())
    if not folder.is_dir():
        raise RuntimeNotReady(SPEAKERS_MISSING)
    files = []
    for relative, (size, digest) in sorted(SPEAKER_FILES.items()):
        if not verified_file(folder / relative, size, digest):
            raise RuntimeNotReady(SPEAKERS_INCOMPLETE)
        files.append((folder / relative, relative))
    return files


def _local_hub_cache():
    from huggingface_hub import constants

    return Path(constants.HF_HUB_CACHE)


def remote_speaker_gaps(client, settings):
    """Pinned speaker files missing or of the wrong size on the runtime.

    The completion marker alone is not trusted: an earlier copy uploaded it first and was then
    interrupted, leaving a marked but incomplete model (B127)."""
    snapshot = model_folder(settings, "speakers")
    gaps = []
    for relative, (size, _) in sorted(SPEAKER_FILES.items()):
        try:
            remote = client.retrieve_file(snapshot + "/" + relative)
        except UCloudAPIError as exc:
            if exc.status_code not in {400, 404}:
                raise ValueError(f"UCloud storage check failed (HTTP {exc.status_code}).") from None
            remote = {}
        if (remote.get("status") or {}).get("sizeInBytes") != size:
            gaps.append(relative)
    return gaps


def remote_speakers_complete(client, settings):
    return not remote_speaker_gaps(client, settings) and listed(client, speaker_marker(settings))


TRANSIENT_UPLOAD = ("interrupted", "timed out", "upload failed", "connection failed", "(HTTP 5")


def upload_retrying(client, server, path, target, guard):
    """One file copy; an interrupted transfer is retried (each attempt replaces the file).

    Other failures (refused request, unsupported protocol, revoked approval) keep their own
    message and are not retried."""
    for attempt in range(1, SEED_ATTEMPTS + 1):
        guard()
        try:
            native.upload(client, server, path, target, guard, lambda **f: None)
            return
        except ValueError as exc:
            if not any(marker in str(exc) for marker in TRANSIENT_UPLOAD):
                raise
            if attempt == SEED_ATTEMPTS:
                raise RuntimeNotReady(
                    f"Copying the speaker model to UCloud was interrupted ({SEED_ATTEMPTS} "
                    "attempts). Check the connection and start again; no recording was uploaded."
                ) from None
            guard()
            time.sleep(SEED_RETRY_SECONDS * attempt)


def seed_speakers(client, server, settings, files, guard, marker):
    """Copy missing or damaged pinned files, check them, then write the completion marker."""
    repo, revision = MODELS["speakers"]
    root = runtime_folder(settings)
    model = "hf/hub/models--" + repo.replace("/", "--")
    snapshot = root + "/" + model + "/snapshots/" + revision
    for name in ("hf", "hf/hub", model, model + "/snapshots", model + "/snapshots/" + revision):
        ensure_folder(client, root + "/" + name)
    gaps = set(remote_speaker_gaps(client, settings))
    created = set()
    for path, relative in files:
        if relative not in gaps:
            continue  # Already there with its pinned size.
        parent = PurePosixPath(relative).parent
        for depth in range(1, len(parent.parts) + 1):
            name = "/".join(parent.parts[:depth])
            if name not in created:
                ensure_folder(client, snapshot + "/" + name)
                created.add(name)
        upload_retrying(client, server, path, snapshot + "/" + relative, guard)
    remaining = remote_speaker_gaps(client, settings)
    if remaining:
        raise RuntimeNotReady(
            "The speaker model copy on UCloud is incomplete (" + ", ".join(remaining) + "). "
            "Start again; no recording was uploaded."
        )
    # Only a checked copy gets the marker, so the runtime never reports a partial model.
    marker.write_text(f"{repo}@{revision}\n", encoding="utf-8", newline="\n")
    upload_retrying(client, server, marker, snapshot + "/" + SPEAKERS_COMPLETE, guard)


def manifest_lines(folder, names):
    lines = []
    for name in names:
        digest = hashlib.sha256()
        with (folder / name).open("rb") as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
        lines.append(f"{digest.hexdigest()}  {name}\n")
    return "".join(lines)


def stop(client, run, checkpoint):
    """Terminate the owned job only if it is still active (check first)."""
    if run.get("job_id") and not run.get("termination_requested"):
        state = remote_state(client, run["job_id"])
        checkpoint(remote_state=state)
        if state not in TERMINAL:
            native.cloud_call(client.terminate_job, run["job_id"])
        checkpoint(termination_requested=True)


def terminate_owned(job, credentials):
    _, settings = frozen(job)
    job_id = job["params"]["ucloud_run"]["job_id"]
    key = credentials.get(providers.credential_scope(settings))
    with native.client_for(settings, key) as client:
        if remote_state(client, job_id) not in TERMINAL:
            native.cloud_call(client.terminate_job, job_id)


def cleanup(client, run, remote, checkpoint):
    """Move the owned run folder (it holds audio) to UCloud Trash."""
    if run.get("cleanup_state") in native.CLEANED:
        return
    checkpoint(cleanup_state="pending")
    try:
        native.trash_owned_folder(client, remote)
    except (ValueError, UCloudAPIError, httpx.HTTPError) as exc:
        checkpoint(
            cleanup_error=str(exc)[:300] if isinstance(exc, ValueError) else "Cleanup failed."
        )
        return
    checkpoint(cleanup_state="trashed", cleanup_error=None)


def checkpoint_job(store, job, **values):
    """Merge independent capacity/reconciliation receipts without changing journal references."""
    from . import restored_owners

    restored_owners.require_job(store, job["id"])
    latest = store.job(job["id"])["params"]
    merged = {**latest.get("ucloud_run", {}), **values}
    run = job["params"].setdefault("ucloud_run", {})
    run.clear()
    run.update(merged)
    latest["ucloud_run"] = run
    job["params"] = latest
    store.update_job(job["id"], params=latest)


def settle(store, credentials, job, *, validate=None):
    """Best effort once local outputs exist: stop the owned job and trash its run folder."""
    from . import restored_owners

    restored_owners.require_job(store, job["id"])
    _, settings = frozen(job)
    run = job["params"].setdefault("ucloud_run", {})

    def checkpoint(**values):
        checkpoint_job(store, job, **values)

    if settings.ucloud_transcriber.cleanup_after_success and run.get("cleanup_state") not in (
        native.CLEANED
    ):
        checkpoint(cleanup_state="pending")
    try:
        key = credentials.get(providers.credential_scope(settings))
        with native.client_for(settings, key) as client:
            stop(client, run, checkpoint)
            from .runtime_capacity import drain

            fresh = drain(store, credentials, job, client=client, checkpoint=checkpoint)
            if (
                fresh["params"]["ucloud_run"].get("remote_state") in TERMINAL
                and run.get("cleanup_state") == "pending"
            ):
                saved = store.all("SELECT id FROM transcript_results WHERE job_id=?", (job["id"],))
                if not saved and validate is not None:
                    validate()  # Cached-file existence is not successful local validation.
                if saved or validate is not None:
                    cleanup(client, run, run_folder(settings, job["id"]), checkpoint)
    except (ValueError, UCloudAPIError, httpx.HTTPError):
        pass  # A pending cleanup stays visible in Activity with a cleanup-only retry.


def _retained_preview(store, client, job, settings):
    """Observe exact allocation and a bounded filename inventory, never download file bodies."""
    run = job["params"].get("ucloud_run", {})
    folder = run_folder(settings, job["id"])
    if (
        job["kind"] != "enhance"
        or job["params"].get("runtime_batch_id")
        or not run.get("job_id")
        or run.get("owned_folder") != folder
    ):
        raise ValueError("Preview needs an exactly owned legacy enhancement run.")
    remote = native.cloud_call(client.retrieve_job, run["job_id"])
    specification = remote.get("specification") if isinstance(remote, dict) else None
    expected_product = job["params"]["ucloud_api"].get(
        "runtime_product", settings.ucloud_transcriber.product
    )
    if (
        not isinstance(specification, dict)
        or str(remote.get("id")) != run["job_id"]
        or specification.get("name") != "CBS enhancement " + job["id"]
        or not run.get("script_application")
        or specification.get("application") != run["script_application"]
        or specification.get("product") != expected_product
        or not isinstance(specification.get("resources"), list)
        or sum(
            isinstance(row, dict)
            and row.get("type") == "file"
            and row.get("path") == folder
            and row.get("readOnly") is False
            for row in specification["resources"]
        )
        != 1
    ):
        raise ValueError("The remote allocation does not match this exact owned enhancement.")
    status = remote.get("status")
    state = status.get("state") if isinstance(status, dict) else None
    if not isinstance(state, str) or state not in TERMINAL:
        raise ValueError("Wait for terminal confirmation before retained-run preview or cleanup.")
    page = native.cloud_call(client.browse_files, folder, items_per_page=64)
    rows = page.get("items") if isinstance(page, dict) else None
    if not isinstance(rows, list) or len(rows) > 64 or page.get("next"):
        raise ValueError("The retained folder inventory is incomplete; files were retained.")
    names = []
    for row in rows:
        path = row.get("id") if isinstance(row, dict) else None
        if not isinstance(path, str) or str(PurePosixPath(path).parent) != folder:
            raise ValueError("Invalid retained folder inventory; files were retained.")
        name = PurePosixPath(path).name
        if name in names:
            raise ValueError("Duplicate retained folder inventory; files were retained.")
        names.append(name)
    public = {path.name for path in WORKER_FILES} | {
        "supervisor.sh",
        "requirements.txt",
        "doctor.json",
        "cache.json",
        "supervisor.json",
        "remote-status.json",
        "progress.json",
        "error.json",
        "remote-worker.lock",
        "speakers-complete.txt",
    }
    # An empty setup-created out directory is public; unknown/nonempty trees remain obligations.
    if "out" in names:
        child = native.cloud_call(client.browse_files, folder + "/out", items_per_page=1)
        if isinstance(child, dict) and child.get("items") == [] and not child.get("next"):
            public.add("out")
    stages = {"transcription.json", "retranscription.json", "alignment.json", "diarization.json"}
    saved = store.all("SELECT id FROM transcript_results WHERE job_id=?", (job["id"],))
    private = bool(set(names) - public)
    return {
        "job_id": job["id"],
        "remote_job_id": run["job_id"],
        "remote_state": state,
        "saved_results": len(saved),
        "available_stage_names": sorted(set(names) & stages),
        "private_files_present": private,
        "inventory_count": len(names),
        "can_cleanup": job["state"] not in {"queued", "running"} and (bool(saved) or not private),
        "scope": "metadata_only",
        "output_integrity": "not_checked",
        "disposition_consent": False,
    }


def retained_preview(store, credentials, job):
    _, settings = frozen(job)
    key = credentials.get(providers.credential_scope(settings))
    with native.client_for(settings, key) as client:
        return _retained_preview(store, client, job, settings)


def retry_cleanup(store, credentials, job):
    """Cleanup-only retry once the owned job has finished or been stopped."""
    from . import restored_owners

    restored_owners.require_job(store, job["id"])
    if job["state"] in {"queued", "running"}:
        raise ValueError("Wait for this enhancement's recovery to finish before cleanup.")
    _, settings = frozen(job)
    run = job["params"].get("ucloud_run", {})
    if run.get("cleanup_state") in native.CLEANED:
        return run["cleanup_state"]
    finished = (
        store.all("SELECT id FROM transcript_results WHERE job_id=?", (job["id"],))
        or run.get("termination_requested")
        or run.get("remote_state") in TERMINAL
    )
    if (
        not run.get("job_id")
        or run.get("owned_folder") != run_folder(settings, job["id"])
        or not finished
    ):
        raise ValueError("Cleanup requires this enhancement's finished job and owned folder.")
    key = credentials.get(providers.credential_scope(settings))
    native.require_setup(settings, key)

    def checkpoint(**values):
        checkpoint_job(store, job, **values)

    with native.client_for(settings, key) as client:
        from .runtime_capacity import refresh

        preview = _retained_preview(store, client, job, settings)
        if not preview["can_cleanup"]:
            raise ValueError(
                "Recover this run's unsaved private outputs before cleanup. "
                "Ordinary cleanup does not grant disposition consent."
            )
        fresh = refresh(store, credentials, job, client=client)
        if fresh["params"]["ucloud_run"].get("remote_state") not in TERMINAL:
            raise ValueError("The UCloud job is still writing; its files were retained.")
        cleanup(client, run, run["owned_folder"], checkpoint)
    return run.get("cleanup_state")


class Run:
    """One enhancement window's remote execution, journaled in the job's `ucloud_run`."""

    def __init__(self, worker, job, recording, folder, options, validate):
        from . import restored_owners

        restored_owners.require_job(worker.store, job["id"])
        self.worker, self.job, self.folder = worker, job, folder
        self.options, self.validate, self.recording = options, validate, recording
        self.store, self.identifier = worker.store, job["id"]
        self.params, self.settings = frozen(job)
        self.runtime_product = runtime_compute.frozen_product(self.params)
        self.prepare = bool(self.params.get("prepare_runtime"))
        self.server = self.settings.base_url
        self.run = job["params"].setdefault("ucloud_run", {})
        self.remote = run_folder(self.settings, self.identifier)

    def checkpoint(self, **values):
        checkpoint_job(self.store, self.job, **values)

    def guard(self):
        if self.worker.cancel_event.is_set():
            raise Cancelled()
        providers.require_approval(
            self.store, self.recording["project_id"], self.params["provider_id"], self.settings
        )

    def message(self, text, progress=0.5):
        self.store.update_job(self.identifier, message=text, progress=progress)

    def upload(self, path, name):
        self.guard()
        native.upload(
            self.client, self.server, path, self.remote + "/" + name, self.guard, lambda **f: None
        )

    def execute(self):
        key = self.worker.credentials.get(providers.credential_scope(self.settings))
        native.require_setup(self.settings, key)
        if self.params.get("runtime_id") != runtime_id():
            raise ValueError(
                "This app version uses a different UCloud runtime than when the enhancement was "
                "queued. Start a new enhancement."
            )
        if self.run.get("job_id") and self.run.get("termination_requested"):
            raise ValueError(
                "This UCloud enhancement job was stopped. Start a new enhancement; "
                "the source result is unchanged."
            )
        self.guard()
        with native.client_for(self.settings, key) as self.client:
            try:
                if not self.run.get("job_id"):
                    self.submit()
                self.poll()
                self.collect()
            except Cancelled:
                if not self.worker.stopping.is_set():
                    self.settle_failure()
                raise
            except Transient:
                raise  # Connection trouble: keep the owned job for an explicit reconnect.
            except Exception:
                self.settle_failure()
                raise

    def settle_failure(self):
        """Stop the allocation; failure never grants disposition of unsaved private files."""
        try:
            stop(self.client, self.run, self.checkpoint)
            from .runtime_capacity import drain

            fresh = drain(
                self.store,
                self.worker.credentials,
                self.job,
                client=self.client,
                checkpoint=self.checkpoint,
                wait=self.worker.cancel_event.wait,
            )
        except (ValueError, UCloudAPIError, httpx.HTTPError):
            return  # Activity retains ownership while termination cannot be verified.
        if (
            fresh["params"].get("ucloud_run", {}).get("remote_state") in TERMINAL
            and self.run.get("cleanup_state") == "pending"
        ):
            try:
                preview = _retained_preview(self.store, self.client, fresh, self.settings)
            except (ValueError, UCloudAPIError, httpx.HTTPError):
                return
            if preview["saved_results"] or not preview["private_files_present"]:
                cleanup(self.client, self.run, self.remote, self.checkpoint)

    def submit(self):
        from . import runtime_batches, runtime_store

        if self.run.get("submission_intent") and not self.run.get("reconciled_not_created"):
            raise ValueError(
                "UCloud allocation outcome is uncertain. Reconcile it in Activity "
                "before starting another job."
            )
        # Retries reuse the exact app version chosen for this run.
        application = self.run.get("script_application")
        if not application:
            application = script_application(self.client)
            self.checkpoint(script_application=application)
        specification = build_script_specification(
            self.settings,
            application,
            self.identifier,
            self.options,
            self.prepare,
            runtime_product=self.runtime_product,
        )
        runtime = runtime_folder(self.settings)
        runtime_batches.require_preparation_idle(self.store, self.settings, self.client)
        if not stamped(self.client, self.settings):
            if not self.prepare:
                # Checked before allocating: an unprepared runtime never costs a B200 job.
                raise ValueError(FAILURES["runtime-missing"])
            ensure_folder(
                self.client, self.settings.ucloud_transcriber.storage_path + "/cbs-runtime"
            )
            ensure_folder(self.client, runtime)
        self.prepare_speakers()
        runtime_batches.ensure_use_lock(self.client, self.settings, self.folder, self.guard)
        self.guard()
        if not self.run.get("owned_folder"):
            try:
                self.client.create_folder(self.remote)
            except UCloudAPIError as exc:
                if exc.status_code == 409:
                    raise ValueError(
                        "The enhancement run folder exists without an ownership receipt. "
                        "No recording was uploaded."
                    ) from None
                raise
            self.checkpoint(owned_folder=self.remote)
        if not self.run.get("scripts_uploaded"):
            self.message("Preparing the UCloud enhancement job…", 0.1)
            ensure_folder(self.client, self.remote + "/out")
            (self.folder / "supervisor.sh").write_text(
                supervisor_script(
                    self.settings, self.identifier, self.prepare, prefetch_models(self.options)
                ),
                encoding="utf-8",
                newline="\n",
            )
            self.upload(self.folder / "supervisor.sh", "supervisor.sh")
            for path in WORKER_FILES:
                self.upload(path, path.name)
            if self.prepare:
                (self.folder / "requirements.txt").write_bytes(requirements_bytes())
                self.upload(self.folder / "requirements.txt", "requirements.txt")
            self.checkpoint(scripts_uploaded=True)
        self.guard()
        while not runtime_store.begin_legacy_submission(self.store, self.identifier):
            self.message("Waiting for a free UCloud runtime allocation slot…", 0.1)
            self.worker.cancel_event.wait(POLL_SECONDS)
            self.guard()
        self.checkpoint(
            job_id=extract_job_id(native.cloud_call(self.client.submit_job, specification))
        )

    def prepare_speakers(self):
        """Before any allocation, put the verified speaker model on the runtime (ADR 0057).

        A download or copy problem then fails here, on this computer, without a GPU job."""
        if not self.options.diarize or remote_speakers_complete(self.client, self.settings):
            return
        self.message("Checking the speaker model on this computer…", 0.04)
        token = self.worker.credentials.get(HF_SCOPE) if self.prepare else None
        if token:
            self.message("Fetching the speaker model to this computer (one time)…", 0.05)
        files = prepare_local_speakers(token)
        self.guard()
        self.message("Copying the speaker model to the UCloud runtime…", 0.08)
        seed_speakers(
            self.client,
            self.server,
            self.settings,
            files,
            self.guard,
            self.folder / "speakers-complete.txt",
        )
        if not remote_speakers_complete(self.client, self.settings):
            raise RuntimeNotReady(
                "The speaker model copy on UCloud could not be confirmed. Start again; "
                "no recording was uploaded."
            )

    def poll(self):
        deadline = time.monotonic() + self.options.timeout_minutes * 60
        if self.prepare:
            deadline += PREPARE_SECONDS
        transient = readiness = 0
        while True:
            self.guard()
            if time.monotonic() >= deadline:
                raise ValueError(
                    "UCloud enhancement exceeded its time limit; its job was stopped. "
                    "Start a new enhancement."
                )
            try:
                state = remote_state(self.client, self.run["job_id"])
                status = read_json(self.client, self.server, self.remote + "/supervisor.json")
                transient = 0
            except Transient:
                transient += 1
                if transient > MAX_TRANSIENT:
                    raise
                self.worker.cancel_event.wait(POLL_SECONDS)
                continue
            if state != self.run.get("remote_state"):
                self.checkpoint(remote_state=state)
            if state in TERMINAL:
                from . import runtime_store

                runtime_store.release_capacity(self.store, self.identifier, legacy=True)
            status = status or {}
            phase = status.get("phase")
            if status.get("generation") not in {None, self.identifier}:
                raise ValueError("The UCloud run folder belongs to another enhancement.")
            if phase == "failed":
                raise ValueError(
                    FAILURES.get(status.get("code"), "The UCloud enhancement job failed.")
                )
            if phase == "finished":
                return
            if state in TERMINAL:
                raise ValueError(
                    "The UCloud enhancement job ended before finishing. Check the job in "
                    "UCloud; the source result is unchanged."
                )
            if phase == "preparing":
                self.message("Preparing the UCloud runtime (one time; can take 20+ minutes)…")
            elif phase == "ready" and not self.run.get("uploaded"):
                if state != "RUNNING":
                    self.message("Waiting for the UCloud job to run…", 0.25)
                elif self.send_inputs():
                    self.checkpoint(uploaded=True)
                else:
                    readiness += 1
                    if readiness > MAX_READINESS_READS:
                        raise RuntimeNotReady(FAILURES["runtime"])
            elif phase == "running":
                progress = read_json(self.client, self.server, self.remote + "/progress.json")
                self.message(STAGES.get((progress or {}).get("stage"), "UCloud · running…"))
            elif phase == "started":
                self.message("UCloud job started · checking the runtime…", 0.25)
            elif not phase:
                self.message("Waiting for the UCloud job to start…", 0.2)
            self.worker.cancel_event.wait(POLL_SECONDS)

    def send_inputs(self):
        """Upload private inputs only to a ready, running job; False means read again."""
        doctor = read_json(self.client, self.server, self.remote + "/doctor.json")
        cache = read_json(self.client, self.server, self.remote + "/cache.json")
        if doctor is None or cache is None:
            return False
        if doctor.get("version") != WHISPERX_VERSION or not doctor.get("ready"):
            raise RuntimeNotReady(FAILURES["runtime"])
        if self.options.device == "cuda" and not (doctor.get("gpu") or {}).get("available"):
            raise RuntimeNotReady(
                "The UCloud job has no usable CUDA GPU. No recording was uploaded."
            )
        if self.params.get("runtime_compute_policy") == runtime_compute.POLICY:
            try:
                runtime_compute.require_gpu_memory(doctor)
            except ValueError as exc:
                raise RuntimeNotReady(str(exc)) from None
        models = cache.get("models") or {}
        missing = [name for name in required_models(self.options) if not models.get(name)]
        if self.options.align and not cache.get("punkt_tab"):
            missing.append("tokenizer")
        if missing == ["speakers"]:
            # Normally copied before allocation; repair the copy only from verified local
            # files. Nothing is downloaded while a GPU job waits.
            self.message("Copying the speaker model to the UCloud runtime…", 0.3)
            seed_speakers(
                self.client,
                self.server,
                self.settings,
                local_speaker_files(),
                self.guard,
                self.folder / "speakers-complete.txt",
            )
        elif missing:
            raise RuntimeNotReady(
                "The UCloud runtime lacks cached models ("
                + ", ".join(missing)
                + "). Prepare the UCloud runtime again. No recording was uploaded."
            )
        self.guard()
        if self.settings.ucloud_transcriber.cleanup_after_success:
            # Journal the obligation before any private byte leaves this computer.
            self.checkpoint(cleanup_state="pending")
        self.message("Uploading enhancement audio and frozen text to UCloud…", 0.35)
        names = ["request.json", "audio.wav"]
        for name in names:
            self.upload(self.folder / name, name)
        (self.folder / "manifest.sha256").write_text(
            manifest_lines(self.folder, names), encoding="utf-8", newline="\n"
        )
        self.upload(self.folder / "manifest.sha256", "manifest.sha256")
        return True

    def collect(self):
        result = read_json(self.client, self.server, self.remote + "/remote-status.json") or {}
        if result.get("state") != "complete":
            error = read_json(self.client, self.server, self.remote + "/error.json") or {}
            from .enhancement import ERRORS

            raise ValueError(
                ERRORS.get(error.get("code"), ERRORS["processing"]).replace("Local", "UCloud")
            )
        for name, enabled in (
            ("transcription", self.options.transcribe),
            ("retranscription", self.options.retranscribe),
            ("alignment", self.options.align),
            ("diarization", self.options.diarize),
        ):
            if not enabled:
                continue
            data = read_remote(
                self.client, self.server, self.remote + "/" + name + ".json", MAX_STAGE_BYTES
            )
            if data is None:
                raise ValueError("The UCloud enhancement output is missing.")
            pending = self.folder / (name + ".cloud.pending")
            pending.write_bytes(data)
            pending.replace(self.folder / (name + ".json"))
        try:
            self.validate()
        except ValueError:
            # Keep invalid outputs for inspection without letting a retry reuse them.
            for name in ("transcription", "retranscription", "alignment", "diarization"):
                path = self.folder / (name + ".json")
                if path.exists():
                    path.replace(self.folder / (name + ".invalid.json"))
            raise
        stop(self.client, self.run, self.checkpoint)
        from .runtime_capacity import drain

        fresh = drain(
            self.store,
            self.worker.credentials,
            self.job,
            client=self.client,
            checkpoint=self.checkpoint,
            wait=self.worker.cancel_event.wait,
        )
        if (
            self.settings.ucloud_transcriber.cleanup_after_success
            and fresh["params"]["ucloud_run"].get("remote_state") in TERMINAL
        ):
            cleanup(self.client, self.run, self.remote, self.checkpoint)


def run_api(worker, job, recording, folder, options, validate):
    """Execute one enhancement window; `validate` checks outputs before remote cleanup."""
    from . import restored_owners

    restored_owners.require_job(worker.store, job["id"])
    if job["params"].get("runtime_batch_id"):
        dispatch = getattr(worker, "runtime_dispatch", None)
        if dispatch is None:
            raise ValueError("This runtime task must run through its shared batch coordinator.")
        return dispatch(job, recording, folder, options, validate)
    Run(worker, job, recording, folder, options, validate).execute()
