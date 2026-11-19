"""Time-split backtest: do the risk screens, or a simple model, anticipate next year's contract disruptions?

Question: the knowledge graph flags suppliers as concentrated, sole-sourced or geographically exposed. Those flags
describe *impact* (how much depends on a supplier). Do they, or anything else in public data, also say something
about *likelihood*: which suppliers will have contracts terminated or cancelled next fiscal year?

Outcome label (from the source data, no annotation): supplier-year y_{t+1} = 1 if any of the supplier's contracts
has a transaction in fiscal year t+1 with action_type_code
    E = terminate for default, X = terminate for cause, F = terminate for convenience, N = legal contract cancellation.
These are *disruption events seen from the buying office*, not proof of supplier fault: terminations for convenience
dominate and can reflect a changed requirement. Default/cause alone are too rare to model (counted in the report).

Unit and leakage control: one row per (supplier UEI, fiscal year t) for suppliers with any transaction in t.
Every feature uses only transactions dated in t (one-year window, so FY2022 rows are complete). Train on
t = FY2022 and FY2023 (outcomes FY2023, FY2024); test on t = FY2024 (outcomes FY2025). A second, earlier split
(train FY2022, test FY2023) checks stability. Nothing about the test year's outcome enters training.

Baselines: size/exposure only, prior-year terminations only, and the current screen (number of flags, no training).
Models: L2 logistic regression (primary, chosen before seeing results) and gradient-boosted trees (comparison).
Metrics: ROC-AUC and average precision with bootstrap 95% CIs, paired bootstrap for the AUC gain over the size
baseline, precision/lift in the top 5%, Brier score and a calibration table.

Writes reports/risk_backtest.json, reports/risk_backtest.md, reports/risk_watchlist.csv and the DuckDB table
`supplier_disruption_score` (FY2026 outlook: trained on all labelled years, scored on FY2025 features).
Run: `make backtest`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from supplier_graph.config import REPORTS
from supplier_graph.graph.schema import CONCENTRATION_MIN_OFFICE, CONCENTRATION_SHARE
from supplier_graph.ingest.risk_feed import likelihood_allowed
from supplier_graph.ingest.transactions import build_request, cache_dir_for
from supplier_graph.stage import connect

EVENT_CODES = ("E", "X", "F", "N")
SOLE_SOURCE_CODES = ("B", "C", "G", "NDO")
SEED = 20261118
N_BOOT = 1000
TOP_FRAC = 0.05

BASE_FEATURES = [
    "log_n_awards", "log_n_awards_open_next", "log_obligations", "deob_share", "mods_per_award",
    "log_change_orders", "log_prior_events", "any_prior_event", "sole_source_share", "single_offer_share",
    "small_business", "log_n_offices", "max_office_share", "foreign_share", "office_event_rate",
]
# Candidate inputs from the MENA risk feed:
#   feed_exposure      the supplier-year has work in a feed-covered country (a static region membership)
#   feed_p_escalation  obligation-weighted p_escalation of those countries, POINT IN TIME: for feature year t only
#                      feed rows with data_cutoff on or before the end of FY t (30 Sep) and week_start no more than
#                      PIT_MAX_AGE_WEEKS earlier. Never a later forecast: that would leak the future into the test.
# Adoption rule, fixed before the schema-2.0.0 run (2026-11-19). A candidate enters the likelihood model only if
#   1. the feed's skill_status is beats_base_rate or beats_country_rate (risk_feed.likelihood_allowed);
#   2. it is evaluable: a point-in-time value exists for >= PIT_MIN_COVERAGE of the feed-exposed supplier-years in
#      every train and test year of the primary split;
#   3. on the primary split, current model + candidate vs the current model: the paired-bootstrap average-precision
#      gain has a 95% CI lower bound > 0, and the mean AUC gain is >= 0.
# Otherwise country risk stays context, and geographic exposure stays an impact attribute (flag_geo_exposed).
FEED_FEATURES = ["feed_exposure", "feed_p_escalation"]
PIT_MAX_AGE_WEEKS = 26
PIT_MIN_COVERAGE = 0.8
FEATURES = list(BASE_FEATURES)  # set by run() from feed_gate()


def eligible_feed_features(skill_status: str | None) -> list[str]:
    """Rule 1: candidates that may be tested at all."""
    return list(FEED_FEATURES) if likelihood_allowed(skill_status) else []


def likelihood_features(skill_status: str | None, adopted: list[str] | tuple = ()) -> list[str]:
    return BASE_FEATURES + [f for f in eligible_feed_features(skill_status) if f in adopted]
# NAVSUP Fleet Logistics Center Yokosuka (code changed N68246 -> N62649 in FY2025): 16-38% of each year's events.
CLUSTER_OFFICES = ("N68246", "N62649")
FLAG_COLS = ["flag_concentrated", "flag_sole_source", "flag_geo_exposed"]


def tx_path() -> str:
    d = cache_dir_for(build_request())
    if not (d / "meta.json").exists():
        raise SystemExit("transactions not pulled; run `make pull-tx`")
    return str(d / "transactions.parquet")


def supplier_years(con, exclude_offices: tuple[str, ...] = ()) -> pd.DataFrame:
    """Feature table: one row per (supplier, fiscal year) from that year's transactions only.
    `exclude_offices` drops those offices' events from the *outcome* only (sensitivity check)."""
    feed = [c for (c,) in con.execute("SELECT DISTINCT country_iso3 FROM risk_feed").fetchall()]
    con.execute(f"""
    CREATE OR REPLACE TEMP VIEW tx AS
    SELECT recipient_uei AS uei, contract_award_unique_key AS award_id, awarding_office_code AS office,
           CAST(action_date_fiscal_year AS INTEGER) AS fy, action_type_code AS act,
           coalesce(TRY_CAST(federal_action_obligation AS DOUBLE), 0) AS obl,
           extent_competed_code AS ec, TRY_CAST(number_of_offers_received AS INTEGER) AS offers,
           contracting_officers_determination_of_business_size_code AS size,
           primary_place_of_performance_country_code AS pop,
           TRY_CAST(period_of_performance_current_end_date AS DATE) AS pop_end
    FROM read_parquet('{tx_path()}') WHERE recipient_uei IS NOT NULL
    """)
    feed_list = ", ".join(f"'{c}'" for c in feed) or "''"
    con.execute(f"""
    CREATE OR REPLACE TEMP VIEW feed_pit AS
    WITH yrs AS (SELECT DISTINCT fy FROM tx), cand AS (
      SELECT y.fy, r.country_iso3 AS pop, r.p_escalation, r.week_start
      FROM yrs y JOIN risk_feed r
        ON r.data_cutoff <= make_timestamptz(y.fy, 9, 30, 23, 59, 59, 'UTC')
       AND r.week_start >= make_date(y.fy, 9, 30) - INTERVAL {PIT_MAX_AGE_WEEKS * 7} DAY
    )
    SELECT fy, pop, p_escalation FROM cand
    QUALIFY row_number() OVER (PARTITION BY fy, pop ORDER BY week_start DESC) = 1
    """)
    pit = con.execute(f"""
      SELECT t.uei, t.fy, sum(greatest(t.obl, 0) * f.p_escalation) / nullif(sum(greatest(t.obl, 0)), 0) AS pit_p,
             sum(greatest(t.obl, 0)) FILTER (WHERE f.p_escalation IS NOT NULL) AS pit_pos
      FROM tx t LEFT JOIN feed_pit f USING (fy, pop)
      WHERE t.pop IN ({feed_list}) GROUP BY ALL""").df()
    excl = ", ".join(f"'{o}'" for o in exclude_offices) or "'__none__'"
    df = con.execute(f"""
    WITH base AS (
      SELECT uei, fy,
             count(DISTINCT award_id) AS n_awards,
             count(DISTINCT award_id) FILTER (WHERE pop_end >= make_date(fy, 10, 1)) AS n_awards_open_next,
             sum(greatest(obl, 0)) AS pos, sum(greatest(-obl, 0)) AS neg, count(*) AS n_tx,
             count(*) FILTER (WHERE act = 'D') AS change_orders,
             count(*) FILTER (WHERE act IN {EVENT_CODES}) AS prior_events,
             sum(greatest(obl, 0)) FILTER (WHERE ec IN {SOLE_SOURCE_CODES}) AS ss_pos,
             sum(greatest(obl, 0)) FILTER (WHERE offers = 1) AS single_pos,
             avg(CASE WHEN size = 'S' THEN 1.0 ELSE 0.0 END) AS small_frac,
             count(DISTINCT office) AS n_offices,
             sum(greatest(obl, 0)) FILTER (WHERE pop IS NOT NULL AND pop <> 'USA') AS foreign_pos,
             sum(greatest(obl, 0)) FILTER (WHERE pop IN ({feed_list})) AS feed_pos
      FROM tx GROUP BY uei, fy
    ), office_tot AS (
      SELECT office, fy, sum(greatest(obl, 0)) AS tot FROM tx WHERE office IS NOT NULL GROUP BY ALL
    ), conc AS (
      SELECT t.uei, t.fy, max(sum_pos / o.tot) AS max_office_share
      FROM (SELECT uei, fy, office, sum(greatest(obl, 0)) AS sum_pos FROM tx WHERE office IS NOT NULL GROUP BY ALL) t
      JOIN office_tot o USING (office, fy) WHERE o.tot >= {CONCENTRATION_MIN_OFFICE} GROUP BY ALL
    ), office_rate AS (
      -- buying-office practice: the office's event rate in year t, leave-one-out (excludes this supplier's rows)
      SELECT so.uei, so.fy,
             sum(so.n_s * (os.ev - so.ev_s) / (os.n - so.n_s)) / sum(so.n_s) AS office_event_rate
      FROM (SELECT uei, office, fy, count(*) AS n_s, count(*) FILTER (WHERE act IN {EVENT_CODES}) AS ev_s
            FROM tx WHERE office IS NOT NULL GROUP BY ALL) so
      JOIN (SELECT office, fy, count(*) AS n, count(*) FILTER (WHERE act IN {EVENT_CODES}) AS ev
            FROM tx WHERE office IS NOT NULL GROUP BY ALL) os USING (office, fy)
      WHERE os.n > so.n_s GROUP BY ALL
    ), outcome AS (
      SELECT uei, fy - 1 AS fy, count(*) AS next_events,
             count(*) FILTER (WHERE act IN ('E', 'X')) AS next_default_cause,
             list(DISTINCT award_id ORDER BY award_id)[:5] AS next_event_awards
      FROM tx WHERE act IN {EVENT_CODES} AND coalesce(office, '') NOT IN ({excl}) GROUP BY ALL
    )
    SELECT b.*, coalesce(c.max_office_share, 0) AS max_office_share,
           coalesce(r.office_event_rate, 0) AS office_event_rate,
           coalesce(o.next_events, 0) AS next_events, coalesce(o.next_default_cause, 0) AS next_default_cause,
           o.next_event_awards
    FROM base b LEFT JOIN conc c USING (uei, fy) LEFT JOIN outcome o USING (uei, fy)
    LEFT JOIN office_rate r USING (uei, fy)
    ORDER BY fy, uei
    """).df()
    pos = df["pos"].replace(0, np.nan)
    df["log_n_awards"] = np.log1p(df["n_awards"])
    df["log_n_awards_open_next"] = np.log1p(df["n_awards_open_next"])
    df["log_obligations"] = np.log1p(df["pos"])
    df["deob_share"] = (df["neg"] / (df["pos"] + df["neg"]).replace(0, np.nan)).fillna(0)
    df["mods_per_award"] = df["n_tx"] / df["n_awards"]
    df["log_change_orders"] = np.log1p(df["change_orders"])
    df["log_prior_events"] = np.log1p(df["prior_events"])
    df["any_prior_event"] = (df["prior_events"] > 0).astype(float)
    df["sole_source_share"] = (df["ss_pos"].fillna(0) / pos).fillna(0)
    df["single_offer_share"] = (df["single_pos"].fillna(0) / pos).fillna(0)
    df["small_business"] = (df["small_frac"] > 0.5).astype(float)
    df["log_n_offices"] = np.log1p(df["n_offices"])
    df["foreign_share"] = (df["foreign_pos"].fillna(0) / pos).fillna(0)
    df["feed_exposure"] = (df["feed_pos"].fillna(0) > 0).astype(float)
    df = df.merge(pit, on=["uei", "fy"], how="left")
    # a value counts only if point-in-time forecasts cover all of the supplier's feed-country obligations
    df["feed_p_available"] = (df["feed_exposure"] == 0) | (df["pit_pos"].fillna(0) >= df["feed_pos"].fillna(0) - 1e-6)
    df["feed_p_escalation"] = np.where(df["feed_exposure"] == 0, 0.0,
                                       np.where(df["feed_p_available"], df["pit_p"], np.nan))
    df["flag_concentrated"] = df["max_office_share"] >= CONCENTRATION_SHARE
    df["flag_sole_source"] = df["sole_source_share"] >= 0.5
    df["flag_geo_exposed"] = (df["foreign_share"] >= 0.25) | (df["feed_exposure"] > 0)
    df["n_flags"] = df[FLAG_COLS].sum(axis=1).astype(float)
    df["y"] = (df["next_events"] > 0).astype(int)
    return df


