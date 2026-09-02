#!/usr/bin/env python3
"""Run a pinned local Domux snapshot and preserve unedited decoded outputs."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


# Loading must remain local after the separately recorded `hf download` step.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

MODEL_ID = "iFlytekOpenSource/Domux"
MODEL_REVISION = "6c71a32f4d624cadfd9fce9d10240d8068e53456"
SEED = 20260826
WARMUP_COMMANDS = (
    "Turn on the utility room light",
    "Set the guest room AC temperature to 23 degrees",
)
FORMAL_DATASET_SHA256 = "0e27842c62d9cd4e4b1467b43e3ebcd346c79c0125c4f40cce97d363c821a0a0"
FORMAL_BASE_COUNT = 48
FORMAL_SAMPLE_COUNT = 96
FORMAL_WARMUP_COUNT = 2
FORMAL_MAX_NEW_TOKENS = 128
EXPECTED_SNAPSHOT_MANIFEST_SHA256 = "5a13462b24fc9b00d132c42718e037bc42fc51a3c6752041998e085579f01416"
OFFICIAL_FILE_PROVENANCE: dict[str, tuple[int, str]] = {
    ".gitattributes": (1570, "52373fe24473b1aa44333d318f578ae6bf04b49b"),
    "README.md": (8025, "d612ee3ed4918175d1ba5ab2d6a7e9e6993ccfc1"),
    "chat_template.jinja": (17592, "b2caa00ebefc1d0e2846c15fca399b95420f1b1b"),
    "config.json": (5007, "96dae7e44d3d01c7aa039191aa4c945453979256"),
    "generation_config.json": (203, "e352f58545ce62e094ac3f1729d5c8fff8c5e7d5"),
    "model-00001-of-00004.safetensors": (
        1422130752, "f26e99e1fa290296828da6d00423b5557125badf7dc8d05f84dfd4f71e35b688",
    ),
    "model-00002-of-00004.safetensors": (
        4697620616, "2a4f634f5fd5b23ab365a79cf94095f2a173f0d187bfb22817c09e0730dd77aa",
    ),
    "model-00003-of-00004.safetensors": (
        2115862072, "bbad30c0bf6ccbea8282c5d79ebc6ea7ff126226239d34431d1f7ae0c5777306",
    ),
    "model-00004-of-00004.safetensors": (
        2011006798, "e9509b71370663bdf5e3b3fe6f649f9d490bc6345857aecaaf5360ac5fea074d",
    ),
    "model.safetensors.index.json": (205883, "e84c00c17aba40c1866e65ce4c19a29675cd1e44"),
    "processor_config.json": (1689, "5465974d23e1eca2c46c2809b26c997946ce0d90"),
    "tokenizer.json": (32169626, "cc8d3a0ce36466ccc1278bf987df5f71db1719b9ca6b4118264f45cb627bfe0f"),
    "tokenizer_config.json": (2741, "af7f25861136b515f4bf64d9d0a6cf9875a6d508"),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"dataset line {line_number} is not an object")
        rows.append(row)
    return rows


def iter_commands(rows: Iterable[dict[str, Any]], split: str) -> Iterable[dict[str, str]]:
    for row in rows:
        if split != "all" and row.get("split") != split:
            continue
        base_id = str(row["base_id"])
        yield {
            "base_id": base_id,
            "variant": "clear",
            "command": str(row.get("clear_command") or row["negative_control"]["utterance"]),
        }
        yield {
            "base_id": base_id,
            "variant": "ambiguous",
            "command": str(row.get("ambiguous_command") or row["positive"]["utterance"]),
        }


def git_blob_sha1(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload, usedforsecurity=False).hexdigest()


def snapshot_manifest(
    snapshot: Path,
    *,
    expected_files: dict[str, tuple[int, str]] | None = None,
    expected_manifest_sha256: str | None = EXPECTED_SNAPSHOT_MANIFEST_SHA256,
) -> tuple[list[dict[str, object]], str]:
    """Verify exact Hub revision bytes and return an all-root-file manifest."""

    expected_files = expected_files or OFFICIAL_FILE_PROVENANCE
    if not snapshot.is_dir():
        raise FileNotFoundError("local snapshot directory does not exist")
    actual_names = {path.name for path in snapshot.iterdir() if path.is_file()}
    if actual_names != set(expected_files):
        missing = sorted(set(expected_files) - actual_names)
        extra = sorted(actual_names - set(expected_files))
        raise RuntimeError(f"snapshot root differs from the registered revision; missing={missing}, extra={extra}")

    entries: list[dict[str, object]] = []
    for name, (expected_size, expected_etag) in sorted(expected_files.items()):
        path = snapshot / name
        if path.is_symlink():
            raise RuntimeError(f"snapshot file must not be a symlink: {name}")
        if path.stat().st_size != expected_size:
            raise RuntimeError(f"snapshot size mismatch for {name}")
        metadata_path = snapshot / ".cache" / "huggingface" / "download" / f"{name}.metadata"
        if not metadata_path.is_file():
            raise RuntimeError(f"missing Hugging Face download metadata for {name}")
        metadata_lines = metadata_path.read_text(encoding="utf-8").splitlines()
        if len(metadata_lines) < 2 or metadata_lines[0] != MODEL_REVISION:
            raise RuntimeError(f"snapshot revision metadata mismatch for {name}")
        if metadata_lines[1] != expected_etag:
            raise RuntimeError(f"snapshot Hub etag mismatch for {name}")
        file_sha256 = sha256_file(path)
        if len(expected_etag) == 64:
            if file_sha256 != expected_etag:
                raise RuntimeError(f"snapshot LFS digest mismatch for {name}")
        elif len(expected_etag) == 40:
            if git_blob_sha1(path) != expected_etag:
                raise RuntimeError(f"snapshot Git blob digest mismatch for {name}")
        else:
            raise RuntimeError(f"unsupported Hub etag format for {name}")
        entries.append({
            "name": name,
            "size_bytes": expected_size,
            "sha256": file_sha256,
            "hub_etag": expected_etag,
        })
    manifest_sha256 = sha256_text(canonical_json(entries))
    if expected_manifest_sha256 is not None and manifest_sha256 != expected_manifest_sha256:
        raise RuntimeError("snapshot manifest differs from the registered fixed revision")
    return entries, manifest_sha256


def refuse_overwrite(paths: Iterable[Path], overwrite: bool) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"refusing to overwrite frozen outputs: {', '.join(existing)}")


def validate_paths(snapshot: Path, dataset: Path, output: Path, metadata_output: Path) -> None:
    resolved = [path.resolve() for path in (snapshot, dataset, output, metadata_output)]
    if len(set(resolved)) != len(resolved):
        raise ValueError("snapshot, dataset, evidence, and metadata paths must be distinct")
    for destination in resolved[2:]:
        if destination.is_relative_to(resolved[0]):
            raise ValueError("outputs must not be written inside the pinned model snapshot")


def atomic_write_pair(
    output: Path,
    raw_bytes: bytes,
    metadata_output: Path,
    metadata_bytes: bytes,
    *,
    overwrite: bool = False,
) -> None:
    """Stage both artifacts, fsync them, then publish evidence before its binding metadata."""

    output.parent.mkdir(parents=True, exist_ok=True)
    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[Path, Path]] = []
    try:
        for destination, payload in ((output, raw_bytes), (metadata_output, metadata_bytes)):
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                staged.append((Path(handle.name), destination))
        if not overwrite and (output.exists() or metadata_output.exists()):
            raise FileExistsError("frozen output appeared while artifacts were being staged")
        for temporary, destination in staged:
            os.replace(temporary, destination)
    finally:
        for temporary, _destination in staged:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata-output", type=Path, required=True)
    parser.add_argument("--mode", choices=("formal", "smoke"), default="formal")
    parser.add_argument("--split", choices=("dev", "eval", "all"), default="eval")
    parser.add_argument("--limit-bases", type=int)
    parser.add_argument("--precision", choices=("bf16", "nf4", "int8"), default="bf16")
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.limit_bases is not None and args.limit_bases <= 0:
        parser.error("--limit-bases must be positive")
    if args.warmup < 0 or args.max_new_tokens <= 0:
        parser.error("warmup must be non-negative and max-new-tokens positive")
    if args.mode == "formal":
        if args.split != "eval":
            parser.error("formal mode requires --split eval")
        if args.limit_bases is not None:
            parser.error("formal mode forbids --limit-bases and selective reruns")
        if args.overwrite:
            parser.error("formal mode forbids --overwrite")
        if args.precision != "bf16":
            parser.error("the registered formal configuration requires --precision bf16")
        if args.warmup != FORMAL_WARMUP_COUNT:
            parser.error(f"formal mode requires --warmup {FORMAL_WARMUP_COUNT}")
        if args.max_new_tokens != FORMAL_MAX_NEW_TOKENS:
            parser.error(f"formal mode requires --max-new-tokens {FORMAL_MAX_NEW_TOKENS}")
    refuse_overwrite((args.output, args.metadata_output), args.overwrite)

    snapshot = args.snapshot.resolve()
    dataset = args.dataset.resolve()
    if not dataset.is_file():
        raise FileNotFoundError(f"dataset not found: {dataset.name}")
    validate_paths(snapshot, dataset, args.output, args.metadata_output)
    dataset_sha256 = sha256_file(dataset)
    if args.mode == "formal" and dataset_sha256 != FORMAL_DATASET_SHA256:
        raise RuntimeError("formal dataset bytes differ from the pre-registered freeze")

    files, manifest_sha256 = snapshot_manifest(snapshot)
    config = json.loads((snapshot / "config.json").read_text(encoding="utf-8"))
    if config.get("architectures") != ["Gemma4ForConditionalGeneration"]:
        raise RuntimeError("unexpected Domux architecture in pinned snapshot")

    # Imports are intentionally delayed so CPU-only replay never needs this stack.
    import torch
    from transformers import AutoModelForMultimodalLM, AutoProcessor

    if not torch.cuda.is_available():
        raise RuntimeError("live Domux inference requires one CUDA GPU")
    if not torch.cuda.is_bf16_supported():
        raise RuntimeError("the BF16 baseline requires a BF16-capable GPU")
    if torch.cuda.device_count() != 1:
        raise RuntimeError("set CUDA_VISIBLE_DEVICES to exactly one selected GPU")

    random.seed(SEED)
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)
    rows = read_jsonl(dataset)
    commands = list(iter_commands(rows, args.split))
    if args.limit_bases is not None:
        allowed_ids = {item["base_id"] for item in commands[: args.limit_bases * 2]}
        commands = [item for item in commands if item["base_id"] in allowed_ids]
    if not commands:
        raise ValueError("selected dataset contains no commands")
    if args.mode == "formal":
        base_count = len({item["base_id"] for item in commands})
        if base_count != FORMAL_BASE_COUNT or len(commands) != FORMAL_SAMPLE_COUNT:
            raise RuntimeError("formal mode requires exactly 48 bases and 96 paired probes")

    quantization_config = None
    if args.precision != "bf16":
        try:
            from transformers import BitsAndBytesConfig
        except ImportError as exc:
            raise RuntimeError("quantized variants require the optional bitsandbytes runtime") from exc
        if args.precision == "nf4":
            quantization_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            )
        else:
            quantization_config = BitsAndBytesConfig(load_in_8bit=True)

    torch.cuda.reset_peak_memory_stats()
    load_started = time.perf_counter()
    processor = AutoProcessor.from_pretrained(snapshot, local_files_only=True)
    model_kwargs: dict[str, object] = {
        "dtype": torch.bfloat16,
        "local_files_only": True,
    }
    if quantization_config is None:
        model = AutoModelForMultimodalLM.from_pretrained(snapshot, **model_kwargs).to("cuda:0")
    else:
        model = AutoModelForMultimodalLM.from_pretrained(
            snapshot,
            **model_kwargs,
            quantization_config=quantization_config,
            device_map={"": 0},
        )
    model.eval()
    torch.cuda.synchronize()
    load_seconds = time.perf_counter() - load_started

    def infer(command: str) -> tuple[str, float, int, int, str]:
        messages = [{"role": "user", "content": [{"type": "text", "text": command}]}]
        inputs = processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(model.device)
        torch.cuda.synchronize()
        started = time.perf_counter()
        with torch.inference_mode():
            output_ids = model.generate(
                **inputs,
                max_new_tokens=args.max_new_tokens,
                do_sample=False,
            )
        torch.cuda.synchronize()
        latency_ms = (time.perf_counter() - started) * 1000
        prompt_tokens = int(inputs["input_ids"].shape[-1])
        generated = output_ids[0][prompt_tokens:]
        token_bytes = generated.detach().cpu().numpy().tobytes()
        decoded = processor.decode(
            generated,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
        return decoded, latency_ms, prompt_tokens, int(generated.shape[-1]), hashlib.sha256(token_bytes).hexdigest()

    for index in range(args.warmup):
        infer(WARMUP_COMMANDS[index % len(WARMUP_COMMANDS)])

    results: list[dict[str, object]] = []
    for index, item in enumerate(commands, start=1):
        command = item["command"]
        try:
            raw_output, latency_ms, input_tokens, output_tokens, token_ids_sha256 = infer(command)
            result = {
                **item,
                "query_sha256": sha256_text(command),
                "status": "ok",
                "raw_output": raw_output,
                "raw_output_sha256": sha256_text(raw_output),
                "generated_token_ids_sha256": token_ids_sha256,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": round(latency_ms, 3),
            }
        except Exception as exc:  # Keep failed samples in the frozen denominator.
            result = {
                **item,
                "query_sha256": sha256_text(command),
                "status": "error",
                "raw_output": "",
                "raw_output_sha256": sha256_text(""),
                "generated_token_ids_sha256": None,
                "input_tokens": None,
                "output_tokens": None,
                "latency_ms": None,
                "error_type": type(exc).__name__,
            }
        results.append(result)
        print(f"[{index:03d}/{len(commands):03d}] {item['base_id']}:{item['variant']} {result['status']}")

    prompt_template = (snapshot / "chat_template.jinja").read_text(encoding="utf-8")
    gpu_properties = torch.cuda.get_device_properties(0)
    raw_bytes = "".join(canonical_json(row) + "\n" for row in results).encode("utf-8")
    raw_evidence_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    runner_path = Path(__file__).resolve()
    case_dir = runner_path.parent
    metadata = {
        "schema_version": 1,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_id": MODEL_ID,
        "tested_revision": MODEL_REVISION,
        "snapshot_revision_verified": True,
        "snapshot_manifest_sha256": manifest_sha256,
        "download_source": "huggingface",
        "artifact_type": "full pinned snapshot" if args.precision == "bf16" else "runtime quantized from pinned snapshot",
        "snapshot_size_bytes": sum(int(item["size_bytes"]) for item in files),
        "snapshot_files": files,
        "runtime": "transformers.AutoModelForMultimodalLM",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "torch_version": torch.__version__,
        "transformers_version": package_version("transformers"),
        "huggingface_hub_version": package_version("huggingface_hub"),
        "accelerate_version": package_version("accelerate"),
        "bitsandbytes_version": package_version("bitsandbytes"),
        "precision": args.precision,
        "compute_dtype": "torch.bfloat16",
        "gpu": gpu_properties.name,
        "gpu_total_memory_bytes": gpu_properties.total_memory,
        "visible_gpu_count": torch.cuda.device_count(),
        "load_seconds": round(load_seconds, 3),
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
        "dataset_sha256": dataset_sha256,
        "raw_evidence_sha256": raw_evidence_sha256,
        "runner_sha256": sha256_file(runner_path),
        "grounding_policy_sha256": sha256_file(case_dir / "clarify_commit.py"),
        "evaluator_sha256": sha256_file(case_dir / "evaluate.py"),
        "prompt_template_sha256": sha256_text(prompt_template),
        "sample_count": len(results),
        "base_count": len({str(row["base_id"]) for row in results}),
        "warmup_count": args.warmup,
        "warmup_source": "independent commands excluded from development and evaluation data",
        "generation": {
            "do_sample": False,
            "temperature": 0,
            "max_new_tokens": args.max_new_tokens,
            "seed": SEED,
        },
        "offline_inference": True,
        "run_mode": args.mode,
        "selective_reruns": 0,
        "sample_failures": sum(row["status"] != "ok" for row in results),
        "command_template": (
            "CUDA_VISIBLE_DEVICES=<selected-id> python run_model.py "
            "--snapshot <LOCAL_PINNED_SNAPSHOT> --dataset data/scenarios.jsonl "
            "--output evidence/domux_raw.jsonl --metadata-output evidence/domux_metadata.json "
            "--mode " + args.mode + " --split " + args.split + " --precision " + args.precision
        ),
    }

    metadata_bytes = (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write_pair(
        args.output,
        raw_bytes,
        args.metadata_output,
        metadata_bytes,
        overwrite=args.overwrite,
    )
    print(json.dumps({
        "status": "complete",
        "base_count": metadata["base_count"],
        "sample_count": metadata["sample_count"],
        "precision": args.precision,
        "failures": sum(row["status"] != "ok" for row in results),
        "peak_allocated_bytes": metadata["peak_allocated_bytes"],
    }, indent=2))
    failures = int(metadata["sample_failures"])
    return 1 if args.mode == "formal" and failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
