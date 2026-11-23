"""Build the command that runs the audit MCP server inside a Landlock sandbox (agents/landlock_exec.py).

bubblewrap was evaluated first but cannot run on the study server: AppArmor restricts unprivileged user
namespaces (kernel.apparmor_restrict_unprivileged_userns=1). Landlock (ABI 4) needs no privileges.

Enforced by the kernel, not by prompts:
  * readable: system libraries, the Python runtime, tool code, the case registry/claims, and ONLY the
    artifact paths registered for the case(s) allowed in this run
  * writable: only the run directory (tool log, report, private tmp)
  * no TCP bind/connect; no_new_privs; environment replaced by an allowlist (no API keys, no SSH agent)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

A = Path(__file__).resolve().parents[1]
# Release fix: the research copy hard-coded environment/.venv; default to the running interpreter's venv.
VENV = Path(os.environ.get("AUDIT_VENV", sys.prefix))
PY_REAL = Path(os.path.realpath(VENV / "bin" / "python3"))
PY_HOME = PY_REAL.parents[1]


def case_paths(case_ids: list[str]) -> list[Path]:
    reg = json.loads((A / "configs" / "cases.json").read_text())
    out = []
    for cid in case_ids:
        spec = reg[cid]
        base = Path(spec.get("base", A))
        for k, v in spec.items():
            if k in ("base", "description"):
                continue
            for x in (v if isinstance(v, list) else [v]):
                p = Path(os.path.expanduser(x))
                p = p if p.is_absolute() else base / p
                if p.exists():
                    out.append(p)
    return sorted(set(out))


def sandbox_cmd(run_dir: Path, case_ids: list[str], toolset: str = "full", max_calls: int = 25,
                target: list[str] | None = None) -> tuple[str, list[str]]:
    run_dir.mkdir(parents=True, exist_ok=True)
    tmp = run_dir / "tmp"
    tmp.mkdir(exist_ok=True)
    ro = ["/usr", "/lib", "/lib64", "/bin", "/etc/ld.so.cache", "/etc/localtime", "/dev/urandom",
          str(PY_HOME / "bin"), str(PY_HOME / "lib"), str(VENV), str(A / "tools"),
          str(A / "configs" / "cases.json"), str(A / "configs" / "claims"), str(A / "agents" / "landlock_exec.py")]
    ro += [str(p) for p in case_paths(case_ids)]
    rw = [str(run_dir), "/dev/null"]
    env = {"AUDIT_RUN_DIR": str(run_dir), "AUDIT_ROOT": str(A), "AUDIT_ALLOWED_CASES": ",".join(case_ids),
           "AUDIT_TOOLSET": toolset, "AUDIT_MAX_CALLS": str(max_calls), "HOME": str(tmp), "TMPDIR": str(tmp),
           "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"}
    args = [str(A / "agents" / "landlock_exec.py")]
    for p in ro:
        args += ["--ro", p]
    for p in rw:
        args += ["--rw", p]
    for k, v in env.items():
        args += ["--env", f"{k}={v}"]
    args += ["--", str(VENV / "bin" / "python3")] + (target or [str(A / "tools" / "mcp_server.py")])
    return str(VENV / "bin" / "python3"), args
