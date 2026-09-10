#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

if [[ "$#" -ne 2 ]]; then
    echo "usage: $0 DATASET_ROOT OUTPUT_DIR" >&2
    exit 2
fi

dataset_root="$(realpath -m -- "$1")"
output_dir="$(realpath -m -- "$2")"
test_manifest="${dataset_root}/test-manifest.jsonl"
private_gold="${dataset_root}/evaluator_private/private-gold-v1/private-gold.jsonl"

test -f "${test_manifest}" || {
    echo "missing test manifest: ${test_manifest}" >&2
    exit 2
}
test -f "${private_gold}" || {
    echo "missing evaluator-private sidecar: ${private_gold}" >&2
    exit 2
}

case_count="$(grep -cve '^[[:space:]]*$' "${test_manifest}")"
if [[ "${case_count}" -lt 1 ]]; then
    echo "test manifest is empty: ${test_manifest}" >&2
    exit 2
fi

cd -- "${REPO_ROOT}"
python scripts/prepare_agent_test_release.py \
    --dataset-root "${dataset_root}" \
    --test-manifest "${test_manifest}" \
    --private-gold-sidecar "${private_gold}" \
    --output-dir "${output_dir}" \
    --limit "${case_count}" \
    --balanced-by ""

echo "case_count=${case_count}"
echo "benchmark=${output_dir}/runtime-release/runtime_input/cases.jsonl"
