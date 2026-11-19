"""Golden question set, generated from templates over the data. No hand-written answers.

* Answerable questions: expected answers are computed by the SQL path (DuckDB) and verified with the
  independent graph path (NetworkX traversal). A question whose two answers disagree is dropped and logged.
* Unanswerable questions are built from entities that are verifiably absent: invented supplier names that
  the resolver and a fuzzy scan both fail to find, countries outside the risk feed, award IDs and program
  codes that don't exist, and questions that need fields the graph does not hold.
Label source for every item is recorded in `label_source`.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from dataclasses import asdict, dataclass, field

from rapidfuzz import fuzz, process

from supplier_graph.config import REPORTS
from supplier_graph.er.normalize import core_name
from supplier_graph.graph import queries as Q
from supplier_graph.graph.build import load_graph
from supplier_graph.graph.schema import nid
from supplier_graph.stage import connect

SEED = 7
PER_TEMPLATE = 5
GOLDEN_PATH = REPORTS / "golden_set.jsonl"
DROPPED_PATH = REPORTS / "golden_dropped.jsonl"


@dataclass
class GoldenItem:
    qid: str
    template: str
    question: str
    answerable: bool
    answer_type: str            # int | float | id | ids | bool | null
    expected: object
    tolerance: float = 0.0       # absolute for float shares; relative if rel_tol
    rel_tol: bool = False
    expected_query: str | None = None       # the named query (or tool) a correct trajectory uses
    expected_params: dict = field(default_factory=dict)
    alt_queries: list[str] = field(default_factory=list)   # equivalent named queries that can also answer it
    required_citations: list[str] = field(default_factory=list)
    reference_facts: dict = field(default_factory=dict)  # for the LLM judge
    # when True, a correct value without the required citations is scored wrong (the value alone can't show the
    # agent looked up the right entity, e.g. p_escalation is ~0.065 for every feed country)
    strict_required_citations: bool = False
    label_source: str = "deterministic query (SQL) verified by graph traversal"


def _qid(template: str, key: str) -> str:
    return f"{template}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"


def _canon(x) -> str:
    return json.dumps(x, sort_keys=True, default=str)


class Builder:
    def __init__(self) -> None:
        self.con = connect(read_only=True)
        self.rng = random.Random(SEED)
        self.items: list[GoldenItem] = []
        self.dropped: list[dict] = []

    # ----------------------------------------------------------- helpers
    def both(self, name: str, params: dict) -> tuple[dict, dict] | None:
        s = Q.run(name, params, "sql", self.con)
        g = Q.run(name, params, "graph")
        if _canon(s) != _canon(g):
            self.dropped.append({"query": name, "params": params, "reason": "sql/graph disagreement",
                                 "sql_summary": s["summary"], "graph_summary": g["summary"]})
            return None
        return s, g

    def pick(self, pool: list, n: int = PER_TEMPLATE) -> list:
        pool = sorted(pool)
        return self.rng.sample(pool, min(n, len(pool)))

    def unique_name_suppliers(self, where: str) -> list[tuple[str, str]]:
        rows = self.con.execute(f"SELECT supplier_id, name FROM supplier_risk WHERE {where} ORDER BY supplier_id").fetchall()
        allnames = Counter(core_name(n) for (n,) in self.con.execute("SELECT name FROM suppliers").fetchall())
        return [(sid, n) for sid, n in rows if allnames[core_name(n)] == 1 and len(core_name(n)) >= 4]

    def add(self, item: GoldenItem) -> None:
        item.question = " ".join(item.question.split())
        self.items.append(item)

    # ----------------------------------------------------------- answerable templates
    def t_dependent_offices(self):
        for sid, name in self.pick(self.unique_name_suppliers("n_dependent_offices >= 2 AND n_prime_awards >= 2")):
            r = self.both("dependent_offices", {"supplier_id": sid})
            if r:
                s = r[0]["summary"]
                self.add(GoldenItem(_qid("dependent_offices_count", sid), "dependent_offices_count",
                         f"How many awarding offices depend on {name}, either as a prime contractor or through "
                         f"subcontracts on their awards? Give the count.", True, "int", s["n_offices"],
                         expected_query="dependent_offices", expected_params={"supplier_id": sid},
                         alt_queries=["blast_radius"], required_citations=[f"supplier:{sid}"],
                         reference_facts={"n_offices": s["n_offices"], "offices": [x["office_code"] for x in r[0]["rows"]]}))

    def t_sole_source_share(self):
        for sid, name in self.pick(self.unique_name_suppliers("n_prime_awards >= 3 AND prime_obligations >= 1e6")):
            r = self.both("supplier_profile", {"supplier_id": sid})
            if r and r[0]["summary"]["sole_source_share"] is not None:
                s = r[0]["summary"]
                self.add(GoldenItem(_qid("sole_source_share", sid), "sole_source_share",
                         f"What share of {name}'s prime-contract obligations were not competed (sole-source)? "
                         f"Answer as a fraction between 0 and 1.", True, "float", s["sole_source_share"], tolerance=0.01,
                         expected_query="supplier_profile", expected_params={"supplier_id": sid},
                         required_citations=[f"supplier:{sid}"],
                         reference_facts={k: s[k] for k in ("sole_source_share", "prime_obligations", "sole_source_obligations")}))

    def t_blast_radius_awards(self):
        for sid, name in self.pick(self.unique_name_suppliers("n_subawards_received >= 1")):
            r = self.both("blast_radius", {"supplier_id": sid})
            if r:
                s = r[0]["summary"]
                n = s["n_awards_direct"] + s["n_awards_indirect"]
                self.add(GoldenItem(_qid("blast_radius_awards", sid), "blast_radius_awards",
                         f"If {name} failed, how many prime awards would be affected, counting both awards it holds "
                         f"as prime and prime awards it works on as a subcontractor? Give the count.", True, "int", n,
                         expected_query="blast_radius", expected_params={"supplier_id": sid},
                         required_citations=[f"supplier:{sid}"],
                         reference_facts={k: s[k] for k in ("n_awards_direct", "n_awards_indirect", "n_offices",
                                                            "n_prime_customers")}))

    def t_blast_radius_programs(self):
        cands = self.unique_name_suppliers("n_prime_awards + n_subawards_received >= 1")
        pool = []
        for sid, name in cands:
            n = self.con.execute("""SELECT count(*) FROM (SELECT program_code FROM awards_scope WHERE supplier_id=$1
                                    UNION SELECT prime_program_code FROM subawards_scope WHERE sub_supplier_id=$1)
                                    WHERE program_code IS NOT NULL""", [sid]).fetchone()[0]
            if 1 <= n <= 4:
                pool.append((sid, name))
        for sid, name in self.pick(pool):
            r = self.both("blast_radius", {"supplier_id": sid})
            if r:
                progs = r[0]["summary"]["programs"]
                self.add(GoldenItem(_qid("blast_radius_programs", sid), "blast_radius_programs",
                         f"Which DoD acquisition programs (program codes) would be touched if {name} failed, through "
                         f"its prime awards or its subcontracts? List the program codes.", True, "ids", progs,
                         expected_query="blast_radius", expected_params={"supplier_id": sid},
                         required_citations=[f"supplier:{sid}"], reference_facts={"programs": progs}))

    def t_top_sole_source_country(self):
        countries = [c for (c,) in self.con.execute("""SELECT pop_country FROM awards_scope WHERE is_sole_source
            AND pop_country NOT IN ('USA') GROUP BY 1 HAVING count(DISTINCT supplier_id) >= 3 ORDER BY 1""").fetchall()]
        names = dict(self.con.execute("SELECT alpha3, name FROM countries").fetchall())
        kept = 0
        for iso in self.pick(countries, len(countries)):
            r = self.both("sole_source_suppliers_by_country", {"country_iso3": iso})
            if not r or len(r[0]["rows"]) < 2:
                continue
            a, b = r[0]["rows"][0], r[0]["rows"][1]
            if a["sole_source_obligations"] < 1.05 * b["sole_source_obligations"]:
                continue  # require a clear winner
            self.add(GoldenItem(_qid("top_sole_source_country", iso), "top_sole_source_country",
                     f"Which supplier has the largest sole-source (non-competed) prime obligations on awards performed "
                     f"in {names.get(iso, iso)}? Give its supplier_id.", True, "id", a["supplier_id"],
                     expected_query="sole_source_suppliers_by_country", expected_params={"country_iso3": iso},
                     required_citations=[f"supplier:{a['supplier_id']}"],
                     reference_facts={"top_supplier": a["name"], "supplier_id": a["supplier_id"],
                                      "sole_source_obligations": a["sole_source_obligations"]}))
            kept += 1
            if kept >= PER_TEMPLATE:
                break

    def t_office_top_supplier(self):
        offices = self.con.execute("""SELECT office_code, office_name FROM office_totals
                                      WHERE n_suppliers >= 5 AND total_obligations >= 5e6 ORDER BY 1""").fetchall()
        kept = 0
        for code, oname in self.pick(offices, len(offices)):
            r = self.both("office_concentration", {"office_code": code})
            if not r or r[0]["rows"][0]["obligations"] < 1.05 * r[0]["rows"][1]["obligations"]:
                continue
            top = r[0]["rows"][0]
            self.add(GoldenItem(_qid("office_top_supplier", code), "office_top_supplier",
                     f"Which supplier receives the largest share of obligations from awarding office {code} ({oname})? "
                     f"Give its supplier_id.", True, "id", top["supplier_id"],
                     expected_query="office_concentration", expected_params={"office_code": code},
                     required_citations=[f"supplier:{top['supplier_id']}"],
                     reference_facts={"top_supplier": top["name"], "share": top["share"], "hhi": r[0]["summary"]["hhi"]}))
            kept += 1
            if kept >= PER_TEMPLATE:
                break

    def t_office_hhi(self):
        offices = self.con.execute("""SELECT office_code, office_name FROM office_totals
                                      WHERE n_suppliers >= 3 AND total_obligations >= 1e6 ORDER BY 1""").fetchall()
        for code, oname in self.pick(offices):
            r = self.both("office_concentration", {"office_code": code})
            if r:
                s = r[0]["summary"]
                self.add(GoldenItem(_qid("office_hhi", code), "office_hhi",
                         f"What is the Herfindahl-Hirschman Index (0 to 10,000 scale) of supplier concentration for "
                         f"awarding office {code} ({oname})?", True, "float", s["hhi"], tolerance=0.02, rel_tol=True,
                         expected_query="office_concentration", expected_params={"office_code": code},
                         required_citations=[f"office:{code}"],
                         reference_facts={k: s[k] for k in ("hhi", "n_suppliers", "top_supplier_share")}))

    def t_program_top_supplier(self):
        progs = self.con.execute("""SELECT program_code, min(program_name) FROM awards_scope WHERE program_code IS NOT NULL
                                    GROUP BY 1 HAVING count(DISTINCT supplier_id) >= 2 ORDER BY 1""").fetchall()
        kept = 0
        for code, pname in self.pick(progs, len(progs)):
            r = self.both("program_suppliers", {"program_code": code})
            if not r or r[0]["rows"][0]["obligations"] < 1.05 * r[0]["rows"][1]["obligations"]:
                continue
            top = r[0]["rows"][0]
            self.add(GoldenItem(_qid("program_top_supplier", code), "program_top_supplier",
                     f"Which supplier received the most obligations on DoD acquisition program {code} ({pname}) in "
                     f"this data? Give its supplier_id.", True, "id", top["supplier_id"],
                     expected_query="program_suppliers", expected_params={"program_code": code},
                     required_citations=[f"supplier:{top['supplier_id']}"],
                     reference_facts={"top_supplier": top["name"], "obligations": top["obligations"]}))
            kept += 1
            if kept >= PER_TEMPLATE:
                break

    def t_country_awards(self):
        countries = [c for (c,) in self.con.execute("""SELECT pop_country FROM awards_scope WHERE pop_country <> 'USA'
                     GROUP BY 1 HAVING count(*) >= 2 ORDER BY 1""").fetchall()]
        names = dict(self.con.execute("SELECT alpha3, name FROM countries").fetchall())
        for iso in self.pick(countries):
            r = self.both("country_exposure", {"country_iso3": iso})
            if r:
                s = r[0]["summary"]
                self.add(GoldenItem(_qid("country_prime_awards", iso), "country_prime_awards",
                         f"How many prime awards in this data were performed in {names.get(iso, iso)}? Give the count.",
                         True, "int", s["n_prime_awards"], expected_query="country_exposure",
                         expected_params={"country_iso3": iso}, required_citations=[f"country:{iso}"],
                         reference_facts={k: s[k] for k in ("n_prime_awards", "prime_obligations", "n_suppliers")}))

    def t_parent_subsidiaries(self):
        parents = self.con.execute("""SELECT parent_uei, parent_name FROM parents WHERE n_children BETWEEN 2 AND 30
                                      AND parent_name IS NOT NULL ORDER BY 1""").fetchall()
        for puei, pname in self.pick(parents):
            r = self.both("parent_subsidiaries", {"parent_uei": puei})
            if r:
                s = r[0]["summary"]
                self.add(GoldenItem(_qid("parent_subsidiaries", puei), "parent_subsidiaries",
                         f"How many subsidiary supplier UEIs roll up to parent company {pname} (parent UEI {puei}) in "
                         f"this data? Give the count.", True, "int", s["n_subsidiaries"],
                         expected_query="parent_subsidiaries", expected_params={"parent_uei": puei},
                         required_citations=[f"parent:{puei}"],
                         reference_facts={k: s[k] for k in ("n_subsidiaries", "total_prime_obligations")}))

    def t_subcontractors(self):
        for sid, name in self.pick(self.unique_name_suppliers("n_subcontractors >= 2")):
            r = self.both("subcontractors_of", {"supplier_id": sid})
            if r:
                s = r[0]["summary"]
                self.add(GoldenItem(_qid("subcontractor_count", sid), "subcontractor_count",
                         f"How many distinct subcontractors does {name} report on its prime awards in this data? "
                         f"Give the count.", True, "int", s["n_subcontractors"], expected_query="subcontractors_of",
                         expected_params={"supplier_id": sid}, required_citations=[f"supplier:{sid}"],
                         reference_facts={k: s[k] for k in ("n_subcontractors", "total_sub_amount", "n_subawards")}))

    def t_office_flagged_share(self):
        offices = self.con.execute("""SELECT office_code, office_name FROM office_totals
                                      WHERE n_suppliers >= 4 AND total_obligations >= 1e6 ORDER BY 1""").fetchall()
        for code, oname in self.pick(offices):
            r = self.both("office_flagged_suppliers", {"office_code": code})
            if r and r[0]["summary"]["flagged_share"] is not None:
                s = r[0]["summary"]
                self.add(GoldenItem(_qid("office_flagged_share", code), "office_flagged_share",
                         f"For awarding office {code} ({oname}), what share of its obligations goes to suppliers that are "
                         f"flagged as concentrated, sole-sourced, or geographically exposed? Answer as a fraction "
                         f"between 0 and 1.", True, "float", s["flagged_share"], tolerance=0.01,
                         expected_query="office_flagged_suppliers", expected_params={"office_code": code},
                         required_citations=[f"office:{code}"],
                         reference_facts={k: s[k] for k in ("flagged_share", "n_flagged_suppliers", "n_suppliers")}))

    def t_country_risk(self):
        G = load_graph()
        rows = self.con.execute("SELECT country_iso3, p_escalation, week_start FROM risk_latest ORDER BY 1").fetchall()
        names = dict(self.con.execute("SELECT alpha3, name FROM countries").fetchall())
        for iso, p, wk in self.pick(rows):
            node = G.nodes.get(nid("Country", iso), {})
            if round(node.get("p_escalation", -1), 6) != round(p, 6) or node.get("risk_week_start") != wk.isoformat():
                self.dropped.append({"query": "country_risk", "params": {"country_iso3": iso}, "reason": "disagreement"})
                continue
            self.add(GoldenItem(_qid("country_risk", iso), "country_risk",
                     f"What is the latest escalation probability (p_escalation) for {names.get(iso, iso)} in the MENA "
                     f"risk feed? Give the probability.", True, "float", round(p, 4), tolerance=0.0006,
                     expected_query="country_risk", expected_params={"country_iso3": iso},
                     required_citations=[f"country:{iso}"],
                     reference_facts={"p_escalation": round(p, 4), "week_start": wk.isoformat()},
                     label_source="risk_latest (SQL) verified against Country node (graph)"))

    def t_award_sole_source(self):
        G = load_graph()
        ids = [a for (a,) in self.con.execute("""SELECT award_id FROM awards_scope WHERE extent_competed_code IS NOT NULL
                                                 AND obl_pos > 1e5 ORDER BY award_id""").fetchall()]
        for aid in self.pick(ids):
            sql = self.con.execute("SELECT is_sole_source, extent_competed FROM awards_scope WHERE award_id=?", [aid]).fetchone()
            node = G.nodes[nid("Award", aid)]
            if bool(node["is_sole_source"]) != bool(sql[0]):
                self.dropped.append({"query": "award_details", "params": {"award_id": aid}, "reason": "disagreement"})
                continue
            self.add(GoldenItem(_qid("award_sole_source", aid), "award_sole_source",
                     f"Was prime award {aid} awarded without competition (sole-source)? Answer true or false.",
                     True, "bool", bool(sql[0]), expected_query="award_details", expected_params={"award_id": aid},
                     required_citations=[f"award:{aid}"],
                     reference_facts={"is_sole_source": bool(sql[0]), "extent_competed": sql[1]},
                     label_source="awards table (SQL) verified against Award node (graph)"))


    # ----------------------------------------------------------- hard tier (multi-hop, ambiguity, noise)
    def h_company_rollup(self):
        """Company named in plain words; needs resolve -> supplier_profile (parent) -> parent_subsidiaries."""
        rows = self.con.execute("""SELECT s.parent_uei, p.parent_name, list(s.name ORDER BY s.name) names FROM suppliers s
                                   JOIN parents p USING (parent_uei) WHERE p.n_children BETWEEN 3 AND 25
                                   GROUP BY ALL ORDER BY 1""").fetchall()
        allnames = Counter(core_name(n) for (n,) in self.con.execute("SELECT name FROM suppliers").fetchall())
        pool = []
        for puei, _pname, names in rows:
            cores = Counter(core_name(n) for n in names)
            top, cnt = cores.most_common(1)[0]
            if cnt >= 2 and allnames[top] == cnt and len(top) >= 5:  # every UEI with this name shares the parent
                pool.append((puei, next(n for n in sorted(names) if core_name(n) == top)))
        for puei, name in self.pick(pool, 3):
            r = self.both("parent_subsidiaries", {"parent_uei": puei})
            if r:
                s = r[0]["summary"]
                self.add(GoldenItem(_qid("hard_company_rollup", puei), "hard_company_rollup",
                         f"{name.title()} appears under several UEIs. How many supplier UEIs in this data roll up to its "
                         f"parent company (count every UEI whose parent is that parent UEI, including the parent's own "
                         f"UEI if it is listed)? Give the count.", True, "int", s["n_subsidiaries"],
                         expected_query="parent_subsidiaries", expected_params={"parent_uei": puei},
                         required_citations=[f"parent:{puei}"],
                         reference_facts={k: s[k] for k in ("n_subsidiaries", "parent_name", "total_prime_obligations")},
                         label_source="deterministic query (SQL) verified by graph traversal; hard tier"))

    def h_office_flagged_blast(self):
        """Persona question: office -> top flagged supplier -> its blast radius (two hops)."""
        offices = self.con.execute("""SELECT office_code, office_name FROM office_totals
                                      WHERE n_suppliers >= 4 AND total_obligations >= 5e6 ORDER BY 1""").fetchall()
        kept = 0
        for code, oname in self.pick(offices, len(offices)):
            r1 = self.both("office_flagged_suppliers", {"office_code": code})
            if not r1 or len(r1[0]["rows"]) < 2 or r1[0]["rows"][0]["obligations"] < 1.05 * r1[0]["rows"][1]["obligations"]:
                continue
            sid = r1[0]["rows"][0]["supplier_id"]
            r2 = self.both("blast_radius", {"supplier_id": sid})
            if not r2 or r2[0]["summary"]["n_offices"] < 2:
                continue
            n = r2[0]["summary"]["n_offices"]
            self.add(GoldenItem(_qid("hard_office_flagged_blast", code), "hard_office_flagged_blast",
                     f"For awarding office {code} ({oname}): among the suppliers it pays that are flagged as concentrated, "
                     f"sole-sourced or geographically exposed, take the one with the largest obligations from this office. "
                     f"If that supplier failed, how many awarding offices in total would be affected? Give the count.",
                     True, "int", n, expected_query="blast_radius", expected_params={"supplier_id": sid},
                     alt_queries=["dependent_offices"], required_citations=[f"supplier:{sid}"],
                     reference_facts={"top_flagged_supplier": r1[0]["rows"][0]["name"], "supplier_id": sid,
                                      "n_offices_affected": n},
                     label_source="two deterministic queries (SQL) each verified by graph traversal; hard tier"))
            kept += 1
            if kept >= 3:
                break

    def h_prime_customers(self):
        """Sub-only supplier: which primes does it reach DoD work through (indirect dependency)."""
        pool = self.unique_name_suppliers("n_prime_awards = 0 AND n_prime_customers BETWEEN 2 AND 4")
        for sid, name in self.pick(pool, 3):
            r = self.both("blast_radius", {"supplier_id": sid})
            if r:
                pcs = r[0]["summary"]["prime_customers"]
                self.add(GoldenItem(_qid("hard_prime_customers", sid), "hard_prime_customers",
                         f"{name} holds no prime awards in this data. Through which prime contractors does it reach DoD "
                         f"maintenance work? List their supplier_ids.", True, "ids", pcs,
                         expected_query="blast_radius", expected_params={"supplier_id": sid},
                         required_citations=[f"supplier:{sid}"], reference_facts={"prime_customers": pcs},
                         label_source="deterministic query (SQL) verified by graph traversal; hard tier"))

    def h_single_point_of_failure(self):
        """No query name in the wording; bool flag, independently recomputed from graph structure."""
        from supplier_graph.graph.schema import CONCENTRATION_MIN_OFFICE, CONCENTRATION_SHARE
        G = load_graph()
        pool_t = self.unique_name_suppliers("flag_concentrated AND n_prime_awards >= 2")
        pool_f = self.unique_name_suppliers("NOT flag_concentrated AND n_prime_awards >= 5")
        for (sid, name), want in [(x, True) for x in self.pick(pool_t, 2)] + [(x, False) for x in self.pick(pool_f, 1)]:
            sn = nid("Supplier", sid)
            best = 0.0
            for u, _, k in G.in_edges(sn, keys=True):
                if k != "AWARDED_TO" or not G.nodes[u]["office_code"]:
                    continue
                on = nid("Office", G.nodes[u]["office_code"])
                tot = sum(G.nodes[a]["obl_pos"] for a, _, kk in G.in_edges(on, keys=True) if kk == "AWARDED_BY")
                mine = sum(G.nodes[a]["obl_pos"] for a, _, kk in G.in_edges(on, keys=True)
                           if kk == "AWARDED_BY" and any(v == sn for _, v, k3 in G.out_edges(a, keys=True) if k3 == "AWARDED_TO"))
                if tot >= CONCENTRATION_MIN_OFFICE:
                    best = max(best, mine / tot)
            if (best >= CONCENTRATION_SHARE) != want:
                self.dropped.append({"query": "flag_concentrated", "params": {"supplier_id": sid}, "reason": "disagreement"})
                continue
            self.add(GoldenItem(_qid("hard_single_point", sid), "hard_single_point",
                     f"Is {name} a concentration risk for any awarding office, meaning it receives at least 25% of that "
                     f"office's obligations (offices with at least $1M in obligations)? Answer true or false.",
                     True, "bool", want, expected_query="supplier_profile", expected_params={"supplier_id": sid},
                     alt_queries=["dependent_offices", "office_concentration"], required_citations=[f"supplier:{sid}"],
                     reference_facts={"flag_concentrated": want, "max_office_share": round(best, 4)},
                     label_source="supplier_risk flag (SQL) verified by recomputation over the graph; hard tier"))

    def h_noisy_name(self):
        """Supplier named with an abbreviation/typo/suffix change (generator from the ER label set)."""
        from supplier_graph.agent.tools import resolve_supplier
        from supplier_graph.er.labels import p_abbreviation, p_suffix, p_typo
        pool = self.unique_name_suppliers("n_dependent_offices >= 2 AND n_prime_awards >= 2")
        kept = 0
        for sid, name in self.pick(pool, len(pool)):
            noisy, applied = {"name": name}, []
            for fn in (p_typo, p_suffix, p_abbreviation):
                new, ok = fn(noisy, self.rng)
                if ok:
                    noisy, applied = new, applied + [fn.__name__]
            if len(applied) < 2 or core_name(noisy["name"]) == core_name(name):
                continue
            cands = resolve_supplier(noisy["name"])["candidates"]
            if not cands or cands[0]["supplier_id"] != sid:
                continue  # keep only noise the resolver can undo, so the item tests the agent, not the resolver
            r = self.both("dependent_offices", {"supplier_id": sid})
            if r:
                n = r[0]["summary"]["n_offices"]
                self.add(GoldenItem(_qid("hard_noisy_name", sid), "hard_noisy_name",
                         f"How many awarding offices depend on {noisy['name']}, directly or through subcontracts? "
                         f"Give the count.", True, "int", n, expected_query="dependent_offices",
                         expected_params={"supplier_id": sid}, alt_queries=["blast_radius"],
                         required_citations=[f"supplier:{sid}"],
                         reference_facts={"true_name": name, "n_offices": n, "noise": applied},
                         label_source="deterministic query (SQL) verified by graph traversal; name noise synthetic"))
                kept += 1
                if kept >= 3:
                    break

    def h_feed_country_supplier_risk(self):
        """Two tools: country exposure in a feed country -> top supplier, plus that country's p_escalation."""
        rows = self.con.execute("""SELECT a.pop_country FROM awards_scope a JOIN risk_latest r ON r.country_iso3 = a.pop_country
                                   GROUP BY 1 HAVING count(DISTINCT supplier_id) >= 2 ORDER BY 1""").fetchall()
        names = dict(self.con.execute("SELECT alpha3, name FROM countries").fetchall())
        for (iso,) in self.pick(rows, 3):
            r = self.both("country_exposure", {"country_iso3": iso})
            if not r or r[0]["summary"]["p_escalation"] is None:
                continue
            s = r[0]["summary"]
            self.add(GoldenItem(_qid("hard_feed_country", iso), "hard_feed_country",
                     f"How much prime-award obligation in this data was performed in {names.get(iso, iso)}, and what is "
                     f"that country's latest escalation probability? Put the escalation probability in the answer field.",
                     True, "float", s["p_escalation"], tolerance=0.0006, strict_required_citations=True,
                     expected_query="country_exposure",
                     expected_params={"country_iso3": iso}, required_citations=[f"country:{iso}"],
                     reference_facts={"prime_obligations": s["prime_obligations"], "p_escalation": s["p_escalation"],
                                      "risk_week_start": s["risk_week_start"]},
                     label_source="deterministic query (SQL) verified by graph traversal; hard tier"))

    # ----------------------------------------------------------- unanswerable
    def unanswerable(self):
        all_cores = sorted({core_name(n) for (n,) in self.con.execute("SELECT name FROM suppliers").fetchall()})
        from supplier_graph.agent.tools import resolve_supplier
        first = ["Northwind", "Bluefjord", "Quillmark", "Ostrava", "Tamberlane", "Velquist", "Corvane", "Halvorsen",
                 "Brightwater", "Marrowgate", "Pellucid", "Saxbury"]
        second = ["Avionics Repair", "Hydraulics Overhaul", "Rotorcraft Sustainment", "Turbine Rebuild",
                  "Marine Propulsion Services", "Armor Refurbishment"]
        made = 0
        for f in self.rng.sample(first, len(first)):
            name = f"{f} {self.rng.choice(second)} LLC"
            hit = process.extractOne(core_name(name), all_cores, scorer=fuzz.token_sort_ratio)
            if (hit and hit[1] >= 80) or resolve_supplier(name)["candidates"]:
                continue
            tmpl = self.rng.choice([
                ("Which awarding offices depend on {n}?", "dependent_offices"),
                ("What is the blast radius if {n} fails? How many awards would be affected?", "blast_radius"),
                ("What share of {n}'s obligations were sole-source?", "supplier_profile")])
            self.add(GoldenItem(_qid("absent_supplier", name), "absent_supplier", tmpl[0].format(n=name), False, "null",
                     None, expected_query="resolve_supplier", expected_params={"name": name},
                     reference_facts={"why": "supplier does not exist in the data"},
                     label_source="synthetic absent entity (verified: resolver returns no candidates, fuzzy scan < 80)"))
            made += 1
            if made >= 5:
                break
        feed = {c for (c,) in self.con.execute("SELECT country_iso3 FROM risk_latest").fetchall()}
        names = dict(self.con.execute("SELECT alpha3, name FROM countries").fetchall())
        data_countries = sorted({c for (c,) in self.con.execute(
            "SELECT pop_country FROM awards_scope WHERE pop_country IS NOT NULL").fetchall()} - feed - {"USA"})
        for iso in self.pick(data_countries, 4):
            self.add(GoldenItem(_qid("risk_not_in_feed", iso), "risk_not_in_feed",
                     f"What is the current escalation probability for {names.get(iso, iso)} according to the risk feed?",
                     False, "null", None, expected_query="country_risk", expected_params={"country_iso3": iso},
                     reference_facts={"why": "country not covered by the MENA risk feed"},
                     label_source="feed coverage metadata (country absent from risk feed)"))
        for i in range(2):
            fake = f"CONT_AWD_N9999926F{9000 + self.rng.randrange(999):04d}_9700_-NONE-_-NONE-"
            assert not self.con.execute("SELECT count(*) FROM awards WHERE award_id=?", [fake]).fetchone()[0]
            self.add(GoldenItem(_qid("absent_award", fake + str(i)), "absent_award",
                     f"Who is the prime contractor on award {fake}, and was it competed?", False, "null", None,
                     expected_query="award_details", expected_params={"award_id": fake},
                     reference_facts={"why": "award ID does not exist in the data"},
                     label_source="synthetic absent award ID (verified absent)"))
        progs = {p for (p,) in self.con.execute("SELECT DISTINCT program_code FROM awards WHERE program_code IS NOT NULL").fetchall()}
        fake_prog = next(c for c in ("Q7X", "Z9Q", "QQ1") if c not in progs)
        self.add(GoldenItem(_qid("absent_program", fake_prog), "absent_program",
                 f"Which suppliers support DoD acquisition program {fake_prog}?", False, "null", None,
                 expected_query="program_suppliers", expected_params={"program_code": fake_prog},
                 reference_facts={"why": "program code not present in the data"},
                 label_source="synthetic absent program code (verified absent)"))
        for qtext in ("What is Sikorsky Aircraft Corporation's on-time delivery rate for repair parts?",
                      "How many employees does Sikorsky Aircraft Corporation have working on Army repair contracts?",
                      "What will the Navy's maintenance obligations to Sikorsky Aircraft Corporation be in FY2027?"):
            self.add(GoldenItem(_qid("out_of_schema", qtext), "out_of_schema", qtext, False, "null", None,
                     expected_query=None, reference_facts={"why": "the graph holds no such field"},
                     label_source="schema metadata (field not in the graph)"))

    def build(self) -> list[GoldenItem]:
        for t in (self.t_dependent_offices, self.t_sole_source_share, self.t_blast_radius_awards,
                  self.t_blast_radius_programs, self.t_top_sole_source_country, self.t_office_top_supplier,
                  self.t_office_hhi, self.t_program_top_supplier, self.t_country_awards, self.t_parent_subsidiaries,
                  self.t_subcontractors, self.t_office_flagged_share, self.t_country_risk, self.t_award_sole_source):
            t()
        main_rng, self.rng = self.rng, random.Random(SEED + 100)  # hard tier has its own stream
        for h in (self.h_company_rollup, self.h_office_flagged_blast, self.h_prime_customers,
                  self.h_single_point_of_failure, self.h_noisy_name, self.h_feed_country_supplier_risk):
            h()
        self.rng = main_rng
        self.unanswerable()
        seen = set()
        self.items = [i for i in self.items if not (i.qid in seen or seen.add(i.qid))]
        REPORTS.mkdir(exist_ok=True)
        GOLDEN_PATH.write_text("".join(json.dumps(asdict(i), default=str) + "\n" for i in self.items))
        DROPPED_PATH.write_text("".join(json.dumps(d, default=str) + "\n" for d in self.dropped))
        self.con.close()
        return self.items


def load_golden() -> list[GoldenItem]:
    return [GoldenItem(**json.loads(line)) for line in GOLDEN_PATH.read_text().splitlines() if line.strip()]


if __name__ == "__main__":
    items = Builder().build()
    c = Counter(i.template for i in items)
    print(json.dumps(dict(c), indent=2), "\ntotal", len(items), "answerable", sum(i.answerable for i in items))
