"""Static results site for interviews: `make site` -> site/index.html (one self-contained file, works offline).

Every number is read from the committed report JSONs, the DuckDB tables or the committed agent logs at build time,
so the page is reproducible from make targets. Slide-style sections; arrow keys / PageUp / PageDown move between
them, and the page also scrolls and prints normally. Charts are inline SVG generated here (no JS libraries).
"""

from __future__ import annotations

import glob
import html
import json
import math
import re
from datetime import UTC, datetime

from supplier_graph.agent import costs
from supplier_graph.config import LOGS, REPORTS, ROOT
from supplier_graph.explain import (
    BACKTEST_URL,
    MODEL_CARD_URL,
    TAGLINE,
    TITLE,
    header_note,
    likelihood_points,
)
from supplier_graph.headline import load as load_headline
from supplier_graph.ingest.risk_feed import feed_in_likelihood
from supplier_graph.stage import connect

OUT = ROOT / "site" / "index.html"


def J(name: str) -> dict:
    return json.loads((REPORTS / name).read_text())


def esc(x) -> str:
    return html.escape(str(x))


def pct(x: float, d: int = 1) -> str:
    return f"{x * 100:.{d}f}%"


def money(x: float) -> str:
    return f"${x / 1e9:,.1f}B" if x >= 1e9 else f"${x / 1e6:,.1f}M"


# ------------------------------------------------------------------ SVG helpers
def svg_stacked_bars(rows: list[tuple[str, list[float]]], parts: list[tuple[str, str]], w: int = 760) -> str:
    """Horizontal 100% stacked bars. rows: (label, [fractions]); parts: (legend, css var)."""
    lw, bh, gap, top = 250, 26, 14, 30
    h = top + len(rows) * (bh + gap) + 10
    pw = w - lw - 20
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" class="chart">']
    x = 10
    for name, var in parts:
        out.append(f'<rect x="{x}" y="6" width="12" height="12" fill="var({var})"/>'
                   f'<text x="{x + 18}" y="16" class="lg">{esc(name)}</text>')
        x += 30 + 8 * len(name)
    for i, (label, vals) in enumerate(rows):
        y = top + i * (bh + gap)
        out.append(f'<text x="{lw - 10}" y="{y + bh * 0.68}" text-anchor="end" class="lb">{esc(label)}</text>')
        x0 = lw
        for v, (_, var) in zip(vals, parts, strict=True):
            ww = v * pw
            out.append(f'<rect x="{x0:.1f}" y="{y}" width="{ww:.1f}" height="{bh}" fill="var({var})"/>')
            if ww > 42:
                out.append(f'<text x="{x0 + ww / 2:.1f}" y="{y + bh * 0.68}" text-anchor="middle" class="in">{pct(v)}</text>')
            x0 += ww
    out.append("</svg>")
    return "".join(out)


def svg_dot_whisker(rows: list[tuple[str, float, float, float, bool]], lo: float, hi: float, ref: float | None,
                    w: int = 760, log: bool = False, fmt=lambda v: f"{v:.2f}", ref_label: str = "") -> str:
    """rows: (label, value, ci_lo, ci_hi, highlight). Optional vertical reference line."""
    lw, rh, top = 260, 34, 18
    h = top + len(rows) * rh + 34
    pw = w - lw - 30
    tf = (lambda v: math.log(v)) if log else (lambda v: v)
    sx = lambda v: lw + (tf(v) - tf(lo)) / (tf(hi) - tf(lo)) * pw  # noqa: E731
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" class="chart">']
    ticks = [0.25, 0.5, 1, 2, 4] if log else [lo + (hi - lo) * k / 4 for k in range(5)]
    for t in ticks:
        if lo <= t <= hi:
            out.append(f'<line x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{top - 6}" y2="{h - 26}" class="grid"/>'
                       f'<text x="{sx(t):.1f}" y="{h - 10}" text-anchor="middle" class="ax">{fmt(t)}</text>')
    if ref is not None:
        out.append(f'<line x1="{sx(ref):.1f}" x2="{sx(ref):.1f}" y1="{top - 10}" y2="{h - 26}" class="ref"/>')
        if ref_label:
            out.append(f'<text x="{sx(ref) + 4:.1f}" y="{top - 2}" class="ax">{esc(ref_label)}</text>')
    for i, (label, v, a, b, hl) in enumerate(rows):
        y = top + i * rh + rh / 2
        cls = "hl" if hl else "mu"
        out.append(f'<text x="{lw - 10}" y="{y + 4}" text-anchor="end" class="lb">{esc(label)}</text>'
                   f'<line x1="{sx(max(a, lo)):.1f}" x2="{sx(min(b, hi)):.1f}" y1="{y}" y2="{y}" class="wh {cls}"/>'
                   f'<circle cx="{sx(v):.1f}" cy="{y}" r="6" class="dot {cls}"/>'
                   f'<text x="{sx(min(b, hi)) + 8:.1f}" y="{y + 4}" class="val">{fmt(v)}</text>')
    out.append("</svg>")
    return "".join(out)


