"""Read-only supplier-risk agent: Claude tool use over four tools, cited answers, full run logging.

The client is constructed with no arguments (`anthropic.Anthropic()`), so credentials resolve from the
environment (ANTHROPIC_API_KEY locally, or Workload Identity Federation variables if set later).
Every run appends one JSON line to logs/agent_runs/<run_id>.jsonl with the prompt, each tool call
(input, output, latency), token usage, latency and cost; every API call is also written to the spend
ledger, which enforces the project budget before each call.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import anthropic

from supplier_graph.agent import costs
from supplier_graph.agent.tools import TOOL_FUNCS, TOOL_SCHEMAS
from supplier_graph.config import LOGS, env

MODEL = env("AGENT_MODEL", "claude-opus-5-5")
EFFORT = env("AGENT_EFFORT", "low")
MAX_STEPS = 8
RUN_DIR = LOGS / "agent_runs"

SYSTEM_PROMPT = """You answer supply-chain dependency questions for a sustainment analyst (a fictional persona) \
using a knowledge graph built only from public data: USAspending DoD prime contracts and subawards for \
maintenance, repair and rebuilding of equipment (PSC category J), FY2023-FY2025, plus a MENA country \
escalation-risk feed. This is a portfolio project. It is not used by, endorsed by, or built for the DoD or any company.

How to work:
- Use the tools. Resolve supplier names to supplier_ids (UEIs) with resolve_supplier before running supplier queries. \
If several candidates are equally good (one company often holds several UEIs), say which one you used and why, \
or use the parent company if the question is about the company as a whole.
- You can only run the named queries offered by run_query. You cannot write new queries.
- Obligation amounts are lifetime obligations on awards that had activity in FY2023-FY2025, with de-obligations floored at zero.

