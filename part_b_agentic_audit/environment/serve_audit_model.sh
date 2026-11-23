#!/usr/bin/env bash
# Serve an agent LLM on ONE free GPU, localhost only (template; adapt paths to your system).
# The held-out experiment in the paper used Qwen3.5-122B-A10B-FP8 (snapshot a099dee70ccfcd8d5dda56aaa0b60cb8ecadabc9)
# on six GPUs with:  --tensor-parallel-size 2 --pipeline-parallel-size 3 --max-model-len 131072
#   --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder --generation-config vllm
# and vLLM 0.24.0; sampling (T=0.6, top_p=0.95, top_k=20) was sent per request by the harness.
#
# Usage: VLLM_BIN=/path/to/vllm MODEL_PATH=/path/to/snapshot environment/serve_audit_model.sh <gpu_index> [port]
set -euo pipefail
GPU=${1:?gpu index}
PORT=${2:-8120}
VLLM_BIN=${VLLM_BIN:?set VLLM_BIN to the vllm executable}
MODEL_PATH=${MODEL_PATH:?set MODEL_PATH to a local model snapshot directory}
SERVED_NAME=${SERVED_NAME:-audit-llm}
LOG=$(dirname "$0")/../runs/vllm_audit_server_$(date +%Y%m%d_%H%M%S).log
mkdir -p "$(dirname "$LOG")"
if ss -ltn | grep -q ":$PORT "; then echo "port $PORT busy" >&2; exit 1; fi
USED=$(nvidia-smi -i "$GPU" --query-gpu=memory.used --format=csv,noheader,nounits)
if [ "$USED" -gt 5000 ]; then echo "GPU $GPU has ${USED} MiB in use; refusing" >&2; exit 1; fi
export CUDA_VISIBLE_DEVICES=$GPU HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# Private HOME so JIT kernel caches do not race with other vLLM processes sharing the home directory.
PRIV_HOME=$(cd "$(dirname "$0")" && pwd)/vllm_home; mkdir -p "$PRIV_HOME"; export HOME=$PRIV_HOME
nohup "$VLLM_BIN" serve "$MODEL_PATH" \
  --served-model-name "$SERVED_NAME" \
  --host 127.0.0.1 --port "$PORT" \
  --tensor-parallel-size 1 --max-model-len 65536 --gpu-memory-utilization 0.85 --max-num-seqs 16 \
  --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --seed 0 > "$LOG" 2>&1 &
echo "pid=$! log=$LOG"
