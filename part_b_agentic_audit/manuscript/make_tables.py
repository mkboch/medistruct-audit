"""Generate the manuscript's agentic-audit LaTeX tables from one analysis directory (no hand-typed numbers).

Usage:
  python manuscript/make_tables.py --in-dir manuscript/corrected_28_tasks --out manuscript/corrected_28_tasks/tables
The input directory must contain scores_summary.json and scores_runs.csv written by
evaluation/scoring/aggregate.py (scoring v2). Legacy usage --exp EXP_ID reads runs/EXP_ID/.
Produces: agentic_results.tex, agentic_paired.tex, agentic_scope.tex, agentic_codes.tex, toolcalling.tex
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

A = Path(__file__).resolve().parents[1]
NAME = {"A": "Deterministic checklist", "B": "OpenClaw with audit tools", "D": "OpenClaw without audit tools"}
SHORT = {"A": "Checklist", "B": "With tools", "D": "Without tools"}


def ci(t, d=3):
    if not t or t[0] is None:
        return "n/a"
    return f"{t[0]:.{d}f} [{t[1]:.{d}f}, {t[2]:.{d}f}]"


def main(argv=None):
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--in-dir")
    src.add_argument("--exp")
    ap.add_argument("--dev", help="unused; kept for backward compatibility")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    ind = Path(a.in_dir) if a.in_dir else A / "runs" / a.exp
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    S = json.loads((ind / "scores_summary.json").read_text())
    runs = list(csv.DictReader(open(ind / "scores_runs.csv")))
    C = S["conditions"]
    conds = [c for c in ("A", "B", "D") if c in C]
    n_tasks = S.get("integrity", {}).get("n_tasks", len({r["task_id"] for r in runs}))
    hdr = " & ".join(SHORT[c] for c in conds)

    rows = [("Task F1$^{a}$", "f1", 3), ("Exact finding-set match", "exact_match", 3), ("Recall", "recall", 3),
            ("Precision$^{b}$", "precision", 3), ("Report submitted", "report_submitted", 3),
            ("Tool calls per task", "n_tool_calls", 1), ("Numbers stated per submitted report", "n_numbers", 1),
            ("Numbers not verbatim in tool output, per submitted report", "unsupported_numbers", 2),
            ("Invalid citations per submitted report", "bad_citations", 2), ("Wall time per task (s)", "duration_s", 1)]
    L = [r"\begin{tabular}{@{}l" + "c" * len(conds) + r"@{}}", r"\toprule",
         rf"Metric (mean over {n_tasks} tasks [95\% CI]) & {hdr} \\", r"\midrule"]
    for lab, k, d in rows:
        L.append(f"{lab} & " + " & ".join(ci(C[c].get(k), d) for c in conds) + r" \\")
    L.append(r"Clean tasks: correct ``no issue''$^{c}$ & " + " & ".join(ci(C[c].get("clean_tasks_correct_no_issue")) for c in conds) + r" \\")
    L.append(r"\midrule")
    L.append("Pooled micro-P / R / F1 & " + " & ".join(
        f"{C[c]['micro_precision']:.3f} / {C[c]['micro_recall']:.3f} / {C[c]['micro_f1']:.3f}" for c in conds) + r" \\")
    for lab, k in (("Identical finding sets across trials", "trials_identical_rate"),
                   ("Mean pairwise Jaccard across trials", "trials_mean_pairwise_jaccard")):
        L.append(f"{lab} & " + " & ".join(f"{C[c][k]:.3f}" if C[c].get(k) is not None else "n/a" for c in conds) + r" \\")
    L.append("Reports submitted / runs & " + " & ".join(f"{C[c]['n_reports_submitted']} / {C[c]['n_runs']}" for c in conds) + r" \\")
    L += [r"\bottomrule", r"\end{tabular}"]
    prec_n = ", ".join(f"{SHORT[c].lower()} {C[c].get('precision_n_tasks', 'n/a')}" for c in conds)
    L.append(r"\par\smallskip\footnotesize $^{a}$Task F1 $=2TP/(2TP+FP+FN)$ per run (0 when no reference finding is detected, "
             r"including missing reports); clean tasks score 1 only for a submitted report asserting no in-scope finding. "
             rf"$^{{b}}$Undefined for runs asserting no in-scope finding; tasks contributing: {prec_n}. "
             r"$^{c}$Missing reports count as incorrect.")
    (out / "agentic_results.tex").write_text("\n".join(L) + "\n")

    P = [r"\begin{tabular}{@{}lccc@{}}", r"\toprule",
         rf"Difference ({n_tasks} paired tasks) & $\Delta$ task F1 & $\Delta$ exact match & $\Delta$ tool calls \\", r"\midrule"]
    for c in conds:
        if c == "A" or "paired_diff_vs_A" not in C[c]:
            continue
        d = C[c]["paired_diff_vs_A"]
        P.append(f"{NAME[c]} $-$ checklist & {ci(d['f1'])} & {ci(d['exact_match'])} & {ci(d['n_tool_calls'], 1)} \\\\")
    P += [r"\bottomrule", r"\end{tabular}"]
    (out / "agentic_paired.tex").write_text("\n".join(P) + "\n")

    sc = defaultdict(dict)
    for k, v in S["by_scope"].items():
        c, s = k.split("|")
        sc[s][c] = v
    nt = defaultdict(set)
    for r in runs:
        nt[r["scope"]].add(r["task_id"])
    Ls = [r"\begin{tabular}{@{}lc" + "cc" * len(conds) + r"@{}}", r"\toprule",
          r"Scope & Tasks & " + " & ".join(f"\\multicolumn{{2}}{{c}}{{{SHORT[c]}: exact / calls}}" for c in conds) + r" \\", r"\midrule"]
    for s in ("full", "claims", "evidence", "med_alignment", "provenance"):
        if s in sc:
            Ls.append(f"{s.replace('_', ' ')} & {len(nt[s])} & " + " & ".join(
                f"{sc[s][c]['exact_match']:.2f} & {sc[s][c]['mean_tool_calls']:.1f}" if c in sc[s] else "-- & --" for c in conds) + r" \\")
    Ls += [r"\bottomrule", r"\end{tabular}"]
    (out / "agentic_scope.tex").write_text("\n".join(Ls) + "\n")

    hit, tot, fp = (defaultdict(lambda: defaultdict(int)) for _ in range(3))
    for r in runs:
        ref = {tuple(x) for x in json.loads(r["reference"])}
        pred = {tuple(x) for x in json.loads(r["pred"] or "[]")}
        for code, t in ref:
            tot[r["condition"]][code] += 1
            hit[r["condition"]][code] += (code, t) in pred
        for code, t in pred - ref:
            fp[r["condition"]][code] += 1
    codes = sorted({c for cc in tot.values() for c in cc} | {c for cc in fp.values() for c in cc})
    Lc = [r"\begin{tabular}{@{}l" + "cc" * len(conds) + r"@{}}", r"\toprule",
          "Finding code & " + " & ".join(f"\\multicolumn{{2}}{{c}}{{{SHORT[c]}: recall / FP}}" for c in conds) + r" \\", r"\midrule"]
    for code in codes:
        cells = [f"{hit[c][code]}/{tot[c][code]} & {fp[c][code]}" if tot[c][code] or fp[c][code] else "-- & 0" for c in conds]
        Lc.append(r"\texttt{" + code.replace("_", r"\_") + "} & " + " & ".join(cells) + r" \\")
    Lc += [r"\bottomrule", r"\end{tabular}"]
    (out / "agentic_codes.tex").write_text("\n".join(Lc) + "\n")

    T = json.loads((A / "runs/model_selection/toolcalling_qwen35_122b.json").read_text())["results"]
    names = {"chat": "Ordinary chat", "required_tool_call": "Required tool call", "auto_tool_selection_and_args": "Automatic selection + typed arguments",
             "no_tool_when_unnecessary": "No tool when unnecessary", "multi_step_sequence": "Multi-step sequence + final answer",
             "tool_error_no_fabrication": "Tool error, no fabricated value", "streaming_tool_call": "Streaming tool call",
             "client_timeout_handling": "Client timeout handled"}
    Lt = [r"\begin{tabular}{@{}lc@{}}", r"\toprule", r"Probe & Passed (of 5) \\", r"\midrule"]
    Lt += [f"{lab} & {round(T[k]['pass_rate'] * 5)} \\\\" for k, lab in names.items()]
    Lt += [r"\bottomrule", r"\end{tabular}"]
    (out / "toolcalling.tex").write_text("\n".join(Lt) + "\n")
    print("wrote", sorted(p.name for p in out.iterdir()))


if __name__ == "__main__":
    main()
