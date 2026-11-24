"""Score audit runs (any condition) against reference findings.

A run directory contains report.json, tool_calls.jsonl, run_meta.json.
Predicted positives = findings with category confirmed_finding or missing_evidence, restricted to the task scope.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SCOPES = {
    "full": None,
    "evidence": {"EVIDENCE_POSTHOC", "EVIDENCE_CAP_LIMITS_COVERAGE", "OFFSETS_NOT_IN_SOURCE", "TAUTOLOGICAL_METRIC"},
    "med_alignment": {"ZERO_FILLED_DENOMINATOR", "CLAIM_NOT_REPRODUCED"},
    "claims": {"CLAIM_NOT_REPRODUCED", "MIXED_DENOMINATORS", "MISSING_ARTIFACT"},
    "provenance": {"HETEROGENEOUS_RUN_SETTINGS", "MISSING_RUN_METADATA", "MALFORMED_RECEIPTS", "FAILURES_CLUSTERED"},
}
ASSERTED = {"confirmed_finding", "missing_evidence"}
METRIC_ALIASES = {"med_section_alignment_rate": ["med", "align"], "coverage": ["coverage"],
                  "span_valid_rate": ["span", "valid"], "substring_support_rate": ["substring"],
                  "evidence_count": ["evidence", "count"]}
ARTIFACT_ALIASES = {"gold_csv": ["gold"], "roundtrip_csv": ["roundtrip"], "claims_file": ["claim"],
                    "receipts_dir": ["receipt"], "views_dir": ["view"], "normalized_dir": ["normalized"],
                    "prompt_file": ["prompt"]}


def norm_target(code: str, target: str) -> str:
    t = (target or "").strip().lower()
    if code == "CLAIM_NOT_REPRODUCED":
        m = re.search(r"c\d+", t)
        return m.group(0).upper() if m else t
    if code in ("ZERO_FILLED_DENOMINATOR", "TAUTOLOGICAL_METRIC"):
        for k, toks in METRIC_ALIASES.items():
            if t == k or all(x in t for x in toks):
                return k
        return t
    if code == "MISSING_ARTIFACT":
        for k, toks in ARTIFACT_ALIASES.items():
            if t == k or any(x in t for x in toks):
                return k
        return t
    return ""


def in_scope(code: str, target: str, scope: str, claims: dict) -> bool:
    s = SCOPES[scope]
    if code in ("NO_ISSUES_FOUND", "OTHER"):
        return False
    if s is None:
        return True
    if code not in s:
        return False
    if scope == "med_alignment":
        if code == "ZERO_FILLED_DENOMINATOR":
            return target == "med_section_alignment_rate"
        if code == "CLAIM_NOT_REPRODUCED":
            return "med_section_alignment" in claims.get(target, "")
    return True


def numbers_in(text: str) -> list[str]:
    return re.findall(r"(?<![A-Za-z_\d.])(\d+\.\d+|\d{2,})(?![A-Za-z_\d])", text or "")


def collect_numbers(obj, out: set):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_numbers(v, out)
    elif isinstance(obj, str):
        for n in numbers_in(obj):
            out.add(float(n))


def supported(numstr: str, pool: set) -> bool:
    x = float(numstr)
    dec = len(numstr.split(".")[1]) if "." in numstr else 0
    tol = 0.5 * 10 ** (-dec) + 1e-9
    for v in pool:
        if abs(v - x) <= tol or abs(v * 100 - x) <= max(tol, 0.051) or abs(v / 100 - x) <= tol:
            return True
    return False


def score_run(run_dir: Path, task: dict, reference: dict, claims_map: dict) -> dict:
    scope = task["scope"]
    rep_p = run_dir / "report.json"
    log_p = run_dir / "tool_calls.jsonl"
    calls = [json.loads(l) for l in open(log_p)] if log_p.exists() else []
    call_ids = {c["call_id"] for c in calls}
    meta = json.loads((run_dir / "run_meta.json").read_text()) if (run_dir / "run_meta.json").exists() else {}
    ref = {(i["code"], norm_target(i["code"], i["target"])) for i in reference["issues"]
           if in_scope(i["code"], norm_target(i["code"], i["target"]), scope, claims_map)}
    # A report that exists but cannot be parsed is treated as not submitted (no credit) and flagged.
    rep, report_malformed = None, False
    if rep_p.exists():
        try:
            rep = json.loads(rep_p.read_text())
            if not isinstance(rep, dict) or not isinstance(rep.get("findings", []), list):
                raise ValueError("report is not an object with a findings list")
        except (ValueError, json.JSONDecodeError):
            rep, report_malformed = None, True
    out = {"task_id": task["task_id"], "case_id": task["case_id"], "scope": scope, "split": task["split"],
           "reference": sorted(map(list, ref)), "report_submitted": rep is not None, "report_malformed": report_malformed}
    n_tool = sum(1 for c in calls if c["tool"] != "submit_audit_report")
    n_err = sum(1 for c in calls if not c.get("ok"))
    out.update(n_tool_calls=n_tool, n_tool_errors=n_err,
               n_argument_errors=sum(1 for c in calls if str(c.get("error", "")).startswith("argument_error")),
               n_submit_attempts=sum(1 for c in calls if c["tool"] == "submit_audit_report"),
               budget_exhausted=any(c.get("error") == "budget_exhausted" for c in calls),
               duration_s=meta.get("duration_s"), total_tokens=meta.get("total_tokens"),
               n_distinct_tools=len({c["tool"] for c in calls if c["tool"] != "submit_audit_report"}),
               n_repeated_calls=n_tool - len({(c["tool"], json.dumps(c.get("args"), sort_keys=True)) for c in calls if c["tool"] != "submit_audit_report"}))
    if rep is None:  # missing or malformed report: no credit on any task
        out.update(tp=0, fp=0, fn=len(ref), precision=None, recall=0.0 if ref else None, exact_match=False,
                   no_issue_correct=None if ref else False, unsupported_numbers=None, n_numbers=None,
                   uncited_confirmed=None, bad_citations=None, false_refutation_unverifiable=0, pred=[])
        return out
    finds = [f for f in rep.get("findings", []) if isinstance(f, dict)]
    pred = set()
    for f in finds:
        if f.get("category") in ASSERTED:
            code = f.get("code", "OTHER")
            k = (code, norm_target(code, f.get("target", "")))
            if in_scope(k[0], k[1], scope, claims_map):
                pred.add(k)
    says_none = any(f.get("code") == "NO_ISSUES_FOUND" for f in finds)
    tp, fp, fn = len(pred & ref), len(pred - ref), len(ref - pred)
    pool = set()
    for c in calls:
        if c["tool"] != "submit_audit_report":
            collect_numbers(c.get("result"), pool)
    collect_numbers(task["instruction"], pool)
    nums = [n for f in finds for n in numbers_in(f.get("statement", ""))] + numbers_in(rep.get("summary", ""))
    unsup = [n for n in nums if not supported(n, pool)]
    confirmed = [f for f in finds if f.get("category") == "confirmed_finding" and f.get("code") != "NO_ISSUES_FOUND"]
    out.update(pred=sorted(map(list, pred)), tp=tp, fp=fp, fn=fn,
               precision=tp / (tp + fp) if tp + fp else None,   # undefined when nothing is asserted
               recall=tp / (tp + fn) if tp + fn else None,
               exact_match=(pred == ref) and (says_none == (not ref)) if not ref else pred == ref,
               no_issue_correct=(says_none and not pred) if not ref else None,
               n_numbers=len(nums), unsupported_numbers=len(unsup), unsupported_examples=unsup[:5],
               uncited_confirmed=sum(1 for f in confirmed if not f.get("evidence_call_ids")),
               bad_citations=sum(1 for f in finds for i in f.get("evidence_call_ids", []) if i not in call_ids),
               false_refutation_unverifiable=sum(1 for (c, t) in pred if c == "CLAIM_NOT_REPRODUCED"
                                                 and t in reference.get("unverifiable_claims", [])))
    return out
