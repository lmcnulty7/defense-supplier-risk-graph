"""reports/risk_backtest.md from reports/risk_backtest.json (written by `make backtest`)."""

from __future__ import annotations

import pandas as pd

from supplier_graph.config import REPORTS

NAMES = {"baseline_size": "Size only (contract count)", "baseline_prior_events": "Prior-year terminations only",
         "current_flags": "Current screen (number of flags, untrained)", "logistic": "Logistic model (primary)",
         "gbm": "Gradient-boosted trees"}


def _ci(x: list) -> str:
    return f"[{x[0]:.3f}, {x[1]:.3f}]"


def _score_table(split: dict) -> str:
    rows = ["| ranking | ROC-AUC [95% CI] | avg precision [95% CI] | precision in top 5% | lift | recall in top 5% |",
            "|---|---|---|---|---|---|"]
    for k, v in split["scores"].items():
        t = v["top_5pct"]
        rows.append(f"| {NAMES[k]} | {v['auc']:.3f} {_ci(v['auc_ci95'])} | {v['ap']:.3f} {_ci(v['ap_ci95'])} | "
                    f"{t['precision']:.3f} | {t['lift']:.2f}x | {t['recall']:.3f} |")
    return "\n".join(rows)


def _gain(split: dict, k: str) -> str:
    a, p = split["auc_gain_vs_size"][k], split["ap_gain_vs_size"][k]
    return (f"AUC {a['mean']:+.3f} {_ci(a['ci'])}; average precision {p['mean']:+.3f} {_ci(p['ci'])} "
            f"(share of bootstrap draws with no gain: {p['p_le_0']:.3f})")


def _flags(eff: dict) -> str:
    rows = ["| flag | supplier-years flagged | odds ratio, raw [95% CI] | odds ratio, size-adjusted [95% CI] |",
            "|---|---|---|---|"]
    for f, v in eff.items():
        rows.append(f"| {f.removeprefix('flag_')} | {v['n_flagged_supplier_years']:,} of {v['n_supplier_years']:,} | "
                    f"{v['odds_ratio_raw']:.2f} {_ci(v['raw_ci95'])} | "
                    f"{v['odds_ratio_size_adjusted']:.2f} {_ci(v['adjusted_ci95'])} |")
    return "\n".join(rows)


def _feed_policy(fp: dict) -> str:
    f = fp.get("feed", {})
    lines = [f"the MENA feed (schema {f.get('schema_version')}, label `{f.get('label')}`, weeks "
             f"{f.get('week_start_min')} to {f.get('week_start_max')}) reports `skill_status = {fp['skill_status']}`. "
             f"Rule: {fp['rule']}"]
    for name, g in fp.get("candidates", {}).items():
        lines.append(f"  - `{name}`: {'**adopted**' if g['adopted'] else 'not adopted'}; {g.get('reason', '')}.")
    lines.append(f"  Likelihood model inputs from the feed: {', '.join(fp['adopted']) or 'none'}. Geographic exposure "
                 "remains an impact attribute (the geo-exposed flag).")
    return "\n".join(lines)


