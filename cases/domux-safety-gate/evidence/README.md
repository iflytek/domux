# 公开运行证据目录

本目录在提交前必须包含同一轮真实运行产生的以下小型、可公开文件：

```text
domux_raw.jsonl                 # 48 条输入、逐条原始 Domux 输出和模型延迟
domux_raw.metadata.json         # 固定 revision、硬件、量化、依赖版本、seed、数据 SHA-256
safety_report.json              # 由 evaluate_safety.py 从以上原始输出重算的完整报告
v2/                             # v2 消融、攻击、held-out 与可重算报告
```

原始 Colab 会话没有保存可公开的脱敏 `run.log`，因此本目录不声称包含它。
可重算的 raw output、metadata、report 与校验器是当前证据链。

严禁放入模型权重、HF token、Cookie、个人缓存路径、私人设备或家庭数据。提交时应运行：

```bash
python verify_evidence.py \
  --dataset example_safety_commands.jsonl \
  --responses evidence/domux_raw.jsonl \
  --report evidence/safety_report.json
```

验证器会重算所有稳定指标、混淆矩阵和逐条判定；安全闸门微秒级延迟会因机器噪声变化，
因此单独保留为运行记录。若验证失败，公开 README 中的指标必须以重算结果为准。

v2 证据的完整复核命令：

```bash
python verify_v2_evidence.py
```
