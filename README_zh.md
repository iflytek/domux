<div align="center">
  <img src="assets/iflytek.png" alt="Domux" width="100%">
</div>

<div align="center">
  <a href="README.md">English</a> | <b>简体中文</b>
</div>

---
<div align="center">
  <h1>Domux</h1>
  <p><b>面向智能家居控制的轻量级低延迟指令理解模型</b></p>

  <p>
    <a href="#-模型下载"><img src="https://img.shields.io/badge/🤗%20Hugging%20Face-模型-yellow"></a>
    <a href="#-模型下载"><img src="https://img.shields.io/badge/🔧%20ModelScope-模型-blue"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-查看%20LICENSE-green"></a>
    <a href="#-快速开始"><img src="https://img.shields.io/badge/推理-vLLM%20%7C%20SGLang-orange"></a>
  </p>
</div>

---

<div align="center">
  <img src="assets/domux.png" alt="Domux Overview" width="90%">
</div>

> 🚀 **一次尝试，也是一份邀请。** Domux 探索了一个新思路：在追求极致响应速度的目标下（端到端响应耗时控制在 **150ms 以内**），文本的语义处理能做到什么程度？这还是非常早期的探索，我们把它分享出来，希望有更多人一起尝试。如果你也对这个方向感兴趣，欢迎关注，一起探索新思路。

Domux (`Domux-Gemma-4-E2B-it`) 是基于 **Gemma-4-E2B-it** 微调的语言模型。它将自然语言智能家居指令转换为结构化的竖线分隔槽位。训练结合了监督微调（SFT）与基于组相对策略优化（GRPO）的强化学习和自定义奖励函数。

## 📰 更新动态

- **2026.06.29** — 发布训练代码、奖励插件和示例数据集。
- **2026.06.25** — 基于 Gemma-4-E2B-it 的 Domux 首次发布。

## ✨ 核心特性

- **快速响应** — 针对边缘设备和服务器的低延迟推理进行优化。
- **结构化槽位输出** — 将自由格式指令解析为固定的 7 字段竖线分隔格式。
- **高准确率** — 98.37% 结果准确率，100% 格式合规性，超越体量大得多的模型。
- **轻量级基座** — 构建于紧凑的 Gemma-4-E2B-it 之上，适合设备端和边缘部署。
- **多动作支持** — 处理映射到多个槽位行的复合指令。
- **跨设备泛化** — 处理每个类别下的任意设备名，而非固定白名单。

## 🏠 支持的设备

Domux 基于具备泛化能力的基座模型，因此设备名**不是一份封闭清单** —— 模型靠泛化能力处理每个类别下的各种设备名。下表只是**代表性举例，并非全部**：各种各样的灯（吸顶灯、灯带、落地灯、台灯、射灯、阅读灯……）都能同样处理。

| 类别 | 设备举例 | 动作 | 属性（取值范围） |
| --- | --- | --- | --- |
| **灯具** | 任意灯具 —— 灯带、落地灯、台灯、射灯、吸顶灯…… | turnOn, turnOff, set, adjustUp, adjustDown | brightness 亮度（0–100%）、color 颜色、colorTemperature 色温（3500/4000/5000/6000 K） |
| **温控** | AC（空调） | turnOn, turnOff, set | temperature 温度（16–29 °C）、mode 模式（Fan / Dry / Heat / Cool） |
| **窗饰** | 窗帘、百叶窗、纱帘 | turnOn, turnOff, set, adjustUp, adjustDown | position 开合位置（0–100%） |
| **音频** | 音乐 | turnOn, turnOff, adjustUp, adjustDown | volume 音量（0–100%） |
| **场景** | 演示模式、电影模式、音乐视频模式…… | activate, deactivate | — |

**颜色**：Blue、Red、Green、Yellow、Orange、Pink、Purple、Cyan、Lavender、White、Warm White、Cool White、Sky Blue 等。