def write_report(res: dict, out: pd.DataFrame) -> None:
    p, e, sx = res["primary_split"], res["earlier_split"], res["sensitivity_excluding_cluster"]
    ev = pd.DataFrame(res["event_counts_by_year"])
    ev_tab = ev.pivot_table(index="fy", columns="act", values="n", fill_value=0).astype(int)
    ev_rows = "\n".join(f"| FY{fy} | " + " | ".join(str(ev_tab.loc[fy].get(c, 0)) for c in ("E", "X", "F", "N")) + " |"
                        for fy in ev_tab.index)
    coef = sorted(p["logistic_coefficients_std"].items(), key=lambda kv: -abs(kv[1]))
    coef_rows = "\n".join(f"| {f} | {c:+.3f} |" for f, c in coef)
    cal = "\n".join(f"| {i + 1} | {b['n']:,} | {b['mean_predicted']:.4f} | {b['observed_rate']:.4f} |"
                    for i, b in enumerate(p["calibration_logistic"]))
    top = "\n".join(f"| {i + 1} | `{t['uei']}` | {t['p']:.3f} | {t['n_awards']} | {'yes' if t['had_event_next_fy'] else 'no'} | "
                    f"{', '.join('`' + a + '`' for a in t['event_award_ids'][:2]) or '-'} |"
                    for i, t in enumerate(p["top10_test_predictions"]))
    o = res["outlook"]
    ex = out[(out.excess_vs_size >= 2) & (out.prime_obligations >= 1e7)].sort_values(
        ["excess_vs_size", "supplier_id"], ascending=[False, True]).head(10)
    ex_rows = "\n".join(f"| `{r.supplier_id}` | {r.name} | {r.p_disruption_next_fy:.3f} | {r.excess_vs_size:.1f}x | "
                        f"${r.prime_obligations / 1e6:,.1f}M | {r.n_dependent_offices} |" for r in ex.itertuples())
    cs = res["cluster_offices_event_share"]
    lo, hi = min(c["cluster_share"] for c in cs), max(c["cluster_share"] for c in cs)
    conv = sum(c["cluster_convenience"] for c in cs) / max(sum(c["cluster_events"] for c in cs), 1)
    hit_top10 = sum(t["had_event_next_fy"] for t in p["top10_test_predictions"])
    text = f"""# Risk backtest: do the screens anticipate next year's contract disruptions?

Generated {res['generated_at_utc']} by `make backtest`. Every number below comes from `reports/risk_backtest.json`.
Portfolio project on public data; the customer persona is fictional; not used by or built for the DoD or any company.

## Bottom line

1. **The existing flags measure impact, not likelihood.** After adjusting for contract volume, the *concentrated*
   flag has no association with next-year disruptions (odds ratio {res['size_adjusted_flag_effects']['flag_concentrated']['odds_ratio_size_adjusted']:.2f}
   {_ci(res['size_adjusted_flag_effects']['flag_concentrated']['adjusted_ci95'])}), and *sole-source* suppliers are
   **less** likely to see one ({res['size_adjusted_flag_effects']['flag_sole_source']['odds_ratio_size_adjusted']:.2f}
   {_ci(res['size_adjusted_flag_effects']['flag_sole_source']['adjusted_ci95'])}): long-running OEM sustainment
   relationships are stable. The *geo-exposed* association ({res['size_adjusted_flag_effects']['flag_geo_exposed']['odds_ratio_size_adjusted']:.2f})
   disappears ({sx['size_adjusted_flag_effects']['flag_geo_exposed']['odds_ratio_size_adjusted']:.2f}
   {_ci(sx['size_adjusted_flag_effects']['flag_geo_exposed']['adjusted_ci95'])}) once one Navy office in Japan is
   excluded. As a likelihood ranking, the current screen scores ROC-AUC {p['scores']['current_flags']['auc']:.3f},
   well below contract count alone ({p['scores']['baseline_size']['auc']:.3f}).
2. **Most of what is predictable is exposure.** A supplier with more open contracts has more chances of one being
   terminated: contract count alone reaches ROC-AUC {p['scores']['baseline_size']['auc']:.3f} on FY2025 outcomes.
3. **A simple model improves the top of the list, not the overall ranking.** Gain over size: {_gain(p, 'logistic')}.
   The earlier split agrees ({_gain(e, 'logistic')}), and so does a run with the Japan office cluster removed
   ({_gain(sx, 'logistic')}). In the top 5% the model's precision is {p['scores']['logistic']['top_5pct']['precision']:.3f}
   versus a {p['test_base_rate']:.3f} base rate ({p['scores']['logistic']['top_5pct']['lift']:.2f}x lift; size alone
   {p['scores']['baseline_size']['top_5pct']['lift']:.2f}x). {hit_top10} of its 10 highest-ranked suppliers had an event.
4. **Disruptions cluster by buying office.** NAVSUP Fleet Logistics Center Yokosuka (codes N68246, then N62649)
   accounts for {lo:.0%}-{hi:.0%} of each year's events across FY2022-FY2025, and {conv:.0%} of its events are
   terminations for convenience. The label partly measures how an office manages orders, not only supplier health.

**Product decision:** keep the flags as *impact* screens and show likelihood on a separate axis. The analyst's
triage view is impact (what depends on the supplier, from the graph) against likelihood (this model), with an
"excess over size" ratio to surface suppliers riskier than their contract volume explains.

## Design

* **Outcome (from the data, not annotated):** supplier-year y = 1 if any of the supplier's contracts has a
  transaction in fiscal year t+1 with action type E (terminate for default), X (terminate for cause), F (terminate
  for convenience) or N (legal contract cancellation). These are *disruption events seen by the buying office*,
  not proof of supplier fault.
* **Data:** {res['source']['files']['transactions']['rows']:,} DoD PSC-J contract transactions, FY2022-FY2025
  (USAspending `/api/v2/download/transactions/`, request `{res['source']['request_key']}`, pulled
  {res['source']['pulled_at_utc'][:10]}, SHA-256 in the cache `meta.json`).
* **Leakage control:** features for year t use only year-t transactions. Train on FY2022-FY2023 features
  (FY2023-FY2024 outcomes); test on FY2024 features (FY2025 outcomes). Earlier split: train FY2022, test FY2023.
* **Baselines:** contract count (size), prior-year terminations, and the current three-flag screen (untrained).
  The logistic model was chosen as primary before results were seen; boosted trees are reported for comparison.
* **Country risk feed policy:** {_feed_policy(res['feed_policy'])}
* **Uncertainty:** {1000} bootstrap resamples of the test year; gains are paired (same resample for both rankings).

### Event counts (transactions)

| year | E default | X cause | F convenience | N cancellation |
|---|---|---|---|---|
{ev_rows}

Default and cause together are too rare to model on their own; they are included in the combined outcome.

## Results

### Primary split: train FY2022-FY2023, test FY2024 features -> FY2025 outcomes

{p['n_test']:,} supplier-years, {p['n_test_pos']} with an event (base rate {p['test_base_rate']:.3f}).

{_score_table(p)}

### Earlier split: train FY2022, test FY2023 features -> FY2024 outcomes

{e['n_test']:,} supplier-years, {e['n_test_pos']} with an event (base rate {e['test_base_rate']:.3f}).

{_score_table(e)}

### Sensitivity: events at NAVSUP FLC Yokosuka removed from the outcome (primary split)

{sx['n_test_pos']} test positives remain. Logistic ROC-AUC {sx['scores']['logistic']['auc']:.3f}, average precision
{sx['scores']['logistic']['ap']:.3f}; size only {sx['scores']['baseline_size']['auc']:.3f} / {sx['scores']['baseline_size']['ap']:.3f}.

### Do the flags predict likelihood? (all labelled supplier-years pooled)

{_flags(res['size_adjusted_flag_effects'])}

Same, with the Japan office cluster removed from the outcome:

{_flags(sx['size_adjusted_flag_effects'])}

### Logistic model coefficients (standardized features, primary split)

| feature | coefficient |
|---|---|
{coef_rows}

### Calibration (logistic, FY2025 outcomes, quintiles of predicted probability)

| quintile | n | mean predicted | observed rate |
|---|---|---|---|
{cal}

### Ten highest-ranked suppliers in the test year, and what happened

| rank | supplier UEI | predicted p | contracts active | event in FY2025? | example terminated award IDs |
|---|---|---|---|---|---|
{top}

## FY{o['outlook_fy']} outlook (`supplier_disruption_score` table, `reports/risk_watchlist.csv`)

Trained on every labelled year and scored on FY{o['scored_on_fy']} features for {o['n_suppliers_scored']:,} suppliers that are
also in the graph. `priority_score` = probability x prime obligations at stake: a triage ranking, not an expected
loss, and it favours large primes because their probability scales with contract count. `excess_vs_size` divides
the model probability by the size-only probability. {o['n_excess_ge_2_and_obligations_ge_10m']} suppliers with at
least $10M in prime obligations have a ratio of 2 or more. The ten highest:

| supplier UEI | name | p (FY{o['outlook_fy']}) | excess over size | prime obligations | dependent offices |
|---|---|---|---|---|---|
{ex_rows}

## Limits

* **The label is a proxy.** Terminations for convenience dominate and can reflect changed requirements, budget
  moves or administrative clean-up, not supplier distress. A real deployment would add CPARS/FAPIIS past
  performance, SAM exclusions and financial data, none of which are in this public scope.
* **One office drives a large share of events,** so office practice is part of what is being predicted.
* **Scope is PSC J only.** A supplier terminated on a non-maintenance contract does not count.
* **Three labelled years.** The splits are small (about 100-150 positives per test year), hence the wide CIs.
* **No causal claim.** "Sole-source suppliers have fewer terminations" is an association, consistent with stable
  OEM sustainment relationships; it does not mean sole-sourcing reduces risk.
"""
    (REPORTS / "risk_backtest.md").write_text(text)
