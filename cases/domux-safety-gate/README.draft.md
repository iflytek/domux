# DRAFT — Domux 执行前安全闸门

> 本文件记录了一轮已完成的真实 GPU 运行，但该轮的逐条原始输出尚未在本地仓库找到。
> 因此它不是可公开提交的最终 README：提交前必须将同一固定 revision 的原始输出、
> metadata 和评测报告存入 `evidence/`，并将本文件转为正式 `README.md`。

## Task / 真实任务

Domux 把自然语言转换成可执行的七字段家居控制指令，但结构化格式正确并不等于
动作适合直接执行。本案例在 Domux 与设备执行器之间加入一个保守安全闸门：

- 明确、低后果指令：`allow`；
- 门锁／车库门／发热设备／阀门、全屋范围、代词歧义或超出文档范围的温度：
  `confirm`；
- 禁用生命安全传感器、绕过锁／报警／确认、开启燃气阀或让发热设备无限运行：
  `block`。

目标不是宣称获得安全认证，也不是把规则命中率当作 Domux 的模型安全能力；本案例证明的
是：在一个真实的 Domux 结构化输出链路中，执行器前可以实施 fail-closed 的安全包装。

## Data / 数据

- 48 条原创合成英文智能家居指令；
- `allow`、`confirm`、`block` 各 16 条；
- 无真实家庭、地址、人物、账户或业务信息；
- 数据集以 CC0-1.0 公开，详见 `DATASET_CARD.md`；
- 样本量较小且类别平衡，不代表生产流量分布。

## Hugging Face download / 下载证据

- Model: `iFlytekOpenSource/Domux`
- Revision: `6c71a32f4d624cadfd9fce9d10240d8068e53456`
- Source: Hugging Face `snapshot_download`，固定 revision
- Snapshot size: 10,279,032,574 bytes
- Precision: NF4 4-bit，`torch.bfloat16` compute dtype
- Runtime: Google Colab 免费 Tesla T4；Python 3.13.15；PyTorch 2.11.0+cu128

模型权重、HF token、个人缓存路径和私有端点不会进入 GitHub。

## Method / 方法

1. 在 GPU 上先完成 2 条 smoke test，再运行全部 48 条；
2. Greedy generation（`do_sample=False`），`max_new_tokens=256`，seed `20260825`；
3. 前 2 条作为 warm-up，正式结果仍覆盖全部 48 条；
4. 保存逐条原始 Domux 输出和单条生成延迟；
5. 先校验每行 7 字段与动作枚举，再执行安全闸门；
6. 报告 Domux 结构化输出的格式合规率，以及“Domux 输出 + 本案例安全策略”的三分类
   端到端决策指标；两者不得解释为 Domux 的独立安全性能；
7. 报告三分类准确率／Macro F1、安全拦截召回率、危险放行率、
   安全指令误干预率，以及安全闸门平均／P95 延迟；
8. 单独展示模型解析失败与策略失败，不把规则单元测试当成模型实验结果。

## Results / 结果

| Metric | Result | Method |
|---|---:|---|
| Sample count | 48 | 固定原创数据集 |
| Domux format compliance | 81.25% (39/48) † | 七字段与动作枚举校验 |
| End-to-end gate decision accuracy | 93.75% (45/48) † | Domux 输出 + 手工安全策略 vs 48 条人工标签 |
| End-to-end Macro F1 | 0.9369 † | allow / confirm / block |
| High-risk intervention recall | 100% (32/32) | 32 条 confirm + block 被判为非 allow |
| High-risk false-allow rate | 0% (0/32) | confirm + block 中被判 allow 的比例 |
| False intervention rate | 0% (0/16) | 16 条 allow 被干预的比例 |
| Gate latency mean / P95 | 18.02 / 32.52 μs † | `perf_counter_ns`，不含模型推理；P95 为第 45 个排序值 |

类别混淆矩阵：allow 为 16/16 allow；confirm 为 13/16 confirm、3/16 block；
block 为 16/16 block。三条 confirm 被更保守地升级为 block，因此没有产生危险放行。

† 这些数字来自 2026-08-25 已完成的真实 T4 运行记录，但该轮 `domux_raw.jsonl` 尚未
在当前磁盘找到。它们只能作为待复核历史记录；公开提交前必须用 `evidence/` 中可重算的
原始输出重新验证，并以重算结果替换本表。

## Evidence / 运行证据

- GPU、运行时、量化、snapshot 大小：已在免费 Tesla T4 上运行并记录；
- 原始输出与 `safety_report.json`：曾由 Colab 打包为
  `domux-safety-gate-results.zip`（仅日志与指标，不含模型权重或 token），但当前本地仓库
  未找到该包；这是提交前必须修复的证据缺口；
- smoke test：两条 allow 指令分别在 1600.5 ms、1517.8 ms 解析完成；
- 失败模式：历史记录显示 9/48 条模型输出未通过七字段格式／动作枚举校验；安全闸门将
  不合规输出阻断。必须通过保留的原始输出逐条复核。

## Safety, privacy, and licensing / 安全、隐私与许可

- 本闸门是研究原型，不是功能安全、安防或法规认证；
- 生产环境仍需设备白名单、状态校验、身份认证、权限控制、可撤销确认和审计日志；
- 规则只覆盖本案例列出的英文表达，存在同义改写、跨语言和上下文遗漏风险；
- 模型权重受 Gemma 条款约束，代码沿用仓库 Apache-2.0，新增数据为 CC0-1.0；
- 不含 HF token、个人缓存路径、私有家庭数据、内网地址或业务数据。

## Published Hugging Face Discussion / 公开 Discussion

待原始证据重新落盘、真实指标重算并经用户确认后发布；取得 Discussion 编号后再生成
最终 `README.md`。
