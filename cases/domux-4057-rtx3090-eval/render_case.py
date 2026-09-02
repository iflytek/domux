#!/usr/bin/env python3
"""Render the final case README and Hugging Face Discussion from real artifacts."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from common import ARTIFACTS_DIR, CASE_DIR, redact_text


DISCUSSION_RE = re.compile(r"^https://huggingface\.co/iFlytekOpenSource/Domux/discussions/\d+(?:#\S+)?$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


def load_json(name: str) -> dict[str, Any]:
    path = ARTIFACTS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"missing real experiment artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_text_lf(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def percent(value: Any) -> str:
    return f"{float(value) * 100:.2f}%"


def gibibytes(byte_count: int) -> str:
    return f"{byte_count / 1024 ** 3:.2f} GiB"


def metric_table(summary: dict[str, Any]) -> str:
    rows = [
        "| Category | Samples | Format | Result accuracy | Slot F1 | Intent F1 | Avg E2E latency¹ |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for category in summary["categories"]:
        rows.append(
            "| {category} | {total} | {format} | {accuracy} | {slot} | {intent} | {latency:.3f}s |".format(
                category=category["category"],
                total=category["total"],
                format=percent(category["format_compliance"]),
                accuracy=percent(category["result_accuracy"]),
                slot=percent(category["slot_f1"]),
                intent=percent(category["intent_f1"]),
                latency=float(category["avg_latency"]),
            )
        )
    overall = summary["overall"]
    rows.append(
        "| **OVERALL** | **{total}** | **{format}** | **{accuracy}** | **{slot}** | **{intent}** | **{latency:.3f}s** |".format(
            total=overall["total"],
            format=percent(overall["format_compliance"]),
            accuracy=percent(overall["result_accuracy"]),
            slot=percent(overall["slot_f1"]),
            intent=percent(overall["intent_f1"]),
            latency=float(overall["avg_latency"]),
        )
    )
    return "\n".join(rows)


def smoke_examples(smoke: dict[str, Any], count: int = 3) -> str:
    sections = []
    for item in smoke["results"][:count]:
        sections.extend(
            [
                f"Input #{item['sample']}:",
                "",
                "```text",
                str(item["query"]),
                "```",
                "",
                "Raw Domux output:",
                "",
                "```text",
                str(item["raw_output"]),
                "```",
                "",
            ]
        )
    return "\n".join(sections)


def failure_examples(analysis: dict[str, Any], count: int = 5) -> str:
    sections = []
    for item in analysis["representative_failures"][:count]:
        sections.extend(
            [
                f"- Sample #{item['idx']} (`{item['category']}`), clusters: `{'`, `'.join(item['clusters'])}`",
                "",
                "  ```text",
                f"  Input: {item['query']}",
                f"  Output: {item['model_output']}",
                f"  Gold: {item['gold']}",
                "  ```",
            ]
        )
    return "\n".join(sections) if sections else "No incorrect samples were observed in this run."


def validate_artifacts(
    download: dict[str, Any], smoke: dict[str, Any], run: dict[str, Any], summary: dict[str, Any], latency: dict[str, Any]
) -> None:
    revision = str(download.get("revision", ""))
    if not REVISION_RE.fullmatch(revision):
        raise ValueError("download_metadata.json must contain the exact 40-character tested revision")
    if not smoke.get("all_passed"):
        raise ValueError("smoke test did not pass; do not render a submission")
    if run.get("completed_samples") != 4057 or run.get("api_error_count") != 0:
        raise ValueError("official evaluation must contain 4,057 completed samples and zero API errors")
    if summary.get("overall", {}).get("total") != 4057:
        raise ValueError("eval_summary.json overall.total must equal 4057")
    if latency.get("errors"):
        raise ValueError("latency benchmark contains errors")
    if not latency.get("overall"):
        raise ValueError("latency benchmark summary is empty")


def build_body(
    *,
    discussion_url: str,
    download: dict[str, Any],
    environment: str,
    smoke: dict[str, Any],
    run: dict[str, Any],
    summary: dict[str, Any],
    analysis: dict[str, Any],
    latency: dict[str, Any],
) -> str:
    revision = download["revision"]
    overall_latency = latency["overall"]
    cluster_rows = "\n".join(
        f"| {cluster['label_zh']} | {cluster['count']} | {cluster['percent_of_failures']:.2f}% |"
        for cluster in analysis["clusters"]
    )
    evidence = "![Domux run evidence](preview.png)" if (CASE_DIR / "preview.png").is_file() else (
        "Public, sanitized console evidence is included in `artifacts/eval_console.txt`; "
        "the per-sample raw JSONL remains local and is intentionally not committed."
    )
    published_section = (
        f"## Published Hugging Face Discussion / 公开 Discussion\n\n- {discussion_url}\n\n"
        if discussion_url
        else ""
    )
    return f"""# Domux 4,057-sample open evaluation on RTX 3090

