# Isolated timing-hint policy: verification

10 November 2026. Local branch `codex/post-mvp-timing-hints`, based on post-MVP source
checkpoint `7cf165c15f7748ed3242e1cad300d2ccf5c53d91`. The parent documentation/browser-test
checkpoint `67068dc` has identical product bytes to that base. This implementation is not
merged, installed or released. Public main and installed MVP 0.1.0 remain unchanged.

See [ADR 0169](decisions/0169-versioned-timing-hint-invalidation.md), SPEC V211/T133 and
the [reviewed proposal](proposals/legacy-timing-hints-2026-11-10.md).

## Implemented behavior

- The two verified fresh timing acceptance points seal the exact host timing policy in a
  canonical v2 envelope within the existing Working-job authority. Ordinary parameters,
  a legacy role row, key removal or a present-null/unknown policy cannot grant or downgrade
  that generation. Existing plain v1 role authority and genuinely legacy missing-policy jobs
  stay legacy.
- Actually applied timing invalidates the passage's immutable creation alignment-fit word
  hints, including words omitted from a partial new map. Recognition confidence and independent
  passage metrics remain; raw hints, wording, voices, bounds and unselected dictionaries retain
  their existing contracts. Structural joins carry invalidation from either input.
- New local/cloud host Apply and new safe/conflict timing adoption use explicit generations.
  Old history, canonical successors, Working proofs and lost replies dispatch their stored
  generation. Public conflict projections report the stored generation truthfully.
- Local capture and contained checks understand both generations and verify the birth seal.
  Source pins are updated without increasing caps. There is no schema migration, UI control,
  historical backfill, model invocation or automatic second-pass alignment.

## Frozen scoped gate

All 133 tracked product files were frozen in the private source manifest before these runs.
Every run checks the exact source hashes; the new test modules are hashed separately. The
393-case run did not preserve pre-run test hashes and a test's two blank lines were formatted
after launch. Its product bytes stayed frozen, but it is not an exact-test-byte immutable gate.

| Scope | Distinct outcomes | Evidence |
| --- | --- | --- |
| Sixteen timing, history, merge/conflict, undo, Working and portable modules | 393 passed, zero skipped | `timing-hints-final-focused.xml` |
| Policy/authority, omitted words, stale-map fences, lost replies, structure and contained adversarial cases | 60 passed, zero skipped | `timing-hints-final-adversarial.xml` |
| Stored-v1 conflict, merge and local timing oracles | 3 passed, zero skipped | `timing-hint-legacy-oracle.xml` |

The three XML inventories have no overlapping cases: **456 distinct passes**, no failures,
errors or skips. Independent review verified the artifacts, frozen product bytes, the new-test
hashes and generation boundaries and gave **scoped Go**. The subsequent complete child gate
reran the 393 cases with before/after test hashes to close that evidence gap; only the
separately hashed 60+3 cases were reused. Full-repository Ruff lint/format checks passed
848 files; the final test-only fixture correction also passed its scoped checks.

Private evidence uses the named files under the current machine's temporary/test-results
storage; raw logs and synthetic databases are not committed. SHA-256 references:

- Frozen source manifest: `d9663cc8feda292d119bc4c0f5af203717d77d5c9739b4d7e434e8b74473a881`.
- 393-case XML: `4e8fe1b72d58e2bd989e85bd4a4e4b41add0e60efdfaea5067c7673328baa7c6`.
- 60-case XML: `ae76bf8751e53d8b6f9431ae4066a683e2b31772159d668795dbc5591c918c4f`.
- Three-case XML: `5b9802fc29da848e3120d7db096c8a3c87610a7f1d2caee8a9973b8efec655ff`.

The v1 oracle was first produced by the untouched parent's actual public writers in a
separate process, then verified using child code. The committed test contains synthetic
seeds and a literal expected canonical payload hash, without copying the old implementation,
databases or machine paths. It checks conflict reconstruction and v1 labelling, ordinary-merge
payload compatibility with honestly scoped reporting, and a plain-authority timing receipt
with idempotent Retry. Its expected payload SHA-256 is
`ce4abc887482ae162f1fd63b2aa9a39c92b94270897415285d4ea0e87aa22a61`.

## Complete child backend gate

The initial and final raw ordered inventories match: **5,600 unique non-browser cases**,
with 620 browser cases deselected. The qualified latest union is **5,574 passed, 26 skipped**,
zero uncovered, failing, error or unknown XML outcomes. Independent reconstruction verifies
the official Pytest XML identity mapping, evidence hashes and current file inventories.
All 133 product files, 407 test files and two gate configuration files match their final
snapshots. Product bytes remained frozen throughout.

This is segmented evidence: 63 qualified prior cases plus 5,537 newly executed cases. The
initial complete segment recorded 5,510 passed, 26 skipped and one failure. That failure was
the unchanged portable transport test's same-size object mutation: Windows can retain all
compared filesystem witnesses despite changed bytes. A controlled red/green probe confirmed
the fixture's missing guarantee of observable drift under the existing quiescence contract.
The test now explicitly advances and asserts mtime after the same corruption, retaining its
refusal assertion. No product contract changed. Its complete 110-case module rerun produced
106 passed, four skipped. The latest union takes 5,427 outcomes from the first segment,
110 from that rerun and 63 from qualified reuse; repeats are not added to the unique count.
The original failure and diagnostic-only isolated pass remain preserved separately.

The skips are explicit: 23 filesystem-link/platform-permission cases, one Linux pinned
decoder case, one Linux flock case and one opt-in tiny model-conversion case. They are not
passes. The child's full browser suite, real providers and device acceptance were not run.
The parent's separately verified 25 relevant provider-browser cases are parent evidence.

Private authoritative references, without raw logs or machine paths in Git:

- `final-coverage.json`: `e71aa682ef33696d96fb70b354759f1430eaa04246325ee85833b0a4ab1f07f2`.
- `gate-handoff.json`: `b63b30f08ab14e137ffb198a68a799405d196d3c4feb6da3254e2a577c5b42ae`.
- Initial segment XML: `b2691d40f651d604e09c6957c0b11eaecf76d476331aa056d6134946b155dece`.
- Corrected module XML: `356f4b62331425feb4715e40fb957cdcbf5c094874bccbe033f5ee2bada40007`.
- Final byte inventory: `bf803942c1ff59f37265cdbf9b078261c0ae006f654c5f08c7653e6969284876`.

## Findings and limits

Tests/review exposed and corrected right-input invalidation loss on join (B424), parameter-only
policy authority (B425), malformed generation TypeErrors (B426), and a contained seal read
before resource accounting (B427). The deterministic transport-fixture correction is B428.
Present-policy validation also runs when a tampered
parameter key is absent. The contained role reader enforces its existing 4 KiB bound.
Normalized captured source remains within existing caps: transcripts 65,411/65,536 bytes,
timing capture 23,745 bytes, contained worker 102,327/114,688 bytes.

Cloud host Apply/reopen/lost-reply is tested with mocks. **Cloud historical transformation
remains `not_checked`**; a local capture verifier is not cloud proof. Ordinary merge reporting
still checks parents rather than claiming full transformation reconstruction. Real providers,
device behavior, model quality and institution-specific acceptance are outside this gate.
No production library, remote evidence, running app or serving environment was changed.
