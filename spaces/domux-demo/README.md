---
title: Domux Demo
emoji: 🏠
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: 6.28.0
python_version: "3.12"
app_file: app.py
pinned: false
license: apache-2.0
short_description: Smart-home commands to structured slots with Domux
models:
  - iFlytekOpenSource/Domux
suggested_hardware: zero-a10g
---

# Domux Demo Space

A Gradio demo for [Domux](https://github.com/iflytek/domux). Type a smart-home command and the demo shows the raw model output, the parsed `action|device|attribute|value|unit|room|floor` slots, whether every line has the 7 fields, and the latency.

This directory is the source of the Hugging Face Space. `.github/workflows/sync-space.yml` uploads it to the Space whenever it changes on `main`.

## Backends

| Mode | When | How it runs |
| --- | --- | --- |
| Local model (default) | `DOMUX_API_BASE` is not set | Loads `iFlytekOpenSource/Domux` with transformers in BF16, pinned to the revision used by the full 4,057-sample evaluation (`cases/domux-4057-full-eval`). On a ZeroGPU Space each request gets a GPU through `@spaces.GPU`. |
| OpenAI-compatible endpoint | `DOMUX_API_BASE` is set | Sends the command to a vLLM or SGLang server (see the main README's Deployment section). The Space itself can then run on free CPU hardware. |

Environment variables (Space **Settings → Variables and secrets**):

| Variable | Default | Description |
| --- | --- | --- |
| `DOMUX_MODEL_ID` | `iFlytekOpenSource/Domux` | Model repo for the local backend |
| `DOMUX_REVISION` | `6c71a32f…` | Model revision for the local backend |
| `DOMUX_API_BASE` | | OpenAI-compatible base URL, e.g. `https://host:8000/v1`. Switches to the endpoint backend |
| `DOMUX_API_MODEL` | `domux` | Served model name on that endpoint |
| `DOMUX_API_KEY` | | Bearer token for that endpoint (store as a secret) |

The model needs about 10 GB for BF16 weights, so the local backend needs a GPU Space (ZeroGPU or a dedicated GPU). It will start on CPU hardware, but each request takes a long time.

## Run locally

```bash
cd spaces/domux-demo
pip install gradio==6.28.0 -r requirements.txt
python app.py                                   # local model; needs a GPU with 20 GB+ VRAM
DOMUX_API_BASE=http://localhost:8000/v1 python app.py   # or point at a running vLLM/SGLang server
```

Open http://127.0.0.1:7860.

## Set up the Space (maintainers)

1. Create a Gradio Space, e.g. `iFlytekOpenSource/Domux-Demo`, and select ZeroGPU hardware (or leave it on CPU and set `DOMUX_API_BASE`).
2. In this GitHub repository add a secret `HF_TOKEN` (a token with write access to the Space) and, if the Space id differs from `iFlytekOpenSource/Domux-Demo`, a variable `HF_SPACE_ID`.
3. Run **Sync Hugging Face Space** from the Actions tab once. Later changes to `spaces/domux-demo/` on `main` sync automatically.

## License

The demo code is Apache-2.0. The Domux model weights are a Gemma derivative governed by the [Gemma Terms of Use](https://ai.google.dev/gemma/terms); see [NOTICE](https://github.com/iflytek/domux/blob/main/NOTICE).