def _boot(y: np.ndarray, scores: dict[str, np.ndarray], rng: np.random.Generator) -> dict:
    """Bootstrap CIs for AUC / AP per score, and the paired AUC difference vs the size baseline."""
    out: dict = {k: {"auc": [], "ap": []} for k in scores}
    diffs: dict[str, list] = {k: [] for k in scores if k != "baseline_size"}
    ap_diffs: dict[str, list] = {k: [] for k in scores if k != "baseline_size"}
    n = len(y)
    for _ in range(N_BOOT):
        idx = rng.integers(0, n, n)
        yb = y[idx]
        if yb.min() == yb.max():
            continue
        aucs = {k: roc_auc_score(yb, s[idx]) for k, s in scores.items()}
        aps = {k: average_precision_score(yb, s[idx]) for k, s in scores.items()}
        for k in scores:
            out[k]["auc"].append(aucs[k])
            out[k]["ap"].append(aps[k])
        for k in diffs:
            diffs[k].append(aucs[k] - aucs["baseline_size"])
            ap_diffs[k].append(aps[k] - aps["baseline_size"])
    ci = lambda xs: [round(float(np.percentile(xs, 2.5)), 4), round(float(np.percentile(xs, 97.5)), 4)]  # noqa: E731
    return {"ci": {k: {"auc": ci(v["auc"]), "ap": ci(v["ap"])} for k, v in out.items()},
            "auc_gain_vs_size": {k: {"mean": round(float(np.mean(v)), 4), "ci": ci(v),
                                     "p_le_0": round(float(np.mean(np.array(v) <= 0)), 4)} for k, v in diffs.items()},
            "ap_gain_vs_size": {k: {"mean": round(float(np.mean(v)), 4), "ci": ci(v),
                                    "p_le_0": round(float(np.mean(np.array(v) <= 0)), 4)} for k, v in ap_diffs.items()}}


