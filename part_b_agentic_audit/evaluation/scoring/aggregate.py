"""Score all runs of an experiment and aggregate per condition.

Usage (re-score saved run directories):
  python evaluation/scoring/aggregate.py --exp EXP_ID --tasks evaluation/tasks/tasks_heldout.json
Usage (aggregate an existing run-level score file, e.g. the released 28-task scores):
  python evaluation/scoring/aggregate.py --scores-csv manuscript/corrected_28_tasks/scores_runs.csv \
      --tasks evaluation/tasks/tasks_heldout.json --exclude-task T29 --out-dir manuscript/corrected_28_tasks

Outputs (in --out-dir, default runs/EXP_ID): scores_runs.csv, scores_summary.json, scores_summary.md

Scoring rules (v2, 2026-11-23):
  * Task F1 for a run on a task WITH reference findings = 2TP / (2TP + FP + FN). It is 0 when no reference
    finding is detected, including runs with no or a malformed report. (v1 computed F1 from precision and
    recall, left it undefined when nothing was asserted, and silently dropped those runs.)
  * Task F1 for a run on a CLEAN task (no reference findings) = 1 if a valid report was submitted and no
    in-scope finding was asserted, else 0.
  * Precision is undefined (None) when nothing in scope is asserted and is averaged only over runs where it
    is defined; the number of contributing tasks is reported.
  * Trials are averaged within a task, then over tasks. 95% CIs: task-level bootstrap (2,000 resamples,
    seed 0). Paired differences use paired task resampling over the identical task set; the script fails
    if any eligible task is missing for any condition, so tasks cannot silently drop out.
  * Per-report quantities (numbers stated, non-verbatim numbers, citations) are means over SUBMITTED reports.
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
SCORING_VERSION = "2.0.0"
CSV_KEYS = ["condition", "task_id", "case_id", "scope", "trial", "clean_task", "report_submitted", "report_malformed",
            "tp", "fp", "fn", "precision", "recall", "f1", "exact_match", "no_issue_correct", "n_tool_calls",
            "n_distinct_tools", "n_repeated_calls", "n_tool_errors", "n_argument_errors", "n_submit_attempts",
            "budget_exhausted", "n_numbers", "unsupported_numbers", "uncited_confirmed", "bad_citations",
            "false_refutation_unverifiable", "duration_s", "total_tokens", "pred", "reference"]
PER_REPORT = {"n_numbers", "unsupported_numbers", "uncited_confirmed", "bad_citations"}


class IntegrityError(RuntimeError):
    """Raised when eligible evaluation records are missing."""


def task_f1(tp: int, fp: int, fn: int, clean: bool, report_submitted: bool) -> float:
    """Run-level task F1 (scoring v2)."""
    if clean:
        return 1.0 if (report_submitted and fp == 0) else 0.0
    denom = 2 * tp + fp + fn
    return 2 * tp / denom if denom else 0.0


def boot(vals, n=2000, seed=0):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None, None, None
    rng = random.Random(seed)
    ms = sorted(statistics.fmean(rng.choices(vals, k=len(vals))) for _ in range(n))
    return statistics.fmean(vals), ms[int(0.025 * n)], ms[int(0.975 * n) - 1]


def claims_map(case_id: str) -> dict:
    reg = json.loads((A / "configs" / "cases.json").read_text())
    spec = reg[case_id]
    p = Path(spec["claims_file"])
    p = p if p.is_absolute() else Path(spec["base"]) / p
    return {c["claim_id"]: c["metric_key"] for c in json.loads(p.read_text())["claims"]}


def rows_from_runs(exp: Path, tasks: dict) -> list[dict]:
    rows = []
    for cond_dir in sorted(p for p in exp.iterdir() if p.is_dir() and p.name in ("A", "B", "C", "D")):
        for tdir in sorted(p for p in cond_dir.iterdir() if p.is_dir() and p.name in tasks):
            t = tasks[tdir.name]
            ref = json.loads((REFS / f"{t['case_id']}.json").read_text())
            cm = claims_map(t["case_id"])
            for trial in sorted(tdir.glob("trial*")):
                s = score_run(trial, t, ref, cm)
                s.update(condition=cond_dir.name, trial=trial.name)
                rows.append(s)
    return rows


def _parse(v: str):
    if v in ("", "None"):
        return None
    if v in ("True", "False"):
        return v == "True"
    if v.startswith("["):
        return json.loads(v)
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def rows_from_csv(path: Path) -> list[dict]:
    rows = []
    for r in csv.DictReader(open(path, newline="")):
        rows.append({k: (_parse(v) if k not in ("condition", "task_id", "case_id", "scope", "trial") else v)
                     for k, v in r.items()})
    return rows


def finalize(rows: list[dict]) -> None:
    for r in rows:
        r.setdefault("report_malformed", False)
        r["clean_task"] = not r["reference"]
        r["f1"] = task_f1(r["tp"], r["fp"], r["fn"], r["clean_task"], bool(r["report_submitted"]))


def check_integrity(rows, eligible: set[str]) -> dict:
    """Every eligible task must appear in every condition; report incomplete trial sets."""
    conds = sorted({r["condition"] for r in rows})
    seen = defaultdict(lambda: defaultdict(int))
    for r in rows:
        seen[r["condition"]][r["task_id"]] += 1
    missing = {c: sorted(eligible - set(seen[c])) for c in conds}
    missing = {c: v for c, v in missing.items() if v}
    if missing:
        raise IntegrityError(f"eligible tasks without any run record: {missing}")
    trials = {c: dict(seen[c]) for c in conds}
    expected = {c: max(trials[c].values()) for c in conds}
    incomplete = {c: {t: n for t, n in trials[c].items() if n < expected[c]} for c in conds}
    return {"conditions": conds, "n_tasks": len(eligible), "trials_expected": expected,
            "tasks_with_incomplete_trials": {c: v for c, v in incomplete.items() if v},
            "malformed_reports": sum(bool(r.get("report_malformed")) for r in rows),
            "missing_reports": sum(not r["report_submitted"] for r in rows)}


def summarize(rows, label: str, integrity: dict) -> dict:
    def per_task(cond, field):
        d = defaultdict(list)
        for r in rows:
            if r["condition"] == cond and r.get(field) is not None:
                d[r["task_id"]].append(float(r[field]))
        return {k: statistics.fmean(v) for k, v in d.items()}

    conds = integrity["conditions"]
    metrics = ["f1", "precision", "recall", "exact_match", "report_submitted", "n_tool_calls", "n_numbers",
               "unsupported_numbers", "uncited_confirmed", "bad_citations", "n_argument_errors",
               "false_refutation_unverifiable", "duration_s", "total_tokens"]
    S = {"label": label, "scoring_version": SCORING_VERSION, "n_runs": len(rows), "integrity": integrity,
         "conditions": {}, "notes": {"per_report_metrics": sorted(PER_REPORT),
                                     "precision": "averaged over runs with at least one in-scope assertion"}}
    for c in conds:
        cr = [r for r in rows if r["condition"] == c]
        tp, fp, fn = (sum(r[k] for r in cr) for k in ("tp", "fp", "fn"))
        res = {"n_runs": len(cr), "n_tasks": len({r["task_id"] for r in cr}),
               "n_reports_submitted": sum(bool(r["report_submitted"]) for r in cr),
               "pooled_tp": tp, "pooled_fp": fp, "pooled_fn": fn,
               "micro_precision": tp / (tp + fp) if tp + fp else None,
               "micro_recall": tp / (tp + fn) if tp + fn else None,
               "micro_f1": 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else None}
        for m in metrics:
            pt = per_task(c, m)
            res[m] = boot(list(pt.values()))
            res[f"{m}_n_tasks"] = len(pt)
        # clean tasks: a missing report counts as incorrect
        d = defaultdict(list)
        for r in cr:
            if r["clean_task"]:
                d[r["task_id"]].append(1.0 if (r["report_submitted"] and r.get("no_issue_correct")) else 0.0)
        res["clean_tasks_correct_no_issue"] = boot([statistics.fmean(v) for v in d.values()]) if d else None
        by_task = defaultdict(list)
        for r in cr:
            by_task[r["task_id"]].append(frozenset(map(tuple, r["pred"] or [])))
        multi = {k: v for k, v in by_task.items() if len(v) > 1}
        if multi:
            res["trials_identical_rate"] = statistics.fmean(1.0 if len(set(v)) == 1 else 0.0 for v in multi.values())
            res["trials_mean_pairwise_jaccard"] = statistics.fmean(
                statistics.fmean((len(x & y) / len(x | y) if x | y else 1.0) for x, y in itertools.combinations(v, 2))
                for v in multi.values())
        S["conditions"][c] = res
    if "A" in conds:
        for c in conds:
            if c == "A":
                continue
            diffs = {}
            for m in ("f1", "exact_match", "n_tool_calls"):
                ta, tc = per_task("A", m), per_task(c, m)
                if set(ta) != set(tc):
                    raise IntegrityError(f"paired comparison {c}-A on {m}: task sets differ "
                                         f"({sorted(set(ta) ^ set(tc))})")
                diffs[m] = boot([tc[k] - ta[k] for k in sorted(ta)])
                diffs[f"{m}_n_tasks"] = len(ta)
            S["conditions"][c]["paired_diff_vs_A"] = diffs
    S["by_scope"] = {}
    for c in conds:
        for sc in sorted({r["scope"] for r in rows}):
            v = [r for r in rows if r["condition"] == c and r["scope"] == sc]
            if v:
                S["by_scope"][f"{c}|{sc}"] = {"n_runs": len(v), "n_tasks": len({r["task_id"] for r in v}),
                                             "exact_match": statistics.fmean(bool(r["exact_match"]) for r in v),
                                             "mean_tool_calls": statistics.fmean(r["n_tool_calls"] for r in v)}
    return S


def write_outputs(rows, S, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "scores_runs.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_KEYS, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["condition"], r["task_id"], r["trial"])):
            w.writerow({k: (json.dumps(r.get(k)) if isinstance(r.get(k), list) else r.get(k)) for k in CSV_KEYS})
    (out / "scores_summary.json").write_text(json.dumps(S, indent=2, default=str))

    def fmt(t):
        return "n/a" if (t is None or t[0] is None) else f"{t[0]:.3f} [{t[1]:.3f}, {t[2]:.3f}]"
    conds = S["integrity"]["conditions"]
    lines = [f"Scoring v{S['scoring_version']} | {S['label']} | tasks: {S['integrity']['n_tasks']}", "",
             "| Metric | " + " | ".join(conds) + " |", "|---|" + "---|" * len(conds)]
    for m in ("f1", "exact_match", "recall", "precision", "n_tool_calls"):
        lines.append(f"| {m} | " + " | ".join(fmt(S["conditions"][c][m]) for c in conds) + " |")
    lines.append("| clean_tasks_correct_no_issue | " + " | ".join(fmt(S["conditions"][c]["clean_tasks_correct_no_issue"]) for c in conds) + " |")
    for m in ("micro_precision", "micro_recall", "micro_f1"):
        lines.append(f"| {m} | " + " | ".join(f"{S['conditions'][c][m]:.3f}" if S["conditions"][c][m] is not None else "n/a" for c in conds) + " |")
    for c in conds:
        if "paired_diff_vs_A" in S["conditions"][c]:
            d = S["conditions"][c]["paired_diff_vs_A"]
            lines.append(f"| paired {c}-A: F1 / exact | {fmt(d['f1'])} / {fmt(d['exact_match'])} (n={d['f1_n_tasks']}) |")
    (out / "scores_summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main(argv=None):
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--exp", help="experiment id under runs/ (re-scores run directories)")
    src.add_argument("--scores-csv", help="aggregate an existing run-level scores CSV")
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--exclude-task", action="append", default=[], help="task id to exclude (repeatable)")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args(argv)
    tasks = {t["task_id"]: t for t in json.loads(Path(a.tasks).read_text())["tasks"]}
    unknown = set(a.exclude_task) - set(tasks)
    if unknown:
        raise SystemExit(f"--exclude-task: unknown task ids {sorted(unknown)}")
    eligible = set(tasks) - set(a.exclude_task)
    if a.exp:
        rows = rows_from_runs(A / "runs" / a.exp, tasks)
        out = Path(a.out_dir) if a.out_dir else A / "runs" / a.exp
        label = f"exp={a.exp}"
    else:
        rows = rows_from_csv(Path(a.scores_csv))
        out = Path(a.out_dir) if a.out_dir else Path(a.scores_csv).parent
        label = f"scores_csv={Path(a.scores_csv).name}"
    rows = [r for r in rows if r["task_id"] in eligible]
    finalize(rows)
    integrity = check_integrity(rows, eligible)
    integrity["excluded_tasks"] = sorted(a.exclude_task)
    S = summarize(rows, label + (f" | excluded={','.join(sorted(a.exclude_task))}" if a.exclude_task else ""), integrity)
    write_outputs(rows, S, out)
    return S


if __name__ == "__main__":
    main()
