"""End-to-end resolution test with identifiers withheld (blocking + scoring + the runner-up margin rule).

Pairwise precision/recall (er.evaluate) does not show what happens when a record arrives with no UEI and must be
found among ~11,600 suppliers. This test simulates that on real records:
  * present: a non-canonical record of a UEI whose normalized name differs from the canonical record's, with UEI and
    CAGE withheld. Correct = linked to its own UEI; wrong = linked to another; unresolved = NOID-.
  * absent: a UEI with a single record is removed from the index entirely; its record should stay unresolved
    (any link is a false merge).
Both matchers (hand-set rule and learned evidence weights), each with and without the facility-address blocking
key, run on identical records. Deterministic sample by hash.
Writes reports/er_holdout.json and appends a section to reports/entity_resolution.md. Run by `make er`.
"""

from __future__ import annotations

import hashlib
import json

import pandas as pd

from supplier_graph.config import REPORTS
from supplier_graph.er import linkage
from supplier_graph.er.evaluate import THRESHOLD_FILE
from supplier_graph.er.matcher import score
from supplier_graph.er.normalize import core_name
from supplier_graph.er.resolve import MARGIN, candidate_scores, decide, fuzzy_resolve
from supplier_graph.stage import connect

N_PRESENT, N_ABSENT = 1500, 500
OP_FILE = REPORTS / "er_operating_point.json"
# Operating-point search (learned matcher + facility blocking). Fixed before the curve was computed:
#   utility = correct-link rate - FALSE_MERGE_WEIGHT x (wrong-link rate + absent false-link rate)
# A false merge silently corrupts two companies' dependency counts; a missed link leaves a visible NOID- record an
# analyst can review, so a false merge is weighted at twice a missed link. Chosen on the tuning half of the records,
# reported on the confirmation half.
FALSE_MERGE_WEIGHT = 2.0
GRID_T = (None, 0.60, 0.70, 0.80, 0.85, 0.90, 0.95)          # None = the dev-tuned threshold
GRID_MARGIN = (MARGIN, 0.05, 0.10, 0.20)
GRID_T_ADDR = (None, 0.80, 0.90, 0.95, 0.99, 2.0)            # address-only candidates; 2.0 = never link on address alone


def _h(x: str) -> str:
    return hashlib.sha1(x.encode()).hexdigest()


def run() -> dict:
    con = connect(read_only=True)
    rec = con.execute("SELECT * FROM entity_records WHERE uei IS NOT NULL ORDER BY record_id").df()
    con.close()
    canon = rec.sort_values(["n_observations", "record_id"], ascending=[False, True], kind="mergesort").drop_duplicates("uei")
    canon_core = dict(zip(canon["uei"], canon["name"].map(core_name), strict=True))
    others = rec[~rec.record_id.isin(canon.record_id)]
    others = others[others["name"].map(core_name) != others["uei"].map(canon_core)]
    present = others.assign(k=others.record_id.map(_h)).sort_values("k").drop_duplicates("uei").head(N_PRESENT)
    counts = rec.groupby("uei").size()
    singles = canon[canon.uei.isin(counts[counts == 1].index)]
    absent = singles.assign(k=singles.uei.map(_h)).sort_values("k").head(N_ABSENT)
    index = canon[~canon.uei.isin(absent.uei)].to_dict("records")
    rule_t = float(json.loads(THRESHOLD_FILE.read_text())["threshold"])
    prob, learned_t = linkage.load_scorer()
    res: dict = {"n_present": int(len(present)), "n_absent": int(len(absent)), "index_size": len(index), "matchers": {}}
    for name, scorer, t, ab in (("rule, name blocking only (v1)", score, rule_t, False),
                                ("rule + facility blocking", score, rule_t, True),
                                ("learned, name blocking only", prob, learned_t, False),
                                ("learned + facility blocking", prob, learned_t, True)):
        p = pd.DataFrame(fuzzy_resolve(present.to_dict("records"), index, scorer, t, ab)).merge(
            present[["record_id", "uei"]], on="record_id")
        a = pd.DataFrame(fuzzy_resolve(absent.to_dict("records"), index, scorer, t, ab))
        linked = p.method == "fuzzy"
        res["matchers"][name] = {
            "threshold": t,
            "present_correct": round(float((linked & (p.supplier_id == p.uei)).mean()), 4),
            "present_wrong_link": round(float((linked & (p.supplier_id != p.uei)).mean()), 4),
            "present_unresolved": round(float((~linked).mean()), 4),
            "absent_false_link": round(float((a.method == "fuzzy").mean()), 4),
            "precision_of_links": round(float((linked & (p.supplier_id == p.uei)).sum()
                                              / max(int(linked.sum()) + int((a.method == "fuzzy").sum()), 1)), 4),
        }
    res["operating_point"] = tradeoff(present, absent, index, prob, learned_t)
    (REPORTS / "er_holdout.json").write_text(json.dumps(res, indent=2))
    OP_FILE.write_text(json.dumps(res["operating_point"]["selected"], indent=2))
    rows = "\n".join(f"| {k} | {v['threshold']} | {v['present_correct']:.1%} | {v['present_wrong_link']:.1%} | "
                     f"{v['present_unresolved']:.1%} | {v['absent_false_link']:.1%} | {v['precision_of_links']:.1%} |"
                     for k, v in res["matchers"].items())
    section = f"""
## End-to-end resolution with identifiers withheld (`reports/er_holdout.json`)

{res['n_present']} real records whose name differs from their UEI's canonical record, plus {res['n_absent']} records
whose UEI was removed from the index entirely, resolved through blocking, scoring and the runner-up margin rule
against {res['index_size']:,} canonical suppliers. Identifiers are withheld from the matcher.

| matcher | threshold | present: correct link | present: wrong link | present: unresolved | absent: false link | precision of all links |
|---|---|---|---|---|---|---|
{rows}

A wrong link merges two companies (it corrupts dependency counts); an unresolved record only splits one.

**How this evolved (disclosed):** the first run used name-based blocking only, and a diagnosis on these same records
showed most losses were blocking misses: renamed or acquired companies at an unchanged facility (Cobham Advanced
Electronic Solutions vs CAES Systems at one street address) never became candidates. The facility key (street +
ZIP5) was added in response. It has no tunable parameter, and the weights and thresholds still come only from the
dev split of the pair labels, but the before/after is measured on the records that motivated it (all {res['n_present']}
eligible records were used; none were left for a fresh confirmation). The production resolver (`make er`) uses the
matcher selected on dev F1 plus facility blocking. Absent-company false links are the price: some may be sibling
registrations at the same site rather than true errors, which these labels cannot tell apart.
"""
    md = REPORTS / "entity_resolution.md"
    text = md.read_text()
    cut = text.find("\n## End-to-end resolution with identifiers withheld")
    md.write_text((text if cut < 0 else text[:cut].rstrip() + "\n") + section + _tradeoff_section(res))
    print(json.dumps(res, indent=2))
    return res