def _top(y: np.ndarray, s: np.ndarray, frac: float = TOP_FRAC) -> dict:
    k = max(int(round(frac * len(y))), 1)
    order = np.argsort(-s, kind="mergesort")[:k]
    prec = float(y[order].mean())
    return {"k": k, "precision": round(prec, 4), "lift": round(prec / float(y.mean()), 2),
            "recall": round(float(y[order].sum() / max(y.sum(), 1)), 4)}


def _calibration(y: np.ndarray, p: np.ndarray, bins: int = 5) -> list[dict]:
    order = np.argsort(p, kind="mergesort")
    out = []
    for chunk in np.array_split(order, bins):
        out.append({"n": int(len(chunk)), "mean_predicted": round(float(p[chunk].mean()), 4),
                    "observed_rate": round(float(y[chunk].mean()), 4)})
    return out


def models() -> dict:
    return {
        "logistic": make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000)),
        "gbm": HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200,
                                              l2_regularization=1.0, random_state=SEED),
    }


def _paired_gain(y: np.ndarray, new: np.ndarray, cur: np.ndarray, rng: np.random.Generator) -> dict:
    d_auc, d_ap = [], []
    n = len(y)
    for _ in range(N_BOOT):
        idx = rng.integers(0, n, n)
        if y[idx].min() == y[idx].max():
            continue
        d_auc.append(roc_auc_score(y[idx], new[idx]) - roc_auc_score(y[idx], cur[idx]))
        d_ap.append(average_precision_score(y[idx], new[idx]) - average_precision_score(y[idx], cur[idx]))
    ci = lambda xs: [round(float(np.percentile(xs, 2.5)), 4), round(float(np.percentile(xs, 97.5)), 4)]  # noqa: E731
    return {"auc_gain": {"mean": round(float(np.mean(d_auc)), 4), "ci": ci(d_auc)},
            "ap_gain": {"mean": round(float(np.mean(d_ap)), 4), "ci": ci(d_ap)}}


