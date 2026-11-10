# Project default language and future recording overrides

Status: implementation plan, 3 November 2026. This document does not add a setting or
change any accepted transcription. The project working library remains local.

## Requested behavior

Offer an optional default language in **Create project** and make it editable later in
project settings. Existing projects and projects left unset retain their present provider
and processing behavior. The separate recording-level selector requested for the project
cards and transcript toolbar remains a later task; do not show a nonfunctional control.

Resolve choices only for a new request, using the recording override if implemented, then
the project default, then the existing model/processing behavior. Record the requested
choice, its scope and the effective supported value as configured, not detected, language.
Changes affect only later accepted requests. Jobs, retries, remote owners and existing
results retain their frozen choices and producer evidence. Do not mutate the saved provider
profile merely to use a project default.

The selected value is constrained by the requested provider/model and its actual execution
path. Native UCloud supports a broader profile language string; its blank profile value uses
the UCloud application's default and does not prove automatic detection. CBS Runtime currently
uses Danish, English or explicit experimental Automatic for supported models; Danish-only
models require Danish. Local alignment supports Danish/English. Unsupported combinations
must be shown before acceptance with an actionable message, not silently converted or
presented as detected language. Fixed-language models keep their required language and the
interface should explain a mismatch before a job is accepted.

## Implementation sequence

1. Specify a bounded canonical language representation and provider capability mapping;
   preserve existing profile values, unknown historical evidence and explicit Runtime Auto.
   Establish how project choices interact with explicit model choices, fixed-language models,
   enhancement follow-ups, passage re-transcription and word-timing jobs. An unset project
   must take the exact current path, including native blank/default semantics.
2. Add nullable project metadata with an atomic, synthetic-tested migration and a concurrency
   revision for edits. Include it in project JSON, state snapshots and metadata fences as
   appropriate. Advance the portable schema/semantic catalog and restore transforms without
   weakening older sealed-package validation; verify backup/restore for old and new libraries.
   Add an architecture decision before changing persistence or admission semantics.
3. Resolve and freeze one compatible choice for each recording/provider combination at the
   durable acceptance boundary. Bind actual submitted language to provider/Runtime evidence,
   and revalidate project metadata without changing queued or retry choices. For native batches,
   group only matching frozen model, effective language and allocation settings; never attach
   incompatible files to one owner. Preserve exact frozen-language output matching.
4. Add the accessible Create project control, project settings edit, clear inheritance/help,
   and request-level mismatch feedback. The project setting must not depend on a provider being
   connected when a project is created. Keep existing classification/storage and note drafts
   independent. Recording overrides are a separately implemented follow-up.
5. Test with synthetic projects and mocked providers: old-library migration; unset behavior;
   Danish/English and native custom values; Runtime Auto/fixed-model conflicts; single and
   multi-file/multi-provider submission; changed project settings during acceptance; queued
   retries and restart; consent and policy checks; exact result/provenance; portable capture,
   isolated restore, stale replies and keyboard/narrow-layout browser behavior.

Live UCloud execution, inference quality and automatic-language accuracy are separate
acceptance tasks. No additional project setting authorizes a paid job, download or private
upload.
