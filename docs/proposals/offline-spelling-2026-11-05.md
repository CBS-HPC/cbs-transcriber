# Offline spelling assistance for passage edits — design proposal, 5 November 2026

Status: original wider design. A local DA/en-GB preview is implemented and independently tested
on codex/offline-spelling-rc4, commit 3870ac2 (9 November). It is unmerged/not pushed and is not
in main or the loaded MVP. Integration requires later user approval. The preview has Danish
checking, British English suggestions, private vocabulary and bounded local execution. Checking
starts off; automatic public dictionary retrieval when needed remains on, preserving opt-out.
Other languages, fast Danish suggestions and real-user/platform/licence acceptance remain open.
The original wider proposal below is not a completion claim.

## Goals and constraints

- Help researchers correct transcription errors while editing a passage, in the language each
  recording is set to (Danish and English first; any language with a pinned Hunspell dictionary).
- **Local only:** transcript text, flagged words, suggestions and custom vocabulary never leave
  this computer. No cloud or browser-vendor spellcheck; no telemetry.
- **Visible, controlled downloads:** the user chose automatic download when needed as the
  default for new installs. Show a notice, preserve an explicit opt-out, and ask before
  downloading when automatic retrieval is off. Use pinned sources and SHA-256 verification.
- **Never silently change text, timing or drafts:** suggestions only edit the open draft when
  the user picks one. Saving follows the current conservative Text-edit rules: unchanged words
  retain exact timing, unique replacements retain their interval and inserts stay untimed;
  ambiguous edits refuse with the draft retained (ADR 0156).
- Keep the browser's built-in spellcheck disabled in transcript fields (`spellcheck="false"`
  is already set): browser "enhanced" spellcheck can send text to a cloud service.

## Engine and dictionaries

- **Engine:** [`spylls`](https://github.com/zverok/spylls) (pure-Python Hunspell port, MPL-2.0)
  runs on Windows/macOS/Linux without native libraries and reads standard Hunspell
  `.aff/.dic` files, including Danish compounding and affix rules. Check speed is adequate for
  passages; suggestions are slower and are computed on demand per word. Measure before
  committing; fall back to `pyenchant` only if it proves necessary (native library, more
  packaging risk).
- **Any language with a Hunspell dictionary (user request, 5 November).** The app retrieves the
  dictionary for the recording's effective language when it is first needed. A packaged
  catalog (`catalogs/spelling_dictionaries.json`) maps language tags (the same IANA tags used for
  recording/project language) to one pinned source per language: exact URL of the `.aff` and
  `.dic` files at a fixed commit/release, byte size, SHA-256, licence id and licence/attribution
  URL. Adding a language is a catalog entry plus a small synthetic check, not new code.
  Candidate sources: [wooorm/dictionaries](https://github.com/wooorm/dictionaries) (≈90
  languages, normalised UTF-8 Hunspell files with a per-language licence file) or the
  [LibreOffice dictionaries](https://git.libreoffice.org/dictionaries) repository; pin by
  commit. Danish: `da` (Stavekontrolden; GPL/LGPL/MPL tri-licence). English: `en-GB`/`en-US`
  (SCOWL licence). Regional variants follow the tag (`de-DE`, `de-CH`, `pt-BR` …); a bare
  language uses the catalog's documented default variant.
- **Retrieval follows the saved preference.** With automatic retrieval enabled (the user's
  choice for fresh installs), first use shows a notice as the dictionary is downloaded. With it
  disabled, first use asks before downloading and an explicit denial is preserved. Settings →
  Local Processing offers "Download spelling dictionaries when needed" and lists installed
  dictionaries with remove buttons.
  The download fetches only the pinned files, verifies size and SHA-256, stores them under the
  app data folder (`<home>/CBS Transcriber/Dictionaries/<lang>/<sha>/`), and never sends
  transcript text. After that, checking is fully offline. A failed or mismatched download keeps
  checking off for that language with a clear message.
- **Licences.** Dictionary bodies are retrieved from pinned publisher files rather than added
  to this repository. Preserve complete licence notices and attribution in the cache and
  Settings links; this does not establish institutional or distribution licence clearance.
- **Quality and speed vary by language.** Hunspell quality differs per dictionary, and highly
  inflected languages (e.g. Finnish, Hungarian) can be slow in a pure-Python engine; each
  catalog entry carries a measured check/suggest timing, and slow languages can be marked
  "check only" (no suggestions) rather than excluded.

## Behavior

- **Language:** each passage uses the recording's effective language under the current
  project/recording policy (ADR 0127); do not restore the removed Processing/provider language
  overrides. Unsupported or mixed language:
  checking is off for that passage with a short note; no guessing.
- **Tokenisation:** Unicode-aware words including æ ø å and diacritics, apostrophes and
  hyphenated compounds; ignore numbers, URLs, emails, speaker labels and bracketed annotations
  (e.g. `[inaudible]`). Each flag carries exact character offsets in the draft.
- **Custom vocabulary (layered):**
  - *Project dictionary* (names, organisations, technical terms) stored in the library with the
    project, included in backups/portable capture, excluded from exports and research reports
    (it may contain participant names). Editable in Project Settings.
  - *Personal dictionary* in the library's settings for words used across projects.
  - "Ignore once" (draft-only, not stored) and "Add to project dictionary".
- **UI in the passage editor:** a quiet "N possible spelling issues" line under the textarea;
  expanding it lists each flagged word with up to five suggestions as buttons, plus Ignore and
  Add to dictionary. Choosing a suggestion replaces exactly that word range in the draft and
  keeps the caret/selection sensible. Optional underline overlay (mirrored text layer behind
  the textarea) if it can be made accurate across fonts and scrolling; the list is the
  accessible primary interface (screen-reader announcements, keyboard operable).
- **When it runs:** debounced (≈400 ms) after typing pauses, only for the open passage; never
  for the whole transcript automatically. A Settings → Editing toggle turns it off.

## API sketch

- `POST /api/spelling/check` `{language, text}` → `{supported, flags:[{start,end,word}]}`
  (same-origin, size-bounded, no logging of text).
- `POST /api/spelling/suggest` `{language, word}` → `{suggestions:[…]}` (cached per word).
- Project/personal vocabulary endpoints with revision fences like other project settings.
- Dictionaries loaded lazily per language in the server process and kept in memory.

## Evaluation before enabling by default

- Synthetic Danish/English passages with known errors: precision/recall of flags, top-5
  suggestion hit rate; false-flag rate on correct Danish inflections, compounds, names and
  English loanwords common in Danish interviews.
- Responsiveness: check ≤ 50 ms for a typical passage, suggestions ≤ 300 ms per word on a
  typical CBS laptop; memory per loaded dictionary.
- Privacy test: no outbound network during checking (socket-level test), no text in logs.
- Browser/platform checks (Windows Edge/Chrome, macOS Safari/Chrome) remain separate
  acceptance; synthetic tests are not user acceptance.

## Decisions needed

1. Dictionary source to pin (wooorm/dictionaries recommended for its normalised files and
   per-language licences) and which languages to list first (proposed: da, en-GB, en-US, de,
   sv, nb, fr, es).
2. **Decided 5 November:** "Download spelling dictionaries when needed" defaults **on** for
   new installs (as for public local models, ADR 0103): the pinned dictionary for a recording's
   language is downloaded automatically on first use, verified and stored locally, with a short
   notice; the user can turn it off in Settings, after which first use asks instead. A saved
   "off" is never changed silently.
3. Spelling checks default on or off for new installs?
4. Should project dictionaries also seed the anonymisation "known names" list (proposal
   2026-10-30) — one shared list of project names — or stay separate?
