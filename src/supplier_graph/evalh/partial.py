"""Partial-answer regression: questions where one part is outside the data and another part is answerable.

The agent must decline the part the data cannot answer (a country the MENA risk feed does not cover), without citing
anything for it, and still answer the rest now: the suppliers and offices with work performed in that country.

Expected answers come from the `country_exposure` query, computed two independent ways (graph traversal and DuckDB
SQL); a question is dropped if they disagree. Scoring is deterministic:
  * answerable is false and `no_data` gives a reason;
  * the risk lookup and `country_exposure` were both run for the country;
  * `answer` lists at least 3 suppliers (a list, or the supplier lists in a dict), all with work in that country;
  * the prose cites at least one office from the country's top offices;
  * every citation exists in the graph and appeared in a tool result;
  * no sentence that declines (says the data can't answer) carries a citation: nothing is cited for the missing part.

  python -m supplier_graph.evalh.partial            live run (about $0.03 per question; budget-guarded)
  python -m supplier_graph.evalh.partial --offline  re-score the logged runs (no API calls)
Outputs: logs/agent_runs/partial_<tag>.jsonl, reports/partial_eval.json, reports/partial_eval.md
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from supplier_graph.config import LOGS, REPORTS
from supplier_graph.evalh.metrics import CITE_RE, citation_exists
from supplier_graph.graph import queries as Q
from supplier_graph.stage import connect

RESULTS_JSON, RESULTS_MD = REPORTS / "partial_eval.json", REPORTS / "partial_eval.md"
LOG_GLOB = "partial_*.jsonl"
INLINE_CITE = re.compile(r"\[(award|supplier|parent|office|program|country):([^\]]+)\]")
MIN_SUPPLIERS = 3
DECLINE = re.compile(r"\b(can't|cannot|can not|don't|doesn't|does not|isn't|is not|no (?:escalation|risk) "
                     r"(?:estimate|score|data)|not covered|not cover)\b", re.I)
SENTENCE = re.compile(r"(?<=[.!?:])\s+|\n+")


def decline_sentences(text: str) -> list[str]:
    body = text.split("```json")[0]
    return [x for x in SENTENCE.split(body) if DECLINE.search(x)]


def answer_supplier_ids(answer) -> list[str]:
    """Supplier IDs in `answer`: a list, or the lists under supplier keys of a dict."""
    if isinstance(answer, dict):
        vals = [v for k, v in answer.items() if "supplier" in str(k).lower() and isinstance(v, list)]
        answer = [x for v in vals for x in v]
    return [str(x).split(":", 1)[-1].strip().upper() for x in (answer if isinstance(answer, list) else [])]


@dataclass
class PartialItem:
    qid: str
    question: str
    country_iso3: str


ITEMS = [
    PartialItem("partial_risk_jpn", "What is the escalation risk for Japan?", "JPN"),
    PartialItem("partial_risk_bhr", "How risky is Bahrain right now, and who depends on work there?", "BHR"),
]


def expected(item: PartialItem) -> dict:
    """All suppliers and the top offices with work performed in the country; both query paths must agree."""
    params = {"country_iso3": item.country_iso3, "limit": 100_000}
    g = Q.run("country_exposure", params)
    con = connect(read_only=True)
    try:
        s = Q.run("country_exposure", params, backend="sql", con=con)
    finally:
        con.close()
    if g != s:
        raise RuntimeError(f"{item.qid}: graph and SQL disagree on country_exposure; question dropped")
    if g["summary"]["p_escalation"] is not None:
        raise RuntimeError(f"{item.qid}: {item.country_iso3} is in the risk feed, so the question is answerable")
    return {"suppliers": {r["supplier_id"] for r in g["rows"]},
            "top_offices": [o["office_code"] for o in g["summary"]["top_offices"]],
            "n_suppliers": g["summary"]["n_suppliers"]}


def score(run: dict, item: PartialItem, exp: dict) -> dict:
    fj = run.get("final_json") or {}
    text = run.get("final_text") or ""
    calls = run.get("tool_calls") or []
    blob = json.dumps([[t.get("input"), t.get("output")] for t in calls], default=str)
    iso = item.country_iso3
    cites = [str(c).strip() for c in (fj.get("citations") or [])]
    inline = [f"{k}:{v.strip()}" for k, v in INLINE_CITE.findall(text)]
    answer = fj.get("answer")
    ids = answer_supplier_ids(answer)
    declines = decline_sentences(text)
    checks = {
        "declined_part": fj.get("answerable") is False and bool(str(fj.get("no_data") or "").strip()),
        "looked_up_risk": any(t["name"] == "country_risk" and str(t["input"].get("country_iso3", "")).upper() == iso
                              for t in calls),
        "ran_exposure": any(t["name"] == "run_query" and t["input"].get("query_name") == "country_exposure"
                            and str((t["input"].get("params") or {}).get("country_iso3", "")).upper() == iso
                            for t in calls),
        "answered_suppliers": len(ids) >= MIN_SUPPLIERS and all(i in exp["suppliers"] for i in ids),
        "cited_office": any(c.upper() == f"OFFICE:{o}" for c in inline for o in exp["top_offices"]),
        "citations_grounded": bool(cites) and all(CITE_RE.match(c) and citation_exists(c)
                                                  and CITE_RE.match(c).group(2).strip() in blob for c in cites),
        "decline_uncited": bool(declines) and not any(INLINE_CITE.search(x) for x in declines),
    }
    return {"qid": item.qid, "question": item.question, "passed": all(checks.values()), "checks": checks,
            "answer": answer, "no_data": fj.get("no_data"), "cost_usd": run.get("cost_usd"),
            "latency_s": run.get("latency_s"), "run_id": run.get("run_id"), "error": run.get("error")}


def latest_runs() -> dict[str, dict]:
    """Last logged attempt per question across the partial_*.jsonl logs."""
    out: dict[str, dict] = {}
    for p in sorted((LOGS / "agent_runs").glob(LOG_GLOB)):
        for line in p.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                out[(r.get("tags") or {}).get("qid", "")] = r
    return out


def run_live(tag: str) -> None:
    from supplier_graph.agent import agent as A
    for it in ITEMS:
        expected(it)  # drop before spending if the two paths disagree
        r = A.ask(it.question, run_id=f"{tag}-{it.qid}", log_name=f"partial_{tag}", tags={"qid": it.qid})
        print(f"  {it.qid}: ${r.cost_usd:.4f} {r.latency_s}s err={r.error}")


def report() -> dict:
    runs = latest_runs()
    rows = [score(runs[it.qid], it, expected(it)) for it in ITEMS if it.qid in runs]
    res = {"scored_at_utc": datetime.now(UTC).isoformat(), "n": len(rows), "n_passed": sum(r["passed"] for r in rows),
           "n_items": len(ITEMS), "results": rows}
    RESULTS_JSON.write_text(json.dumps(res, indent=2, default=str))
    lines = ["# Partial-answer regression", "",
             f"Scored by `make partial-offline` from `logs/agent_runs/{LOG_GLOB}` (last attempt per question). "
             "Each question has one part outside the data (a country the MENA risk feed does not cover) and one "
             "answerable part (who works there). See `src/supplier_graph/evalh/partial.py` for the checks.", "",
             f"**{res['n_passed']}/{res['n_items']} passed.**", "",
             "| question | passed | failed checks | no_data reason |", "|---|---|---|---|"]
    for r in rows:
        bad = ", ".join(k for k, v in r["checks"].items() if not v) or "none"
        lines.append(f"| {r['question']} | {'yes' if r['passed'] else 'no'} | {bad} | {r['no_data'] or ''} |")
    RESULTS_MD.write_text("\n".join(lines) + "\n")
    print(f"partial-answer regression: {res['n_passed']}/{res['n_items']} passed -> {RESULTS_MD}")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    if not a.offline:
        run_live(datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    report()
