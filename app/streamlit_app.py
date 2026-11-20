"""Streamlit demo: supplier risk table, graph explorer (blast radius), agent chat with citations.

Run: `make app` (needs `make data graph` first). The agent tab calls the Anthropic API and records spend
in the project ledger; it refuses to run once the budget guard trips.
"""

from __future__ import annotations

import json
import re

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from supplier_graph.agent import costs
from supplier_graph.agent.tools import resolve_supplier
from supplier_graph.explain import BACKTEST_URL, MODEL_CARD_URL, likelihood_md, models_md
from supplier_graph.graph import queries as Q
from supplier_graph.graph.build import load_graph
from supplier_graph.graph.schema import nid
from supplier_graph.headline import load as load_headline
from supplier_graph.ingest.risk_feed import feed_in_likelihood, usage_text
from supplier_graph.outlook_chart import filter_outlook, outlook_chart
from supplier_graph.stage import connect

st.set_page_config(page_title="Supplier Risk Graph", layout="wide")


@st.cache_resource
def graph():
    return load_graph()


@st.cache_data
def sql(q: str, params: tuple = ()) -> pd.DataFrame:
    con = connect(read_only=True)
    try:
        return con.execute(q, list(params)).df()
    finally:
        con.close()


@st.cache_data
def meta() -> dict:
    return json.loads(sql("SELECT v FROM stage_meta WHERE k='stage'").iloc[0, 0])


m = meta()
feed = m["risk_feed"]
st.title("Defense supplier-risk graph")
st.caption(
    "Portfolio project on **public** USAspending data (DoD prime contracts and subawards, PSC J maintenance/repair, "
    "FY2023-FY2025). The customer persona is **fictional**. Not used by, endorsed by, or built for the DoD or any "
    f"company. Data pulled {m['usaspending']['pulled_at_utc'][:10]}. Risk feed: "
    f"{'**SYNTHETIC FIXTURE**' if feed['is_fixture'] else 'MENA feed snapshot'} ({feed['note']})."
)
SKILL = feed.get("skill_status", "unknown")
FEED_USED = feed_in_likelihood()


@st.cache_data
def headline() -> dict[str, str]:
    try:
        return load_headline()
    except (OSError, ValueError):
        return {}


HL = headline()
STALE_HEADLINE = "Run `make headline` to load the model summary."


def from_headline(fn) -> str | None:
    try:
        return fn(HL)
    except KeyError:
        return None
st.info(f"Country risk feed (schema {feed.get('schema_version', '?')}, label `{feed.get('label', '?')}`): skill "
        f"status **{SKILL}**. {feed.get('skill_text', '')} {usage_text()} Geographic exposure is an *impact* "
        "attribute.")

tab_risk, tab_outlook, tab_graph, tab_agent, tab_about = st.tabs(
    ["Supplier risk table", "Likelihood x impact", "Graph explorer", "Ask the agent", "About the data"])

# ---------------------------------------------------------------- risk table
with tab_risk:
    c1, c2, c3, c4 = st.columns(4)
    f_conc = c1.checkbox("Concentrated (>=25% of an office)", value=True)
    f_sole = c2.checkbox("Sole-source (>=50% non-competed)")
    f_geo = c3.checkbox("Geo-exposed (foreign / feed country)")
    min_obl = c4.number_input("Min prime obligations ($M)", value=1.0, min_value=0.0, step=1.0)
    office = st.text_input("Only suppliers paid by office code (optional)", "").strip().upper()
    where = ["prime_obligations >= ?"]
    params: list = [min_obl * 1e6]
    for on, col in ((f_conc, "flag_concentrated"), (f_sole, "flag_sole_source"), (f_geo, "flag_geo_exposed")):
        if on:
            where.append(col)
    if office:
        where.append("supplier_id IN (SELECT supplier_id FROM supplier_office WHERE office_code = ?)")
        params.append(office)
    df = sql(f"""SELECT supplier_id, name, state, country, prime_obligations, sole_source_share, max_office_share,
                        max_share_office_code, n_dependent_offices, n_subawards_received, foreign_share,
                        feed_countries, max_p_escalation, pagerank_pct, flag_concentrated, flag_sole_source, flag_geo_exposed
                 FROM supplier_risk WHERE {' AND '.join(where)} ORDER BY prime_obligations DESC LIMIT 500""", tuple(params))
    st.write(f"{len(df):,} suppliers (top 500 by prime obligations shown).")
    st.dataframe(df.assign(prime_obligations=(df.prime_obligations / 1e6).round(2)).rename(
        columns={"prime_obligations": "prime_obligations_$M"}), width="stretch", hide_index=True)
    st.caption(f"`max_p_escalation` is {'a likelihood input' if FEED_USED else 'context only'} (feed skill status: {SKILL}). "
               "Flags are a triage screen, not a ranking: in sustainment, OEMs routinely sole-source support for "
               "their own platforms, so most obligations go to at least one flagged supplier. Combine flags with "
               "blast radius in the graph explorer to prioritize.")

