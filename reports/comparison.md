# Agent comparison: models, tools and the grounding guard

Scored 2026-11-19T07:14:24.750648+00:00 by `make compare-offline` from committed logs (`logs/agent_runs/cmp_*.jsonl` and the
main eval log). Same deterministic scorer as `reports/eval_report.md`. Portfolio project on public data; fictional persona.

## Bottom line

1. **The graph is what answers the questions.** Without tools, Opus declines everything on the challenge set
   (0/38 answerable). It does not invent answers, but it cannot help.
2. **With named, verified queries, structured questions are easy even for a small model.** Haiku 5.5 answers every
   answerable challenge question in three runs (38/38 / 38/38 / 38/38)
   at about 1/49 of Opus's cost per question on this set. On the v1 set it scores 83/88 / 83/88 / 83/88
   vs Opus 87/88; of Haiku's misses per run, 3 / 3 / 3 are output-contract
   violations (a dict in the answer field that contains the right value).
3. **The real failure mode is entity grounding.** On near-miss names (a real supplier with one distinctive word
   swapped), Haiku silently answered for the look-alike: correct on 1/8, 3/8, 3/8 of 8 traps across
   runs. Opus got 7/8, 7/8, 7/8 (the near-miss traps were re-run twice for Opus to
   measure its variance there; see *Run-to-run variance*).
4. **A tool-side fix closes most of the gap.** The resolver's grounding guard lists query words the matched name
   lacks and tells the model not to substitute silently. Haiku with the guard: 7/8, 8/8, 7/8 of 8,
   with no loss on answerable challenge questions (v1 with the guard: 82/88 / 83/88 / 83/88).
   Replaying the 88 logged Opus resolver calls on answerable questions, the guard fires on
   0, so it cannot change those answers. It is on by default (`RESOLVER_GUARD=1`).

The one trap every configuration missed (`u_near_miss-ebf5d8f3`, "Execujet Mro Svcs Middle East Dwc Optics") is the
most debatable label: the look-alike shares five distinctive words, and both models said they were substituting the
closest candidate rather than doing it silently. It is scored as a miss, as labelled before the runs.

The first draft of the challenge set (`reports/challenge_set_draft.jsonl`) saturated: Haiku scored 44/44 / 44/44 / 44/44 on its answerable questions in three runs. Four reasoning-over-output templates were added and the near-miss traps increased; the agent's prompt and tools were not changed in response. The final set below was then run once per configuration.

## Challenge set (v2): 38 answerable + 14 traps

Repeated configurations show each run separated by "/". 95% CIs are Wilson intervals (single-run rows only).

| configuration | runs | answerable correct [95% CI] | refusals correct [95% CI] | fabricated IDs | cost / question | latency |
|---|---|---|---|---|---|---|
| Opus 5.5 + tools (shipped agent, guard off) | 1 | 38/38 [0.91, 1.00] | 13/14 [0.69, 0.99] | 0 | $0.0412 | 9.7 s |
| Haiku 5.5 + tools (guard off) | 3 | 38/38 / 38/38 / 38/38 | 7/14 / 9/14 / 9/14 | 0 / 0 / 0 | $0.0008 | 4.8 s |
| Haiku 5.5 + tools + grounding guard | 3 | 38/38 / 38/38 / 38/38 | 13/14 / 14/14 / 13/14 | 0 / 0 / 0 | $0.0008 | 4.7 s |
| Opus 5.5, no tools | 1 | 0/38 [0.00, 0.09] | 14/14 [0.78, 1.00] | 0 | $0.0067 | 3.6 s |

### By template

| template | Opus 5.5 + tools | Haiku 5.5 + tools | Haiku 5.5 + tools + grounding guard | Opus 5.5, no tools |
|---|---|---|---|---|
| c_acronym | 4/4 | 4/4 / 4/4 / 4/4 | 4/4 / 4/4 / 4/4 | 0/4 |
| c_compare_two | 4/4 | 4/4 / 4/4 / 4/4 | 4/4 / 4/4 / 4/4 | 0/4 |
| c_indirect_only | 2/2 | 2/2 / 2/2 / 2/2 | 2/2 / 2/2 / 2/2 | 0/2 |
| c_office_filter | 5/5 | 5/5 / 5/5 / 5/5 | 5/5 / 5/5 / 5/5 | 0/5 |
| c_paraphrase_blast | 2/2 | 2/2 / 2/2 / 2/2 | 2/2 / 2/2 / 2/2 | 0/2 |
| c_parent_award_sum | 5/5 | 5/5 / 5/5 / 5/5 | 5/5 / 5/5 / 5/5 | 0/5 |
| c_parent_total | 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 0/3 |
| c_program_ratio_argmax | 2/2 | 2/2 / 2/2 / 2/2 | 2/2 / 2/2 / 2/2 | 0/2 |
| c_program_ss_share | 2/2 | 2/2 / 2/2 / 2/2 | 2/2 / 2/2 / 2/2 | 0/2 |
| c_rank_three | 4/4 | 4/4 / 4/4 / 4/4 | 4/4 / 4/4 / 4/4 | 0/4 |
| c_shared_offices | 5/5 | 5/5 / 5/5 / 5/5 | 5/5 / 5/5 / 5/5 | 0/5 |
| u_false_premise | 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 |
| u_near_miss | 7/8 | 1/8 / 3/8 / 3/8 | 7/8 / 8/8 / 7/8 | 8/8 |
| u_us_only_risk | 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 |

