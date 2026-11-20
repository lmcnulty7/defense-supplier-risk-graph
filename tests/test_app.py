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
