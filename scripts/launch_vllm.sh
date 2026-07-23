#!/bin/bash
set -e

# Launch vLLM as an external service (port :8000)
# This is optional; the app can start vLLM internally if needed

if [ ! -d ".venv" ]; then
    echo "Error: .venv not found. Run: python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'"
    exit 1
fi

source .venv/bin/activate

# Ensure vllm is installed
if ! python -c "import vllm" 2>/dev/null; then
    echo "vllm not installed. Run: pip install -e '.[dev]'"
    exit 1
fi

# Create models directory
mkdir -p models

# Export HF cache to local models directory (keeps weights with the repo)
export HF_HOME="$(pwd)/models/huggingface"

# Start vLLM API server
# Using quantized Qwen2.5-7B to fit in available GPU VRAM (4-bit = ~3.5GB vs 7B fp16 = ~15GB)
# --model Qwen/Qwen2.5-7B-Instruct-GPTQ-Int4: 4-bit quantized version (much smaller)
# --tensor-parallel-size 1: Single GPU (no sharding)
# --gpu-memory-utilization 0.8: Can be higher with quantized model
# --max-model-len 2048: Context window
# --port 8000: Listen on localhost:8000
echo "Starting vLLM API server on http://0.0.0.0:8000"
echo "Model: Qwen/Qwen2.5-7B-Instruct-GPTQ-Int4 (4-bit quantized)"
echo "First start will download model (~3.5GB)…"
echo ""

# Suppress verbose HF + vLLM logs; only show errors and API ready message
VLLM_LOG_LEVEL=ERROR HF_HUB_VERBOSITY=critical \
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-7B-Instruct-GPTQ-Int4 \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.8 \
  --max-model-len 2048 \
  --port 8000 \
  2>&1 | grep -E "(Loaded|ready|error|Error|ERROR)" || true
