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
                # name and code (possibly wrapped), then the dollars in bold on their own line; never cut short
                assert re.search(rf"\({re.escape(code)}\)\n<b>\$[\d,.]+[KMB]?</b>$", n["label"]), n["label"]
            assert "…" not in n["label"], n["label"]
            if n["id"].startswith("office:"):
                assert code in n["title"]  # full name and code on hover


def test_united_states_never_drawn(sample):
    from supplier_graph.graph import queries as Q
    from supplier_graph.graph.build import load_graph
    from supplier_graph.graph_view import blast_view, neighbourhood
    G = load_graph()
    for sid in sample:
        rows = Q.run("blast_radius", {"supplier_id": sid})["rows"]
        for nodes, *_ in (blast_view(G, sid, rows), neighbourhood(G, sid, rows)):
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


def test_short_office_names():
    from supplier_graph.graph_view import office_short
    assert office_short("NAVAL AIR SYSTEMS COMMAND", "N00019") == "NAVAIR"
    assert office_short("DCMA VERTICAL LIFT STRATFORD", "S0707A") == "DCMA Stratford"
    assert office_short("FA8124  AFLCMC LPK", "FA8124") == "AFLCMC LPK"  # leading office code dropped
    assert office_short("W6QK ACC-RSA", "W58RGZ") == "ACC-RSA"  # leading unit code dropped
    assert office_short("DCMA NORTHEAST", "S2206A") == "DCMA NORTHEAST"


def test_no_arrowheads_and_labels_on_white():
    from supplier_graph.graph_view import BLAST_OPTIONS, OPTIONS
    for o in (OPTIONS, BLAST_OPTIONS):
        assert o["edges"]["arrows"] == {"to": {"enabled": False}, "from": {"enabled": False}}
        assert o["nodes"]["font"]["multi"] == "html" and o["nodes"]["font"]["background"].startswith("rgba(255")


def test_award_view_draws_every_affected_office(sample):
    """The award-level view caps award nodes, never offices: it shows the same offices as the metric."""
    from supplier_graph.graph import queries as Q
    from supplier_graph.graph.build import load_graph
    from supplier_graph.graph_view import MAX_DIRECT, MAX_SUB, neighbourhood
    G = load_graph()
    for sid in sample:
        br = Q.run("blast_radius", {"supplier_id": sid})
        nodes, edges, shown, total = neighbourhood(G, sid, br["rows"])
        offices = {n["id"] for n in nodes if n["id"].startswith("office:")}
        assert offices == {f"office:{r['office_code']}" for r in br["rows"]}, sid
        assert len(offices) == br["summary"]["n_offices"], sid
        awards = [n for n in nodes if n["id"].startswith("award:")]
        assert len(awards) == shown <= MAX_DIRECT + MAX_SUB and shown <= total, sid
        assert total == br["summary"]["n_awards_direct"] + br["summary"]["n_awards_indirect"], sid
        assert all(n["label"] == "" and n["id"].split(":", 1)[1] in n["title"] for n in awards)  # ID on hover only
        linked = {e["to"] for e in edges} | {e["from"] for e in edges}
        assert offices <= linked, sid  # every office is connected to something


def test_office_edge_amounts_are_the_suppliers_own_money(sample):
    """Solid office edges carry the supplier's own prime obligations (they sum to its prime obligations); dashed
    (subcontract-only) edges carry its own subaward amounts, with the prime contract value only in the hover."""
    from supplier_graph.graph import queries as Q
    from supplier_graph.graph.build import load_graph
    from supplier_graph.graph_view import blast_view, money, office_money
    G = load_graph()
    for sid in sample:
        prof = Q.run("supplier_profile", {"supplier_id": sid})["summary"]
        dep = {r["office_code"]: r for r in Q.run("dependent_offices", {"supplier_id": sid})["rows"]}
        nodes, edges = blast_view(G, sid, Q.run("blast_radius", {"supplier_id": sid})["rows"])
        labels = {n["id"]: n["label"] for n in nodes}
        office_edges = [e for e in edges if e.get("kind")]
        solid = [e for e in office_edges if e["kind"] == "direct"]
        assert abs(sum(e["amount"] for e in solid) - prof["prime_obligations"]) < 0.01 * max(len(solid), 1), sid
        for e in office_edges:
            code = e["to"].split(":", 1)[1]
            assert e["dashes"] == (e["kind"] == "subcontract")
            assert labels[e["to"]].endswith(f"<b>{money(e['amount'])}</b>"), labels[e["to"]]
            if e["kind"] == "subcontract":
                assert dep[code]["n_direct_awards"] == 0
                assert abs(e["amount"] - dep[code]["sub_amount"]) < 0.01, (sid, code)
                pv = office_money(G, sid)[code]["prime_via"]
                assert f"{money(pv)} prime contract" in e["title"] and "subcontracts: " in e["title"]


def test_sikorsky_dashed_edge_example():
    """The reported case: ACC-RSA W9124P is $14M of S & K prime contracts, of which Sikorsky's subawards are $7.7M."""
    from supplier_graph.graph import queries as Q
    from supplier_graph.graph.build import load_graph
    from supplier_graph.graph_view import blast_view
    sid = "UTJWTSLMFNG4"
    nodes, edges = blast_view(load_graph(), sid, Q.run("blast_radius", {"supplier_id": sid})["rows"])
    (e,) = [e for e in edges if e["to"] == "office:W9124P"]
    assert e["kind"] == "subcontract" and e["dashes"]
    assert "$14M prime contracts via S & K" in e["title"] and "Sikorsky's subcontracts: $7.7M" in e["title"]
    assert next(n for n in nodes if n["id"] == "office:W9124P")["label"].endswith("<b>$7.7M</b>")


def test_award_view_styling(sample):
    """Award view: dotted edges to offices with no awards drawn are green; the supplier's label sits below on white
    with extra mass to keep award nodes off it; the legend adds the award dot and the dotted line."""
    from supplier_graph.graph import queries as Q
    from supplier_graph.graph.build import load_graph
    from supplier_graph.graph_view import (
        AWARD_LEGEND,
        COLORS,
        LEGEND,
        OFFICE_EDGE,
        legend_html,
        neighbourhood,
    )
    G = load_graph()
    dotted_seen = False
    for sid in sample:
        nodes, edges, _, _ = neighbourhood(G, sid, Q.run("blast_radius", {"supplier_id": sid})["rows"])
        centre = next(n for n in nodes if n["id"] == f"supplier:{sid}")
        assert centre["font"]["background"].startswith("rgba(255,255,255") and centre["font"]["vadjust"] > 0
        assert centre["mass"] > 1
        for e in edges:
            if e.get("dashes") == [2, 6]:
                dotted_seen = True
                assert e["color"] == {"color": OFFICE_EDGE} and e["to"].startswith("office:")
    assert dotted_seen  # the sample includes a supplier with more offices than drawn awards reach
    labels = [x[2] for x in AWARD_LEGEND]
    assert labels[0] == "Award" and AWARD_LEGEND[0][1] == COLORS["Award"]
    assert ("dotted", OFFICE_EDGE, "Office with no awards drawn") in AWARD_LEGEND
    assert [x for x in LEGEND if x[0] == "dot"] == [x for x in AWARD_LEGEND if x[0] == "dot"][1:]
    html = legend_html(AWARD_LEGEND)
    assert "3px dotted" in html and "Office with no awards drawn" in html and "<svg" not in html
