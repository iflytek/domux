# DRAFT — Domux 执行前安全闸门

> 本文件是实验草稿，不是参赛结果。所有带 `待真实运行` 的字段必须由固定
> Hugging Face revision 的真实 GPU 输出替换后，才能发布 Discussion 或提交 PR。

## Task / 真实任务

Domux 把自然语言转换成可执行的七字段家居控制指令，但结构化格式正确并不等于
动作适合直接执行。本案例在 Domux 与设备执行器之间加入一个保守安全闸门：

- 明确、低后果指令：`allow`；
- 门锁／车库门／发热设备／阀门、全屋范围、代词歧义或超出文档范围的温度：
  `confirm`；
- 禁用生命安全传感器、绕过锁／报警／确认、开启燃气阀或让发热设备无限运行：
  `block`。

目标不是宣称获得安全认证，而是证明真实集成不能把模型的结构化输出直接等同于
设备执行许可。

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
- Snapshot size: 待真实运行
- Precision: 默认 NF4 4-bit；实际 GPU 与 compute dtype 待真实运行

模型权重、HF token、个人缓存路径和私有端点不会进入 GitHub。

## Method / 方法

1. 在 GPU 上先完成 2 条 smoke test，再运行全部 48 条；
2. `temperature=0` 等价的 greedy generation，`max_new_tokens=256`；
3. 前 2 条作为 warm-up，正式结果仍覆盖全部 48 条；
4. 保存逐条原始 Domux 输出和单条生成延迟；
5. 先校验每行 7 字段与动作枚举，再执行安全闸门；
6. 报告格式合规率、三分类准确率／Macro F1、安全拦截召回率、危险放行率、
   安全指令误干预率，以及安全闸门平均／P95 延迟；
7. 单独展示模型解析失败与策略失败，不把规则单元测试当成模型实验结果。

## Results / 结果

| Metric | Result | Method |
|---|---:|---|
| Sample count | 48 | 固定原创数据集 |
| Domux format compliance | 待真实运行 | 七字段与动作枚举校验 |
| Gate decision accuracy | 待真实运行 | 48 条人工标签 |
| Macro F1 | 待真实运行 | allow / confirm / block |
| Safety intercept recall | 待真实运行 | 32 条 confirm + block |
| Unsafe pass rate | 待真实运行 | 高风险样本被判 allow 的比例 |
| False intervention rate | 待真实运行 | 16 条 allow 被干预的比例 |
| Gate latency mean / P95 | 待真实运行 | `perf_counter_ns`，不含模型推理 |

## Evidence / 运行证据

- GPU、运行时、量化、snapshot 大小：待真实运行；
- 原始输出：待真实运行；
- 日志／截图：待真实运行；
- 失败案例：待真实运行。

## Safety, privacy, and licensing / 安全、隐私与许可

- 本闸门是研究原型，不是功能安全、安防或法规认证；
- 生产环境仍需设备白名单、状态校验、身份认证、权限控制、可撤销确认和审计日志；
- 规则只覆盖本案例列出的英文表达，存在同义改写、跨语言和上下文遗漏风险；
- 模型权重受 Gemma 条款约束，代码沿用仓库 Apache-2.0，新增数据为 CC0-1.0；
- 不含 HF token、个人缓存路径、私有家庭数据、内网地址或业务数据。

## Published Hugging Face Discussion / 公开 Discussion

待真实实验完成并经用户确认后发布；取得 Discussion 编号后再生成最终 `README.md`。
