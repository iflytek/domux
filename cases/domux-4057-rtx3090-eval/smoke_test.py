#!/usr/bin/env python3
"""Run five public Domux examples before the full evaluation."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from common import ARTIFACTS_DIR, ensure_artifact_dirs, format_valid, parse_instructions, request_completion


EXAMPLES = (
    (
        "Turn on the living room light",
        "turnOn|Light|*|*|*|Living Room|*",
    ),
    (
        "Set bedroom AC to 22 degrees",
        "set|AC|temperature|22|Celsius|Bedroom|*",
    ),
    (
        "Close the curtains 20 percent",
        "adjustDown|Curtain|openness|20|Percent|*|*",
    ),
    (
        "Turn on the Master Light in the Master Bedroom on the Second Floor, set brightness to 80%, color temperature to 4000K, color to Blue, and mode to Reading.",
        "\n".join(
            (
                "turnOn|Light|*|*|*|Master Bedroom|Second Floor",
                "set|Light|brightness|80|Percent|Master Bedroom|Second Floor",
                "set|Light|colorTemperature|4000|Kelvin|Master Bedroom|Second Floor",
                "set|Light|color|Blue|*|Master Bedroom|Second Floor",
                "set|Light|mode|Reading|*|Master Bedroom|Second Floor",
            )
        ),
    ),
    (
        "Turn off all lights in the Living Room on the Ground Floor, set the AC to Cool mode at 24 degrees in the Guest Bedroom, and open the curtains halfway in the Dining Room.",
        "\n".join(
            (
                "turnOff|Light|*|*|*|Living Room|Ground Floor",
                "set|AC|mode|Cool|*|Guest Bedroom|*",
                "set|AC|temperature|24|Celsius|Guest Bedroom|*",
                "set|Curtain|openness|50|Percent|Dining Room|*",
            )
        ),
    ),
)


def main() -> int:
    ensure_artifact_dirs()
    records = []
    failed = False
    all_expected_matches = True
    for number, (query, expected) in enumerate(EXAMPLES, 1):
        output, latency, error = request_completion(query)
        valid = format_valid(output)
        expected_match = set(parse_instructions(output)) == set(parse_instructions(expected))
        failed = failed or bool(error) or not valid
        all_expected_matches = all_expected_matches and expected_match
        record = {
            "sample": number,
            "query": query,
            "raw_output": output,
            "expected": expected,
            "format_valid": valid,
            "expected_match": expected_match,
            "latency_seconds": round(latency, 6),
            "error": error,
        }
        records.append(record)
        print(f"[{number}/{len(EXAMPLES)}] format={valid} expected={expected_match} error={error is not None}")

    artifact = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "temperature": 0.0,
        "max_examples": len(EXAMPLES),
        "all_passed": not failed,
        "all_expected_matches": all_expected_matches,
        "results": records,
    }
    output_path = ARTIFACTS_DIR / "smoke_results.json"
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Smoke-test evidence: {output_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
