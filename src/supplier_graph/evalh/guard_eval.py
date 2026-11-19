"""Offline check of the resolver's grounding guard (agent/tools.missing_words); no API calls.

The guard flags query words that the matched supplier's name lacks. It must stay quiet for the same company written
differently (typos, abbreviations, suffixes) and fire for a different company with a similar name. Measured on:
  * the ER label pairs (test split): same-UEI pairs and synthetic perturbations should NOT flag;
    hard negatives (similar names, different UEI and parent) SHOULD flag;
  * the agent eval questions that name a supplier with noise (hard_noisy_name) or a near-miss (u_near_miss), by
    running the actual resolver.
Writes reports/guard_eval.json. Run: `make guard-eval`.
"""

from __future__ import annotations

import json
import re

from supplier_graph.agent.tools import missing_words, resolve_supplier
from supplier_graph.config import REPORTS
from supplier_graph.er.labels import build_labels
from supplier_graph.er.normalize import core_name
from supplier_graph.stage import connect


def run() -> dict:
    con = connect(read_only=True)
    labels = build_labels(con.execute("SELECT * FROM entity_records ORDER BY record_id").df())
    con.close()
    test = labels[labels.split == "test"]
    out: dict = {"er_pairs_test": {}}
    # direction matters: the guard asks whether the *query* (record b, the perturbed or second record) has words
    # the *candidate* (record a) lacks
    test = test.assign(flag=[bool(missing_words(core_name(b), core_name(a)))
                             for a, b in zip(test["a_name"], test["b_name"], strict=True)])
    for src, sub in test.groupby("label_source"):
        out["er_pairs_test"][src] = {"n": int(len(sub)), "flag_rate": round(float(sub["flag"].mean()), 4)}
    syn = test[test.label_source == "synthetic:pos"]
    na = syn[~syn["perturbations"].str.contains("acronym", na=False)]
    out["synthetic_pos_excluding_acronym"] = {"n": int(len(na)), "flag_rate": round(float(na["flag"].mean()), 4),
                                              "note": "acronym queries bypass the guard (resolved by the acronym rule)"}
    out["synthetic_pos_flag_rate_by_perturbation"] = {
        p: {"n": int(m.sum()), "flag_rate": round(float(syn.loc[m, "flag"].mean()), 4)}
        for p in ("abbreviation", "punctuation", "suffix", "typo", "address", "case", "acronym", "token_drop")
        for m in [syn["perturbations"].str.contains(p, na=False)] if m.any()}
    qs = [json.loads(line) for p in ("golden_set.jsonl", "challenge_set.jsonl")
          for line in (REPORTS / p).read_text().splitlines() if line.strip()]
    rows = []
    for d in qs:
        if d["template"] not in ("hard_noisy_name", "u_near_miss"):
            continue
        m = re.search(r"depend on (.+?), directly", d["question"])
        r = resolve_supplier(m.group(1))
        rows.append({"qid": d["qid"], "template": d["template"], "name": m.group(1),
                     "flagged": r.get("exact_name_match") is False})
    out["agent_questions"] = {
        t: {"n": sum(r["template"] == t for r in rows), "flagged": sum(r["flagged"] for r in rows if r["template"] == t)}
        for t in ("hard_noisy_name", "u_near_miss")}
    out["agent_question_rows"] = rows
    # replay every logged Opus resolver call (guard was off when they ran): would the guard have fired?
    import glob

    from supplier_graph.agent import tools as T
    from supplier_graph.evalh.challenge import load_challenge
    from supplier_graph.evalh.golden import load_golden
    T.GUARD = True
    answerable = {i.qid: i.answerable for i in load_golden() + load_challenge()}
    logs = sorted(glob.glob(str(REPORTS.parent / "logs/agent_runs/eval_*.jsonl")))[-1:] + \
        sorted(glob.glob(str(REPORTS.parent / "logs/agent_runs/cmp_challenge_opus_tools_r*.jsonl")))
    replay = {"answerable": {"calls": 0, "flagged": 0}, "unanswerable": {"calls": 0, "flagged": 0}}
    for path in logs:
        for line in open(path).read().splitlines():  # noqa: SIM115
            if not line.strip():
                continue
            r = json.loads(line)
            q = r.get("tags", {}).get("qid")
            if q not in answerable:
                continue
            for t in r["tool_calls"]:
                if t["name"] == "resolve_supplier":
                    k = "answerable" if answerable[q] else "unanswerable"
                    replay[k]["calls"] += 1
                    replay[k]["flagged"] += resolve_supplier(t["input"]["name"]).get("exact_name_match") is False
    out["replay_logged_opus_resolver_calls"] = replay
    (REPORTS / "guard_eval.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k != "agent_question_rows"}, indent=2))
    return out


if __name__ == "__main__":
    run()
