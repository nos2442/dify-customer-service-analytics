#!/bin/sh
set -eu

# Launch one course model at a time on the 10 GB VM.
# Usage: sh launch_course_models.sh qwen2.5-instruct
#        sh launch_course_models.sh deepseek-r1-distill-qwen
# Stop an existing model with: xinference terminate --model-uid MODEL_NAME
# (Run that CLI inside the xinference container.)
model=${1:-qwen2.5-instruct}
case "$model" in
  qwen2.5-instruct)
    quantization=q4_k_m
    file=qwen2.5-1.5b-instruct-q4_k_m.gguf
    ;;
  deepseek-r1-distill-qwen)
    quantization=Q4_K_M
    file=DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf
    ;;
  *) echo "Choose qwen2.5-instruct or deepseek-r1-distill-qwen" >&2; exit 2 ;;
esac

compose_file=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/xinference-compose.yaml
test -r "/home/software/models/$file"

# The v1.17.1 built-in specs use the string 1_5, not the float 1.5.
# --model-path bypasses the download/cache step and accepts a GGUF file.
docker compose -f "$compose_file" exec -T xinference xinference launch \
  --endpoint http://127.0.0.1:9997 \
  --model-name "$model" --model-uid "$model" \
  --model-engine llama.cpp --size-in-billions 1_5 \
  --model-format ggufv2 --quantization "$quantization" \
  --model-path "/models/$file" --n-gpu none \
  --n_gpu_layers 0 --n_ctx 4096 --n_parallel 1 --n_threads 6 \
  --use_mmap true --use_mlock false

# This CLI also finishes polling when a launch reaches ERROR; check READY.
docker compose -f "$compose_file" exec -T xinference python -c \
  'import sys; from xinference.client import RESTfulClient; c=RESTfulClient("http://127.0.0.1:9997"); states=c.get_instance_info(sys.argv[1], sys.argv[1]); assert states and all(s["status"] == "READY" for s in states), states; print(sys.argv[1] + " READY")' \
  "$model"
