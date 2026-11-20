"""Agent loop tests with a scripted fake client (no API spend)."""
from types import SimpleNamespace as NS

import pytest

from supplier_graph.agent import agent as A
from supplier_graph.agent import costs


class FakeClient:
    def __init__(self, responses):
        self._r = list(responses)
        self.calls = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})
        return self._r.pop(0)


def usage(i=10, o=20):
    return NS(input_tokens=i, output_tokens=o, cache_creation_input_tokens=0, cache_read_input_tokens=0,
              model_dump=lambda: {"input_tokens": i, "output_tokens": o})


def tool_use(name, inp, id_="t1"):
    return NS(type="tool_use", name=name, input=inp, id=id_)


def text(t):
    return NS(type="text", text=t)


@pytest.fixture(autouse=True)
def isolated_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(costs, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(A, "RUN_DIR", tmp_path / "runs")


def test_parse_final_json():
    t = 'Answer.\n```json\n{"answerable": false, "answer": null, "citations": []}\n```'
    assert A.parse_final_json(t) == {"answerable": False, "answer": None, "citations": []}
    assert A.parse_final_json("no block") is None


def test_loop_executes_tools_and_logs(monkeypatch):
    monkeypatch.setitem(A.TOOL_FUNCS, "country_risk", lambda i: {"country_iso3": i["country_iso3"], "in_risk_feed": False})
    final = 'Not covered [country:ATA].\n```json\n{"answerable": false, "answer": null, "citations": ["country:ATA"]}\n```'
    fc = FakeClient([NS(content=[tool_use("country_risk", {"country_iso3": "ATA"})], stop_reason="tool_use",
                        usage=usage(), model="claude-opus-5-5"),
                     NS(content=[text(final)], stop_reason="end_turn", usage=usage(), model="claude-opus-5-5")])
    run = A.ask("Risk for Antarctica?", client=fc)
    assert run.steps == 2 and run.tool_calls[0].name == "country_risk" and run.final_json["answerable"] is False
    assert run.cost_usd == pytest.approx(2 * costs.call_cost("claude-opus-5-5", usage()))
    sent = fc.calls[1]["messages"][-1]["content"][0]
    assert sent["type"] == "tool_result" and sent["tool_use_id"] == "t1"
    assert fc.calls[0]["output_config"] == {"effort": A.EFFORT} and "tool_choice" not in fc.calls[0]
    assert (A.RUN_DIR / f"{run.run_id}.jsonl").exists()


def test_tool_exception_returned_as_error(monkeypatch):
    def boom(_):
        raise RuntimeError("bad")
    monkeypatch.setitem(A.TOOL_FUNCS, "award_details", boom)
    fc = FakeClient([NS(content=[tool_use("award_details", {"award_id": "X"})], stop_reason="tool_use", usage=usage(), model="m"),
                     NS(content=[text("cannot")], stop_reason="end_turn", usage=usage(), model="m")])
    run = A.ask("q", client=fc)
    assert run.tool_calls[0].is_error and fc.calls[1]["messages"][-1]["content"][0]["is_error"]


def test_budget_guard_stops_before_call(monkeypatch):
    monkeypatch.setenv("BUDGET_USD", "0.0")
    fc = FakeClient([])
    run = A.ask("q", client=fc)
    assert run.error.startswith("budget") and fc.calls == []


def test_tools_are_read_only_named_queries():
    names = {t["name"] for t in A.TOOL_SCHEMAS}
    assert names == {"resolve_supplier", "run_query", "country_risk", "award_details"}
    rq = next(t for t in A.TOOL_SCHEMAS if t["name"] == "run_query")
    assert "enum" in rq["input_schema"]["properties"]["query_name"]


@pytest.mark.data
def test_resolver_handles_typos_and_absent_names():
    from supplier_graph.agent.tools import resolve_supplier
    assert resolve_supplier("Sikorsky Aircraft Corp")["candidates"][0]["supplier_id"] == "UTJWTSLMFNG4"
    assert resolve_supplier("Zyxwvut Quantum Widgets Ltd")["candidates"] == []


@pytest.mark.live
def test_live_agent_answers_with_citations():
    run = A.ask("Which awarding offices depend on Sikorsky Aircraft Corporation?")
    assert run.final_json and run.final_json["citations"]


# ---- how the app presents an answer (agent/present.py)
def test_status_declined_shows_reason_not_citations():
    from supplier_graph.agent.present import DECLINED, PARTLY, status
    label, cites, reason = status({"answerable": False, "answer": None, "citations": ["country:JPN"],
                                   "no_data": "Japan is not in the risk feed"})
    assert (label, cites, reason) == (DECLINED, [], "Japan is not in the risk feed")
    label, cites, reason = status({"answerable": False, "answer": ["X1"], "citations": ["supplier:X1"],
                                   "no_data": "no risk estimate"})
    assert (label, cites, reason) == (PARTLY, ["supplier:X1"], "no risk estimate")
    assert status({"answerable": True, "answer": 3, "citations": ["office:N1"]})[:2] == ("Answered", ["office:N1"])
    assert status({"answerable": False, "answer": None, "citations": []})[2]  # a reason even if the model gave none


def test_trace_lines_are_plain_language():
    from supplier_graph.agent.present import describe, plural
    assert describe("country_risk", {"country_iso3": "JPN"}, {"country_name": "Japan", "in_risk_feed": False},
                    0.1) == "Looked up country risk for Japan → not in feed (0.1s)"
    assert describe("run_query", {"query_name": "country_exposure", "params": {"country_iso3": "JPN"}},
                    {"rows": [1, 2]}, 0.25) == "Ran query “country exposure” for country_iso3 JPN → 2 rows (0.2s)"
    assert "no match" in describe("resolve_supplier", {"name": "Acme"}, {"candidates": []}, 0.0)
    assert (plural(1, "tool call"), plural(2, "tool call")) == ("1 tool call", "2 tool calls")
