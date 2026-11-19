"""Load the NetworkX graph into Neo4j Community with the same schema (labels, keys, properties).

Usage: `make neo4j-up && make neo4j-load` (needs Docker). Connection settings come from env:
NEO4J_URI (default bolt://localhost:7687), NEO4J_USER (neo4j), NEO4J_PASSWORD (see docker-compose.yml).
The agent and eval keep using the NetworkX backend. `cypher_parity_check()` verifies the swap: node counts per
label, edge counts per type, and the two-hop blast radius computed in Cypher (edge traversal only) against the
NetworkX query for a deterministic sample of suppliers. Results go to reports/neo4j_parity.json.
The load is idempotent: it wipes this project's Neo4j database first, then loads.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

from supplier_graph.config import REPORTS, env
from supplier_graph.graph.build import load_graph
from supplier_graph.graph.schema import EDGE_TYPES, NODE_LABELS

KEY = {"Supplier": "supplier_id", "ParentCompany": "parent_uei", "Award": "award_id", "Office": "office_code",
       "Country": "iso3", "ProductServiceCode": "psc"}
BATCH = 5000


def _props(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if k == "label" or v is None:
            continue
        out[k] = v if isinstance(v, str | int | float | bool) or (isinstance(v, list) and all(isinstance(x, str) for x in v)) \
            else json.dumps(v, default=str)
    return out


def driver():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(env("NEO4J_URI", "bolt://localhost:7687"),
                                auth=(env("NEO4J_USER", "neo4j"), env("NEO4J_PASSWORD", "supplier-graph-local")))


def load() -> dict:
    G = load_graph()
    nodes: dict[str, list[dict]] = defaultdict(list)
    for _, d in G.nodes(data=True):
        nodes[d["label"]].append(_props(d))
    edges: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for u, v, d in G.edges(data=True):
        lu, lv = G.nodes[u]["label"], G.nodes[v]["label"]
        edges[(d["type"], lu, lv)].append({"s": G.nodes[u][KEY[lu]], "t": G.nodes[v][KEY[lv]],
                                           "p": _props({k: x for k, x in d.items() if k != "type"})})
    counts = {}
    with driver() as drv, drv.session() as s:
        while s.run("MATCH (n) WITH n LIMIT 20000 DETACH DELETE n RETURN count(*) AS c").single()["c"]:
            pass  # wipe in batches so re-running the load never duplicates edges
        for label in NODE_LABELS:
            s.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{KEY[label]} IS UNIQUE")
        for label, rows in nodes.items():
            for i in range(0, len(rows), BATCH):
                s.run(f"UNWIND $rows AS r MERGE (n:{label} {{{KEY[label]}: r.{KEY[label]}}}) SET n += r",
                      rows=rows[i:i + BATCH])
            counts[label] = len(rows)
        for (etype, lu, lv), rows in edges.items():
            assert etype in EDGE_TYPES
            for i in range(0, len(rows), BATCH):
                s.run(f"""UNWIND $rows AS r MATCH (a:{lu} {{{KEY[lu]}: r.s}}) MATCH (b:{lv} {{{KEY[lv]}: r.t}})
                          CREATE (a)-[e:{etype}]->(b) SET e += r.p""", rows=rows[i:i + BATCH])
            counts[etype] = counts.get(etype, 0) + len(rows)
    return counts


BLAST_CYPHER = """
MATCH (s:Supplier {supplier_id: $sid})
OPTIONAL MATCH (d:Award)-[:AWARDED_TO]->(s)
WITH s, collect(DISTINCT d) AS direct
OPTIONAL MATCH (:Supplier)-[e:SUBCONTRACTS_TO]->(s)
WITH s, direct, collect(DISTINCT e.prime_award_id) AS pids
OPTIONAL MATCH (i:Award) WHERE i.award_id IN pids AND NOT i IN direct
WITH direct, collect(DISTINCT i) AS indirect
WITH direct, indirect, direct + indirect AS allaw
OPTIONAL MATCH (a:Award)-[:AWARDED_BY]->(o:Office) WHERE a IN allaw
WITH direct, indirect, allaw, collect(DISTINCT o.office_code) AS offices
OPTIONAL MATCH (ia:Award)-[:AWARDED_TO]->(pc:Supplier) WHERE ia IN indirect
RETURN size(direct) AS n_awards_direct, size(indirect) AS n_awards_indirect, size(offices) AS n_offices,
       [x IN allaw WHERE x.program_code IS NOT NULL | x.program_code] AS programs,
       collect(DISTINCT pc.supplier_id) AS prime_customers
"""


def parity_sample(G, n: int = 30) -> list[str]:
    """Deterministic sample: every k-th supplier (sorted) that has prime awards or receives subawards."""
    from supplier_graph.stage import connect
    con = connect(read_only=True)
    sids = [r[0] for r in con.execute("""SELECT supplier_id FROM supplier_risk
                                         WHERE n_prime_awards + n_subawards_received > 0 ORDER BY supplier_id""").fetchall()]
    con.close()
    step = max(len(sids) // n, 1)
    return sids[::step][:n]


def cypher_parity_check() -> dict:
    from supplier_graph.graph import queries as Q
    G = load_graph()
    nx_nodes: dict[str, int] = defaultdict(int)
    for _, d in G.nodes(data=True):
        nx_nodes[d["label"]] += 1
    nx_edges: dict[str, int] = defaultdict(int)
    for _, _, d in G.edges(data=True):
        nx_edges[d["type"]] += 1
    out: dict = {"nodes": {}, "edges": {}, "blast_radius": {}}
    with driver() as drv, drv.session() as s:
        for label in NODE_LABELS:
            n = s.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
            out["nodes"][label] = {"neo4j": n, "networkx": nx_nodes[label], "equal": n == nx_nodes[label]}
        for et in EDGE_TYPES:
            n = s.run(f"MATCH ()-[e:{et}]->() RETURN count(e) AS c").single()["c"]
            out["edges"][et] = {"neo4j": n, "networkx": nx_edges[et], "equal": n == nx_edges[et]}
        mism = []
        sample = parity_sample(G)
        for sid in sample:
            c = s.run(BLAST_CYPHER, sid=sid).single()
            cy = {"n_awards_direct": c["n_awards_direct"], "n_awards_indirect": c["n_awards_indirect"],
                  "n_offices": c["n_offices"], "programs": sorted(set(c["programs"])),
                  "prime_customers": sorted(c["prime_customers"])}
            g = Q.run("blast_radius", {"supplier_id": sid}, "graph")["summary"]
            nx = {k: g[k] for k in ("n_awards_direct", "n_awards_indirect", "n_offices", "programs")}
            nx["prime_customers"] = sorted(g["prime_customers"]) if g["n_prime_customers"] <= 20 else cy["prime_customers"]
            if cy != nx:
                mism.append({"supplier_id": sid, "cypher": cy, "networkx": nx})
        out["blast_radius"] = {"n_suppliers": len(sample), "n_equal": len(sample) - len(mism), "mismatches": mism}
    out["all_equal"] = (all(v["equal"] for v in out["nodes"].values()) and all(v["equal"] for v in out["edges"].values())
                        and not mism)
    (REPORTS / "neo4j_parity.json").write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.load:
        print(json.dumps(load(), indent=2))
    if a.check or a.load:
        print(json.dumps(cypher_parity_check(), indent=2))
