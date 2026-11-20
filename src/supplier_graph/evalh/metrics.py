"""Deterministic scoring of agent runs against the golden set."""

from __future__ import annotations

import json
import re
import statistics
from functools import lru_cache

from supplier_graph.evalh.golden import GoldenItem
from supplier_graph.graph.build import load_graph
from supplier_graph.graph.schema import nid
from supplier_graph.stage import connect

CITE_RE = re.compile(r"^(award|supplier|parent|office|program|country|psc):(.+)$")


def _num(x) -> float | None:
    if isinstance(x, bool):
        return None
    if isinstance(x, int | float):
        return float(x)
    if isinstance(x, str):
        t = x.strip().replace(",", "").replace("$", "")
        try:
            return float(t)
        except ValueError:
            return None
    if isinstance(x, list) and len(x) == 1:
        return _num(x[0])
    return None


def _id(x) -> str:
    s = str(x).strip().upper()
    return s.split(":", 1)[1] if ":" in s else s


def _bool(x) -> bool | None:
    if isinstance(x, bool):
        return x
    if isinstance(x, str) and x.strip().lower() in ("true", "yes", "false", "no"):
        return x.strip().lower() in ("true", "yes")
    return None


def answer_correct(item: GoldenItem, fj: dict | None) -> tuple[bool, str]:
    """Returns (correct, failure_category or 'ok')."""
    if fj is None:
        return False, "no_final_json"
    said_answerable = fj.get("answerable")
    if not item.answerable:
        return (said_answerable is False), ("ok" if said_answerable is False else "answered_unanswerable")
    if said_answerable is False:
        return False, "false_refusal"
    a, e = fj.get("answer"), item.expected
    t = item.answer_type
    if t == "int":
        v = _num(a)
        ok = v is not None and abs(v - float(e)) < 1e-9
    elif t == "float":
        v = _num(a)
        tol = item.tolerance * abs(float(e)) if item.rel_tol else item.tolerance
        ok = v is not None and abs(v - float(e)) <= tol + 1e-12
    elif t == "id":
        ok = (_id(a[0]) if isinstance(a, list) and len(a) == 1 else _id(a)) == _id(e)
    elif t == "ids":
        got = {_id(x) for x in (a if isinstance(a, list) else [a] if a is not None else [])}
        ok = got == {_id(x) for x in e}
    elif t == "bool":
        ok = _bool(a) is not None and _bool(a) == bool(e)
    else:
        ok = False
    return ok, ("ok" if ok else "wrong_value")


@lru_cache(maxsize=1)
def _programs() -> frozenset[str]:
    con = connect(read_only=True)
    out = frozenset(p for (p,) in con.execute("SELECT DISTINCT program_code FROM awards WHERE program_code IS NOT NULL").fetchall())
    con.close()
    return out


def citation_exists(c: str) -> bool:
    m = CITE_RE.match(c.strip())
    if not m:
        return False
    kind, key = m.group(1), m.group(2).strip()
    if kind == "program":
        return key in _programs()
    label = {"award": "Award", "supplier": "Supplier", "parent": "ParentCompany", "office": "Office",
             "country": "Country", "psc": "ProductServiceCode"}[kind]
    return nid(label, key.upper() if kind == "country" else key) in load_graph()


def citation_metrics(run: dict, item: GoldenItem) -> dict:
    fj = run.get("final_json") or {}
    cites = [str(c) for c in (fj.get("citations") or [])]
    blob = json.dumps([t["output"] for t in run.get("tool_calls", [])] + [t["input"] for t in run.get("tool_calls", [])],
                      default=str)
    exists = [c for c in cites if citation_exists(c)]
    grounded = [c for c in exists if CITE_RE.match(c).group(2).strip() in blob]
    norm = {c.strip().lower() for c in cites}
    req = [r for r in item.required_citations]
    return {"n_citations": len(cites), "n_exist": len(exists), "n_grounded": len(grounded),
            "precision": (len(grounded) / len(cites)) if cites else None,
            "required_hit": all(r.lower() in norm for r in req) if req else None,
            "fabricated": [c for c in cites if c not in exists]}


