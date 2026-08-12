"""CLI for GGUF inspection and Ollama-backed Domux inference."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .gguf import inspect_gguf
from .ollama import LocalGGUFStore, OllamaClient, import_local_gguf


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _progress(update: dict[str, Any]) -> None:
    status = update.get("status", "")
    total = update.get("total")
    completed = update.get("completed")
    if total and completed is not None:
        percent = completed * 100 / total
        print(f"\r{status}: {percent:5.1f}%", end="", file=sys.stderr, flush=True)
    elif status:
        print(f"\r{status}", end="", file=sys.stderr, flush=True)
    if status == "success":
        print(file=sys.stderr)


def _file_progress(current: int, total: int) -> None:
    percent = 100 if not total else current * 100 / total
    print(
        f"\rReading GGUF metadata: {percent:5.1f}%",
        end="",
        file=sys.stderr,
        flush=True,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default="http://127.0.0.1:11434",
        help="Ollama server URL (default: %(default)s)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    inspect_cmd = commands.add_parser("inspect", help="inspect a local GGUF header")
    inspect_cmd.add_argument("path", type=Path)
    inspect_cmd.add_argument("--progress", action="store_true")

    local_cmd = commands.add_parser("local", help="list GGUF files below a directory")
    local_cmd.add_argument("directory", type=Path)

    commands.add_parser("server-info", help="show Ollama server version")
    commands.add_parser("models", help="list models stored in Ollama")

    pull_cmd = commands.add_parser("pull", help="pull an official Ollama model")
    pull_cmd.add_argument("model")
    pull_cmd.add_argument("--load", action="store_true", help="load after pulling")
    pull_cmd.add_argument("--keep-alive", default="5m")

    load_cmd = commands.add_parser("load", help="load an installed model")
    load_cmd.add_argument("model")
    load_cmd.add_argument("--keep-alive", default="5m")

    run_cmd = commands.add_parser("run", help="run one deterministic Domux prompt")
    run_cmd.add_argument("model")
    run_cmd.add_argument("prompt")
    run_cmd.add_argument("--keep-alive", default="5m")

    import_cmd = commands.add_parser("import-gguf", help="import a local GGUF")
    import_cmd.add_argument("path", type=Path)
    import_cmd.add_argument("--name", required=True)
    import_cmd.add_argument("--ollama-binary", default="ollama")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "inspect":
        result = inspect_gguf(
            args.path,
            progress=_file_progress if args.progress else None,
        )
        if args.progress:
            print(file=sys.stderr)
        _print_json(result.to_dict())
        return 0
    if args.command == "local":
        _print_json(
            [item.to_dict() for item in LocalGGUFStore(args.directory).list_models()]
        )
        return 0
    if args.command == "import-gguf":
        _print_json(
            import_local_gguf(
                args.path,
                args.name,
                ollama_binary=args.ollama_binary,
            ).to_dict()
        )
        return 0

    client = OllamaClient(args.host)
    if args.command == "server-info":
        _print_json({"host": args.host, "version": client.version()})
    elif args.command == "models":
        _print_json(client.list_models())
    elif args.command == "pull":
        if args.load:
            result = client.pull_and_load(
                args.model,
                keep_alive=args.keep_alive,
                progress=_progress,
            )
        else:
            result = client.pull(args.model, progress=_progress)
        _print_json(result)
    elif args.command == "load":
        _print_json(client.load(args.model, keep_alive=args.keep_alive))
    elif args.command == "run":
        print(client.generate(args.model, args.prompt, keep_alive=args.keep_alive))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
