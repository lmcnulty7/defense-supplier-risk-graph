"""Score the matcher on the automatic label sets and write reports/entity_resolution.md.

The threshold is tuned on the dev split only (max F1 over the pooled dev pairs) and all
reported numbers come from the held-out test split.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from supplier_graph.config import REPORTS
from supplier_graph.er.labels import REC_COLS, build_labels
from supplier_graph.er.matcher import MatchRecord, initials, score
from supplier_graph.er.normalize import core_name, normalize_name
from supplier_graph.stage import connect

THRESHOLD_FILE = REPORTS / "er_threshold.json"
METRICS_FILE = REPORTS / "er_metrics.json"


def score_pairs(df: pd.DataFrame, acronyms: bool = True) -> pd.Series:
    out = []
    for row in df.itertuples(index=False):
        d = row._asdict()
        a = MatchRecord(**{c: d[f"a_{c}"] for c in REC_COLS})
        b = MatchRecord(**{c: d[f"b_{c}"] for c in REC_COLS})
        out.append(score(a, b, acronyms=acronyms))
    return pd.Series(out, index=df.index)


def prf(y: np.ndarray, pred: np.ndarray) -> dict:
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / (tp + fn) if tp + fn else float("nan")
    f = 2 * p * r / (p + r) if p + r and not np.isnan(p + r) else float("nan")
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn, "n": int(len(y))}


def tune_threshold(dev: pd.DataFrame) -> float:
    best_t, best_f = 0.8, -1.0
    for t in np.arange(0.50, 0.99, 0.01):
        m = prf(dev["label"].to_numpy(), (dev["score"] >= t).astype(int).to_numpy())
        if not np.isnan(m["f1"]) and m["f1"] > best_f:
            best_t, best_f = round(float(t), 2), m["f1"]
    return best_t


def run() -> dict:
    con = connect(read_only=True)
    records = con.execute("SELECT * FROM entity_records ORDER BY record_id").df()
    con.close()
    labels = build_labels(records)
    excluded = labels.attrs.get("excluded_ambiguous_negatives", 0)
    labels["score"] = score_pairs(labels)
    labels["family"] = labels["label_source"].str.split(":").str[0]
    dev, test = labels[labels.split == "dev"], labels[labels.split == "test"]
    t = tune_threshold(dev)
    labels["pred"] = (labels["score"] >= t).astype(int)
    test = labels[labels.split == "test"]

    metrics: dict = {"generated_at_utc": datetime.now(UTC).isoformat(), "threshold": t,
                     "threshold_tuned_on": "dev split (30% of anchor UEIs), max F1, pooled label sets",
                     "excluded_ambiguous_negatives": excluded,
                     "label_counts": labels.groupby(["label_source", "split"]).size().unstack(fill_value=0).to_dict()}
    for fam in ("identifier", "synthetic"):
        sub = test[test.family == fam]
        metrics[fam] = prf(sub["label"].to_numpy(), sub["pred"].to_numpy())
    idp = test[(test.label_source == "identifier:uei")]
    metrics["identifier_pos_share_normalized_identical"] = round(float(idp["normalized_identical"].astype(bool).mean()), 4)
    hard = idp[~idp["normalized_identical"].astype(bool)]
    metrics["identifier_recall_on_non_trivial_pos"] = round(float(hard["pred"].mean()), 4) if len(hard) else None
    syn = test[test.label_source == "synthetic:pos"].copy()
    per = {}
    for p in ("abbreviation", "punctuation", "suffix", "typo", "address", "case", "acronym", "token_drop"):
        s = syn[syn["perturbations"].str.contains(p, na=False)]
        per[p] = {"n": int(len(s)), "recall": round(float(s["pred"].mean()), 4) if len(s) else None}
    metrics["synthetic_recall_by_perturbation"] = per
    metrics["ablation_v1_no_acronyms"] = ablation_v1(labels)
    metrics["learned_linkage"] = learned_linkage(labels, metrics)
    metrics["acronym_collisions_real_data"] = acronym_collisions(records)
    # threshold sensitivity on test (reported, not used for selection)
    metrics["sensitivity"] = {
        str(round(x, 2)): {fam: prf(test[test.family == fam]["label"].to_numpy(),
                                    (test[test.family == fam]["score"] >= x).astype(int).to_numpy())
                           for fam in ("identifier", "synthetic")}
        for x in (t - 0.05, t, t + 0.05)
    }
    fails = {
        "false_negatives": test[(test.label == 1) & (test.pred == 0)].sort_values("score").head(8),
        "false_positives": test[(test.label == 0) & (test.pred == 1)].sort_values("score", ascending=False).head(8),
    }
    REPORTS.mkdir(exist_ok=True)
    THRESHOLD_FILE.write_text(json.dumps({"threshold": t, "source": "er.evaluate dev split"}, indent=2))
    METRICS_FILE.write_text(json.dumps(metrics, indent=2, default=str))
    write_report(metrics, fails)
    print(json.dumps({k: metrics[k] for k in ("threshold", "identifier", "synthetic")}, indent=2))
    return metrics


def ablation_v1(labels: pd.DataFrame) -> dict:
    """The v1 matcher (no acronym feature), re-tuned on dev the same way and scored on the same test pairs."""
    lab = labels.copy()
    lab["score"] = score_pairs(lab, acronyms=False)
    t = tune_threshold(lab[lab.split == "dev"])
    lab["pred"] = (lab["score"] >= t).astype(int)
    test = lab[lab.split == "test"]
    out: dict = {"threshold": t}
    for fam in ("identifier", "synthetic"):
        sub = test[test.family == fam]
        out[fam] = prf(sub["label"].to_numpy(), sub["pred"].to_numpy())
    acr = test[(test.label_source == "synthetic:pos") & test["perturbations"].str.contains("acronym", na=False)]
    out["acronym_recall"] = round(float(acr["pred"].mean()), 4) if len(acr) else None
    return out


def learned_linkage(labels: pd.DataFrame, rule: dict) -> dict:
    """Learned evidence weights (er.linkage) vs the hand-set rule. Selection uses dev F1 only."""
    from supplier_graph.er import linkage
    lab = labels.copy()
    dev_mask = lab.split == "dev"
    model = linkage.fit(lab[dev_mask])
    lab["lscore"] = model.predict_proba(linkage.evidence_matrix(lab))[:, 1]
    t = tune_threshold(lab[dev_mask].assign(score=lab.loc[dev_mask, "lscore"]))
    lab["lpred"] = (lab["lscore"] >= t).astype(int)
    saved = linkage.save(model, t)
    dev = lab[dev_mask]
    dev_f1 = {"learned": prf(dev["label"].to_numpy(), dev["lpred"].to_numpy())["f1"],
              "rule": prf(dev["label"].to_numpy(), (dev["score"] >= rule["threshold"]).astype(int).to_numpy())["f1"]}
    test = lab[lab.split == "test"]
    out: dict = {"threshold": t, "weights": dict(zip(saved["features"], saved["coef"], strict=True)),
                 "intercept": saved["intercept"], "dev_f1": dev_f1,
                 "selected": "learned" if dev_f1["learned"] > dev_f1["rule"] else "rule"}
    for fam in ("identifier", "synthetic"):
        sub = test[test.family == fam]
        out[fam] = prf(sub["label"].to_numpy(), sub["lpred"].to_numpy())
    syn = test[test.label_source == "synthetic:pos"]
    out["acronym_recall"] = round(float(syn[syn["perturbations"].str.contains("acronym", na=False)]["lpred"].mean()), 4)
    return out


def acronym_collisions(records: pd.DataFrame) -> dict:
    """Real-data risk of the acronym rule: how many single-token names equal the initials of a supplier with a
    *different* UEI. The geographic features must then separate them; same-state cases are the dangerous ones."""
    rec = records.dropna(subset=["uei"]).copy()
    rec["core"] = rec["name"].map(core_name)
    by_init: dict[str, set[tuple[str, str]]] = {}
    rec["full"] = rec["name"].map(normalize_name)
    for r in rec[["core", "full", "uei", "state"]].drop_duplicates().itertuples(index=False):
        for i in initials(r.core, r.full):
            by_init.setdefault(i, set()).add((r.uei, r.state or ""))
    short = rec[rec["core"].str.fullmatch(r"[A-Z]{3,}")][["core", "uei", "state"]].drop_duplicates()
    n_any = n_same_state = 0
    examples = []
    for r in short.itertuples(index=False):
        others = {(u, st) for u, st in by_init.get(r.core, set()) if u != r.uei}
        if others:
            n_any += 1
            if any(st and st == (r.state or "") for _, st in others):
                n_same_state += 1
            if len(examples) < 5:
                examples.append(r.core)
    return {"single_token_names": int(len(short)), "collide_with_other_uei": n_any,
            "collide_same_state": n_same_state, "examples": examples}


def _pair_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_none_\n"
    lines = ["| source | score | record A (name / address) | record B (name / address) |", "|---|---|---|---|"]
    for r in df.itertuples():
        a = f"{r.a_name} / {r.a_address} {r.a_city} {r.a_state}".replace("|", "/")
        b = f"{r.b_name} / {r.b_address} {r.b_city} {r.b_state}".replace("|", "/")
        lines.append(f"| {r.label_source} | {r.score:.3f} | {a} | {b} |")
    return "\n".join(lines) + "\n"


def write_report(m: dict, fails: dict) -> None:
    lc = m["label_counts"]
    def cnt(src):
        return {s: lc.get(s, {}).get(src, 0) for s in ("dev", "test")}
    rows = "\n".join(
        f"| {src} | {cnt(src)['dev']} | {cnt(src)['test']} |"
        for src in ("identifier:uei", "identifier:neg", "synthetic:pos", "synthetic:neg"))
    idm, sym = m["identifier"], m["synthetic"]
    ab, ac, ll = m["ablation_v1_no_acronyms"], m["acronym_collisions_real_data"], m["learned_linkage"]
    per = "\n".join(f"| {k} | {v['n']} | {v['recall']} |" for k, v in m["synthetic_recall_by_perturbation"].items())
    sens = "\n".join(
        f"| {t} | {v['identifier']['precision']} | {v['identifier']['recall']} | {v['synthetic']['precision']} | {v['synthetic']['recall']} |"
        for t, v in m["sensitivity"].items())
    text = f"""# Entity resolution report

