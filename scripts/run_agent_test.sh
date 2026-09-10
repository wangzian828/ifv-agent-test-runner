#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

usage() {
    echo "usage: $0 --benchmark FILE --output-dir DIR [--concurrency N] [--maximum-attempts N]" >&2
}

benchmark=""
output_dir=""
concurrency="10"
maximum_attempts="4"
while (($#)); do
    case "$1" in
        --benchmark) benchmark="$2"; shift 2 ;;
        --output-dir) output_dir="$2"; shift 2 ;;
        --concurrency) concurrency="$2"; shift 2 ;;
        --maximum-attempts) maximum_attempts="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage; exit 2 ;;
    esac
done

[[ -n "${benchmark}" && -n "${output_dir}" ]] || { usage; exit 2; }
export OMP_NUM_THREADS=1
export OCR_BACKEND=baidu
export BROWSE_FETCH_PROVIDER=jina
export VISUAL_SEARCH_PROVIDER=serper_lens
export IMAGE_UPLOAD_PROVIDER=oss
export QWEN_LOCAL_API_KEY="${QWEN_TEACHER_API_KEY:-}"
export BROWSE_EXTRACT_PROVIDER=qwen_local
export BROWSE_EXTRACT_MODEL="${QWEN_TEACHER_MODEL:-}"
export BROWSE_EXTRACT_BASE_URL="${QWEN_TEACHER_BASE_URL:-}"
export BROWSE_EXTRACT_API_KEY="${QWEN_TEACHER_API_KEY:-}"
export BROWSE_EXTRACT_WIRE_API=chat_completions

cd -- "${REPO_ROOT}"
python scripts/preflight_agent_test.py
python scripts/run_agent_test_rollout.py \
    --benchmark "${benchmark}" \
    --output-dir "${output_dir}" \
    --profile teacher-qwen-server \
    --concurrency "${concurrency}" \
    --maximum-attempts "${maximum_attempts}"
