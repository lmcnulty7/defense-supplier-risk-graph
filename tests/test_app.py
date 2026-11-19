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
