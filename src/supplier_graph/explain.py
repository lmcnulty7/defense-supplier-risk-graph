"""Short explanations of the project's models, shared by the Streamlit app and the results site.

Every number comes from reports/headline.json (`make headline`) via `headline.load()`; tests/test_headline.py renders
the app and the site and checks that each number in this text is a headline value. Detail lives in
docs/model_card_likelihood.md and reports/risk_backtest.md.
"""

from __future__ import annotations

REPO_URL = "https://github.com/lmcnulty7/defense-supplier-risk-graph/blob/main"
MODEL_CARD_URL = f"{REPO_URL}/docs/model_card_likelihood.md"
BACKTEST_URL = f"{REPO_URL}/reports/risk_backtest.md"
REPORTS = [("Agent evaluation", "reports/eval_report.md"), ("Agent model comparison", "reports/comparison.md"),
           ("Supplier matching", "reports/entity_resolution.md"), ("Termination likelihood", "reports/risk_backtest.md")]

# ---------------------------------------------------------------- header (app and the site's first slide)
TITLE = "Defense supplier-risk graph"
TAGLINE = "Find which defense contracts depend on risky suppliers, and what breaks if one fails."


def header_note(feed_used: bool) -> str:
    return ("Public USAspending data (DoD maintenance contracts, FY2023 to FY2025) · portfolio project, fictional "
            "customer, not affiliated with the DoD · "
            + ("country risk feeds the likelihood score" if feed_used else "country risk is context only"))


def likelihood_points(h: dict[str, str]) -> list[tuple[str, str]]:
    """The four-line summary of the termination-likelihood model: (label, text); `excess_vs_size` is marked as code
    with backticks."""
    return [
        ("What it predicts", "the chance a supplier has a contract terminated or cancelled next fiscal year (a proxy "
                             "for disruption, not proof of supplier failure)."),
        ("How", "a logistic regression on that year's contract activity (size, churn, past terminations, "
                f"competition), {h['lk_split']}."),
        ("How well", f"precision in the top 5% riskiest is {h['top5_model']}, against a {h['lk_base_rate']} base "
                     f"rate, {h['top5_size']} for size alone and {h['top5_flags']} for the three flags."),
        ("What it means", "most predictable risk is size, so the useful signal is `excess_vs_size` (riskier than size "
                          "explains); the flags show impact, not likelihood, which is why the chart separates them."),
    ]


def likelihood_md(h: dict[str, str]) -> str:
    """The summary as Markdown, for the Likelihood x impact tab."""
    return "\n".join(f"- **{label}:** {text}" for label, text in likelihood_points(h))


def models_md(h: dict[str, str]) -> str:
    """One line per model for the About the data tab: what it predicts and its headline number."""
    shipped_correct, shipped_wrong = h["er_op_correct"].split(" → ")[1], h["er_op_wrong"].split(" → ")[1]
    return "\n".join([
        "- **Supplier matching:** whether two contract records are the same company; the shipped setting links "
        f"{shipped_correct} of renamed held-out records correctly and {shipped_wrong} wrongly. A looser setting gets "
        f"{h['er_blocking_correct']} correct but {h['er_blocking_wrong']} wrong, so I chose fewer wrong links.",
        "- **Termination likelihood:** the chance of a contract termination or cancellation next fiscal year; "
        f"precision in the top 5% {h['top5_model']} against a {h['lk_base_rate']} base rate.",
        "- **Agent:** cited answers to supplier-dependency questions, or a statement that the data can't answer; Opus "
        f"answered {h['v1_opus']} answerable questions correctly and declined {h['v1_opus_ref']} trick questions.",
    ])


def feed_line(h: dict[str, str], skill_short: str, usage_short: str) -> str:
    return (f"Country risk from my MENA forecaster (UCDP-based, {h['feed_snapshot']} snapshot). {skill_short}; "
            f"{usage_short}.")


def about_md(h: dict[str, str], n_awards: int, n_subawards: int, n_suppliers: int) -> str:
    """Top of the About the data tab (above the feed details, models and evaluation links)."""
    return f"""
**Scope.** DoD maintenance and repair contracts (category J) and their subcontracts, FY2023 to FY2025. Dollar figures
are lifetime totals.

**Row counts.** {n_awards:,} prime awards, {n_subawards:,} subawards, {n_suppliers:,} suppliers.

**Provenance.** Pulled from USAspending on {h['pulled']}. Every record in the graph traces back to the source request
it came from.
"""


def caveats_md(h: dict[str, str]) -> str:
    return (f"**Caveats.** The termination label is a proxy ({h['conv_share']} are terminations for convenience); only "
            f"{h['sub_coverage']} of contracts report subcontracts, so indirect dependencies are undercounted; one Navy "
            f"office in Japan accounts for {h['cluster_share']} of each year's terminations.")


def evaluation_md() -> str:
    return "**Evaluation.** " + " · ".join(f"[{label}]({REPO_URL}/{path})" for label, path in REPORTS)
