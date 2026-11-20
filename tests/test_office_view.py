"""Office view: sole-source figures agree between graph traversal and SQL; office names are short and carry the
code; the HHI line explains the number."""

import pytest

pytestmark = pytest.mark.data


@pytest.fixture(scope="module")
def ctx():
    from supplier_graph.graph.build import load_graph
    from supplier_graph.stage import connect
    con = connect(read_only=True)
    yield load_graph(), con
    con.close()


def test_sole_source_majority_offices_agree(ctx):
    from supplier_graph.headline import load
    from supplier_graph.office_view import g_sole_source_majority_offices, s_sole_source_majority_offices
    G, con = ctx
    g, s = g_sole_source_majority_offices(G), s_sole_source_majority_offices(con)
    assert g == s and g
    assert all(r["share"] > 0.5 and r["sole_source_share"] > 0.5 for r in g)
    assert load()["offices_sole_source_majority"] == f"{len(g):,}"  # the Overview's second fact


@pytest.mark.parametrize("code", ["N00019", "S0707A", "W58RGZ", "N62649"])
def test_sole_source_at_office_agrees(ctx, code):
    from supplier_graph.office_view import g_sole_source_at_office, s_sole_source_at_office
    G, con = ctx
    assert g_sole_source_at_office(G, code) == s_sole_source_at_office(con, code)


def test_office_options_and_hhi_line(ctx):
    from supplier_graph.office_view import hhi_line, office_options
    G, _ = ctx
    opts = office_options(G)
    assert opts["N00019"] == "NAVAIR (N00019)" and len(opts) == sum(1 for _, d in G.nodes(data=True)
                                                                  if d.get("label") == "Office")
    assert all(v.endswith(f"({k})") for k, v in opts.items())
    assert "highly concentrated" in hhi_line(2500) and "not concentrated" in hhi_line(400)
    assert "moderately" in hhi_line(1200)
