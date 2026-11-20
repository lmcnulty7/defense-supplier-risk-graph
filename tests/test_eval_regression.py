"""Evaluation regression suite.

Offline tests (always run when data is built): the golden set's integrity and dual-path verification, scorer
behavior, and regression gates on the last scored live run (reports/eval_results.json).
Live test (RUN_LIVE=1): re-runs a stratified sample of the golden set against the API and applies the same gates.
"""
import json

import pytest

from supplier_graph.config import REPORTS
from supplier_graph.evalh.golden import GoldenItem, load_golden
from supplier_graph.evalh.metrics import answer_correct, citation_exists, tool_call_correct
from supplier_graph.graph import queries as Q
from supplier_graph.stage import connect

pytestmark = pytest.mark.data

GATES = {"answer_correctness": 0.90, "correct_refusal_rate": 0.90, "citation_precision_micro": 0.95,
         "tool_call_correctness": 0.85}


@pytest.fixture(scope="module")
def golden():
    return load_golden()


def test_golden_size_and_mix(golden):
    assert 60 <= len(golden) <= 110
    assert sum(not g.answerable for g in golden) >= 10
    assert all(g.label_source for g in golden)
    assert len({g.qid for g in golden}) == len(golden)


def test_golden_answers_reverify_on_both_paths(golden):
    con = connect(read_only=True)
    for g in golden:
        if g.answerable and g.expected_query in Q.QUERIES:
            s = Q.run(g.expected_query, g.expected_params, "sql", con)
            gr = Q.run(g.expected_query, g.expected_params, "graph")
            assert json.dumps(s, sort_keys=True, default=str) == json.dumps(gr, sort_keys=True, default=str), g.qid
    con.close()


def test_unanswerable_entities_really_absent(golden):
    from supplier_graph.agent.tools import award_details, country_risk, resolve_supplier
    for g in golden:
        if g.template == "absent_supplier":
            assert resolve_supplier(g.expected_params["name"])["candidates"] == []
        if g.template == "absent_award":
            assert "error" in award_details(g.expected_params["award_id"])
        if g.template == "risk_not_in_feed":
            assert country_risk(g.expected_params["country_iso3"])["in_risk_feed"] is False


def _item(**kw):
    base = dict(qid="q", template="t", question="?", answerable=True, answer_type="int", expected=3)
    return GoldenItem(**{**base, **kw})


def test_scorer_rules():
    assert answer_correct(_item(), {"answerable": True, "answer": 3})[0]
    assert answer_correct(_item(), {"answerable": True, "answer": "3"})[0]
    assert not answer_correct(_item(), {"answerable": True, "answer": 4})[0]
    assert answer_correct(_item(), {"answerable": False, "answer": None}) == (False, "false_refusal")
    f = _item(answer_type="float", expected=0.5, tolerance=0.01)
    assert answer_correct(f, {"answerable": True, "answer": 0.505})[0] and not answer_correct(f, {"answerable": True, "answer": 0.52})[0]
    ids = _item(answer_type="ids", expected=["A", "B"])
    assert answer_correct(ids, {"answerable": True, "answer": ["supplier:b", "a"]})[0]
    un = _item(answerable=False, answer_type="null", expected=None)
    assert answer_correct(un, {"answerable": False, "answer": None})[0]
    assert answer_correct(un, {"answerable": True, "answer": 5}) == (False, "answered_unanswerable")
    assert answer_correct(_item(), None) == (False, "no_final_json")


def test_citation_existence_check():
    con = connect(read_only=True)
    aid = con.execute("SELECT award_id FROM awards LIMIT 1").fetchone()[0]
    con.close()
    assert citation_exists(f"award:{aid}") and not citation_exists("award:CONT_AWD_FAKE")
    assert not citation_exists("supplier:NOTREAL00000") and not citation_exists("garbage")


def test_tool_call_alternatives():
    it = _item(expected_query="blast_radius", expected_params={"supplier_id": "X"}, alt_queries=["dependent_offices"])
    alt = {"tool_calls": [{"name": "run_query", "input": {"query_name": "dependent_offices", "params": {"supplier_id": "X"}}}]}
    wrong = {"tool_calls": [{"name": "run_query", "input": {"query_name": "dependent_offices", "params": {"supplier_id": "Y"}}}]}
    assert tool_call_correct(alt, it) is True and tool_call_correct(wrong, it) is False


def test_last_live_run_meets_gates(golden):
    res = json.loads((REPORTS / "eval_results.json").read_text())
    assert res["golden_size"] == len(golden)
    for k, floor in GATES.items():
        assert res["overall"][k] >= floor, f"{k}={res['overall'][k]} < {floor}"
    assert res["overall"]["n_fabricated_citations"] == 0
    assert res["judge"].get("used") is not True or res["judge"]["validation_agreement"] >= 0.85


@pytest.mark.live
def test_live_sample_meets_gates(golden):
    from supplier_graph.agent.agent import ask
    from supplier_graph.evalh.metrics import aggregate, score_run
    from supplier_graph.evalh.run import _stratified
    sample = _stratified(golden, 12)
    scores = [score_run(json.loads(json.dumps(ask(g.question, log_name="pytest_live", tags={"qid": g.qid}).__dict__,
                                                default=lambda o: o.__dict__)), g) for g in sample]
    agg = aggregate(scores)
    assert agg["answer_correctness"] >= 0.8 and agg["correct_refusal_rate"] >= 0.8 and agg["n_fabricated_citations"] == 0


# ---- partial answers: decline the uncovered part without citing it, answer the rest now (evalh/partial.py)
def test_partial_answer_regression_passes_on_logged_runs():
    from supplier_graph.evalh.partial import ITEMS, expected, latest_runs, score
    runs = latest_runs()
    assert {it.qid for it in ITEMS} <= set(runs), "run `make partial-eval` (live) to log the partial-answer questions"
    failed = {it.qid: [k for k, v in r["checks"].items() if not v]
              for it in ITEMS if not (r := score(runs[it.qid], it, expected(it)))["passed"]}
    assert not failed, failed


def test_partial_scorer_rejects_cited_decline_and_offer_to_answer():
    """The pre-fix behavior (decline cites the uncovered country, offers to list suppliers instead of listing them)
    must fail."""
    from supplier_graph.evalh.partial import ITEMS, expected, latest_runs, score
    it = next(i for i in ITEMS if i.country_iso3 == "JPN")
    old = dict(latest_runs()[it.qid])
    old["final_text"] = ("I can't give you an escalation risk for Japan [country:JPN]. The risk feed only covers MENA "
                         "countries. I can list the suppliers with work performed in Japan if that helps.\n\n```json\n"
                         '{"answerable": false, "answer": null, "citations": ["country:JPN"]}\n```')
    old["final_json"] = {"answerable": False, "answer": None, "citations": ["country:JPN"]}
    r = score(old, it, expected(it))
    assert not r["passed"]
    assert not r["checks"]["decline_uncited"] and not r["checks"]["answered_suppliers"]
    assert not r["checks"]["declined_part"]  # no no_data reason
