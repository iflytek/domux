# RTX 3090 evaluation runbook

This runbook keeps Hugging Face credentials and private server details outside
the repository. Run every command from a clone of your GitHub Fork on the Linux
GPU server.

## 1. One-time account and environment setup

Accept the Gemma terms on the Domux Hugging Face model page, then authenticate
interactively on the server:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r cases/domux-4057-rtx3090-eval/requirements-case.txt
pip install "transformers==5.5.1"
pip install \
  "nvidia-cuda-nvcc==13.0.88" \
  "nvidia-cuda-crt==13.0.88" \
  "nvidia-nvvm==13.0.88"
hf auth login
```

The second install is intentional: vLLM 0.22.0 can otherwise resolve to
Transformers 4.57.x through its optional structured-output dependency, while
Domux uses the newer `gemma4` configuration. This case does not use structured
decoding; it requires Transformers 5.5.1 and verifies the version before model
startup.

The CUDA compiler pins keep FlashInfer JIT on CUDA 13.0. The unpinned CUDA
compiler packages can otherwise resolve to a newer 13.x toolchain than the 580
driver supports. The startup script sets `CUDA_HOME`, `CUDACXX`, and `PATH` only
inside the case process; it does not replace the host's system CUDA toolkit.

Never pass a token on the command line and never enable shell tracing with
`set -x`. Check that the intended GPU is available:

```bash
nvidia-smi
```

The official `vllm==0.22.0` wheel uses CUDA 13, so its NVIDIA driver must be
580 or newer. Driver installation and reboot are host-administration tasks and
must be completed before this runbook when the server has an older driver.

## 2. Download the pinned snapshot and start vLLM

In terminal A:

```bash
source .venv/bin/activate
export CUDA_VISIBLE_DEVICES=0
# Optional only when the server cannot reach huggingface.co directly:
# export HF_ENDPOINT=https://hf-mirror.com
./cases/domux-4057-rtx3090-eval/prepare_and_serve.sh \
  2>&1 | tee cases/domux-4057-rtx3090-eval/artifacts/raw/server_console.log
```

The script downloads the pinned Hugging Face revision
`6c71a32f4d624cadfd9fce9d10240d8068e53456`, records only sanitized metadata
(including the effective download endpoint), and starts vLLM 0.22.0 on
`127.0.0.1:8000`. The private snapshot path stays out of public artifacts.

Wait for the server to report that it is ready before continuing.

## 3. Run smoke, correctness, analysis, and latency

In terminal B on the same server:

```bash
source .venv/bin/activate
export DOMUX_BASE_URL=http://127.0.0.1:8000/v1
export DOMUX_API_KEY=EMPTY
export DOMUX_MODEL=domux
export DOMUX_MAX_WORKERS=20
export DOMUX_REQUEST_TIMEOUT=30
export DOMUX_MAX_TOKENS=256
export DOMUX_EVAL_WARMUP_SAMPLES=5
export DOMUX_LATENCY_WARMUP=20
export DOMUX_LATENCY_SAMPLES=100
export DOMUX_LATENCY_REPEATS=3
./cases/domux-4057-rtx3090-eval/run_pipeline.sh
```

Expected gates:

- All five public smoke examples return valid seven-field outputs; semantic
  matches against the README examples are recorded but do not replace the full evaluation.
- The official run writes exactly 4,057 results with zero API errors.
- Failure analysis produces JSON plus a Markdown report with 10–15 examples.
- The latency run performs 20 warm-ups and 100 measured requests three times
  at concurrency 1, reporting median and nearest-rank P95 end-to-end latency.

Raw JSONL and raw console logs remain ignored under `artifacts/raw/`. Aggregated
JSON, Markdown, environment information, and sanitized console excerpts are
intended for review and commit.

## 4. Review for privacy

Before publishing, search the case directory for secrets, private addresses,
usernames in cache paths, and forbidden weight extensions:

```bash
rg -n -i 'authorization|bearer|hf_|sk-|/home/|/root/|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.' \
  cases/domux-4057-rtx3090-eval \
  -g '!artifacts/raw/**'
find cases/domux-4057-rtx3090-eval -type f \
  \( -name '*.safetensors' -o -name '*.gguf' -o -name '*.pt' -o -name '*.pth' -o -name '*.ckpt' \)
```

Review every match manually. Expected documentation references such as `hf auth
login` are safe; actual token values, private IPs, and personal paths are not.

## 5. Publish in the required order

First generate the Discussion draft from the real artifacts:

```bash
python cases/domux-4057-rtx3090-eval/render_case.py
```

Publish `artifacts/DISCUSSION_DRAFT.md` at the official Domux Hugging Face
Discussions with the title:

```text
[HER Hack-Astron #4] Domux 4057 条开放评测复现：RTX 3090 BF16 性能与失败簇分析
```

Then render and validate the final GitHub case:

```bash
python cases/domux-4057-rtx3090-eval/render_case.py \
  --discussion-url https://huggingface.co/iFlytekOpenSource/Domux/discussions/<number> \
  --author posuizhiyu-maker
python scripts/validate_cases.py --self-test
python scripts/validate_cases.py
```

Commit the case without raw logs or weights. Use this PR title:

```text
[case] domux-4057-rtx3090-eval - 4057-sample BF16 evaluation and failure analysis on RTX 3090
```

The PR description must contain `Ref #20`, not `Closes #20`.
