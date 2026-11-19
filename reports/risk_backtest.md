# Risk backtest: do the screens anticipate next year's contract disruptions?

Generated 2026-11-19T07:10:44.254994+00:00 by `make backtest`. Every number below comes from `reports/risk_backtest.json`.
Portfolio project on public data; the customer persona is fictional; not used by or built for the DoD or any company.

## Bottom line

1. **The existing flags measure impact, not likelihood.** After adjusting for contract volume, the *concentrated*
   flag has no association with next-year disruptions (odds ratio 0.77
   [0.494, 1.078]), and *sole-source* suppliers are
   **less** likely to see one (0.66
   [0.515, 0.823]): long-running OEM sustainment
   relationships are stable. The *geo-exposed* association (1.75)
   disappears (1.00
   [0.729, 1.358]) once one Navy office in Japan is
   excluded. As a likelihood ranking, the current screen scores ROC-AUC 0.635,
   well below contract count alone (0.758).
2. **Most of what is predictable is exposure.** A supplier with more open contracts has more chances of one being
   terminated: contract count alone reaches ROC-AUC 0.758 on FY2025 outcomes.
3. **A simple model improves the top of the list, not the overall ranking.** Gain over size: AUC +0.013 [-0.013, 0.039]; average precision +0.070 [0.028, 0.110] (share of bootstrap draws with no gain: 0.001).
   The earlier split agrees (AUC +0.005 [-0.023, 0.033]; average precision +0.071 [0.022, 0.125] (share of bootstrap draws with no gain: 0.003)), and so does a run with the Japan office cluster removed
   (AUC +0.022 [-0.007, 0.049]; average precision +0.032 [0.002, 0.063] (share of bootstrap draws with no gain: 0.013)). In the top 5% the model's precision is 0.177
   versus a 0.026 base rate (6.76x lift; size alone
   5.52x). 8 of its 10 highest-ranked suppliers had an event.
4. **Disruptions cluster by buying office.** NAVSUP Fleet Logistics Center Yokosuka (codes N68246, then N62649)
   accounts for 17%-38% of each year's events across FY2022-FY2025, and 100% of its events are
   terminations for convenience. The label partly measures how an office manages orders, not only supplier health.

**Product decision:** keep the flags as *impact* screens and show likelihood on a separate axis. The analyst's
triage view is impact (what depends on the supplier, from the graph) against likelihood (this model), with an
"excess over size" ratio to surface suppliers riskier than their contract volume explains.

## Design

* **Outcome (from the data, not annotated):** supplier-year y = 1 if any of the supplier's contracts has a
  transaction in fiscal year t+1 with action type E (terminate for default), X (terminate for cause), F (terminate
  for convenience) or N (legal contract cancellation). These are *disruption events seen by the buying office*,
  not proof of supplier fault.
* **Data:** 187,320 DoD PSC-J contract transactions, FY2022-FY2025
  (USAspending `/api/v2/download/transactions/`, request `91570fa37fa508b8`, pulled
  2026-11-19, SHA-256 in the cache `meta.json`).
* **Leakage control:** features for year t use only year-t transactions. Train on FY2022-FY2023 features
  (FY2023-FY2024 outcomes); test on FY2024 features (FY2025 outcomes). Earlier split: train FY2022, test FY2023.
* **Baselines:** contract count (size), prior-year terminations, and the current three-flag screen (untrained).
  The logistic model was chosen as primary before results were seen; boosted trees are reported for comparison.
* **Country risk feed policy:** the MENA feed (schema 2.0.0, label `escalated_next4w_ucdp`, weeks 2026-08-24 to 2026-11-09) reports `skill_status = beats_base_rate`. Rule: a feed-derived input enters the likelihood model only if (1) the feed's skill_status is beats_base_rate or beats_country_rate, (2) point-in-time values (data_cutoff on or before the end of the feature year, at most 26 weeks old) cover >= 80% of feed-exposed supplier-years in every train and test year, and (3) adding it to the current model gives a paired-bootstrap AP gain with 95% CI above 0 and a non-negative AUC gain on the primary split. Fixed before the schema 2.0.0 run.
  - `feed_exposure`: not adopted; does not improve the current model: AP gain -0.0004 [-0.0073, 0.0071], AUC gain -0.0006.
  - `feed_p_escalation`: not adopted; not evaluable: point-in-time values cover less than 80% of feed-exposed supplier-years in some train/test year (coverage {'2022': 0.0, '2023': 0.0, '2024': 0.0}).
  Likelihood model inputs from the feed: none. Geographic exposure remains an impact attribute (the geo-exposed flag).
* **Uncertainty:** 1000 bootstrap resamples of the test year; gains are paired (same resample for both rankings).

### Event counts (transactions)

| year | E default | X cause | F convenience | N cancellation |
|---|---|---|---|---|
| FY2022 | 3 | 5 | 206 | 17 |
| FY2023 | 2 | 4 | 210 | 13 |
| FY2024 | 1 | 6 | 169 | 17 |
| FY2025 | 6 | 17 | 204 | 27 |

