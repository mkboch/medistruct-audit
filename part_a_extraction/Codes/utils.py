import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import jsonschema


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(s: str) -> str:
    h = hashlib.sha256()
    h.update((s or "").encode("utf-8", errors="ignore"))
    return h.hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_ws(s: str) -> str:
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


SECTION_HEADERS = [
    "CHIEF COMPLAINT",
    "HISTORY OF PRESENT ILLNESS",
    "HPI",
    "PAST MEDICAL HISTORY",
    "HOSPITAL COURSE",
    "BRIEF HOSPITAL COURSE",
    "DISCHARGE DIAGNOSIS",
    "DISCHARGE DIAGNOSES",
    "DISCHARGE MEDICATIONS",
    "MEDICATIONS ON ADMISSION",
    "DISCHARGE INSTRUCTIONS",
    "FOLLOWUP INSTRUCTIONS",
    "PHYSICAL EXAM",
    "PERTINENT RESULTS",
]


def split_into_sections(note: str) -> Dict[str, str]:
    note = normalize_ws(note)
    upper = note.upper()
    hits = []
    for h in SECTION_HEADERS:
        idx = upper.find(h)
        if idx != -1:
            hits.append((idx, h))
    if not hits:
        return {"FULL": note}

    hits.sort()
    sections = {}
    for i, (idx, h) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(note)
        sections[h] = note[idx:end].strip()
    return sections


def build_extraction_view(note: str, max_chars: int = 26000) -> Tuple[str, Dict[str, Any]]:
    note_full = note or ""
    meta: Dict[str, Any] = {"orig_chars": len(note_full), "max_chars": max_chars}

    sections = split_into_sections(note_full)
    if "FULL" not in sections:
        order = [
            "CHIEF COMPLAINT",
            "HISTORY OF PRESENT ILLNESS",
            "HPI",
            "BRIEF HOSPITAL COURSE",
            "HOSPITAL COURSE",
            "DISCHARGE DIAGNOSIS",
            "DISCHARGE DIAGNOSES",
            "DISCHARGE MEDICATIONS",
            "DISCHARGE INSTRUCTIONS",
            "PERTINENT RESULTS",
        ]
        parts = [sections[k] for k in order if k in sections]
        view = "\n\n".join(parts).strip()
        meta["strategy"] = "section_prioritized" if view else "fallback_full"
        if not view:
            view = normalize_ws(note_full)
    else:
        view = normalize_ws(note_full)
        meta["strategy"] = "full_no_sections"

    if len(view) <= max_chars:
        meta["trimmed"] = False
        meta["view_chars"] = len(view)
        return view, meta

    head_len = int(max_chars * 0.7)
    tail_len = max_chars - head_len
    trimmed = view[:head_len] + "\n\n[...TRUNCATED...]\n\n" + view[-tail_len:]
    meta["trimmed"] = True
    meta["view_chars"] = len(view)
    meta["trimmed_chars"] = len(trimmed)
    meta["strategy"] = meta["strategy"] + "+head_tail"
    return trimmed, meta


def normalize_structured(obj: Any) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        return {}

    def norm_list(v):
        if v is None:
            return []
        if isinstance(v, dict):
            return list(v.keys())
        if isinstance(v, list):
            out = []
            for x in v:
                if isinstance(x, str):
                    out.append(x)
                elif isinstance(x, dict):
                    out.extend(list(x.keys()))
                else:
                    out.append(str(x))
            return out
        if isinstance(v, str):
            return [v]
        return [str(v)]

    for k in ["symptoms", "diagnoses", "medications"]:
        obj[k] = norm_list(obj.get(k, []))

    if not isinstance(obj.get("assessment_summary", ""), str):
        obj["assessment_summary"] = str(obj.get("assessment_summary", ""))

    ev = obj.get("evidence", [])
    if not isinstance(ev, list):
        ev = []
    norm_ev = []
    for e in ev:
        if not isinstance(e, dict):
            continue
        field = e.get("field")
        item = e.get("item")
        start = e.get("start")
        end = e.get("end")
        if field is None or item is None or start is None or end is None:
            continue
        try:
            s = int(start)
            t = int(end)
        except Exception:
            continue
        norm_ev.append({"field": str(field), "item": str(item), "start": s, "end": t})
    obj["evidence"] = norm_ev
    return obj


