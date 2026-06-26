#!/bin/bash
# SFT (Supervised Fine-Tuning) Script for Domux-Gemma Model
# Framework: ms_swift (ModelScope-Swift)
# Training Method: LoRA Fine-tuning

# Experiment Tracking (Optional)
# SwanLab is used for experiment logging and visualization
# If you want to use it, set your API key and configure project/experiment names below
# export SWANLAB_API_KEY="your_api_key_here"
# Or disable tracking by removing --report_to, --swanlab_mode, --swanlab_project, --swanlab_exp_name

# Multi-GPU training configuration
CUDA_VISIBLE_DEVICES=0,1,2,3 \
NPROC_PER_NODE=4 \
swift sft \
    --model /path/to/base/model \
    --check_model false \
    --tuner_type lora \
    --dataset /path/to/train.jsonl \
    --val_dataset /path/to/val.jsonl \
    --torch_dtype bfloat16 \
    --attn_impl eager \
    --num_train_epochs 3 \
    --dataset_num_proc 8 \
    --per_device_train_batch_size 2 \
    --gradient_accumulation_steps 8 \
    --learning_rate 1e-4 \
    --lora_rank 8 \
    --lora_alpha 16 \
    --lora_dropout 0.05 \
    --target_modules all-linear \
    --max_length 1500 \
    --warmup_steps 0.1 \
    --split_dataset_ratio 0.1 \
    --eval_steps 100 \
    --save_steps 500 \
    --save_total_limit 3 \
    --logging_steps 5 \
    # --- Experiment Tracking (SwanLab) ---
    # Configure these if you set SWANLAB_API_KEY above, or remove them to disable tracking
    --report_to swanlab \
    --swanlab_mode cloud \
    --swanlab_project "your_project_name" \
    --swanlab_exp_name "experiment_name" \
    --output_dir /path/to/output
