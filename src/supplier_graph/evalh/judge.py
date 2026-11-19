"""LLM judge for free-text quality only (never for correctness, which is scored deterministically).

The judge rates whether an answer's prose (a) directly answers the question and (b) is consistent with the
reference facts, with no contradicting numbers or entities, and (c) for unanswerable questions, says the data
cannot answer instead of inventing one. Before it is used, it is validated on cases with known labels:
template-rendered correct answers (label PASS) and deliberately corrupted answers (label FAIL: wrong number,
wrong entity, false refusal, invented answer). Its agreement rate is reported, and its verdicts are used only
if agreement >= JUDGE_MIN_AGREEMENT.
"""

from __future__ import annotations

import argparse
import json
import random

import anthropic

from supplier_graph.agent import costs
from supplier_graph.config import REPORTS, env
from supplier_graph.evalh.golden import GoldenItem, load_golden

JUDGE_MODEL = env("JUDGE_MODEL", "claude-opus-5-5")
JUDGE_MIN_AGREEMENT = 0.85
VALIDATION_PATH = REPORTS / "judge_validation.json"

RUBRIC = """You grade the prose quality of an answer from a supply-chain data assistant.
You get the question, REFERENCE FACTS computed deterministically from the data (treat them as true), and the CANDIDATE answer.
Return PASS only if all hold:
1. It directly answers the question asked (or, if the reference says the question is unanswerable, it clearly says the data cannot answer and does not invent an answer).
2. Nothing in it contradicts the reference facts: headline numbers, counts, shares, IDs and entity names must match them (small rounding is fine).
3. It does not refuse or hedge when the reference facts contain the answer.
Extra details that are not in the reference facts are fine if they do not contradict them.
Return FAIL otherwise. Be strict about contradictions."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict", "reason"],
          "properties": {"verdict": {"type": "string", "enum": ["PASS", "FAIL"]}, "reason": {"type": "string"}}}


def judge(question: str, reference: dict, answerable: bool, candidate: str, client=None, run_id: str = "judge") -> dict:
    client = client or anthropic.Anthropic()
    costs.check_budget(0.02)
    ref = {"answerable": answerable, **reference}
    resp = client.beta.messages.create(
        model=JUDGE_MODEL, max_tokens=2000, system=RUBRIC,
        messages=[{"role": "user", "content": f"QUESTION:\n{question}\n\nREFERENCE FACTS:\n{json.dumps(ref, default=str)}"
                                              f"\n\nCANDIDATE ANSWER:\n{candidate}"}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    cost = costs.record(getattr(resp, "model", JUDGE_MODEL) or JUDGE_MODEL, resp.usage, "judge", run_id)
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        out = {"verdict": "FAIL", "reason": f"unparseable judge output: {text[:100]}"}
    out["cost_usd"] = cost
    return out


# ------------------------------------------------------------- validation set (known labels)
def _fmt(v) -> str:
    if isinstance(v, float) and v <= 1:
        return f"{v:.4f}"
    return json.dumps(v) if isinstance(v, list | dict) else str(v)


def render_good(item: GoldenItem) -> str:
    if not item.answerable:
        return (f"The data cannot answer this: {item.reference_facts.get('why', 'not in the data')}. "
                "I found no matching records, so I won't estimate.")
    facts = "; ".join(f"{k} = {_fmt(v)}" for k, v in item.reference_facts.items())
    return f"The answer is {_fmt(item.expected)}. Supporting figures from the data: {facts}."


def render_corrupt(item: GoldenItem, rng: random.Random, others: list[GoldenItem]) -> tuple[str, str]:
    if not item.answerable:
        return ("Yes. It is a well-established supplier with 14 dependent offices and about $52.3M in obligations, "
                "most of it sole-source.", "invented_answer")
    kind = rng.choice(["wrong_value", "false_refusal"]) if item.answer_type in ("int", "float") else \
        rng.choice(["wrong_entity", "false_refusal"])
    if kind == "false_refusal":
        return "The data does not contain enough information to answer this question.", kind
    if item.answer_type == "bool":
        facts = {k: (not v if isinstance(v, bool) else v) for k, v in item.reference_facts.items()}
        return f"The answer is {_fmt(not item.expected)}. Supporting figures from the data: " + \
            "; ".join(f"{k} = {_fmt(v)}" for k, v in facts.items()) + ".", "wrong_value"
    if kind == "wrong_value":
        e = item.expected
        bad = (e + 7 if e < 5 else round(e * 2.5)) if item.answer_type == "int" else round(min(e * 2 + 0.15, 0.99), 4) \
            if e <= 1 else round(e * 2.5, 1)
        facts = {k: (bad if v == e else v) for k, v in item.reference_facts.items()}
        return f"The answer is {_fmt(bad)}. Supporting figures from the data: " + \
            "; ".join(f"{k} = {_fmt(v)}" for k, v in facts.items()) + ".", kind
    other = next((o for o in others if o.answer_type == item.answer_type and o.expected != item.expected), None)
    if other is None:
        return f"The answer is {_fmt(['999', 'Q7X'])}.", "wrong_entity"
    return f"The answer is {_fmt(other.expected)} ({other.reference_facts.get('top_supplier', '')}).", "wrong_entity"


def validate(n_items: int = 20, client=None) -> dict:
    items = load_golden()
    rng = random.Random(11)
    ans = [i for i in items if i.answerable]
    un = [i for i in items if not i.answerable]
    pick = rng.sample(ans, min(n_items - 5, len(ans))) + rng.sample(un, min(5, len(un)))
    cases = []
    for it in pick:
        cases.append((it, render_good(it), "PASS", "known_good"))
        bad, kind = render_corrupt(it, rng, ans)
        cases.append((it, bad, "FAIL", kind))
    results = []
    for it, cand, label, kind in cases:
        v = judge(it.question, it.reference_facts, it.answerable, cand, client=client, run_id="judge_validation")
        results.append({"qid": it.qid, "case": kind, "label": label, "verdict": v["verdict"], "agree": v["verdict"] == label,
                        "reason": v["reason"], "cost_usd": v["cost_usd"]})
    by_case: dict[str, list[bool]] = {}
    for r in results:
        by_case.setdefault(r["case"], []).append(r["agree"])
    out = {"model": JUDGE_MODEL, "n_cases": len(results),
           "agreement": round(sum(r["agree"] for r in results) / len(results), 4),
           "agreement_by_case": {k: {"n": len(v), "agreement": round(sum(v) / len(v), 4)} for k, v in by_case.items()},
           "min_agreement_to_use": JUDGE_MIN_AGREEMENT,
           "label_source": "known_good = template-rendered from deterministic reference facts; FAIL cases = "
                           "deliberate synthetic corruptions",
           "cost_usd": round(sum(r["cost_usd"] for r in results), 4), "results": results}
    out["usable"] = out["agreement"] >= JUDGE_MIN_AGREEMENT
    VALIDATION_PATH.write_text(json.dumps(out, indent=2))
    return out


SUBTLE_PATH = REPORTS / "judge_validation_subtle.json"


def _subtle_corrupt(prose: str, item: GoldenItem) -> tuple[str, str] | None:
    """One small, plausible error in otherwise real agent prose: a count off by one, or a share moved 5 points."""
    import re
    e = item.expected
    if item.answer_type == "int" and isinstance(e, int) and e >= 2:
        pat = re.compile(rf"(?<![\w.,$]){e}(?![\w.,%])")
        if pat.search(prose):
            return pat.sub(str(e + 1), prose), "off_by_one_count"
        return None
    if item.answer_type == "float" and isinstance(e, float) and 0.1 <= e <= 0.9:
        for txt in (f"{e * 100:.1f}%", f"{e * 100:.0f}%"):
            if txt in prose:
                shifted = f"{(e + 0.05) * 100:.1f}%" if txt.endswith(".1f%") or "." in txt else f"{(e + 0.05) * 100:.0f}%"
                return prose.replace(txt, shifted), "share_plus_5_points"
    return None


def validate_subtle(n_pairs: int = 15, client=None) -> dict:
    """Validation on the agent's real prose (from the logged eval run): deterministically correct answers (label
    PASS) and the same prose with one subtle corruption (label FAIL). Complements the blatant-corruption set."""
    from supplier_graph.agent.agent import JSON_BLOCK
    from supplier_graph.evalh.metrics import score_run
    from supplier_graph.evalh.run import latest_eval_log
    items = {i.qid: i for i in load_golden()}
    _, runs = latest_eval_log()
    pool = []
    for r in sorted(runs, key=lambda x: x["tags"]["qid"]):
        it = items.get(r["tags"]["qid"])
        if not it or not it.answerable or not score_run(r, it)["correct"]:
            continue
        prose = JSON_BLOCK.sub("", r["final_text"]).strip()
        bad = _subtle_corrupt(prose, it)
        if bad:
            pool.append((it, prose, bad))
    rng = random.Random(13)
    pick = rng.sample(pool, min(n_pairs, len(pool)))
    results = []
    for it, good, (bad, kind) in pick:
        for cand, label, case in ((good, "PASS", "real_correct_prose"), (bad, "FAIL", kind)):
            v = judge(it.question, it.reference_facts, it.answerable, cand, client=client, run_id="judge_validation_subtle")
            results.append({"qid": it.qid, "case": case, "label": label, "verdict": v["verdict"],
                            "agree": v["verdict"] == label, "reason": v["reason"], "cost_usd": v["cost_usd"]})
    by_case: dict[str, list[bool]] = {}
    for r in results:
        by_case.setdefault(r["case"], []).append(r["agree"])
    out = {"model": JUDGE_MODEL, "n_cases": len(results), "pool_size": len(pool),
           "agreement": round(sum(r["agree"] for r in results) / len(results), 4),
           "agreement_by_case": {k: {"n": len(v), "agreement": round(sum(v) / len(v), 4)} for k, v in by_case.items()},
           "label_source": "PASS = agent prose whose answer the deterministic scorer marked correct; FAIL = the same "
                           "prose with one synthetic subtle corruption",
           "cost_usd": round(sum(r["cost_usd"] for r in results), 4), "results": results}
    SUBTLE_PATH.write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--validate-subtle", action="store_true")
    a = ap.parse_args()
    if a.validate_subtle:
        r = validate_subtle()
        print(json.dumps({k: v for k, v in r.items() if k != "results"}, indent=2))
    if a.validate:
        r = validate()
        print(json.dumps({k: v for k, v in r.items() if k != "results"}, indent=2))