## Task / 真实任务

I reproduced the complete public Domux evaluation on a consumer RTX 3090, then
recomputed failure clusters by output field. The goal was to answer two practical
questions: whether the published result can be reproduced on accessible hardware,
and which command-understanding failures still matter in a real smart-home pipeline.

本案例在单张 RTX 3090 上运行官方 4,057 条测试集，并按 action、device、attribute、
value、unit、room、floor、多意图数量及格式错误重新聚类，避免只展示一个总体准确率。

## Hugging Face download / 下载证据

- Model: `iFlytekOpenSource/Domux`
- Tested revision: `{revision}`
- Download call: `huggingface_hub.snapshot_download(repo_id, revision=<testedRevision>)`
- Hugging Face endpoint used by this run: `{download['download_source']}`
- Snapshot size: {gibibytes(int(download['snapshot_bytes']))}
- Artifact: full Hugging Face BF16 snapshot; no model weights are included in this case.

## Setup / 环境

- Runtime: vLLM 0.22.0, Transformers {download['transformers']}, OpenAI-compatible `/v1/chat/completions`
- CUDA compiler used for FlashInfer JIT: {download['cuda_compiler']}; the driver and CUDA runtime are recorded below
- Hardware: one NVIDIA GeForce RTX 3090 24 GB; the second installed RTX 3090 was not used
- Precision: BF16
- Correctness run: temperature 0, max tokens {run['max_tokens']}, concurrency {run['max_workers']},
  request timeout {run['request_timeout_seconds']}s, first {run['latency_warmup_samples']} dataset indices
  excluded only from the reported latency
- Latency run: concurrency 1, {latency['warmup_samples']} warm-up requests,
  {latency['measured_samples_per_repeat']} measured requests × {latency['repeats']} repeats

Sanitized environment record:

```text
{environment.strip()}
```

## What happened / 实际过程

The model was downloaded from Hugging Face at the pinned revision, served on GPU 0,
smoke-tested with five public commands, and then evaluated with the unmodified official
dataset and metric implementation. The wrapper changes configuration and output paths
only; it does not change parsing or scoring.

{smoke_examples(smoke)}
{evidence}

## Results / 结果

{metric_table(summary)}

¹ Concurrent correctness run: end-to-end HTTP request latency at concurrency
{run['max_workers']}; this is not time-to-first-token.

Sequential latency benchmark:

| Samples | Repeats | Median E2E | P95 E2E | Mean E2E | Throughput |
|---:|---:|---:|---:|---:|---:|
| {latency['measured_samples_per_repeat']} | {latency['repeats']} | {overall_latency['median_seconds']:.6f}s | {overall_latency['p95_seconds_nearest_rank']:.6f}s | {overall_latency['mean_seconds']:.6f}s | {overall_latency['throughput_requests_per_second']:.4f} req/s |

Failure clusters use failed samples as the denominator. A sample can be in more
than one cluster, so cluster counts do not sum to the number of failed samples.

| Failure cluster | Samples | Share of failed samples |
|---|---:|---:|
{cluster_rows}

Representative failures:

{failure_examples(analysis)}

The complete generated report is in [`artifacts/FAILURE_ANALYSIS.md`](artifacts/FAILURE_ANALYSIS.md).

## Why it mattered / 价值

This run provides a reproducible consumer-GPU baseline, separates correctness from
single-request latency, and identifies concrete failure groups for future data or
reward-function work. It also demonstrates why a smart-home system must evaluate
full structured outputs rather than relying on format compliance alone.

