# Final report

## Summary (2026-11-19)

**Latest: a more useful app, and the agent re-evaluated.** Five additions, all read-only, offline and sourced from
the graph and DuckDB: an *Overview* landing tab (three headline facts, featured suppliers); *What if it fails?*
(remove suppliers; offices, dollars and programs affected, and per office the alternative suppliers doing the same
4-character service code, ranked by whether they already serve that office); an *Office view* (top suppliers, top
share, HHI, sole-source suppliers, its own graph); linked navigation (click a table row or graph node, Back returns);
and deterministic follow-up suggestions after agent answers. 188 of 757 offices rely on one sole-source supplier for
over half their spending. The agent gained a fifth tool, `what_if_remove`, and its prompt now answers the answerable
part of a question and cites nothing for the missing part. With the owner's approval the API cap rose from $10 to
$20, and the agent eval was re-run on the current prompt and tools (Opus once, Haiku three times with and without the
guard, the no-tools baseline once; logs of the earlier prompt are archived, not mixed in): v1 Opus 87/88 and 13/13;
challenge set (now 44 answerable + 16 traps, including 8 what-if questions) Opus 44/44 and 15/16, Haiku 43/44, 44/44, 43/44 with
traps 12/16, 12/16, 12/16, Haiku + guard traps 15/16, 16/16, 16/16, no tools 0/44.

**Earlier: country-risk feed v2.** The MENA feed moved to schema 2.0.0, label escalated_next4w_ucdp (a UCDP-defined
escalation, replacing the ACLED-defined one) with `skill_status = beats_base_rate`. `.env` now points at `v2/latest`;
`make feed-snapshot stage er graph backtest` rebuilt everything, and the 8 questions whose expected answers changed
were re-asked for Opus and all six Haiku runs ($0.30). Every score is unchanged. A backtest gate, fixed before the run,
decided whether `p_escalation` may feed the termination-likelihood model: **not adopted: 0% point-in-time coverage**.
The v2 feed's forecasts start 2026-08-24, after every feature year (FY2022-FY2024), so it cannot be tested without
using the future to predict the past. Feed-country exposure was testable and gave no gain (AP -0.0004 [-0.0073, 0.0071]).
Country risk therefore stays context, the likelihood model and every backtest number are unchanged, and the app,
site and agent answers say why.

