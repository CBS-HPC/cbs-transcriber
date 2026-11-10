# 0130 — CBS Runtime models unchecked in new setups

## Decision

Supersede ADR 0126's default selection of Edda large: all bundled CBS Runtime
models are unchecked in new setups. The OpenAI and native UCloud Transcriber
defaults remain unchanged. Users may explicitly enable Runtime models later.
For a brand-new UCloud connection created through the API without a model list,
newly appended bundled Runtime profiles also start disabled; an explicitly
selected source profile retains its supplied enabled flag. Existing connections
continue to use the prior enabled-by-default expansion and backfill behavior.
New incomplete connections retain a private pending marker and explicit model
choices across saves and startup until the first eligible expansion. Joining an
existing connection group drops the pending marker without changing that group.

## Preservation

Existing profiles, model visibility, approvals and already frozen work retain
their existing values. This decision neither removes Runtime functionality nor
cancels work or changes an existing allocation. Existing partial connections
and custom catalog defaults retain their documented compatibility behavior.

## Rationale

CBS Runtime work is deferred at the user's request. Hiding its models from new
transcription choices by default reduces confusion while keeping the user's
existing work and their ability to opt in intact.
