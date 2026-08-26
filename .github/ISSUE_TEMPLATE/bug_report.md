---
name: 🐛 Bug report / 缺陷报告
about: Report incorrect model output, a crash, or broken tooling / 报告模型输出错误、崩溃或工具链问题
title: '[Bug] '
labels: ['bug']
---

<!--
Before filing, please search existing issues to avoid duplicates.
提交前请先搜索已有 issue，避免重复。
-->

## What went wrong / 问题描述

<!-- A clear description of the incorrect behaviour. / 清晰描述出现的错误行为。 -->

## Category / 问题类别

<!-- Keep the one that applies. / 保留适用的一项。 -->

- [ ] Incorrect structured output (intent / device / room / action / value) / 结构化输出错误
- [ ] Inference or deployment failure (vLLM / SGLang / Transformers) / 推理或部署失败
- [ ] Training or reward plugin issue / 训练或奖励插件问题
- [ ] Evaluation script (`eval/run_eval.py`) issue / 评测脚本问题
- [ ] Documentation error / 文档错误
- [ ] Other / 其他

## Reproduction / 复现步骤

**Input utterance / 输入指令:**

```text
<!-- e.g. 把客厅的灯调暗一点 -->
```

**Expected output / 期望输出:**

```json

```

**Actual output / 实际输出:**

```json

```

<!--
For non-output bugs, give the exact commands and the full error traceback instead.
若不是输出类问题，请改为提供完整命令与报错栈。
-->

## Environment / 运行环境

| Item / 项目 | Value / 值 |
| --- | --- |
| Model source / 模型来源 | <!-- Hugging Face / ModelScope / local fine-tune --> |
| Model version / 模型版本 | <!-- e.g. Domux-Gemma-4-E2B-it --> |
| Inference backend / 推理后端 | <!-- vLLM 0.22.0 / SGLang 0.5.12 / Transformers --> |
| Python / OS | |
| GPU / hardware / 硬件 | |

## Additional context / 补充信息

<!--
Logs, screenshots, sampling parameters (temperature, top_p), or a link to a case
under `cases/` that demonstrates the problem.
日志、截图、采样参数（temperature、top_p），或 `cases/` 下可复现该问题的案例链接。
-->
