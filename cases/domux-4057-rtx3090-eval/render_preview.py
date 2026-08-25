#!/usr/bin/env python3
"""Render a shareable run-evidence image from sanitized public artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


CASE_DIR = Path(__file__).resolve().parent
ARTIFACTS_DIR = CASE_DIR / "artifacts"


def load_json(name: str) -> dict:
    return json.loads((ARTIFACTS_DIR / name).read_text(encoding="utf-8"))


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = (
        "C:/Windows/Fonts/consolab.ttf" if bold else "C:/Windows/Fonts/consola.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    )
    for name in names:
        if Path(name).is_file():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def main() -> int:
    summary = load_json("eval_summary.json")
    run = load_json("eval_run_metadata.json")
    latency = load_json("latency_summary.json")["overall"]
    download = load_json("download_metadata.json")

    image = Image.new("RGB", (1600, 1200), "#0b1020")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((55, 55, 1545, 1145), radius=24, fill="#111827", outline="#334155", width=3)
    draw.ellipse((86, 87, 106, 107), fill="#fb7185")
    draw.ellipse((118, 87, 138, 107), fill="#fbbf24")
    draw.ellipse((150, 87, 170, 107), fill="#4ade80")

    normal = font(28)
    strong = font(30, bold=True)
    small = font(23)
    y = 145

    lines = [
        ("Domux official 4,057-sample evaluation — sanitized evidence", "#f8fafc", strong),
        (f"revision {download['revision']}", "#94a3b8", small),
        ("", "#f8fafc", normal),
        ("$ CUDA_VISIBLE_DEVICES=0 python run_official_eval.py --max-workers 20", "#67e8f9", normal),
        (f"Evaluation complete: {run['completed_samples']} samples, {run['api_error_count']} API errors", "#4ade80", strong),
        ("", "#f8fafc", normal),
        ("Category                 N    Format   Accuracy   Slot F1  Intent F1", "#f8fafc", normal),
    ]
    for category in summary["categories"]:
        lines.append(
            (
                f"{category['category']:<22} {category['total']:>4}  "
                f"{category['format_compliance']:>7.2%}  {category['result_accuracy']:>8.2%}  "
                f"{category['slot_f1']:>7.2%}  {category['intent_f1']:>9.2%}",
                "#cbd5e1",
                normal,
            )
        )
    overall = summary["overall"]
    lines.extend(
        [
            (
                f"{'OVERALL':<22} {overall['total']:>4}  {overall['format_compliance']:>7.2%}  "
                f"{overall['result_accuracy']:>8.2%}  {overall['slot_f1']:>7.2%}  "
                f"{overall['intent_f1']:>9.2%}",
                "#fbbf24",
                strong,
            ),
            ("", "#f8fafc", normal),
            ("Sequential latency: warm-up 20; 100 requests x 3 repeats", "#67e8f9", normal),
            (
                f"median {latency['median_seconds']:.6f}s | p95 {latency['p95_seconds_nearest_rank']:.6f}s | "
                f"throughput {latency['throughput_requests_per_second']:.4f} req/s",
                "#4ade80",
                strong,
            ),
            ("", "#f8fafc", small),
            ("RTX 3090 24 GB | BF16 | vLLM 0.22.0 | temperature 0", "#94a3b8", small),
            ("Rendered from committed sanitized JSON artifacts; this is not a raw terminal capture.", "#64748b", small),
        ]
    )

    for text, color, selected_font in lines:
        draw.text((95, y), text, fill=color, font=selected_font)
        y += 50 if selected_font == small else 55

    output = CASE_DIR / "preview.png"
    image.save(output, optimize=True)
    print(f"Rendered preview: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
