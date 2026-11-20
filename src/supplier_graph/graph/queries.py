"""Library of named, parameterized, read-only queries.

Every query has two independent implementations:
  * `graph`: traversal of the NetworkX knowledge graph (the serving path the agent uses), and
  * `sql`:   DuckDB SQL over the staged tables (used to verify golden answers in the eval harness).
Both return the same canonical shape: {"query", "params", "summary", "rows", "citations"}, with money
rounded to cents and rows in a deterministic order, so results can be compared exactly.

The risk flags (concentrated / sole-source / geo-exposed) are computed once in SQL
(graph/build.py) and copied onto Supplier nodes; for `office_flagged_suppliers` the independent check
therefore covers the traversal, not the flag computation itself.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import duckdb

from supplier_graph.graph.build import load_graph
from supplier_graph.graph.schema import nid
from supplier_graph.stage import connect


class QueryError(ValueError):
    pass


def m(x: float | None) -> float:
    return round(float(x or 0.0), 2)


def _result(name: str, params: dict, summary: dict, rows: list[dict], award_ids=(), entity_ids=()) -> dict:
    return {"query": name, "params": params, "summary": summary, "rows": rows,
            "citations": {"award_ids": sorted(set(award_ids)), "entity_ids": sorted(set(entity_ids))}}


# ------------------------------------------------------------------ graph helpers
def _G():
    return load_graph()


def _supplier(G, sid: str) -> str:
    n = nid("Supplier", sid)
    if n not in G:
        raise QueryError(f"unknown supplier_id {sid!r}")
    return n


def _prime_awards(G, sn: str) -> list[dict]:
    return [G.nodes[u] for u, _, k in G.in_edges(sn, keys=True) if k == "AWARDED_TO"]


def _sub_edges_in(G, sn: str) -> list[dict]:
    return [d for _, _, k, d in G.in_edges(sn, keys=True, data=True) if d.get("type") == "SUBCONTRACTS_TO"]


def _sub_edges_out(G, sn: str) -> list[tuple[str, dict]]:
    return [(v, d) for _, v, _, d in G.out_edges(sn, keys=True, data=True) if d.get("type") == "SUBCONTRACTS_TO"]


def _award(G, aid: str) -> dict:
    return G.nodes[nid("Award", aid)]


def _award_supplier(G, aid: str) -> str:
    for _, v, k in G.out_edges(nid("Award", aid), keys=True):
        if k == "AWARDED_TO":
            return G.nodes[v]["supplier_id"]
    raise QueryError(aid)


def _top_ids(pairs, n: int = 3) -> list[str]:
    """Top-n distinct award ids by their largest associated amount; ties broken by id."""
    best: dict[str, float] = {}
    for v, a in pairs:
        best[a] = max(best.get(a, float("-inf")), float(v or 0))
    return [a for a, _ in sorted(best.items(), key=lambda t: (-t[1], t[0]))[:n]]


def _top_offices(rows, n: int = 5) -> list[dict]:
    """The n offices with the most prime obligations performed in a country: (code, name, n_awards, obligations)."""
    out = [{"office_code": c, "office_name": nm, "n_awards": int(k), "obligations": m(v)} for c, nm, k, v in rows]
    return sorted(out, key=lambda r: (-r["obligations"], r["office_code"]))[:n]


# ------------------------------------------------------------------ queries: graph path
def g_supplier_profile(supplier_id: str) -> dict:
    G = _G()
    sn = _supplier(G, supplier_id)
    s = G.nodes[sn]
    awards = _prime_awards(G, sn)
    prime = sum(a["obl_pos"] for a in awards)
    sole = sum(a["obl_pos"] for a in awards if a["is_sole_source"])
    subs_in = _sub_edges_in(G, sn)
    offices = {a["office_code"] for a in awards if a["office_code"]} | \
              {_award(G, d["prime_award_id"])["office_code"] for d in subs_in if _award(G, d["prime_award_id"])["office_code"]}
    foreign = sorted({a["pop_country"] for a in awards if a["pop_country"] and a["pop_country"] != "USA"} |
                     {d["pop_country"] for d in subs_in if d["pop_country"] and d["pop_country"] != "USA"})
    top = sorted(awards, key=lambda a: (-a["obl_pos"], a["award_id"]))[:10]
    summary = {"supplier_id": supplier_id, "name": s["name"], "parent_uei": s.get("parent_uei"),
               "n_prime_awards": len(awards), "prime_obligations": m(prime),
               "sole_source_obligations": m(sole),
               "sole_source_share": round(sole / prime, 4) if prime else None,
               "n_subawards_received": len(subs_in), "sub_amount_received": m(sum(max(d["amount"] or 0, 0) for d in subs_in)),
               "n_dependent_offices": len(offices), "foreign_countries": foreign,
               "flag_concentrated": s.get("flag_concentrated"), "flag_sole_source": s.get("flag_sole_source"),
               "flag_geo_exposed": s.get("flag_geo_exposed")}
    rows = [{"award_id": a["award_id"], "obligated": m(a["obl_pos"]), "office_code": a["office_code"],
             "is_sole_source": bool(a["is_sole_source"])} for a in top]
    return _result("supplier_profile", {"supplier_id": supplier_id}, summary, rows,
                   [r["award_id"] for r in rows], [supplier_id])


def g_dependent_offices(supplier_id: str) -> dict:
    G = _G()
    sn = _supplier(G, supplier_id)
    agg: dict[str, dict] = defaultdict(lambda: {"direct_obligations": 0.0, "n_direct_awards": 0,
                                                "sub_amount": 0.0, "n_indirect_awards": set(), "_ids": []})
    for a in _prime_awards(G, sn):
        if not a["office_code"]:
            continue
        o = agg[a["office_code"]]
        o["direct_obligations"] += a["obl_pos"]
        o["n_direct_awards"] += 1
        o["_ids"].append((a["obl_pos"], a["award_id"]))
    for d in _sub_edges_in(G, sn):
        a = _award(G, d["prime_award_id"])
        if not a["office_code"]:
            continue
        o = agg[a["office_code"]]
        o["sub_amount"] += max(d["amount"] or 0, 0)
        o["n_indirect_awards"].add(a["award_id"])
        o["_ids"].append((max(d["amount"] or 0, 0), a["award_id"]))
    rows, cites = [], []
    for code, o in agg.items():
        ids = _top_ids(o["_ids"])
        cites += ids
        rows.append({"office_code": code, "office_name": G.nodes[nid("Office", code)]["name"],
                     "direct_obligations": m(o["direct_obligations"]), "n_direct_awards": o["n_direct_awards"],
                     "sub_amount": m(o["sub_amount"]), "n_indirect_awards": len(o["n_indirect_awards"]),
                     "example_award_ids": ids})
    rows.sort(key=lambda r: (-r["direct_obligations"], -r["sub_amount"], r["office_code"]))
    summary = {"supplier_id": supplier_id, "n_offices": len(rows),
               "n_offices_direct": sum(r["n_direct_awards"] > 0 for r in rows),
               "n_offices_indirect_only": sum(r["n_direct_awards"] == 0 for r in rows)}
    return _result("dependent_offices", {"supplier_id": supplier_id}, summary, rows, cites, [supplier_id])


def g_blast_radius(supplier_id: str) -> dict:
    G = _G()
    sn = _supplier(G, supplier_id)
    direct = _prime_awards(G, sn)
    indirect_ids = sorted({d["prime_award_id"] for d in _sub_edges_in(G, sn)} - {a["award_id"] for a in direct})
    indirect = [_award(G, a) for a in indirect_ids]
    all_aw = direct + indirect
    offices = defaultdict(lambda: {"n_awards": 0, "obligations": 0.0})
    for a in all_aw:
        if a["office_code"]:
            offices[a["office_code"]]["n_awards"] += 1
            offices[a["office_code"]]["obligations"] += a["obl_pos"]
    programs = sorted({a["program_code"] for a in all_aw if a["program_code"]})
    prime_customers = sorted({_award_supplier(G, a["award_id"]) for a in indirect})
    summary = {"supplier_id": supplier_id, "n_awards_direct": len(direct), "n_awards_indirect": len(indirect),
               "obligations_direct": m(sum(a["obl_pos"] for a in direct)),
               "obligations_indirect_awards": m(sum(a["obl_pos"] for a in indirect)),
               "n_offices": len(offices), "programs": programs, "n_prime_customers": len(prime_customers),
               "prime_customers": prime_customers[:20]}
    rows = sorted(({"office_code": k, "office_name": G.nodes[nid("Office", k)]["name"], "n_awards": v["n_awards"],
                    "obligations": m(v["obligations"])} for k, v in offices.items()),
                  key=lambda r: (-r["obligations"], r["office_code"]))
    cites = _top_ids([(a["obl_pos"], a["award_id"]) for a in direct], 5) + \
        _top_ids([(a["obl_pos"], a["award_id"]) for a in indirect], 5)
    return _result("blast_radius", {"supplier_id": supplier_id}, summary, rows, cites,
                   [supplier_id, *prime_customers[:20]])


def g_sole_source_suppliers_by_country(country_iso3: str, limit: int = 10) -> dict:
    G = _G()
    cn = nid("Country", country_iso3.upper())
    if cn not in G:
        raise QueryError(f"no awards performed in {country_iso3!r} in scope")
    agg: dict[str, dict] = defaultdict(lambda: {"obl": 0.0, "n": 0, "_ids": []})
    for u, _, k in G.in_edges(cn, keys=True):
        if k != "PERFORMED_IN":
            continue
        a = G.nodes[u]
        if not a["is_sole_source"]:
            continue
        s = _award_supplier(G, a["award_id"])
        agg[s]["obl"] += a["obl_pos"]
        agg[s]["n"] += 1
        agg[s]["_ids"].append((a["obl_pos"], a["award_id"]))
    rows = [{"supplier_id": s, "name": G.nodes[nid("Supplier", s)]["name"], "sole_source_obligations": m(v["obl"]),
             "n_sole_source_awards": v["n"], "example_award_ids": _top_ids(v["_ids"])} for s, v in agg.items()]
    rows.sort(key=lambda r: (-r["sole_source_obligations"], r["supplier_id"]))
    total = len(rows)
    rows = rows[:limit]
    summary = {"country_iso3": country_iso3.upper(), "n_suppliers": total,
               "total_sole_source_obligations": m(sum(v["obl"] for v in agg.values()))}
    return _result("sole_source_suppliers_by_country", {"country_iso3": country_iso3.upper(), "limit": limit}, summary,
                   rows, [i for r in rows for i in r["example_award_ids"]], [r["supplier_id"] for r in rows])


def g_office_concentration(office_code: str, limit: int = 10) -> dict:
    G = _G()
    on = nid("Office", office_code)
    if on not in G:
        raise QueryError(f"unknown office_code {office_code!r}")
    agg: dict[str, dict] = defaultdict(lambda: {"obl": 0.0, "n": 0, "_ids": []})
    for u, _, k in G.in_edges(on, keys=True):
        if k != "AWARDED_BY":
            continue
        a = G.nodes[u]
        s = _award_supplier(G, a["award_id"])
        agg[s]["obl"] += a["obl_pos"]
        agg[s]["n"] += 1
        agg[s]["_ids"].append((a["obl_pos"], a["award_id"]))
    total = sum(v["obl"] for v in agg.values())
    shares = {s: (v["obl"] / total if total else 0.0) for s, v in agg.items()}
    rows = [{"supplier_id": s, "name": G.nodes[nid("Supplier", s)]["name"], "obligations": m(v["obl"]),
             "share": round(shares[s], 4), "n_awards": v["n"], "example_award_ids": _top_ids(v["_ids"])}
            for s, v in agg.items()]
    rows.sort(key=lambda r: (-r["obligations"], r["supplier_id"]))
    summary = {"office_code": office_code, "office_name": G.nodes[on]["name"], "total_obligations": m(total),
               "n_suppliers": len(rows), "hhi": round(sum(x * x for x in shares.values()) * 10000, 1),
               "top_supplier_share": rows[0]["share"] if rows else None}
    rows = rows[:limit]
    return _result("office_concentration", {"office_code": office_code, "limit": limit}, summary, rows,
                   [i for r in rows for i in r["example_award_ids"]], [r["supplier_id"] for r in rows])


def g_program_suppliers(program_code: str, limit: int = 15) -> dict:
    G = _G()
    agg: dict[str, dict] = defaultdict(lambda: {"obl": 0.0, "sole": 0.0, "n": 0, "_ids": []})
    pname = None
    for _n, a in G.nodes(data=True):
        if a.get("label") != "Award" or a.get("program_code") != program_code:
            continue
        pname = a["program_name"] if pname is None else min(pname, a["program_name"] or pname)
        s = _award_supplier(G, a["award_id"])
        agg[s]["obl"] += a["obl_pos"]
        agg[s]["sole"] += a["obl_pos"] if a["is_sole_source"] else 0.0
        agg[s]["n"] += 1
        agg[s]["_ids"].append((a["obl_pos"], a["award_id"]))
    if not agg:
        raise QueryError(f"no awards for program_code {program_code!r} in scope")
    rows = [{"supplier_id": s, "name": G.nodes[nid("Supplier", s)]["name"], "obligations": m(v["obl"]),
             "sole_source_obligations": m(v["sole"]), "n_awards": v["n"], "example_award_ids": _top_ids(v["_ids"])}
            for s, v in agg.items()]
    rows.sort(key=lambda r: (-r["obligations"], r["supplier_id"]))
    summary = {"program_code": program_code, "program_name": pname, "n_suppliers": len(rows),
               "total_obligations": m(sum(v["obl"] for v in agg.values())),
               "sole_source_obligations": m(sum(v["sole"] for v in agg.values()))}
    rows = rows[:limit]
    return _result("program_suppliers", {"program_code": program_code, "limit": limit}, summary, rows,
                   [i for r in rows for i in r["example_award_ids"]], [r["supplier_id"] for r in rows])


def g_country_exposure(country_iso3: str, limit: int = 10) -> dict:
    G = _G()
    iso = country_iso3.upper()
    cn = nid("Country", iso)
    if cn not in G:
        raise QueryError(f"no awards or subawards performed in {iso!r} in scope and no risk-feed entry")
    c = G.nodes[cn]
    agg: dict[str, dict] = defaultdict(lambda: {"prime": 0.0, "sub": 0.0, "_ids": []})
    offices: dict[str, dict] = defaultdict(lambda: {"n_awards": 0, "obligations": 0.0})
    n_prime = 0
    for u, _, k in G.in_edges(cn, keys=True):
        if k != "PERFORMED_IN":
            continue
        a = G.nodes[u]
        n_prime += 1
        if a["office_code"]:
            offices[a["office_code"]]["n_awards"] += 1
            offices[a["office_code"]]["obligations"] += a["obl_pos"]
        s = _award_supplier(G, a["award_id"])
        agg[s]["prime"] += a["obl_pos"]
        agg[s]["_ids"].append((a["obl_pos"], a["award_id"]))
    n_sub = 0
    for _, v, _, d in G.edges(keys=True, data=True):
        if d.get("type") == "SUBCONTRACTS_TO" and d.get("pop_country") == iso:
            n_sub += 1
            s = G.nodes[v]["supplier_id"]
            agg[s]["sub"] += max(d["amount"] or 0, 0)
            agg[s]["_ids"].append((max(d["amount"] or 0, 0), d["prime_award_id"]))
    rows = [{"supplier_id": s, "name": G.nodes[nid("Supplier", s)]["name"], "prime_obligations": m(v["prime"]),
             "sub_amount": m(v["sub"]), "example_award_ids": _top_ids(v["_ids"])} for s, v in agg.items()]
    rows.sort(key=lambda r: (-(r["prime_obligations"] + r["sub_amount"]), r["supplier_id"]))
    summary = {"country_iso3": iso, "country_name": c.get("name"), "n_prime_awards": n_prime,
               "prime_obligations": m(sum(v["prime"] for v in agg.values())), "n_subawards": n_sub,
               "sub_amount": m(sum(v["sub"] for v in agg.values())), "n_suppliers": len(rows),
               "top_offices": _top_offices([(k, G.nodes[nid("Office", k)]["name"], v["n_awards"], v["obligations"])
                                            for k, v in offices.items()]),
               "p_escalation": round(c["p_escalation"], 4) if c.get("p_escalation") is not None else None,
               "risk_week_start": c.get("risk_week_start"), "risk_skill_status": c.get("risk_skill_status")}
    rows = rows[:limit]
    return _result("country_exposure", {"country_iso3": iso, "limit": limit}, summary, rows,
                   [i for r in rows for i in r["example_award_ids"]], [r["supplier_id"] for r in rows])


def g_parent_subsidiaries(parent_uei: str) -> dict:
    G = _G()
    pn = nid("ParentCompany", parent_uei)
    if pn not in G:
        raise QueryError(f"unknown parent_uei {parent_uei!r}")
    rows = []
    for u, _, k in G.in_edges(pn, keys=True):
        if k != "SUBSIDIARY_OF":
            continue
        aw = _prime_awards(G, u)
        rows.append({"supplier_id": G.nodes[u]["supplier_id"], "name": G.nodes[u]["name"],
                     "prime_obligations": m(sum(a["obl_pos"] for a in aw)), "n_prime_awards": len(aw),
                     "example_award_ids": _top_ids([(a["obl_pos"], a["award_id"]) for a in aw])})
    rows.sort(key=lambda r: (-r["prime_obligations"], r["supplier_id"]))
    summary = {"parent_uei": parent_uei, "parent_name": G.nodes[pn].get("name"), "n_subsidiaries": len(rows),
               "total_prime_obligations": m(sum(r["prime_obligations"] for r in rows))}
    return _result("parent_subsidiaries", {"parent_uei": parent_uei}, summary, rows,
                   [i for r in rows for i in r["example_award_ids"]], [parent_uei, *[r["supplier_id"] for r in rows]])


def g_subcontractors_of(supplier_id: str, limit: int = 15) -> dict:
    G = _G()
    sn = _supplier(G, supplier_id)
    agg: dict[str, dict] = defaultdict(lambda: {"amt": 0.0, "n": 0, "_ids": []})
    for v, d in _sub_edges_out(G, sn):
        s = G.nodes[v]["supplier_id"]
        agg[s]["amt"] += max(d["amount"] or 0, 0)
        agg[s]["n"] += 1
        agg[s]["_ids"].append((max(d["amount"] or 0, 0), d["prime_award_id"]))
    rows = [{"supplier_id": s, "name": G.nodes[nid("Supplier", s)]["name"], "sub_amount": m(v["amt"]),
             "n_subawards": v["n"], "example_award_ids": _top_ids(v["_ids"])} for s, v in agg.items()]
    rows.sort(key=lambda r: (-r["sub_amount"], r["supplier_id"]))
    summary = {"supplier_id": supplier_id, "n_subcontractors": len(rows),
               "total_sub_amount": m(sum(v["amt"] for v in agg.values())),
               "n_subawards": sum(v["n"] for v in agg.values())}
    rows = rows[:limit]
    return _result("subcontractors_of", {"supplier_id": supplier_id, "limit": limit}, summary, rows,
                   [i for r in rows for i in r["example_award_ids"]], [supplier_id, *[r["supplier_id"] for r in rows]])


def g_office_flagged_suppliers(office_code: str, limit: int = 15) -> dict:
    """The persona's question for one office: which suppliers it depends on are concentrated,
    sole-sourced or geographically exposed, and how much of the office's obligations ride on them."""
    G = _G()
    on = nid("Office", office_code)
    if on not in G:
        raise QueryError(f"unknown office_code {office_code!r}")
    agg: dict[str, dict] = defaultdict(lambda: {"obl": 0.0, "n": 0, "_ids": []})
    total = 0.0
    for u, _, k in G.in_edges(on, keys=True):
        if k != "AWARDED_BY":
            continue
        a = G.nodes[u]
        total += a["obl_pos"]
        s = _award_supplier(G, a["award_id"])
        agg[s]["obl"] += a["obl_pos"]
        agg[s]["n"] += 1
        agg[s]["_ids"].append((a["obl_pos"], a["award_id"]))
    rows = []
    for s, v in agg.items():
        node = G.nodes[nid("Supplier", s)]
        flags = [f for f in ("concentrated", "sole_source", "geo_exposed") if node.get(f"flag_{f}")]
        if flags:
            rows.append({"supplier_id": s, "name": node["name"], "flags": flags, "obligations": m(v["obl"]),
                         "n_awards": v["n"], "example_award_ids": _top_ids(v["_ids"])})
    rows.sort(key=lambda r: (-r["obligations"], r["supplier_id"]))
    flagged_total = sum(r["obligations"] for r in rows)
    summary = {"office_code": office_code, "office_name": G.nodes[on]["name"], "total_obligations": m(total),
               "n_suppliers": len(agg), "n_flagged_suppliers": len(rows), "flagged_obligations": m(flagged_total),
               "flagged_share": round(flagged_total / total, 4) if total else None}
    rows = rows[:limit]
    return _result("office_flagged_suppliers", {"office_code": office_code, "limit": limit}, summary, rows,
                   [i for r in rows for i in r["example_award_ids"]], [r["supplier_id"] for r in rows])


