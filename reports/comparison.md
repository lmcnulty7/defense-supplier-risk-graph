# Agent comparison: models, tools and the grounding guard

Scored 2026-11-20T07:13:29.937756+00:00 by `make compare-offline` from committed logs (`logs/agent_runs/cmp_*.jsonl` and the
main eval log). Same deterministic scorer as `reports/eval_report.md`. Portfolio project on public data; fictional persona.

## Bottom line

Results use the agent's current prompt (answers the answerable part of a question, cites nothing for the missing part)
and its five tools, including `what_if_remove`. Runs under the earlier prompt are not mixed in.

1. **The graph is what answers the questions.** Without tools, Opus declines everything on the challenge set
   (0/44 answerable). It does not invent answers, but it cannot help.
2. **With named, verified queries, structured questions are easy even for a small model.** Haiku 5.5 scores
   43/44 / 44/44 / 43/44 on the answerable challenge questions in three
   runs (its misses: c_what_if_top_alt-7b88b430, c_what_if_top_alt-f8972b7e) at about 1/52 of Opus's cost per question on this set. On the v1 set it scores
   83/88 / 83/88 / 83/88 vs Opus 87/88; of Haiku's misses per run,
   3 / 3 / 3 are output-contract violations (a dict in the answer field that contains the right value).
3. **The real failure mode is entity grounding.** On near-miss names (a real supplier with one distinctive word
   swapped), Haiku often answered for the look-alike: it caught 4/8, 4/8, 4/8
   across runs. Opus caught 7/8.
4. **A tool-side fix closes the gap.** The resolver's grounding guard lists query words the matched name lacks and
   tells the model not to substitute silently. Haiku with the guard: 8/8, 8/8, 8/8,
   with 44/44 / 44/44 / 44/44 on answerable challenge questions
   (v1 with the guard: 83/88 / 83/88 / 82/88).
   Replaying the 96 logged Opus resolver calls on answerable questions, the guard fires on
   0, so it cannot change those answers. It is on by default (`RESOLVER_GUARD=1`).
5. **What-if questions** (the `what_if_remove` tool; 6 answerable plus 2 traps: a look-alike company and
   "how many months would a switch take"): Opus 6/6 + 2/2, Haiku 5/6 + 2/2, 6/6 + 2/2, 5/6 + 2/2,
   Haiku with the guard 6/6 + 2/2, 6/6 + 2/2, 6/6 + 2/2 (answerable correct / traps declined, per run).

Opus's near-miss miss (`u_near_miss-ebf5d8f3`) is the most debatable label: the look-alike shares the other distinctive words, and Opus said the matched name lacks the swapped word and called it a likely rather than certain match. It is scored as a miss, as labelled before the runs.

The first draft of the challenge set (`reports/challenge_set_draft.jsonl`) saturated: Haiku scored 44/44 / 44/44 / 44/44 on its answerable questions in three runs. Four reasoning-over-output templates were added and the near-miss traps increased; the agent's prompt and tools were not changed in response. The final set below was then run as listed for each configuration.

## Challenge set (v2): 44 answerable + 16 traps

Repeated configurations show each run separated by "/". 95% CIs are Wilson intervals (single-run rows only).

| configuration | runs | answerable correct [95% CI] | refusals correct [95% CI] | fabricated IDs | cost / question | latency |
|---|---|---|---|---|---|---|
| Opus 5.5 + tools (shipped agent, guard off) | 1 | 44/44 [0.92, 1.00] | 15/16 [0.72, 0.99] | 0 | $0.0447 | 9.4 s |
| Haiku 5.5 + tools (guard off) | 3 | 43/44 / 44/44 / 43/44 | 12/16 / 12/16 / 12/16 | 0 / 0 / 0 | $0.0009 | 4.8 s |
| Haiku 5.5 + tools + grounding guard | 3 | 44/44 / 44/44 / 44/44 | 15/16 / 16/16 / 16/16 | 0 / 0 / 0 | $0.0009 | 4.9 s |
| Opus 5.5, no tools | 1 | 0/44 [0.00, 0.08] | 16/16 [0.81, 1.00] | 0 | $0.0067 | 3.5 s |

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
| c_what_if_no_alt | 2/2 | 2/2 / 2/2 / 2/2 | 2/2 / 2/2 / 2/2 | 0/2 |
| c_what_if_pair | 2/2 | 2/2 / 2/2 / 2/2 | 2/2 / 2/2 / 2/2 | 0/2 |
| c_what_if_top_alt | 2/2 | 1/2 / 2/2 / 1/2 | 2/2 / 2/2 / 2/2 | 0/2 |
| u_false_premise | 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 |
| u_near_miss | 7/8 | 4/8 / 4/8 / 4/8 | 8/8 / 8/8 / 8/8 | 8/8 |
| u_us_only_risk | 3/3 | 3/3 / 3/3 / 3/3 | 2/3 / 3/3 / 3/3 | 3/3 |
| u_what_if_absent | 1/1 | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 | 1/1 |
| u_what_if_capacity | 1/1 | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 | 1/1 |

## v1 golden set: 88 answerable + 13 unanswerable

| configuration | runs | answerable correct [95% CI] | refusals correct [95% CI] | fabricated IDs | cost / question | latency |
|---|---|---|---|---|---|---|
| Opus 5.5 + tools (shipped agent, guard off) | 1 | 87/88 [0.94, 1.00] | 13/13 [0.77, 1.00] | 0 | $0.0374 | 7.3 s |
| Haiku 5.5 + tools (guard off) | 3 | 83/88 / 83/88 / 83/88 | 13/13 / 13/13 / 13/13 | 1 / 0 / 0 | $0.0007 | 3.9 s |
| Haiku 5.5 + tools + grounding guard | 3 | 83/88 / 83/88 / 82/88 | 13/13 / 13/13 / 13/13 | 0 / 0 / 3 | $0.0007 | 3.8 s |

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
| hard_noisy_name | 2/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 2/3 |
| hard_office_flagged_blast | 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
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
* **Run-to-run variance (Haiku, three runs each):** answerable accuracy across runs is 43/44 to 44/44
  (challenge), identical (v1), 82/88 to 83/88 (v1 with guard). **Opus:** each set ran
  once with the current prompt (the challenge run cost $2.68), so its run-to-run variance is not measured here.
* **Cost:** total Anthropic spend for the whole project is $16.74, under the $20 cap
  (raised from $10 on 2026-11-19; `logs/spend_ledger.jsonl`).

## Limits

* Small sets: 44 answerable challenge questions and 16 traps. A one-question difference is not significant.
* The challenge set was revised once after a pilot (disclosed above) and extended with eight what-if questions when
  the `what_if_remove` tool was added; it is harder by design, not representative of real analyst traffic.
* Opus results are single runs.
