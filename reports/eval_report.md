# Evaluation report

Run `20261120T064235Z` scored 2026-11-20T07:14:06.771722+00:00. Agent model `claude-opus-5-5` (effort `low`).
Reproduce the scoring with `make eval-offline` (no API calls) or the full run with `make eval` (live, budget-guarded).
Numbers below come from `reports/eval_results.json`.

## Golden set

* **101 questions** from 25 templates (88 answerable,
  13 unanswerable). Built by `supplier_graph.evalh.golden`; nothing is hand-labeled.
* Answerable expected values come from the **SQL path** (DuckDB) and are **verified with the graph path** (NetworkX
  traversal). A question whose two paths disagree is dropped and logged to `reports/golden_dropped.jsonl`;
  **0 were dropped** in this build. Country-risk and award questions are verified the same way
  (SQL table vs. graph node attributes).
* Unanswerable questions use verifiably absent entities: invented supplier names (the resolver finds no
  candidates and a fuzzy scan scores below 80), countries outside the risk feed, nonexistent award IDs and
  program codes, and questions needing fields the graph doesn't hold (delivery rates, headcount, forecasts).
* Every item records its `label_source`.

## Headline metrics

| metric | value |
|---|---|
| answer correctness (answerable) | **98.9%** |
| correct refusal rate (unanswerable) | **100.0%** |
| false refusal rate (answerable) | 1.1% |
| citation precision (cited IDs that exist **and** appeared in this run's tool output; micro) | **100.0%** |
| citation existence rate | 100.0% |
| fabricated citations (IDs not in the data) | 0 |
| required-citation hit rate (key entity cited) | 98.9% |
| tool-call correctness (expected named query/tool called with the expected key parameter) | **95.9%** |
| steps per question (mean / max) | 2.505 / 5 |
| cost (total / mean per question) | $3.7745 / $0.0374 |
| latency (mean / p90) | 7.2545s / 10.21s |
| API/budget errors | 0 |

Total Anthropic spend for the whole project at scoring time: **$16.7425** (cap $20).

## Results by question type

| template | kind | n | correct | tool-call correct | citation precision | steps | cost/q | latency |
|---|---|---|---|---|---|---|---|---|
| absent_award | unanswerable | 2 | 100.0% | 100.0% | n/a | 2.0 | $0.0220 | 4.21s |
| absent_program | unanswerable | 1 | 100.0% | 100.0% | n/a | 2.0 | $0.0212 | 3.58s |
| absent_supplier | unanswerable | 3 | 100.0% | 100.0% | n/a | 2.0 | $0.0212 | 4.13s |
| award_sole_source | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0266 | 4.708s |
| blast_radius_awards | answerable | 5 | 100.0% | 100.0% | 100.0% | 3.0 | $0.0370 | 8.52s |
| blast_radius_programs | answerable | 5 | 100.0% | 100.0% | 100.0% | 3.0 | $0.0368 | 8.322s |
| country_prime_awards | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0345 | 5.122s |
| country_risk | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0244 | 4.602s |
| dependent_offices_count | answerable | 5 | 100.0% | 100.0% | 100.0% | 3.0 | $0.0383 | 8.396s |
| hard_company_rollup | answerable | 3 | 100.0% | 100.0% | 100.0% | 4.3333 | $0.0517 | 9.95s |
| hard_feed_country | answerable | 3 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0476 | 8.81s |
| hard_noisy_name | answerable | 3 | 66.7% | 66.7% | 100.0% | 2.6667 | $0.0370 | 7.3767s |
| hard_office_flagged_blast | answerable | 3 | 100.0% | 0.0% | 100.0% | 3.0 | $0.0682 | 9.7833s |
| hard_prime_customers | answerable | 3 | 100.0% | 100.0% | 100.0% | 3.0 | $0.0378 | 9.61s |
| hard_single_point | answerable | 3 | 100.0% | 100.0% | 100.0% | 3.6667 | $0.0514 | 9.97s |
| office_flagged_share | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0455 | 8.916s |
| office_hhi | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0419 | 7.206s |
| office_top_supplier | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0343 | 6.472s |
| out_of_schema | unanswerable | 3 | 100.0% | n/a | 100.0% | 2.6667 | $0.0431 | 8.7233s |
| parent_subsidiaries | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0287 | 5.266s |
| program_top_supplier | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0320 | 5.648s |
| risk_not_in_feed | unanswerable | 4 | 100.0% | 100.0% | 100.0% | 3.0 | $0.0404 | 8.4625s |
| sole_source_share | answerable | 5 | 100.0% | 100.0% | 100.0% | 3.0 | $0.0401 | 8.318s |
| subcontractor_count | answerable | 5 | 100.0% | 100.0% | 100.0% | 3.0 | $0.0400 | 7.812s |
| top_sole_source_country | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0346 | 7.05s |

## LLM judge (free-text quality only)

Correctness is never judged by an LLM; it is scored deterministically above. Judge model `claude-opus-5-5`, validated on 40 cases with known labels (20 template-rendered correct answers and 20 deliberate corruptions: wrong value, wrong entity, false refusal, invented answer). **Agreement 100.0%** (known_good: 100.0%, false_refusal: 100.0%, wrong_value: 100.0%, wrong_entity: 100.0%, invented_answer: 100.0%). The use threshold is 85.0%, so the judge may be used.

The judge's free-text pass was not run on this agent run (skipped (--no-judge or offline)); it is optional and costs extra API calls.

Judge validation limits: the corruptions are deliberate and fairly blatant, and the "good" answers are
template-rendered, not real agent prose. 100% agreement here shows the judge catches gross contradictions and
false or invented refusals. It does not show it catches subtle errors (a slightly wrong share, a
right-number-wrong-unit). Treat its pass rate as a smoke test of prose quality, not a correctness metric.

## Failure analysis

Failure categories: {'false_refusal': 1}.

* **hard_noisy_name-3b531f36** (false_refusal). Q: How many awarding offices depend on ORACME ELEVATOR HOLDCO, LIMITED, directly or through subcontracts? Give the count.
  Expected `2`, got `null` (answerable=False). Tools: resolve_supplier.

## Metric definitions

* **Answer correctness**: the final JSON `answer` matches the expected value. Ints exact; shares within ±0.01;
  HHI within ±2% relative; IDs exact; ID lists as sets; booleans exact. A refusal on an answerable
  question counts as wrong.
* **Correct refusal**: on unanswerable questions, the agent returns `answerable: false`.
* **Citation precision**: of the IDs in the final `citations` list, the share that exist in the data **and**
  were returned by a tool in that run (grounded). Fabricated IDs count against it.
* **Tool-call correctness**: the run called the named query (or tool) that the template's deterministic answer
  came from, with the same key parameter (supplier_id, office_code, country_iso3, ...). Not defined for
  out-of-schema questions.

## Limits

* Templates cover the query library by construction, so this measures whether the agent picks and parametrizes
  the right query, resolves names, reads results correctly, cites, and refuses. It does not measure open-ended
  analysis.
* Names in supplier questions are chosen to be unique in the data. Ambiguous names (one company holding many
  UEIs) are a known harder case and are exercised only in the demo, not scored.
* One run per question with adaptive thinking at low effort: results can vary run to run. The pytest regression
  thresholds sit below the observed values to absorb some variance.
