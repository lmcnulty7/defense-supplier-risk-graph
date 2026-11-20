"""Streamlit demo: supplier risk table, likelihood x impact, graph explorer (blast radius), agent chat with citations.

Run: `make app` (needs `make data graph` first). The agent tab calls the Anthropic API and records spend
in the project ledger; it refuses to run once the budget guard trips.
"""

from __future__ import annotations

import json
import re

import pandas as pd
import streamlit as st

from supplier_graph.agent import costs
from supplier_graph.agent.present import describe, plural, status
from supplier_graph.agent.tools import resolve_supplier
from supplier_graph.explain import (
    BACKTEST_URL,
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
)
from supplier_graph.graph import queries as Q
from supplier_graph.graph.build import load_graph
from supplier_graph.graph_view import (
    BLAST_OPTIONS,
    HEIGHT_PX,
    OPTIONS,
    blast_view,
    legend_html,
    neighbourhood,
    network_html,
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
from supplier_graph.outlook_chart import filter_outlook, outlook_chart
from supplier_graph.stage import connect
from supplier_graph.tables import (
    BLAST_COLUMNS,
    DEPENDENT_COLUMNS,
    OUTLOOK_COLUMNS,
    RISK_COLUMNS,
    count_line,
    flags_text,
    office_text,
    readable,
)

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


@st.cache_data
def headline() -> dict[str, str]:
    try:
        return load_headline()
    except (OSError, ValueError):
        return {}


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


# ---------------------------------------------------------------- header
st.title(TITLE)
st.markdown(TAGLINE)
st.caption(header_note(FEED_USED))
if feed["is_fixture"]:
    st.warning(f"Country risk comes from a synthetic fixture: {feed['note']}.")

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
                        r.max_p_escalation, r.pagerank_pct, r.flag_concentrated, r.flag_sole_source, r.flag_geo_exposed
                 FROM supplier_risk r
                 LEFT JOIN (SELECT office_code, min(office_name) AS office_name FROM office_totals GROUP BY 1) o
                   ON o.office_code = r.max_share_office_code
                 WHERE {' AND '.join(where)} ORDER BY r.prime_obligations DESC, r.supplier_id""", tuple(params))
    st.write(count_line(len(df)))
    top = df.head(500)
    disp, cfg = readable(top.assign(top_office=[office_text(n, c) for n, c in
                                                zip(top.max_share_office_name, top.max_share_office_code, strict=True)]),
                         RISK_COLUMNS)
    st.dataframe(disp, column_config=cfg, width="stretch", hide_index=True, key="risk_table")
    st.download_button("Download the filtered table (CSV)", df.to_csv(index=False).encode(),
                       file_name="supplier_risk_filtered.csv", mime="text/csv", icon=":material/download:",
                       key="risk_csv")
    st.caption("Flags are a screening tool, not a ranking. Most maintenance spending goes to some flagged supplier, "
               "so open a supplier in the Graph explorer to see what depends on it.")

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
        show(from_headline(likelihood_md))
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
        disp, cfg = readable(view, OUTLOOK_COLUMNS)
        st.dataframe(disp, column_config=cfg, width="stretch", hide_index=True, key="outlook_table")

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

        awards_view = st.toggle("Show individual awards", key="graph_awards")
        if awards_view:
            g_nodes, g_edges, n_shown, n_awards = neighbourhood(G, sid, br["rows"])
            options = OPTIONS
            legend = (f"{n_shown:,} of {plural(n_awards, 'award')} shown (the largest direct awards and prime awards "
                      "it subcontracts on); hover an award for its ID. Red = the supplier, blue = awards (dashed edge "
                      "= it subcontracts on that award), green = every affected office (dotted edge = none of its "
                      "awards drawn), orange = foreign countries, purple = parent company.")
        else:
            g_nodes, g_edges = blast_view(G, sid, br["rows"])
            options = BLAST_OPTIONS
            legend = None
        n_off = sum(n["id"].startswith("office:") for n in g_nodes)
        if legend is None:
            st.html(legend_html())
            st.caption("Hover a line for its contract count and top awards.")
        st.iframe(network_html(g_nodes, g_edges, options=options), height=HEIGHT_PX + 20,
                  alt=f"Network of {s['name']} [{sid}]: {plural(n_off, 'office')}, {plural(len(g_nodes), 'node')}")
        if legend:
            st.caption(legend)
        c1, c2 = st.columns(2)
        c1.subheader("Offices affected (blast radius)")
        disp, cfg = readable(pd.DataFrame(br["rows"], columns=["office_code", "office_name", "n_awards",
                                                                "obligations"]), BLAST_COLUMNS)
        c1.dataframe(disp, column_config=cfg, hide_index=True, width="stretch", key="blast_table")
        c2.subheader("Dependent offices (direct vs. via subcontracts)")
        dep = Q.run("dependent_offices", {"supplier_id": sid})["rows"]
        disp, cfg = readable(pd.DataFrame(dep, columns=["office_code", "office_name", "direct_obligations",
                                                        "n_direct_awards", "sub_amount", "n_indirect_awards",
                                                        "example_award_ids"]), DEPENDENT_COLUMNS)
        c2.dataframe(disp, column_config=cfg, hide_index=True, width="stretch", key="dependent_table")

# ---------------------------------------------------------------- agent chat
CITE = re.compile(r"\[(award|supplier|parent|office|program|country):([^\]]+)\]")
EXAMPLES = [
    "Which offices would be affected if Sikorsky Aircraft Corporation failed?",
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


with tab_agent:
    spent, budget = costs.total_spent(), costs.budget_usd()
    left = int(max(budget - spent, 0.0) / per_question_usd())
    st.caption("Read-only Claude agent with four tools; every fact cites the record it came from.")
    if left > 0:
        st.caption(f"About {plural(left, 'question')} left in this project's API budget.")
    else:
        st.caption("The API budget is used up. To ask more, set BUDGET_USD in .env (see the README).")
    q = st.selectbox("Example questions", [""] + EXAMPLES)
    question = st.text_area("Question", q, height=80)
    if st.button("Ask", disabled=not question.strip() or left <= 0):
        from supplier_graph.agent.agent import JSON_BLOCK, ask
        with st.spinner("Thinking and querying the graph..."):
            run = ask(question.strip(), log_name="streamlit")
        if run.error:
            st.error(run.error)
        st.markdown(linkify(JSON_BLOCK.sub("", run.final_text)))
        label, cites, reason = status(run.final_json)
        if label:
            st.markdown(f"**{label}**")
        if reason:
            st.markdown(f"**No data:** {reason}")
        if cites:
            st.markdown("**Citations:** " + ", ".join(f"`{c}`" for c in cites))
        st.caption(f"{plural(run.steps, 'step')}, {plural(len(run.tool_calls), 'tool call')}, ${run.cost_usd:.4f}, "
                   f"{run.latency_s}s")
        with st.expander("Tool-call trace"):
            for i, t in enumerate(run.tool_calls):
                st.markdown(describe(t.name, t.input, t.output, t.latency_s))
                with st.expander("Raw JSON", key=f"trace_raw_{i}"):
                    st.json({"tool": t.name, "input": t.input, "output": t.output}, expanded=False)

# ---------------------------------------------------------------- about
with tab_about:
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
