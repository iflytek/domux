# 复现与证据说明

本实验不下载模型、不调用付费 API、不接入设备。使用现有 Hugging Face 本地缓存，在 CPU 上顺序生成 raw 和 normalized；所有输出仅离线记录。

## 数据与工具检查

从仓库根目录执行（普通 Python 可运行数据检查；CPU 运行器沿用已有 torch/transformers 环境）：

```powershell
python -B cases/domux-seniorsafe/scripts/validate_data.py
python -B cases/domux-seniorsafe/scripts/validate_data.py cases/domux-seniorsafe/data/challenge-v1.jsonl --spec cases/domux-seniorsafe/data/challenge-v1.spec.json --freeze cases/domux-seniorsafe/artifacts/generalization-v1/freeze.json
python -B -m unittest discover -s cases/domux-seniorsafe/scripts -p 'test_*.py'
```

旧集默认仍要求80条/40对；新规模由 spec 声明。新集含160条/80对，解析评分136条；8条歧义表达和16条危险设备策略样本不评分解析。source=ai-authored-synthetic。

## 已冻结实验的完整回放

以下使用新的 `replay` 目录，避免覆盖首轮证据；`--snapshot` 可传已有固定版本缓存目录。未传时仍需本地已有缓存，离线变量会禁止下载。

```powershell
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
$env:HF_HUB_DISABLE_PROGRESS_BARS='1'
foreach ($phase in @('raw','normalized')) {
    python -B cases/domux-seniorsafe/scripts/run_transformers_cpu.py --revision 6c71a32f4d624cadfd9fce9d10240d8068e53456 --data cases/domux-seniorsafe/data/challenge-v1.jsonl --data-spec cases/domux-seniorsafe/data/challenge-v1.spec.json --freeze cases/domux-seniorsafe/artifacts/generalization-v1/freeze.json --pipeline $phase --output "cases/domux-seniorsafe/artifacts/replay/${phase}_outputs.jsonl" --environment-output "cases/domux-seniorsafe/artifacts/replay/${phase}_environment.json" --run-id "replay-$phase" --dtype bfloat16 --threads 16 --max-new-tokens 128
    if ($LASTEXITCODE -ne 0) { break }
}
```

恢复中断时，在相同命令末尾增加 `--resume`。只接受完整JSONL行、原记录前缀、相同数据/代码/版本/配置；不修剪或覆盖首轮文件。若输出带 error，则完成状态不是 complete，严格评分会拒绝成功汇总。

首轮结束后执行：

```powershell
python -B cases/domux-seniorsafe/scripts/challenge_report.py
python -B cases/domux-seniorsafe/scripts/label_sensitivity.py
```

报告脚本先验证完整冻结数据、推理参数、代码摘要与环境，再调用严格配对评分；输出使用排他创建，已有报告不会覆盖。

`label_review.json` 单列冻结后发现的4条相对调节单位标签问题，记录时尚未生成受影响样本。`label_sensitivity.py` 仅在内存里覆盖这些标签，补算 `label_sensitivity.json`；保留原始输入、预测和 primary metrics，不把标签修正当作模型提升。该分析脚本在初始冻结之后新增，其摘要另记于最终验证文件，不属于最初22份冻结文件。

## 冻结语义

- `baseline_lock.json`：构造新集前记录旧数据与三份推理规则的字节摘要。
- `coverage.json`：冻结前的覆盖与跨集精确去重检查；不证明语义模板独立。
- `freeze.json`：首次新集推理前冻结的22个文件与预定模型参数；本地时间/摘要不等于第三方时间戳或独立审计。
- 本实验使用原文件**字节**摘要。Git/编辑器转换LF/CRLF也会导致校验失败，应停止并恢复记录的源文件，不能删除摘要检查冒充同次实验。交付的 `frozen_sources.zip` 保留这些原始字节用于跨环境复核；不包含模型权重或凭据。
- 本轮没有设备控制器；`candidate` 只表示离线规则候选。格式合法、测试通过、候选产生都不等于用户授权或设备操作安全。
- 读取本轮输出后，challenge-v1 已成为已见集。后续修复应保留首轮结果并建立下一份未见测试集。