# ------------------------------------------------------------------ what-if scenario (remove suppliers)
def _ids(supplier_ids) -> list[str]:
    """Normalize a list (or comma/space separated string) of UEIs: upper case, unique, sorted."""
    if isinstance(supplier_ids, str):
        supplier_ids = supplier_ids.replace(",", " ").split()
    out = sorted({str(x).strip().upper() for x in (supplier_ids or []) if str(x).strip()})
    if not out:
        raise QueryError("what_if_remove needs at least one supplier_id")
    if len(out) > 10:
        raise QueryError("what_if_remove takes at most 10 supplier_ids")
    return out


def _alternatives(pscs: set, office: str, removed: set, by_psc: dict, at_office: dict, names: dict) -> dict:
    """Suppliers (not removed) with DoD prime awards in the same 4-character PSC codes, ranked by whether they
    already hold such awards from this office, then by their obligations in those codes."""
    obl: dict[str, float] = defaultdict(float)
    for p in pscs:
        for sid, v in by_psc.get(p, {}).items():
            if sid not in removed:
                obl[sid] += v
    serves = {sid for p in pscs for sid in at_office.get((office, p), ()) if sid not in removed}
    ranked = sorted(obl, key=lambda x: (x not in serves, -round(obl[x], 2), x))
    return {"n_alternatives": len(ranked), "n_alternatives_at_office": len(serves),
            "top_alternatives": [{"supplier_id": x, "name": names[x], "serves_office": x in serves,
                                  "obligations": m(obl[x])} for x in ranked[:3]]}


