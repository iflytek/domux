---
title: Output-aware fail-closed gate reveals and intercepts Domux execution mismatches
author: yangmengze608-afk
date: 2026-08-25
category: safety-boundary-integration
testedRevision: 6c71a32f4d624cadfd9fce9d10240d8068e53456
runtime: transformers 5.15.0, PyTorch 2.11.0+cu128, Python 3.13.15, NF4
hardware: Google Colab free Tesla T4, 15 GB VRAM, Linux 6.6.122+
downloadSource: huggingface
channels:
  - [INSERT_PUBLIC_DOMUX_DISCUSSION_URL]
---

# 从漂亮指标到真实失败：Domux 输出感知 Fail-Closed 安全闸门

> 发布前最终稿。真实 Discussion URL 尚未产生；发布后只替换括号内 URL，
> 然后将本文件复制为 `README.md`。不得写入 token、权重、缓存路径或私有家庭数据。

## Task / 真实任务

Domux 把自然语言家居指令转换成七字段结构化输出。但“结构合法”不等于
“可以执行”。本案例在 Domux 与假想设备执行器之间加入安全闸门：

```text
Natural-language command -> Domux -> seven-field output
    -> structural parser -> input risk -> output risk -> consistency checks
    -> allow / confirm / block -> executor
```

第一版防的是用户说了什么；第二版开始检查模型真正准备执行什么。

## Real Domux run / 真实运行

- Model: `iFlytekOpenSource/Domux`
- Fixed revision: `6c71a32f4d624cadfd9fce9d10240d8068e53456`
- Hardware: Google Colab free Tesla T4, 15 GB VRAM
- Precision: NF4 4-bit; compute dtype `torch.bfloat16`
- Generation: greedy, `do_sample=False`, `max_new_tokens=256`, seed `20260825`
- Runtime: Python 3.13.15, PyTorch 2.11.0+cu128, transformers 5.15.0
- Frozen evidence: 48 条输入、53 行非空原始输出；本次修复没有重跑或改写它们。

这 48 条是手工设计、类别平衡的 **development/policy regression suite**，
不是独立泛化 benchmark。

## Why I stopped trusting v1 / 为什么不再相信 93.75%

v1 得到 `45/48 = 93.75%`。但受控消融显示：给全部 48 条输入同一个合法低风险
output，v1 反而得到 `48/48 = 100%`。这证明旧指标主要在重述 input-side rule，
不能证明 Domux output semantics 对安全决策有贡献。

同时，旧报告的 `81.25% format compliance` 也是命名错误：

- 七字段结构合规：`48/48` samples，`53/53` lines；
- v1 旧 action whitelist 接受：`39/48 = 81.25%`；
- v2 action 可识别：`48/48`；
- v2 完整语义支持：`47/48` samples，`52/53` lines。

`unlock` / `lock` / `disable` / `override` / `bypass` / `keep` 是结构合法的 action，
不应被 parser 宣称为 malformed；它们应进入安全语义层。

## v2 design / 输出感知设计

v2 逐行读取 `action | device | attribute | value | unit | room | floor`，并取最高严重度：

- life safety 关闭/绕过与燃气流开启：`block`；
- 周界访问、高后果 utility、有界加热和未知低风险语义：至少 `confirm`；
- 低风险 input 却引入 gas / life-safety disable / unlock / extreme heat：`block`；
- 空或非七字段输出：fail closed；
- 温度阈值是明示的 demo policy，不冒充厂商通用标准。

## Controlled comparison / 受控对照

| Test | v1 original | Parser-fixed v1 | v2 output-aware |
|---|---:|---:|---:|
| Frozen 48 exact accuracy | 45/48 (93.75%) | 48/48 (100%) | 47/48 (97.92%) |
| Frozen 48 Macro F1 | 0.9369 | 1.0000 | 0.9791 |
| Constant valid output accuracy | 48/48 (100%) | 48/48 (100%) | 33/48 (68.75%) |
| Null output accuracy | 16/48 (33.33%) | 16/48 (33.33%) | 16/48 (33.33%) |
| Null output false interventions | 16/16 | 16/16 | 16/16 |
| Real-output mismatch intercepted | 96/256 | 0/256 | 256/256 |
| Synthetic fault suite exact | 10/20 | 7/20 | 20/20 |

v2 在 frozen 48 上的唯一 exact error 是将“关闭前门摄像头”从 `confirm`
更保守地升级为 `block`。这不是 dangerous allow，但是明确的决策校准偏差。

## Real-output cross-pair mismatch attack / 真实输出交叉错配

