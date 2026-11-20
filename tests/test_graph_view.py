"""Graph explorer view: the drawn offices are exactly the "Offices affected" metric, the United States is never
drawn, and office labels always carry the office code."""

import re

import pytest

pytestmark = pytest.mark.data

FIXED = ["UTJWTSLMFNG4", "NJVNVWQJMPA4"]  # Sikorsky (Stratford, CT); Boeing (Mesa, AZ)


@pytest.fixture(scope="module")
def sample() -> list[str]:
    """Five suppliers that exercise different shapes: the two above, the largest supplier, the subcontractor on the
    most prime awards (offices reached through subcontracts), and the largest supplier with foreign work."""
    from supplier_graph.stage import connect
    con = connect(read_only=True)
    try:
        largest = [r[0] for r in con.execute("SELECT supplier_id FROM supplier_risk ORDER BY prime_obligations DESC, "
                                              "supplier_id LIMIT 3").fetchall()]
        subs = [r[0] for r in con.execute("""SELECT sub_supplier_id FROM subawards_scope GROUP BY 1
                                             ORDER BY count(DISTINCT prime_award_id) DESC, 1 LIMIT 3""").fetchall()]
        foreign = [r[0] for r in con.execute("SELECT supplier_id FROM supplier_risk WHERE foreign_share > 0 "
                                             "ORDER BY prime_obligations DESC, supplier_id LIMIT 3").fetchall()]
    finally:
        con.close()
    out = list(FIXED)
    for group in (largest, subs):
        out.append(next(x for x in group if x not in out))
    out.append(next(x for x in foreign if x not in out))  # last one has foreign work
    return out


def test_office_nodes_equal_offices_affected_metric(sample):
    from supplier_graph.graph import queries as Q
    from supplier_graph.graph.build import load_graph
    from supplier_graph.graph_view import blast_view
    G = load_graph()
    for sid in sample:
        br = Q.run("blast_radius", {"supplier_id": sid})
        nodes, edges = blast_view(G, sid, br["rows"])
        offices = {n["id"] for n in nodes if n["id"].startswith("office:")}
        assert len(offices) == br["summary"]["n_offices"], sid  # the metric shown above the graph
        assert offices == {f"office:{r['office_code']}" for r in br["rows"]}, sid
        sn = f"supplier:{sid}"
        assert {e["to"] for e in edges if e["from"] == sn and e["to"].startswith("office:")} == offices, sid
        assert not any(n["id"].startswith("award:") for n in nodes), sid  # no award nodes by default
        for n in nodes:
            if n["id"].startswith("office:"):
                code = n["id"].split(":", 1)[1]
                assert re.search(rf"\({re.escape(code)}\) · \$[\d,.]+[KMB]?$", n["label"]), n["label"]


def test_united_states_never_drawn(sample):
    from supplier_graph.graph import queries as Q
    from supplier_graph.graph.build import load_graph
    from supplier_graph.graph_view import blast_view, neighbourhood
    G = load_graph()
    for sid in sample:
        for nodes, _ in (blast_view(G, sid, Q.run("blast_radius", {"supplier_id": sid})["rows"]),
                         neighbourhood(G, sid)):
            assert "country:USA" not in {n["id"] for n in nodes}, sid


def test_foreign_country_drawn_when_present(sample):
    from supplier_graph.graph import queries as Q
    from supplier_graph.graph.build import load_graph
    from supplier_graph.graph_view import blast_view
    G = load_graph()
    sid = sample[-1]
    prof = Q.run("supplier_profile", {"supplier_id": sid})["summary"]
    nodes, _ = blast_view(G, sid, Q.run("blast_radius", {"supplier_id": sid})["rows"])
    assert {n["id"] for n in nodes if n["id"].startswith("country:")} == {f"country:{c}" for c in
                                                                          prof["foreign_countries"]}


def test_page_is_offline_and_escapes_script_tags():
    from supplier_graph.graph_view import network_html
    html = network_html([{"id": "a", "label": "</script><b>x", "title": "line1\nline2"}], [])
    assert "</script><b>" not in html  # labels cannot close the script block
    assert not re.search(r"""(?:src|href)\s*=\s*["']?(?:https?:)?//""", html, re.I)  # nothing loaded from the network
