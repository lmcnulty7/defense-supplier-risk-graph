"""Office view: who an awarding office depends on, and the offices that rely on one sole-source supplier.

Top suppliers, shares and the HHI come from the registered `office_concentration` query. The sole-source figures here
are computed two independent ways (graph traversal and DuckDB SQL) and tests/test_office_view.py checks they agree.
"""

from __future__ import annotations

from collections import defaultdict

import networkx as nx

from supplier_graph.graph.schema import nid
from supplier_graph.graph_view import COLORS, OFFICE_EDGE, money, office_short, two_line

MAJORITY = 0.5  # "over half"


def m(x) -> float:
    return round(float(x or 0.0), 2)


# ---------------------------------------------------------------- per supplier at one office
def _office_awards(G: nx.MultiDiGraph, code: str) -> list[dict]:
    on = nid("Office", code)
    if on not in G:
        raise KeyError(code)
    return [G.nodes[u] for u, _, k in G.in_edges(on, keys=True) if k == "AWARDED_BY"]


def _award_supplier(G: nx.MultiDiGraph, award_node: dict) -> str:
    for _, v, k in G.out_edges(nid("Award", award_node["award_id"]), keys=True):
        if k == "AWARDED_TO":
            return G.nodes[v]["supplier_id"]
    raise KeyError(award_node["award_id"])


def g_sole_source_at_office(G: nx.MultiDiGraph, code: str) -> list[dict]:
    """Suppliers with non-competed prime awards from this office: their sole-source and total obligations there."""
    agg: dict[str, dict] = defaultdict(lambda: {"obl": 0.0, "sole": 0.0})
    for a in _office_awards(G, code):
        s = _award_supplier(G, a)
        agg[s]["obl"] += a["obl_pos"]
        if a["is_sole_source"]:
            agg[s]["sole"] += a["obl_pos"]
    rows = [{"supplier_id": s, "name": G.nodes[nid("Supplier", s)]["name"], "sole_source_obligations": m(v["sole"]),
             "obligations": m(v["obl"])} for s, v in agg.items() if v["sole"] > 0]
    return sorted(rows, key=lambda r: (-r["sole_source_obligations"], r["supplier_id"]))


def s_sole_source_at_office(con, code: str) -> list[dict]:
    rows = con.execute("""SELECT a.supplier_id, s.name, sum(a.obl_pos) FILTER (WHERE a.is_sole_source) sole,
                                 sum(a.obl_pos) obl
                          FROM awards_scope a JOIN suppliers s USING (supplier_id)
                          WHERE a.office_code = ? GROUP BY 1, 2""", [code]).fetchall()
    out = [{"supplier_id": sid, "name": nm, "sole_source_obligations": m(sole), "obligations": m(obl)}
           for sid, nm, sole, obl in rows if (sole or 0) > 0]
    return sorted(out, key=lambda r: (-r["sole_source_obligations"], r["supplier_id"]))


# ---------------------------------------------------------------- offices relying on one sole-source supplier
def g_sole_source_majority_offices(G: nx.MultiDiGraph) -> list[dict]:
    """Offices where one supplier gets over half of the office's obligations and over half of that supplier's
    obligations from the office were not competed."""
    by_office: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for _n, d in G.nodes(data=True):
        if d.get("label") != "Award" or not d.get("office_code"):
            continue
        v = by_office[d["office_code"]][_award_supplier(G, d)]
        v[0] += d["obl_pos"]
        v[1] += d["obl_pos"] if d["is_sole_source"] else 0.0
    out = []
    for code, sup in by_office.items():
        total = sum(v[0] for v in sup.values())
        sid, (obl, sole) = max(sup.items(), key=lambda kv: (kv[1][0], kv[0]))
        if total > 0 and obl / total > MAJORITY and obl > 0 and sole / obl > MAJORITY:
            out.append({"office_code": code, "supplier_id": sid, "share": round(obl / total, 4),
                        "sole_source_share": round(sole / obl, 4)})
    return sorted(out, key=lambda r: r["office_code"])


def s_sole_source_majority_offices(con) -> list[dict]:
    rows = con.execute("""
      WITH so AS (SELECT office_code, supplier_id, sum(obl_pos) obl,
                         coalesce(sum(obl_pos) FILTER (WHERE is_sole_source), 0) sole
                  FROM awards_scope WHERE office_code IS NOT NULL GROUP BY 1, 2),
           t AS (SELECT office_code, sum(obl) total FROM so GROUP BY 1),
           top AS (SELECT * FROM so QUALIFY row_number() OVER (PARTITION BY office_code
                                                               ORDER BY obl DESC, supplier_id DESC) = 1)
      SELECT top.office_code, top.supplier_id, top.obl / t.total share, top.sole / top.obl ss
      FROM top JOIN t USING (office_code)
      WHERE t.total > 0 AND top.obl > 0 AND top.obl / t.total > ? AND top.sole / top.obl > ?
      ORDER BY 1""", [MAJORITY, MAJORITY]).fetchall()
    return [{"office_code": c, "supplier_id": s, "share": round(sh, 4), "sole_source_share": round(ss, 4)}
            for c, s, sh, ss in rows]


# ---------------------------------------------------------------- app helpers
def office_options(G: nx.MultiDiGraph) -> dict[str, str]:
    """office code -> "Short name (CODE)", sorted by label, for a searchable dropdown."""
    opts = {d["office_code"]: f"{office_short(d.get('name'), d['office_code'])} ({d['office_code']})"
            for _n, d in G.nodes(data=True) if d.get("label") == "Office"}
    return dict(sorted(opts.items(), key=lambda kv: kv[1].lower()))


def office_graph(G: nx.MultiDiGraph, code: str, rows: list[dict], sole: dict[str, float]) -> tuple[list, list]:
    """The office in the centre and its top suppliers around it (same styling as the Graph explorer): edge width by
    obligations; a supplier is labeled with its obligations from this office, and the hover says how much of that
    was sole-source."""
    on = nid("Office", code)
    d = G.nodes[on]
    nodes = [{"id": on, "label": two_line(f"{office_short(d.get('name'), code)} ({code})"), "shape": "dot",
              "size": 28, "color": COLORS["Office"], "title": f"{d.get('name')} ({code})", "font": {"size": 19}}]
    edges = []
    top = max((r["obligations"] for r in rows), default=0.0) or 1.0
    for r in rows:
        sn = nid("Supplier", r["supplier_id"])
        ss = sole.get(r["supplier_id"], 0.0)
        tip = (f"{r['name']} ({r['supplier_id']})\n{money(r['obligations'])} from this office, {r['share']:.1%} of "
               f"its spending\nsole-source: {money(ss)}")
        nodes.append({"id": sn, "label": two_line(r["name"], r["obligations"]), "shape": "dot", "size": 16,
                      "color": COLORS["Supplier"], "title": tip})
        edges.append({"from": on, "to": sn, "title": tip, "color": {"color": OFFICE_EDGE},
                      "width": 1 + 11 * (r["obligations"] / top) ** 0.5})
    return nodes, edges


def hhi_line(hhi: float) -> str:
    """One plain line explaining the HHI (bands as in the 2023 US Merger Guidelines: above 1,800 is highly
    concentrated, 1,000 to 1,800 moderately)."""
    band = "highly concentrated" if hhi > 1800 else "moderately concentrated" if hhi >= 1000 else "not concentrated"
    return (f"Concentration index (HHI): {hhi:,.0f} out of 10,000, the sum of each supplier's squared percentage share. "
            f"10,000 means one supplier gets everything; this office is {band} (above 1,800 counts as high).")
