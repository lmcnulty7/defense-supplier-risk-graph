"""'What if it fails?' scenario: plain-language summary of the `what_if_remove` query, and the featured suppliers
on the Overview tab. Every number comes from the query (graph and SQL paths, parity-tested)."""

from __future__ import annotations

import re

from supplier_graph.graph import queries as Q
from supplier_graph.graph_view import money, office_short, plural

SUFFIX = re.compile(r"\b(THE|INC|INCORPORATED|LLC|L\.?L\.?C|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|LP|L\.P|"
                    r"PLC|INTERNATIONAL)\b\.?,?", re.I)
CAVEAT = ("An alternative here means a supplier doing the same type of work (same service code), not proof it could "
          "take over.")
FEATURED_POOL = 300  # largest suppliers by prime obligations considered for the featured cards
ANCHOR = "UTJWTSLMFNG4"  # Sikorsky Aircraft (Stratford, CT): the demo's running example


def short_name(name: str) -> str:
    """'SIKORSKY AIRCRAFT CORPORATION' -> 'Sikorsky Aircraft'."""
    words = SUFFIX.sub(" ", name or "").replace(",", " ").split()
    return " ".join(w if any(c.isdigit() for c in w) else w.title() for w in words) or (name or "").title()


def names_phrase(names: list[str]) -> str:
    s = [short_name(n) for n in names]
    return s[0] if len(s) == 1 else ", ".join(s[:-1]) + " and " + s[-1]


def headline(res: dict) -> str:
    """'Removing Sikorsky Aircraft affects 7 offices and $167M; DCMA Stratford has no alternative supplier already
    doing this work (same service code).'"""
    s, rows = res["summary"], res["rows"]
    head = f"Removing {names_phrase(s['names'])} affects {plural(s['n_offices'], 'office')} and " \
           f"{money(s['prime_obligations'])}"
    if s["sub_amount"] > 0:
        head += f" (plus {money(s['sub_amount'])} in subcontracts)"
    lonely = [r for r in rows if r["n_alternatives_at_office"] == 0]
    k = s["n_offices_no_alternative_at_office"]
    if not k:
        return head + "; every affected office already has another supplier doing the same type of work."
    first = office_short(lonely[0]["office_name"], lonely[0]["office_code"]) if lonely else "one office"
    if k == 1:
        return head + f"; {first} has no alternative supplier already doing this work (same service code)."
    return head + (f"; {k} offices, including {first}, have no alternative supplier already doing this work "
                   "(same service code).")


def run(supplier_ids: list[str], limit: int = 50) -> dict:
    return Q.run("what_if_remove", {"supplier_ids": supplier_ids, "limit": limit})


def featured(pool: list[str]) -> list[dict]:
    """Three featured suppliers: Sikorsky, then from scenario results on the largest suppliers (`pool`, ordered by
    prime obligations): the one whose failure leaves the most dollars at offices with no supplier already doing
    the same work, and the one that leaves the most such offices. Deterministic."""
    results = {sid: run([sid]) for sid in pool}

    def lonely_dollars(r: dict) -> float:
        return sum(x["obligations"] for x in r["rows"] if x["n_alternatives_at_office"] == 0)

    rest = [sid for sid in pool if sid != ANCHOR]
    a = max(rest, key=lambda sid: (lonely_dollars(results[sid]), sid))
    b = max((sid for sid in rest if sid != a),
            key=lambda sid: (results[sid]["summary"]["n_offices_no_alternative_at_office"],
                             results[sid]["summary"]["prime_obligations"], sid))
    out = []
    for sid, why in ((ANCHOR, "The running example: a helicopter maker with a small, concentrated footprint."),
                     (a, "Most money at offices with no other supplier already doing the same work."),
                     (b, "Most offices left with no other supplier already doing the same work.")):
        r = results.get(sid) or run([sid])
        out.append({"supplier_id": sid, "name": r["summary"]["names"][0], "why": why, "result": r})
    return out
