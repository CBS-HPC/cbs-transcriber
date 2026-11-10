# 0116 — Preserve repair speaker names and frozen model evidence

Accepted 30 October 2026. Refines repair result and producer evidence without changing
inference, saved source text, timings, database schema or historical frozen requests.

A single-passage timing repair recombines that slice with untouched source passages. Its
worker's slice-only speaker map must not replace the full frozen source map: the combined
result retains all existing speaker names and labels, including unselected passages. The
single-passage path is alignment-only and does not discard newly detected speakers.

For a CBS Runtime batch with a frozen plan, report the validated model/dependency pins from
that plan rather than reconstructing them from a later host registry. Conflicting or malformed
present pins are rejected, and absent frozen dependencies remain unknown. Validated ASR
language evidence may provide its observed ASR pin. No-plan local and older remote jobs keep
their previous behavior; no historical result is backfilled.

The Runtime singleton contract treats an absent or explicit-null batch plan as legacy only
when no batch owner exists. Keep that path's prior metadata behavior; reject missing/null
plans on a batch-owned job and reject malformed non-null plans without registry fallback.

Synthetic worker/coordinator and Store tests exercise selected repair names, sibling
preservation, registry changes after freezing, missing/conflicting pins and legacy cases.
They do not establish real UCloud model execution or inference accuracy.
