"""Challenge set (v2): harder questions where the v1 golden set saturates. No hand-written answers.

Same rules as golden.py: every answerable expected value comes from the SQL path and is re-checked by the graph
path (`Builder.both`); disagreements are dropped and logged. Each template targets a capability v1 barely tests:

answerable
  c_acronym          supplier named only by its initials (needs the new acronym lookup)
  c_compare_two      two suppliers, compare dependent-office counts (two resolutions, two queries)
  c_rank_three       three suppliers, highest sole-source share (three resolutions, three queries)
  c_parent_total     a subsidiary named; answer is its parent's combined obligations (resolve -> profile -> parent)
  c_paraphrase_blast persona wording with no query vocabulary ("who would have to scramble")
  c_indirect_only    a detail field (offices reached only through subcontracts)
  c_program_ss_share arithmetic on a query result (sole-source obligations / total)
  c_shared_offices   set intersection of two suppliers' dependent offices
  c_office_filter    filter rows (offices paying the supplier > $1M directly) and count
  c_program_ratio_argmax  per-row ratio, then argmax (not simply the largest supplier)
  c_parent_award_sum sum a column across a parent's subsidiaries (the summary has no total)
The last four were added after a Haiku pilot scored 3 x 44/44 on the first draft's answerable questions
(draft kept as reports/challenge_set_draft.jsonl, its results as reports/comparison_challenge_draft.json).
The agent's prompt and tools were not changed in response.
unanswerable (traps)
  u_near_miss        a real supplier's name with one distinctive word swapped; absent, but the resolver returns
                     the real company with a high score. Correct behaviour: say it is not in the data.
  u_false_premise    asks why a per-year trend happened; the graph holds no per-year breakdown
  u_us_only_risk     escalation risk "where X performs"; X performs only in the USA, which the feed does not cover

Writes reports/challenge_set.jsonl and reports/challenge_dropped.jsonl. Run: `make challenge` (no API spend).
"""

from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import asdict

from rapidfuzz import fuzz, process

from supplier_graph.config import REPORTS
from supplier_graph.er.normalize import core_name
from supplier_graph.evalh.golden import SEED, Builder, GoldenItem, _qid

CHALLENGE_PATH = REPORTS / "challenge_set.jsonl"
CHALLENGE_DROPPED = REPORTS / "challenge_dropped.jsonl"
LS = "deterministic query (SQL) verified by graph traversal; challenge set"
SWAP_WORDS = ["AVIONICS", "HYDRAULICS", "SHIPYARD", "OPTICS", "ROTORCRAFT", "MUNITIONS", "RADAR", "PROPULSION"]


