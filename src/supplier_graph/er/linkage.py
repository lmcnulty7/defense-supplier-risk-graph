"""Learned evidence linkage: how much should each kind of agreement count?

The rule-based matcher (matcher.score) combines name, address, ZIP, city, state and country with hand-set weights.
This module learns those weights instead: an L2 logistic regression on the agreement evidence (matcher.evidence),
with separate missing-value indicators, fitted on the *dev* split of the automatic label sets. It is the supervised
counterpart of Fellegi-Sunter linkage (log-odds weight per agreement pattern); supervised fits here because the
automatic labels exist. Threshold: max F1 on dev, the same rule as the hand-set matcher. Which matcher to use is
decided on dev F1 only; both are reported on test (er.evaluate).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from supplier_graph.config import REPORTS
from supplier_graph.er.labels import REC_COLS
from supplier_graph.er.matcher import EVIDENCE, MatchRecord, evidence

MODEL_FILE = REPORTS / "er_linkage_model.json"


def evidence_matrix(df: pd.DataFrame) -> np.ndarray:
    rows = []
    for r in df.itertuples(index=False):
        d = r._asdict()
        rows.append(evidence(MatchRecord(**{c: d[f"a_{c}"] for c in REC_COLS}),
                             MatchRecord(**{c: d[f"b_{c}"] for c in REC_COLS})))
    return np.array(rows, dtype=float)


def fit(dev: pd.DataFrame) -> LogisticRegression:
    return LogisticRegression(C=1.0, max_iter=5000).fit(evidence_matrix(dev), dev["label"].to_numpy())


def save(m: LogisticRegression, threshold: float) -> dict:
    out = {"features": EVIDENCE, "coef": [round(float(c), 4) for c in m.coef_[0]],
           "intercept": round(float(m.intercept_[0]), 4), "threshold": threshold,
           "fitted_on": "dev split of the automatic ER label sets (identifier-derived + synthetic, pooled)"}
    MODEL_FILE.write_text(json.dumps(out, indent=2))
    return out


def load_scorer():
    """Probability scorer from the saved model (for resolve.py when the learned matcher is selected)."""
    m = json.loads(MODEL_FILE.read_text())
    w, b = np.array(m["coef"]), m["intercept"]

    def prob(a: MatchRecord, c: MatchRecord) -> float:
        z = float(np.dot(w, evidence(a, c)) + b)
        return round(1 / (1 + np.exp(-z)), 4)
    return prob, m["threshold"]
