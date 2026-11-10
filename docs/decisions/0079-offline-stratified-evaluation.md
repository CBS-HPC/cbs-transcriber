# 0079 — Offline stratified model evaluation

Accepted 28 October 2026 for T89. Extend the producer-bound runner of ADR 0076 with an offline
plan and supplied-prediction comparison layer. Do not execute models or infer model identity.

A plan freezes candidate/artifact revisions and reference-manifest hashes. Keep Danish
read-speech smoke, Danish interviews, English interviews and code-switching as separate strata.
Only the existing licensed public fixtures are available initially; absent interview strata
remain missing requirements. A small read-speech smoke set is not interview acceptance or an
independent holdout for every candidate. Retain documented training/model-selection exposure.

Supplied prediction bundles bind their declared candidate/artifact and exact references.
Those bindings establish consistency of submitted observations, not which model actually ran.
Operator-supplied identity remains unverified; it is distinct from an accepted producer's
immutable creation receipt. Do not rank unrun models or turn missing metrics into zero/perfect
scores. Reuse existing Unicode normalization, edit counts and reviewed timing/speaker scorers.
Report corpus/stratum coverage and supplied performance scopes explicitly.

Validate bounded strict JSON and safe file paths. Routine comparison is offline and does not
download data, weights or licences. Alias clip/run IDs in output, exclude arbitrary metadata
and private paths, and never overwrite existing output. Retain exact source hashes and limits
needed to reproduce the calculation.

Model-card labels, retrieved governing text, publisher ancestry claims, converted artifacts
and CBS approval are separate evidence. Pin and cite primary sources for new licence findings;
unavailable/gated text remains unavailable. NC/custom conditions and unpinned base artifacts
remain review questions. This stage changes no catalog/default/provider eligibility and
claims no runtime validation, model accuracy ranking, training-data audit or legal clearance.