def _what_if_rows(offices: dict, removed: set, by_psc: dict, at_office: dict, names: dict, office_names: dict,
                  limit: int) -> tuple[list[dict], int, int]:
    rows = []
    for code, o in offices.items():
        alt = _alternatives(o["psc"], code, removed, by_psc, at_office, names)
        rows.append({"office_code": code, "office_name": office_names[code],
                     "obligations": m(o["direct"] + o["sub"]), "direct_obligations": m(o["direct"]),
                     "sub_amount": m(o["sub"]), "n_awards": len(o["awards"]), "psc_codes": sorted(o["psc"]), **alt})
    rows.sort(key=lambda r: (-r["obligations"], r["office_code"]))
    no_alt_here = sum(r["n_alternatives_at_office"] == 0 for r in rows)
    no_alt = sum(r["n_alternatives"] == 0 for r in rows)
    return rows[:limit], no_alt_here, no_alt


def _what_if_result(ids, names_removed, offices, rows, no_here, no_any, prime, sub, n_awards, programs, codes,
                    top_awards, limit) -> dict:
    summary = {"supplier_ids": ids, "names": names_removed, "n_offices": len(offices), "n_awards": n_awards,
               "prime_obligations": m(prime), "sub_amount": m(sub), "programs": programs, "program_codes": codes,
               "n_offices_no_alternative_at_office": no_here, "n_offices_no_alternative": no_any}
    ents = ids + [a["supplier_id"] for r in rows for a in r["top_alternatives"]]
    return _result("what_if_remove", {"supplier_ids": ids, "limit": limit}, summary, rows, top_awards, ents)


