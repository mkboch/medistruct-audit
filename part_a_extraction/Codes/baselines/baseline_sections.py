# Codes/baselines/baseline_sections.py
import re
from typing import Dict, List

SECTION_HEADERS = [
    "DISCHARGE MEDICATIONS", "MEDICATIONS", "DISCHARGE MEDS",
    "DISCHARGE DIAGNOSIS", "DISCHARGE DIAGNOSES", "DIAGNOSES", "PROBLEM LIST",
    "CHIEF COMPLAINT", "HISTORY OF PRESENT ILLNESS", "HPI",
]

def _normalize_header(h: str) -> str:
    return re.sub(r"[^A-Z0-9 ]+", "", h.upper()).strip()

def _split_items(block: str) -> List[str]:
    # bullets, numbered lines, or plain lines
    lines = []
    for raw in block.splitlines():
        s = raw.strip()
        if not s:
            continue
        s = re.sub(r"^[\-\*\u2022]\s*", "", s)          # bullet
        s = re.sub(r"^\d+[\).\s]+\s*", "", s)           # 1) / 1.
        # keep only non-trivial lines
        if len(s) >= 2:
            lines.append(s)
    # sometimes items are separated by ";"
    out = []
    for ln in lines:
        parts = [p.strip() for p in ln.split(";") if p.strip()]
        out.extend(parts if len(parts) > 1 else [ln])
    return out

def extract_sections(note_text: str) -> Dict[str, str]:
    """
    Very lightweight section splitter:
    finds header lines and captures text until next header.
    """
    text = note_text or ""
    # Build regex that matches a header at line start
    header_re = r"(?m)^(%s)\s*:?\s*$" % "|".join(map(re.escape, SECTION_HEADERS))
    matches = list(re.finditer(header_re, text, flags=re.IGNORECASE))
    if not matches:
        return {}

    out = {}
    for i, m in enumerate(matches):
        h = _normalize_header(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end].strip("\n")
        # keep the longest occurrence per header
        if h not in out or len(block) > len(out[h]):
            out[h] = block
    return out

def baseline_from_sections(note_text: str) -> Dict[str, object]:
    sec = extract_sections(note_text)

    meds_block = sec.get("DISCHARGE MEDICATIONS") or sec.get("MEDICATIONS") or sec.get("DISCHARGE MEDS") or ""
    dx_block = (
        sec.get("DISCHARGE DIAGNOSIS") or sec.get("DISCHARGE DIAGNOSES")
        or sec.get("DIAGNOSES") or sec.get("PROBLEM LIST") or ""
    )
    cc_block = sec.get("CHIEF COMPLAINT") or ""
    hpi_block = sec.get("HISTORY OF PRESENT ILLNESS") or sec.get("HPI") or ""

    meds = _split_items(meds_block)[:30]
    dx = _split_items(dx_block)[:30]

    # symptoms baseline: take a few short phrases from CC + first lines of HPI
    sym = []
    sym.extend(_split_items(cc_block)[:10])
    sym.extend(_split_items("\n".join(hpi_block.splitlines()[:8]))[:10])
    sym = sym[:20]

    summary = ""
    if cc_block.strip():
        summary = cc_block.strip().replace("\n", " ")
    elif hpi_block.strip():
        summary = " ".join([ln.strip() for ln in hpi_block.splitlines()[:3] if ln.strip()])[:400]

    return {
        "symptoms": sym,
        "diagnoses": dx,
        "medications": meds,
        "assessment_summary": summary,
    }