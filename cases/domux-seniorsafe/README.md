---
title: Domux runs full SeniorSafe intent benchmark on a CPU-only Windows PC with BF16 Transformers
author: Lumi Ruvenne
date: 2026-08-30
category: smart-home-command
testedRevision: 6c71a32f4d624cadfd9fce9d10240d8068e53456
runtime: transformers 5.16.1 + torch 2.13.0+cpu (CPU-only)
hardware: Intel 14-core CPU (Model 170), 31.4 GB RAM, Windows 11, no GPU
downloadSource: huggingface
channels:
  - https://huggingface.co/iFlytekOpenSource/Domux/discussions/7
---

# Domux on a consumer CPU: 80-sample SeniorSafe intent benchmark, raw vs normalized pipelines

## Prospective generalization follow-up / 新增泛化验证

The original 80-record narrative below is preserved historical/development evidence.
A later frozen, AI-authored challenge adds **160 texts / 80 paired scenarios**, with
**320 real CPU inferences** and unchanged inference policies. Full details and failure
examples are in [Generalization v1 review](artifacts/generalization-v1/REVIEW.md).

- Frozen-label exact match: raw **75/136 (55.1%)**, normalized **84/136 (61.8%)**.
- Four unit-label issues are separately documented; the protocol-label sensitivity
  scores are **77/136 (56.6%)** and **88/136 (64.7%)**, without changing predictions
  or overwriting the original data/results.
- **8 exact-match regressions**, **5/24 input-policy false allows**, and **2/24
  blocked-label requests still marked output candidates** remain. No devices were operated.
- This is a prospective synthetic challenge, not a third-party blind test or real
  elderly speech evaluation. English clean and Chinese/mixed challenge inputs
  confound language with perturbation; naming differences are not all semantic errors.
- The experiment exposes real rewrite bugs (e.g. 65 percent partially rewritten
  as 60, and Balcony Light partially rewritten as Desk Lamp). These inference
  defects were **not repaired within the frozen experiment**. The case is not safe
  for direct device integration.

See the [reproduction guide](artifacts/generalization-v1/RUNBOOK.md) for configurable
dataset validation, frozen-run guards, evidence files and conservative resume behavior.

## Task / 真实任务

Elderly users speak to smart-home devices in dialect-flavored, ASR-corrupted,
code-switched Mandarin. Domux must turn these utterances into a strict
7-slot command format (`action|device|attribute|value|unit|room|floor`), and
risky/ambiguous utterances must not be executed. This case benchmarks Domux on
the synthetic [SeniorSafe set](data/seniorsafe.jsonl) (80 utterances: 40 clean
plus 40 paired noisy variants) and measures how much a deterministic,
auditable text-normalization pre-pass recovers accuracy on CPU-only hardware —
a realistic deployment constraint for a home hub box without a GPU.

## Hugging Face download / 下载证据

- Model: iFlytekOpenSource/Domux
- Revision: `6c71a32f4d624cadfd9fce9d10240d8068e53456`
- Command:

      hf download iFlytekOpenSource/Domux --revision 6c71a32f4d624cadfd9fce9d10240d8068e53456

- Full BF16 safetensors snapshot, 4 shards, 10,279,032,574 bytes (~9.6 GiB),
  downloaded directly from Hugging Face with a read token after accepting the
  Gemma terms. No weights are committed to GitHub.

## Setup / 环境

- Runtime: Python 3.12.13, transformers 5.16.1, torch 2.13.0+cpu,
  torchvision 0.28.0+cpu, accelerate 1.14.0 (see
  [requirements-cpu.txt](requirements-cpu.txt))
- Hardware: Intel 14-core CPU (family 6 model 170), 18 logical threads,
  31.4 GB RAM, Windows 11 26200, **no GPU**
- Precision: BF16 on CPU (`dtype=torch.bfloat16`, `device_map="cpu"`,
  `low_cpu_mem_usage=True`)
- Inference: greedy decoding (`do_sample=False`), `max_new_tokens=128`,
  `torch.set_num_threads(16)`, KV cache enabled, chat template via
  `Gemma4Processor`
