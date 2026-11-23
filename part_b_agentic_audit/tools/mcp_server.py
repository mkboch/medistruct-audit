"""MCP (stdio) server exposing the deterministic audit tools to an OpenClaw agent.

Environment:
  AUDIT_RUN_DIR        writable directory for this run's tool log and report (required)
  AUDIT_ALLOWED_CASES  comma-separated case ids visible in this run
  AUDIT_MAX_CALLS      tool-call budget (default 30); submit_audit_report is always allowed
  AUDIT_TOOLSET        "full" (default) or "notool" (only list/claims/submit; condition D)
"""
from __future__ import annotations

import json
import os
import time
import traceback
import uuid
from pathlib import Path

from mcp.server.mcpserver import MCPServer

import audit_core as core

RUN_DIR = Path(os.environ["AUDIT_RUN_DIR"])
RUN_DIR.mkdir(parents=True, exist_ok=True)
LOG = RUN_DIR / "tool_calls.jsonl"
MAX_CALLS = int(os.environ.get("AUDIT_MAX_CALLS", "30"))
TOOLSET = os.environ.get("AUDIT_TOOLSET", "full")
_state = {"n": 0}

server = MCPServer(
    "clinical-extraction-audit",
    instructions=(
        "Deterministic, read-only audit tools over saved clinical-extraction artifacts. "
        "Tools compute all numbers; never state a number that no tool returned. Every tool result has a "
        "call_id; cite call_ids as evidence in submit_audit_report. Finish by calling submit_audit_report."
    ),
)


def _log(entry: dict) -> None:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _run(name: str, fn, **kwargs) -> str:
    call_id = "c" + uuid.uuid4().hex[:8]
    t0 = time.time()
    entry = {"call_id": call_id, "tool": name, "args": kwargs, "t_start": t0}
    if name != "submit_audit_report":
        _state["n"] += 1
        if _state["n"] > MAX_CALLS:
            out = {"call_id": call_id, "error": f"tool-call budget of {MAX_CALLS} exhausted; call submit_audit_report now"}
            _log({**entry, "ok": False, "error": "budget_exhausted", "duration_s": 0})
            return json.dumps(out)
    try:
        res = fn(**kwargs)
        out = {"call_id": call_id, **res}
        _log({**entry, "ok": True, "duration_s": round(time.time() - t0, 3), "result": res})
    except core.AuditError as e:
        out = {"call_id": call_id, "error": str(e)}
        _log({**entry, "ok": False, "error": "argument_error: " + str(e), "duration_s": round(time.time() - t0, 3)})
    except Exception as e:  # tool failure: report, do not crash the server
        out = {"call_id": call_id, "error": f"tool failure: {type(e).__name__}: {e}"}
        _log({**entry, "ok": False, "error": "tool_failure", "trace": traceback.format_exc()[-2000:],
              "duration_s": round(time.time() - t0, 3)})
    return json.dumps(out, ensure_ascii=False)


@server.tool()
def list_cases() -> str:
    """List the case ids available for auditing in this session."""
    return _run("list_cases", lambda: {"result": {"case_ids": core.allowed_case_ids()}})


@server.tool()
def list_case_artifacts(case_id: str) -> str:
    """List which saved artifacts (receipts, outputs, views, run metadata, code, prompt, labels, roundtrip, claims) are registered for a case and whether each exists."""
    return _run("list_case_artifacts", core.list_case_artifacts, case_id=case_id)


@server.tool()
def get_reported_claims(case_id: str) -> str:
    """Return the quantitative claims reported for a case (claim_id, text, metric_key, reported_value)."""
    return _run("get_reported_claims", core.get_reported_claims, case_id=case_id)