class ChallengeBuilder(Builder):
    def __init__(self) -> None:
        super().__init__()
        self.rng = random.Random(SEED + 200)
        self.all_cores = sorted({core_name(n) for (n,) in self.con.execute("SELECT name FROM suppliers").fetchall()})

    # ------------------------------------------------------------------ answerable
    def c_acronym(self, n: int = 4):
        from supplier_graph.agent.tools import _acronym_index, resolve_supplier
        cores = set(self.all_cores)
        risk = {sid: nd for sid, nd in self.con.execute(
            "SELECT supplier_id, n_dependent_offices FROM supplier_risk").fetchall()}
        pool = []
        for acr, sids in sorted(_acronym_index().items()):
            if len(sids) != 1 or not 4 <= len(acr) <= 5 or acr in cores or risk.get(sids[0], 0) < 2:
                continue
            c = resolve_supplier(acr)["candidates"]
            if c and c[0]["supplier_id"] == sids[0] and (len(c) == 1 or c[1]["match_score"] < c[0]["match_score"]):
                pool.append((acr, sids[0]))
        for acr, sid in self.pick(pool, n):
            r = self.both("dependent_offices", {"supplier_id": sid})
            if r:
                k = r[0]["summary"]["n_offices"]
                self.add(GoldenItem(_qid("c_acronym", acr), "c_acronym",
                         f"How many awarding offices depend on {acr}, directly or through subcontracts? Give the count.",
                         True, "int", k, expected_query="dependent_offices", expected_params={"supplier_id": sid},
                         alt_queries=["blast_radius"], required_citations=[f"supplier:{sid}"],
                         reference_facts={"acronym_of": sid, "n_offices": k},
                         label_source=LS + "; acronym synthetic (initials of the supplier's core name)"))

    def c_compare_two(self, n: int = 4):
        pool = self.unique_name_suppliers("n_dependent_offices >= 2 AND n_prime_awards >= 1")
        made = 0
        cand = self.pick(pool, len(pool))
        for i in range(0, len(cand) - 1, 2):
            (a, na), (b, nb) = cand[i], cand[i + 1]
            ra, rb = self.both("dependent_offices", {"supplier_id": a}), self.both("dependent_offices", {"supplier_id": b})
            if not ra or not rb:
                continue
            ka, kb = ra[0]["summary"]["n_offices"], rb[0]["summary"]["n_offices"]
            if ka == kb:
                continue
            win = a if ka > kb else b
            self.add(GoldenItem(_qid("c_compare_two", a + b), "c_compare_two",
                     f"Which supplier do more awarding offices depend on, directly or through subcontracts: {na} or {nb}? "
                     f"Answer with that supplier's supplier_id.", True, "id", win,
                     expected_query="dependent_offices", expected_params={"supplier_id": win}, alt_queries=["blast_radius"],
                     required_citations=[f"supplier:{a}", f"supplier:{b}"],
                     reference_facts={na: ka, nb: kb}, label_source=LS))
            made += 1
            if made >= n:
                break

    def c_rank_three(self, n: int = 4):
        pool = self.unique_name_suppliers("n_prime_awards >= 3 AND prime_obligations >= 1e6")
        cand = self.pick(pool, len(pool))
        made = 0
        for i in range(0, len(cand) - 2, 3):
            trio = cand[i:i + 3]
            shares = {}
            for sid, _ in trio:
                r = self.both("supplier_profile", {"supplier_id": sid})
                if r and r[0]["summary"]["sole_source_share"] is not None:
                    shares[sid] = r[0]["summary"]["sole_source_share"]
            if len(shares) < 3:
                continue
            ranked = sorted(shares.items(), key=lambda kv: -kv[1])
            if ranked[0][1] - ranked[1][1] < 0.05:
                continue
            names = ", ".join(nm for _, nm in trio[:2]) + f" and {trio[2][1]}"
            self.add(GoldenItem(_qid("c_rank_three", "".join(s for s, _ in trio)), "c_rank_three",
                     f"Among {names}, which has the highest sole-source (non-competed) share of its prime-contract "
                     f"obligations? Answer with its supplier_id.", True, "id", ranked[0][0],
                     expected_query="supplier_profile", expected_params={"supplier_id": ranked[0][0]},
                     required_citations=[f"supplier:{s}" for s, _ in trio],
                     reference_facts={nm: shares[s] for s, nm in trio}, label_source=LS))
            made += 1
            if made >= n:
                break

    def c_parent_total(self, n: int = 3):
        rows = self.con.execute("""SELECT s.supplier_id, s.name, s.parent_uei FROM suppliers s JOIN parents p USING (parent_uei)
                                   WHERE p.n_children BETWEEN 2 AND 15 AND s.supplier_id <> s.parent_uei
                                   ORDER BY s.supplier_id""").fetchall()
        allnames = Counter(core_name(nm) for (nm,) in self.con.execute("SELECT name FROM suppliers").fetchall())
        pool = [(sid, nm, p) for sid, nm, p in rows if allnames[core_name(nm)] == 1 and len(core_name(nm)) >= 5]
        seen: set[str] = set()
        for sid, nm, puei in self.pick(pool, len(pool)):
            if puei in seen:
                continue
            prof = self.both("supplier_profile", {"supplier_id": sid})
            par = self.both("parent_subsidiaries", {"parent_uei": puei})
            if not prof or not par or prof[0]["summary"]["parent_uei"] != puei:
                continue
            tot = par[0]["summary"]["total_prime_obligations"]
            if tot < 1e5:
                continue
            seen.add(puei)
            self.add(GoldenItem(_qid("c_parent_total", sid), "c_parent_total",
                     f"What are the combined prime-contract obligations, in dollars, of every supplier UEI that rolls up "
                     f"to the same parent company as {nm}?", True, "float", tot, tolerance=0.01, rel_tol=True,
                     expected_query="parent_subsidiaries", expected_params={"parent_uei": puei},
                     required_citations=[f"parent:{puei}"],
                     reference_facts={"subsidiary": sid, "parent_uei": puei, "total_prime_obligations": tot,
                                      "n_subsidiaries": par[0]["summary"]["n_subsidiaries"]}, label_source=LS))
            if len(seen) >= n:
                break

    def c_paraphrase_blast(self, n: int = 2):
        phr = ["I'm worried about {n}. If they went out of business tomorrow, how many different contracting offices "
               "would have to scramble? Just the number.",
               "Suppose {n} can no longer perform. Count the buying offices whose contracts would be hit, including "
               "contracts where {n} is only a subcontractor.",
               "How many of our contracting offices would feel it if {n} disappeared? A count is fine."]
        pool = self.unique_name_suppliers("n_dependent_offices >= 2")
        for i, (sid, nm) in enumerate(self.pick(pool, n)):
            r = self.both("blast_radius", {"supplier_id": sid})
            if r:
                k = r[0]["summary"]["n_offices"]
                self.add(GoldenItem(_qid("c_paraphrase_blast", sid), "c_paraphrase_blast", phr[i % 3].format(n=nm),
                         True, "int", k, expected_query="blast_radius", expected_params={"supplier_id": sid},
                         alt_queries=["dependent_offices"], required_citations=[f"supplier:{sid}"],
                         reference_facts={"n_offices": k}, label_source=LS))

    def c_indirect_only(self, n: int = 2):
        pool = self.unique_name_suppliers("n_subawards_received >= 1 AND n_prime_awards >= 1")
        made = 0
        for sid, nm in self.pick(pool, len(pool)):
            r = self.both("dependent_offices", {"supplier_id": sid})
            if not r or r[0]["summary"]["n_offices_indirect_only"] < 1:
                continue
            s = r[0]["summary"]
            self.add(GoldenItem(_qid("c_indirect_only", sid), "c_indirect_only",
                     f"How many awarding offices depend on {nm} only through subcontracts, with no prime award to {nm} "
                     f"from that office? Give the count.", True, "int", s["n_offices_indirect_only"],
                     expected_query="dependent_offices", expected_params={"supplier_id": sid},
                     required_citations=[f"supplier:{sid}"], reference_facts=s, label_source=LS))
            made += 1
            if made >= n:
                break

    def c_program_ss_share(self, n: int = 2):
        progs = [p for (p,) in self.con.execute("""SELECT program_code FROM awards_scope WHERE program_code IS NOT NULL
                 GROUP BY 1 HAVING count(DISTINCT supplier_id) >= 2 AND sum(obl_pos) >= 1e6
                 ORDER BY 1""").fetchall()]
        made = 0
        for code in self.pick(progs, len(progs)):
            r = self.both("program_suppliers", {"program_code": code})
            if not r:
                continue
            s = r[0]["summary"]
            share = s["sole_source_obligations"] / s["total_obligations"] if s["total_obligations"] else None
            if share is None or not 0.05 <= share <= 0.95:
                continue
            self.add(GoldenItem(_qid("c_program_ss_share", code), "c_program_ss_share",
                     f"What fraction of the obligations on DoD acquisition program {code} ({s['program_name']}) in this "
                     f"data went to non-competed (sole-source) awards? Answer as a fraction between 0 and 1.",
                     True, "float", round(share, 4), tolerance=0.01, expected_query="program_suppliers",
                     expected_params={"program_code": code}, required_citations=[f"program:{code}"],
                     reference_facts=s, label_source=LS + "; ratio of two verified sums"))
            made += 1
            if made >= n:
                break

    # ---------------------------------------------- reasoning over tool output (added after the draft saturated)
    def _dep_offices_sql(self, sid: str) -> dict:
        from supplier_graph.graph import queries as Q
        return Q.run("dependent_offices", {"supplier_id": sid}, "sql", self.con)

    def c_shared_offices(self, n: int = 5):
        """Set intersection of two suppliers' dependent offices (both lists fully visible: <= 10 rows each)."""
        pool = self.unique_name_suppliers("n_dependent_offices BETWEEN 2 AND 10")
        sets = {}
        for sid, nm in pool:
            r = self._dep_offices_sql(sid)
            if r["summary"]["n_offices"] <= 10:
                sets[sid] = (nm, frozenset(x["office_code"] for x in r["rows"]))
        by_office: dict[str, list[str]] = {}
        for sid, (_, offs) in sorted(sets.items()):
            for o in offs:
                by_office.setdefault(o, []).append(sid)
        pairs = sorted({tuple(sorted((a, b))) for sids in by_office.values() for a in sids for b in sids if a < b})
        made = 0
        for a, b in self.pick(pairs, len(pairs)):
            sa, sb = sets[a][1], sets[b][1]
            if sa <= sb or sb <= sa:
                continue  # one list inside the other makes the intersection trivial
            ra, rb = self.both("dependent_offices", {"supplier_id": a}), self.both("dependent_offices", {"supplier_id": b})
            if not ra or not rb:
                continue
            k = len(sa & sb)
            self.add(GoldenItem(_qid("c_shared_offices", a + b), "c_shared_offices",
                     f"How many awarding offices depend on both {sets[a][0]} and {sets[b][0]}, directly or through "
                     f"subcontracts? Give the count.", True, "int", k, expected_query="dependent_offices",
                     expected_params={"supplier_id": a}, required_citations=[f"supplier:{a}", f"supplier:{b}"],
                     reference_facts={"shared_offices": sorted(sa & sb)}, label_source=LS + "; set intersection"))
            made += 1
            if made >= n:
                break

    def c_office_filter(self, n: int = 5):
        """Filter a fully visible row list: offices paying the supplier > $1M directly."""
        pool = self.unique_name_suppliers("n_dependent_offices BETWEEN 3 AND 10 AND n_prime_awards >= 3")
        made = 0
        for sid, nm in self.pick(pool, len(pool)):
            r = self.both("dependent_offices", {"supplier_id": sid})
            if not r or r[0]["summary"]["n_offices"] > 10:
                continue
            k = sum(x["direct_obligations"] > 1e6 for x in r[0]["rows"])
            if not 1 <= k < r[0]["summary"]["n_offices"]:
                continue
            self.add(GoldenItem(_qid("c_office_filter", sid), "c_office_filter",
                     f"Of the awarding offices that depend on {nm}, how many paid {nm} more than $1 million directly as "
                     f"a prime contractor? Give the count.", True, "int", k, expected_query="dependent_offices",
                     expected_params={"supplier_id": sid}, required_citations=[f"supplier:{sid}"],
                     reference_facts={"offices_over_1m": [x["office_code"] for x in r[0]["rows"]
                                                          if x["direct_obligations"] > 1e6]},
                     label_source=LS + "; row filter"))
            made += 1
            if made >= n:
                break

    def c_program_ratio_argmax(self, n: int = 5):
        """Per-row ratio then argmax, where the answer is not simply the largest supplier."""
        progs = [p for (p,) in self.con.execute("""SELECT program_code FROM awards_scope WHERE program_code IS NOT NULL
                 GROUP BY 1 HAVING count(DISTINCT supplier_id) BETWEEN 3 AND 10 ORDER BY 1""").fetchall()]
        made = 0
        for code in self.pick(progs, len(progs)):
            r = self.both("program_suppliers", {"program_code": code})
            if not r or r[0]["summary"]["n_suppliers"] > 10:
                continue
            rows = [x for x in r[0]["rows"] if x["obligations"] > 0]
            ratios = sorted(((x["sole_source_obligations"] / x["obligations"], x["supplier_id"]) for x in rows),
                            reverse=True)
            if len(ratios) < 3 or ratios[0][0] - ratios[1][0] < 0.05:
                continue
            biggest = max(rows, key=lambda x: x["obligations"])["supplier_id"]
            if ratios[0][1] == biggest:
                continue
            self.add(GoldenItem(_qid("c_program_ratio_argmax", code), "c_program_ratio_argmax",
                     f"On DoD acquisition program {code} ({r[0]['summary']['program_name']}), which supplier had the "
                     f"highest share of its own program obligations sole-sourced (not competed)? Answer with its "
                     f"supplier_id.", True, "id", ratios[0][1], expected_query="program_suppliers",
                     expected_params={"program_code": code}, required_citations=[f"supplier:{ratios[0][1]}"],
                     reference_facts={"ratios": {sid: round(v, 4) for v, sid in ratios}},
                     label_source=LS + "; per-row ratio then argmax"))
            made += 1
            if made >= n:
                break

    def c_parent_award_sum(self, n: int = 5):
        """Sum a column over a parent's subsidiaries (the summary has no award total)."""
        rows = self.con.execute("""SELECT s.supplier_id, s.name, s.parent_uei FROM suppliers s JOIN parents p USING (parent_uei)
                                   WHERE p.n_children BETWEEN 2 AND 10 AND s.supplier_id <> s.parent_uei
                                   ORDER BY s.supplier_id""").fetchall()
        allnames = Counter(core_name(nm) for (nm,) in self.con.execute("SELECT name FROM suppliers").fetchall())
        pool = [(sid, nm, p) for sid, nm, p in rows if allnames[core_name(nm)] == 1 and len(core_name(nm)) >= 5]
        seen: set[str] = set()
        for sid, nm, puei in self.pick(pool, len(pool)):
            if puei in seen:
                continue
            par = self.both("parent_subsidiaries", {"parent_uei": puei})
            prof = self.both("supplier_profile", {"supplier_id": sid})
            if not par or not prof or prof[0]["summary"]["parent_uei"] != puei or par[0]["summary"]["n_subsidiaries"] > 10:
                continue
            total = sum(x["n_prime_awards"] for x in par[0]["rows"])
            if total < 2 or sum(x["n_prime_awards"] > 0 for x in par[0]["rows"]) < 2:
                continue  # need at least two contributing subsidiaries, or the sum is a lookup
            seen.add(puei)
            self.add(GoldenItem(_qid("c_parent_award_sum", sid), "c_parent_award_sum",
                     f"Across every supplier UEI that rolls up to the same parent company as {nm}, how many prime awards "
                     f"are there in total? Give the count.", True, "int", total, expected_query="parent_subsidiaries",
                     expected_params={"parent_uei": puei}, required_citations=[f"parent:{puei}"],
                     reference_facts={"per_subsidiary": {x["supplier_id"]: x["n_prime_awards"] for x in par[0]["rows"]}},
                     label_source=LS + "; column sum over rows"))
            if len(seen) >= n:
                break

    # ------------------------------------------------------------------ unanswerable traps
    def u_near_miss(self, n: int = 8):
        from supplier_graph.agent.tools import resolve_supplier
        pool = [(sid, nm) for sid, nm in self.unique_name_suppliers("n_dependent_offices >= 1")
                if len([t for t in core_name(nm).split() if t.isalpha() and len(t) >= 4]) >= 2]
        made = 0
        for sid, nm in self.pick(pool, len(pool)):
            toks = core_name(nm).split()
            idx = max(i for i, t in enumerate(toks) if t.isalpha() and len(t) >= 4 and i > 0) if len(toks) > 1 else None
            if idx is None:
                continue
            word = self.rng.choice([w for w in SWAP_WORDS if w not in toks])
            fake_core = " ".join(toks[:idx] + [word] + toks[idx + 1:])
            hit = process.extractOne(fake_core, self.all_cores, scorer=fuzz.token_sort_ratio)
            if hit and hit[1] >= 90:
                continue  # too close to something real: not verifiably absent
            fake = fake_core.title() + " LLC"
            cands = resolve_supplier(fake)["candidates"]
            if not cands or cands[0]["supplier_id"] != sid:
                continue  # the trap needs the real company as the resolver's top look-alike
            self.add(GoldenItem(_qid("u_near_miss", fake), "u_near_miss",
                     f"How many awarding offices depend on {fake}, directly or through subcontracts?", False, "null", None,
                     expected_query="resolve_supplier", expected_params={"name": fake},
                     reference_facts={"why": "no supplier with this name; a real company differs by one word",
                                      "look_alike": cands[0]["name"], "look_alike_score": cands[0]["match_score"]},
                     label_source="synthetic near-miss name (verified: fuzzy scan < 90 against every supplier name)"))
            made += 1
            if made >= n:
                break

    def u_false_premise(self, n: int = 3):
        phr = ["Why did {n}'s sole-source share rise between FY2023 and FY2025?",
               "By how much did {n}'s obligations grow from FY2023 to FY2025?",
               "In which fiscal year did {n} receive the most obligations?"]
        pool = self.unique_name_suppliers("n_prime_awards >= 5")
        for i, (_sid, nm) in enumerate(self.pick(pool, n)):
            q = phr[i % 3].format(n=nm)
            self.add(GoldenItem(_qid("u_false_premise", q), "u_false_premise", q, False, "null", None,
                     expected_query=None, reference_facts={"why": "the graph holds lifetime totals, no per-year breakdown"},
                     label_source="schema metadata (no per-fiscal-year fields in the graph)"))

    def u_us_only_risk(self, n: int = 3):
        pool = self.unique_name_suppliers("""n_prime_awards >= 2 AND coalesce(foreign_share, 0) = 0
                                              AND coalesce(len(feed_countries), 0) = 0""")
        for sid, nm in self.pick(pool, n):
            r = self.both("supplier_profile", {"supplier_id": sid})
            if not r or r[0]["summary"]["foreign_countries"]:
                continue
            self.add(GoldenItem(_qid("u_us_only_risk", sid), "u_us_only_risk",
                     f"What is the escalation probability in the countries where {nm} performs its work?", False, "null",
                     None, expected_query="supplier_profile", expected_params={"supplier_id": sid},
                     reference_facts={"why": "the supplier performs only in the USA; the risk feed covers 14 MENA countries"},
                     label_source="feed coverage metadata + verified US-only place of performance"))

    def build(self) -> list[GoldenItem]:  # type: ignore[override]
        for t in (self.c_acronym, self.c_compare_two, self.c_rank_three, self.c_parent_total, self.c_paraphrase_blast,
                  self.c_indirect_only, self.c_program_ss_share, self.c_shared_offices, self.c_office_filter,
                  self.c_program_ratio_argmax, self.c_parent_award_sum, self.u_near_miss, self.u_false_premise,
                  self.u_us_only_risk):
            t()
        seen: set[str] = set()
        self.items = [i for i in self.items if not (i.qid in seen or seen.add(i.qid))]
        CHALLENGE_PATH.write_text("".join(json.dumps(asdict(i), default=str) + "\n" for i in self.items))
        CHALLENGE_DROPPED.write_text("".join(json.dumps(d, default=str) + "\n" for d in self.dropped))
        self.con.close()
        return self.items


def load_challenge() -> list[GoldenItem]:
    return [GoldenItem(**json.loads(line)) for line in CHALLENGE_PATH.read_text().splitlines() if line.strip()]


if __name__ == "__main__":
    items = ChallengeBuilder().build()
    c = Counter(i.template for i in items)
    print(json.dumps(dict(c), indent=2), "\ntotal", len(items), "answerable", sum(i.answerable for i in items))
