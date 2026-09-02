#!/usr/bin/env bash
set -euo pipefail

echo "collected_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "os_kernel=$(uname -srm)"
echo "python=$(python --version 2>&1)"
echo "visible_gpu=${CUDA_VISIBLE_DEVICES:-0}"
echo "gpu_inventory:"
nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv,noheader
echo "cuda_runtime_reported_by_nvidia_smi:"
nvidia-smi | sed -n '1,3p' | sed 's/[[:space:]]*$//'
echo "package_versions:"
python - <<'PY'
from importlib.metadata import PackageNotFoundError, version

for package in (
    "vllm",
    "transformers",
    "huggingface-hub",
    "requests",
    "torch",
    "nvidia-cuda-nvcc",
):
    try:
        print(f"{package}={version(package)}")
    except PackageNotFoundError:
        print(f"{package}=NOT_INSTALLED")
PY
