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
import re
import textwrap
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
    "nodes": {"font": {"multi": "html", "size": 17, "background": "rgba(255,255,255,0.9)",
                       "bold": {"size": 21, "mod": "bold"}}},
    "edges": {"color": {"inherit": True}, "smooth": {"enabled": True, "type": "dynamic"},
              "arrows": {"to": {"enabled": False}, "from": {"enabled": False}}},
    "interaction": {"dragNodes": True, "hideEdgesOnDrag": False, "hideNodesOnDrag": False},
    "physics": {"enabled": True, "stabilization": {"enabled": True, "fit": True, "iterations": 1000,
                                                    "onlyDynamicEdges": False, "updateInterval": 50}},
}

# the office view is a star: spread it out so labels don't collide
BLAST_OPTIONS = {**OPTIONS, "edges": {**OPTIONS["edges"], "smooth": {"enabled": False}},
                 "physics": {**OPTIONS["physics"], "barnesHut": {"gravitationalConstant": -12000, "centralGravity": 0.2,
                                                                 "springLength": 230, "avoidOverlap": 0.6}}}

# the award view: dozens of small award nodes around the supplier; push them apart and keep them off its label
AWARD_OPTIONS = {**OPTIONS, "physics": {**OPTIONS["physics"], "barnesHut": {
    "gravitationalConstant": -9000, "centralGravity": 0.25, "springLength": 170, "avoidOverlap": 0.5}}}
CENTER_FONT = {"size": 19, "background": "rgba(255,255,255,0.95)", "vadjust": 8}  # label below, on white


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


