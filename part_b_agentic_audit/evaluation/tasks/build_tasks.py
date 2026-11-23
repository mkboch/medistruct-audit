"""Build case registry, synthetic fixtures, real-case claims, and the dev / held-out task sets.

Run from part_b_agentic_audit/:  python evaluation/tasks/build_tasks.py

PUBLIC-RELEASE NOTE (modified from the research copy):
  * The historical extraction outputs (restricted MIMIC-IV-Note derivatives) are NOT distributed.
    Researchers with their own authorised outputs set MEDSTRUCT_HIST_DIR to the directory that
    contains Codes/, Dataset/ and Outputs/ in the historical layout; otherwise the real case is
    registered but its artifacts are reported as missing by the tools.
  * The 10-note external pilot case (real_mgh10, held-out task T29) is restricted and has been
    removed from the registry, claims, and references. T29 remains in tasks_heldout.json so task
    numbering matches the paper, but it cannot be executed from the public release.
  * This script writes BUILD_MANIFEST.json; the historical FROZEN_MANIFEST.json is left untouched.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

A = Path(__file__).resolve().parents[2]
HIST = Path(os.environ.get("MEDSTRUCT_HIST_DIR", Path.home() / "med_struct_audit"))
CFG = A / "configs"
FIX = A / "evaluation" / "fixtures"
REFS = FIX / "references"          # never mounted into the agent sandbox
CASES_DIR = FIX / "cases"
TASKS = A / "evaluation" / "tasks"

INSTR = {
    "full": ("Audit case {cid}. Determine whether the reported claims are reproducible from the saved artifacts and "
             "whether the evaluation metrics and evidence spans can be trusted. Investigate any problem you find "
             "before concluding. If you find no problems, say so."),
    "evidence": ("For case {cid}, investigate whether the evidence spans and the evidence-based metrics (span validity, "
                 "coverage, substring support) can be interpreted as evidence of model faithfulness to the source note."),
    "med_alignment": "For case {cid}, investigate whether the reported medication section alignment result is reliable.",
    "claims": ("For case {cid}, check whether each reported quantitative claim is reproducible from the saved artifacts, "
               "and explain any discrepancy."),
    "provenance": ("For case {cid}, assess whether the run is fully documented and internally consistent "
                   "(run settings, run metadata, receipt integrity, and failure patterns)."),
}

DEV_SPECS = [
    {"case_id": "dev_syn_a", "seed": 11, "n_notes": 80, "evidence_mode": "posthoc", "cap_total": 12, "cap_meds": 4,
     "med_impl": "zero_fill", "no_med_section_frac": 0.2, "n_fail": 3, "fail_clustered": True,
     "perturb_claims": ["gold_micro_f1_symptoms"]},
    {"case_id": "dev_syn_b", "seed": 12, "n_notes": 60, "evidence_mode": "model", "med_impl": "null_na"},
]
# Held-out specs were written before any agent run and are frozen by hash in tasks_heldout.json.
HELD_SPECS = [
    {"case_id": "H01", "seed": 101, "n_notes": 90, "evidence_mode": "posthoc", "cap_total": 12, "cap_meds": 4, "med_impl": "zero_fill",
     "no_med_section_frac": 0.25, "n_fail": 4, "fail_clustered": True, "hetero_frac": 0.1, "perturb_claims": ["mean_coverage"]},
    {"case_id": "H02", "seed": 102, "n_notes": 70, "evidence_mode": "model", "med_impl": "null_na"},
    {"case_id": "H03", "seed": 103, "n_notes": 70, "evidence_mode": "model", "med_impl": "zero_fill", "no_med_section_frac": 0.3},
    {"case_id": "H04", "seed": 104, "n_notes": 70, "evidence_mode": "posthoc", "med_impl": "null_na", "view_reordered": True},
    {"case_id": "H05", "seed": 105, "n_notes": 80, "evidence_mode": "model", "med_impl": "null_na", "hetero_frac": 0.15, "hetero_documented": False},
    {"case_id": "H06", "seed": 106, "n_notes": 60, "evidence_mode": "model", "med_impl": "null_na", "n_malformed": 3},
    {"case_id": "H07", "seed": 107, "n_notes": 60, "evidence_mode": "model", "med_impl": "null_na", "drop_roundtrip": True,
     "perturb_claims": ["gold_micro_f1_medications", "roundtrip_jaccard_symptoms"]},
    {"case_id": "H08", "seed": 108, "n_notes": 80, "evidence_mode": "posthoc", "cap_total": 8, "cap_meds": 3, "med_impl": "zero_fill",
     "n_fail": 3, "all_records_convention": ["mean_coverage", "mean_evidence_count"]},
    {"case_id": "H09", "seed": 109, "n_notes": 70, "evidence_mode": "model", "med_impl": "null_na", "n_fail": 2},
    {"case_id": "H10", "seed": 110, "n_notes": 70, "evidence_mode": "model", "med_impl": "null_na",
     "perturb_claims": ["schema_valid_count", "roundtrip_jaccard_diagnoses"]},
    {"case_id": "H11", "seed": 111, "n_notes": 70, "evidence_mode": "posthoc", "cap_total": 12, "cap_meds": 4, "med_impl": "null_na",
     "view_reordered": False},
    {"case_id": "H12", "seed": 112, "n_notes": 60, "evidence_mode": "model", "med_impl": "null_na", "drop_run_meta": True},
    {"case_id": "H13", "seed": 113, "n_notes": 60, "evidence_mode": "model", "med_impl": "null_na", "drop_gold": True},
    {"case_id": "H14", "seed": 114, "n_notes": 80, "evidence_mode": "model", "med_impl": "null_na", "n_fail": 5, "fail_clustered": True},
    {"case_id": "H15", "seed": 115, "n_notes": 70, "evidence_mode": "model", "med_impl": "null_na"},
    {"case_id": "H16", "seed": 116, "n_notes": 80, "evidence_mode": "posthoc", "cap_total": 12, "cap_meds": 4, "med_impl": "zero_fill",
     "hetero_frac": 0.1, "perturb_claims": ["mean_med_section_alignment_rate"]},
    {"case_id": "H17", "seed": 117, "n_notes": 80, "evidence_mode": "model", "med_impl": "null_na", "n_fail": 4,
     "all_records_convention": ["mean_span_valid_rate"]},
    {"case_id": "H18", "seed": 118, "n_notes": 150, "evidence_mode": "model", "med_impl": "null_na"},
]
SCOPED_HELD = [("H01", "med_alignment"), ("H04", "evidence"), ("H07", "claims"), ("H08", "claims"),
               ("H16", "med_alignment"), ("H02", "evidence"), ("H12", "provenance"), ("H14", "provenance"),
               ("H03", "med_alignment"), ("H15", "claims")]

DESC_REAL = "Saved outputs of a structured-extraction run over discharge summaries (receipts, normalized outputs, views, source notes, metadata, code, prompt, labels, roundtrip, reported claims)."
REAL = {
    "real_mimic5k": {
        "base": str(HIST), "description": DESC_REAL,
        "receipts_dir": "Outputs/discharge_extract_v2_run5k_FINAL/receipts",
        "normalized_dir": "Outputs/discharge_extract_v2_run5k_FINAL/normalized",
        "views_dir": "Outputs/discharge_extract_v2_run5k_FINAL/views",
        "source_jsonl": "Dataset/inputs_discharge_5k.jsonl",
        "run_meta_files": ["Outputs/discharge_extract_v2_run5k_FINAL/run_meta.json",
                           "Outputs/discharge_extract_v2_run5k_RETRY/run_meta.json",
                           "Outputs/discharge_extract_v2_run5k_RETRY94/run_meta.json"],
        "extractor_code": ["Codes/run_extract.py", "Codes/utils.py"],
        "prompt_file": "Codes/prompts/extract_discharge_v2.txt",
        "gold_csv": "Outputs/gold50_pack/gold50_annotation.csv",
        "roundtrip_csv": "Outputs/roundtrip_gold50/roundtrip_eval_summary.csv",
        "claims_file": str(CFG / "claims" / "real_mimic5k.json")},
    # real_mgh10 (restricted external pilot) removed from the public release.
}
CLAIMS_REAL = {
    "real_mimic5k": {"source": "Historical manuscript Table 1 (Medi_Struct_PNAS draft)", "claims": [
        {"claim_id": "C01", "text": "5k schema-valid outputs", "metric_key": "schema_valid_count", "reported_value": 4994, "tolerance": 0},
        {"claim_id": "C02", "text": "Evidence span validity (mean)", "metric_key": "mean_span_valid_rate", "reported_value": 0.9984},
        {"claim_id": "C03", "text": "Evidence coverage (mean)", "metric_key": "mean_coverage", "reported_value": 0.5760},
        {"claim_id": "C04", "text": "Substring support rate (mean)", "metric_key": "mean_substring_support_rate", "reported_value": 0.9996},
        {"claim_id": "C05", "text": "Medication section alignment (mean)", "metric_key": "mean_med_section_alignment_rate", "reported_value": 0.6603},
        {"claim_id": "C06", "text": "Mean evidence count", "metric_key": "mean_evidence_count", "reported_value": 9.945},
        {"claim_id": "C07", "text": "Evidence present rate, symptoms", "metric_key": "present_rate_symptoms", "reported_value": 0.718},
        {"claim_id": "C08", "text": "Evidence present rate, diagnoses", "metric_key": "present_rate_diagnoses", "reported_value": 0.837},
        {"claim_id": "C09", "text": "Evidence present rate, medications", "metric_key": "present_rate_medications", "reported_value": 0.336},
        {"claim_id": "C10", "text": "Gold50 micro-F1, symptoms", "metric_key": "gold_micro_f1_symptoms", "reported_value": 0.326},
        {"claim_id": "C11", "text": "Gold50 micro-F1, diagnoses", "metric_key": "gold_micro_f1_diagnoses", "reported_value": 0.261},
        {"claim_id": "C12", "text": "Gold50 micro-F1, medications", "metric_key": "gold_micro_f1_medications", "reported_value": 0.504},
        {"claim_id": "C13", "text": "Roundtrip mean Jaccard, symptoms", "metric_key": "roundtrip_jaccard_symptoms", "reported_value": 0.762},
        {"claim_id": "C14", "text": "Roundtrip mean Jaccard, diagnoses", "metric_key": "roundtrip_jaccard_diagnoses", "reported_value": 0.757},
        {"claim_id": "C15", "text": "Roundtrip mean Jaccard, medications", "metric_key": "roundtrip_jaccard_medications", "reported_value": 0.613}]},
    # real_mgh10 claims removed from the public release (restricted external pilot).
}
# Real-case reference for real_mimic5k comes from an independent manual audit (2026-11-22), rule-checked.
# The real_mgh10 reference is not distributed.
REF_REAL = {
    "real_mimic5k": {"case_id": "real_mimic5k", "derivation": "manual audit 2026-11-22 (AUDIT_REPORT.md), rule-checked",
                     "unverifiable_claims": [], "issues": [
        {"code": "CLAIM_NOT_REPRODUCED", "target": "C10"}, {"code": "CLAIM_NOT_REPRODUCED", "target": "C12"},
        {"code": "MIXED_DENOMINATORS", "target": ""}, {"code": "EVIDENCE_POSTHOC", "target": ""},
        {"code": "EVIDENCE_CAP_LIMITS_COVERAGE", "target": ""}, {"code": "OFFSETS_NOT_IN_SOURCE", "target": ""},
        {"code": "TAUTOLOGICAL_METRIC", "target": "span_valid_rate"}, {"code": "TAUTOLOGICAL_METRIC", "target": "substring_support_rate"},
        {"code": "ZERO_FILLED_DENOMINATOR", "target": "med_section_alignment_rate"},
        {"code": "HETEROGENEOUS_RUN_SETTINGS", "target": ""}, {"code": "MISSING_RUN_METADATA", "target": ""},
        {"code": "FAILURES_CLUSTERED", "target": ""}]},
}


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    (CFG / "claims").mkdir(parents=True, exist_ok=True)
    for k, v in CLAIMS_REAL.items():
        (CFG / "claims" / f"{k}.json").write_text(json.dumps(v, indent=1))
    reg_path = CFG / "cases.json"
    reg_path.write_text(json.dumps(REAL, indent=1))
    spec_path = FIX / "fixture_specs.json"
    spec_path.write_text(json.dumps(DEV_SPECS + HELD_SPECS, indent=1))
    subprocess.run([sys.executable, str(FIX / "make_fixtures.py"), "--out", str(CASES_DIR), "--refs", str(REFS),
                    "--registry", str(reg_path), "--spec", str(spec_path)], check=True)
    REFS.mkdir(parents=True, exist_ok=True)
    for k, v in REF_REAL.items():
        (REFS / f"{k}.json").write_text(json.dumps(v, indent=1))

    def task(tid, cid, scope, split):
        return {"task_id": tid, "case_id": cid, "scope": scope, "split": split, "instruction": INSTR[scope].format(cid=cid)}

    dev = [task("D01", "real_mimic5k", "evidence", "dev"), task("D02", "real_mimic5k", "med_alignment", "dev"),
           task("D03", "real_mimic5k", "claims", "dev"), task("D04", "real_mimic5k", "full", "dev"),
           task("D05", "dev_syn_a", "full", "dev"), task("D06", "dev_syn_b", "full", "dev")]
    held = [task(f"T{i + 1:02d}", s["case_id"], "full", "heldout") for i, s in enumerate(HELD_SPECS)]
    held += [task(f"T{len(held) + j + 1:02d}", c, sc, "heldout") for j, (c, sc) in enumerate(SCOPED_HELD)]
    held.append(task(f"T{len(held) + 1:02d}", "real_mgh10", "full", "heldout"))  # restricted; not runnable publicly
    for name, ts in (("tasks_dev.json", dev), ("tasks_heldout.json", held)):
        (TASKS / name).write_text(json.dumps({"version": "1.0.0", "tasks": ts}, indent=1))
    manifest = {p.name: sha_file(p) for p in [TASKS / "tasks_dev.json", TASKS / "tasks_heldout.json", spec_path, reg_path]}
    manifest.update({f"ref:{p.name}": sha_file(p) for p in sorted(REFS.glob("*.json"))})
    (TASKS / "BUILD_MANIFEST.json").write_text(json.dumps(manifest, indent=1))
    print(f"dev={len(dev)} heldout={len(held)} cases={len(json.loads(reg_path.read_text()))}")


if __name__ == "__main__":
    main()
