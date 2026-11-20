# 0171 — First-use UCloud and optional Hugging Face setup

11 November 2026. User-authorized front-page addition; other frontend redesign stays held.
Post-MVP branch only, with the installed app and its jobs preserved.

The user subsequently chose automatic HF access checking and configured local/UCloud
preparation in Activity (ADR 0173), superseding the earlier Save-only proposal.
UCloud-only setup still does not implicitly start preparation.

## 12 November presentation amendment

The user requests an independent setup dialog matching Settings/Export, with no inline
connection card in the empty front page or above project content. Retain the existing
eligibility, completion marker, optional HF field, Skip, Settings handoff and accepted-save
fences. Close/Escape act as Skip only when no credential Save is pending; they cannot undo
an accepted save. The change grants no new credential, model or job authority.

Use the same pale-yellow missing-credential decoration for UCloud and HF: no saved
credential and no draft. A blank input retaining a stored key stays normal; typing removes
the missing decoration without implying validity or a successful connection check.

## 19 November amendment: no saved UCloud key

The user asked that a reload show the key panel whenever no UCloud API key is saved. The
completion marker now applies only while a visible UCloud profile has a saved key. Without
one, setup returns on every page load, also for a browser that once chose Skip, and
Skip/Close/Escape last for that page. An existing profile without a key keeps the exact
Settings handoff. Saving a key ends the reminder; the marker is still never credential or
admission authority. The empty home page also gets a centred Create a project button, the
same action as the sidebar's.

## Decision

Offer a compact first-use form on the home page: password fields for the UCloud API key and
optional Hugging Face token, the existing OS-store remember choice, Connect and save, and
Skip for now. Keep project creation usable. Preserve the delivered blue-on-light CBS logo
and returning-user empty page; having zero projects is not the first-use test.

Reuse native UCloud discovery and provider Save with the exact resolved setup, opaque
connection_check proof and fresh MIG allocation policy. Do not implement another credential
or connection trust mechanism. If discovery needs account/storage/application choices, hand
the current draft to the existing UCloud Settings controls instead of adding a new selector.
If a visible native or CBS Runtime-only UCloud profile already exists, hand the draft to
those controls with its exact ID/version/setup instead of creating a duplicate that could bypass disabled models or
Protected denials. Multiple existing profiles use the existing explicit Settings selection.
Only libraries without an existing visible UCloud connection use the fresh discovery/MIG
create path. Preserve all saved flags and approvals.

Save an optional HF token through the primary credential PUT and report the returned queued
Activity task. Access checks and configured preparation belong to that background owner,
never inline browser/Save work. The existing HF Check route now asynchronously rechecks a
saved token; onboarding does not need to call it after Save. No recording is uploaded and
UCloud-only onboarding does not authorize Runtime preparation.

## First-use state and races

Use a credential-free browser localStorage completion/dismissal marker, written only after
explicit successful Save or Skip. A complete existing UCloud setup suppresses the offer too.
The marker is UX state, never credential/admission authority, and lives outside library data;
empty/new libraries or same-origin restarts do not reset it. Cleared storage or a different
browser can offer setup again. If storage is unavailable, use a graceful session-only state.
There is no global app-file write, schema migration, startup claim or new claim API.

Keep credentials only in current password drafts and the existing credential mechanism;
never localStorage, logs, URLs or public responses. Clear the drafts on success, Skip and
Settings handoff. Client generation/view fences prevent late discovery responses dispatching
new writes after Skip/navigation. Skip can cancel a read-only discovery flow; disable it and
handoff briefly during an actual credential Save already dispatched to the server. Accepted
saves may finish and cannot be rolled back by a client fence. Partial or uncertain saves
must be reported honestly; UCloud and HF persistence are not one atomic transaction.

## Gate

Use synthetic browser/backend fixtures to verify first use, optional/malformed HF tokens,
Skip, returning users, new/empty libraries, unavailable localStorage, ambiguous native setup,
exact connection proof/MIG choice, late discovery, accepted-save controls and partial failures.
Assert no inline HF checks/downloads, duplicate setup owner, credential echo or browser secret
persistence; token Save may queue the explicitly authorized Activity task. Verify existing
native-profile policy is retained and cannot be widened by creating an automatic duplicate.
UCloud/HF remain editable through Settings. All checks use mocks and isolated browser ports;
the real app/user data and environment are untouched.
