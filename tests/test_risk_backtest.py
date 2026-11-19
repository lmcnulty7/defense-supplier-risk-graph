"""Risk backtest: leakage guards and report integrity (needs `make backtest`)."""

from __future__ import annotations

import json

import pytest

from supplier_graph.config import REPORTS

pytestmark = pytest.mark.data
RES = REPORTS / "risk_backtest.json"


@pytest.fixture(scope="module")
def res() -> dict:
    if not RES.exists():
        pytest.skip("run `make backtest` first")
    return json.loads(RES.read_text())


def test_splits_are_forward_in_time(res):
    for sp in ("primary_split", "earlier_split"):
        s = res[sp]
        assert max(s["train_years"]) < s["test_year"], "training data must precede the test year"


def test_feature_window_has_no_outcome_columns(res):
    from supplier_graph.risk.backtest import FEATURES
    assert not any(f.startswith("next_") or f == "y" for f in FEATURES)
    assert set(res["features"]) == set(FEATURES)


def test_labels_come_from_source_action_types(res):
    acts = {r["act"] for r in res["event_counts_by_year"]}
    assert acts <= {"E", "X", "F", "N"}
    assert res["primary_split"]["n_test_pos"] > 50, "too few positives to report metrics"


def test_reported_gain_is_not_overstated(res):
    """The report claims a gain in average precision over the size baseline; its CI must exclude zero."""
    g = res["primary_split"]["ap_gain_vs_size"]["logistic"]
    assert g["ci"][0] > 0


def test_outlook_table_matches_graph(res):
    from supplier_graph.stage import connect
    con = connect(read_only=True)
    n, bad = con.execute("""SELECT count(*), count(*) FILTER (WHERE p_disruption_next_fy NOT BETWEEN 0 AND 1)
                            FROM supplier_disruption_score""").fetchone()
    missing = con.execute("""SELECT count(*) FROM supplier_disruption_score d
                             LEFT JOIN supplier_risk r USING (supplier_id) WHERE r.supplier_id IS NULL""").fetchone()[0]
    con.close()
    assert n == res["outlook"]["n_suppliers_scored"] and bad == 0 and missing == 0
