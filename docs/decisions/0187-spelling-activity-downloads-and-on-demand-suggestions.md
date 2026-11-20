# 0187 — Spelling: Activity dictionary downloads and on-demand suggestions

Accepted on 20 November 2026 for the hidden v2 spelling feature (ADR 0185), implemented on
`claude/spelling-redesign`. It replaces ADR 0185's fixed issue panel below the Text editor and
its in-editor download notice. The user asked that dictionaries "automatically download ... for
the languages that are selected. This should likely be an Activity", and: "I only want the word
suggestions to appear by double clicking on a word (or something). I do not want these fixed
word suggestion boxes."

## User choices (20 November)

- Trigger: the user proposed **Ctrl+click** instead of double-click, so double-click keeps
  plain word selection. macOS uses ⌘+click (Ctrl+click there is the context menu).
- Nothing under the text box: possible issues are **coloured** in the text, and the editor has
  a **Spelling** checkbox that is remembered for every passage.
- The popover offers the suggestion, **Add to recording vocabulary**, **Add to project
  vocabulary** and **Ignore once**.
- Settings gets its own **Spelling** tab; **Local Processing** keeps the automatic local
  processing settings only.

## Decision

### Automatic dictionary downloads in Activity

- `spelling_tasks.DictionaryTasks` is a server-lifetime owner like ADR 0158/0173 tasks: one
  download at a time, at most 16 retained cards, no persistence. Each card is
  `kind: spelling_dictionary`, id `spelling-dictionary:<hex>`, with states queued, running,
  completed (shown as *ready*), failed, cancelled and interrupted, a progress fraction per
  verified file, **Retry download** for failed/stopped cards and **Dismiss** for finished ones.
- It reuses `SpellingService`'s install path: the same pinned URLs, byte/hash checks, owned
  cache folder, staging and marker-last publication. It waits for the shared cache lock instead
  of returning *busy*, and shutdown stops it and removes partial files. No transcript text,
  vocabulary or credential is sent.
- **Automatic** downloads need checking and automatic downloads both on. They cover every
  catalog language chosen as a project default or recording override that is not installed.
  Triggers: a spelling settings save that leaves both on, a project language save, a single
  or bulk recording language save, a project created with a language, and the editor's first
  use (`POST /api/spelling/dictionary-tasks` with the open recording). Never at startup.
  Turning checking or automatic downloads off stops automatic downloads that have not
  published yet.
- **Explicit consent** remains with automatic downloads off: the editor's **Download
  dictionary** button, Settings → Spelling's per-dictionary Download and Activity's Retry each
  request one language with consent. Consent may download while checking is off, as the
  earlier explicit route did. A language that failed during this server's lifetime is not
  restarted by automatic triggers, only by Retry or explicit consent, so an offline computer
  does not loop.
- Activity's fixed message is shown on failure; exception text (which can hold paths) is not.
  The earlier synchronous `POST /api/recordings/{id}/spelling/dictionary` stays for
  compatibility and is no longer used by the page.

### Coloured words and the suggestion popover

- The editor's spelling layer is a second overlay inside `.passage-text-surface`, behind the
  transparent textarea and after the playback-highlighting layer. It mirrors the draft with
  transparent text and colours only flagged words (tinted background and a wavy underline),
  synchronised to the textarea's width and scroll. It never changes the textarea's value,
  selection, focus or scroll.
- **Ctrl+click** (⌘+click) on a coloured word, or **Alt+Enter** with the caret or selection
  inside one, opens a small `role="dialog"` popover anchored below the word. The keyboard route
  moves focus into the popover (onto the suggestion once it arrives); Escape closes it and
  returns to the textarea with the selection unchanged. Pointer or focus elsewhere, typing, a
  new check, navigation or opt-out closes it.
- Choosing a suggestion selects exactly the flagged range and inserts the word with the
  browser's `insertText` editing command, so it is ordinary input and the browser's own Undo
  restores the word. Where that command is unavailable, it falls back to the earlier
  `setRangeText` plus input event. All existing guards still apply: recording, result,
  revision, view epoch, passage, language key, exact draft and range, the saving/structure
  locks, request serials, the replaced busy set and late-response checks.
- One preferred suggestion is requested only when the popover opens (unchanged 500 ms limit).
  Danish stays check-only and says so in the popover.
- **Coexisting with uncertainty marks.** The user wants the dotted underline of uncertain
  words (review hints, `.uncertain-word`: amber, dotted, 3 px below the text) inside the open
  Text editor too. Spelling keeps a distinct style (red wavy line 6 px below the text plus a
  light tint) in its own layer, so a later uncertainty layer can sit beside it in the same
  `.passage-text-surface`, and one word can carry both lines without either hiding the other.
  A synthetic check with both layers on one word showed the dotted line above the wavy one.
  Showing uncertainty in the editor is not part of this decision.
- **The Spelling checkbox** sits in the editor toolbar and saves the library preference, so
  it applies to every passage and survives reloads. There is no visible status line: a
  screen-reader live region announces finished checks and states, and the checkbox's tooltip
  repeats them. A **Download dictionary** button appears in the toolbar only when automatic
  downloads are off or a download did not finish.

### Settings → Spelling

- The spelling settings moved from Local Processing to their own tab (`data-feature`
  marked): checking on/off, automatic downloads, Personal vocabulary, and every catalog
  dictionary with its status, size, licence link and Download or Remove. A finished download
  refreshes only the dictionary list, never unsaved checkboxes.

## Hidden in the MVP profile

`POST /api/spelling/dictionary-tasks` and its Retry join `GUARDED_ROUTES`; the task owner is
constructed disabled, so language and settings saves start nothing. Listing and Dismiss stay
open. The Spelling tab, panel, File Settings buttons and every dynamic entry point check the
feature; MVP pages make no spelling requests.

## Consequences

Synthetic unit tests cover triggers, consent, revision fences, failure/Retry/Dismiss, one
download at a time, opt-out and shutdown; browser tests cover the Activity card, coloured
words, Ctrl+click and Alt+Enter, Undo, Ignore, both vocabulary scopes, the remembered toggle,
the Settings tab and MVP hiding. Not verified: macOS ⌘+click and Option+Enter, Firefox/Safari
`insertText` Undo, screen-reader announcements, real dictionaries and real users. The
coloured layer assumes the textarea's font metrics match the playback layer, as that layer does.
