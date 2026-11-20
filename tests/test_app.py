"""Headless tests of the Streamlit app (no API calls: the agent call is replaced where Ask is pressed).

Tabs run lazily (only the open tab's code runs), so each test opens its tab through Session State first.
"""
import pytest

pytestmark = pytest.mark.data
TABS = ["Overview", "What if it fails?", "Supplier risk table", "Likelihood x impact", "Graph explorer", "Office view",
        "Ask the agent", "About the data"]


def _open(tab: str):
    from streamlit.testing.v1 import AppTest

    from supplier_graph.config import ROOT
    at = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=300)
    at.session_state["tab"] = tab
    return at.run()


def _tab(at, label):
    return next(t for t in at.tabs if t.label == label)


@pytest.mark.parametrize("tab", TABS)
def test_every_tab_renders_without_exceptions(tab):
    at = _open(tab)
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs] == TABS
    assert any("fictional" in c.value for c in at.caption)
    t = _tab(at, tab)
    assert t.markdown or t.metric or t.dataframe or t.caption, f"{tab} rendered nothing"


def _outlook_counts(at) -> tuple[dict[str, int], int, dict]:
    """Rows per named chart layer, rows in the outlook table, and the chart spec."""
    import json

    import pyarrow as pa
    pr = at.get("vega_lite_chart")[0].proto
    spec = json.loads(pr.spec)
    rows = {d.name: pa.ipc.open_stream(d.data.data).read_all().num_rows for d in pr.datasets}
    layers = {layer["name"]: rows[layer["data"]["name"]] for layer in spec["layer"]}
    (table,) = _tab(at, "Likelihood x impact").dataframe
    return layers, len(table.value), spec


def test_outlook_highlight_follows_filters():
    """Likelihood x impact: the highlighted layer is exactly the filtered table; gray shows every supplier."""
    from supplier_graph.outlook_chart import BASE_NAME, HIGHLIGHT_NAME, RULE_NAME, Y_TITLE
    at = _open("Likelihood x impact")
    assert not at.exception, at.exception
    layers, n_table, spec = _outlook_counts(at)
    n_all = layers[BASE_NAME]
    assert layers[HIGHLIGHT_NAME] == n_table < n_all
    assert layers[RULE_NAME] == 1
    assert spec["layer"][0]["encoding"]["x"]["scale"]["type"] == "log"
    assert spec["layer"][0]["encoding"]["y"]["title"] == Y_TITLE

    at.number_input(key="outlook_min_m").set_value(100.0)
    at.number_input(key="outlook_min_x").set_value(1.0)
    at.run()
    layers2, n_table2, _ = _outlook_counts(at)
    assert layers2[HIGHLIGHT_NAME] == n_table2
    assert n_table2 != n_table
    assert layers2[BASE_NAME] == n_all  # the full population never changes

    at.number_input(key="outlook_min_m").set_value(0.0)  # no rule on a log axis at $0
    at.run()
    layers3, n_table3, _ = _outlook_counts(at)
    assert layers3[HIGHLIGHT_NAME] == n_table3 and RULE_NAME not in layers3


def test_agent_answer_shows_status_reason_and_readable_trace(monkeypatch):
    """Click Ask with the API call replaced by the logged partial-answer run for Japan (no spend): the page shows the
    status, the no-data reason, pluralized counts and one plain line per tool call with the raw JSON nested."""
    from supplier_graph.agent import agent as A
    from supplier_graph.evalh.partial import latest_runs
    rec = latest_runs()["partial_risk_jpn"]
    run = A.AgentRun(**{**{k: v for k, v in rec.items() if k != "tool_calls"},
                        "tool_calls": [A.ToolCall(**t) for t in rec["tool_calls"]]})
    monkeypatch.setattr(A, "ask", lambda *a, **k: run)
    monkeypatch.setenv("BUDGET_USD", "1000")  # the button is disabled once the ledger reaches the guard
    at = _open("Ask the agent")
    _tab(at, "Ask the agent").selectbox[0].set_value("What is the escalation risk for Japan?").run()
    next(b for b in _tab(at, "Ask the agent").button if b.label == "Ask").click().run()
    assert not at.exception, at.exception
    tab = _tab(at, "Ask the agent")
    md = [m.value for m in tab.markdown]
    assert "**Partly answered**" in md
    assert any(m.startswith("**No data:**") for m in md)
    assert any(m.startswith("**Citations:**") and "supplier:" in m for m in md)  # the answered part is cited
    assert any(c.value.startswith("2 steps, 2 tool calls,") for c in tab.caption)
    (trace,) = [e for e in tab.expander if e.label == "Tool-call trace"]
    lines = [m.value for m in trace.markdown]
    assert any(x.startswith("Looked up country risk for Japan → not in feed (") for x in lines), lines
    assert [e.label for e in trace.expander] == ["Raw JSON", "Raw JSON"]


def test_agent_answer_persists_with_followups_and_citation_links(monkeypatch):
    """The answer stays on screen after a click; follow-up buttons fill the question box (no model call); a cited
    supplier button opens it in the Graph explorer with a Back link."""
    from supplier_graph.agent import agent as A
    from supplier_graph.evalh.partial import latest_runs
    rec = latest_runs()["partial_risk_jpn"]
    run = A.AgentRun(**{**{k: v for k, v in rec.items() if k != "tool_calls"},
                        "tool_calls": [A.ToolCall(**t) for t in rec["tool_calls"]]})
    calls = []
    monkeypatch.setattr(A, "ask", lambda *a, **k: calls.append(1) or run)
    monkeypatch.setenv("BUDGET_USD", "1000")
    at = _open("Ask the agent")
    _tab(at, "Ask the agent").selectbox[0].set_value("What is the escalation risk for Japan?").run()
    next(b for b in _tab(at, "Ask the agent").button if b.label == "Ask").click().run()
    tab = _tab(at, "Ask the agent")
    ups = [b for b in tab.button if b.key and b.key.startswith("followup_")]
    assert 2 <= len(ups) <= 3
    ups[0].click().run()
    assert at.session_state["agent_q"] == ups[0].label and len(calls) == 1  # filled, not asked
    assert any(m.value == "**Partly answered**" for m in _tab(at, "Ask the agent").markdown)  # still shown
    cite = next(b for b in _tab(at, "Ask the agent").button if b.key and b.key.startswith("cite_supplier:"))
    sid = cite.key.split(":", 1)[1]
    cite.click().run()
    assert at.session_state["tab"] == "Graph explorer" and at.session_state["supplier"] == sid
    assert any(b.label == "Back to Ask the agent" for b in _tab(at, "Graph explorer").button)
