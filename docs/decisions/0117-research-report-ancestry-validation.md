# 0117 — Validate captured source ancestry in research reports

Accepted 30 October 2026. Refines read-only research report verification without rewriting
stored history, changing producer protocols or adding evidence to legacy results.

Synthetic corrupted rows demonstrated that reports could accept noncanonical snapshots,
contradictory predecessor/source ancestry, source cycles or a retargeted source inconsistent
with preserved producer inputs. Malformed observation metadata could also be projected into
reports. No ordinary writer exploit or proof that authentic historical records were affected
was established.

Within the report's bounded read snapshot, validate canonical version payloads, typed and
bounded observation metadata, continuous predecessor ancestry and stable producer source
identity. Reject cycles and reject a source link that contradicts known captured context or
retained frozen producer source evidence, including exact version identity when available.
Read and validate bounded source metadata once per result and never project raw private job
metadata into a report. Validate selected and unselected Working references under the same rules.

Missing legacy job evidence and unpinned snapshots remain unknown; never resolve them against
today's active result, invent hashes, backfill history or claim stronger proof. These checks
detect defined contradictions, not all possible tampering or data corruption.

Tests use synthetic valid, legacy and corrupted histories and scoped privacy sentinels.
Windows symlink capability and real institutional research-data acceptance remain separate.
