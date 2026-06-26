# 🎓 Training Guide

<div align="center">

**Complete training pipeline for Domux-Gemma slot filling model**

![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![PyTorch](https://img.shields.io/badge/PyTorch-2.0+-ee4c2c.svg)
![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)

</div>

---

## 📋 Table of Contents

- [🎯 Overview](#-overview)
- [🚀 Training Pipeline](#-training-pipeline)
  - [Stage 1: Supervised Fine-Tuning (SFT)](#stage-1-supervised-fine-tuning-sft)
  - [Stage 2: GRPO Reinforcement Learning](#stage-2-grpo-reinforcement-learning)
- [📊 Data Format](#-data-format)
  - [SFT Data Format](#sft-data-format)
  - [GRPO Data Format](#grpo-data-format)
- [🎯 Reward Functions](#-reward-functions)
- [⚙️ Hyperparameter Tuning](#️-hyperparameter-tuning)
- [📈 Monitoring Training](#-monitoring-training)
- [🔧 Troubleshooting](#-troubleshooting)

---

## 🎯 Overview

This directory contains training scripts and reward functions for fine-tuning the Domux-Gemma model using a two-stage approach:

1. **SFT (Supervised Fine-Tuning)**: Fine-tune with LoRA on labeled data
2. **GRPO (Group Relative Policy Optimization)**: Reinforce with custom reward functions

---

## 🚀 Training Pipeline

### Stage 1: Supervised Fine-Tuning (SFT)

Fine-tune the base model on labeled data with LoRA:

```bash
cd training/scripts
bash train_sft.sh
```

#### ✅ Before running:

1. Update `--model` path to your base model
2. Update `--dataset` and `--val_dataset` paths
3. Update `--output_dir` for checkpoints
4. (Optional) Configure SwanLab API key for experiment tracking

#### 🔄 After training:

Merge LoRA weights with the base model:

```bash
swift export --model_dir /path/to/checkpoint --output_dir /path/to/merged
```

### Stage 2: GRPO Reinforcement Learning

Use the merged SFT model for GRPO training:

```bash
cd training/scripts
bash train_grpo.sh
```

#### ✅ Before running:

1. Update `--model` to your merged SFT checkpoint
2. Update `--external_plugins` to point to `reward_plugin_slot.py`
3. Update dataset paths (GRPO format, see below)
4. Update `--output_dir`

---

## 📊 Data Format

### SFT Data Format

JSONL file with conversation format:

```json
{"messages": [
  {"role": "system", "content": "You are a smart home assistant..."},
  {"role": "user", "content": "Turn on living room light"},
  {"role": "assistant", "content": "turnOn|light|*|*|*|living room|*"}
]}
```

### GRPO Data Format

JSONL with prompts and ground-truth solutions:

```json
{
  "messages": [
    {"role": "system", "content": "You are a smart home assistant..."},
    {"role": "user", "content": "Turn on living room light"}
  ],
  "solution": "turnOn|light|*|*|*|living room|*"
}
```

> 💡 The `solution` field is used by reward functions to evaluate model outputs.

For detailed data specifications, see [`data/README.md`](data/README.md).

---

## 🎯 Reward Functions

Located in [`rewards/reward_plugin_slot.py`](rewards/reward_plugin_slot.py):

### 🎲 SlotAccuracy

Measures semantic correctness using weighted field matching:

- **Field weights**: action=0.25, device=0.25, attribute=0.20, value=0.15, unit=0.05, room=0.08, floor=0.02
- **Algorithm**: Order-preserving alignment (LCS-based DP)
- **Features**: Handles don't-care fields (`*`) and numeric tolerance

### ✅ SlotFormat

Checks output format compliance:

| Score | Criteria |
|-------|----------|
| 1.0 | All segments have 7 fields and valid action keywords |
| 0.3-1.0 | Partial credit: `0.3 + 0.7 × (valid segment ratio)` |
| 0.2 | Minimal credit: Pipe format detected but no valid segments |

---

## ⚙️ Hyperparameter Tuning

### 🔧 SFT Stage

| Parameter | Recommendation | Notes |
|-----------|----------------|-------|
| `lora_rank` | 16/32 | Increase for better capacity |
| `learning_rate` | 5e-5 to 2e-4 | Adjust based on convergence |
| `num_train_epochs` | 5-10 | More epochs for small datasets |

### 🎯 GRPO Stage

| Parameter | Recommendation | Notes |
|-----------|----------------|-------|
| `reward_weights` | Balance format vs accuracy | Adjust based on priorities |
| `beta` | 0.01-0.1 | KL divergence control |
| `num_generations` | 16/32 | Better exploration with more |
| `learning_rate` | 5e-7 to 5e-6 | Stable RL updates |

---

## 📈 Monitoring Training

### 🦢 With SwanLab

Uncomment and set `SWANLAB_API_KEY` in training scripts. View metrics at [https://swanlab.cn/](https://swanlab.cn/)

### 📊 Without SwanLab

Remove `--report_to swanlab` and related flags. Metrics will be logged to console and `output_dir/runs/`.

---

## 🔧 Troubleshooting

### 💾 OOM (Out of Memory)

- Reduce `per_device_train_batch_size`
- Reduce `max_length` or `vllm_max_model_len`
- For GRPO: reduce `num_generations` or `vllm_gpu_memory_utilization`

### 📉 Low Reward Scores

- Check data format matches reward function expectations
- Verify `--columns '{"solution": "solution"}'` is set correctly
- Review reward function logic in `reward_plugin_slot.py`

### 🐌 Slow Training

- Enable vLLM for GRPO (`--use_vllm true`)
- Increase `gradient_accumulation_steps` and reduce batch size
- Use `bf16` dtype (`--torch_dtype bfloat16`)

---

<div align="center">

**Need help?** Check the [main README](../README.md) or open an issue on GitHub.

</div>
