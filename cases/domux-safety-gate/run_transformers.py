#!/usr/bin/env python3
"""Download a pinned Domux snapshot from Hugging Face and run real inference."""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

import torch
from huggingface_hub import snapshot_download
from transformers import AutoModelForMultimodalLM, AutoProcessor, BitsAndBytesConfig


MODEL_ID = "iFlytekOpenSource/Domux"
MODEL_REVISION = "6c71a32f4d624cadfd9fce9d10240d8068e53456"


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quantization", choices=("nf4", "none"), default="nf4")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--warmup", type=int, default=2)
    args = parser.parse_args()

    if not torch.cuda.is_available():
        raise RuntimeError("This reproducible experiment requires a CUDA GPU runtime")

    snapshot = Path(snapshot_download(MODEL_ID, revision=MODEL_REVISION))
    quantization = None
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    if args.quantization == "nf4":
        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=dtype,
            bnb_4bit_use_double_quant=True,
        )

    processor = AutoProcessor.from_pretrained(snapshot)
    model = AutoModelForMultimodalLM.from_pretrained(
        snapshot,
        device_map="auto",
        torch_dtype=dtype,
        quantization_config=quantization,
    )
    model.eval()

    rows = read_jsonl(args.dataset)
    if args.limit:
        rows = rows[: args.limit]

    def infer(command: str) -> tuple[str, float]:
        messages = [{"role": "user", "content": [{"type": "text", "text": command}]}]
        inputs = processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(model.device)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        started = time.perf_counter()
        with torch.inference_mode():
            output_ids = model.generate(**inputs, max_new_tokens=256, do_sample=False)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        elapsed_ms = (time.perf_counter() - started) * 1_000
        generated = output_ids[0][inputs["input_ids"].shape[-1]:]
        return processor.decode(generated, skip_special_tokens=True).strip(), elapsed_ms

    for warmup_row in rows[: args.warmup]:
        infer(str(warmup_row["command"]))

    results: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        raw_output, latency_ms = infer(str(row["command"]))
        results.append({
            "id": row["id"],
            "command": row["command"],
            "raw_output": raw_output,
            "model_latency_ms": latency_ms,
        })
        print(f"[{index}/{len(rows)}] {row['id']}: {latency_ms:.1f} ms")

    metadata = {
        "model_id": MODEL_ID,
        "tested_revision": MODEL_REVISION,
        "snapshot_size_bytes": directory_size(snapshot),
        "quantization": args.quantization,
        "compute_dtype": str(dtype),
        "torch_version": torch.__version__,
        "transformers_runtime": "AutoModelForMultimodalLM",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "cuda_available": torch.cuda.is_available(),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "warmup_count": min(args.warmup, len(rows)),
        "sample_count": len(results),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in results) + "\n",
        encoding="utf-8",
    )
    args.output.with_suffix(".metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