def validate_schema(obj: Dict[str, Any], schema: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errs: List[str] = []
    try:
        jsonschema.validate(instance=obj, schema=schema)
        return True, errs
    except jsonschema.ValidationError as e:
        errs.append(str(e))
        return False, errs
    except Exception as e:
        errs.append(f"schema_validate_failed: {e}")
        return False, errs


def evidence_span_checks(note_text: str, structured: Dict[str, Any]) -> Dict[str, Any]:
    note = note_text or ""
    L = len(note)
    ev = structured.get("evidence", [])
    if not isinstance(ev, list):
        ev = []

    valid_flags = []
    invalid_idx = []
    supported = set()

    # 1) span validity + collect supported (field,item)
    for i, e in enumerate(ev):
        try:
            s = int(e.get("start"))
            t = int(e.get("end"))
        except Exception:
            valid_flags.append(False)
            invalid_idx.append(i)
            continue
        ok = (0 <= s < t <= L)
        valid_flags.append(ok)
        if not ok:
            invalid_idx.append(i)
        else:
            supported.add((str(e.get("field", "")), str(e.get("item", "")).strip().lower()))

    span_valid_rate = (sum(valid_flags) / len(valid_flags)) if valid_flags else 0.0

    # 2) coverage (exact string match of item -> evidence item)
    total_items = 0
    covered_items = 0
    for field in ["symptoms", "diagnoses", "medications"]:
        items = structured.get(field, [])
        if not isinstance(items, list):
            continue
        for it in items:
            total_items += 1
            key = (field, str(it).strip().lower())
            if key in supported:
                covered_items += 1

    coverage = (covered_items / total_items) if total_items > 0 else 0.0

    # 3) evidence_present_rate per field
    present = {f: 0 for f in ["symptoms", "diagnoses", "medications"]}
    total_f = {f: 0 for f in ["symptoms", "diagnoses", "medications"]}

    for field in ["symptoms", "diagnoses", "medications"]:
        items = structured.get(field, [])
        if not isinstance(items, list):
            continue
        for it in items:
            total_f[field] += 1
            key = (field, str(it).strip().lower())
            if key in supported:
                present[field] += 1

    evidence_present_rate = {
        f: (present[f] / total_f[f] if total_f[f] else 0.0)
        for f in present
    }

    # 4) substring_support_rate (faithfulness proxy)
    def tokset(x: str) -> set:
        x = re.sub(r"[^\w\s]", " ", (x or "").lower())
        toks = [t for t in x.split() if len(t) >= 3]
        return set(toks)

    support_flags = []
    for e in ev:
        try:
            s = int(e.get("start"))
            t = int(e.get("end"))
        except Exception:
            continue
        if not (0 <= s < t <= L):
            continue
        substr = note[s:t]
        item = str(e.get("item", ""))
        A = tokset(substr)
        B = tokset(item)
        if not B:
            continue
        support_flags.append(1 if len(A & B) > 0 else 0)

    substring_support_rate = (sum(support_flags) / len(support_flags)) if support_flags else 0.0

    # 5) medication section alignment rate
    secs = split_into_sections(note)
    med_blob = ""
    if "DISCHARGE MEDICATIONS" in secs:
        med_blob = secs["DISCHARGE MEDICATIONS"]
    elif "MEDICATIONS ON ADMISSION" in secs:
        med_blob = secs["MEDICATIONS ON ADMISSION"]

    med_start = note.find(med_blob) if med_blob else -1
    med_end = med_start + len(med_blob) if med_start != -1 else -1

    align_flags = []
    for e in ev:
        if str(e.get("field", "")) != "medications":
            continue
        try:
            s = int(e.get("start"))
            t = int(e.get("end"))
        except Exception:
            continue
        if med_start != -1 and med_end != -1:
            align_flags.append(1 if (med_start <= s and t <= med_end) else 0)

    med_section_alignment_rate = (sum(align_flags) / len(align_flags)) if align_flags else 0.0

    return {
        "evidence_count": len(valid_flags),
        "span_valid_rate": span_valid_rate,
        "span_invalid_indices": invalid_idx,
        "coverage": coverage,
        "evidence_present_rate": evidence_present_rate,
        "substring_support_rate": substring_support_rate,
        "med_section_alignment_rate": med_section_alignment_rate,
    }


def jaccard(a: List[str], b: List[str]) -> float:
    sa = set([str(x).strip().lower() for x in (a or []) if str(x).strip() != ""])
    sb = set([str(x).strip().lower() for x in (b or []) if str(x).strip() != ""])
    if len(sa) == 0 and len(sb) == 0:
        return 1.0
    if len(sa) == 0 or len(sb) == 0:
        return 0.0
    return len(sa & sb) / len(sa | sb)

def normalize_for_containment(s: str) -> str:
    """
    Light normalization for substring/containment checks.
    Lowercase + collapse whitespace + strip most punctuation.
    """
    import re
    s = (s or "").lower().strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s/().%-]", "", s)
    return s.strip()


def safe_json_loads(s: str):
    """
    Best-effort JSON parsing with repair.
    Returns: (ok: bool, obj: Any, err: str)
    Uses repair_json_text() if available in this module.
    """
    import json
    try:
        return True, json.loads(s), ""
    except Exception as e1:
        # Try repair path if repair_json_text exists
        try:
            if "repair_json_text" in globals():
                fixed = repair_json_text(s)
                return True, json.loads(fixed), ""
        except Exception as e2:
            return False, {}, f"{e1} | repair_failed: {e2}"
        return False, {}, str(e1)