def feed_gate(df: pd.DataFrame, skill_status: str | None, train_years: list[int], test_year: int) -> dict:
    """Apply the adoption rule (see FEED_FEATURES) to each feed-derived candidate. Own RNG stream, so the main
    results' bootstrap draws do not depend on whether the gate ran."""
    rng = np.random.default_rng(SEED + 1)
    years = [*train_years, test_year]
    out: dict = {}
    for f in FEED_FEATURES:
        r: dict = {"adopted": False}
        out[f] = r
        if f not in eligible_feed_features(skill_status):
            r["reason"] = f"feed skill_status {skill_status!r} is not beats_base_rate or beats_country_rate"
            continue
        exp = df[df.fy.isin(years) & (df.feed_exposure > 0)]
        cov = {str(y): round(float(df.loc[(df.fy == y) & (df.feed_exposure > 0), f].notna().mean()), 4)
               if ((df.fy == y) & (df.feed_exposure > 0)).any() else None for y in years}
        r["coverage_feed_exposed_supplier_years"] = cov
        r["n_feed_exposed_supplier_years"] = int(len(exp))
        if any(c is None or c < PIT_MIN_COVERAGE for c in cov.values()):
            r["reason"] = (f"not evaluable: point-in-time values cover less than {PIT_MIN_COVERAGE:.0%} of feed-exposed "
                           f"supplier-years in some train/test year (coverage {cov})")
            continue
        tr, te = df[df.fy.isin(train_years)], df[df.fy == test_year]
        fill = float(tr.loc[tr.feed_exposure > 0, f].median()) if f == "feed_p_escalation" else 0.0
        trf, tef = tr.assign(**{f: tr[f].fillna(fill)}), te.assign(**{f: te[f].fillna(fill)})
        cur = models()["logistic"].fit(tr[BASE_FEATURES], tr["y"]).predict_proba(te[BASE_FEATURES])[:, 1]
        new = models()["logistic"].fit(trf[BASE_FEATURES + [f]], trf["y"]).predict_proba(tef[BASE_FEATURES + [f]])[:, 1]
        y = te["y"].to_numpy()
        r.update({"auc_current": round(float(roc_auc_score(y, cur)), 4), "auc_with": round(float(roc_auc_score(y, new)), 4),
                  "ap_current": round(float(average_precision_score(y, cur)), 4),
                  "ap_with": round(float(average_precision_score(y, new)), 4), **_paired_gain(y, new, cur, rng)})
        r["adopted"] = r["ap_gain"]["ci"][0] > 0 and r["auc_gain"]["mean"] >= 0
        r["reason"] = ("improves the current model (AP gain CI excludes 0, AUC not worse)" if r["adopted"] else
                       f"does not improve the current model: AP gain {r['ap_gain']['mean']:+.4f} "
                       f"{r['ap_gain']['ci']}, AUC gain {r['auc_gain']['mean']:+.4f}")
    return out


