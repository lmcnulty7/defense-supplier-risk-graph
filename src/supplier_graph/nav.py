"""Linked navigation between the app's tabs, on a Session State mapping (testable without Streamlit).

The tab bar, the Graph explorer's supplier box, the Office view's office and the scenario's suppliers are keyed
widgets bound to URL query parameters, so setting these keys switches the view (and gives each view a link). A
stack of origins drives the "Back" button; switching tabs by hand clears it.
"""

from __future__ import annotations

from collections.abc import MutableMapping

OVERVIEW, SCENARIO, RISK, LIKELIHOOD, GRAPH, OFFICE, AGENT, ABOUT = TABS = (
    "Overview", "What if it fails?", "Supplier risk table", "Likelihood x impact", "Graph explorer", "Office view",
    "Ask the agent", "About the data")
TAB_KEY, SUPPLIER_KEY, PICK_KEY, OFFICE_KEY, SCENARIO_KEY, BACK_KEY = (
    "tab", "supplier", "supplier_pick", "office", "scenario", "nav_back")


def _go(state: MutableMapping, tab: str, origin: str | None) -> None:
    if origin and origin != tab:
        state.setdefault(BACK_KEY, []).append(origin)
        state[BACK_KEY] = state[BACK_KEY][-10:]
    state[TAB_KEY] = tab


def open_supplier(state: MutableMapping, supplier_id: str, origin: str | None) -> None:
    """Show this supplier in the Graph explorer."""
    state[SUPPLIER_KEY] = supplier_id
    state.pop(PICK_KEY, None)  # the candidate list changes with the query; take its first (exact UEI) match
    _go(state, GRAPH, origin)


def open_office(state: MutableMapping, office_code: str, origin: str | None) -> None:
    state[OFFICE_KEY] = office_code
    _go(state, OFFICE, origin)


def open_scenario(state: MutableMapping, supplier_ids: list[str], origin: str | None) -> None:
    state[SCENARIO_KEY] = list(supplier_ids)
    _go(state, SCENARIO, origin)


def open_node(state: MutableMapping, node_id: str | None, origin: str) -> None:
    """A clicked graph node: offices open the Office view, suppliers the Graph explorer."""
    if not node_id or ":" not in node_id:
        return
    kind, key = node_id.split(":", 1)
    if kind == "office":
        open_office(state, key, origin)
    elif kind == "supplier":
        open_supplier(state, key, origin)


def back_target(state: MutableMapping) -> str | None:
    stack = state.get(BACK_KEY) or []
    return stack[-1] if stack else None


def back(state: MutableMapping) -> None:
    stack = state.get(BACK_KEY) or []
    if stack:
        state[TAB_KEY] = stack.pop()
        state[BACK_KEY] = stack


def clear_back(state: MutableMapping) -> None:
    state[BACK_KEY] = []


def selected_index(state: MutableMapping, table_key: str) -> int | None:
    """First selected row of a selectable st.dataframe (its state is an object or a dict)."""
    sel = state.get(table_key)
    if sel is None:
        return None
    selection = sel.get("selection") if isinstance(sel, dict) else getattr(sel, "selection", None)
    rows = (selection.get("rows") if isinstance(selection, dict) else getattr(selection, "rows", None)) or []
    return rows[0] if rows else None


def open_selected(state: MutableMapping, table_key: str, ids_key: str, kind: str, origin: str) -> None:
    """Row click: open the row's supplier (Graph explorer) or office (Office view). `ids_key` holds the row ids in
    display order, stored when the table was drawn."""
    i = selected_index(state, table_key)
    ids = state.get(ids_key) or []
    if i is None or i >= len(ids):
        return
    (open_supplier if kind == "supplier" else open_office)(state, ids[i], origin)
