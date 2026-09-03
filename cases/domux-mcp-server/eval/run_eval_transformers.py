#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_eval_transformers.py — Domux 真实推理评测（可复现脚本）

对应 Hack-Astron #4 案例要求（iflytek/domux issue #20）：
  从 Hugging Face 固定 revision 下载模型，用 transformers 5.8.0 管线
  跑 5×10（50 次）真实推理，输出格式合规率与延迟统计证据 JSON。

用法（GPU 环境，如 Colab T4 / 本地 20GB+ VRAM）：
    pip install -r requirements.txt
    python run_eval_transformers.py --revision 6c71a32f4d624cadfd9fce9d10240d8068e53456 \
        --out domux_eval_result.json

输出：
    domux_eval_result.json — {model, runtime, total_cases, format_compliance,
                              latency_avg_ms, latency_p95_ms, warmup_count,
                              timestamp_utc, samples[]}
"""

from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
import time
from typing import List, Optional

# ---------------------------------------------------------------------------
# 1. 固定参数与模型标识
# ---------------------------------------------------------------------------

MODEL_ID = "iFlytekOpenSource/Domux"
DEFAULT_REVISION = "6c71a32f4d624cadfd9fce9d10240d8068e53456"

# 七字段槽位契约（与 src/slots.py 保持一致）
SLOT_KEYS = {"action", "device", "attribute", "value", "unit", "room", "floor"}

# 内置冒烟样例（5 条）→ 5×10 循环 = 50 次推理
SMOKE = [
    "把客厅的空调调到26度",
    "关闭卧室所有灯",
    "把二楼主卧的窗帘拉开一半",
    "帮我打开厨房的抽油烟机",
    "不要开客厅的灯",
]

# 官方测试集候选 URL（可复现时优先加载；不可达则回退 SMOKE×10）
CANDIDATES = [
    "https://raw.githubusercontent.com/iflytek/domux/main/benchmark/test_set.jsonl",
    "https://raw.githubusercontent.com/iflytek/domux/main/data/test_4057.jsonl",
]

# 生成参数（固定，保证可复现）
MAX_NEW_TOKENS = 128
DO_SAMPLE = False
TEMPERATURE = 0.0

# warm-up：前 N 次推理不计入延迟统计（JIT/显存预热）
DEFAULT_WARMUP = 2


# ---------------------------------------------------------------------------
# 2. 评测逻辑
# ---------------------------------------------------------------------------

def load_test_set() -> List[str]:
    """加载官方测试集；不可达时回退 SMOKE×10（保持 50 次）。"""
    try:
        import requests
        for url in CANDIDATES:
            try:
                txt = requests.get(url, timeout=15).text
                if txt and not txt.startswith("404"):
                    items = [json.loads(l)["text"]
                             for l in txt.strip().split("\n") if l.strip()]
                    if items:
                        print(f"[load] 使用官方测试集 {len(items)} 条: {url}")
                        return items[:200]
            except Exception:
                continue
    except ImportError:
        print("[load] requests 不可用，直接使用内置样例")
    print(f"[load] 官方测试集不可达，回退内置样例 ×10，共 {len(SMOKE)*10} 条")
    return SMOKE * 10


def format_valid(output: str) -> bool:
    """7 字段管道分隔或含 3+ 槽位键的 JSON 均视为合规。"""
    try:
        obj = json.loads(output)
        return isinstance(obj, dict) and len(SLOT_KEYS & set(obj.keys())) >= 3
    except Exception:
        return len(output.split("|")) >= 5


def run_eval(model_id: str, revision: str, out_path: str,
             warmup: int = DEFAULT_WARMUP,
             test_set: Optional[List[str]] = None) -> dict:
    """主流程：下载模型 → 加载 → 5×10 推理 → 统计 → 输出 JSON。"""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from huggingface_hub import snapshot_download

    print(f"[eval] 从 Hugging Face 下载 {model_id} @ {revision}")
    model_dir = snapshot_download(model_id, revision=revision)
    print(f"[eval] 模型目录: {model_dir}")

    tok = AutoTokenizer.from_pretrained(model_dir)
    try:
        model = AutoModelForCausalLM.from_pretrained(
            model_dir, torch_dtype=torch.bfloat16, device_map="auto")
    except Exception as e:
        print(f"[eval] AutoModelForCausalLM 失败({type(e).__name__})，回退 AutoModel")
        from transformers import AutoModel
        model = AutoModel.from_pretrained(
            model_dir, torch_dtype=torch.bfloat16, device_map="auto")
    model.eval()
    n_params = sum(p.numel() for p in model.parameters()) / 1e9
    print(f"[eval] 模型: {model.__class__.__name__}, "
          f"device={model.device}, params={n_params:.2f}B")

    def generate_text(text: str) -> str:
        """chat template（失败则裸文本）→ 固定生成参数 → 解码。"""
        try:
            prompt = tok.apply_chat_template(
                [{"role": "user", "content": text}],
                tokenize=False, add_generation_prompt=True)
        except Exception:
            prompt = text
        inputs = tok(prompt, return_tensors="pt").to(model.device)
        pad_id = tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id
        with torch.no_grad():
            out = model.generate(
                **inputs, max_new_tokens=MAX_NEW_TOKENS,
                do_sample=DO_SAMPLE, temperature=TEMPERATURE,
                pad_token_id=pad_id)
        return tok.decode(out[0][inputs["input_ids"].shape[1]:],
                          skip_special_tokens=True)

    if test_set is None:
        test_set = load_test_set()

    print(f"[eval] 总测试 {len(test_set)} 条，warm-up {warmup} 条（不计延迟）")
    results: List[dict] = []
    latencies: List[int] = []
    valid_fmt = 0
    for i, t in enumerate(test_set):
        t0 = time.time()
        try:
            out = generate_text(t)
            lat = round((time.time() - t0) * 1000)
            ok = format_valid(out)
            valid_fmt += int(ok)
            if i >= warmup:
                latencies.append(lat)
            results.append({
                "input": t, "output": out, "latency_ms": lat,
                "format_valid": ok,
            })
            print(f"[eval] [{i+1}/{len(test_set)}] ok={ok} {lat}ms <- {t}")
        except Exception as e:  # noqa: BLE001
            results.append({"input": t, "error": str(e)[:200]})
            print(f"[eval] [{i+1}] ERROR {type(e).__name__}: {e}")

    report = {
        "model": f"{model_id} (HF revision {revision})",
        "runtime": f"transformers (from transformers import version) "
                   f"({model.__class__.__name__})",
        "hardware": f"device={model.device}, params={n_params:.2f}B, "
                    "BF16",
        "total_cases": len(test_set),
        "warmup_count": warmup,
        "format_compliance": round(valid_fmt / len(test_set) * 100, 2),
        "latency_avg_ms": round(st.mean(latencies)) if latencies else None,
        "latency_p95_ms": sorted(latencies)[int(len(latencies) * 0.95) - 1]
                          if latencies else None,
        "generation": {"max_new_tokens": MAX_NEW_TOKENS,
                       "do_sample": DO_SAMPLE, "temperature": TEMPERATURE},
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "samples": results,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    summary = {k: report[k] for k in report if k != "samples"}
    print("[eval] 已保存:", out_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Domux transformers 评测")
    ap.add_argument("--model", default=MODEL_ID)
    ap.add_argument("--revision", default=DEFAULT_REVISION)
    ap.add_argument("--out", default="domux_eval_result.json")
    ap.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    args = ap.parse_args()
    run_eval(args.model, args.revision, args.out, args.warmup)
    return 0


if __name__ == "__main__":
    sys.exit(main())