def svg_scatter(pts: list[tuple[float, float, bool]], w: int = 760, h: int = 380) -> str:
    """x = prime obligations (log10 $), y = predicted probability (log10). Highlighted = excess over size >= 2."""
    lm, r, t, b = 64, 20, 14, 46
    xs = [math.log10(max(p[0], 1e4)) for p in pts]
    ys = [math.log10(max(p[1], 1e-4)) for p in pts]
    x0, x1, y0, y1 = 4, math.ceil(max(xs)), -4, 0
    sx = lambda v: lm + (v - x0) / (x1 - x0) * (w - lm - r)  # noqa: E731
    sy = lambda v: h - b - (v - y0) / (y1 - y0) * (h - t - b)  # noqa: E731
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" class="chart">']
    for k in range(x0, x1 + 1):
        lab = {4: "$10K", 5: "$100K", 6: "$1M", 7: "$10M", 8: "$100M", 9: "$1B", 10: "$10B", 11: "$100B"}.get(k, "")
        out.append(f'<line x1="{sx(k):.1f}" x2="{sx(k):.1f}" y1="{t}" y2="{h - b}" class="grid"/>'
                   f'<text x="{sx(k):.1f}" y="{h - b + 18}" text-anchor="middle" class="ax">{lab}</text>')
    for k, lab in ((-4, "0.01%"), (-3, "0.1%"), (-2, "1%"), (-1, "10%"), (0, "100%")):
        out.append(f'<line x1="{lm}" x2="{w - r}" y1="{sy(k):.1f}" y2="{sy(k):.1f}" class="grid"/>'
                   f'<text x="{lm - 8}" y="{sy(k) + 4:.1f}" text-anchor="end" class="ax">{lab}</text>')
    for (_, _, hl), xv, yv in sorted(zip(pts, xs, ys, strict=True), key=lambda z: z[0][2]):
        out.append(f'<circle cx="{sx(xv):.1f}" cy="{sy(yv):.1f}" r="{3.2 if hl else 2}" class="{"pt hl" if hl else "pt"}"/>')
    out.append(f'<text x="{(lm + w - r) / 2}" y="{h - 6}" text-anchor="middle" class="ax">money at stake (log scale) →'
               f'</text>'
               f'<text x="14" y="{(t + h - b) / 2}" text-anchor="middle" class="ax" '
               f'transform="rotate(-90 14 {(t + h - b) / 2})">chance of a termination next year →</text></svg>')
    return "".join(out)


def svg_grouped(groups: list[tuple[str, list[tuple[str, list[float], str]]]], w: int = 760) -> str:
    """Vertical bars per group; each bar may hold several runs (drawn as dots on the bar). values in [0,1]."""
    lm, t, b, h = 40, 44, 54, 324
    gw = (w - lm - 10) / len(groups)
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" class="chart">']
    x = lm
    for name, _, var in groups[0][1]:
        out.append(f'<rect x="{x}" y="4" width="12" height="12" fill="var({var})"/>'
                   f'<text x="{x + 18}" y="14" class="lg">{esc(name)}</text>')
        x += 40 + 8 * len(name)
    sy = lambda v: h - b - v * (h - t - b)  # noqa: E731
    for k in (0, 0.25, 0.5, 0.75, 1):
        out.append(f'<line x1="{lm}" x2="{w - 10}" y1="{sy(k):.1f}" y2="{sy(k):.1f}" class="grid"/>'
                   f'<text x="{lm - 6}" y="{sy(k) + 4:.1f}" text-anchor="end" class="ax">{int(k * 100)}%</text>')
    for gi, (gname, bars) in enumerate(groups):
        bw = (gw - 30) / len(bars)
        gx = lm + gi * gw + 15
        for bi, (_, vals, var) in enumerate(bars):
            x = gx + bi * bw
            m = sum(vals) / len(vals)
            out.append(f'<rect x="{x + 3:.1f}" y="{sy(m):.1f}" width="{bw - 6:.1f}" height="{sy(0) - sy(m):.1f}" '
                       f'fill="var({var})" opacity="0.9"/>')
            for v in vals if len(vals) > 1 else []:
                out.append(f'<circle cx="{x + bw / 2:.1f}" cy="{sy(v):.1f}" r="3.5" class="rundot"/>')
            out.append(f'<text x="{x + bw / 2:.1f}" y="{sy(max(vals)) - 10:.1f}" text-anchor="middle" '
                       f'class="val">{m * 100:.0f}%{" mean" if len(vals) > 1 else ""}</text>')
        out.append(f'<text x="{gx + (gw - 30) / 2:.1f}" y="{h - b + 20}" text-anchor="middle" class="lb">{esc(gname)}</text>')
    out.append("</svg>")
    return "".join(out)


# ------------------------------------------------------------------ content
def logged_answer(qid_prefix: str) -> dict:
    f = sorted(glob.glob(str(LOGS / "agent_runs" / "eval_*.jsonl")))[-1]
    for line in open(f).read().splitlines():  # noqa: SIM115
        r = json.loads(line)
        if r["tags"]["qid"].startswith(qid_prefix):
            return r
    raise KeyError(qid_prefix)


def guard_example() -> tuple[dict, dict] | None:
    def load(name: str) -> dict:
        p = LOGS / "agent_runs" / name
        return {json.loads(x)["tags"]["qid"]: json.loads(x) for x in p.read_text().splitlines() if x.strip()}
    base, guard = load("cmp_challenge_haiku_tools_r1.jsonl"), load("cmp_challenge_haiku_tools_guard_r1.jsonl")
    for q in sorted(base):
        if q.startswith("u_near_miss") and q in guard:
            b, g = base[q].get("final_json") or {}, guard[q].get("final_json") or {}
            if b.get("answerable") is True and g.get("answerable") is False:
                return base[q], guard[q]
    return None


def prose(run: dict, n: int = 900) -> str:
    from supplier_graph.agent.agent import JSON_BLOCK
    t = JSON_BLOCK.sub("", run["final_text"]).strip()
    t = esc(t[:n] + ("…" if len(t) > n else ""))
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"\[(award|supplier|office|parent|program|country):([^\]]+)\]", r'<span class="cite">\1:\2</span>', t)
    return t.replace("\n", "<br>")


