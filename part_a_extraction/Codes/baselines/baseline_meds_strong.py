# Codes/baselines/baseline_meds_strong.py
import re
from typing import Dict, List, Tuple

# -------------------------
# 1) Section detection
# -------------------------
MED_HEADERS = [
    r"DISCHARGE\s+MEDICATIONS?",
    r"MEDICATIONS?\s+ON\s+DISCHARGE",
    r"DISCHARGE\s+MEDS",
    r"MEDICATION\s+LIST",
    r"HOME\s+MEDICATIONS?",
    r"DISCHARGE\s+PRESCRIPTIONS?",
    r"MEDICATIONS?\s*:",
]

# Common section headers that may follow meds section
NEXT_HEADERS = [
    r"DISCHARGE\s+DIAGNOSES?",
    r"DISCHARGE\s+INSTRUCTIONS?",
    r"FOLLOW[- ]?UP",
    r"APPOINTMENTS?",
    r"DIET",
    r"ACTIVITY",
    r"PHYSICAL\s+EXAM",
    r"LABS?",
    r"PERTINENT\s+RESULTS?",
    r"HOSPITAL\s+COURSE",
    r"BRIEF\s+HOSPITAL\s+COURSE",
    r"PROCEDURES?",
    r"ALLERGIES",
]

def _normalize_ws(s: str) -> str:
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def find_meds_section(note_text: str) -> str:
    """
    Returns best-guess discharge meds section text (may be empty).
    Uses regex headers + nearest following header boundary.
    """
    txt = _normalize_ws(note_text)
    up = txt.upper()

    # Find earliest meds header occurrence
    starts: List[Tuple[int, int]] = []  # (start_idx, header_len)
    for pat in MED_HEADERS:
        m = re.search(pat, up, flags=re.IGNORECASE)
        if m:
            starts.append((m.start(), m.end() - m.start()))
    if not starts:
        return ""

    start, hlen = sorted(starts, key=lambda x: x[0])[0]
    body_start = start + hlen

    # Find next header after meds start
    end = len(txt)
    tail = up[body_start:]
    for pat in NEXT_HEADERS:
        m2 = re.search(r"\n\s*" + pat + r"\b", tail, flags=re.IGNORECASE)
        if m2:
            end = body_start + m2.start()
            break

    return txt[body_start:end].strip()

# -------------------------
# 2) Line parsing
# -------------------------
BULLET_PAT = re.compile(r"^\s*([-*•]+|\d+[\).]|[A-Z][\).])\s+")
JUNK_LINE_PAT = re.compile(
    r"^\s*(?:none|n/?a|not\s+applicable|no\s+medications)\s*$",
    flags=re.IGNORECASE,
)

def split_candidate_lines(section: str) -> List[str]:
    """
    Turn a meds section into candidate medication lines.
    - Break on newlines
    - Merge wrapped lines (indented continuations)
    """
    if not section:
        return []
    lines = section.split("\n")
    out: List[str] = []
    cur = ""

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            if cur:
                out.append(cur.strip())
                cur = ""
            continue

        # If line looks like continuation (indent) and we have current buffer, append
        if cur and (raw.startswith(" ") or raw.startswith("\t")) and not BULLET_PAT.match(line):
            cur += " " + line.strip()
        else:
            if cur:
                out.append(cur.strip())
            cur = line.strip()

    if cur:
        out.append(cur.strip())

    # Clean bullets + drop junk
    cleaned = []
    for x in out:
        x = BULLET_PAT.sub("", x).strip()
        if not x:
            continue
        if JUNK_LINE_PAT.match(x):
            continue
        cleaned.append(x)
    return cleaned

# -------------------------
# 3) Strong name normalization ("ingredient-ish")
# -------------------------
ROUTE_FREQ_DOSE = set("""
po iv im sc sq pr sl inh neb topical top td patch bid tid qid qhs qam qpm qd daily prn
q4h q6h q8h q12h q24h qod once twice three times every
mg mcg g gram grams ml unit units meq tab tabs tablet tablets cap caps capsule capsules
solution susp suspension syrup oint cream gel drops spray inhaler nebules
""".split())

PARENS = re.compile(r"\([^)]*\)")
NUMS  = re.compile(r"\b\d+(\.\d+)?\b")
PUNCT = re.compile(r"[^\w\s\-/+]")

def med_base_name_v2(s: str) -> str:
    """
    Aggressive but practical: try to reduce a med string to its base drug name.
    Keeps multiword drug names and combo drugs (with '-' or '/').
    """
    if not s:
        return ""

    x = s.lower().strip()
    x = PARENS.sub(" ", x)
    x = x.replace("w/", " with ")
    x = x.replace("w ", " with ")
    x = x.replace("per md", " ")
    x = x.replace("as directed", " ")
    x = x.replace("as needed", " ")
    x = x.replace("duration", " ")
    x = x.replace("refills", " ")

    # Remove obvious dose numbers early
    x = NUMS.sub(" ", x)
    x = PUNCT.sub(" ", x)
    x = re.sub(r"\s+", " ", x).strip()

    toks = [t for t in x.split() if t]
    keep = []
    for t in toks:
        if t in ROUTE_FREQ_DOSE:
            continue
        # drop common non-name tokens
        if t in {"take", "give", "start", "stop", "continue", "tablet", "capsule"}:
            continue
        keep.append(t)

    # heuristics: keep first 1–4 tokens that look like a drug name
    # but allow combo separators within tokens (e.g., amoxicillin-clavulanate)
    if not keep:
        return ""

    # remove trailing generic words that are not drug names
    tail_drop = {"with", "meals", "meal", "pain", "fever", "wheezing", "sober", "sob"}
    while keep and keep[-1] in tail_drop:
        keep = keep[:-1]

    if not keep:
        return ""

    # often the core drug is early
    core = keep[:5]
    return " ".join(core).strip()

def baseline_meds_strong(note_text: str) -> Dict[str, List[str]]:
    """
    Baseline outputs:
      - medications: list[str] (normalized base names)
      - symptoms/diagnoses empty (this baseline focuses on meds only)
    """
    sec = find_meds_section(note_text)
    lines = split_candidate_lines(sec)

    meds = []
    seen = set()
    for ln in lines:
        base = med_base_name_v2(ln)
        if not base:
            continue
        if base in seen:
            continue
        seen.add(base)
        meds.append(base)

    return {"symptoms": [], "diagnoses": [], "medications": meds, "assessment_summary": ""}