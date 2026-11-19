"""Agent comparison: the same questions under different configurations, scored by the same deterministic rules.

Configurations
  opus_tools     claude-opus-5-5, four read-only tools (the shipped agent)
  haiku_tools    claude-haiku-5-5, same tools and prompt (about 1/40 of Opus's per-token price)
  opus_no_tools  claude-opus-5-5, no tools: what a plain chat model gives the analyst
  *_guard        the same with the resolver's grounding guard on (flags query words the matched name lacks)
Question sets: `golden` (v1, 101 questions) and `challenge` (v2, harder, reports/challenge_set.jsonl).

  python -m supplier_graph.evalh.compare --set challenge --config haiku_tools [--repeat 2] [--limit 6]
  python -m supplier_graph.evalh.compare --offline        re-score every logged comparison run, no API calls

Logs go to logs/agent_runs/cmp_<set>_<config>_r<k>.jsonl (committed). A run resumes: questions already in its log
are not asked again. The opus_tools/golden cell reuses the main eval log (make eval). Results:
reports/comparison.json and reports/comparison.md (`make compare-offline`).
"""

from __future__ import annotations

import argparse
import json
import math
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from supplier_graph.agent import agent as A
from supplier_graph.agent import costs
from supplier_graph.config import LOGS, REPORTS
from supplier_graph.evalh.challenge import load_challenge
from supplier_graph.evalh.golden import GoldenItem, load_golden
from supplier_graph.evalh.metrics import aggregate, score_run

CONFIGS = {
    "opus_tools": {"model": "claude-opus-5-5", "use_tools": True, "guard": False},
    "haiku_tools": {"model": "claude-haiku-5-5", "use_tools": True, "guard": False},
    "opus_no_tools": {"model": "claude-opus-5-5", "use_tools": False, "guard": False},
    # resolver grounding guard on (agent/tools.missing_words); added after the challenge set exposed look-alike
    # substitution. Prompt and model unchanged, so any difference is the tool change.
    "haiku_tools_guard": {"model": "claude-haiku-5-5", "use_tools": True, "guard": True},
    "opus_tools_guard": {"model": "claude-opus-5-5", "use_tools": True, "guard": True},
}
SETS = {"golden": load_golden, "challenge": load_challenge}
RUN_DIR = LOGS / "agent_runs"
_lock = threading.Lock()


def log_path(qset: str, config: str, rep: int):
    return RUN_DIR / f"cmp_{qset}_{config}_r{rep}.jsonl"


def _read(p) -> list[dict]:
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []


