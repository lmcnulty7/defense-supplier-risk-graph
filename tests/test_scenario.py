"""'What if it fails?' scenario: removal totals equal the Graph explorer's numbers, and the alternative-supplier logic
(same 4-character PSC, ranked by already serving the office, then obligations) holds against independent SQL."""

import pytest

pytestmark = pytest.mark.data

SIKORSKY, BOEING_MESA = "UTJWTSLMFNG4", "NJVNVWQJMPA4"


@pytest.fixture(scope="module")
def sample() -> list[str]:
    from supplier_graph.stage import connect
    con = connect(read_only=True)
    try:
        big = [r[0] for r in con.execute("SELECT supplier_id FROM supplier_risk ORDER BY prime_obligations DESC, "
                                         "supplier_id LIMIT 2").fetchall()]
        sub = con.execute("""SELECT sub_supplier_id FROM subawards_scope GROUP BY 1
                             ORDER BY count(DISTINCT prime_award_id) DESC, 1 LIMIT 1""").fetchone()[0]
    finally:
        con.close()
    return list(dict.fromkeys([SIKORSKY, BOEING_MESA, *big, sub]))


def test_single_removal_matches_graph_explorer(sample):
    """Offices, dollars, awards and per-office amounts equal what the Graph explorer shows for the same supplier."""
    from supplier_graph.graph import queries as Q
    from supplier_graph.graph.build import load_graph
    from supplier_graph.graph_view import blast_view, office_money
    G = load_graph()
    for sid in sample:
        res = Q.run("what_if_remove", {"supplier_ids": [sid], "limit": 50})
        s = res["summary"]
        prof = Q.run("supplier_profile", {"supplier_id": sid})["summary"]
        br = Q.run("blast_radius", {"supplier_id": sid})
        assert s["n_offices"] == br["summary"]["n_offices"], sid  # the "Offices affected" metric
        assert s["prime_obligations"] == prof["prime_obligations"], sid  # the "Prime obligations" metric
        assert s["sub_amount"] == prof["sub_amount_received"], sid
        assert s["n_awards"] == br["summary"]["n_awards_direct"] + br["summary"]["n_awards_indirect"], sid
        offices = {r["office_code"]: r for r in res["rows"]}
        money_at = office_money(G, sid)
        for code, r in offices.items():
            assert abs(r["direct_obligations"] - money_at[code]["direct"]) < 0.01, (sid, code)
            assert abs(r["sub_amount"] - money_at[code]["own_sub"]) < 0.01, (sid, code)
        if s["n_offices"] <= 50:
            assert set(offices) == {r["office_code"] for r in br["rows"]}, sid
            nodes, _ = blast_view(G, sid, br["rows"])
            assert len([n for n in nodes if n["id"].startswith("office:")]) == s["n_offices"]


def test_multi_removal_is_union(sample):
    from supplier_graph.graph import queries as Q
    a, b = SIKORSKY, BOEING_MESA
    both = Q.run("what_if_remove", {"supplier_ids": [a, b], "limit": 50})
    one = [Q.run("what_if_remove", {"supplier_ids": [x], "limit": 50}) for x in (a, b)]
    assert both["summary"]["prime_obligations"] == pytest.approx(sum(r["summary"]["prime_obligations"] for r in one),
                                                                 abs=0.02)
    assert {r["office_code"] for r in both["rows"]} == {r["office_code"] for x in one for r in x["rows"]}
    assert set(both["summary"]["programs"]) == {p for x in one for p in x["summary"]["programs"]}


def test_alternatives_follow_the_definition(sample):
    """Alternatives: not removed, prime awards in the office's PSC codes; 'serves' = such awards from that office;
    ranked serves-first then by obligations in those codes; counts agree with SQL."""
    from supplier_graph.graph import queries as Q
    from supplier_graph.stage import connect
    con = connect(read_only=True)
    try:
        for sid in sample[:3]:
            res = Q.run("what_if_remove", {"supplier_ids": [sid], "limit": 50})
            for r in res["rows"][:5]:
                pscs = r["psc_codes"]
                assert pscs and all(len(p) == 4 for p in pscs)
                obl = dict(con.execute("""SELECT supplier_id, sum(obl_pos) FROM awards_scope
                                          WHERE list_contains(?, psc) AND supplier_id <> ? GROUP BY 1""",
                                       [pscs, sid]).fetchall())
                serves = {x for (x,) in con.execute("""SELECT DISTINCT supplier_id FROM awards_scope
                                                       WHERE list_contains(?, psc) AND office_code = ?
                                                         AND supplier_id <> ?""",
                                                    [pscs, r["office_code"], sid]).fetchall()}
                assert r["n_alternatives"] == len(obl)
                assert r["n_alternatives_at_office"] == len(serves)
                expect = sorted(obl, key=lambda x: (x not in serves, -round(obl[x], 2), x))[:3]
                assert [a["supplier_id"] for a in r["top_alternatives"]] == expect
                for a in r["top_alternatives"]:
                    assert a["serves_office"] == (a["supplier_id"] in serves)
                    assert a["supplier_id"] != sid  # a removed supplier is never its own alternative
    finally:
        con.close()


def test_headline_line_for_sikorsky():
    from supplier_graph import scenario as SC
    line = SC.headline(SC.run([SIKORSKY]))
    assert line == ("Removing Sikorsky Aircraft affects 7 offices and $167M (plus $8.0M in subcontracts); DCMA "
                    "Stratford has no alternative supplier already doing this work (same service code).")


def test_featured_suppliers_are_deterministic():
    from supplier_graph import scenario as SC
    from supplier_graph.stage import connect
    con = connect(read_only=True)
    try:
        pool = [r[0] for r in con.execute("SELECT supplier_id FROM supplier_risk ORDER BY prime_obligations DESC, "
                                          "supplier_id LIMIT ?", [SC.FEATURED_POOL]).fetchall()]
    finally:
        con.close()
    a, b = SC.featured(pool), SC.featured(pool)
    assert [f["supplier_id"] for f in a] == [f["supplier_id"] for f in b]
    assert a[0]["supplier_id"] == SIKORSKY and len({f["supplier_id"] for f in a}) == 3
