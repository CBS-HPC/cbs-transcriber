# Bounded supplied result payload — offline evidence

29 October 2026. T91b3, ADR 0091, V147. The measured policy and pure implementation have
independent GO. Full history/database/package semantics, S1/PR02 and T91 remain incomplete.

## What the policy observes

Only caller-supplied immutable bytes, matching supplied SHA-256, exact snapshot-v1 envelope
and canonical UTF-8 spelling. Inner JSON remains opaque; empty lists and non-dictionary
segment entries are not assigned new passage/speaker semantics. Counts are syntactic.
The committed `1a710ca` and `b21bfd5` history codec functions agree even though broader history
report metadata evolved. The implementation must not import that evolving module or edit
application writer/reader behavior.

All fixed ceilings apply together: **8 MiB raw bytes, 500,000 lexical tokens, depth 24,
2 MiB interior raw quoted-string span, 128-byte atom tokens**. Tokens include opening
containers, quoted keys/values and primitive values. String spans exclude delimiters and
include escape spelling. Valid canonical snapshots below 8 MiB can still be unsupported by
another ceiling. Resource refusal never means corruption, disposal or normalization.

Use strict lexical preflight before JSON graph allocation, then standard-library decoding
with duplicate/nonfinite checks and streaming canonical comparison. Short float tokens may
expand on re-encoding; compare bounded UTF-8 pieces immediately instead of building a whole
canonical string. Preserve caller guards outside curation scopes and do not attach parser
exceptions retaining raw data to the curated refusal.

## Design measurements before code

The ignored experiment ran isolated standard-library children on **Python 3.13.5, Anaconda
64-bit Windows**. It performed no SQLite, Store, provider, inference or history-module import.
Meaning was checked against committed `1a710ca` codec source, full LF source hash
`83f8b1d4a3b5ca01e67adf853423b9e4c71b9f895eb8293d125c40fcff2ecc63`.

Sixteen selected synthetic observations and eleven exact/over-limit controls support the
explicit policy ceiling. They are not permanent test counts or representative interview/ASR
benchmarks. Each allocation sample held input bytes before starting `tracemalloc`; its
additional traced peak includes explicit UTF-8 conversion, JSON graph/hooks and canonical
comparison. It excludes input construction, preflight, tracing metadata, native/RSS and OS
memory. Timings under tracing are not app speed measurements.

| Synthetic shape | Raw bytes | Lexical tokens | Additional traced peak, bytes | B policy |
| --- | ---: | ---: | ---: | --- |
| 8,000 words, duplicated current/original maps | 1,483,412 | 179,689 | 6,867,291 | supported |
| 24,000 words, duplicated maps | 4,472,812 | 539,049 | 20,604,395 | unsupported tokens; peak measured under broader prototype |
| 400,000 empty objects | 1,200,062 | 400,007 | 30,099,633 | supported |
| One 200,000-key object | 3,288,954 | 400,008 | 50,531,632 | supported |
| Same wide object with late duplicate | 3,288,966 | 400,010 | 50,531,700 | duplicate refusal |
| Two 1 MiB Unicode-widened strings | 2,097,245 | 12 | 21,126,699 | supported |
| Two exact 2 MiB widened spans | 4,194,397 | 12 | 42,098,219 | supported |
| Exact 8 MiB, independently bounded widened strings | 8,388,608 | 16 | 75,651,988 | supported |
| 200,000 noncanonical `1e15` values | 1,000,062 | 200,007 | 7,431,209 | canonical-prefix refusal |

The largest observed sample is about **72.15 MiB additional traced allocation**. It is not
a universal upper bound, RSS, a Python/OS heap cap or the existing reader's SQLite-only
64 MiB cap. Dense keys, Unicode widening and sorting can combine differently. No private-data
security acceptance follows from these synthetic observations.

For comparison, whole-buffer re-encoding traced 9,827,869 bytes for the 8,000-word sample,
33,558,925 for the 1 MiB string pair and 15,026,407 for the invalid float array. This justifies
the streaming comparison; it does not establish a general memory/speed improvement.

The B-only controls cover exact/over 2 MiB string spans, 8 MiB minus one/exact/plus one,
depth 24/25, atom length 128/129 and 500,000/500,001 tokens. Every over-budget control called
payload `json.loads` **zero times**. Escaped NUL spelling remains supported; a late unpaired
surrogate refuses. A broader prototype's acceptance of depth 25 is not B acceptance.

