# Synthetic NVivo / REFI-QDA validation pack

This is a preparation pack, **not desktop acceptance evidence**. Every manual result starts
`NOT_RUN`. No NVivo installation, import, export or Windows/Mac interoperability has been
tested by the builder. Record the actual installed product/version and observations below.
The source baseline is `99cb57f0635658c9799f1b4ceb7ae0566f26987f`; the pack manifest also
hashes the exact builder, fixture and exporter source used. It is not a signed provenance
attestation. All people, words, codes and media in this pack are synthetic.

## Build and preserve the pack

Use an already installed development environment. From the matching checkout, choose a new
output path. The builder refuses an existing path, never registers a library, starts no server
or worker, and makes no network/model calls. A disposable temporary Store supplies genuine
current application exports; its database is not distributed or offered for restore. Remove
any `CBS_TRANSCRIBER_MODELS` override before running; it is deliberately refused.

Windows PowerShell:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
.venv/Scripts/python.exe scripts/build_nvivo_validation_pack.py test-results/nvivo-validation-pack
Compress-Archive -LiteralPath test-results/nvivo-validation-pack -DestinationPath test-results/nvivo-validation-pack.zip
```

macOS shell, using an existing environment:

```sh
PYTHONPATH=src .venv/bin/python scripts/build_nvivo_validation_pack.py test-results/nvivo-validation-pack
ditto -c -k --keepParent test-results/nvivo-validation-pack test-results/nvivo-validation-pack.zip
```

No dependency installation, app update or library activation is needed for this experiment.
Keep the original generated pack unchanged. Extract a working copy into a new unsynced local
test folder on each machine. Store NVivo projects, observations and screenshots beside that
copy, outside the preserved pack. Save a copy of `manual-results.csv` for each platform.
`pack-manifest.json` is written last: its absence means the build is incomplete. Rebuilding
creates fresh Store IDs/timestamps; compare text/semantics and recorded hashes, not ZIP byte
identity. The manifest inventories all preceding files, excluding itself. Hashes detect a
changed copy; they grant no upload, restore, disposal or authenticity authority.

## Contents and exact expectations

| Artifact | Meaning / acceptance oracle |
| --- | --- |
| `fixture.json` | Hand-authored exact Danish/English text, speakers, times, expected Windows rows, coding quotes and proposed REFI probes. JSON escapes expose tab/CRLF; `café` and `café` intentionally differ. |
| `danish/`, `english/` | Each has four passages with speaker sequence A, B, B, A (three consecutive-speaker blocks), and seven current export formats. No export invents word timing. |
| `source-version.json`, `identities.json` | Exact synthetic immutable payload bytes plus version/result IDs and SHA-256. These IDs are local fixture identities, not external NVivo identities. |
| `windows.txt` | UTF-8 BOM, tab-separated Timespan/Speaker/Content, one passage per row, tenth-second rounding. Saved text stays literal; whitespace is flattened in this exported copy. |
| `mac.txt` | UTF-8 BOM, speaker-colon text and F5-style `#hh:mm:ss-t#` markers, one passage per paragraph. Content colon-space is an explicit speaker-recognition risk to inspect. |
| `transcript.json` | Current exact text, speaker map and actual nullable timing. No source settings, originals or credentials. CRLF/tab and Unicode code points survive in decoded strings. |
| `transcript.csv` | BOM and quoted literal fields; formula-like text begins with an extra apostrophe by design. This is a spreadsheet safeguard, not part of the source wording. |
| `readable.txt` | Readable `INTERVIEW`/speaker grouping; consecutive B passages group together. It is not the platform-native timed transcript contract. |
| `.srt`, `.vtt` | Actual bounded intervals, millisecond rounding and escaped subtitle markup. Optional comparison only; their desktop import behavior is unverified. |
| `clock-no-speech.wav`, `clock-cues.json` | 12 s mono 16-bit 16 kHz PCM; 20 ms tone cues at exact passage boundaries. Both language files are clock fixtures with no speech. Scripted words do not describe their audio. |
| `reviewed-coding.json` | Current `cbs-topic-review-export-v1` prototype JSON, not an NVivo coding import. Five items: accepted imported, changed+accepted imported, rejected imported, unreviewed metadata only, accepted manual combining-accent evidence. |
| `working-project-*.zip` | Existing project exporter with exact pinned Working payloads despite a newer active alternative. Native archives export three items and report two skipped; JSON exports four and reports one skipped. Original audio is not inside these project ZIPs. |
| `missing-start/`, `start-only/`, `refusals.json` | Controlled timing gaps. Missing-start refuses both native formats/SRT/VTT before writing those files. Start-only native rounds 3599.99 s to 01:00:00.0 and invents no end; SRT/VTT refuse. Readable/CSV/JSON retain every passage. |
| `automated-checks.json` | Builder assertions about CBS output structure only. PASS here never means NVivo acceptance. |
| `manual-results.csv` | Empty observation/evidence fields and `NOT_RUN` status for every desktop case. |