if TOOLSET == "full":
    @server.tool()
    def validate_structured_outputs(case_id: str) -> str:
        """Count parsed/malformed receipt files, JSON- and schema-valid outputs, and failure categories."""
        return _run("validate_structured_outputs", core.validate_structured_outputs, case_id=case_id)

    @server.tool()
    def identify_failure_patterns(case_id: str) -> str:
        """Summarize failed records: categories, concentration by subject, trimmed views, view lengths."""
        return _run("identify_failure_patterns", core.identify_failure_patterns, case_id=case_id)

    @server.tool()
    def inspect_run_metadata(case_id: str) -> str:
        """Read run metadata files and compare them with the model/prompt/decoding/view settings recorded in receipts."""
        return _run("inspect_run_metadata", core.inspect_run_metadata, case_id=case_id)

    @server.tool()
    def measure_exact_text_support(case_id: str) -> str:
        """Uncapped rate at which extracted items occur verbatim (case-insensitive) in the extraction view and, if available, the source note, by field."""
        return _run("measure_exact_text_support", core.measure_exact_text_support, case_id=case_id)

    @server.tool()
    def inspect_evidence_construction(case_id: str) -> str:
        """Inspect how evidence spans are produced: prompt/code inspection plus empirical checks of saved spans (matching, occurrence position, per-record limits, offset base)."""
        return _run("inspect_evidence_construction", core.inspect_evidence_construction, case_id=case_id)

    @server.tool()
    def check_metric_denominators(case_id: str, metric: str) -> str:
        """For a per-record receipt metric (span_valid_rate, coverage, substring_support_rate, med_section_alignment_rate, evidence_count), compare means under different denominators and count non-applicable records and how they were scored."""
        return _run("check_metric_denominators", core.check_metric_denominators, case_id=case_id, metric=metric)

    @server.tool()
    def evaluate_gold_labels(case_id: str) -> str:
        """Recompute micro precision/recall/F1 of saved predictions against the saved reference labels, by field."""
        return _run("evaluate_gold_labels", core.evaluate_gold_labels, case_id=case_id)

    @server.tool()
    def evaluate_roundtrip(case_id: str) -> str:
        """Recompute roundtrip (structured->text->structured) Jaccard statistics from the saved roundtrip file."""
        return _run("evaluate_roundtrip", core.evaluate_roundtrip, case_id=case_id)

    @server.tool()
    def verify_claim(case_id: str, claim_id: str) -> str:
        """Recompute one reported claim from saved artifacts under each applicable convention and state whether the reported value is reproduced within tolerance."""
        return _run("verify_claim", core.verify_claim, case_id=case_id, claim_id=claim_id)


@server.tool()
def submit_audit_report(case_id: str, findings: list[dict], summary: str) -> str:
    """Submit the final audit report. findings: list of objects with keys
    code (one of: CLAIM_NOT_REPRODUCED, ZERO_FILLED_DENOMINATOR, MIXED_DENOMINATORS, EVIDENCE_POSTHOC,
    EVIDENCE_CAP_LIMITS_COVERAGE, OFFSETS_NOT_IN_SOURCE, TAUTOLOGICAL_METRIC, HETEROGENEOUS_RUN_SETTINGS,
    MISSING_RUN_METADATA, MALFORMED_RECEIPTS, MISSING_ARTIFACT, FAILURES_CLUSTERED, NO_ISSUES_FOUND, OTHER),
    category (confirmed_finding | suspicion | missing_evidence | tool_failure | interpretation | recommendation),
    target (claim_id for CLAIM_NOT_REPRODUCED; metric name for ZERO_FILLED_DENOMINATOR and TAUTOLOGICAL_METRIC;
    artifact key for MISSING_ARTIFACT; otherwise empty), statement (text; any number must come from a cited tool result),
    evidence_call_ids (list of call_ids supporting the statement). summary: short overall conclusion."""
    def _submit():
        errs = core.validate_report(findings)
        if errs:
            raise core.AuditError("report rejected: " + "; ".join(errs[:10]))
        rep = {"case_id": case_id, "findings": findings, "summary": summary, "submitted_at": time.time()}
        (RUN_DIR / "report.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"result": {"accepted": True, "n_findings": len(findings)}}
    return _run("submit_audit_report", _submit)


if __name__ == "__main__":
    server.run("stdio")
