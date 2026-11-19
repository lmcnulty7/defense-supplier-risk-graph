import json

import pandas as pd
import pytest

from supplier_graph.agent.agent import CAVEAT_PREFIX, ToolCall, skill_caveat
from supplier_graph.config import REPORTS
from supplier_graph.ingest import risk_feed
from supplier_graph.risk.backtest import (
    BASE_FEATURES,
    FEED_FEATURES,
    eligible_feed_features,
    likelihood_features,
)


def _fixture() -> pd.DataFrame:
    return pd.read_csv(risk_feed.FIXTURE_PATH, dtype={"schema_version": str})


def _write(tmp_path, df: pd.DataFrame, name: str = "feed.csv") -> str:
    p = tmp_path / name
    df.to_csv(p, index=False)
    return str(p)


def _v11(skill: str, version: str = "1.1.0") -> pd.DataFrame:
    df = _fixture()
    df["schema_version"] = version
    df["skill_status"] = skill
    return df


def test_fixture_loads_and_is_flagged():
    feed = risk_feed.load("")
    assert feed.is_fixture and "fixture" in feed.note
    assert set(risk_feed.CORE_COLUMNS) <= set(feed.df.columns)
    assert feed.skill_status == risk_feed.SKILL_UNKNOWN and not feed.likelihood_allowed


@pytest.mark.parametrize("skill,allowed", [("not_demonstrated", False), ("beats_base_rate", True),
                                           ("beats_country_rate", True)])
def test_each_skill_status(tmp_path, skill, allowed):
    feed = risk_feed.load(_write(tmp_path, _v11(skill)))
    assert feed.skill_status == skill and (feed.df["skill_status"] == skill).all()
    assert feed.likelihood_allowed is allowed
    assert feed.describe()["skill_text"] == risk_feed.SKILL_TEXT[skill]
    # skill only makes a feed input eligible; the backtest gate decides adoption
    assert eligible_feed_features(skill) == (FEED_FEATURES if allowed else [])
    assert likelihood_features(skill) == BASE_FEATURES
    assert likelihood_features(skill, ["feed_p_escalation"]) == BASE_FEATURES + (["feed_p_escalation"] if allowed else [])


def test_v10_without_skill_is_unknown_and_not_allowed(tmp_path):
    feed = risk_feed.load(_write(tmp_path, _fixture()))
    assert feed.skill_status == "unknown" and not risk_feed.likelihood_allowed(feed.skill_status)
    assert likelihood_features("unknown", FEED_FEATURES) == BASE_FEATURES
    assert likelihood_features(None, FEED_FEATURES) == BASE_FEATURES


def test_rejects_unknown_skill_value(tmp_path):
    with pytest.raises(risk_feed.SchemaError, match="unknown skill_status"):
        risk_feed.load(_write(tmp_path, _v11("great")))


def test_rejects_mixed_skill_values(tmp_path):
    df = _v11("not_demonstrated")
    df.loc[0, "skill_status"] = "beats_base_rate"
    with pytest.raises(risk_feed.SchemaError, match="constant"):
        risk_feed.load(_write(tmp_path, df))


def test_v11_requires_skill_status(tmp_path):
    df = _fixture()
    df["schema_version"] = "1.1.0"
    with pytest.raises(risk_feed.SchemaError, match="requires a skill_status"):
        risk_feed.load(_write(tmp_path, df))


def test_v2_accepted_with_skill_and_label(tmp_path):
    df = _v11("beats_base_rate", "2.0.0")
    df["label"] = "UCDP-based escalation (hypothetical v2 label)"
    feed = risk_feed.load(_write(tmp_path, df))
    assert feed.schema_version == "2.0.0" and feed.label.startswith("UCDP")
    assert list(feed.df.columns) == risk_feed.OUT_COLUMNS


def test_v2_without_skill_rejected(tmp_path):
    df = _fixture()
    df["schema_version"] = "2.0.0"
    with pytest.raises(risk_feed.SchemaError):
        risk_feed.load(_write(tmp_path, df))


def test_rejects_schema_major_3(tmp_path):
    with pytest.raises(risk_feed.SchemaError, match="major 3"):
        risk_feed.load(_write(tmp_path, _v11("not_demonstrated", "3.0.0")))


def test_extra_trailing_columns_tolerated(tmp_path):
    df = _v11("not_demonstrated", "1.2.0")
    df["some_future_column"] = 1
    assert list(risk_feed.load(_write(tmp_path, df)).df.columns) == risk_feed.OUT_COLUMNS


def test_manifest_pointer_resolution_and_sha(tmp_path):
    df = _v11("not_demonstrated")
    _write(tmp_path, df)
    sha = risk_feed._sha256(tmp_path / "feed.csv")
    (tmp_path / "latest.json").write_text(json.dumps({"schema_version": "1.1.0", "csv": "feed.csv",
                                                      "sha256": {"feed.csv": sha}}))
    feed = risk_feed.load(str(tmp_path / "latest"))
    assert not feed.is_fixture and len(feed.df) == len(df)
    (tmp_path / "latest.json").write_text(json.dumps({"schema_version": "1.1.0", "csv": "feed.csv",
                                                      "sha256": {"feed.csv": "0" * 64}}))
    with pytest.raises(risk_feed.SchemaError, match="sha256"):
        risk_feed.load(str(tmp_path / "latest"))


