<div align="center">
  <img src="assets/iflytek.png" alt="Domux" width="100%">
</div>

<div align="center">
  <b>English</b> | <a href="README_zh.md">简体中文</a>
</div>

---
<div align="center">
  <h1>Domux</h1>
  <p><b>A lightweight multimodal model for pipe-delimited slot filling in smart-home control.</b></p>

  <p>
    <a href="#-model-download"><img src="https://img.shields.io/badge/🤗%20Hugging%20Face-Model-yellow"></a>
    <a href="#-model-download"><img src="https://img.shields.io/badge/🔧%20ModelScope-Model-blue"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-See%20LICENSE-green"></a>
    <a href="#-quick-start"><img src="https://img.shields.io/badge/Inference-vLLM%20%7C%20SGLang-orange"></a>
  </p>
</div>

---

<div align="center">
  <img src="assets/domux.png" alt="Domux Overview" width="90%">
</div>

> 🚀 **An experiment, and an open invitation.** Domux explores a new idea: how far can text semantic parsing go under an aggressive latency budget — keeping end-to-end response under **150ms**? This is an early-stage exploration, and we're sharing it in the hope that others will try it too. If this direction interests you, we'd love for you to follow along and explore it together.

Domux (`Domux-Gemma-4-E2B-it`) is a fine-tuned multimodal language model built on **Gemma-4-E2B-it**. It turns natural-language smart-home commands into structured, pipe-delimited slots. Training combines supervised fine-tuning (SFT) with reinforcement learning via Group Relative Policy Optimization (GRPO) and custom reward functions.

## 📰 News

- **2026.06** — Released training code, reward plugins, and example datasets.
- **2026.06** — Initial release of Domux based on Gemma-4-E2B-it.

## ✨ Key Features

- **Fast response** — Optimized for low-latency inference on edge devices and servers.
- **Structured slot output** — Parses free-form commands into a fixed 7-field pipe-delimited schema.
- **High accuracy** — 98.37% result accuracy with 100% format compliance, outperforming much larger models.
- **Lightweight base** — Built on the compact Gemma-4-E2B-it, suitable for on-device and edge deployment.
- **Multi-action support** — Handles compound commands that map to multiple slot lines.
- **Generalizes across devices** — Handles arbitrary device names within each category, not a fixed whitelist.

## 🏠 Supported Devices

Domux is built on a generalist base model, so device names are **not a closed list** — it handles arbitrary names within each category by generalization. The devices below are representative examples, not the full set: any kind of light (ceiling light, strip light, floor lamp, desk lamp, spotlight, reading light…) works the same way.

| Category | Example Devices | Actions | Attributes (range) |
| --- | --- | --- | --- |
| **Lighting** | any light — strip light, floor lamp, desk lamp, spot light, ceiling light… | turnOn, turnOff, set, adjustUp, adjustDown | brightness (0–100%), color, colorTemperature (3500/4000/5000/6000 K) |
| **Climate** | AC | turnOn, turnOff, set | temperature (16–29 °C), mode (Fan / Dry / Heat / Cool) |
| **Window** | curtain, blind, sheer | turnOn, turnOff, set, adjustUp, adjustDown | position (0–100%) |
| **Audio** | music | turnOn, turnOff, adjustUp, adjustDown | volume (0–100%) |
| **Scene** | presentation mode, movie mode, music video mode… | activate, deactivate | — |

**Colors**: Blue, Red, Green, Yellow, Orange, Pink, Purple, Cyan, Lavender, White, Warm White, Cool White, Sky Blue, and more.

**Fuzzy adjustment**: commands without an explicit value (e.g. *"dim the lamp"*, *"turn it down a bit"*) map to `adjustUp` / `adjustDown` with the value left as `*`, to be resolved downstream.

### Roadmap

This is an early exploration, and we plan to keep expanding it:

- 🔜 **More devices** — broader device categories beyond lighting, climate, window, and audio
- 🔜 **More scenes** — richer scene/mode coverage
- 🔜 **Fuzzy intent** — better handling of vague, implicit, and context-dependent commands

For data format and how to extend coverage, see [training/data/README.md](training/data/README.md).

