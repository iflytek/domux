#!/usr/bin/env python3
"""Cluster official-evaluation failures and render a reviewable report."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from common import ARTIFACTS_DIR, RAW_ARTIFACTS_DIR, SLOT_NAMES, ensure_artifact_dirs, parse_instructions


CLUSTER_LABELS = {
    "api_error": "API 请求失败",
    "format_invalid": "非七字段格式",
    "missing_intent": "缺少动作/意图",
    "extra_intent": "多输出动作/意图",
    "action_mismatch": "action 错误",
    "device_mismatch": "device 错误",
    "attribute_mismatch": "attribute 错误",
    "value_mismatch": "value 错误",
    "unit_mismatch": "unit 错误",
    "room_mismatch": "room 错误",
    "floor_mismatch": "floor 错误",
    "omitted_attribute_failure": "省略属性样本失败",
    "non_standard_naming_failure": "非标准设备名样本失败",
    "unclassified_mismatch": "其他集合匹配错误",
}


def align_instructions(
    predicted: list[tuple[str, ...]], gold: list[tuple[str, ...]]
) -> tuple[list[tuple[tuple[str, ...], tuple[str, ...]]], int, int]:
    remaining = list(gold)
    pairs: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for prediction in predicted:
        if not remaining:
            break
        best_index = max(
            range(len(remaining)),
            key=lambda index: sum(a == b for a, b in zip(prediction, remaining[index])),
        )
        pairs.append((prediction, remaining.pop(best_index)))
    return pairs, max(0, len(gold) - len(predicted)), max(0, len(predicted) - len(gold))


def classify_failure(item: dict[str, Any]) -> set[str]:
    clusters: set[str] = set()
    if item.get("error"):
        clusters.add("api_error")
    if not item.get("format_valid", False):
        clusters.add("format_invalid")
    predicted = parse_instructions(item.get("model_output"))
    gold = parse_instructions(item.get("gold"))
    pairs, missing, extra = align_instructions(predicted, gold)
    if missing:
        clusters.add("missing_intent")
    if extra:
        clusters.add("extra_intent")
    for prediction, expected in pairs:
        for index, slot_name in enumerate(SLOT_NAMES):
            if prediction[index] != expected[index]:
                clusters.add(f"{slot_name}_mismatch")
    if item.get("category") == "omitted_attribute":
        clusters.add("omitted_attribute_failure")
    if item.get("category") == "non_standard_naming":
        clusters.add("non_standard_naming_failure")
    if not clusters:
        clusters.add("unclassified_mismatch")
    return clusters


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def format_percent(value: Any) -> str:
    return f"{float(value) * 100:.2f}%"


def markdown_report(analysis: dict[str, Any], summary: dict[str, Any]) -> str:
    lines = [
        "# Domux 4,057 条开放评测失败簇分析",
        "",
        "> 本报告由逐样本原始输出重新计算。一个失败样本可能同时属于多个错误簇，",
        "> 因此各错误簇数量之和可能大于失败样本总数。",
        "",
        "## 总览",
        "",
        f"- 完成样本：{analysis['total_samples']}",
        f"- 完全正确：{analysis['correct_samples']}",
        f"- 失败样本：{analysis['failed_samples']}",
        f"- API 错误：{analysis['api_error_samples']}",
        "",
        "## 官方指标",
        "",
        "| 类别 | 样本 | 格式合规 | Result Accuracy | Slot F1 | Intent F1 | 并发评测平均延迟 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for category in summary.get("categories", []):
        lines.append(
            "| {category} | {total} | {format} | {accuracy} | {slot} | {intent} | {latency:.3f}s |".format(
                category=category["category"],
                total=category["total"],
                format=format_percent(category["format_compliance"]),
                accuracy=format_percent(category["result_accuracy"]),
                slot=format_percent(category["slot_f1"]),
                intent=format_percent(category["intent_f1"]),
                latency=float(category["avg_latency"]),
            )
        )
    overall = summary.get("overall", {})
    if overall:
        lines.append(
            "| **OVERALL** | **{total}** | **{format}** | **{accuracy}** | **{slot}** | **{intent}** | **{latency:.3f}s** |".format(
                total=overall["total"],
                format=format_percent(overall["format_compliance"]),
                accuracy=format_percent(overall["result_accuracy"]),
                slot=format_percent(overall["slot_f1"]),
                intent=format_percent(overall["intent_f1"]),
                latency=float(overall["avg_latency"]),
            )
        )

    lines.extend(
        [
            "",
            "该延迟是并发评测中的端到端 HTTP 请求延迟，不是 TTFT；单并发延迟另见 `latency_summary.json`。",
            "",
            "## 错误簇",
            "",
            "| 错误簇 | 样本数 | 占失败样本 |",
            "|---|---:|---:|",
        ]
    )
    for cluster in analysis["clusters"]:
        lines.append(
            f"| {CLUSTER_LABELS.get(cluster['name'], cluster['name'])} | {cluster['count']} | {cluster['percent_of_failures']:.2f}% |"
        )

    lines.extend(["", "## 代表性失败", ""])
    for number, example in enumerate(analysis["representative_failures"], 1):
        labels = "、".join(CLUSTER_LABELS.get(name, name) for name in example["clusters"])
        lines.extend(
            [
                f"### {number}. 样本 #{example['idx']}（{example['category']}）",
                "",
                f"错误簇：{labels}",
                "",
                "输入：",
                "",
                "```text",
                str(example["query"]),
                "```",
                "",
                "Domux 原始输出：",
                "",
                "```text",
                str(example["model_output"]),
                "```",
                "",
                "标准答案：",
                "",
                "```text",
                str(example["gold"]),
                "```",
                "",
            ]
        )

    lines.extend(
        [
            "## 边界与安全说明",
            "",
            "- 完全匹配率对大小写、字段值和动作数量都很严格；Slot F1 更适合观察局部正确性。",
            "- Domux 的输出是控制语义，不是最终设备授权。门锁、燃气、加热设备等高风险动作仍应在执行层增加身份校验、状态检查和二次确认。",
            "- 本报告只描述本次固定 revision、硬件和参数下的结果，不将未测试的优化写成已实现能力。",
            "- 后续可针对高频 slot 错误补充难例，但新增数据必须明确来源和许可，并重新运行相同评测验证。",
            "",
        ]
    )
    return "\n".join(lines)


def build_analysis(results: list[dict[str, Any]], max_examples: int = 15) -> dict[str, Any]:
    failures = [item for item in results if not item.get("result_correct", False)]
    cluster_counts: Counter[str] = Counter()
    examples_by_cluster: dict[str, list[dict[str, Any]]] = defaultdict(list)
    enriched = []
    for item in failures:
        clusters = sorted(classify_failure(item))
        enriched_item = {
            "idx": item.get("idx"),
            "category": item.get("category"),
            "query": item.get("query"),
            "model_output": item.get("model_output"),
            "gold": item.get("gold"),
            "clusters": clusters,
        }
        enriched.append(enriched_item)
        for cluster in clusters:
            cluster_counts[cluster] += 1
            if len(examples_by_cluster[cluster]) < 3:
                examples_by_cluster[cluster].append(enriched_item)

    representative = sorted(enriched, key=lambda item: (-len(item["clusters"]), int(item["idx"])))[:max_examples]
    failure_count = len(failures)
    known_names = sorted(CLUSTER_LABELS, key=lambda name: cluster_counts[name], reverse=True)
    extra_names = [name for name, _ in cluster_counts.most_common() if name not in CLUSTER_LABELS]
    clusters = [
        {
            "name": name,
            "label_zh": CLUSTER_LABELS.get(name, name),
            "count": cluster_counts[name],
            "percent_of_failures": round(cluster_counts[name] / failure_count * 100, 2) if failure_count else 0.0,
            "examples": examples_by_cluster[name],
        }
        for name in known_names + extra_names
    ]
    return {
        "total_samples": len(results),
        "correct_samples": len(results) - failure_count,
        "failed_samples": failure_count,
        "api_error_samples": sum(1 for item in results if item.get("error")),
        "clusters": clusters,
        "representative_failures": representative,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=RAW_ARTIFACTS_DIR / "eval_results.jsonl")
    parser.add_argument("--summary", type=Path, default=ARTIFACTS_DIR / "eval_summary.json")
    parser.add_argument("--max-examples", type=int, default=15)
    args = parser.parse_args()
    ensure_artifact_dirs()
    if not args.results.is_file() or not args.summary.is_file():
        parser.error("run run_official_eval.py before failure analysis")
    results = read_jsonl(args.results)
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    analysis = build_analysis(results, max_examples=args.max_examples)
    json_path = ARTIFACTS_DIR / "failure_analysis.json"
    markdown_path = ARTIFACTS_DIR / "FAILURE_ANALYSIS.md"
    json_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown_report(analysis, summary), encoding="utf-8")
    print(f"Failure analysis: {analysis['failed_samples']} failures across {len(analysis['clusters'])} clusters")
    print(f"JSON: {json_path}")
    print(f"Markdown: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
