#!/usr/bin/env python3
"""Shared helpers for the Domux RTX 3090 evaluation case."""

from __future__ import annotations

import json
import math
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


CASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CASE_DIR.parents[1]
DATASET_PATH = REPO_ROOT / "eval" / "smart_home_control_test_set.jsonl"
ARTIFACTS_DIR = CASE_DIR / "artifacts"
RAW_ARTIFACTS_DIR = ARTIFACTS_DIR / "raw"
SLOT_NAMES = ("action", "device", "attribute", "value", "unit", "room", "floor")


def ensure_artifact_dirs() -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    RAW_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


def env_int(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum or (maximum is not None and value > maximum):
        upper = f" and <= {maximum}" if maximum is not None else ""
        raise ValueError(f"{name} must be >= {minimum}{upper}, got {value}")
    return value


def api_config() -> dict[str, Any]:
    return {
        "base_url": os.getenv("DOMUX_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/"),
        "api_key": os.getenv("DOMUX_API_KEY", "EMPTY"),
        "model": os.getenv("DOMUX_MODEL", "domux"),
        "timeout": env_int("DOMUX_REQUEST_TIMEOUT", 30, maximum=600),
        "max_tokens": env_int("DOMUX_MAX_TOKENS", 256, maximum=4096),
    }


def load_samples(path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for idx, line in enumerate(handle, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            item["idx"] = idx
            samples.append(item)
    return samples


def stratified_samples(
    samples: Iterable[dict[str, Any]], total: int, *, offset_per_category: int = 0
) -> list[dict[str, Any]]:
    """Select deterministically in round-robin order across sorted categories."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        grouped[str(sample["category"])].append(sample)
    categories = sorted(grouped)
    positions = {category: offset_per_category for category in categories}
    selected: list[dict[str, Any]] = []
    while len(selected) < total:
        made_progress = False
        for category in categories:
            position = positions[category]
            if position < len(grouped[category]):
                selected.append(grouped[category][position])
                positions[category] += 1
                made_progress = True
                if len(selected) == total:
                    break
        if not made_progress:
            break
    if len(selected) != total:
        raise ValueError(f"requested {total} samples but only selected {len(selected)}")
    return selected


def parse_instructions(value: Any) -> list[tuple[str, ...]]:
    parsed: list[tuple[str, ...]] = []
    if not value:
        return parsed
    for part in str(value).replace("&", "\n").splitlines():
        line = part.strip()
        if line and line.count("|") == 6:
            parsed.append(tuple(field.strip() for field in line.split("|")))
    return parsed


def format_valid(value: Any) -> bool:
    if not value:
        return False
    lines = [line.strip() for line in str(value).replace("&", "\n").splitlines() if line.strip()]
    return bool(lines) and all(line.count("|") == 6 for line in lines)


def percentile_nearest_rank(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("cannot calculate a percentile from an empty sequence")
    if percentile <= 0 or percentile > 100:
        raise ValueError("percentile must be in (0, 100]")
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return ordered[rank - 1]


def request_completion(query: str) -> tuple[str | None, float, str | None]:
    import requests

    config = api_config()
    payload = {
        "model": config["model"],
        "messages": [{"role": "user", "content": query}],
        "temperature": 0.0,
        "max_tokens": config["max_tokens"],
    }
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }
    started = time.perf_counter()
    try:
        response = requests.post(
            f"{config['base_url']}/chat/completions",
            headers=headers,
            json=payload,
            timeout=config["timeout"],
        )
        elapsed = time.perf_counter() - started
        if response.status_code != 200:
            return None, elapsed, f"HTTP {response.status_code}: {response.text[:500]}"
        output = response.json()["choices"][0]["message"]["content"].strip()
        return output, elapsed, None
    except Exception as exc:  # noqa: BLE001 - errors are recorded as benchmark evidence
        return None, time.perf_counter() - started, str(exc)


AUTHORIZATION_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+")
SECRET_RE = re.compile(r"(?i)\b(?:hf_[A-Za-z0-9]{8,}|sk-[A-Za-z0-9_-]{8,})\b")
HOME_PATTERNS = (
    re.compile(r"(?i)C:\\Users\\[^\\\s\"']+"),
    re.compile(r"/(?:home/[^/\s\"']+|root)(?=/|\b)"),
    re.compile(r"(?i)(?:[A-Z]:\\|/)[^\r\n\"']*?\.cache[/\\]huggingface[^\s\"']*"),
)
PRIVATE_IP_RE = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
)


def redact_text(text: str) -> str:
    result = AUTHORIZATION_RE.sub(r"\1[REDACTED]", text)
    result = SECRET_RE.sub("[REDACTED]", result)
    for pattern in HOME_PATTERNS:
        result = pattern.sub("<USER_HOME>", result)
    result = PRIVATE_IP_RE.sub("<PRIVATE_IP>", result)
    return result
