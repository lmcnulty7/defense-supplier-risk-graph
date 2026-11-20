"""Streamlit demo: overview, what-if scenario, supplier risk table, likelihood x impact, graph explorer, office view,
agent chat with citations.

Run: `make app` (needs `make data graph` first). Read-only and offline: every number comes from reports/HEADLINE.md
or a tested query. The agent tab calls the Anthropic API and records spend in the project ledger; it refuses to run
once the budget guard trips.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict

import pandas as pd
import streamlit as st

from supplier_graph import nav
from supplier_graph import scenario as SC
from supplier_graph.agent import costs
from supplier_graph.agent.present import describe, plural, status
from supplier_graph.agent.tools import resolve_supplier
from supplier_graph.explain import (
    BACKTEST_URL,
    HOW_TO,
    MODEL_CARD_URL,
    TAGLINE,
    TITLE,
    about_md,
    caveats_md,
    evaluation_md,
    feed_line,
    header_note,
    likelihood_md,
    models_md,
    overview_facts,
)
from supplier_graph.followups import suggest
from supplier_graph.graph import queries as Q
from supplier_graph.graph.build import load_graph
from supplier_graph.graph_component import network
from supplier_graph.graph_view import (
    AWARD_LEGEND,
    AWARD_OPTIONS,
    BLAST_OPTIONS,
    blast_view,
    legend_html,
    money,
    neighbourhood,
    program_names,
)
from supplier_graph.headline import load as load_headline
from supplier_graph.ingest.risk_feed import (
    SKILL_SHORT,
    SKILL_UNKNOWN,
    feed_in_likelihood,
    risk_note,
    usage_short,
)
from supplier_graph.office_view import g_sole_source_at_office, hhi_line, office_graph, office_options
from supplier_graph.outlook_chart import filter_outlook, outlook_chart
from supplier_graph.stage import connect
from supplier_graph.tables import (
    BLAST_COLUMNS,
    DEPENDENT_COLUMNS,
    OFFICE_SUPPLIER_COLUMNS,
    OUTLOOK_COLUMNS,
    RISK_COLUMNS,
    SCENARIO_COLUMNS,
    SOLE_SOURCE_COLUMNS,
    alternatives_text,
    count_line,
    flags_text,
    office_text,
    readable,
)

st.set_page_config(page_title="Supplier Risk Graph", layout="wide")
ss = st.session_state


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


@st.cache_data
def headline() -> dict[str, str]:
    try:
        return load_headline()
    except (OSError, ValueError):
        return {}


@st.cache_data
def offices() -> dict[str, str]:
    return office_options(graph())


@st.cache_data
def scenario_pool() -> dict[str, str]:
    """The 2,000 largest suppliers by prime obligations: supplier_id -> "NAME (City, ST)"."""
    df = sql("""SELECT supplier_id, name, coalesce(city, '') city, coalesce(state, '') st FROM supplier_risk
                ORDER BY prime_obligations DESC, supplier_id LIMIT 2000""")
    return {r.supplier_id: f"{r.name} ({r.city.title()}, {r.st})" if r.city else r.name for r in df.itertuples()}


@st.cache_data(show_spinner="Finding featured suppliers…")
def featured() -> list[dict]:
    pool = list(scenario_pool())[:SC.FEATURED_POOL]
    return SC.featured(pool)


m = meta()
feed = m["risk_feed"]
SKILL = feed.get("skill_status", SKILL_UNKNOWN)
FEED_USED = feed_in_likelihood()
HL = headline()
STALE_HEADLINE = "Run `make headline` to load the model summary."


def from_headline(fn, *args) -> str | None:
    try:
        return fn(HL, *args)
    except KeyError:
        return None


def show(md: str | None) -> None:
    if md:
        st.markdown(md)
    else:
        st.info(STALE_HEADLINE)


def back_button(here: str) -> None:
    target = nav.back_target(ss)
    if target:
        st.button(f"Back to {target}", icon=":material/arrow_back:", key=f"back_{here}", on_click=nav.back,
                  args=(ss,), type="tertiary")


def supplier_name(sid: str) -> str:
    n = f"supplier:{sid}"
    return graph().nodes[n]["name"] if n in graph() else sid


# ---------------------------------------------------------------- header
st.title(TITLE)
st.markdown(TAGLINE)
st.caption(header_note(FEED_USED))
if feed["is_fixture"]:
    st.warning(f"Country risk comes from a synthetic fixture: {feed['note']}.")

tabs = dict(zip(nav.TABS, st.tabs(list(nav.TABS), key=nav.TAB_KEY, on_change=lambda: nav.clear_back(ss)),
                strict=True))

# ---------------------------------------------------------------- overview
with tabs[nav.OVERVIEW]:
    if tabs[nav.OVERVIEW].open:
        facts = from_headline(overview_facts)
        if facts:
            with st.container(horizontal=True):
                for value, what, note in facts:
                    with st.container(border=True):
                        st.subheader(value)
                        st.markdown(what)
                        st.caption(note)
        else:
            st.info(STALE_HEADLINE)
        st.caption(HOW_TO)
        st.subheader("Featured suppliers")
        with st.container(horizontal=True):
            for f in featured():
                s = f["result"]["summary"]
                with st.container(border=True):
                    st.markdown(f"**{SC.short_name(f['name'])}**")
                    st.caption(f["why"])
                    st.markdown(f"{plural(s['n_offices'], 'office')} affected · {money(s['prime_obligations'])} at "
                                f"stake · {plural(s['n_offices_no_alternative_at_office'], 'office')} with no "
                                "supplier already doing the same work")
                    with st.container(horizontal=True):
                        st.button("Graph explorer", key=f"feat_gx_{f['supplier_id']}", icon=":material/hub:",
                                  on_click=nav.open_supplier, args=(ss, f["supplier_id"], nav.OVERVIEW))
                        st.button("What if it fails?", key=f"feat_sc_{f['supplier_id']}", icon=":material/bolt:",
                                  on_click=nav.open_scenario, args=(ss, [f["supplier_id"]], nav.OVERVIEW))

# ---------------------------------------------------------------- what if it fails?
with tabs[nav.SCENARIO]:
    if tabs[nav.SCENARIO].open:
        back_button("scenario")
        pool = scenario_pool()
        ss.setdefault(nav.SCENARIO_KEY, [])
        names = {**pool, **{x: supplier_name(x) for x in ss[nav.SCENARIO_KEY] if x not in pool}}
        picked = st.multiselect("Suppliers to remove", list(names), key=nav.SCENARIO_KEY, format_func=names.get,
                                placeholder="Pick one or more suppliers", persist_state="session",
                                help="The 2,000 largest suppliers. Open any other supplier in the Graph explorer and "
                                     "click What if it fails?")
        if not picked:
            st.caption("Remove one or more suppliers to see which offices, dollars and programs depend on them, and "
                       "whether other suppliers already do the same work.")
            st.button("Try Sikorsky Aircraft", on_click=nav.open_scenario, args=(ss, [SC.ANCHOR], None))
        else:
            res = SC.run(picked)
            s = res["summary"]
            st.markdown(f"**{SC.headline(res)}**")
            k = st.columns(4)
            k[0].metric("Offices affected", f"{s['n_offices']:,}")
            k[1].metric("Prime obligations at stake", money(s["prime_obligations"]))
            k[2].metric("Their subcontracts", money(s["sub_amount"]))
            k[3].metric("Offices with no supplier already doing this work", f"{s['n_offices_no_alternative_at_office']:,}")
            if s["programs"]:
                st.markdown(f"**Programs touched:** {', '.join(s['programs'])}")
            rows = pd.DataFrame(res["rows"])
            if len(rows):
                rows["alternatives"] = [alternatives_text(t) for t in rows.top_alternatives]
                ss["_scenario_offices"] = list(rows.office_code)
                disp, cfg = readable(rows, SCENARIO_COLUMNS)
                st.dataframe(disp, column_config=cfg, hide_index=True, width="stretch", key="scenario_table",
                             on_select=lambda: nav.open_selected(ss, "scenario_table", "_scenario_offices", "office", nav.SCENARIO),
                             selection_mode="single-row")
                more = f"Largest {len(rows)} of {s['n_offices']:,} offices shown. " if s["n_offices"] > len(rows) else ""
                st.caption(f"{more}Click a row to open the office. {SC.CAVEAT}")

# ---------------------------------------------------------------- risk table
with tabs[nav.RISK]:
    if tabs[nav.RISK].open:
        c1, c2, c3, c4 = st.columns(4)
        f_conc = c1.checkbox("Concentrated (>=25% of an office)", value=True, key="risk_conc", persist_state="session")
        f_sole = c2.checkbox("Sole-source (>=50% non-competed)", key="risk_sole", persist_state="session")
        f_geo = c3.checkbox("Geo-exposed (foreign / feed country)", key="risk_geo", persist_state="session")
        min_obl = c4.number_input("Min prime obligations ($M)", value=1.0, min_value=0.0, step=1.0, key="risk_min",
                                  persist_state="session")
        office_opts = offices()
        office = st.selectbox("Only suppliers paid by this office (optional)", [""] + list(office_opts),
                              format_func=lambda c: office_opts.get(c, "All offices"), key="risk_office",
                              persist_state="session")
        where = ["r.prime_obligations >= ?"]
        params: list = [min_obl * 1e6]
        for on, col in ((f_conc, "flag_concentrated"), (f_sole, "flag_sole_source"), (f_geo, "flag_geo_exposed")):
            if on:
                where.append(f"r.{col}")
        if office:
            where.append("r.supplier_id IN (SELECT supplier_id FROM supplier_office WHERE office_code = ?)")
            params.append(office)
        df = sql(f"""SELECT r.supplier_id, r.name, r.state, r.country, r.prime_obligations, r.sole_source_share,
                            r.max_office_share, r.max_share_office_code, o.office_name AS max_share_office_name,
                            r.n_dependent_offices, r.n_subawards_received, r.foreign_share, r.feed_countries,
                            r.max_p_escalation, r.pagerank_pct, r.flag_concentrated, r.flag_sole_source,
                            r.flag_geo_exposed
                     FROM supplier_risk r
                     LEFT JOIN (SELECT office_code, min(office_name) AS office_name FROM office_totals GROUP BY 1) o
                       ON o.office_code = r.max_share_office_code
                     WHERE {' AND '.join(where)} ORDER BY r.prime_obligations DESC, r.supplier_id""", tuple(params))
        st.write(count_line(len(df)))
        top = df.head(500)
        ss["_risk_ids"] = list(top.supplier_id)
        disp, cfg = readable(top.assign(top_office=[office_text(n, c) for n, c in
                                                    zip(top.max_share_office_name, top.max_share_office_code,
                                                        strict=True)]), RISK_COLUMNS)
        st.dataframe(disp, column_config=cfg, width="stretch", hide_index=True, key="risk_table",
                     on_select=lambda: nav.open_selected(ss, "risk_table", "_risk_ids", "supplier", nav.RISK),
                     selection_mode="single-row")
        st.download_button("Download the filtered table (CSV)", df.to_csv(index=False).encode(),
                           file_name="supplier_risk_filtered.csv", mime="text/csv", icon=":material/download:",
                           key="risk_csv")
        st.caption("Click a row to open the supplier in the Graph explorer. Flags are a screening tool, not a ranking. "
                   "Most maintenance spending goes to some flagged supplier, so open a supplier in the Graph explorer "
                   "to see what depends on it.")

# ---------------------------------------------------------------- likelihood x impact (risk backtest)
with tabs[nav.LIKELIHOOD]:
    if tabs[nav.LIKELIHOOD].open:
        try:
            out = sql("""SELECT supplier_id, name, p_disruption_next_fy, excess_vs_size, prime_obligations,
                                n_dependent_offices, flag_concentrated, flag_sole_source, flag_geo_exposed, outlook_fy
                         FROM supplier_disruption_score ORDER BY priority_score DESC""")
        except Exception:
            out = None
        if out is None or out.empty:
            st.info("Run `make backtest` to build the disruption outlook.")
        else:
            show(from_headline(likelihood_md))
            st.caption(f"Details: [model card]({MODEL_CARD_URL}), [backtest report]({BACKTEST_URL}).")
            c1, c2 = st.columns(2)
            min_m = c1.number_input("Min obligations at stake ($M)", value=10.0, min_value=0.0, step=5.0,
                                    key="outlook_min_m", persist_state="session")
            min_x = c2.number_input("Min likelihood vs size-only prediction (x)", value=2.0, min_value=0.0, step=0.5,
                                    key="outlook_min_x", persist_state="session")
            view = filter_outlook(out, min_m, min_x)
            st.write(f"{len(view):,} of {len(out):,} suppliers match both filters (highlighted; gray = all "
                     "suppliers).")
            st.altair_chart(outlook_chart(out, view, min_m), key="outlook_chart",
                            alt=f"Termination likelihood against obligations at stake; {len(view)} suppliers "
                                "highlighted")
            ss["_outlook_ids"] = list(view.supplier_id)
            disp, cfg = readable(view, OUTLOOK_COLUMNS)
            st.dataframe(disp, column_config=cfg, width="stretch", hide_index=True, key="outlook_table",
                         on_select=lambda: nav.open_selected(ss, "outlook_table", "_outlook_ids", "supplier", nav.LIKELIHOOD),
                         selection_mode="single-row")
            st.caption("Click a row to open the supplier in the Graph explorer.")

# ---------------------------------------------------------------- graph explorer
with tabs[nav.GRAPH]:
    if tabs[nav.GRAPH].open:
        back_button("graph")
        ss.setdefault(nav.SUPPLIER_KEY, "Sikorsky Aircraft Corporation")
        name = st.text_input("Supplier name or UEI", key=nav.SUPPLIER_KEY, persist_state="session")
        cands = resolve_supplier(name)["candidates"] if name else []
        if not cands:
            st.warning("No supplier in the data matches that name.")
        else:
            labels = {c["supplier_id"]: f"{c['name']} ({c['city']}, {c['state']}) [{c['supplier_id']}], "
                                        f"${c['prime_obligations'] / 1e6:,.1f}M prime" for c in cands}
            # no key: the widget's identity follows its options, so a new name shows its own first candidate
            sid = st.selectbox("Candidate", list(labels), format_func=labels.get)
            ss["_gx_sid"] = sid
            prof = Q.run("supplier_profile", {"supplier_id": sid})
            br = Q.run("blast_radius", {"supplier_id": sid})
            s, b = prof["summary"], br["summary"]
            k = st.columns(5)
            k[0].metric("Prime obligations", f"${s['prime_obligations'] / 1e6:,.1f}M")
            k[1].metric("Sole-source share", "n/a" if s["sole_source_share"] is None else f"{s['sole_source_share']:.1%}")
            k[2].metric("Awards at risk", f"{b['n_awards_direct'] + b['n_awards_indirect']:,}")
            k[3].metric("Offices affected", f"{b['n_offices']:,}")
            k[4].metric("Prime contractors it subcontracts for", f"{b['n_prime_customers']:,}")

            G = graph()
            progs, n_unnamed = program_names(G, sid)
            countries = [G.nodes[f"country:{c}"]["name"] if f"country:{c}" in G else c for c in s["foreign_countries"]]
            facts = [f"**Flags:** {flags_text(s) or 'none'}"]
            if progs:
                facts.append(f"**Programs:** {', '.join(progs)}"
                             + (f" (+{plural(n_unnamed, 'program code')} without a name)" if n_unnamed else ""))
            facts.append(f"**Foreign work:** {', '.join(countries) or 'none'}")
            st.markdown("  \n".join(facts))
            fc = sql("SELECT coalesce(array_to_string(feed_countries, ', '), '') AS fc FROM supplier_risk "
                     "WHERE supplier_id = ?", (sid,))
            fc = [c for c in (fc.fc.iloc[0] if len(fc) else "").split(", ") if c]
            if fc:
                st.caption(f"Works in risk-feed countries {', '.join(fc)}. {risk_note(SKILL)}")

            def ask_about(sid=sid, nm=s["name"]):
                ss["agent_q"] = f"Which offices depend on {nm}, directly or through subcontracts?"
                nav._go(ss, nav.AGENT, nav.GRAPH)

            with st.container(horizontal=True, vertical_alignment="center"):
                st.button("What if it fails?", icon=":material/bolt:", key="gx_whatif",
                          on_click=nav.open_scenario, args=(ss, [sid], nav.GRAPH))
                st.button("Ask the agent about it", icon=":material/chat:", key="gx_ask", on_click=ask_about)
                awards_view = st.toggle("Show individual awards", key="graph_awards", persist_state="session")

            def on_node(net_key: str):
                node = ss[net_key].clicked
                if node and node != f"supplier:{ss.get('_gx_sid')}":
                    nav.open_node(ss, node, nav.GRAPH)

            if awards_view:
                g_nodes, g_edges, n_shown, n_awards = neighbourhood(G, sid, br["rows"])
                net_key, options = "gx_net_awards", AWARD_OPTIONS
                st.html(legend_html(AWARD_LEGEND))
                st.caption(f"{n_shown:,} of {plural(n_awards, 'award')} shown (the largest direct awards and prime "
                           "awards it subcontracts on). Hover an award for its ID. Click an office to open it in the "
                           "Office view, or a prime contractor to open it here.")
            else:
                g_nodes, g_edges = blast_view(G, sid, br["rows"])
                net_key, options = "gx_net", BLAST_OPTIONS
                st.html(legend_html())
                st.caption("Hover a line for its contract count and top awards. Click an office to open it in the "
                           "Office view, or a prime contractor to open it here.")
            n_off = sum(n["id"].startswith("office:") for n in g_nodes)
            network(g_nodes, g_edges, key=net_key, options=options, on_click=lambda: on_node(net_key),
                    title=f"Network of {s['name']} [{sid}]: {plural(n_off, 'office')}, "
                          f"{plural(len(g_nodes), 'node')}")
            c1, c2 = st.columns(2)
            c1.subheader("Offices affected (blast radius)")
            ss["_blast_offices"] = [r["office_code"] for r in br["rows"]]
            disp, cfg = readable(pd.DataFrame(br["rows"], columns=["office_code", "office_name", "n_awards",
                                                                    "obligations"]), BLAST_COLUMNS)
            c1.dataframe(disp, column_config=cfg, hide_index=True, width="stretch", key="blast_table",
                         on_select=lambda: nav.open_selected(ss, "blast_table", "_blast_offices", "office", nav.GRAPH),
                         selection_mode="single-row")
            c2.subheader("Dependent offices (direct vs. via subcontracts)")
            dep = Q.run("dependent_offices", {"supplier_id": sid})["rows"]
            disp, cfg = readable(pd.DataFrame(dep, columns=["office_code", "office_name", "direct_obligations",
                                                            "n_direct_awards", "sub_amount", "n_indirect_awards",
                                                            "example_award_ids"]), DEPENDENT_COLUMNS)
            c2.dataframe(disp, column_config=cfg, hide_index=True, width="stretch", key="dependent_table")

# ---------------------------------------------------------------- office view
with tabs[nav.OFFICE]:
    if tabs[nav.OFFICE].open:
        back_button("office")
        opts = offices()
        ss.setdefault(nav.OFFICE_KEY, "N00019")
        code = st.selectbox("Office", list(opts), format_func=opts.get, key=nav.OFFICE_KEY, persist_state="session",
                            help="Type to search by name or code.")
        conc = Q.run("office_concentration", {"office_code": code, "limit": 15})
        cs = conc["summary"]
        sole = g_sole_source_at_office(graph(), code)
        k = st.columns(4)
        k[0].metric("Obligations", money(cs["total_obligations"]))
        k[1].metric("Suppliers", f"{cs['n_suppliers']:,}")
        k[2].metric("Top supplier's share", "n/a" if cs["top_supplier_share"] is None
                    else f"{cs['top_supplier_share']:.1%}")
        k[3].metric("Sole-source suppliers", f"{len(sole):,}")
        st.caption(hhi_line(cs["hhi"]))
        nodes, edges = office_graph(graph(), code, conc["rows"], {r["supplier_id"]: r["sole_source_obligations"]
                                                                  for r in sole})

        def on_office_node():
            node = ss["office_net"].clicked
            if node and node.startswith("supplier:"):
                nav.open_node(ss, node, nav.OFFICE)

        network(nodes, edges, key="office_net", options=BLAST_OPTIONS, on_click=on_office_node,
                title=f"Office network of {opts[code]}: {plural(len(nodes) - 1, 'supplier')}")
        st.caption("Top 15 suppliers by obligations from this office; thicker line = more money. Click a supplier to "
                   "open it in the Graph explorer.")
        c1, c2 = st.columns(2)
        c1.subheader("Top suppliers")
        top = pd.DataFrame(conc["rows"])
        ss["_office_ids"] = list(top.supplier_id) if len(top) else []
        disp, cfg = readable(top.assign(supplier_id=top.get("supplier_id")), OFFICE_SUPPLIER_COLUMNS) if len(top) \
            else (top, {})
        c1.dataframe(disp, column_config=cfg, hide_index=True, width="stretch", key="office_table",
                     on_select=lambda: nav.open_selected(ss, "office_table", "_office_ids", "supplier", nav.OFFICE),
                     selection_mode="single-row")
        c2.subheader("Sole-source suppliers")
        sdf = pd.DataFrame(sole, columns=["supplier_id", "name", "sole_source_obligations", "obligations"])
        sdf["sole_source_share"] = (sdf.sole_source_obligations / sdf.obligations).where(sdf.obligations > 0)
        ss["_sole_ids"] = list(sdf.supplier_id)
        disp, cfg = readable(sdf, SOLE_SOURCE_COLUMNS)
        c2.dataframe(disp, column_config=cfg, hide_index=True, width="stretch", key="sole_table",
                     on_select=lambda: nav.open_selected(ss, "sole_table", "_sole_ids", "supplier", nav.OFFICE),
                     selection_mode="single-row")
        st.caption("Sole-source share = the part of that supplier's obligations from this office that was not "
                   "competed. Click a row to open the supplier.")

# ---------------------------------------------------------------- agent chat
CITE = re.compile(r"\[(award|supplier|parent|office|program|country):([^\]]+)\]")
EXAMPLES = [
    "Which offices would be affected if Sikorsky Aircraft Corporation failed?",
    "If Sikorsky Aircraft Corporation failed, which offices would have no other supplier already doing the same work?",
    "For office N00164 (NSWC CRANE), which flagged suppliers carry the most obligations?",
    "Which suppliers hold sole-source contracts performed in Bahrain?",
    "Which companies subcontract to Sikorsky Aircraft Corporation?",
    "If Alsalam Aerospace Industries failed, what would be affected, and what is the escalation risk where it works?",
    "What is the escalation risk for Japan?",  # the decline example: no risk score, but who works there
]


def linkify(text: str) -> str:
    def rep(mt):
        kind, key = mt.group(1), mt.group(2)
        if kind == "award":
            return f"[\\[award:{key[:18]}…\\]](https://www.usaspending.gov/award/{key})"
        return f"`[{kind}:{key}]`"
    return CITE.sub(rep, text)


def per_question_usd() -> float:
    try:
        return float(HL["cost_opus"].lstrip("$"))
    except (KeyError, ValueError):
        return 0.04


def set_question(text: str) -> None:
    ss["agent_q"] = text


with tabs[nav.AGENT]:
    if tabs[nav.AGENT].open:
        spent, budget = costs.total_spent(), costs.budget_usd()
        left = int(max(budget - spent, 0.0) / per_question_usd())
        st.caption("Read-only Claude agent with five tools; every fact cites the record it came from.")
        if left > 0:
            st.caption(f"About {plural(left, 'question')} left in this project's API budget.")
        else:
            st.caption("The API budget is used up. To ask more, set BUDGET_USD in .env (see the README).")
        ss.setdefault("agent_q", "")
        st.selectbox("Example questions", [""] + EXAMPLES, key="agent_example",
                     on_change=lambda: set_question(ss["agent_example"]))
        question = st.text_area("Question", key="agent_q", height=80, persist_state="session")
        if st.button("Ask", disabled=not question.strip() or left <= 0):
            from supplier_graph.agent.agent import ask
            with st.spinner("Thinking and querying the graph..."):
                ss["agent_run"] = json.loads(json.dumps(asdict(ask(question.strip(), log_name="streamlit")),
                                                        default=str))
        run = ss.get("agent_run")
        if run:
            from supplier_graph.agent.agent import JSON_BLOCK
            if run.get("error"):
                st.error(run["error"])
            st.markdown(f"**Q: {run['question']}**")
            st.markdown(linkify(JSON_BLOCK.sub("", run.get("final_text") or "")))
            label, cites, reason = status(run.get("final_json"))
            if label:
                st.markdown(f"**{label}**")
            if reason:
                st.markdown(f"**No data:** {reason}")
            if cites:
                st.markdown("**Citations:** " + ", ".join(f"`{c}`" for c in cites))
                links = [c for c in cites if c.startswith(("supplier:", "office:"))][:5]
                if links:
                    with st.container(horizontal=True):
                        for c in links:
                            kind, key = c.split(":", 1)
                            lab = SC.short_name(supplier_name(key)) if kind == "supplier" else offices().get(key, key)
                            st.button(lab, key=f"cite_{c}", icon=":material/hub:" if kind == "supplier"
                                      else ":material/account_balance:", on_click=nav.open_node, args=(ss, c, nav.AGENT))
                    st.caption("Open a cited supplier in the Graph explorer or a cited office in the Office view. "
                               "Award citations link to USAspending.")
            st.caption(f"{plural(run.get('steps') or 0, 'step')}, {plural(len(run.get('tool_calls') or []), 'tool call')}"
                       f", ${run.get('cost_usd') or 0:.4f}, {run.get('latency_s')}s")
            ups = suggest(run["question"], run.get("tool_calls") or [], run.get("final_json"), EXAMPLES[:3],
                          office_label=lambda c: offices().get(c, c))
            if ups:
                st.markdown("**Ask next**")
                with st.container(horizontal=True):
                    for i, u in enumerate(ups):
                        st.button(u, key=f"followup_{i}", on_click=set_question, args=(u,))
            with st.expander("Tool-call trace"):
                for i, t in enumerate(run.get("tool_calls") or []):
                    st.markdown(describe(t["name"], t["input"], t["output"], t["latency_s"]))
                    with st.expander("Raw JSON", key=f"trace_raw_{i}"):
                        st.json({"tool": t["name"], "input": t["input"], "output": t["output"]}, expanded=False)

# ---------------------------------------------------------------- about
with tabs[nav.ABOUT]:
    if tabs[nav.ABOUT].open:
        show(from_headline(about_md, m["row_counts"]["awards"], m["row_counts"]["subawards"],
                           len(sql("SELECT 1 FROM suppliers"))))
        line = from_headline(feed_line, SKILL_SHORT.get(SKILL, SKILL_SHORT[SKILL_UNKNOWN]), usage_short())
        if line:
            st.markdown(f"**Country risk feed.** {line}")
            with st.expander("Feed details"):
                st.markdown(f"File `{HL['feed_file']}` · {HL['feed_version']} · skill status {SKILL}")
        st.markdown("**Models.**")
        show(from_headline(models_md))
        st.markdown(evaluation_md())
        show(from_headline(caveats_md))
