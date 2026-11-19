"""Pairwise record matcher. It sees only names and addresses, never UEI / CAGE / parent IDs.

`MatchRecord` deliberately has no identifier fields, so evaluation on identifier-derived
labels is fair: the matcher cannot peek at the identifiers that produced the labels.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from supplier_graph.er.normalize import core_name, normalize_address, normalize_name, normalize_zip

DEFAULT_THRESHOLD = 0.80  # overwritten by the threshold tuned on the dev split (reports/er_threshold.json)


@dataclass(frozen=True)
class MatchRecord:
    name: str
    address: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    country: str = ""

    @property
    def core(self) -> str:
        return core_name(self.name)

    @property
    def addr(self) -> str:
        return normalize_address(self.address)

    @property
    def zip5(self) -> str:
        return normalize_zip(self.zip)


def _eq(a: str, b: str) -> float | None:
    a, b = (a or "").strip().upper(), (b or "").strip().upper()
    if not a or not b:
        return None
    return 1.0 if a == b else 0.0


ACRONYM_STOPWORDS = {"OF", "THE", "FOR", "&", "AND"}
ACRONYM_NAME_SCORE = 0.92  # name similarity credited to an exact acronym match; geography still has to agree


def initials(core: str, full: str | None = None) -> set[str]:
    """Possible acronyms of a name with >= 3 alphabetic words. Variants: core words, core without stopwords, and
    (when the normalized full name is given) words including legal suffixes and THE, with '&' read as A or dropped
    ("QUALITY REFRIGERATION CORP" -> QRC, "THE GYRO HOUSE" -> TGH, "BARKER AIR & HYDRAULICS" -> BAAH).
    NAME_CANON abbreviations keep the first letter (SYSTEMS -> SYS), so initials survive normalization."""
    seqs = [core.split()]
    if full:
        seqs += [full.replace("&", "A").split(), full.replace("&", " ").split()]
    out = set()
    for toks in seqs:
        words = [t for t in toks if t.isalpha()]
        for ws in (words, [w for w in words if w not in ACRONYM_STOPWORDS]):
            if len(ws) >= 3:
                out.add("".join(w[0] for w in ws))
    return out


def is_acronym_of(short: str, long: str, long_full: str | None = None) -> bool:
    """True when `short` is a single alphabetic token (>= 3 letters) equal to the initials of `long`."""
    t = short.split()
    return len(t) == 1 and len(t[0]) >= 3 and t[0].isalpha() and t[0] in initials(long, long_full)


def features(a: MatchRecord, b: MatchRecord, acronyms: bool = True) -> dict[str, float | None]:
    ca, cb = a.core, b.core
    name = max(fuzz.token_set_ratio(ca, cb), fuzz.ratio(ca, cb)) / 100 if ca and cb else 0.0
    # token_set_ratio is 100 when one name's tokens are a subset of the other's; temper that
    # for very short subsets ("ACME" vs "ACME MARINE SVCS") with the plain ratio.
    if ca and cb and min(len(ca), len(cb)) < 0.5 * max(len(ca), len(cb)):
        name = min(name, (fuzz.ratio(ca, cb) / 100 + name) / 2)
    # Acronyms ("GDLS" vs "GENERAL DYNAMICS LAND SYSTEMS") defeat string similarity entirely (v1 recall 0.10).
    if acronyms and ca and cb and (is_acronym_of(ca, cb, normalize_name(b.name))
                                   or is_acronym_of(cb, ca, normalize_name(a.name))):
        name = max(name, ACRONYM_NAME_SCORE)
    addr = fuzz.token_set_ratio(a.addr, b.addr) / 100 if a.addr and b.addr else None
    return {
        "name": name,
        "addr": addr,
        "zip": _eq(a.zip5, b.zip5),
        "city": _eq(a.city, b.city),
        "state": _eq(a.state, b.state),
        "country": _eq(a.country, b.country),
    }


EVIDENCE = ["name_sim", "acronym", "addr", "addr_missing", "zip", "zip_missing", "city", "city_missing",
            "state", "state_missing", "country", "country_missing"]


def evidence(a: MatchRecord, b: MatchRecord) -> list[float]:
    """Agreement evidence for learned linkage weights (er.linkage): raw name similarity, an acronym indicator, and
    each geographic comparison with a separate missing-value indicator (missing is not the same as disagreeing)."""
    f = features(a, b, acronyms=False)
    ca, cb = a.core, b.core
    acr = bool(ca and cb and (is_acronym_of(ca, cb, normalize_name(b.name)) or is_acronym_of(cb, ca, normalize_name(a.name))))
    out = [f["name"], float(acr)]
    for k in ("addr", "zip", "city", "state", "country"):
        out += [0.0, 1.0] if f[k] is None else [float(f[k]), 0.0]
    return out


def score(a: MatchRecord, b: MatchRecord, acronyms: bool = True) -> float:
    """Weighted similarity in [0, 1]; missing fields drop out and the weights renormalize.
    `acronyms=False` reproduces the v1 matcher (kept for the before/after ablation in er.evaluate)."""
    f = features(a, b, acronyms=acronyms)
    weights = {"name": 0.55, "addr": 0.20, "zip": 0.10, "city": 0.08, "state": 0.04, "country": 0.03}
    num = den = 0.0
    for k, w in weights.items():
        v = f[k]
        if v is None:
            continue
        num += w * v
        den += w
    s = num / den if den else 0.0
    # Hard geographic contradictions are strong evidence of different entities.
    if f["country"] == 0.0 or (f["state"] == 0.0 and f["zip"] == 0.0):
        s *= 0.85
    return round(s, 4)


def is_match(a: MatchRecord, b: MatchRecord, threshold: float = DEFAULT_THRESHOLD) -> bool:
    return score(a, b) >= threshold
