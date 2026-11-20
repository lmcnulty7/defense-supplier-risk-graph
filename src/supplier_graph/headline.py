"""Every headline number in one generated file: `make headline` -> reports/HEADLINE.md and reports/headline.json.

Values are read from the result JSONs written by the make targets (never typed by hand). README, FINAL_REPORT, the
site, the brief, the demo script and the interview walkthrough must quote these exact strings;
tests/test_headline.py enforces both that this file is current and that those documents agree with it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass

from supplier_graph.config import REPORTS, ROOT

OUT_MD, OUT_JSON = REPORTS / "HEADLINE.md", REPORTS / "headline.json"


@dataclass
class H:
    key: str
    text: str            # the exact string documents must use
    label: str
    source: str
    section: str
    alts: tuple[str, ...] = ()  # accepted roundings (e.g. "56%" on a plain-language slide)


def _j(name: str) -> dict:
    return json.loads((REPORTS / name).read_text())


def pct(x: float, d: int = 1) -> str:
    return f"{x * 100:.{d}f}%"


def spend_total() -> float:
    from supplier_graph.agent import costs
    return round(costs.total_spent(), 2)


def tests_collected() -> int:
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=ROOT, capture_output=True,
                         text=True, check=False).stdout
    m = re.search(r"(\d+) tests? collected", out)
    return int(m.group(1)) if m else -1


def build(n_tests: int | None = None) -> list[H]:
    gs, er, ho, neo = _j("graph_summary.json"), _j("er_metrics.json"), _j("er_holdout.json"), _j("neo4j_parity.json")
    bt, cmp_, gd = _j("risk_backtest.json"), _j("comparison.json")["cells"], _j("guard_eval.json")
    jv, js, ev = _j("judge_validation.json"), _j("judge_validation_subtle.json"), _j("eval_results.json")["overall"]
    draft = _j("comparison_challenge_draft.json")["cells"]
    sc, ps = gs["scope"], bt["primary_split"]
    s = ps["scores"]
    adj, adj_x = bt["size_adjusted_flag_effects"], bt["sensitivity_excluding_cluster"]["size_adjusted_flag_effects"]
    ll, hm, op = er["learned_linkage"], ho["matchers"], ho["operating_point"]
    v1, blk = hm["rule, name blocking only (v1)"], hm["learned + facility blocking"]
    d_c, s_c = op["default"]["confirm"], op["selected_point"]["confirm"]
    sel = op["selected_point"]

    def reps(k: str) -> list[dict]:
        return cmp_.get(k, {}).get("reps") or []

    def runs(k: str, field: str) -> str:
        return ", ".join(r[field] for r in reps(k))

    def nm(k: str) -> list[int]:
        part = [p["u_near_miss"]["correct"] for p in cmp_.get(k, {}).get("partial_reps_by_template", [])
                if "u_near_miss" in p]
        return [r["by_template"]["u_near_miss"]["correct"] for r in reps(k)] + part

    def fab(k: str) -> str:
        return ", ".join(str(r["n_fabricated_citations"]) for r in reps(k))

    opus_cost = reps("challenge/opus_tools")[0]["cost_mean_usd"]
    haiku_cost = sum(r["cost_mean_usd"] for r in reps("challenge/haiku_tools")) / len(reps("challenge/haiku_tools"))
    cl = [c["cluster_share"] for c in bt["cluster_offices_event_share"]]
    conv = sum(e["n"] for e in bt["event_counts_by_year"] if e["act"] == "F")
    n_ev = sum(e["n"] for e in bt["event_counts_by_year"])
    rp = gd["replay_logged_opus_resolver_calls"]["answerable"]
    feed = gs["risk_feed"]
    gp, ge = bt["feed_policy"]["candidates"]["feed_p_escalation"], bt["feed_policy"]["candidates"]["feed_exposure"]
    D, E, G, B, A, T = "Data", "Entity resolution", "Graph", "Risk backtest", "Agent", "Trust and cost"
    h = [
        H("n_prime_awards", f"{sc['n_prime_awards']:,}", "DoD PSC-J prime awards, FY2023-FY2025", "graph_summary.json", D),
        H("total_obligations", f"${sc['total_prime_obligations'] / 1e9:,.1f}B", "lifetime obligations on those awards",
          "graph_summary.json", D),
        H("n_subawards", f"{sc['n_subawards']:,}", "subawards", "graph_summary.json", D),
        H("n_suppliers", f"{gs['nodes']['Supplier']:,}", "resolved suppliers", "graph_summary.json", D),
        H("n_parents", f"{gs['nodes']['ParentCompany']:,}", "parent companies", "graph_summary.json", D),
        H("n_offices", f"{gs['nodes']['Office']:,}", "awarding offices", "graph_summary.json", D),
        H("n_transactions", f"{bt['source']['files']['transactions']['rows']:,}", "contract transactions FY2022-FY2025",
          "risk_backtest.json", D),
        H("sub_coverage", pct(sc["prime_awards_with_subawards_in_scope"] / sc["n_prime_awards"]),
          "share of prime awards with any reported subaward", "graph_summary.json", D),
        H("flagged_share", pct(sc["obligation_share_to_flagged_suppliers"], 2),
          "obligations going to a supplier with at least one flag", "graph_summary.json", D, ("87%",)),
        H("n_flagged", f"{gs['suppliers_any_flag']:,}", "suppliers with at least one flag", "graph_summary.json", D),
        H("feed_skill", feed["skill_status"], "MENA feed skill_status", "graph_summary.json", D),
        H("feed_version", f"schema {feed['schema_version']}, label {feed['label']}", "MENA feed version and forecast target",
          "graph_summary.json", D),
        H("feed_gate_p", ("adopted" if gp["adopted"] else
                          f"not adopted: {min(gp['coverage_feed_exposed_supplier_years'].values()) * 100:.0f}% "
                          "point-in-time coverage" if "coverage_feed_exposed_supplier_years" in gp else "not adopted"),
          "p_escalation as a termination-likelihood input (backtest gate)", "risk_backtest.json", B),
        H("feed_gate_exposure", f"AP {ge['ap_gain']['mean']:+.4f} [{ge['ap_gain']['ci'][0]:.4f}, {ge['ap_gain']['ci'][1]:.4f}]"
          if "ap_gain" in ge else ge.get("reason", "n/a"),
          "feed-country exposure added to the likelihood model: AP gain, 95% CI (not adopted)", "risk_backtest.json", B),
        H("er_id_pr", f"P {ll['identifier']['precision']:.3f} / R {ll['identifier']['recall']:.3f}",
          "pairwise, identifier-derived test pairs (learned weights)", "er_metrics.json", E),
        H("er_syn_pr", f"P {ll['synthetic']['precision']:.3f} / R {ll['synthetic']['recall']:.3f}",
          "pairwise, synthetic test pairs (learned weights)", "er_metrics.json", E),
        H("er_acronym", f"{er['ablation_v1_no_acronyms']['acronym_recall']:.2f} → {ll['acronym_recall']:.2f}",
          "acronym recall, v1 → learned", "er_metrics.json", E),
        H("er_index", f"{ho['index_size']:,}", "suppliers in the end-to-end index", "er_holdout.json", E),
        H("er_n_present", f"{ho['n_present']}", "renamed records resolved end to end (UEI withheld)", "er_holdout.json", E),
        H("er_v1_correct", pct(v1["present_correct"]), "end-to-end correct links, v1 matcher", "er_holdout.json", E,
          ("56%",)),
        H("er_blocking_correct", pct(blk["present_correct"]),
          "end-to-end correct links after facility blocking + learned weights (dev threshold)", "er_holdout.json", E,
          ("83%",)),
        H("er_v1_wrong", pct(v1["present_wrong_link"], 2), "wrong links, v1", "er_holdout.json", E),
        H("er_blocking_wrong", pct(blk["present_wrong_link"], 2), "wrong links after the blocking fix",
          "er_holdout.json", E),
        H("er_v1_absent", pct(v1["absent_false_link"]), "absent-company false links, v1", "er_holdout.json", E),
        H("er_blocking_absent", pct(blk["absent_false_link"]), "absent-company false links after the blocking fix",
          "er_holdout.json", E),
        H("er_op_point", f"threshold {sel['threshold']:.2f}, margin {sel['margin']:.2f}",
          "shipped operating point (chosen on the tuning half)", "er_holdout.json", E),
        H("er_op_absent", f"{pct(d_c['absent_false_link'])} → {pct(s_c['absent_false_link'])}",
          "absent-company false links, previous → shipped point (confirmation half)", "er_holdout.json", E),
        H("er_op_wrong", f"{pct(d_c['present_wrong_link'])} → {pct(s_c['present_wrong_link'])}",
          "wrong links, previous → shipped point (confirmation half)", "er_holdout.json", E),
        H("er_op_correct", f"{pct(d_c['present_correct'])} → {pct(s_c['present_correct'])}",
          "correct links, previous → shipped point (confirmation half)", "er_holdout.json", E),
        H("neo4j_blast", f"{neo['blast_radius']['n_equal']}/{neo['blast_radius']['n_suppliers']}",
          "Cypher blast radius equal to NetworkX (sampled suppliers); all node/edge counts equal", "neo4j_parity.json",
          G),
        H("bt_n", f"{ps['n_test']:,}", "test supplier-years (FY2024 features → FY2025 outcomes)", "risk_backtest.json", B),
        H("bt_pos", f"{ps['n_test_pos']}", "of which had a termination/cancellation event", "risk_backtest.json", B),
        H("auc_flags", f"{s['current_flags']['auc']:.3f}", "ROC-AUC, current flags", "risk_backtest.json", B, ("0.64",)),
        H("auc_size", f"{s['baseline_size']['auc']:.3f}", "ROC-AUC, contract count only", "risk_backtest.json", B,
          ("0.76",)),
        H("auc_model", f"{s['logistic']['auc']:.3f}", "ROC-AUC, logistic model", "risk_backtest.json", B, ("0.77",)),
        H("auc_gain", f"{ps['auc_gain_vs_size']['logistic']['mean']:+.3f} [{ps['auc_gain_vs_size']['logistic']['ci'][0]:.3f}, "
          f"{ps['auc_gain_vs_size']['logistic']['ci'][1]:.3f}]", "model AUC gain over size, 95% CI (not significant)",
          "risk_backtest.json", B),
        H("ap_gain", f"{ps['ap_gain_vs_size']['logistic']['mean']:+.3f} [{ps['ap_gain_vs_size']['logistic']['ci'][0]:.3f}, "
          f"{ps['ap_gain_vs_size']['logistic']['ci'][1]:.3f}]", "model average-precision gain over size, 95% CI",
          "risk_backtest.json", B),
        H("or_conc", f"{adj['flag_concentrated']['odds_ratio_raw']:.2f} → {adj['flag_concentrated']['odds_ratio_size_adjusted']:.2f}",
          "concentrated: raw → size-adjusted odds ratio", "risk_backtest.json", B),
        H("or_sole", f"{adj['flag_sole_source']['odds_ratio_size_adjusted']:.2f} "
          f"[{adj['flag_sole_source']['adjusted_ci95'][0]:.2f}, {adj['flag_sole_source']['adjusted_ci95'][1]:.2f}]",
          "sole-source size-adjusted odds ratio", "risk_backtest.json", B),
        H("or_geo", f"{adj['flag_geo_exposed']['odds_ratio_size_adjusted']:.2f} → {adj_x['flag_geo_exposed']['odds_ratio_size_adjusted']:.2f}",
          "geo-exposed size-adjusted odds ratio → without the Japan office cluster", "risk_backtest.json", B),
        H("cluster_share", f"{min(cl) * 100:.0f}%-{max(cl) * 100:.0f}%", "share of each year's events from one Navy "
          "office in Japan", "risk_backtest.json", B),
        H("auc_prior", f"{s['baseline_prior_events']['auc']:.3f}", "ROC-AUC, prior terminations only",
          "risk_backtest.json", B),
        H("lk_features", f"{len(bt['features'])} supplier-year features", "inputs to the termination-likelihood model",
          "risk_backtest.json", B),
        H("lk_split", f"trained on FY{min(ps['train_years'])}-FY{max(ps['train_years'])}, tested on FY{ps['test_year']} "
                      f"features against FY{ps['test_year'] + 1} outcomes", "likelihood model time split",
          "risk_backtest.json", B),
        H("lk_n_train", f"{ps['n_train']:,}", "training supplier-years", "risk_backtest.json", B),
        H("lk_train_pos", f"{ps['n_train_pos']}", "training supplier-years with an event", "risk_backtest.json", B),
        H("lk_base_rate", pct(ps["test_base_rate"]), "test base rate (share of supplier-years with an event)",
          "risk_backtest.json", B),
        H("top5_k", f"{s['logistic']['top_5pct']['k']}", "suppliers in the top 5% of the test year",
          "risk_backtest.json", B),
        H("top5_model", pct(s["logistic"]["top_5pct"]["precision"]), "top-5% precision, logistic model",
          "risk_backtest.json", B),
        H("top5_size", pct(s["baseline_size"]["top_5pct"]["precision"]), "top-5% precision, contract count only",
          "risk_backtest.json", B),
        H("top5_prior", pct(s["baseline_prior_events"]["top_5pct"]["precision"]),
          "top-5% precision, prior terminations only", "risk_backtest.json", B),
        H("top5_flags", pct(s["current_flags"]["top_5pct"]["precision"]), "top-5% precision, current flags",
          "risk_backtest.json", B),
        H("conv_share", pct(conv / max(n_ev, 1), 0), "termination/cancellation actions FY2022-FY2025 that were "
          "terminations for convenience", "risk_backtest.json", B),
        H("outlook_scored", f"{bt['outlook']['n_suppliers_scored']:,}", f"suppliers scored for "
          f"FY{bt['outlook']['outlook_fy']}", "risk_backtest.json", B),
        H("outlook_n", f"{bt['outlook']['n_excess_ge_2_and_obligations_ge_10m']}", "FY2026 suppliers with >= 2x the "
          "size-predicted likelihood and >= $10M at stake", "risk_backtest.json", B),
        H("v1_opus", f"{reps('golden/opus_tools')[0]['answerable_correct']}", "v1: Opus answerable correct",
          "comparison.json", A),
        H("v1_opus_ref", f"{reps('golden/opus_tools')[0]['refusals_correct']}", "v1: Opus correct refusals",
          "comparison.json", A),
        H("v1_opus_fab", f"{ev['n_fabricated_citations']}", "v1: Opus fabricated IDs", "eval_results.json", A),
        H("v1_haiku", runs("golden/haiku_tools", "answerable_correct"), "v1: Haiku answerable correct, three runs",
          "comparison.json", A),
        H("v1_haiku_guard", runs("golden/haiku_tools_guard", "answerable_correct"),
          "v1: Haiku + guard answerable correct, three runs", "comparison.json", A),
        H("v1_haiku_fab", f"{fab('golden/haiku_tools')} (guard: {fab('golden/haiku_tools_guard')})",
          "v1: Haiku citation IDs not found, per run (real supplier IDs cited under the parent: prefix)",
          "comparison.json", A),
        H("draft_haiku", ", ".join(r["answerable_correct"] for r in draft["challenge/haiku_tools"]["reps"]),
          "first challenge draft: Haiku answerable correct (saturated; set revised)", "comparison_challenge_draft.json", A),
        H("ch_opus", f"{reps('challenge/opus_tools')[0]['answerable_correct']}", "challenge: Opus answerable correct",
          "comparison.json", A),
        H("ch_opus_traps", f"{reps('challenge/opus_tools')[0]['refusals_correct']}", "challenge: Opus traps declined",
          "comparison.json", A),
        H("ch_haiku", runs("challenge/haiku_tools", "answerable_correct"), "challenge: Haiku answerable, three runs",
          "comparison.json", A),
        H("ch_haiku_traps", runs("challenge/haiku_tools", "refusals_correct"), "challenge: Haiku traps declined",
          "comparison.json", A),
        H("ch_guard_traps", runs("challenge/haiku_tools_guard", "refusals_correct"),
          "challenge: Haiku + guard traps declined", "comparison.json", A),
        H("ch_notools", f"{reps('challenge/opus_no_tools')[0]['answerable_correct']}",
          "challenge: Opus without tools, answerable", "comparison.json", A),
        H("nm_haiku", ", ".join(f"{x}/8" for x in nm("challenge/haiku_tools")), "near-miss traps caught, Haiku",
          "comparison.json", A),
        H("nm_guard", ", ".join(f"{x}/8" for x in nm("challenge/haiku_tools_guard")),
          "near-miss traps caught, Haiku + guard", "comparison.json", A),
        H("nm_opus", ", ".join(f"{x}/8" for x in nm("challenge/opus_tools")),
          "near-miss traps caught, Opus (three runs of the trap template)", "comparison.json", A),
        H("cost_opus", f"${opus_cost:.3f}", "Opus cost per challenge question", "comparison.json", A),
        H("cost_ratio", f"1/{round(opus_cost / haiku_cost)}", "Haiku cost relative to Opus per challenge question",
          "comparison.json", A),
        H("guard_replay", f"{rp['flagged']} of {rp['calls']}", "logged Opus resolver calls on answerable questions "
          "where the guard fires", "guard_eval.json", A),
        H("judge_blatant", f"{round(jv['agreement'] * jv['n_cases'])}/{jv['n_cases']}",
          "LLM judge agreement, blatant corruptions", "judge_validation.json", T),
        H("judge_subtle", f"{round(js['agreement'] * js['n_cases'])}/{js['n_cases']}",
          "LLM judge agreement, subtle corruptions in real agent prose", "judge_validation_subtle.json", T),
        H("spend", f"${spend_total():.2f}", "total Anthropic API spend (cap $10)", "logs/spend_ledger.jsonl", T),
    ]
    if n_tests is not None:
        h.append(H("tests", f"{n_tests} tests", "pytest tests collected (`make test`)", "pytest --collect-only", T))
    return h


def load() -> dict[str, str]:
    """The committed headline values ({key: exact text}); the app and the site quote these."""
    return {h["key"]: h["text"] for h in json.loads(OUT_JSON.read_text())}


def render(h: list[H]) -> str:
    out = ["# Headline numbers", "",
           "Generated by `make headline` from the result JSONs; do not edit by hand. README, FINAL_REPORT, the site,",
           "the brief, the demo script and the interview walkthrough quote these strings, and `tests/test_headline.py`",
           "fails if they disagree.", ""]
    for sec in dict.fromkeys(x.section for x in h):
        out += [f"## {sec}", "", "| key | value | what | source |", "|---|---|---|---|"]
        out += [f"| `{x.key}` | {x.text} | {x.label} | `{x.source}` |" for x in h if x.section == sec]
        out.append("")
    return "\n".join(out)


def main() -> None:
    h = build(tests_collected())
    OUT_JSON.write_text(json.dumps([asdict(x) for x in h], indent=2, ensure_ascii=False) + "\n")
    OUT_MD.write_text(render(h))
    print(f"wrote {OUT_MD} ({len(h)} numbers)")


if __name__ == "__main__":
    main()
