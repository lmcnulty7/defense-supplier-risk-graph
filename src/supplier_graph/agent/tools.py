"""The agent's five read-only tools. No free-form query generation: `run_query` only runs named,
parameterized queries from graph/queries.py."""

from __future__ import annotations

import json
import re
from functools import lru_cache

from rapidfuzz import fuzz, process

from supplier_graph.config import env
from supplier_graph.er.matcher import ACRONYM_NAME_SCORE, initials
from supplier_graph.er.normalize import LEGAL_SUFFIXES, NAME_CANON, core_name
from supplier_graph.graph import queries as Q
from supplier_graph.ingest.risk_feed import SKILL_TEXT, SKILL_UNKNOWN, feed_in_likelihood, usage_text
from supplier_graph.stage import connect

UEI_RE = re.compile(r"^[A-Z0-9]{12}$")
MAX_ROWS = 10
# Grounding guard (added after the challenge set showed agents substituting a look-alike company): flag query words
# the candidate's name lacks. Typos and abbreviations still match (fuzzy ratio >= 75 or a prefix), a swapped
# distinctive word does not. Set RESOLVER_GUARD=0 to reproduce the unguarded tool.
GUARD = env("RESOLVER_GUARD", "1") == "1"


_EXPAND: dict[str, list[str]] = {}
for _long, _short in NAME_CANON.items():
    _EXPAND.setdefault(_short, []).append(_long)
_SUFFIX_WORDS = sorted(w for w in LEGAL_SUFFIXES | {"INCORPORATED", "CORPORATION", "COMPANY", "LIMITED"} if len(w) >= 4)


def _close(t: str, c: str) -> bool:
    return fuzz.ratio(t, c) >= 75 or c.startswith(t) or t.startswith(c)


def missing_words(query_core: str, cand_core: str) -> list[str]:
    """Query words with no fuzzy counterpart in the candidate (abbreviations expanded, legal-suffix typos ignored)."""
    forms = [x for c in cand_core.split() for x in (c, *_EXPAND.get(c, []))]
    out = []
    for t in query_core.split():
        if not t.isalpha() or len(t) < 3:
            continue
        if any(_close(t, x) for x in forms) or any(fuzz.ratio(t, w) >= 80 for w in _SUFFIX_WORDS):
            continue
        out.append(t)
    return out


@lru_cache(maxsize=1)
def _supplier_index() -> tuple[list[str], list[str], dict[str, dict]]:
    con = connect(read_only=True)
    rows = con.execute("""
      SELECT s.supplier_id, s.name, s.city, s.state, s.country, s.name_variants, s.sam_legal_name,
             r.prime_obligations, r.n_prime_awards, r.n_subawards_received
      FROM suppliers s JOIN supplier_risk r USING (supplier_id) ORDER BY s.supplier_id""").fetchall()
    con.close()
    keys, owners, info = [], [], {}
    for sid, name, city, state, country, variants, sam_name, obl, npa, nsub in rows:
        info[sid] = {"supplier_id": sid, "name": name, "city": city, "state": state, "country": country,
                     "prime_obligations": round(obl or 0, 2), "n_prime_awards": npa, "n_subawards_received": nsub}
        for v in sorted(set((variants or []) + ([sam_name] if sam_name else []))):
            c = core_name(v)
            if c:
                keys.append(c)
                owners.append(sid)
    return keys, owners, info


@lru_cache(maxsize=1)
def _acronym_index() -> dict[str, list[str]]:
    keys, owners, _ = _supplier_index()
    idx: dict[str, set[str]] = {}
    for key, sid in zip(keys, owners, strict=True):
        for i in initials(key):
            idx.setdefault(i, set()).add(sid)
    return {k: sorted(v) for k, v in idx.items()}


def name_score(qc: str, key: str) -> float:
    """Name-only similarity of two core names. Token-set similarity only counts when most query
    tokens are (fuzzily) present in the candidate, so one shared word ("QUANTUM") is not a match."""
    qt, kt = qc.split(), key.split()
    covered = sum(any(fuzz.ratio(t, k) >= 85 for k in kt) for t in qt) / len(qt) if qt else 0.0
    whole = max(fuzz.token_sort_ratio(qc, key), fuzz.ratio(qc, key)) / 100
    subset = fuzz.token_set_ratio(qc, key) / 100 if covered >= 0.75 else 0.0
    return round(max(whole, subset * (0.85 + 0.15 * covered)), 4)