_PSC_INDEX: dict[int, tuple[dict, dict]] = {}


def _psc_index(G) -> tuple[dict, dict]:
    """psc -> {supplier: obligations in that psc}; (office, psc) -> {suppliers with such awards from the office}."""
    if id(G) not in _PSC_INDEX:
        by_psc: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        at_office: dict[tuple[str, str], set[str]] = defaultdict(set)
        for _n, d in G.nodes(data=True):
            if d.get("label") != "Award" or not d.get("psc"):
                continue
            s = _award_supplier(G, d["award_id"])
            by_psc[d["psc"]][s] += d["obl_pos"]
            if d.get("office_code"):
                at_office[(d["office_code"], d["psc"])].add(s)
        _PSC_INDEX.clear()
        _PSC_INDEX[id(G)] = (by_psc, at_office)
    return _PSC_INDEX[id(G)]


def g_what_if_remove(supplier_ids, limit: int = 25) -> dict:
    G = _G()
    ids = _ids(supplier_ids)
    sns = {sid: _supplier(G, sid) for sid in ids}
    removed = set(ids)
    offices: dict[str, dict] = defaultdict(lambda: {"direct": 0.0, "sub": 0.0, "awards": set(), "psc": set()})
    affected: set[str] = set()
    prime = sub = 0.0
    direct_pairs, indirect_pairs = [], []
    for sn in sns.values():
        direct = _prime_awards(G, sn)
        direct_ids = {a["award_id"] for a in direct}
        for a in direct:
            prime += a["obl_pos"]
            affected.add(a["award_id"])
            direct_pairs.append((a["obl_pos"], a["award_id"]))
            if a["office_code"]:
                o = offices[a["office_code"]]
                o["direct"] += a["obl_pos"]
                o["awards"].add(a["award_id"])
                o["psc"].add(a["psc"])
        for d in _sub_edges_in(G, sn):
            amt = max(d["amount"] or 0, 0)
            sub += amt
            a = _award(G, d["prime_award_id"])
            affected.add(a["award_id"])
            if a["award_id"] in direct_ids:
                continue
            indirect_pairs.append((a["obl_pos"], a["award_id"]))
            if a["office_code"]:
                o = offices[a["office_code"]]
                o["sub"] += amt
                o["awards"].add(a["award_id"])
                o["psc"].add(a["psc"])
    progs = {a["program_code"]: a.get("program_name") for a in (_award(G, x) for x in affected) if a["program_code"]}
    by_psc, at_office = _psc_index(G)
    names = {d["supplier_id"]: d["name"] for _n, d in G.nodes(data=True) if d.get("label") == "Supplier"}
    office_names = {c: G.nodes[nid("Office", c)]["name"] for c in offices}
    rows, no_here, no_any = _what_if_rows(offices, removed, by_psc, at_office, names, office_names, limit)
    return _what_if_result(ids, [names[i] for i in ids], offices, rows, no_here, no_any, prime, sub, len(affected),
                           sorted({v for v in progs.values() if v}), sorted(progs),
                           _top_ids(direct_pairs, 5) + _top_ids(indirect_pairs, 5), limit)


