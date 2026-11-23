"""Run audit tasks through OpenClaw (conditions B and D) or the deterministic baseline (condition A).

Conditions:
  A  deterministic checklist (no LLM)                      -> evaluation/baselines/deterministic_baseline.py
  B  OpenClaw agent + local LLM + full audit toolset       (adaptive tool selection)
  D  OpenClaw agent + local LLM + metadata-only toolset    (list artifacts, reported claims, submit)

Usage:
  python agents/run_agent.py --exp EXP_ID --cond B --tasks evaluation/tasks/tasks_dev.json [--task D01] [--trials 1]
Environment: AUDIT_LLM_PORT, AUDIT_LLM_MODEL_ID (required for B/D).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

A = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(A / "agents"))
sys.path.insert(0, str(A / "evaluation" / "baselines"))
from sandbox import sandbox_cmd  # noqa: E402

OPENCLAW = A / "environment" / "openclaw_npm" / "node_modules" / ".bin" / "openclaw"
NODE_BIN = Path.home() / ".local/opt/node-v24.22.3-linux-x64/bin"
PROTOCOL = A / "prompts" / os.environ.get("AUDIT_PROTOCOL", "agent_protocol_v2.md")
BASE_CFG = A / "configs" / "openclaw" / "openclaw.audit.json5"
MAX_CALLS = int(os.environ.get("AUDIT_MAX_CALLS", "40"))
TIMEOUT_S = 900
THINKING = os.environ.get("AUDIT_THINKING", "on")   # qwen-chat-template levels: off | on | ultra
# Qwen model-card thinking-mode settings for precise tasks; sent per request (server uses --generation-config vllm)
SAMPLING = {"temperature": 0.6, "topP": 0.95, "extra_body": {"top_k": 20}}
NOTOOL_TOOLS = ["list_cases", "list_case_artifacts", "get_reported_claims", "submit_audit_report"]
AUDIT_TOOLS = NOTOOL_TOOLS + ["validate_structured_outputs", "identify_failure_patterns", "inspect_run_metadata",
                              "measure_exact_text_support", "inspect_evidence_construction", "check_metric_denominators",
                              "evaluate_gold_labels", "evaluate_roundtrip", "verify_claim"]


def model_info(port: str) -> dict:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=10) as r:
            d = json.load(r)["data"][0]
        return {"served_id": d["id"], "root": d.get("root"), "max_model_len": d.get("max_model_len")}
    except Exception as e:
        return {"error": str(e)}


def tool_version() -> str:
    sys.path.insert(0, str(A / "tools"))
    import audit_core
    return audit_core.TOOL_VERSION


def vllm_version() -> str | None:
    return os.environ.get("AUDIT_VLLM_VERSION")


def openclaw_version() -> str:
    # Release fix: condition A (no LLM) must run without OpenClaw installed.
    if not OPENCLAW.exists():
        return "not installed"
    env = {**os.environ, "PATH": f"{NODE_BIN}:{os.environ['PATH']}"}
    return subprocess.run([str(OPENCLAW), "--version"], capture_output=True, text=True, env=env).stdout.strip()


def run_llm(task: dict, cond: str, run_dir: Path, trial: int) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    state = run_dir / "state"
    work = run_dir / "workspace"
    state.mkdir(exist_ok=True)
    work.mkdir(exist_ok=True)
    toolset = "full" if cond == "B" else "notool"
    cmd, args = sandbox_cmd(run_dir / "mcp", [task["case_id"]], toolset, MAX_CALLS)
    include = AUDIT_TOOLS if toolset == "full" else NOTOOL_TOOLS
    override = {"mcp": {"servers": {"audit": {"command": cmd, "args": args, "transport": "stdio", "enabled": True,
                                              "connectionTimeoutMs": 30000, "requestTimeoutMs": 300000,
                                              "toolFilter": {"include": include}}}},
                "agents": {"defaults": {"models": {f"vllm/{os.environ['AUDIT_LLM_MODEL_ID']}": {"params": SAMPLING}}}}}
    msg = PROTOCOL.read_text().replace("{max_calls}", str(MAX_CALLS)).replace("{instruction}", task["instruction"])
    (run_dir / "message.md").write_text(msg)
    port, mid = os.environ["AUDIT_LLM_PORT"], os.environ["AUDIT_LLM_MODEL_ID"]
    env = {"PATH": f"{NODE_BIN}:/usr/bin:/bin", "HOME": str(state), "VLLM_API_KEY": "vllm-local",
           "AUDIT_LLM_PORT": port, "AUDIT_LLM_MODEL_ID": mid, "LANG": "C.UTF-8",
           "OPENCLAW_DISABLE_BONJOUR": "1", "NO_COLOR": "1"}
    argv = [str(OPENCLAW), "agent", "exec", "--message-file", str(run_dir / "message.md"),
            "--config", str(BASE_CFG), "--config-override", json.dumps(override),
            "--state-dir", str(state), "--cwd", str(work), "--model", f"vllm/{mid}",
            "--thinking", THINKING,
            "--timeout", str(TIMEOUT_S), "--json"]  # HOME=state dir: no stored OpenClaw credentials visible
    t0 = time.time()
    try:
        p = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=TIMEOUT_S + 120)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        rc, out, err = -9, e.stdout or "", (e.stderr or "") + "\nHARNESS TIMEOUT"
    dur = time.time() - t0
    (run_dir / "openclaw_stdout.json").write_text(out if isinstance(out, str) else out.decode())
    (run_dir / "openclaw_stderr.log").write_text((err if isinstance(err, str) else err.decode())[-20000:])
    try:
        res = json.loads(out)
    except Exception:
        res = {}
    usage = res.get("usage") or {}
    for src in ("mcp/tool_calls.jsonl", "mcp/report.json"):
        s = run_dir / src
        if s.exists():
            (run_dir / Path(src).name).write_text(s.read_text())
    meta = {"condition": f"{cond}_openclaw_{toolset}", "task_id": task["task_id"], "case_id": task["case_id"],
            "trial": trial, "exit_code": rc, "openclaw_status": res.get("status"), "duration_s": round(dur, 2),
            "total_tokens": usage.get("total") or usage.get("totalTokens") or usage.get("total_tokens"),
            "usage": usage, "tool_summary": res.get("toolSummary"), "final_text": (res.get("final") or "")[:4000]}
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", required=True)
    ap.add_argument("--cond", required=True, choices=["A", "B", "D"])
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--task", action="append")
    ap.add_argument("--trials", type=int, default=1)
    a = ap.parse_args()
    tasks = json.loads(Path(a.tasks).read_text())["tasks"]
    if a.task:
        tasks = [t for t in tasks if t["task_id"] in a.task]
    exp_dir = A / "runs" / a.exp
    exp_dir.mkdir(parents=True, exist_ok=True)
    manifest_p = exp_dir / f"experiment_{a.cond}.json"
    if not manifest_p.exists():
        port = os.environ.get("AUDIT_LLM_PORT")
        manifest = {"experiment_id": a.exp, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "hostname": socket.gethostname(),
                    "platform": platform.platform(), "model": model_info(port) if port else None,
                    "model_id_configured": os.environ.get("AUDIT_LLM_MODEL_ID"),
                    "model_revision": os.environ.get("AUDIT_LLM_REVISION"), "vllm_version": vllm_version(),
                    "openclaw_version": openclaw_version(), "prompt_version": PROTOCOL.name,
                    "tool_version": tool_version(),
                    "baseline_rules_version": "1.0.0", "evaluation_set": str(Path(a.tasks).name),
                    "frozen_manifest": json.loads((A / "evaluation/tasks/FROZEN_MANIFEST.json").read_text()),
                    "sampling": SAMPLING,
                    "max_tool_calls": MAX_CALLS, "timeout_s": TIMEOUT_S, "thinking": THINKING}
        manifest_p.write_text(json.dumps(manifest, indent=2))
    for t in tasks:
        for k in range(1, a.trials + 1):
            rd = exp_dir / a.cond / t["task_id"] / f"trial{k}"
            if (rd / "run_meta.json").exists():
                print("skip", rd)
                continue
            if a.cond == "A":
                import deterministic_baseline as db
                m = db.run(t["case_id"], rd)
                m.update(task_id=t["task_id"], trial=k)
                (rd / "run_meta.json").write_text(json.dumps(m, indent=2))
            else:
                m = run_llm(t, a.cond, rd, k)
            print(json.dumps({k2: m.get(k2) for k2 in ("task_id", "trial", "exit_code", "openclaw_status", "duration_s", "total_tokens")}))


if __name__ == "__main__":
    main()
