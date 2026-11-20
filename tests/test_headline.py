"""reports/HEADLINE.md is current, and the presentation documents agree with it.

Two checks:
* presence: each document quotes the exact headline strings it is responsible for;
* conflicts: in every presentation document, numbers of the kinds that have drifted before (run fractions, AUCs,
  spend, test counts, end-to-end ER rates) must be one of the headline values. A stale number fails the test.
"""

from __future__ import annotations

import json
import re

import pytest

from supplier_graph.config import REPORTS, ROOT
from supplier_graph.headline import build
from supplier_graph.headline import tests_collected as n_tests_collected

pytestmark = pytest.mark.data

H = {h["key"]: h for h in json.loads((REPORTS / "headline.json").read_text())}

# the termination-likelihood model card
LIKELIHOOD_KEYS = ["lk_features", "lk_split", "lk_n_train", "lk_train_pos", "bt_n", "bt_pos", "lk_base_rate", "top5_k",
                   "auc_gain", "ap_gain", "conv_share", "cluster_share"]
LIKELIHOOD_TABLE = ["auc_model", "auc_size", "auc_prior", "auc_flags", "top5_model", "top5_size", "top5_prior",
                    "top5_flags"]
# the four-line model summary (app Likelihood x impact tab, site slide)
SUMMARY_KEYS = ["lk_split", "top5_model", "lk_base_rate", "top5_size", "top5_flags"]
# texts written after HEADLINE.md existed: every number in them must be a headline value. Exceptions: the random
# ROC-AUC (0.5), the "above 1" threshold of excess_vs_size, "top 5%" and "95% intervals". (The agent tab's
# "about N questions left" is live state from the spend ledger, not a reported result, so it is not listed.)
STRICT = ["docs/model_card_likelihood.md", "app:header", "app:likelihood", "app:about", "site:title",
          "site:likelihood-model"]
STRICT_OK = {"0.5", "1", "5", "95"}

DOCS = {
    "README.md": ["n_prime_awards", "total_obligations", "n_suppliers", "auc_flags", "auc_size", "or_sole",
                  "nm_haiku", "nm_guard", "er_v1_correct", "er_blocking_correct", "er_op_absent", "spend",
                  "feed_skill"],
    "FINAL_REPORT.md": ["n_prime_awards", "total_obligations", "n_subawards", "n_suppliers", "n_parents", "n_offices",
                        "n_transactions", "flagged_share", "er_id_pr", "er_syn_pr", "er_v1_correct",
                        "er_blocking_correct", "er_op_absent", "er_op_wrong", "er_op_correct", "neo4j_blast",
                        "auc_flags", "auc_size", "auc_model", "auc_gain", "ap_gain", "or_conc", "or_sole", "or_geo",
                        "cluster_share", "v1_opus", "v1_opus_ref", "v1_haiku", "v1_haiku_fab", "ch_opus",
                        "ch_opus_traps", "ch_haiku", "ch_guard_traps", "ch_notools", "nm_haiku", "nm_guard",
                        "nm_opus", "guard_replay", "judge_blatant", "judge_subtle", "spend", "tests", "feed_skill"],
    "site/index.html": ["n_prime_awards", "n_suppliers", "er_v1_correct", "er_blocking_correct", "neo4j_blast",
                        "v1_opus", "spend", "cost_ratio"],
    "docs/brief.md": ["n_prime_awards", "auc_flags", "auc_size", "nm_guard", "spend"],
    "docs/demo_script.md": ["er_v1_correct", "er_blocking_correct", "nm_haiku", "nm_guard", "spend"],
    "docs/INTERVIEW_WALKTHROUGH.md": [k for k in H if k != "tests"],
    "docs/model_card_likelihood.md": ["n_transactions", "feed_gate_p", *LIKELIHOOD_KEYS, *LIKELIHOOD_TABLE],
    # rendered Streamlit text (AppTest), see _app_texts()
    "app:likelihood": SUMMARY_KEYS,
    "app:header": [],
    "app:about": ["n_prime_awards", "n_subawards", "n_suppliers", "pulled", "feed_snapshot", "feed_file", "feed_version",
                  "er_blocking_correct", "er_blocking_wrong", "top5_model", "lk_base_rate", "v1_opus", "v1_opus_ref",
                  "conv_share", "sub_coverage", "cluster_share"],
    "site:title": ["n_prime_awards", "total_obligations", "n_suppliers", "v1_opus", "spend"],
    "site:likelihood-model": SUMMARY_KEYS,
}
# documents rewritten later in the presentation-readiness plan are enforced once rewritten
PENDING: set[str] = set()
DOCS = {k: v for k, v in DOCS.items() if k not in PENDING}
SCAN = list(DOCS)


_APP: dict[str, str] = {}


