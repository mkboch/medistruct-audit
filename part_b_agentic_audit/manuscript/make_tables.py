"""Generate LaTeX tables/macros for the manuscript from experiment outputs (no hand-typed numbers).

Usage: python manuscript/make_tables.py --exp EXP_heldout --dev EXP_dev_v2 --out manuscript/generated
Produces: agentic_results.tex (macros + main table), agentic_scope.tex, toolcalling.tex, real_case.tex
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

A = Path(__file__).resolve().parents[1]
COND = {"A": "Deterministic checklist (A)", "B": "OpenClaw agent, full tools (B)", "D": "OpenClaw agent, metadata only (D)"}


def ci(t, d=3):
    if not t or t[0] is None:
        return "n/a"
    return f"{t[0]:.{d}f} [{t[1]:.{d}f}, {t[2]:.{d}f}]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", required=True)
    ap.add_argument("--dev", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    S = json.loads((A / "runs" / a.exp / "scores_summary.json").read_text())
    C = S["conditions"]
    conds = [c for c in ("A", "B", "D") if c in C]
    rows = [("Task F1", "f1", 3), ("Precision", "precision", 3), ("Recall", "recall", 3), ("Exact match", "exact_match", 3),
            ("Report submitted", "report_submitted", 3), ("Tool calls per task", "n_tool_calls", 1),
            ("Numbers stated per report", "n_numbers", 1), ("Unsupported numbers per report", "unsupported_numbers", 2),
            ("Invalid citations per report", "bad_citations", 2), ("Wall time per task (s)", "duration_s", 1),
            ("Tokens per task (thousands)", "total_tokens", 1)]
    L = [r"\begin{tabular}{@{}l" + "c" * len(conds) + r"@{}}", r"\toprule",
         "Metric (mean over tasks [95\\% CI]) & " + " & ".join(c for c in conds) + r" \\", r"\midrule"]
    for lab, k, d in rows:
        vals = []
        for c in conds:
            t = C[c].get(k)
            if k == "total_tokens" and t and t[0] is not None:
                t = [x / 1000 for x in t]
            vals.append(ci(t, d))
        L.append(f"{lab} & " + " & ".join(vals) + r" \\")
    L.append(r"\midrule")
    for lab, k in (("Micro-F1 (pooled findings)", "micro_f1"), ("Clean tasks: correctly reported no issue", "clean_tasks_correct_no_issue"),
                   ("Identical finding sets across 3 trials", "trials_identical_rate"),
                   ("Mean pairwise Jaccard across trials", "trials_mean_pairwise_jaccard")):
        vals = []
        for c in conds:
            v = C[c].get(k)
            vals.append(ci(v) if isinstance(v, list) else (f"{v:.3f}" if isinstance(v, (int, float)) else "n/a"))
        L.append(f"{lab} & " + " & ".join(vals) + r" \\")
    L += [r"\bottomrule", r"\end{tabular}"]
    (out / "agentic_results.tex").write_text("\n".join(L) + "\n")
    # paired differences
    P = []
    for c in conds:
        if c == "A" or "paired_diff_vs_A" not in C[c]:
            continue
        d = C[c]["paired_diff_vs_A"]
        P.append(f"{COND[c]} $-$ A & {ci(d['f1'])} & {ci(d['exact_match'])} & {ci(d['n_tool_calls'], 1)} \\\\")
    (out / "agentic_paired.tex").write_text("\n".join([r"\begin{tabular}{@{}lccc@{}}", r"\toprule",
                                                        r"Difference & $\Delta$ task F1 & $\Delta$ exact match & $\Delta$ tool calls \\", r"\midrule"]
                                                       + P + [r"\bottomrule", r"\end{tabular}"]) + "\n")
    # by scope
    sc = defaultdict(dict)
    for k, v in S["by_scope"].items():
        c, s = k.split("|")
        sc[s][c] = v
    Ls = [r"\begin{tabular}{@{}lc" + "cc" * len(conds) + r"@{}}", r"\toprule",
          r"Scope & $n$ tasks & " + " & ".join(f"\\multicolumn{{2}}{{c}}{{{c}: exact / calls}}" for c in conds) + r" \\", r"\midrule"]
    tasks = json.loads((A / "evaluation/tasks/tasks_heldout.json").read_text())["tasks"]
    nt = defaultdict(int)
    for t in tasks:
        nt[t["scope"]] += 1
    for s in ("full", "claims", "evidence", "med_alignment", "provenance"):
        if s in sc:
            Ls.append(f"{s.replace('_', ' ')} & {nt[s]} & " + " & ".join(
                f"{sc[s][c]['exact_match']:.2f} & {sc[s][c]['mean_tool_calls']:.1f}" if c in sc[s] else "-- & --" for c in conds) + r" \\")
    Ls += [r"\bottomrule", r"\end{tabular}"]
    (out / "agentic_scope.tex").write_text("\n".join(Ls) + "\n")
    # per-code recall for B and A
    runs = list(csv.DictReader(open(A / "runs" / a.exp / "scores_runs.csv")))
    hit, tot = defaultdict(lambda: defaultdict(int)), defaultdict(lambda: defaultdict(int))
    fp = defaultdict(lambda: defaultdict(int))
    for r in runs:
        ref = {tuple(x) for x in json.loads(r["reference"])}
        pred = {tuple(x) for x in json.loads(r["pred"])}
        for code, t in ref:
            tot[r["condition"]][code] += 1
            hit[r["condition"]][code] += (code, t) in pred
        for code, t in pred - ref:
            fp[r["condition"]][code] += 1
    codes = sorted({c for cc in tot.values() for c in cc} | {c for cc in fp.values() for c in cc})
    Lc = [r"\begin{tabular}{@{}l" + "cc" * len(conds) + r"@{}}", r"\toprule",
          "Finding code & " + " & ".join(f"\\multicolumn{{2}}{{c}}{{{c}: recall / FP}}" for c in conds) + r" \\", r"\midrule"]
    for code in codes:
        cells = []
        for c in conds:
            n = tot[c][code]
            cells.append(f"{hit[c][code]}/{n} & {fp[c][code]}" if n or fp[c][code] else "-- & 0")
        Lc.append(r"\texttt{" + code.replace("_", r"\_") + "} & " + " & ".join(cells) + r" \\")
    Lc += [r"\bottomrule", r"\end{tabular}"]
    (out / "agentic_codes.tex").write_text("\n".join(Lc) + "\n")
    # tool-calling
    T = json.loads((A / "runs/model_selection/toolcalling_qwen35_122b.json").read_text())["results"]
    names = {"chat": "Ordinary chat", "required_tool_call": "Required tool call", "auto_tool_selection_and_args": "Automatic selection + typed arguments",
             "no_tool_when_unnecessary": "No tool when unnecessary", "multi_step_sequence": "Multi-step sequence + final answer",
             "tool_error_no_fabrication": "Tool error, no fabricated value", "streaming_tool_call": "Streaming tool call",
             "client_timeout_handling": "Client timeout handled"}
    Lt = [r"\begin{tabular}{@{}lc@{}}", r"\toprule", r"Probe & Passed (of 5) \\", r"\midrule"]
    for k, lab in names.items():
        Lt.append(f"{lab} & {round(T[k]['pass_rate'] * 5)} \\\\")
    Lt += [r"\bottomrule", r"\end{tabular}"]
    (out / "toolcalling.tex").write_text("\n".join(Lt) + "\n")
    print("wrote", sorted(p.name for p in out.iterdir()))


if __name__ == "__main__":
    main()
