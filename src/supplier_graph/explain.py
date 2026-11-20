"""Short explanations of the project's models, shared by the Streamlit app and the results site.

Every number comes from reports/headline.json (`make headline`) via `headline.load()`; tests/test_headline.py renders
the app and the site and checks that each number in this text is a headline value. Detail lives in
docs/model_card_likelihood.md and reports/risk_backtest.md.
"""

from __future__ import annotations

REPO_URL = "https://github.com/lmcnulty7/defense-supplier-risk-graph/blob/main"
MODEL_CARD_URL = f"{REPO_URL}/docs/model_card_likelihood.md"
BACKTEST_URL = f"{REPO_URL}/reports/risk_backtest.md"


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
    return "\n".join([
        "- **Supplier matching:** whether two contract records are the same company; end-to-end correct links on "
        f"renamed records {h['er_v1_correct']} (first matcher) → {h['er_blocking_correct']}.",
        "- **Termination likelihood:** the chance of a contract termination or cancellation next fiscal year; "
        f"precision in the top 5% {h['top5_model']} against a {h['lk_base_rate']} base rate.",
        "- **Agent:** cited answers to supplier-dependency questions, or a statement that the data can't answer; Opus "
        f"answered {h['v1_opus']} answerable questions correctly and declined {h['v1_opus_ref']} trick questions.",
    ])
