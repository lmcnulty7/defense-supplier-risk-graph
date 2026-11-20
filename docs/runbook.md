# Runbook

## Prerequisites
* macOS/Linux with Python 3.11+ (developed on Anaconda Python 3.12; python.org 3.13 on this Mac lacked SSL certs).
* `.env` in the repo root (never committed):
  ```
  ANTHROPIC_API_KEY=...            # or Workload Identity Federation vars; the client takes no arguments
  SAM_API_KEY=...                  # optional; enables SAM enrichment
  MENA_RISK_FEED_PATH=../Escalating states markov chain/outputs/risk_feed/v1/latest   # only for `make feed-snapshot`
  # optional: BUDGET_USD=9.00  AGENT_MODEL=claude-opus-5-5  AGENT_EFFORT=low  SAM_DAILY_BUDGET=8  NEO4J_PASSWORD=...
  ```

## Fresh clone, offline (no network, no API key)
```bash
make install          # venv + package + dev deps (BOOTSTRAP_PY=python3.12 to choose the interpreter)
make offline          # = make data graph backtest site, all from data/cache and data/feeds (committed)
make test             # regression gates; live-API tests skip without RUN_LIVE=1
.venv/bin/python -m playwright install chromium  # optional: enables the graph explorer browser test (skips without it)
make app              # every tab works; only "Ask the agent" needs ANTHROPIC_API_KEY in .env
```
Checked on 2026-11-19 in a fresh clone with no `.env` and outbound HTTP blocked: every report rebuilt identically
apart from timestamps, all tests passed, and the app served.

## Build everything step by step (no LLM spend)
```bash
make install          # venv + package + dev deps
make data             # pull (cached) -> stage -> ER eval + resolution
make graph            # graph + risk features
make test             # unit + data + regression gates (live tests skipped)
make app              # Streamlit demo at http://localhost:8501
make backtest         # transactions (cached) + risk backtest + FY2026 outlook
make headline         # reports/HEADLINE.md: every headline number, from the result JSONs
make site             # site/index.html results page (open it in a browser; works offline)
```
`make pull` is a cache hit after the first run. Use `make pull ARGS=--refresh` to re-download (about 7 minutes; USAspending
builds the file asynchronously).

## Evaluation
```bash
make eval-offline     # re-score the committed eval log; no API calls
make judge-validate   # about $0.20: re-validate the LLM judge on 40 known-label cases
make eval             # full live run, about $3 for 101 questions, budget-guarded
make challenge        # rebuild the v2 challenge set (no spend)
make compare-offline  # re-score every logged comparison run (no spend)
make compare ARGS="--set challenge --config haiku_tools_guard --repeat 3"   # live; Haiku is ~$0.001/question
make guard-eval       # offline check of the resolver grounding guard
make judge-validate-subtle  # about $0.18
.venv/bin/python -m supplier_graph.evalh.run --reuse   # run only questions missing from the latest eval log
.venv/bin/python -m supplier_graph.evalh.run --rerun-qids reports/feed_refresh_rerun_qids.json   # re-ask listed questions
make compare ARGS="--set challenge --config opus_tools --reps 2,3 --template u_near_miss"   # partial repetition
RUN_LIVE=1 make test  # adds the live pytest checks (about $0.40)
```
Spend is recorded per API call in `logs/spend_ledger.jsonl`. Calls stop before `BUDGET_USD` would be exceeded.
Check spend with `.venv/bin/python -c "from supplier_graph.agent.costs import total_spent; print(total_spent())"`.

## Routine refresh
1. `make pull ARGS=--refresh && make stage er graph`
2. `make eval-offline` (cheap check), then `make eval` if the data changed materially (the golden set is regenerated from data).
3. Commit `reports/` and the new `logs/agent_runs/eval_*.jsonl`.

## SAM enrichment (rate-limited)
`make sam` enriches up to `SAM_DAILY_BUDGET` x 10 subawardee UEIs per day, largest first, never re-fetching cached UEIs.
Then run `make stage er graph`.

## Swap to Neo4j
1. Start Docker Desktop. 2. `make neo4j-up` (Neo4j 5 Community, ports 7474/7687). 3. `make neo4j-load`; it prints
node-count parity with NetworkX. The agent still serves from NetworkX; switching the serving path means
implementing `graph/queries.py` functions in Cypher and adding them as a third path to the existing parity tests.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `SSL: CERTIFICATE_VERIFY_FAILED` | Use the venv (`.venv/bin/python`) built from Anaconda, or run `Install Certificates.command` for python.org builds. |
| `risk feed ... SchemaError` | The feed's `schema_version` major isn't 1 or 2, `skill_status` is missing or invalid, or the sha256 doesn't match its manifest. Fix the feed and re-run `make feed-snapshot`, or delete `data/feeds/mena/` and unset `MENA_RISK_FEED_PATH` to use the labeled fixture. |
| Refreshing the country-risk feed | `make feed-snapshot` (reads `MENA_RISK_FEED_PATH`), then `make stage er graph backtest`. Regenerate the question set with `python -m supplier_graph.evalh.golden`, list the questions whose expected answers changed, and re-ask only those with `--rerun-qids` (see `reports/feed_refresh_rerun_qids.json`). |
| Tests skipped with "staged data not built" | Run `make data graph`. |
| `budget: Anthropic spend ...` | The guard tripped. Raise `BUDGET_USD` only deliberately (hard cap $10 for this project). |
| SAM 429 | Daily limit reached; the cache keeps what was fetched. Retry tomorrow. |
| Different numbers after re-pull | Expected: USAspending revises records. Pulls are keyed by request hash and dated in `meta.json`. |

## macOS: "No module named supplier_graph"

macOS (iCloud Desktop & Documents sync) can set the hidden flag on `.venv`, and Python 3.12 skips hidden `.pth`
files. Make targets and pytest set `PYTHONPATH=src`, so they keep working. For ad-hoc commands, prefix
`PYTHONPATH=src`, or run `chflags -R nohidden .venv`.