- Pipeline A (**raw**): utterance straight to the model.
  Pipeline B (**normalized**): deterministic rule-based normalization first
  (ASR replacements, dialect phrases, keyword translation into the canonical
  English slot vocabulary), then the model. Every normalization edit is logged.

## What happened / 实际过程

Model load takes ~6 s; each 80-sample run takes ~13 min on CPU. Zero runtime
errors across both full runs. Representative pairs (raw vs normalized):

```text
# ASR error recovered by normalization
raw in : 把卧室诶西调到二十四度。        ("诶西" is ASR garbage for "AC")
raw out: set|AC|temperature|24|Celsius|卧室|*     room slot in Chinese -> wrong
norm in: 把BedroomACset to24 Celsius
norm out: set|AC|temperature|24|Celsius|Bedroom|* -> exact match

# Code-switching recovered
raw in : 卧室 AC set 到 24 degrees。
raw out: set|AC|temperature|24|Celsius|卧室|*     -> wrong
norm in: Bedroom AC set 到 24 degrees
norm out: set|AC|temperature|24|Celsius|Bedroom|* -> exact match

# Self-correction: normalization destroyed the correction context (regression)
raw in : 把客厅窗帘关上，不对，是卧室的窗帘。
raw out: turnOff|Curtain|*|*|*|Bedroom|* -> correct
norm in: 是Bedroom的Curtain              (negated first clause dropped)
norm out: turnOn|Curtain|*|*|*|Bedroom|* -> wrong, and flips the action
```

Run log excerpt (real run, revision `6c71a32f`):

```text
[raw] 1/5 ss-001-clean latency_ms=9572.101 error=False
[raw] 2/5 ss-001-elderly_style latency_ms=8639.103 error=False
[raw] 3/5 ss-002-clean latency_ms=16080.642 error=False
[raw] 4/5 ss-002-elderly_style latency_ms=18621.756 error=False
[raw] 5/5 ss-003-clean latency_ms=15153.866 error=False
```

## Audit fixes / 审查修复

The current case remains an **offline developer benchmark**, not a device controller.
The original results below are preserved historical evidence. Current runner code
uses stricter, versioned scoring and must write to a new run directory; never
combine its results with the original logs as if they came from the same pipeline.

- Risk matching is case-insensitive for English aliases; a room alone no longer
  resolves a missing device. This remains a limited lexical policy, not a safety certification.
- Correction utterances retain their full context instead of dropping earlier clauses.
- Parsing requires non-empty fields and a known action. Exact match now preserves
  order and duplicates; slot/intent F1 use separate ordered dynamic programming.
- Both runners retain raw model output for research, including blocked examples.
  `safety_decision` is the input-policy label. `output_decision` separately checks
  model output and can be `reject`, `clarify`, or `candidate`. **Candidate never
  grants execution permission**; every row records `execution_performed: false`.
- Outputs are created exclusively, with code/data/settings fingerprints and a final
  result digest. `--resume` accepts only a matching intact prefix. A torn last line
  is refused without altering it. Do not run simultaneous writers against one run.
- Errors produce a nonzero exit and a failed manifest; incomplete runs cannot be
  silently scored as completed experiments. Existing error rows are preserved on
  resume; use a new run to retry a failed experiment.

Offline checks (no packages or model downloads required):

```powershell
python cases/domux-seniorsafe/scripts/validate_data.py
python -m unittest discover -s cases/domux-seniorsafe/scripts -p 'test_*.py' -v
```

The workflow test uses a local synthetic HTTP provider. Its perfect scores test
program plumbing only, **not Domux quality**. Newly authored safety counterexamples
are regression cases, not an untouched statistical holdout set.

Reproduce a fresh paired CPU run using the existing environment and cached model:

