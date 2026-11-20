"""Likelihood x impact view for the Streamlit app: the filter and the Altair chart.

The chart draws every scored supplier as a light gray dot and the suppliers that match both filters in color on top,
with a dashed vertical line at the min-obligations filter. Altair renders through Streamlit's bundled Vega-Lite, so
the chart needs no CDN.
"""

from __future__ import annotations

import altair as alt
import pandas as pd

X_FLOOR = 1e4  # log scale: obligations below $10K (including zero or negative) are drawn at $10K
X_TICKS = [1e4, 1e6, 1e8, 1e10]
X_LABELS = ("datum.value >= 1e9 ? '$' + datum.value / 1e9 + 'B' : "
            "datum.value >= 1e6 ? '$' + datum.value / 1e6 + 'M' : '$' + datum.value / 1e3 + 'K'")
Y_TITLE = "P(termination next FY)"
BASE_NAME, HIGHLIGHT_NAME, RULE_NAME = "all_suppliers", "matching", "min_obligations_rule"


def filter_outlook(out: pd.DataFrame, min_obligations_m: float, min_excess: float) -> pd.DataFrame:
    """Suppliers with at least `min_obligations_m` $M at stake and likelihood at least `min_excess` x size-only."""
    return out[(out.prime_obligations >= min_obligations_m * 1e6) & (out.excess_vs_size >= min_excess)]


def _points(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "name": df.name,
        "p_disruption_next_fy": df.p_disruption_next_fy,
        "prime_obligations_m": df.prime_obligations / 1e6,
        "excess_vs_size": df.excess_vs_size,
        "obligations_plot": df.prime_obligations.clip(lower=X_FLOOR),
    })


def outlook_chart(out: pd.DataFrame, view: pd.DataFrame, min_obligations_m: float) -> alt.LayerChart:
    """All suppliers in gray, `view` highlighted on top, dashed rule at the min-obligations filter."""
    x = alt.X("obligations_plot:Q", title="Prime obligations at stake (log scale)",
              scale=alt.Scale(type="log", domain=[X_FLOOR, 1e11], clamp=True),
              axis=alt.Axis(values=X_TICKS, labelExpr=X_LABELS))
    y = alt.Y("p_disruption_next_fy:Q", title=Y_TITLE, scale=alt.Scale(domain=[0, 1]))
    tooltip = [alt.Tooltip("name:N", title="Supplier"),
               alt.Tooltip("p_disruption_next_fy:Q", title="p_disruption_next_fy", format=".3f"),
               alt.Tooltip("prime_obligations_m:Q", title="Prime obligations ($M)", format=",.1f"),
               alt.Tooltip("excess_vs_size:Q", title="excess_vs_size", format=".2f")]
    base = (alt.Chart(_points(out), name=BASE_NAME).mark_circle(size=28, color="#bdbdbd", opacity=0.45)
            .encode(x=x, y=y, tooltip=tooltip))
    highlight = (alt.Chart(_points(view), name=HIGHLIGHT_NAME).mark_circle(size=60, opacity=0.9)
                 .encode(x=x, y=y, tooltip=tooltip))
    layers: list[alt.Chart] = [base, highlight]
    if min_obligations_m * 1e6 > X_FLOOR:
        rule = pd.DataFrame({"obligations_plot": [min_obligations_m * 1e6]})
        layers.append(alt.Chart(rule, name=RULE_NAME).mark_rule(strokeDash=[6, 4], color="#666666")
                      .encode(x="obligations_plot:Q"))
    return alt.layer(*layers).properties(height=340)
