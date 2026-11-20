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
MAX_PRIMES = 20
OFFICE_EDGE = "#7fbf7f"
HOME = nid("Country", "USA")  # never drawn: every supplier works there
# pyvis's defaults for a directed network, which the explorer used before
OPTIONS = {
    "edges": {"color": {"inherit": True}, "smooth": {"enabled": True, "type": "dynamic"}, "arrows": "to"},
    "interaction": {"dragNodes": True, "hideEdgesOnDrag": False, "hideNodesOnDrag": False},
    "physics": {"enabled": True, "stabilization": {"enabled": True, "fit": True, "iterations": 1000,
                                                    "onlyDynamicEdges": False, "updateInterval": 50}},
}

# the office view is a star: spread it out so labels don't collide
BLAST_OPTIONS = {**OPTIONS, "edges": {**OPTIONS["edges"], "smooth": {"enabled": False}},
                 "physics": {**OPTIONS["physics"], "barnesHut": {"gravitationalConstant": -12000, "centralGravity": 0.2,
                                                                 "springLength": 230, "avoidOverlap": 0.6}}}


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
    """Award-level view ("Show individual awards"): the supplier, its largest direct awards (up to 25) and prime
    awards it subcontracts on (up to 15), each award's office and foreign country, and the supplier's parent."""
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
            if v == HOME:
                continue
            if k in ("AWARDED_BY", "PERFORMED_IN") or (k == "AWARDED_TO" and not is_direct):
                add(v, 16)
                edges.append({"from": a, "to": v, "title": k})
    for _, v, k in G.out_edges(sn, keys=True):
        if k == "SUBSIDIARY_OF":
            add(v, 18)
            edges.append({"from": sn, "to": v, "title": "SUBSIDIARY_OF"})
    return list(nodes.values()), edges


def money(x: float) -> str:
    """$350K, $41M, $1.2B."""
    x = float(x or 0)
    if x >= 1e9:
        return f"${x / 1e9:,.1f}B"
    if x >= 1e6:
        return f"${x / 1e6:,.0f}M"
    if x >= 1e3:
        return f"${x / 1e3:,.0f}K"
    return f"${x:,.0f}"


def short(name: str | None, n: int = 24) -> str:
    name = " ".join((name or "").split())
    return name if len(name) <= n else name[: n - 1].rstrip() + "…"


def office_label(name: str | None, code: str, amount: float) -> str:
    """Short office name + office code (always: several offices share a name) + dollars at stake."""
    return f"{short(name)} ({code}) · {money(amount)}"


