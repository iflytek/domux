# Domux safety-gate 最终提交前审计（2026-08-25）

## 结论

**CONDITIONAL PASS（证据与方法已通过；仅待公开发布步骤）**。

当前代码、48 条真实模型输出、运行 metadata、指标报告和公开文案已形成闭环，并通过
本地重算。按照当前任务边界，尚未发布 Hugging Face Discussion、未 push、未创建 PR；
因此正式 `README.md` 的 `channels` 仍缺真实 Discussion URL，官方 case validator 不能在
公开发布前完成最终通过。

## 已核实事实

- 模型：`iFlytekOpenSource/Domux`
- 固定 revision：`6c71a32f4d624cadfd9fce9d10240d8068e53456`
- 真实环境：Google Colab 免费 Tesla T4，NF4，`torch.bfloat16`
- 版本：Python 3.13.15，PyTorch 2.11.0+cu128，transformers 5.15.0，
  accelerate 1.14.0，bitsandbytes 0.50.1，huggingface_hub 1.27.0
- 数据：48 条，allow / confirm / block 各 16 条；SHA-256
  `c124529599fc13664fbe2018e141b6c95c269e4cdd60c4b8d4b87a466d8a277a`
- 原始输出：`evidence/domux_raw.jsonl`
- 环境与生成参数：`evidence/domux_raw.metadata.json`
- 完整评测：`evidence/safety_report.json`

## 最终指标

| 指标 | 结果 |
|---|---:|
| Domux format compliance | 81.25% (39/48) |
| End-to-end gate accuracy | 93.75% (45/48) |
| End-to-end Macro F1 | 0.9369458128 |
| High-risk intervention recall | 100% (32/32) |
| High-risk false-allow rate | 0% (0/32) |
| Safe false-intervention rate | 0% (0/16) |
| Gate latency mean / P95 | 12.09 / 25.07 us |

混淆矩阵：allow `16/16 allow`；confirm `13/16 confirm, 3/16 block`；block
`16/16 block`。三条错误是 confirm 被保守升级为 block，没有高风险误放行。

## 已发现并修复

1. 历史原始输出未落盘：已用相同固定 revision 在免费 T4 最小补跑，恢复三份 evidence。
2. metadata 缺依赖、seed 和数据散列：推理脚本与新 metadata 已补齐。
3. 评测器未拒绝重复／缺失／额外 ID：已 fail closed，并增加回归测试。
4. 文档曾把历史数字标为待复核：已全部替换为可重算结果。
5. 安全指标命名不够清楚：改为 high-risk intervention recall 与 false-allow rate，保留
   旧字段仅作兼容。
6. Domux 与外围规则贡献容易混淆：README 和 Discussion 已明确，格式合规率是最直接的
   Domux 指标，三分类指标属于 Domux 输出加外部规则的集成结果。

## 仍需主动披露的局限

- 48 条样本较小、合成、英文、类别平衡且按规则设计，不能代表生产分布或安全认证。
- 安全语义主要由源命令上的显式规则决定；高三分类指标不能证明 Domux 独立理解安全意图。
- 部分高风险设备超出 Domux 文档设备清单；这些样本测试的是执行边界的安全拒绝。
- 9/48 输出未通过支持的格式／动作校验，全部由 wrapper 阻断。
- 没有中文、ASR 噪声、多用户上下文、设备状态、真实授权系统或物理执行验证。
- Colab 截图可增强评委直观信心，但原始输出和 metadata 已足够重算指标。

## 最值得写进 Discussion 的三个亮点

1. 固定 revision、真实免费 T4、NF4、完整依赖版本与 dataset SHA-256，可追溯。
2. 48 条逐项原始输出和严格 ID 完整性校验，代码到指标可自动重算。
3. fail-closed 边界没有危险误放行，并明确区分 Domux 格式表现与外围安全规则贡献。

## 验证记录

- `python3 -m unittest -v ...`：9/9 通过。
- `verify_evidence.py`：`status=ok`，48 条和主要指标完全一致。
- `py_compile`：通过。
- `scripts/validate_cases.py --self-test`：通过。
- `git diff --check`：通过。
- 长效 HF token 模式扫描：0 命中。
- 模型权重／超过 20 MB 文件扫描：0 命中。

## 发布前剩余动作

1. 发布标题严格为 `[HER Hack-Astron #4] Domux execution safety gate: fail closed on malformed and high-risk commands`。
2. 将真实 Discussion URL 填入 `README.SUBMISSION_DRAFT.md` 的 `channels` 和正文，改名为
   `README.md`。
3. 运行官方 case validator。
4. push fork 并创建 PR；PR description 必须包含 `Ref #20`，不得使用 `Closes #20`。

以上动作尚未执行。
