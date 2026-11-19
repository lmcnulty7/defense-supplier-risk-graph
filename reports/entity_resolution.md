# Entity resolution report

Generated 2026-11-19T07:10:19.281579+00:00 by `make er`. All numbers below come from `reports/er_metrics.json`.

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
| identifier:uei | 757 | 1743 |
| identifier:neg | 753 | 1747 |
| synthetic:pos | 622 | 1378 |
| synthetic:neg | 606 | 1394 |

* `identifier:uei`: same UEI, raw name or address strings differ (source: identifier match).
* `identifier:neg`: core-name token-set similarity >= 85 but different UEI **and** different ultimate parent (source: identifier match).
  **68** candidate pairs with identical normalized name *and* address were excluded as
  ambiguous: they are one legal entity holding several UEIs at one site (e.g. multiple registrations of the
  same company), which no name/address matcher can or should separate. This rule was added after the first
  run surfaced such pairs as "false positives"; it is a labeling fix, applied before threshold tuning.
* `synthetic:pos`: a real record vs. a programmatic perturbation of it (abbreviation, punctuation, legal suffix, typo, address reformat, case, acronym, dropped word) (source: synthetic).
* `synthetic:neg`: a real record vs. a perturbed copy of its hard-negative neighbour (source: synthetic).

Splits are by a hash of the anchor UEI (30% dev, 70% test). The threshold **0.64** was
chosen on dev by max F1; everything below is test.

## Results (test split)

| set | precision | recall | F1 | TP | FP | FN | TN |
|---|---|---|---|---|---|---|---|
| identifier-derived | 0.9099 | 0.9558 | 0.9323 | 1666 | 165 | 77 | 1582 |
| synthetic | 0.9241 | 0.9891 | 0.9555 | 1363 | 112 | 15 | 1282 |

* Share of identifier positives whose normalized name **and** address are identical (trivial after
  normalization): **0.51**. Recall on the non-trivial rest:
  **0.9098**.

### Synthetic recall by perturbation type (a pair can carry several)

| perturbation | n | recall |
|---|---|---|
| abbreviation | 264 | 0.9962 |
| punctuation | 357 | 0.9944 |
| suffix | 410 | 0.9927 |
| typo | 366 | 0.9891 |
| address | 373 | 0.9973 |
| case | 420 | 0.9929 |
| acronym | 101 | 0.8614 |
| token_drop | 175 | 0.9714 |

### Before / after: acronym feature (same test pairs; v1 re-tuned on dev the same way)

v1 had no acronym rule; its acronym recall was 0.10. The current matcher credits a name similarity of 0.92 when
one core name is a single token equal to the initials of the other, so address, ZIP, city and state must still
agree for the pair to clear the threshold.

| matcher | threshold | id precision | id recall | synth precision | synth recall | acronym recall |
|---|---|---|---|---|---|---|
| v1 (no acronyms) | 0.63 | 0.908 | 0.957 | 0.9192 | 0.9332 | 0.099 |
| current | 0.64 | 0.9099 | 0.9558 | 0.9241 | 0.9891 | 0.8614 |

### Learned evidence weights vs the hand-set rule (`reports/er_linkage_model.json`)

A logistic regression on the same agreement evidence (raw name similarity, an acronym indicator, address, ZIP, city,
state, country, each with a missing-value indicator), fitted on dev. Dev F1: learned 0.9519,
rule 0.9451; selected on dev: **learned**.

| matcher (test) | threshold | id precision | id recall | synth precision | synth recall | acronym recall |
|---|---|---|---|---|---|---|
| hand-set rule (current) | 0.64 | 0.9099 | 0.9558 | 0.9241 | 0.9891 | 0.8614 |
| learned evidence weights | 0.51 | 0.9485 | 0.9094 | 0.9536 | 1.0 | 1.0 |

Learned log-odds weights: name_sim +2.66, acronym +1.56, addr +3.18, addr_missing -0.04, zip +0.29, zip_missing -0.17, city +1.67, city_missing -0.04, state +3.26, state_missing +2.78, country +2.53, country_missing +0.00.

**Real-data collision check.** Of 1618 distinct single-token supplier names (3+ letters), 68 equal
the initials of a supplier with a *different* UEI, and 9 of those share a state, where geography is
weakest at separating them. Examples: TTS, WCR, FEI, ISC, OSI. These are the pairs the acronym rule could merge wrongly;
the synthetic test set does not contain them, so treat synthetic acronym precision as optimistic.

### Threshold sensitivity (test; reported for transparency, not used for selection)

| threshold | id precision | id recall | synth precision | synth recall |
|---|---|---|---|---|
| 0.59 | 0.8891 | 0.9662 | 0.9048 | 0.9927 |
| 0.64 | 0.9099 | 0.9558 | 0.9241 | 0.9891 |
| 0.69 | 0.9311 | 0.9151 | 0.9445 | 0.9884 |

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

