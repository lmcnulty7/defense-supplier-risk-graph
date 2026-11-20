"""Linked navigation: the nav helpers on a plain dict, and the app's buttons and Back link through AppTest."""

import pytest

from supplier_graph import nav


def test_open_and_back_on_a_dict():
    st = {nav.TAB_KEY: nav.RISK, nav.PICK_KEY: "OLD"}
    nav.open_supplier(st, "UEI1", nav.RISK)
    assert st[nav.TAB_KEY] == nav.GRAPH and st[nav.SUPPLIER_KEY] == "UEI1" and nav.PICK_KEY not in st
    nav.open_node(st, "office:N00019", nav.GRAPH)
    assert st[nav.TAB_KEY] == nav.OFFICE and st[nav.OFFICE_KEY] == "N00019"
    assert nav.back_target(st) == nav.GRAPH
    nav.back(st)
    assert st[nav.TAB_KEY] == nav.GRAPH and nav.back_target(st) == nav.RISK
    nav.back(st)
    assert st[nav.TAB_KEY] == nav.RISK and nav.back_target(st) is None
    nav.open_node(st, "award:X", nav.GRAPH)  # awards are not linked views
    assert st[nav.TAB_KEY] == nav.RISK
    nav.open_scenario(st, ["A", "B"], nav.OVERVIEW)
    assert st[nav.SCENARIO_KEY] == ["A", "B"] and st[nav.TAB_KEY] == nav.SCENARIO
    nav.clear_back(st)
    assert nav.back_target(st) is None


def test_row_selection_opens_the_row():
    st = {"tbl": {"selection": {"rows": [1], "columns": []}}, "_ids": ["A", "B"], nav.TAB_KEY: nav.RISK}
    nav.open_selected(st, "tbl", "_ids", "supplier", nav.RISK)
    assert st[nav.SUPPLIER_KEY] == "B" and st[nav.TAB_KEY] == nav.GRAPH
    st = {"tbl": {"selection": {"rows": [0]}}, "_ids": ["N00019"], nav.TAB_KEY: nav.SCENARIO}
    nav.open_selected(st, "tbl", "_ids", "office", nav.SCENARIO)
    assert st[nav.OFFICE_KEY] == "N00019" and st[nav.TAB_KEY] == nav.OFFICE
    st = {"tbl": {"selection": {"rows": []}}, nav.TAB_KEY: nav.RISK}
    nav.open_selected(st, "tbl", "_ids", "supplier", nav.RISK)
    assert st[nav.TAB_KEY] == nav.RISK  # nothing selected: stay


def _app():
    from streamlit.testing.v1 import AppTest

    from supplier_graph.config import ROOT
    return AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=300)


def _tab(at, label):
    return next(t for t in at.tabs if t.label == label)


@pytest.mark.data
def test_featured_card_opens_graph_explorer_and_back_returns():
    at = _app().run()
    assert not at.exception, at.exception
    ov = _tab(at, nav.OVERVIEW)
    gx = next(b for b in ov.button if b.label == "Graph explorer")  # the first card: Sikorsky
    gx.click().run()
    assert at.session_state[nav.TAB_KEY] == nav.GRAPH
    assert at.session_state[nav.SUPPLIER_KEY] == "UTJWTSLMFNG4"
    g = _tab(at, nav.GRAPH)
    assert any(m.label == "Offices affected" and m.value == "7" for m in g.metric)
    back = next(b for b in g.button if b.label.startswith("Back to"))
    assert back.label == f"Back to {nav.OVERVIEW}"
    back.click().run()
    assert at.session_state[nav.TAB_KEY] == nav.OVERVIEW


@pytest.mark.data
def test_graph_explorer_what_if_button_opens_scenario():
    at = _app()
    at.session_state[nav.TAB_KEY] = nav.GRAPH
    at.run()
    next(b for b in _tab(at, nav.GRAPH).button if b.label == "What if it fails?").click().run()
    assert at.session_state[nav.TAB_KEY] == nav.SCENARIO
    sc = _tab(at, nav.SCENARIO)
    assert any(m.value.startswith("**Removing Sikorsky Aircraft affects 7 offices") for m in sc.markdown)
    assert any(m.label == "Offices affected" and m.value == "7" for m in sc.metric)
    assert any(b.label == f"Back to {nav.GRAPH}" for b in sc.button)
