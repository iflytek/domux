# 外部审核包：Domux 执行前安全闸门

> 用途：将本文件和下列源码一并交给 GPT、Claude 或人工审阅者，要求他们只基于
> 可复现证据评估，不把规则单测或宣传性描述误判为模型能力证明。

## 1. 审核目标

审核这个参赛案例是否满足以下条件：

1. 实验是否真实运行了固定版本的 `iFlytekOpenSource/Domux`，而非伪造输出；
2. 安全闸门的规则、数据标注、评测代码和报告之间是否一致；
3. 文档中的指标是否被实现和运行证据支持；
4. 是否存在安全、隐私、许可证、可复现性或参赛规则风险；
5. 在提交 Hugging Face Discussion 和 GitHub PR 前，哪些问题必须修复，哪些可作为局限披露。

## 2. 项目与边界

- 案例：在 Domux 的结构化家居控制输出与设备执行器之间加入三分类安全闸门。
- 标签：`allow`（直接执行）、`confirm`（要求确认）、`block`（拒绝）。
- 数据：48 条原创英文合成家居指令；三类各 16 条；不含真人家庭、地址或账户数据。
- 明确不主张：功能安全认证、真实家庭部署效果、生产泛化能力或模型本身“安全”。
- 参赛提交尚未发生：没有公开 Discussion、没有 GitHub PR、没有上传模型权重。

## 3. 已验证运行事实

以下内容来自 2026-08-25 Google Colab 免费 Tesla T4 的真实输出：

- 模型：`iFlytekOpenSource/Domux`
- 固定 revision：`6c71a32f4d624cadfd9fce9d10240d8068e53456`
- 快照大小：10,279,032,574 bytes
- 推理：NF4 4-bit，`torch.bfloat16` compute dtype，Python 3.13.15，PyTorch 2.11.0+cu128。
- 单元测试：9/9 通过。
- 48 条全量评测：
  - 七字段结构合规率：100%（48/48 samples；53/53 lines）
  - v1 action whitelist 接受率：81.25%（39/48）
  - 闸门决策准确率：93.75%（45/48）
  - Macro F1：0.9369458128
  - 高风险拦截召回率：100%（32/32）
  - 危险误放行率：0%（0/32）
  - 安全指令误干预率：0%（0/16）
  - 闸门平均/P95 延迟：12.09 / 25.07 μs（不含模型推理）
- 混淆矩阵：allow 16→allow；confirm 13→confirm、3→block；block 16→block。

重要解释：三条应为 `confirm` 的样本被更保守地判为 `block`；没有高风险样本被判为
`allow`。全部 48 个样本、53 个非空输出行都满足七字段结构；9 个样本仅因 action 不在
v1 手写词表而被 parser 拒绝。v1 安全语义来自 input regex，而非 output fields。

## 4. 提供给审核者的文件

请附上本目录内这些文件：

```text
README.draft.md                    # 参赛案例叙述及局限
RUN_RESULTS_2026-08-25.md          # 真实运行记录与指标
safety_gate.py                     # 安全闸门实现
evaluate_safety.py                 # 指标计算和失败处理
run_transformers.py                # 固定 revision 的真实模型推理脚本
example_safety_commands.jsonl      # 48 条数据及人工标签
DATASET_CARD.md                    # 数据卡与许可说明
test_safety_gate.py
test_dataset.py
test_evaluate_safety.py            # 覆盖评测器输入完整性与重复 ID；总计 9 项测试通过
evidence/domux_raw.jsonl           # 48 条真实原始输出
evidence/domux_raw.metadata.json   # 固定 revision、依赖、seed、GPU 与 dataset SHA-256
evidence/safety_report.json        # 可重算完整报告
verify_evidence.py                 # 原始输出与报告一致性校验
```

`colab_run.ipynb` 已被追踪且不含执行输出或 token。原始输出、metadata 和完整报告已由
2026-08-25 的最小真实补跑恢复到 `evidence/`，并通过 `verify_evidence.py`。可公开截图仍是
可选增强证据，不是指标重算所必需的来源。

## 5. 请审核者逐项回答

1. `safety_gate.py` 是否能对解析失败、模糊范围、高后果设备和明显危险指令采取
   fail-closed 行为？请给出具体反例（如有）。
2. 数据标签与规则定义是否一致？是否存在数据泄漏、重复样本、过度贴合规则或可被
   文本表面特征投机的问题？
3. `evaluate_safety.py` 是否正确计算表中每项指标？特别检查：
   - `unsafe_pass_rate` 的分母是否为 32 条高风险样本；
   - `safety_intercept_recall` 是否把 `confirm` 与 `block` 都视为拦截；
   - `format_compliance` 与最终安全决策是否被错误混为同一指标。
4. `run_transformers.py` 是否确实固定 Hugging Face revision、保存逐条原始输出，且没有
   将 token、权重或私人路径写入结果？
5. 指标表述是否夸大？请把每个不被证据支持的表述改成审慎表述。
6. 依据 Domux Issue #20 的要求，案例还缺哪些提交材料（如 Discussion 链接、PR 结构、
   日志截图、原始输出包）？
7. 最终给出以下审查等级之一：
   - `可提交`：仅需补齐公开发布步骤；
   - `小修后可提交`：列出精确修改；
   - `不建议提交`：列出阻断性问题与复现实验方案。

8. 请明确区分：本案例是否证明了 Domux 的格式输出表现，还是主要证明了命令文本上的
   手工安全规则。若后者成立，指出文档中需要收紧的因果表述。

## 6. 审核约束

- 不得要求或接收 Hugging Face token、浏览器 Cookie、账号密码、模型权重或私人文件路径。
- 不得把“9/9 单元测试通过”误写为“模型安全性得到证明”。
- 不得把 48 条原创合成英文样本的结果泛化为真实家庭、中文指令、多设备并发或长期部署效果。
- 若发现不一致，应引用具体文件、函数、数据 id 或指标字段，不能泛泛而谈。

## 7. 建议回复格式

```markdown
## 结论
可提交 / 小修后可提交 / 不建议提交

## 必须修复
- [文件:行/函数] 问题 → 建议修改

## 非阻断性局限
- 说明

## 指标与实现核对
| 断言 | 证据 | 结论 |
|---|---|---|

## 参赛提交清单
- [ ] Hugging Face Discussion
- [ ] 公开原始输出/日志证据
- [ ] `cases/<id>/README.md`
- [ ] GitHub PR（正文含 `Ref #20`，不含权重）
```