def _app_texts() -> dict[str, str]:
    """Render the Streamlit app headless once and return the model descriptions as displayed."""
    if not _APP:
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=120).run()
        assert not at.exception, at.exception
        outlook, about = at.tabs[1], at.tabs[4]
        assert (outlook.label, about.label) == ("Likelihood x impact", "About the data")
        (_APP["app:likelihood"],) = [m.value for m in outlook.markdown if "**What it predicts:**" in m.value]
        # everything in the About tab, including the collapsed feed details
        _APP["app:about"] = "\n".join(m.value for m in about.markdown)
        assert "**Termination likelihood:**" in _APP["app:about"] and "Feed details" in [e.label for e in about.expander]
        # header: title, tagline, small caption (above the tabs)
        _APP["app:header"] = "\n".join([at.title[0].value, at.main.markdown[0].value, at.main.caption[0].value])
    return _APP


def _text(rel: str) -> str:
    if rel.startswith("app:"):
        return _app_texts()[rel]
    if rel.startswith("site:"):
        page = (ROOT / "site" / "index.html").read_text()
        m = re.search(rf'<section class="slide" id="{rel[5:]}">(.*?)</section>', page, flags=re.S)
        assert m, f"site has no slide {rel[5:]!r}"
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).replace("&amp;", "&").replace("&#x27;", "'")
    t = (ROOT / rel).read_text()
    if rel.endswith(".html"):
        t = re.sub(r"<svg.*?</svg>", " ", t, flags=re.S)
        t = re.sub(r"<[^>]+>", " ", t)
        t = re.sub(r"\s+", " ", t)
    return t.replace("&amp;", "&").replace("&#x27;", "'").replace("&quot;", '"')


def _allowed(*keys: str) -> set[str]:
    out: set[str] = set()
    for k in keys:
        for s in (H[k]["text"], *H[k]["alts"]):
            out |= set(re.findall(r"\d[\d,.]*", s))
    return out


def test_headline_file_is_current():
    fresh = {h.key: h.text for h in build(n_tests_collected())}
    assert fresh == {k: v["text"] for k, v in H.items()}, "run `make headline`"


@pytest.mark.parametrize("doc", sorted(DOCS))
def test_document_quotes_headline_numbers(doc):
    text = _text(doc)
    missing = [f"{k} = {H[k]['text']!r}" for k in DOCS[doc] if H[k]["text"] not in text]
    assert not missing, f"{doc} lacks: {missing}"


FRACTIONS = {"88": ("v1_opus", "v1_haiku", "v1_haiku_guard"), "38": ("ch_opus", "ch_haiku", "ch_notools"),
             "14": ("ch_opus_traps", "ch_haiku_traps", "ch_guard_traps"), "13": ("v1_opus_ref",),
             "8": ("nm_haiku", "nm_guard", "nm_opus"), "44": ("draft_haiku",), "40": ("judge_blatant",),
             "30": ("judge_subtle", "neo4j_blast")}


@pytest.mark.parametrize("doc", SCAN)
def test_no_stale_numbers(doc):
    text = _text(doc)
    bad = []
    for m in re.finditer(r"(?<![\d/.])(\d{1,2})/(88|38|14|13|8|44|40|30)(?![\d/])", text):
        ok = {H[k]["text"] for k in FRACTIONS[m.group(2)]}
        if not any(m.group(0) in s for s in ok) and m.group(0) not in ("14/14", "13/13", "8/8", "38/38", "0/38"):
            bad.append(m.group(0))
    for m in re.finditer(r"(?<![\d.])(\d{2,3})\s+(?:passed|tests\b)", text):
        if f"{m.group(1)} tests" != H["tests"]["text"]:
            bad.append(m.group(0))
    for m in re.finditer(r"\$(\d+\.\d\d)\b", text):  # project spend is the only $X.XX over $1 we quote
        if float(m.group(1)) >= 5 and f"${m.group(1)}" != H["spend"]["text"]:
            bad.append(m.group(0))
    aucs = _allowed("auc_flags", "auc_size", "auc_model", "auc_gain", "auc_prior") | {"0.5", "0.563", "0.56", "0.769", "1", "0.85"}
    for m in re.finditer(r"AUC[^.\n]{0,40}?\b(0\.\d{2,3})\b", text):
        if m.group(1) not in aucs:
            bad.append(m.group(0))
    er = _allowed("er_v1_correct", "er_blocking_correct", "er_op_correct", "er_v1_wrong", "er_blocking_wrong",
                  "er_op_wrong", "er_v1_absent", "er_blocking_absent", "er_op_absent")
    for m in re.finditer(r"(\d{1,2}(?:\.\d{1,2})?)%(?=[^.\n]{0,30}(?:correct link|wrong link|false link|renamed))", text):
        if m.group(1) not in er:
            bad.append(m.group(0) + " (ER)")
    assert not bad, f"{doc}: numbers not in reports/HEADLINE.md: {bad}"


NUM = re.compile(r"\d+(?:[.,]\d+)*")


@pytest.mark.parametrize("doc", STRICT)
def test_new_text_numbers_all_from_headline(doc):
    """Every number in the model descriptions is a HEADLINE.md value (or one of the STRICT_OK constants)."""
    allowed = {n for h in H.values() for n in NUM.findall(h["text"])} | STRICT_OK
    text = _text(doc)
    assert len(text) > 150, f"{doc}: text not found"
    bad = sorted({n for n in NUM.findall(text) if n not in allowed})
    assert not bad, f"{doc}: numbers not in reports/HEADLINE.md: {bad}"
