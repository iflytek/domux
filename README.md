# Domux-Gemma-4-E2B-it

A fine-tuned multimodal language model based on Gemma-4-E2B-it for pipe-delimited slot filling tasks, trained using supervised fine-tuning (SFT) and reinforcement learning with Group Relative Policy Optimization (GRPO).

## Model Overview

- **Base Model**: Gemma-4-E2B-it
- **Training Method**: SFT → GRPO with custom reward functions
- **Task**: Pipe-delimited slot filling for smart home control
- **Framework**: [ms_swift](https://github.com/modelscope/swift) (ModelScope-Swift)

## Model Download

🤗 **Hugging Face**: [Link to your model]  
🔧 **ModelScope**: [Link to your model]

## Training Pipeline

### 1. Supervised Fine-Tuning (SFT)

Fine-tuned with LoRA on task-specific data:

```bash
bash training/scripts/train_sft.sh
```

Key hyperparameters:
- LoRA rank: 8, alpha: 16
- Learning rate: 1e-4
- Batch size: 2 × 8 gradient accumulation steps
- Epochs: 3

### 2. GRPO Reinforcement Learning

Further optimized using custom reward functions:

```bash
bash training/scripts/train_grpo.sh
```

**Reward Functions** (see [training/rewards/reward_plugin_slot.py](training/rewards/reward_plugin_slot.py)):
- `slot_accuracy` (weight: 1.0): Semantic correctness with field-level matching
- `slot_format` (weight: 0.5): Output format compliance

Key GRPO parameters:
- Beta: 0.04
- Epsilon: 0.2 - 0.28
- Num generations: 8
- Learning rate: 1e-6

## Output Format

The model outputs pipe-delimited slots with 7 fields:

```
action|device|attribute|value|unit|room|floor
```

Example:
```
turnOn|light|*|*|*|living room|1
set|air conditioner|temperature|22|celsius|bedroom|2
adjustDown|curtain|openness|20|percent|*|*
```

## Quick Start

### Installation

```bash
# Install ms_swift
pip install ms-swift[llm] -U

# Or install from source
git clone https://github.com/modelscope/swift.git
cd swift
pip install -e .[llm]
```

### Inference Example

```python
from swift.llm import ModelType, get_template, inference

model_dir = "path/to/downloaded/model"
template = get_template(ModelType.gemma, model_dir=model_dir)

query = "Turn on the living room light and set bedroom AC to 22 degrees"
response = inference(model_dir, template, query)
print(response)
```

## Training Details

See [training/README.md](training/README.md) for detailed training instructions, data format, and configuration options.

## Repository Structure

```
domux/
├── LICENSE
├── README.md
├── requirements.txt
└── training/
    ├── README.md
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

## Citation

If you use this model or training code, please cite:

```bibtex
@software{domux_gemma_2026,
  title = {Domux-Gemma-4-E2B-it: Slot Filling Model with GRPO},
  author = {Your Name},
  year = {2026},
  url = {https://github.com/iflytek/domux}
}
```

## License

[Your chosen license - currently using the LICENSE file in the repo]

## Acknowledgments

- Base model: [Gemma](https://ai.google.dev/gemma)
- Training framework: [ModelScope-Swift](https://github.com/modelscope/swift)
- Experiment tracking: [SwanLab](https://swanlab.cn/)
