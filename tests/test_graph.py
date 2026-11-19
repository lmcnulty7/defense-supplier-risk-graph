import pytest

from supplier_graph.graph.build import load_graph
from supplier_graph.graph.schema import EDGE_TYPES, NODE_LABELS
from supplier_graph.stage import connect

pytestmark = pytest.mark.data


def test_schema_and_provenance_everywhere():
    G = load_graph()
    labels = {d["label"] for _, d in G.nodes(data=True)}
    assert labels == set(NODE_LABELS)
    assert all("prov_source" in d and "prov_pulled_at" in d for _, d in G.nodes(data=True))
    types = set()
    for *_, d in G.edges(data=True):
        assert "prov_source" in d and "prov_pulled_at" in d
        types.add(d["type"])
    assert types == set(EDGE_TYPES)


def test_supplier_risk_features_sane():
    con = connect(read_only=True)
    bad = con.execute("""SELECT count(*) FROM supplier_risk WHERE sole_source_share < 0 OR sole_source_share > 1.0000001
                         OR max_office_share > 1.0000001 OR n_dependent_offices < n_direct_offices""").fetchone()[0]
    n_graph = sum(1 for _, d in load_graph().nodes(data=True) if d["label"] == "Supplier")
    n_sql = con.execute("SELECT count(*) FROM supplier_risk").fetchone()[0]
    con.close()
    assert bad == 0 and n_graph == n_sql


def test_risk_feed_on_country_nodes():
    G = load_graph()
    feed = [d for _, d in G.nodes(data=True) if d["label"] == "Country" and "p_escalation" in d]
    assert feed and all(0 <= d["p_escalation"] <= 1 and d["risk_week_start"] for d in feed)
