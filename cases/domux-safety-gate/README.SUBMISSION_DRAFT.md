---
title: Domux safety gate blocks malformed and high-risk smart-home commands
author: yangmengze608-afk
date: 2026-08-25
category: safety-boundary-integration
testedRevision: 6c71a32f4d624cadfd9fce9d10240d8068e53456
runtime: transformers [RECORD_FROM_EVIDENCE_METADATA]
hardware: Google Colab free Tesla T4, 15 GB VRAM, Linux [RECORD_FROM_EVIDENCE_METADATA]
downloadSource: huggingface
channels:
  - [INSERT_PUBLIC_DOMUX_DISCUSSION_URL]
---

# Domux execution safety gate: fail closed on malformed and high-risk commands

> Pre-publication draft. Replace only bracketed evidence fields after the public
> Hugging Face Discussion exists and `evidence/` has passed `verify_evidence.py`.
> Do not add model weights, tokens, cache paths, or private household data.

## Problem

Domux converts natural-language smart-home requests into a seven-field control format. A
valid-looking structured output is not, by itself, authorization to act. This integration
adds a conservative execution gate between Domux and a hypothetical dispatcher:

- allow explicit low-consequence controls;
- require confirmation for perimeter access, heating appliances, utility controls,
  broad scope, ambiguous pronouns, and out-of-range temperatures;
- block safety-system disablement, bypass requests, gas-valve activation, unbounded heat,
  and malformed/unsupported structured output.

This is a research prototype, not a safety certification or a claim that Domux itself is a
safety model.

## Real Domux run

- Model: `iFlytekOpenSource/Domux`
- Hugging Face revision: `6c71a32f4d624cadfd9fce9d10240d8068e53456`
- Download: `snapshot_download` in `run_transformers.py`
- Runtime: `transformers` + `BitsAndBytesConfig` NF4, greedy generation
  (`do_sample=False`, `max_new_tokens=256`, seed `20260825`)
- Hardware: Google Colab free Tesla T4
- Input: 48 original, balanced, English synthetic smart-home commands under CC0-1.0

Run the reproducible notebook `colab_run.ipynb` on a GPU, then retain only the small
`evidence/` bundle. `run_transformers.py` records the exact package versions, seed,
dataset SHA-256, GPU, precision, and snapshot size in metadata.

## Evaluation design and Domux's role

The reported end-to-end decision is produced by **both** Domux output and the external
safety policy:

1. Domux parses the command into a seven-field output.
2. The integration fail-closes if that output is empty, malformed, or uses an unsupported
   action.
3. The policy evaluates the source command for high-consequence and ambiguity triggers
   before a hypothetical dispatch.

Therefore the format-compliance figure is the most direct Domux-specific measurement in
this case. The three-class gate metrics measure this combined integration, not independent
Domux safety or semantic-understanding performance. Several high-risk device requests are
deliberately outside Domux's documented device inventory; their role is to test safe refusal
at the execution boundary, not to score Domux on unsupported devices.

## Results

Fill this table only from `evidence/safety_report.json` after it passes
`verify_evidence.py`.

| Metric | Result | Meaning |
|---|---:|---|
| Samples | 48 | Balanced synthetic policy set; 16 per class |
| Domux format compliance | [RECOMPUTED] | Seven fields plus supported action |
| End-to-end gate decision accuracy | [RECOMPUTED] | Domux output + safety policy vs hand labels |
| End-to-end Macro F1 | [RECOMPUTED] | Macro average across allow/confirm/block |
| High-risk intercept recall | [RECOMPUTED] | 32 confirm/block labels not passed as allow |
| Dangerous false-allow rate | [RECOMPUTED] | High-risk labels passed as allow / 32 |
| Safe false-intervention rate | [RECOMPUTED] | allow labels not passed as allow / 16 |
| Gate latency mean/P95 | [RECOMPUTED] | Policy only, excludes model generation |

Include the exact confusion matrix and per-class precision/recall/F1 from the verified report.

## Representative raw outputs and failure cases

Include two representative raw outputs from `evidence/domux_raw.jsonl`, one accepted and one
blocked. Include at least one malformed-output example and explain that it was blocked by the
wrapper rather than attributed to a successful Domux parse.

## Limitations

- The dataset is small, synthetic, English-only, hand-authored, and policy-aligned; it is not
  a production distribution or a safety certification corpus.
- The gate uses explicit, auditable rules over the source command. A strong score does not show
  that Domux independently understands safety intent.
- The current policy does not replace inventory validation, authentication, user-specific
  authorization, state checking, rate limits, audit logs, or a real confirmation UX.
- The test does not evaluate Chinese, ASR noise, multi-user context, long-horizon automation,
  or physical-device behavior.

## Reproduction

```bash
python -m unittest -v test_safety_gate.py test_dataset.py test_evaluate_safety.py
python run_transformers.py --dataset example_safety_commands.jsonl \
  --output evidence/domux_raw.jsonl --quantization nf4 --warmup 2 --seed 20260825
python evaluate_safety.py --dataset example_safety_commands.jsonl \
  --responses evidence/domux_raw.jsonl --output evidence/safety_report.json
python verify_evidence.py --dataset example_safety_commands.jsonl \
  --responses evidence/domux_raw.jsonl --report evidence/safety_report.json
```

## Published Hugging Face Discussion

[INSERT_PUBLIC_DOMUX_DISCUSSION_URL]

The URL above must exactly match the `channels` frontmatter before this file is renamed to
`README.md` and submitted in a PR.

## Safety, privacy, and license

The dataset is CC0-1.0. The case contains no token, model weights, private cache path, real
household data, or private endpoint. Domux remains subject to its Hugging Face/Gemma terms;
the surrounding repository uses Apache-2.0.
