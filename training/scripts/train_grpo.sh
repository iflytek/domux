#!/bin/bash
# GRPO (Group Relative Policy Optimization) Training Script
# Framework: ms_swift (ModelScope-Swift)
# Training Method: LoRA GRPO with Custom Reward Functions
#
# This script performs reinforcement learning fine-tuning on top of an SFT model
# using custom reward functions defined in reward_plugin_slot.py

# Experiment Tracking (Optional)
# SwanLab is used for experiment logging and visualization
# If you want to use it, set your API key and configure project/experiment names below
# export SWANLAB_API_KEY="your_api_key_here"
# Or disable tracking by removing --report_to, --swanlab_mode, --swanlab_project, --swanlab_exp_name

# Multi-GPU training configuration
CUDA_VISIBLE_DEVICES=0,1,2,3 \
NPROC_PER_NODE=4 \
VLLM_USE_V1=0 \
swift rlhf \
    --rlhf_type grpo \
    --model /path/to/sft/merged/checkpoint \
    --check_model false \
    --attn_impl eager \
    --enable_thinking false \
    --external_plugins /path/to/reward_plugin_slot.py \
    --reward_funcs slot_accuracy slot_format \
    --reward_weights 1.0 0.5 \
    --use_vllm true \
    --vllm_mode colocate \
    --vllm_gpu_memory_utilization 0.5 \
    --vllm_max_model_len 2048 \
    --sleep_level 1 \
    --tuner_type lora \
    --lora_rank 8 \
    --lora_alpha 16 \
    --lora_dropout 0.05 \
    --target_modules all-linear \
    --torch_dtype bfloat16 \
    --dataset /path/to/train_grpo.jsonl \
    --val_dataset /path/to/val_grpo.jsonl \
    --columns '{"solution": "solution"}' \
    --max_length 1024 \
    --max_completion_length 256 \
    --num_train_epochs 1 \
    --per_device_train_batch_size 2 \
    --per_device_eval_batch_size 2 \
    --gradient_accumulation_steps 4 \
    --learning_rate 1e-6 \
    --lr_scheduler_type cosine \
    --num_generations 8 \
    --num_generations_eval 2 \
    --temperature 1.0 \
    --beta 0.04 \
    --epsilon 0.2 \
    --epsilon_high 0.28 \
    --scale_rewards none \
    --eval_steps 200 \
    --eval_limit 300 \
    --save_steps 200 \
    --save_total_limit 3 \
    --logging_steps 20 \
    --log_completions true \
    # --- Experiment Tracking (SwanLab) ---
    # Configure these if you set SWANLAB_API_KEY above, or remove them to disable tracking
    --report_to swanlab \
    --swanlab_mode cloud \
    --swanlab_project "your_project_name" \
    --swanlab_exp_name "experiment_name" \
    --output_dir /path/to/output