def resolve_supplier(name: str, limit: int = 5) -> dict:
    keys, owners, info = _supplier_index()
    q = name.strip().upper()
    if UEI_RE.match(q) and q in info:
        return {"query": name, "candidates": [{**info[q], "match_score": 1.0, "matched_on": "exact UEI"}]}
    qc = core_name(name)
    if not qc:
        return {"query": name, "candidates": [], "note": "empty name after normalization"}
    hits = process.extract(qc, keys, scorer=fuzz.WRatio, limit=60)
    best: dict[str, float] = {}
    best_key: dict[str, str] = {}
    for key, _, idx in hits:
        sc = name_score(qc, key)
        sid = owners[idx]
        if sc > best.get(sid, -1):
            best[sid], best_key[sid] = sc, key
    acr = _acronym_index().get(qc, []) if re.fullmatch(r"[A-Z]{3,}", qc) else []
    for sid in acr:  # "GDLS" -> GENERAL DYNAMICS LAND SYSTEMS (same rule as the ER matcher)
        best[sid] = max(best.get(sid, 0), ACRONYM_NAME_SCORE)
    ranked = sorted(best.items(), key=lambda kv: (-kv[1], -info[kv[0]]["prime_obligations"], kv[0]))
    cands = [{**info[s], "match_score": round(sc, 3)} for s, sc in ranked if sc >= 0.6][:limit]
    out = {"query": name, "candidates": cands}
    if acr and any(c["supplier_id"] in acr for c in cands):
        out["acronym_matches"] = [s for s in acr if s in {c["supplier_id"] for c in cands}]
    if GUARD and cands and not out.get("acronym_matches"):
        for c in cands:
            c["missing_query_words"] = missing_words(qc, best_key.get(c["supplier_id"], ""))
        if cands[0]["missing_query_words"]:
            out["exact_name_match"] = False
            out["note"] = ("no supplier name contains every word of the query; the top candidate lacks "
                           f"{', '.join(cands[0]['missing_query_words'])}. It may be a different company: do not "
                           "substitute it silently. Say the named supplier is not in the data, and mention the "
                           "closest candidate.")
            return out
    if not cands:
        out["note"] = "no supplier in the data matches this name (score >= 0.6); it may not be in scope"
    elif len(cands) > 1 and cands[0]["match_score"] - cands[1]["match_score"] < 0.03:
        out["note"] = "several close candidates; disambiguate by city/state or ask the user"
    return out


def _compact(res: dict) -> dict:
    res = json.loads(json.dumps(res, default=str))
    res["rows"] = res["rows"][:MAX_ROWS]
    return res


def run_query(query_name: str, params: dict) -> dict:
    try:
        return _compact(Q.run(query_name, params or {}, backend="graph"))
    except Q.QueryError as e:
        return {"error": str(e), "query": query_name, "params": params}


def what_if_remove(supplier_ids, limit: int = 25) -> dict:
    """Scenario: what breaks if these suppliers fail (offices, dollars, programs, alternatives per office)."""
    try:
        return _compact(Q.run("what_if_remove", {"supplier_ids": supplier_ids, "limit": limit}, backend="graph"))
    except Q.QueryError as e:
        return {"error": str(e), "supplier_ids": supplier_ids}


def country_risk(country_iso3: str) -> dict:
    iso = (country_iso3 or "").strip().upper()
    con = connect(read_only=True)
    r = con.execute("""SELECT country_iso3, week_start, p_escalation, model_version, calibration, top_drivers,
                              data_cutoff, generated_at, feed_source, feed_is_fixture, skill_status FROM risk_latest
                       WHERE country_iso3 = ?""", [iso]).fetchone()
    name = con.execute("SELECT name FROM countries WHERE alpha3 = ?", [iso]).fetchone()
    covered = [c for (c,) in con.execute("SELECT country_iso3 FROM risk_latest ORDER BY 1").fetchall()]
    con.close()
    if not r:
        return {"country_iso3": iso, "country_name": name[0] if name else None, "in_risk_feed": False,
                "note": "the MENA risk feed has no estimate for this country", "feed_countries": covered}
    keys = ["country_iso3", "week_start", "p_escalation", "model_version", "calibration", "top_drivers",
            "data_cutoff", "generated_at", "feed_source", "feed_is_fixture", "skill_status"]
    d = dict(zip(keys, r, strict=True))
    d["top_drivers"] = json.loads(d["top_drivers"])
    d["p_escalation"] = round(d["p_escalation"], 4)
    d["feed_source"] = d["feed_source"].split("/")[-1]
    d["used_in_likelihood_score"] = feed_in_likelihood()
    d["skill_note"] = (SKILL_TEXT.get(d["skill_status"], SKILL_TEXT[SKILL_UNKNOWN]) + " " + usage_text()
                       + " Report this alongside the number.")
    return json.loads(json.dumps({"country_name": name[0] if name else None, "in_risk_feed": True, **d}, default=str))


