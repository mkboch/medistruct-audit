"""Condition A: deterministic auditing workflow (no LLM).

Runs a fixed, exhaustive sequence of the same audit tools and applies frozen decision rules to
produce findings in the same report format as the agent. Rules were frozen before held-out
fixtures were generated (see CHANGELOG.md).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import audit_core as core  # noqa: E402

RULES_VERSION = "1.0.0"
METRICS = ["med_section_alignment_rate", "coverage", "span_valid_rate", "substring_support_rate"]


def run(case_id: str, run_dir: Path) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    log = []
    t0 = time.time()

    def call(name, fn, **kw):
        cid = f"b{len(log):03d}"
        t = time.time()
        try:
            res = fn(**kw)
            log.append({"call_id": cid, "tool": name, "args": kw, "ok": True, "duration_s": round(time.time() - t, 3), "result": res})
            return cid, res
        except Exception as e:
            log.append({"call_id": cid, "tool": name, "args": kw, "ok": False, "error": str(e)})
            return cid, None

    F = []

    def add(code, target, statement, ids):
        F.append({"code": code, "category": "missing_evidence" if code == "MISSING_ARTIFACT" else "confirmed_finding",
                  "target": target, "statement": statement, "evidence_call_ids": ids})

    a_id, arts = call("list_case_artifacts", core.list_case_artifacts, case_id=case_id)
    for k in ("gold_csv", "roundtrip_csv", "receipts_dir", "normalized_dir", "views_dir", "claims_file", "prompt_file"):
        v = (arts or {}).get("result", {}).get("artifacts", {}).get(k)
        if isinstance(v, dict) and v.get("registered", True) is not False and v.get("exists") is False:
            add("MISSING_ARTIFACT", k, f"Registered artifact {k} is missing.", [a_id])
    v_id, val = call("validate_structured_outputs", core.validate_structured_outputs, case_id=case_id)
    if val and val.get("result") and val["result"]["n_malformed_receipt_files"] > 0:
        add("MALFORMED_RECEIPTS", "", f"{val['result']['n_malformed_receipt_files']} receipt files could not be parsed.", [v_id])
    f_id, fp = call("identify_failure_patterns", core.identify_failure_patterns, case_id=case_id)
    if fp and fp.get("result"):
        r = fp["result"]
        if r["n_failed"] >= 2 and (r["top_subject_share_of_failures"] or 0) >= 0.5:
            add("FAILURES_CLUSTERED", "", f"{r['top_subject_failure_count']} of {r['n_failed']} failures come from one subject.", [f_id])
    m_id, meta = call("inspect_run_metadata", core.inspect_run_metadata, case_id=case_id)
    if meta and meta.get("result"):
        r = meta["result"]
        if r["n_distinct_receipt_setting_combinations"] > 1:
            add("HETEROGENEOUS_RUN_SETTINGS", "", f"{r['n_distinct_receipt_setting_combinations']} distinct setting combinations in receipts.", [m_id])
        if r["n_receipts_with_undocumented_settings"] > 0 or r["run_meta_problems"]:
            add("MISSING_RUN_METADATA", "", f"{r['n_receipts_with_undocumented_settings']} receipts have settings not documented by run metadata; {len(r['run_meta_problems'])} run-metadata files missing/unparseable.", [m_id])
    e_id, evc = call("inspect_evidence_construction", core.inspect_evidence_construction, case_id=case_id)
    posthoc = False
    if evc and evc.get("result"):
        st, em = evc["result"]["static"], evc["result"]["empirical"] or {}
        first = em.get("share_spans_at_first_occurrence_in_view")
        eq = em.get("share_spans_text_equals_item_in_view")
        posthoc = bool((st.get("prompt_requests_empty_evidence") or st.get("code_assigns_evidence_after_model_output"))
                       and first is not None and first >= 0.99 and eq is not None and eq >= 0.99)
        if posthoc:
            add("EVIDENCE_POSTHOC", "", "Spans are constructed after model output by first-occurrence string matching.", [e_id])
            add("TAUTOLOGICAL_METRIC", "span_valid_rate", "Span validity holds by construction for string-matched spans.", [e_id])
            add("TAUTOLOGICAL_METRIC", "substring_support_rate", "Substring support holds by construction for string-matched spans.", [e_id])
            caps = [c for c in st.get("code_cap_parameters", []) if c["value"] > 0]
            cov, verb = em.get("item_coverage_by_spans"), em.get("item_verbatim_presence_in_view")
            if caps and (em.get("share_records_at_max_spans") or 0) >= 0.2 and cov is not None and verb is not None and cov < verb - 0.05:
                add("EVIDENCE_CAP_LIMITS_COVERAGE", "", f"Span coverage {cov} vs verbatim presence {verb}; per-record cap binds.", [e_id])
        src = em.get("share_spans_text_equals_item_in_source")
        if src is not None and eq is not None and eq >= 0.99 and src < 0.9:
            add("OFFSETS_NOT_IN_SOURCE", "", f"Only {src} of spans match the item at the same offsets in the source note.", [e_id])
    for metric in METRICS:
        d_id, den = call("check_metric_denominators", core.check_metric_denominators, case_id=case_id, metric=metric)
        if den and den.get("result"):
            r = den["result"]
            ma, mv = r["mean_applicable_records"], r["mean_valid_records"]
            if r["n_not_applicable_scored_zero"] > 0 and (ma is None or mv is None or abs(ma - mv) >= 0.01):
                add("ZERO_FILLED_DENOMINATOR", metric, f"{r['n_not_applicable_scored_zero']} non-applicable records scored 0; valid mean {mv}, applicable mean {ma}.", [d_id])
    call("measure_exact_text_support", core.measure_exact_text_support, case_id=case_id)
    call("evaluate_gold_labels", core.evaluate_gold_labels, case_id=case_id)
    call("evaluate_roundtrip", core.evaluate_roundtrip, case_id=case_id)
    c_id, cl = call("get_reported_claims", core.get_reported_claims, case_id=case_id)
    conv_only = {"valid_records_only": 0, "all_records_failed_as_zero": 0}
    mixed_ids = []
    for c in ((cl or {}).get("result") or {}).get("claims", []):
        k_id, vr = call("verify_claim", core.verify_claim, case_id=case_id, claim_id=c["claim_id"])
        if not vr:
            continue
        if vr["status"] == "not_reproduced":
            add("CLAIM_NOT_REPRODUCED", c["claim_id"], f"Reported {vr['reported_value']}; recomputed {vr['recomputed']}.", [k_id])
        elif vr["status"] == "reproduced" and c["metric_key"].startswith("mean_"):
            m = vr["matches_by_convention"]
            if m.get("valid_records_only") and not m.get("all_records_failed_as_zero"):
                conv_only["valid_records_only"] += 1
                mixed_ids.append(k_id)
            if m.get("all_records_failed_as_zero") and not m.get("valid_records_only"):
                conv_only["all_records_failed_as_zero"] += 1
                mixed_ids.append(k_id)
    if all(conv_only.values()):
        add("MIXED_DENOMINATORS", "", f"Reported means use different denominators: {conv_only}.", mixed_ids)
    if not F:
        add("NO_ISSUES_FOUND", "", "No issue detected by the fixed checklist.", [])
    report = {"case_id": case_id, "findings": F, "summary": "deterministic checklist", "rules_version": RULES_VERSION}
    (run_dir / "report.json").write_text(json.dumps(report, indent=2))
    with open(run_dir / "tool_calls.jsonl", "w") as f:
        for e in log:
            f.write(json.dumps(e) + "\n")
    meta = {"condition": "A_deterministic", "case_id": case_id, "n_tool_calls": len(log), "duration_s": round(time.time() - t0, 2),
            "status": "ok"}
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2))
    return meta


if __name__ == "__main__":
    print(run(sys.argv[1], Path(sys.argv[2])))
