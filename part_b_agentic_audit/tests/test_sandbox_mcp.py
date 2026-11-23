"""Integration test (Linux, Landlock ABI >= 4): start the sandboxed MCP server with a real MCP stdio
client on a synthetic case, call tools, then run sandbox_probe.py inside an identical sandbox.

Prerequisite: python evaluation/tasks/build_tasks.py   (generates synthetic cases incl. H01, H02)
Run:          python tests/test_sandbox_mcp.py [H02] [H01]
"""
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

A = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(A / "agents"))
from sandbox import sandbox_cmd  # noqa: E402

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


async def main(case_id: str, other_case: str):
    run_dir = Path(tempfile.mkdtemp(prefix="mcp_sbx_"))
    cmd, args = sandbox_cmd(run_dir, [case_id], "full", 25)
    async with stdio_client(StdioServerParameters(command=cmd, args=args)) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("tools:", sorted(t.name for t in tools.tools))
            res = await s.call_tool("validate_structured_outputs", {"case_id": case_id})
            print("validate ok:", "error" not in json.loads(res.content[0].text))
            res = await s.call_tool("list_case_artifacts", {"case_id": other_case})
            print("disallowed case rejected:", "disallowed" in res.content[0].text)
    reg = json.loads((A / "configs" / "cases.json").read_text())
    allowed = str(Path(reg[case_id]["base"]) / reg[case_id]["receipts_dir"])
    other = str(Path(reg[other_case]["base"]) / reg[other_case]["receipts_dir"])
    probe = str(A / "tests" / "sandbox_probe.py")
    c2, a2 = sandbox_cmd(run_dir, [case_id], "full", 25,
                         target=[probe, str(A / "evaluation/fixtures/references"), allowed, other])
    cut = a2.index("--")
    a2 = a2[:cut] + ["--ro", probe, "--env", f"PROBE_REAL_HOME={Path.home()}"] + a2[cut:]
    pr = subprocess.run([c2] + a2, capture_output=True, text=True)
    print(pr.stdout, pr.stderr[-500:])
    print("tool log lines:", sum(1 for _ in open(run_dir / "tool_calls.jsonl")))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "H02", sys.argv[2] if len(sys.argv) > 2 else "H01"))
