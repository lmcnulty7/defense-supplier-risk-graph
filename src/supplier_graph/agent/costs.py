"""Token pricing and a persistent spend ledger that enforces the project's Anthropic budget.

Prices are USD per 1M tokens from Anthropic's published table (snapshot 2026-11-03). Cache writes are
billed at 1.25x input (5-minute TTL); cache reads at the listed cache-read price.
The ledger (logs/spend_ledger.jsonl, committed) records every API call's cost. `check_budget()` raises
before a call if the running total would exceed BUDGET_USD (default 9.00; the project's hard cap is $20, raised from $10 on 2026-11-19).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from supplier_graph.config import LOGS, env

PRICES = {  # input, output, cache_read
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
}
LEDGER = LOGS / "spend_ledger.jsonl"


class BudgetExceeded(RuntimeError):
    pass


def budget_usd() -> float:
    return float(env("BUDGET_USD", "9.00"))


def call_cost(model: str, usage) -> float:
    pin, pout, pread = PRICES.get(model, PRICES["claude-opus-5-5"])
    g = (lambda k: getattr(usage, k, None) or 0) if not isinstance(usage, dict) else (lambda k: usage.get(k) or 0)
    cost = (g("input_tokens") * pin + g("cache_creation_input_tokens") * pin * 1.25
            + g("cache_read_input_tokens") * pread + g("output_tokens") * pout) / 1e6
    return round(cost, 6)


def total_spent() -> float:
    if not LEDGER.exists():
        return 0.0
    return round(sum(json.loads(line)["cost_usd"] for line in LEDGER.read_text().splitlines() if line.strip()), 6)


def check_budget(estimate: float = 0.05) -> None:
    spent = total_spent()
    if spent + estimate > budget_usd():
        raise BudgetExceeded(f"Anthropic spend ${spent:.4f} + est ${estimate:.2f} would exceed budget ${budget_usd():.2f}")


def record(model: str, usage, purpose: str, run_id: str) -> float:
    cost = call_cost(model, usage)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    u = usage if isinstance(usage, dict) else usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
    with LEDGER.open("a") as f:
        f.write(json.dumps({"ts": datetime.now(UTC).isoformat(), "run_id": run_id, "purpose": purpose, "model": model,
                            "usage": {k: u.get(k) for k in ("input_tokens", "output_tokens",
                                                            "cache_creation_input_tokens", "cache_read_input_tokens")},
                            "cost_usd": cost}) + "\n")
    return cost
