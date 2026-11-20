"""Plain-language presentation of an agent run for the app: answer status, the no-data reason, and one readable line
per tool call ("Looked up country risk for Japan → not in feed (0.1s)")."""

from __future__ import annotations

DECLINED = "Declined (outside data coverage)"
PARTLY = "Partly answered"
ANSWERED = "Answered"
NO_DATA_FALLBACK = "the data does not cover this question"


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def status(final_json: dict | None) -> tuple[str | None, list[str], str | None]:
    """(status label, citations to show, no-data reason). A declined answer shows no citation list."""
    if not final_json:
        return None, [], None
    cites = [str(c) for c in final_json.get("citations") or []]
    reason = (str(final_json.get("no_data") or "").strip() or None)
    if final_json.get("answerable") is False:
        if final_json.get("answer") in (None, [], {}, ""):
            return DECLINED, [], reason or NO_DATA_FALLBACK
        return PARTLY, cites, reason or NO_DATA_FALLBACK
    return ANSWERED, cites, reason


def _name(params: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in params.items() if k != "limit")


def describe(name: str, inp: dict, out: dict, latency_s: float) -> str:
    """One readable line for a tool call."""
    out = out if isinstance(out, dict) else {}
    err = out.get("error")
    if name == "resolve_supplier":
        c = out.get("candidates") or []
        res = (f"{plural(len(c), 'candidate')}, best: {c[0]['name'].title()} ({c[0]['supplier_id']})" if c
               else "no match in the data")
        line = f"Looked up supplier “{inp.get('name', '')}” → {res}"
    elif name == "country_risk":
        cname = out.get("country_name") or inp.get("country_iso3", "")
        if err:
            res = err
        elif out.get("in_risk_feed"):
            res = f"escalation probability {out.get('p_escalation')} (week of {out.get('week_start')})"
        else:
            res = "not in feed"
        line = f"Looked up country risk for {cname} → {res}"
    elif name == "award_details":
        line = f"Fetched award {inp.get('award_id', '')} → " + (err or f"{out.get('office_name', '')}, "
                                                               f"{out.get('pop_country', '')}")
    elif name == "run_query":
        q = str(inp.get("query_name", "")).replace("_", " ")
        params = _name(inp.get("params") or {})
        res = err or plural(len(out.get("rows") or []), "row")
        line = f"Ran query “{q}”" + (f" for {params}" if params else "") + f" → {res}"
    else:
        line = f"{name} → {err or 'done'}"
    return f"{line} ({latency_s:.1f}s)"