```powershell
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
foreach ($pipeline in @('raw', 'normalized')) {
    .\.venv\Scripts\python.exe -B cases/domux-seniorsafe/scripts/run_transformers_cpu.py `
        --revision 6c71a32f4d624cadfd9fce9d10240d8068e53456 `
        --pipeline $pipeline --run-id "local-v2-$pipeline" --threads 16 `
        --output "cases/domux-seniorsafe/artifacts/local-v2/${pipeline}_outputs.jsonl" `
        --environment-output "cases/domux-seniorsafe/artifacts/local-v2/${pipeline}_environment.json"
    if ($LASTEXITCODE -ne 0) { throw 'Run failed; inspect evidence before continuing' }
}
.\.venv\Scripts\python.exe -B cases/domux-seniorsafe/scripts/score.py `
    --raw cases/domux-seniorsafe/artifacts/local-v2/raw_outputs.jsonl `
    --normalized cases/domux-seniorsafe/artifacts/local-v2/normalized_outputs.jsonl `
    --output cases/domux-seniorsafe/artifacts/local-v2/metrics.json
```

Choose another directory if it already exists. To resume, repeat the same runner
command with `--resume` and unchanged code, inputs and settings. New strict scoring
requires sibling `*_environment.json` manifests. `--allow-legacy` is only for
explicit historical rescoring and cannot restore missing provenance.

For the API runner, revision is caller-declared (`model_revision_verified: false`);
the client cannot prove that a remote server actually serves those weights. CPU
`--snapshot` accepts the matching Hugging Face cache layout, not arbitrary folders.

## Verified audit rerun / 修复后全量重跑

The audit rerun completed **80 raw + 80 normalized CPU inferences with zero runtime
errors**, under matching code/data/settings fingerprints. All 31 local tests and
the data/case validators passed. This verifies the offline benchmark, not a device controller.

| Metric | Fresh raw | Fixed normalized |
|---|---:|---:|
| Exact match (70 evaluable) | 55.7% (39/70) | **85.7% (60/70)** |
| Slot F1 (v2 ordered scorer) | 0.8956 | 0.9666 |
| Intent F1 (v2 ordered scorer) | 0.5674 | 0.8652 |
| Average generation latency | 8.54 s | 8.77 s |
| P95 generation latency (nearest rank) | 10.44 s | 11.13 s |

Recovery is 23/31 (74.2%); regression is 2/39 (5.1%). The fixed normalizer improves
exact match by 10 percentage points over the original normalized run. Ten parse
failures remain; two are regressions relative to fresh raw. No device was executed.
New latency observations do not establish a causal speedup over the old run.

See the [audit report](artifacts/audit-v2/REVIEW.md),
[paired metrics](artifacts/audit-v2/metrics.json),
[remaining failures](artifacts/audit-v2/remaining_parse_failures.json), and
[verification record](artifacts/audit-v2/verification.json).
The original results below retain their original scorer; their F1 values are not
directly interchangeable with v2. An explicit historical rescore is included in
the audit directory. This local update does not update the remote Discussion.

## Historical results / 原始版本结果

70/80 samples are parse-evaluable; the 10 `ambiguous_reference` /
`high_risk_ambiguity` samples expect a safety decision (6 `clarify`, 4
`reject`) instead of a parseable command and are excluded from parse metrics
by design. Safety metrics use 15 labeled non-execute samples in total (11 clarify,
4 reject), including five high-risk clean samples. Latency is wall-clock
`model.generate` time per sample, no warm-up pass, single run.

| Metric | Raw | Normalized | Method |
|---|---:|---:|---|
| Format compliance (evaluable) | 100% | 100% | 7-field parse |
| Result accuracy (exact match) | 55.7% | **75.7%** | 39/70 vs 53/70 |
| Slot F1 | 0.894 | **0.938** | slot-level micro F1 |
| Intent F1 | 0.553 | **0.746** | intent-level micro F1 |
| Avg latency (ms/sample) | 9,444 | 9,406 | wall clock, CPU BF16 |
| Runtime errors | 0 | 0 | 80 samples each |
| Normalizer recovery rate | — | 67.7% | raw-wrong fixed by normalization (21/31) |
| Normalizer regression rate | — | 17.9% | raw-right broken by normalization (7/39) |
| Safety decision accuracy (rule layer only) | 100% | 100% | `safety_decision(text)` vs dataset labels; the model is not consulted |
| Dangerous execute rate (rule layer only) | 0% | 0% | the rule layer never returns `execute` on risky samples; true by construction, not a model metric |

Per-group result accuracy (normalized): `code_switching` 100%,
`asr_error` 100%, `negation` 80%, `clean` 77.5%, `elderly_style` 60%,
`repetition` 60%, `self_correction` 40%.

Limitations observed:

- The dominant raw failure is language mismatch: Domux echoes Chinese room
  nouns (`卧室`) and Chinese units (`摄氏度`) that the canonical vocabulary
  marks wrong even when the intent is perfect. Normalization fixes this class
  almost entirely.
- The rule-based normalizer is a double-edged sword: it broke 7 previously
  correct samples. Going through `regressed_ids`, only 1 of the 7 is the
  self-correction context drop (`ss-008`, where rewriting drops the
  "不对，是…" clause and can flip `turnOff` into `turnOn`); the other 6 are
  plain splicing defects on clean text — replacements were concatenated
  without spaces (`Living RoomLightset toBlue` produced the bogus device
  slot `Lightset`, `BedroomHeaterset to24 Celsius` produced `Heaterset`) and
  the lexicon missed `厨房` / `三十度` / bare `安防`. These are fixed in
  [normalize.py](scripts/normalize.py) after this run (space-padded
  substitutions plus the missing lexicon entries). The later audit also removed
  destructive correction rewriting. The original 75.7% does not measure these changes.
- Average generation latency was ~9.4 s; nearest-rank P95 was ~14.6 s in both
  original runs. Suitability for spoken-home interaction has not been user-tested.

## Why it mattered / 价值

- Demonstrates that this Domux snapshot can complete an offline benchmark on this no-GPU Windows PC:
  6 s load, ~9.4 s per command, zero errors across 160 CPU inferences.
- Quantifies a cheap, auditable pre-pass: +20.0 pp exact-match accuracy,
  +19.3 pp intent F1 in the original scorer. Normalization overhead was not separately
  measured in that run; the original evidence also records its
  17.9% regression rate and pinpointing the exact splicing bugs behind it.
- The recovery/regression ID lists in
  [artifacts/metrics.json](artifacts/metrics.json) give the next person a
  concrete fix list for the normalizer rules.

## Published Hugging Face Discussion / 公开 Discussion

Published: the full case is posted in the official Domux Discussions at the
URL below, which also appears in the frontmatter.

- https://huggingface.co/iFlytekOpenSource/Domux/discussions/7

## Safety, privacy, and licensing / 安全、隐私与许可

- No HF tokens, cache paths, or credentials are committed; the run environment
  records a redacted snapshot name only.
- All 80 utterances are synthetic (generated by
  [generate_dataset.py](scripts/generate_dataset.py)); no private household or
  business data is involved. The dataset ships in this case under the
  repository license.
- Ambiguity handling for high-risk actions, verified against the run logs: on
  all 10 `ambiguous_reference` / `high_risk_ambiguity` samples the model itself
  emitted seven-field command strings in **both** pipelines
  (for example `turnOn|Door Lock|*|*|*|*|*` and `turnOn|Gas Valve|*|*|*|*|*`).
  Nothing in the model refuses, asks for confirmation, or produces an
  unparseable reply. The offline rule function (`safety_decision` in
  [normalize.py](scripts/normalize.py)) labels 6 of those texts `clarify` and 4
  `reject`. These runners have no device executor and do not demonstrate a
  deployed interlock or a confirmation conversation. The reported 100% safety-decision accuracy and
  0% dangerous-execute rate therefore measure the rule layer against the
  dataset's own labels — they are not model behavior. A future deployment needs
  device identity resolution, capability validation, authorization, confirmation,
  cancellation and execution acknowledgements; these are outside this benchmark.

## Notes and gotchas / 踩坑记录

- `Gemma4Processor` needs `pillow` **and CPU `torchvision`** even for
  text-only chat; both are missing from a plain torch-CPU install.
- Install CPU wheels from the PyTorch CPU index:
  `uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu`.
- The historical CPU run used BF16 without a float32 fallback. The recorded
  `model_load_rss_delta_bytes` is a before/after RSS difference, **not peak memory**.
- huggingface.co web pages can return HTTP 418 behind some proxy exit nodes;
  the API endpoints and `hf download` kept working. Retry later or switch node.
- `hf auth login` device codes expire in ~10 minutes; a Read token via
  `--token` is the calmer path.
