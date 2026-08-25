#!/usr/bin/env bash
set -euo pipefail

case_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
artifacts_dir="${case_dir}/artifacts"
model_id="iFlytekOpenSource/Domux"
default_revision="6c71a32f4d624cadfd9fce9d10240d8068e53456"
mkdir -p "${artifacts_dir}/raw"

for command_name in python hf nvidia-smi; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "ERROR: missing required command: ${command_name}" >&2
    exit 1
  fi
done

if ! python -c "import importlib.metadata as m, transformers, vllm; assert vllm.__version__ == '0.22.0', vllm.__version__; assert transformers.__version__ == '5.5.1', transformers.__version__; assert m.version('nvidia-cuda-nvcc') == '13.0.88', m.version('nvidia-cuda-nvcc')"; then
  echo "ERROR: activate the pinned vLLM, Transformers, and CUDA 13.0 environment from RUNBOOK.md" >&2
  exit 1
fi

cuda_home="$(python - <<'PY'
import sysconfig
from pathlib import Path

print(Path(sysconfig.get_paths()["purelib"]) / "nvidia" / "cu13")
PY
)"
if [[ ! -x "${cuda_home}/bin/nvcc" ]]; then
  echo "ERROR: CUDA 13 nvcc was not found in the active Python environment" >&2
  exit 1
fi
export CUDA_HOME="${cuda_home}"
export CUDACXX="${cuda_home}/bin/nvcc"
export PATH="${cuda_home}/bin:${PATH}"

driver_version="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n 1 | tr -d '[:space:]')"
driver_major="${driver_version%%.*}"
if [[ ! "${driver_major}" =~ ^[0-9]+$ ]] || (( driver_major < 580 )); then
  echo "ERROR: the official vllm==0.22.0 wheel uses CUDA 13 and requires NVIDIA driver 580 or newer; found ${driver_version}" >&2
  exit 1
fi

hf auth whoami >/dev/null
revision="${DOMUX_REVISION:-${default_revision}}"

if [[ ! "${revision}" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo "ERROR: resolved revision is not a 40-character commit SHA" >&2
  exit 1
fi

echo "Downloading pinned Hugging Face revision ${revision}"
snapshot_path="$(python - "${model_id}" "${revision}" <<'PY'
import sys

from huggingface_hub import snapshot_download

print(snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2]))
PY
)"
snapshot_bytes="$(python - "${snapshot_path}" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
print(sum(path.stat().st_size for path in root.rglob("*") if path.is_file()))
PY
)"

export DOMUX_REVISION_RESOLVED="${revision}"
export DOMUX_SNAPSHOT_BYTES="${snapshot_bytes}"
export DOMUX_DOWNLOAD_ENDPOINT="${HF_ENDPOINT:-https://huggingface.co}"
python - "${artifacts_dir}/download_metadata.json" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

payload = {
    "created_at_utc": datetime.now(timezone.utc).isoformat(),
    "model": "iFlytekOpenSource/Domux",
    "revision": os.environ["DOMUX_REVISION_RESOLVED"],
    "download_method": "huggingface_hub.snapshot_download(repo_id, revision=<testedRevision>)",
    "snapshot_bytes": int(os.environ["DOMUX_SNAPSHOT_BYTES"]),
    "download_source": os.environ["DOMUX_DOWNLOAD_ENDPOINT"],
    "artifact_type": "full BF16 snapshot",
    "runtime": "vllm-0.22.0",
    "transformers": "5.5.1",
    "cuda_compiler": "13.0.88",
    "precision": "bfloat16",
    "visible_gpu": os.environ.get("CUDA_VISIBLE_DEVICES", "0"),
}
Path(sys.argv[1]).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
echo "Starting vLLM on GPU ${CUDA_VISIBLE_DEVICES}; the private snapshot path is not printed"
exec python -m vllm.entrypoints.openai.api_server \
  --model "${snapshot_path}" \
  --served-model-name domux \
  --host 127.0.0.1 \
  --port "${DOMUX_PORT:-8000}" \
  --dtype bfloat16 \
  --max-model-len 2048 \
  --gpu-memory-utilization 0.9
