"""reports/comparison.md from reports/comparison.json (`make compare-offline`)."""

from __future__ import annotations

import json

from supplier_graph.config import REPORTS

LABEL = {"opus_tools": "Opus 5.5 + tools (shipped agent, guard off)", "haiku_tools": "Haiku 5.5 + tools (guard off)",
         "opus_no_tools": "Opus 5.5, no tools", "haiku_tools_guard": "Haiku 5.5 + tools + grounding guard",
         "opus_tools_guard": "Opus 5.5 + tools + grounding guard"}
ORDER = ["opus_tools", "opus_tools_guard", "haiku_tools", "haiku_tools_guard", "opus_no_tools"]


def _ci(x) -> str:
    return f"[{x[0]:.2f}, {x[1]:.2f}]" if x else "-"


def _row(config: str, cell: dict) -> str:
    reps = cell.get("reps") or []
    if not reps:
        return f"| {LABEL[config]} | incomplete | | | | | |"
    r0 = reps[0]
    if len(reps) > 1:
        ans = " / ".join(r["answerable_correct"] for r in reps)
        ref = " / ".join(r["refusals_correct"] for r in reps)
        cost = sum(r["cost_mean_usd"] for r in reps) / len(reps)
        lat = sum(r["latency_mean_s"] for r in reps) / len(reps)
        fab = " / ".join(str(r["n_fabricated_citations"]) for r in reps)
    else:
        ans = f"{r0['answerable_correct']} {_ci(r0['answer_correctness_ci95'])}"
        ref = f"{r0['refusals_correct']} {_ci(r0['correct_refusal_ci95'])}"
        cost, lat, fab = r0["cost_mean_usd"], r0["latency_mean_s"], str(r0["n_fabricated_citations"])
    return f"| {LABEL[config]} | {len(reps)} | {ans} | {ref} | {fab} | ${cost:.4f} | {lat:.1f} s |"


def _table(res: dict, qset: str) -> str:
    rows = ["| configuration | runs | answerable correct [95% CI] | refusals correct [95% CI] | fabricated IDs | "
            "cost / question | latency |", "|---|---|---|---|---|---|---|"]
    for c in ORDER:
        cell = res["cells"].get(f"{qset}/{c}")
        if cell:
            rows.append(_row(c, cell))
    return "\n".join(rows)


