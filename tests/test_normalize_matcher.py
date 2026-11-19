import random

from supplier_graph.er.labels import PERTURBATIONS, perturb, split_of
from supplier_graph.er.matcher import MatchRecord, score
from supplier_graph.er.normalize import (
    blocking_keys,
    core_name,
    normalize_address,
    normalize_name,
    normalize_zip,
)


def test_normalize_name_suffixes_and_punctuation():
    assert normalize_name("Acme Corporation, Inc.") == "ACME CORP INC"
    assert core_name("The Acme Corporation, Inc.") == "ACME"
    assert core_name("A.B.C. Services L.L.C.") == core_name("ABC SERVICES LLC")


def test_normalize_address_and_zip():
    assert normalize_address("123 North Main Street, Suite 4") == "123 N MAIN ST STE 4"
    assert normalize_zip("12345-6789") == "12345"
    assert normalize_zip(None) == ""


def test_blocking_keys_nonempty():
    keys = blocking_keys("ACME MARINE", "VA", "23510")
    assert any(k.startswith("T1:") for k in keys) and any(k.startswith("Z:") for k in keys)


def test_matcher_prefers_same_entity_over_lookalike():
    a = MatchRecord("ACME MARINE SERVICES INC", "100 Harbor Rd", "Norfolk", "VA", "23510", "USA")
    same = MatchRecord("Acme Marine Svcs, LLC", "100 Harbor Road", "NORFOLK", "VA", "23510-1234", "USA")
    other = MatchRecord("ACME MARINE SERVICES INC", "9 Desert Way", "Phoenix", "AZ", "85001", "USA")
    assert score(a, same) > score(a, other)
    assert score(a, same) >= 0.9


def test_matcher_has_no_identifier_fields():
    fields = set(MatchRecord.__dataclass_fields__)
    assert not fields & {"uei", "cage", "cage_code", "parent_uei", "duns"}


def test_perturb_always_changes_record():
    rng = random.Random(0)
    base = {"name": "GENERAL DYNAMICS CORPORATION", "address": "100 MAIN STREET", "city": "X", "state": "VA",
            "zip": "22030-1234", "country": "USA"}
    for _ in range(50):
        new, applied = perturb(base, rng)
        assert applied and set(applied) <= set(PERTURBATIONS)
        assert new != base


def test_split_is_deterministic():
    assert split_of("ABC123") == split_of("ABC123")
    assert {split_of(f"U{i}") for i in range(200)} == {"dev", "test"}
