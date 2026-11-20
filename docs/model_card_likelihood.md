# Model card: supplier termination likelihood

*Portfolio project on public data. The customer persona is fictional. Not used by, endorsed by, or built for the DoD
or any company. Every number below is a value in [`reports/HEADLINE.md`](../reports/HEADLINE.md) (`make headline`),
and `tests/test_headline.py` checks that this card quotes no other.*

## Model details

* **Type:** logistic regression (scikit-learn, standardized inputs, scikit-learn's default regularization).
* **Output:** `p_disruption_next_fy`, the probability that a supplier has any contract termination or cancellation
  in the next fiscal year; and `excess_vs_size`, that probability divided by the probability from a size-only model
  (the same method on `log_n_awards` and `log_n_awards_open_next`). Above 1 means riskier than the supplier's contract
  volume alone predicts.
* **Code and reproduction:** `src/supplier_graph/risk/backtest.py`; `make backtest` writes
  `reports/risk_backtest.json`, `reports/risk_backtest.md` and the `supplier_disruption_score` table.
* **Alternatives tried:** gradient-boosted trees scored no better than the logistic regression on the same split, so
  the simpler model ships (`reports/risk_backtest.md`).

## Intended use

* Rank suppliers for an analyst's review in a likelihood × impact view: likelihood from this model, impact from the
  obligations at stake and the dependency graph, shown on separate axes.
* Point at suppliers that are riskier than their size suggests (`excess_vs_size` above 1) so a reviewer can look at
  why.
* Triage support for a human reviewer. The output is a starting point for questions, not a finding.

## Out-of-scope use

* Predicting supplier failure, bankruptcy, financial distress or poor performance. The label is mostly the
  government's own decision to end contracts (see *Limitations*).
* Any decision about a specific company: source selection, award, debarment, responsibility determinations or
  public statements about a supplier.
* Contracts outside DoD maintenance, repair and rebuild (product service code J), or later years without re-running
  the backtest.
* Treating country escalation risk as part of the score: the MENA country-risk feed is not an input
  (not adopted: 0% point-in-time coverage).

## Data

* **Source:** USAspending.gov contract transactions for DoD PSC-J awards, FY2022-FY2025 (187,320 transactions),
  cached with request parameters, pull time and content hashes.
* **Unit:** a supplier-year (supplier UEI and fiscal year). Features use only that year's transactions; the label
  uses the next year's.
* **Label:** any transaction with action type E (default), X (cause), F (convenience) or N (legal cancellation) on the
  supplier's contracts in the next fiscal year. The label comes from administrative records by a metadata rule; nothing
  was annotated by hand.
* **Split:** trained on FY2022-FY2023, tested on FY2024 features against FY2025 outcomes. Training: 11,375
  supplier-years (212 with an event). Test: 5,541 supplier-years (145 with an event), base rate 2.6%.

## Features

15 supplier-year features, computed from the supplier's contract transactions in the feature year:

| Group | Feature | Meaning |
|---|---|---|
| Size and exposure | `log_n_awards` | contracts with activity in the year |
| | `log_n_awards_open_next` | contracts still open into the next year |
| | `log_obligations` | dollars obligated in the year |
| | `foreign_share` | share of dollars performed outside the United States |
| Contract churn | `deob_share` | share of dollar actions that took money back (de-obligations) |
| | `mods_per_award` | contract modifications per contract |
| | `log_change_orders` | change orders |
| History | `log_prior_events` | terminations and cancellations in the year |
| | `any_prior_event` | whether there was any termination or cancellation in the year |
| | `office_event_rate` | termination rate at the supplier's buying offices, excluding the supplier itself |
| Competition | `sole_source_share` | share of dollars awarded without competition |
| | `single_offer_share` | share of dollars where only one offer was received |
| | `small_business` | whether most awards were small-business awards |
| Dependence | `log_n_offices` | offices paying the supplier |
| | `max_office_share` | largest share of any one office's dollars that goes to the supplier |

## Evaluation

Test year (FY2024 features against FY2025 outcomes), 5,541 supplier-years:

| Method | ROC-AUC | Precision in the top 5% (277 suppliers) |
|---|---|---|
| Likelihood model | 0.772 | 17.7% |
| Size alone (contract counts) | 0.758 | 14.4% |
| Prior terminations alone | 0.563 | 7.6% |
| Three-flag screen (concentrated, sole-source, geo-exposed) | 0.635 | 6.5% |
| Random pick (base rate) | 0.5 | 2.6% |

* **Gain over size alone** (paired bootstrap, 95% intervals): ROC-AUC +0.013 [-0.013, 0.039], not distinguishable from
  zero; average precision +0.070 [0.028, 0.110]. The model ranks the whole population about as well as counting
  contracts, and is better at the top of the list.
* **Robustness:** an earlier split and a rerun without the dominant Navy office give the same ordering of methods by
  ROC-AUC (`reports/risk_backtest.md`).
* **The flags are impact measures, not likelihood:** the three-flag screen ranks below contract count.

## Limitations

* **Proxy label.** 87% of termination and cancellation actions are terminations for convenience, the government's
  decision rather than a supplier default. A high score means "contracts likely to be ended", not "supplier likely to
  fail".
* **One office drives the label.** A single Navy office in Japan accounts for 17%-38% of each year's events, so the
  model partly learns that office's practice.
* **Small advantage over size.** Overall ranking barely improves on counting contracts; most of the value is at the
  top of the list.
* **Narrow inputs.** Only contract transactions: no financial, ownership, workforce or supply-chain data. The
  country-risk feed could not be tested on the backtest years (not adopted: 0% point-in-time coverage).
* **Ratio instability.** `excess_vs_size` divides two probabilities; for suppliers with very small size-only
  probabilities the ratio can be large on little evidence.
* **Scope.** DoD PSC-J maintenance contracts only; results may not transfer to other categories or agencies.

## Ethical considerations

* **Named companies.** The data is public, but scores attach to real, named suppliers. Presenting a score as evidence
  of wrongdoing or weakness would be unfair, because the label mostly reflects buyers' decisions.
* **Buyer practice becomes supplier risk.** Office-level termination habits (the Navy office in Japan,
  `office_event_rate`) raise the scores of that office's suppliers regardless of their own conduct.
* **Small-business status is an input.** `small_business` raises or lowers scores; any use that steered awards away
  from small businesses would conflict with federal small-business goals. Award decisions are out of scope.
* **Human review.** The score supports a reviewer's triage and should be shown with its drivers, the impact axis and
  this card's caveats.
* **No affiliation.** Not used by, endorsed by, or built for the DoD or any company.
