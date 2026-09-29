"""Small stdlib Ollama REST client and local GGUF importer."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .gguf import GGUFMetadata, inspect_gguf


class OllamaError(RuntimeError):
    """Raised when Ollama cannot complete an operation."""


class OllamaClient:
    """Connect to a local or remote Ollama REST API."""

    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
    ) -> urllib.response.addinfourl:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + endpoint,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OllamaError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise OllamaError(
                f"Cannot connect to Ollama at {self.base_url}: {exc.reason}"
            ) from exc

    def _json(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._request(method, endpoint, payload) as response:
            body = response.read()
        return json.loads(body) if body else {}

    def _stream(
        self,
        endpoint: str,
        payload: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        with self._request("POST", endpoint, payload) as response:
            for line in response:
                if line.strip():
                    yield json.loads(line)

    def version(self) -> str:
        return str(self._json("GET", "/api/version").get("version", "unknown"))

    def list_models(self) -> list[dict[str, Any]]:
        return self._json("GET", "/api/tags").get("models", [])

    def show(self, model: str) -> dict[str, Any]:
        return self._json("POST", "/api/show", {"model": model, "verbose": False})

    def pull(
        self,
        model: str,
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        last = {}
        for update in self._stream("/api/pull", {"model": model, "stream": True}):
            last = update
            if progress:
                progress(update)
        return last

    def load(self, model: str, keep_alive: str | int = "5m") -> dict[str, Any]:
        return self._json(
            "POST",
            "/api/generate",
            {"model": model, "prompt": "", "stream": False, "keep_alive": keep_alive},
        )

    def generate(
        self,
        model: str,
        prompt: str,
        *,
        keep_alive: str | int = "5m",
    ) -> str:
        result = self._json(
            "POST",
            "/api/generate",
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": keep_alive,
                "options": {"temperature": 0},
            },
        )
        return str(result.get("response", ""))

    def pull_and_load(
        self,
        model: str,
        *,
        keep_alive: str | int = "5m",
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        self.pull(model, progress=progress)
        return self.load(model, keep_alive=keep_alive)


class LocalGGUFStore:
    """Discover and inspect local GGUF weights separately from Ollama storage."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).expanduser().resolve()

    def list_models(self) -> list[GGUFMetadata]:
        if not self.root.exists():
            return []
        return [inspect_gguf(path) for path in sorted(self.root.rglob("*.gguf"))]


def import_local_gguf(
    path: str | os.PathLike[str],
    model: str,
    *,
    ollama_binary: str = "ollama",
) -> GGUFMetadata:
    """Import a local GGUF through Ollama's supported Modelfile workflow."""
    metadata = inspect_gguf(path)
    source = Path(metadata.path)
    with tempfile.TemporaryDirectory(prefix="domux-ollama-") as directory:
        modelfile = Path(directory) / "Modelfile"
        escaped_source = source.as_posix()
        modelfile.write_text(f'FROM "{escaped_source}"\n', encoding="utf-8")
        try:
            subprocess.run(
                [ollama_binary, "create", model, "-f", str(modelfile)],
                check=True,
            )
        except FileNotFoundError as exc:
            raise OllamaError(f"Ollama executable not found: {ollama_binary}") from exc
        except subprocess.CalledProcessError as exc:
            raise OllamaError(
                f"ollama create failed with exit code {exc.returncode}"
            ) from exc
    return metadata