def award_details(award_id: str) -> dict:
    con = connect(read_only=True)
    cur = con.execute("""SELECT a.award_id, a.piid, a.parent_piid, a.obligated, a.potential_value, a.base_action_date,
                                a.latest_action_date, a.sub_agency_name, a.office_code, a.office_name, a.pop_country,
                                a.psc, a.psc_description, a.naics_code, a.program_code, a.program_name,
                                a.extent_competed_code, a.extent_competed, a.is_sole_source, a.offers_received,
                                a.award_type, a.description, a.permalink, a.supplier_id, s.name AS supplier_name
                         FROM awards_scope a JOIN suppliers s USING (supplier_id) WHERE a.award_id = ?""", [award_id])
    row = cur.fetchone()
    if not row:
        con.close()
        return {"error": f"award_id {award_id!r} not found in scope"}
    d = dict(zip([c[0] for c in cur.description], row, strict=True))
    subs = con.execute("""SELECT sub_supplier_id, s.name, sum(amt_pos) amt, count(*) n FROM subawards_scope x
                          JOIN suppliers s ON s.supplier_id = x.sub_supplier_id WHERE prime_award_id = ?
                          GROUP BY 1, 2 ORDER BY amt DESC, 1 LIMIT 5""", [award_id]).fetchall()
    n_sub = con.execute("SELECT count(*) FROM subawards_scope WHERE prime_award_id = ?", [award_id]).fetchone()[0]
    con.close()
    d["n_subawards"] = n_sub
    d["top_subcontractors"] = [{"supplier_id": a, "name": b, "amount": round(c, 2), "n_subawards": n} for a, b, c, n in subs]
    return json.loads(json.dumps(d, default=str))


TOOL_FUNCS = {
    "resolve_supplier": lambda i: resolve_supplier(i["name"], i.get("limit", 5)),
    "run_query": lambda i: run_query(i["query_name"], i.get("params", {})),
    "country_risk": lambda i: country_risk(i["country_iso3"]),
    "award_details": lambda i: award_details(i["award_id"]),
    "what_if_remove": lambda i: what_if_remove(i["supplier_ids"], i.get("limit", 25)),
}
# what_if_remove has its own tool, so run_query does not list it
RUN_QUERY_NAMES = sorted(q for q in Q.QUERIES if q != "what_if_remove")

_QUERY_DOC = "\n".join(
    f"- {q.name}({', '.join(list(q.params) + [f'{k}={v}' for k, v in q.optional.items()])}): {q.description}"
    for q in Q.QUERIES.values() if q.name in RUN_QUERY_NAMES)

TOOL_SCHEMAS = [
    {"name": "resolve_supplier",
     "description": "Map a supplier name (any spelling) or a UEI to candidate supplier_ids (UEIs) with city, state "
                    "and obligations. Always use this before supplier queries unless you already have a UEI.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["name"],
                      "properties": {"name": {"type": "string"}, "limit": {"type": "integer"}}}},
    {"name": "run_query",
     "description": "Run one named, read-only, parameterized query over the supplier graph. Queries:\n" + _QUERY_DOC,
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["query_name", "params"],
                      "properties": {"query_name": {"type": "string", "enum": RUN_QUERY_NAMES},
                                     "params": {"type": "object"}}}},
    {"name": "country_risk",
     "description": "Latest MENA escalation-risk estimate (p_escalation, week, model version, drivers) for a country "
                    "by ISO alpha-3 code. Says so if the feed does not cover the country.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["country_iso3"],
                      "properties": {"country_iso3": {"type": "string"}}}},
    {"name": "what_if_remove",
     "description": "Scenario for 'what if these suppliers fail?': " + Q.QUERIES["what_if_remove"].description
                    + " Takes supplier_ids (UEIs from resolve_supplier).",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["supplier_ids"],
                      "properties": {"supplier_ids": {"type": "array", "items": {"type": "string"}},
                                     "limit": {"type": "integer"}}}},
    {"name": "award_details",
     "description": "Full details of one prime award by award_id (USAspending generated unique award key), "
                    "including office, program, competition, place of performance and top subcontractors.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["award_id"],
                      "properties": {"award_id": {"type": "string"}}}},
]
