# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Memory-efficient GGUF v2/v3 metadata inspection with Q4_0, Q4_K_M,
  Q5_K_M, Q6_K, and Q8_0 recognition.
- Local/remote Ollama REST support for model listing, pulling with progress,
  loading, and inference.
- A CLI for local GGUF discovery/import and Ollama-managed models, with
  explicit separation from Safetensors training storage.

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

[Unreleased]: https://github.com/iflytek/domux/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/iflytek/domux/releases/tag/v0.1.0