# ---------------------------------------------------------------- likelihood x impact (risk backtest)
with tab_outlook:
    try:
        out = sql("""SELECT supplier_id, name, p_disruption_next_fy, excess_vs_size, prime_obligations,
                            n_dependent_offices, flag_concentrated, flag_sole_source, flag_geo_exposed, outlook_fy
                     FROM supplier_disruption_score ORDER BY priority_score DESC""")
    except Exception:
        out = None
    if out is None or out.empty:
        st.info("Run `make backtest` to build the disruption outlook.")
    else:
        md = from_headline(likelihood_md)
        st.markdown(md) if md else st.info(STALE_HEADLINE)
        st.caption(f"Details: [model card]({MODEL_CARD_URL}), [backtest report]({BACKTEST_URL}).")
        c1, c2 = st.columns(2)
        min_m = c1.number_input("Min obligations at stake ($M)", value=10.0, min_value=0.0, step=5.0,
                                key="outlook_min_m")
        min_x = c2.number_input("Min likelihood vs size-only prediction (x)", value=2.0, min_value=0.0, step=0.5,
                                key="outlook_min_x")
        view = filter_outlook(out, min_m, min_x)
        st.write(f"{len(view):,} of {len(out):,} suppliers match both filters (highlighted; gray = all suppliers).")
        st.altair_chart(outlook_chart(out, view, min_m), key="outlook_chart",
                        alt=f"Termination likelihood against obligations at stake; {len(view)} suppliers highlighted")
        st.dataframe(view.assign(prime_obligations=(view.prime_obligations / 1e6).round(1),
                                 p_disruption_next_fy=view.p_disruption_next_fy.round(4),
                                 excess_vs_size=view.excess_vs_size.round(2))
                     .rename(columns={"prime_obligations": "prime_obligations_$M"}).drop(columns="outlook_fy"),
                     width="stretch", hide_index=True, key="outlook_table")

# ---------------------------------------------------------------- graph explorer
with tab_graph:
    name = st.text_input("Supplier name or UEI", "Sikorsky Aircraft Corporation")
    cands = resolve_supplier(name)["candidates"] if name else []
    if not cands:
        st.warning("No supplier in the data matches that name.")
    else:
        labels = [f"{c['name']} ({c['city']}, {c['state']}) [{c['supplier_id']}], ${c['prime_obligations'] / 1e6:,.1f}M prime"
                  for c in cands]
        sid = cands[labels.index(st.selectbox("Candidate", labels))]["supplier_id"]
        prof = Q.run("supplier_profile", {"supplier_id": sid})
        br = Q.run("blast_radius", {"supplier_id": sid})
        s, b = prof["summary"], br["summary"]
        k = st.columns(5)
        k[0].metric("Prime obligations", f"${s['prime_obligations'] / 1e6:,.1f}M")
        k[1].metric("Sole-source share", "n/a" if s["sole_source_share"] is None else f"{s['sole_source_share']:.0%}")
        k[2].metric("Awards at risk", b["n_awards_direct"] + b["n_awards_indirect"])
        k[3].metric("Offices affected", b["n_offices"])
        k[4].metric("Prime customers (as sub)", b["n_prime_customers"])
        st.write("Flags:", ", ".join(f for f in ("concentrated", "sole_source", "geo_exposed") if s.get(f"flag_{f}")) or "none",
                 "| Programs touched:", ", ".join(b["programs"]) or "none",
                 "| Foreign countries:", ", ".join(s["foreign_countries"]) or "none")
        fc = sql("SELECT coalesce(array_to_string(feed_countries, ', '), '') AS fc FROM supplier_risk "
                 "WHERE supplier_id = ?", (sid,))
        fc = [c for c in (fc.fc.iloc[0] if len(fc) else "").split(", ") if c]
        if fc:
            st.caption(f"Works in risk-feed countries {', '.join(fc)}: an impact/context attribute. "
                       f"Feed skill status {SKILL}. {usage_text()}")

        G = graph()
        from pyvis.network import Network
        net = Network(height="560px", width="100%", directed=True, bgcolor="#ffffff", cdn_resources="in_line")
        colors = {"Supplier": "#d62728", "Award": "#9ecae1", "Office": "#2ca02c", "Country": "#ff7f0e",
                  "ParentCompany": "#9467bd", "ProductServiceCode": "#8c564b"}
        sn = nid("Supplier", sid)
        shown: set[str] = set()

        def add(n: str, size: int = 12) -> None:
            if n in shown:
                return
            d = G.nodes[n]
            title = d.get("name") or d.get("award_id") or n
            net.add_node(n, label=(d.get("name") or d.get("piid") or n)[:28], title=f"{d['label']}: {title}",
                         color=colors[d["label"]], size=size)
            shown.add(n)

        add(sn, 28)
        direct = [u for u, _, kk in G.in_edges(sn, keys=True) if kk == "AWARDED_TO"]
        sub_awards = list({nid("Award", d["prime_award_id"]) for _, _, kk, d in G.in_edges(sn, keys=True, data=True)
                           if d.get("type") == "SUBCONTRACTS_TO"})
        for a in sorted(direct, key=lambda x: -G.nodes[x]["obl_pos"])[:25] + sub_awards[:15]:
            add(a, 10)
            net.add_edge(a, sn, title="AWARDED_TO" if a in direct else "prime award (supplier subcontracts)",
                         dashes=a not in direct)
            for _, v, kk in G.out_edges(a, keys=True):
                if kk in ("AWARDED_BY", "PERFORMED_IN") or (kk == "AWARDED_TO" and a not in direct):
                    add(v, 16)
                    net.add_edge(a, v, title=kk)
        for _, v, kk in G.out_edges(sn, keys=True):
            if kk == "SUBSIDIARY_OF":
                add(v, 18)
                net.add_edge(sn, v, title="SUBSIDIARY_OF")
        # vis.js is inlined (cdn_resources="in_line"); drop pyvis's cosmetic Bootstrap CDN tags so the page makes no
        # outbound request (works on an air-gapped network)
        html = "\n".join(ln for ln in net.generate_html().splitlines() if "cdn.jsdelivr.net" not in ln)
        components.html(html, height=580)
        st.caption("Red = suppliers, blue = awards (dashed edge = the supplier is a subcontractor on that award), "
                   "green = offices, orange = countries, purple = parent. Up to 40 awards are drawn; the tables have all of them.")
        c1, c2 = st.columns(2)
        c1.subheader("Offices affected (blast radius)")
        c1.dataframe(pd.DataFrame(br["rows"]), hide_index=True, width="stretch")
        c2.subheader("Dependent offices (direct vs. via subcontracts)")
        c2.dataframe(pd.DataFrame(Q.run("dependent_offices", {"supplier_id": sid})["rows"]), hide_index=True,
                     width="stretch")

