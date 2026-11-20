"""Headless smoke test of the Streamlit app (no API calls: the Ask button is not pressed)."""
import pytest

pytestmark = pytest.mark.data


def test_app_renders_without_exceptions():
    from streamlit.testing.v1 import AppTest

    from supplier_graph.config import ROOT
    at = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=120).run()
    assert not at.exception, at.exception
    assert any("fictional" in c.value for c in at.caption)
    assert len(at.tabs) == 5


def _outlook_counts(at) -> tuple[dict[str, int], int, dict]:
    """Rows per named chart layer, rows in the outlook table, and the chart spec."""
    import json

    import pyarrow as pa
    pr = at.get("vega_lite_chart")[0].proto
    spec = json.loads(pr.spec)
    rows = {d.name: pa.ipc.open_stream(d.data.data).read_all().num_rows for d in pr.datasets}
    layers = {layer["name"]: rows[layer["data"]["name"]] for layer in spec["layer"]}
    tab = at.tabs[1]
    assert tab.label == "Likelihood x impact"
    (table,) = tab.dataframe
    return layers, len(table.value), spec


def test_outlook_highlight_follows_filters():
    """Likelihood x impact: the highlighted layer is exactly the filtered table; gray shows every supplier."""
    from streamlit.testing.v1 import AppTest

    from supplier_graph.config import ROOT
    from supplier_graph.outlook_chart import BASE_NAME, HIGHLIGHT_NAME, RULE_NAME, Y_TITLE
    at = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=120).run()
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
    from streamlit.testing.v1 import AppTest

    from supplier_graph.agent import agent as A
    from supplier_graph.config import ROOT
    from supplier_graph.evalh.partial import latest_runs
    rec = latest_runs()["partial_risk_jpn"]
    run = A.AgentRun(**{**{k: v for k, v in rec.items() if k != "tool_calls"},
                        "tool_calls": [A.ToolCall(**t) for t in rec["tool_calls"]]})
    monkeypatch.setattr(A, "ask", lambda *a, **k: run)
    monkeypatch.setenv("BUDGET_USD", "1000")  # the button is disabled once the ledger reaches the guard
    at = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=120).run()
    tab = at.tabs[3]
    assert tab.label == "Ask the agent"
    tab.selectbox[0].set_value("What is the escalation risk for Japan?").run()
    at.tabs[3].button[0].click().run()
    assert not at.exception, at.exception
    tab = at.tabs[3]
    md = [m.value for m in tab.markdown]
    assert "**Partly answered**" in md
    assert any(m.startswith("**No data:**") for m in md)
    assert any(m.startswith("**Citations:**") and "supplier:" in m for m in md)  # the answered part is cited
    assert any(c.value.startswith("2 steps, 2 tool calls,") for c in tab.caption)
    (trace,) = [e for e in tab.expander if e.label == "Tool-call trace"]
    lines = [m.value for m in trace.markdown]
    assert any(x.startswith("Looked up country risk for Japan → not in feed (") for x in lines), lines
    assert [e.label for e in trace.expander] == ["Raw JSON", "Raw JSON"]