def run_live(qset: str, config: str, rep: int, limit: int | None, workers: int = 3,
             rerun: set[str] | None = None, only: str | None = None) -> None:
    items = SETS[qset]()
    if only:
        items = [i for i in items if i.template == only]
    if limit:
        items = items[:limit]
    p = log_path(qset, config, rep)
    done = {r["tags"]["qid"] for r in _read(p)} - (rerun or set())
    todo = [i for i in items if i.qid not in done]
    cfg = dict(CONFIGS[config])
    from supplier_graph.agent import tools as T
    T.GUARD = cfg.pop("guard")
    print(f"[compare] {qset}/{config}/r{rep}: {len(todo)} to run ({len(done)} logged), spent ${costs.total_spent():.4f}")

    def one(it: GoldenItem) -> None:
        r = A.ask(it.question, run_id=f"cmp-{qset}-{config}-r{rep}-{it.qid}", log_name=p.stem,
                  tags={"qid": it.qid, "set": qset, "config": config, "rep": rep}, **cfg)
        with _lock:
            print(f"  {it.qid:34s} steps={r.steps} ${r.cost_usd:.4f} {r.latency_s:5.1f}s "
                  f"ans={json.dumps((r.final_json or {}).get('answer'), default=str)[:30]} err={r.error}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, todo))


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def _cells() -> dict[tuple[str, str], list[list[dict]]]:
    cells: dict[tuple[str, str], list[list[dict]]] = defaultdict(list)
    for p in sorted(RUN_DIR.glob("cmp_*_r*.jsonl")):
        qset, rest = p.stem.removeprefix("cmp_").split("_", 1)
        config = rest.rsplit("_r", 1)[0]
        cells[(qset, config)].append(_read(p))
    evals = sorted(RUN_DIR.glob("eval_*.jsonl"), key=lambda q: q.stat().st_mtime)
    if evals:
        cells[("golden", "opus_tools")].insert(0, _read(evals[-1]))
    return cells


def score_cell(runs_by_rep: list[list[dict]], items: list[GoldenItem]) -> dict:
    by_id = {i.qid: i for i in items}
    reps, partial = [], []
    for runs in runs_by_rep:
        latest = {}
        for r in runs:
            q = r.get("tags", {}).get("qid")
            if q in by_id:
                latest[q] = r  # last attempt per question wins
        scores = [score_run(latest[q], by_id[q]) for q in sorted(latest)]
        if len(scores) < len(items):
            # partial repetition (e.g. one template re-run for variance under the budget): report only templates
            # whose questions are all present
            full = {t for t in {s["template"] for s in scores}
                    if sum(s["template"] == t for s in scores) == sum(i.template == t for i in items)}
            if full:
                partial.append({t: {"correct": sum(s["correct"] for s in scores if s["template"] == t),
                                    "n": sum(s["template"] == t for s in scores)} for t in sorted(full)})
            continue
        agg = aggregate(scores)
        ans = [s for s in scores if s["answerable"]]
        un = [s for s in scores if not s["answerable"]]
        k_a, k_u = sum(s["correct"] for s in ans), sum(s["correct"] for s in un)
        agg["answerable_correct"] = f"{k_a}/{len(ans)}"
        agg["answer_correctness_ci95"] = wilson(k_a, len(ans))
        agg["refusals_correct"] = f"{k_u}/{len(un)}"
        agg["correct_refusal_ci95"] = wilson(k_u, len(un))
        agg["by_template"] = {t: {"correct": sum(s["correct"] for s in scores if s["template"] == t),
                                  "n": sum(s["template"] == t for s in scores)}
                              for t in sorted({s["template"] for s in scores})}
        agg["failures"] = [{"qid": s["qid"], "category": s["category"], "expected": s["expected"], "got": s["got"]}
                           for s in scores if not s["correct"]]
        reps.append(agg)
    if not reps:
        return {"complete_repetitions": 0, "partial_reps_by_template": partial}
    out = {"complete_repetitions": len(reps), "reps": reps, "partial_reps_by_template": partial}
    if len(reps) > 1:
        accs = [r["answer_correctness"] for r in reps]
        out["answer_correctness_range"] = [min(accs), max(accs)]
    return out


def offline() -> dict:
    res = {"scored_at_utc": datetime.now(UTC).isoformat(), "configs": CONFIGS, "cells": {},
           "anthropic_spend_total_usd": costs.total_spent()}
    for (qset, config), runs in sorted(_cells().items()):
        res["cells"][f"{qset}/{config}"] = score_cell(runs, SETS[qset]())
    (REPORTS / "comparison.json").write_text(json.dumps(res, indent=2, default=str))
    from supplier_graph.evalh.compare_report import write_report
    write_report(res)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", choices=list(SETS))
    ap.add_argument("--config", choices=list(CONFIGS))
    ap.add_argument("--repeat", type=int, default=1, help="number of independent repetitions (r1..rN)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--rerun-qids", help="JSON list of qids to re-ask in each repetition (last attempt wins)")
    ap.add_argument("--reps", help="comma-separated repetition numbers to run (default 1..--repeat)")
    ap.add_argument("--template", help="only questions of this template (partial repetition)")
    a = ap.parse_args()
    rerun = set(json.loads(Path(a.rerun_qids).read_text())) if a.rerun_qids else None
    if not a.offline:
        reps = [int(x) for x in a.reps.split(",")] if a.reps else range(1, a.repeat + 1)
        for rep in reps:
            run_live(a.set, a.config, rep, a.limit, a.workers, rerun=rerun, only=a.template)
    res = offline()
    for k, v in res["cells"].items():
        r = (v.get("reps") or [{}])[0]
        print(f"{k:28s} reps={v['complete_repetitions']} correct={r.get('answerable_correct')} "
              f"refusals={r.get('refusals_correct')} cost_mean=${r.get('cost_mean_usd')}")


if __name__ == "__main__":
    main()