## v1 golden set: 88 answerable + 13 unanswerable

| configuration | runs | answerable correct [95% CI] | refusals correct [95% CI] | fabricated IDs | cost / question | latency |
|---|---|---|---|---|---|---|
| Opus 5.5 + tools (shipped agent, guard off) | 1 | 87/88 [0.94, 1.00] | 13/13 [0.77, 1.00] | 0 | $0.0325 | 7.1 s |
| Haiku 5.5 + tools (guard off) | 3 | 83/88 / 83/88 / 83/88 | 13/13 / 13/13 / 13/13 | 0 / 0 / 3 | $0.0006 | 3.6 s |
| Haiku 5.5 + tools + grounding guard | 3 | 82/88 / 83/88 / 83/88 | 13/13 / 13/13 / 13/13 | 0 / 0 / 4 | $0.0006 | 3.7 s |

### By template

| template | Opus 5.5 + tools | Haiku 5.5 + tools | Haiku 5.5 + tools + grounding guard |
|---|---|---|---|
| absent_award | 2/2 | 2/2 / 2/2 / 2/2 | 2/2 / 2/2 / 2/2 |
| absent_program | 1/1 | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 |
| absent_supplier | 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| award_sole_source | 5/5 | 5/5 / 5/5 / 5/5 | 5/5 / 5/5 / 5/5 |
| blast_radius_awards | 5/5 | 4/5 / 4/5 / 4/5 | 4/5 / 4/5 / 4/5 |
| blast_radius_programs | 5/5 | 5/5 / 5/5 / 5/5 | 5/5 / 5/5 / 5/5 |
| country_prime_awards | 5/5 | 5/5 / 5/5 / 5/5 | 5/5 / 5/5 / 5/5 |
| country_risk | 5/5 | 5/5 / 5/5 / 5/5 | 5/5 / 5/5 / 5/5 |
| dependent_offices_count | 5/5 | 5/5 / 5/5 / 5/5 | 5/5 / 5/5 / 5/5 |
| hard_company_rollup | 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| hard_feed_country | 3/3 | 0/3 / 0/3 / 0/3 | 0/3 / 0/3 / 0/3 |
| hard_noisy_name | 2/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| hard_office_flagged_blast | 3/3 | 3/3 / 3/3 / 3/3 | 2/3 / 3/3 / 3/3 |
| hard_prime_customers | 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| hard_single_point | 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| office_flagged_share | 5/5 | 5/5 / 5/5 / 5/5 | 5/5 / 5/5 / 5/5 |
| office_hhi | 5/5 | 5/5 / 5/5 / 5/5 | 5/5 / 5/5 / 5/5 |
| office_top_supplier | 5/5 | 5/5 / 5/5 / 5/5 | 5/5 / 5/5 / 5/5 |
| out_of_schema | 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| parent_subsidiaries | 5/5 | 4/5 / 4/5 / 4/5 | 4/5 / 4/5 / 4/5 |
| program_top_supplier | 5/5 | 5/5 / 5/5 / 5/5 | 5/5 / 5/5 / 5/5 |
| risk_not_in_feed | 4/4 | 4/4 / 4/4 / 4/4 | 4/4 / 4/4 / 4/4 |
| sole_source_share | 5/5 | 5/5 / 5/5 / 5/5 | 5/5 / 5/5 / 5/5 |
| subcontractor_count | 5/5 | 5/5 / 5/5 / 5/5 | 5/5 / 5/5 / 5/5 |
| top_sole_source_country | 5/5 | 5/5 / 5/5 / 5/5 | 5/5 / 5/5 / 5/5 |

## Grounding guard, offline

Offline, the guard flags 8/8 near-miss names and 0/3 noisy-but-correct names. On the ER test pairs it fires on 0.08% of synthetic same-company pairs (acronyms excluded; they bypass the guard), 7.8% of same-UEI pairs (mostly genuine renames, e.g. Turbo Systems to Accelleron) and 26.5% of different-company hard negatives. It is a targeted warning for a swapped distinctive word, not a general disambiguator. (`reports/guard_eval.json`, `make guard-eval`)

## Other evaluation checks

* **Judge on subtle errors:** on real agent prose, agreement 100% over 30 cases (real_correct_prose: 100% of 15, off_by_one_count: 100% of 12, share_plus_5_points: 100% of 3). `reports/judge_validation_subtle.json`.
* **Run-to-run variance (Haiku, three runs each):** answerable accuracy across runs is identical
  (challenge), identical (v1), 82/88 to 83/88 (v1 with guard); trap refusals vary by up
  to two questions. **Opus:** the full challenge set ran once (one run cost $2.14; two more would take total
  spend past the $10 cap), so its answerable-question variance is not measured. Its eight near-miss traps, where
  variance matters most for the guard comparison, were re-run twice more: 7/8, 7/8, 7/8.
* **Cost:** total Anthropic spend for the whole project is $9.00, under the $10 cap
  (`logs/spend_ledger.jsonl`).

## Limits

* Small sets: 38 answerable challenge questions and 14 traps. A one-question difference is not significant.
* The challenge set was revised once after a pilot (disclosed above), which makes it harder by design, not
  representative of real analyst traffic.
* Opus results are single runs, except the near-miss traps (three runs).
