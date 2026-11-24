# medistruct-audit

Code for the preprint *Auditable clinical note structuring with local language models, and an evaluation of agentic quality assurance* (manuscript link: to be added after posting).

**This repository contains source code only.** It contains no clinical notes, no MIMIC-IV-Note data or derived patient-level records, no reference labels, and no model outputs for real patients. See [docs/DATA_ACCESS.md](docs/DATA_ACCESS.md).

## What the study did

**Part A: local schema-constrained extraction (historical study, March 2026).**
- Qwen2.5-7B-Instruct, served locally with vLLM 0.16.0, converted 5,000 MIMIC-IV-Note discharge summaries into symptoms, diagnoses, medications and an assessment summary under a JSON schema.
- Every note received a hash-stamped receipt.
- The study measured:
  - schema validity;
  - lexical grounding (verbatim presence of extracted items);
  - agreement with a 50-note reference set against rule-based baselines;
  - structured → text → structured roundtrip self-consistency.
- Evidence spans were attached **after** extraction by string matching. The paper explains why several common evidence metrics are therefore uninformative.

**Part B: agentic quality assurance (November 2026).**
- An OpenClaw 2026.11.6 agent, driven by a locally served Qwen3.5-122B-A10B model, audits *saved* extraction artifacts.
- It does this only through twelve deterministic, read-only audit tools. The tools are exposed over the Model Context Protocol and sandboxed with Linux Landlock.
- It was compared on frozen held-out tasks with:
  - a deterministic checklist that uses the same tools;
  - the same agent without the audit tools (metadata only).
- **The deterministic checklist was more accurate than the agent at complete fault detection.** The agent was more efficient only on some focused questions.

**The two models had different roles.** Qwen2.5-7B-Instruct performed the clinical extraction and the roundtrip; Qwen3.5-122B-A10B only drove the audit agent over saved artifacts. Neither is evaluated as a competitor to the other.

## Primary results (28 synthetic held-out tasks, scoring v2)

| Metric (mean over 28 tasks) | Deterministic checklist | OpenClaw with audit tools | OpenClaw without audit tools |
|---|---:|---:|---:|
| Task F1 | 0.962 | 0.876 | 0.350 |
| Exact finding-set match | 0.929 | 0.667 | 0.202 |
| Pooled micro-F1 | 0.982 | 0.863 | 0.341 |

Paired differences (OpenClaw with audit tools − checklist; 95% task-bootstrap CI):
- task F1: −0.085 [−0.184, 0.020];
- exact match: −0.262 [−0.429, −0.095].

**Provenance of these numbers:**
- Source: [`part_b_agentic_audit/manuscript/corrected_28_tasks/`](part_b_agentic_audit/manuscript/corrected_28_tasks/README.md), regenerated from the released run-level scores.
- Task T29, an audit of a ten-note pilot case, is excluded because the pilot's provenance could not be verified.
- **Scoring v2 fixes a defect in the first release.** Runs that detected none of a task's faults had undefined task F1 and were silently dropped; they now score 0.
- The original 29-task v1 summary is kept unchanged in `part_b_agentic_audit/runs/EXP_heldout/scores_summary.json`.

## Repository layout

```
part_a_extraction/
  Codes/                     historical extraction code, preserved in its original layout
    run_extract.py           extraction runner (vLLM OpenAI-compatible API, JSON-schema output, receipts)
    utils.py                 extraction view, normalisation, schema validation, evidence metrics, Jaccard
    mimic_discharge_loader.py  first-N loader for the MIMIC-IV-Note discharge table (user-supplied file)
    prompts/                 all historical prompt templates (see docs/REPRODUCIBILITY.md for which were used)
    configs/                 JSON schemas
    baselines/               rule-based, scispaCy, medication-rule and LLM-no-tool baselines
    eval/eval_gold50_baselines.py   per-note reference-set scoring for all systems
    eval/make_pnas_table_results.py historical table script (contains a known key bug; kept for provenance)
    run_roundtrip_vllm.py    roundtrip regeneration + re-extraction (the script used for the paper)
    roundtrip_eval.py        roundtrip Jaccard scoring (reproduces the saved summary byte-for-byte)
  reconstructed/
    gold50_micro_summary.py  RECONSTRUCTED micro-F1 summary (original script not preserved)
part_b_agentic_audit/
  tools/                     deterministic audit tools (audit_core.py) and MCP server (mcp_server.py)
  agents/                    Landlock launcher, sandbox builder, OpenClaw experiment harness
  configs/                   pinned OpenClaw config; claims for the (restricted) real 5k case
  prompts/                   agent protocol v1 (development) and v2 (frozen for held-out runs)
  evaluation/                synthetic fault-injection generator, task sets, references, baseline, scoring
  environment/               Python lockfile, OpenClaw npm pin, model-serving template
  manuscript/make_tables.py  generates the paper's Part B tables from aggregate results
  runs/                      aggregate results only (no per-run traces)
  tests/                     unit, sandbox, plumbing and tool-calling tests
tests/test_part_a_smoke.py   CPU-only synthetic tests for Part A
requirements/                pinned dependency lists
docs/                        REPRODUCIBILITY, DATA_ACCESS, SECURITY, PROVENANCE
```

