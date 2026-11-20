"""Follow-up questions after an agent answer, chosen by rules from the answer's tools and entities (no LLM call).

The entity is the first supplier the answer cites (else the first one it resolved), else an office, else a country.
The kind of follow-up depends on the queries the run used, e.g. after "who depends on X": its sole-source share,
"what if X fails?" and "who else does this work?".
"""

from __future__ import annotations

from collections.abc import Callable

from supplier_graph.scenario import short_name

MAX = 3


def _queries(calls: list[dict]) -> set[str]:
    out = set()
    for t in calls:
        if t["name"] == "run_query":
            out.add(str((t.get("input") or {}).get("query_name")))
        else:
            out.add(t["name"])
    return out


def _supplier(calls: list[dict], cites: list[str]) -> tuple[str, str] | None:
    names: dict[str, str] = {}
    for t in calls:
        out = t.get("output") or {}
        for c in out.get("candidates") or []:
            names.setdefault(c["supplier_id"], c["name"])
        summ = out.get("summary") or {}
        if summ.get("supplier_id") and summ.get("name"):
            names.setdefault(summ["supplier_id"], summ["name"])
        for sid, nm in zip(summ.get("supplier_ids") or [], summ.get("names") or [], strict=False):
            names.setdefault(sid, nm)
    for c in cites:
        if c.startswith("supplier:") and c[9:] in names:
            return c[9:], names[c[9:]]
    for t in calls:
        if t["name"] == "resolve_supplier":
            cands = (t.get("output") or {}).get("candidates") or []
            if cands:
                return cands[0]["supplier_id"], cands[0]["name"]
    return None


def _office(calls: list[dict], cites: list[str]) -> str | None:
    for t in calls:
        p = (t.get("input") or {}).get("params") or {}
        if p.get("office_code"):
            return str(p["office_code"]).upper()
    return next((c[7:] for c in cites if c.startswith("office:")), None)


def _country(calls: list[dict]) -> tuple[str, str] | None:
    for t in calls:
        inp, out = t.get("input") or {}, t.get("output") or {}
        iso = inp.get("country_iso3") or (inp.get("params") or {}).get("country_iso3")
        if iso:
            name = out.get("country_name") or (out.get("summary") or {}).get("country_name") or str(iso).upper()
            return str(iso).upper(), name
    return None


def suggest(question: str, tool_calls: list[dict], final_json: dict | None, defaults: list[str],
            office_label: Callable[[str], str] = lambda c: c) -> list[str]:
    cites = [str(c) for c in ((final_json or {}).get("citations") or [])]
    used = _queries(tool_calls)
    out: list[str] = []
    sup = _supplier(tool_calls, cites)
    office = _office(tool_calls, cites)
    country = _country(tool_calls)
    if sup:
        n = short_name(sup[1])
        if "what_if_remove" in used:
            out = [f"Which offices depend on {n}, directly or through subcontracts?",
                   f"What is {n}'s sole-source share?", f"Which companies subcontract to {n}?"]
        elif used & {"dependent_offices", "blast_radius"}:
            out = [f"What is {n}'s sole-source share?",
                   f"What if {n} fails? Which offices would have no alternative supplier?",
                   f"Who else does the same type of work as {n}?"]
        else:
            out = [f"Which offices depend on {n}, directly or through subcontracts?",
                   f"What if {n} fails? Which offices would have no alternative supplier?",
                   f"Which companies subcontract to {n}?"]
    elif office:
        o = office_label(office)
        out = [f"Which suppliers does office {o} depend on most, and how concentrated is it?",
               f"Which flagged suppliers carry the most obligations at office {o}?",
               f"What if the largest supplier of office {o} fails?"]
        if "office_concentration" in used:
            out = out[1:]
        elif "office_flagged_suppliers" in used:
            out = [out[0], out[2]]
    elif country:
        iso, name = country
        out = [f"Which suppliers and offices have work performed in {name}?",
               f"Which suppliers hold sole-source contracts performed in {name}?"]
        if "country_exposure" in used:
            out = out[1:]
    out = out or list(defaults)
    q = question.strip().lower()
    seen: set[str] = set()
    return [x for x in out if x.lower() != q and not (x in seen or seen.add(x))][:MAX]
