"""Deterministic audit tools over saved clinical-extraction artifacts.

Design rules
------------
* Read-only: functions never write to case artifact locations.
* No clinical text is returned: outputs contain counts, rates, record IDs,
  hashes, file paths, and code/prompt lines only. Note text, views and label
  strings are never included in any return value.
* Cases are addressed only by registered case_id (configs/cases.json); free
  file paths are not accepted from callers.
* Every result carries provenance (files + SHA-256 or directory listing hash)
  and the tool version.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import statistics
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

TOOL_VERSION = "1.0.0"
ROOT = Path(os.environ.get("AUDIT_ROOT", Path(__file__).resolve().parents[1]))
CASES_FILE = Path(os.environ.get("AUDIT_CASES_FILE", ROOT / "configs" / "cases.json"))
FIELDS = ("symptoms", "diagnoses", "medications")
RECEIPT_METRICS = ("span_valid_rate", "coverage", "substring_support_rate",
                   "med_section_alignment_rate", "evidence_count")
DEFAULT_TOL = 0.001
csv.field_size_limit(10**9)

SECTION_HEADERS = [
    "CHIEF COMPLAINT", "HISTORY OF PRESENT ILLNESS", "HPI", "PAST MEDICAL HISTORY",
    "HOSPITAL COURSE", "BRIEF HOSPITAL COURSE", "DISCHARGE DIAGNOSIS", "DISCHARGE DIAGNOSES",
    "DISCHARGE MEDICATIONS", "MEDICATIONS ON ADMISSION", "DISCHARGE INSTRUCTIONS",
    "FOLLOWUP INSTRUCTIONS", "PHYSICAL EXAM", "PERTINENT RESULTS",
]


class AuditError(Exception):
    """Raised for caller errors (unknown case, bad argument)."""


# ----------------------------------------------------------------- registry
def _cases() -> dict:
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))


def allowed_case_ids() -> list[str]:
    ids = list(_cases())
    allow = os.environ.get("AUDIT_ALLOWED_CASES")
    if allow:
        ids = [c for c in ids if c in allow.split(",")]
    return ids


def get_case(case_id: str) -> dict:
    if not isinstance(case_id, str) or case_id not in allowed_case_ids():
        raise AuditError(f"unknown or disallowed case_id: {case_id!r}. Allowed: {allowed_case_ids()}")
    spec = dict(_cases()[case_id])
    base = Path(spec.get("base", ROOT))
    out = {"case_id": case_id, "description": spec.get("description", "")}
    for k, v in spec.items():
        if k in ("base", "description"):
            continue
        if isinstance(v, list):
            out[k] = [_abs(base, x) for x in v]
        elif isinstance(v, str):
            out[k] = _abs(base, v)
        else:
            out[k] = v
    return out


def _abs(base: Path, p: str) -> Path:
    q = Path(os.path.expanduser(p))
    return q if q.is_absolute() else (base / q)


# --------------------------------------------------------------- provenance
def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def prov(p: Path | None) -> dict:
    if p is None:
        return {"path": None, "exists": False}
    p = Path(p)
    if not p.exists():
        return {"path": str(p), "exists": False}
    if p.is_dir():
        names = sorted(os.listdir(p))
        h = hashlib.sha256()
        for n in names:
            h.update(n.encode())
            try:
                h.update(str(os.path.getsize(p / n)).encode())
            except OSError:
                pass
        return {"path": str(p), "exists": True, "kind": "dir", "n_files": len(names),
                "listing_sha256": h.hexdigest()}
    return {"path": str(p), "exists": True, "kind": "file", "sha256": sha256_file(p)}


def _wrap(tool: str, case: dict, files: list, result: dict) -> dict:
    return {"tool": tool, "tool_version": TOOL_VERSION, "case_id": case["case_id"],
            "provenance": [prov(f) for f in files], "result": result}


def _missing(tool: str, case: dict, what: str, path) -> dict:
    return {"tool": tool, "tool_version": TOOL_VERSION, "case_id": case["case_id"],
            "status": "missing_artifact", "missing": what,
            "provenance": [prov(path) if path else {"path": None, "exists": False}],
            "result": None}


def _r(x, n=4):
    return None if x is None else round(float(x), n)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


# ---------------------------------------------------------- record loading
@lru_cache(maxsize=8)
def _records(case_id: str) -> dict:
    """Load receipts (+normalized outputs, + views) for a case. Cached per process."""
    case = get_case(case_id)
    rdir = case.get("receipts_dir")
    if not rdir or not Path(rdir).is_dir():
        return {"ok": False}
    receipts, malformed = [], []
    for p in sorted(Path(rdir).glob("*.json")):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(r, dict) or "note_id" not in r:
                raise ValueError("not a receipt object")
            receipts.append(r)
        except Exception:
            malformed.append(p.stem)
    return {"ok": True, "receipts": receipts, "malformed": malformed}


def _is_valid(r: dict) -> bool:
    return bool((r.get("parse") or {}).get("json_ok")) and bool((r.get("schema_validation") or {}).get("is_valid"))


def _read_text(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _load_norm(case: dict, nid: str) -> dict | None:
    d = case.get("normalized_dir")
    if not d:
        return None
    p = Path(d) / f"{nid}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_view(case: dict, nid: str) -> str | None:
    d = case.get("views_dir")
    return _read_text(Path(d) / f"{nid}.txt") if d else None


@lru_cache(maxsize=4)
def _sources(case_id: str) -> dict:
    case = get_case(case_id)
    p = case.get("source_jsonl")
    out = {}
    if p and Path(p).exists():
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    out[str(rec["note_id"])] = str(rec.get("text", ""))
                except Exception:
                    continue
    return out


def _sections(text: str) -> dict:
    up = text.upper()
    hits = sorted((up.find(h), h) for h in SECTION_HEADERS if up.find(h) != -1)
    out = {}
    for i, (idx, h) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        out[h] = (idx, end)
    return out


# ================================================================== TOOLS
def list_case_artifacts(case_id: str) -> dict:
    case = get_case(case_id)
    keys = ["receipts_dir", "normalized_dir", "views_dir", "source_jsonl", "run_meta_files",
            "extractor_code", "prompt_file", "gold_csv", "roundtrip_csv", "claims_file"]
    arts = {}
    files = []
    for k in keys:
        v = case.get(k)
        if v is None:
            arts[k] = {"registered": False}
        elif isinstance(v, list):
            arts[k] = [{"path": str(x), "exists": Path(x).exists()} for x in v]
            files += v
        else:
            arts[k] = {"path": str(v), "exists": Path(v).exists()}
            files.append(v)
    return _wrap("list_case_artifacts", case, [], {"description": case["description"], "artifacts": arts})


def get_reported_claims(case_id: str) -> dict:
    case = get_case(case_id)
    p = case.get("claims_file")
    if not p or not Path(p).exists():
        return _missing("get_reported_claims", case, "claims_file", p)
    claims = json.loads(Path(p).read_text(encoding="utf-8"))
    pub = [{k: c[k] for k in ("claim_id", "text", "metric_key", "reported_value") if k in c}
           for c in claims["claims"]]
    return _wrap("get_reported_claims", case, [p], {"source": claims.get("source", ""), "claims": pub})


def validate_structured_outputs(case_id: str) -> dict:
    case = get_case(case_id)
    R = _records(case_id)
    if not R["ok"]:
        return _missing("validate_structured_outputs", case, "receipts_dir", case.get("receipts_dir"))
    rec = R["receipts"]
    valid = [r for r in rec if _is_valid(r)]
    failed = [r for r in rec if not _is_valid(r)]
    cats = Counter()
    for r in failed:
        cats[_fail_category(r)] += 1
    json_ok_invalid = sum(1 for r in failed if (r.get("parse") or {}).get("json_ok"))
    n_norm = len(list(Path(case["normalized_dir"]).glob("*.json"))) if case.get("normalized_dir") and Path(case["normalized_dir"]).is_dir() else None
    return _wrap("validate_structured_outputs", case, [case.get("receipts_dir"), case.get("normalized_dir")], {
        "n_receipt_files": len(rec) + len(R["malformed"]),
        "n_receipts_parsed": len(rec),
        "n_malformed_receipt_files": len(R["malformed"]),
        "malformed_receipt_ids": R["malformed"][:20],
        "n_valid": len(valid), "n_failed": len(failed),
        "valid_rate_over_parsed_receipts": _r(len(valid) / len(rec)) if rec else None,
        "n_json_ok_but_schema_invalid": json_ok_invalid,
        "failure_categories": dict(cats),
        "failed_ids": [r["note_id"] for r in failed][:50],
        "n_normalized_files": n_norm,
    })


def _fail_category(r: dict) -> str:
    err = ((r.get("parse") or {}).get("error") or "") + " " + " ".join((r.get("schema_validation") or {}).get("errors") or [])
    e = err.lower()
    if "input tokens" in e or "context length" in e or "maximum context" in e:
        return "context_length_exceeded"
    if any(s in e for s in ("unterminated", "expecting", "jsondecode", "extra data", "invalid control")):
        return "malformed_json"
    if "empty message" in e:
        return "empty_response"
    if "http" in e or "timeout" in e or "connection" in e:
        return "transport_error"
    if (r.get("parse") or {}).get("json_ok"):
        return "schema_violation"
    return "other"


def identify_failure_patterns(case_id: str) -> dict:
    case = get_case(case_id)
    R = _records(case_id)
    if not R["ok"]:
        return _missing("identify_failure_patterns", case, "receipts_dir", case.get("receipts_dir"))
    failed = [r for r in R["receipts"] if not _is_valid(r)]
    by_subj = Counter(str(r.get("subject_id")) for r in failed)
    top = by_subj.most_common(1)[0] if by_subj else (None, 0)
    trimmed = sum(1 for r in failed if ((r.get("input_preprocessing") or {}).get("extraction_view") or {}).get("trimmed"))
    view_chars = [((r.get("input_preprocessing") or {}).get("extraction_view") or {}).get("view_chars") for r in failed]
    all_vc = [((r.get("input_preprocessing") or {}).get("extraction_view") or {}).get("view_chars") for r in R["receipts"]]
    return _wrap("identify_failure_patterns", case, [case.get("receipts_dir")], {
        "n_failed": len(failed),
        "failures_by_category": dict(Counter(_fail_category(r) for r in failed)),
        "n_distinct_subjects_among_failures": len(by_subj),
        "top_subject_failure_count": top[1],
        "top_subject_share_of_failures": _r(top[1] / len(failed)) if failed else None,
        "n_failed_with_trimmed_view": trimmed,
        "median_view_chars_failed": _r(statistics.median([v for v in view_chars if v]), 1) if any(view_chars) else None,
        "median_view_chars_all": _r(statistics.median([v for v in all_vc if v]), 1) if any(all_vc) else None,
        "failed_ids": [r["note_id"] for r in failed][:50],
    })


def inspect_run_metadata(case_id: str) -> dict:
    case = get_case(case_id)
    metas, missing = [], []
    for p in case.get("run_meta_files") or []:
        if Path(p).exists():
            try:
                m = json.loads(Path(p).read_text(encoding="utf-8"))
                metas.append({"file": str(p), "model": m.get("model"),
                              "prompt_sha256": (m.get("prompt_sha256") or "")[:12],
                              "schema_sha256": (m.get("schema_sha256") or "")[:12],
                              "max_tokens": (m.get("decoding") or {}).get("max_tokens"),
                              "temperature": (m.get("decoding") or {}).get("temperature"),
                              "max_view_chars": m.get("max_view_chars"),
                              "retry_max_view_chars": m.get("retry_max_view_chars"),
                              "inputs": m.get("inputs"), "started": m.get("run_started_utc")})
            except Exception:
                missing.append({"file": str(p), "problem": "unparseable"})
        else:
            missing.append({"file": str(p), "problem": "missing"})
    R = _records(case_id)
    combos = Counter()
    if R["ok"]:
        for r in R["receipts"]:
            vm = (r.get("input_preprocessing") or {}).get("extraction_view") or {}
            combos[(r.get("model"), (r.get("prompt_sha256") or "")[:12], (r.get("schema_sha256") or "")[:12],
                    (r.get("decoding") or {}).get("max_tokens"), vm.get("max_chars"))] += 1
    combo_rows = []
    for (model, psha, ssha, mt, vc), n in combos.most_common():
        documented = any(m["model"] == model and m["prompt_sha256"] == psha and m["max_tokens"] == mt
                         and vc in (m["max_view_chars"], m["retry_max_view_chars"]) for m in metas)
        combo_rows.append({"model": model, "prompt_sha256": psha, "schema_sha256": ssha, "max_tokens": mt,
                           "view_max_chars": vc, "n_receipts": n, "documented_by_run_meta": documented})
    return _wrap("inspect_run_metadata", case, list(case.get("run_meta_files") or []) + [case.get("receipts_dir")], {
        "run_meta_records": metas, "run_meta_problems": missing,
        "n_distinct_receipt_setting_combinations": len(combo_rows),
        "receipt_setting_combinations": combo_rows,
        "n_receipts_with_undocumented_settings": sum(c["n_receipts"] for c in combo_rows if not c["documented_by_run_meta"]),
    })


def measure_exact_text_support(case_id: str) -> dict:
    """Uncapped verbatim (case-insensitive) presence of every extracted item in the extraction view
    (and in the source note when a source file is registered)."""
    case = get_case(case_id)
    R = _records(case_id)
    if not R["ok"] or not case.get("views_dir") or not Path(case["views_dir"]).is_dir():
        return _missing("measure_exact_text_support", case, "receipts_dir/views_dir", case.get("views_dir"))
    src = _sources(case_id)
    tot, inv, ins = Counter(), Counter(), Counter()
    n_notes = 0
    for r in R["receipts"]:
        if not _is_valid(r):
            continue
        nid = r["note_id"]
        S, view = _load_norm(case, nid), _load_view(case, nid)
        if S is None or view is None:
            continue
        n_notes += 1
        vlow = view.lower()
        slow = src.get(nid, "").lower() if src else ""
        for f in FIELDS:
            for it in S.get(f) or []:
                t = str(it).strip().lower()
                if not t:
                    continue
                tot[f] += 1
                inv[f] += t in vlow
                if src:
                    ins[f] += t in slow
    T = sum(tot.values())
    res = {"n_valid_notes_scored": n_notes, "denominator": "all extracted items in schema-valid records, no cap",
           "by_field": {f: {"n_items": tot[f], "verbatim_in_view_rate": _r(inv[f] / tot[f]) if tot[f] else None,
                            "verbatim_in_source_rate": (_r(ins[f] / tot[f]) if tot[f] else None) if src else None}
                        for f in FIELDS},
           "overall": {"n_items": T, "verbatim_in_view_rate": _r(sum(inv.values()) / T) if T else None,
                       "verbatim_in_source_rate": (_r(sum(ins.values()) / T) if T else None) if src else None},
           "source_registered": bool(src)}
    return _wrap("measure_exact_text_support", case, [case.get("normalized_dir"), case.get("views_dir"), case.get("source_jsonl")], res)


def inspect_evidence_construction(case_id: str) -> dict:
    """How are evidence spans produced? Combines static inspection of the registered extractor code and
    prompt with empirical checks on saved spans (first-occurrence matching, caps, offset base)."""
    case = get_case(case_id)
    static = {"prompt_requests_empty_evidence": None, "prompt_requests_offsets_from_model": None,
              "code_assigns_evidence_after_model_output": None, "code_cap_parameters": [], "code_lines": []}
    pf = case.get("prompt_file")
    if pf and Path(pf).exists():
        pt = Path(pf).read_text(encoding="utf-8", errors="ignore")
        static["prompt_requests_empty_evidence"] = bool(re.search(r'"evidence"\s*:\s*\[\s*\]|empty list', pt, re.I))
        static["prompt_requests_offsets_from_model"] = bool(re.search(r"\b(start|end)\b.*\b(offset|character)", pt, re.I | re.S)) and not static["prompt_requests_empty_evidence"]
    for cf in case.get("extractor_code") or []:
        if not Path(cf).exists():
            continue
        for i, line in enumerate(Path(cf).read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            s = line.strip()
            if re.search(r'\[\s*["\']evidence["\']\s*\]\s*=', s):
                static["code_assigns_evidence_after_model_output"] = True
                static["code_lines"].append(f"{Path(cf).name}:{i}: {s[:160]}")
            m = re.findall(r"\b(cap_\w+)\s*[:=]\s*(?:int\s*=\s*)?(\d+)", s)
            for name, val in m:
                static["code_cap_parameters"].append({"name": name, "value": int(val), "where": f"{Path(cf).name}:{i}"})
            if ".find(" in s and ("needle" in s or "item" in s):
                static["code_lines"].append(f"{Path(cf).name}:{i}: {s[:160]}")
    if static["code_assigns_evidence_after_model_output"] is None and case.get("extractor_code"):
        static["code_assigns_evidence_after_model_output"] = False
    static["code_lines"] = static["code_lines"][:12]

    R = _records(case_id)
    emp = None
    if R["ok"] and case.get("views_dir"):
        src = _sources(case_id)
        counts, med_counts = [], []
        n_sp = n_view_match = n_first = n_src_match = n_cov_items = n_items = n_verb_items = 0
        for r in R["receipts"]:
            if not _is_valid(r):
                continue
            S, view = _load_norm(case, r["note_id"]), _load_view(case, r["note_id"])
            if S is None or view is None:
                continue
            vlow = view.lower()
            slow = src.get(r["note_id"], "").lower() if src else None
            ev = S.get("evidence") or []
            counts.append(len(ev))
            med_counts.append(sum(1 for e in ev if e.get("field") == "medications"))
            covered = set()
            for e in ev:
                try:
                    a, b = int(e["start"]), int(e["end"])
                except Exception:
                    continue
                item = str(e.get("item", "")).strip().lower()
                n_sp += 1
                if vlow[a:b] == item:
                    n_view_match += 1
                    if vlow.find(item) == a:
                        n_first += 1
                if slow is not None and slow[a:b] == item:
                    n_src_match += 1
                covered.add((e.get("field"), item))
            for f in FIELDS:
                for it in S.get(f) or []:
                    t = str(it).strip().lower()
                    if not t:
                        continue
                    n_items += 1
                    n_cov_items += (f, t) in covered
                    n_verb_items += t in vlow
        if counts:
            mx, mmx = max(counts), max(med_counts)
            emp = {"n_valid_records": len(counts), "n_spans": n_sp,
                   "max_spans_per_record": mx, "share_records_at_max_spans": _r(sum(c == mx for c in counts) / len(counts)),
                   "max_medication_spans_per_record": mmx,
                   "share_records_at_max_medication_spans": _r(sum(c == mmx for c in med_counts) / len(med_counts)),
                   "share_spans_text_equals_item_in_view": _r(n_view_match / n_sp) if n_sp else None,
                   "share_spans_at_first_occurrence_in_view": _r(n_first / n_sp) if n_sp else None,
                   "share_spans_text_equals_item_in_source": (_r(n_src_match / n_sp) if n_sp else None) if src else None,
                   "item_coverage_by_spans": _r(n_cov_items / n_items) if n_items else None,
                   "item_verbatim_presence_in_view": _r(n_verb_items / n_items) if n_items else None}
    return _wrap("inspect_evidence_construction", case,
                 [pf] + list(case.get("extractor_code") or []) + [case.get("normalized_dir"), case.get("views_dir")],
                 {"static": static, "empirical": emp,
                  "notes": "share_spans_text_equals_item_in_source is null when no source file is registered."})


def check_metric_denominators(case_id: str, metric: str) -> dict:
    case = get_case(case_id)
    if metric not in RECEIPT_METRICS:
        raise AuditError(f"metric must be one of {RECEIPT_METRICS}")
    R = _records(case_id)
    if not R["ok"]:
        return _missing("check_metric_denominators", case, "receipts_dir", case.get("receipts_dir"))
    allv, validv, applic = [], [], []
    n_null = n_zero_valid = n_na_zero = n_na_null = 0
    for r in R["receipts"]:
        m = (r.get("evidence_metrics") or {})
        v = m.get(metric)
        allv.append(0.0 if v is None else float(v))
        if not _is_valid(r):
            continue
        if v is None:
            n_null += 1
        else:
            validv.append(float(v))
            n_zero_valid += float(v) == 0.0
        applicable = _applicable(case, r, metric)
        if applicable is None:
            continue
        if applicable:
            if v is not None:
                applic.append(float(v))
        else:
            if v is None:
                n_na_null += 1
            elif float(v) == 0.0:
                n_na_zero += 1
    res = {"metric": metric, "n_records": len(R["receipts"]), "n_valid": len(validv) + n_null,
           "mean_all_records_failed_as_zero": _r(_mean(allv)),
           "mean_valid_records": _r(_mean(validv)),
           "n_valid_records_value_zero": n_zero_valid, "n_valid_records_value_null": n_null,
           "applicability_rule": _APPLIC_RULE.get(metric),
           "n_applicable": len(applic) if applic or n_na_zero or n_na_null else None,
           "n_not_applicable_scored_zero": n_na_zero, "n_not_applicable_scored_null": n_na_null,
           "mean_applicable_records": _r(_mean(applic))}
    return _wrap("check_metric_denominators", case, [case.get("receipts_dir"), case.get("normalized_dir"), case.get("views_dir")], res)


_APPLIC_RULE = {
    "med_section_alignment_rate": "applicable iff a DISCHARGE MEDICATIONS or MEDICATIONS ON ADMISSION header is found in the view AND the record has >=1 medication evidence span",
    "coverage": "applicable iff the record has >=1 extracted item",
    "span_valid_rate": "applicable iff the record has >=1 evidence span",
    "substring_support_rate": "applicable iff the record has >=1 evidence span",
    "evidence_count": "always applicable",
}


def _applicable(case: dict, r: dict, metric: str):
    S = _load_norm(case, r["note_id"])
    if S is None:
        return None
    ev = S.get("evidence") or []
    if metric == "med_section_alignment_rate":
        view = _load_view(case, r["note_id"])
        if view is None:
            return None
        secs = _sections(view)
        has_sec = "DISCHARGE MEDICATIONS" in secs or "MEDICATIONS ON ADMISSION" in secs
        return has_sec and any(e.get("field") == "medications" for e in ev)
    if metric == "coverage":
        return any(S.get(f) for f in FIELDS)
    if metric in ("span_valid_rate", "substring_support_rate"):
        return len(ev) > 0
    return True


def _split_cell(s) -> list[str]:
    s = (s or "").strip()
    if not s:
        return []
    parts = s.split(";") if ";" in s else s.splitlines()
    return [p.strip() for p in parts if p.strip()]


def _canon(x: str) -> str:
    return re.sub(r"\s+", " ", (x or "").strip().lower())


_MED_STOP = set("po iv im sc pr qd daily bid tid qid qhs qam qpm qod tablet tab tabs capsule cap caps patch "
                "inhaler neb nebulizer spray mg mcg g gram grams ml l unit units meq % w meals w/meals with".split())


def _med_base(x: str) -> str:
    x = re.sub(r"[^\w\s\-+/]", " ", (x or "").lower().strip())
    toks = [t for t in re.sub(r"\s+", " ", x).split()
            if t not in _MED_STOP and not re.fullmatch(r"\d+(\.\d+)?", t) and not re.fullmatch(r"\d+/\d+", t)]
    return " ".join(toks[:2])


def evaluate_gold_labels(case_id: str) -> dict:
    case = get_case(case_id)
    g = case.get("gold_csv")
    if not g or not Path(g).exists():
        return _missing("evaluate_gold_labels", case, "gold_csv", g)
    rows = list(csv.DictReader(open(g, encoding="utf-8", errors="replace")))
    out, missing_pred = {}, 0
    preds = {}
    for row in rows:
        S = _load_norm(case, row["note_id"])
        if S is None:
            missing_pred += 1
        preds[row["note_id"]] = S or {}
    for f in FIELDS:
        c = _med_base if f == "medications" else _canon
        TP = P = G = 0
        f1s = []
        for row in rows:
            Gs = {c(x) for x in _split_cell(row.get(f"gold_{f}")) if c(x)}
            Ps = {c(x) for x in (preds[row["note_id"]].get(f) or []) if c(str(x))}
            tp = len(Gs & Ps)
            TP, P, G = TP + tp, P + len(Ps), G + len(Gs)
            if not Gs and not Ps:
                f1s.append(1.0)
            else:
                p = tp / len(Ps) if Ps else 0.0
                rr = tp / len(Gs) if Gs else 0.0
                f1s.append(2 * p * rr / (p + rr) if p + rr else 0.0)
        mp = TP / P if P else 0.0
        mr = TP / G if G else 0.0
        out[f] = {"tp": TP, "n_pred": P, "n_gold": G, "micro_precision": _r(mp), "micro_recall": _r(mr),
                  "micro_f1": _r(2 * mp * mr / (mp + mr) if mp + mr else 0.0), "mean_note_f1": _r(_mean(f1s))}
    return _wrap("evaluate_gold_labels", case, [g, case.get("normalized_dir")], {
        "n_notes": len(rows), "n_notes_missing_prediction": missing_pred,
        "matching": "lowercase+whitespace exact set match; medications on base name (first two tokens after removing dose/route/frequency/form tokens)",
        "by_field": out})


def evaluate_roundtrip(case_id: str) -> dict:
    case = get_case(case_id)
    p = case.get("roundtrip_csv")
    if not p or not Path(p).exists():
        return _missing("evaluate_roundtrip", case, "roundtrip_csv", p)
    rows = list(csv.DictReader(open(p, encoding="utf-8")))
    res = {"n_notes": len(rows), "aggregation": "mean over notes of per-note Jaccard", "by_field": {}}
    for f in FIELDS:
        vals = [float(r[f"{f}_jaccard"]) for r in rows if r.get(f"{f}_jaccard") not in (None, "")]
        res["by_field"][f] = {"mean_jaccard": _r(_mean(vals)), "min": _r(min(vals)) if vals else None,
                              "n": len(vals), "n_perfect": sum(v == 1.0 for v in vals)}
    return _wrap("evaluate_roundtrip", case, [p], res)


# ------------------------------------------------------------- verify_claim
def verify_claim(case_id: str, claim_id: str) -> dict:
    case = get_case(case_id)
    p = case.get("claims_file")
    if not p or not Path(p).exists():
        return _missing("verify_claim", case, "claims_file", p)
    claims = {c["claim_id"]: c for c in json.loads(Path(p).read_text(encoding="utf-8"))["claims"]}
    if claim_id not in claims:
        raise AuditError(f"unknown claim_id {claim_id!r}; known: {sorted(claims)}")
    c = claims[claim_id]
    key, rep = c["metric_key"], c["reported_value"]
    tol = float(c.get("tolerance", DEFAULT_TOL))
    base = {"tool": "verify_claim", "tool_version": TOOL_VERSION, "case_id": case_id, "claim_id": claim_id,
            "metric_key": key, "reported_value": rep, "tolerance": tol}

    def done(status, recomputed: dict, src_tool, files):
        matches = {k: (v is not None and abs(float(v) - float(rep)) <= tol) for k, v in recomputed.items()}
        any_match = any(matches.values())
        return {**base, "status": status if status != "ok" else ("reproduced" if any_match else "not_reproduced"),
                "recomputed": recomputed, "matches_by_convention": matches, "computed_with": src_tool,
                "provenance": [prov(f) for f in files]}

    m = re.fullmatch(r"(gold_micro_f1|gold_micro_precision|gold_micro_recall)_(symptoms|diagnoses|medications)", key)
    if m:
        out = evaluate_gold_labels(case_id)
        if out.get("status") == "missing_artifact":
            return {**base, "status": "cannot_verify", "reason": "missing gold_csv", "provenance": out["provenance"]}
        stat = m.group(1).replace("gold_", "")
        return done("ok", {"micro": out["result"]["by_field"][m.group(2)][stat]}, "evaluate_gold_labels", [case.get("gold_csv")])
    m = re.fullmatch(r"roundtrip_jaccard_(symptoms|diagnoses|medications)", key)
    if m:
        out = evaluate_roundtrip(case_id)
        if out.get("status") == "missing_artifact":
            return {**base, "status": "cannot_verify", "reason": "missing roundtrip_csv", "provenance": out["provenance"]}
        return done("ok", {"mean_over_notes": out["result"]["by_field"][m.group(1)]["mean_jaccard"]}, "evaluate_roundtrip", [case.get("roundtrip_csv")])
    m = re.fullmatch(r"exact_support_(symptoms|diagnoses|medications|overall)", key)
    if m:
        out = measure_exact_text_support(case_id)
        if out.get("status") == "missing_artifact":
            return {**base, "status": "cannot_verify", "reason": "missing views", "provenance": out["provenance"]}
        f = m.group(1)
        v = out["result"]["overall"] if f == "overall" else out["result"]["by_field"][f]
        return done("ok", {"verbatim_in_view": v["verbatim_in_view_rate"]}, "measure_exact_text_support", [case.get("views_dir")])
    R = _records(case_id)
    if not R["ok"]:
        return {**base, "status": "cannot_verify", "reason": "missing receipts_dir", "provenance": [prov(case.get("receipts_dir"))]}
    rec = R["receipts"]
    if key in ("schema_valid_count", "schema_valid_rate"):
        nv = sum(_is_valid(r) for r in rec)
        n_files = len(rec) + len(R["malformed"])
        val = {"over_all_receipt_files": nv if key == "schema_valid_count" else _r(nv / n_files) if n_files else None}
        return done("ok", val, "validate_structured_outputs", [case.get("receipts_dir")])
    m = re.fullmatch(r"mean_(span_valid_rate|coverage|substring_support_rate|med_section_alignment_rate|evidence_count)", key)
    if m:
        d = check_metric_denominators(case_id, m.group(1))["result"]
        return done("ok", {"valid_records_only": d["mean_valid_records"],
                           "all_records_failed_as_zero": d["mean_all_records_failed_as_zero"],
                           "applicable_records_only": d["mean_applicable_records"]},
                    "check_metric_denominators", [case.get("receipts_dir")])
    m = re.fullmatch(r"present_rate_(symptoms|diagnoses|medications)", key)
    if m:
        vals = [float(((r.get("evidence_metrics") or {}).get("evidence_present_rate") or {}).get(m.group(1), 0.0))
                for r in rec if _is_valid(r)]
        return done("ok", {"valid_records_only": _r(_mean(vals))}, "receipts", [case.get("receipts_dir")])
    return {**base, "status": "cannot_verify", "reason": f"no recomputation rule for metric_key {key!r}", "provenance": []}


# --------------------------------------------------------- report schema
FINDING_CODES = [
    "CLAIM_NOT_REPRODUCED", "ZERO_FILLED_DENOMINATOR", "MIXED_DENOMINATORS", "EVIDENCE_POSTHOC",
    "EVIDENCE_CAP_LIMITS_COVERAGE", "OFFSETS_NOT_IN_SOURCE", "TAUTOLOGICAL_METRIC",
    "HETEROGENEOUS_RUN_SETTINGS", "MISSING_RUN_METADATA", "MALFORMED_RECEIPTS", "MISSING_ARTIFACT",
    "FAILURES_CLUSTERED", "NO_ISSUES_FOUND", "OTHER",
]
CATEGORIES = ["confirmed_finding", "suspicion", "missing_evidence", "tool_failure", "interpretation", "recommendation"]
TARGETED = {"CLAIM_NOT_REPRODUCED": "claim_id", "ZERO_FILLED_DENOMINATOR": "metric name",
            "TAUTOLOGICAL_METRIC": "metric name", "MISSING_ARTIFACT": "artifact key"}


def validate_report(findings: Any) -> list[str]:
    errs = []
    if not isinstance(findings, list) or not findings:
        return ["findings must be a non-empty list"]
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            errs.append(f"finding {i}: must be an object")
            continue
        if f.get("code") not in FINDING_CODES:
            errs.append(f"finding {i}: code must be one of {FINDING_CODES}")
        if f.get("category") not in CATEGORIES:
            errs.append(f"finding {i}: category must be one of {CATEGORIES}")
        if f.get("code") in TARGETED and not f.get("target"):
            errs.append(f"finding {i}: code {f.get('code')} requires target ({TARGETED[f['code']]})")
        if not isinstance(f.get("statement", ""), str) or not f.get("statement"):
            errs.append(f"finding {i}: statement required")
        if not isinstance(f.get("evidence_call_ids", []), list):
            errs.append(f"finding {i}: evidence_call_ids must be a list")
    return errs
