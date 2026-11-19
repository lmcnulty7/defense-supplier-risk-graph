"""End-to-end ER gates (reports/er_holdout.json from `make er`)."""

from __future__ import annotations

import json

import pytest

from supplier_graph.config import REPORTS

pytestmark = pytest.mark.data


def test_shipped_resolver_beats_v1_without_more_wrong_links():
    p = REPORTS / "er_holdout.json"
    if not p.exists():
        pytest.skip("run `make er`")
    m = json.loads(p.read_text())["matchers"]
    v1, new = m["rule, name blocking only (v1)"], m["learned + facility blocking"]
    assert new["present_correct"] > v1["present_correct"] + 0.1
    assert new["present_wrong_link"] <= v1["present_wrong_link"]
    assert new["precision_of_links"] >= v1["precision_of_links"]


def test_operating_point_chosen_on_tuning_half_and_confirmed():
    p = REPORTS / "er_holdout.json"
    if not p.exists():
        pytest.skip("run `make er`")
    op = json.loads(p.read_text())["operating_point"]
    d, s = op["default"], op["selected_point"]
    # chosen by the stated rule on the tuning half
    assert s["utility_tune"] == max(g["utility_tune"] for g in op["curve"])
    # on the untouched confirmation half it cuts false merges without giving up more than 10 points of correct links
    assert s["confirm"]["absent_false_link"] < d["confirm"]["absent_false_link"]
    assert s["confirm"]["present_wrong_link"] <= d["confirm"]["present_wrong_link"]
    assert s["confirm"]["present_correct"] >= d["confirm"]["present_correct"] - 0.10
    shipped = json.loads((REPORTS / "er_operating_point.json").read_text())
    assert (shipped["threshold"], shipped["margin"]) == (s["threshold"], s["margin"])


def test_decide_rules():
    from supplier_graph.er.resolve import decide
    row = {"record_id": "r", "name": "ACME", "zip": "12345"}
    assert decide(row, [("A", 0.9, True), ("B", 0.85, True)], 0.7, margin=0.02)["supplier_id"] == "A"
    assert decide(row, [("A", 0.9, True), ("B", 0.85, True)], 0.7, margin=0.10)["method"] == "unresolved_ambiguous"
    assert decide(row, [("A", 0.9, False)], 0.7, t_addr=2.0)["method"] == "unresolved"  # never link on address alone
    assert decide(row, [("A", 0.6, True)], 0.7)["method"] == "unresolved"
