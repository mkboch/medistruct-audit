from pathlib import Path
import json
import pandas as pd
import numpy as np

RUN5K = Path("Outputs/discharge_extract_v2_run5k_FINAL")
RDIR  = RUN5K / "receipts"

GOLD_SUMTAB   = Path("Outputs/gold50_pack/gold50_summary_table.csv")
ROUNDTRIP_SUM = Path("Outputs/roundtrip_gold50/roundtrip_eval_summary.csv")

OUT_TEX = Path("table_results.tex")

def mean_safe(x):
    x = [v for v in x if v is not None]
    return float(np.mean(x)) if x else float("nan")

def main():
    # ---- 5k success + evidence audit ----
    total = 0
    both_ok = 0
    ev = {
        "span_valid_rate": [],
        "coverage": [],
        "substring_support_rate": [],
        "med_section_alignment_rate": [],
        "evidence_count": [],
        "present_symptoms": [],
        "present_diagnoses": [],
        "present_medications": [],
    }

    for p in RDIR.glob("*.json"):
        total += 1
        r = json.loads(p.read_text())
        ok = bool(r.get("parse", {}).get("json_ok")) and bool(r.get("schema_validation", {}).get("is_valid"))
        if not ok:
            continue
        both_ok += 1
        m = r.get("evidence_metrics", {}) or {}
        for k in ["span_valid_rate","coverage","substring_support_rate","med_section_alignment_rate","evidence_count"]:
            if k in m:
                ev[k].append(float(m[k]))
        pr = m.get("present_rates", {}) or {}
        # these keys exist in your updated receipts
        ev["present_symptoms"].append(float(pr.get("symptoms", 0.0)))
        ev["present_diagnoses"].append(float(pr.get("diagnoses", 0.0)))
        ev["present_medications"].append(float(pr.get("medications", 0.0)))

    success_rate = both_ok / total if total else 0.0

    # ---- gold50 baseline summary ----
    sumtab = pd.read_csv(GOLD_SUMTAB)
    ours = sumtab[sumtab["system"]=="ours"].iloc[0]
    base = sumtab.sort_values("medications_microF1", ascending=False)
    # keep the best non-ours baseline row (by medications_microF1)
    best_baseline = base[base["system"]!="ours"].iloc[0]

    # ---- roundtrip ----
    rt = pd.read_csv(ROUNDTRIP_SUM)
    rt_mean = rt[["symptoms_jaccard","diagnoses_jaccard","medications_jaccard"]].mean()

    # ---- emit a compact latex table ----
    lines = []
    lines.append(r"\begin{tabular}{@{}p{3.4cm}p{3.7cm}@{}}")
    lines.append(r"\toprule")
    lines.append(r"\textbf{Metric} & \textbf{Value} \\")
    lines.append(r"\midrule")
    lines.append(rf"5k schema-valid rate & {both_ok}/{total} ({success_rate*100:.2f}\%) \\")
    lines.append(rf"Evidence span validity (mean) & {mean_safe(ev['span_valid_rate']):.4f} \\")
    lines.append(rf"Evidence coverage (mean) & {mean_safe(ev['coverage']):.4f} \\")
    lines.append(rf"Substring support rate (mean) & {mean_safe(ev['substring_support_rate']):.4f} \\")
    lines.append(rf"Medication section alignment (mean) & {mean_safe(ev['med_section_alignment_rate']):.4f} \\")
    lines.append(rf"Mean evidence count & {mean_safe(ev['evidence_count']):.2f} \\")
    lines.append(rf"Evidence present rate (symptoms/diagnoses/meds) & "
                 rf"{mean_safe(ev['present_symptoms']):.3f} / {mean_safe(ev['present_diagnoses']):.3f} / {mean_safe(ev['present_medications']):.3f} \\")
    lines.append(r"\midrule")
    lines.append(rf"Gold50 (ours) micro-F1 (sym/dx/meds) & "
                 rf"{float(ours['symptoms_microF1']):.3f} / {float(ours['diagnoses_microF1']):.3f} / {float(ours['medications_microF1']):.3f} \\")
    lines.append(rf"Gold50 (best baseline: {best_baseline['system']}) micro-F1 (sym/dx/meds) & "
                 rf"{float(best_baseline['symptoms_microF1']):.3f} / {float(best_baseline['diagnoses_microF1']):.3f} / {float(best_baseline['medications_microF1']):.3f} \\")
    lines.append(r"\midrule")
    lines.append(rf"Roundtrip mean Jaccard (sym/dx/meds) & "
                 rf"{float(rt_mean['symptoms_jaccard']):.3f} / {float(rt_mean['diagnoses_jaccard']):.3f} / {float(rt_mean['medications_jaccard']):.3f} \\")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")

    OUT_TEX.write_text("\n".join(lines), encoding="utf-8")
    print("Wrote", OUT_TEX)

if __name__ == "__main__":
    main()