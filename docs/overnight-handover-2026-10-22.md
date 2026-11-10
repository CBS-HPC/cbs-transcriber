# Research branch handover: 21–22 October 2026

Work is isolated on `feature/overnight-research`. Main remains clean at `8d2e555`.
The running app, active jobs, real library and credentials were not changed or migrated.
No merge, push, publication, paid request, private transcript access, real UCloud allocation
or model-weight download was performed.

## Delivered stages

1. **T43 first history/report delivery** (`1464980`): immutable exact result/notes versions,
   backed-up transactional schema-6 migration, frozen enhancement source identity, scoped
   read-only history and historical export, plus a bounded metadata-first methods report.
   Legacy observations explicitly retain unknown prior history. Selected-text inclusion is
   opt-in; reports exclude private notes, filenames, endpoint identities and credentials.
2. **T44 simpler Provider Settings** (`cbf088f`): short Basic setup and one Advanced disclosure,
   concise capabilities/Protected summaries, offline vLLM configuration and preserved saved
   credentials/custom models/policy choices. Late replies cannot erase newer settings drafts.
   Saving a configuration neither hosts a model nor establishes speech readiness.
3. **T38 manual evidence/review prototype**: versioned approved project codebooks, frozen
   source/codebook hashes, exact Unicode quotations, manual topic/evaluation coding, strict
   imported suggestions, immutable proposals, append-only reviews, safe retries, pagination,
   older-source warnings and separate reviewed-evidence JSON export. No automatic sentiment
   model or fabricated scorer is exposed. See [the guide](topic-evidence.md).

## Verification and reviewed findings

Final checks: **474 unit/API tests and 53 Chromium workflows passed (527 total)**. Ruff,
formatting, Prettier, JavaScript syntax, source/wheel builds and package-content comparisons
passed. Commands use the repository-local Python 3.13 environment:

```powershell
uv run pytest
uv run pytest -m browser -o addopts=''
uv run ruff check .
uv run ruff format --check .
npx --offline prettier@3.6.2 --check src/cbs_transcriber/static/app.js src/cbs_transcriber/static/app.css src/cbs_transcriber/static/index.html
node --check src/cbs_transcriber/static/app.js
uv build
```

Independent reviews and regressions cover historical hash corruption/report redaction,
provider draft races, malformed/duplicate-key suggestion imports, exact Unicode evidence,
ownership, rollback, conflicting reviews and post-commit refresh retries. Synthetic Chromium
screenshots were inspected. Build checks compare packaged application/static/worker bytes
with their saved sources; the source archive includes the new guides and tests.

A broad run also exposed two existing enhancement verification issues. A normal held Windows
reader reproduced a frozen-request replacement error through the real uncertain-allocation
retry. Exact unchanged JSON writes are now a no-op; changed writes remain atomic and errors
still propagate (ADR 0034). Separately, the synthetic cancellation worker falsely wrote success
when its child was killed first. Its stub now rejects that unsuccessful exit, with forced-order
coverage and actual interpreter/descendant termination assertions; production cancellation
logic was unchanged. SPEC B77–B82 records the findings and fixes.

Ignored local evidence: `test-results/overnight-final-unit.log`,
`overnight-final-browser.log`, `overnight-final-build.log`, `topic-api.log`, and
`topics-manual-evidence.png`. The earlier failed broad-run log is retained separately as
`overnight-unit-investigation.log`; it is not final passing evidence.

## Limits and next work

- Schema-5/6 upgrades are verified on temporary synthetic libraries only. This branch's
  schema 7 has not been applied to the user's main library.
- T43 still needs complete processing-time artifact/environment/build capture, restore as
  a new version, wider metadata history, larger-history scaling and researcher acceptance.
- T38 automatic text-model execution, topic discovery/maps, methods-report integration and
  Danish/English quality evaluation remain later work. The prototype exports current reviewed
  evidence, with unreviewed metadata; it excludes proposal text and prior review payloads.
- Existing local/UCloud speech quality, real native UCloud/SSH lifecycle acceptance, NVivo
  Windows/Mac desktop imports, and real Intel/Apple Silicon Mac testing remain separate from
  offline software verification. CBS installer/shortcut/plugin deployment is still a roadmap.
- The running main app intentionally does not show these research-branch additions. Review
  and activation are a later step; no automatic merge or server restart is scheduled.

The bounded overnight stages and final checks are complete. The follow-up is disabled on
completion so it does not repeat completed work or expand into the later roadmap silently.