Generated {m['generated_at_utc']} by `make er`. All numbers below come from `reports/er_metrics.json`.

## Method

1. **Exact identifiers first.** Records carrying a UEI resolve to that UEI. Records with no UEI but a
   CAGE code resolve through the CAGE-to-UEI map observed on prime awards.
2. **Blocked fuzzy matching** for records with neither: candidates come from shared blocking keys
   (first name token, first two tokens, longest token + state, ZIP5 + name prefix); the pairwise scorer
   combines core-name similarity (legal suffixes stripped, common words abbreviated), address
   token similarity, ZIP5, city, state and country. A best candidate above the threshold is accepted;
   ties between different UEIs are left unresolved.
3. **Corporate hierarchy** comes from `recipient_parent_uei` / `subawardee_parent_uei`.

The matcher's input type (`MatchRecord`) has no identifier fields. Labels below are built from
identifiers, but the matcher never sees them.

## Label sets (all automatic; no manual labels)

| label source | dev pairs | test pairs |
|---|---|---|
{rows}

* `identifier:uei`: same UEI, raw name or address strings differ (source: identifier match).
* `identifier:neg`: core-name token-set similarity >= 85 but different UEI **and** different ultimate parent (source: identifier match).
  **{m['excluded_ambiguous_negatives']}** candidate pairs with identical normalized name *and* address were excluded as
  ambiguous: they are one legal entity holding several UEIs at one site (e.g. multiple registrations of the
  same company), which no name/address matcher can or should separate. This rule was added after the first
  run surfaced such pairs as "false positives"; it is a labeling fix, applied before threshold tuning.
