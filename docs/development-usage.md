# AI development usage

This ledger records the AI effort used to develop CBS Transcriber. It is development usage,
separate from the app's transcription/GPU/storage charges, human time and licence fees.
Maintain it at release, major milestone and handoff boundaries under [AGENTS.md](../AGENTS.md).
Structured values and assumptions are in [development-usage.json](development-usage.json).

## Provider status

| Provider | MVP accounting status | Owner |
| --- | --- | --- |
| Codex | Recorded local token total reconciled; credit/cost equivalent estimated | Codex |
| Claude | Pending; no figures supplied yet | Claude, as assigned by the user |
| Combined AI total | Pending Claude's accounting and compatible scopes/units | Reconcile after both are available |

Pending means unknown, not zero. Claude should document its own models, token/cache categories,
date scope, source evidence and any actual/estimated cost without copying Codex's credit rates.

## Codex baseline: MVP 0.1.0

Scope is the retained, indexed local CBS Transcriber sessions through publication of
[MVP 0.1.0](https://github.com/CBS-HPC/cbs-transcriber/releases/tag/v0.1.0), source commit
`5cf2e64d5fe5980586f9ec3d170b216eb1f98384`.

- First included usage: **20 October 2026, 09:06:25.858 UTC** (11:06 CEST).
- Cutoff: **10 November 2026, 12:41:11 UTC** (13:41:11 Europe/Copenhagen).
- Last included usage event: 10 November 2026, 12:40:44.622 UTC.
- Coverage: **173 sessions: two main chats and 171 subagents**; 50,455 positive usage updates.
- The user confirmed on 10 November that **all development used Standard speed**.

| Token category | Recorded tokens |
| --- | ---: |
| Uncached input | 164,999,507 |
| Cached input | 13,239,098,112 |
| All input, including cached | 13,404,097,619 |
| Output, including reasoning | 22,392,838 |
| **Total: all input plus output** | **13,426,490,457** |

Cached input is 98.77% of input and is already included in it. Reasoning accounts for
12,097,151 output tokens and is already included in output. Neither subset is added again.
The large input total includes repeated cached conversation/tool context; it is not a count
of unique text or application code.

| Recorded model | Tokens | Standard credit-rate equivalent |
| --- | ---: | ---: |
| GPT-6.1 Sol | 7,199,775,996 | 28,451.52 |
| GPT-6.1 Terra | 6,226,714,461 | 9,247.21 |
| **Total** | **13,426,490,457** | **37,698.72** |

Credit totals are calculated before rounding the displayed rows.

Main chats account for 3,631,633,954 tokens; subagents account for 9,794,856,503.
The figures include project audits, experiments and later-deferred work performed before
publication, not just effort that ultimately shipped. Later release documentation, installation
follow-ups and post-MVP planning are outside this fixed baseline.

## Credits and Edu cost estimates

**37,698.72 credits is a rate-card equivalent, not a verified project credit debit.** Actual
credit spend and CBS's contracted cash price remain unverified. Account balance snapshots are
not a project charge ledger: purchases, replenishments and other work can affect them.

The calculation uses the [published OpenAI Codex rates](https://learn.chatgpt.com/docs/pricing)
viewed on **10 November 2026**, with user-confirmed Standard speed:

| Model | Uncached input / 1M tokens | Cached input / 1M | Output / 1M |
| --- | ---: | ---: | ---: |
| GPT-6.1 Sol | 50 credits | 2.5 credits | 250 credits |
| GPT-6.1 Terra | 25 credits | 1.25 credits | 125 credits |

For each model: `(input - cached) × uncached rate + cached × cached rate + output × output rate`,
dividing each token quantity by one million. This does not establish the historical contracted
rate card or an invoice. API pricing is a separate billing system.

Public university Edu examples give monetary benchmarks:

| Published institutional example | Price / credit | Value of the Standard credit equivalent |
| --- | ---: | ---: |
| [UC Berkeley](https://technology.berkeley.edu/news/new-chatgpt-credit-limit-options) | US$0.04 | US$1,507.95 |
| [Vanderbilt](https://tdx.vanderbilt.edu/TDClient/33/Portal/Requests/Service/233/OpenAI-EDU-ChatGPT-New-Feature-Request) | US$0.05 | US$1,884.94 |

The estimate is therefore **approximately US$1,508–$1,885**, excluding the Edu seat/licence fee.
These examples are not CBS contract terms or a confidence interval for its bill. Included or
prepaid credits may cover consumption without an additional cash payment. No exchange-rate
conversion is assumed. OpenAI's [education-plan guidance](https://learn.chatgpt.com/docs/enterprise/education-plans)
refers institutions to their agreement/account team for their terms.

## Method, checks and coverage limits

The audit selected project threads from local checkout/origin metadata and checked descendant
edges. It excluded an inherited fork baseline of 322,286,626 tokens and ignored 1,159 repeated
counter snapshots across the scanned records. Actual new/cached requests in forked sessions
remain counted. First-request counters followed by positive cumulative increments exactly
match an independent sum of the retained last-request counters. Every session also reconciles
to its metadata counter as inherited baseline plus pre/post-release usage.

No counter resets, arithmetic anomalies or counter-method disagreements were found. An
independent read-only review accepted the recorded local token total, with the billing and
coverage qualifications above. Detailed per-session snapshot hashes/byte counts and the
reproduction script are retained locally; raw histories, identifiers and balances are not
part of this repository.

Deleted/unindexed sessions, other computers and cloud-only work may be absent. The total is
for retained local evidence, not proof of complete account-wide history. The usage audit and
this documentation work occurred after publication and are excluded from the MVP baseline.

## Future checkpoints

Add separate non-overlapping phases for post-MVP planning/implementation and subsequent
releases. Record explicit boundaries, provider/model, input/cache/output categories, scope,
coverage gaps and rate assumptions. If a cumulative milestone view is useful, label it as
cumulative and do not add it to phase totals. Preserve the original release baseline unless a
documented reconciliation correction is needed. Keep measured usage distinct from estimates
and leave missing Claude or billing values pending until their owner supplies evidence.