* **Published** as a fresh public repository with a single commit
  ([github.com/lmcnulty7/defense-supplier-risk-graph](https://github.com/lmcnulty7/defense-supplier-risk-graph)):
  code under MIT (`LICENSE`), data attribution in `NOTICE.md`, and no output of the retired ACLED-based v1 feed
  (see *Public release* below).
* **ER operating point:** chosen on half the held-out records and confirmed on the other half, it cut absent-company
  false links 8.7% → 4.4% and wrong links 3.8% → 0.6%, for 83.6% → 78.5% correct links.
* **Opus variance:** Opus ran once per set with the current prompt (near-miss traps: 7/8); its run-to-run variance is not
  measured.
* **One source of truth:** `reports/HEADLINE.md` (`make headline`); README, this report, the site, brief, demo script
  and walkthrough are tested against it.
* **Presentable:** one-screen README, a 2-minute demo script with exact clicks, a regenerated interview walkthrough,
  and a fresh clone that rebuilds every report offline with no API key (checked).
* Spend: $16.74 of the $20 cap (raised from $10 by the owner on 2026-11-19; `BUDGET_USD=19.40` in `.env`). Tests: 176
  tests (live-API tests skip without `RUN_LIVE=1`).

*Portfolio project on public data; the customer persona is fictional; not used by, endorsed by, or built for the DoD
or any company.*

**Present it:** `make offline` (or `make site`) writes `site/index.html`; `make app` runs the live demo;
`docs/demo_script.md` is the shot list; `docs/INTERVIEW_WALKTHROUGH.md` is the interview prep.

## What was built

1. **Ingestion** (`make pull`, `make pull-tx`, `make feed-snapshot`): cached USAspending bulk jobs (DoD PSC-J prime
   awards and subawards FY2023-FY2025; 187,320 contract transactions FY2022-FY2025), a budgeted SAM.gov public-API
   enrichment, and a schema- and skill-checked MENA risk-feed snapshot. Every pull records its request body, time,
   row counts and SHA-256; the cache is committed.
2. **Entity resolution** (`make er`): UEI, then CAGE, then blocked matching (name, acronym and facility keys) with
   learned evidence weights and an operating point chosen on held-out records. Automatic label sets with identifiers
   hidden, plus an end-to-end resolution test.
3. **Knowledge graph** (`make graph`): 6 node labels and 6 edge types with provenance, verified against Neo4j; risk
   screens and 10 named queries, each in graph and SQL with exact agreement.
4. **Risk backtest** (`make backtest`): a time-split test of whether the screens anticipate next year's contract
   terminations, against size, prior-termination and current-flag baselines, and a FY2026 likelihood-by-impact
   outlook.
5. **Agent:** a Claude tool-use loop with five read-only tools (including the `what_if_remove` scenario) that cites IDs
   or declines, answers the answerable part of a mixed question, a grounding guard in the name resolver, and a
   deterministic skill caveat on country-risk answers.
6. **Evaluation:** a v1 question set (101), a harder challenge set (60, including 8 what-if questions) and a
   partial-answer regression (2), all dual-path verified; Haiku and
   no-tools baselines; a validated LLM judge; pytest gates.
7. **Packaging:** README, `reports/HEADLINE.md`, results site, Streamlit app (overview, what-if scenario, risk table,
   likelihood x impact, graph explorer, office view, agent, about), design doc, brief, runbook, demo
   script, interview walkthrough, data licenses.

## Key numbers (all from `reports/HEADLINE.md`)

| Area | Number |
|---|---|
| Data | 57,596 prime awards ($225.2B lifetime obligations), 35,842 subawards, 11,658 suppliers, 9,096 parents, 757 offices; 187,320 transactions |
| Flags | 86.75% of obligations go to a supplier with at least one flag |
| ER, pairwise (test split) | identifier-derived P 0.949 / R 0.909; synthetic P 0.954 / R 1.000 |
| ER, end to end (620 renamed records, UEI withheld) | correct links 55.8% (v1) → 82.7% after facility blocking + learned weights |
| ER, shipped operating point (confirmation half) | absent-company false links 8.7% → 4.4%; wrong links 3.8% → 0.6%; correct links 83.6% → 78.5% |
| Neo4j parity | all node and edge counts equal; Cypher blast radius 30/30 |
| Backtest (FY2025 outcomes) | ROC-AUC: flags 0.635, contract count 0.758, model 0.772. Gain over size: AUC +0.013 [-0.013, 0.039]; average precision +0.070 [0.028, 0.110] |
| Flags vs likelihood (odds ratios) | concentrated 3.06 → 0.77 after size; sole-source 0.66 [0.52, 0.82]; geo-exposed 1.75 → 1.00 without one Navy office in Japan, which is 17%-38% of each year's events |
| Agent, v1 (101 questions) | Opus 87/88 answerable, 13/13 refusals, 0 fabricated IDs. Haiku 83/88, 83/88, 83/88; IDs not found per Haiku run 1, 0, 0 (guard: 0, 0, 3), real suppliers cited under the wrong prefix |
| Agent, challenge (44 + 16 traps) | Opus 44/44 and 15/16. Haiku 43/44, 44/44, 43/44, traps 12/16, 12/16, 12/16. Haiku + guard traps 15/16, 16/16, 16/16. Opus without tools 0/44 |
| Near-miss traps (8) | Haiku 4/8, 4/8, 4/8 → with guard 8/8, 8/8, 8/8; Opus 7/8 (one run). The guard fires on 0 of 96 logged Opus lookups on answerable questions |
| Judge | 40/40 blatant, 30/30 subtle planted errors |
| Country-risk feed | schema 2.0.0, label escalated_next4w_ucdp; skill status `beats_base_rate`. As a likelihood input: not adopted: 0% point-in-time coverage. Feed-country exposure: AP -0.0004 [-0.0073, 0.0071], not adopted |
| Anthropic spend | **$16.74**, under the $20 cap (raised from $10 on 2026-11-19) |
| Tests | 176 tests |

## What the results say

* **The flags measure impact, not likelihood.** They rank next-year terminations worse than contract count;
  concentration's raw effect is size; sole-source relationships are the most stable; the geographic effect is one
  office's practice. The product shows likelihood and impact on separate axes.
* **The agent's failure mode is grounding, not reasoning.** With named, verified queries even Haiku answers every
  structured question. A name that is almost right breaks it; the guard fixes that in the tool.
* **Pairwise ER metrics overstate.** Above 0.9 on pairs, 55.8% end to end. Blocking was the cause; the operating
  point then traded a few correct links for far fewer false merges.

## Honest caveats

* The disruption label is a proxy dominated by terminations for convenience.
* Decisions informed by results are disclosed with before and after numbers: ER label exclusions, the hard tier and
  accepted alternative queries, the challenge-set revision after a Haiku pilot saturated, and the facility key. The
  ER operating point is clean (tuning half vs confirmation half).
* Opus's full-set variance is unmeasured (budget); only its near-miss traps were repeated.
* Citation checks prove IDs exist and came from the tools, not that each supports its sentence.
* Subcontract reporting covers 3.8% of prime awards, so indirect dependencies are a lower bound.

## Open items

* **SAM.gov enrichment is partial:** 67 entities; `make sam` adds more within the API's daily limit.
* **Country risk as a likelihood input:** re-run `make backtest` when the MENA feed publishes forecasts covering
  FY2022-FY2024; the pre-registered gate decides.
* **Opus full-set variance** is unmeasured (budget); only its near-miss traps were repeated.

## Two-minute talk track

> "The customer is a fictional sustainment analyst: which contracts depend on concentrated, sole-sourced or exposed
> suppliers, and what breaks if one fails?
>
> I pulled every DoD maintenance contract and subcontract from USAspending for three years, cached and hashed so
> every number traces to a source, and resolved 11,658 suppliers. My matcher scored above 0.9 on labelled pairs, but
> with identifiers withheld it resolved only 55.8% of renamed companies. The losses were acquisitions at the same
> facility, which my blocking never compared. Facility blocking and learned weights took it to 82.7%, and a stricter
> operating point, chosen on half the records and confirmed on the other half, cut false merges of absent companies
> from 8.7% → 4.4%.
>
> The graph links suppliers, parents, awards, offices and countries, and I checked it twice: every query in graph
> and SQL, and the whole graph against Neo4j.
>
> Then I tested my own flags against next year's terminations. They score 0.635, below contract count at 0.758.
> Concentration looks three times riskier until you control for size, and sole-source suppliers are the most
> stable. The flags measure impact, so the product shows likelihood on its own axis.
>
> The agent is Claude with five read-only tools that cites record IDs or declines. My first test set saturated, so I
> built a harder one with traps. Even Haiku answers nearly every structured question. Its failure was a company name
> one word off: it answered for the look-alike. I fixed that in the resolver, not the prompt: 4/8, 4/8, 4/8 without
> the check, 8/8, 8/8, 8/8 with it. Every number is in one generated file and tested, and the whole project cost
> $16.74."

## Public release

The public repository is a fresh history with one commit, built from the working repository by an export step that
keeps the code, tests, presentation documents and publishable data, and leaves out:

* the private working files (agent instructions, plans, the progress and blocker logs);
* everything that holds output of the retired ACLED-based v1 country-risk feed. ACLED's EULA (section 7.1, effective
  2026-11-01) bars ML training and testing on ACLED content without written consent, and the feed's producer
  retired v1 for that reason. Concretely: the v1 feed snapshot (never in this history), two console logs and a pilot
  result file that print v1 answers, and the superseded attempts in the agent-run logs. Those are the
  country-risk questions asked before the feed changed and then re-asked; every scorer uses only the last attempt
  per question, so the public logs reproduce the same scores. In `reports/judge_validation.json` one v1 probability
  quoted in two judge reasons is replaced by a marked placeholder; verdicts and agreement are unchanged.

Checked on 2026-11-19:

| Check | Result |
|---|---|
| Secrets | None in any file: no `sk-ant-` strings, API-key query strings or bearer tokens; `.env` and `.streamlit/secrets.toml` are not included. SAM responses carry only SAM's `REPLACE_WITH_API_KEY` placeholder. |
| v1 (ACLED-based) feed output | None in data, logs, reports or the site (checked by model version and by every v1-only probability). |
| Author | GitHub no-reply address; no personal email or local paths in any file. |
| Licenses | Code MIT; data attribution in `NOTICE.md` and `docs/DATA_LICENSES.md` (USAspending, SAM.gov public API, UCDP CC BY 4.0, GDELT). |
| Clean clone | A fresh clone, `make install` (needs internet for pip), then with no `.env`: `make test` passes (live-API and Neo4j tests skip) and `make offline eval-offline compare-offline guard-eval headline` reproduces `reports/HEADLINE.md` exactly. |
