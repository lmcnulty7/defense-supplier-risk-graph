import json

import pytest

from supplier_graph.stage import connect

pytestmark = pytest.mark.data


@pytest.fixture(scope="module")
def con():
    c = connect(read_only=True)
    yield c
    c.close()


def test_award_ids_unique(con):
    n, d = con.execute("SELECT count(*), count(DISTINCT award_id) FROM awards").fetchone()
    assert n == d > 0


def test_provenance_recorded(con):
    meta = json.loads(con.execute("SELECT v FROM stage_meta WHERE k='stage'").fetchone()[0])
    u = meta["usaspending"]
    assert u["pulled_at_utc"] and u["request_key"] and u["files"]["prime"]["sha256"]
    assert meta["row_counts"]["awards"] == u["files"]["prime"]["rows"]


def test_country_codes_are_alpha3(con):
    bad = con.execute("""SELECT count(*) FROM (SELECT pop_country c FROM awards UNION ALL
                         SELECT pop_country FROM subawards) WHERE c IS NOT NULL AND length(c) <> 3""").fetchone()[0]
    assert bad == 0


def test_every_award_and_subaward_resolved(con):
    a = con.execute("SELECT count(*) FROM awards WHERE award_id NOT IN (SELECT award_id FROM award_supplier)").fetchone()[0]
    s = con.execute("SELECT count(*) FROM subawards WHERE subaward_id NOT IN (SELECT subaward_id FROM subaward_supplier)").fetchone()[0]
    assert a == 0 and s == 0


def test_sole_source_definition(con):
    wrong = con.execute("""SELECT count(*) FROM awards WHERE is_sole_source <>
                           coalesce(extent_competed_code IN ('B','C','G','NDO'), FALSE)""").fetchone()[0]
    assert wrong == 0


def test_er_metrics_written():
    from supplier_graph.config import REPORTS
    m = json.loads((REPORTS / "er_metrics.json").read_text())
    for fam in ("identifier", "synthetic"):
        assert 0 <= m[fam]["precision"] <= 1 and 0 <= m[fam]["recall"] <= 1
