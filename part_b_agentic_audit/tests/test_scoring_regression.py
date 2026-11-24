"""Regression tests for scoring v2 (task-F1 definition, missing/malformed reports, aggregation integrity).
Run: python -m pytest -q tests/test_scoring_regression.py   (from part_b_agentic_audit/)"""
import csv
import json
import sys
from pathlib import Path

import pytest

A = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(A / "evaluation" / "scoring"))
import aggregate as ag  # noqa: E402
from score import score_run  # noqa: E402

REF2 = {"issues": [{"code": "EVIDENCE_POSTHOC", "target": ""}, {"code": "MIXED_DENOMINATORS", "target": ""}],
        "unverifiable_claims": []}
REF0 = {"issues": [], "unverifiable_claims": []}


def task(tid="T1"):
    return {"task_id": tid, "case_id": "synthetic", "scope": "full", "split": "heldout", "instruction": "Audit case."}


def run(tmp_path, findings=None, raw=None, name="r"):
    d = tmp_path / name
    d.mkdir()
    (d / "tool_calls.jsonl").write_text(json.dumps({"call_id": "c1", "tool": "validate_structured_outputs", "ok": True, "result": {}}) + "\n")
    if raw is not None:
        (d / "report.json").write_text(raw)
    elif findings is not None:
        (d / "report.json").write_text(json.dumps({"findings": findings, "summary": ""}))
    return d


def f(code, target=""):
    return {"code": code, "category": "confirmed_finding", "target": target, "statement": "s", "evidence_call_ids": ["c1"]}


def scored(tmp_path, ref, **kw):
    s = score_run(run(tmp_path, **kw), task(), ref, {})
    s["clean_task"] = not s["reference"]
    s["f1"] = ag.task_f1(s["tp"], s["fp"], s["fn"], s["clean_task"], s["report_submitted"])
    return s


# 1. non-empty reference, no predictions
def test_faults_but_no_predictions_scores_zero(tmp_path):
    s = scored(tmp_path, REF2, findings=[f("NO_ISSUES_FOUND")])
    assert (s["tp"], s["fp"], s["fn"]) == (0, 0, 2)
    assert s["f1"] == 0.0 and s["precision"] is None and s["recall"] == 0.0


# 2. non-empty reference, no submitted report
def test_faults_and_no_report_scores_zero(tmp_path):
    s = scored(tmp_path, REF2)
    assert s["report_submitted"] is False and s["f1"] == 0.0 and s["exact_match"] is False


# 3. empty reference, correctly empty report
def test_clean_task_correct_empty_report(tmp_path):
    s = scored(tmp_path, REF0, findings=[f("NO_ISSUES_FOUND")])
    assert s["f1"] == 1.0 and s["no_issue_correct"] is True
    assert s["precision"] is None            # not artificially set to 1


# 4. empty reference, erroneous finding
def test_clean_task_false_positive(tmp_path):
    s = scored(tmp_path, REF0, findings=[f("EVIDENCE_POSTHOC")])
    assert s["fp"] == 1 and s["f1"] == 0.0 and s["precision"] == 0.0


# 5. partial correct detection
def test_partial_detection(tmp_path):
    s = scored(tmp_path, REF2, findings=[f("EVIDENCE_POSTHOC")])
    assert (s["tp"], s["fp"], s["fn"]) == (1, 0, 1) and s["f1"] == pytest.approx(2 / 3)


# 6. correct detections plus false positives
def test_detection_with_false_positive(tmp_path):
    s = scored(tmp_path, REF2, findings=[f("EVIDENCE_POSTHOC"), f("MIXED_DENOMINATORS"), f("MALFORMED_RECEIPTS")])
    assert (s["tp"], s["fp"], s["fn"]) == (2, 1, 0) and s["f1"] == pytest.approx(4 / 5)


