# Offline spelling dictionary pins and feasibility (9 November 2026)

Status: research and isolated synthetic evaluation on the unmerged spelling branch (9 November), carried into v2 with the hidden spelling port ([ADR 0185](../decisions/0185-local-passage-spelling.md)). This does not activate spelling in MVP, establish institutional licence clearance, or validate real transcript quality.

## Publisher and fixed sources

The new [catalog](../../src/cbs_transcriber/catalogs/spelling_dictionaries.json) pins public `.aff`, `.dic` and complete licence files from [wooorm/dictionaries commit 8cfea406b505e4d7df52d5a19bce525df98c54ab](https://github.com/wooorm/dictionaries/commit/8cfea406b505e4d7df52d5a19bce525df98c54ab). The publisher explicitly distinguishes its code licence from each dictionary's own licence in its [README](https://github.com/wooorm/dictionaries/blob/8cfea406b505e4d7df52d5a19bce525df98c54ab/readme.md). Dictionary bodies are not added to Git.

The [pinned Danish README](https://github.com/wooorm/dictionaries/blob/8cfea406b505e4d7df52d5a19bce525df98c54ab/dictionaries/da/readme.md) identifies Stavekontrolden as upstream. Its licence notice identifies version 2.8.034, dated 2023-11-01. The upstream homepage was unavailable to the research browser; provenance is based on the fixed distributor README/notice, not a successful live upstream availability check.

The [pinned English README](https://github.com/wooorm/dictionaries/blob/8cfea406b505e4d7df52d5a19bce525df98c54ab/dictionaries/en-GB/readme.md) identifies the SCOWL publisher. The [actual notice](https://github.com/wooorm/dictionaries/blob/8cfea406b505e4d7df52d5a19bce525df98c54ab/dictionaries/en-GB/license) identifies en_GB-ise 2020.12.07, size 60. Bare app language `en` explicitly selects **British English with -ise spelling**, not wooorm's separate American `en` dictionary. The current [upstream site](https://wordlist.aspell.net/) now calls its newer database ESDB; this catalog deliberately does not claim that the fixed 2020 dictionary is the latest upstream release. `en-US` is not silently mapped to en-GB. Initial catalog scope is Danish and English only.

| Dictionary | File | Bytes | SHA-256 |
|---|---|---:|---|
| da |index.aff|81584|cb5ef704de0ab8575c883c97981d5d242260c7ebe47b28c15c205443aca15976|
| da |index.dic|3719300|9a12345e1881c96f18cc460dbd062e47509571d5f2a2a9bf041da169856dc19e|
| da |license|787|ef2785b76dbcb1d0b620435158624a9002b995ee4521cf5d41b055e29eb9d1af|
| en-GB |index.aff|3086|8ae1f19d4840d957728ad90555d5a8dff6cc5c046279c95ff0c00fc0a0136c7b|
| en-GB |index.dic|552106|869fe17ba4ee4b5401c60a666ee2d6a3dcc237f460b7294435df1cc6a799aa57|
| en-GB |license|15739|81b644347b40804c25811267efbea024a30b2bb1642fefa9c9c50f7b0bbcb67f|

All six catalog descriptors were compared with the downloaded bytes, including exact size and SHA-256. Retrieval used public HTTPS GETs with no authentication and redirects disabled, bounded to 8 MiB per object and 20 MiB total for this evaluation. Files remain in private Temp storage for evaluation. No private text or recordings were sent anywhere.

## Licence provenance, not clearance

- **Danish:** the [fixed package metadata](https://github.com/wooorm/dictionaries/blob/8cfea406b505e4d7df52d5a19bce525df98c54ab/dictionaries/da/package.json) declares alternative GPL2/LGPL2.1/MPL1.1 terms. The [pinned notice](https://github.com/wooorm/dictionaries/blob/8cfea406b505e4d7df52d5a19bce525df98c54ab/dictionaries/da/license) names those versions and the rights holders/data provenance. The catalog normalizes the fixed-version expression to `GPL-2.0-only OR LGPL-2.1-only OR MPL-1.1`; it does not assert an or-later permission or select an institutional legal position.
- **English:** the [publisher metadata](https://github.com/wooorm/dictionaries/blob/8cfea406b505e4d7df52d5a19bce525df98c54ab/dictionaries/en-GB/package.json) uses the shorthand `(MIT AND BSD)`. Its complete notice contains several historical component terms and attributions, including Atkinson/VarCon, modified Ispell, WordNet and UKACD. That shorthand is not represented as a verified modern MIT/BSD-3-Clause expression. `LicenseRef-SCOWL-2020-12-07` is a catalog-local SPDX reference defined by the exact pinned full notice and hash above. A standard-ID mapping and institutional clearance remain unassessed.

The installer must retain and verify the complete licence as a mandatory third artifact alongside the dictionary pair. A successful download or local use is not a determination of redistribution, attribution or institutional approval. No dictionary body is redistributed by this repository, and this work adds no legal approval gate of its own.

## Spylls 0.1.7 isolated smoke evaluation

Only synthetic word lists and the pinned public files were used. This is a small feasibility probe on one Windows host, not a quality corpus, a language benchmark or clean-machine acceptance. Engine/dependency licensing is tracked separately by the integration owner.

| Observation | Danish | English en-GB |
|---|---|---|
| Cold dictionary load observed | 2.13 s and 3.63 s in separate phase probes | 0.478 s |
| Warm 60-token lookup, 3 samples | 219–276 ms; median 236.9 ms | 2.27–2.30 ms; median 2.28 ms |
| Hand-picked lookup checks | 10 ordinary words accepted; 5 typos rejected; 4 morphological/compound examples accepted | 10 ordinary words accepted; 5 typos rejected; 4 inflections accepted |
| Suggestion generation | No first suggestion for synthetic `sproggg` before the 20 s whole-child cap after loading/checks; earlier aggregate trial also hit 45 s cap | First correction for 3 tested typos below 1 ms; collecting up to 5 or exhaustion took 247–285 ms |
| Whole-process peak working set | 223440896 bytes | 65089536 bytes |

Danish lookup is usable as a bounded local check, but the 60-token fixture missed the proposal's 50 ms target. The observed cold-load and whole-process memory figures must be accounted for by worker bounds; they are not per-dictionary exclusive memory measurements. These samples do not establish a worst-case check time.

**Danish is explicitly check-only for this pinned engine/dictionary pair.** Its catalog capabilities disable suggestions with `max_suggestions=0`; the API should return a check-only status without launching suggestion work. English permits at most 5 suggestions, with hard worker deadlines; the upper bound is not a promise that five will exist or finish. Neither language can run unbounded suggestion generation in the serving process. Killing/reaping timed-out isolated work and keeping load/check/suggest limits separate are required implementation properties, not inferred from this research script.

## Reproducibility and scope

Private Temp evaluation artifacts: `spelling-dictionary-publisher-eval.json` (full source inventory); `spelling-real-dictionary-smoke.py`; `spelling-real-dictionary-smoke.json`; `spelling-real-da-lookup.json`; phase logs including the retained timeout evidence. The public evaluation directory contains only fixed dictionary/notice/metadata bytes. It is not an app cache and is not imported as runtime authority.

Actual checking/suggestions must use installed verified local files and perform no network requests. Only an explicit public-dictionary installation path may retrieve these fixed URLs; it must not send transcript text, credentials, arbitrary URLs or redirects. Custom vocabulary, feature defaults, UI draft fences, cache ownership and worker security are separate integration work. Institutional licence review, real Danish/English quality, unsupported regional variants and real platform acceptance remain open. The spelling branch must remain separate from MVP until the user's later approval.
