"""Stage cached pulls into DuckDB (data/supplier_graph.duckdb).

Tables
  awards          one row per prime award (contract_award_unique_key)
  subawards       one row per reported subaward
  entity_records  one row per distinct (role, identifiers, name, address) observation, the ER input
  risk_feed       validated MENA risk feed (all weeks) + provenance columns
  stage_meta      provenance of every staged source (request key, pull time, row counts)

Sole-source definition (documented in docs/design_doc.md): extent_competed_code in
B (not available for competition), C (not competed), G (not competed under SAP),
NDO (non-competitive delivery order).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import duckdb
import pandas as pd
import pycountry

from supplier_graph.config import DUCKDB_PATH
from supplier_graph.ingest import risk_feed, sam
from supplier_graph.ingest.usaspending import build_request, cache_dir_for

SOLE_SOURCE_CODES = ("B", "C", "G", "NDO")


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DUCKDB_PATH), read_only=read_only)


def stage() -> dict:
    cdir = cache_dir_for(build_request())
    meta = json.loads((cdir / "meta.json").read_text())
    prime, sub = cdir / "prime.parquet", cdir / "subawards.parquet"
    con = connect()
    codes = ", ".join(f"'{c}'" for c in SOLE_SOURCE_CODES)
    con.execute(f"""
    CREATE OR REPLACE TABLE awards AS
    SELECT
      contract_award_unique_key                           AS award_id,
      award_id_piid                                       AS piid,
      parent_award_id_piid                                AS parent_piid,
      TRY_CAST(total_obligated_amount AS DOUBLE)          AS obligated,
      TRY_CAST(potential_total_value_of_award AS DOUBLE)  AS potential_value,
      TRY_CAST(award_base_action_date AS DATE)            AS base_action_date,
      TRY_CAST(award_latest_action_date AS DATE)          AS latest_action_date,
      TRY_CAST(award_latest_action_date_fiscal_year AS INTEGER) AS latest_action_fy,
      TRY_CAST(period_of_performance_current_end_date AS DATE) AS pop_end_date,
      awarding_sub_agency_code                            AS sub_agency_code,
      awarding_sub_agency_name                            AS sub_agency_name,
      awarding_office_code                                AS office_code,
      awarding_office_name                                AS office_name,
      NULLIF(recipient_uei, '')                           AS recipient_uei,
      recipient_name, recipient_name_raw,
      NULLIF(cage_code, '')                               AS cage_code,
      NULLIF(recipient_parent_uei, '')                    AS recipient_parent_uei,
      recipient_parent_name,
      recipient_country_code, recipient_address_line_1, recipient_city_name,
      recipient_state_code, recipient_zip_4_code,
      NULLIF(primary_place_of_performance_country_code, '') AS pop_country,
      primary_place_of_performance_country_name           AS pop_country_name,
      primary_place_of_performance_city_name              AS pop_city,
      primary_place_of_performance_state_code             AS pop_state,
      award_type,
      product_or_service_code                             AS psc,
      product_or_service_code_description                 AS psc_description,
      naics_code, naics_description,
      CASE WHEN dod_acquisition_program_code IN ('', '000') THEN NULL ELSE dod_acquisition_program_code END AS program_code,
      dod_acquisition_program_description                 AS program_name,
      dod_claimant_program_code, dod_claimant_program_description,
      NULLIF(extent_competed_code, '')                    AS extent_competed_code,
      extent_competed,
      TRY_CAST(number_of_offers_received AS INTEGER)      AS offers_received,
      COALESCE(extent_competed_code IN ({codes}), FALSE)  AS is_sole_source,
      prime_award_base_transaction_description            AS description,
      usaspending_permalink                               AS permalink
    FROM read_parquet('{prime}')
    """)
    con.execute(f"""
    CREATE OR REPLACE TABLE subawards AS
    SELECT
      md5(concat_ws('|', prime_award_unique_key, subaward_number, subaward_sam_report_id,
                    subaward_action_date, subawardee_uei, subaward_amount)) AS subaward_id,
      prime_award_unique_key                    AS prime_award_id,
      prime_award_piid, NULLIF(prime_awardee_uei, '') AS prime_awardee_uei, prime_awardee_name,
      prime_award_awarding_office_code          AS office_code,
      subaward_number, subaward_sam_report_id,
      TRY_CAST(subaward_amount AS DOUBLE)       AS amount,
      TRY_CAST(subaward_action_date AS DATE)    AS action_date,
      TRY_CAST(subaward_action_date_fiscal_year AS INTEGER) AS action_fy,
      NULLIF(subawardee_uei, '')                AS subawardee_uei,
      subawardee_name, subawardee_dba_name,
      NULLIF(subawardee_parent_uei, '')         AS subawardee_parent_uei,
      subawardee_parent_name,
      subawardee_country_code, subawardee_address_line_1, subawardee_city_name,
      subawardee_state_code, subawardee_zip_code,
      NULLIF(subaward_primary_place_of_performance_country_code, '') AS pop_country,
      subaward_primary_place_of_performance_country_name AS pop_country_name,
      subaward_description                      AS description,
      usaspending_permalink                     AS permalink
    FROM read_parquet('{sub}')
    """)
    # Country codes: prime files use ISO alpha-3, subaward files mix alpha-2 and alpha-3.
    cdf = pd.DataFrame([{"alpha2": c.alpha_2, "alpha3": c.alpha_3, "name": c.name} for c in pycountry.countries])
    cdf = pd.concat([cdf, pd.DataFrame([{"alpha2": "UK", "alpha3": "GBR", "name": "United Kingdom"}])])
    con.register("cdf", cdf)
    con.execute("CREATE OR REPLACE TABLE countries AS SELECT * FROM cdf")
    con.unregister("cdf")
    for col in ("pop_country", "subawardee_country_code"):
        con.execute(f"""UPDATE subawards SET {col} = c.alpha3 FROM countries c
                        WHERE length(subawards.{col}) = 2 AND upper(subawards.{col}) = c.alpha2""")
    con.execute("""UPDATE awards SET pop_country = c.alpha3 FROM countries c
                   WHERE length(awards.pop_country) = 2 AND upper(awards.pop_country) = c.alpha2""")
    # Exact duplicate subaward reports (same key fields) collapse to one row.
    con.execute("""
    CREATE OR REPLACE TABLE subawards AS
    SELECT * FROM subawards QUALIFY row_number() OVER (PARTITION BY subaward_id
      ORDER BY action_date, description, permalink, pop_country, subawardee_parent_uei) = 1
    """)
    # SAM.gov enrichment (whatever is cached so far; budget-limited, see ingest/sam.py)
    sam_rows = sam.load_cache()
    sam_df = pd.DataFrame(sam_rows, columns=["uei", "cage_code", "legal_business_name", "dba_name",
                                             "registration_status", "address_line_1", "city", "state",
                                             "zip", "country_iso3", "pulled_at_utc"])
    con.register("sam_df", sam_df)
    con.execute("""CREATE OR REPLACE TABLE sam_entities AS SELECT * FROM sam_df
                   QUALIFY row_number() OVER (PARTITION BY uei
                     ORDER BY (registration_status = 'Active') DESC, pulled_at_utc DESC) = 1""")
    con.unregister("sam_df")

    con.execute("""
    CREATE OR REPLACE TABLE entity_observations AS
    WITH obs AS (
      SELECT 'prime_recipient' AS role, recipient_uei AS uei, cage_code, recipient_parent_uei AS parent_uei,
             recipient_parent_name AS parent_name, recipient_name AS name, recipient_address_line_1 AS address,
             recipient_city_name AS city, recipient_state_code AS state, recipient_zip_4_code AS zip,
             recipient_country_code AS country, award_id AS source_id, obligated AS amount
      FROM awards
      UNION ALL
      SELECT 'prime_recipient_raw', recipient_uei, cage_code, recipient_parent_uei, recipient_parent_name,
             recipient_name_raw, recipient_address_line_1, recipient_city_name, recipient_state_code,
             recipient_zip_4_code, recipient_country_code, award_id, obligated
      FROM awards WHERE recipient_name_raw IS NOT NULL AND recipient_name_raw <> recipient_name
      UNION ALL
      SELECT 'subawardee', subawardee_uei, NULL, subawardee_parent_uei, subawardee_parent_name,
             subawardee_name, subawardee_address_line_1, subawardee_city_name, subawardee_state_code,
             subawardee_zip_code, subawardee_country_code, subaward_id, amount
      FROM subawards
      UNION ALL
      SELECT 'sam_entity', uei, cage_code, NULL, NULL, legal_business_name, address_line_1, city, state,
             zip, country_iso3, 'sam:' || uei, NULL
      FROM sam_entities
    )
    SELECT md5(concat_ws('|', role, coalesce(uei,''), coalesce(cage_code,''), coalesce(name,''),
                         coalesce(address,''), coalesce(city,''), coalesce(zip,''))) AS record_id, *
    FROM obs WHERE name IS NOT NULL AND name <> ''
    """)
    # One deterministic representative per record_id: the most frequent (parent, state, country)
    # combination, ties broken by value. Identity fields are constant per record_id (they form the hash).
    con.execute("""
    CREATE OR REPLACE TABLE entity_records AS
    WITH combo AS (
      SELECT record_id, role, uei, cage_code, name, address, city, zip, parent_uei, parent_name, state, country,
             count(*) AS n, sum(amount) AS amt
      FROM entity_observations GROUP BY ALL
    ), tot AS (SELECT record_id, sum(n) AS n_observations, sum(amt) AS total_amount FROM combo GROUP BY 1)
    SELECT c.* EXCLUDE (n, amt), t.n_observations, t.total_amount
    FROM combo c JOIN tot t USING (record_id)
    QUALIFY row_number() OVER (PARTITION BY record_id ORDER BY c.n DESC, c.parent_uei NULLS LAST,
                               c.parent_name NULLS LAST, c.state NULLS LAST, c.country NULLS LAST) = 1
    ORDER BY record_id
    """)
    feed = risk_feed.load()
    fdf = feed.df.copy()
    fdf["feed_source"] = feed.source_path
    fdf["feed_is_fixture"] = feed.is_fixture
    con.register("fdf", fdf)
    con.execute("CREATE OR REPLACE TABLE risk_feed AS SELECT * FROM fdf")
    con.unregister("fdf")

    counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("awards", "subawards", "entity_records", "sam_entities", "risk_feed")}
    stage_meta = {
        "staged_at_utc": datetime.now(UTC).isoformat(),
        "usaspending": meta,
        "risk_feed": feed.describe(),
        "sam_entities_cached": len(sam_df),
        "row_counts": counts,
    }
    con.execute("CREATE OR REPLACE TABLE stage_meta (k VARCHAR, v JSON)")
    con.execute("INSERT INTO stage_meta VALUES ('stage', ?)", [json.dumps(stage_meta)])
    con.close()
    print(json.dumps(stage_meta["row_counts"], indent=2), "\n", feed.note, f"skill_status={feed.skill_status}",
          *feed.notices)
    return stage_meta


def sam_candidate_ueis(limit: int = 500) -> list[str]:
    """Subawardee UEIs (no CAGE in subaward data), largest subaward totals first."""
    con = connect(read_only=True)
    rows = con.execute("""
      SELECT subawardee_uei FROM subawards WHERE subawardee_uei IS NOT NULL
      GROUP BY 1 ORDER BY sum(amount) DESC NULLS LAST LIMIT ?""", [limit]).fetchall()
    con.close()
    return [r[0] for r in rows]


if __name__ == "__main__":
    stage()
