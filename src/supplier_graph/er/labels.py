"""Automatic ER evaluation labels. No manual annotation.

Label sources (stored in column `label_source`):
  identifier:uei      positive  two records with the same UEI whose raw name or address strings differ
  identifier:neg      negative  similar core names (token-set >= 85) but different UEIs and different
                                ultimate parents (coalesce(parent_uei, uei)); both UEIs present.
                                Pairs with identical normalized name AND address are excluded as
                                ambiguous (one legal entity holding several UEIs at one site) and counted.
  synthetic:pos       positive  a real record vs. a programmatic perturbation of itself
  synthetic:neg       negative  a real record vs. a perturbation of its hard-negative neighbour

Pairs are split dev/test by a hash of the anchor UEI, so no entity appears in both splits.
Identifiers are kept in the label table for auditing but are never passed to the matcher.
"""

from __future__ import annotations

import hashlib
import random
import re
from collections import defaultdict
from dataclasses import dataclass

import pandas as pd
from rapidfuzz import fuzz

from supplier_graph.er.normalize import blocking_keys, core_name, normalize_address, normalize_zip

SEED = 20261118
REC_COLS = ["name", "address", "city", "state", "zip", "country"]


def split_of(uei: str, dev_share: float = 0.3) -> str:
    h = int(hashlib.sha256(uei.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "dev" if h < dev_share else "test"


# ---------------------------------------------------------------- perturbations
ABBREV = [("CORPORATION", "CORP"), ("INCORPORATED", "INC"), ("COMPANY", "CO"), ("INTERNATIONAL", "INTL"),
          ("TECHNOLOGIES", "TECH"), ("SERVICES", "SVCS"), ("SYSTEMS", "SYS"), ("MANUFACTURING", "MFG"),
          ("ENGINEERING", "ENGR"), ("ASSOCIATES", "ASSOC"), ("LIMITED", "LTD"), ("AND", "&")]
SUFFIXES = ["INC", "INC.", "LLC", "L.L.C.", "CORP", "CORPORATION", "CO", "LTD", "INCORPORATED", ""]
ADDR_SWAPS = [("STREET", "ST"), ("AVENUE", "AVE"), ("ROAD", "RD"), ("DRIVE", "DR"), ("SUITE", "STE"),
              ("BOULEVARD", "BLVD"), ("NORTH", "N"), ("SOUTH", "S"), ("EAST", "E"), ("WEST", "W"),
              ("PARKWAY", "PKWY"), ("BUILDING", "BLDG")]
SUFFIX_RE = re.compile(r"[ ,]+(INC\.?|INCORPORATED|LLC|L\.L\.C\.|CORP\.?|CORPORATION|CO\.?|COMPANY|LTD\.?|LIMITED)$")


def _swap_words(s: str, pairs, rng: random.Random) -> tuple[str, bool]:
    s_up = s.upper()
    opts = [(a, b) for a, b in pairs if re.search(rf"\b{re.escape(a)}\b", s_up)] + \
           [(b, a) for a, b in pairs if re.search(rf"\b{re.escape(b)}\b", s_up)]
    if not opts:
        return s, False
    a, b = rng.choice(opts)
    return re.sub(rf"\b{re.escape(a)}\b", b, s_up, count=1), True


def p_abbreviation(r: dict, rng) -> tuple[dict, bool]:
    name, ok = _swap_words(r["name"], ABBREV, rng)
    return {**r, "name": name}, ok


def p_punctuation(r: dict, rng) -> tuple[dict, bool]:
    name = r["name"]
    if re.search(r"[.,&'-]", name):
        new = re.sub(r"[.,'-]", "", name) if rng.random() < 0.7 else name.replace(",", "")
    else:
        toks = name.split()
        if len(toks) < 2:
            return r, False
        i = rng.randrange(len(toks) - 1)
        toks[i] = toks[i] + ","
        new = " ".join(toks)
    return {**r, "name": new}, new != name


def p_suffix(r: dict, rng) -> tuple[dict, bool]:
    name = r["name"].upper().strip()
    base = SUFFIX_RE.sub("", name)
    new_suffix = rng.choice(SUFFIXES)
    new = (base + (", " if rng.random() < 0.3 and new_suffix else " ") + new_suffix).strip()
    return {**r, "name": new}, new != name


def p_typo(r: dict, rng) -> tuple[dict, bool]:
    toks = r["name"].split()
    idx = [i for i, t in enumerate(toks) if len(t) >= 5 and t.isalpha()]
    if not idx:
        return r, False
    i = rng.choice(idx)
    t = list(toks[i])
    j = rng.randrange(1, len(t) - 1)
    op = rng.choice(["swap", "drop", "sub", "dup"])
    if op == "swap":
        t[j], t[j + 1 if j + 1 < len(t) else j - 1] = t[j + 1 if j + 1 < len(t) else j - 1], t[j]
    elif op == "drop":
        del t[j]
    elif op == "sub":
        t[j] = rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ".replace(t[j].upper(), ""))
    else:
        t.insert(j, t[j])
    toks[i] = "".join(t)
    return {**r, "name": " ".join(toks)}, True


def p_address(r: dict, rng) -> tuple[dict, bool]:
    addr = r.get("address") or ""
    if not addr:
        return r, False
    choice = rng.random()
    if choice < 0.5:
        new, ok = _swap_words(addr, ADDR_SWAPS, rng)
        if not ok:
            new = addr.upper().replace(".", "") if "." in addr else addr.title()
    elif choice < 0.75:
        new = re.sub(r"\b(SUITE|STE|UNIT|#)\s*\S+", "", addr.upper()).strip(" ,") or addr.lower()
    else:
        new = addr.replace(" ", "  ", 1).lower()
    z = r.get("zip") or ""
    z_new = normalize_zip(z) if len(re.sub(r"\D", "", z)) > 5 and rng.random() < 0.5 else z
    return {**r, "address": new, "zip": z_new}, (new != addr or z_new != z)


def _core_tokens(name: str) -> tuple[list[str], str]:
    up = name.upper().strip()
    base = SUFFIX_RE.sub("", up).replace(",", " ").split()
    suffix = up[len(SUFFIX_RE.sub("", up)):].strip(" ,")
    return base, suffix


def p_acronym(r: dict, rng) -> tuple[dict, bool]:
    """'GENERAL DYNAMICS LAND SYSTEMS INC' -> 'GDLS INC' (only for names with >= 3 core words)."""
    toks, suffix = _core_tokens(r["name"])
    words = [t for t in toks if t.isalpha()]
    if len(words) < 3 or len(words) != len(toks):
        return r, False
    return {**r, "name": ("".join(t[0] for t in words) + " " + suffix).strip()}, True


def p_token_drop(r: dict, rng) -> tuple[dict, bool]:
    """Drop one non-first core word ('ACME MARINE REPAIR SERVICES' -> 'ACME REPAIR SERVICES')."""
    toks, suffix = _core_tokens(r["name"])
    if len(toks) < 3:
        return r, False
    i = rng.randrange(1, len(toks))
    return {**r, "name": (" ".join(toks[:i] + toks[i + 1:]) + " " + suffix).strip()}, True


def p_case(r: dict, rng) -> tuple[dict, bool]:
    return {**r, "name": r["name"].title() if r["name"].isupper() else r["name"].upper()}, True


PERTURBATIONS = {"abbreviation": p_abbreviation, "punctuation": p_punctuation, "suffix": p_suffix,
                 "typo": p_typo, "address": p_address, "case": p_case,
                 "acronym": p_acronym, "token_drop": p_token_drop}


def perturb(r: dict, rng: random.Random) -> tuple[dict, list[str]]:
    """Apply 1-3 perturbations; always at least one that changed the record."""
    applied: list[str] = []
    out = dict(r)
    order = list(PERTURBATIONS)
    rng.shuffle(order)
    k = rng.choice([1, 1, 2, 2, 3])
    for name in order:
        if len(applied) >= k:
            break
        new, ok = PERTURBATIONS[name](out, rng)
        if ok and new != out:
            out, applied = new, applied + [name]
    if not applied or out == r:  # compositions can cancel out (e.g. case then re-uppercase)
        out, applied = p_case(r, rng)[0], ["case"]
    return out, applied


# ---------------------------------------------------------------- pair builders
@dataclass
class LabelConfig:
    max_pos_per_uei: int = 2
    max_identifier_pos: int = 2500
    max_hard_neg: int = 2500
    neg_name_sim: int = 85
    n_synthetic_pos: int = 2000
    n_synthetic_neg: int = 2000


def _rec(row) -> dict:
    return {c: ("" if pd.isna(row[c]) else str(row[c])) for c in REC_COLS}


def build_labels(records: pd.DataFrame, cfg: LabelConfig | None = None) -> pd.DataFrame:
    cfg = cfg or LabelConfig()
    rng = random.Random(SEED)
    recs = records[records["uei"].notna()].sort_values("record_id", kind="mergesort").copy()
    recs["ultimate"] = recs["parent_uei"].fillna(recs["uei"])
    recs["core"] = recs["name"].map(core_name)
    recs["naddr"] = recs["address"].map(normalize_address)
    recs = recs.reset_index(drop=True)
    rows: list[dict] = []

    def add(a, b, label, source, anchor_uei, extra=None):
        rows.append({
            **{f"a_{c}": a[c] for c in REC_COLS}, **{f"b_{c}": b[c] for c in REC_COLS},
            "label": int(label), "label_source": source, "anchor_uei": anchor_uei,
            "split": split_of(anchor_uei), **(extra or {}),
        })

    # identifier-derived positives
    pos = 0
    for uei, grp in recs.groupby("uei", sort=True):
        if len(grp) < 2 or pos >= cfg.max_identifier_pos:
            continue
        g = grp.drop_duplicates(subset=["name", "address"])
        if len(g) < 2:
            continue
        idx = list(g.index)
        rng.shuffle(idx)
        pairs = [(idx[i], idx[j]) for i in range(len(idx)) for j in range(i + 1, len(idx))]
        for i, j in pairs[: cfg.max_pos_per_uei]:
            a, b = recs.loc[i], recs.loc[j]
            easy = a["core"] == b["core"] and a["naddr"] == b["naddr"]
            add(_rec(a), _rec(b), 1, "identifier:uei", uei,
                {"a_uei": a["uei"], "b_uei": b["uei"], "normalized_identical": easy})
            pos += 1

    # hard negatives: blocked candidate generation on core-name tokens
    canon = (recs.sort_values(["n_observations", "record_id"], ascending=[False, True], kind="mergesort")
             .drop_duplicates("uei").reset_index(drop=True))
    blocks: dict[str, list[int]] = defaultdict(list)
    for i, row in canon.iterrows():
        for key in blocking_keys(row["core"], None, None)[:2]:
            blocks[key].append(i)
    seen: set[tuple[int, int]] = set()
    negs: list[tuple[int, int, float]] = []
    ambiguous = 0
    for members in blocks.values():
        if len(members) < 2 or len(members) > 400:
            continue
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                i, j = members[x], members[y]
                key = (min(i, j), max(i, j))
                if key in seen:
                    continue
                seen.add(key)
                a, b = canon.loc[i], canon.loc[j]
                if a["ultimate"] == b["ultimate"] or a["uei"] == b["uei"]:
                    continue
                if a["core"] == b["core"] and a["naddr"] == b["naddr"]:
                    ambiguous += 1  # same legal name at the same address under two UEIs: not separable
                    continue
                sim = fuzz.token_set_ratio(a["core"], b["core"])
                if sim >= cfg.neg_name_sim:
                    negs.append((i, j, sim))
    rng.shuffle(negs)
    negs = negs[: cfg.max_hard_neg]
    for i, j, sim in negs:
        a, b = canon.loc[i], canon.loc[j]
        add(_rec(a), _rec(b), 0, "identifier:neg", a["uei"],
            {"a_uei": a["uei"], "b_uei": b["uei"], "name_token_set": sim})

    # synthetic positives: perturb sampled canonical records
    sample = canon.sample(n=min(cfg.n_synthetic_pos, len(canon)), random_state=SEED)
    for _, row in sample.iterrows():
        a = _rec(row)
        b, applied = perturb(a, rng)
        add(a, b, 1, "synthetic:pos", row["uei"], {"perturbations": "+".join(applied)})

    # synthetic negatives: perturb the hard-negative neighbour
    for i, j, _ in negs[: cfg.n_synthetic_neg]:
        a = _rec(canon.loc[i])
        b, applied = perturb(_rec(canon.loc[j]), rng)
        add(a, b, 0, "synthetic:neg", canon.loc[i, "uei"], {"perturbations": "+".join(applied)})

    out = pd.DataFrame(rows)
    out.attrs["excluded_ambiguous_negatives"] = ambiguous
    return out
