"""Plain explanations of the project's models for the Streamlit app.

Every number comes from reports/headline.json (`make headline`) via `headline.load()`; tests/test_headline.py renders
the app and checks that each number in this text is a headline value.
"""

from __future__ import annotations

from supplier_graph.risk.backtest import FEATURE_GROUPS


def feature_groups_md() -> str:
    return "\n".join(f"- **{group}:** " + "; ".join(desc for _, desc in feats) + "."
                     for group, feats in FEATURE_GROUPS.items())


def likelihood_md(h: dict[str, str]) -> str:
    """Body of the 'How likelihood is predicted' expander on the Likelihood x impact tab."""
    return f"""
A logistic regression on {h['lk_features']}, each computed from the supplier's contract transactions in that fiscal
year:

{feature_groups_md()}

**Training and testing.** The model was {h['lk_split']}: {h['lk_n_train']} training supplier-years
({h['lk_train_pos']} with an event) and {h['bt_n']} test supplier-years ({h['bt_pos']} with an event). The outcome is any
termination or cancellation on the supplier's contracts in the next fiscal year.

**`excess_vs_size`** is the model's probability divided by the probability from a size-only model (the same method,
using only contracts with activity and contracts still open into the next year). Above 1 means the supplier is
riskier than its contract volume alone predicts.

| Method (test year) | ROC-AUC | Precision in the top 5% ({h['top5_k']} suppliers) |
|---|---|---|
| Likelihood model | {h['auc_model']} | {h['top5_model']} |
| Size alone (contract counts) | {h['auc_size']} | {h['top5_size']} |
| Prior terminations alone | {h['auc_prior']} | {h['top5_prior']} |
| Three-flag screen | {h['auc_flags']} | {h['top5_flags']} |
| Random pick (base rate) | 0.5 | {h['lk_base_rate']} |

Gain over size alone, with 95% bootstrap intervals: ROC-AUC {h['auc_gain']}, average precision {h['ap_gain']}.

**Caveat.** The outcome is a proxy, not supplier failure. {h['conv_share']} of termination and cancellation actions
are terminations for convenience (the government's decision, not a supplier default), and one Navy office in Japan
accounts for {h['cluster_share']} of each year's events.
"""


def models_md(h: dict[str, str]) -> str:
    """'Models in this project' section of the About the data tab."""
    return f"""
**Supplier matching (entity resolution)**
- *Predicts:* whether two contract records belong to the same company.
- *Inputs:* normalized names, acronyms and facility address with ZIP code; learned evidence weights (logistic
  regression) score candidate pairs found by name, acronym and facility blocking. UEI and CAGE matches come first.
- *Tested:* on automatically labelled pairs with identifiers hidden, and end to end on {h['er_n_present']} renamed
  records with the UEI withheld.
- *Headline:* end-to-end correct links {h['er_v1_correct']} (first matcher) → {h['er_blocking_correct']}; the shipped
  operating point cut absent-company false links {h['er_op_absent']}.

**Termination likelihood**
- *Predicts:* the probability of any contract termination or cancellation for the supplier in the next fiscal year.
- *Inputs:* {h['lk_features']} from that year's contract transactions (size and exposure, contract churn, history,
  competition, dependence).
- *Tested:* {h['lk_split']}, against size alone, prior terminations and the three-flag screen.
- *Headline:* ROC-AUC {h['auc_model']} against {h['auc_size']} for size alone; precision in the top 5%
  {h['top5_model']} against {h['top5_size']}.

**Agent**
- *Predicts:* answers to dependency questions, citing a record ID for every fact, or a statement that the data
  cannot answer.
- *Inputs:* the question and four read-only tools over the graph (find a supplier, run a named query, look up
  country risk, fetch award details). The model is Claude Opus; Claude Haiku runs in the comparisons.
- *Tested:* question sets whose answers are computed by deterministic queries and checked two independent ways,
  plus trick questions about entities that are not in the data.
- *Headline:* Opus answered {h['v1_opus']} answerable questions and declined {h['v1_opus_ref']} trick questions,
  and {h['ch_opus']} on the harder set; with the name-lookup check, Haiku caught {h['nm_guard']} look-alike names.
"""