* `synthetic:pos`: a real record vs. a programmatic perturbation of it (abbreviation, punctuation, legal suffix, typo, address reformat, case, acronym, dropped word) (source: synthetic).
* `synthetic:neg`: a real record vs. a perturbed copy of its hard-negative neighbour (source: synthetic).

Splits are by a hash of the anchor UEI (30% dev, 70% test). The threshold **{m['threshold']}** was
chosen on dev by max F1; everything below is test.

## Results (test split)

| set | precision | recall | F1 | TP | FP | FN | TN |
|---|---|---|---|---|---|---|---|
| identifier-derived | {idm['precision']} | {idm['recall']} | {idm['f1']} | {idm['tp']} | {idm['fp']} | {idm['fn']} | {idm['tn']} |
| synthetic | {sym['precision']} | {sym['recall']} | {sym['f1']} | {sym['tp']} | {sym['fp']} | {sym['fn']} | {sym['tn']} |

* Share of identifier positives whose normalized name **and** address are identical (trivial after
  normalization): **{m['identifier_pos_share_normalized_identical']}**. Recall on the non-trivial rest:
  **{m['identifier_recall_on_non_trivial_pos']}**.

### Synthetic recall by perturbation type (a pair can carry several)

| perturbation | n | recall |
|---|---|---|
{per}

