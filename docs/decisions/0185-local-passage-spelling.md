# 0185 — Local spelling assistance for an open passage

> Update 20 November 2026: the issue panel, in-editor download notice and Local Processing
> section are replaced by [ADR 0187](0187-spelling-activity-downloads-and-on-demand-suggestions.md)
> (coloured words, double-click/Alt+Enter popover, Activity downloads, Settings → Spelling), and
> [ADR 0186](0186-recording-spelling-vocabulary.md) adds recording vocabulary. The engine,
> pins, worker bounds, privacy, retention and MVP refusals below are unchanged.

Accepted for v2 on 19 November 2026: the user chose **"Hidden in v2"**. The previously tested
preview ships in the code behind the `spelling` release-profile feature (ADR 0145): on in
`full`, hidden and refused in `mvp`. The user's later underline-in-editor design is not part of
this decision. This record renumbers and replaces the preview's private-branch ADR 0160/0161
(codex/offline-spelling-rc4, feature commit 3870ac2 and preferred-suggestion follow-up
2c2d61b, 9–10 November), whose numbers now belong to other decisions. The code was re-applied
to v2 as new commits; no private-history commit was merged or cherry-picked.

## Decision

Use pinned Spylls 0.1.7 with separately retrieved, pinned Danish and British English Hunspell
dictionaries. Preserve exact source/byte/hash/licence attribution, including full publisher
licence notices ([pins and feasibility](../research/offline-spelling-dictionaries-2026-11-09.md)).
These observations are not institutional licence clearance. The catalog declares English's
regional default and exact supported aliases; never guess another language. Danish is
check-only: its suggestions did not finish within the bounded feasibility timeout.

Checking is initially off until the user enables it. Automatic public dictionary retrieval
defaults on when needed, as previously requested; preserve explicit false and require explicit
consent when it is off. Status, checking and suggestions never download. Retrieval is a
separate first-use action with a visible notice and no transcript text, credentials or redirects.
Verified public dictionary bodies live in an owned app-cache folder outside every library;
they are disposable, excluded from project exports and library capture.

Run only for the open Text editor after a typing pause, with bounded text/word work and explicit
unsupported/over-limit states. Derive language from the recording's current effective preference
on the server. Return exact UTF-16 offsets and request/language identity; the browser also
checks recording, result, view, textarea and exact draft before applying a suggestion. Replacing
a word changes only the draft through normal input handling. Save, timing, Working, history
and structural undo continue through their existing authorities. Keep vendor spellcheck off.
In v2 the issue panel sits below the Text editor's highlighting surface, never inside it.

Dictionary inference runs in a bounded, credential-free child with Python socket operations
blocked; this is not an operating-system security sandbox. Bound cold loading separately from
warm checking/suggestions; a deadline kills and reaps the worker. A suggestion requests one
preferred English candidate within the unchanged 500 ms limit: genuine dictionary exhaustion
could exceed that limit after producing a useful first candidate in 1–2 ms, so the worker
never pulls a second candidate. The accessible issue/suggestion list is sufficient; no textarea
overlay or whole-transcript checking is introduced.

A busy Check retries only its exact current draft, with capped backoff and at most nine retries
within twelve seconds. Input, navigation and opt-out cancel it, including a pending opt-out
write. Suggestions and dictionary installation never retry automatically. Semantic install
busy is not ready; retain an explicit usable current retry control. Late content, status and
cleanup must respect current intent as well as view identity.

Store operational preferences in settings row 12 and private vocabulary in versioned settings
row 13. (The preview used rows 10/11; v2 already uses them for checked-provider receipts and
runtime generations, so the port moved them. Rows 10/11 are never read as spelling data, and
restore never retains them as vocabulary.)
Personal/project scopes have independent CAS revisions; project writes verify live ownership in
the same transaction. Project deletion includes only that project's vocabulary revision in its
metadata fence and removes its entry in the same Delete transaction. Preserve other
project/personal words and content revisions. Vocabulary is not transcript text, a topic
codebook or an anonymisation names list, and stays out of transcript exports/research reports.

Database backups/captures retain vocabulary. Existing isolated restore may retain only the
strict bounded data-only row 13 with live project owners, exact retained-row digest and legacy
absence behavior. Operational row 12 stays stripped: restore must not enable checking or
downloads. This is compatibility for the new private metadata, not new restore authority.
Malformed vocabulary refuses before target promotion. Changing the portable semantics
worker's vocabulary validator requires repinning it (`scripts/repin_portable_semantics.py`).

## Hidden in the MVP profile

- The server refuses new work with 403 `feature_disabled` before any handler: saving spelling
  preferences, Check, Suggest and public dictionary retrieval (`GUARDED_ROUTES`). No worker
  starts and nothing is downloaded or created in the cache.
- Existing private data stays available: settings, dictionary list and personal/project
  vocabulary reads; removing an installed public dictionary; and vocabulary saves that only
  remove words. A vocabulary save that would add a word is refused as `feature_disabled`
  inside the same writer transaction that checks the revision. Project Delete still removes
  that project's vocabulary, and backups/restore keep the validated words as above.
- The browser hides the static Settings section, Project vocabulary button and vocabulary
  dialog (`data-feature="spelling"` plus the CSS rule), and `spelling.js` checks
  `featureOn("spelling")` before creating the editor panel, loading settings or opening the
  dialog, so MVP pages make no spelling requests.

## Consequences

The new runtime dependency `spylls==0.1.7` is locked by uv. Its wheel metadata classifier says
MIT while its shipped LICENSE file is the Mozilla Public License 2.0; treat it as MPL-2.0 until
a licence review decides otherwise. The app uses it unmodified as a separate library, and
dictionary bodies are never redistributed. Institutional review of the engine and both
dictionaries remains open, as do broader languages, fast Danish suggestions, dictionary updates,
researcher-quality and clean-device/Mac acceptance. The underline-in-editor design (TODO,
12 November) would replace the panel only after its own decision.

The preview's synthetic backend/browser/privacy/retention gates passed on 9–10 November in the
private history; the ported v2 tests re-run them with the hidden switch. No dictionary bodies,
transcript text or user vocabulary enter Git.