def _rates(dec_p: list[dict], uei: list[str], dec_a: list[dict]) -> dict:
    linked = [d["method"] == "fuzzy" for d in dec_p]
    corr = sum(lk and d["supplier_id"] == u for lk, d, u in zip(linked, dec_p, uei, strict=True))
    wrong = sum(linked) - corr
    af = sum(d["method"] == "fuzzy" for d in dec_a)
    n_p, n_a = max(len(dec_p), 1), max(len(dec_a), 1)
    return {"present_correct": round(corr / n_p, 4), "present_wrong_link": round(wrong / n_p, 4),
            "present_unresolved": round(1 - sum(linked) / n_p, 4), "absent_false_link": round(af / n_a, 4),
            "precision_of_links": round(corr / max(sum(linked) + af, 1), 4)}


def _utility(r: dict, w: float = FALSE_MERGE_WEIGHT) -> float:
    return r["present_correct"] - w * (r["present_wrong_link"] + r["absent_false_link"])


def tradeoff(present: pd.DataFrame, absent: pd.DataFrame, index: list[dict], prob, learned_t: float) -> dict:
    """Threshold / margin / address-only abstain grid for the shipped matcher; choose on one half, confirm on the
    other. Halves are fixed by record hash (present) and UEI hash (absent)."""
    p_rows, a_rows = present.to_dict("records"), absent.to_dict("records")
    cp, ca = candidate_scores(p_rows, index, prob, True), candidate_scores(a_rows, index, prob, True)
    half_p = [int(_h(r["record_id"] + "|half"), 16) % 2 for r in p_rows]
    half_a = [int(_h(r["uei"] + "|half"), 16) % 2 for r in a_rows]

    def ev(t, m, ta, which):
        P = [(r, c) for r, c, h in zip(p_rows, cp, half_p, strict=True) if which is None or h == which]
        A = [(r, c) for r, c, h in zip(a_rows, ca, half_a, strict=True) if which is None or h == which]
        return _rates([decide(r, c, t, m, ta) for r, c in P], [r["uei"] for r, _ in P],
                      [decide(r, c, t, m, ta) for r, c in A])

    grid = []
    for t0 in GRID_T:
        t = learned_t if t0 is None else t0
        for m in GRID_MARGIN:
            for ta in GRID_T_ADDR:
                if ta is not None and ta <= t:
                    continue
                tune = ev(t, m, ta, 0)
                grid.append({"threshold": t, "margin": m, "t_addr": ta, "tune": tune,
                             "utility_tune": round(_utility(tune), 4)})
    default = next(g for g in grid if g["threshold"] == learned_t and g["margin"] == MARGIN and g["t_addr"] is None)
    best = max(grid, key=lambda g: (g["utility_tune"], -g["threshold"], -g["margin"]))
    sens = {}
    for w in (1.0, 3.0):
        b = max(grid, key=lambda g, w=w: (_utility(g["tune"], w), -g["threshold"], -g["margin"]))
        sens[str(w)] = {k: b[k] for k in ("threshold", "margin", "t_addr")}
    for g in (default, best):
        g["confirm"] = ev(g["threshold"], g["margin"], g["t_addr"], 1)
        g["all"] = ev(g["threshold"], g["margin"], g["t_addr"], None)
    # the curve: for each threshold, the best (margin, t_addr) by tuning utility
    curve = []
    for t in sorted({g["threshold"] for g in grid}):
        g = max((x for x in grid if x["threshold"] == t), key=lambda x: x["utility_tune"])
        curve.append({k: g[k] for k in ("threshold", "margin", "t_addr", "tune", "utility_tune")})
    sel = {"matcher": "learned + facility blocking", "threshold": best["threshold"], "margin": best["margin"],
           "t_addr": best["t_addr"], "false_merge_weight": FALSE_MERGE_WEIGHT,
           "chosen_on": "tuning half of the held-out records (utility = correct - 2 x (wrong + absent false))"}
    return {"rule": f"utility = correct-link rate - {FALSE_MERGE_WEIGHT:g} x (wrong-link rate + absent false-link "
                    "rate); fixed before the curve was computed",
            "n_tune": {"present": half_p.count(0), "absent": half_a.count(0)},
            "n_confirm": {"present": half_p.count(1), "absent": half_a.count(1)},
            "grid_size": len(grid), "curve": curve, "default": default, "selected_point": best,
            "selected": sel, "weight_sensitivity": sens}