def evaluate_split(df: pd.DataFrame, train_years: list[int], test_year: int, rng: np.random.Generator) -> dict:
    tr, te = df[df.fy.isin(train_years)], df[df.fy == test_year]
    y_tr, y_te = tr["y"].to_numpy(), te["y"].to_numpy()
    scores: dict[str, np.ndarray] = {}
    fitted = {}
    base_size = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))
    base_size.fit(tr[["log_n_awards", "log_n_awards_open_next"]], y_tr)
    scores["baseline_size"] = base_size.predict_proba(te[["log_n_awards", "log_n_awards_open_next"]])[:, 1]
    base_prior = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))
    base_prior.fit(tr[["log_prior_events"]], y_tr)
    scores["baseline_prior_events"] = base_prior.predict_proba(te[["log_prior_events"]])[:, 1]
    # current screen: more flags = riskier; tiny size tie-break keeps the ranking deterministic, not informative
    scores["current_flags"] = te["n_flags"].to_numpy() + 1e-9 * te["log_n_awards"].to_numpy()
    for name, mdl in models().items():
        mdl.fit(tr[FEATURES], y_tr)
        fitted[name] = mdl
        scores[name] = mdl.predict_proba(te[FEATURES])[:, 1]
    res: dict = {"train_years": train_years, "test_year": test_year, "n_train": int(len(tr)),
                 "n_train_pos": int(y_tr.sum()), "n_test": int(len(te)), "n_test_pos": int(y_te.sum()),
                 "test_base_rate": round(float(y_te.mean()), 4), "scores": {}}
    boot = _boot(y_te, scores, rng)
    for k, s in scores.items():
        res["scores"][k] = {"auc": round(float(roc_auc_score(y_te, s)), 4),
                            "ap": round(float(average_precision_score(y_te, s)), 4),
                            "auc_ci95": boot["ci"][k]["auc"], "ap_ci95": boot["ci"][k]["ap"],
                            f"top_{int(TOP_FRAC * 100)}pct": _top(y_te, s)}
    res["auc_gain_vs_size"] = boot["auc_gain_vs_size"]
    res["ap_gain_vs_size"] = boot["ap_gain_vs_size"]
    for k in ("logistic", "gbm", "baseline_size"):
        res["scores"][k]["brier"] = round(float(brier_score_loss(y_te, scores[k])), 5)
    res["calibration_logistic"] = _calibration(y_te, scores["logistic"])
    # flag-by-flag event rates in the test year (does each screen separate likelihood at all?)
    res["event_rate_by_flag"] = {
        f: {"flagged": round(float(te.loc[te[f], "y"].mean()), 4) if te[f].any() else None,
            "not_flagged": round(float(te.loc[~te[f], "y"].mean()), 4), "n_flagged": int(te[f].sum())}
        for f in FLAG_COLS}
    lr = fitted["logistic"].named_steps["logisticregression"]
    res["logistic_coefficients_std"] = {f: round(float(c), 4) for f, c in zip(FEATURES, lr.coef_[0], strict=True)}
    te = te.assign(p=scores["logistic"])
    hits = te.sort_values("p", ascending=False).head(10)
    res["top10_test_predictions"] = [
        {"uei": r.uei, "p": round(float(r.p), 4), "had_event_next_fy": bool(r.y),
         "event_award_ids": list(r.next_event_awards) if isinstance(r.next_event_awards, list | np.ndarray) else [],
         "n_awards": int(r.n_awards)} for r in hits.itertuples()]
    return res


