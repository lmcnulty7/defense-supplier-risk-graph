"""Deterministic name and address normalization for entity resolution."""

from __future__ import annotations

import re
import unicodedata

LEGAL_SUFFIXES = {
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LLC", "L L C", "LTD", "LIMITED",
    "LP", "LLP", "PLC", "PC", "PLLC", "GMBH", "AG", "SA", "SAS", "BV", "NV", "SPA", "SRL", "AB", "AS", "OY",
    "PTY", "KK", "THE", "DBA",
}
NAME_CANON = {
    "INCORPORATED": "INC", "CORPORATION": "CORP", "COMPANY": "CO", "LIMITED": "LTD",
    "INTERNATIONAL": "INTL", "INTERNATL": "INTL", "TECHNOLOGIES": "TECH", "TECHNOLOGY": "TECH",
    "SERVICES": "SVCS", "SERVICE": "SVC", "SYSTEMS": "SYS", "SYSTEM": "SYS", "MANUFACTURING": "MFG",
    "ENGINEERING": "ENGR", "INDUSTRIES": "IND", "INDUSTRY": "IND", "ASSOCIATES": "ASSOC",
    "GROUP": "GRP", "AMERICA": "AMER", "AMERICAN": "AMER", "AEROSPACE": "AERO", "AND": "&",
    "SOLUTIONS": "SOLNS", "LABORATORIES": "LABS", "LABORATORY": "LAB", "BROTHERS": "BROS",
    "DEPARTMENT": "DEPT", "NATIONAL": "NATL", "MANAGEMENT": "MGMT", "ENTERPRISES": "ENTPR",
}
ADDR_CANON = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR", "BOULEVARD": "BLVD", "LANE": "LN",
    "COURT": "CT", "PLACE": "PL", "PARKWAY": "PKWY", "HIGHWAY": "HWY", "SUITE": "STE", "BUILDING": "BLDG",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W", "NORTHEAST": "NE", "NORTHWEST": "NW",
    "SOUTHEAST": "SE", "SOUTHWEST": "SW", "CIRCLE": "CIR", "TERRACE": "TER", "FLOOR": "FL",
    "POST OFFICE BOX": "PO BOX", "P O BOX": "PO BOX", "ROOM": "RM", "UNIT": "UNIT", "WAY": "WAY",
    "SQUARE": "SQ", "TRAIL": "TRL", "CENTER": "CTR", "MOUNT": "MT", "FORT": "FT", "SAINT": "ST",
}


def _ascii_upper(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return s.upper()


def _tokens(s: str) -> list[str]:
    s = s.replace("&", " & ")
    s = re.sub(r"[^A-Z0-9& ]+", " ", s.replace(".", ""))
    return s.split()


def normalize_name(name: str | None) -> str:
    """Canonical form: upper ASCII, punctuation removed, common words abbreviated."""
    toks = _tokens(_ascii_upper(name))
    joined = " ".join(toks)
    joined = re.sub(r"\bL L C\b", "LLC", joined)
    joined = re.sub(r"\bL L P\b", "LLP", joined)
    return " ".join(NAME_CANON.get(t, t) for t in joined.split())


def core_name(name: str | None) -> str:
    """normalize_name with legal-form suffixes and 'THE' removed (used for matching and blocking)."""
    toks = [t for t in normalize_name(name).split() if t not in LEGAL_SUFFIXES]
    return " ".join(toks)


def normalize_address(addr: str | None) -> str:
    s = " ".join(_tokens(_ascii_upper(addr)))
    for long, short in (("POST OFFICE BOX", "PO BOX"), ("P O BOX", "PO BOX")):
        s = s.replace(long, short)
    return " ".join(ADDR_CANON.get(t, t) for t in s.split())


def normalize_zip(z: str | None) -> str:
    digits = re.sub(r"[^0-9]", "", str(z or ""))
    return digits[:5]


def blocking_keys(core: str, state: str | None, zip5: str | None) -> list[str]:
    """Cheap keys so the fuzzy matcher only compares plausible candidates."""
    state = state if isinstance(state, str) else ""
    zip5 = zip5 if isinstance(zip5, str) else ""
    toks = [t for t in core.split() if len(t) > 1] or core.split()
    keys = []
    if toks:
        keys.append(f"T1:{toks[0]}")
        if len(toks) > 1:
            keys.append(f"T2:{toks[0]}|{toks[1]}")
        longest = max(toks, key=len)
        keys.append(f"TL:{longest}|{(state or '')[:2]}")
    if zip5:
        keys.append(f"Z:{zip5}|{(toks[0] if toks else '')[:3]}")
    return keys