**模糊调整**：不带明确数值的指令（如 *"dim the lamp"*、*"turn it down a bit"*）会映射为 `adjustUp` / `adjustDown`，值字段留空为 `*`，交由下游解析。

### 路线图

作为早期探索，Domux 仍在持续演进，后续计划聚焦三个方向：

- 🔜 **更广的设备覆盖** — 在灯具、温控、窗饰、音频之外扩展更多品类
- 🔜 **更丰富的场景** — 支持更多场景与模式
- 🔜 **更强的模糊意图理解** — 更好地处理含糊、隐含和依赖上下文的指令

## 🎬 示例演示

模型输出包含 7 个字段的竖线分隔槽位：

```
action|device|attribute|value|unit|room|floor
```

### 基础示例

| 输入 | 输出 |
| --- | --- |
| Turn on the living room light（打开客厅的灯） | `turnOn\|Light\|*\|*\|*\|Living Room\|*` |
| Set bedroom AC to 22 degrees（将卧室空调设为 22 度） | `set\|AC\|temperature\|22\|Celsius\|Bedroom\|*` |
| Close the curtains 20 percent（关闭窗帘 20%） | `adjustDown\|Curtain\|openness\|20\|Percent\|*\|*` |

### 复杂多属性指令

**输入：**
```
Turn on the Master Light in the Master Bedroom on the Second Floor, 
set brightness to 80%, color temperature to 4000K, color to Blue, and mode to Reading.
（打开二楼主卧的主灯，将亮度设为 80%，色温设为 4000K，颜色设为蓝色，模式设为阅读）
```

**输出：**
```
turnOn|Light|*|*|*|Master Bedroom|Second Floor
set|Light|brightness|80|Percent|Master Bedroom|Second Floor
set|Light|colorTemperature|4000|Kelvin|Master Bedroom|Second Floor
set|Light|color|Blue|*|Master Bedroom|Second Floor
set|Light|mode|Reading|*|Master Bedroom|Second Floor
```

### 多房间场景

**输入：**
```
Turn off all lights in the Living Room on the Ground Floor, 
set the AC to Cool mode at 24 degrees in the Guest Bedroom, 
and open the curtains halfway in the Dining Room.
（关闭一楼客厅的所有灯，将客房空调设为制冷模式 24 度，将餐厅窗帘打开一半）
```

**输出：**
```
turnOff|Light|*|*|*|Living Room|Ground Floor
set|AC|mode|Cool|*|Guest Bedroom|*
set|AC|temperature|24|Celsius|Guest Bedroom|*
set|Curtain|openness|50|Percent|Dining Room|*
```

使用 `*` 表示未指定或无关字段。

## 📊 Benchmark 评估

在涵盖 4 个维度（单意图、多意图、属性省略、非标准命名）的 **4,057 个样本**的综合测试集上进行评估，与 **11 个主流模型**进行基准对比，包括 Qwen3.5 系列（2B-27B）、Gemma 4 系列以及领先的闭源 API（DeepSeek-V4、Claude Haiku 4.5、Gemini 3.5 Flash）。

### 核心结果

| 指标 | Domux (E2B) | Qwen3.5-27B | Gemma 4-31B | DeepSeek-V4 | Claude Haiku 4.5 | Gemini 3.5 Flash |
| --- | --- | --- | --- | --- | --- | --- |
| **结果准确率** | **98.37%** | 81.4% | 85.2% | 90.3% | 91.4% | 88.7% |
| 格式合规性 | **100.00%** | 97.2% | 97.8% | 99.0% | 99.5% | 98.5% |
| Slot F1 | **99.64%** | 92.8% | 94.5% | 96.1% | 96.8% | 95.3% |
| Intent F1 (多意图) | **98.96%** | 86.7% | 90.5% | 93.5% | 93.7% | 91.5% |
| 延迟 (单/多) | **130/210 ms** | 1660/2320 ms | 1860/2600 ms | 1200/1680 ms | 900/1260 ms | 700/980 ms |

### 分类性能

