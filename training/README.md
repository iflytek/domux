
- Use more epochs (5-10) if dataset is small

### GRPO Stage
- Adjust `reward_weights` to balance format vs accuracy
- Tune `beta` (0.01-0.1) for KL divergence control
- Increase `num_generations` (16/32) for better exploration
- Lower `learning_rate` (5e-7 to 5e-6) for stable RL updates

## Monitoring Training

### With SwanLab
Uncomment and set `SWANLAB_API_KEY` in training scripts. View metrics at https://swanlab.cn/

### Without SwanLab
Remove `--report_to swanlab` and related flags. Metrics will be logged to console and `output_dir/runs/`.

## Troubleshooting

**OOM (Out of Memory):**
- Reduce `per_device_train_batch_size`
- Reduce `max_length` or `vllm_max_model_len`
- For GRPO: reduce `num_generations` or `vllm_gpu_memory_utilization`

**Low Reward Scores:**
- Check data format matches reward function expectations
- Verify `--columns '{"solution": "solution"}'` is set correctly
- Review reward function logic in `reward_plugin_slot.py`

**Slow Training:**
- Enable vLLM for GRPO (`--use_vllm true`)
- Increase `gradient_accumulation_steps` and reduce batch size
- Use `bf16` dtype (`--torch_dtype bfloat16`)
