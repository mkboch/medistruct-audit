# Codes/baselines/baseline_meds_section_v2.py
import re
from typing import Dict, List

MED_HEADERS = [
    "DISCHARGE MEDICATIONS",
    "MEDICATIONS ON DISCHARGE",
    "DISCHARGE MEDS",
    "DISCHARGE MEDICATION",
    "MEDICATIONS:",
]

STOPWORDS = set("""
po iv im subcut sc sq sl pr prn bid tid qid qhs qam qpm qd qod
daily nightly morning evening bedtime
tablet tab tabs capsule cap caps
mg mcg g ml unit units % meq
inhalation inh spray topical ophthalmic otic rectal vaginal
solution suspension syrup cream ointment patch gel
w meals w/ meals with meals before meals after meals
as needed
""".split())

ROUTE_WORDS = set(["po","iv","im","sc","sq","sl","pr","inh","topical","ophthalmic","otic","rectal","vaginal"])

def _normalize_ws(s: str) -> str:
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def _find_med_section(note: str) -> str:
    note = _normalize_ws(note)
    up = note.upper()
    hits = []
    for h in MED_HEADERS:
        i = up.find(h)
        if i != -1:
            hits.append((i, h))
    if not hits:
        return ""
    hits.sort()
    start = hits[0][0]
    # end at next ALLCAPS header-ish line or after N chars (safe)
    tail = note[start:]
    # heuristic stop: next line that looks like a header
    lines = tail.splitlines()
    out_lines = []
    for idx, line in enumerate(lines):
        if idx > 0:
            # stop if likely next section header
            if re.match(r"^[A-Z][A-Z /]{6,}$", line.strip()):
                break
        out_lines.append(line)
        # safety cap
        if sum(len(x) for x in out_lines) > 8000:
            break
    return "\n".join(out_lines).strip()

def _is_noise_line(line: str) -> bool:
    l = line.strip().lower()
    if not l:
        return True
    if l in ["none", "n/a"]:
        return True
    # common separators
    if set(l) <= set("-=* _"):
        return True
    # avoid pure dosing lines
    if re.match(r"^\d+(\.\d+)?\s*(mg|mcg|g|ml|unit|units)\b", l):
        return True
    return False

def _split_candidates(section: str) -> List[str]:
    # split by newline; keep bullet-like lines
    cands = []
    for line in section.splitlines():
        line = line.strip()
        if _is_noise_line(line):
            continue
        # remove leading bullets/numbers
        line = re.sub(r"^\s*[\-\*\u2022]+\s*", "", line)
        line = re.sub(r"^\s*\d+\.\s*", "", line)
        # collapse spaces
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            cands.append(line)
    return cands

def _med_base_name_v2(s: str) -> str:
    """
    Convert 'Examplemab 10 mg PO DAILY' -> 'examplemab'  (synthetic example; release copy)
    This is intentionally conservative + high precision.
    """
    s0 = (s or "").strip().lower()
    s0 = re.sub(r"[^\w\s\-/().%]", " ", s0)
    s0 = re.sub(r"\s+", " ", s0).strip()

    # remove numbers/dose tokens and common admin tokens
    s0 = re.sub(r"\b\d+(\.\d+)?\b", " ", s0)
    s0 = re.sub(r"\b(mg|mcg|g|ml|meq|unit|units|%)\b", " ", s0)
    s0 = re.sub(r"\b(once|twice|three|four)\b", " ", s0)

    toks = [t for t in s0.split() if t and (t not in STOPWORDS)]
    if not toks:
        return ""

    # stop at route word (often begins dosing spec)
    out = []
    for t in toks:
        if t in ROUTE_WORDS:
            break
        out.append(t)

    # join but limit length
    name = " ".join(out).strip()
    name = re.sub(r"\s+", " ", name)
    return name

def baseline_meds_section_v2(note_text: str) -> Dict[str, List[str]]:
    section = _find_med_section(note_text or "")
    if not section:
        return {"symptoms": [], "diagnoses": [], "medications": []}

    cands = _split_candidates(section)
    meds = []
    seen = set()
    for c in cands:
        b = _med_base_name_v2(c)
        if not b:
            continue
        if b in seen:
            continue
        seen.add(b)
        meds.append(b)

    return {"symptoms": [], "diagnoses": [], "medications": meds}