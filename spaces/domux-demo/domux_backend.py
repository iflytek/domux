"""Inference backends and slot parsing for the Domux demo Space."""

import os
import time
from dataclasses import dataclass

FIELDS = ["action", "device", "attribute", "value", "unit", "room", "floor"]
MAX_NEW_TOKENS = 256
MAX_QUERY_CHARS = 1000

# Same model revision as the full 4,057-sample evaluation in cases/domux-4057-full-eval.
DEFAULT_MODEL_ID = "iFlytekOpenSource/Domux"
DEFAULT_REVISION = "6c71a32f4d624cadfd9fce9d10240d8068e53456"


@dataclass
class ParseResult:
    raw: str
    rows: list[list[str]]
    format_ok: bool
    latency_ms: float


def split_slots(output: str) -> tuple[list[list[str]], bool]:
    """Split model output into 7-field rows.

    Lines are separated by newlines (or `&`, as in eval/run_eval.py). The output is
    format-compliant only when every non-empty line has exactly seven fields.
    """
    rows: list[list[str]] = []
    format_ok = bool(output.strip())
    for line in output.replace("&", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) != len(FIELDS):
            format_ok = False
            fields = (fields + [""] * len(FIELDS))[: len(FIELDS)]
        rows.append(fields)
    return rows, format_ok


class LocalBackend:
    """Runs the model with transformers, as in cases/domux-4057-full-eval/eval_direct.py."""

    def __init__(self, model_id: str, revision: str) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._torch = torch
        self.device = "cuda" if torch.cuda.is_available() or _on_zero_gpu() else "cpu"
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_id, revision=revision, dtype=torch.bfloat16
        ).to(self.device)
        self.model.eval()
        self.label = f"{model_id}@{revision[:8]} (transformers, {self.device})"

    def generate(self, query: str) -> str:
        text = self.tokenizer.apply_chat_template(
            [{"role": "user", "content": query}], add_generation_prompt=True, tokenize=False
        )
        inputs = self.tokenizer(text, return_tensors="pt").to(self.model.device)
        with self._torch.inference_mode():
            output = self.model.generate(
                **inputs,
                max_new_tokens=MAX_NEW_TOKENS,
                do_sample=False,
                pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id,
            )
        new_tokens = output[0][inputs["input_ids"].shape[1] :]
        return self.tokenizer.decode(new_tokens, skip_special_tokens=True).strip()


class OpenAIBackend:
    """Calls an OpenAI-compatible endpoint (vLLM / SGLang) serving Domux."""

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 30) -> None:
        import requests

        self._session = requests.Session()
        self.url = f"{base_url.rstrip('/')}/chat/completions"
        self.model = model
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.timeout = timeout
        self.label = f"{model} (OpenAI-compatible endpoint)"

    def generate(self, query: str) -> str:
        response = self._session.post(
            self.url,
            headers=self.headers,
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": query}],
                "temperature": 0.0,
                "max_tokens": MAX_NEW_TOKENS,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"]).strip()


def backend_from_env() -> "LocalBackend | OpenAIBackend":
    """Use DOMUX_API_BASE when set, otherwise load the model in-process."""
    api_base = os.getenv("DOMUX_API_BASE", "").strip()
    if api_base:
        return OpenAIBackend(
            api_base,
            os.getenv("DOMUX_API_MODEL", "domux").strip() or "domux",
            os.getenv("DOMUX_API_KEY", "").strip(),
        )
    return LocalBackend(
        os.getenv("DOMUX_MODEL_ID", DEFAULT_MODEL_ID).strip() or DEFAULT_MODEL_ID,
        os.getenv("DOMUX_REVISION", DEFAULT_REVISION).strip() or DEFAULT_REVISION,
    )


def run(generate, query: str) -> ParseResult:
    query = query.strip()
    if not query:
        raise ValueError("Enter a smart-home command.")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"Commands are limited to {MAX_QUERY_CHARS} characters.")
    started = time.perf_counter()
    raw = generate(query)
    latency_ms = (time.perf_counter() - started) * 1000
    rows, format_ok = split_slots(raw)
    return ParseResult(raw=raw, rows=rows, format_ok=format_ok, latency_ms=latency_ms)


def _on_zero_gpu() -> bool:
    return os.getenv("SPACES_ZERO_GPU", "").lower() in {"1", "true"}