- **单意图**：99.64% 准确率
- **多意图**：98.05% 准确率
- **属性省略**：99.05% 准确率
- **非标准命名**：95.89% 准确率

### 推理性能（NVIDIA A100）

- **吞吐量**：并发 60-80 时峰值约 5,500 token/s
- **延迟**：单意图 130ms，多意图 210ms（纯推理）
- **零失败率**：124,116 个并发请求中 100% 成功

📄 **完整报告**：[Technical Evaluation Report (EN)](Domux_Technical_Evaluation_Report.pdf) | [评测技术报告 (中文)](Domux_Technical_Evaluation_Report_zh.pdf)

## 📥 模型下载

| 模型 | 基座 | Hugging Face | ModelScope |
| --- | --- | --- | --- |
| Domux-Gemma-4-E2B-it | Gemma-4-E2B-it | [🤗 链接](#) | [🔧 链接](#) |

## 🚀 快速开始

### 硬件

模型以 **BF16 精度**运行，单卡部署需要 **20GB 及以上显存**。

### 安装

```bash
# 方式 1：vLLM
pip install "vllm==0.22.0"

# 方式 2：SGLang
pip install "sglang[all]==0.5.12"
```

### 推理

使用 vLLM 进行离线推理。直接将用户指令作为 query 输入：

```python
from vllm import LLM, SamplingParams

llm = LLM(model="path/to/model", dtype="bfloat16")
sampling = SamplingParams(temperature=0.0, max_tokens=256)

prompt = "Turn on the Master Light in the Master Bedroom on the Second Floor, set brightness to 80%, color temperature to 4000K, color to Blue, and mode to Reading."
output = llm.chat([{"role": "user", "content": prompt}], sampling)
print(output[0].outputs[0].text)

# 输出：
# turnOn|Light|*|*|*|Master Bedroom|Second Floor
# set|Light|brightness|80|Percent|Master Bedroom|Second Floor
# set|Light|colorTemperature|4000|Kelvin|Master Bedroom|Second Floor
# set|Light|color|Blue|*|Master Bedroom|Second Floor
# set|Light|mode|Reading|*|Master Bedroom|Second Floor
```

## 🔧 部署指南

使用 vLLM 或 SGLang 将模型部署为 OpenAI 兼容的 API 服务。

### 使用 vLLM 部署

```bash
python -m vllm.entrypoints.openai.api_server \
  --model path/to/model \
  --served-model-name domux \
  --host 0.0.0.0 \
  --port 8000 \
  --dtype bfloat16 \
  --max-model-len 2048 \
  --gpu-memory-utilization 0.9
```

### 使用 SGLang 部署

```bash
python -m sglang.launch_server \
  --model-path path/to/model \
  --host 0.0.0.0 \
  --port 8000 \
  --dtype bfloat16 \
  --context-length 2048
```

### 调用 API

两种服务均暴露相同的 OpenAI 兼容接口：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="EMPTY"
)

response = client.chat.completions.create(
    model="domux",
    messages=[
        {"role": "user", "content": "Turn on the Master Light in the Master Bedroom on the Second Floor, set brightness to 80%, color temperature to 4000K, color to Blue, and mode to Reading."}
    ],
    temperature=0.0
)

res = response.choices[0].message.content
print(res)

# 输出：
'''
turnOn|Light|*|*|*|Master Bedroom|Second Floor
set|Light|brightness|80|Percent|Master Bedroom|Second Floor
set|Light|colorTemperature|4000|Kelvin|Master Bedroom|Second Floor
set|Light|color|Blue|*|Master Bedroom|Second Floor
set|Light|mode|Reading|*|Master Bedroom|Second Floor
'''
```

## 📄 许可证

查看本仓库中的 [LICENSE](LICENSE) 文件。

## 🙏 致谢

- 基座模型：[Gemma](https://ai.google.dev/gemma)
- 训练框架：[ModelScope-Swift](https://github.com/modelscope/swift)
- 实验跟踪：[SwanLab](https://swanlab.cn/)