# ------------------------------------------------------------------ queries: SQL path
def _q(con, sql: str, params: list | None = None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _exists(con, sql: str, params: list) -> bool:
    return bool(con.execute(sql, params).fetchone()[0])


def _top_sql(ids: list | None, vals: list | None) -> list[str]:
    return _top_ids(list(zip(vals or [], ids or [], strict=True)))


def s_supplier_profile(con, supplier_id: str) -> dict:
    if not _exists(con, "SELECT count(*) FROM suppliers WHERE supplier_id=?", [supplier_id]):
        raise QueryError(f"unknown supplier_id {supplier_id!r}")
    r = _q(con, """
      SELECT s.supplier_id, s.name, s.parent_uei,
        (SELECT count(*) FROM awards_scope WHERE supplier_id=s.supplier_id) n_prime_awards,
        (SELECT coalesce(sum(obl_pos),0) FROM awards_scope WHERE supplier_id=s.supplier_id) prime,
        (SELECT coalesce(sum(obl_pos),0) FROM awards_scope WHERE supplier_id=s.supplier_id AND is_sole_source) sole,
        (SELECT count(*) FROM subawards_scope WHERE sub_supplier_id=s.supplier_id) n_sub,
        (SELECT coalesce(sum(amt_pos),0) FROM subawards_scope WHERE sub_supplier_id=s.supplier_id) sub_amt,
        (SELECT count(DISTINCT oc) FROM (SELECT office_code oc FROM awards_scope WHERE supplier_id=s.supplier_id
            UNION ALL SELECT prime_office_code FROM subawards_scope WHERE sub_supplier_id=s.supplier_id) WHERE oc IS NOT NULL) n_off,
        (SELECT list(DISTINCT c ORDER BY c) FROM (SELECT pop_country c FROM awards_scope WHERE supplier_id=s.supplier_id
            UNION ALL SELECT pop_country FROM subawards_scope WHERE sub_supplier_id=s.supplier_id) WHERE c IS NOT NULL AND c<>'USA') fc,
        r.flag_concentrated, r.flag_sole_source, r.flag_geo_exposed
      FROM suppliers s JOIN supplier_risk r USING (supplier_id) WHERE s.supplier_id=?""", [supplier_id])[0]
    top = _q(con, """SELECT award_id, obl_pos, office_code, is_sole_source FROM awards_scope WHERE supplier_id=?
                     ORDER BY obl_pos DESC, award_id LIMIT 10""", [supplier_id])
    summary = {"supplier_id": supplier_id, "name": r["name"], "parent_uei": r["parent_uei"],
               "n_prime_awards": r["n_prime_awards"], "prime_obligations": m(r["prime"]),
               "sole_source_obligations": m(r["sole"]),
               "sole_source_share": round(r["sole"] / r["prime"], 4) if r["prime"] else None,
               "n_subawards_received": r["n_sub"], "sub_amount_received": m(r["sub_amt"]),
               "n_dependent_offices": r["n_off"], "foreign_countries": list(r["fc"] or []),
               "flag_concentrated": r["flag_concentrated"], "flag_sole_source": r["flag_sole_source"],
               "flag_geo_exposed": r["flag_geo_exposed"]}
    rows = [{"award_id": t["award_id"], "obligated": m(t["obl_pos"]), "office_code": t["office_code"],
             "is_sole_source": bool(t["is_sole_source"])} for t in top]
    return _result("supplier_profile", {"supplier_id": supplier_id}, summary, rows,
                   [x["award_id"] for x in rows], [supplier_id])


def s_dependent_offices(con, supplier_id: str) -> dict:
    if not _exists(con, "SELECT count(*) FROM suppliers WHERE supplier_id=?", [supplier_id]):
        raise QueryError(f"unknown supplier_id {supplier_id!r}")
    rows = _q(con, """
      WITH d AS (SELECT office_code, sum(obl_pos) obl, count(*) n, list(award_id) ids, list(obl_pos) vals
                 FROM awards_scope WHERE supplier_id=? AND office_code IS NOT NULL GROUP BY 1),
           i AS (SELECT prime_office_code office_code, sum(amt_pos) amt, count(DISTINCT prime_award_id) n,
                        list(prime_award_id) ids, list(amt_pos) vals
                 FROM subawards_scope WHERE sub_supplier_id=? AND prime_office_code IS NOT NULL GROUP BY 1)
      SELECT coalesce(d.office_code, i.office_code) office_code, o.office_name,
             coalesce(d.obl,0) obl, coalesce(d.n,0) nd, coalesce(i.amt,0) amt, coalesce(i.n,0) ni,
             list_concat(coalesce(d.ids, []), coalesce(i.ids, [])) ids, list_concat(coalesce(d.vals, []), coalesce(i.vals, [])) vals
      FROM d FULL JOIN i USING (office_code) JOIN office_totals o ON o.office_code = coalesce(d.office_code, i.office_code)
    """, [supplier_id, supplier_id])
    out = [{"office_code": r["office_code"], "office_name": r["office_name"], "direct_obligations": m(r["obl"]),
            "n_direct_awards": r["nd"], "sub_amount": m(r["amt"]), "n_indirect_awards": r["ni"],
            "example_award_ids": _top_sql(r["ids"], r["vals"])} for r in rows]
    out.sort(key=lambda r: (-r["direct_obligations"], -r["sub_amount"], r["office_code"]))
    summary = {"supplier_id": supplier_id, "n_offices": len(out),
               "n_offices_direct": sum(r["n_direct_awards"] > 0 for r in out),
               "n_offices_indirect_only": sum(r["n_direct_awards"] == 0 for r in out)}
    return _result("dependent_offices", {"supplier_id": supplier_id}, summary, out,
                   [i for r in out for i in r["example_award_ids"]], [supplier_id])


def s_blast_radius(con, supplier_id: str) -> dict:
    if not _exists(con, "SELECT count(*) FROM suppliers WHERE supplier_id=?", [supplier_id]):
        raise QueryError(f"unknown supplier_id {supplier_id!r}")
    br = """WITH br AS (
      SELECT award_id, 'direct' kind FROM awards_scope WHERE supplier_id = $1
      UNION
      SELECT DISTINCT prime_award_id, 'indirect' FROM subawards_scope WHERE sub_supplier_id = $1
        AND prime_award_id NOT IN (SELECT award_id FROM awards_scope WHERE supplier_id = $1)) """
    p = [supplier_id]
    s = _q(con, br + """SELECT count(*) FILTER (WHERE kind='direct') nd, count(*) FILTER (WHERE kind='indirect') ni,
                          coalesce(sum(obl_pos) FILTER (WHERE kind='direct'),0) od,
                          coalesce(sum(obl_pos) FILTER (WHERE kind='indirect'),0) oi,
                          count(DISTINCT office_code) noff,
                          list(DISTINCT program_code ORDER BY program_code) FILTER (WHERE program_code IS NOT NULL) progs
                   FROM br JOIN awards_scope USING (award_id)""", p)[0]
    pc = [r["supplier_id"] for r in _q(con, br + """SELECT DISTINCT a.supplier_id FROM br JOIN awards_scope a USING (award_id)
                                               WHERE kind='indirect' ORDER BY 1""", p)]
    rows = _q(con, br + """SELECT a.office_code, min(o.office_name) office_name, count(*) n, sum(a.obl_pos) obl
                      FROM br JOIN awards_scope a USING (award_id) JOIN office_totals o USING (office_code)
                      GROUP BY 1""", p)
    cd = [r["award_id"] for r in _q(con, br + """SELECT award_id FROM br JOIN awards_scope USING (award_id) WHERE kind='direct'
                                            ORDER BY obl_pos DESC, award_id LIMIT 5""", p)]
    ci = [r["award_id"] for r in _q(con, br + """SELECT award_id FROM br JOIN awards_scope USING (award_id) WHERE kind='indirect'
                                            ORDER BY obl_pos DESC, award_id LIMIT 5""", p)]
    summary = {"supplier_id": supplier_id, "n_awards_direct": s["nd"], "n_awards_indirect": s["ni"],
               "obligations_direct": m(s["od"]), "obligations_indirect_awards": m(s["oi"]),
               "n_offices": s["noff"], "programs": list(s["progs"] or []), "n_prime_customers": len(pc),
               "prime_customers": pc[:20]}
    out = [{"office_code": r["office_code"], "office_name": r["office_name"], "n_awards": r["n"],
            "obligations": m(r["obl"])} for r in rows]
    out.sort(key=lambda r: (-r["obligations"], r["office_code"]))
    return _result("blast_radius", {"supplier_id": supplier_id}, summary, out, cd + ci, [supplier_id, *pc[:20]])


def s_sole_source_suppliers_by_country(con, country_iso3: str, limit: int = 10) -> dict:
    iso = country_iso3.upper()
    if not _exists(con, """SELECT count(*) FROM (SELECT pop_country c FROM awards_scope UNION ALL
                           SELECT pop_country FROM subawards_scope UNION ALL SELECT country_iso3 FROM risk_latest) WHERE c=?""", [iso]):
        raise QueryError(f"no awards performed in {country_iso3!r} in scope")
    rows = _q(con, """SELECT a.supplier_id, s.name, sum(obl_pos) obl, count(*) n, list(award_id) ids, list(obl_pos) vals
                      FROM awards_scope a JOIN suppliers s USING (supplier_id)
                      WHERE pop_country=? AND is_sole_source GROUP BY 1, 2""", [iso])
    out = [{"supplier_id": r["supplier_id"], "name": r["name"], "sole_source_obligations": m(r["obl"]),
            "n_sole_source_awards": r["n"], "example_award_ids": _top_sql(r["ids"], r["vals"])} for r in rows]
    out.sort(key=lambda r: (-r["sole_source_obligations"], r["supplier_id"]))
    summary = {"country_iso3": iso, "n_suppliers": len(out), "total_sole_source_obligations": m(sum(r["obl"] for r in rows))}
    out = out[:limit]
    return _result("sole_source_suppliers_by_country", {"country_iso3": iso, "limit": limit}, summary, out,
                   [i for r in out for i in r["example_award_ids"]], [r["supplier_id"] for r in out])


def s_office_concentration(con, office_code: str, limit: int = 10) -> dict:
    o = _q(con, "SELECT * FROM office_totals WHERE office_code=?", [office_code])
    if not o:
        raise QueryError(f"unknown office_code {office_code!r}")
    rows = _q(con, """SELECT a.supplier_id, s.name, sum(obl_pos) obl, count(*) n, list(award_id) ids, list(obl_pos) vals
                      FROM awards_scope a JOIN suppliers s USING (supplier_id) WHERE office_code=? GROUP BY 1, 2""", [office_code])
    total = sum(r["obl"] for r in rows)
    out = [{"supplier_id": r["supplier_id"], "name": r["name"], "obligations": m(r["obl"]),
            "share": round(r["obl"] / total, 4) if total else 0.0, "n_awards": r["n"],
            "example_award_ids": _top_sql(r["ids"], r["vals"])} for r in rows]
    out.sort(key=lambda r: (-r["obligations"], r["supplier_id"]))
    hhi = round(sum((r["obl"] / total) ** 2 for r in rows) * 10000, 1) if total else 0.0
    summary = {"office_code": office_code, "office_name": o[0]["office_name"], "total_obligations": m(total),
               "n_suppliers": len(out), "hhi": hhi, "top_supplier_share": out[0]["share"] if out else None}
    out = out[:limit]
    return _result("office_concentration", {"office_code": office_code, "limit": limit}, summary, out,
                   [i for r in out for i in r["example_award_ids"]], [r["supplier_id"] for r in out])


def s_program_suppliers(con, program_code: str, limit: int = 15) -> dict:
    rows = _q(con, """SELECT a.supplier_id, s.name, min(program_name) pname, sum(obl_pos) obl,
                             sum(CASE WHEN is_sole_source THEN obl_pos ELSE 0 END) sole, count(*) n,
                             list(award_id) ids, list(obl_pos) vals
                      FROM awards_scope a JOIN suppliers s USING (supplier_id) WHERE program_code=? GROUP BY 1, 2""",
              [program_code])
    if not rows:
        raise QueryError(f"no awards for program_code {program_code!r} in scope")
    out = [{"supplier_id": r["supplier_id"], "name": r["name"], "obligations": m(r["obl"]),
            "sole_source_obligations": m(r["sole"]), "n_awards": r["n"],
            "example_award_ids": _top_sql(r["ids"], r["vals"])} for r in rows]
    out.sort(key=lambda r: (-r["obligations"], r["supplier_id"]))
    # min over all suppliers (a program code can carry several names); rows[0] was unordered, so nondeterministic
    names = [r["pname"] for r in rows if r["pname"]]
    summary = {"program_code": program_code, "program_name": min(names) if names else None, "n_suppliers": len(out),
               "total_obligations": m(sum(r["obl"] for r in rows)),
               "sole_source_obligations": m(sum(r["sole"] for r in rows))}
    out = out[:limit]
    return _result("program_suppliers", {"program_code": program_code, "limit": limit}, summary, out,
                   [i for r in out for i in r["example_award_ids"]], [r["supplier_id"] for r in out])


def s_country_exposure(con, country_iso3: str, limit: int = 10) -> dict:
    iso = country_iso3.upper()
    known = _exists(con, """SELECT count(*) FROM (SELECT pop_country c FROM awards_scope UNION ALL
                            SELECT pop_country FROM subawards_scope UNION ALL SELECT country_iso3 FROM risk_latest) WHERE c=?""", [iso])
    if not known:
        raise QueryError(f"no awards or subawards performed in {iso!r} in scope and no risk-feed entry")
    rows = _q(con, """
      WITH x AS (SELECT supplier_id, obl_pos p, 0.0 s, award_id aid FROM awards_scope WHERE pop_country=$1
                 UNION ALL SELECT sub_supplier_id, 0.0, amt_pos, prime_award_id FROM subawards_scope WHERE pop_country=$1)
      SELECT x.supplier_id, s.name, sum(p) prime, sum(s) sub, list(aid) ids, list(p + s) vals
      FROM x JOIN suppliers s USING (supplier_id) GROUP BY 1, 2""", [iso])
    cnt = _q(con, """SELECT (SELECT count(*) FROM awards_scope WHERE pop_country=$1) np,
                            (SELECT count(*) FROM subawards_scope WHERE pop_country=$1) ns""", [iso])[0]
    offs = _q(con, """SELECT a.office_code, min(o.office_name) office_name, count(*) n, sum(a.obl_pos) obl
                      FROM awards_scope a JOIN office_totals o USING (office_code)
                      WHERE a.pop_country=$1 AND a.office_code IS NOT NULL GROUP BY 1""", [iso])
    rk = _q(con, "SELECT p_escalation, week_start, skill_status FROM risk_latest WHERE country_iso3=?", [iso])
    name = _q(con, "SELECT name FROM countries WHERE alpha3=?", [iso])
    out = [{"supplier_id": r["supplier_id"], "name": r["name"], "prime_obligations": m(r["prime"]),
            "sub_amount": m(r["sub"]), "example_award_ids": _top_sql(r["ids"], r["vals"])} for r in rows]
    out.sort(key=lambda r: (-(r["prime_obligations"] + r["sub_amount"]), r["supplier_id"]))
    summary = {"country_iso3": iso, "country_name": name[0]["name"] if name else iso, "n_prime_awards": cnt["np"],
               "prime_obligations": m(sum(r["prime"] for r in rows)), "n_subawards": cnt["ns"],
               "sub_amount": m(sum(r["sub"] for r in rows)), "n_suppliers": len(out),
               "top_offices": _top_offices([(r["office_code"], r["office_name"], r["n"], r["obl"]) for r in offs]),
               "p_escalation": round(rk[0]["p_escalation"], 4) if rk else None,
               "risk_week_start": rk[0]["week_start"].isoformat() if rk else None,
               "risk_skill_status": rk[0]["skill_status"] if rk else None}
    out = out[:limit]
    return _result("country_exposure", {"country_iso3": iso, "limit": limit}, summary, out,
                   [i for r in out for i in r["example_award_ids"]], [r["supplier_id"] for r in out])


def s_parent_subsidiaries(con, parent_uei: str) -> dict:
    p = _q(con, "SELECT * FROM parents WHERE parent_uei=?", [parent_uei])
    if not p:
        raise QueryError(f"unknown parent_uei {parent_uei!r}")
    rows = _q(con, """SELECT s.supplier_id, s.name, coalesce(sum(a.obl_pos),0) obl, count(a.award_id) n,
                             list(a.award_id) FILTER (WHERE a.award_id IS NOT NULL) ids,
                             list(a.obl_pos) FILTER (WHERE a.award_id IS NOT NULL) vals
                      FROM suppliers s LEFT JOIN awards_scope a USING (supplier_id)
                      WHERE s.parent_uei=? GROUP BY 1, 2""", [parent_uei])
    out = [{"supplier_id": r["supplier_id"], "name": r["name"], "prime_obligations": m(r["obl"]),
            "n_prime_awards": r["n"], "example_award_ids": _top_sql(r["ids"], r["vals"])} for r in rows]
    out.sort(key=lambda r: (-r["prime_obligations"], r["supplier_id"]))
    summary = {"parent_uei": parent_uei, "parent_name": p[0]["parent_name"], "n_subsidiaries": len(out),
               "total_prime_obligations": m(sum(r["prime_obligations"] for r in out))}
    return _result("parent_subsidiaries", {"parent_uei": parent_uei}, summary, out,
                   [i for r in out for i in r["example_award_ids"]], [parent_uei, *[r["supplier_id"] for r in out]])


def s_subcontractors_of(con, supplier_id: str, limit: int = 15) -> dict:
    if not _exists(con, "SELECT count(*) FROM suppliers WHERE supplier_id=?", [supplier_id]):
        raise QueryError(f"unknown supplier_id {supplier_id!r}")
    rows = _q(con, """SELECT sub_supplier_id supplier_id, s.name, sum(amt_pos) amt, count(*) n,
                             list(prime_award_id) ids, list(amt_pos) vals
                      FROM subawards_scope x JOIN suppliers s ON s.supplier_id = x.sub_supplier_id
                      WHERE prime_supplier_id=? GROUP BY 1, 2""", [supplier_id])
    out = [{"supplier_id": r["supplier_id"], "name": r["name"], "sub_amount": m(r["amt"]), "n_subawards": r["n"],
            "example_award_ids": _top_sql(r["ids"], r["vals"])} for r in rows]
    out.sort(key=lambda r: (-r["sub_amount"], r["supplier_id"]))
    summary = {"supplier_id": supplier_id, "n_subcontractors": len(out),
               "total_sub_amount": m(sum(r["amt"] for r in rows)), "n_subawards": sum(r["n"] for r in rows)}
    out = out[:limit]
    return _result("subcontractors_of", {"supplier_id": supplier_id, "limit": limit}, summary, out,
                   [i for r in out for i in r["example_award_ids"]], [supplier_id, *[r["supplier_id"] for r in out]])


def s_office_flagged_suppliers(con, office_code: str, limit: int = 15) -> dict:
    o = _q(con, "SELECT * FROM office_totals WHERE office_code=?", [office_code])
    if not o:
        raise QueryError(f"unknown office_code {office_code!r}")
    total = o[0]["total_obligations"]
    n_sup = o[0]["n_suppliers"]
    rows = _q(con, """SELECT a.supplier_id, r.name, r.flag_concentrated, r.flag_sole_source, r.flag_geo_exposed,
                             sum(obl_pos) obl, count(*) n, list(award_id) ids, list(obl_pos) vals
                      FROM awards_scope a JOIN supplier_risk r USING (supplier_id)
                      WHERE office_code=? AND (r.flag_concentrated OR r.flag_sole_source OR r.flag_geo_exposed)
                      GROUP BY 1, 2, 3, 4, 5""", [office_code])
    out = [{"supplier_id": r["supplier_id"], "name": r["name"],
            "flags": [f for f in ("concentrated", "sole_source", "geo_exposed") if r[f"flag_{f}"]],
            "obligations": m(r["obl"]), "n_awards": r["n"], "example_award_ids": _top_sql(r["ids"], r["vals"])}
           for r in rows]
    out.sort(key=lambda r: (-r["obligations"], r["supplier_id"]))
    flagged_total = sum(r["obligations"] for r in out)
    summary = {"office_code": office_code, "office_name": o[0]["office_name"], "total_obligations": m(total),
               "n_suppliers": n_sup, "n_flagged_suppliers": len(out), "flagged_obligations": m(flagged_total),
               "flagged_share": round(flagged_total / total, 4) if total else None}
    out = out[:limit]
    return _result("office_flagged_suppliers", {"office_code": office_code, "limit": limit}, summary, out,
                   [i for r in out for i in r["example_award_ids"]], [r["supplier_id"] for r in out])


def s_what_if_remove(con, supplier_ids, limit: int = 25) -> dict:
    ids = _ids(supplier_ids)
    known = {r["supplier_id"] for r in _q(con, "SELECT supplier_id FROM suppliers WHERE list_contains($1, supplier_id)",
                                           [ids])}
    for sid in ids:
        if sid not in known:
            raise QueryError(f"unknown supplier_id {sid!r}")
    removed = set(ids)
    d = _q(con, """SELECT award_id, supplier_id, office_code, psc, obl_pos, program_code, program_name
                   FROM awards_scope WHERE list_contains($1, supplier_id)""", [ids])
    s_rows = _q(con, """SELECT s.sub_supplier_id, s.amt_pos, a.award_id, a.office_code, a.psc, a.obl_pos,
                               a.program_code, a.program_name
                        FROM subawards_scope s JOIN awards_scope a ON a.award_id = s.prime_award_id
                        WHERE list_contains($1, s.sub_supplier_id)""", [ids])
    offices: dict[str, dict] = defaultdict(lambda: {"direct": 0.0, "sub": 0.0, "awards": set(), "psc": set()})
    affected, progs = set(), {}
    direct_by: dict[str, set] = defaultdict(set)
    direct_pairs, indirect_pairs = [], []
    for r in d:
        affected.add(r["award_id"])
        direct_by[r["supplier_id"]].add(r["award_id"])
        direct_pairs.append((r["obl_pos"], r["award_id"]))
        if r["program_code"]:
            progs[r["program_code"]] = r["program_name"]
        if r["office_code"]:
            o = offices[r["office_code"]]
            o["direct"] += r["obl_pos"]
            o["awards"].add(r["award_id"])
            o["psc"].add(r["psc"])
    for r in s_rows:
        affected.add(r["award_id"])
        if r["program_code"]:
            progs[r["program_code"]] = r["program_name"]
        if r["award_id"] in direct_by[r["sub_supplier_id"]]:
            continue
        indirect_pairs.append((r["obl_pos"], r["award_id"]))
        if r["office_code"]:
            o = offices[r["office_code"]]
            o["sub"] += r["amt_pos"]
            o["awards"].add(r["award_id"])
            o["psc"].add(r["psc"])
    pscs = sorted({p for o in offices.values() for p in o["psc"]})
    by_psc: dict[str, dict[str, float]] = defaultdict(dict)
    at_office: dict[tuple[str, str], set[str]] = defaultdict(set)
    if pscs:
        for r in _q(con, "SELECT psc, supplier_id, sum(obl_pos) v FROM awards_scope WHERE list_contains($1, psc) "
                         "GROUP BY 1, 2", [pscs]):
            by_psc[r["psc"]][r["supplier_id"]] = r["v"]
        for r in _q(con, "SELECT DISTINCT office_code, psc, supplier_id FROM awards_scope "
                         "WHERE list_contains($1, psc) AND office_code IS NOT NULL", [pscs]):
            at_office[(r["office_code"], r["psc"])].add(r["supplier_id"])
    cand = sorted({x for v in by_psc.values() for x in v} | removed)
    names = {r["supplier_id"]: r["name"] for r in _q(
        con, "SELECT supplier_id, name FROM suppliers WHERE list_contains($1, supplier_id)", [cand])}
    office_names = {r["office_code"]: r["office_name"] for r in _q(
        con, "SELECT office_code, min(office_name) office_name FROM office_totals WHERE list_contains($1, office_code) "
             "GROUP BY 1", [sorted(offices)])} if offices else {}
    rows, no_here, no_any = _what_if_rows(offices, removed, by_psc, at_office, names, office_names, limit)
    prime = sum(r["obl_pos"] for r in d)
    sub = sum(r["amt_pos"] for r in s_rows)
    return _what_if_result(ids, [names[i] for i in ids], offices, rows, no_here, no_any, prime, sub, len(affected),
                           sorted({v for v in progs.values() if v}), sorted(progs),
                           _top_ids(direct_pairs, 5) + _top_ids(indirect_pairs, 5), limit)


# ------------------------------------------------------------------ registry
@dataclass(frozen=True)
class QuerySpec:
    name: str
    description: str
    params: dict[str, str]
    graph_fn: Callable[..., dict]
    sql_fn: Callable[..., dict]
    optional: dict[str, Any] = field(default_factory=dict)


QUERIES: dict[str, QuerySpec] = {q.name: q for q in [
    QuerySpec("supplier_profile", "Totals, sole-source share, dependent-office count, foreign countries and risk flags "
              "for one supplier, plus its 10 largest prime awards.",
              {"supplier_id": "UEI from resolve_supplier"}, g_supplier_profile, s_supplier_profile),
    QuerySpec("dependent_offices", "Every awarding office that depends on a supplier, directly (prime awards) or "
              "indirectly (the supplier is a subcontractor on that office's awards).",
              {"supplier_id": "UEI"}, g_dependent_offices, s_dependent_offices),
    QuerySpec("blast_radius", "Two-hop blast radius if a supplier fails: hop 1 = awards it holds as prime or "
              "subcontracts on; hop 2 = the offices, programs and prime contractors attached to those awards.",
              {"supplier_id": "UEI"}, g_blast_radius, s_blast_radius),
    QuerySpec("sole_source_suppliers_by_country", "Suppliers with non-competed prime awards performed in a country, "
              "ranked by sole-source obligations there.",
              {"country_iso3": "ISO alpha-3"}, g_sole_source_suppliers_by_country, s_sole_source_suppliers_by_country,
              {"limit": 10}),
    QuerySpec("office_concentration", "Supplier concentration for one awarding office: shares of obligations, HHI.",
              {"office_code": "awarding office code"}, g_office_concentration, s_office_concentration, {"limit": 10}),
    QuerySpec("program_suppliers", "Suppliers on a DoD acquisition program's awards with obligations and "
              "sole-source amounts.", {"program_code": "DoD acquisition program code, e.g. 341"},
              g_program_suppliers, s_program_suppliers, {"limit": 15}),
    QuerySpec("what_if_remove", "Scenario: remove one or more suppliers (list of UEIs). Returns the offices affected, "
              "prime obligations and subaward dollars at stake, programs touched, and for each affected office its "
              "service codes (4-character PSC) and alternative suppliers: suppliers with DoD awards in the same "
              "service codes, ranked by whether they already do that work for the office, then by obligations. An "
              "alternative means the same type of work, not proof it could take over.",
              {"supplier_ids": "list of UEIs (1 to 10)"}, g_what_if_remove, s_what_if_remove, {"limit": 25}),
    QuerySpec("country_exposure", "Prime awards and subawards performed in a country, the suppliers involved, the "
              "offices awarding that work (top_offices), and the country's latest p_escalation if the risk feed "
              "covers it.",
              {"country_iso3": "ISO alpha-3"}, g_country_exposure, s_country_exposure, {"limit": 10}),
    QuerySpec("parent_subsidiaries", "Subsidiary suppliers under a parent UEI with their prime obligations.",
              {"parent_uei": "parent UEI"}, g_parent_subsidiaries, s_parent_subsidiaries),
    QuerySpec("subcontractors_of", "Subcontractors a prime supplier reports, with subaward amounts.",
              {"supplier_id": "UEI"}, g_subcontractors_of, s_subcontractors_of, {"limit": 15}),
    QuerySpec("office_flagged_suppliers", "For one office: suppliers it pays that are flagged concentrated, "
              "sole-source or geographically exposed, and the share of obligations riding on them.",
              {"office_code": "awarding office code"}, g_office_flagged_suppliers, s_office_flagged_suppliers,
              {"limit": 15}),
]}


def run(name: str, params: dict, backend: str = "graph", con: duckdb.DuckDBPyConnection | None = None) -> dict:
    if name not in QUERIES:
        raise QueryError(f"unknown query {name!r}; choose one of {sorted(QUERIES)}")
    spec = QUERIES[name]
    allowed = set(spec.params) | set(spec.optional)
    extra = set(params) - allowed
    missing = set(spec.params) - set(params)
    if extra or missing:
        raise QueryError(f"{name}: missing {sorted(missing)} / unexpected {sorted(extra)}; params are {sorted(allowed)}")
    kwargs = {**spec.optional, **params}
    if "limit" in kwargs:
        kwargs["limit"] = max(1, min(int(kwargs["limit"]), 50))
    if backend == "graph":
        return spec.graph_fn(**kwargs)
    own = con is None
    con = con or connect(read_only=True)
    try:
        return spec.sql_fn(con, **kwargs)
    finally:
        if own:
            con.close()
