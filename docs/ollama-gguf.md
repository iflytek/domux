# Ollama and GGUF

Domux includes a dependency-free local-model toolkit in `domux_runtime`.
It reads GGUF metadata without loading tensor weights and connects to local or
remote Ollama servers through their REST API.

## Inspect local GGUF weights

```bash
python -m domux_runtime.cli inspect /models/domux-q4_k_m.gguf --progress
python -m domux_runtime.cli local /models
```

The parser accepts GGUF v2 and v3. It reports architecture, model name, tensor
count, tensor type distribution, and the primary quantization. Domux marks
`Q4_0`, `Q4_K_M`, `Q5_K_M`, `Q6_K`, and `Q8_0` as supported
quantizations.

Only the header, metadata, and tensor descriptors are read. Tensor data is
left on disk, so inspecting a large model does not duplicate its weights in
memory. Metadata arrays are summarized after the first 16 values.

## Import a GGUF into Ollama

Install and start Ollama, then run:

```bash
python -m domux_runtime.cli import-gguf /models/domux-q4_k_m.gguf --name domux
```

This validates the GGUF first and invokes Ollama's supported Modelfile import
flow. The local GGUF remains in your chosen directory; Ollama stores its own
managed model separately.

## Connect, pull, load, and run

```bash
# Verify a local or remote server
python -m domux_runtime.cli --host http://127.0.0.1:11434 server-info

# List Ollama-managed models
python -m domux_runtime.cli models

# Pull an official model with progress, then load it
python -m domux_runtime.cli pull gemma3 --load --keep-alive 10m

# Load an existing model or run a command
python -m domux_runtime.cli load domux --keep-alive 10m
python -m domux_runtime.cli run domux "Turn on the living room light"
```

For a remote Ollama instance, put `--host URL` before the subcommand.

## Python API

```python
from domux_runtime import OllamaClient, inspect_gguf

metadata = inspect_gguf("/models/domux-q4_k_m.gguf")
print(metadata.quantization, metadata.architecture)

client = OllamaClient("http://127.0.0.1:11434")
client.pull_and_load("gemma3", progress=print)
print(client.generate("gemma3", "Turn on the living room light"))
```

## Storage layout

- Local GGUF storage is explicit and user-managed. `LocalGGUFStore` only
  discovers files below the directory you pass.
- Ollama storage is accessed only through the Ollama API/CLI. Domux never
  reads or modifies Ollama's internal blob directory.
- Safetensors training checkpoints remain part of the existing
  ModelScope-Swift workflow and are not mixed with local GGUF discovery.

Ollama support for a model architecture is independent from the GGUF container
version. A valid v2/v3 file can still be rejected by Ollama when its
architecture or tensors are unsupported.