def size_adjusted_flag_effects(df: pd.DataFrame, rng: np.random.Generator, n_boot: int = 300) -> dict:
    """Odds ratio of each flag for a next-year event, raw and adjusted for size, pooled over labelled years."""
    lab = df[df.y.notna() & (df.fy < df.fy.max())]
    size = ["log_n_awards", "log_n_awards_open_next"]
    out = {}
    for f in FLAG_COLS:
        def fit(d: pd.DataFrame, adjust: bool, f: str = f) -> float:
            cols = ([*size, f] if adjust else [f])
            m = LogisticRegression(C=1e6, max_iter=5000).fit(d[cols].astype(float), d["y"])
            return float(np.exp(m.coef_[0][-1]))
        raw, adj = fit(lab, False), fit(lab, True)
        bs_raw, bs_adj = [], []
        for _ in range(n_boot):
            d = lab.iloc[rng.integers(0, len(lab), len(lab))]
            bs_raw.append(fit(d, False))
            bs_adj.append(fit(d, True))
        pct = lambda xs: [round(float(np.percentile(xs, 2.5)), 3), round(float(np.percentile(xs, 97.5)), 3)]  # noqa: E731
        out[f] = {"odds_ratio_raw": round(raw, 3), "raw_ci95": pct(bs_raw),
                  "odds_ratio_size_adjusted": round(adj, 3), "adjusted_ci95": pct(bs_adj),
                  "n_flagged_supplier_years": int(lab[f].sum()), "n_supplier_years": int(len(lab))}
    return out