## 🎬 Demo

The model outputs pipe-delimited slots with 7 fields:

```
action|device|attribute|value|unit|room|floor
```

### Basic Examples

| Input | Output |
| --- | --- |
| Turn on the living room light | `turnOn\|Light\|*\|*\|*\|Living Room\|*` |
| Set bedroom AC to 22 degrees | `set\|AC\|temperature\|22\|Celsius\|Bedroom\|*` |
| Close the curtains 20 percent | `adjustDown\|Curtain\|openness\|20\|Percent\|*\|*` |

### Complex Multi-Attribute Command

**Input:**
```
Turn on the Master Light in the Master Bedroom on the Second Floor, 
set brightness to 80%, color temperature to 4000K, color to Blue, and mode to Reading.
```

**Output:**
```
turnOn|Light|*|*|*|Master Bedroom|Second Floor
set|Light|brightness|80|Percent|Master Bedroom|Second Floor
set|Light|colorTemperature|4000|Kelvin|Master Bedroom|Second Floor
set|Light|color|Blue|*|Master Bedroom|Second Floor
set|Light|mode|Reading|*|Master Bedroom|Second Floor
```

### Multi-Room Scenario

**Input:**
```
Turn off all lights in the Living Room on the Ground Floor, 
set the AC to Cool mode at 24 degrees in the Guest Bedroom, 
and open the curtains halfway in the Dining Room.
```

**Output:**
```
turnOff|Light|*|*|*|Living Room|Ground Floor
set|AC|mode|Cool|*|Guest Bedroom|*
set|AC|temperature|24|Celsius|Guest Bedroom|*
set|Curtain|openness|50|Percent|Dining Room|*
```

Use `*` for unspecified or don't-care fields.

## 📊 Performance

Evaluated on a comprehensive test set of **4,057 samples** across 4 dimensions (single intent, multi-intent, omitted attributes, non-standard naming), benchmarked against **11 mainstream models** including Qwen3.5 series (2B-27B), Gemma 4 series, and leading closed-source APIs (DeepSeek-V4, Claude Haiku 4.5, Gemini 3.5 Flash).

### Key Results

| Metric | Domux (E2B) | Qwen3.5-27B | Gemma 4-31B | DeepSeek-V4 | Claude Haiku 4.5 | Gemini 3.5 Flash |
| --- | --- | --- | --- | --- | --- | --- |
| **Result Accuracy** | **98.37%** | 81.4% | 85.2% | 90.3% | 91.4% | 88.7% |
| Format Compliance | **100.00%** | 97.2% | 97.8% | 99.0% | 99.5% | 98.5% |
| Slot F1 | **99.64%** | 92.8% | 94.5% | 96.1% | 96.8% | 95.3% |
| Intent F1 (Multi) | **98.96%** | 86.7% | 90.5% | 93.5% | 93.7% | 91.5% |
| Latency (Single/Multi) | **130/210 ms** | 1660/2320 ms | 1860/2600 ms | 1200/1680 ms | 900/1260 ms | 700/980 ms |

### Performance by Category

- **Single Intent**: 99.64% accuracy
- **Multi-Intent**: 98.05% accuracy
- **Omitted Attributes**: 99.05% accuracy
- **Non-Standard Naming**: 95.89% accuracy

### Inference Performance (NVIDIA A100)

- **Throughput**: Peak ~5,500 token/s at concurrency 60-80
- **Latency**: Single intent 130ms, multi-intent 210ms (pure inference)
- **Zero Failure Rate**: 100% success across 124,116 concurrent requests

📄 **Full Report**: [Technical Evaluation Report (EN)](Domux_Technical_Evaluation_Report_EN.pdf) | [评测技术报告 (中文)](Domux_评测技术报告_ZH.pdf)

## 📥 Model Download

