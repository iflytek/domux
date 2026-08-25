# [HER Hack-Astron #4] Domux execution safety gate: fail closed on malformed and high-risk commands

> 发布前检查：将本文中 `[EVIDENCE]` 替换为 `evidence/` 重算出的真实内容；加入两条
> 代表性 `raw_output` 和一张脱敏的 Colab 运行截图；不要粘贴 token、缓存路径或模型权重。

## Problem

Domux can turn a smart-home request into a structured seven-field command, but structured
output should not be treated as authority to execute. I built a small fail-closed safety gate
between Domux and a hypothetical dispatcher.

## Why this matters

High-consequence controls such as perimeter access, heating appliances, utility valves, broad
home-wide actions, and ambiguous references need an explicit policy layer. A malformed model
output must not silently reach a device executor.

## Experiment setup

- Model: `iFlytekOpenSource/Domux`
- Fixed revision: `6c71a32f4d624cadfd9fce9d10240d8068e53456`
- Runtime/hardware: [EVIDENCE: package versions, Google Colab free Tesla T4]
- Precision: NF4 4-bit; greedy generation; `max_new_tokens=256`; seed `20260825`
- Data: 48 original CC0-1.0 English synthetic commands, balanced across allow / confirm / block

The full raw outputs, run metadata, and recomputable report are committed under
`cases/domux-safety-gate/evidence/`.

## Domux's role and safety-gate design

Domux performs the natural-language-to-structured-output step. The wrapper first validates
the seven-field output and supported action; malformed output is blocked. It then applies a
conservative command policy:

- allow: explicit low-consequence lighting, AC, curtain, and scene controls;
- confirm: perimeter, heat, utility, broad-scope, ambiguous, or out-of-range requests;
- block: safety disablement/bypass, gas-valve activation, and unbounded heat.

The result is an **integration** metric, not a claim that Domux independently provides safety
classification. The format-compliance rate is the direct model-output measurement.

## Evaluation and results

| Metric | Verified result |
|---|---:|
| Format compliance | [EVIDENCE] |
| End-to-end gate accuracy | [EVIDENCE] |
| End-to-end Macro F1 | [EVIDENCE] |
| High-risk intercept recall | [EVIDENCE] |
| Dangerous false-allow rate | [EVIDENCE] |

Include the verified confusion matrix and per-class metrics here.

## Representative outputs

```text
[EVIDENCE: accepted input → raw Domux output → allow]
[EVIDENCE: malformed/high-risk input → raw Domux output → block/confirm]
```

## Failure cases and limitations

- [EVIDENCE: number and examples of format-invalid outputs]
- The sample is small, synthetic, English-only, and policy-aligned.
- The gate is intentionally rule-based; strong gate metrics should not be interpreted as a
  standalone Domux safety benchmark.
- This prototype does not replace real authorization, inventory/state validation, audit logs,
  or a confirmation UI.

## Reproduction

Repository case directory: `cases/domux-safety-gate/`

Run the tests, `run_transformers.py`, `evaluate_safety.py`, then `verify_evidence.py` as shown
in the case README. No model weights or credentials are included.

## Links

- Repository case/PR: [INSERT_PR_URL]
- Public raw-evidence files: [INSERT_REPOSITORY_LINK]
