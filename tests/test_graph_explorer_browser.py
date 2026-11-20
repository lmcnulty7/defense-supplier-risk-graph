"""Every graph in a real browser, offline: drawn, fitted, no console errors, no outside requests; offices match the
"Offices affected" metric; clicks on graph nodes and table rows navigate, and Back returns.

Starts `streamlit run` on a free local port and drives headless Chromium (Playwright) with every non-local request
blocked. Skips when Playwright or its Chromium is not installed
(`pip install -e .[dev] && python -m playwright install chromium`).
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from supplier_graph.config import ROOT

pytestmark = pytest.mark.data
sync_api = pytest.importorskip("playwright.sync_api")

# (text typed into "Supplier name or UEI", text identifying the candidate to pick, its UEI)
SUPPLIERS = [("THE BOEING COMPANY", "(MESA, AZ)", "NJVNVWQJMPA4"),
             ("Sikorsky Aircraft Corporation", "SIKORSKY AIRCRAFT CORPORATION (STRATFORD, CT)", "UTJWTSLMFNG4")]

# evaluated on the graph's <div class="net"> (inside the component's shadow root); the vis.Network is el.__net
CANVAS_CHECK = """el => {
  const n = el.__net, c = el.querySelector('canvas');
  if (!n || !c) return {canvas: !!c, nodes: 0, ink: 0, ids: [], outside: []};
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  let ink = 0;
  for (let i = 0; i < d.length; i += 4) if (d[i + 3] > 0 && (d[i] < 245 || d[i + 1] < 245 || d[i + 2] < 245)) ink++;
  const ids = n.body.data.nodes.getIds(), pos = n.getPositions(ids);
  const outside = ids.filter(i => { const p = n.canvasToDOM(pos[i]);
    return p.x < 0 || p.y < 0 || p.x > el.clientWidth || p.y > el.clientHeight; });
  return {canvas: true, nodes: ids.length, ink: ink, ids: ids, outside: outside};
}"""
NODE_XY = """(el, id) => { const n = el.__net; const p = n.canvasToDOM(n.getPositions([id])[id]); return [p.x, p.y]; }"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def app_url():
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "ANTHROPIC_API_KEY": ""}
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "app" / "streamlit_app.py"), "--server.headless=true",
         f"--server.port={port}", "--server.address=127.0.0.1", "--browser.gatherUsageStats=false"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # ignore HTTP(S)_PROXY for localhost
    try:
        for _ in range(120):
            try:
                if direct.open(f"{url}/_stcore/health", timeout=1).read() == b"ok":
                    break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.fail("streamlit did not start")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=20)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # browser binaries not installed
            pytest.skip(f"Chromium for Playwright not available: {str(e).splitlines()[0]}")
        yield b
        b.close()


@pytest.fixture
def page(app_url, browser):
    """A fresh session on the app with non-local requests blocked; collects console errors and blocked URLs."""
    ctx = browser.new_context(viewport={"width": 1400, "height": 1100})
    pg = ctx.new_page()
    pg.errors, pg.outside = [], []
    pg.on("console", lambda m: m.type == "error" and pg.errors.append(m.text))
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))

    def route(r):
        if r.request.url.startswith(("http://127.0.0.1", "ws://127.0.0.1", "data:", "blob:", "about:")):
            r.continue_()
        else:
            pg.outside.append(r.request.url)
            r.abort()
    pg.route("**/*", route)
    pg.goto(app_url)
    pg.get_by_role("tab", name="Overview").wait_for(timeout=60_000)
    yield pg
    ctx.close()


def _graph(page, title_prefix: str, must_contain: str = ""):
    """Wait for exactly one drawn graph whose title starts with the prefix (and contains `must_contain`).
    Playwright's CSS locators reach into the component's shadow root."""
    sel = f'div.net[data-title^="{title_prefix}"]' + (f'[data-title*="{must_contain}"]' if must_contain else "")
    loc = page.locator(sel)
    deadline = time.time() + 60
    while time.time() < deadline:
        if loc.count() == 1 and loc.evaluate("el => !!el.__net"):
            break
        page.wait_for_timeout(250)
    else:
        raise AssertionError(f"no single drawn graph for {sel}; found {loc.count()}; errors: {page.errors}")
    last = None
    for _ in range(40):  # wait until the layout has stopped moving (positions equal across two polls)
        pos = loc.evaluate("el => JSON.stringify(el.__net.getPositions())")
        if pos == last:
            break
        last = pos
        page.wait_for_timeout(400)
    page.wait_for_timeout(500)  # the fit after stabilizing
    return loc