Main-case passage intervals are 0–2.04, 2.04–5.97, 6.10–9.99 and 10.10–11.90 seconds.
Expected native intervals are 0.0–2.0, 2.0–6.0, 6.1–10.0 and 10.1–11.9. Maximum rounding
error is 0.05 s; record any extra importer rounding separately. Silence gaps are deliberate,
not missing transcript text. Media playback probes test clock/seek behavior, not ASR or
speaker-recognition accuracy. There are two unique named speakers and four passage rows;
consecutive same-speaker passages must remain distinguishable in native import.

The coding oracle uses half-open **Unicode code-point** offsets into the frozen source
passage, not UTF-16 units or offsets into flattened native text. Accepted flexibility and
corrected negative speed evidence are in passage 1, after an emoji; manual literal evidence
selects decomposed `café` in passage 2, not precomposed `café`. Three items are accepted, one
rejected and one unreviewed. Rejected items retain their last reviewed evidence with an
explicit rejection state; unreviewed items contain no proposal body. Never turn either into
approved NVivo coding. Private review reasons and producer declarations are excluded. The
full local review history is retained only inside the disposable Store during generation;
this public reviewed export deliberately does not contain earlier review payloads.

## Record before testing

For each machine record operator/date, NVivo product name + complete version/build, licence
edition if it changes capabilities, OS version/architecture, language/locale and import
encoding/mapping choices. Use the installed help to identify the appropriate local import or
export action; menu names/support vary and are not asserted by this protocol. Record that
exact action. Use a new empty disposable project for each format; do not import both native
formats into one project and confuse duplicate sources with exporter behavior.

Check SHA-256 for the copied input files against `pack-manifest.json` before import and again
after the experiment. Windows: `Get-FileHash -Algorithm SHA256 <file>`; Mac:
`shasum -a 256 <file>`. Record observed transcript text/speaker/timing independently, alongside
screenshots or exported text where available. Do not silently repair a failed import to PASS.
An explicit second trial with a documented workaround is a separate result. Use `PASS`,
`FAIL`, `BLOCKED` or `NOT_SUPPORTED` only after observing the installed application; otherwise
leave `NOT_RUN`. Unsupported mappings remain gaps rather than passing by omission.

## Windows native transcript protocol

1. **WIN-01:** In a new test project import `danish/clock-no-speech.wav`, then attach
   `danish/windows.txt` as its transcript using the installed timed-transcript importer.
   Record UTF-8/BOM detection, delimiter/header and any manual column mapping. Expect four
   rows, Timespan/Speaker/Content interpreted correctly, and exactly two named speakers.
2. **WIN-02:** Compare all four content cells with `fixture.json` → `expected_windows`.
   Check æøå/ø, emoji, combining accents, quotes, literal `<kode>` and `&`. Tab and CRLF become
   spaces only in this native copy. Check that colon-space inside passage 2 stays content and
   the leading `=1+1` remains text. No `ACTIVE DECOY` wording may appear.
3. **WIN-03:** Inspect each imported interval and seek at every cue in `clock-cues.json`.
   Compare native rounding with the table above; confirm last end and both silence gaps.
   Record the application's actual precision/seek behavior, including any extra rounding.
4. **WIN-04:** Repeat WIN-01–03 in a fresh project for English. Separately inspect
   `start-only/windows.txt`: start must be 01:00:00.0 with no claimed end; whether that installed
   NVivo importer accepts start-only rows is an observed compatibility question. There is no
   matching one-hour media fixture, so this is a text-parser probe only, not seeking evidence.

## macOS native transcript protocol

1. **MAC-01:** In a new test project import the Danish WAV and attach `danish/mac.txt` using
   the installed media-transcript importer. Record how it interprets the F5-style markers and
   speaker labels. Expect four passage paragraphs and exactly the two named speakers.
