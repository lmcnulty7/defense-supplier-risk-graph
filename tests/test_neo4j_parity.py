"""Neo4j parity (B3). Runs only when a Neo4j server is reachable (`make neo4j-up && make neo4j-load`)."""

from __future__ import annotations

import pytest

from supplier_graph.graph import neo4j_backend as N


def _reachable() -> bool:
    try:
        with N.driver() as d:
            d.verify_connectivity()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="Neo4j not running (make neo4j-up && make neo4j-load)")


def test_counts_and_blast_radius_match_networkx():
    res = N.cypher_parity_check()
    assert all(v["equal"] for v in res["nodes"].values()), res["nodes"]
    assert all(v["equal"] for v in res["edges"].values()), res["edges"]
    assert res["blast_radius"]["mismatches"] == []
    assert res["all_equal"]