{published_section}## Safety, privacy, and licensing / 安全、隐私与许可

- Tokens, personal cache paths, hostnames, usernames, and private addresses are not included.
- The test prompts come from the repository's official open evaluation set; no private household data was added.
- This evaluates semantic parsing, not execution authorization. High-risk devices still require identity checks,
  current-state validation, policy checks, and explicit confirmation in the downstream executor.
- Raw model weights and the local Hugging Face snapshot are not committed.

## Notes and gotchas / 踩坑记录

- Pin the 40-character Hugging Face revision before downloading and record it again in the case frontmatter.
- The official evaluator defaults to concurrency 20; its latency is not directly comparable with sequential latency.
- On this host, vLLM 0.22.0 installed a CUDA 13 runtime, which required upgrading the NVIDIA driver to the 580 series.
- The dependency resolver selected Transformers 4.x because xgrammar 0.2.4 declares `transformers<5`, but that version did not recognize the checkpoint's `gemma4` architecture. Transformers 5.5.1 was installed explicitly. Structured decoding was not used in this experiment.
- FlashInfer JIT initially found the host CUDA 11.5 compiler. The run scripts point `CUDA_HOME` and `CUDACXX` to the environment's pinned CUDA 13.0 compiler instead.
- Preserve raw per-sample output locally for audit, but publish only the aggregate metrics and representative examples.
- Run `python scripts/validate_cases.py` before opening the PR.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discussion-url")
    parser.add_argument("--author", default="posuizhiyu-maker")
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()
    if args.discussion_url and not DISCUSSION_RE.fullmatch(args.discussion_url):
        parser.error("--discussion-url must be a public iFlytekOpenSource/Domux Discussion URL")
    if not args.author.strip():
        parser.error("--author cannot be empty")

    download = load_json("download_metadata.json")
    smoke = load_json("smoke_results.json")
    run = load_json("eval_run_metadata.json")
    summary = load_json("eval_summary.json")
    analysis = load_json("failure_analysis.json")
    latency = load_json("latency_summary.json")
    environment_path = ARTIFACTS_DIR / "environment.txt"
    if not environment_path.is_file():
        raise FileNotFoundError(f"missing real experiment artifact: {environment_path}")
    environment = "\n".join(
        line.rstrip()
        for line in redact_text(environment_path.read_text(encoding="utf-8", errors="replace")).splitlines()
    )
    validate_artifacts(download, smoke, run, summary, latency)
    body = build_body(
        discussion_url=args.discussion_url or "",
        download=download,
        environment=environment,
        smoke=smoke,
        run=run,
        summary=summary,
        analysis=analysis,
        latency=latency,
    )
    if args.discussion_url:
        frontmatter = f"""---
title: Domux 4,057-sample BF16 evaluation and failure analysis on RTX 3090
author: {args.author}
date: {args.date}
category: evaluation
testedRevision: {download['revision']}
runtime: vllm-0.22.0
hardware: NVIDIA-GeForce-RTX-3090-24GB-Linux-single-GPU
downloadSource: huggingface
channels:
  - {args.discussion_url}
---

"""
        write_text_lf(CASE_DIR / "README.md", frontmatter + body)
        print(f"Rendered case: {CASE_DIR / 'README.md'}")
    discussion_body = body
    if args.discussion_url:
        discussion_body = body.replace(
            "## Published Hugging Face Discussion / 公开 Discussion\n\n- " + args.discussion_url + "\n\n",
            "",
        )
    discussion_body = discussion_body.replace(
        "The complete generated report is in [`artifacts/FAILURE_ANALYSIS.md`](artifacts/FAILURE_ANALYSIS.md).",
        "The complete generated report is included in the GitHub case as `artifacts/FAILURE_ANALYSIS.md`.",
    ).replace(
        "![Domux run evidence](preview.png)",
        "A terminal screenshot from the same run should be attached to this Discussion before publication.",
    )
    discussion_path = ARTIFACTS_DIR / "DISCUSSION_DRAFT.md"
    write_text_lf(
        discussion_path,
        "# [HER Hack-Astron #4] Domux 4057 条开放评测复现：RTX 3090 BF16 性能与失败簇分析\n\n"
        + discussion_body,
    )
    print(f"Rendered Discussion draft: {discussion_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