### Before / after: acronym feature (same test pairs; v1 re-tuned on dev the same way)

v1 had no acronym rule; its acronym recall was 0.10. The current matcher credits a name similarity of 0.92 when
one core name is a single token equal to the initials of the other, so address, ZIP, city and state must still
agree for the pair to clear the threshold.

| matcher | threshold | id precision | id recall | synth precision | synth recall | acronym recall |
|---|---|---|---|---|---|---|
| v1 (no acronyms) | {ab['threshold']} | {ab['identifier']['precision']} | {ab['identifier']['recall']} | {ab['synthetic']['precision']} | {ab['synthetic']['recall']} | {ab['acronym_recall']} |
| current | {m['threshold']} | {idm['precision']} | {idm['recall']} | {sym['precision']} | {sym['recall']} | {m['synthetic_recall_by_perturbation']['acronym']['recall']} |

### Learned evidence weights vs the hand-set rule (`reports/er_linkage_model.json`)

A logistic regression on the same agreement evidence (raw name similarity, an acronym indicator, address, ZIP, city,
state, country, each with a missing-value indicator), fitted on dev. Dev F1: learned {ll['dev_f1']['learned']},
rule {ll['dev_f1']['rule']}; selected on dev: **{ll['selected']}**.

| matcher (test) | threshold | id precision | id recall | synth precision | synth recall | acronym recall |
|---|---|---|---|---|---|---|
| hand-set rule (current) | {m['threshold']} | {idm['precision']} | {idm['recall']} | {sym['precision']} | {sym['recall']} | {m['synthetic_recall_by_perturbation']['acronym']['recall']} |
| learned evidence weights | {ll['threshold']} | {ll['identifier']['precision']} | {ll['identifier']['recall']} | {ll['synthetic']['precision']} | {ll['synthetic']['recall']} | {ll['acronym_recall']} |

Learned log-odds weights: {', '.join(f"{k} {v:+.2f}" for k, v in ll['weights'].items())}.

**Real-data collision check.** Of {ac['single_token_names']} distinct single-token supplier names (3+ letters), {ac['collide_with_other_uei']} equal
the initials of a supplier with a *different* UEI, and {ac['collide_same_state']} of those share a state, where geography is
weakest at separating them. Examples: {', '.join(ac['examples']) or 'none'}. These are the pairs the acronym rule could merge wrongly;
the synthetic test set does not contain them, so treat synthetic acronym precision as optimistic.

### Threshold sensitivity (test; reported for transparency, not used for selection)

| threshold | id precision | id recall | synth precision | synth recall |
|---|---|---|---|---|
{sens}

## Limits of each label set

* **Identifier-derived positives skew easy.** Most same-UEI variation in USAspending is formatting
  (case, punctuation, the raw vs. SAM-normalized name). Real-world hard cases such as renamed companies,
  acquisitions and DBA names are under-represented, so identifier recall is optimistic.
* **Identifier-derived negatives are hard but narrow.** They are near-duplicate names of distinct
  companies (e.g. similarly named small businesses). They test precision where it matters most but say
  little about the bulk of easy non-matches.
* **Identifiers themselves can be wrong.** A UEI shared by two records is treated as truth; data-entry
  errors in UEIs would show up as false "errors" for the matcher.
* **Synthetic perturbations are only as realistic as the generator.** They cover the listed edit types,
  not transliteration, translated names, mergers, or wholly different trade names. Synthetic recall is an
  upper bound on robustness to those specific edits, not a general accuracy estimate.
* **Prevalence is artificial.** Both sets are roughly balanced; in production, true matches among
  blocked candidates are rarer, so production precision would be lower than shown.

## Failure analysis (test)

Lowest-scoring missed matches (false negatives):

{_pair_table(fails['false_negatives'])}
Highest-scoring wrong matches (false positives):

{_pair_table(fails['false_positives'])}
"""
    (REPORTS / "entity_resolution.md").write_text(text)


if __name__ == "__main__":
    run()
