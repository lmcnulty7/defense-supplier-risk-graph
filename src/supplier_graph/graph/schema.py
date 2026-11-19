"""Graph schema shared by the NetworkX backend and the Neo4j loader.

Nodes (label: key property)
  Supplier(supplier_id)  ParentCompany(parent_uei)  Award(award_id)  Office(office_code)
  Country(iso3)          ProductServiceCode(psc)
Edges
  (Award)-[:AWARDED_TO]->(Supplier)              prime award recipient
  (Supplier)-[:SUBCONTRACTS_TO]->(Supplier)      one edge per subaward (key = subaward_id),
                                                 props: prime_award_id, amount, action_date, pop_country
  (Supplier)-[:SUBSIDIARY_OF]->(ParentCompany)
  (Award)-[:PERFORMED_IN]->(Country)             primary place of performance
  (Award)-[:AWARDED_BY]->(Office)
  (Award)-[:CATEGORIZED_AS]->(ProductServiceCode)
Every node and edge carries `prov_source`, `prov_pulled_at`, `prov_request_key` and, where it applies,
`prov_method` (e.g. the ER method that produced a Supplier link).
"""

from __future__ import annotations

NODE_LABELS = ("Supplier", "ParentCompany", "Award", "Office", "Country", "ProductServiceCode")
EDGE_TYPES = ("AWARDED_TO", "SUBCONTRACTS_TO", "SUBSIDIARY_OF", "PERFORMED_IN", "AWARDED_BY", "CATEGORIZED_AS")

PREFIX = {"Supplier": "supplier", "ParentCompany": "parent", "Award": "award", "Office": "office",
          "Country": "country", "ProductServiceCode": "psc"}

# Risk-flag thresholds (documented in docs/design_doc.md)
CONCENTRATION_SHARE = 0.25      # supplier holds >= 25% of an office's obligations ...
CONCENTRATION_MIN_OFFICE = 1e6  # ... for an office with >= $1M total obligations in scope
SOLE_SOURCE_SHARE = 0.50        # >= 50% of the supplier's obligations are non-competed
FOREIGN_SHARE = 0.25            # >= 25% of the supplier's prime obligations performed outside the USA


def nid(label: str, key: str) -> str:
    return f"{PREFIX[label]}:{key}"


def split_nid(node_id: str) -> tuple[str, str]:
    prefix, key = node_id.split(":", 1)
    label = {v: k for k, v in PREFIX.items()}[prefix]
    return label, key
