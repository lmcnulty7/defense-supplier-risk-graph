"""Graph explorer in a real browser: the network is drawn, offline, with no console errors.

Starts `streamlit run` on a free local port, drives headless Chromium (Playwright) with every non-local request
blocked, and checks the drawn canvas for two suppliers. Skips when Playwright or its Chromium is not installed
(`pip install -e .[dev] && python -m playwright install chromium`).
"""

from __future__ import annotations

import os
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

CANVAS_INK = """() => {
  const c = document.querySelector('canvas');
  if (!c || !window.network) return {canvas: !!c, nodes: 0, ink: 0};
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  let ink = 0;
  for (let i = 0; i < d.length; i += 4) if (d[i + 3] > 0 && (d[i] < 245 || d[i + 1] < 245 || d[i + 2] < 245)) ink++;
  return {canvas: true, nodes: window.network.body.data.nodes.length, ink: ink, w: c.width, h: c.height};
}"""


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
    try:
        for _ in range(120):
            try:
                if urllib.request.urlopen(f"{url}/_stcore/health", timeout=1).read() == b"ok":
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


@pytest.mark.parametrize(("query", "pick", "uei"), SUPPLIERS, ids=["boeing-mesa", "sikorsky"])
def test_graph_explorer_draws_network_offline(app_url, browser, query, pick, uei):
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    errors: list[str] = []
    outside: list[str] = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))

    def route(r):
        if r.request.url.startswith(("http://127.0.0.1", "ws://127.0.0.1", "data:", "blob:", "about:")):
            r.continue_()
        else:
            outside.append(r.request.url)
            r.abort()
    page.route("**/*", route)

    page.goto(app_url)
    page.get_by_role("tab", name="Graph explorer").click()
    box = page.get_by_label("Supplier name or UEI")
    box.fill(query)
    box.press("Enter")
    # wait for the rerun to settle: one "Candidate" selectbox (the old one lingers briefly), showing the typed name
    page.wait_for_function("""q => { const els = document.querySelectorAll('input[aria-label="Candidate"]');
                                     return els.length === 1 && els[0].value.toUpperCase().includes(q); }""",
                           arg=query.upper(), timeout=60_000)
    page.get_by_label("Candidate").click()
    page.get_by_role("option", name=pick).first.click()

    frame_el = page.locator(f'iframe[title^="Network of"][title*="[{uei}]"]')
    sync_api.expect(frame_el).to_be_visible(timeout=60_000)
    frame = frame_el.element_handle().content_frame()
    try:
        frame.wait_for_function("() => window.network && document.querySelector('canvas')", timeout=20_000)
        page.wait_for_timeout(3000)  # let physics stabilise
    except sync_api.TimeoutError:
        pass  # reported by the assertions below, with the console errors
    res = frame.evaluate(CANVAS_INK)
    title = frame_el.get_attribute("title")
    page.close()

    assert res["canvas"] and res["nodes"] > 1, f"no network drawn in {title!r}: {res}; console errors: {errors}"
    assert res["ink"] > 1000, f"canvas is blank: {res}"
    assert not errors, f"console errors: {errors}"
    assert not outside, f"requests outside localhost: {outside}"
