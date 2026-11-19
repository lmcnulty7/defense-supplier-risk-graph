import os

import pytest

from supplier_graph.config import DUCKDB_PATH


def _have_data() -> bool:
    if not DUCKDB_PATH.exists():
        return False
    import duckdb
    try:
        con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
        ok = bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='suppliers'").fetchone()[0])
        con.close()
        return ok
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    have = _have_data()
    live = os.environ.get("RUN_LIVE") == "1"
    for item in items:
        if "data" in item.keywords and not have:
            item.add_marker(pytest.mark.skip(reason="staged data not built; run `make data`"))
        if "live" in item.keywords and not live:
            item.add_marker(pytest.mark.skip(reason="live API test; set RUN_LIVE=1"))
