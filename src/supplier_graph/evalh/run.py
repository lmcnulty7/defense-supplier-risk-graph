"""Run the evaluation: golden set -> live agent runs -> deterministic metrics -> (validated) judge -> report.

  python -m supplier_graph.evalh.run              full live run (costs API money; budget-guarded)
  python -m supplier_graph.evalh.run --limit 6    pilot on a stratified sample
  python -m supplier_graph.evalh.run --offline    re-score the latest logged eval run, no API calls
  python -m supplier_graph.evalh.run --rerun-qids F.json   re-ask only these questions (e.g. after a data refresh
                                                  changed their expected answers); appended to the latest log,
                                                  where the last attempt per question wins
Outputs: logs/agent_runs/eval_<tag>.jsonl, reports/eval_results.json, reports/eval_report.md
"""

from __future__ import annotations

import argparse
import json
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from supplier_graph.agent import agent as A
from supplier_graph.agent import costs
from supplier_graph.config import LOGS, REPORTS
from supplier_graph.evalh import judge as J
from supplier_graph.evalh.golden import Builder, GoldenItem, load_golden
from supplier_graph.evalh.metrics import aggregate, score_run

RESULTS = REPORTS / "eval_results.json"
_lock = threading.Lock()


def _stratified(items: list[GoldenItem], n: int) -> list[GoldenItem]:
    by: dict[str, list[GoldenItem]] = defaultdict(list)
    for i in items:
        by[i.template].append(i)
    out, k = [], 0
    while len(out) < n and any(by.values()):
        for t in sorted(by):
            if by[t] and len(out) < n:
                out.append(by[t].pop(0))
        k += 1
    return out


def run_live(items: list[GoldenItem], tag: str, workers: int = 3) -> list[dict]:
    log_name = f"{'pilot' if 'pilot' in tag else 'eval'}_{tag}"

    def one(it: GoldenItem) -> dict:
        r = A.ask(it.question, run_id=f"{tag}-{it.qid}", log_name=log_name, tags={"qid": it.qid, "eval_tag": tag})
        with _lock:
            print(f"  {it.qid:40s} steps={r.steps} ${r.cost_usd:.4f} {r.latency_s:5.1f}s "
                  f"ans={json.dumps((r.final_json or {}).get('answer'), default=str)[:40]} err={r.error}", flush=True)
        return json.loads(json.dumps(r.__dict__, default=lambda o: o.__dict__))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(one, items))


def latest_eval_log() -> tuple[str, list[dict]]:
    logs = sorted((LOGS / "agent_runs").glob("eval_*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not logs:
        raise SystemExit("no eval logs found; run `make eval` first")
    p = logs[-1]
    latest: dict[str, dict] = {}
    for line in p.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            latest.pop(r["tags"]["qid"], None)
            latest[r["tags"]["qid"]] = r  # last attempt per question wins
    return p.stem.removeprefix("eval_"), list(latest.values())


def judge_runs(runs: list[dict], items: dict[str, GoldenItem], tag: str, prior: dict | None = None) -> dict:
    val = json.loads(J.VALIDATION_PATH.read_text()) if J.VALIDATION_PATH.exists() else None
    if not val:
        return {"used": False, "reason": "judge not validated (run `make judge-validate`)"}
    if not val["usable"]:
        return {"used": False, "reason": f"judge agreement {val['agreement']} < {J.JUDGE_MIN_AGREEMENT}; not used",
                "validation_agreement": val["agreement"]}
    out = []
    old = {v["qid"]: v for v in (prior or {}).get("verdicts", [])} if prior and prior.get("used") else {}
    for r in runs:
        it = items[r["tags"]["qid"]]
        if not r.get("final_text"):
            continue
        if it.qid in old:
            out.append(old[it.qid])
            continue
        prose = A.JSON_BLOCK.sub("", r["final_text"]).strip()
        v = J.judge(it.question, it.reference_facts, it.answerable, prose, run_id=f"judge_{tag}")
        out.append({"qid": it.qid, "template": it.template, "verdict": v["verdict"], "reason": v["reason"]})
    pass_rate = round(sum(o["verdict"] == "PASS" for o in out) / len(out), 4) if out else None
    return {"used": True, "validation_agreement": val["agreement"], "n": len(out), "pass_rate": pass_rate, "verdicts": out}


def _drop(judged: dict | None, qids: set[str]) -> dict | None:
    """Prior judge verdicts minus re-asked questions (their prose changed, so they are judged again)."""
    if not judged or not judged.get("used"):
        return judged
    return {**judged, "verdicts": [v for v in judged.get("verdicts", []) if v["qid"] not in qids]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--reuse", action="store_true", help="reuse runs from the latest eval log; only run new questions")
    ap.add_argument("--rerun-qids", help="JSON list of qids to re-ask; everything else is reused from the latest log")
    a = ap.parse_args()
    rerun: set[str] = set(json.loads(Path(a.rerun_qids).read_text())) if a.rerun_qids else set()

    if a.offline:
        items = load_golden()
        tag, runs = latest_eval_log()
    else:
        items = Builder().build()
        sel = _stratified(items, a.limit) if a.limit else items
        prior: list[dict] = []
        if a.reuse or rerun:
            tag, prior = latest_eval_log()
            done = {r["tags"]["qid"] for r in prior} - rerun
            sel = [i for i in sel if i.qid not in done]
            prior = [r for r in prior if r["tags"]["qid"] not in rerun]
        else:
            tag = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + (f"_pilot{a.limit}" if a.limit else "")
        print(f"[eval] {len(sel)} new questions, tag {tag}, reused {len(prior)}, spent so far ${costs.total_spent():.4f}")
        runs = prior + run_live(sel, tag, a.workers)
    by_id = {i.qid: i for i in items}
    runs = [r for r in runs if r.get("tags", {}).get("qid") in by_id]
    scores = [score_run(r, by_id[r["tags"]["qid"]]) for r in runs]
    overall = aggregate(scores)
    by_template = {t: aggregate([s for s in scores if s["template"] == t]) for t in sorted({s["template"] for s in scores})}
    prev = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    judged = prev.get("judge") if a.offline and prev.get("tag") == tag else None
    if judged is None:
        judged = {"used": False, "reason": "skipped (--no-judge or offline)"} if (a.no_judge or a.offline) \
            else judge_runs(runs, by_id, tag, _drop(prev.get("judge"), rerun) if prev.get("tag") == tag else None)
    res = {"tag": tag, "scored_at_utc": datetime.now(UTC).isoformat(), "model": A.MODEL, "effort": A.EFFORT,
           "golden_size": len(items), "overall": overall, "by_template": by_template, "judge": judged,
           "scores": scores, "anthropic_spend_total_usd": costs.total_spent()}
    if a.limit and not a.offline:
        (REPORTS / f"eval_results_{tag}.json").write_text(json.dumps(res, indent=2, default=str))
    else:
        RESULTS.write_text(json.dumps(res, indent=2, default=str))
        from supplier_graph.evalh.report import write_report
        write_report(res, runs, by_id)
    print(json.dumps(overall, indent=2))


if __name__ == "__main__":
    main()
