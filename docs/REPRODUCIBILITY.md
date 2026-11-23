# Reproducibility

This page documents the two experiments separately and says what can and cannot be reproduced from this code-only release. Each fact carries a provenance label:

- **[confirmed]**: established by a contemporaneous record (run receipts, server logs or vLLM usage records written at run time, model-cache provenance).
- **[inferred]**: established by elimination or by consistency with saved outputs.
- **[unknown]**: not recoverable.

## Part A: historical extraction study (March 2026)

### Data
- **Source:** MIMIC-IV-Note, `discharge` table, version 2.2 [inferred].
  - The local copy had 331,793 rows from 145,914 subjects with v2.2 note identifiers. The PhysioNet v2.2 page reports 331,794 / 145,915.
  - No download record or checksum was retained.
- **Sample:** the first 5,000 rows in file order, via `mimic_discharge_loader.py --limit 5000`. That is 5,000 unique notes from 2,249 patients [confirmed]. No other inclusion or exclusion criteria.

### Model and serving
| Item | Value | Status |
|---|---|---|
| Model | `Qwen/Qwen2.5-7B-Instruct` (recorded in all 5,000 receipts) | confirmed |
| Checkpoint | Hugging Face revision `a09a35458c702b33eeacc393d103063234e8bc28`. This was the only snapshot ever cached, downloaded by vLLM at its first start on 2026-03-05; the snapshot's weight index was fetched within the same second as that start. | confirmed (cache provenance) |
| Precision | bfloat16 (model config and vLLM usage records) | confirmed |
| Serving framework | vLLM **0.16.0**. vLLM's own usage records show every Qwen2-architecture engine start between 2026-03-05 and 2026-03-15 was 0.16.0; the receipt timestamps of every extraction and roundtrip pass fall within those engine sessions. | confirmed |
| Server stack (from the same records) | CUDA 12.8; tensor parallel 1; pipeline parallel 1; one NVIDIA H100 80GB; prefix caching on; GPU memory utilisation 0.9 (main pass), 0.85–0.6 (retry passes) | confirmed |
| Context length | 8,192 tokens. The server's own overflow error text is stored in the failed note's receipt. | confirmed |
| Endpoint | local OpenAI-compatible server, `127.0.0.1:8001` (receipts) | confirmed |
| Python environment | Client and server packages pinned in `requirements/extraction.txt` and `requirements/vllm-server-historical.txt`. These are freezes of environments that still exist and contain vLLM 0.16.0. Which of two such environments ran the server is not recorded. | inferred |

### Extraction settings
- **Prompt and schema:** `prompts/extract_discharge_v2.txt` (SHA-256 `ec87f0df9e824665…`) and `configs/schema_v2_spans.json` (`27a26795bdedeea7…`). These are byte-identical to the hashes in all 5,000 receipts [confirmed].
- **Decoding:** temperature 0, top-p 1.0, `response_format={"type":"json_schema"}`; system message "Return ONLY valid JSON that matches the schema. No extra text." [confirmed]
- **Extraction view:**
  - section-prioritised reassembly of 14 headers;
  - whitespace normalisation;
  - head/tail truncation (70%/30%) above the character budget;
  - 37 views truncated [confirmed].
- **Passes:** the final result set merges three passes [confirmed]:

  | Notes | Max output tokens | View budget | In-run retry | HTTP retries |
  |---|---|---|---|---|
  | 4,670 | 500 | 26,000 chars | 16,000-char view / 900 tokens | 4 |
  | 242 | 500 | 18,000 chars | — | — |
  | 88 | 400 | 16,000 chars | 12,000 chars / 600 tokens | 8 |

  - The 242-note pass has **no surviving run-metadata file**; only its receipts record its settings.
  - The second pass's metadata records 6 HTTP retries.
- **Failure definition:** `json_ok ∧ schema_valid`. 4,994 / 5,000 notes passed; the failures were 5 malformed JSON outputs and 1 context overflow [confirmed].
- **Receipt rewrite:** receipt files were rewritten on 2026-03-14, after extraction. Extraction timestamps inside the receipts are 03-06 to 03-08. The rewrite is consistent with a re-computation of receipt-level metrics, and the extraction fields are unaffected [inferred].

### Evidence spans and grounding
Evidence spans were not produced by the model (the prompt requests an empty list). `run_extract.generate_evidence_spans` anchors each item at its first case-insensitive occurrence in the extraction view, keeping at most 12 spans per note and at most 4 for medications. Metric definitions are in `utils.evidence_span_checks`. The uncapped exact-text support reported in the paper is computed by `part_b_agentic_audit/tools/audit_core.py: measure_exact_text_support`.

### Reference set (gold50)
- **Source:** 50 notes from the run. The reference lists were prepared at MGH; annotator qualifications, protocol and adjudication are still to be documented.
- **Not distributed:** the labels and the per-note predictions.
- **Scoring:** `Codes/eval/eval_gold50_baselines.py` (per note) and `reconstructed/gold50_micro_summary.py` (micro-averaged, reconstructed).

