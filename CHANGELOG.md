# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-06-30

First public release of Domux (`Domux-Gemma-4-E2B-it`), a lightweight,
low-latency command-understanding model for smart-home control, built on
Gemma-4-E2B-it.

### Added
- SFT + GRPO training pipeline, scripts, and reward plugins (`training/`)
- Smart-home control evaluation set and evaluation script (`eval/`)
- Output format specification (`docs/output-spec.md`, `docs/output-spec.zh.md`)
- Interpretability report (`docs/interpretability-report.md`, `docs/interpretability-report.zh.md`)
- Benchmark reports in English and Chinese (`docs/benchmark-report.pdf`, `docs/benchmark-report.zh.pdf`)
- `NOTICE` file documenting Apache-2.0 licensing for source code and the
  Gemma Terms of Use governing the model weights
- Bilingual README (English / 简体中文)

[0.1.0]: https://github.com/iflytek/domux/releases/tag/v0.1.0
