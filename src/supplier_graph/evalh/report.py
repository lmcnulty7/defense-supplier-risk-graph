"""Write reports/eval_report.md from eval results (all numbers come from reports/eval_results.json)."""

from __future__ import annotations

import json
from collections import Counter

from supplier_graph.config import REPORTS
from supplier_graph.evalh import judge as J
from supplier_graph.evalh.golden import DROPPED_PATH, GoldenItem


def _p(x) -> str:
    return "n/a" if x is None else f"{x:.1%}" if isinstance(x, float) and x <= 1 else str(x)


def write_report(res: dict, runs: list[dict], items: dict[str, GoldenItem]) -> None:
    o = res["overall"]
    rows = []
    for t, m in res["by_template"].items():
        acc = m["answer_correctness"] if m["n_answerable"] else m["correct_refusal_rate"]
        rows.append(f"| {t} | {'answerable' if m['n_answerable'] else 'unanswerable'} | {m['n']} | {_p(acc)} | "
                    f"{_p(m['tool_call_correctness'])} | {_p(m['citation_precision_micro'])} | {m['steps_mean']} | "
                    f"${m['cost_mean_usd']:.4f} | {m['latency_mean_s']}s |")
    fails = [s for s in res["scores"] if not s["correct"] or s["category"] != "ok"]
    cats = Counter(s["category"] for s in fails)
    run_by_q = {r["tags"]["qid"]: r for r in runs}
    fail_lines = []
    for s in fails:
        it = items[s["qid"]]
        r = run_by_q.get(s["qid"], {})
        tools = ", ".join(t["name"] + (f"({t['input'].get('query_name')})" if t["name"] == "run_query" else "")
                          for t in r.get("tool_calls", []))
        fail_lines.append(f"* **{s['qid']}** ({s['category']}). Q: {it.question}\n  Expected `{json.dumps(s['expected'], default=str)}`, "
                          f"got `{json.dumps(s['got'], default=str)}` (answerable={s['said_answerable']}). Tools: {tools or 'none'}.")
    dropped = DROPPED_PATH.read_text().splitlines() if DROPPED_PATH.exists() else []
    val = json.loads(J.VALIDATION_PATH.read_text()) if J.VALIDATION_PATH.exists() else None
    jd = res["judge"]
    by_case = ", ".join(f"{k}: {_p(v['agreement'])}" for k, v in val["agreement_by_case"].items()) if val else ""
    judge_txt = "Judge not run." if not val else (
        f"Judge model `{val['model']}`, validated on {val['n_cases']} cases with known labels "
        f"(20 template-rendered correct answers and 20 deliberate corruptions: wrong value, wrong entity, false refusal, "
        f"invented answer). **Agreement {_p(val['agreement'])}** "
        f"({by_case}). "
        f"The use threshold is {_p(val['min_agreement_to_use'])}, so the judge "
        f"{'may be used' if val['usable'] else 'is NOT used'}.")
    if not jd.get("used"):
        judge_txt += (f"\n\nThe judge's free-text pass was not run on this agent run ({jd.get('reason', 'skipped')}); "
                      "it is optional and costs extra API calls.")
    if jd.get("used"):
        jfails = [v for v in jd["verdicts"] if v["verdict"] == "FAIL"]
        judge_txt += (f"\n\nOn the agent's {jd['n']} answers, the judge's free-text **pass rate is {_p(jd['pass_rate'])}**."
                      + ("" if not jfails else " Judge FAIL verdicts:\n" + "\n".join(
                          f"  * {v['qid']}: {v['reason']}" for v in jfails)))
    text = f"""# Evaluation report

Run `{res['tag']}` scored {res['scored_at_utc']}. Agent model `{res['model']}` (effort `{res['effort']}`).
Reproduce the scoring with `make eval-offline` (no API calls) or the full run with `make eval` (live, budget-guarded).
Numbers below come from `reports/eval_results.json`.

## Golden set

* **{res['golden_size']} questions** from {len(res['by_template'])} templates ({o['n_answerable']} answerable,
  {o['n_unanswerable']} unanswerable). Built by `supplier_graph.evalh.golden`; nothing is hand-labeled.
* Answerable expected values come from the **SQL path** (DuckDB) and are **verified with the graph path** (NetworkX
  traversal). A question whose two paths disagree is dropped and logged to `reports/golden_dropped.jsonl`;
  **{len(dropped)} were dropped** in this build. Country-risk and award questions are verified the same way
  (SQL table vs. graph node attributes).
* Unanswerable questions use verifiably absent entities: invented supplier names (the resolver finds no
  candidates and a fuzzy scan scores below 80), countries outside the risk feed, nonexistent award IDs and
  program codes, and questions needing fields the graph doesn't hold (delivery rates, headcount, forecasts).
* Every item records its `label_source`.

## Headline metrics

| metric | value |
|---|---|
| answer correctness (answerable) | **{_p(o['answer_correctness'])}** |
| correct refusal rate (unanswerable) | **{_p(o['correct_refusal_rate'])}** |
| false refusal rate (answerable) | {_p(o['false_refusal_rate'])} |
| citation precision (cited IDs that exist **and** appeared in this run's tool output; micro) | **{_p(o['citation_precision_micro'])}** |
| citation existence rate | {_p(o['citation_exists_rate'])} |
| fabricated citations (IDs not in the data) | {o['n_fabricated_citations']} |
| required-citation hit rate (key entity cited) | {_p(o['required_citation_hit_rate'])} |
| tool-call correctness (expected named query/tool called with the expected key parameter) | **{_p(o['tool_call_correctness'])}** |
| steps per question (mean / max) | {o['steps_mean']} / {o['steps_max']} |
| cost (total / mean per question) | ${o['cost_total_usd']:.4f} / ${o['cost_mean_usd']:.4f} |
| latency (mean / p90) | {o['latency_mean_s']}s / {o['latency_p90_s']}s |
| API/budget errors | {o['n_errors']} |

Total Anthropic spend for the whole project at scoring time: **${res['anthropic_spend_total_usd']:.4f}** (cap $20).

## Results by question type

| template | kind | n | correct | tool-call correct | citation precision | steps | cost/q | latency |
|---|---|---|---|---|---|---|---|---|
{chr(10).join(rows)}

## LLM judge (free-text quality only)

Correctness is never judged by an LLM; it is scored deterministically above. {judge_txt}

Judge validation limits: the corruptions are deliberate and fairly blatant, and the "good" answers are
template-rendered, not real agent prose. 100% agreement here shows the judge catches gross contradictions and
false or invented refusals. It does not show it catches subtle errors (a slightly wrong share, a
right-number-wrong-unit). Treat its pass rate as a smoke test of prose quality, not a correctness metric.

## Failure analysis

Failure categories: {dict(cats) if cats else 'none'}.

{chr(10).join(fail_lines) if fail_lines else 'No failures in this run.'}

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
"""
    notes = REPORTS / f"eval_notes_{res['tag']}.md"
    if notes.exists():
        text = text.replace("## Metric definitions", notes.read_text().rstrip() + "\n\n## Metric definitions")
    (REPORTS / "eval_report.md").write_text(text)