def tool_call_correct(run: dict, item: GoldenItem) -> bool | None:
    if item.expected_query is None:
        return None
    calls = run.get("tool_calls", [])
    exp = {k: str(sorted(v) if isinstance(v, list) else v).upper() for k, v in item.expected_params.items()}
    for t in calls:
        if item.expected_query in ("country_risk", "award_details", "resolve_supplier", "what_if_remove"):
            if t["name"] != item.expected_query:
                continue
            got = {k: str(sorted(v) if isinstance(v, list) else v).upper() for k, v in t["input"].items()}
            if item.expected_query == "resolve_supplier" or all(got.get(k) == v for k, v in exp.items()):
                return True
        elif t["name"] == "run_query" and t["input"].get("query_name") in [item.expected_query, *item.alt_queries]:
            got = {k: str(v).upper() for k, v in (t["input"].get("params") or {}).items()}
            if t["input"]["query_name"] == item.expected_query:
                if all(got.get(k) == v for k, v in exp.items()):
                    return True
                continue
            # an equivalent query must use the same key parameter if it takes one; if it takes a different key
            # (office_concentration for a supplier question), the expected entity must appear in its output
            out = json.dumps(t.get("output"), default=str).upper()
            if all((got.get(k) == v) if k in got else (v in out) for k, v in exp.items()):
                return True
    return False


def score_run(run: dict, item: GoldenItem) -> dict:
    ok, cat = answer_correct(item, run.get("final_json"))
    cm = citation_metrics(run, item)
    if ok and item.answerable and cm["required_hit"] is False:
        if getattr(item, "strict_required_citations", False):
            ok, cat = False, "missing_required_citation"
        else:
            cat = "ok_missing_required_citation"
    return {"qid": item.qid, "template": item.template, "answerable": item.answerable, "correct": ok,
            "category": cat if not run.get("error") else f"error:{run['error'][:60]}",
            "tool_call_correct": tool_call_correct(run, item), "citations": cm, "steps": run.get("steps"),
            "cost_usd": run.get("cost_usd"), "latency_s": run.get("latency_s"),
            "expected": item.expected, "got": (run.get("final_json") or {}).get("answer"),
            "said_answerable": (run.get("final_json") or {}).get("answerable")}


def _mean(xs):
    xs = [float(x) for x in xs if x is not None]
    return round(statistics.mean(xs), 4) if xs else None


def aggregate(scores: list[dict]) -> dict:
    ans = [s for s in scores if s["answerable"]]
    un = [s for s in scores if not s["answerable"]]
    allc = sum(s["citations"]["n_citations"] for s in scores)
    return {
        "n": len(scores), "n_answerable": len(ans), "n_unanswerable": len(un),
        "answer_correctness": _mean([s["correct"] for s in ans]),
        "correct_refusal_rate": _mean([s["correct"] for s in un]),
        "false_refusal_rate": _mean([s["said_answerable"] is False for s in ans]),
        "overall_accuracy": _mean([s["correct"] for s in scores]),
        "citation_precision_micro": round(sum(s["citations"]["n_grounded"] for s in scores) / allc, 4) if allc else None,
        "citation_precision_macro": _mean([s["citations"]["precision"] for s in scores]),
        "citation_exists_rate": round(sum(s["citations"]["n_exist"] for s in scores) / allc, 4) if allc else None,
        "required_citation_hit_rate": _mean([s["citations"]["required_hit"] for s in ans]),
        "n_fabricated_citations": sum(len(s["citations"]["fabricated"]) for s in scores),
        "tool_call_correctness": _mean([s["tool_call_correct"] for s in scores]),
        "steps_mean": _mean([s["steps"] for s in scores]),
        "steps_max": max((s["steps"] or 0) for s in scores) if scores else None,
        "cost_total_usd": round(sum(s["cost_usd"] or 0 for s in scores), 4),
        "cost_mean_usd": _mean([s["cost_usd"] for s in scores]),
        "latency_mean_s": _mean([s["latency_s"] for s in scores]),
        "latency_p90_s": (sorted(s["latency_s"] for s in scores)[int(0.9 * (len(scores) - 1))] if scores else None),
        "n_errors": sum(s["category"].startswith("error") for s in scores),
    }