| source | score | record A (name / address) | record B (name / address) |
|---|---|---|---|
| identifier:uei | 0.227 | SIMBIONIX USA CORPORATION / 811 1ST AVE SEATTLE WA | SURGICAL SCIENCE NORTH AMERICA CORPORATION / 5381 S ALKIRE CIR LITTLETON CO |
| identifier:uei | 0.399 | HITACHI ENERGY USA INC / 901 MAIN CAMPUS DR RALEIGH NC | ABB ENTERPRISE SOFTWARE INC. / 305 GREGSON DR CARY NC |
| identifier:uei | 0.450 | THE W. W. WILLIAMS COMPANY, LLC / 3880 ZANE TRACE DR COLUMBUS OH | CP COMPANY, LLC / 3880 ZANE TRACE DR COLUMBUS OH |
| synthetic:pos | 0.450 | THE ENTWISTLE COMPANY LLC / 6 BIGELOW ST HUDSON MA | THE COMPANY LLC / 6 BIGELOW ST HUDSON MA |
| identifier:uei | 0.469 | LK CONSTRUCTION CO., LTD. / 18, NAMSURA 1-GIL, OKSEO-MYEON GUNSAN-SI  | LK TECH CO., LTD. / 754 GONGHANG-RO, OKSEO-MYEON GUNSAN  |
| identifier:uei | 0.476 | TK ELEVADORES PORTUGAL, UNIPESSOAL, LDA / ZONA INDUSTRIAL DA ABRUNHEIRA, EDIFICIO 4 2B SINTRA  | THYSSENKRUPP ELEVADORES, S.A. / RUA DAS INDUSTRIAS, 16 QUELUZ  |
| synthetic:pos | 0.484 | PIRIL INSAAT TICARET BILGISAYAR ELEKTRONIK BURO DONANIMLARI ANONIM SIRKETI / CEMALPASA MAH 63014 SK SUMER TOWERS ADANA  | PITBEBCAS / CEMALPASA MAH 63014 SK SUMER TOWERS ADANA  |
| identifier:uei | 0.518 | FLOWORKS USA LP / 3750 HIGHWAY 225 PASADENA TX | FLOWORKS USA LP / 136 EASTPORT RD JACKSONVILLE FL |

Highest-scoring wrong matches (false positives):

| source | score | record A (name / address) | record B (name / address) |
|---|---|---|---|
| identifier:neg | 1.000 | FAIRLEAD INTEGRATED, LLC / 650 CHAUTAUQUA AVE PORTSMOUTH VA | FAIRLEAD INTEGRATED POWER AND CONTROLS, LLC / 650 CHAUTAUQUA AVE PORTSMOUTH VA |
| synthetic:neg | 1.000 | FIDELITY TECHNOLOGY SERVICES, LLC / 2501 KUTZTOWN RD READING PA | Fidelity Technologies Corporation / 2501 KUTZTOWN RD READING PA |
| identifier:neg | 1.000 | PD POWER SYSTEMS, LLC / 6225 BRANDON AVE STE 460 SPRINGFIELD VA | PD SYSTEMS, LLC / 6225 BRANDON AVE STE 460 SPRINGFIELD VA |
| identifier:neg | 1.000 | A&E ELEVATOR II LLC / 1 N MAIN ST SHREWSBURY PA | A&E ELEVATOR, LLC. / 1 N MAIN ST SHREWSBURY PA |
| identifier:neg | 1.000 | FIDELITY TECHNOLOGY SERVICES, LLC / 2501 KUTZTOWN RD READING PA | FIDELITY TECHNOLOGIES CORPORATION / 2501 KUTZTOWN RD READING PA |
| identifier:neg | 1.000 | CTE II JV, LLC / 12802 SCIENCE DR ORLANDO FL | CTE JV, LLC / 12802 SCIENCE DR ORLANDO FL |
| identifier:neg | 1.000 | KSARIA SERVICE CORPORATION / 6 WENTWORTH DR HUDSON NH | KSARIA CORPORATION / 6 WENTWORTH DR HUDSON NH |
| synthetic:neg | 1.000 | CTE II JV, LLC / 12802 SCIENCE DR ORLANDO FL | CTE JV INCORPORATED / 12802 SCIENCE DR ORLANDO FL |

## Production resolution (`make er`)

* Entity records (distinct role/identifier/name/address observations): **19,777**; by method: uei: 19,777.
* Suppliers: **11,658**; parent UEIs: **9,096**.
* Prime awards mapped to a supplier: 57,596 / 57,596;
  subawards mapped: 35,842 / 35,842.