def blast_view(G: nx.MultiDiGraph, supplier_id: str, office_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Default view: the supplier in the centre with one edge per affected office (`office_rows` are the
    blast_radius query's rows, so the graph and the "Offices affected" metric always agree), edge width scaled by
    obligations; its parent (purple), the prime contractors it subcontracts for (red, dashed) and the foreign
    countries where its work is performed (orange). No award nodes."""
    sn = nid("Supplier", supplier_id)
    s = G.nodes[sn]
    nodes: list[dict] = [{"id": sn, "label": short(s["name"], 30), "title": f"Supplier: {s['name']} ({supplier_id})",
                          "color": COLORS["Supplier"], "size": 30, "shape": "dot", "font": {"size": 16}}]
    edges: list[dict] = []

    # awards behind each office: direct prime awards, plus prime awards the supplier subcontracts on
    direct = {u for u, _, k in G.in_edges(sn, keys=True) if k == "AWARDED_TO"}
    sub_in = [d for _, _, k, d in G.in_edges(sn, keys=True, data=True) if d.get("type") == "SUBCONTRACTS_TO"]
    indirect = {nid("Award", d["prime_award_id"]) for d in sub_in} - direct
    by_office: dict[str, list[tuple[float, str, bool]]] = {}
    for a in direct | indirect:
        d = G.nodes[a]
        if d.get("office_code"):
            by_office.setdefault(d["office_code"], []).append((d["obl_pos"] or 0.0, d["award_id"], a in direct))

    top = max((r["obligations"] for r in office_rows), default=0.0) or 1.0
    for r in office_rows:
        code = r["office_code"]
        aw = sorted(by_office.get(code, []), key=lambda t: (-t[0], t[1]))
        n_dir = sum(t[2] for t in aw)
        ids = ", ".join(t[1] for t in aw[:3])
        tip = (f"{r['office_name']} ({code})\n{r['n_awards']:,} award{'s' if r['n_awards'] != 1 else ''} "
               f"({n_dir:,} direct, {r['n_awards'] - n_dir:,} via subcontracts), {money(r['obligations'])}\n"
               f"Top awards: {ids}")
        on = nid("Office", code)
        nodes.append({"id": on, "label": office_label(r["office_name"], code, r["obligations"]), "title": tip,
                      "color": COLORS["Office"], "size": 14, "shape": "dot"})
        edges.append({"from": sn, "to": on, "title": tip, "dashes": n_dir == 0, "color": {"color": OFFICE_EDGE},
                      "width": 1 + 11 * (r["obligations"] / top) ** 0.5})

    primes: dict[str, float] = {}
    for d in sub_in:
        for _, v, k in G.out_edges(nid("Award", d["prime_award_id"]), keys=True):
            if k == "AWARDED_TO":
                primes[v] = primes.get(v, 0.0) + max(d.get("amount") or 0.0, 0.0)
    for v, amt in sorted(primes.items(), key=lambda t: (-t[1], t[0]))[:MAX_PRIMES]:
        name = G.nodes[v]["name"]
        nodes.append({"id": v, "label": f"{short(name)} · {money(amt)}", "color": COLORS["Supplier"], "size": 16,
                      "shape": "dot", "title": f"Prime contractor: {name}\nsubcontracts to this supplier: {money(amt)}"})
        edges.append({"from": v, "to": sn, "dashes": True, "color": {"color": COLORS["Supplier"]},
                      "title": f"{name} subcontracts {money(amt)} to this supplier"})

    for _, v, k in G.out_edges(sn, keys=True):
        if k == "SUBSIDIARY_OF":
            pname = G.nodes[v]["name"]
            nodes.append({"id": v, "label": short(pname, 30), "title": f"Parent company: {pname}",
                          "color": COLORS["ParentCompany"], "size": 18, "shape": "dot"})
            edges.append({"from": sn, "to": v, "title": "subsidiary of"})

    countries: dict[str, float] = {}
    for a in direct:
        d = G.nodes[a]
        if d.get("pop_country") and d["pop_country"] != "USA":
            countries[d["pop_country"]] = countries.get(d["pop_country"], 0.0) + (d["obl_pos"] or 0.0)
    for d in sub_in:
        if d.get("pop_country") and d["pop_country"] != "USA":
            countries[d["pop_country"]] = countries.get(d["pop_country"], 0.0) + max(d.get("amount") or 0.0, 0.0)
    for iso, amt in sorted(countries.items()):
        cn = nid("Country", iso)
        cname = G.nodes[cn]["name"] if cn in G else iso
        nodes.append({"id": cn, "label": f"{cname} · {money(amt)}", "title": f"Work performed in {cname} ({iso})",
                      "color": COLORS["Country"], "size": 14, "shape": "dot"})
        edges.append({"from": sn, "to": cn, "title": f"work performed in {cname}"})
    return nodes, edges


def program_names(G: nx.MultiDiGraph, supplier_id: str) -> tuple[list[str], int]:
    """Names of the DoD acquisition programs on the supplier's direct and subcontracted prime awards, and how many
    program codes have no name in the data."""
    sn = nid("Supplier", supplier_id)
    awards = {u for u, _, k in G.in_edges(sn, keys=True) if k == "AWARDED_TO"} | \
        {nid("Award", d["prime_award_id"]) for _, _, k, d in G.in_edges(sn, keys=True, data=True)
         if d.get("type") == "SUBCONTRACTS_TO"}
    named: dict[str, str] = {}
    unnamed: set[str] = set()
    for a in awards:
        d = G.nodes[a]
        if d.get("program_code"):
            if d.get("program_name"):
                named[d["program_code"]] = d["program_name"]
            else:
                unnamed.add(d["program_code"])
    return sorted(set(named.values())), len(unnamed - set(named))


def _js_json(x) -> str:
    """JSON safe to embed in a <script> block."""
    return json.dumps(x).replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def network_html(nodes: list[dict], edges: list[dict], height_px: int = HEIGHT_PX, options: dict | None = None) -> str:
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
const tip = x => {{ if (typeof x.title !== "string") return x; const d = document.createElement("div");
  d.style.whiteSpace = "pre"; d.textContent = x.title; return Object.assign({{}}, x, {{title: d}}); }};
const box = document.getElementById("net");
window.network = new vis.Network(box,
  {{nodes: new vis.DataSet({_js_json(nodes)}.map(tip)), edges: new vis.DataSet({_js_json(edges)}.map(tip))}},
  {_js_json(options or OPTIONS)});
// The app can create this frame inside a hidden tab (zero size); fit again once it has a real size and after the
// layout settles, so the whole network is always in view.
const fit = () => {{ if (box.clientWidth > 0 && box.clientHeight > 0) {{ window.network.redraw(); window.network.fit(); }} }};
window.network.once("stabilizationIterationsDone", fit);
new ResizeObserver(fit).observe(box);
</script>
</body></html>
"""