Frozen ignored study: `test-results/portable-payload-study.json`, SHA-256
`0b9222bdb74cc43566bf2026630ff1dcef4f98f789c5eb6c433539b9e3e4893a`.
It contains the individual source/report hashes and observations. Final main prototype hash:
`67060036d885fde44a4431fc42dedf5c4dcd2a1b8a850229a7f11817a4a97899`.
Independent review gave design/numerical GO before production code. Ten overlapping tiny
committed-codec semantic probes also pass; they are not implementation-test coverage.

## Implementation verification

- **103 distinct permanent cases pass**, with no skips. The initial run took 26.99 s
  (XML 24.621 s). The final owned-only image passed the same 103 cases in 37.13 s
  (XML 34.833 s), with its own empty pytest configuration/root and explicit module path.
  It needs no Git repository, historical object, extra reference file or active history/core
  module. The overlapping exact-8-MiB case passed separately in 12.82 s (XML 10.682 s);
  these reruns are not additional distinct tests.
- The tests cover frozen snapshot-codec differential meaning, exact and over resource limits
  before decoding, opaque inner JSON, distinct integer/REAL/boolean/signed-zero/Unicode
  spellings, malformed/nonfinite/Unicode refusals, parser-cause privacy, internal resource
  faults, caller exception identity and streaming comparison.
- Independent implementation review gave **GO** on the final hashes after 23 check groups:
  500 canonical vectors against the actual committed `1a710ca` AST-only codec, fourteen
  malformed/Unicode/noncanonical cases, 552 caller-exception observations across eight types
  and 69 guard positions, plus preparse, streaming and privacy/resource controls. These are
  overlapping synthetic checks, not counts to add to the 103 permanent cases.
- Scoped Ruff lint/format and compilation pass. No existing reader/worker/codec/catalog changed,
  and their completed broad suites were not repeated.

The initial draft test oracle depended on `git show`; review replaced it with a small frozen
self-contained reference before ordinary verification. Test/harness refinements also fixed
`zip(strict=True)`, a long citation line, an empty mistaken test selector and parent pytest
configuration discovery. None introduced a production fix or a new runtime invariant; no
zero-test run is counted as verification.

An offline wheel built from committed `7e564ff` plus only the new module contains the exact
frozen source. A fresh Python `-I -S -B` process imported it without SQLite, FastAPI, Torch or
history, accepted opaque JSON, refused a mismatched hash and returned no private canaries.
The probe then explicitly loaded the wheel's actual committed history codec, verifying its
full LF source hash `0f19cefcd1c37e03189108b13991f9a6b258f69c03f7ef219f86c02050ff487e`,
and passed six writer plus six refusal vectors. That explicit oracle import is separate from
the kernel's dependency/purity check; the active working history source was never used.
No package was installed or serving environment synchronized.

Ignored implementation evidence: `test-results/portable-payload-{final,owned-final,focused}.xml`,
`portable-payload-owned-final.txt`, `portable-payload-review-probe.txt` and its runnable script,
and `portable-payload-wheel.txt` with the standalone wheel probe/build scripts.

| Frozen file | SHA-256, Python source normalized to LF |
| --- | --- |
| portable_payload.py | 7fc37c490bcf1fe0268299f383b9a4951cf1ae351631fd6c5b17ce4cd5465698 |
| test_portable_payload.py | 29d54bc9c0a682f5071ad13307d2456eb40c3e65b69a366e1a1f1446736f1ea8 |

The result level is `result_payload_envelope_only`, reference `matched_supplied_payload`.
Only fixed policy/format, `sha256`, `bytes`, syntactic entry/token/depth counts and unchecked
scope labels are returned; no graph or transcript strings escape through the result.

This pure prerequisite leaves database membership, sequence/head/mirror semantics,
source/producer evidence, package/capture/custody, credential exclusion, quarantine and
Restore explicitly unchecked, with `restore_ready: false`. No application API/UI, reader
protocol, schema, library migration, environment synchronization, running-app activation,
paid service, private-data inspection, merge or push is part of the work.

The shared index was occupied by the independent admission/repair stage, so publication
waited for its `c0a6c26` commit before regenerating the owned projection. That commit already
carried this module's AGENTS paragraph; it is not restaged here. New source/test hashes remain
the verified ones above. The other stage's core changes and longstanding document deletions
are excluded from this stage's staging operations.