def _check(loc, page) -> dict:
    res = loc.evaluate(CANVAS_CHECK)
    title = loc.get_attribute("data-title")
    assert res["canvas"] and res["nodes"] > 1, f"no network drawn in {title!r}: {res}; errors: {page.errors}"
    assert res["ink"] > 1000, f"canvas is blank: {title}"
    assert not res["outside"], f"nodes drawn outside the visible frame in {title}: {res['outside']}"
    assert "country:USA" not in res["ids"]
    return res


def _metric(page, label: str) -> int:
    v = page.locator('[data-testid="stMetric"]').filter(has_text=label).first \
        .locator('[data-testid="stMetricValue"]').inner_text()
    return int(v.replace(",", ""))


def _pick_supplier(page, query: str, pick: str):
    box = page.get_by_label("Supplier name or UEI")
    box.fill(query)
    box.press("Enter")
    page.wait_for_function("""q => { const els = document.querySelectorAll('input[aria-label="Candidate"]');
                                     return els.length === 1 && els[0].value.toUpperCase().includes(q); }""",
                           arg=query.upper(), timeout=60_000)
    page.get_by_label("Candidate").click()
    page.get_by_role("option", name=pick).first.click()


@pytest.mark.parametrize(("query", "pick", "uei"), SUPPLIERS, ids=["boeing-mesa", "sikorsky"])
def test_graph_explorer_draws_both_views(page, query, pick, uei):
    page.get_by_role("tab", name="Graph explorer").click()
    _pick_supplier(page, query, pick)
    loc = _graph(page, "Network of", f"[{uei}]")
    res = _check(loc, page)
    offices = _metric(page, "Offices affected")
    assert sum(i.startswith("office:") for i in res["ids"]) == offices

    page.get_by_text("Show individual awards").click()  # the award-level view caps awards, never offices
    page.get_by_text(re.compile(r"of [\d,]+ awards? shown")).wait_for(timeout=60_000)
    aloc = _graph(page, "Network of", f"[{uei}]")
    ares = _check(aloc, page)
    assert any(i.startswith("award:") for i in ares["ids"])
    assert sum(i.startswith("office:") for i in ares["ids"]) == offices
    assert not page.errors, f"console errors: {page.errors}"
    assert not page.outside, f"requests outside localhost: {page.outside}"


def test_office_click_opens_office_view_and_back_returns(page):
    """Click an office node in the Graph explorer: the Office view opens for that office and draws its own graph;
    Back returns to the Graph explorer."""
    page.get_by_role("tab", name="Graph explorer").click()
    loc = _graph(page, "Network of", "[UTJWTSLMFNG4]")
    _check(loc, page)
    loc.scroll_into_view_if_needed()  # the graph sits below the fold; a click must land on screen
    x, y = loc.evaluate(NODE_XY, "office:N00019")
    bb = loc.bounding_box()
    assert loc.evaluate(f"el => el.__net.getNodeAt({{x: {x}, y: {y}}})") == "office:N00019"
    page.mouse.click(bb["x"] + x, bb["y"] + y)
    office = _graph(page, "Office network of NAVAIR (N00019)")
    _check(office, page)
    assert page.locator('input[aria-label="Office"]').input_value() == "NAVAIR (N00019)"
    page.get_by_role("button", name="Back to Graph explorer").click()
    _check(_graph(page, "Network of", "[UTJWTSLMFNG4]"), page)
    assert page.get_by_role("tab", name="Graph explorer", selected=True).count() == 1
    assert not page.errors, f"console errors: {page.errors}"
    assert not page.outside, f"requests outside localhost: {page.outside}"


def test_table_row_click_opens_graph_explorer(page):
    """Click the first row of the supplier risk table: that supplier opens in the Graph explorer, with Back."""
    page.get_by_role("tab", name="Supplier risk table").click()
    grid = page.locator('[data-testid="stDataFrame"]').first
    grid.wait_for(timeout=60_000)
    page.wait_for_timeout(1500)
    bb = grid.bounding_box()
    page.mouse.click(bb["x"] + 14, bb["y"] + 35 + 17)  # the first data row's selection cell
    _check(_graph(page, "Network of", "LOCKHEED MARTIN CORPORATION"), page)
    assert page.get_by_role("button", name="Back to Supplier risk table").count() == 1
    assert not page.errors, f"console errors: {page.errors}"