def row(cond, tid, trial, tp, fp, fn, ref=True, report=True, exact=None):
    reference = [["EVIDENCE_POSTHOC", ""]] if ref else []
    return {"condition": cond, "task_id": tid, "case_id": "c", "scope": "full", "trial": f"trial{trial}",
            "report_submitted": report, "report_malformed": False, "tp": tp, "fp": fp, "fn": fn,
            "precision": (tp / (tp + fp)) if tp + fp else None, "recall": (tp / (tp + fn)) if tp + fn else None,
            "exact_match": exact if exact is not None else (fp == 0 and fn == 0), "no_issue_correct": (fp == 0) if not ref else None,
            "n_tool_calls": 5, "n_numbers": 1, "unsupported_numbers": 0, "uncited_confirmed": 0, "bad_citations": 0,
            "n_argument_errors": 0, "false_refutation_unverifiable": 0, "duration_s": 1.0, "total_tokens": 10,
            "pred": [["EVIDENCE_POSTHOC", ""]] if tp else [], "reference": reference}


# 7. three-trial within-task aggregation (zeros included, not dropped)
def test_three_trial_mean_includes_zero_detection():
    rows = [row("B", "T1", 1, 1, 0, 0), row("B", "T1", 2, 0, 0, 1), row("B", "T1", 3, 0, 0, 1, report=False),
            row("A", "T1", 1, 1, 0, 0)]
    ag.finalize(rows)
    S = ag.summarize(rows, "t", ag.check_integrity(rows, {"T1"}))
    assert S["conditions"]["B"]["f1"][0] == pytest.approx(1 / 3)
    assert S["conditions"]["B"]["f1_n_tasks"] == 1


# 8. paired bootstrap requires identical task membership
def test_paired_comparison_uses_all_tasks():
    rows = [row("A", t, 1, 1, 0, 0) for t in ("T1", "T2", "T3")] + [row("B", t, 1, 0, 0, 1) for t in ("T1", "T2", "T3")]
    ag.finalize(rows)
    S = ag.summarize(rows, "t", ag.check_integrity(rows, {"T1", "T2", "T3"}))
    d = S["conditions"]["B"]["paired_diff_vs_A"]
    assert d["f1_n_tasks"] == 3 and d["f1"][0] == pytest.approx(-1.0)


def test_missing_task_for_a_condition_fails_loudly():
    rows = [row("A", t, 1, 1, 0, 0) for t in ("T1", "T2")] + [row("B", "T1", 1, 1, 0, 0)]
    ag.finalize(rows)
    with pytest.raises(ag.IntegrityError):
        ag.check_integrity(rows, {"T1", "T2"})


# 9. missing trial and malformed report
def test_malformed_report_gets_no_credit(tmp_path):
    s = scored(tmp_path, REF0, raw="{not json")
    assert s["report_submitted"] is False and s["report_malformed"] is True
    assert s["f1"] == 0.0 and s["no_issue_correct"] is False


def test_incomplete_trials_are_reported():
    rows = [row("B", "T1", k, 1, 0, 0) for k in (1, 2, 3)] + [row("B", "T2", 1, 1, 0, 0)] + \
           [row("A", t, 1, 1, 0, 0) for t in ("T1", "T2")]
    ag.finalize(rows)
    integ = ag.check_integrity(rows, {"T1", "T2"})
    assert integ["tasks_with_incomplete_trials"] == {"B": {"T2": 1}}


# 10. restricted pilot-task exclusion via --exclude-task
def test_exclude_task_from_scores_csv(tmp_path):
    rows = [row(c, t, 1, 1, 0, 0) for c in ("A", "B") for t in ("T01", "T29")]
    p = tmp_path / "scores_runs.csv"
    with open(p, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ag.CSV_KEYS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(r.get(k)) if isinstance(r.get(k), list) else r.get(k)) for k in ag.CSV_KEYS})
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps({"tasks": [task("T01"), task("T29")]}))
    S = ag.main(["--scores-csv", str(p), "--tasks", str(tasks), "--exclude-task", "T29", "--out-dir", str(tmp_path / "o")])
    assert S["integrity"]["n_tasks"] == 1 and S["integrity"]["excluded_tasks"] == ["T29"]
    out = list(csv.DictReader(open(tmp_path / "o" / "scores_runs.csv")))
    assert {r["task_id"] for r in out} == {"T01"}
    with pytest.raises(SystemExit):
        ag.main(["--scores-csv", str(p), "--tasks", str(tasks), "--exclude-task", "T99", "--out-dir", str(tmp_path / "o2")])
