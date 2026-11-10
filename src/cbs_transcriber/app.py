"""Loopback web application. Media remains ID based; project creation chooses storage."""

import json
import math
import os
import re
import secrets
import shutil
import threading
from contextlib import asynccontextmanager, contextmanager, nullcontext
from html import escape
from itertools import islice
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, urlparse

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi import Path as PathParameter
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import (
    audio_inputs,
    deletions,
    enhancement_api,
    enhancement_cloud,
    huggingface_access,
    input_admission,
    language_preferences,
    libraries,
    library_deletion,
    local_concurrency,
    original_inspection,
    portable_routes,
    release_profile,
    remote_jobs,
    remote_release,
    result_merges,
    review_hints,
    routing,
    runtime_batches,
    runtime_discard,
    runtime_preparation,
    runtime_store,
    wording_adoptions,
    working_existing,
    working_manual,
)
from .alignment import compare_transcripts, phrase_occurrences
from .annotations import MAX_BUNDLE_BYTES, MAX_PASSAGES
from .catalog import provider_catalog, speech_catalog, whisperx_model
from .enhancement import (
    HF_SCOPE,
    EnhancementOptions,
    LocalSettings,
    ProcessingSettings,
    cached_hardware,
    check_timing_units,
    hardware_status,
    local_settings,
    passage_input,
    passage_inputs,
    pending_job_stages,
    realign_enabled,
    require_local_runtime,
    retranscription_inputs,
    save_local_settings,
    save_realign,
    timing_units,
    validate_input,
)
from .export_tasks import Conflict as ExportConflict
from .export_tasks import DownloadResponse, ExportTasks
from .exports import export_bytes, media_type, project_archive, recording_archive
from .features import FeatureRequest, native_features
from .jobs import (
    ActivityRetained,
    JobControlConflict,
    JobQueue,
    activity_inspection_links,
    activity_listing,
    activity_state,
    clear_activity,
    hide_activity,
    inspection_upload_group,
)
from .media import MEDIA_SUFFIXES
from .providers import (
    PROTECTED_DEFAULTS,
    Credentials,
    Settings,
    StaleProvider,
    admission_profiles,
    backfill_native_profiles,
    check_models,
    credential_scope,
    default_provider_id,
    delete_profile,
    fresh_native_settings,
    migrate_native_allocation_defaults,
    profile,
    public_profiles,
    public_settings,
    require_approval,
    require_enabled,
    require_key,
    save_native_allocation,
    save_native_language,
    save_profile,
    save_runtime_language,
    save_settings,
    set_default_provider,
)
from .storage import default_library_root
from .storage import synced as folder_synced
from .store import SCHEMA_VERSION, Store, new_id
from .transcripts import (
    MANUAL_SPLIT_POLICY,
    MANUAL_TEXT_TIMING_POLICY,
    MAX_TRANSCRIPT_BYTES,
    TEXT_TIMING_POLICY,
    ExportFormat,
    delete_passage,
    edit_manual_text_retaining_timing,
    edit_mapped_word,
    edit_segment,
    edit_text_retaining_timing,
    edit_timed_word,
    export_filename,
    merge_passages,
    split_passage,
    split_passage_manual,
)


class Name(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class ProjectName(Name):
    expected_name: str | None = Field(default=None, max_length=160)


class RuntimePreparationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class RuntimePreparationReconcile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_not_created: bool = False
    fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ProjectCreate(Name):
    classification: Literal["standard", "protected"] = "standard"
    storage_parent: str | None = Field(default=None, max_length=2000)
    default_language: str | None = Field(default=None, max_length=35)


class LanguagePreference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str | None = Field(max_length=35)
    revision: int = Field(strict=True, ge=0, lt=2**63)


class RecordingLanguageRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(strict=True, min_length=1, max_length=100)
    revision: int = Field(strict=True, ge=0, lt=2**63)


class BulkRecordingLanguage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str | None = Field(max_length=35)
    recordings: list[RecordingLanguageRevision] = Field(min_length=1, max_length=1000)


class ProjectEdit(BaseModel):
    classification: Literal["standard", "protected"]
    expected_classification: Literal["standard", "protected"] | None = None


class ProjectStorage(BaseModel):
    storage_parent: str = Field(min_length=1, max_length=2000)
    expected_storage_path: str | None = Field(default=None, max_length=2000)


class RetranscribeRequest(EnhancementOptions):
    result_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(strict=True, ge=0, lt=2**63)
    segment_ids: list[str] = Field(min_length=1, max_length=200)


class EditingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    realign_edits: bool = Field(strict=True)


class LibraryChoice(BaseModel):
    path: str = Field(strict=True, min_length=1, max_length=2000)
    create: bool = Field(default=False, strict=True)


class LibraryVisibility(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(strict=True, min_length=1, max_length=2000)
    token: str = Field(strict=True, min_length=64, max_length=64)
    hidden: bool = Field(strict=True)


class LibraryDeletePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(strict=True, min_length=1, max_length=2000)


class LibraryDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(strict=True, min_length=1, max_length=2000)
    fingerprint: str = Field(strict=True, min_length=64, max_length=64)
    confirm_name: str = Field(strict=True, min_length=1, max_length=500)


def install_library_deletion(app, current=None):
    """Permanent deletion of an inactive library (ADR 0144); never the open one."""

    def conflict(error):
        return HTTPException(409, str(error)[:700])

    if current is not None:

        @app.post("/api/libraries/delete-preview")
        def preview_library_deletion(body: LibraryDeletePreview):
            try:
                return library_deletion.preview(body.path, current=current)
            except library_deletion.Conflict as error:
                raise conflict(error) from None

        @app.post("/api/libraries/delete")
        def delete_library(body: LibraryDelete):
            try:
                return library_deletion.delete(
                    body.path,
                    fingerprint=body.fingerprint,
                    confirm_name=body.confirm_name,
                    current=current,
                )
            except library_deletion.Conflict as error:
                raise conflict(error) from None

    @app.post("/api/libraries/deletions/{identifier}/retry")
    def retry_library_deletion(identifier: Annotated[str, PathParameter(pattern="^[0-9a-f]{64}$")]):
        try:
            return library_deletion.retry(identifier)
        except library_deletion.Conflict as error:
            raise conflict(error) from None


STATIC = Path(__file__).parent / "static"


def json_response(recording):
    """Encode completed native projections once, preserving rare legacy encoder behavior."""
    try:
        response = JSONResponse(recording)
    except (TypeError, ValueError, RecursionError):  # UnicodeError is a ValueError too.
        pass
    else:
        # The old encoder omitted nested _sa* keys, even in plain JSON metadata.
        # False positives in literal values only take the compatible slower path.
        if b'"_sa' not in response.body:
            return response
    return JSONResponse(jsonable_encoder(recording))


def protect(app):
    """Loopback hosts, same-origin mutations and strict browser headers for every local app."""
    # A server incarnation is distinct even when two libraries contain identical IDs.
    app.state.browser_instance = secrets.token_hex(16)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            same_origin = not origin
            if origin:
                try:
                    parsed = urlparse(origin)
                    same_origin = (
                        parsed.netloc == request.headers.get("host")
                        and parsed.scheme == request.url.scheme
                    )
                except ValueError:
                    same_origin = False
            if not same_origin or request.headers.get("x-cbs-request") != "1":
                return JSONResponse({"detail": "Use the local CBS Transcriber interface."}, 403)
        # Legacy non-browser clients remain supported, but cannot be fenced without
        # carrying an instance. This is a browser safety guard, not authentication.
        instance = request.headers.get("x-cbs-instance")
        resource_instance = request.query_params.get("cbs_instance")
        api = request.url.path.startswith("/api/")
        switch_probe = request.method == "GET" and request.url.path == "/api/library/switch-ready"
        browser = "origin" in request.headers or "sec-fetch-site" in request.headers
        supplied = [value for value in (instance, resource_instance) if value is not None]
        if (
            api
            and not switch_probe
            and (
                any(value != app.state.browser_instance for value in supplied)
                or (browser and not supplied)
            )
        ):
            return JSONResponse(
                {
                    "code": "library_instance_changed",
                    "detail": "This page belongs to an earlier library. Copy unsaved edits, "
                    "then reload before continuing.",
                },
                409,
                headers={"Cache-Control": "no-store"},
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if (
            request.url.path.startswith("/api/")
            or request.url.path == "/"
            or request.url.path.startswith("/static/")
            and request.url.path.endswith((".js", ".css"))
        ):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid_choice(request, exc):
        return JSONResponse({"detail": str(exc)}, 400)

    @app.get("/static/index.html", include_in_schema=False)
    @app.get("/static/library.html", include_in_schema=False)
    def old_static_page():
        # HTML must be served through the route that binds the current instance.
        return RedirectResponse("/", status_code=303, headers={"Cache-Control": "no-store"})


def browser_page(app, name):
    """Bind this page to the current serving instance, not to library IDs."""
    html = (STATIC / name).read_text(encoding="utf-8")
    html = html.replace("<body", f'<body data-cbs-instance="{app.state.browser_instance}"', 1)
    profile = getattr(app.state, "profile", None)
    if profile is not None:
        # Known before any script runs, so hidden entry points never flash (ADR 0145).
        label = f' data-release-label="{escape(profile.label)}"' if profile.label else ""
        off = " ".join(sorted(profile.disabled))
        html = html.replace(
            "<html", f'<html data-profile="{profile.name}" data-off="{off}"{label}', 1
        )
    return HTMLResponse(html)


def create_chooser_app():
    """Start-up chooser; the launcher receives an in-memory checked library selection."""
    app = FastAPI(title="CBS Transcriber", docs_url=None, redoc_url=None)
    app.state.choose = None
    protect(app)

    @app.get("/api/libraries")
    def list_libraries():
        return {
            "current": None,
            "can_switch": True,
            "default_new": str(libraries.default_new()),
            "libraries": libraries.known(),
            "pending_deletions": library_deletion.pending(),
        }

    install_library_deletion(app)

    @app.post("/api/libraries/open", status_code=202)
    def open_library(body: LibraryChoice):
        if app.state.choose is None:
            raise HTTPException(409, "Start CBS Transcriber with its launcher to choose a library.")
        target = libraries.select(body.path, create=body.create, schema=SCHEMA_VERSION)
        app.state.choose(target)
        return {"library": str(target.path)}

    @app.post("/api/libraries/visibility")
    def set_library_visibility(body: LibraryVisibility):
        libraries.set_hidden(body.path, body.token, hidden=body.hidden)
        return {"ok": True}

    @app.post("/api/storage/browse")
    def browse_storage():
        from .storage import choose_folder

        return {"path": choose_folder()}

    app.mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static")

    @app.get("/")
    def index():
        return browser_page(app, "library.html")

    return app


class NotesEdit(BaseModel):
    text: str = Field(strict=True, max_length=20_000)
    revision: int = Field(strict=True, ge=0, le=2**63 - 1)
    reason: str | None = Field(default=None, strict=True, max_length=2000)


class WorkingChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str = Field(strict=True, min_length=1, max_length=100)
    role_revision: int = Field(strict=True, ge=0, lt=2**63)
    make_working: bool = Field(strict=True)


def working_choice(body):
    return body.working_choice.model_dump() if body.working_choice is not None else None


class Edit(BaseModel):
    revision: int = Field(ge=0)
    mode: Literal["word", "free", "restore", "timed_word", "mapped_word"]
    text: str | None = Field(default=None, max_length=200_000)
    word_index: int | None = Field(default=None, ge=0)
    start: float | None = Field(default=None, strict=True, ge=0, allow_inf_nan=False)
    end: float | None = Field(default=None, strict=True, ge=0, allow_inf_nan=False)
    reason: str | None = Field(default=None, strict=True, max_length=2000)
    working_choice: WorkingChoice | None = None

    @model_validator(mode="before")
    @classmethod
    def strict_timed_word_identity(cls, value):
        if isinstance(value, dict):
            if value.get("mode") in {"timed_word", "mapped_word"}:
                if (
                    type(value.get("word_index")) is not int
                    or type(value.get("revision")) is not int
                ):
                    raise ValueError("Use an exact word index and revision.")
            else:
                # These were ignored extra fields for older edit modes.
                value = {key: item for key, item in value.items() if key not in {"start", "end"}}
        return value


class FreeEditV1(Edit):
    mode: Literal["free"]
    realign: bool = Field(strict=True)


class FreeEditV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(strict=True, ge=0, le=2**63 - 1)
    mode: Literal["free"]
    text: str = Field(strict=True, max_length=200_000)
    reason: str | None = Field(default=None, strict=True, max_length=2000)
    working_choice: WorkingChoice | None = None


class SpeakerEdit(BaseModel):
    revision: int = Field(ge=0)
    speaker: str = Field(max_length=200)
    name: str = Field(min_length=1, max_length=160)
    reason: str | None = Field(default=None, strict=True, max_length=2000)
    working_choice: WorkingChoice | None = None


class PassageSpeakerEdit(BaseModel):
    revision: int = Field(ge=0)
    speaker_id: str | None = Field(default=None, strict=True, min_length=1, max_length=200)
    name: str | None = Field(default=None, strict=True, max_length=200)
    reason: str | None = Field(default=None, strict=True, max_length=2000)
    working_choice: WorkingChoice | None = None


class PassageStructureEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(strict=True, ge=0, le=2**63 - 1)
    result_id: str = Field(strict=True, min_length=1, max_length=100)
    reason: str | None = Field(default=None, strict=True, max_length=2000)
    working_choice: WorkingChoice | None = None


class PassageMerge(PassageStructureEdit):
    direction: Literal["up", "down"]
    speaker_id: str | None = Field(default=None, strict=True, min_length=1, max_length=200)


class PassageSplit(PassageStructureEdit):
    cursor: int = Field(strict=True, gt=0, lt=200_000)
    split_time: float | None = Field(default=None, strict=True, ge=0, allow_inf_nan=False)


class ExportSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    formats: list[ExportFormat] = Field(min_length=0, max_length=8)
    grouping: Literal["speaker", "timestamp"] = "speaker"
    include_originals: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def different_formats(self):
        if not self.formats and not self.include_originals:
            raise ValueError("Choose a transcript format or include original recordings.")
        if len(set(self.formats)) != len(self.formats):
            raise ValueError("Choose each transcript format only once.")
        return self


class ProjectExports(ExportSelection):
    recording_ids: list[Annotated[str, Field(strict=True, min_length=1, max_length=100)]] = Field(
        min_length=1, max_length=10_000
    )
    results: Literal["working", "active", "all"] = "working"
    collection: Annotated[str, Field(strict=True, min_length=1, max_length=100)] | None = None

    @model_validator(mode="after")
    def different_recordings(self):
        if len(set(self.recording_ids)) != len(self.recording_ids):
            raise ValueError("Choose each recording only once.")
        return self


class RecordingExports(ExportSelection):
    result_id: str | None = Field(default=None, strict=True, min_length=1, max_length=100)

    @model_validator(mode="after")
    def saved_result(self):
        if self.result_id is None and not self.include_originals:
            raise ValueError("Choose the saved result to export.")
        return self


class ResultMergePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incoming_result_id: str = Field(strict=True, min_length=1, max_length=100)
    revision: int = Field(strict=True, ge=0, lt=2**63)


class ResultMergeApply(ResultMergePreview):
    fingerprint: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(default=None, strict=True, max_length=2000)
    working_choice: WorkingChoice | None = None


class ConflictDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passage_id: str = Field(strict=True, min_length=1, max_length=200)
    group: Literal["wording", "timing", "speaker"]
    choice: Literal["current", "incoming"]


class ConflictPreview(ResultMergePreview):
    decisions: list[ConflictDecision] = Field(default_factory=list, max_length=600)


class ConflictApply(ConflictPreview):
    fingerprint: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(default=None, strict=True, max_length=2000)
    working_choice: WorkingChoice | None = None


class WorkingPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str | None = Field(default=None, strict=True, min_length=1, max_length=100)
    revision: int = Field(strict=True, ge=0, lt=2**63)
    role_revision: int = Field(strict=True, ge=0, lt=2**63)


class WorkingApply(WorkingPreview):
    fingerprint: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")


class WordingPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_result_id: str = Field(strict=True, min_length=1, max_length=100)
    target_passage_id: str = Field(strict=True, min_length=1, max_length=200)
    source_passage_id: str = Field(strict=True, min_length=1, max_length=200)
    revision: int = Field(strict=True, ge=0, lt=2**63)


class WordingAdopt(WordingPreview):
    fingerprint: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(default=None, strict=True, max_length=2000)
    working_choice: WorkingChoice | None = None


class ReportSelection(BaseModel):
    recording_id: str = Field(strict=True, min_length=1, max_length=100)
    result_id: str = Field(strict=True, min_length=1, max_length=100)
    version_id: str = Field(strict=True, min_length=1, max_length=100)


class ResearchReport(BaseModel):
    selections: list[ReportSelection] | None = Field(default=None, min_length=1, max_length=100)
    include_selected_transcripts: bool = Field(default=False, strict=True)
    # The visible collection, or "unsorted", limits the default selection (ADR 0060).
    collection: str | None = Field(default=None, max_length=100)


class AnnotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodebookSave(AnnotationRequest):
    payload: dict
    revision: int = Field(strict=True, ge=0, le=2**63 - 1)
    reason: str | None = Field(default=None, strict=True, max_length=2000)


class AnnotationRunCreate(AnnotationRequest):
    result_id: str = Field(strict=True, min_length=1, max_length=100)
    source_version_id: str = Field(strict=True, min_length=1, max_length=100)
    codebook_version_id: str = Field(strict=True, min_length=1, max_length=100)
    passage_ids: list[Annotated[str, Field(strict=True, min_length=1, max_length=200)]] | None = (
        Field(default=None, min_length=1, max_length=MAX_PASSAGES)
    )
    request_id: str = Field(strict=True, min_length=1, max_length=100)


class AnnotationImport(AnnotationRequest):
    bundle: dict
    request_id: str = Field(strict=True, min_length=1, max_length=100)


class AnnotationAdd(AnnotationRequest):
    payload: dict
    request_id: str = Field(strict=True, min_length=1, max_length=100)
    reason: str | None = Field(default=None, strict=True, max_length=2000)


class AnnotationReview(AnnotationRequest):
    revision: int = Field(strict=True, ge=0, le=2**63 - 1)
    action: Literal["accept", "change", "reject"]
    payload: dict | None = None
    request_id: str = Field(strict=True, min_length=1, max_length=100)
    reason: str | None = Field(default=None, strict=True, max_length=2000)


class ProviderEdit(Settings):
    # The profile version the form loaded; a different saved version is rejected (409).
    expected_version: str | None = Field(default=None, max_length=64)
    api_key: str | None = Field(default=None, max_length=4096)
    remember: bool = True
    ucloud_token: str | None = Field(default=None, max_length=4096)
    hf_token: str | None = Field(default=None, max_length=4096)
    # Public template/invalid state may be echoed by generic profile clients; it is not a choice.
    native_allocation_choice: Literal["full", "mig1g", "template", "invalid"] | None = None


class Transcribe(BaseModel):
    recording_ids: list[str] = Field(min_length=1, max_length=100)
    provider_ids: list[str] | None = Field(default=None, min_length=1, max_length=20)
    replace: bool = False
    requested_features: FeatureRequest | None = None
    enhancement: EnhancementOptions | None = None
    # Explicit, confirmed one-time preparation of the UCloud runtime: for UCloud WhisperX
    # models (T70) and for this request's UCloud follow-ups (ADR 0059).
    prepare_runtime: bool = False

    @model_validator(mode="after")
    def supported_followup_language(self):
        if self.enhancement is not None and self.enhancement.language == "auto":
            raise ValueError("Automatic language follow-ups are not yet available.")
        return self


class ProviderConfig(ProviderEdit):
    name: str = Field(min_length=1, max_length=160)
    enabled_models: (
        list[Annotated[str, Field(strict=True, min_length=1, max_length=200)]] | None
    ) = Field(default=None, max_length=100)
    enabled_models_touched: (
        list[Annotated[str, Field(strict=True, min_length=1, max_length=200)]] | None
    ) = Field(default=None, max_length=100)
    enabled_model_versions: dict[str, str] | None = Field(default=None, max_length=1000)


class ProviderChoice(BaseModel):
    provider_id: str | None = None


class RuntimeLanguageChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Literal["", "da", "en", "auto"]
    expected_version: str = Field(strict=True, pattern=r"^[a-f0-9]{16}$")


class NativeAllocationChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allocation: Literal["full", "mig1g"]
    expected_version: str = Field(strict=True, pattern=r"^[a-f0-9]{16}$")


class NativeLanguageChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str = Field(strict=True, max_length=20)
    expected_version: str = Field(strict=True, pattern=r"^[a-f0-9]{16}$")


class VersionRestore(BaseModel):
    revision: int = Field(strict=True, ge=0, lt=2**63)
    reason: str | None = Field(default=None, strict=True, max_length=2000)
    working_choice: WorkingChoice | None = None


class Revision(BaseModel):
    revision: int = Field(strict=True, ge=0, lt=2**63)


class RealignApply(Revision):
    result_id: str | None = Field(default=None, min_length=1, max_length=100)
    working_choice: WorkingChoice | None = None


class BackupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Empty: the library's own backups folder. Otherwise an absolute folder of the user's.
    destination: str | None = Field(default=None, max_length=2000)


class CollectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)


class CollectionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=20000)


class CollectionMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recording_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        min_length=1, max_length=5000
    )
    # None moves the recordings out of every collection ("Unsorted").
    collection_id: str | None = Field(default=None, max_length=100)


