import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

A = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(A / "tools"))
sys.path.insert(0, str(A / "evaluation" / "baselines"))


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fx")
    specs = [
        {"case_id": "t_posthoc", "seed": 1, "n_notes": 30, "evidence_mode": "posthoc", "cap_total": 6, "cap_meds": 2,
         "med_impl": "zero_fill", "no_med_section_frac": 0.3, "n_fail": 3, "fail_clustered": True,
         "perturb_claims": ["mean_coverage"], "all_records_convention": ["mean_evidence_count"]},
        {"case_id": "t_model", "seed": 2, "n_notes": 25, "evidence_mode": "model", "med_impl": "null_na"},
        {"case_id": "t_missing", "seed": 3, "n_notes": 20, "evidence_mode": "model", "med_impl": "null_na",
         "drop_roundtrip": True, "n_malformed": 2, "drop_run_meta": True},
    ]
    (tmp / "spec.json").write_text(json.dumps(specs))
    subprocess.run([sys.executable, str(A / "evaluation/fixtures/make_fixtures.py"), "--out", str(tmp / "cases"),
                    "--refs", str(tmp / "refs"), "--registry", str(tmp / "cases.json"), "--spec", str(tmp / "spec.json")],
                   check=True)
    os.environ["AUDIT_CASES_FILE"] = str(tmp / "cases.json")
    os.environ.pop("AUDIT_ALLOWED_CASES", None)
    import audit_core
    importlib.reload(audit_core)
    return audit_core, tmp


def texts(tmp, case):
    out = []
    for p in (tmp / "cases" / case / "views").glob("*.txt"):
        out.append(p.read_text())
    return out


def test_registry_restriction(env):
    core, _ = env
    with pytest.raises(core.AuditError):
        core.validate_structured_outputs("../../etc/passwd")
    os.environ["AUDIT_ALLOWED_CASES"] = "t_model"
    try:
        with pytest.raises(core.AuditError):
            core.validate_structured_outputs("t_posthoc")
    finally:
        os.environ.pop("AUDIT_ALLOWED_CASES")


def test_validity_counts(env):
    core, _ = env
    r = core.validate_structured_outputs("t_posthoc")["result"]
    assert r["n_failed"] == 3 and r["n_valid"] == 27
    r = core.validate_structured_outputs("t_missing")["result"]
    assert r["n_malformed_receipt_files"] == 2


def test_failure_cluster(env):
    core, _ = env
    r = core.identify_failure_patterns("t_posthoc")["result"]
    assert r["top_subject_share_of_failures"] == 1.0


def test_evidence_posthoc_detected(env):
    core, _ = env
    r = core.inspect_evidence_construction("t_posthoc")["result"]
    assert r["static"]["prompt_requests_empty_evidence"] is True
    assert r["empirical"]["share_spans_at_first_occurrence_in_view"] == 1.0
    assert r["empirical"]["max_spans_per_record"] <= 6
    m = core.inspect_evidence_construction("t_model")["result"]
    assert m["static"]["prompt_requests_empty_evidence"] is False
    assert m["empirical"]["share_spans_at_first_occurrence_in_view"] < 0.99


def test_denominators(env):
    core, _ = env
    r = core.check_metric_denominators("t_posthoc", "med_section_alignment_rate")["result"]
    assert r["n_not_applicable_scored_zero"] > 0
    m = core.check_metric_denominators("t_model", "med_section_alignment_rate")["result"]
    assert m["n_not_applicable_scored_zero"] == 0
    with pytest.raises(core.AuditError):
        core.check_metric_denominators("t_model", "rm -rf")


def test_claims_reproduce_exactly_as_injected(env):
    core, tmp = env
    for case in ("t_posthoc", "t_model", "t_missing"):
        ref = json.loads((tmp / "refs" / f"{case}.json").read_text())
        bad = {i["target"] for i in ref["issues"] if i["code"] == "CLAIM_NOT_REPRODUCED"}
        for c in core.get_reported_claims(case)["result"]["claims"]:
            v = core.verify_claim(case, c["claim_id"])
            if c["claim_id"] in ref["unverifiable_claims"]:
                assert v["status"] == "cannot_verify", (case, c)
            elif c["claim_id"] in bad:
                assert v["status"] == "not_reproduced", (case, c, v)
            else:
                assert v["status"] == "reproduced", (case, c, v)


def test_no_text_leak(env):
    core, tmp = env
    for case in ("t_posthoc", "t_model"):
        outs = json.dumps([core.list_case_artifacts(case), core.validate_structured_outputs(case),
                           core.identify_failure_patterns(case), core.inspect_run_metadata(case),
                           core.measure_exact_text_support(case), core.inspect_evidence_construction(case),
                           core.evaluate_gold_labels(case), core.evaluate_roundtrip(case)])
        for t in texts(tmp, case):
            for line in t.splitlines():
                if len(line) > 40:
                    assert line not in outs


def test_read_only(env):
    core, tmp = env
    before = {p: p.stat().st_mtime_ns for p in (tmp / "cases").rglob("*") if p.is_file()}
    core.measure_exact_text_support("t_posthoc")
    core.inspect_evidence_construction("t_posthoc")
    after = {p: p.stat().st_mtime_ns for p in (tmp / "cases").rglob("*") if p.is_file()}
    assert before == after


def test_baseline_matches_reference(env):
    core, tmp = env
    import deterministic_baseline as db
    importlib.reload(db)
    for case in ("t_posthoc", "t_model", "t_missing"):
        db.run(case, tmp / "runs" / case)
        rep = json.loads((tmp / "runs" / case / "report.json").read_text())
        pred = {(f["code"], f["target"]) for f in rep["findings"] if f["code"] != "NO_ISSUES_FOUND"}
        ref = {(i["code"], i["target"]) for i in json.loads((tmp / "refs" / f"{case}.json").read_text())["issues"]}
        assert pred == ref, (case, pred ^ ref)
