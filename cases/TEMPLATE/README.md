---
title: One-line summary of the Domux result
author: your-github-handle
date: 2026-08-24
category: smart-home-command
testedRevision: 40-character-hugging-face-commit-sha
runtime: vllm-version-or-sglang-version
hardware: gpu-model-vram-and-os
downloadSource: huggingface
channels:
  - https://huggingface.co/iFlytekOpenSource/Domux/discussions/REPLACE_ME
---

# <Case title>

> Replace every placeholder. The channels list must contain only a public
> Discussion URL under iFlytekOpenSource/Domux. Never paste an HF token,
> private endpoint, personal cache path, or private household data.

## Task / 真实任务

What smart-home command-understanding problem did you solve? Explain why it was
useful outside a staged demo.

说明真实任务、目标用户、设备和预期结构化结果。

## Hugging Face download / 下载证据

- Model: iFlytekOpenSource/Domux
- Revision: <same commit SHA as testedRevision>
- Command:

      hf download iFlytekOpenSource/Domux --revision <commit-sha>

- Snapshot or derived artifact size: <size; redact personal cache path>

Explain whether this was a full BF16 snapshot, quantized artifact, or another
derivative and how it was produced. Do not commit model weights to GitHub.

## Setup / 环境

- Runtime and version:
- GPU, VRAM, CPU, RAM, and OS:
- Precision or quantization:
- Important inference or evaluation parameters:

## What happened / 实际过程

Show representative inputs and raw Domux outputs. Include a screenshot or log
excerpt that demonstrates a real run using the downloaded revision.

![Domux run evidence](preview.png)

## Results / 结果

For metrics, include the sample size, measurement method, warm-up policy, and
enough detail to reproduce the number. Include failure cases and limitations,
not only successful examples.

| Metric | Result | Method |
|---|---:|---|
| Example metric | REPLACE_ME | REPLACE_ME |

## Why it mattered / 价值

What became faster, safer, more accurate, more accessible, or easier to deploy?

## Published Hugging Face Discussion / 公开 Discussion

The following URL must exactly match the channels frontmatter:

- https://huggingface.co/iFlytekOpenSource/Domux/discussions/REPLACE_ME

## Safety, privacy, and licensing / 安全、隐私与许可

- Confirm tokens and personal cache paths are removed.
- Confirm prompts contain no private household or business information.
- Identify the license or permission for any added dataset.
- Describe ambiguity handling for high-risk smart-home actions.

## Notes and gotchas / 踩坑记录

Document anything that would save the next person time.