class PassageTiming(BaseModel):
    result_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(strict=True, ge=0, lt=2**63)
    segment_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        min_length=1, max_length=1000
    )


class HuggingFaceToken(BaseModel):
    hf_token: str | None = Field(default=None, max_length=4096)
    remember: bool = True


class WordingSearch(BaseModel):
    left_id: str
    right_id: str
    phrase: str = Field(min_length=1, max_length=2000)


class Retry(BaseModel):
    replace: bool = False


class ActivityHide(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9-]+$")


class InputIntentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    epoch: int = Field(strict=True, ge=1, le=2**53)
    phase: Literal["waiting", "materializing", "terminal"]


class InputInspectionAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: str = Field(strict=True, pattern=r"^[0-9a-f]{32}$")
    epoch: int = Field(strict=True, ge=1, le=2**53)


class InputPreparationConsent(InputIntentAction):
    confirmed: bool = Field(strict=True)
    fingerprint: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")


class ReconcileRemote(BaseModel):
    remote_id: str | None = Field(
        default=None, min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$"
    )
    not_created: bool = False


class RuntimeBatchSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_allocations: int = Field(strict=True, ge=1, le=4)


class RuntimeDiscard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str = Field(strict=True, min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    confirmed: bool = Field(strict=True)


class LocalSettingsEdit(ProcessingSettings):
    hf_token: str | None = Field(default=None, max_length=4096)
    remember: bool = True


class CloudEnhancementEdit(enhancement_cloud.CloudEnhancementSettings):
    api_token: str | None = Field(default=None, max_length=4096)
    remember: bool = True


class EnhanceRequest(EnhancementOptions):
    result_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=0)
    segment_id: str | None = Field(default=None, min_length=1, max_length=100)
    segment_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] | None = Field(
        default=None, min_length=1, max_length=1000
    )

    @model_validator(mode="after")
    def supported_source_language(self):
        if self.language == "auto":
            raise ValueError("Automatic language for existing transcripts is not yet available.")
        return self


def clean_name(value):
    value = value.strip()
    if not value:
        raise ValueError("Please enter a name.")
    return value


