# Every reported number is reproducible from these targets. Python: the repo-local venv.
PY ?= .venv/bin/python
PIP ?= .venv/bin/pip
# Import the package from src/ directly. macOS (iCloud/Finder) can set the hidden flag on the venv's editable-install
# .pth file, which Python 3.12 then skips; PYTHONPATH makes every target independent of that file.
export PYTHONPATH := $(CURDIR)/src

.PHONY: offline headline feed-snapshot install pull pull-tx sam stage er data graph queries test test-all lint eval eval-offline judge-validate app neo4j-up neo4j-load clean-derived backtest challenge compare compare-offline guard-eval judge-validate-subtle site

BOOTSTRAP_PY ?= python3

install:            ## create venv and install package + dev deps (BOOTSTRAP_PY=python3.12 to choose the interpreter)
	test -d .venv || $(BOOTSTRAP_PY) -m venv .venv
	$(PIP) install -q -e ".[dev]"

offline:            ## rebuild everything from the committed cache (no network, no API key): DuckDB, graph, backtest, site
	$(MAKE) data graph backtest site

pull:               ## USAspending bulk download (cached by request hash; add ARGS=--refresh to re-pull)
	$(PY) -m supplier_graph.ingest.usaspending $(ARGS)

sam:                ## SAM.gov entity enrichment, budgeted (SAM_DAILY_BUDGET, default 8 calls)
	$(PY) -m supplier_graph.ingest.sam

feed-snapshot:      ## copy the validated MENA feed (MENA_RISK_FEED_PATH) into data/feeds/mena/ (then: make stage er graph backtest)
	$(PY) -m supplier_graph.ingest.risk_feed

stage:              ## cached pulls + risk feed -> DuckDB
	$(PY) -m supplier_graph.stage

er:                 ## ER evaluation (reports/entity_resolution.md) then resolution
	$(PY) -m supplier_graph.er.evaluate
	$(PY) -m supplier_graph.er.resolve
	$(PY) -m supplier_graph.er.holdout

data: pull stage er ## full data build

pull-tx:            ## USAspending contract transactions FY2022-FY2025 for the risk backtest (cached)
	$(PY) -m supplier_graph.ingest.transactions $(ARGS)

backtest: pull-tx   ## time-split backtest of disruption likelihood + FY2026 outlook (reports/risk_backtest.md)
	$(PY) -m supplier_graph.risk.backtest

graph:              ## build the knowledge graph + risk features (NetworkX; Neo4j via neo4j-load)
	$(PY) -m supplier_graph.graph.build

test:               ## unit + data tests (no API spend)
	$(PY) -m pytest -q

lint:
	.venv/bin/ruff check src tests app

eval:               ## golden set + live agent run + metrics (costs API money; capped)
	$(PY) -m supplier_graph.evalh.run

eval-offline:       ## rebuild golden set and re-score the last logged agent run (no API spend)
	$(PY) -m supplier_graph.evalh.run --offline

judge-validate:     ## validate the LLM judge on known-good / corrupted answers
	$(PY) -m supplier_graph.evalh.judge --validate

challenge:          ## build the v2 challenge set (reports/challenge_set.jsonl); no API spend
	$(PY) -m supplier_graph.evalh.challenge

compare:            ## live comparison run, e.g. make compare ARGS="--set challenge --config haiku_tools --repeat 3"
	$(PY) -m supplier_graph.evalh.compare $(ARGS)

compare-offline:    ## re-score every logged comparison run (reports/comparison.md); no API spend
	$(PY) -m supplier_graph.evalh.compare --offline

guard-eval:         ## offline check of the resolver grounding guard (reports/guard_eval.json)
	$(PY) -m supplier_graph.evalh.guard_eval

judge-validate-subtle: ## judge agreement on real agent prose with subtle corruptions (costs ~$0.18)
	$(PY) -m supplier_graph.evalh.judge --validate-subtle

headline:           ## every headline number in one file (reports/HEADLINE.md) from the result JSONs
	$(PY) -m supplier_graph.headline

site:               ## static results site for interviews (site/index.html), from the committed reports
	$(PY) -m supplier_graph.site

app:
	.venv/bin/streamlit run app/streamlit_app.py

neo4j-up:
	docker compose up -d neo4j

neo4j-load:         ## load the same graph into Neo4j (needs neo4j-up)
	$(PY) -m supplier_graph.graph.neo4j_backend --load

clean-derived:      ## remove derived DuckDB/graph (cache is kept)
	rm -f data/supplier_graph.duckdb data/supplier_graph.duckdb.wal
	rm -rf data/graph
