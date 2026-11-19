"""USAspending custom award download (POST /api/v2/download/awards/).

Endpoint, filters and column names were verified against the live API on 2026-11-18
(see docs/design_doc.md, "Data sources"). One async download job returns prime award
summaries and subawards for the scope in config.SCOPE. The result is cached under
data/cache/usaspending/<request-hash>/ with the exact request body, pull timestamp,
source file names, row counts and SHA-256 of each cached parquet. Raw zips are deleted
after conversion to keep disk use small; re-running with --refresh pulls again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import requests

from supplier_graph.config import CACHE, SCOPE

API = "https://api.usaspending.gov/api/v2"
POLL_SECONDS = 15  # be polite to the status endpoint
USER_AGENT = "supplier-graph-portfolio/0.1 (public-data research)"

PRIME_COLUMNS = [
    "contract_award_unique_key", "award_id_piid", "parent_award_id_piid",
    "total_obligated_amount", "current_total_value_of_award", "potential_total_value_of_award",
    "award_base_action_date", "award_latest_action_date", "award_latest_action_date_fiscal_year",
    "period_of_performance_start_date", "period_of_performance_current_end_date",
    "awarding_agency_name", "awarding_sub_agency_code", "awarding_sub_agency_name",
    "awarding_office_code", "awarding_office_name",
    "recipient_uei", "recipient_name", "recipient_name_raw", "recipient_doing_business_as_name",
    "cage_code", "recipient_parent_uei", "recipient_parent_name",
    "recipient_country_code", "recipient_address_line_1", "recipient_city_name",
    "recipient_state_code", "recipient_zip_4_code",
    "primary_place_of_performance_country_code", "primary_place_of_performance_country_name",
    "primary_place_of_performance_city_name", "primary_place_of_performance_state_code",
    "award_type", "product_or_service_code", "product_or_service_code_description",
    "naics_code", "naics_description",
    "dod_acquisition_program_code", "dod_acquisition_program_description",
    "dod_claimant_program_code", "dod_claimant_program_description",
    "extent_competed_code", "extent_competed", "number_of_offers_received",
    "other_than_full_and_open_competition",
    "domestic_or_foreign_entity", "country_of_product_or_service_origin_code",
    "prime_award_base_transaction_description", "usaspending_permalink", "last_modified_date",
]

SUB_COLUMNS = [
    "prime_award_unique_key", "prime_award_piid", "prime_awardee_uei", "prime_awardee_name",
    "prime_award_awarding_sub_agency_name", "prime_award_awarding_office_code",
    "prime_award_awarding_office_name",
    "subaward_type", "subaward_sam_report_id", "subaward_number", "subaward_amount",
    "subaward_action_date", "subaward_action_date_fiscal_year",
    "subawardee_uei", "subawardee_name", "subawardee_dba_name",
    "subawardee_parent_uei", "subawardee_parent_name",
    "subawardee_country_code", "subawardee_address_line_1", "subawardee_city_name",
    "subawardee_state_code", "subawardee_zip_code",
    "subaward_primary_place_of_performance_country_code",
    "subaward_primary_place_of_performance_country_name",
    "subaward_description", "usaspending_permalink",
]


def build_request(scope: dict = SCOPE) -> dict:
    return {
        "filters": {
            "agencies": [{"type": "awarding", "tier": "toptier", "name": scope["awarding_toptier_agency"]}],
            "prime_and_sub_award_types": {"prime_awards": scope["prime_award_types"], "sub_awards": ["procurement"]},
            "time_period": [{"start_date": scope["fy_start"], "end_date": scope["fy_end"]}],
            "psc_codes": {"require": scope["psc_require"]},
        },
        "columns": PRIME_COLUMNS + [c for c in SUB_COLUMNS if c not in PRIME_COLUMNS],
        "file_format": "csv",
        "limit": 500000,
    }


def request_key(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_dir_for(body: dict) -> Path:
    return CACHE / "usaspending" / request_key(body)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Content-Type": "application/json"})
    return s


def pull(refresh: bool = False, scope: dict = SCOPE) -> Path:
    body = build_request(scope)
    out = cache_dir_for(body)
    meta_path = out / "meta.json"
    if meta_path.exists() and not refresh:
        print(f"[usaspending] cache hit {out}")
        return out
    out.mkdir(parents=True, exist_ok=True)
    (out / "request.json").write_text(json.dumps(body, indent=2))
    s = _session()
    started = datetime.now(UTC)
    r = s.post(f"{API}/download/awards/", data=json.dumps(body), timeout=120)
    r.raise_for_status()
    job = r.json()
    print(f"[usaspending] job {job['file_name']}")
    while True:
        time.sleep(POLL_SECONDS)
        st = s.get(job["status_url"], timeout=60).json()
        print(f"[usaspending] status={st['status']} rows={st.get('total_rows')} secs={st.get('seconds_elapsed')}")
        if st["status"] == "finished":
            break
        if st["status"] == "failed":
            raise RuntimeError(f"download failed: {st.get('message')}")
    zpath = out / "download.zip"
    with s.get(job["file_url"], stream=True, timeout=600) as resp:
        resp.raise_for_status()
        with zpath.open("wb") as f:
            for chunk in resp.iter_content(1 << 20):
                f.write(chunk)
    extract = out / "_extract"
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        z.extractall(extract)
    files: dict[str, dict] = {}
    con = duckdb.connect()
    for kind, prefix, cols in (("prime", "Contracts_PrimeAwardSummaries", PRIME_COLUMNS),
                               ("subawards", "Contracts_Subawards", SUB_COLUMNS)):
        parts = sorted(str(extract / n) for n in names if n.split("/")[-1].startswith(prefix))
        target = out / f"{kind}.parquet"
        if not parts:
            raise RuntimeError(f"no {prefix} files in download: {names}")
        sel = ", ".join(f'"{c}"' for c in cols)
        con.execute(
            f"COPY (SELECT {sel} FROM read_csv({parts!r}, all_varchar=true, header=true)) "
            f"TO '{target}' (FORMAT parquet, COMPRESSION zstd)"
        )
        n = con.execute(f"SELECT count(*) FROM '{target}'").fetchone()[0]
        files[kind] = {"source_files": [Path(p).name for p in parts], "rows": n,
                       "parquet": target.name, "sha256": sha256_file(target)}
    con.close()
    shutil.rmtree(extract)
    zip_bytes = zpath.stat().st_size
    zpath.unlink()
    meta = {
        "source": "USAspending API /api/v2/download/awards/",
        "pulled_at_utc": started.isoformat(),
        "finished_at_utc": datetime.now(UTC).isoformat(),
        "request_key": request_key(body),
        "download_file_name": job["file_name"],
        "zip_bytes_deleted_after_conversion": zip_bytes,
        "files": files,
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    pull(refresh=args.refresh)


if __name__ == "__main__":
    main()
