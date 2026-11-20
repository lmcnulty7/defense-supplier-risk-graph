"""Clickable network graph for the app: an inline Streamlit custom component (v2) around vis-network.

vis-network's JS and CSS are the copies bundled with pyvis, inlined (no CDN, no network request). Unlike an iframe,
the component can report clicks back to Python: clicking an office or supplier node returns its id
("office:N00019", "supplier:UTJWTSLMFNG4") as a one-rerun trigger, which the app uses to navigate.
"""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from supplier_graph.graph_view import HEIGHT_PX, OPTIONS, vis_assets

_JS, _CSS = vis_assets()

HTML = """<div class="net" role="img"></div>"""

CSS = _CSS + """
.net { width: 100%; background: #ffffff; border-radius: 0.5rem; }
div.vis-tooltip { white-space: pre; font-family: sans-serif; font-size: 13px; }
"""

# The vis UMD bundle defines globalThis.vis when loaded as a module; the default export then draws the network
# and re-draws only when the data changes.
JS = _JS + """

const CLICKABLE = ["office:", "supplier:"];

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const box = parentElement.querySelector(".net");
  if (!box || !data) return;
  box.style.height = (data.height || 560) + "px";
  box.setAttribute("aria-label", data.title || "network graph");
  box.dataset.title = data.title || "";
  const tip = (x) => {
    if (typeof x.title !== "string") return x;
    const d = document.createElement("div");
    d.textContent = x.title;
    return Object.assign({}, x, { title: d });
  };
  const key = JSON.stringify([data.nodes, data.edges, data.options]);
  if (box.__key === key && box.__net) return;
  box.__key = key;
  if (box.__net) { const old = box.__net; box.__net = null; old.destroy(); }
  const net = new vis.Network(box, {
    nodes: new vis.DataSet(data.nodes.map(tip)),
    edges: new vis.DataSet(data.edges.map(tip)),
  }, data.options);
  box.__net = net;
  // always fit the network currently shown (an older one may have been destroyed when the data changed)
  const fit = () => {
    const n = box.__net;
    if (n && box.clientWidth > 0 && box.clientHeight > 0) { n.redraw(); n.fit(); }
  };
  net.once("stabilizationIterationsDone", fit);
  if (!box.__ro) { box.__ro = new ResizeObserver(fit); box.__ro.observe(box); }
  net.on("click", (p) => {
    const id = p.nodes && p.nodes[0];
    if (id && CLICKABLE.some((k) => id.startsWith(k))) setTriggerValue("clicked", id);
  });
  net.on("hoverNode", (p) => {
    box.style.cursor = CLICKABLE.some((k) => String(p.node).startsWith(k)) ? "pointer" : "default";
  });
  net.on("blurNode", () => { box.style.cursor = "default"; });
}
"""

_COMPONENT = st.components.v2.component("supplier_graph.network", html=HTML, css=CSS, js=JS)


def network(nodes: list[dict], edges: list[dict], *, key: str, title: str, options: dict | None = None,
            height: int = HEIGHT_PX, on_click: Callable[[], None] | None = None):
    """Mount the graph. `title` names it for assistive technology and tests. Returns the component result; its
    `clicked` attribute is the clicked node id for one rerun. `on_click` runs before the script reruns (read the id
    from `st.session_state[key].clicked`)."""
    opts = dict(options or OPTIONS)
    opts["interaction"] = {**opts.get("interaction", {}), "hover": True, "tooltipDelay": 120}
    return _COMPONENT(data={"nodes": nodes, "edges": edges, "options": opts, "height": height, "title": title},
                      key=key, height=height, on_clicked_change=on_click or (lambda: None))