def neighbourhood(G: nx.MultiDiGraph, supplier_id: str, office_rows: list[dict]
                  ) -> tuple[list[dict], list[dict], int, int]:
    """Award-level view ("Show individual awards"): the supplier, its largest direct awards (up to 25) and prime
    awards it subcontracts on (up to 15), each drawn award's foreign country, the supplier's parent, and every
    affected office (`office_rows`, the blast_radius rows) even when none of its awards is drawn. Award IDs are in
    the hover text, not on the canvas. Returns (nodes, edges, awards shown, awards in total)."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add(n: str, size: int = 12) -> None:
        if n in nodes:
            return
        d = G.nodes[n]
        title = d.get("name") or d.get("award_id") or n
        if d["label"] == "Award":
            label = ""  # shown on hover
        elif d["label"] == "Office":
            label = wrapped(f"{office_short(d.get('name'), d['office_code'])} ({d['office_code']})")
        else:
            label = wrapped(d.get("name") or n)
        nodes[n] = {"id": n, "label": label, "title": f"{d['label']}: {title}", "color": COLORS[d["label"]],
                    "size": size, "shape": "dot"}

    sn = nid("Supplier", supplier_id)
    add(sn, 28)
    nodes[sn].update({"mass": 5, "font": CENTER_FONT})
    direct = [u for u, _, k in G.in_edges(sn, keys=True) if k == "AWARDED_TO"]
    sub_awards = sorted({nid("Award", d["prime_award_id"]) for _, _, k, d in G.in_edges(sn, keys=True, data=True)
                         if d.get("type") == "SUBCONTRACTS_TO"} - set(direct))
    shown = sorted(direct, key=lambda x: (-G.nodes[x]["obl_pos"], x))[:MAX_DIRECT] + sub_awards[:MAX_SUB]
    reached: set[str] = set()
    for a in shown:
        add(a, 10)
        is_direct = a in direct
        edges.append({"from": a, "to": sn, "title": "awarded to the supplier" if is_direct else
                      "prime award (the supplier subcontracts on it)", "dashes": not is_direct})
        for _, v, k in G.out_edges(a, keys=True):
            if v == HOME:
                continue
            if k in ("AWARDED_BY", "PERFORMED_IN") or (k == "AWARDED_TO" and not is_direct):
                add(v, 16)
                edges.append({"from": a, "to": v, "title": k.replace("_", " ").lower()})
                if k == "AWARDED_BY":
                    reached.add(v)
    for r in office_rows:  # offices whose awards are all hidden still appear, linked straight to the supplier
        on = nid("Office", r["office_code"])
        if on not in reached:
            add(on, 16)
            edges.append({"from": sn, "to": on, "dashes": [2, 6], "color": {"color": OFFICE_EDGE}, "width": 2,
                          "title": f"{plural(r['n_awards'], 'award')} at this office not drawn"})
    for _, v, k in G.out_edges(sn, keys=True):
        if k == "SUBSIDIARY_OF":
            add(v, 18)
            edges.append({"from": sn, "to": v, "title": "subsidiary of"})
    return list(nodes.values()), edges, len(shown), len(direct) + len(sub_awards)


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def money(x: float) -> str:
    """$350K, $7.7M, $41M, $1.2B."""
    x = float(x or 0)
    if x >= 1e9:
        return f"${x / 1e9:,.1f}B"
    if x >= 1e7:
        return f"${x / 1e6:,.0f}M"
    if x >= 1e6:
        return f"${x / 1e6:,.1f}M"
    if x >= 1e3:
        return f"${x / 1e3:,.0f}K"
    return f"${x:,.0f}"


# Readable names for common offices (USAspending's office names are abbreviated and upper case). Full names and
# codes stay in the hover text.
OFFICE_SHORT = {
    "NAVAL AIR SYSTEMS COMMAND": "NAVAIR",
    "NAVAL AIR WARFARE CENTER AIR DIV": "NAWC Aircraft Division",
    "NAWC TRAINING SYSTEMS DIV": "NAWC Training Systems",
    "NAVSEA HQ": "NAVSEA HQ",
    "SUP OF SHIPBUILDING CONV AND REPAIR": "SUPSHIP",
    "MID ATLANTIC REG MAINT CTR": "Mid-Atlantic Regional Maintenance Center",
    "SOUTHWEST REGIONAL MAINT CENTER": "Southwest Regional Maintenance Center",
    "PUGET SOUND NAVAL SHIPYARD IMF": "Puget Sound Naval Shipyard",
    "PEARL HARBOR NAVAL SHIPYARD IMF": "Pearl Harbor Naval Shipyard",
    "FLEET READINESS CENTER": "Fleet Readiness Center",
    "MSCHQ NORFOLK": "Military Sealift Command HQ",
    "MISSILE DEFENSE AGENCY (MDA)": "Missile Defense Agency",
    "PAE STRATEGIC SYSTEMS PROGRAMS": "Strategic Systems Programs",
    "W4GG HQ US ARMY TACOM": "Army TACOM",
    "DLA LAND AT ABERDEEN": "DLA Land Aberdeen",
    "DCMA VERTICAL LIFT STRATFORD": "DCMA Stratford",
    "DCMA FIXED WING FT WORTH": "DCMA Fort Worth",
    "DCMA FIXED WING ST LOUIS": "DCMA St. Louis",
    "DCMA FIXED WING PUGET SOUND": "DCMA Puget Sound",
    "NAVSUP FLT LOG CTR YOKOSUKA": "NAVSUP FLC Yokosuka",
    "NAVSUP FLT LOG CTR PUGET SOUND": "NAVSUP FLC Puget Sound",
}
PREFIX_CODE = re.compile(r"^[A-Z]{1,2}\d[A-Z0-9]{2,4}$")  # leading office/unit codes such as FA8124 or W6QK
WRAP = 24


def office_short(name: str | None, code: str) -> str:
    """A readable office name: the abbreviation map, else the name without a leading office/unit code."""
    name = " ".join((name or "").split())
    if name in OFFICE_SHORT:
        return OFFICE_SHORT[name]
    words = name.split()
    if len(words) > 1 and (words[0] == code or PREFIX_CODE.match(words[0])):
        words = words[1:]
    return " ".join(words) or code


def esc(text: str) -> str:
    """Keep names from being read as vis-network html-label tags (it only knows <b>, <i>, <code>)."""
    return text.replace("<", "‹").replace(">", "›")


def wrapped(name: str | None, width: int = WRAP) -> str:
    """Wrap a name onto several lines instead of cutting it."""
    return "\n".join(esc(x) for x in textwrap.wrap(" ".join((name or "").split()), width)) or ""


def two_line(name: str, amount: float | None = None) -> str:
    """Name (wrapped), then the dollar amount in bold and larger."""
    return wrapped(name) + (f"\n<b>{money(amount)}</b>" if amount is not None else "")


def office_label(name: str | None, code: str, amount: float) -> str:
    """Short office name + office code (several offices share a name), then the dollars at stake."""
    return two_line(f"{office_short(name, code)} ({code})", amount)


def office_money(G: nx.MultiDiGraph, supplier_id: str) -> dict[str, dict]:
    """Per office: the supplier's own prime obligations there (direct), its own subaward amounts on other companies'
    prime contracts there (own_sub), the obligations on those prime contracts (prime_via) and their prime
    contractors, award counts and the top 3 award IDs."""
    sn = nid("Supplier", supplier_id)
    out: dict[str, dict] = {}

    def at(code: str) -> dict:
        return out.setdefault(code, {"direct": 0.0, "n_direct": 0, "own_sub": 0.0, "prime_via": 0.0,
                                     "_ind": set(), "_primes": set(), "_ids": []})
    direct = {u for u, _, k in G.in_edges(sn, keys=True) if k == "AWARDED_TO"}
    for a in direct:
        d = G.nodes[a]
        if d.get("office_code"):
            o = at(d["office_code"])
            o["direct"] += d["obl_pos"] or 0.0
            o["n_direct"] += 1
            o["_ids"].append((d["obl_pos"] or 0.0, d["award_id"]))
    for _, _, _k, e in G.in_edges(sn, keys=True, data=True):
        if e.get("type") != "SUBCONTRACTS_TO":
            continue
        an = nid("Award", e["prime_award_id"])
        d = G.nodes[an]
        if not d.get("office_code") or an in direct:
            continue
        o = at(d["office_code"])
        o["own_sub"] += max(e.get("amount") or 0.0, 0.0)
        if an not in o["_ind"]:
            o["_ind"].add(an)
            o["prime_via"] += d["obl_pos"] or 0.0
            o["_ids"].append((d["obl_pos"] or 0.0, d["award_id"]))
            o["_primes"] |= {G.nodes[v]["name"] for _, v, kk in G.out_edges(an, keys=True) if kk == "AWARDED_TO"}
    for o in out.values():
        o["n_indirect"] = len(o.pop("_ind"))
        o["primes"] = sorted(o.pop("_primes"))
        o["top_ids"] = [i for _, i in sorted(o.pop("_ids"), key=lambda t: (-t[0], t[1]))[:3]]
    return out


def blast_view(G: nx.MultiDiGraph, supplier_id: str, office_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Default view: the supplier in the centre with one edge per affected office (`office_rows` are the
    blast_radius query's rows, so the graph and the "Offices affected" metric always agree). A solid edge is labeled
    with the supplier's own prime obligations at that office; a dashed edge (reached only through subcontracts) with
    the supplier's own subaward amount, the prime contract value being in the hover. Edge width follows that amount.
    Also its parent (purple), the prime contractors it subcontracts for (red, dashed) and the foreign countries where
    its work is performed (orange). No award nodes."""
    sn = nid("Supplier", supplier_id)
    s = G.nodes[sn]
    edges: list[dict] = []

    direct = {u for u, _, k in G.in_edges(sn, keys=True) if k == "AWARDED_TO"}
    sub_in = [d for _, _, k, d in G.in_edges(sn, keys=True, data=True) if d.get("type") == "SUBCONTRACTS_TO"]
    money_at = office_money(G, supplier_id)
    first = s["name"].split()[0].title() if s["name"] else "the supplier"
    owner = f"{first}'s" if not first.endswith("s") else f"{first}'"

    prime = sum(G.nodes[a]["obl_pos"] or 0.0 for a in direct)
    nodes: list[dict] = [{"id": sn, "label": two_line(s["name"], prime), "color": COLORS["Supplier"], "size": 30,
                          "shape": "dot", "title": f"Supplier: {s['name']} ({supplier_id})\nprime obligations "
                                                   f"{money(prime)}", "font": {"size": 19}}]
    amounts = {r["office_code"]: (money_at[r["office_code"]]["direct"] or money_at[r["office_code"]]["own_sub"])
               for r in office_rows}
    top = max(amounts.values(), default=0.0) or 1.0
    for r in office_rows:
        code = r["office_code"]
        o = money_at[code]
        solid = o["n_direct"] > 0
        amt = amounts[code]
        lines = [f"{r['office_name']} ({code})",
                 f"{plural(r['n_awards'], 'contract')} ({o['n_direct']:,} direct, {o['n_indirect']:,} via "
                 f"subcontracts)"]
        if solid:
            lines.append(f"{owner} prime contracts here: {money(o['direct'])}")
        if o["n_indirect"] or o["own_sub"]:
            via = ", ".join(o["primes"]) or "other primes"
            lines.append(f"{money(o['prime_via'])} prime contract{'s' if o['n_indirect'] != 1 else ''} via {via}; "
                         f"{owner} subcontracts: {money(o['own_sub'])}")
        lines.append("Top awards: " + ", ".join(o["top_ids"]))
        tip = "\n".join(lines)
        on = nid("Office", code)
        nodes.append({"id": on, "label": office_label(r["office_name"], code, amt), "title": tip,
                      "color": COLORS["Office"], "size": 14, "shape": "dot"})
        edges.append({"from": sn, "to": on, "title": tip, "dashes": not solid, "color": {"color": OFFICE_EDGE},
                      "width": 1 + 11 * (amt / top) ** 0.5, "amount": round(amt, 2),
                      "kind": "direct" if solid else "subcontract"})

    primes: dict[str, float] = {}
    for d in sub_in:
        for _, v, k in G.out_edges(nid("Award", d["prime_award_id"]), keys=True):
            if k == "AWARDED_TO":
                primes[v] = primes.get(v, 0.0) + max(d.get("amount") or 0.0, 0.0)
    for v, amt in sorted(primes.items(), key=lambda t: (-t[1], t[0]))[:MAX_PRIMES]:
        name = G.nodes[v]["name"]
        nodes.append({"id": v, "label": two_line(name, amt), "color": COLORS["Supplier"], "size": 16,
                      "shape": "dot", "title": f"Prime contractor: {name}\nsubcontracts to this supplier: {money(amt)}"})
        edges.append({"from": v, "to": sn, "dashes": True, "color": {"color": COLORS["Supplier"]},
                      "title": f"{name} subcontracts {money(amt)} to this supplier"})

    for _, v, k in G.out_edges(sn, keys=True):
        if k == "SUBSIDIARY_OF":
            pname = G.nodes[v]["name"]
            nodes.append({"id": v, "label": two_line(pname), "title": f"Parent company: {pname}",
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
        nodes.append({"id": cn, "label": two_line(cname, amt), "title": f"Work performed in {cname} ({iso})",
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


LEGEND = [("dot", COLORS["Office"], "Office that would be affected"),
          ("dot", COLORS["Supplier"], "Prime contractor it subcontracts for"),
          ("dot", COLORS["ParentCompany"], "Parent company"),
          ("dot", COLORS["Country"], "Foreign country"),
          ("thick", OFFICE_EDGE, "More money"),
          ("dashed", OFFICE_EDGE, "Only through subcontracts")]
# award view: edge widths don't scale with money there, so "More money" is left out
AWARD_LEGEND = [("dot", COLORS["Award"], "Award"), *LEGEND[:4],
                ("dashed", COLORS["Award"], "Award it only subcontracts on"),
                ("dotted", OFFICE_EDGE, "Office with no awards drawn")]
LINE_STYLE = {"thick": "6px solid", "dashed": "2px dashed", "dotted": "3px dotted"}


def legend_html(entries: list[tuple[str, str, str]] | None = None) -> str:
    """One-row legend (small dots and line samples), shown by the app above the graph; the office view's by
    default, AWARD_LEGEND for the award view."""
    def mark(kind: str, color: str) -> str:  # styled spans: st.html strips inline SVG
        if kind == "dot":
            style = f"width:11px;height:11px;border-radius:50%;background:{color}"
        else:
            style = f"width:28px;height:0;border-top:{LINE_STYLE[kind]} {color}"
        return f'<span aria-hidden="true" style="display:inline-block;{style}"></span>'
    items = "".join(f'<span style="display:inline-flex;align-items:center;gap:6px;white-space:nowrap">'
                    f'{mark(k, c)}{label}</span>' for k, c, label in (entries or LEGEND))
    return (f'<div role="list" aria-label="Graph legend" style="display:flex;flex-wrap:wrap;gap:6px 18px;'
            f'font-size:0.875rem;align-items:center">{items}</div>')


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
