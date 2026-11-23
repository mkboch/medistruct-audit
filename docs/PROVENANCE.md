# File provenance

| Path | Origin |
|---|---|
| `part_a_extraction/Codes/**` | Historical research code, byte-identical to the March 2026 files **except** one docstring line in `baselines/baseline_meds_section_v2.py` (line 92), where an example medication string in the note format was replaced by a synthetic one; behaviour is unchanged. Hashes are in `docs/MANIFEST.sha256`. |
| `part_a_extraction/reconstructed/gold50_micro_summary.py` | **Reconstructed** 2026-11-23. The original summary script was not preserved. Validated against the restricted artifacts; see its header. |
| `part_b_agentic_audit/**` | Code written for the November 2026 agentic study, unmodified **except** the files listed below. |
| `part_b_agentic_audit/evaluation/tasks/build_tasks.py` | Modified for release: restricted pilot case removed; private path replaced by `MEDSTRUCT_HIST_DIR`; writes `BUILD_MANIFEST.json` instead of overwriting the historical `FROZEN_MANIFEST.json`. |
| `part_b_agentic_audit/tests/test_sandbox_mcp.py`, `tests/sandbox_probe.py` | Modified for release: probe synthetic cases and generic locations instead of the authors' server paths. |
| `part_b_agentic_audit/agents/sandbox.py` | Modified for release (also: server hostname removed from a comment): the Python environment used inside the sandbox defaults to the running interpreter's environment (overridable with `AUDIT_VENV`) instead of a hard-coded path. |
| `part_b_agentic_audit/agents/run_agent.py` | Modified for release: the OpenClaw version lookup no longer fails when OpenClaw is not installed (needed to run the deterministic condition A alone). |
| `part_b_agentic_audit/environment/serve_audit_model.sh` | Modified for release: model and vLLM paths are parameters, not the authors' local paths. |
| `part_b_agentic_audit/evaluation/tasks/FROZEN_MANIFEST.json` | Historical. These hashes were recorded before the held-out runs and refer to the research copies (including the removed restricted files), so they intentionally do not all match this release. |
| `part_b_agentic_audit/runs/EXP_heldout/scores_summary.json`, `runs/model_selection/*.json` | Historical aggregate results (no per-run records). |
| `tests/test_part_a_smoke.py`, `docs/*`, `README.md`, `requirements/*`, `CITATION.cff`, `LICENSE-PENDING.md`, `.gitignore` | Written for this release. |

## Historical files not included
| Historical file | Reason |
|---|---|
| `Codes/eval/eval_gold50_ours_v2.py` | Empty file. |
| `Codes/eval/normalize_map.json` | Synonym map built after viewing the reference labels; not used for reported results; may reflect label content. |
| `Codes/run_pipeline_vllm.py` | Early prototype with a broken import; not used for reported results. |
| `Codes/run_roundtrip.py`, `run_regen.py`, `run_regen_deterministic.py`, `regen_to_inputs.py` | Earlier roundtrip variants used during development; not used for reported results. |
| `Codes/run_reproducibility*.py`, `run_repro_k.py` | Repeat-run experiment not reported in the paper. |
| `Codes/run_verify.py`, `mine_worst_cases.py` | Manual-inspection helpers that write note excerpts; not used for reported results. |
