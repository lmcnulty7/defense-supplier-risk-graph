# Requirements and acceptance criteria

Each requirement is testable. "Verified by" names the test or make target that checks it. Status is as of the
final run (2026-11-18, second session).

| ID | Requirement | Acceptance criterion | Verified by | Status |
|---|---|---|---|---|
| D1 | Pull DoD PSC-J prime awards and subawards, FY2023-FY2025, from USAspending | One cached bulk job; cache stores request body, pull time, row counts, SHA-256 | `make pull`; `tests/test_stage_data.py::test_provenance_recorded` | Met |
| D2 | Respect rate limits; cache every pull | Re-running `make pull` makes no network call (cache hit); SAM calls are budgeted per day | `ingest/usaspending.py` cache check; `SAM_DAILY_BUDGET` | Met |
| D3 | Keys only in `.env`, never in git | `.env` gitignored; no key strings in the repo | `.gitignore`; `git grep` | Met |
| D4 | MENA risk feed: path from `MENA_RISK_FEED_PATH`, pinned as a committed snapshot; accept schema majors 1 and 2; synthetic fixture fallback, labeled | Major-3 feed rejected; 1.1+/2.x without `skill_status` rejected; sha256 checked; fixture flagged `is_fixture` and shown in UI and README | `tests/test_risk_feed.py` | Met |
| D4b | Honour the feed's `skill_status` | Each value tested; feed inputs excluded from the likelihood model unless skill is demonstrated; status shown in app, site and agent answers | `tests/test_risk_feed.py` (`test_each_skill_status`, `test_caveat_*`), `reports/risk_backtest.json` `feed_policy` | Met |
| D5 | Stage in DuckDB with consistent codes | Country codes are all ISO alpha-3; award IDs unique | `tests/test_stage_data.py` | Met |
| E1 | Resolve suppliers: exact UEI/CAGE first, then blocked fuzzy matching; hierarchy from parent UEI | 100% of award and subaward rows map to a supplier | `tests/test_stage_data.py::test_every_award_and_subaward_resolved` | Met |
| E2 | Automatic ER labels: identifier positives, hard negatives, synthetic pairs; identifiers hidden from matcher | Matcher input type has no identifier fields; labels carry `label_source` | `tests/test_normalize_matcher.py::test_matcher_has_no_identifier_fields`; `reports/entity_resolution.md` | Met |
| E3 | Report ER precision/recall separately for identifier-derived and synthetic sets, with limits | Both sets reported on a held-out split; threshold tuned on dev only | `make er`; `reports/er_metrics.json` | Met |
| G1 | Knowledge graph with 6 node labels and 6 edge types, provenance on every node and edge | Schema and provenance test passes | `tests/test_graph.py` | Met (NetworkX; Neo4j parity verified, `reports/neo4j_parity.json`, `tests/test_neo4j_parity.py`) |
| G2 | Risk features per supplier: office concentration, sole-source share, dependent offices, centrality, foreign/feed exposure joined to latest `p_escalation` | `supplier_risk` table populated; shares within [0, 1] | `tests/test_graph.py::test_supplier_risk_features_sane` | Met |
| G3 | Library of parameterized queries, each tested | 10 named queries; graph and SQL implementations agree exactly | `tests/test_queries.py` | Met |
| A1 | Claude tool-use agent; client constructed with no arguments; read-only; 5 tools (incl. `what_if_remove`) | `anthropic.Anthropic()`; `run_query` restricted to an enum of named queries | `tests/test_agent.py` | Met |
| A2 | Every factual claim cites award/entity IDs; agent says when data can't answer | Citation precision >= 95%, 0 fabricated IDs, correct-refusal rate >= 90% | `tests/test_eval_regression.py::test_last_live_run_meets_gates` | Met (100% / 0 / 100%) |
| A3 | Log every run: prompt, tool calls, results, tokens, latency, cost | One JSONL record per run; spend ledger per API call | `tests/test_agent.py::test_loop_executes_tools_and_logs` | Met |
| A4 | Total Anthropic spend < $20 (raised from $10 by the owner on 2026-11-19) | Budget guard refuses calls past `BUDGET_USD` (default $9) | `tests/test_agent.py::test_budget_guard_stops_before_call`; `logs/spend_ledger.jsonl` | Met (see FINAL_REPORT for total) |
| V1 | Golden set of 60-100+ template questions with >= 10 unanswerable; answers by deterministic query; verified by a second path; disagreements dropped and logged | 101 questions (13 unanswerable); all re-verify SQL vs graph | `tests/test_eval_regression.py::test_golden_answers_reverify_on_both_paths` | Met (101 slightly exceeds 100 because of the hard tier) |
| V2 | Metrics: correctness, citation precision, tool-call correctness, refusal rate, steps, cost, latency | All present in `reports/eval_results.json` | `make eval-offline` | Met |
| V3 | LLM judge only for free-text quality, validated on known answers; not used if agreement is poor | Agreement reported; judge used only if >= 85% | `reports/judge_validation.json` | Met (100% on 40 cases; limits stated) |
| V4 | Runs as a pytest regression suite with a report by question type and failure analysis | `pytest` gates; `reports/eval_report.md` | `make test` | Met |
| P1 | Docs, Streamlit demo, README with Mermaid diagram, demo script | Files present; app renders headless without exceptions | `tests/test_app.py` | Met |
| R1 | Every reported number reproducible from a make target | Two consecutive `make stage er graph` runs give identical table hashes | clean-clone check (FINAL_REPORT, *Public release*) | Met |
| E3b | ER operating point chosen by a stated rule on held-out records and confirmed on a separate half | Selected point cuts absent-company false links and wrong links on the confirmation half, losing at most 10 points of correct links | `tests/test_er_holdout.py::test_operating_point_chosen_on_tuning_half_and_confirmed` | Met |
| E3 | Resolution works end to end when identifiers are missing | With the UEI withheld from 620 renamed records, the shipped resolver links more correctly than v1 without more wrong links | `tests/test_er_holdout.py`; `reports/er_holdout.json` | Met (82.7% vs 55.8% correct; 3.55% vs 5.32% wrong) |
| G2 | The Neo4j backend matches the NetworkX graph | Node and edge counts equal per label and type; Cypher blast radius equals the graph query on a sample | `tests/test_neo4j_parity.py`; `reports/neo4j_parity.json` | Met (30/30) |
| K1 | Test whether the risk screens anticipate disruption, with time-forward splits and baselines | Train years precede the test year; claimed gains have CIs that exclude zero | `tests/test_risk_backtest.py`; `reports/risk_backtest.md` | Met (finding: flags are impact screens) |
| V5 | Harder evaluation with baselines | Challenge set rebuilds deterministically; the shipped agent is at least 90% correct and at least 80% on refusals; the guard improves near-miss handling without losing answerable questions; no-tools baseline fabricates nothing | `tests/test_challenge_compare.py`; `reports/comparison.md` | Met |
| V6 | Model variance measured where the budget allows | Haiku runs every set 3 times; Opus runs each set once on the current prompt (stated in the reports) | `reports/comparison.json` | Met for Haiku; not measured for Opus |
| P2 | Presentable results for an interview | `make site` builds a self-contained page from the committed reports | `site/index.html` | Met |
| P3 | Every headline number lives in one generated file | `make headline` writes `reports/HEADLINE.md` from the result JSONs; README, FINAL_REPORT, the site, brief, demo script and walkthrough quote it, and stale numbers fail | `tests/test_headline.py` | Met |
