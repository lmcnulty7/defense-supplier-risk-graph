"""Resolve entity_records to suppliers and build the corporate hierarchy.

Writes DuckDB tables:
  record_supplier   record_id -> supplier_id, method (uei | cage | fuzzy | unresolved), score
  suppliers         one row per supplier_id with canonical name, address, parent, CAGE codes
  parents           one row per parent UEI with its name and child count
  award_supplier / subaward_supplier   source rows -> supplier_id
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict

import pandas as pd

from supplier_graph.config import REPORTS
from supplier_graph.er.evaluate import THRESHOLD_FILE
from supplier_graph.er.matcher import DEFAULT_THRESHOLD, MatchRecord, initials, score
from supplier_graph.er.normalize import (
    blocking_keys,
    core_name,
    normalize_address,
    normalize_name,
    normalize_zip,
)
from supplier_graph.stage import connect


def _threshold() -> float:
    if THRESHOLD_FILE.exists():
        return float(json.loads(THRESHOLD_FILE.read_text())["threshold"])
    return DEFAULT_THRESHOLD


def _scorer():
    """The matcher selected on dev F1 by er.evaluate: learned evidence weights or the hand-set rule."""
    metrics = REPORTS / "er_metrics.json"
    sel = json.loads(metrics.read_text()).get("learned_linkage", {}).get("selected") if metrics.exists() else None
    from supplier_graph.er import linkage
    if sel == "learned" and linkage.MODEL_FILE.exists():
        prob, t = linkage.load_scorer()
        return prob, t, "learned"
    return score, _threshold(), "rule"


def _operating_point(method: str) -> dict:
    """Threshold / margin / address-only rule chosen by er.holdout's tradeoff search (tuning half, confirmed on the
    other half). Applies only to the matcher it was chosen for."""
    p = REPORTS / "er_operating_point.json"
    op = json.loads(p.read_text()) if p.exists() else {}
    return op if op.get("matcher", "").startswith(method) else {}


def _acr_keys(name: str) -> list[str]:
    """Acronym blocking keys, so 'QRC' meets 'QUALITY REFRIGERATION CORP' (no shared token otherwise)."""
    c = core_name(name)
    if re.fullmatch(r"[A-Z]{3,}", c):
        return [f"A:{c}"]
    return [f"A:{i}" for i in initials(c, normalize_name(name))]


def _addr_keys(row: dict) -> list[str]:
    """Facility blocking key (normalized street address + ZIP5). Defense suppliers are often renamed or acquired
    while keeping their facility (Cobham Advanced Electronic Solutions -> CAES Systems at the same street address),
    and name-based keys never pair those records. Scoring still decides; this only makes the comparison happen."""
    a, z = normalize_address(row.get("address")), normalize_zip(row.get("zip"))
    return [f"AD:{a}|{z}"] if a and z else []


MARGIN = 0.02  # a link must beat the runner-up UEI by more than this


def candidate_scores(rows: list[dict], canon_rows: list[dict], scorer, address_blocking: bool = True) -> list[list]:
    """Per record: every candidate UEI from blocking, as (uei, score, found_via_name_keys), best first.
    found_via_name_keys is False when the only shared blocking key was the facility address."""
    index: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(canon_rows):
        for k in (blocking_keys(core_name(row["name"]), row["state"], normalize_zip(row["zip"]))
                  + _acr_keys(row["name"]) + (_addr_keys(row) if address_blocking else [])):
            index[k].append(i)
    out = []
    for row in rows:
        name_keys = blocking_keys(core_name(row["name"]), row["state"], normalize_zip(row["zip"])) + _acr_keys(row["name"])
        via_name = {i for k in name_keys for i in index.get(k, [])[:500]}
        cands = via_name | ({i for k in _addr_keys(row) for i in index.get(k, [])[:500]} if address_blocking else set())
        q = _mr(row)
        best: dict[str, tuple[float, bool]] = {}
        for i in cands:
            u, sc = canon_rows[i]["uei"], scorer(q, _mr(canon_rows[i]))
            prev = best.get(u)
            best[u] = (max(sc, prev[0]) if prev else sc, (prev[1] if prev else False) or i in via_name)
        out.append(sorted(((u, sc, vn) for u, (sc, vn) in best.items()), key=lambda x: (-x[1], x[0])))
    return out


def decide(row: dict, cands: list, t: float, margin: float = MARGIN, t_addr: float | None = None) -> dict:
    """Link when the best eligible UEI clears its threshold and beats the runner-up by more than `margin`.
    Candidates found only through the facility key must clear max(t, t_addr) (t_addr=None: same as t;
    t_addr > 1: never link on address alone). Otherwise abstain with a stable NOID- id."""
    ta = t if t_addr is None else max(t, t_addr)
    top = [(u, sc) for u, sc, vn in cands if sc >= (t if vn else ta)]
    if top and (len(top) == 1 or top[0][1] - top[1][1] > margin):
        return {"record_id": row["record_id"], "supplier_id": top[0][0], "method": "fuzzy", "score": top[0][1]}
    sid = "NOID-" + hashlib.sha1(f"{core_name(row['name'])}|{normalize_zip(row['zip'])}".encode()).hexdigest()[:10]
    return {"record_id": row["record_id"], "supplier_id": sid,
            "method": "unresolved_ambiguous" if top else "unresolved", "score": top[0][1] if top else None}


def fuzzy_resolve(rows: list[dict], canon_rows: list[dict], scorer, t: float,
                  address_blocking: bool = True, margin: float = MARGIN, t_addr: float | None = None) -> list[dict]:
    """Blocked fuzzy resolution of identifier-less records against one canonical record per UEI."""
    cs = candidate_scores(rows, canon_rows, scorer, address_blocking)
    return [decide(r, c, t, margin, t_addr) for r, c in zip(rows, cs, strict=True)]


def _mr(row) -> MatchRecord:
    def g(k):
        v = row[k]
        return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)
    return MatchRecord(g("name"), g("address"), g("city"), g("state"), g("zip"), g("country"))


def resolve() -> dict:
    con = connect()
    rec = con.execute("SELECT * FROM entity_records ORDER BY record_id").df()
    cage_map = (rec[rec.uei.notna() & rec.cage_code.notna()]
                .groupby("cage_code")["uei"].agg(lambda s: sorted(set(s))).to_dict())

    out: list[dict] = []
    has_uei = rec[rec.uei.notna()]
    for r in has_uei.itertuples():
        out.append({"record_id": r.record_id, "supplier_id": r.uei, "method": "uei", "score": 1.0})

    rest = rec[rec.uei.isna()]
    cage_ok = [isinstance(c, str) and len(cage_map.get(c, [])) == 1 for c in rest.cage_code]
    via_cage = rest[pd.Series(cage_ok, index=rest.index, dtype=bool)]
    for r in via_cage.itertuples():
        out.append({"record_id": r.record_id, "supplier_id": cage_map[r.cage_code][0], "method": "cage", "score": 1.0})
    rest = rest[~rest.record_id.isin(via_cage.record_id)]

    # Fuzzy: blocked comparison against one canonical record per UEI (most-observed record).
    canon = has_uei.sort_values(["n_observations", "record_id"], ascending=[False, True],
                                kind="mergesort").drop_duplicates("uei")
    scorer, t, method = _scorer()
    op = _operating_point(method)
    t = op.get("threshold", t)
    out += fuzzy_resolve(rest.to_dict("records"), canon.to_dict("records"), scorer, t,
                         margin=op.get("margin", MARGIN), t_addr=op.get("t_addr"))
    rs = pd.DataFrame(out)
    con.register("rs", rs)
    con.execute("CREATE OR REPLACE TABLE record_supplier AS SELECT * FROM rs")
    con.unregister("rs")

    # Canonical supplier attributes: the amount-weighted most common prime/sub name & address.
    con.execute("""
    CREATE OR REPLACE TABLE suppliers AS
    WITH j AS (
      SELECT rs.record_id, rs.supplier_id, rs.method, er.* EXCLUDE (record_id)
      FROM record_supplier rs JOIN entity_records er USING (record_id)
    ), pick AS (
      SELECT supplier_id, name, address, city, state, zip, country,
             row_number() OVER (PARTITION BY supplier_id
               ORDER BY (role = 'prime_recipient') DESC, n_observations DESC, total_amount DESC NULLS LAST,
                        name, address NULLS LAST, city NULLS LAST, zip NULLS LAST, record_id) AS rn
      FROM j WHERE role <> 'prime_recipient_raw'
    ), par AS (
      SELECT supplier_id, parent_uei, parent_name FROM (
        SELECT supplier_id, parent_uei, parent_name, sum(n_observations) AS n FROM j
        WHERE parent_uei IS NOT NULL GROUP BY ALL)
      QUALIFY row_number() OVER (PARTITION BY supplier_id ORDER BY n DESC, parent_uei, parent_name NULLS LAST) = 1
    ), agg AS (
      SELECT supplier_id,
             list(DISTINCT cage_code ORDER BY cage_code) FILTER (WHERE cage_code IS NOT NULL) AS cage_codes,
             list(DISTINCT name ORDER BY name) AS name_variants,
             list(DISTINCT method ORDER BY method) AS resolution_methods,
             count(*) AS n_records
      FROM j GROUP BY supplier_id
    )
    SELECT a.supplier_id, p.name, p.address, p.city, p.state, p.zip, p.country,
           par.parent_uei, par.parent_name, a.cage_codes, a.name_variants, a.resolution_methods, a.n_records,
           s.legal_business_name AS sam_legal_name, s.cage_code AS sam_cage_code
    FROM agg a LEFT JOIN pick p ON p.supplier_id = a.supplier_id AND p.rn = 1
    LEFT JOIN par ON par.supplier_id = a.supplier_id
    LEFT JOIN sam_entities s ON s.uei = a.supplier_id
    ORDER BY a.supplier_id
    """)
    con.execute("""
    CREATE OR REPLACE TABLE parents AS
    SELECT parent_uei, min(parent_name) AS parent_name, count(*) AS n_children
    FROM suppliers WHERE parent_uei IS NOT NULL GROUP BY parent_uei ORDER BY parent_uei
    """)
    con.execute("""
    CREATE OR REPLACE TABLE award_supplier AS
    SELECT o.source_id AS award_id, rs.supplier_id, rs.method
    FROM entity_observations o JOIN record_supplier rs USING (record_id) WHERE o.role = 'prime_recipient'
    """)
    con.execute("""
    CREATE OR REPLACE TABLE subaward_supplier AS
    SELECT o.source_id AS subaward_id, rs.supplier_id, rs.method
    FROM entity_observations o JOIN record_supplier rs USING (record_id) WHERE o.role = 'subawardee'
    """)
    stats = {
        "records": len(rec),
        "by_method": rs["method"].value_counts().to_dict(),
        "suppliers": con.execute("SELECT count(*) FROM suppliers").fetchone()[0],
        "parents": con.execute("SELECT count(*) FROM parents").fetchone()[0],
        "awards_mapped": con.execute("SELECT count(DISTINCT award_id) FROM award_supplier").fetchone()[0],
        "awards_total": con.execute("SELECT count(*) FROM awards").fetchone()[0],
        "subawards_mapped": con.execute("SELECT count(DISTINCT subaward_id) FROM subaward_supplier").fetchone()[0],
        "subawards_total": con.execute("SELECT count(*) FROM subawards").fetchone()[0],
        "threshold": t,
        "fuzzy_matcher": method,
        "fuzzy_operating_point": {"threshold": t, "margin": op.get("margin", MARGIN), "t_addr": op.get("t_addr")},
    }
    con.execute("DELETE FROM stage_meta WHERE k = 'resolve'")
    con.execute("INSERT INTO stage_meta VALUES ('resolve', ?)", [json.dumps(stats, default=str)])
    con.close()
    _append_report(stats)
    print(json.dumps(stats, indent=2, default=str))
    return stats


def _append_report(stats: dict) -> None:
    from supplier_graph.config import REPORTS
    path = REPORTS / "entity_resolution.md"
    if not path.exists():
        return
    text = path.read_text().split("\n## Production resolution")[0].rstrip() + "\n"
    methods = ", ".join(f"{k}: {v:,}" for k, v in stats["by_method"].items())
    text += f"""
## Production resolution (`make er`)

* Entity records (distinct role/identifier/name/address observations): **{stats['records']:,}**; by method: {methods}.
* Suppliers: **{stats['suppliers']:,}**; parent UEIs: **{stats['parents']:,}**.
* Prime awards mapped to a supplier: {stats['awards_mapped']:,} / {stats['awards_total']:,};
  subawards mapped: {stats['subawards_mapped']:,} / {stats['subawards_total']:,}.

In this scope (FY2023-FY2025) every prime and subaward record carries a UEI, so the batch pipeline
resolves everything by exact identifier and the fuzzy path is not exercised in batch. The fuzzy matcher
still matters in two places: (1) the agent's `resolve_supplier` tool, which maps analyst-typed names
(abbreviations, typos, missing suffixes) to supplier IDs, and (2) older or third-party data without UEIs.
That is why it is evaluated above with identifiers hidden.
"""
    path.write_text(text)


if __name__ == "__main__":
    resolve()