2. **MAC-02:** Compare all text to the same flattened content oracle as Windows. Pay special
   attention to `Hun sagde: ` / `She said: ` inside content: additional speaker labels or dropped
   content are a failure to record, even when the outer speaker labels appear correct. Check
   emoji, accent code points and punctuation through an available text export, not sight alone.
3. **MAC-03:** Repeat the cue/rounding/gap/end checks in WIN-03; record actual marker and seek
   interpretation. A successful plain-text import without functional media times does not
   satisfy the timing case.
4. **MAC-04:** Repeat in a fresh English project. Inspect `start-only/mac.txt` separately;
   there must be a start marker and no fabricated end marker. Record compatibility as observed;
   the one-hour text-parser control supplies no playback evidence.

## Shared preservation and coding checks

- **BOTH-01:** Inspect each `working-project-*.zip` manifest/report before importing its
  transcript members. The four designated source version/hash pairs match `identities.json`;
  native archives explicitly skip missing-start and missing-Working, JSON only missing-Working.
  Check that skipped text remains in its readable/JSON control copies and that no empty native
  file masquerades as a complete export. No timestamp is inferred for the untimed passage.
- **BOTH-02:** Compare decoded JSON to exact fixture text, including CRLF and both accent forms.
  If manually coding native flattened text, locate the exact displayed quote independently;
  source-version code-point offsets cannot be copied across changed whitespace. Record source
  and imported-text correspondence or refuse the mapping. Current native text exports carry
  no machine-readable coding/version links.
- **BOTH-03:** Read both reviewed-coding JSON files. In a disposable test project manually code
  only the three accepted items per source. Confirm corrected speed evidence and the intended
  decomposed-accent occurrence. Keep a separate rejection/pending ledger; do not create approved
  coding for rejected or unreviewed items. This manual transcription is a design probe, not an
  existing automatic CBS→NVivo coding transfer or a validated XML mapping.

## REFI-QDA design probes — current app support is NOT_IMPLEMENTED

No `.qdc` or `.qdpx` file is generated by this pack. Existing reviewed JSON is **not** REFI-QDA.
Hierarchical CBS codebooks, summaries/memos and REFI import/export remain planned in T88.
The flat current codebook is preserved; the proposed hierarchy and memo in
`fixture.json/refi_design_probe` are separate manual requirements, not implemented fields.

- **REFI-01:** If the installed NVivo version offers REFI codebook export/import, manually
  create the proposed `Work` parent with Flexibility/Speed children and separate Literal text
  node. Export `.qdc`; import it into a second empty project. Record every node/name/definition,
  parent-child edge and the application's ID preservation/remapping. Explicitly record whether
  stable external IDs can be represented. Repeat independently on each platform.
- **REFI-02:** If supported, export the manually assembled media/transcript/coding/memo project
  as `.qdpx`; import into another new project. Inspect full exact text, both named speakers,
  all intervals, code hierarchy and all six accepted spans across the two languages. Confirm
  rejected/unreviewed items were not promoted to coding. A ZIP/XML validity check is not a pass.
- **REFI-03:** Round-trip the synthetic memo from `refi_design_probe`, linked to its intended
  source/code. Record text and linkage preservation separately. Record polarity, attribution,
  review states/history, immutable version/hash links, CRLF normalization and timing precision
  as supported/mapped/unsupported individually; do not silently flatten/drop them. Do not claim
  that current reviewed JSON provides history or memos which it intentionally excludes.
- **REFI-04:** Exchange each exported `.qdc`/`.qdpx` between the two actual platforms if both are
  available. Import into fresh projects, never overwrite the original test project. Repeat the
  same checks and document directional limitations and exact product versions. Missing second
  platform/product capability is BLOCKED/NOT_SUPPORTED, not cross-platform acceptance.

## Acceptance decision and follow-up

Bind every observation to input SHA-256, source version/hash, platform/build and case ID.
Attach evidence and a minimal reproduction for failures. A reviewer signs off transcript
text/speakers/timing, coding correspondence and each REFI mapping separately. Readable-text
success cannot approve media timing, and manual coding cannot approve a future `.qdpx` writer.
Any proposed automatic mapping must preserve source/destination identity, exact text and review
state, and explicitly report unsupported material before implementation. Keep T88 and actual
Windows/Mac acceptance pending until this evidence exists.

Contracts and limits come from ADR 0010 (native transcript formats), ADR 0024 (export gaps),
ADR 0093 (Working versions), `docs/topic-evidence.md` and `docs/ai-analysis-nvivo-roadmap.md` in
the source checkout. This protocol does not assert current third-party menu names or support.