| Model | Base | Hugging Face | ModelScope |
| --- | --- | --- | --- |
| Domux-Gemma-4-E2B-it | Gemma-4-E2B-it | [🤗 Link](#) | [🔧 Link](#) |

## 🚀 Quick Start

### Hardware

The model runs in **BF16 precision** and requires **20GB+ GPU memory**.

### Installation

```bash
# Option 1: vLLM
pip install vllm

# Option 2: SGLang
pip install "sglang[all]"
```

### Inference

Offline inference with vLLM. Pass the user command directly as the query:

```python
from vllm import LLM, SamplingParams

llm = LLM(model="path/to/model", dtype="bfloat16")
sampling = SamplingParams(temperature=0.0, max_tokens=256)

prompt = "Turn on the Master Light in the Master Bedroom on the Second Floor, set brightness to 80%, color temperature to 4000K, color to Blue, and mode to Reading."
output = llm.chat([{"role": "user", "content": prompt}], sampling)
print(output[0].outputs[0].text)

# Output:
# turnOn|Light|*|*|*|Master Bedroom|Second Floor
# set|Light|brightness|80|Percent|Master Bedroom|Second Floor
# set|Light|colorTemperature|4000|Kelvin|Master Bedroom|Second Floor
# set|Light|color|Blue|*|Master Bedroom|Second Floor
# set|Light|mode|Reading|*|Master Bedroom|Second Floor
```

## 🔧 Deployment

Serve the model as an OpenAI-compatible API with either vLLM or SGLang.

### Serve with vLLM

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

### Serve with SGLang

```bash
python -m sglang.launch_server \
  --model-path path/to/model \
  --host 0.0.0.0 \
  --port 8000 \
  --dtype bfloat16 \
  --context-length 2048
```

### Call the API

Both servers expose the same OpenAI-compatible endpoint:

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

# Output:
'''
turnOn|Light|*|*|*|Master Bedroom|Second Floor
set|Light|brightness|80|Percent|Master Bedroom|Second Floor
set|Light|colorTemperature|4000|Kelvin|Master Bedroom|Second Floor
set|Light|color|Blue|*|Master Bedroom|Second Floor
set|Light|mode|Reading|*|Master Bedroom|Second Floor
'''
```

## 🧩 Output Parser

The model emits raw pipe-delimited text. A lightweight offline parser ([parser/](parser/)) turns it into validated, structured JSON — **pure Python standard library, zero dependencies**.

```bash
# Single or multi-line command → pretty JSON
echo "turnOn|Light|*|*|*|Living Room|*" | python parser/domux_parser.py

# Batch mode: one prediction per line → one JSON object per line
python parser/domux_parser.py --jsonl predictions.txt > parsed.jsonl
```

As a library:

```python
from parser.domux_parser import parse

res = parse("set|AC|temperature|22|Celsius|Bedroom|*")
res.valid          # True
res.slots[0].value # 22 (int)
res.to_json()      # structured JSON string
```

Validation rules (action enum, `*` semantics, line splitting) stay consistent with training. See [parser/README.md](parser/README.md) for output conventions and scope.

## 📂 Repository Structure

```
domux/
├── LICENSE
├── README.md
├── README_zh.md
├── Domux_Technical_Evaluation_Report_EN.pdf
├── Domux_评测技术报告_ZH.pdf
├── assets/
│   ├── iflytek.png
│   └── domux.png
├── parser/
│   ├── README.md
│   ├── domux_parser.py
│   └── test_domux_parser.py
└── training/
    ├── README.md
    ├── requirements.txt
    ├── rewards/
    │   └── reward_plugin_slot.py
    ├── scripts/
    │   ├── train_sft.sh
    │   └── train_grpo.sh
    └── data/
        ├── README.md
        ├── example_sft.jsonl
        └── example_grpo.jsonl
```

## 📄 License

See the [LICENSE](LICENSE) file in this repository.

## 🙏 Acknowledgments

- Base model: [Gemma](https://ai.google.dev/gemma)
- Training framework: [ModelScope-Swift](https://github.com/modelscope/swift)
- Experiment tracking: [SwanLab](https://swanlab.cn/)

## 📌 Citation

If you find Domux useful in your research or applications, please consider citing:

```bibtex
@misc{domux2026,
  title  = {Domux: Slot Filling for Smart-Home Control via SFT and GRPO},
  author = {iFLYTEK},
  year   = {2026},
  howpublished = {\url{https://github.com/iflytek/domux}}
}
```