In this scope (FY2023-FY2025) every prime and subaward record carries a UEI, so the batch pipeline
resolves everything by exact identifier and the fuzzy path is not exercised in batch. The fuzzy matcher
still matters in two places: (1) the agent's `resolve_supplier` tool, which maps analyst-typed names
(abbreviations, typos, missing suffixes) to supplier IDs, and (2) older or third-party data without UEIs.
That is why it is evaluated above with identifiers hidden.

## End-to-end resolution with identifiers withheld (`reports/er_holdout.json`)

620 real records whose name differs from their UEI's canonical record, plus 500 records
whose UEI was removed from the index entirely, resolved through blocking, scoring and the runner-up margin rule
against 11,158 canonical suppliers. Identifiers are withheld from the matcher.

| matcher | threshold | present: correct link | present: wrong link | present: unresolved | absent: false link | precision of all links |
|---|---|---|---|---|---|---|
| rule, name blocking only (v1) | 0.64 | 55.8% | 5.3% | 38.9% | 7.0% | 83.6% |
| rule + facility blocking | 0.64 | 70.0% | 5.7% | 24.3% | 8.6% | 84.8% |
| learned, name blocking only | 0.51 | 55.0% | 4.7% | 40.3% | 7.4% | 83.8% |
| learned + facility blocking | 0.51 | 82.7% | 3.5% | 13.7% | 8.4% | 88.9% |

A wrong link merges two companies (it corrupts dependency counts); an unresolved record only splits one.

**How this evolved (disclosed):** the first run used name-based blocking only, and a diagnosis on these same records
showed most losses were blocking misses: renamed or acquired companies at an unchanged facility (Cobham Advanced
Electronic Solutions vs CAES Systems at one street address) never became candidates. The facility key (street +
ZIP5) was added in response. It has no tunable parameter, and the weights and thresholds still come only from the
dev split of the pair labels, but the before/after is measured on the records that motivated it (all 620
eligible records were used; none were left for a fresh confirmation). The production resolver (`make er`) uses the
matcher selected on dev F1 plus facility blocking. Absent-company false links are the price: some may be sibling
registrations at the same site rather than true errors, which these labels cannot tell apart.

## Operating point: can an abstain rule cut absent-company false links? (`reports/er_holdout.json`)

Absent-company false links rose from 7.0% to 8.4% when facility blocking was added. This searches 140
operating points of the shipped matcher (learned weights + facility blocking): the link threshold, the runner-up
margin, and a separate, stricter threshold for candidates found **only** through the facility address (an abstain
rule for address-only matches). Rule: utility = correct-link rate - 2 x (wrong-link rate + absent false-link rate); fixed before the curve was computed. A false merge corrupts two
companies' dependency counts; a missed link leaves a visible `NOID-` record. Records are split by hash: points are
chosen on the tuning half (303 present, 248 absent) and reported on the
confirmation half (317 present, 252 absent).

### Tradeoff curve (tuning half; best margin and address rule at each threshold)

| threshold | margin | address-only threshold | correct link | wrong link | absent false link | utility |
|---|---|---|---|---|---|---|
| 0.51 | 0.10 | same as threshold | 79.2% | 1.3% | 6.9% | 0.629 |
| 0.60 | 0.10 | same as threshold | 79.2% | 1.0% | 6.0% | 0.651 |
| 0.70 | 0.20 | same as threshold | 78.2% | 0.3% | 4.8% | 0.679 |
| 0.80 | 0.20 | same as threshold | 75.6% | 1.0% | 3.6% | 0.663 |
| 0.85 | 0.05 | same as threshold | 69.6% | 1.7% | 4.0% | 0.583 |
| 0.90 | 0.05 | same as threshold | 57.8% | 1.0% | 2.8% | 0.501 |
| 0.95 | 0.05 | same as threshold | 37.3% | 0.7% | 0.4% | 0.352 |

### Selected vs previous operating point, confirmation half

| operating point | threshold / margin / address-only | correct | wrong | unresolved | absent false link | precision of links |
|---|---|---|---|---|---|---|
| previous (dev-tuned threshold) | 0.51 / 0.02 / same as threshold | 83.6% | 3.8% | 12.6% | 8.7% | 88.6% |
| selected | 0.70 / 0.20 / same as threshold | 78.5% | 0.6% | 20.8% | 4.4% | 95.0% |

The selected point is now used by the production resolver (`reports/er_operating_point.json`). In the batch build every record carries a UEI, so this changes no graph number; it governs records that arrive without identifiers.
Weight sensitivity (chosen on the tuning half): weight 1.0: threshold 0.70, margin 0.05, address-only same as threshold; weight 3.0: threshold 0.70, margin 0.20, address-only same as threshold. On all 620 + 500 records the
selected point gives 78.4% correct, 0.48% wrong and
4.6% absent false links (previous: 82.7%,
3.55%, 8.4%).
