# Evaluation report

Run `20261118T092857Z` scored 2026-11-19T07:13:28.808675+00:00. Agent model `claude-opus-5-5` (effort `low`).
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
| required-citation hit rate (key entity cited) | 100.0% |
| tool-call correctness (expected named query/tool called with the expected key parameter) | **99.0%** |
| steps per question (mean / max) | 2.4257 / 4 |
| cost (total / mean per question) | $3.2860 / $0.0325 |
| latency (mean / p90) | 7.1044s / 9.8s |
| API/budget errors | 0 |

Total Anthropic spend for the whole project at scoring time: **$8.9759** (budget $10).

## Results by question type

| template | kind | n | correct | tool-call correct | citation precision | steps | cost/q | latency |
|---|---|---|---|---|---|---|---|---|
| absent_award | unanswerable | 2 | 100.0% | 100.0% | n/a | 2.0 | $0.0188 | 4.54s |
| absent_program | unanswerable | 1 | 100.0% | 100.0% | n/a | 2.0 | $0.0185 | 4.25s |
| absent_supplier | unanswerable | 3 | 100.0% | 100.0% | n/a | 2.0 | $0.0179 | 4.2333s |
| award_sole_source | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0230 | 4.986s |
| blast_radius_awards | answerable | 5 | 100.0% | 100.0% | 100.0% | 3.0 | $0.0303 | 8.23s |
| blast_radius_programs | answerable | 5 | 100.0% | 100.0% | 100.0% | 3.0 | $0.0295 | 9.692s |
| country_prime_awards | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0278 | 5.498s |
| country_risk | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0212 | 4.544s |
| dependent_offices_count | answerable | 5 | 100.0% | 100.0% | 100.0% | 3.0 | $0.0347 | 8.586s |
| hard_company_rollup | answerable | 3 | 100.0% | 100.0% | 100.0% | 4.0 | $0.0449 | 8.8833s |
| hard_feed_country | answerable | 3 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0420 | 7.7633s |
| hard_noisy_name | answerable | 3 | 66.7% | 66.7% | 100.0% | 2.6667 | $0.0333 | 8.1367s |
| hard_office_flagged_blast | answerable | 3 | 100.0% | 100.0% | 100.0% | 3.0 | $0.0566 | 9.5733s |
| hard_prime_customers | answerable | 3 | 100.0% | 100.0% | 100.0% | 3.0 | $0.0330 | 10.0733s |
| hard_single_point | answerable | 3 | 100.0% | 100.0% | 100.0% | 4.0 | $0.0682 | 11.53s |
| office_flagged_share | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0421 | 8.792s |
| office_hhi | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0400 | 7.512s |
| office_top_supplier | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0313 | 6.494s |
| out_of_schema | unanswerable | 3 | 100.0% | n/a | 100.0% | 1.3333 | $0.0204 | 4.9833s |
| parent_subsidiaries | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0248 | 5.442s |
| program_top_supplier | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0313 | 6.286s |
| risk_not_in_feed | unanswerable | 4 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0173 | 3.4775s |
| sole_source_share | answerable | 5 | 100.0% | 100.0% | 100.0% | 3.0 | $0.0382 | 8.31s |
| subcontractor_count | answerable | 5 | 100.0% | 100.0% | 100.0% | 3.0 | $0.0366 | 8.21s |
| top_sole_source_country | answerable | 5 | 100.0% | 100.0% | 100.0% | 2.0 | $0.0316 | 6.372s |

## LLM judge (free-text quality only)

Correctness is never judged by an LLM; it is scored deterministically above. Judge model `claude-opus-5-5`, validated on 40 cases with known labels (20 template-rendered correct answers and 20 deliberate corruptions: wrong value, wrong entity, false refusal, invented answer). **Agreement 100.0%** (known_good: 100.0%, false_refusal: 100.0%, wrong_value: 100.0%, wrong_entity: 100.0%, invented_answer: 100.0%). The use threshold is 85.0%, so the judge is used.

On the agent's 101 answers, the judge's free-text **pass rate is 99.0%**. Judge FAIL verdicts:
  * hard_noisy_name-3b531f36: The reference treats the question as answerable: the misspelled name is noise for ORACLE ELEVATOR HOLDCO, INC., and the count is 2 offices. The candidate found the correct entity but refused to give the count and did not provide the answer of 2.

Judge validation limits: the corruptions are deliberate and fairly blatant, and the "good" answers are
template-rendered, not real agent prose. 100% agreement here shows the judge catches gross contradictions and
false or invented refusals. It does not show it catches subtle errors (a slightly wrong share, a
right-number-wrong-unit). Treat its pass rate as a smoke test of prose quality, not a correctness metric.

## Failure analysis

Failure categories: {'false_refusal': 1}.

* **hard_noisy_name-3b531f36** (false_refusal). Q: How many awarding offices depend on ORACME ELEVATOR HOLDCO, LIMITED, directly or through subcontracts? Give the count.
  Expected `2`, got `null` (answerable=False). Tools: resolve_supplier.

## Analyst notes on this run (written after reading the logs; the numbers above are generated)

1. **The base tier saturates.** All 83 base-tier questions (one template = one named query, unique supplier names)
   were answered correctly, with 100% citation precision and zero fabricated IDs. That shows the plumbing works
   (resolution, query choice, parametrization, reading results, citing, refusing), but a 100% score cannot
   tell versions apart. It is a regression floor, not a quality claim. The 18-question hard tier exists for
   that reason.
2. **The one wrong answer was a cautious refusal, and arguably the better behavior.** For `hard_noisy_name-3b531f36`
   the question named "ORACME ELEVATOR HOLDCO, LIMITED". The resolver's top candidate was the right entity,
   "ORACLE ELEVATOR HOLDCO, INC." (score above the 0.6 floor). The agent declined to assume, because both the
   name and the legal form differed, and offered to proceed if the analyst meant Oracle. The metric counts this
   as a false refusal. For a sustainment analyst, asking first may be preferable to silently merging two legal
   entities. This is a precision/recall policy choice, worth raising with the customer rather than tuning away.
3. **Judge-caught prose issue with a correct JSON answer** (`parent_subsidiaries-8f402205`). The JSON answer
   (4 UEIs) was right, but the prose argued the "real" subsidiary count was 3 because one UEI is the parent's own.
   The base-tier question wording ("subsidiary supplier UEIs") is ambiguous about self-parented UEIs. The hard-tier
   wording fixes it explicitly. This is a golden-set wording issue, not an agent error.
4. **Tool-call correctness needed equivalence classes.** Without them, three correct hard-tier answers scored as wrong
   trajectories: the agent used `dependent_offices` instead of `blast_radius` (same office count), and
   `office_concentration` to compute shares directly instead of reading the flag from `supplier_profile`.
   Accepted alternatives are now declared per template (`alt_queries`), and an alternative must still use the same
   key parameter.
5. **Cost and latency.** The mean is about $0.03 and 7 s per question. Hard-tier multi-hop questions cost about 2x
   the base tier (3-4 steps). Prompt caching of the system prompt and tools across questions keeps input cost low,
   and output and thinking tokens dominate the cost.

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