def create_app(
    data_dir=None, *, start_worker=True, credentials=None, project_parent=None, release=None
):
    # An unknown profile refuses startup before the library is opened (ADR 0145).
    release = release_profile.resolve(release)
    override = os.environ.get("CBS_TRANSCRIBER_DATA_DIR")
    root = data_dir or (Path(override).expanduser() if override else default_library_root())
    store = Store(root)
    if project_parent:
        store.default_parent = Path(project_parent)
    credentials = credentials or Credentials()
    queue = JobQueue(store, credentials)
    deletion_requests = deletions.Deletions(store, queue, acquire_file_lease=not start_worker)
    export_manager = ExportTasks(store, acquire_file_lease=not start_worker)
    processing_started = False
    profiles_initialized = False
    initialization_lock = threading.Lock()

    def initialize_processing():
        nonlocal processing_started, profiles_initialized
        with initialization_lock:
            if deletion_requests.file_recovery_blocked:
                return
            if not profiles_initialized:
                migrate_native_allocation_defaults(store)
                backfill_native_profiles(store)
                # ADR 0141: after file recovery and before workers; time-bounded.
                app.state.working_existing = working_existing.startup(store)
                profiles_initialized = True
            if start_worker and not processing_started:
                queue.start()
                processing_started = True
                if not os.environ.get("CBS_TRANSCRIBER_NO_STARTUP_CHECK"):
                    threading.Thread(
                        target=hardware_status, args=(local_settings(store),), daemon=True
                    ).start()

    @asynccontextmanager
    async def lifespan(app):
        with store.library_lock() if start_worker else nullcontext():
            error = None
            try:
                # Recover files before any profile writes or queue admission. Test/no-worker
                # apps use a transient startup lease, released before HTTP worker requests.
                with (
                    store.library_lock()
                    if not start_worker and not store._lease_depth
                    else nullcontext()
                ):
                    deletion_requests.recover_files()
                    initialize_processing()
                yield
            except BaseException as primary:
                error = primary
                raise
            finally:
                cleanup_error = None
                try:
                    export_manager.close()
                except BaseException as cleanup:
                    cleanup_error = cleanup
                try:
                    if processing_started:
                        queue.stop()
                except BaseException as cleanup:
                    if cleanup_error is None:
                        cleanup_error = cleanup
                if cleanup_error is not None:
                    if error is not None:
                        raise error from cleanup_error
                    raise cleanup_error

    app = FastAPI(title="CBS Transcriber", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store, app.state.queue, app.state.credentials = store, queue, credentials
    from . import passage_restore_routes, passage_review_routes

    passage_review_routes.install(app, store)
    passage_restore_routes.install(app, store)
    app.state.deletions = deletion_requests
    app.state.export_tasks = export_manager
    app.state.profile = release
    # The launcher sets this to restart the server on another library (ADR 0051).
    app.state.switch_library = None

    # Complete captures, isolated restore and manual UCloud retention (ADR 0096). Install
    # before protect(): Starlette makes later middleware outermost, so host/same-origin checks
    # must refuse a request before it can wait for or occupy a capture admission slot.
    def local_mutation_allowed(request):
        retry = request.url.path.startswith("/api/local-deletions/") and request.url.path.endswith(
            "/retry"
        )
        return not deletion_requests.file_recovery_blocked or retry

    portable_routes.install(
        app,
        store,
        queue,
        credentials,
        mutation_allowed=local_mutation_allowed,
        capture_guard=export_manager.exclusive,
    )

    @contextmanager
    def export_safe_staging():
        with export_manager.exclusive(), app.state.portable_capture_gate.promote_mutation():
            yield

    deletion_requests.staging = export_safe_staging

    @app.middleware("http")
    async def local_file_recovery_admission(request, call_next):
        mutation = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        if mutation and not local_mutation_allowed(request):
            return JSONResponse(
                {"detail": "Finish local file recovery in Activity before changing this library."},
                409,
            )
        return await call_next(request)

    def feature_refusal(exc):
        return JSONResponse(
            {"code": release_profile.CODE, "feature": exc.feature, "detail": str(exc)}, 403
        )

    @app.middleware("http")
    async def release_profile_admission(request, call_next):
        # Outside the capture gate: a hidden feature never waits for or takes a mutation slot.
        feature = release_profile.guarded(request.method, request.url.path)
        if feature is not None and not release.enabled(feature):
            return feature_refusal(release_profile.FeatureDisabled(feature))
        return await call_next(request)

    protect(app)

    @app.exception_handler(release_profile.FeatureDisabled)
    async def feature_disabled(request, exc):
        return feature_refusal(exc)

    def placed(options):
        """Without UCloud processing, Automatic runs here and UCloud is refused (ADR 0145)."""
        place = release.place(options.execution)
        if place == options.execution:
            return options
        return options.model_copy(update={"execution": place, "prepare_runtime": False})

    def require_runtime_idle(*, project_id=None, recording_ids=None):
        deletion_requests.require_files_idle()
        export_manager.require_idle(project_id=project_id, recording_ids=recording_ids)
        try:
            if (
                project_id is None
                and recording_ids is None
                and runtime_preparation.obligations(store)
            ):
                raise ValueError(
                    "Resolve UCloud runtime preparation in Activity before switching libraries."
                )
            runtime_batches.require_no_obligations(
                store, project_id=project_id, recording_ids=recording_ids
            )
            if input_admission.obligations(
                store, project_id=project_id, recording_ids=recording_ids
            ):
                raise ValueError(
                    "Finish this native selection's preparation in Activity "
                    "before moving or deleting its files."
                )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.exception_handler(KeyError)
    async def missing_item(request, exc):
        return JSONResponse({"detail": "The requested item no longer exists."}, 404)

    @app.exception_handler(ExportConflict)
    async def export_conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, 409)

    @app.exception_handler(input_admission.AdmissionConflict)
    async def preparation_conflict(request, exc):
        return JSONResponse({"detail": str(exc)[:700]}, 409)

    @app.exception_handler(language_preferences.Conflict)
    async def language_preference_conflict(request, exc):
        return JSONResponse({"detail": str(exc)[:700]}, 409)

    @app.exception_handler(working_manual.Conflict)
    async def working_choice_conflict(request, exc):
        return JSONResponse({"detail": str(exc)[:700]}, 409)

    @app.exception_handler(JobControlConflict)
    async def job_control_conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, 409)

    @app.exception_handler(runtime_preparation.Conflict)
    async def runtime_preparation_conflict(request, exc):
        return JSONResponse({"detail": str(exc)[:700]}, 409)

    @app.exception_handler(input_admission.PolicyDenied)
    async def preparation_denied(request, exc):
        return JSONResponse({"detail": str(exc)[:700]}, 403)

    @app.exception_handler(ValueError)
    async def invalid_operation(request, exc):
        message = str(exc)
        status = 409 if "another window" in message else 400
        return JSONResponse({"detail": message[:700]}, status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Validation errors normally echo input values, which can include credentials.
        return JSONResponse({"detail": "Invalid request fields. Check the supplied values."}, 422)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/features")
    def release_features():
        return release.public()

    @app.get("/api/languages")
    def languages():
        return language_preferences.catalog()

    @app.get("/api/projects")
    def projects():
        rows = store.all(
            "SELECT p.*,COUNT(r.id) AS recordings FROM projects p LEFT JOIN recordings r "
            "ON p.id=r.project_id GROUP BY p.id ORDER BY p.created"
        )
        return [
            {
                **row,
                "storage_path": str(Path(row["storage_dir"]) if row["storage_dir"] else store.root),
                "snapshot_error": store.snapshot_errors.get(row["id"]),
            }
            for row in rows
        ]

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectCreate):
        return store.create_project(
            clean_name(body.name), body.classification, body.storage_parent, body.default_language
        )

    @app.put("/api/projects/{identifier}/language")
    def project_language(identifier: str, body: LanguagePreference):
        try:
            return language_preferences.save(
                store, "project", identifier, body.language, body.revision
            )
        except language_preferences.Conflict as error:
            raise HTTPException(409, str(error)) from None

    @app.put("/api/projects/{identifier}/recording-languages")
    def recording_languages(identifier: str, body: BulkRecordingLanguage):
        try:
            return language_preferences.save_recordings(
                store, identifier, body.language, [row.model_dump() for row in body.recordings]
            )
        except language_preferences.Conflict as error:
            raise HTTPException(409, str(error)) from None

    @app.get("/api/storage")
    def storage():
        return {
            "library": str(store.root),
            "default_backup_parent": str(store.root / "backups"),
            "default_project_parent": str(store.default_parent),
            "default_parent_synced": folder_synced(store.default_parent),
        }

    @app.get("/api/library/switch-ready")
    def library_switch_ready(expected: str = Query(min_length=1, max_length=4000)):
        # This deliberately reveals no path, project or other library content.
        return {"ready": expected == str(store.root)}

    @app.get("/api/libraries")
    def list_libraries():
        return {
            "current": str(store.root),
            "can_switch": app.state.switch_library is not None,
            "can_delete": True,
            "default_new": str(libraries.default_new()),
            "libraries": libraries.known(store.root),
            "pending_deletions": library_deletion.pending(),
        }

    install_library_deletion(app, store.root)

    @app.post("/api/libraries/visibility")
    def set_library_visibility(body: LibraryVisibility):
        libraries.set_hidden(body.path, body.token, hidden=body.hidden, current=store.root)
        return {"ok": True}

    def operational_rows(rows):
        from . import restored_owners

        with store.connection() as db:
            return [row for row in rows if not restored_owners.is_restored(db, "job", row["id"])]

    @app.post("/api/libraries/open", status_code=202)
    def open_library(body: LibraryChoice):
        if app.state.switch_library is None:
            raise HTTPException(
                409, "This server was not started by the launcher; restart it with --data-dir."
            )
        target = libraries.select(body.path, create=body.create, schema=SCHEMA_VERSION)
        if target.path == store.root:
            raise ValueError("This library is already open.")
        with export_manager.exclusive(), queue.lock:
            # Restarting stops local workers; never interrupt queued or running work silently.
            require_runtime_idle()
            if operational_rows(
                store.all("SELECT id FROM jobs WHERE state IN ('queued','running')")
            ):
                raise HTTPException(
                    409, "Finish or cancel queued and running jobs before opening another library."
                )
            app.state.switch_library(target)
            export_manager.stop_admission()
        return {"library": str(target.path)}

    # Verified local backups (ADR 0061); step 1 towards keeping copies on UCloud.
    @app.get("/api/library/backups")
    def library_backups():
        from .backup import backups

        return {"folder": str(store.root / "backups"), "backups": backups(store)}

    @app.post("/api/library/backups", status_code=201)
    def back_up_library(body: BackupRequest):
        from .backup import create_backup

        return create_backup(store, body.destination or None)

    @app.post("/api/storage/browse")
    def browse_storage():
        from .storage import choose_folder

        return {"path": choose_folder()}

    @app.patch("/api/projects/{identifier}/classification")
    def classify_project(identifier: str, body: ProjectEdit):
        with queue.lock:
            store.one("SELECT id FROM projects WHERE id=?", (identifier,))
            active = store.all(
                "SELECT j.id FROM jobs j JOIN recordings r ON r.id=j.recording_id "
                "WHERE r.project_id=? AND j.kind IN ('transcribe','enhance') AND j.state='running'",
                (identifier,),
            )
            if operational_rows(active):
                raise HTTPException(
                    409,
                    "Cancel or finish running transcription/enhancement "
                    "before changing classification.",
                )
            with store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                project = db.execute(
                    "SELECT classification FROM projects WHERE id=?", (identifier,)
                ).fetchone()
                if project is None:
                    raise KeyError(identifier)
                if body.expected_classification is not None and (
                    project["classification"] != body.expected_classification
                ):
                    raise HTTPException(409, "Classification changed. Reopen project settings.")
                db.execute(
                    "UPDATE projects SET classification=? WHERE id=?",
                    (body.classification, identifier),
                )
            store.snapshot_project(identifier)
        return {"ok": True}

    @app.patch("/api/projects/{identifier}")
    def rename_project(identifier: str, body: ProjectName):
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            project = db.execute("SELECT name FROM projects WHERE id=?", (identifier,)).fetchone()
            if project is None:
                raise KeyError(identifier)
            if body.expected_name is not None and project["name"] != body.expected_name:
                raise HTTPException(409, "Project name changed. Reopen project settings.")
            db.execute("UPDATE projects SET name=? WHERE id=?", (clean_name(body.name), identifier))
        store.snapshot_project(identifier)
        return {"ok": True}

    @app.patch("/api/projects/{identifier}/notes")
    def project_notes(identifier: str, body: NotesEdit):
        return store.save_notes(
            "projects", identifier, body.text, body.revision, reason=body.reason
        )

    def note_history(kind, identifier, limit, before_revision):
        rows = store.note_versions(kind, identifier, limit=limit, before_revision=before_revision)
        fields = {
            "id",
            "project_id",
            "recording_id",
            "scope",
            "notes_revision",
            "text_sha256",
            "observed_at",
            "actor_kind",
            "action",
        }
        return [{key: value for key, value in row.items() if key in fields} for row in rows]

    @app.get("/api/projects/{identifier}/notes/versions")
    def project_note_versions(
        identifier: str,
        limit: int = Query(default=50, ge=1, le=100),
        before_revision: int | None = Query(default=None, ge=1),
    ):
        return note_history("projects", identifier, limit, before_revision)

    @app.post("/api/projects/{identifier}/storage")
    def move_project_storage(identifier: str, body: ProjectStorage):
        from .storage import relocate_project

        with export_manager.exclusive(), queue.lock, store.snapshot_lock:
            store.one("SELECT id FROM projects WHERE id=?", (identifier,))
            if body.expected_storage_path is not None and (
                str(store.project_folder(identifier)) != body.expected_storage_path
            ):
                raise HTTPException(409, "Storage folder changed. Reopen project settings.")
            require_runtime_idle(project_id=identifier)
            active = store.all(
                "SELECT j.id FROM jobs j JOIN recordings r ON r.id=j.recording_id "
                "WHERE r.project_id=? AND j.state IN ('queued','running')",
                (identifier,),
            )
            if operational_rows(active):
                raise HTTPException(
                    409,
                    "Finish or cancel this project's active jobs before moving storage.",
                )
            return relocate_project(store, identifier, body.storage_parent)

    @app.post("/api/projects/{identifier}/deletion-preview")
    def preview_project_deletion(identifier: str):
        # Captures one short-lived operation; nothing is cancelled or removed (ADR 0101).
        return deletion_requests.preview("project", identifier)

    @app.delete("/api/projects/{identifier}")
    def delete_project(
        identifier: str,
        operation_id: str | None = Query(default=None, max_length=32),
        confirm_shared: bool = False,
        fingerprint: str | None = Query(default=None, max_length=64),
    ):
        status, body = deletion_requests.request(
            "project",
            identifier,
            operation_id=operation_id,
            confirm_shared=confirm_shared,
            fingerprint=fingerprint,
        )
        return JSONResponse(body, status)

    @app.get("/api/projects/{identifier}/recordings")
    def recordings(identifier: str):
        store.one("SELECT id FROM projects WHERE id=?", (identifier,))
        # Canonical writer prefixes are cheap advisory candidates; export verifies full bytes.
        rows = store.all(
            "SELECT id,project_id,name,filename,kind,status,duration,revision,created, "
            "notes,notes_revision,collection_id,language_override,language_revision, "
            "provider_id,active_transcript_id,(segments!='[]') AS has_transcript, "
            "(SELECT COUNT(*) FROM transcript_results t WHERE t.recording_id=recordings.id "
            "AND t.id NOT IN (SELECT result_id FROM result_trash)) "
            "AS transcript_count, "
            "EXISTS(SELECT 1 FROM transcript_results t WHERE t.recording_id=recordings.id "
            "AND t.id NOT IN (SELECT result_id FROM result_trash) AND t.segments LIKE '[{%') "
            "AS export_all, "
            "EXISTS(SELECT 1 FROM transcript_results t WHERE t.recording_id=recordings.id "
            "AND t.id=recordings.active_transcript_id "
            "AND t.id NOT IN (SELECT result_id FROM result_trash) AND t.segments LIKE '[{%') "
            "AS export_active, "
            "EXISTS(SELECT 1 FROM recording_result_roles w "
            "JOIN result_versions v ON v.id=w.working_version_id "
            "JOIN transcript_results t ON t.id=v.result_id "
            "WHERE w.recording_id=recordings.id AND t.recording_id=recordings.id "
            "AND t.id NOT IN (SELECT result_id FROM result_trash) "
            'AND v.payload LIKE \'{"format":"cbs-result-snapshot-v1","segments":[{%\') '
            "AS export_working FROM recordings "
            "WHERE project_id=? ORDER BY created",
            (identifier,),
        )
        for row in rows:
            row["export_eligible"] = {
                mode: bool(row.pop("export_" + mode)) for mode in ("working", "active", "all")
            }
            row["source_readiness"] = input_admission.source_readiness(store, row["id"])
        return rows

    @app.post("/api/recordings", status_code=201)
    def upload_recording(
        project_id: Annotated[str, Form()],
        file: Annotated[UploadFile, File()],
        collection_id: Annotated[str | None, Form(max_length=100)] = None,
        upload_batch: Annotated[str | None, Form()] = None,
    ):
        # Files added together share one batch ID: one Activity card (V162).
        if upload_batch is not None and not re.fullmatch(r"[0-9a-f]{32}", upload_batch):
            raise ValueError("The upload batch identifier is invalid.")
        with store.snapshot_lock:
            return copy_recording(project_id, file, collection_id or None, upload_batch)

    # Collections (ADR 0060): named groups inside a project; metadata only, files never move.
    @app.get("/api/projects/{identifier}/collections")
    def project_collections(identifier: str):
        return store.collections(identifier)

    @app.post("/api/projects/{identifier}/collections", status_code=201)
    def create_collection(identifier: str, body: CollectionCreate):
        created = store.create_collection(identifier, body.name)
        store.snapshot_project(identifier)
        return created

    @app.patch("/api/collections/{identifier}")
    def update_collection(identifier: str, body: CollectionEdit):
        updated = store.update_collection(identifier, body.name, body.notes)
        store.snapshot_project(updated["project_id"])
        return updated

    @app.delete("/api/collections/{identifier}")
    def delete_collection(identifier: str):
        project_id = store.collection(identifier)["project_id"]
        store.delete_collection(identifier)
        store.snapshot_project(project_id)
        return {"ok": True}

    @app.post("/api/projects/{identifier}/collections/move")
    def move_to_collection(identifier: str, body: CollectionMove):
        moved = store.move_recordings(identifier, body.recording_ids, body.collection_id)
        store.snapshot_project(identifier)
        return {"moved": moved}

    def copy_recording(project_id, file, collection_id=None, upload_batch=None):
        store.one("SELECT id FROM projects WHERE id=?", (project_id,))
        if collection_id is not None:
            # Checked before any bytes are copied.
            store.one(
                "SELECT id FROM collections WHERE id=? AND project_id=?",
                (collection_id, project_id),
            )
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not filename or len(filename) > 240 or "\x00" in filename:
            raise ValueError("Choose a media file with a valid filename.")
        suffix = Path(filename).suffix.lower()
        if suffix not in MEDIA_SUFFIXES:
            raise ValueError("This media format is not supported. Choose an audio or video file.")
        identifier = new_id()
        directory = store.directory(identifier, project_id)
        directory.mkdir(parents=True)
        published = False
        try:
            size = 0
            with (directory / ("original" + suffix)).open("xb") as output:
                while block := file.file.read(1024 * 1024):
                    size += len(block)
                    if size > 20 * 1024**3:
                        raise ValueError("Media files must be 20 GB or smaller.")
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if not size:
                raise ValueError("The media file is empty.")
            job_id = store.publish_media_recording(
                project_id,
                filename,
                identifier,
                collection_id,
                params={"upload_batch": upload_batch} if upload_batch else None,
            )
            published = True
            recording, job = store.recording(identifier), store.job(job_id)
        except Exception:
            if not published:
                # A commit may have succeeded before its connection/reply failed. Retain
                # bytes on observed or uncertain publication; only proved absence permits cleanup.
                try:
                    absent = not store.all("SELECT id FROM recordings WHERE id=?", (identifier,))
                except Exception:
                    absent = False
                if absent:
                    audio_inputs._owned(directory, directory, directory_only=True)
                    shutil.rmtree(directory)
            raise
        finally:
            file.file.close()
        queue.notify()
        return {"recording": recording, "job": job}

    @app.get("/api/recordings/{identifier}")
    def recording(identifier: str):
        return {
            **store.recording(identifier),
            "source_readiness": input_admission.source_readiness(store, identifier),
        }

    @app.put("/api/recordings/{identifier}/language")
    def recording_language(identifier: str, body: LanguagePreference):
        try:
            return language_preferences.save(
                store, "recording", identifier, body.language, body.revision
            )
        except language_preferences.Conflict as error:
            raise HTTPException(409, str(error)) from None

    @app.patch("/api/recordings/{identifier}/notes")
    def recording_notes(identifier: str, body: NotesEdit):
        return store.save_notes(
            "recordings", identifier, body.text, body.revision, reason=body.reason
        )

    @app.get("/api/recordings/{identifier}/notes/versions")
    def recording_note_versions(
        identifier: str,
        limit: int = Query(default=50, ge=1, le=100),
        before_revision: int | None = Query(default=None, ge=1),
    ):
        return note_history("recordings", identifier, limit, before_revision)

    @app.patch("/api/recordings/{identifier}/provider")
    def choose_recording_provider(identifier: str, body: ProviderChoice):
        current = store.recording(identifier)
        if body.provider_id:
            selected = profile(store, body.provider_id)
            require_enabled(Settings.model_validate(selected["settings"]))
            require_approval(
                store,
                current["project_id"],
                selected["id"],
                Settings.model_validate(selected["settings"]),
            )
        store.execute(
            "UPDATE recordings SET provider_id=? WHERE id=?", (body.provider_id, identifier)
        )
        return store.recording(identifier)

    @app.get("/api/recordings/{identifier}/alignment")
    def comparison_alignment(
        identifier: str,
        left_id: str,
        right_id: str,
        anchor_left: int | None = Query(default=None, ge=0),
        anchor_right: int | None = Query(default=None, ge=0),
        anchor_length: int | None = Query(default=None, ge=1, le=100),
    ):
        left = store.result(identifier, left_id)
        right = store.result(identifier, right_id)
        values = (anchor_left, anchor_right, anchor_length)
        if any(v is not None for v in values) and any(v is None for v in values):
            raise ValueError("Select a matching occurrence in each transcript.")
        return compare_transcripts(
            left["segments"], right["segments"], values if anchor_left is not None else None
        )

    @app.post("/api/recordings/{identifier}/alignment/phrases")
    def comparison_phrases(identifier: str, body: WordingSearch):
        left = store.result(identifier, body.left_id)
        right = store.result(identifier, body.right_id)
        return {
            "left": phrase_occurrences(left["segments"], body.phrase),
            "right": phrase_occurrences(right["segments"], body.phrase),
        }

    @app.get("/api/recordings/{identifier}/transcripts/{result_id}")
    def transcript_result(identifier: str, result_id: str):
        return store.result(identifier, result_id)

    @app.post("/api/recordings/{identifier}/transcripts/{result_id}/merge-preview")
    def preview_result_merge(identifier: str, result_id: str, body: ResultMergePreview):
        return result_merges.preview(
            store, identifier, result_id, body.incoming_result_id, body.revision
        )

    @app.post("/api/recordings/{identifier}/working-preview")
    def preview_working_version(identifier: str, body: WorkingPreview):
        from . import result_roles

        return result_roles.preview(
            store, identifier, body.version_id, body.revision, body.role_revision
        )

    @app.post("/api/recordings/{identifier}/working")
    def mark_working_version(identifier: str, body: WorkingApply):
        from . import result_roles

        return result_roles.apply(
            store, identifier, body.version_id, body.revision, body.role_revision, body.fingerprint
        )

    @app.post("/api/recordings/{identifier}/transcripts/{result_id}/merge")
    def apply_result_merge(identifier: str, result_id: str, body: ResultMergeApply):
        return result_merges.apply(
            store,
            identifier,
            result_id,
            body.incoming_result_id,
            body.revision,
            body.fingerprint,
            body.reason,
            working_choice=working_choice(body),
        )

    @app.post("/api/recordings/{identifier}/transcripts/{result_id}/conflicts-preview")
    def preview_conflict_review(identifier: str, result_id: str, body: ConflictPreview):
        from . import result_conflicts

        return result_conflicts.preview(
            store,
            identifier,
            result_id,
            body.incoming_result_id,
            body.revision,
            [item.model_dump() for item in body.decisions],
        )

    @app.post("/api/recordings/{identifier}/transcripts/{result_id}/conflicts")
    def apply_conflict_review(identifier: str, result_id: str, body: ConflictApply):
        from . import result_conflicts

        return result_conflicts.apply(
            store,
            identifier,
            result_id,
            body.incoming_result_id,
            body.revision,
            [item.model_dump() for item in body.decisions],
            body.fingerprint,
            body.reason,
            working_choice=working_choice(body),
        )

    @app.post("/api/recordings/{identifier}/transcripts/{result_id}/wording-preview")
    def preview_wording_adoption(identifier: str, result_id: str, body: WordingPreview):
        return wording_adoptions.preview(
            store,
            identifier,
            result_id,
            body.source_result_id,
            body.target_passage_id,
            body.source_passage_id,
            body.revision,
        )

    @app.post("/api/recordings/{identifier}/transcripts/{result_id}/wording-adopt")
    def adopt_wording(identifier: str, result_id: str, body: WordingAdopt):
        return wording_adoptions.apply(
            store,
            identifier,
            result_id,
            body.source_result_id,
            body.target_passage_id,
            body.source_passage_id,
            body.revision,
            body.fingerprint,
            body.reason,
            working_choice=working_choice(body),
        )

    @app.get("/api/recordings/{identifier}/transcripts/{result_id}/hints")
    def transcript_hints(identifier: str, result_id: str):
        # Model signals for review only; never part of the transcript, its history or exports.
        return review_hints.current_hints(store, identifier, result_id)

    @app.get("/api/recordings/{identifier}/trashed-transcripts")
    def trashed_transcripts(identifier: str):
        store.one("SELECT id FROM recordings WHERE id=?", (identifier,))
        return store.results(identifier, trashed=True)

    @app.delete("/api/recordings/{identifier}/transcripts/{result_id}")
    def trash_transcript(identifier: str, result_id: str, body: Revision):
        return store.set_result_trashed(identifier, result_id, body.revision, trashed=True)

    @app.post("/api/recordings/{identifier}/transcripts/{result_id}/restore")
    def restore_transcript(identifier: str, result_id: str, body: Revision):
        return store.set_result_trashed(identifier, result_id, body.revision, trashed=False)

    @app.get("/api/recordings/{identifier}/transcripts/{result_id}/versions")
    def transcript_versions(
        identifier: str,
        result_id: str,
        limit: int = Query(default=50, ge=1, le=100),
        before_sequence: int | None = Query(default=None, ge=1),
    ):
        rows = store.result_versions(
            identifier, result_id, limit=limit, before_sequence=before_sequence
        )
        fields = {
            "id",
            "result_id",
            "sequence",
            "predecessor_id",
            "source_version_id",
            "payload_sha256",
            "observed_at",
            "action",
            "actor_kind",
        }
        return [{key: value for key, value in row.items() if key in fields} for row in rows]

    @app.get("/api/recordings/{identifier}/transcripts/{result_id}/versions/{version_id}")
    def transcript_version(identifier: str, result_id: str, version_id: str):
        return store.result_version(identifier, result_id, version_id)

    @app.get("/api/recordings/{identifier}/transcripts/{result_id}/versions/{version_id}/export")
    def export_transcript_version(
        identifier: str,
        result_id: str,
        version_id: str,
        grouping: Literal["speaker", "timestamp"] = "speaker",
        export_format: Annotated[ExportFormat, Query(alias="format")] = "text",
    ):
        version = store.result_version(identifier, result_id, version_id)
        payload = version["payload"]
        content = export_bytes(payload["segments"], payload["speakers"], grouping, export_format)
        filename = export_filename("transcript-version", export_format=export_format)
        return Response(
            content,
            media_type=media_type(export_format),
            headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename)},
        )

    @app.post("/api/recordings/{identifier}/transcripts/{result_id}/versions/{version_id}/restore")
    def restore_transcript_version(
        identifier: str, result_id: str, version_id: str, body: VersionRestore
    ):
        # Append the exact earlier state as the newest version; nothing in history is removed.
        version = store.result_version(identifier, result_id, version_id)
        payload = version["payload"]
        return store.save_transcript(
            identifier,
            payload["segments"],
            payload["speakers"],
            body.revision,
            action="version_restore",
            context={"restored_from_version_id": version_id},
            reason=body.reason,
            result_id=result_id,
            working_choice=working_choice(body),
        )

    @app.post("/api/recordings/{identifier}/transcripts/{result_id}/activate")
    def activate_transcript(identifier: str, result_id: str, body: Revision):
        return store.activate_result(identifier, result_id, body.revision)

    @app.get("/api/projects/{identifier}/codebook/versions")
    def codebook_versions(
        identifier: str,
        limit: int = Query(default=50, ge=1, le=100),
        before_sequence: int | None = Query(default=None, ge=1),
    ):
        return store.codebook_versions(identifier, limit=limit, before_sequence=before_sequence)

    @app.post("/api/projects/{identifier}/codebook/versions", status_code=201)
    def save_codebook(identifier: str, body: CodebookSave):
        return store.save_codebook(identifier, body.payload, body.revision, reason=body.reason)

    @app.get("/api/projects/{identifier}/codebook/versions/{version_id}")
    def codebook_version(identifier: str, version_id: str):
        return store.codebook_version(identifier, version_id)

    @app.get("/api/recordings/{identifier}/annotation-runs")
    def annotation_runs(
        identifier: str,
        limit: int = Query(default=50, ge=1, le=100),
        before_sequence: int | None = Query(default=None, ge=1),
    ):
        return store.annotation_runs(identifier, limit=limit, before_sequence=before_sequence)

    @app.post("/api/recordings/{identifier}/annotation-runs", status_code=201)
    def create_annotation_run(identifier: str, body: AnnotationRunCreate):
        return store.create_annotation_run(
            identifier,
            body.result_id,
            body.source_version_id,
            body.codebook_version_id,
            passage_ids=body.passage_ids,
            request_id=body.request_id,
        )

    @app.post("/api/recordings/{identifier}/annotation-runs/import", status_code=201)
    async def import_annotations(identifier: str, request: Request):
        # Bound external suggestion files before JSON parsing, without echoing their text.
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_BUNDLE_BYTES:
                raise HTTPException(413, "Suggestion imports must be at most 2 MiB.")

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        def invalid_number(value):
            raise ValueError

        try:
            decoded = json.loads(
                data, object_pairs_hook=unique_object, parse_constant=invalid_number
            )
            body = AnnotationImport.model_validate(decoded)
        except (ValueError, UnicodeError, RecursionError, ValidationError) as exc:
            raise HTTPException(422, "Use a valid suggestion import request.") from exc
        return store.import_annotation_run(identifier, body.bundle, request_id=body.request_id)

    @app.get("/api/recordings/{identifier}/annotation-runs/{run_id}")
    def annotation_run(identifier: str, run_id: str):
        return store.annotation_run(identifier, run_id)

    @app.get("/api/recordings/{identifier}/annotation-runs/{run_id}/items")
    def annotation_items(
        identifier: str,
        run_id: str,
        topic_id: str | None = Query(default=None, min_length=1, max_length=100),
        review_state: Literal["unreviewed", "accepted", "rejected"] | None = None,
        polarity: Literal["positive", "negative", "mixed", "neutral", "uncertain"] | None = None,
        limit: int = Query(default=100, ge=1, le=100),
        before_sequence: int | None = Query(default=None, ge=1),
    ):
        return store.annotations(
            identifier,
            run_id,
            topic_id=topic_id,
            review_state=review_state,
            polarity=polarity,
            limit=limit,
            before_sequence=before_sequence,
        )

    @app.post("/api/recordings/{identifier}/annotation-runs/{run_id}/items", status_code=201)
    def add_annotation(identifier: str, run_id: str, body: AnnotationAdd):
        return store.add_manual_annotation(
            identifier, run_id, body.payload, request_id=body.request_id, reason=body.reason
        )

    @app.get("/api/recordings/{identifier}/annotation-runs/{run_id}/items/{item_id}")
    def annotation_item(identifier: str, run_id: str, item_id: str):
        return store.annotation_item(identifier, run_id, item_id)

    @app.patch("/api/recordings/{identifier}/annotation-runs/{run_id}/items/{item_id}")
    def review_annotation(identifier: str, run_id: str, item_id: str, body: AnnotationReview):
        return store.review_annotation(
            identifier,
            run_id,
            item_id,
            body.revision,
            body.action,
            payload=body.payload,
            reason=body.reason,
            request_id=body.request_id,
        )

    @app.get("/api/recordings/{identifier}/annotation-runs/{run_id}/items/{item_id}/reviews")
    def annotation_review_events(
        identifier: str,
        run_id: str,
        item_id: str,
        limit: int = Query(default=50, ge=1, le=100),
        before_revision: int | None = Query(default=None, ge=1),
    ):
        return store.annotation_review_events(
            identifier, run_id, item_id, limit=limit, before_revision=before_revision
        )

    @app.get(
        "/api/recordings/{identifier}/annotation-runs/{run_id}/items/{item_id}/reviews/{event_id}"
    )
    def annotation_review_event(identifier: str, run_id: str, item_id: str, event_id: str):
        return store.annotation_review_event(identifier, run_id, item_id, event_id)

    @app.get("/api/recordings/{identifier}/annotation-runs/{run_id}/export")
    def export_annotations(identifier: str, run_id: str):
        from .annotations import export_bytes as annotation_export_bytes

        return Response(
            annotation_export_bytes(store.export_annotation_run(identifier, run_id)),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="topic-evidence.json"'},
        )

    @app.patch("/api/recordings/{identifier}")
    def rename_recording(identifier: str, body: Name):
        store.recording(identifier)
        store.execute(
            "UPDATE recordings SET name=? WHERE id=?", (clean_name(body.name), identifier)
        )
        store.snapshot_project(store.recording(identifier)["project_id"])
        return store.recording(identifier)

    @app.post("/api/recordings/{identifier}/deletion-preview")
    def preview_recording_deletion(identifier: str):
        return deletion_requests.preview("recording", identifier)

    @app.delete("/api/recordings/{identifier}")
    def delete_recording(
        identifier: str,
        operation_id: str | None = Query(default=None, max_length=32),
        confirm_shared: bool = False,
        fingerprint: str | None = Query(default=None, max_length=64),
    ):
        status, body = deletion_requests.request(
            "recording",
            identifier,
            operation_id=operation_id,
            confirm_shared=confirm_shared,
            fingerprint=fingerprint,
        )
        return JSONResponse(body, status)

    @app.post("/api/recordings/{identifier}/transcript")
    def import_transcript(
        identifier: str,
        file: Annotated[UploadFile, File()],
        revision: Annotated[int, Form()],
        replace: Annotated[bool, Form()] = False,
    ):
        with queue.lock:
            current = store.recording(identifier)
            if queue.busy(identifier):
                raise HTTPException(409, "Wait for or cancel the active job before importing.")
            if current["segments"] and not replace:
                raise HTTPException(409, "Confirm adding another transcript first.")
            data = file.file.read(MAX_TRANSCRIPT_BYTES + 1)
            file.file.close()
            store.import_transcript(identifier, data, file.filename or "", revision)
            return store.recording(identifier)

    @app.patch("/api/recordings/{identifier}/segments/{segment_id}")
    def update_segment(identifier: str, segment_id: str, body: Edit):
        return save_segment_edit(identifier, segment_id, body)

    @app.patch("/api/recordings/{identifier}/segments/{segment_id}/edit-v1")
    def update_segment_v1(identifier: str, segment_id: str, body: FreeEditV1):
        return save_segment_edit(identifier, segment_id, body, realign=body.realign)

    @app.patch("/api/recordings/{identifier}/segments/{segment_id}/edit-v2")
    def update_segment_v2(identifier: str, segment_id: str, body: FreeEditV2):
        return save_segment_edit(identifier, segment_id, body, text_policy=TEXT_TIMING_POLICY)

    @app.patch("/api/recordings/{identifier}/segments/{segment_id}/edit-v3")
    def update_segment_v3(identifier: str, segment_id: str, body: FreeEditV2):
        return save_segment_edit(
            identifier, segment_id, body, text_policy=MANUAL_TEXT_TIMING_POLICY
        )

    def save_segment_edit(
        identifier: str, segment_id: str, body: Edit | FreeEditV2, *, realign=None, text_policy=None
    ):
        current = store.recording(identifier)
        if (text_policy or body.mode in {"timed_word", "mapped_word"}) and sum(
            isinstance(segment, dict) and segment.get("id") == segment_id
            for segment in current["segments"]
        ) > 1:
            raise ValueError("The saved result has ambiguous passage identifiers.")
        for index, segment in enumerate(current["segments"]):
            if segment["id"] == segment_id:
                timing_warning = None
                if text_policy:
                    edit = (
                        edit_manual_text_retaining_timing
                        if text_policy == MANUAL_TEXT_TIMING_POLICY
                        else edit_text_retaining_timing
                    )
                    updated, timing_warning = edit(segment, body.text)
                elif body.mode == "timed_word":
                    updated = edit_timed_word(
                        segment, body.word_index, body.start, body.end, body.text
                    )
                elif body.mode == "mapped_word":
                    updated = edit_mapped_word(segment, body.word_index, body.text)
                else:
                    updated = edit_segment(segment, body.mode, body.text, body.word_index)
                current["segments"][index] = updated
                saved = store.save_transcript(
                    identifier,
                    current["segments"],
                    current["speakers"],
                    body.revision,
                    action={
                        "word": "word_correction",
                        "mapped_word": "word_correction",
                        "timed_word": "word_timing_edit",
                        "free": "free_rewrite",
                        "restore": "original_restore",
                    }[body.mode],
                    context={
                        "segment_id": segment_id,
                        "mode": body.mode,
                        **({"text_policy": text_policy} if text_policy else {}),
                        **(
                            {"word_index": body.word_index}
                            if getattr(body, "word_index", None) is not None
                            else {}
                        ),
                    },
                    reason=body.reason,
                    working_choice=working_choice(body),
                )
                if timing_warning:
                    saved["timing_retention_warning"] = timing_warning
                if not text_policy and body.mode == "free" and segment["text"] != body.text:
                    outcome = queue_realign(saved, segment_id, requested=realign)
                    if outcome and outcome.get("warning"):
                        saved["realign_warning"] = outcome["warning"]
                return json_response(saved)
        raise KeyError(segment_id)

    def expects_word_timing(recording):
        active = next(
            (t for t in recording["transcripts"] if t["id"] == recording["active_transcript_id"]),
            None,
        )
        source = active["source"] if active else {}
        return (
            source.get("capability") in {"words", "words_speakers"}
            or (source.get("options") or {}).get("align") is True
            or any(s.get("original_words") or s.get("words") for s in recording["segments"])
        )

    def queue_realign(recording, segment_id, *, requested=None):
        """Opt-in T69: align an edited passage locally; never blocks or fails the save."""
        enabled = realign_enabled(store) if requested is None else requested
        # A hidden "Realign after saving" never queues work; the edit itself still saves and
        # "Add word timing" remains the explicit route (ADR 0145).
        if not release.enabled("editor_realign"):
            return None
        if not enabled or not expects_word_timing(recording):
            return None
        settings = local_settings(store)
        options = EnhancementOptions(
            runtime_python=settings.runtime_python,
            device=settings.device,
            language=settings.language,
            execution="local",
            align=True,
            diarize=False,
            allow_downloads=False,
            timing_policy="retain-estimates",
        )
        try:
            options, language_guard, preference = language_preferences.enhancement_options(
                store, recording, options
            )
        except ValueError as error:
            return {"warning": str(error)}
        with queue.lock:
            try:
                source = store.freeze_result(
                    recording["id"], recording["active_transcript_id"], recording["revision"]
                )
                passage_input(source, segment_id, options, recording["duration"])
            except (ValueError, KeyError):
                return None  # Unbounded or too long: "Add word timing" explains why.
            superseded = operational_rows(
                store.all(
                    "WITH safe_jobs AS (SELECT id,recording_id,kind,state,"
                    "CASE WHEN json_valid(params) THEN params ELSE 'null' END AS params "
                    "FROM jobs) SELECT id FROM safe_jobs "
                    "WHERE recording_id=? AND kind='enhance' AND state='queued' "
                    "AND json_extract(params,'$.realign')=1 "
                    "AND json_extract(params,'$.segment_id')=?",
                    (recording["id"], segment_id),
                )
            )
            from . import timing_capture

            try:
                job = store.add_job(
                    recording["id"],
                    "enhance",
                    timing_capture.freeze(
                        {
                            "enhancement": options.model_dump(),
                            "source_snapshot": source,
                            "segment_id": segment_id,
                            "realign": True,
                            "provider_name": "Local word timing for an edited passage",
                            **({"language_preference": preference} if preference else {}),
                        }
                    ),
                    language_guard=language_guard,
                    capture_timing_role=True,
                )
            except (language_preferences.Conflict, ValueError, KeyError) as error:
                return {"warning": str(error)}
            for row in superseded:
                store.update_job(
                    row["id"], state="cancelled", message="Superseded by a newer edit."
                )
        queue.notify()
        return {"job": job}

    @app.post("/api/recordings/{identifier}/retranscriptions", status_code=202)
    def retranscribe_passages(identifier: str, body: RetranscribeRequest):
        """T71: re-transcribe selected passages with WhisperX into one new combined result."""
        options = input_admission.fresh_options(
            body.model_dump(exclude={"result_id", "revision", "segment_ids"})
            | {"retranscribe": True, "align": True, "diarize": False, "transcribe": False}
        )
        requested_execution = options.execution
        recording = store.recording(identifier)
        options, language_guard, preference = language_preferences.enhancement_options(
            store, recording, options
        )
        source = store.freeze_result(identifier, body.result_id, body.revision)
        # The existing multi-passage transport uses one whole WAV. T90 owns bounded bundles;
        # retain this route's frozen legacy contract until that transport is implemented.
        selected = retranscription_inputs(source, body.segment_ids, recording["duration"])
        clip_seconds = sum(s["end"] - s["start"] for s in selected["segments"])
        extra = {}
        if options.execution == "auto":
            options, extra, reason = routing.Router(store, credentials, options).decide(
                recording | {"duration": clip_seconds}, options
            )
            extra = {**extra, "routing": {"mode": "auto", "reason": reason}}
        if options.execution == "local":
            require_local_runtime(options)
        else:
            if options.allow_downloads:
                raise ValueError(
                    "UCloud enhancement uses the prepared runtime's cached models. "
                    "Use 'Prepare the UCloud runtime' instead of per-run downloads."
                )
            chosen = extra.get("ucloud_api") or enhancement_api.connection(
                store, credentials, [recording["project_id"]]
            )
            if not options.prepare_runtime and not enhancement_api.whisperx_ready(
                credentials, chosen, options
            ):
                if requested_execution == "ucloud" and local_settings(store).prepare_ucloud_models:
                    # Freeze consent before the remote projection consumed by Run is made.
                    # An Automatic placement never gains preparation consent after routing.
                    options = options.model_copy(update={"prepare_runtime": True})
                else:
                    raise HTTPException(
                        409,
                        {
                            "code": "prepare_runtime",
                            "message": enhancement_api.PREPARE_QUESTION.format(
                                detail=f" or lacks the {options.whisper_model} model"
                            ),
                        },
                    )
            if requested_execution == "ucloud":
                options, extra = enhancement_api.freeze_profile(
                    store,
                    credentials,
                    {"id": chosen["provider_id"], "settings": chosen["settings"]},
                    [recording["project_id"]],
                    options,
                )
        with queue.lock:
            recording = require_enhancement_idle(identifier, options)
            source = store.freeze_result(identifier, body.result_id, body.revision)
            selected = retranscription_inputs(source, body.segment_ids, recording["duration"])
            job = store.enqueue_enhancement(
                identifier,
                body.result_id,
                body.revision,
                {
                    "enhancement": options.model_dump(),
                    "segment_ids": [s["id"] for s in selected["segments"]],
                    "retranscription": True,
                    "provider_name": ("UCloud" if options.execution == "ucloud" else "Local")
                    + f" WhisperX {options.whisper_model} re-transcription",
                    **({"language_preference": preference} if preference else {}),
                    **extra,
                },
                language_guard=language_guard,
            )
        queue.notify()
        return job

    @app.get("/api/editing-settings")
    def editing_settings():
        return {"realign_edits": realign_enabled(store)}

    @app.put("/api/editing-settings")
    def update_editing_settings(body: EditingSettings):
        save_realign(store, body.realign_edits)
        return {"realign_edits": realign_enabled(store)}

    @app.post("/api/recordings/{identifier}/passage-timing", status_code=202)
    def add_passage_timing(identifier: str, body: PassageTiming):
        """Freeze selected timing: small requests stay local; prepared large ones use UCloud."""
        from . import repair_cloud

        settings = local_settings(store)
        options = EnhancementOptions(
            runtime_python=settings.runtime_python,
            device=settings.device,
            language=settings.language,
            execution="local",
            align=True,
            diarize=False,
            allow_downloads=False,
            timing_policy="retain-estimates",
        )

        def pending_ids():
            pending = set()
            for row in operational_rows(
                store.all(
                    "WITH safe_jobs AS (SELECT id,recording_id,kind,state,"
                    "CASE WHEN json_valid(params) THEN params ELSE 'null' END AS params "
                    "FROM jobs) SELECT id,state,params FROM safe_jobs "
                    "WHERE recording_id=? AND kind='enhance' "
                    "AND json_extract(params,'$.realign')=1 "
                    "AND json_extract(params,'$.source_snapshot.id')=?",
                    (identifier, body.result_id),
                )
            ):
                params = json.loads(row["params"])
                ready = params.get("realign_output") and not params.get("realign_applied")
                partial = (
                    row["state"] in {"failed", "cancelled"}
                    and params.get("repair_cloud", {}).get("protocol") == repair_cloud.PROTOCOL
                    and params.get("ucloud_run", {}).get("repair", {}).get("published") is True
                    and params.get("ucloud_run", {}).get("remote_state") in enhancement_api.TERMINAL
                )
                if (
                    row["state"] in {"queued", "running"}
                    or ready
                    and (row["state"] == "completed" or partial)
                ):
                    pending.update(params.get("segment_ids") or [params.get("segment_id")])
            return pending

        with queue.lock:
            recording = store.recording(identifier)
            options, language_guard, preference = language_preferences.enhancement_options(
                store, recording, options
            )
            if recording["status"] != "ready":
                raise HTTPException(409, "Wait for media preparation to finish.")
            if (
                recording["active_transcript_id"] != body.result_id
                or recording["revision"] != body.revision
            ):
                raise HTTPException(
                    409, "This result changed in another window. Reload before adding timing."
                )
            source = store.freeze_result(identifier, body.result_id, body.revision)
            pending = pending_ids()
            wanted = [i for i in dict.fromkeys(body.segment_ids) if i not in pending]
            units = (
                [
                    unit
                    for unit in timing_units(
                        source,
                        wanted,
                        options,
                        recording["duration"],
                        # Repair all leaves out what cannot be timed alone; one tag explains.
                        skip_invalid=len(wanted) > 1,
                    )
                    if not set(unit["ids"]) & pending
                ]
                if wanted
                else []
            )
            if not units:
                raise HTTPException(409, "Word timing for this passage is already on its way.")
            ids = [i for unit in units for i in unit["ids"]]

        # Runtime/network checks never hold the queue lock. No whole-source inspection is started.
        options, extra = routing.repair_route(
            store,
            credentials,
            recording,
            source,
            units,
            options,
            cloud=release.enabled("ucloud_enhancement"),
        )
        with queue.lock:
            current = store.recording(identifier)
            if (
                current["active_transcript_id"] != body.result_id
                or current["revision"] != body.revision
            ):
                raise HTTPException(
                    409,
                    "This result changed while checking processing. Reload before adding timing.",
                )
            if set(ids) & pending_ids():
                raise HTTPException(409, "Word timing for this passage is already on its way.")
            if options.execution == "ucloud" and "audio_input" in extra:
                cache = original_inspection.read(store, identifier)
                frozen = extra["repair_routing"]
                if (
                    cache is None
                    or cache["state"] != "ready"
                    or cache["generation"] != frozen["source_generation"]
                    or cache["receipt_sha256"] != frozen["source_receipt_sha256"]
                    or cache["receipt"] != extra["audio_input"]
                ):
                    raise HTTPException(
                        409, "The original-v1 source proof changed. Retry word timing."
                    )
            job = store.enqueue_enhancement(
                identifier,
                body.result_id,
                body.revision,
                {
                    "enhancement": options.model_dump(),
                    "timing_units": units,
                    **({"segment_id": ids[0]} if len(ids) == 1 else {"segment_ids": ids}),
                    "realign": True,
                    "passage_timing": True,
                    "provider_name": ("UCloud" if options.execution == "ucloud" else "Local")
                    + " word timing for "
                    + ("one passage" if len(ids) == 1 else f"{len(ids)} passages"),
                    **({"language_preference": preference} if preference else {}),
                    **extra,
                },
                selected_ids=ids,
                language_guard=language_guard,
            )
        queue.notify()
        return job

    @app.post("/api/recordings/{identifier}/realign/{job_id}")
    def apply_realign(identifier: str, job_id: str, body: RealignApply):
        """Add realigned word timing to the same result as a timing-only version (ADR 0052)."""
        job = store.job(job_id)
        params = job["params"]
        owned = job["recording_id"] == identifier and job["kind"] == "enhance"
        if not owned or not params.get("realign"):
            raise KeyError(job_id)
        from . import repair_cloud

        source = params["source_snapshot"]
        if body.result_id is not None and body.result_id != source["id"]:
            raise HTTPException(409, "Open the result this word timing belongs to.")
        if params.get("repair_cloud", {}).get("protocol") == repair_cloud.PROTOCOL:
            if body.result_id != source["id"]:
                raise HTTPException(409, "Choose the exact result before applying UCloud timing.")
            try:
                proof = repair_cloud.validate_local_outputs(store, job)
                return store.apply_repair_timing(
                    identifier,
                    body.result_id,
                    job_id,
                    body.revision,
                    proof["fingerprint"],
                    working_choice=working_choice(body),
                )
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
        try:
            return store.apply_local_timing(
                identifier, source["id"], job_id, body.revision, working_choice=working_choice(body)
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    def structure_recording(identifier, body):
        current = store.recording(identifier)
        if (
            current["revision"] != body.revision
            or current["active_transcript_id"] != body.result_id
        ):
            raise ValueError("This result changed in another window. Reload before saving.")
        return current

    @app.get("/api/recordings/{identifier}/merge-undo-options")
    def passage_merge_undo_options(identifier: str, result_id: str):
        from . import passage_undo

        try:
            return passage_undo.options(store, identifier, result_id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/recordings/{identifier}/segments/{segment_id}/undo-merge")
    def undo_passage_merge(identifier: str, segment_id: str, body: PassageStructureEdit):
        from . import passage_undo

        try:
            saved = passage_undo.apply(
                store,
                identifier,
                body.result_id,
                segment_id,
                body.revision,
                body.reason,
                working_choice=working_choice(body),
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return json_response(saved)

    @app.post("/api/recordings/{identifier}/segments/{segment_id}/undo-split")
    def undo_passage_split(identifier: str, segment_id: str, body: PassageStructureEdit):
        from . import passage_undo

        try:
            saved = passage_undo.apply_split(
                store,
                identifier,
                body.result_id,
                segment_id,
                body.revision,
                body.reason,
                working_choice=working_choice(body),
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return json_response(saved)

    @app.post("/api/recordings/{identifier}/segments/{segment_id}/merge")
    def merge_segment(identifier: str, segment_id: str, body: PassageMerge):
        current = structure_recording(identifier, body)
        segments = merge_passages(
            current["segments"], segment_id, body.direction, body.speaker_id, current["speakers"]
        )
        index = next(
            i for i, segment in enumerate(current["segments"]) if segment["id"] == segment_id
        )
        adjacent = current["segments"][index + (-1 if body.direction == "up" else 1)]["id"]
        return json_response(
            store.save_transcript(
                identifier,
                segments,
                current["speakers"],
                body.revision,
                action="passage_merge",
                context={
                    "segment_id": segment_id,
                    "adjacent_segment_id": adjacent,
                    "direction": body.direction,
                },
                reason=body.reason,
                result_id=body.result_id,
                working_choice=working_choice(body),
            )
        )

    @app.post("/api/recordings/{identifier}/segments/{segment_id}/split")
    def split_segment(identifier: str, segment_id: str, body: PassageSplit):
        return save_passage_split(identifier, segment_id, body)

    @app.post("/api/recordings/{identifier}/segments/{segment_id}/split-v2")
    def split_segment_v2(identifier: str, segment_id: str, body: PassageSplit):
        return save_passage_split(identifier, segment_id, body, manual=True)

    def save_passage_split(identifier, segment_id, body, *, manual=False):
        current = structure_recording(identifier, body)
        split = split_passage_manual if manual else split_passage
        segments = split(current["segments"], segment_id, body.cursor, body.split_time)
        return json_response(
            store.save_transcript(
                identifier,
                segments,
                current["speakers"],
                body.revision,
                action="passage_split",
                context={
                    "segment_id": segment_id,
                    "cursor": body.cursor,
                    **({"split_policy": MANUAL_SPLIT_POLICY} if manual else {}),
                    **({"split_time": body.split_time} if body.split_time is not None else {}),
                },
                reason=body.reason,
                result_id=body.result_id,
                working_choice=working_choice(body),
            )
        )

    @app.post("/api/recordings/{identifier}/segments/{segment_id}/delete")
    def delete_segment(identifier: str, segment_id: str, body: PassageStructureEdit):
        current = structure_recording(identifier, body)
        return json_response(
            store.save_transcript(
                identifier,
                delete_passage(current["segments"], segment_id),
                current["speakers"],
                body.revision,
                action="passage_delete",
                context={"segment_id": segment_id},
                reason=body.reason,
                result_id=body.result_id,
                working_choice=working_choice(body),
            )
        )

    @app.patch("/api/recordings/{identifier}/segments/{segment_id}/speaker")
    def update_passage_speaker(identifier: str, segment_id: str, body: PassageSpeakerEdit):
        current = store.recording(identifier)
        segment = next((s for s in current["segments"] if s["id"] == segment_id), None)
        if segment is None:
            raise KeyError(segment_id)
        if (body.speaker_id is None) == (body.name is None):
            raise ValueError("Choose one existing speaker or enter a new speaker name.")
        known = {s["speaker"] for s in current["segments"]} | set(current["speakers"])
        if body.speaker_id is not None:
            if body.speaker_id not in known:
                raise ValueError("Choose an existing speaker from this result.")
            speaker = body.speaker_id
        else:
            name = clean_name(body.name)
            speaker = "MANUAL_" + new_id()
            while speaker in known:
                speaker = "MANUAL_" + new_id()
            current["speakers"][speaker] = name
        segment["speaker"] = speaker
        return json_response(
            store.save_transcript(
                identifier,
                current["segments"],
                current["speakers"],
                body.revision,
                action="passage_speaker",
                context={"segment_id": segment_id},
                reason=body.reason,
                working_choice=working_choice(body),
            )
        )

    @app.patch("/api/recordings/{identifier}/speakers")
    def update_speaker(identifier: str, body: SpeakerEdit):
        current = store.recording(identifier)
        if body.speaker not in {s["speaker"] for s in current["segments"]}:
            raise ValueError("Choose an existing speaker.")
        current["speakers"][body.speaker] = clean_name(body.name)
        return json_response(
            store.save_transcript(
                identifier,
                current["segments"],
                current["speakers"],
                body.revision,
                action="speaker_rename",
                reason=body.reason,
                working_choice=working_choice(body),
            )
        )

    @app.get("/api/recordings/{identifier}/media/{variant}")
    def playback(identifier: str, variant: Literal["audio", "video", "original"]):
        current = store.recording(identifier)
        directory = store.directory(identifier)
        filename = {
            "audio": "audio.mp3",
            "video": "video.mp4",
            "original": ("original" + Path(current["filename"]).suffix.lower()),
        }[variant]
        path = directory / filename
        if not path.is_file():
            raise HTTPException(
                404, "Playback is still being prepared or this view is unavailable."
            )
        return FileResponse(
            path,
            filename=current["filename"] if variant == "original" else filename,
            content_disposition_type="attachment" if variant == "original" else "inline",
        )

    @app.get("/api/recordings/{identifier}/export")
    def export(
        identifier: str,
        grouping: Literal["speaker", "timestamp"] = "speaker",
        result_id: str | None = None,
        export_format: Annotated[ExportFormat, Query(alias="format")] = "text",
    ):
        recording = store.recording(identifier)
        current = store.result(identifier, result_id) if result_id else recording
        content = export_bytes(current["segments"], current["speakers"], grouping, export_format)
        filename = export_filename(recording["filename"], export_format=export_format)
        return Response(
            content,
            media_type=media_type(export_format),
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
            },
        )

    def export_archive_response(archive, filename):
        def chunks():
            try:
                while data := archive.read(65536):
                    yield data
            finally:
                archive.close()

        return StreamingResponse(
            chunks(),
            media_type="application/zip",
            background=BackgroundTask(archive.close),
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/projects/{identifier}/export")
    def export_project(
        identifier: str,
        grouping: Literal["speaker", "timestamp"] = "speaker",
        export_format: Annotated[ExportFormat, Query(alias="format")] = "text",
        results: Literal["working", "active", "all"] = "working",
        collection: Annotated[str | None, Query(max_length=100)] = None,
    ):
        archive = project_archive(
            store, identifier, export_format, grouping, results, collection or None
        )
        return export_archive_response(archive, f"project-{identifier}.zip")

    @app.post("/api/projects/{identifier}/exports")
    def export_project_selection(identifier: str, body: ProjectExports):
        archive = project_archive(
            store,
            identifier,
            grouping=body.grouping,
            results=body.results,
            collection=body.collection,
            recording_ids=body.recording_ids,
            formats=body.formats,
            include_originals=body.include_originals,
        )
        return export_archive_response(archive, f"project-{identifier}.zip")

    @app.post("/api/projects/{identifier}/export-tasks", status_code=202)
    def prepare_project_export(identifier: str, body: ProjectExports):
        return export_manager.start(identifier, **body.model_dump())

    @app.post("/api/recordings/{identifier}/export-tasks", status_code=202)
    def prepare_recording_export(identifier: str, body: RecordingExports):
        return export_manager.start_recording(identifier, **body.model_dump())

    @app.get("/api/export-tasks")
    def export_tasks():
        return {"tasks": export_manager.activity()}

    @app.get("/api/export-tasks/{identifier}/download")
    def download_prepared_export(identifier: str):
        return DownloadResponse(export_manager.open_download(identifier))

    @app.post("/api/export-tasks/{identifier}/cancel")
    def cancel_prepared_export(identifier: str):
        return export_manager.cancel(identifier)

    @app.delete("/api/export-tasks/{identifier}/discard")
    def discard_prepared_export(identifier: str):
        return export_manager.discard(identifier)

    @app.post("/api/recordings/{identifier}/exports")
    def export_recording_formats(identifier: str, body: RecordingExports):
        archive = recording_archive(
            store,
            identifier,
            body.result_id,
            body.formats,
            body.grouping,
            include_originals=body.include_originals,
        )
        return export_archive_response(archive, f"recording-{identifier}.zip")

    @app.post("/api/projects/{identifier}/research-report")
    def research_report(identifier: str, body: ResearchReport):
        from .research_reports import project_report

        content = project_report(
            store,
            identifier,
            selections=[selection.model_dump() for selection in body.selections]
            if body.selections is not None
            else None,
            include_selected_transcripts=body.include_selected_transcripts,
            **({"collection": body.collection} if body.collection else {}),
        )
        return Response(
            content,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="project-research-report.zip"'},
        )

    @app.get("/api/settings")
    def settings():
        return public_settings(store, credentials)

    @app.get("/api/providers")
    def providers():
        return public_profiles(store, credentials)

    @app.post("/api/providers/check-models")
    def available_models(body: ProviderEdit):
        settings = Settings.model_validate(body.model_dump())
        key = body.api_key if body.api_key else credentials.get(settings.base_url)
        return check_models(settings, key)

    @app.post("/api/providers/check-ucloud")
    def check_ucloud(body: ProviderEdit):
        from .ucloud_native import check_setup

        settings = Settings.model_validate(body.model_dump())
        if settings.provider_type != "ucloud":
            raise ValueError("Choose the UCloud Transcriber provider.")
        key = body.api_key or credentials.get(credential_scope(settings))
        return check_setup(settings, key)

    @app.get("/api/providers/catalog")
    def model_catalog():
        return speech_catalog()

    @app.get("/api/providers/model-presets")
    def model_presets():
        return provider_catalog()

    @app.post("/api/providers/discover-ucloud")
    def discover_ucloud(body: ProviderEdit):
        from .ucloud_discovery import discover
        from .ucloud_native import COMPUTE_ID, MIG_PRODUCT

        settings = fresh_native_settings(
            Settings.model_validate(body.model_dump(exclude_unset=True))
        )
        if settings.provider_type != "ucloud":
            raise ValueError("Choose the UCloud Transcriber provider.")
        key = body.api_key or credentials.get(credential_scope(settings))
        requested = body.native_allocation_choice
        if requested not in (None, "full", "mig1g"):
            raise ValueError("Choose full B200 or native 1g MIG for discovery.")
        target = COMPUTE_ID if requested == "full" else MIG_PRODUCT["id"]
        return discover(
            settings,
            key,
            compute_id=None
            if settings.ucloud_transcriber.template_job_id and requested is None
            else target,
        )

    @app.post("/api/providers", status_code=201)
    def create_provider(body: ProviderConfig):
        release.require_provider(body.provider_type)
        if "approved_for" not in body.model_fields_set:
            body.approved_for = ["protected"] if PROTECTED_DEFAULTS[body.provider_type] else []
        if (
            body.provider_type == "ucloud"
            and "cleanup_after_success" not in body.ucloud_transcriber.model_fields_set
        ):
            body.ucloud_transcriber.cleanup_after_success = True
        try:
            return save_profile(
                store,
                credentials,
                clean_name(body.name),
                Settings.model_validate(body.model_dump(exclude_unset=True)),
                body.api_key,
                body.remember,
                enabled_models=body.enabled_models,
                enabled_models_touched=body.enabled_models_touched,
                enabled_model_versions=body.enabled_model_versions,
                ucloud_token=body.ucloud_token,
                hf_token=body.hf_token,
                native_allocation_choice=body.native_allocation_choice,
            )
        except StaleProvider as exc:
            raise HTTPException(409, str(exc)) from None

    @app.put("/api/providers/default")
    def choose_default_provider(body: ProviderChoice):
        if not body.provider_id:
            raise ValueError("Choose a configured provider.")
        set_default_provider(store, body.provider_id)
        return public_profiles(store, credentials)

    @app.put("/api/providers/{identifier}")
    def update_provider(identifier: str, body: ProviderConfig):
        release.require_provider(body.provider_type)
        release.require_provider(profile(store, identifier)["settings"]["provider_type"])
        try:
            return save_profile(
                store,
                credentials,
                clean_name(body.name),
                Settings.model_validate(body.model_dump(exclude_unset=True)),
                body.api_key,
                body.remember,
                identifier,
                enabled_models=body.enabled_models,
                enabled_models_touched=body.enabled_models_touched,
                enabled_model_versions=body.enabled_model_versions,
                expected_version=body.expected_version,
                ucloud_token=body.ucloud_token,
                hf_token=body.hf_token,
            )
        except StaleProvider as exc:
            raise HTTPException(409, str(exc)) from None

    @app.delete("/api/providers/{identifier}")
    def remove_provider(identifier: str):
        delete_profile(store, identifier)
        return public_profiles(store, credentials)

    @app.put("/api/providers/{identifier}/runtime-language")
    def runtime_language(identifier: str, body: RuntimeLanguageChoice):
        try:
            return save_runtime_language(
                store, credentials, identifier, body.language, body.expected_version
            )
        except StaleProvider as exc:
            raise HTTPException(409, str(exc)) from None

    @app.put("/api/providers/{identifier}/native-allocation")
    def native_allocation(identifier: str, body: NativeAllocationChoice):
        try:
            return save_native_allocation(
                store, credentials, identifier, body.allocation, body.expected_version
            )
        except StaleProvider as exc:
            raise HTTPException(409, str(exc)) from None

    @app.put("/api/providers/{identifier}/native-language")
    def native_language(identifier: str, body: NativeLanguageChoice):
        try:
            return save_native_language(
                store, credentials, identifier, body.language, body.expected_version
            )
        except StaleProvider as exc:
            raise HTTPException(409, str(exc)) from None

    @app.put("/api/settings")
    def update_settings(body: ProviderEdit):
        release.require_provider(body.provider_type)
        try:
            return save_settings(
                store,
                credentials,
                Settings.model_validate(body.model_dump(exclude_unset=True)),
                body.api_key,
                body.remember,
                expected_version=body.expected_version,
                ucloud_token=body.ucloud_token,
                hf_token=body.hf_token,
            )
        except StaleProvider as exc:
            raise HTTPException(409, str(exc)) from None

    def conflicting_job(recording_id, provider_id, settings, *, original=False):
        active = store.all(
            "SELECT id,kind FROM jobs WHERE recording_id=? AND state IN ('queued','running')",
            (recording_id,),
        )
        for row in operational_rows(active):
            if original and row["kind"] in {"media", "source-inspect"}:
                continue
            if row["kind"] == "media":
                return True
            params = store.job(row["id"])["params"]
            if params.get("provider_id") == provider_id or (
                not params.get("provider_id") and params.get("settings") == settings
            ):
                return True
        return False

    @app.post("/api/transcriptions", status_code=202)
    def transcribe(body: Transcribe):
        identifiers = list(dict.fromkeys(body.provider_ids or [default_provider_id(store)]))
        selected, native_allocations = admission_profiles(store, identifiers)
        recording_ids = list(dict.fromkeys(body.recording_ids))
        if len(recording_ids) * len(selected) > 100:
            raise ValueError("Select at most 100 recording/provider combinations at once.")
        if body.enhancement is not None:
            body.enhancement = placed(body.enhancement)
        for provider in selected:
            settings = Settings.model_validate(provider["settings"])
            release.require_provider(settings.provider_type, runtime=bool(whisperx_model(settings)))
            require_enabled(settings)
            require_key(settings, credentials.get(credential_scope(settings)))
            if settings.provider_type == "ucloud" and not whisperx_model(settings):
                from .ucloud_native import require_compute

                require_compute(settings)
        # Acceptance freezes choices only. Source inspection and full preflight run in workers;
        # only a short read-only runtime readiness probe may ask for consent first (ADR 0098).
        saved = local_settings(store)
        plans = []
        for identifier in recording_ids:
            recording = store.recording(identifier)
            language_guard = language_preferences.guard(store, recording)
            preference = language_preferences.selected(language_guard)
            route = input_admission.snapshot_routing(
                store, credentials, recording["project_id"], body.enhancement
            )
            plans.extend(
                (
                    identifier,
                    language_preferences.apply_to_provider(provider, preference),
                    route,
                    language_guard,
                    preference,
                )
                for provider in selected
            )

        def item(identifier, provider, route, language_guard, preference, consent):
            extras = body.enhancement
            requested = body.requested_features or FeatureRequest()
            native = native_features(Settings.model_validate(provider["settings"]))
            needs_followup = (requested.word_timing and not native.word_timing) or (
                requested.speaker_labels and not native.speaker_labels
            )
            if extras is not None and needs_followup and preference is not None:
                language = preference["language"]
                needs_alignment = requested.word_timing and not native.word_timing
                if language not in {"da", "en"} and needs_alignment:
                    raise ValueError(
                        "Word timing follow-ups currently support Danish and English only."
                    )
                if language in {"da", "en"}:
                    extras = EnhancementOptions.model_validate(
                        {**extras.model_dump(), "language": language}
                    )
            if extras is not None and consent is not None:
                extras = extras.model_copy(update={"prepare_runtime": True})
            return {
                "recording_id": identifier,
                "language_guard": language_guard,
                "choice": input_admission.choice(
                    provider=provider,
                    native_allocation=native_allocations.get(provider["id"]),
                    requested_features=(body.requested_features or FeatureRequest()).model_dump(),
                    enhancement=extras.model_dump() if extras is not None else None,
                    prepare_runtime=consent is not None,
                    routing_snapshot=route,
                    replace=body.replace or body.provider_ids is not None,
                    language_default=saved.language,
                    prepare_consent=consent,
                    language_preference=preference,
                ),
            }

        consent = "request" if body.prepare_runtime else None
        items = [item(*plan, consent) for plan in plans]
        if consent is None:
            items = consented_items(items, plans, item, saved)
        with queue.lock:
            jobs = input_admission.accept(store, items)
        queue.notify()
        return jobs

    def consented_items(items, plans, build, saved):
        """Ask once for the whole request, or freeze standing consent (ADR 0098)."""
        try:
            decisions = input_admission.preparation_decisions(
                credentials, items, standing=saved.prepare_ucloud_models
            )
        except input_admission.PreparationRequired as error:
            raise HTTPException(
                409,
                {
                    "code": "prepare_runtime",
                    "message": str(error),
                    "recordings": error.recordings,
                },
            ) from None
        return [
            build(*plan, decision) if decision is not None else current
            for current, plan, decision in zip(items, plans, decisions, strict=True)
        ]

    hf_receipts = {}

    @app.get("/api/huggingface-settings")
    def huggingface_settings():
        token = credentials.get(HF_SCOPE)
        receipt = hf_receipts.get(
            huggingface_access.token_fingerprint(token), huggingface_access.token_state(token)
        )
        # Offline: whether verified speaker files are on this computer right now. A failed
        # check keeps its reason until the files are there.
        local = enhancement_api.local_speaker_status()
        if local["speaker_model_local"] == "verified" or "speaker_model_local" not in receipt:
            return receipt | local
        return receipt

    @app.put("/api/huggingface-settings")
    def update_huggingface_settings(body: HuggingFaceToken):
        if body.hf_token is not None:
            token = body.hf_token.strip()
            if any(c.isspace() or ord(c) < 32 for c in token):
                raise ValueError("Use a Hugging Face access token without spaces or line breaks.")
            credentials.set(HF_SCOPE, token, body.remember)
        return huggingface_settings()

    @app.post("/api/huggingface-settings/check")
    def check_huggingface_settings(body: HuggingFaceToken):
        token = body.hf_token.strip() if body.hf_token else credentials.get(HF_SCOPE)
        result = huggingface_access.check_token(token)
        # The check also prepares the speaker model here (ADR 0057): with access granted it is
        # downloaded once, retried on transient errors and verified file by file.
        allowed = result["token_status"] == "valid" and result["speaker_model_access"] == "allowed"
        result |= enhancement_api.local_speaker_status(token if allowed else None)
        # This is only a last-check receipt, not lasting authorization for inference.
        # Credential changes cannot reuse a previous token's successful check.
        hf_receipts[huggingface_access.token_fingerprint(token)] = result
        while len(hf_receipts) > 8:
            hf_receipts.pop(next(iter(hf_receipts)))
        return result

    @app.get("/api/enhancement-settings")
    def enhancement_settings():
        return local_settings(store).model_dump() | {
            "has_hf_token": bool(credentials.get(HF_SCOPE))
        }

    @app.put("/api/enhancement-settings")
    def update_enhancement_settings(body: LocalSettingsEdit):
        if body.hf_token is not None:
            credentials.set(HF_SCOPE, body.hf_token, body.remember)
        preserve = set(ProcessingSettings.model_fields) - body.model_fields_set
        save_local_settings(store, body, preserve_fields=preserve)
        return enhancement_settings()

    @app.post("/api/enhancement-runtime/check")
    def enhancement_runtime(body: LocalSettings):
        return hardware_status(body, refresh=True)

    @app.get("/api/local-concurrency")
    def local_concurrency_settings():
        """Settings → Local Processing: parallel local tasks per lane (ADR 0146)."""
        return local_concurrency.status(store, queue)

    @app.put("/api/local-concurrency")
    def update_local_concurrency(body: local_concurrency.LocalConcurrency):
        # Omitted lanes keep their saved value; null restores automatic. New claims follow
        # the new limits at once; running tasks are never stopped by a lower limit. The claim
        # lock orders concurrent saves so the applied limits always match the saved row.
        with queue.lock:
            queue.set_lane_limits(local_concurrency.save(store, body))
        return local_concurrency.status(store, queue)

    @app.get("/api/local-runtime")
    def local_runtime():
        """Provider Settings → Processing: the last check of this computer, never a new one."""
        settings = local_settings(store)
        status = cached_hardware(settings)
        gpu = ((status or {}).get("gpu") or {}).get("available")
        return {
            "status": status,
            "device": settings.device,
            "effective_device": "cuda"
            if settings.device == "cuda" or (settings.device == "auto" and gpu)
            else "cpu",
        }

    @app.get("/api/enhancement-routing")
    def enhancement_routing():
        return routing.limits(routing.rules(store))

    @app.put("/api/enhancement-routing")
    def update_enhancement_routing(body: routing.Rules):
        return routing.limits(routing.save_rules(store, body))

    @app.get("/api/enhancement-api/status")
    def enhancement_api_status():
        """Offline summary of the Provider Settings connection used for UCloud enhancement."""
        limits = routing.limits(routing.rules(store))
        try:
            chosen = enhancement_api.connection(store, credentials, [])
        except ValueError as exc:
            return limits | {"connected": False, "message": str(exc)}
        settings = Settings.model_validate(chosen["settings"])
        return limits | {
            "connected": True,
            "provider_name": "UCloud",
            "project_id": settings.ucloud_transcriber.project_id,
            "storage_path": settings.ucloud_transcriber.storage_path,
            "runtime_id": enhancement_api.runtime_id(),
            "provider_id": chosen["provider_id"],
            "connection_fingerprint": runtime_preparation.fingerprint(chosen),
        }

    @app.post("/api/enhancement-api/check")
    def check_enhancement_api():
        """Read-only storage check; starts no job and uploads nothing."""
        return enhancement_api.runtime_prepared(store, credentials)

    @app.post("/api/enhancement-api/prepare")
    def prepare_enhancement_api(body: RuntimePreparationRequest):
        result = runtime_preparation.request(store, credentials, body.connection_fingerprint)
        queue.notify()
        return JSONResponse(result, status_code=202 if result["operation"] else 200)

    @app.post("/api/runtime-preparations/{identifier}/cancel", status_code=202)
    def cancel_runtime_preparation(identifier: str):
        result = runtime_preparation.cancel(store, credentials, identifier)
        queue.notify()
        return result

    @app.post("/api/runtime-preparations/{identifier}/retry", status_code=202)
    def retry_runtime_preparation(identifier: str):
        result = runtime_preparation.retry(store, credentials, identifier)
        queue.notify()
        return result

    @app.post("/api/runtime-preparations/{identifier}/reconcile")
    def reconcile_runtime_preparation(identifier: str, body: RuntimePreparationReconcile):
        result = runtime_preparation.reconcile(store, credentials, identifier, **body.model_dump())
        queue.notify()
        return result

    @app.post("/api/runtime-preparations/{identifier}/cleanup")
    def cleanup_runtime_preparation(identifier: str):
        return runtime_preparation.cleanup(store, credentials, identifier)

    @app.post("/api/runtime-preparations/{identifier}/hide")
    def hide_runtime_preparation(identifier: str):
        return runtime_preparation.hide(store, identifier)

    @app.get("/api/enhancement-cloud-settings")
    def cloud_enhancement_settings():
        settings = enhancement_cloud.load_cloud_settings(store)
        return settings.model_dump() | {
            "has_api_token": bool(credentials.get(enhancement_cloud.token_scope(settings)))
        }

    @app.put("/api/enhancement-cloud-settings")
    def update_cloud_enhancement_settings(body: CloudEnhancementEdit):
        settings = enhancement_cloud.CloudEnhancementSettings.model_validate(body.model_dump())
        if "approved_for_protected" not in body.model_fields_set:
            settings.approved_for_protected = enhancement_cloud.load_cloud_settings(
                store
            ).approved_for_protected
        if body.api_token is not None:
            credentials.set(enhancement_cloud.token_scope(settings), body.api_token, body.remember)
        enhancement_cloud.save_cloud_settings(store, settings)
        return cloud_enhancement_settings()

    @app.post("/api/enhancement-cloud/check")
    def cloud_enhancement_check():
        return enhancement_cloud.check_setup(
            enhancement_cloud.load_cloud_settings(store), credentials
        )

    def enhancement_source(identifier, body, options, *, audio_input=None):
        recording = require_enhancement_idle(identifier, options)
        if audio_input is not None:
            recording = audio_inputs.effective_recording(
                {"params": {"audio_input": audio_input}}, recording
            )
        source = store.freeze_result(identifier, body.result_id, body.revision)
        if body.segment_id is not None and body.segment_ids is not None:
            raise ValueError("Choose one passage or a list of passages, not both.")
        if body.segment_id is not None:
            passage_input(source, body.segment_id, options, recording["duration"])
        elif body.segment_ids is not None:
            passage_inputs(source, body.segment_ids, options, recording["duration"])
        else:
            validate_input(source, options, recording["duration"])
        return recording, source

    def require_enhancement_idle(identifier, options, *, original=False):
        recording = store.recording(identifier)
        # One active enhancement per recording and lane (ADR 0049); auto waits for both lanes.
        safe = "CASE WHEN json_valid(params) THEN params ELSE 'null' END"
        remote = f"COALESCE(json_extract({safe},'$.enhancement.execution'),'')='ucloud'"
        lane = {"ucloud": f" AND {remote}", "local": f" AND NOT {remote}"}.get(
            options.execution, ""
        )
        if (not original and recording["status"] != "ready") or operational_rows(
            store.all(
                "SELECT id FROM jobs WHERE recording_id=? AND kind='enhance' "
                "AND state IN ('queued','running')" + lane,
                (identifier,),
            )
        ):
            raise HTTPException(
                409, "Wait for media preparation or this recording's active enhancement."
            )
        return recording

    @app.post("/api/recordings/{identifier}/enhancements", status_code=202)
    def enhance_transcript(identifier: str, body: EnhanceRequest):
        options = placed(input_admission.fresh_options(body.model_dump()))
        recording = store.recording(identifier)
        options, language_guard, preference = language_preferences.enhancement_options(
            store, recording, options
        )
        repair = body.segment_id is not None or body.segment_ids is not None
        if not repair:
            recording = require_enhancement_idle(identifier, options, original=True)
            route = input_admission.snapshot_routing(
                store, credentials, recording["project_id"], options
            )

            def build(identifier, options, route, language_guard, preference, consent):
                if consent is not None:
                    options = options.model_copy(update={"prepare_runtime": True})
                return {
                    "recording_id": identifier,
                    "language_guard": language_guard,
                    "choice": input_admission.choice(
                        options=options.model_dump(),
                        routing_snapshot=route,
                        source={"result_id": body.result_id, "revision": body.revision},
                        prepare_consent=consent,
                        language_preference=preference,
                    ),
                }

            plan = (identifier, options, route, language_guard, preference)
            [chosen] = consented_items([build(*plan, None)], [plan], build, local_settings(store))
            with queue.lock:
                job = input_admission.accept(store, [chosen])[0]
            queue.notify()
            return job
        routed, verified = {}, False
        if repair and options.execution == "ucloud":
            raise ValueError("Passage repairs run on this computer; choose local processing.")
        if repair and options.execution == "auto":
            options = options.model_copy(update={"execution": "local", "prepare_runtime": False})
            routed = {"routing": {"mode": "auto", "reason": "Passage repairs run here."}}
        require_enhancement_idle(identifier, options)
        store.freeze_result(identifier, body.result_id, body.revision)
        # Stage 2 owns cached timebase proof for bounded repairs; keep their current path.
        audio_input = None
        recording, _ = enhancement_source(identifier, body, options, audio_input=audio_input)
        if options.execution == "auto":
            # Runtime and storage checks can take seconds; keep the queue lock available.
            options, extra, reason = routing.Router(store, credentials, options).decide(
                recording, options
            )
            routed, verified = {**extra, "routing": {"mode": "auto", "reason": reason}}, True
        if options.execution == "local" and not verified:
            # Interpreter checks can take seconds. Keep the queue claim lock available.
            require_local_runtime(options)
        cloud = {}
        if options.execution == "ucloud" and "ucloud_api" not in routed:
            # New UCloud enhancements use the Provider Settings connection over the API. Its
            # UCloud and Hugging Face checks are network reads: outside the queue lock.
            options, cloud = enhancement_api.freeze(
                store, credentials, [recording["project_id"]], options
            )
            # An unprepared runtime is asked about once, unless Settings → Processing holds
            # standing consent (ADR 0098); it is never prepared implicitly otherwise.
            if (
                not options.prepare_runtime
                and "ucloud_api" in cloud
                and enhancement_api.runtime_stamped(credentials, cloud["ucloud_api"]) is False
            ):
                if local_settings(store).prepare_ucloud_models:
                    options = options.model_copy(update={"prepare_runtime": True})
                else:
                    raise HTTPException(
                        409,
                        {
                            "code": "prepare_runtime",
                            "message": enhancement_api.PREPARE_QUESTION.format(detail=""),
                        },
                    )
        with queue.lock:
            recording, source = enhancement_source(
                identifier, body, options, audio_input=audio_input
            )
            target_ids = set(body.segment_ids or [])
            # The project's approval is checked again under the lock, as for transcriptions.
            if "ucloud_api" in cloud:
                require_approval(
                    store,
                    recording["project_id"],
                    cloud["ucloud_api"]["provider_id"],
                    Settings.model_validate(cloud["ucloud_api"]["settings"]),
                )
            elif "cloud_settings" in cloud:
                enhancement_cloud.require_approval(
                    store,
                    recording["project_id"],
                    enhancement_cloud.CloudEnhancementSettings.model_validate(
                        cloud["cloud_settings"]
                    ),
                )
            if (
                options.execution == "local"
                and options.diarize
                and options.allow_downloads
                and not credentials.get(HF_SCOPE)
            ):
                raise ValueError(
                    "Add a Hugging Face read token and accept Community-1 terms "
                    "before downloading speaker models."
                )
            job = store.enqueue_enhancement(
                identifier,
                body.result_id,
                body.revision,
                {
                    "enhancement": options.model_dump(),
                    **({"audio_input": audio_input} if audio_input is not None else {}),
                    **({"segment_id": body.segment_id} if body.segment_id is not None else {}),
                    **(
                        {
                            "segment_ids": [
                                segment["id"]
                                for segment in source["segments"]
                                if segment["id"] in target_ids
                            ]
                        }
                        if body.segment_ids is not None
                        else {}
                    ),
                    "provider_name": ("UCloud" if options.execution == "ucloud" else "Local")
                    + " WhisperX enhancement",
                    **({"language_preference": preference} if preference else {}),
                    **cloud,
                    **routed,
                },
                language_guard=language_guard,
            )
        queue.notify()
        return job

    @app.get("/api/activity")
    def activity():
        return activity_state(store)

    @app.post("/api/activity/clear")
    def clear_finished_activity():
        # Hides finished jobs from the Activity list only; every job row is kept (V105).
        return {"cleared_at": clear_activity(store)}

    @app.post("/api/activity/hide")
    def hide_from_activity(body: ActivityHide):
        # Removes one finished card from the Activity list; every row is kept (V162).
        try:
            return hide_activity(store, body.job_id)
        except ActivityRetained as error:
            raise HTTPException(409, str(error)) from None

    @app.get("/api/jobs")
    def jobs():
        # Job rows deliberately exclude settings snapshots and transcript chunk bodies.
        query = (
            "WITH safe_jobs AS (SELECT id,recording_id,kind,state,progress,message,created,updated,"
            "CASE WHEN json_valid(params) THEN params ELSE 'null' END AS params,"
            "CASE WHEN json_valid(params) THEN json_type(params)='object' ELSE 0 END "
            "AS params_valid FROM jobs), "
            "safe_native AS (SELECT id,state,CASE WHEN json_valid(params) THEN params "
            "ELSE 'null' END AS params FROM native_batch_runs) "
            "SELECT j.id,j.recording_id,j.kind,j.state,j.progress,j.message,j.created,j.updated, "
            "CASE WHEN j.params_valid THEN json_type(j.params,'$.input_intent') "
            "ELSE 'invalid' END AS input_intent, "
            "json_extract(j.params,'$.provider_name') AS provider_name, "
            "json_extract(j.params,'$.settings.model') AS model, "
            "json_extract(j.params,'$.segment_id') AS segment_id, "
            "json_extract(j.params,'$.segment_ids') AS segment_ids, "
            "json_extract(j.params,'$.source_snapshot.id') AS source_result_id, "
            "(SELECT t.id FROM transcript_results t WHERE t.job_id=j.id "
            "AND t.id NOT IN (SELECT result_id FROM result_trash)) AS result_id, "
            "json_extract(j.params,'$.ucloud_run.job_id') AS ucloud_job_id, "
            "json_extract(j.params,'$.ucloud_run.termination_requested') AS ucloud_stop_requested, "
            "json_extract(j.params,'$.ucloud_run.cancellation_requested') "
            "AS ucloud_cancel_requested, "
            "json_extract(j.params,'$.ucloud_run.remote_state') AS ucloud_remote_state, "
            "json_extract(j.params,'$.ucloud_run.cleanup_state') AS ucloud_cleanup_state, "
            "json_extract(j.params,'$.ucloud_run.cleanup_error') AS ucloud_cleanup_error, "
            "json_extract(j.params,'$.ucloud_run.cleanup_stage') AS ucloud_cleanup_stage, "
            "json_extract(j.params,'$.ucloud_run.cleanup_error_code') "
            "AS ucloud_cleanup_error_code, "
            "json_extract(j.params,'$.ucloud_run.cleanup_http_status') "
            "AS ucloud_cleanup_http_status, "
            "json_extract(j.params,'$.ucloud_run.cleanup_attempted_at') "
            "AS ucloud_cleanup_attempted_at, "
            "(json_extract(j.params,'$.ucloud_run.job_id') IS NULL AND "
            "(json_extract(j.params,'$.ucloud_run.submission_attempted')=1 OR "
            "json_extract(j.params,'$.ucloud_run.submission_intent')=1) AND "
            "COALESCE(json_extract(j.params,'$.ucloud_run.reconciled_not_created'),0)=0) "
            "AS ucloud_submission_uncertain, "
            "CASE WHEN json_extract(j.params,'$.settings.provider_type')='ucloud' THEN "
            "json_extract(j.params,'$.settings.base_url') WHEN j.kind='enhance' THEN "
            "COALESCE(json_extract(j.params,'$.ucloud_api.settings.base_url'), "
            "json_extract(j.params,'$.cloud_settings.server')) END AS ucloud_server, "
            "json_extract(j.params,'$.native_batch_id') AS ucloud_batch_id, "
            "json_extract(j.params,'$.runtime_batch_id') AS runtime_batch_id, "
            "CASE WHEN j.kind='media' THEN json_extract(j.params,'$.upload_batch') END "
            "AS upload_batch, "
            "json_extract(j.params,'$.routing.reason') AS routing_reason, "
            "COALESCE(json_extract(j.params,'$.realign'),0) AS realign, "
            "(json_extract(j.params,'$.realign_output') IS NOT NULL AND "
            "json_extract(j.params,'$.realign_applied') IS NULL) AS realign_ready, "
            "json_extract(j.params,'$.enhancement.execution') AS execution, "
            "CASE WHEN j.kind='enhance' AND "
            "json_extract(j.params,'$.source_snapshot.source_version_id') IS NOT NULL THEN "
            "COALESCE((SELECT v.id FROM result_versions v WHERE "
            "v.result_id=json_extract(j.params,'$.source_snapshot.id') "
            "ORDER BY v.sequence DESC LIMIT 1),'') "
            "!=json_extract(j.params,'$.source_snapshot.source_version_id') "
            "ELSE 0 END AS source_changed, "
            "r.name AS recording_name,r.project_id FROM safe_jobs j JOIN recordings r "
            "ON r.id=j.recording_id WHERE "
            "j.id IN (SELECT id FROM safe_jobs ORDER BY created DESC LIMIT 200) "
            "OR j.state IN ('queued','running') "
            "OR json_extract(j.params,'$.ucloud_run.cleanup_state')='pending' "
            "OR json_extract(j.params,'$.native_batch_id') IN (SELECT id FROM safe_native "
            "WHERE state IN ('queued','running') OR "
            "json_extract(params,'$.ucloud_run.cleanup_state')='pending' OR "
            "(json_extract(params,'$.ucloud_run.job_id') IS NULL AND "
            "(json_extract(params,'$.ucloud_run.submission_attempted')=1 OR "
            "json_extract(params,'$.ucloud_run.submission_intent')=1) AND "
            "COALESCE(json_extract(params,'$.ucloud_run.reconciled_not_created'),0)=0) OR "
            "(state!='completed' AND json_extract(params,'$.ucloud_run.job_id') IS NOT NULL "
            "AND COALESCE(json_extract(params,'$.ucloud_run.termination_requested'),0)=0 "
            "AND COALESCE(json_extract(params,'$.ucloud_run.cancellation_requested'),0)=0 "
            "AND COALESCE(json_extract(params,'$.ucloud_run.remote_state'),'') NOT IN "
            "('SUCCESS','FAILURE','EXPIRED','ABORTED','CANCELLED','SUSPENDED'))) "
            "OR (j.state!='completed' AND (ucloud_submission_uncertain=1 OR "
            "(ucloud_job_id IS NOT NULL AND COALESCE(ucloud_stop_requested,0)=0 "
            "AND COALESCE(ucloud_cancel_requested,0)=0 "
            "AND COALESCE(ucloud_remote_state,'') NOT IN "
            "('SUCCESS','FAILURE','EXPIRED','ABORTED','CANCELLED','SUSPENDED')))) "
            "OR (j.kind='source-inspect' AND j.state IN ('queued','running')) "
            "OR (json_type(j.params,'$.input_intent') IS NOT NULL AND "
            "(j.state IN ('queued','running') OR "
            "json_extract(j.params,'$.input_intent.native_group.resolved')=0)) "
            "OR NOT j.params_valid OR j.id IN (SELECT value FROM json_each(?)) "
            "ORDER BY j.created DESC"
        )
        from . import restored_owners

        with store.connection() as snapshot:
            snapshot.execute("BEGIN")
            rows = [dict(row) for row in snapshot.execute(query, ("[]",)).fetchall()]
            extra_ids, activity_metadata = activity_listing(store, rows, connection=snapshot)
            if extra_ids:
                rows = [dict(row) for row in snapshot.execute(query, (json.dumps(extra_ids),))]
            exclusion = restored_owners.sql_exclusion(snapshot, "job", "j.id")
            historical_ids = {
                row["id"]
                for row in snapshot.execute(
                    "SELECT j.id FROM jobs j WHERE j.id IN (SELECT value FROM json_each(?)) "
                    + f"AND NOT ({exclusion})",
                    (json.dumps([row["id"] for row in rows]),),
                )
            }
            from .native_batch import status_fields

            preparations, native_status = {}, {}
            for row in rows:
                if row["id"] in historical_ids:
                    continue
                batch_id = row["ucloud_batch_id"]
                if batch_id and batch_id not in native_status:
                    captured = snapshot.execute(
                        "SELECT id,recording_id,params FROM jobs WHERE id=?", (row["id"],)
                    ).fetchone()
                    native_status[batch_id] = status_fields(
                        store,
                        {**dict(captured), "params": json.loads(captured["params"])},
                        connection=snapshot,
                    )
                if row["input_intent"] is not None and row["id"] not in historical_ids:
                    try:
                        preparations[row["id"]] = input_admission.projection(
                            store, row["id"], connection=snapshot
                        )
                    except (ValueError, KeyError):
                        preparations[row["id"]] = None
            inspection_ids, inspection_links = activity_inspection_links(
                snapshot, rows, preparations
            )
            missing_inspections = set(inspection_ids) - {row["id"] for row in rows}
            if missing_inspections:
                # Capture exact linked inspectors from the same snapshot, even if older than
                # the recent-history bound. No worker/control ownership is transferred.
                rows = [
                    dict(row)
                    for row in snapshot.execute(
                        query, (json.dumps(sorted(set(extra_ids) | missing_inspections)),)
                    )
                ]
            for identifier, link in inspection_links.items():
                activity_metadata.setdefault(identifier, {})["activity_inspection"] = link
        batches = {}
        for row in rows:
            if row["id"] in historical_ids:
                # Archival parameters and batch receipts need not satisfy today's live
                # protocols. Project metadata before any preparation/native decoder runs.
                safe = {
                    key: row[key]
                    for key in (
                        "id",
                        "recording_id",
                        "kind",
                        "state",
                        "progress",
                        "created",
                        "updated",
                        "recording_name",
                        "project_id",
                        "provider_name",
                        "model",
                        "ucloud_batch_id",
                        "runtime_batch_id",
                        "result_id",
                    )
                }
                for key in ("recording_name", "provider_name", "model"):
                    safe[key] = safe[key][:240] if isinstance(safe[key], str) else None
                for key in ("kind", "state", "created", "updated"):
                    safe[key] = str(safe[key])[:80]
                for key in ("ucloud_batch_id", "runtime_batch_id"):
                    value = safe[key]
                    if (
                        not isinstance(value, str)
                        or len(value) != 32
                        or any(char not in "0123456789abcdef" for char in value)
                    ):
                        safe[key] = None
                progress = safe["progress"]
                safe["progress"] = (
                    min(1, max(0, progress))
                    if isinstance(progress, (int, float)) and math.isfinite(progress)
                    else 0
                )
                safe.update(
                    restored_history=True,
                    archived_state=safe["state"],
                    state="historical",
                    message=(
                        "Restored history. Source allocations cannot be controlled "
                        "from this library."
                    ),
                    realign=False,
                    realign_ready=False,
                    source_changed=False,
                    segment_ids=None,
                    ucloud_cleanup_state="historical",
                    ucloud_submission_uncertain=False,
                    ucloud_job_id=None,
                    ucloud_server=None,
                )
                membership = store.all(
                    "SELECT batch_id FROM runtime_batch_members WHERE job_id=? LIMIT 1",
                    (row["id"],),
                )
                if membership:
                    safe["runtime_batch_id"] = membership[0]["batch_id"]
                batch_id = safe["ucloud_batch_id"]
                if isinstance(batch_id, str) and len(batch_id) == 32:
                    if batch_id not in batches:
                        metadata = store.all(
                            "SELECT CASE WHEN length(CAST(members AS BLOB))<=65536 "
                            "THEN CASE WHEN json_valid(members) THEN json_array_length(members) "
                            "ELSE NULL END ELSE NULL END AS count "
                            "FROM native_batch_runs WHERE id=? LIMIT 1",
                            (batch_id,),
                        )
                        known = [
                            {
                                "job_id": item["id"],
                                "recording_id": item["recording_id"],
                                "recording_name": str(item["recording_name"])[:240],
                                "state": "historical",
                                "progress": 0,
                                "message": "Restored history.",
                                "result_id": item.get("result_id"),
                            }
                            for item in islice(
                                (item for item in rows if item.get("ucloud_batch_id") == batch_id),
                                100,
                            )
                        ]
                        count = metadata[0]["count"] if metadata else None
                        if isinstance(count, int) and len(known) < count <= 100:
                            known.extend(
                                {
                                    "job_id": None,
                                    "recording_id": None,
                                    "recording_name": "Removed recording",
                                    "state": "historical",
                                    "progress": 0,
                                    "message": "Restored history.",
                                    "result_id": None,
                                }
                                for _ in range(count - len(known))
                            )
                        batches[batch_id] = {
                            "ucloud_batch_count": count if isinstance(count, int) else len(known),
                            "ucloud_batch_members": known,
                            "ucloud_batch_state": "historical",
                            "ucloud_batch_progress": 0,
                            "ucloud_batch_unresolved": False,
                            "ucloud_batch_message": safe["message"],
                        }
                    safe.update(batches[batch_id])
                row.clear()
                row.update(safe)
                continue
            row.update(activity_metadata.get(row["id"], {}))
            intent = row.pop("input_intent", None)
            try:
                if row["kind"] == "source-inspect":
                    cache = original_inspection.read(store, row["recording_id"])
                    if cache is None or cache["inspection_job_id"] != row["id"]:
                        raise ValueError("Original inspection evidence is invalid.")
                    upload_group = inspection_upload_group(store, row["recording_id"])
                    row["inspection_upload_batch"] = upload_group[1] if upload_group else None
                    row["input_preparation"] = {
                        "kind": "inspection",
                        "phase": cache["state"],
                        "epoch": cache["epoch"],
                        "generation": cache["generation"],
                        "source_state": cache["state"],
                        "can_cancel": cache["state"] in {"pending", "running"}
                        and not cache["cancel_requested"],
                        "can_retry": cache["state"] == "error",
                        "error_code": cache["error"]["code"] if cache["error"] else None,
                    }
                elif intent is not None:
                    row["input_preparation"] = preparations[row["id"]]
                    if row["input_preparation"] is None:
                        raise ValueError("Preparation evidence is invalid.")
            except (ValueError, KeyError):
                row["input_preparation"] = {
                    "kind": "inspection" if row["kind"] == "source-inspect" else "intent",
                    "phase": "error",
                    "can_cancel": False,
                    "can_retry": False,
                    "error_code": "invalid_evidence",
                }
                row["message"] = (
                    "Preparation evidence is invalid. Preserve the library "
                    "and start no replacement work."
                )
            row["segment_ids"] = json.loads(row["segment_ids"]) if row["segment_ids"] else None
            row["source_changed"] = bool(row["source_changed"])
            row["realign"], row["realign_ready"] = bool(row["realign"]), bool(row["realign_ready"])
            if row["ucloud_batch_id"]:
                row.update(native_status[row["ucloud_batch_id"]])
        # Owner obligations outlive the recent per-file query, including deleted member rows.
        from .native_batch import retained_activity

        retained = retained_activity(store)
        # A later, verified deletion release replaces this poll's older member view.
        # Only a current retained-owner receipt can supersede captured native rows.
        released = {card["remote_release"]["id"] for card in retained}
        return (
            [row for row in rows if row.get("ucloud_batch_id") not in released]
            + runtime_batches.activity(store)
            + runtime_preparation.activity(store)
            + retained
            + deletion_requests.file_activity()
            + export_manager.activity()
        )

    @app.post("/api/local-deletions/{identifier}/retry")
    def retry_local_deletion(identifier: Annotated[str, PathParameter(pattern=r"^[a-f0-9]{32}$")]):
        with deletion_requests._file_action():
            result = deletion_requests.retry_files(identifier)
            initialize_processing()
        return {"ok": result["state"] == "complete", "local_cleanup": result}

    @app.post("/api/input-intents/{identifier}/cancel")
    def cancel_input_intent(identifier: str, body: InputIntentAction):
        with queue.lock:
            try:
                result = input_admission.cancel(
                    store, identifier, epoch=body.epoch, phase=body.phase
                )
            except input_admission.StaleInput as error:
                raise HTTPException(409, str(error)) from None
        queue.notify()
        return result

    @app.post("/api/input-intents/{identifier}/retry")
    def retry_input_intent(identifier: str, body: InputIntentAction):
        with queue.lock:
            deletion_requests.invalidate_jobs([identifier])
            try:
                result = input_admission.retry(
                    store, identifier, epoch=body.epoch, phase=body.phase
                )
            except input_admission.StaleInput as error:
                raise HTTPException(409, str(error)) from None
        queue.notify()
        return result

    @app.post("/api/input-intents/{identifier}/prepare", status_code=202)
    def prepare_input_intent(identifier: str, body: InputPreparationConsent):
        with queue.lock:
            try:
                result = input_admission.confirm_preparation(
                    store,
                    identifier,
                    epoch=body.epoch,
                    phase=body.phase,
                    fingerprint=body.fingerprint,
                    confirmed=body.confirmed,
                )
            except input_admission.StaleInput as error:
                raise HTTPException(409, str(error)) from None
        queue.notify()
        return result

    def inspect_action(identifier, body, operation):
        with queue.lock:
            job = store.job(identifier)
            if job["kind"] != "source-inspect":
                raise HTTPException(409, "This job is not an original inspection.")
            try:
                result = operation(store, identifier, generation=body.generation, epoch=body.epoch)
            except original_inspection.StaleInspection as error:
                raise HTTPException(409, str(error)) from None
        queue.notify()
        return result

    @app.post("/api/input-inspections/{identifier}/cancel")
    def cancel_input_inspection(identifier: str, body: InputInspectionAction):
        return inspect_action(identifier, body, original_inspection.cancel)

    @app.post("/api/input-inspections/{identifier}/retry")
    def retry_input_inspection(identifier: str, body: InputInspectionAction):
        deletion_requests.invalidate_jobs([identifier])
        return inspect_action(identifier, body, original_inspection.retry)

    @app.post("/api/jobs/{identifier}/cancel")
    def cancel_job(identifier: str):
        from . import restored_owners

        restored_owners.require_job(store, identifier)
        candidate = store.job(identifier)
        if "input_intent" in candidate["params"]:
            raise HTTPException(409, "Input preparation needs its phase-bound cancellation action.")
        if candidate["kind"] == "source-inspect":
            raise HTTPException(409, "Source inspection uses its internal inspection worker.")
        batch = runtime_batches.job_batch(store, candidate)
        if batch:
            with queue.control_jobs([identifier]):
                result = runtime_batches.cancel(store, credentials, batch["id"])
            queue.notify()
            return result
        return queue.cancel(identifier) or {"ok": True}

    @app.get("/api/jobs/{identifier}/retained-ucloud-preview")
    def retained_ucloud_preview(identifier: str):
        from .enhancement_api import retained_preview

        return retained_preview(store, credentials, store.job(identifier))

    @app.post("/api/jobs/{identifier}/cleanup-ucloud")
    def cleanup_ucloud(identifier: str):
        from . import restored_owners

        restored_owners.require_job(store, identifier)
        from . import repair_cloud
        from .ucloud_native import cleanup_owned

        batch = runtime_batches.job_batch(store, store.job(identifier))
        if batch:
            return runtime_batches.retry_cleanup(store, credentials, batch["id"])
        job = store.job(identifier)
        if job["params"].get("repair_cloud", {}).get("protocol") == repair_cloud.PROTOCOL:
            if job["state"] in {"queued", "running"}:
                raise HTTPException(409, "Wait for the repair to finish before cleanup.")
            return {"cleanup_state": repair_cloud.retry_cleanup(store, credentials, job)}
        with queue.lock:
            job = store.job(identifier)
            if job["kind"] == "enhance" and "ucloud_api" in job["params"]:
                if job["state"] in {"queued", "running"}:
                    raise HTTPException(409, "Wait for the enhancement to finish before cleanup.")
                return {"cleanup_state": enhancement_api.retry_cleanup(store, credentials, job)}
            if job["kind"] != "transcribe" or job["state"] in {"queued", "running"}:
                raise HTTPException(409, "Wait for native transcription to finish before cleanup.")
            if job["params"].get("settings", {}).get("provider_type") != "ucloud":
                raise ValueError("Cleanup applies only to native Transcriber jobs.")
            if job["params"].get("native_batch_id"):
                from .native_batch import batch_for_job, cleanup_batch

                batch = batch_for_job(store, job)
                if batch["state"] in {"queued", "running"}:
                    raise HTTPException(409, "Wait for the UCloud batch to finish before cleanup.")
                return {"cleanup_state": cleanup_batch(store, credentials, batch["id"])}
        return {"cleanup_state": cleanup_owned(store, credentials, job)}

    @app.post("/api/jobs/{identifier}/reconcile")
    def reconcile_remote_job(identifier: str, body: ReconcileRemote):
        from . import restored_owners

        restored_owners.require_job(store, identifier)
        from . import repair_cloud

        job = store.job(identifier)
        if job["params"].get("repair_cloud", {}).get("protocol") == repair_cloud.PROTOCOL:
            return repair_cloud.reconcile(store, credentials, job, body.remote_id, body.not_created)
        batch = runtime_batches.job_batch(store, store.job(identifier))
        if batch:
            return reconcile_runtime(batch["id"], body)
        with queue.lock:
            remote_jobs.reconcile(
                store, credentials, store.job(identifier), body.remote_id, body.not_created
            )
        return {"ok": True}

    @app.post("/api/jobs/{identifier}/retry")
    def retry_job(identifier: str, body: Retry):
        from . import restored_owners

        restored_owners.require_job(store, identifier)
        with queue.control_jobs([identifier]):
            return retry_job_owned(identifier, body)

    def retry_job_owned(identifier: str, body: Retry):
        candidate = store.job(identifier)
        deletion_requests.invalidate_jobs([identifier])
        from . import repair_cloud

        if candidate["params"].get("repair_cloud", {}).get("protocol") == repair_cloud.PROTOCOL:
            result = repair_cloud.retry(store, credentials, candidate)
            queue.notify()
            return result
        if "input_intent" in candidate["params"]:
            raise HTTPException(409, "Input preparation needs its phase-bound retry action.")
        if candidate["kind"] == "source-inspect":
            raise HTTPException(409, "Source inspection uses its internal inspection worker.")
        batch = runtime_batches.job_batch(store, candidate)
        if batch:
            result = runtime_batches.retry(store, credentials, batch["id"])
            queue.notify()
            return result
        if candidate["kind"] == "enhance" and candidate["state"] in {
            "failed",
            "cancelled",
            "interrupted",
        }:
            options = EnhancementOptions.model_validate(candidate["params"]["enhancement"])
            folder = store.directory(candidate["recording_id"]) / "enhancements" / identifier
            if options.execution == "local" and pending_job_stages(
                folder, candidate["params"], options
            ):
                try:
                    require_local_runtime(options)
                except ValueError as exc:
                    raise ValueError(
                        str(exc) + " Retry keeps this job's original Python runtime; "
                        "start a new enhancement to use a different configured runtime."
                    ) from None
        if candidate["params"].get("native_batch_id"):
            from .native_batch import retry_batch

            return retry_batch(queue, candidate, body.replace)
        with queue.lock:
            job = store.job(identifier)
            current = store.recording(job["recording_id"])
            current = audio_inputs.effective_recording(job, current)
            original = input_admission.uses_original(job, current)
            if job["kind"] == "transcribe":
                busy = conflicting_job(
                    current["id"],
                    job["params"].get("provider_id"),
                    job["params"].get("settings"),
                    original=original,
                )
            elif original:
                busy = bool(
                    store.all(
                        "SELECT id FROM jobs WHERE recording_id=? "
                        "AND state IN ('queued','running') "
                        "AND kind IN ('transcribe','enhance')",
                        (current["id"],),
                    )
                )
            else:
                busy = queue.busy(current["id"])
            if job["state"] not in {"failed", "cancelled", "interrupted"} or busy:
                raise HTTPException(409, "This job cannot be retried while work is active.")
            if job["kind"] == "transcribe":
                require_approval(
                    store,
                    current["project_id"],
                    job["params"].get("provider_id"),
                    Settings.model_validate(job["params"]["settings"]),
                )
                if current["segments"] and not body.replace:
                    raise HTTPException(409, "Confirm adding this job's result on retry.")
                job["params"]["revision"] = current["revision"]
            elif job["kind"] == "enhance":
                options = EnhancementOptions.model_validate(job["params"]["enhancement"])
                if (
                    job["params"].get("segment_id") is not None
                    and job["params"].get("segment_ids") is not None
                ):
                    raise ValueError("Choose one passage or a list of passages, not both.")
                if job["params"].get("timing_units") is not None:
                    # Passage word timing (ADR 0058) may search between neighbours.
                    check_timing_units(
                        job["params"]["source_snapshot"],
                        job["params"]["timing_units"],
                        options,
                        current["duration"],
                    )
                elif job["params"].get("segment_id") is not None:
                    passage_input(
                        job["params"]["source_snapshot"],
                        job["params"]["segment_id"],
                        options,
                        current["duration"],
                    )
                elif job["params"].get("segment_ids") is not None:
                    passage_inputs(
                        job["params"]["source_snapshot"],
                        job["params"]["segment_ids"],
                        options,
                        current["duration"],
                    )
                else:
                    validate_input(job["params"]["source_snapshot"], options, current["duration"])
                if options.execution == "ucloud" and "ucloud_api" in job["params"]:
                    enhancement_api.require_current(store, credentials, job, current["project_id"])
                elif options.execution == "ucloud":
                    settings = enhancement_cloud.CloudEnhancementSettings.model_validate(
                        job["params"]["cloud_settings"]
                    )
                    enhancement_cloud.require_approval(store, current["project_id"], settings)
                    enhancement_cloud.require_setup(settings, credentials)
                if current["status"] != "ready" and not input_admission.uses_original(job, current):
                    raise HTTPException(
                        409, "Finish media preparation before retrying enhancement."
                    )
            else:
                store.execute(
                    "UPDATE recordings SET status='preparing' WHERE id=?", (current["id"],)
                )
            store.update_job(
                identifier,
                state="queued",
                message="Queued for retry",
                params=job["params"],
            )
        queue.notify()
        return {"ok": True}

    @app.get("/api/runtime-batches/settings")
    def runtime_batch_settings():
        return runtime_store.settings(store)

    @app.put("/api/runtime-batches/settings")
    def update_runtime_batch_settings(body: RuntimeBatchSettings):
        result = runtime_store.save_settings(store, body.max_allocations)
        queue.notify()
        return result

    @app.post("/api/runtime-batches/{identifier}/cancel")
    def cancel_runtime_batch(identifier: str):
        from . import restored_owners

        restored_owners.require_owner(store, "runtime_batch", identifier)
        with queue.control_owner("runtime_batch", identifier):
            result = runtime_batches.cancel(store, credentials, identifier)
        queue.notify()
        return result

    @app.post("/api/runtime-batches/{identifier}/retry")
    def retry_runtime_batch(identifier: str):
        from . import restored_owners

        restored_owners.require_owner(store, "runtime_batch", identifier)
        members = [member["job_id"] for member in runtime_store.get(store, identifier)["members"]]
        with queue.control_owner("runtime_batch", identifier):
            deletion_requests.invalidate_jobs(members)
            result = runtime_batches.retry(store, credentials, identifier)
        queue.notify()
        return result

    def reconcile_runtime(identifier, body):
        if body.remote_id is not None:
            raise ValueError(
                "CBS Runtime batches reconnect by their exact saved run name. Omit the remote ID."
            )
        result = runtime_batches.reconcile(
            store, credentials, identifier, confirm_not_created=body.not_created
        )
        queue.notify()
        return result

    @app.post("/api/runtime-batches/{identifier}/reconcile")
    def reconcile_runtime_batch(identifier: str, body: ReconcileRemote):
        return reconcile_runtime(identifier, body)

    @app.post("/api/runtime-batches/{identifier}/cleanup")
    def cleanup_runtime_batch(identifier: str):
        return runtime_batches.retry_cleanup(store, credentials, identifier)

    @app.post("/api/remote-releases/{kind}/{identifier}/retry")
    def retry_remote_release(kind: Literal["runtime_batch", "native_batch"], identifier: str):
        # Retries only the Trash step a confirmed Delete already authorized (ADR 0101).
        with queue.control_owner(kind, identifier):
            return remote_release.retry(store, credentials, kind, identifier)

    @app.post("/api/remote-releases/{kind}/{identifier}/reconcile")
    def reconcile_remote_release(
        kind: Literal["runtime_batch", "native_batch"], identifier: str, body: ReconcileRemote
    ):
        with queue.control_owner(kind, identifier):
            if kind == "runtime_batch":
                return reconcile_runtime(identifier, body)
            remote_jobs.reconcile_native_owner(
                store, credentials, identifier, body.remote_id, body.not_created
            )
        return {"ok": True}

    @app.post("/api/runtime-batches/{identifier}/discard-preview")
    def preview_runtime_discard(identifier: str):
        return runtime_discard.preview(store, credentials, identifier)

    @app.post("/api/runtime-batches/{identifier}/discard")
    def discard_runtime_batch(identifier: str, body: RuntimeDiscard):
        if body.confirmed is not True:
            raise HTTPException(422, "Confirm the displayed whole-batch discard first.")
        return runtime_discard.confirm(
            store,
            credentials,
            identifier,
            fingerprint=body.fingerprint,
            confirmed=body.confirmed,
        )

    app.mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static")

    @app.get("/")
    def index():
        return browser_page(app, "index.html")

    return app
