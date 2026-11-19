"""SAM.gov Entity Management API v3 enrichment (public entity data; needs SAM_API_KEY).

Personal SAM.gov keys may be limited to as few as 10 requests per day, so this module is
budgeted: it enriches at most SAM_DAILY_BUDGET requests per run (default 8), caching every
response per UEI in data/cache/sam/entities.jsonl with the request parameters and pull time.
Later runs skip UEIs already cached, so enrichment accumulates over days.

Purpose: subaward records carry no CAGE code; SAM supplies CAGE, legal name and physical
address for subawardee UEIs so the entity resolver can use them.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import requests

from supplier_graph.config import CACHE, env

URL = "https://api.sam.gov/entity-information/v3/entities"
CACHE_FILE = CACHE / "sam" / "entities.jsonl"
BATCH = 10  # SAM returns at most 10 entities per page for the synchronous JSON API


def cached_ueis(path: Path = CACHE_FILE) -> set[str]:
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        out.update(rec["requested_ueis"])
    return out


def load_cache(path: Path = CACHE_FILE) -> list[dict]:
    """Flatten cached responses to one dict per entity."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        for ent in rec["response"].get("entityData", []) or []:
            reg = ent.get("entityRegistration", {}) or {}
            core = ent.get("coreData", {}) or {}
            addr = core.get("physicalAddress", {}) or {}
            rows.append({
                "uei": reg.get("ueiSAM"), "cage_code": reg.get("cageCode"),
                "legal_business_name": reg.get("legalBusinessName"), "dba_name": reg.get("dbaName"),
                "registration_status": reg.get("registrationStatus"),
                "address_line_1": addr.get("addressLine1"), "city": addr.get("city"),
                "state": addr.get("stateOrProvinceCode"), "zip": addr.get("zipCode"),
                "country_iso3": addr.get("countryCode"),
                "pulled_at_utc": rec["pulled_at_utc"],
            })
    return rows


def enrich(ueis: list[str], budget: int | None = None) -> int:
    key = env("SAM_API_KEY")
    if not key:
        print("[sam] SAM_API_KEY not set; skipping (set it in .env; see .env.example)")
        return 0
    budget = budget if budget is not None else int(env("SAM_DAILY_BUDGET", "8"))
    done = cached_ueis()
    todo = [u for u in dict.fromkeys(ueis) if u and u not in done]
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    calls = 0
    for i in range(0, len(todo), BATCH):
        if calls >= budget:
            break
        chunk = todo[i:i + BATCH]
        params = {"ueiSAM": "[" + "~".join(chunk) + "]",
                  "includeSections": "entityRegistration,coreData"}
        r = requests.get(URL, params={**params, "api_key": key}, timeout=60)
        calls += 1
        if r.status_code == 429:
            print("[sam] rate limited (429); stopping for today")
            break
        r.raise_for_status()
        rec = {"pulled_at_utc": datetime.now(UTC).isoformat(), "request_params": params,
               "requested_ueis": chunk, "response": r.json()}
        with CACHE_FILE.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        print(f"[sam] call {calls}: {len(chunk)} UEIs -> {rec['response'].get('totalRecords')} records")
    return calls


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--budget", type=int, default=None)
    args = ap.parse_args()
    from supplier_graph.stage import sam_candidate_ueis
    enrich(sam_candidate_ueis(), budget=args.budget)


if __name__ == "__main__":
    main()
