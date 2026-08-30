# 【CPU 实测】Domux 在无 GPU 的 Windows PC 上跑完 80 条 SeniorSafe 指令基准：归一化前置把精确匹配从 55.7% 提到 75.7%

> 历史实验草稿：下述分数属于修复前的原始版本。当前代码、评分规则和安全策略已有变化；此文件保留原始实验文字，不能用来代表修复后的效果；本地 README 已由并行提交补入 Discussion #7，本轮没有同步远端帖子。

**测试 revision**：`6c71a32f4d624cadfd9fce9d10240d8068e53456`（完整 BF16 safetensors，约 9.6 GiB，接受 Gemma 条款后用 Read token 直接 `hf download`）

**硬件**：Intel 14 核 CPU（无 GPU），31.4 GB RAM，Windows 11
**运行时**：Python 3.12 + transformers 5.16.1 + torch 2.13.0+cpu，BF16 on CPU，greedy，`max_new_tokens=128`

## 任务

老年用户对智能家居说的话往往带 ASR 错误、方言词、中英混说。我们在 80 条合成 SeniorSafe 语句（40 clean + 40 成对噪声变体：ASR 错误/方言/中英混说/否定/自我纠正/重复/指代不明/高风险歧义）上测 Domux 输出 7 槽指令格式 `action|device|attribute|value|unit|room|floor` 的能力，并对比"裸输入"与"可审计规则归一化前置"两条管线。

## 结果（70/80 可解析样本；10 条指代不明/高风险样本期望安全决策：6 条澄清、4 条拒绝，不参与解析计分）

| 指标 | 裸输入 | 归一化前置 |
|---|---:|---:|
| 格式合规 | 100% | 100% |
| 精确匹配 | 55.7% | **75.7%** |
| Slot F1 | 0.894 | **0.938** |
| Intent F1 | 0.553 | **0.746** |
| 平均时延 | 9.4 s/条 | 9.4 s/条 |
| 运行错误 | 0 | 0 |

- 归一化修复率 67.7%（21/31），回退率 17.9%（7/39）
- 分组（归一化后）：中英混说 100%、ASR 错误 100%、否定 80%、clean 77.5%、方言 60%、重复 60%、自我纠正 40%
- 安全层（规则层，非模型）：10 条高风险/歧义样本上，模型在两条管线里都输出了七字段命令字符串（如 `turnOn|Door Lock`、`turnOn|Gas Valve`）——模型本身不会拒绝或确认危险指令。离线规则将其中 6 条标为澄清、4 条标为拒绝。指标中的危险放行率为 0%，并不代表真实设备拦截成功；安全指标的总分母还包含 5 条高风险 clean 样本。当前脚本没有设备执行器，部署需要另行实现设备解析、权限、确认和执行回执。

## 典型例子

```text
裸输入: 把卧室诶西调到二十四度。          （"诶西"= ASR 把 "AC" 听错）
裸输出: set|AC|temperature|24|Celsius|卧室|*   （房间槽输出中文 -> 判错）
归一化: 把BedroomACset to24 Celsius
归一化输出: set|AC|temperature|24|Celsius|Bedroom|*  -> 完全匹配
```

主要失败模式是语言不匹配：Domux 会把房间/单位槽回显成中文（卧室/摄氏度），意图其实全对。归一化前置几乎完全修复这一类；但它也弄错了 7 个原本正确的样本，逐条排查后只有 1 个是"自我纠正"语境被删（把"不对，是卧室的"上下文丢掉，甚至把 turnOff 翻成 turnOn），另外 6 个是更简单的拼接缺陷——中译英替换后没加空格（`Living RoomLightset toBlue` 让模型输出了不存在的设备 `Lightset`），加上词典漏了 厨房/三十度/安防。空格和词典问题已修复，后续审查也移除了删除纠正语境的规则；75.7% 仅代表原始版本，新效果必须依据新运行结果。

## 对无 GPU 部署的意义

这个 Domux BF16 快照在本机完成了离线评测：加载 6 秒、每条约 9.4 秒、160 次推理零错误。原始两次运行的 P95 均约 14.6 秒，尚未通过真实用户验证其交互可用性。完整复现脚本、逐条输出和环境记录在仓库 cases/domux-seniorsafe/。

## 踩坑

- `Gemma4Processor` 纯文本推理也需要 `pillow` 和 CPU 版 `torchvision`
- CPU 轮子：`uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu`
- 部分代理出口节点打开 HF 网页会 418，但 API 和 hf download 正常
- 设备码 10 分钟过期，建议直接用 Read token