攻击不伪造 Domux output：取 frozen run 中 16 条 allow input，与 16 条在 block case 上
真实观察到且互不重复的 output 做全笛卡尔积，得到 256 个 routing/state-association
mismatch。它模拟输出错绑定，不是原 48 条的模型分布。

- v1 original dangerous allow: `160/256`；其余 `96/256` 只因旧 action whitelist fail closed；
- parser-fixed v1 dangerous allow: `256/256`；
- v2 dangerous allow: `0/256`，output-semantic interception `256/256`，Wilson 95% CI
  `[98.52%, 100%]`。

真实 observed output pool 包括 `turnOn|Gas Valve|...`、`turnOff|Smoke Alarm|...`、
`pause|Gas Leak Sensor|...` 与 `set|Heater|temperature|45|Celsius|...`。这些 output
原本不是在 benign input 下生成；错配是攻击构造。

## Independent held-out / 冻结后一次性独立测试

v2 在原始 commit `ad243f999d75bce3f1be35667ff3eaa734ef70e5` 冻结后，一个被明确禁止
读取代码、regex、tests、旧数据和 evidence 的独立生成器创建了 84 条新的
synthetic gate held-out。评测只运行一次，结果出现后没有再改 gate。
为满足 DCO，PR 提交前仅重写了 commit message；远端可解析的等价冻结 commit 是
`f7186768855398d13ecb5a0b205db02f68190708`，两者 tree hash 均为
`b97f29f78d95630196e1298e565148e08ef61517`。

| Held-out metric | Result |
|---|---:|
| Exact accuracy | 51/84 = 60.71% (Wilson 95% CI 50.02–70.47%) |
| Macro F1 | 0.6152 |
| Risky intervention | 49/56 = 87.50% (95% CI 76.37–93.81%) |
| Risky labels passed as allow | 7/56 = 12.50% |
| Block-labelled cases passed as allow | 0/23 |
| Block exact recall | 15/23 = 65.22% (8 条降为 confirm) |
| False intervention on allow | 18/28 = 64.29% (95% CI 45.83–79.29%) |

| Category | Exact |
|---|---:|
| clean | 5/12 |
| high_consequence | 10/12 |
| paraphrase | 6/12 |
| multilingual | 8/12 |
| ambiguous | 8/12 |
| multi_device | 6/12 |
| output_mismatch | 8/12 |

该结果是评审应当看到的主要 limitation：v2 对 `set|device|power|off` 这类属性编码的
life-safety semantics 覆盖不足；对同义改写、中文歧义、位置同义词和 `set` vs `turnOn`
标准化过度保守。这个 held-out 不得被重标或修改；若未来建 v3，它将成为
development set，而 v3 必须再使用新的独立 test set。

## Limitations / 局限

- v2 是可审计的规则原型，不是功能安全、安防或法规认证。
- 48 条 regression 与 20 条 fault suite 是 development material；其中的 100% 不是泛化声明。
- 84 条 held-out 是独立生成的 synthetic gate test，不是 Domux 模型准确率。
- 规则仍然依赖有限 taxonomy 和 input regex，而 held-out 已证明召回与误干预都不足。
- 不包含设备 inventory/state validation、身份与权限、可撤销确认 UI、rate limit 或审计日志。
- NF4 只是实际运行配置；没有 matched BF16 control，因此不做量化因果归因。
- 旧 GPU run 的 warm-up 复用正式集前两条；生成是无状态 greedy，没有发现结果污染证据。
  未来脚本已改用不进入评测集的独立 warm-up prompt，未为此重跑旧实验。

## Reproduction / 复现

```bash
python -m unittest -v \
  test_safety_gate.py test_safety_gate_v1.py test_dataset.py test_evaluate_safety.py
python verify_evidence.py --dataset example_safety_commands.jsonl \
  --responses evidence/domux_raw.jsonl --report evidence/safety_report.json
python run_v2_experiments.py
python evaluate_heldout.py --verify-only
python verify_v2_evidence.py
```

`evidence/domux_raw.*` 保留原始真实 Domux run；`evidence/v2/` 包含可重算消融、
256 组真实输出错配、20 条开发 fault suite、84 条独立 held-out、生成提示、
frozen commit、Wilson CI 与逐条失败。

## Published Hugging Face Discussion / 公开 Discussion

[INSERT_PUBLIC_DOMUX_DISCUSSION_URL]

上述 URL 必须与 frontmatter `channels` 完全一致，才能生成正式 `README.md`。

## Safety, privacy, and licensing / 安全、隐私与许可

案例不含 HF token、Cookie、密码、权重、私有缓存路径或真实家庭数据。
新增 synthetic data 按 CC0-1.0 公开；Domux/Gemma 仍受其原许可条款约束，仓库代码为 Apache-2.0。
