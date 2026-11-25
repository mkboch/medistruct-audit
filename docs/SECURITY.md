# Security and release controls

## How this release was assembled
- **Fresh history.** The repository was created with a new Git history. No commits, branches or reflogs from the research workspace were copied.
- **Explicit allow-list.** Files were copied one by one from an allow-list; nothing was copied recursively. Symbolic links were refused.
- **Provenance-based exclusion (primary control).** Excluded categories:
  - every file under the historical `Dataset/` and `Outputs/` trees;
  - every agent run trace;
  - the external pilot case;
  - the reference labels;
  - the case registry with local paths;
  - internal notes and server inventories.
- **Content checks (secondary controls):**
  - a secrets scanner;
  - identifier pattern searches;
  - an n-gram overlap scan of every released text file against the restricted corpora (source notes, extraction views, regenerated notes, reference labels). Only counts were reported.

  Results are recorded in `CODE_RELEASE_AUDIT.md` in the authors' private records.

## Runtime security of the agentic audit
There are three processes, and they are protected differently.

| Process | How it is restricted |
|---|---|
| MCP audit-tool server (`tools/mcp_server.py`) | **Kernel-enforced Landlock sandbox** (details below) |
| OpenClaw agent process | **Configuration only** (`configs/openclaw/openclaw.audit.json5`), not Landlock |
| Model server (vLLM) | **Not sandboxed by this code.** Serve it locally on infrastructure you control |

**OpenClaw agent process.** It is run per task with `openclaw agent exec`, so no gateway or network listener is started.
- Its built-in runtime, file-system, web, UI, automation, messaging, session, memory and browser tools are denied, exec is denied, and elevated mode is disabled.
- In the experiments, the agent's model could therefore call only the MCP audit tools.
- OpenClaw's own container sandbox is switched off, because it needs Docker, which was unavailable. These restrictions therefore rely on OpenClaw enforcing its configuration correctly.
- The harness gives OpenClaw a private per-run `HOME` and an explicit environment, so stored OpenClaw credentials are not visible.

**MCP audit-tool server** (`agents/sandbox.py` with `agents/landlock_exec.py`; Linux ≥ 6.7, Landlock ABI ≥ 4; it refuses to start without ABI 4):
- **reads and execution** are limited to:
  - system library and binary directories;
  - the Python runtime and virtual environment;
  - the tool code;
  - the case registry and claims files;
  - the artifact paths registered for the case(s) allowed in that run.

  Other cases' artifacts, the synthetic reference answers and the home directory are not readable;
- **writes** are limited to the run directory and `/dev/null`;
- **TCP** bind and connect are denied;
- `no_new_privs` is set, and the environment is replaced by an allow-list (no API keys, no SSH agent);
- tools accept only registered case IDs, not free file paths, and the process enforces a tool-call budget.

**Limits of the sandbox.** It reduces what the tool process can do; it does not guarantee containment against every defect.
- **Sockets:** Landlock ABI 4 does not restrict UDP or Unix-domain sockets. The tool server has no code paths that use them, but the kernel does not block them.
- **Access rights:** the sandbox handles file-system rights up to Landlock ABI 3. Rights added in later ABIs (for example device ioctl) are not restricted.
- **What was tested:** `tests/test_sandbox_mcp.py` (with `tests/sandbox_probe.py`) checks the following inside the sandbox:
  - listing the home directory, `~/.ssh`, another case's artifacts and the reference answers;
  - writing into another case's directory;
  - outbound TCP to an external and a local address;
  - the environment contents;
  - that the tools reject an unregistered case.

  It does not test every possible escape route.

## Reporting
Report suspected sensitive content in this repository privately to the corresponding author. Do not open a public issue.
