#!/usr/bin/env python3
"""Create a public log copy with tokens, home paths, and private IPs redacted."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import redact_text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("input and output must be different files")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(redact_text(args.input.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    print(f"Sanitized log: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
