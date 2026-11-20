"""MENA country risk feed loader.

Where the feed comes from (first match wins):
  1. an explicit path argument (tests, `make feed-snapshot`);
  2. the committed snapshot data/feeds/mena/latest.json (pinned, so every number is reproducible from a clean clone);
  3. MENA_RISK_FEED_PATH (relative paths resolve against the repo root);
  4. the bundled synthetic fixture (fixtures/risk_feed_fixture.csv), clearly labelled as such.
`make feed-snapshot` copies the validated live feed from MENA_RISK_FEED_PATH into data/feeds/mena/ (see snapshot()).

Accepted path forms: a .csv or .parquet file; a manifest .json with {"schema_version", "parquet"|"csv", "sha256"?}
pointing at a sibling file (sha256 is checked when present); a path whose ".json" sibling is such a manifest
(".../v1/latest" -> ".../v1/latest.json"); a directory containing latest.json.

Schema versions (the producer's SCHEMA.md):
  * 1.0.x  nine core columns, no skill_status -> skill_status "unknown" (treated as not demonstrated);
  * 1.1.x+ adds skill_status, required, one of SKILL_STATUSES, constant within a file;
  * 2.x    accepted (planned producer change of the escalation *label*: a new meaning behind p_escalation). Same core
           columns plus skill_status; an optional label-description column is carried through as `feed_label`.
  Any other major is rejected.

Skill policy: p_escalation may inform a *likelihood* score only when skill_status is beats_base_rate or
beats_country_rate (likelihood_allowed). Otherwise country risk is shown as context next to geographic exposure,
which stays an impact attribute.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from supplier_graph.config import FIXTURES, ROOT, env, resolve_repo_path

CORE_COLUMNS = [
    "schema_version", "country_iso3", "week_start", "p_escalation", "model_version",
    "calibration", "top_drivers", "data_cutoff", "generated_at",
]
REQUIRED_COLUMNS = CORE_COLUMNS  # backwards-compatible name
SKILL_STATUSES = ("not_demonstrated", "beats_base_rate", "beats_country_rate")
SKILL_UNKNOWN = "unknown"
LIKELIHOOD_SKILL = ("beats_base_rate", "beats_country_rate")
SUPPORTED_MAJORS = (1, 2)
LABEL_COLUMNS = ("label", "label_definition", "escalation_definition", "label_source")
V1_LABEL = ("ACLED fatalities in weeks W+1..W+4 exceed both twice the trailing 12-week rate and 10 deaths "
            "(producer SCHEMA.md, schema 1.x)")
OUT_COLUMNS = [*CORE_COLUMNS, "skill_status", "feed_label"]
FIXTURE_PATH = FIXTURES / "risk_feed_fixture.csv"
SNAPSHOT_DIR = ROOT / "data" / "feeds" / "mena"

SKILL_TEXT = {
    "not_demonstrated": "The feed's model has not demonstrated skill over the regional base rate. Escalation "
                        "probabilities are shown as context only and do not drive any likelihood score.",
    "beats_base_rate": "The feed's model beats the regional base rate but not each country's own historical rate; "
                       "use its ranking of countries, not the exact level.",
    "beats_country_rate": "The feed's model beats both the regional base rate and each country's historical rate.",
    SKILL_UNKNOWN: "The feed predates skill reporting (schema 1.0.x), so skill is treated as not demonstrated. "
                   "Escalation probabilities are shown as context only.",
}

# one-clause versions for the app's plain-language lines
SKILL_SHORT = {
    "not_demonstrated": "It has not beaten the regional average, so treat it as context",
    "beats_base_rate": "It beats the regional average but not each country's own history, so use it to rank countries",
    "beats_country_rate": "It beats both the regional average and each country's own history",
    SKILL_UNKNOWN: "Its skill is not reported, so treat it as context",
}


class SchemaError(ValueError):
    pass


@dataclass
class RiskFeed:
    df: pd.DataFrame
    source_path: str
    is_fixture: bool
    note: str
    schema_version: str = ""
    skill_status: str = SKILL_UNKNOWN
    label: str = V1_LABEL
    notices: list[str] = field(default_factory=list)

    @property
    def likelihood_allowed(self) -> bool:
        return likelihood_allowed(self.skill_status)

    def describe(self) -> dict:
        return {"source": self.source_path, "is_fixture": self.is_fixture, "note": self.note,
                "schema_version": self.schema_version, "skill_status": self.skill_status,
                "skill_text": SKILL_TEXT[self.skill_status], "likelihood_allowed": self.likelihood_allowed,
                "label": self.label, "notices": self.notices}


def likelihood_allowed(skill_status: str | None) -> bool:
    """True only when the producer has demonstrated skill; unknown or missing counts as not demonstrated."""
    return skill_status in LIKELIHOOD_SKILL


def _version(v: str) -> tuple[int, int]:
    try:
        parts = str(v).split(".")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except ValueError as e:
        raise SchemaError(f"unparseable schema_version {v!r}") from e


def _major(v: str) -> int:
    return _version(v)[0]


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve(path: Path) -> tuple[Path, list[str]]:
    notices: list[str] = []
    if path.is_dir():
        path = path / "latest.json"
    if not path.exists() and path.with_suffix(".json").exists():
        path = path.with_suffix(".json")
    if path.suffix == ".json":
        manifest = json.loads(path.read_text())
        if _major(manifest.get("schema_version", "0")) not in SUPPORTED_MAJORS:
            raise SchemaError(f"manifest schema_version {manifest.get('schema_version')} is not a supported major "
                              f"{SUPPORTED_MAJORS}")
        target = manifest.get("parquet") or manifest.get("csv")
        if not target:
            raise SchemaError("manifest names no parquet/csv file")
        data = path.parent / target
        want = (manifest.get("sha256") or {}).get(target)
        if want and data.exists() and _sha256(data) != want:
            raise SchemaError(f"sha256 mismatch for {data.name}")
        # a producer publishes a new major in a sibling v<N>/ folder; say so, but never switch silently
        if path.parent.name.startswith("v") and path.parent.name[1:].isdigit():
            cur = int(path.parent.name[1:])
            for sib in sorted(path.parent.parent.glob("v*/latest.json")):
                n = sib.parent.name[1:]
                if n.isdigit() and int(n) > cur:
                    notices.append(f"a newer feed major is published at {sib.parent.name}/; point MENA_RISK_FEED_PATH there "
                                   "after reviewing the producer's migration note")
        path = data
    if not path.exists():
        raise FileNotFoundError(path)
    return path, notices


def validate(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in CORE_COLUMNS if c not in df.columns]
    if missing:
        raise SchemaError(f"missing columns: {missing}")
    versions = df["schema_version"].astype(str).unique()
    if len(versions) != 1:
        raise SchemaError(f"schema_version must be constant within a file, got {sorted(versions)}")
    major, minor = _version(versions[0])
    if major not in SUPPORTED_MAJORS:
        raise SchemaError(f"schema_version major {major} not in {SUPPORTED_MAJORS}")
    if "skill_status" in df.columns:
        vals = set(df["skill_status"].astype(str).unique())
        if not vals <= set(SKILL_STATUSES):
            raise SchemaError(f"unknown skill_status {sorted(vals - set(SKILL_STATUSES))}")
        if len(vals) != 1:
            raise SchemaError(f"skill_status must be constant within a file, got {sorted(vals)}")
    elif major >= 2 or minor >= 1:
        raise SchemaError(f"schema {versions[0]} requires a skill_status column")
    label_col = next((c for c in LABEL_COLUMNS if c in df.columns), None)
    out = df[CORE_COLUMNS].copy()
    out["skill_status"] = df["skill_status"].astype(str) if "skill_status" in df.columns else SKILL_UNKNOWN
    if label_col:
        out["feed_label"] = df[label_col].astype(str)
    else:
        out["feed_label"] = V1_LABEL if major == 1 else "schema 2.x label; see the producer's SCHEMA.md"
    out["country_iso3"] = out["country_iso3"].astype(str).str.upper()
    if not out["country_iso3"].str.fullmatch(r"[A-Z]{3}").all():
        raise SchemaError("country_iso3 must be ISO alpha-3")
    out["week_start"] = pd.to_datetime(out["week_start"]).dt.date
    if any(pd.Timestamp(d).weekday() != 0 for d in out["week_start"].unique()):
        raise SchemaError("week_start must be a Monday")
    out["p_escalation"] = out["p_escalation"].astype(float)
    if not out["p_escalation"].between(0, 1).all():
        raise SchemaError("p_escalation outside [0, 1]")
    for d in out["top_drivers"].head(50):
        drivers = json.loads(d)
        if not isinstance(drivers, list) or len(drivers) > 5:
            raise SchemaError("top_drivers must be a JSON list of up to 5 items")
    return out[OUT_COLUMNS]


def _read(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype={"schema_version": str})


def _rel(path: Path) -> str:
    """Repo-relative path when inside the repo (no local usernames in committed reports)."""
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return f"<outside repo>/{path.parent.name}/{path.name}"


def _feed(df: pd.DataFrame, path: Path, is_fixture: bool, note: str, notices: list[str]) -> RiskFeed:
    return RiskFeed(df, _rel(path), is_fixture, note, str(df["schema_version"].iloc[0]),
                    str(df["skill_status"].iloc[0]), str(df["feed_label"].iloc[0]), notices)


def load(path_str: str | None = None) -> RiskFeed:
    tried: list[str] = []
    candidates: list[tuple[str, str]] = []
    if path_str is not None:
        if path_str:
            candidates.append((path_str, "explicit path"))
    else:
        if (SNAPSHOT_DIR / "latest.json").exists():
            candidates.append((str(SNAPSHOT_DIR / "latest.json"), "committed snapshot"))
        if env("MENA_RISK_FEED_PATH"):
            candidates.append((env("MENA_RISK_FEED_PATH"), "MENA_RISK_FEED_PATH"))
    for p, how in candidates:
        try:
            path, notices = _resolve(resolve_repo_path(p))
            df = validate(_read(path))
            return _feed(df, path, False, f"MENA feed from {how} ({path.name})", notices)
        except FileNotFoundError:
            tried.append(f"{how} {p!r} not found")
    note = "; ".join(tried) + "; using synthetic fixture" if tried else "no MENA feed configured; using synthetic fixture"
    return _feed(validate(_read(FIXTURE_PATH)), FIXTURE_PATH, True, note, [])


def attribution(df: pd.DataFrame) -> str:
    if "ucdp" in str(df["feed_label"].iloc[0]).lower():
        return ("Model output of the author's MENA escalation project, trained on openly licensed inputs: the UCDP "
                "Georeferenced Event Dataset and UCDP Candidate Events Dataset (CC BY 4.0; Sundberg & Melander 2013; "
                "Davies, Pettersson & Oberg 2026; Hegre et al. 2020; ucdp.uu.se) and The GDELT Project "
                "(gdeltproject.org). Contains model outputs only.")
    return ("Model output of the author's MENA escalation project, derived from ACLED data (acleddata.com, used under "
            "ACLED's EULA and Attribution Policy) and The GDELT Project. Contains no ACLED event records. ACLED bears "
            "no responsibility for the analysis or conclusions presented here.")


def feed_in_likelihood() -> bool:
    """Whether the committed backtest adopted p_escalation as a likelihood input (risk/backtest.py feed_gate)."""
    p = ROOT / "reports" / "risk_backtest.json"
    if not p.exists():
        return False
    return "feed_p_escalation" in json.loads(p.read_text()).get("feed_policy", {}).get("adopted", [])


def usage_text() -> str:
    return ("Escalation probabilities feed the termination-likelihood score (the backtest showed they improve it)."
            if feed_in_likelihood() else
            "Escalation probabilities are context here: they do not drive the termination-likelihood score, because "
            "the backtest could not show that they improve it.")


def usage_short() -> str:
    return "it feeds the likelihood score" if feed_in_likelihood() else "it doesn't affect likelihood scores"


def risk_note(skill_status: str | None) -> str:
    """One line for wherever a country-risk number is shown."""
    role = "a likelihood input" if feed_in_likelihood() else "context only"
    return f"Country risk is {role} (feed skill status: {skill_status or SKILL_UNKNOWN})."


def snapshot(src: str | None = None, dest: Path = SNAPSHOT_DIR) -> dict:
    """Copy the validated live feed (MENA_RISK_FEED_PATH) into the repo with a manifest; old snapshots are replaced."""
    src = src or env("MENA_RISK_FEED_PATH")
    if not src:
        raise SystemExit("MENA_RISK_FEED_PATH is not set")
    path, notices = _resolve(resolve_repo_path(src))
    df = validate(_read(path))
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("risk_feed_*"):
        old.unlink()
    shutil.copy2(path, dest / path.name)
    manifest = {"schema_version": str(df["schema_version"].iloc[0]), "skill_status": str(df["skill_status"].iloc[0]),
                "copied_from": path.name, "model_version": str(df["model_version"].iloc[0]),
                "generated_at": str(df["generated_at"].iloc[0]), "rows": len(df),
                "countries": int(df["country_iso3"].nunique()),
                "week_start_min": str(min(df["week_start"])), "week_start_max": str(max(df["week_start"])),
                ("parquet" if path.suffix == ".parquet" else "csv"): path.name,
                "sha256": {path.name: _sha256(dest / path.name)}, "notices": notices,
                "label": str(df["feed_label"].iloc[0]), "attribution": attribution(df)}
    (dest / "latest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def latest_by_country(feed: RiskFeed) -> pd.DataFrame:
    df = feed.df.sort_values("week_start")
    latest = df.groupby("country_iso3", as_index=False).tail(1).reset_index(drop=True)
    latest["feed_source"] = feed.source_path
    latest["feed_is_fixture"] = feed.is_fixture
    return latest


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=2))