def _templates(res: dict, qset: str) -> str:
    cfgs = [c for c in ORDER if res["cells"].get(f"{qset}/{c}", {}).get("reps")]
    temps = sorted({t for c in cfgs for t in res["cells"][f"{qset}/{c}"]["reps"][0]["by_template"]})
    head = "| template | " + " | ".join(LABEL[c].split(" (")[0] for c in cfgs) + " |"
    rows = [head, "|---|" + "---|" * len(cfgs)]
    for t in temps:
        cells = []
        for c in cfgs:
            reps = res["cells"][f"{qset}/{c}"]["reps"]
            cells.append(" / ".join(f"{r['by_template'][t]['correct']}/{r['by_template'][t]['n']}"
                                    for r in reps if t in r["by_template"]))
        rows.append(f"| {t} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def write_report(res: dict) -> None:
    guard = json.loads((REPORTS / "guard_eval.json").read_text()) if (REPORTS / "guard_eval.json").exists() else None
    subtle = json.loads((REPORTS / "judge_validation_subtle.json").read_text()) \
        if (REPORTS / "judge_validation_subtle.json").exists() else None
    draft = json.loads((REPORTS / "comparison_challenge_draft.json").read_text()) \
        if (REPORTS / "comparison_challenge_draft.json").exists() else None
    c = res["cells"]

    def reps(k: str) -> list[dict]:
        return c.get(k, {}).get("reps") or []

    def contract_dicts(k: str) -> list[int]:
        return [sum(isinstance(f["got"], dict) for f in r["failures"]) for r in reps(k)]

    def spread(k: str) -> str:
        vals = sorted({r["answerable_correct"] for r in reps(k)})
        return "identical" if len(vals) == 1 else f"{vals[0]} to {vals[-1]}"

    oc = reps("challenge/opus_tools")[0]["cost_mean_usd"] if reps("challenge/opus_tools") else None
    hc = sum(r["cost_mean_usd"] for r in reps("challenge/haiku_tools")) / max(len(reps("challenge/haiku_tools")), 1)
    ratio = f"about 1/{round(oc / hc)}" if oc and hc else "a fraction"
    rp = guard.get("replay_logged_opus_resolver_calls", {}).get("answerable") if guard else None
    def nm(k: str) -> list:
        full = [r["by_template"].get("u_near_miss", {}).get("correct") for r in reps(k)]
        part = [p["u_near_miss"]["correct"] for p in c.get(k, {}).get("partial_reps_by_template", [])
                if "u_near_miss" in p]
        return full + part

    opus_cost = sum(r["cost_mean_usd"] * (r["n_answerable"] + r["n_unanswerable"]) for r in reps("challenge/opus_tools"))
    gtxt = ""
    if guard:
        er = guard["er_pairs_test"]
        gtxt = (f"Offline, the guard flags {guard['agent_questions']['u_near_miss']['flagged']}/"
                f"{guard['agent_questions']['u_near_miss']['n']} near-miss names and "
                f"{guard['agent_questions']['hard_noisy_name']['flagged']}/{guard['agent_questions']['hard_noisy_name']['n']}"
                f" noisy-but-correct names. On the ER test pairs it fires on "
                f"{guard['synthetic_pos_excluding_acronym']['flag_rate']:.2%} of synthetic same-company pairs "
                f"(acronyms excluded; they bypass the guard), {er['identifier:uei']['flag_rate']:.1%} of same-UEI pairs "
                f"(mostly genuine renames, e.g. Turbo Systems to Accelleron) and {er['identifier:neg']['flag_rate']:.1%} "
                f"of different-company hard negatives. It is a targeted warning for a swapped distinctive word, not a "
                f"general disambiguator. (`reports/guard_eval.json`, `make guard-eval`)")
    dtxt = ""
    if draft and "challenge/haiku_tools" in draft.get("cells", {}):
        d = draft["cells"]["challenge/haiku_tools"]["reps"]
        dtxt = (f"The first draft of the challenge set (`reports/challenge_set_draft.jsonl`) saturated: Haiku scored "
                f"{' / '.join(r['answerable_correct'] for r in d)} on its answerable questions in three runs. Four "
                f"reasoning-over-output templates were added and the near-miss traps increased; the agent's prompt and "
                f"tools were not changed in response. The final set below was then run once per configuration.")
    sub = ""
    if subtle:
        by_case = ", ".join(f"{k}: {v['agreement']:.0%} of {v['n']}" for k, v in subtle["agreement_by_case"].items())
        sub = (f"* **Judge on subtle errors:** on real agent prose, agreement {subtle['agreement']:.0%} over "
               f"{subtle['n_cases']} cases ({by_case}). `reports/judge_validation_subtle.json`.")
    text = f"""# Agent comparison: models, tools and the grounding guard

Scored {res['scored_at_utc']} by `make compare-offline` from committed logs (`logs/agent_runs/cmp_*.jsonl` and the
main eval log). Same deterministic scorer as `reports/eval_report.md`. Portfolio project on public data; fictional persona.

## Bottom line

1. **The graph is what answers the questions.** Without tools, Opus declines everything on the challenge set
   ({reps('challenge/opus_no_tools')[0]['answerable_correct'] if reps('challenge/opus_no_tools') else '-'} answerable). It does not invent answers, but it cannot help.
2. **With named, verified queries, structured questions are easy even for a small model.** Haiku 5.5 answers every
   answerable challenge question in three runs ({' / '.join(r['answerable_correct'] for r in reps('challenge/haiku_tools'))})
   at {ratio} of Opus's cost per question on this set. On the v1 set it scores {' / '.join(r['answerable_correct'] for r in reps('golden/haiku_tools'))}
   vs Opus {reps('golden/opus_tools')[0]['answerable_correct'] if reps('golden/opus_tools') else '-'}; of Haiku's misses per run, {' / '.join(map(str, contract_dicts('golden/haiku_tools')))} are output-contract
   violations (a dict in the answer field that contains the right value).
3. **The real failure mode is entity grounding.** On near-miss names (a real supplier with one distinctive word
   swapped), Haiku silently answered for the look-alike: correct on {', '.join(f'{x}/8' for x in nm('challenge/haiku_tools'))} of 8 traps across
   runs. Opus got {', '.join(f'{x}/8' for x in nm('challenge/opus_tools'))} (the near-miss traps were re-run twice for Opus to
   measure its variance there; see *Run-to-run variance*).
4. **A tool-side fix closes most of the gap.** The resolver's grounding guard lists query words the matched name
   lacks and tells the model not to substitute silently. Haiku with the guard: {', '.join(f'{x}/8' for x in nm('challenge/haiku_tools_guard'))} of 8,
   with no loss on answerable challenge questions (v1 with the guard: {' / '.join(r['answerable_correct'] for r in reps('golden/haiku_tools_guard'))}).
   Replaying the {rp['calls'] if rp else '?'} logged Opus resolver calls on answerable questions, the guard fires on
   {rp['flagged'] if rp else '?'}, so it cannot change those answers. It is on by default (`RESOLVER_GUARD=1`).

The one trap every configuration missed (`u_near_miss-ebf5d8f3`, "Execujet Mro Svcs Middle East Dwc Optics") is the
most debatable label: the look-alike shares five distinctive words, and both models said they were substituting the
closest candidate rather than doing it silently. It is scored as a miss, as labelled before the runs.

{dtxt}

## Challenge set (v2): {reps('challenge/opus_tools')[0]['n_answerable'] if reps('challenge/opus_tools') else '?'} answerable + {reps('challenge/opus_tools')[0]['n_unanswerable'] if reps('challenge/opus_tools') else '?'} traps

Repeated configurations show each run separated by "/". 95% CIs are Wilson intervals (single-run rows only).

{_table(res, 'challenge')}

### By template

{_templates(res, 'challenge')}

## v1 golden set: 88 answerable + 13 unanswerable

{_table(res, 'golden')}

### By template

{_templates(res, 'golden')}

## Grounding guard, offline

{gtxt}

## Other evaluation checks

{sub}
* **Run-to-run variance (Haiku, three runs each):** answerable accuracy across runs is {spread('challenge/haiku_tools')}
  (challenge), {spread('golden/haiku_tools')} (v1), {spread('golden/haiku_tools_guard')} (v1 with guard); trap refusals vary by up
  to two questions. **Opus:** the full challenge set ran once (one run cost ${opus_cost:.2f}; two more would take total
  spend past the $10 cap), so its answerable-question variance is not measured. Its eight near-miss traps, where
  variance matters most for the guard comparison, were re-run twice more: {', '.join(f'{x}/8' for x in nm('challenge/opus_tools'))}.
* **Cost:** total Anthropic spend for the whole project is ${res['anthropic_spend_total_usd']:.2f}, under the $10 cap
  (`logs/spend_ledger.jsonl`).

## Limits

* Small sets: 38 answerable challenge questions and 14 traps. A one-question difference is not significant.
* The challenge set was revised once after a pilot (disclosed above), which makes it harder by design, not
  representative of real analyst traffic.
* Opus results are single runs, except the near-miss traps (three runs).
"""
    (REPORTS / "comparison.md").write_text(text)
