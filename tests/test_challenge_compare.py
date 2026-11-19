"""Challenge set, grounding guard, acronym rule and comparison gates."""

from __future__ import annotations

import json

import pytest

from supplier_graph.agent.tools import missing_words
from supplier_graph.config import REPORTS
from supplier_graph.er.matcher import MatchRecord, is_acronym_of, score


def test_acronym_rule():
    assert is_acronym_of("GDLS", "GENERAL DYNAMICS LAND SYS")
    assert not is_acronym_of("GD", "GENERAL DYNAMICS")          # needs >= 3 letters / words
    assert not is_acronym_of("GDLS INC", "GENERAL DYNAMICS LAND SYS")
    a = MatchRecord("GDLS INC", "38500 MOUND RD", "STERLING HEIGHTS", "MI", "48310", "USA")
    b = MatchRecord("General Dynamics Land Systems Inc.", "38500 Mound Road", "Sterling Heights", "MI", "48310", "USA")
    assert score(a, b) > score(a, b, acronyms=False)


def test_guard_flags_swapped_word_not_noise():
    assert missing_words("FUTEK ADVANCED SENSOR OPTICS", "FUTEK ADVANCED SENSOR TECH") == ["OPTICS"]
    assert missing_words("PHOENIX INTERNAITONAL SYS", "PHOENIX INTL SYS") == []       # typo of an abbreviated word
    assert missing_words("SARCO HYDRAULICS INOCRPORATED", "SARCO HYDRAULICS") == []    # typo of a legal suffix
    assert missing_words("ACME MARINE", "ACME MARINE REPAIR SVCS") == []               # dropped word is fine


@pytest.mark.data
def test_challenge_set_is_deterministic():
    from supplier_graph.evalh.challenge import CHALLENGE_PATH, ChallengeBuilder
    before = CHALLENGE_PATH.read_text()
    ChallengeBuilder().build()
    assert CHALLENGE_PATH.read_text() == before
    items = [json.loads(line) for line in before.splitlines()]
    assert len(items) >= 50 and sum(not i["answerable"] for i in items) >= 10


@pytest.fixture(scope="module")
def cmp() -> dict:
    p = REPORTS / "comparison.json"
    if not p.exists():
        pytest.skip("run `make compare-offline`")
    return json.loads(p.read_text())["cells"]


def _near_miss(rep: dict) -> int:
    return rep["by_template"]["u_near_miss"]["correct"]


@pytest.mark.data
def test_gates_shipped_agent_on_challenge(cmp):
    r = cmp["challenge/opus_tools"]["reps"][0]
    assert r["answer_correctness"] >= 0.9 and r["correct_refusal_rate"] >= 0.8 and r["n_fabricated_citations"] == 0


@pytest.mark.data
def test_guard_improves_near_miss_without_hurting_answers(cmp):
    base, guard = cmp["challenge/haiku_tools"]["reps"], cmp["challenge/haiku_tools_guard"]["reps"]
    assert min(_near_miss(r) for r in guard) > max(_near_miss(r) for r in base)
    assert min(r["answer_correctness"] for r in guard) >= min(r["answer_correctness"] for r in base)


@pytest.mark.data
def test_no_tools_baseline_never_fabricates(cmp):
    r = cmp["challenge/opus_no_tools"]["reps"][0]
    assert r["n_fabricated_citations"] == 0
