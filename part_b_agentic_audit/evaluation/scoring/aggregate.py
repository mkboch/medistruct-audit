"""Score all runs of an experiment and aggregate per condition.

Usage: python evaluation/scoring/aggregate.py --exp EXP_ID --tasks evaluation/tasks/tasks_heldout.json
Writes runs/EXP_ID/scores_runs.csv, scores_summary.json, scores_summary.md
Statistics: trials averaged within task; 95% CIs by task-level bootstrap (2000 resamples, seed 0);
paired condition differences by paired task bootstrap.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

A = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from score import score_run  # noqa: E402

REFS = A / "evaluation" / "fixtures" / "references"


def claims_map(case_id: str) -> dict:
    reg = json.loads((A / "configs" / "cases.json").read_text())
    spec = reg[case_id]
    p = Path(spec["claims_file"])
    p = p if p.is_absolute() else Path(spec["base"]) / p
    return {c["claim_id"]: c["metric_key"] for c in json.loads(p.read_text())["claims"]}


def f1(p, r):
    if p is None or r is None:
        return None
    return 2 * p * r / (p + r) if p + r else 0.0


def boot(vals, n=2000, seed=0):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None, None, None
    rng = random.Random(seed)
    ms = sorted(statistics.fmean(rng.choices(vals, k=len(vals))) for _ in range(n))
    return statistics.fmean(vals), ms[int(0.025 * n)], ms[int(0.975 * n) - 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", required=True)
    ap.add_argument("--tasks", required=True)
    a = ap.parse_args()
    tasks = {t["task_id"]: t for t in json.loads(Path(a.tasks).read_text())["tasks"]}
    exp = A / "runs" / a.exp
    rows = []
    for cond_dir in sorted(p for p in exp.iterdir() if p.is_dir()):
        for tdir in sorted(p for p in cond_dir.iterdir() if p.is_dir() and p.name in tasks):
            t = tasks[tdir.name]
            ref = json.loads((REFS / f"{t['case_id']}.json").read_text())
            cm = claims_map(t["case_id"])
            for trial in sorted(tdir.glob("trial*")):
                s = score_run(trial, t, ref, cm)
                s.update(condition=cond_dir.name, trial=trial.name, f1=f1(s["precision"], s["recall"]))
                s["clean_task"] = not s["reference"]
                if s["clean_task"]:  # no reference issues: success iff nothing asserted in scope
                    s["f1"] = 1.0 if (s["fp"] == 0 and s["report_submitted"]) else 0.0
                rows.append(s)
    keys = ["condition", "task_id", "case_id", "scope", "trial", "clean_task", "report_submitted", "tp", "fp", "fn",
            "precision", "recall", "f1", "exact_match", "no_issue_correct", "n_tool_calls", "n_distinct_tools",
            "n_repeated_calls", "n_tool_errors", "n_argument_errors", "n_submit_attempts", "budget_exhausted",
            "n_numbers", "unsupported_numbers", "uncited_confirmed", "bad_citations", "false_refutation_unverifiable",
            "duration_s", "total_tokens", "pred", "reference"]
    with open(exp / "scores_runs.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(r[k]) if isinstance(r.get(k), list) else r.get(k)) for k in keys})

    def per_task(cond, field, fn=lambda x: x):
        d = defaultdict(list)
        for r in rows:
            if r["condition"] == cond and r.get(field) is not None:
                d[r["task_id"]].append(float(fn(r[field])))
        return {k: statistics.fmean(v) for k, v in d.items()}

    conds = sorted({r["condition"] for r in rows})
    summary = {"experiment": a.exp, "n_runs": len(rows), "conditions": {}}
    metrics = [("f1", "F1 (task)"), ("precision", "Precision"), ("recall", "Recall"), ("exact_match", "Exact match"),
               ("report_submitted", "Report submitted"), ("n_tool_calls", "Tool calls"), ("unsupported_numbers", "Unsupported numbers"),
               ("n_numbers", "Numbers stated"), ("uncited_confirmed", "Uncited confirmed findings"), ("bad_citations", "Invalid citations"),
               ("n_argument_errors", "Argument errors"), ("false_refutation_unverifiable", "False refutation of unverifiable claim"),
               ("duration_s", "Duration (s)"), ("total_tokens", "Tokens")]
    for c in conds:
        cr = [r for r in rows if r["condition"] == c]
        tp, fp, fn_ = sum(r["tp"] for r in cr), sum(r["fp"] for r in cr), sum(r["fn"] for r in cr)
        res = {"n_runs": len(cr), "n_tasks": len({r["task_id"] for r in cr}),
               "micro_precision": tp / (tp + fp) if tp + fp else None, "micro_recall": tp / (tp + fn_) if tp + fn_ else None}
        res["micro_f1"] = f1(res["micro_precision"], res["micro_recall"])
        for m, _ in metrics:
            res[m] = boot(list(per_task(c, m).values()))
        clean = [r for r in cr if r["clean_task"]]
        res["clean_tasks_correct_no_issue"] = boot(list(({r["task_id"]: 0.0 for r in clean} | per_task(c, "no_issue_correct")).values())) if clean else None
        # cross-trial reproducibility of predicted finding sets
        by_task = defaultdict(list)
        for r in cr:
            by_task[r["task_id"]].append(frozenset(map(tuple, r["pred"])))
        multi = {k: v for k, v in by_task.items() if len(v) > 1}
        if multi:
            ident = statistics.fmean(1.0 if len(set(v)) == 1 else 0.0 for v in multi.values())
            jac = statistics.fmean(statistics.fmean((len(x & y) / len(x | y) if x | y else 1.0) for x, y in itertools.combinations(v, 2))
                                   for v in multi.values())
            res["trials_identical_rate"], res["trials_mean_pairwise_jaccard"] = ident, jac
        summary["conditions"][c] = res
    # paired differences vs deterministic baseline A
    if "A" in conds:
        for c in conds:
            if c == "A":
                continue
            diffs = {}
            for m in ("f1", "exact_match", "n_tool_calls"):
                ta, tc = per_task("A", m), per_task(c, m)
                common = sorted(set(ta) & set(tc))
                diffs[m] = boot([tc[k] - ta[k] for k in common])
            summary["conditions"][c]["paired_diff_vs_A"] = diffs
    # by scope
    summary["by_scope"] = {}
    for c in conds:
        for sc in sorted({r["scope"] for r in rows}):
            v = [r for r in rows if r["condition"] == c and r["scope"] == sc]
            if v:
                summary["by_scope"][f"{c}|{sc}"] = {"n_runs": len(v), "exact_match": statistics.fmean(bool(r["exact_match"]) for r in v),
                                                   "mean_tool_calls": statistics.fmean(r["n_tool_calls"] for r in v)}
    (exp / "scores_summary.json").write_text(json.dumps(summary, indent=2, default=str))

    def fmt(t):
        if t is None or t[0] is None:
            return "n/a"
        return f"{t[0]:.3f} [{t[1]:.3f}, {t[2]:.3f}]"
    lines = ["| Metric | " + " | ".join(conds) + " |", "|---|" + "---|" * len(conds)]
    for m, lab in metrics:
        lines.append(f"| {lab} | " + " | ".join(fmt(summary["conditions"][c].get(m)) for c in conds) + " |")
    for m in ("micro_precision", "micro_recall", "micro_f1", "trials_identical_rate", "trials_mean_pairwise_jaccard"):
        lines.append(f"| {m} | " + " | ".join(f"{summary['conditions'][c][m]:.3f}" if summary["conditions"][c].get(m) is not None else "n/a" for c in conds) + " |")
    (exp / "scores_summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