def outlook(df: pd.DataFrame, con) -> pd.DataFrame:
    """FY2026 outlook: fit on every labelled year, score FY2025 features, join impact from the graph."""
    last = int(df.fy.max())
    lab = df[df.fy < last]
    mdl = models()["logistic"].fit(lab[FEATURES], lab["y"])
    size = ["log_n_awards", "log_n_awards_open_next"]
    size_mdl = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(lab[size], lab["y"])
    cur = df[df.fy == last].copy()
    cur["p_disruption_next_fy"] = mdl.predict_proba(cur[FEATURES])[:, 1]
    cur["p_size_only"] = size_mdl.predict_proba(cur[size])[:, 1]
    # > 1: riskier than contract volume alone predicts (the part of the score that is not just size)
    cur["excess_vs_size"] = cur["p_disruption_next_fy"] / cur["p_size_only"]
    risk = con.execute("""SELECT r.supplier_id, s.name, r.prime_obligations, r.n_dependent_offices,
                                 r.flag_concentrated, r.flag_sole_source, r.flag_geo_exposed
                          FROM supplier_risk r JOIN suppliers s USING (supplier_id)""").df()
    out = cur[["uei", "p_disruption_next_fy", "p_size_only", "excess_vs_size", "n_awards_open_next",
               "prior_events", "office_event_rate"]].rename(columns={"uei": "supplier_id"})
    out = out.merge(risk, on="supplier_id", how="inner")
    # Triage score: likelihood x what is at stake. Not an expected loss; a ranking for analyst attention.
    out["priority_score"] = out["p_disruption_next_fy"] * out["prime_obligations"].clip(lower=0)
    out["p_rank_pct"] = out["p_disruption_next_fy"].rank(pct=True).round(4)
    out["scored_on_fy"], out["outlook_fy"] = last, last + 1
    return out.sort_values(["priority_score", "supplier_id"], ascending=[False, True]).reset_index(drop=True)


