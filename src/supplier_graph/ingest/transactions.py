"""USAspending prime-transaction download (POST /api/v2/download/transactions/) for the risk backtest.

Why: the award-summary pull has one row per award, so it cannot say *when* something happened to a supplier.
Transactions (contract modifications) carry `action_type_code`, which includes public supplier-failure events:
E = terminate for default (complete or partial), F = terminate for convenience. These are the outcome labels for
the risk backtest (`supplier_graph.risk.backtest`). They come from the source data, not from annotation.

Verified against the live API on 2026-11-18 with a 100-row probe: `award_type_codes` must be passed directly
(`prime_and_sub_award_types` is ignored by this endpoint and expands to every award type), and the `columns`
subset is honoured. Scope matches config.SCOPE (DoD, contract types A-D, PSC J) but the window starts one fiscal
year earlier (FY2022) so the first backtest fold has a year of history. Cached exactly like the award pull:
request body, pull time, row count and SHA-256 under data/cache/usaspending_tx/<request-hash>/.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from supplier_graph.config import CACHE, SCOPE
from supplier_graph.ingest.usaspending import API, POLL_SECONDS, _session, request_key, sha256_file

TX_FY_START = "2021-10-01"  # FY2022 (one year of history before the main FY2023-FY2025 scope)

TX_COLUMNS = [
    "contract_transaction_unique_key", "contract_award_unique_key", "award_id_piid", "modification_number",
    "federal_action_obligation", "action_date", "action_date_fiscal_year",
    "action_type_code", "action_type", "award_type_code",
    "period_of_performance_start_date", "period_of_performance_current_end_date",
    "awarding_office_code", "recipient_uei", "recipient_parent_uei", "recipient_name",
    "primary_place_of_performance_country_code", "product_or_service_code",
    "extent_competed_code", "number_of_offers_received",
    "contracting_officers_determination_of_business_size_code",
    "dod_acquisition_program_code", "transaction_description",
]


def build_request(scope: dict = SCOPE) -> dict:
    return {
        "filters": {
            "agencies": [{"type": "awarding", "tier": "toptier", "name": scope["awarding_toptier_agency"]}],
            "award_type_codes": scope["prime_award_types"],
            "time_period": [{"start_date": TX_FY_START, "end_date": scope["fy_end"]}],
            "psc_codes": {"require": scope["psc_require"]},
        },
        "columns": TX_COLUMNS,
        "file_format": "csv",
        "limit": 500000,
    }


def cache_dir_for(body: dict) -> Path:
    return CACHE / "usaspending_tx" / request_key(body)


def pull(refresh: bool = False) -> Path:
    body = build_request()
    out = cache_dir_for(body)
    if (out / "meta.json").exists() and not refresh:
        print(f"[transactions] cache hit {out}")
        return out
    out.mkdir(parents=True, exist_ok=True)
    (out / "request.json").write_text(json.dumps(body, indent=2))
    s = _session()
    started = datetime.now(UTC)
    r = s.post(f"{API}/download/transactions/", data=json.dumps(body), timeout=120)
    r.raise_for_status()
    job = r.json()
    print(f"[transactions] job {job['file_name']}")
    while True:
        time.sleep(POLL_SECONDS)
        st = s.get(job["status_url"], timeout=60).json()
        print(f"[transactions] status={st['status']} rows={st.get('total_rows')} secs={st.get('seconds_elapsed')}")
        if st["status"] == "finished":
            break
        if st["status"] == "failed":
            raise RuntimeError(f"download failed: {st.get('message')}")
    zpath = out / "download.zip"
    with s.get(job["file_url"], stream=True, timeout=900) as resp:
        resp.raise_for_status()
        with zpath.open("wb") as f:
            for chunk in resp.iter_content(1 << 20):
                f.write(chunk)
    extract = out / "_extract"
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        z.extractall(extract)
    parts = sorted(str(extract / n) for n in names if n.split("/")[-1].startswith("Contracts_PrimeTransactions"))
    if not parts:
        raise RuntimeError(f"no Contracts_PrimeTransactions files in download: {names}")
    target = out / "transactions.parquet"
    sel = ", ".join(f'"{c}"' for c in TX_COLUMNS)
    con = duckdb.connect()
    con.execute(f"COPY (SELECT {sel} FROM read_csv({parts!r}, all_varchar=true, header=true)) "
                f"TO '{target}' (FORMAT parquet, COMPRESSION zstd)")
    n = con.execute(f"SELECT count(*) FROM '{target}'").fetchone()[0]
    con.close()
    shutil.rmtree(extract)
    zip_bytes = zpath.stat().st_size
    zpath.unlink()
    meta = {
        "source": "USAspending API /api/v2/download/transactions/",
        "pulled_at_utc": started.isoformat(),
        "finished_at_utc": datetime.now(UTC).isoformat(),
        "request_key": request_key(body),
        "download_file_name": job["file_name"],
        "zip_bytes_deleted_after_conversion": zip_bytes,
        "files": {"transactions": {"source_files": [Path(p).name for p in parts], "rows": n,
                                   "parquet": target.name, "sha256": sha256_file(target)}},
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true")
    pull(refresh=ap.parse_args().refresh)


if __name__ == "__main__":
    main()
