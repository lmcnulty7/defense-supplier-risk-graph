"""Paths and environment. Keys come from .env (never committed)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=False)

DATA = ROOT / "data"
CACHE = DATA / "cache"
REPORTS = ROOT / "reports"
LOGS = ROOT / "logs"
FIXTURES = ROOT / "fixtures"
DUCKDB_PATH = Path(os.environ.get("SUPPLIER_GRAPH_DB", DATA / "supplier_graph.duckdb"))
GRAPH_DIR = DATA / "graph"

# Scope (documented in README / design doc). FY2023-FY2025 = 2022-10-01 .. 2025-09-30.
SCOPE = {
    "awarding_toptier_agency": "Department of Defense",
    "psc_require": [["Service", "J"]],  # J = Maintenance, Repair and Rebuilding of Equipment
    "fy_start": "2022-10-01",
    "fy_end": "2025-09-30",
    "prime_award_types": ["A", "B", "C", "D"],  # definitive contracts, POs, delivery orders, BPA calls
}


def env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name, default)
    return val if val not in ("", None) else default


def resolve_repo_path(p: str) -> Path:
    path = Path(p).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()