## Installation (Linux, Python 3.11–3.13)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements/extraction.txt      # Part A client side
pip install -r requirements/agentic.txt         # Part B tools, MCP server, tests
# optional baselines: pip install -r requirements/extraction-baselines-optional.txt
```

- **Model serving:** a GPU server with vLLM is needed separately; it is not installed by these files. See `requirements/vllm-server-historical.txt` and `docs/REPRODUCIBILITY.md`.
- **Part B:** additionally needs Node.js ≥ 24.16 (24.22.3 was used) and OpenClaw 2026.11.6 (`part_b_agentic_audit/environment/openclaw_npm/package.json`).

## Tests (CPU only, no data, no GPU)

```bash
python -m pytest -q tests                                  # Part A smoke tests (synthetic text)
cd part_b_agentic_audit
python -m pytest -q tests/test_audit_core.py tests/test_scoring_regression.py   # audit tools; scoring v2 regressions
python evaluation/tasks/build_tasks.py                     # generate synthetic cases + task sets
python tests/test_sandbox_mcp.py H02 H01                   # Landlock sandbox + MCP (Linux >= 6.7)
# reproduce the paper's primary 28-task analysis and tables from the released run-level scores
python evaluation/scoring/aggregate.py --scores-csv manuscript/corrected_28_tasks/scores_runs.csv \
    --tasks evaluation/tasks/tasks_heldout.json --exclude-task T29 --out-dir /tmp/medistruct_28
python manuscript/make_tables.py --in-dir /tmp/medistruct_28 --out /tmp/medistruct_28/tables
```

## Running on your own authorised data

1. **Extraction (Part A).** Serve a model behind a **local** OpenAI-compatible endpoint, then run:
   ```bash
   export OPENAI_BASE_URL=http://127.0.0.1:<port>/v1 OPENAI_API_KEY=EMPTY
   cd part_a_extraction/Codes
   python mimic_discharge_loader.py --input /path/to/your/discharge.csv.gz --out ../../data_local/inputs_5k.jsonl --limit 5000
   python run_extract.py --inputs ../../data_local/inputs_5k.jsonl --out_dir ../../data_local/extract_run \
     --schema configs/schema_v2_spans.json --prompt prompts/extract_discharge_v2.txt --model Qwen/Qwen2.5-7B-Instruct
   ```
   Clinical text must only be sent to an endpoint you control. Never point `OPENAI_BASE_URL` at a third-party service when processing MIMIC data (see docs/DATA_ACCESS.md).
2. **Roundtrip.** `run_roundtrip_vllm.py` followed by `roundtrip_eval.py`. Commands are in docs/REPRODUCIBILITY.md.
3. **Agentic audit (Part B).** Generate cases and tasks, serve an agent model with tool calling, then run `agents/run_agent.py`. Commands are in docs/REPRODUCIBILITY.md.

## Known limitations

- **Restricted experiments:** they cannot be reproduced end to end without MIMIC-IV-Note access. Some historical metadata was not retained (docs/REPRODUCIBILITY.md).
- **Reconstructed script:** `reconstructed/gold50_micro_summary.py` is a reconstruction and is not claimed to be identical to the lost original.
- **Excluded held-out task:** task T29 audits a ten-note pilot case whose provenance could not be verified. It is excluded from the primary analysis and cannot be run from this release. It remains in the task manifest so that task numbering is unchanged.
- **Agent re-scoring from raw runs:** this needs the saved agent run directories, which are not distributed. The released run-level scores reproduce the aggregate results exactly.
- **Roundtrip comparability:** the original extraction used JSON-schema-constrained decoding (500 output tokens; 400 in one retry pass). The roundtrip re-extraction used unconstrained chat output (1,100 tokens) with a prompt that cannot be uniquely identified among three compatible templates. These differences can affect measured roundtrip consistency.

## License

**No license has been granted yet.** A license decision by the authors and their institutions is pending; see [LICENSE-PENDING.md](LICENSE-PENDING.md). Until then, the code is provided for inspection and research reproducibility only, and all rights are reserved.

## Citation

See [CITATION.cff](CITATION.cff).
