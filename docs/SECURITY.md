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
- The agent model can call only the audit tools. OpenClaw's built-in shell, file-system, web, messaging and automation tools are denied by configuration.
- The tool server runs under Landlock (`agents/landlock_exec.py`):
  - reads are limited to system libraries, the Python runtime, tool code and the artifacts of the case under audit;
  - writes are limited to the run directory;
  - TCP is denied;
  - `no_new_privs` is set;
  - the environment is replaced by an allow-list.
- Landlock ABI 4 does not cover UDP or Unix sockets. The tool server has no code paths that use them.

## Reporting
Report suspected sensitive content in this repository privately to the corresponding author. Do not open a public issue.