def build() -> str:
    gs, er, ho, neo = J("graph_summary.json"), J("er_metrics.json"), J("er_holdout.json"), J("neo4j_parity.json")
    bt, cmp_, gd = J("risk_backtest.json"), J("comparison.json")["cells"], J("guard_eval.json")
    jv, js, ev = J("judge_validation.json"), J("judge_validation_subtle.json"), J("eval_results.json")
    sc = gs["scope"]
    tx_rows = bt["source"]["files"]["transactions"]["rows"]
    spent = costs.total_spent()
    ps = bt["primary_split"]
    adj, adj_x = bt["size_adjusted_flag_effects"], bt["sensitivity_excluding_cluster"]["size_adjusted_flag_effects"]
    ll = er["learned_linkage"]
    hm = ho["matchers"]
    v1, best = hm["rule, name blocking only (v1)"], hm["learned + facility blocking"]
    opd, ops = ho["operating_point"]["default"]["confirm"], ho["operating_point"]["selected_point"]["confirm"]
    opsel = ho["operating_point"]["selected_point"]

    con = connect(read_only=True)
    pts = con.execute("""SELECT prime_obligations, p_disruption_next_fy, excess_vs_size >= 2 AND prime_obligations >= 1e7
                         FROM supplier_disruption_score WHERE prime_obligations > 0""").fetchall()
    watch = con.execute("""SELECT supplier_id, name, p_disruption_next_fy, excess_vs_size, prime_obligations,
                                  n_dependent_offices FROM supplier_disruption_score
                           WHERE excess_vs_size >= 2 AND prime_obligations >= 1e7
                           ORDER BY prime_obligations DESC, supplier_id LIMIT 6""").fetchall()
    con.close()

    def reps(k: str) -> list[dict]:
        return cmp_.get(k, {}).get("reps") or []

    def nm(k: str) -> list[int]:
        part = [p["u_near_miss"]["correct"] for p in cmp_.get(k, {}).get("partial_reps_by_template", [])
                if "u_near_miss" in p]
        return [r["by_template"]["u_near_miss"]["correct"] for r in reps(k)] + part

    ov = ev["overall"]
    ex = logged_answer("hard_office_flagged_blast")
    gx = guard_example()
    cfg_rows = [("Large model + tools", "challenge/opus_tools"), ("Small model + tools", "challenge/haiku_tools"),
                ("Small + tools + fix", "challenge/haiku_tools_guard"), ("Large model, no tools", "challenge/opus_no_tools")]
    groups = []
    for label, k in cfg_rows:
        rs = reps(k)
        groups.append((label, [("real questions answered correctly", [r["answer_correctness"] for r in rs], "--c1"),
                               ("trick questions correctly declined", [r["correct_refusal_rate"] for r in rs], "--c3")]))
    cost_cells = {label: (sum(r["cost_mean_usd"] for r in reps(k)) / len(reps(k)) if reps(k) else None)
                  for label, k in cfg_rows}

    names = {"baseline_size": "Just count their contracts", "baseline_prior_events": "Had a termination last year",
             "current_flags": "My risk flags, as-is", "logistic": "Trained model", "gbm": "Trained model (trees)"}
    auc_rows = [(names[k], v["auc"], *v["auc_ci95"], k == "logistic") for k, v in ps["scores"].items()]
    ap_rows = [(names[k], v["ap"], *v["ap_ci95"], k == "logistic") for k, v in ps["scores"].items()]
    or_rows = []
    for f, lab in (("flag_concentrated", "Concentrated"), ("flag_sole_source", "Sole-source"), ("flag_geo_exposed", "Overseas")):
        or_rows.append((f"{lab}: raw", adj[f]["odds_ratio_raw"], *adj[f]["raw_ci95"], False))
        or_rows.append((f"{lab}: vs same-size firms", adj[f]["odds_ratio_size_adjusted"], *adj[f]["adjusted_ci95"], True))
        or_rows.append((f"{lab}: excl. Japan office", adj_x[f]["odds_ratio_size_adjusted"],
                        *adj_x[f]["adjusted_ci95"], True))
    cl = bt["cluster_offices_event_share"]
    cl_lo, cl_hi = min(c["cluster_share"] for c in cl), max(c["cluster_share"] for c in cl)
    apg = ps["ap_gain_vs_size"]["logistic"]
    aug = ps["auc_gain_vs_size"]["logistic"]
    sc_ = ps["scores"]
    er_plain = {"rule, name blocking only (v1)": "Original matcher", "rule + facility blocking": "Original + address check",
                "learned, name blocking only": "Learned weights", "learned + facility blocking": "Learned + address (shipped)"}
    hk_cost = cost_cells["Small model + tools"]
    op_cost = cost_cells["Large model + tools"]
    ratio = round(op_cost / hk_cost) if op_cost and hk_cost else None
    hg_ch = reps("challenge/haiku_tools_guard")
    nt_ch = reps("challenge/opus_no_tools")

    def details(inner: str) -> str:
        return f'<details><summary>Technical detail (for follow-up questions)</summary><div class="det">{inner}</div></details>'

    slides: list[tuple[str, str]] = []
    slides.append(("title", f"""
<h1>{esc(TITLE)}</h1>
<p class="lede">{esc(TAGLINE)}</p>
<p class="fine">{esc(header_note(feed_in_likelihood()))}</p>
<div class="kpis">
 <div><b>{sc['n_prime_awards']:,}</b><span>contracts ({money(sc['total_prime_obligations'])})</span></div>
 <div><b>{gs['nodes']['Supplier']:,}</b><span>companies</span></div>
 <div><b>{round(ov['answer_correctness'] * ov['n_answerable'])}/{ov['n_answerable']}</b><span>assistant answers correct</span></div>
 <div><b>${spent:.2f}</b><span>total AI cost to build and test</span></div>
</div>
<p class="fine">Use ← → or PageUp/PageDown to move between sections.</p>"""))

    slides.append(("overview", f"""
<h2>The project in one minute</h2>
<p>The imagined customer is an analyst who keeps military equipment maintained. Their question: <i>"Which of our contracts
depend on risky suppliers, and what breaks if one of them fails?"</i></p>
<table class="steps">
<tr><th></th><th>What I did</th><th>What I found</th></tr>
<tr><td><b>1. Collect</b></td><td>Downloaded every DoD maintenance and repair contract for 2023-2025 from USAspending.gov
(the government's public spending database), plus subcontracts and {tx_rows:,} contract changes.</td>
<td>{sc['n_prime_awards']:,} contracts worth {money(sc['total_prime_obligations'])}, paid to {gs['nodes']['Supplier']:,} companies.</td></tr>
<tr><td><b>2. Match</b></td><td>The same company is spelled differently across records, and some records lack its ID
number. I built a matcher that decides which records are the same company.</td>
<td>An easy test said it was over 90% accurate. A realistic test said {pct(v1['present_correct'], 0)}. I found why and
raised it to {pct(best['present_correct'], 0)}.</td></tr>
<tr><td><b>3. Connect</b></td><td>Built a map (a "knowledge graph") linking each company to its contracts, its owner, the
offices that pay it and the countries where the work happens. Flagged companies that look risky.</td>
<td>Every question is answered two independent ways, and both always agree.</td></tr>
<tr><td><b>4. Test the flags</b></td><td>Checked whether flagged companies actually had contracts ended early the following
year.</td><td>Not more than their size explains. The flags show <b>how much is at stake</b>, not <b>how likely</b> a problem is.</td></tr>
<tr><td><b>5. Ask</b></td><td>Built an AI assistant (Claude) that can only answer by looking things up in the map, and must
cite a record ID for every fact. Tested it with known-answer and trick questions.</td>
<td>Real questions: answered correctly. Weak spot: company names that are almost right. Fixed in the lookup tool.</td></tr>
</table>
<p class="big">The pattern: each part looked fine on an easy test. A realistic test found the real problem, which I then
fixed or reported.</p>"""))

    rf = gs["risk_feed"]
    fpol = bt["feed_policy"]
    feed_used = "feed_p_escalation" in fpol["adopted"]
    from supplier_graph.ingest.risk_feed import usage_text
    feed_use = usage_text()
    slides.append(("glossary", f"""
<h2>Words on these slides</h2>
<dl class="gloss">
<dt>Contract (prime award)</dt><dd>An agreement the DoD signs directly with a company.</dd>
<dt>Subcontract (subaward)</dt><dd>Part of that work passed on to another company.</dd>
<dt>Supplier / parent company</dt><dd>A company receiving DoD money / the company that owns it.</dd>
<dt>UEI</dt><dd>The government's ID number for a company. When a record has it, matching is easy.</dd>
<dt>Office</dt><dd>The DoD contracting office that signs and pays for the contract.</dd>
<dt>Concentrated</dt><dd>Flag: the supplier gets at least 25% of some office's money.</dd>
<dt>Sole-source</dt><dd>Flag: at least half the supplier's money comes from contracts awarded without competition.</dd>
<dt>Overseas (geo-exposed)</dt><dd>Flag: at least 25% of its work is done abroad, or any of it in a country covered by
the Middle East conflict-risk feed.</dd>
<dt>Conflict-risk feed</dt><dd>Weekly escalation probabilities for 14 Middle East and North Africa countries, from my separate
forecasting project (version {esc(rf['schema_version'])}). Its own evaluation says it <b>{esc(rf['skill_status'].replace('_', ' '))}</b>.
{esc(feed_use)}</dd>
<dt>Blast radius</dt><dd>What a supplier's failure would touch: its contracts, then the offices, programs and prime
contractors attached to them.</dd>
<dt>Termination</dt><dd>The government ending a contract early. The public record's closest stand-in for "something went
wrong".</dd>
<dt>Opus / Haiku</dt><dd>A large and a small Claude AI model. The small one is far cheaper.</dd>
<dt>Knowledge graph</dt><dd>Data stored as things (companies, contracts, offices) and the links between them, so "what
connects to what" questions are easy.</dd>
</dl>"""))

    slides.append(("data", f"""
<h2>Step 1: The data</h2>
<p>Source: USAspending.gov. Scope: DoD contracts in product category J (maintenance, repair and rebuilding of equipment),
fiscal years 2023-2025. Every download is saved with what was asked for and when, so every number can be traced back.</p>
<div class="kpis">
 <div><b>{sc['n_prime_awards']:,}</b><span>contracts</span></div>
 <div><b>{sc['n_subawards']:,}</b><span>subcontracts</span></div>
 <div><b>{gs['nodes']['Office']:,}</b><span>paying offices</span></div>
 <div><b>{gs['nodes']['ParentCompany']:,}</b><span>parent companies</span></div>
</div>
<ul>
 <li><b>Subcontracts are rarely reported.</b> Only {pct(sc['prime_awards_with_subawards_in_scope'] / sc['n_prime_awards'])}
     of contracts list any, so hidden dependencies are undercounted.</li>
 <li><b>The conflict-risk feed is {'used in the likelihood score' if feed_used else 'background only'}.</b> Its model
     reports skill status "{esc(rf['skill_status'])}". {'The backtest showed it improves the likelihood score.' if feed_used else
     "But it only has forecasts from " + esc(fpol['feed']['week_start_min']) + " on, so there is no way to test it against the 2023-2025 terminations; until then its numbers are labelled as context in the app and in the assistant's answers."}</li>
 <li><b>The flags cover almost everyone.</b> {pct(sc['obligation_share_to_flagged_suppliers'], 0)} of the money goes to a
     supplier with at least one flag. A warning that fires on nearly everything can't single out who is about to fail.
     That was the first hint of the Step 4 finding.</li>
</ul>
{details(f"Exact share to flagged suppliers {pct(sc['obligation_share_to_flagged_suppliers'], 2)} ({gs['suppliers_any_flag']:,} of "
         f"{gs['nodes']['Supplier']:,} suppliers). {sc['prime_awards_with_subawards_in_scope']:,} of {sc['n_prime_awards']:,} "
         f"contracts have reported subawards. Program codes on {sc['awards_with_program_code']:,} awards "
         f"({pct(sc['program_code_obligation_share'])} of obligations); {sc['awards_missing_pop_country']:,} awards lack a "
         f"place-of-performance country. Also loaded: {tx_rows:,} contract transactions (FY2022-FY2025), SAM.gov entity "
         "records, and a Middle East conflict-risk feed from a separate project.")}"""))

    slides.append(("er", f"""
<h2>Step 2: Working out which records are the same company</h2>
<p>"Boeing Co" and "The Boeing Company" are one company. So are a firm and its new name after an acquisition. Records with
the government ID are easy to group; for records without it, a matcher compares names and addresses.</p>
<div class="two">
<div>
<p><b>Easy test:</b> show the matcher two records and ask "same company?" It scored over 90%.</p>
<p><b>Realistic test:</b> take {ho['n_present']} real records of companies that were renamed, hide their ID, and ask the
system to find the right company among {ho['index_size']:,}. The original matcher got only <b>{pct(v1['present_correct'], 0)}</b>.</p>
<p><b>Why:</b> to save time, the matcher only compared records with similar names. A renamed company has a different name
but usually the same address, so it was never compared.</p>
<p><b>Fix:</b> also compare records at the same address, and learn from the data how much each clue (name, address, ZIP,
city, state) should count. Result: <b>{pct(best['present_correct'], 0)}</b>, with fewer wrong matches.</p>
<p><b>Then fewer false matches:</b> companies that aren't in the list at all were sometimes matched to a neighbour at
the same address. Requiring a clearer winner, chosen on half the records and checked on the other half, cut those
false matches from {pct(opd['absent_false_link'])} to {pct(ops['absent_false_link'])}, for
{(opd['present_correct'] - ops['present_correct']) * 100:.0f} fewer points of correct matches.</p>
</div>
<div>{svg_stacked_bars([(er_plain.get(k, k), [v['present_correct'], v['present_wrong_link'], v['present_unresolved']])
                        for k, v in hm.items()],
                       [("right company", "--c1"), ("wrong company", "--c4"), ("not found", "--c0")], w=560)}
<p class="note">Lesson: test the way the system will actually be used.</p></div>
</div>
{details(f"End to end: correct links {pct(v1['present_correct'])} (original) → {pct(best['present_correct'])} (learned + "
         f"address check, dev-tuned threshold). Shipped operating point: threshold {opsel['threshold']:.2f}, margin "
         f"{opsel['margin']:.2f}, chosen on the tuning half to maximise correct − 2 × false merges; on the confirmation half "
         f"absent false links {pct(opd['absent_false_link'])} → {pct(ops['absent_false_link'])}, wrong links "
         f"{pct(opd['present_wrong_link'])} → {pct(ops['present_wrong_link'])}, correct {pct(opd['present_correct'])} → "
         f"{pct(ops['present_correct'])}. "
         f"Pairwise test split, learned weights: precision {ll['identifier']['precision']:.3f} / recall "
         f"{ll['identifier']['recall']:.3f} on identifier-derived pairs; acronym recall "
         f"{er['ablation_v1_no_acronyms']['acronym_recall']:.2f} → {ll['acronym_recall']:.2f}. Labels are automatic: pairs "
         "sharing an ID, look-alike pairs with different IDs, and synthetic misspellings. Learned weights are a logistic "
         "regression on agreement evidence, chosen over hand-set weights on the dev split. Wrong links "
         f"{pct(v1['present_wrong_link'], 2)} → {pct(best['present_wrong_link'], 2)}. Cost: companies not in the index "
         f"falsely linked {pct(v1['absent_false_link'])} → {pct(best['absent_false_link'])}. Disclosure: the address check "
         "was added after diagnosing these same records; it has no tunable setting.")}"""))

    nodes_ok = all(v["equal"] for v in neo["nodes"].values())
    edges_ok = all(v["equal"] for v in neo["edges"].values())
    slides.append(("graph", f"""
<h2>Step 3: A map of who depends on whom</h2>
<div class="two">
<div>
<p>Each company, parent company, contract, office, country and product category is a point on the map. Links say who
got which contract, who subcontracts to whom, who owns whom, who paid, and where the work was done. Every point and link
records which download it came from.</p>
<p>Ten standard questions are pre-written ("which offices depend on this supplier?", "what is its blast radius?" and so
on). <b>Each is computed two independent ways</b>, and the tests fail if the answers ever differ.</p>
<p>I also loaded the map into Neo4j, a standard graph database, and checked it gives exactly the same results.</p>
</div>
<div class="answer"><p class="q">Example question to the assistant: {esc(ex['question'])}</p><p>{prose(ex, 700)}</p>
<p class="fine">A real logged answer. Grey tags are record IDs; the scorer checks each one against what the lookup
returned. {len(ex['tool_calls'])} lookups, ${ex['cost_usd']:.3f}, {ex['latency_s']:.0f} seconds.</p></div>
</div>
{details("Schema: Supplier, ParentCompany, Award, Office, Country, ProductServiceCode nodes; AWARDED_TO, SUBCONTRACTS_TO, "
         "SUBSIDIARY_OF, PERFORMED_IN, AWARDED_BY, CATEGORIZED_AS edges. The two paths are a NetworkX graph traversal and "
         f"DuckDB SQL. Neo4j: node counts {'equal' if nodes_ok else 'NOT equal'} for every label, edge counts "
         f"{'equal' if edges_ok else 'NOT equal'} for every type, and a Cypher two-hop blast radius equal to NetworkX for "
         f"{neo['blast_radius']['n_equal']}/{neo['blast_radius']['n_suppliers']} sampled suppliers.")}"""))

    slides.append(("backtest", f"""
<h2>Step 4: Do my risk flags predict trouble? I checked against what happened</h2>
<p>Public records show when the government ends a contract early (a termination). Using only data up to one year, each
method ranks suppliers by how likely they are to have a termination the <i>next</i> year; then I check against what
actually happened ({ps['n_test']:,} suppliers, {ps['n_test_pos']} had one).</p>
<div class="two">
<div><h3>How well each method ranks everyone (0.5 = coin flip, 1 = perfect)</h3>{svg_dot_whisker(auc_rows, 0.5, 0.85, None, w=520)}</div>
<div><h3>How good the top of the list is (dashed line = random picks)</h3>{svg_dot_whisker(ap_rows, 0, 0.3, ps['test_base_rate'], w=520, ref_label='random')}</div>
</div>
<p><b>Result:</b> simply counting a supplier's contracts ({sc_['baseline_size']['auc']:.2f}) beats my flags
({sc_['current_flags']['auc']:.2f}). Big suppliers have more contracts, so more chances for one to be ended. A trained
model ({sc_['logistic']['auc']:.2f}) barely beats counting overall, but is clearly better at the top of the list.</p>
<p class="fine">Lines are 95% ranges: if two overlap a lot, the difference may be luck.</p>
{details(f"Outcome: any termination (default, cause, convenience) or cancellation in the next fiscal year. Train on FY2022-FY2023 "
         f"features, test on FY2024 features → FY2025 outcomes. Left chart ROC-AUC, right chart average precision, with "
         f"1,000-resample bootstrap CIs. Model gain over size: AUC {aug['mean']:+.3f} (CI {aug['ci'][0]:+.3f} to "
         f"{aug['ci'][1]:+.3f}, not significant); average precision {apg['mean']:+.3f} (CI {apg['ci'][0]:+.3f} to "
         f"{apg['ci'][1]:+.3f}, significant). An earlier split and a run without the dominant office agree.")}"""))

    # The same four lines as the app's Likelihood x impact tab; every number is a reports/HEADLINE.md value (checked by
    # tests/test_headline.py). Detail: docs/model_card_likelihood.md and reports/risk_backtest.md.
    lk_points = "".join(
        f"<li><b>{label}:</b> {esc(text).replace('`excess_vs_size`', '<code>excess_vs_size</code>')}</li>"
        for label, text in likelihood_points(load_headline()))
    slides.append(("likelihood-model", f"""
<h2>How the likelihood score is predicted</h2>
<ul>{lk_points}</ul>
<p class="fine">Details: <a href="{MODEL_CARD_URL}">model card</a>, <a href="{BACKTEST_URL}">backtest report</a>.</p>"""))

    slides.append(("flags", f"""
<h2>Finding: the flags show what's at stake, not who will fail</h2>
<p>How much more often flagged suppliers had a termination than unflagged ones. <b>1 = no difference</b>; 2 = twice as
often; 0.5 = half as often. "vs same-size firms" compares suppliers with a similar number of contracts.</p>
{svg_dot_whisker(or_rows, 0.25, 4.5, 1.0, log=True, ref_label='no difference')}
<ul>
 <li><b>Concentrated</b> looked 3× riskier, but only because those suppliers are bigger. Against same-size firms, no difference.</li>
 <li><b>Sole-source</b> suppliers were <b>less</b> likely to have a termination: long-running maintenance deals with the
     original manufacturer are stable.</li>
 <li><b>Overseas</b>: the whole effect comes from one Navy office in Japan, which ends many contracts for its own
     administrative reasons ({pct(cl_lo, 0)}-{pct(cl_hi, 0)} of each year's terminations). Without it, no difference.</li>
</ul>
<p class="big">So what: a flag tells you how bad a failure would be. "How likely" needs its own separate score.</p>"""))

    wl = "".join(f"<tr><td>{esc(n)}</td><td>{p * 100:.1f}%</td><td>{x:.1f}×</td><td>{money(o)}</td><td>{d}</td></tr>"
                 for _, n, p, x, o, d in watch)
    slides.append(("triage", f"""
<h2>What the tool shows instead: two separate scores</h2>
<div class="two">
<div>{svg_scatter([(o, p, bool(h)) for o, p, h in pts], w=560, h=360)}</div>
<div>
<p>Each dot is one supplier, scored for FY{bt['outlook']['outlook_fy']}. <b>Further right</b> = more money at stake.
<b>Higher</b> = more likely to have a termination next year.</p>
<p><b>Red dots</b> ({bt['outlook']['n_excess_ge_2_and_obligations_ge_10m']} suppliers) have at least $10M at stake and are at
least twice as likely as their size alone would suggest: the ones worth a closer look.</p>
<table><tr><th>supplier</th><th>chance</th><th>vs size</th><th>at stake</th><th>offices</th></tr>{wl}</table>
<p class="fine">"Chance" is the chance of any termination or cancellation, not of the company failing. Largest six red dots shown.</p>
</div></div>"""))

    slides.append(("agent-eval", f"""
<h2>Step 5: The AI assistant, and testing it fairly</h2>
<p>You type a question. Claude can't search freely: it can only use four lookup tools (find a company by name, run one of
the ten pre-written questions, get a country's risk, get a contract's details). Every fact must cite a record ID, or it must
say the data can't answer.</p>
<p><b>Testing:</b> questions whose correct answers are computed from the data, plus trick questions about companies that
don't exist. The first test was too easy ({round(ov['answer_correctness'] * ov['n_answerable'])}/{ov['n_answerable']}
right, {round(ov['correct_refusal_rate'] * ov['n_unanswerable'])}/{ov['n_unanswerable']} tricks declined), so I wrote a harder
one with comparisons, rankings and look-alike names.</p>
{svg_grouped(groups)}
<ul>
 <li><b>Without the lookup tools</b>, Claude answers nothing ({nt_ch[0]['answerable_correct'] if nt_ch else '-'}). It declines
     rather than making things up, but it can't help. The map is what makes it useful.</li>
 <li><b>With the tools, even the small model</b> gets every real question right, in all three runs, at about
     {f"1/{ratio}" if ratio else "a fraction"} of the large model's cost.</li>
 <li>The small model's mistakes were all on trick questions. The next slide shows which ones.</li>
</ul>
<p class="fine">Black dots are separate runs (small model 3 times; large model once on the full set, because two more
full runs would break the $10 budget, and 3 times on the look-alike tricks). Cost per question:
{' · '.join(f"{k} ${v:.4f}" for k, v in cost_cells.items() if v is not None)}.</p>"""))

    gtxt = ""
    if gx:
        b, g = gx
        gtxt = f"""<div class="two"><div class="answer bad"><p class="q">Small model, before the fix</p><p>{prose(b, 420)}</p></div>
<div class="answer good"><p class="q">Small model, after the fix</p><p>{prose(g, 420)}</p></div></div>"""
    slides.append(("guard", f"""
<h2>The weak spot, and the fix: look-alike company names</h2>
<p><b>The trick:</b> ask about a company whose name is a real supplier's name with one word changed. The name lookup finds
the real company as the closest match, and the small model often answered about it anyway, as if it were the company asked about.</p>
<p><b>The fix:</b> the lookup now lists any words in the question that the matched name doesn't contain, and says not to
swap companies silently. I changed the tool, not the instructions to the AI, so it works for any model.</p>
<p class="big">Look-alike tricks caught (of 8): small model {', '.join(map(str, nm('challenge/haiku_tools')))} →
with the fix {', '.join(map(str, nm('challenge/haiku_tools_guard')))}. Large model: {', '.join(map(str, nm('challenge/opus_tools')))}.
Real questions: still {', '.join(r['answerable_correct'] for r in hg_ch) if hg_ch else '-'}.</p>
{gtxt}
{details(f"Offline checks: the check fires on {gd['agent_questions']['u_near_miss']['flagged']}/"
         f"{gd['agent_questions']['u_near_miss']['n']} look-alike tricks, "
         f"{gd['agent_questions']['hard_noisy_name']['flagged']}/{gd['agent_questions']['hard_noisy_name']['n']} misspelled-but-correct "
         f"names, {pct(gd['synthetic_pos_excluding_acronym']['flag_rate'], 2)} of synthetic same-company pairs, and "
         f"{gd['replay_logged_opus_resolver_calls']['answerable']['flagged']} of "
         f"{gd['replay_logged_opus_resolver_calls']['answerable']['calls']} logged large-model lookups on real questions. "
         "One trick every model missed is arguably mislabelled; it is still counted as a miss.")}"""))

    slides.append(("trust", f"""
<h2>Why these numbers can be trusted</h2>
<ul>
 <li><b>The right answers are computed, not written by hand.</b> Each one comes from the data and is re-checked a second,
     independent way. Questions where the two disagree are thrown out.</li>
 <li><b>Answers are graded by code, not by opinion.</b> An AI grader is used only for writing quality, and I tested it
     first by planting errors in real answers: it caught {jv['agreement'] * 100:.0f}% of {jv['n_cases']} obvious ones and
     {js['agreement'] * 100:.0f}% of {js['n_cases']} subtle ones (a count off by one, a percentage off by 5 points).</li>
 <li><b>No hand-made labels.</b> Test labels come from ID numbers, deliberate misspellings and the government's own
     termination codes.</li>
 <li><b>Changes made after seeing results are disclosed.</b> The harder question set was revised once after a trial run,
     and the address check was added after studying failures. Both are reported with before and after numbers.</li>
 <li><b>Automatic checks.</b> The test suite fails if any of these results gets worse.</li>
</ul>"""))

    slides.append(("limits", """
<h2>Limits, and what I'd do with a real customer</h2>
<div class="two"><div><h3>Limits</h3><ul>
 <li>Public data only: subcontracts are rarely reported, and there is no performance, financial or debarment data.</li>
 <li>"Termination" is a stand-in for trouble. Most are for the government's convenience, not the supplier's fault, and
     one office accounts for a large share.</li>
 <li>The question sets are small and built from templates. The large model ran the full sets once (two more runs would
     break the $10 budget); only its look-alike tricks were repeated (7 of 8 caught each time).</li>
 <li>The country-risk feed is months old and barely varies between countries.</li>
</ul></div><div><h3>Next, with a customer</h3><ul>
 <li>Sit with the analyst: which decisions would the two-score view change? Set thresholds to fit their work.</li>
 <li>Add performance ratings, debarment lists and financial data, and re-test against supplier-fault outcomes.</li>
 <li>Replace template questions with the analyst's real questions, keeping the two-way answer check.</li>
 <li>Run on a production graph database, and track how often the name check fires.</li>
</ul></div></div>"""))

    slides.append(("reproduce", f"""
<h2>Reproduce it</h2>
<p>Every number on these slides is read from a report produced by one of these commands.</p>
<pre>make install
make data graph          # download (cached), match companies (+ realistic test), build the map
make backtest            # contract changes + the "do flags predict trouble?" test + FY2026 scores
make challenge           # the harder question set (no AI cost)
make eval-offline compare-offline guard-eval   # re-score the saved assistant runs (no AI cost)
make test                # automatic checks
make site                # this page
make app                 # interactive demo</pre>
<p>Total AI cost for everything above: <b>${spent:.2f}</b> (cap $10).
Built {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC.</p>"""))

    body = "\n".join(f'<section class="slide" id="{sid}"><div class="inner">{content}</div></section>'
                     for sid, content in slides)
    return PAGE.replace("{{BODY}}", body).replace("{{N}}", str(len(slides)))


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Supplier Risk Graph</title>
<style>
:root{--bg:#fbfaf7;--fg:#1d1f23;--mu:#5d636e;--card:#ffffff;--line:#e3e1db;--acc:#1f5fbf;
--c0:#c9ccd2;--c1:#2f6fd0;--c3:#2a9d77;--c4:#d1495b;--cite:#eef0f3}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#15171b;--fg:#e8e9ec;--mu:#a1a7b3;
--card:#1e2126;--line:#30343b;--acc:#7aa7ff;--c0:#4a505a;--c1:#5b8ff0;--c3:#3fbf93;--c4:#ef6b7c;--cite:#2a2e35}}
:root[data-theme="dark"]{--bg:#15171b;--fg:#e8e9ec;--mu:#a1a7b3;--card:#1e2126;--line:#30343b;--acc:#7aa7ff;
--c0:#4a505a;--c1:#5b8ff0;--c3:#3fbf93;--c4:#ef6b7c;--cite:#2a2e35}
*{box-sizing:border-box}html{scroll-snap-type:y proximity}
body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif}
.slide{min-height:100vh;display:flex;align-items:center;scroll-snap-align:start;border-bottom:1px solid var(--line)}
.inner{max-width:1080px;margin:0 auto;padding:56px 32px;width:100%}
h1{font-size:52px;line-height:1.05;margin:.2em 0 .3em;letter-spacing:-.02em}
h2{font-size:32px;margin:0 0 .6em;letter-spacing:-.01em}h3{font-size:15px;color:var(--mu);margin:.2em 0 .4em;font-weight:600}
.kicker{color:var(--acc);font-weight:600;text-transform:uppercase;letter-spacing:.08em;font-size:13px}
.lede{font-size:21px;color:var(--mu);max-width:820px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:28px 0}
.kpis div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.kpis b{display:block;font-size:30px;letter-spacing:-.01em}.kpis span{color:var(--mu);font-size:14px}
.flow{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:22px 0}
.flow div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;font-size:14px}
.flow b{display:block;color:var(--acc);margin-bottom:4px;font-size:16px}
blockquote{font-size:22px;margin:0 0 10px;padding-left:18px;border-left:4px solid var(--acc)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:28px;align-items:start}
.answer{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;font-size:14.5px}
.answer.bad{border-color:var(--c4)}.answer.good{border-color:var(--c3)}
.answer .q{font-weight:600;margin-top:0}.cite{background:var(--cite);border-radius:4px;padding:0 4px;font:12px ui-monospace,Menlo,monospace;color:var(--mu)}
.note{color:var(--mu);font-size:15px}.fine{color:var(--mu);font-size:13px}.big{font-size:20px;font-weight:600}
table{border-collapse:collapse;font-size:14px;width:100%}td,th{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--mu);font-weight:600}
details{margin-top:18px;color:var(--mu);font-size:14px}summary{cursor:pointer;font-weight:600}
.det{padding:8px 0 0 16px;border-left:3px solid var(--line);margin-top:6px}
.steps td{vertical-align:top;font-size:15px}.steps td:first-child{white-space:nowrap}
.gloss{display:grid;grid-template-columns:220px 1fr;gap:8px 18px;margin:0}.gloss dt{font-weight:600}.gloss dd{margin:0;color:var(--mu)}
pre{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;overflow-x:auto;font-size:14px}
code{font:14px ui-monospace,Menlo,monospace}
.chart{width:100%;height:auto;margin:6px 0}.chart text{fill:var(--fg)}.chart .ax,.chart .lg{fill:var(--mu);font-size:12px}
.chart .lb{font-size:13px}.chart .in{fill:#fff;font-size:12px;font-weight:600}.chart .val{font-size:12px;fill:var(--mu)}
.chart .grid{stroke:var(--line)}.chart .ref{stroke:var(--mu);stroke-dasharray:4 4}
.chart .wh{stroke-width:3}.chart .wh.hl{stroke:var(--c1)}.chart .wh.mu{stroke:var(--c0)}
.chart .dot.hl{fill:var(--c1)}.chart .dot.mu{fill:var(--c0)}
.chart .pt{fill:var(--c0);opacity:.55}.chart .pt.hl{fill:var(--c4);opacity:.95}.chart .rundot{fill:var(--fg);opacity:.75}
#nav{position:fixed;right:16px;bottom:14px;color:var(--mu);font-size:13px;background:var(--card);border:1px solid var(--line);
border-radius:20px;padding:4px 12px}
#theme{position:fixed;right:16px;top:14px;background:var(--card);color:var(--mu);border:1px solid var(--line);border-radius:20px;
padding:4px 12px;font-size:13px;cursor:pointer}
@media (max-width:820px){.two,.flow,.kpis,.gloss{grid-template-columns:1fr}.inner{padding:40px 16px}h1{font-size:38px}h2{font-size:26px}}
@media print{.slide{min-height:auto;page-break-after:always}#nav,#theme{display:none}}
</style></head><body>
<button id="theme" type="button">theme</button>
{{BODY}}
<div id="nav"><span id="pos">1</span> / {{N}}</div>
<script>
const S=[...document.querySelectorAll('.slide')];
function cur(){let b=0,d=1e9;S.forEach((s,i)=>{const t=Math.abs(s.getBoundingClientRect().top);if(t<d){d=t;b=i}});return b}
function go(i){i=Math.max(0,Math.min(S.length-1,i));S[i].scrollIntoView({behavior:'smooth'})}
addEventListener('keydown',e=>{if(['ArrowRight','ArrowDown','PageDown',' '].includes(e.key)){e.preventDefault();go(cur()+1)}
if(['ArrowLeft','ArrowUp','PageUp'].includes(e.key)){e.preventDefault();go(cur()-1)}});
addEventListener('scroll',()=>{document.getElementById('pos').textContent=cur()+1},{passive:true});
document.getElementById('theme').onclick=()=>{const r=document.documentElement;
r.dataset.theme=(r.dataset.theme==='dark'||(!r.dataset.theme&&matchMedia('(prefers-color-scheme: dark)').matches))?'light':'dark'};
</script></body></html>"""


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(build())
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