Default and cause together are too rare to model on their own; they are included in the combined outcome.

## Results

### Primary split: train FY2022-FY2023, test FY2024 features -> FY2025 outcomes

5,541 supplier-years, 145 with an event (base rate 0.026).

| ranking | ROC-AUC [95% CI] | avg precision [95% CI] | precision in top 5% | lift | recall in top 5% |
|---|---|---|---|---|---|
| Size only (contract count) | 0.758 [0.712, 0.802] | 0.120 [0.086, 0.179] | 0.144 | 5.52x | 0.276 |
| Prior-year terminations only | 0.563 [0.536, 0.594] | 0.110 [0.066, 0.162] | 0.076 | 2.90x | 0.145 |
| Current screen (number of flags, untrained) | 0.635 [0.588, 0.683] | 0.065 [0.045, 0.101] | 0.065 | 2.48x | 0.124 |
| Logistic model (primary) | 0.772 [0.730, 0.812] | 0.195 [0.137, 0.261] | 0.177 | 6.76x | 0.338 |
| Gradient-boosted trees | 0.769 [0.728, 0.812] | 0.184 [0.130, 0.248] | 0.173 | 6.62x | 0.331 |

### Earlier split: train FY2022, test FY2023 features -> FY2024 outcomes

5,655 supplier-years, 107 with an event (base rate 0.019).

| ranking | ROC-AUC [95% CI] | avg precision [95% CI] | precision in top 5% | lift | recall in top 5% |
|---|---|---|---|---|---|
| Size only (contract count) | 0.765 [0.712, 0.817] | 0.151 [0.099, 0.230] | 0.148 | 7.84x | 0.393 |
| Prior-year terminations only | 0.599 [0.560, 0.641] | 0.117 [0.062, 0.182] | 0.081 | 4.30x | 0.215 |
| Current screen (number of flags, untrained) | 0.623 [0.566, 0.681] | 0.054 [0.031, 0.096] | 0.039 | 2.05x | 0.103 |
| Logistic model (primary) | 0.770 [0.718, 0.821] | 0.228 [0.150, 0.316] | 0.159 | 8.40x | 0.421 |
| Gradient-boosted trees | 0.762 [0.708, 0.813] | 0.197 [0.120, 0.283] | 0.152 | 8.03x | 0.402 |

### Sensitivity: events at NAVSUP FLC Yokosuka removed from the outcome (primary split)

136 test positives remain. Logistic ROC-AUC 0.764, average precision
0.116; size only 0.742 / 0.086.

### Do the flags predict likelihood? (all labelled supplier-years pooled)

| flag | supplier-years flagged | odds ratio, raw [95% CI] | odds ratio, size-adjusted [95% CI] |
|---|---|---|---|
| concentrated | 920 of 16,916 | 3.06 [2.140, 4.059] | 0.77 [0.494, 1.078] |
| sole_source | 6,331 of 16,916 | 0.70 [0.557, 0.885] | 0.66 [0.515, 0.823] |
| geo_exposed | 1,935 of 16,916 | 2.25 [1.778, 2.982] | 1.75 [1.347, 2.268] |

Same, with the Japan office cluster removed from the outcome:

| flag | supplier-years flagged | odds ratio, raw [95% CI] | odds ratio, size-adjusted [95% CI] |
|---|---|---|---|
| concentrated | 920 of 16,916 | 3.19 [2.327, 4.304] | 0.98 [0.655, 1.388] |
| sole_source | 6,331 of 16,916 | 0.78 [0.617, 0.960] | 0.74 [0.579, 0.921] |
| geo_exposed | 1,935 of 16,916 | 1.32 [0.959, 1.793] | 1.00 [0.729, 1.358] |

### Logistic model coefficients (standardized features, primary split)

| feature | coefficient |
|---|---|
| log_n_awards | +0.639 |
| log_prior_events | +0.278 |
| deob_share | -0.225 |
| small_business | +0.204 |
| foreign_share | +0.194 |
| log_obligations | +0.182 |
| log_n_offices | +0.177 |
| sole_source_share | -0.156 |
| log_n_awards_open_next | -0.133 |
| single_offer_share | +0.128 |
| max_office_share | -0.104 |
| office_event_rate | +0.088 |
| log_change_orders | +0.070 |
| any_prior_event | -0.057 |
| mods_per_award | +0.028 |

### Calibration (logistic, FY2025 outcomes, quintiles of predicted probability)

| quintile | n | mean predicted | observed rate |
|---|---|---|---|
| 1 | 1,109 | 0.0043 | 0.0045 |
| 2 | 1,108 | 0.0073 | 0.0108 |
| 3 | 1,108 | 0.0091 | 0.0199 |
| 4 | 1,108 | 0.0133 | 0.0144 |
| 5 | 1,108 | 0.0590 | 0.0812 |

### Ten highest-ranked suppliers in the test year, and what happened

