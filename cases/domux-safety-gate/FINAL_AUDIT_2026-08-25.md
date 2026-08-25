# Domux Safety Gate 最终提交前审计（2026-08-25）

## 结论

**CONDITIONAL PASS**。真实性、本地可重算性、方法披露和权重/secret 边界已达到
可公开审核状态。但安全稳健性只能评为研究原型：冻结后独立 held-out 仅 `51/84`，
误干预 `18/28`。它适合作为“主动发现失败并公开局限”的比赛案例，
不适合声称产品安全。

尚未发布 Hugging Face Discussion，因此真实 Discussion URL 不存在，正式 `README.md`
与官方 full validator 必须留到发布步骤。本轮没有 push，没有创建 PR。

## 根因

1. **Parser metric bug**：`81.25%` 是 v1 action-vocabulary acceptance，不是 format compliance。
2. **Input-only safety bug**：v1 安全决策检查 input，不检查待执行 output semantics。
3. **Evaluation circularity**：48 条与规则高度同构；constant valid output 下 v1 仍是 48/48。
4. **Metric degeneracy**：null output 可以给出 32/32 risky interception，但完全来自 fail closed，
   并同时误拦 16/16 allow。

## 修复与冻结

- v1 baseline commit: `63f60d1884059379784c06ae35e84838d5525f9d`
- v2 pre-held-out frozen commit: `ad243f999d75bce3f1be35667ff3eaa734ef70e5`
- frozen `safety_gate.py` SHA-256:
  `4cc417053fe93d70f54d2b943a5e485a8a6bb09d69723dfe42cae2df88db3924`
- held-out 结果出现后没有修改 v2。

v2 分离 syntax / action recognition / semantic support，逐行使用七字段，并增加 output risk、
input-output mismatch、range/unit/location checks 和明确 interception mode。

## 可重算数字

| 层 | 结果 |
|---|---:|
| Structural schema | 48/48 samples; 53/53 lines |
| Legacy action acceptance | 39/48 |
| v2 semantic support | 47/48 samples; 52/53 lines |
| v1 / parser-fixed / v2 frozen-48 exact | 45/48 / 48/48 / 47/48 |
| Real-output cross-pair dangerous allow | 160/256 / 256/256 / 0/256 |
| Development fault suite exact | 10/20 / 7/20 / 20/20 |
| Independent held-out exact | v2 51/84; Macro F1 0.6152 |
| Held-out risky intervention | 49/56; Wilson 95% CI 76.37–93.81% |
| Held-out block passed as allow | 0/23 |
| Held-out false intervention | 18/28; Wilson 95% CI 45.83–79.29% |

## 仍未解决

- `set|device|power|off` 可将“关闭”编码到 attribute/value，v2 的 action-centric 禁用语义
  对此覆盖不足；8 条 block 标签在 held-out 中被降为 confirm。
- 同义改写、中文歧义和 room/floor 同义词仍有召回缺口。
- `set` vs `turnOn/turnOff` 和自定义设备 taxonomy 导致较多保守误干预。
- held-out 已是最终 test set，不得用来调 v2；如建 v3，必须再生成新 test set。
- 没有身份、授权、设备状态、确认 UI、rate limit、审计日志或物理设备验证。

## 已删除/收紧的 claim

- `81.25% format compliance` -> legacy action-vocabulary acceptance。
- `unsupported output/device` 导致 v1 生命安全 block -> 实际是 input regex。
- NF4 导致 parser failure -> 没有 BF16 对照，删除因果归因。
- `100% safe` / `production-safe` / `Domux safety accuracy` -> 禁止使用。

## 审计判定

- Methodological credibility: **91/100**
- Reproducibility: **94/100**
- Safety robustness: **62/100**
- Competition differentiation: **89/100**

今天是否建议作为诚实的研究案例提交：**YES**。
是否建议将 v2 接入真实设备：**NO**。