# ---------------------------------------------------------------- agent chat
CITE = re.compile(r"\[(award|supplier|parent|office|program|country):([^\]]+)\]")


def linkify(text: str) -> str:
    def rep(mt):
        kind, key = mt.group(1), mt.group(2)
        if kind == "award":
            return f"[\\[award:{key[:18]}…\\]](https://www.usaspending.gov/award/{key})"
        return f"`[{kind}:{key}]`"
    return CITE.sub(rep, text)


with tab_agent:
    spent, budget = costs.total_spent(), costs.budget_usd()
    st.caption(f"Read-only Claude agent with four tools and cited answers. Project API spend so far: "
               f"${spent:.2f} of the ${budget:.2f} guard.")
    examples = ["Which awarding offices depend on Sikorsky Aircraft Corporation, directly or through subcontracts?",
                "For office N00164 (NSWC CRANE), which flagged suppliers carry the most obligations?",
                "If Alsalam Aerospace Industries failed, what would be affected, and what is the escalation risk where it works?",
                "What is the escalation risk for Japan?"]
    q = st.selectbox("Example questions", [""] + examples)
    question = st.text_area("Question", q, height=80)
    if st.button("Ask", disabled=not question.strip() or spent >= budget):
        from supplier_graph.agent.agent import JSON_BLOCK, ask
        with st.spinner("Thinking and querying the graph..."):
            run = ask(question.strip(), log_name="streamlit")
        if run.error:
            st.error(run.error)
        st.markdown(linkify(JSON_BLOCK.sub("", run.final_text)))
        if run.final_json:
            st.write("**Citations:**", ", ".join(f"`{c}`" for c in run.final_json.get("citations", [])) or "none",
                     "| answerable:", run.final_json.get("answerable"))
        st.caption(f"{run.steps} steps, {len(run.tool_calls)} tool calls, ${run.cost_usd:.4f}, {run.latency_s}s")
        with st.expander("Tool-call trace"):
            for t in run.tool_calls:
                st.markdown(f"**{t.name}** `{json.dumps(t.input)}` ({t.latency_s}s)")
                st.json(t.output, expanded=False)

# ---------------------------------------------------------------- about
with tab_about:
    st.markdown(f"""
**Scope.** DoD (toptier 097) prime contracts (types A/B/C/D) and procurement subawards with PSC category **J**
(maintenance, repair and rebuilding of equipment), action dates FY2023-FY2025. Obligations are lifetime totals on
those awards, with de-obligations floored at zero.

**Row counts.** {m['row_counts']['awards']:,} prime awards, {m['row_counts']['subawards']:,} subawards,
{len(sql('SELECT 1 FROM suppliers')):,} suppliers.

**Provenance.** USAspending request key `{m['usaspending']['request_key']}` pulled {m['usaspending']['pulled_at_utc']}.
Every graph node and edge carries `prov_source`, `prov_pulled_at` and `prov_request_key`.

**Risk feed.** {feed['note']}. Source: `{feed['source'].split('/')[-1]}`, schema {feed.get('schema_version', '?')},
skill status **{SKILL}**. {feed.get('skill_text', '')} Escalation label: {feed.get('label', 'n/a')}.

**Evaluation.** See `reports/eval_report.md` and `reports/entity_resolution.md` in the repo.
""")
    st.subheader("Models in this project")
    md = from_headline(models_md)
    if md:
        st.markdown(md)
    else:
        st.info(STALE_HEADLINE)