def run() -> dict:
    global FEATURES
    con = connect()
    skill = [r[0] for r in con.execute("SELECT DISTINCT skill_status FROM risk_feed").fetchall()]
    skill_status = skill[0] if len(skill) == 1 else None
    df = supplier_years(con)
    rng = np.random.default_rng(SEED)
    years = sorted(int(x) for x in df.fy.unique())
    labelled = [y for y in years if y < max(years)]  # the last year has no next-year outcome
    gate = feed_gate(df, skill_status, labelled[:-1], labelled[-1])
    FEATURES = likelihood_features(skill_status, [f for f, g in gate.items() if g["adopted"]])
    feed_meta = con.execute("""SELECT any_value(schema_version), any_value(feed_label), min(week_start), max(week_start),
                                      count(*) FROM risk_feed""").fetchone()
    counts = con.execute(f"""SELECT fy, act, count(*) n, count(DISTINCT uei) suppliers
                             FROM tx WHERE act IN {EVENT_CODES} GROUP BY ALL ORDER BY 1, 2""").df()
    cl = ", ".join(f"'{o}'" for o in CLUSTER_OFFICES)
    cluster = con.execute(f"""SELECT fy, count(*) FILTER (WHERE office IN ({cl})) AS cluster_events, count(*) AS events,
                                     count(*) FILTER (WHERE office IN ({cl}) AND act = 'F') AS cluster_convenience
                              FROM tx WHERE act IN {EVENT_CODES} GROUP BY fy ORDER BY fy""").df()
    cluster["cluster_share"] = (cluster.cluster_events / cluster.events).round(4)
    res: dict = {
        "cluster_offices_event_share": cluster.to_dict("records"),
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source": json.loads((cache_dir_for(build_request()) / "meta.json").read_text()),
        "outcome": "any transaction with action_type_code in E (default), X (cause), F (convenience), N (legal "
                   "cancellation) on the supplier's contracts in fiscal year t+1",
        "event_counts_by_year": counts.to_dict("records"),
        "supplier_years": {str(y): {"n": int((df.fy == y).sum()),
                                    "n_pos": int(df.loc[df.fy == y, "y"].sum()) if y in labelled else None}
                           for y in years},
        "features": FEATURES,
        "feed_policy": {"skill_status": skill_status, "feed_features_used": FEATURES != BASE_FEATURES,
                        "adopted": [f for f in FEATURES if f in FEED_FEATURES],
                        "feed": {"schema_version": feed_meta[0], "label": feed_meta[1],
                                 "week_start_min": str(feed_meta[2]), "week_start_max": str(feed_meta[3]),
                                 "rows": int(feed_meta[4])},
                        "candidates": gate,
                        "rule": "a feed-derived input enters the likelihood model only if (1) the feed's skill_status "
                                "is beats_base_rate or beats_country_rate, (2) point-in-time values (data_cutoff on or "
                                f"before the end of the feature year, at most {PIT_MAX_AGE_WEEKS} weeks old) cover >= "
                                f"{PIT_MIN_COVERAGE:.0%} of feed-exposed supplier-years in every train and test year, "
                                "and (3) adding it to the current model gives a paired-bootstrap AP gain with 95% CI "
                                "above 0 and a non-negative AUC gain on the primary split. Fixed before the schema "
                                "2.0.0 run."},
        "primary_split": evaluate_split(df, labelled[:-1], labelled[-1], rng),
        "earlier_split": evaluate_split(df, labelled[:1], labelled[1], rng) if len(labelled) >= 3 else None,
    }
    res["size_adjusted_flag_effects"] = size_adjusted_flag_effects(df, rng)
    df_x = supplier_years(con, exclude_offices=CLUSTER_OFFICES)
    sx = evaluate_split(df_x, labelled[:-1], labelled[-1], rng)
    res["sensitivity_excluding_cluster"] = {
        "excluded_offices": list(CLUSTER_OFFICES), "n_test_pos": sx["n_test_pos"],
        "scores": {k: {m: v[m] for m in ("auc", "auc_ci95", "ap", "ap_ci95")} for k, v in sx["scores"].items()},
        "auc_gain_vs_size": sx["auc_gain_vs_size"], "ap_gain_vs_size": sx["ap_gain_vs_size"],
        "size_adjusted_flag_effects": size_adjusted_flag_effects(df_x, rng)}
    out = outlook(df, con)
    con.register("outlook_df", out)
    con.execute("CREATE OR REPLACE TABLE supplier_disruption_score AS SELECT * FROM outlook_df")
    con.close()
    res["outlook"] = {"scored_on_fy": int(out.scored_on_fy.iloc[0]), "outlook_fy": int(out.outlook_fy.iloc[0]),
                      "n_suppliers_scored": int(len(out)),
                      "n_excess_ge_2_and_obligations_ge_10m": int(((out.excess_vs_size >= 2)
                                                                   & (out.prime_obligations >= 1e7)).sum())}
    REPORTS.mkdir(exist_ok=True)
    cols = ["supplier_id", "name", "p_disruption_next_fy", "excess_vs_size", "prime_obligations", "priority_score",
            "n_dependent_offices", "n_awards_open_next", "prior_events", "flag_concentrated", "flag_sole_source",
            "flag_geo_exposed"]
    out.head(50)[cols].round({"p_disruption_next_fy": 4, "excess_vs_size": 2, "prime_obligations": 2, "priority_score": 2}).to_csv(
        REPORTS / "risk_watchlist.csv", index=False)
    (REPORTS / "risk_backtest.json").write_text(json.dumps(res, indent=2, default=str))
    from supplier_graph.risk.report import write_report
    write_report(res, out)
    p = res["primary_split"]["scores"]
    print(json.dumps({k: {"auc": v["auc"], "auc_ci95": v["auc_ci95"], "ap": v["ap"]} for k, v in p.items()}, indent=2))
    return res


if __name__ == "__main__":
    run()
