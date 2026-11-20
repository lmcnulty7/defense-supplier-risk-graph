"""Graph explorer view: a supplier's neighbourhood drawn with vis-network, fully offline.

The page is written here rather than by pyvis. pyvis's `in_line` template still links Bootstrap from a CDN, and the
earlier workaround (rebuilding the page with `str.splitlines()` to drop those lines) corrupted the inlined
vis-network.min.js: `splitlines()` also splits on U+0085 and other Unicode separators that appear inside the
library's string literals, so the script failed with "Invalid or unexpected token" and nothing was drawn.

The vis-network JS and CSS are the copies bundled with the pyvis package (a project dependency), inlined verbatim.
The page makes no network request.
"""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files

import networkx as nx

from supplier_graph.graph.schema import nid

HEIGHT_PX = 560
COLORS = {"Supplier": "#d62728", "Award": "#9ecae1", "Office": "#2ca02c", "Country": "#ff7f0e",
          "ParentCompany": "#9467bd", "ProductServiceCode": "#8c564b"}
MAX_DIRECT, MAX_SUB = 25, 15
# pyvis's defaults for a directed network, which the explorer used before
OPTIONS = {
    "edges": {"color": {"inherit": True}, "smooth": {"enabled": True, "type": "dynamic"}, "arrows": "to"},
    "interaction": {"dragNodes": True, "hideEdgesOnDrag": False, "hideNodesOnDrag": False},
    "physics": {"enabled": True, "stabilization": {"enabled": True, "fit": True, "iterations": 1000,
                                                    "onlyDynamicEdges": False, "updateInterval": 50}},
}


@cache
def vis_assets() -> tuple[str, str]:
    """(vis-network.min.js, vis-network.css) as bundled with pyvis; the newest bundled version wins."""
    lib = files("pyvis") / "templates" / "lib"
    dirs = sorted((d for d in lib.iterdir() if d.name.startswith("vis-")),
                  key=lambda d: tuple(int(x) for x in d.name[4:].split(".")))
    if not dirs:
        raise FileNotFoundError(f"no bundled vis-network under {lib}; reinstall pyvis")
    d = dirs[-1]
    return (d / "vis-network.min.js").read_text(encoding="utf-8"), (d / "vis-network.css").read_text(encoding="utf-8")


def neighbourhood(G: nx.MultiDiGraph, supplier_id: str) -> tuple[list[dict], list[dict]]:
    """vis-network nodes and edges: the supplier, its largest direct awards (up to 25) and prime awards it
    subcontracts on (up to 15), each award's office and country, and the supplier's parent."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add(n: str, size: int = 12) -> None:
        if n in nodes:
            return
        d = G.nodes[n]
        title = d.get("name") or d.get("award_id") or n
        nodes[n] = {"id": n, "label": (d.get("name") or d.get("piid") or n)[:28], "title": f"{d['label']}: {title}",
                    "color": COLORS[d["label"]], "size": size, "shape": "dot"}

    sn = nid("Supplier", supplier_id)
    add(sn, 28)
    direct = [u for u, _, k in G.in_edges(sn, keys=True) if k == "AWARDED_TO"]
    sub_awards = sorted({nid("Award", d["prime_award_id"]) for _, _, k, d in G.in_edges(sn, keys=True, data=True)
                         if d.get("type") == "SUBCONTRACTS_TO"})
    for a in sorted(direct, key=lambda x: -G.nodes[x]["obl_pos"])[:MAX_DIRECT] + sub_awards[:MAX_SUB]:
        add(a, 10)
        is_direct = a in direct
        edges.append({"from": a, "to": sn, "title": "AWARDED_TO" if is_direct else
                      "prime award (supplier subcontracts)", "dashes": not is_direct})
        for _, v, k in G.out_edges(a, keys=True):
            if k in ("AWARDED_BY", "PERFORMED_IN") or (k == "AWARDED_TO" and not is_direct):
                add(v, 16)
                edges.append({"from": a, "to": v, "title": k})
    for _, v, k in G.out_edges(sn, keys=True):
        if k == "SUBSIDIARY_OF":
            add(v, 18)
            edges.append({"from": sn, "to": v, "title": "SUBSIDIARY_OF"})
    return list(nodes.values()), edges


def _js_json(x) -> str:
    """JSON safe to embed in a <script> block."""
    return json.dumps(x).replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def network_html(nodes: list[dict], edges: list[dict], height_px: int = HEIGHT_PX) -> str:
    """A self-contained page that draws the network; `window.network` is the vis.Network (used by tests)."""
    js, css = vis_assets()
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<style>{css}</style>
<style>html, body {{ margin: 0; background: #ffffff; }} #net {{ width: 100%; height: {height_px}px; }}</style>
<script>{js}</script>
</head><body>
<div id="net"></div>
<script>
window.network = new vis.Network(document.getElementById("net"),
  {{nodes: new vis.DataSet({_js_json(nodes)}), edges: new vis.DataSet({_js_json(edges)})}}, {_js_json(OPTIONS)});
</script>
</body></html>
"""
