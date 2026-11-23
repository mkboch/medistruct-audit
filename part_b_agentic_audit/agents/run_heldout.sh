#!/usr/bin/env bash
# Frozen held-out evaluation driver (conditions A, B, D). Detached; resumable (skips finished runs).
cd "$(dirname "$0")/.." && . environment/.venv/bin/activate
export AUDIT_LLM_PORT=8097 AUDIT_LLM_MODEL_ID=qwen35-test AUDIT_LLM_REVISION=a099dee70ccfcd8d5dda56aaa0b60cb8ecadabc9 AUDIT_VLLM_VERSION=0.24.0
T=evaluation/tasks/tasks_heldout.json
python agents/run_agent.py --exp EXP_heldout --cond A --tasks $T --trials 1
for k in 1 2 3; do
  python agents/run_agent.py --exp EXP_heldout --cond B --tasks $T --trials $k
  python agents/run_agent.py --exp EXP_heldout --cond D --tasks $T --trials $k
done
python evaluation/scoring/aggregate.py --exp EXP_heldout --tasks $T
echo HELDOUT_DONE