### Roundtrip (structured → text → structured)
| Item | Value | Status |
|---|---|---|
| Script | `Codes/run_roundtrip_vllm.py`. Output layout, metadata keys, CSV header and default token limits match the saved run exactly. | confirmed |
| Notes | the 50 reference-set notes, read from their Part A structured outputs | confirmed |
| Model | Qwen2.5-7B-Instruct, same server, for both steps | confirmed |
| Decoding | temperature 0, top-p 1.0; 1,400 tokens (regeneration) and 1,100 tokens (re-extraction) | confirmed |
| Regeneration prompt | `prompts/regen_v1.txt` (SHA-256 `145987be649026dc…`). It is the only template containing the `<<<STRUCTURED_JSON>>>` placeholder the script substitutes. | inferred (by elimination) |
| Re-extraction prompt | **Not recorded.** All 50 raw outputs have an empty evidence list, which excludes `extract_v1.txt`. The remaining candidates are `extract_discharge_v2.txt`, `extract_discharge_roundtrip_v1.txt` and `extract_discharge_roundtrip_v2_highcaps.txt`; none can be singled out from the saved artifacts. | unknown |
| Re-extraction mode | plain chat completion without JSON-schema decoding; output parsed, normalised and schema-validated afterwards (50/50 valid) | confirmed |
| Metric | per-note Jaccard of lower-cased, stripped item sets (1.0 if both empty), averaged over notes. `roundtrip_eval.py` reproduces the saved summary file byte for byte. | confirmed |

No prompt file was modified after 2026-03-06, so the templates in this repository are the versions that existed when the roundtrip ran on 2026-03-15.

### What you can reproduce
- **Without MIMIC access:** the full code path on synthetic inputs (`tests/test_part_a_smoke.py`).
- **With your own credentialed MIMIC-IV-Note copy and a local vLLM server:** the full pipeline. Exact numerical agreement is not guaranteed:
  - GPU numerics and vLLM scheduling can change outputs even at temperature 0;
  - the 242-note pass settings must be taken from this page;
  - the reference labels are not distributed.

## Part B: agentic quality assurance (November 2026)

| Item | Value |
|---|---|
| Agent framework | OpenClaw 2026.11.6 (commit 06afb10), `openclaw agent exec` (embedded; no gateway, no network listener) |
| Agent model | Qwen3.5-122B-A10B-FP8, revision `a099dee70ccfcd8d5dda56aaa0b60cb8ecadabc9` |
| Agent model serving | vLLM 0.24.0; TP 2 × PP 3; 131,072-token context; `--reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder` |
| Agent sampling and limits | thinking on; temperature 0.6, top-p 0.95, top-k 20 (sent per request); 16,384 output tokens; 40 tool calls; 900 s timeout |
| Tools | 12 read-only tools + report submission (`tools/audit_core.py` v1.0.0, `tools/mcp_server.py`) |
| Sandbox | Landlock ABI ≥ 4 (`agents/landlock_exec.py`): read-only case artifacts, write only to the run directory, no TCP, environment allow-list |
| Instructions | `prompts/agent_protocol_v2.md` (v1 was used only on development tasks) |
| Conditions | A deterministic checklist (rules v1.0.0); B agent with full tools; D agent with metadata only |
| Tasks | 6 development tasks; 29 held-out tasks (18 synthetic cases, 10 focused questions, 1 restricted real case), 3 trials for B and D |
| Statistics | trials averaged within each task; 2,000-resample task-level bootstrap (seed 0); paired bootstrap for differences (`evaluation/scoring/aggregate.py`) |

Reproduce the synthetic part:

```bash
cd part_b_agentic_audit
python evaluation/tasks/build_tasks.py                          # synthetic cases, task sets, references
python agents/run_agent.py --exp MY_EXP --cond A --tasks evaluation/tasks/tasks_heldout.json
export AUDIT_LLM_PORT=<port> AUDIT_LLM_MODEL_ID=<served-name> AUDIT_VLLM_VERSION=<version>
python agents/run_agent.py --exp MY_EXP --cond B --tasks evaluation/tasks/tasks_heldout.json --trials 3
python agents/run_agent.py --exp MY_EXP --cond D --tasks evaluation/tasks/tasks_heldout.json --trials 3
python evaluation/scoring/aggregate.py --exp MY_EXP --tasks evaluation/tasks/tasks_heldout.json
```

**Restricted parts of Part B:**
- Development tasks D01–D04 audit the restricted real 5,000-note run. They require your own Part A outputs in the historical layout; point `MEDSTRUCT_HIST_DIR` to them.
- Held-out task T29 audits a restricted external pilot. It cannot be run from this release.

**Shipped results:** `runs/EXP_heldout/scores_summary.json` holds the paper's aggregate results; per-run traces are not distributed. `manuscript/make_tables.py` regenerates Tables 2–6 from a complete run directory.

### Synthetic fixtures
The held-out synthetic cases are generated deterministically by `evaluation/fixtures/make_fixtures.py` from template vocabulary written for this project. They contain no patient data and no text copied from any clinical record. Reference answers derive from the injected faults, independently of the audit tools.