def test_newer_major_folder_is_noticed_not_followed(tmp_path):
    for v, ver in (("v1", "1.1.0"), ("v2", "2.0.0")):
        d = tmp_path / v
        d.mkdir()
        _write(d, _v11("not_demonstrated", ver))
        (d / "latest.json").write_text(json.dumps({"schema_version": ver, "csv": "feed.csv"}))
    feed = risk_feed.load(str(tmp_path / "v1" / "latest"))
    assert feed.schema_version == "1.1.0" and any("v2" in n for n in feed.notices)


def test_rejects_non_monday(tmp_path):
    df = _fixture()
    df.loc[0, "week_start"] = "2026-07-07"
    with pytest.raises(risk_feed.SchemaError):
        risk_feed.load(_write(tmp_path, df))


def test_latest_by_country_one_row_each():
    latest = risk_feed.latest_by_country(risk_feed.load(""))
    assert latest["country_iso3"].is_unique


def test_snapshot_copies_and_validates(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write(src, _v11("not_demonstrated"))
    (src / "latest.json").write_text(json.dumps({"schema_version": "1.1.0", "csv": "feed.csv"}))
    man = risk_feed.snapshot(str(src / "latest"), dest=tmp_path / "snap")
    assert man["skill_status"] == "not_demonstrated" and (tmp_path / "snap" / "feed.csv").exists()
    assert risk_feed.load(str(tmp_path / "snap" / "latest")).skill_status == "not_demonstrated"


def test_committed_snapshot_is_the_staged_feed():
    """Clean-clone reproducibility: stage reads the committed snapshot, whose sha256 matches its manifest."""
    man = json.loads((risk_feed.SNAPSHOT_DIR / "latest.json").read_text())
    feed = risk_feed.load()
    assert not feed.is_fixture and feed.source_path.endswith(man.get("parquet") or man["csv"])
    assert feed.skill_status == man["skill_status"]


# ---- agent answers always carry the skill status when they used a country-risk number
def _risk_call(skill: str) -> ToolCall:
    return ToolCall("country_risk", {"country_iso3": "SAU"},
                    {"in_risk_feed": True, "p_escalation": 0.06, "skill_status": skill}, 0.0)


@pytest.mark.parametrize("skill", ["not_demonstrated", "unknown", "beats_base_rate", "beats_country_rate"])
def test_caveat_states_skill_for_every_status(skill):
    out = skill_caveat("Saudi Arabia: p = 0.06.", [_risk_call(skill)])
    assert CAVEAT_PREFIX in out and skill in out
    if skill in ("beats_base_rate", "beats_country_rate"):  # also says how the tool uses it (from the backtest gate)
        assert "termination-likelihood score" in out


def test_caveat_not_added_when_model_states_skill():
    assert skill_caveat("p = 0.06 (skill: beats base rate).", [_risk_call("beats_base_rate")]) == \
        "p = 0.06 (skill: beats base rate)."


def test_caveat_from_query_summary_and_not_duplicated():
    tc = ToolCall("run_query", {}, {"summary": {"p_escalation": 0.06, "risk_skill_status": "not_demonstrated"}}, 0.0)
    once = skill_caveat("x", [tc])
    assert CAVEAT_PREFIX in once and skill_caveat(once, [tc]) == once
    assert skill_caveat("no risk numbers here", [ToolCall("run_query", {}, {"summary": {}}, 0.0)]) == \
        "no risk numbers here"


def test_v2_feed_is_staged_with_its_label():
    man = json.loads((risk_feed.SNAPSHOT_DIR / "latest.json").read_text())
    feed = risk_feed.load()
    if man["schema_version"].startswith("2."):
        assert feed.label == man["label"] == "escalated_next4w_ucdp"
        assert "UCDP" in man["attribution"] and "ACLED" not in man["attribution"]


@pytest.mark.data
def test_backtest_feed_gate_follows_its_rule():
    """p_escalation enters the likelihood model only if the pre-registered gate adopted it."""
    fp = json.loads((REPORTS / "risk_backtest.json").read_text())["feed_policy"]
    bt_features = json.loads((REPORTS / "risk_backtest.json").read_text())["features"]
    for name, g in fp["candidates"].items():
        assert (name in bt_features) == g["adopted"]
        if g["adopted"]:
            assert risk_feed.likelihood_allowed(fp["skill_status"])
            assert g["ap_gain"]["ci"][0] > 0 and g["auc_gain"]["mean"] >= 0
            assert all(c >= 0.8 for c in g["coverage_feed_exposed_supplier_years"].values())
    assert risk_feed.feed_in_likelihood() == ("feed_p_escalation" in fp["adopted"])
