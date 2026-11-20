"""Each named query: graph and SQL implementations agree, and errors are clean."""
import json

import pytest

from supplier_graph.graph import queries as Q
from supplier_graph.stage import connect

pytestmark = pytest.mark.data


@pytest.fixture(scope="module")
def con():
    c = connect(read_only=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def ids(con):
    one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "supplier": one("SELECT supplier_id FROM supplier_risk WHERE n_subawards_received>3 AND n_prime_awards>3 "
                        "ORDER BY prime_obligations DESC LIMIT 1"),
        "sub_only": one("SELECT supplier_id FROM supplier_risk WHERE n_prime_awards=0 AND n_subawards_received>0 "
                        "ORDER BY sub_amount_received DESC LIMIT 1"),
        "office": one("SELECT office_code FROM office_totals ORDER BY total_obligations DESC LIMIT 1"),
        "small_office": one("SELECT office_code FROM office_totals WHERE n_awards BETWEEN 3 AND 10 ORDER BY office_code LIMIT 1"),
        "parent": one("SELECT parent_uei FROM parents ORDER BY n_children DESC LIMIT 1"),
        "prime": one("SELECT supplier_id FROM supplier_risk ORDER BY n_subcontractors DESC LIMIT 1"),
        "program": one("SELECT program_code FROM awards WHERE program_code IS NOT NULL GROUP BY 1 ORDER BY count(*) DESC LIMIT 1"),
    }


def _cases(ids):
    return [
        ("supplier_profile", {"supplier_id": ids["supplier"]}),
        ("supplier_profile", {"supplier_id": ids["sub_only"]}),
        ("dependent_offices", {"supplier_id": ids["supplier"]}),
        ("dependent_offices", {"supplier_id": ids["sub_only"]}),
        ("blast_radius", {"supplier_id": ids["supplier"]}),
        ("blast_radius", {"supplier_id": ids["sub_only"]}),
        ("sole_source_suppliers_by_country", {"country_iso3": "JPN"}),
        ("sole_source_suppliers_by_country", {"country_iso3": "bhr", "limit": 3}),
        ("office_concentration", {"office_code": ids["office"]}),
        ("office_concentration", {"office_code": ids["small_office"]}),
        ("program_suppliers", {"program_code": ids["program"]}),
        ("country_exposure", {"country_iso3": "BHR"}),
        ("country_exposure", {"country_iso3": "SAU"}),
        ("parent_subsidiaries", {"parent_uei": ids["parent"]}),
        ("subcontractors_of", {"supplier_id": ids["prime"]}),
        ("office_flagged_suppliers", {"office_code": ids["office"]}),
        ("what_if_remove", {"supplier_ids": [ids["supplier"]]}),
        ("what_if_remove", {"supplier_ids": [ids["sub_only"]]}),
        ("what_if_remove", {"supplier_ids": [ids["supplier"], ids["prime"]], "limit": 5}),
    ]


def test_every_query_is_covered(ids):
    assert {n for n, _ in _cases(ids)} == set(Q.QUERIES)


@pytest.mark.parametrize("i", range(19))
def test_graph_and_sql_agree(ids, con, i):
    name, params = _cases(ids)[i]
    g = Q.run(name, params, "graph")
    s = Q.run(name, params, "sql", con)
    assert json.dumps(g, sort_keys=True, default=str) == json.dumps(s, sort_keys=True, default=str)
    assert g["citations"]["award_ids"] or g["citations"]["entity_ids"]


def test_blast_radius_counts_indirect(ids):
    r = Q.run("blast_radius", {"supplier_id": ids["sub_only"]})
    assert r["summary"]["n_awards_direct"] == 0 and r["summary"]["n_awards_indirect"] > 0
    assert r["summary"]["n_prime_customers"] >= 1


@pytest.mark.parametrize("backend", ["graph", "sql"])
def test_unknown_entities_raise_query_error(backend, con):
    for name, params in [("supplier_profile", {"supplier_id": "NOTAREALUEI1"}),
                         ("office_concentration", {"office_code": "ZZZZZZ"}),
                         ("country_exposure", {"country_iso3": "ATA"}),
                         ("program_suppliers", {"program_code": "ZZZ"}),
                         ("what_if_remove", {"supplier_ids": ["NOTAREALUEI1"]}),
                         ("what_if_remove", {"supplier_ids": []})]:
        with pytest.raises(Q.QueryError):
            Q.run(name, params, backend, con)


def test_bad_params_rejected():
    with pytest.raises(Q.QueryError):
        Q.run("supplier_profile", {"uei": "X"})
    with pytest.raises(Q.QueryError):
        Q.run("drop_tables", {})


@pytest.mark.data
def test_program_name_agrees_when_a_code_has_several_names():
    """Program 523 carries two names across its awards; both paths must report the same one (regression)."""
    from supplier_graph.stage import connect
    con = connect(read_only=True)
    codes = [c for (c,) in con.execute("""SELECT program_code FROM awards_scope WHERE program_code IS NOT NULL
                                          GROUP BY 1 HAVING count(DISTINCT program_name) > 1 ORDER BY 1""").fetchall()]
    for code in codes:
        assert Q.run("program_suppliers", {"program_code": code}, "sql", con)["summary"] == \
            Q.run("program_suppliers", {"program_code": code}, "graph")["summary"]
    con.close()