def _fmt_ta(ta) -> str:
    return "same as threshold" if ta is None else ("never" if ta > 1 else f"{ta:.2f}")


def _tradeoff_section(res: dict) -> str:
    op = res["operating_point"]
    d, b = op["default"], op["selected_point"]
    curve = "\n".join(f"| {c['threshold']:.2f} | {c['margin']:.2f} | {_fmt_ta(c['t_addr'])} | "
                       f"{c['tune']['present_correct']:.1%} | {c['tune']['present_wrong_link']:.1%} | "
                       f"{c['tune']['absent_false_link']:.1%} | {c['utility_tune']:.3f} |" for c in op["curve"])

    def row(name, g, k):
        r = g[k]
        return (f"| {name} | {g['threshold']:.2f} / {g['margin']:.2f} / {_fmt_ta(g['t_addr'])} | {r['present_correct']:.1%} | "
                f"{r['present_wrong_link']:.1%} | {r['present_unresolved']:.1%} | {r['absent_false_link']:.1%} | "
                f"{r['precision_of_links']:.1%} |")
    same = (d["threshold"], d["margin"], d["t_addr"]) == (b["threshold"], b["margin"], b["t_addr"])
    sens = "; ".join(f"weight {w}: threshold {v['threshold']:.2f}, margin {v['margin']:.2f}, address-only "
                     f"{_fmt_ta(v['t_addr'])}" for w, v in op["weight_sensitivity"].items())
    return f"""
## Operating point: can an abstain rule cut absent-company false links? (`reports/er_holdout.json`)

Absent-company false links rose from 7.0% to 8.4% when facility blocking was added. This searches {op['grid_size']}
operating points of the shipped matcher (learned weights + facility blocking): the link threshold, the runner-up
margin, and a separate, stricter threshold for candidates found **only** through the facility address (an abstain
rule for address-only matches). Rule: {op['rule']}. A false merge corrupts two
companies' dependency counts; a missed link leaves a visible `NOID-` record. Records are split by hash: points are
chosen on the tuning half ({op['n_tune']['present']} present, {op['n_tune']['absent']} absent) and reported on the
confirmation half ({op['n_confirm']['present']} present, {op['n_confirm']['absent']} absent).

### Tradeoff curve (tuning half; best margin and address rule at each threshold)

| threshold | margin | address-only threshold | correct link | wrong link | absent false link | utility |
|---|---|---|---|---|---|---|
{curve}

### Selected vs previous operating point, confirmation half

| operating point | threshold / margin / address-only | correct | wrong | unresolved | absent false link | precision of links |
|---|---|---|---|---|---|---|
{row("previous (dev-tuned threshold)", d, "confirm")}
{row("selected", b, "confirm")}

{"The selected point is the previous one: no abstain rule beat it under this utility." if same else
 "The selected point is now used by the production resolver (`reports/er_operating_point.json`). In the batch build every record carries a UEI, so this changes no graph number; it governs records that arrive without identifiers."}
Weight sensitivity (chosen on the tuning half): {sens}. On all {res['n_present']} + {res['n_absent']} records the
selected point gives {b['all']['present_correct']:.1%} correct, {b['all']['present_wrong_link']:.2%} wrong and
{b['all']['absent_false_link']:.1%} absent false links (previous: {d['all']['present_correct']:.1%},
{d['all']['present_wrong_link']:.2%}, {d['all']['absent_false_link']:.1%}).
"""


if __name__ == "__main__":
    run()
