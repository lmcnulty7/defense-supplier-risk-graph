"""Build the knowledge graph (NetworkX MultiDiGraph) and supplier risk features.

Outputs
  data/graph/graph.pkl.gz           the graph (same schema the Neo4j loader writes)
  DuckDB: awards_scope, subawards_scope, office_totals, supplier_office, risk_latest, supplier_risk
  reports/graph_summary.json        node/edge counts and risk-flag counts

Monetary shares use max(obligated, 0) per award so de-obligations don't produce negative shares.
Obligations are lifetime totals of awards with any action in FY2023-FY2025 (see design doc).
"""

from __future__ import annotations

import gzip
import json
import pickle
from datetime import UTC, datetime
from functools import lru_cache

import networkx as nx
import pandas as pd

from supplier_graph.config import GRAPH_DIR, REPORTS
from supplier_graph.graph.schema import (
    CONCENTRATION_MIN_OFFICE,
    CONCENTRATION_SHARE,
    FOREIGN_SHARE,
    SOLE_SOURCE_SHARE,
    nid,
)
from supplier_graph.stage import connect

GRAPH_PATH = GRAPH_DIR / "graph.pkl.gz"

FEATURE_SQL = f"""
CREATE OR REPLACE TABLE awards_scope AS
SELECT a.*, greatest(coalesce(a.obligated, 0), 0) AS obl_pos, s.supplier_id, s.method AS er_method
FROM awards a JOIN award_supplier s USING (award_id);

CREATE OR REPLACE TABLE subawards_scope AS
SELECT s.*, greatest(coalesce(s.amount, 0), 0) AS amt_pos, ss.supplier_id AS sub_supplier_id,
       ss.method AS er_method, a.supplier_id AS prime_supplier_id, a.office_code AS prime_office_code,
       a.program_code AS prime_program_code
FROM subawards s
JOIN subaward_supplier ss USING (subaward_id)
JOIN awards_scope a ON a.award_id = s.prime_award_id;

CREATE OR REPLACE TABLE office_totals AS
SELECT office_code, min(office_name) AS office_name, min(sub_agency_name) AS sub_agency_name,
       sum(obl_pos) AS total_obligations, count(*) AS n_awards, count(DISTINCT supplier_id) AS n_suppliers
FROM awards_scope WHERE office_code IS NOT NULL GROUP BY office_code;

CREATE OR REPLACE TABLE supplier_office AS
SELECT a.supplier_id, a.office_code, sum(a.obl_pos) AS obligations, count(*) AS n_awards,
       sum(a.obl_pos) / nullif(max(o.total_obligations), 0) AS share_of_office,
       max(o.total_obligations) AS office_total
FROM awards_scope a JOIN office_totals o USING (office_code)
GROUP BY a.supplier_id, a.office_code;

CREATE OR REPLACE TABLE risk_latest AS
SELECT * FROM risk_feed QUALIFY row_number() OVER (PARTITION BY country_iso3 ORDER BY week_start DESC) = 1;

CREATE OR REPLACE TABLE supplier_exposure AS
WITH x AS (
  SELECT supplier_id, pop_country AS iso3, obl_pos AS amt FROM awards_scope
  UNION ALL
  SELECT sub_supplier_id, pop_country, amt_pos FROM subawards_scope
)
SELECT supplier_id, iso3, sum(amt) AS amount FROM x WHERE iso3 IS NOT NULL GROUP BY ALL;

CREATE OR REPLACE TABLE supplier_risk AS
WITH prime AS (
  SELECT supplier_id, count(*) AS n_prime_awards, sum(obl_pos) AS prime_obligations,
         sum(CASE WHEN is_sole_source THEN obl_pos ELSE 0 END) AS sole_source_obligations,
         count(*) FILTER (WHERE is_sole_source) AS n_sole_source_awards,
         sum(CASE WHEN pop_country IS NOT NULL AND pop_country <> 'USA' THEN obl_pos ELSE 0 END) AS foreign_prime_obligations
  FROM awards_scope GROUP BY supplier_id
), sub AS (
  SELECT sub_supplier_id AS supplier_id, count(*) AS n_subawards_received, sum(amt_pos) AS sub_amount_received,
         count(DISTINCT prime_supplier_id) AS n_prime_customers
  FROM subawards_scope GROUP BY 1
), subgiven AS (
  SELECT prime_supplier_id AS supplier_id, count(*) AS n_subawards_given, count(DISTINCT sub_supplier_id) AS n_subcontractors
  FROM subawards_scope GROUP BY 1
), offices AS (
  SELECT supplier_id, count(DISTINCT office_code) AS n_dependent_offices FROM (
    SELECT supplier_id, office_code FROM awards_scope WHERE office_code IS NOT NULL
    UNION SELECT sub_supplier_id, prime_office_code FROM subawards_scope WHERE prime_office_code IS NOT NULL)
  GROUP BY supplier_id
), direct_offices AS (
  SELECT supplier_id, count(DISTINCT office_code) AS n_direct_offices FROM awards_scope
  WHERE office_code IS NOT NULL GROUP BY 1
), conc AS (
  SELECT supplier_id, share_of_office AS max_office_share, office_code AS max_share_office_code
  FROM supplier_office WHERE office_total >= {CONCENTRATION_MIN_OFFICE}
  QUALIFY row_number() OVER (PARTITION BY supplier_id ORDER BY share_of_office DESC, office_code) = 1
), expo AS (
  SELECT e.supplier_id,
         list(DISTINCT e.iso3 ORDER BY e.iso3) FILTER (WHERE e.iso3 <> 'USA') AS foreign_countries,
         list(DISTINCT e.iso3 ORDER BY e.iso3) FILTER (WHERE r.country_iso3 IS NOT NULL) AS feed_countries,
         max(r.p_escalation) AS max_p_escalation,
         sum(e.amount * r.p_escalation) / nullif(sum(e.amount), 0) AS exposure_weighted_p_escalation,
         sum(CASE WHEN r.country_iso3 IS NOT NULL THEN e.amount ELSE 0 END) AS feed_country_amount
  FROM supplier_exposure e LEFT JOIN risk_latest r ON r.country_iso3 = e.iso3
  GROUP BY e.supplier_id
)
SELECT s.supplier_id, s.name, s.city, s.state, s.country, s.parent_uei, s.parent_name,
       coalesce(p.n_prime_awards, 0) AS n_prime_awards,
       coalesce(p.prime_obligations, 0) AS prime_obligations,
       coalesce(p.sole_source_obligations, 0) AS sole_source_obligations,
       coalesce(p.n_sole_source_awards, 0) AS n_sole_source_awards,
       p.sole_source_obligations / nullif(p.prime_obligations, 0) AS sole_source_share,
       coalesce(p.foreign_prime_obligations, 0) AS foreign_prime_obligations,
       p.foreign_prime_obligations / nullif(p.prime_obligations, 0) AS foreign_share,
       coalesce(sub.n_subawards_received, 0) AS n_subawards_received,
       coalesce(sub.sub_amount_received, 0) AS sub_amount_received,
       coalesce(sub.n_prime_customers, 0) AS n_prime_customers,
       coalesce(sg.n_subawards_given, 0) AS n_subawards_given,
       coalesce(sg.n_subcontractors, 0) AS n_subcontractors,
       coalesce(o.n_dependent_offices, 0) AS n_dependent_offices,
       coalesce(d.n_direct_offices, 0) AS n_direct_offices,
       c.max_office_share, c.max_share_office_code,
       x.foreign_countries, x.feed_countries, x.max_p_escalation, x.exposure_weighted_p_escalation,
       coalesce(x.feed_country_amount, 0) AS feed_country_amount,
       coalesce(c.max_office_share >= {CONCENTRATION_SHARE}, FALSE) AS flag_concentrated,
       coalesce(p.sole_source_obligations / nullif(p.prime_obligations, 0) >= {SOLE_SOURCE_SHARE}, FALSE) AS flag_sole_source,
       (coalesce(p.foreign_prime_obligations / nullif(p.prime_obligations, 0) >= {FOREIGN_SHARE}, FALSE)
        OR coalesce(len(x.feed_countries) > 0, FALSE)) AS flag_geo_exposed
FROM suppliers s
LEFT JOIN prime p USING (supplier_id)
LEFT JOIN sub USING (supplier_id)
LEFT JOIN subgiven sg USING (supplier_id)
LEFT JOIN offices o USING (supplier_id)
LEFT JOIN direct_offices d USING (supplier_id)
LEFT JOIN conc c USING (supplier_id)
LEFT JOIN expo x USING (supplier_id);
"""


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.date().isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def build() -> nx.MultiDiGraph:
    con = connect()
    con.execute(FEATURE_SQL)
    meta = json.loads(con.execute("SELECT v FROM stage_meta WHERE k='stage'").fetchone()[0])
    u = meta["usaspending"]
    prov_us = {"prov_source": "USAspending /api/v2/download/awards/", "prov_pulled_at": u["pulled_at_utc"],
               "prov_request_key": u["request_key"]}
    prov_feed = {"prov_source": f"MENA risk feed ({'FIXTURE, synthetic' if meta['risk_feed']['is_fixture'] else 'live'})",
                 "prov_pulled_at": meta["staged_at_utc"], "prov_request_key": meta["risk_feed"]["source"]}
    prov_derived = {"prov_source": "derived: USAspending parent UEI", "prov_pulled_at": u["pulled_at_utc"],
                    "prov_request_key": u["request_key"]}

    G = nx.MultiDiGraph()
    sup = con.execute("SELECT * FROM suppliers").df()
    for r in sup.to_dict("records"):
        G.add_node(nid("Supplier", r["supplier_id"]), label="Supplier", supplier_id=r["supplier_id"], name=r["name"],
                   city=_clean(r["city"]), state=_clean(r["state"]), country=_clean(r["country"]),
                   parent_uei=_clean(r["parent_uei"]),
                   cage_codes=list(r["cage_codes"]) if r["cage_codes"] is not None else [],
                   prov_method=",".join(r["resolution_methods"]), **prov_us)
    for r in con.execute("SELECT * FROM parents").df().to_dict("records"):
        G.add_node(nid("ParentCompany", r["parent_uei"]), label="ParentCompany", parent_uei=r["parent_uei"],
                   name=_clean(r["parent_name"]), **prov_derived)
    for r in sup[sup.parent_uei.notna()].to_dict("records"):
        G.add_edge(nid("Supplier", r["supplier_id"]), nid("ParentCompany", r["parent_uei"]), key="SUBSIDIARY_OF",
                   type="SUBSIDIARY_OF", **prov_derived)
    for r in con.execute("SELECT * FROM office_totals").df().to_dict("records"):
        G.add_node(nid("Office", r["office_code"]), label="Office", office_code=r["office_code"],
                   name=r["office_name"], sub_agency=r["sub_agency_name"], **prov_us)
    names = dict(con.execute("SELECT alpha3, name FROM countries").fetchall())
    risk = {r["country_iso3"]: r for r in con.execute("SELECT * FROM risk_latest").df().to_dict("records")}
    used = {c for (c,) in con.execute(
        "SELECT DISTINCT pop_country FROM awards_scope WHERE pop_country IS NOT NULL UNION "
        "SELECT DISTINCT pop_country FROM subawards_scope WHERE pop_country IS NOT NULL").fetchall()}
    for iso in sorted(used | set(risk)):
        attrs = {"label": "Country", "iso3": iso, "name": names.get(iso, iso), **prov_us}
        if iso in risk:
            rr = risk[iso]
            attrs.update(p_escalation=float(rr["p_escalation"]), risk_week_start=_clean(rr["week_start"]),
                         risk_model_version=rr["model_version"], risk_data_cutoff=str(rr["data_cutoff"]),
                         risk_skill_status=rr["skill_status"],
                         risk_feed_is_fixture=bool(rr["feed_is_fixture"]), **{f"risk_{k}": v for k, v in prov_feed.items()})
        G.add_node(nid("Country", iso), **attrs)
    for r in con.execute("SELECT psc, min(psc_description) d FROM awards_scope WHERE psc IS NOT NULL GROUP BY 1").fetchall():
        G.add_node(nid("ProductServiceCode", r[0]), label="ProductServiceCode", psc=r[0], description=r[1], **prov_us)

    aw = con.execute("""SELECT award_id, piid, parent_piid, obligated, obl_pos, potential_value, base_action_date,
                        latest_action_date, latest_action_fy, sub_agency_name, office_code, office_name, pop_country, psc,
                        naics_code, program_code, program_name, extent_competed_code, extent_competed, is_sole_source,
                        offers_received, award_type, permalink, supplier_id, er_method FROM awards_scope""").df()
    for r in aw.to_dict("records"):
        a = nid("Award", r["award_id"])
        G.add_node(a, label="Award", **{k: _clean(v) for k, v in r.items() if k not in ("supplier_id", "er_method")},
                   **prov_us)
        G.add_edge(a, nid("Supplier", r["supplier_id"]), key="AWARDED_TO", type="AWARDED_TO",
                   obligated=_clean(r["obligated"]), prov_method=f"er:{r['er_method']}", **prov_us)
        if _clean(r["office_code"]):
            G.add_edge(a, nid("Office", r["office_code"]), key="AWARDED_BY", type="AWARDED_BY", **prov_us)
        if _clean(r["pop_country"]):
            G.add_edge(a, nid("Country", r["pop_country"]), key="PERFORMED_IN", type="PERFORMED_IN", **prov_us)
        if _clean(r["psc"]):
            G.add_edge(a, nid("ProductServiceCode", r["psc"]), key="CATEGORIZED_AS", type="CATEGORIZED_AS", **prov_us)
    sa = con.execute("""SELECT subaward_id, prime_award_id, amount, action_date, pop_country, subaward_number,
                        prime_supplier_id, sub_supplier_id, er_method FROM subawards_scope""").df()
    for r in sa.to_dict("records"):
        G.add_edge(nid("Supplier", r["prime_supplier_id"]), nid("Supplier", r["sub_supplier_id"]),
                   key=r["subaward_id"], type="SUBCONTRACTS_TO", subaward_id=r["subaward_id"],
                   prime_award_id=r["prime_award_id"], amount=_clean(r["amount"]),
                   action_date=_clean(r["action_date"]), pop_country=_clean(r["pop_country"]),
                   subaward_number=_clean(r["subaward_number"]), prov_method=f"er:{r['er_method']}", **prov_us)

    # Centrality: PageRank on the undirected simple projection (structure only, unweighted).
    pr = nx.pagerank(nx.Graph(G), alpha=0.85, max_iter=200, tol=1e-8)
    sr = con.execute("SELECT * FROM supplier_risk").df()
    sr["pagerank"] = sr["supplier_id"].map(lambda s: pr.get(nid("Supplier", s), 0.0))
    sr["pagerank_pct"] = sr["pagerank"].rank(pct=True)
    con.register("sr", sr)
    con.execute("CREATE OR REPLACE TABLE supplier_risk AS SELECT * FROM sr")
    con.unregister("sr")
    for r in sr.to_dict("records"):
        n = G.nodes[nid("Supplier", r["supplier_id"])]
        n.update(pagerank=r["pagerank"], flag_concentrated=bool(r["flag_concentrated"]),
                 flag_sole_source=bool(r["flag_sole_source"]), flag_geo_exposed=bool(r["flag_geo_exposed"]))

    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(GRAPH_PATH, "wb") as f:
        pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)
    summary = {
        "built_at_utc": datetime.now(UTC).isoformat(),
        "nodes": dict(pd.Series([d["label"] for _, d in G.nodes(data=True)]).value_counts()),
        "edges": dict(pd.Series([d["type"] for *_, d in G.edges(data=True)]).value_counts()),
        "subawards_dropped_prime_out_of_scope": con.execute(
            "SELECT count(*) FROM subawards WHERE prime_award_id NOT IN (SELECT award_id FROM awards)").fetchone()[0],
        "flags": {k: int(sr[k].sum()) for k in ("flag_concentrated", "flag_sole_source", "flag_geo_exposed")},
        "suppliers_any_flag": int((sr.flag_concentrated | sr.flag_sole_source | sr.flag_geo_exposed).sum()),
        "suppliers_with_feed_country_exposure": con.execute(
            "SELECT count(*) FROM supplier_risk WHERE len(feed_countries) > 0").fetchone()[0],
        "risk_feed": meta["risk_feed"],
    }
    # Scope facts quoted in the docs (each was previously computed ad hoc; now written by `make graph`).
    flagged = "flag_concentrated OR flag_sole_source OR flag_geo_exposed"
    summary["scope"] = {
        "n_prime_awards": con.execute("SELECT count(*) FROM awards").fetchone()[0],
        "total_prime_obligations": round(con.execute("SELECT sum(obl_pos) FROM awards_scope").fetchone()[0], 2),
        "n_subawards": con.execute("SELECT count(*) FROM subawards").fetchone()[0],
        "obligation_share_to_flagged_suppliers": round(con.execute(
            f"SELECT sum(prime_obligations) FILTER (WHERE {flagged}) / sum(prime_obligations) FROM supplier_risk"
        ).fetchone()[0], 4),
        "prime_awards_with_subawards_in_scope": con.execute(
            "SELECT count(DISTINCT prime_award_id) FROM subawards WHERE prime_award_id IN (SELECT award_id FROM awards)"
        ).fetchone()[0],
        "prime_award_ids_referenced_by_subawards": con.execute(
            "SELECT count(DISTINCT prime_award_id) FROM subawards").fetchone()[0],
        "awards_missing_pop_country": con.execute("SELECT count(*) FROM awards_scope WHERE pop_country IS NULL").fetchone()[0],
        "awards_with_program_code": con.execute(
            "SELECT count(*) FROM awards_scope WHERE program_code IS NOT NULL").fetchone()[0],
        "program_code_obligation_share": round(con.execute(
            "SELECT sum(obl_pos) FILTER (WHERE program_code IS NOT NULL) / sum(obl_pos) FROM awards_scope").fetchone()[0], 4),
    }
    summary["nodes"] = {k: int(v) for k, v in summary["nodes"].items()}
    summary["edges"] = {k: int(v) for k, v in summary["edges"].items()}
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "graph_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    con.close()
    load_graph.cache_clear()
    print(json.dumps(summary, indent=2, default=str))
    return G


@lru_cache(maxsize=1)
def load_graph() -> nx.MultiDiGraph:
    with gzip.open(GRAPH_PATH, "rb") as f:
        return pickle.load(f)


if __name__ == "__main__":
    build()
