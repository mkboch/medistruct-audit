# Data access

## What this repository contains
This repository distributes research source code, synthetic evaluation fixtures, synthetic run-level scores, and aggregate research results. It does not distribute restricted MIMIC-IV-Note clinical notes, patient-level extraction outputs, reference annotations, or restricted patient-level derivatives.

Included:
- **Source code:** the extraction and roundtrip code, prompts and JSON schemas, audit tools, agent harness, scoring and table scripts, and tests.
- **Synthetic evaluation fixtures:**
  - the fault-injection generator and its specifications (`evaluation/fixtures/`);
  - the task sets;
  - the reference answers for the synthetic cases.

  The synthetic case artifacts themselves are generated deterministically by `evaluation/tasks/build_tasks.py` and are not stored in git.
- **Synthetic run-level scores:** `part_b_agentic_audit/manuscript/corrected_28_tasks/scores_runs.csv` covers the 28 synthetic held-out tasks. It holds finding codes, counts, timings and token totals computed on synthetic fault-injected cases. It has no clinical text and no rows for the pilot case or the real extraction run.
- **Aggregate research results:**
  - Part B summaries and tables (primary 28-task analysis, archived 29-task summaries);
  - model-selection summaries;
  - for the real extraction run, the study-level claims checked by the audit tools (`configs/claims/real_mimic5k.json`) and the corresponding audit reference answers. These contain aggregate values and finding codes only, with no note identifiers.

## What is not in this repository
- MIMIC-IV-Note notes, tables or files, nor any data derived from them at the patient or note level, such as extraction views, model outputs, receipts with note identifiers, evidence spans or regenerated notes;
- the 50-note reference annotations or per-note predictions;
- data from the external pilot used as one held-out audit case (task T29);
- per-run agent traces (tool logs, reports, OpenClaw state).

## MIMIC-IV-Note
MIMIC-IV-Note is a restricted-access resource. Obtain it yourself from PhysioNet (https://physionet.org/content/mimic-iv-note/), which requires:
- credentialed access;
- completion of the required human-subjects training;
- signing the PhysioNet Credentialed Health Data Use Agreement 1.5.0.

Do not ask the authors for copies; they are not permitted to share the data.

## Where to put your own authorised copy
Keep data outside version control. The provided `.gitignore` excludes `data_local/`, `Dataset/`, `Outputs/`, `*.csv.gz`, `*.jsonl` and run directories. A suggested layout is:

```
data_local/
  discharge.csv.gz            # your own copy of MIMIC-IV-Note discharge.csv.gz
  inputs_5k.jsonl             # produced by mimic_discharge_loader.py
  extract_run/                # produced by run_extract.py
```

To audit the historical layout with the Part B tools, set `MEDSTRUCT_HIST_DIR` to a directory containing `Codes/`, `Dataset/` and `Outputs/` in the original structure.

## Language models and online services
Run every model that receives clinical text **locally**, on infrastructure you control. PhysioNet's guidance on large language models states that credentialed data must not be sent to third-party online services unless:
- zero data retention is verified;
- data are not used for training; and
- there is no human review.

The extraction scripts send text to whatever `OPENAI_BASE_URL` you configure, so set it to a local server only.

### What the audit tools read and what the audit model receives
- **The deterministic audit-tool process** (`tools/audit_core.py`, served by `tools/mcp_server.py`) can read the artifacts registered for the case under audit in the case registry `part_b_agentic_audit/configs/cases.json`. `build_tasks.py` generates the registry locally, and it is not stored in git. For a real extraction run these can include receipts, structured outputs, extraction views, the source-note file, reference annotations and roundtrip outputs, which the tools need in order to compute measurements. The process cannot read other cases' artifacts, the hidden synthetic reference answers or the user's home directory; see docs/SECURITY.md for what the sandbox does and does not restrict.
- **The language model** receives only the tools' structured return values. By design these are:
  - counts and rates;
  - record identifiers (for example failed note IDs);
  - file paths and SHA-256 hashes;
  - lines from the registered prompt and extractor code;
  - the authored study-level claims and provenance metadata.

  No tool returns note text, extraction views, extracted item strings or reference-label strings. One residual path exists: when a tool fails unexpectedly, the MCP server returns the exception type and message to the model, and the tools do not sanitise that message.
- **Record identifiers and paths:** if you audit restricted runs, tool results and run logs contain record identifiers and local file paths. Keep run directories private, and serve the audit model locally.