| rank | supplier UEI | predicted p | contracts active | event in FY2025? | example terminated award IDs |
|---|---|---|---|---|---|
| 1 | `FTW2SER4A9Q3` | 0.982 | 198 | yes | `CONT_AWD_N6264925F0023_9700_N6824623G0004_9700`, `CONT_AWD_N6264925F0146_9700_N6824623G0004_9700` |
| 2 | `JZUMWWR4MAF7` | 0.957 | 174 | yes | `CONT_AWD_N6264925F0089_9700_N6264921G0002_9700`, `CONT_AWD_N6264925F0105_9700_N6264921G0002_9700` |
| 3 | `VMZWP1XBR475` | 0.943 | 123 | yes | `CONT_AWD_N6264925F0171_9700_N6824621G0023_9700`, `CONT_AWD_N6264925F0178_9700_N6824621G0023_9700` |
| 4 | `LS3NFWZLJLB3` | 0.927 | 59 | yes | `CONT_AWD_N6264925F0327_9700_N6824623G0002_9700`, `CONT_AWD_N6264925F0351_9700_N6824623G0002_9700` |
| 5 | `RBCJDAS355L9` | 0.850 | 43 | yes | `CONT_AWD_FA465924P0003_9700_-NONE-_-NONE-`, `CONT_AWD_HQ003423C0108_9700_-NONE-_-NONE-` |
| 6 | `KCYUSLQREK38` | 0.836 | 69 | yes | `CONT_AWD_N6264925F0076_9700_N6824623G0001_9700`, `CONT_AWD_N6264925F0168_9700_N6824623G0001_9700` |
| 7 | `VDAPD41KB5W9` | 0.778 | 56 | yes | `CONT_AWD_N6264925F0119_9700_N6264922G0007_9700`, `CONT_AWD_N6264925F0202_9700_N6264922G0007_9700` |
| 8 | `RG6LJJM6JBS3` | 0.774 | 111 | no | - |
| 9 | `SXFFDJ5JVDD8` | 0.673 | 470 | no | - |
| 10 | `YHKJQV4GUXK9` | 0.634 | 67 | yes | `CONT_AWD_N6264925F0207_9700_N6264922G0006_9700` |

## FY2026 outlook (`supplier_disruption_score` table, `reports/risk_watchlist.csv`)

Trained on every labelled year and scored on FY2025 features for 5,407 suppliers that are
also in the graph. `priority_score` = probability x prime obligations at stake: a triage ranking, not an expected
loss, and it favours large primes because their probability scales with contract count. `excess_vs_size` divides
the model probability by the size-only probability. 36 suppliers with at
least $10M in prime obligations have a ratio of 2 or more. The ten highest:

| supplier UEI | name | p (FY2026) | excess over size | prime obligations | dependent offices |
|---|---|---|---|---|---|
| `EN2AWZWQ1WA1` | BRODOGRADILISTE VIKTOR LENAC D.D. | 0.300 | 9.2x | $106.7M | 1 |
| `MVQPNXTQLYE5` | ADVANCED ELECTRONICS COMPANY LIMITED | 0.079 | 5.9x | $22.2M | 5 |
| `K84MXRVQ8BW5` | PACIFIC SHIPYARDS INTERNATIONAL, LLC | 0.812 | 5.7x | $298.1M | 7 |
| `YFNLTN7RMMC6` | ARISTA AVIATION SERVICES, LLC. | 0.417 | 5.6x | $46.9M | 4 |
| `JQ8KFGG5RCL4` | BAYONNE DRYDOCK & REPAIR CORP. | 0.145 | 5.0x | $35.9M | 5 |
| `D9P7CUAJBG55` | CPI ELECTRON DEVICE BUSINESS, INC. | 0.296 | 4.8x | $14.0M | 10 |
| `FG7HKV9NTDK5` | DRS SUSTAINMENT SYSTEMS, INC | 0.743 | 4.7x | $367.7M | 4 |
| `JWEMUM8429K1` | UD CONTRACTING INC | 0.209 | 3.7x | $25.6M | 1 |
| `EEN8SHG8AAC7` | ALABAMA SHIPYARD LLC | 0.197 | 3.5x | $380.0M | 2 |
| `DCHNBAWWCBD6` | TYONEK SERVICES OVERHAUL FACILITY - STENNIS, LLC | 0.039 | 2.9x | $15.8M | 4 |

## Limits

* **The label is a proxy.** Terminations for convenience dominate and can reflect changed requirements, budget
  moves or administrative clean-up, not supplier distress. A real deployment would add CPARS/FAPIIS past
  performance, SAM exclusions and financial data, none of which are in this public scope.
* **One office drives a large share of events,** so office practice is part of what is being predicted.
* **Scope is PSC J only.** A supplier terminated on a non-maintenance contract does not count.
* **Three labelled years.** The splits are small (about 100-150 positives per test year), hence the wide CIs.
* **No causal claim.** "Sole-source suppliers have fewer terminations" is an association, consistent with stable
  OEM sustainment relationships; it does not mean sole-sourcing reduces risk.