Answer rules:
- Every factual claim must cite the IDs it rests on, inline in square brackets: [award:<award_id>], \
[supplier:<uei>], [parent:<uei>], [office:<code>], [program:<code>], [country:<iso3>]. Cite only IDs that \
appeared in tool results in this conversation.
- If the data cannot answer the question (the entity is not in the data, the country is not covered, or the \
question needs information the graph does not hold), say so plainly and do not guess.
- If only part of the question can be answered, answer that part now (run the queries; do not offer to) and say \
in one sentence which part the data cannot answer. Example: for a country the risk feed does not cover, decline the \
risk score, then list the suppliers and offices with work performed there (run_query country_exposure), cited.
- Never cite an ID as support for something the data lacks: when you decline, cite nothing for the missing part \
(no [country:...] next to "no risk estimate"). Cite only IDs that support facts you state.
- Keep the prose short: a direct answer first, then up to five supporting bullets.
- End with a JSON block in a ```json fence, exactly this shape:
  {"answerable": true|false, "answer": <the single value or list the question asks for, or null>, \
"citations": ["award:...", "supplier:...", ...], "no_data": null or "<one short reason the data cannot answer \
(part of) the question>"}
  answerable is false whenever the question as asked cannot be fully answered; for a partly answerable question, \
put the answerable part's value in "answer" (for example the supplier_ids found) and its IDs in "citations". \
Use plain numbers (no $ or commas) for amounts and counts, ISO codes for countries, and supplier_ids for suppliers."""


# Baseline only (evalh.compare): the same task and output contract with no tools, to measure what the graph adds.
NO_TOOLS_PROMPT = SYSTEM_PROMPT.split("How to work:")[0] + """In this configuration you have NO tools and no access \
to the graph. Answer from your own knowledge only if you are confident; otherwise say you cannot answer from the data.

Answer rules:
- Cite IDs only if you are certain they exist, inline as [award:<id>], [supplier:<uei>], etc.
- Keep the prose short.
- End with a JSON block in a ```json fence, exactly this shape:
  {"answerable": true|false, "answer": <the single value or list the question asks for, or null>, \
"citations": [...], "no_data": null or "<one short reason>"}"""

FALLBACK_MODELS = ("claude-opus", "claude-fable")  # server-side fallback is not offered for Haiku


@dataclass
class ToolCall:
    name: str
    input: dict
    output: dict
    latency_s: float
    is_error: bool = False


@dataclass
class AgentRun:
    run_id: str
    question: str
    model: str
    effort: str
    started_at: str
    final_text: str = ""
    final_json: dict | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    steps: int = 0
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0,
                                                 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0})
    cost_usd: float = 0.0
    latency_s: float = 0.0
    stop_reason: str | None = None
    models_served: list[str] = field(default_factory=list)
    error: str | None = None
    tags: dict = field(default_factory=dict)


JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


def parse_final_json(text: str) -> dict | None:
    blocks = JSON_BLOCK.findall(text or "")
    if not blocks:
        return None
    try:
        return json.loads(blocks[-1])
    except json.JSONDecodeError:
        return None


CAVEAT_PREFIX = "Data caveat (added by the application):"


def _feed_skill_seen(tool_calls: list) -> str | None:
    """skill_status of any country-risk number a tool returned in this run, if one was returned."""
    for tc in tool_calls:
        out = tc.output if isinstance(tc, ToolCall) else tc.get("output", {})
        if not isinstance(out, dict):
            continue
        if out.get("in_risk_feed") and "p_escalation" in out:
            return out.get("skill_status") or "unknown"
        summ = out.get("summary") or {}
        if isinstance(summ, dict) and summ.get("p_escalation") is not None:
            return summ.get("risk_skill_status") or "unknown"
    return None


def skill_caveat(final_text: str, tool_calls: list) -> str:
    """Deterministically attach the feed's skill status and how this tool uses it to any answer that used a
    country-risk number. The model is told (via the tool output) to report it; this guarantees it is shown even when
    the model doesn't."""
    from supplier_graph.ingest.risk_feed import risk_note
    status = _feed_skill_seen(tool_calls)
    if status is None or not final_text or CAVEAT_PREFIX in final_text or "skill" in final_text.lower():
        return final_text
    return f"{final_text.rstrip()}\n\n{CAVEAT_PREFIX} {risk_note(status)}"


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def ask(question: str, run_id: str | None = None, log_name: str | None = None, tags: dict | None = None,
        client: anthropic.Anthropic | None = None, model: str = MODEL, effort: str = EFFORT,
        use_tools: bool = True) -> AgentRun:
    client = client or _client()
    run = AgentRun(run_id=run_id or uuid.uuid4().hex[:12], question=question, model=model, effort=effort,
                   started_at=datetime.now(UTC).isoformat(), tags=tags or {})
    messages: list = [{"role": "user", "content": question}]
    t0 = time.time()
    try:
        for _ in range(MAX_STEPS):
            costs.check_budget()
            kw: dict = {"tools": TOOL_SCHEMAS} if use_tools else {}
            if model.startswith(FALLBACK_MODELS):
                kw.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            resp = client.beta.messages.create(
                model=model, max_tokens=8000, system=SYSTEM_PROMPT if use_tools else NO_TOOLS_PROMPT,
                messages=messages, output_config={"effort": effort}, cache_control={"type": "ephemeral"}, **kw,
            )
            run.steps += 1
            served = getattr(resp, "model", model) or model
            run.models_served.append(served)
            run.cost_usd += costs.record(served, resp.usage, "agent", run.run_id)
            for k in run.usage:
                run.usage[k] += getattr(resp.usage, k, 0) or 0
            run.stop_reason = resp.stop_reason
            messages.append({"role": "assistant", "content": resp.content})  # keep thinking blocks intact
            if resp.stop_reason == "refusal":
                run.error = f"refusal: {getattr(resp, 'stop_details', None)}"
                break
            if resp.stop_reason != "tool_use":
                run.final_text = "".join(b.text for b in resp.content if b.type == "text")
                break
            results = []
            for b in resp.content:
                if b.type != "tool_use":
                    continue
                ts = time.time()
                try:
                    out = TOOL_FUNCS[b.name](b.input)
                    err = "error" in out
                except Exception as e:  # tool bugs are returned to the model, never crash the run
                    out, err = {"error": f"{type(e).__name__}: {e}"}, True
                run.tool_calls.append(ToolCall(b.name, dict(b.input), out, round(time.time() - ts, 3), err))
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": err,
                                "content": json.dumps(out, default=str)})
            messages.append({"role": "user", "content": results})
        else:
            run.error = f"max steps ({MAX_STEPS}) reached"
    except costs.BudgetExceeded as e:
        run.error = f"budget: {e}"
    except anthropic.APIStatusError as e:
        run.error = f"api {e.status_code}: {e.message}"
    except anthropic.APIConnectionError as e:
        run.error = f"connection: {e}"
    run.latency_s = round(time.time() - t0, 2)
    run.cost_usd = round(run.cost_usd, 6)
    run.final_text = skill_caveat(run.final_text, run.tool_calls)
    run.final_json = parse_final_json(run.final_text)
    log(run, log_name or run.run_id)
    return run


def log(run: AgentRun, name: str) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    with (RUN_DIR / f"{name}.jsonl").open("a") as f:
        f.write(json.dumps(asdict(run), default=str) + "\n")


if __name__ == "__main__":
    import sys
    r = ask(" ".join(sys.argv[1:]) or "Which offices depend on Sikorsky Aircraft Corporation?")
    print(r.final_text)
    print(f"\n[steps={r.steps} tools={[t.name for t in r.tool_calls]} cost=${r.cost_usd:.4f} "
          f"latency={r.latency_s}s tokens={r.usage} error={r.error}]")
