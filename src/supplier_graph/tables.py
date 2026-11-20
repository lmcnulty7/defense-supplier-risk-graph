"""Readable tables for the Streamlit app: plain column labels (the variable name is the hover help), dollars in
$M with thousands separators, shares as percentages with one decimal, counts with thousands separators, and the
three risk flags combined into one text column. The data keeps its variable names, so CSV exports use them."""

from __future__ import annotations

import pandas as pd

# variable -> (label, kind); kinds: musd (dollars shown as $M), share (0-1 shown as %), prob (0-1 shown as %),
# count, ratio, text, list
SPEC: dict[str, tuple[str, str]] = {
    "name": ("Supplier", "text"),
    "state": ("State", "text"),
    "prime_obligations": ("Prime obligations ($M)", "musd"),
    "sole_source_share": ("Sole-source share", "share"),
    "max_office_share": ("Top office's reliance on supplier", "share"),
    "top_office": ("Top office", "text"),
    "n_dependent_offices": ("Offices depending on supplier", "count"),
    "n_subawards_received": ("Subcontracts received", "count"),
    "foreign_share": ("Foreign work share", "share"),
    "flags": ("Flags", "text"),
    "supplier_id": ("UEI", "text"),
    "p_disruption_next_fy": ("P(termination next FY)", "prob"),
    "excess_vs_size": ("Likelihood vs size-only prediction", "ratio"),
    "office": ("Office", "text"),
    "n_awards": ("Awards", "count"),
    "obligations": ("Obligations at stake ($M)", "musd"),
    "direct_obligations": ("Direct obligations ($M)", "musd"),
    "n_direct_awards": ("Direct awards", "count"),
    "sub_amount": ("Subcontract amount received ($M)", "musd"),
    "n_indirect_awards": ("Awards via subcontracts", "count"),
    "example_award_ids": ("Example awards", "list"),
    "psc_codes": ("Service codes", "list"),
    "n_alternatives_at_office": ("Alternatives already serving this office", "count"),
    "n_alternatives": ("Alternatives doing this work anywhere", "count"),
    "alternatives": ("Top alternatives", "text"),
    "share": ("Share of office spending", "share"),
    "sole_source_obligations": ("Sole-source obligations ($M)", "musd"),
}
FLAGS = (("flag_concentrated", "Concentrated"), ("flag_sole_source", "Sole-source"), ("flag_geo_exposed", "Geo-exposed"))

RISK_COLUMNS = ["name", "state", "prime_obligations", "sole_source_share", "max_office_share", "top_office",
                "n_dependent_offices", "n_subawards_received", "foreign_share", "flags", "supplier_id"]
OUTLOOK_COLUMNS = ["name", "p_disruption_next_fy", "excess_vs_size", "prime_obligations", "n_dependent_offices",
                   "flags", "supplier_id"]
BLAST_COLUMNS = ["office", "n_awards", "obligations"]
SCENARIO_COLUMNS = ["office", "obligations", "psc_codes", "n_alternatives_at_office", "n_alternatives", "alternatives"]
OFFICE_SUPPLIER_COLUMNS = ["name", "obligations", "share", "n_awards", "supplier_id"]
SOLE_SOURCE_COLUMNS = ["name", "sole_source_obligations", "sole_source_share", "supplier_id"]
DEPENDENT_COLUMNS = ["office", "direct_obligations", "n_direct_awards", "sub_amount", "n_indirect_awards",
                     "example_award_ids"]


def flags_text(row) -> str:
    return " · ".join(label for col, label in FLAGS if bool(row.get(col)))


def office_text(name, code) -> str:
    """Office name + code (several offices share a name)."""
    if not code or pd.isna(code):
        return ""
    return f"{name} ({code})" if name and not pd.isna(name) else str(code)


def readable(df: pd.DataFrame, columns: list[str]) -> tuple[pd.DataFrame, dict]:
    """(display frame, column_config) for st.dataframe. Adds `flags` / `office` when their inputs are present."""
    import streamlit as st

    d = df.copy()
    if "flags" in columns and "flags" not in d:
        d["flags"] = d.apply(flags_text, axis=1) if len(d) else pd.Series(dtype=str)
    if "office" in columns and "office" not in d:
        d["office"] = [office_text(n, c) for n, c in zip(d["office_name"], d["office_code"], strict=True)]
    config: dict = {}
    for col in columns:
        label, kind = SPEC[col]
        if kind == "musd":
            d[col] = d[col].astype(float) / 1e6
            config[col] = st.column_config.NumberColumn(label, help=col, format="$%,.1fM")
        elif kind in ("share", "prob"):
            d[col] = d[col].astype(float) * 100
            if col == "foreign_share":  # blank unless the supplier has foreign work
                d[col] = d[col].where(d[col] > 0)
            config[col] = st.column_config.NumberColumn(label, help=col, format="%.1f%%")
        elif kind == "count":
            d[col] = d[col].astype("Int64")
            config[col] = st.column_config.NumberColumn(label, help=col, format="%,d")
        elif kind == "ratio":
            config[col] = st.column_config.NumberColumn(label, help=col, format="%.2f×")
        elif kind == "list":
            config[col] = st.column_config.ListColumn(label, help=col)
        else:
            config[col] = st.column_config.TextColumn(label, help=col)
    return d[columns], config


def alternatives_text(top: list[dict]) -> str:
    """'AMENTUM SERVICES, INC. (serves this office) · V2X AEROSPACE LLC'."""
    return " · ".join(a["name"] + (" (serves this office)" if a["serves_office"] else "") for a in top) or "none"


def count_line(n: int, cap: int = 500) -> str:
    return f"{n:,} suppliers" if n <= cap else f"Showing top {cap:,} of {n:,} by prime obligations"
