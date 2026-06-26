# Domux Output Parser

Offline parser that converts raw Domux model output into validated, structured JSON.

The model outputs 7-field pipe-delimited slots (one per line):

```
action|device|attribute|value|unit|room|floor
```

This parser transforms them into structured objects with format validation. **Pure Python stdlib, no dependencies, no Home Assistant connection required.**

## Usage

```bash
# Single/multi-line command → pretty JSON
echo "turnOn|light|*|*|*|living room|1" | python parser/domux_parser.py

# Batch mode: one prediction per line, one JSON object per output line (for dataset/eval pipelines)
python parser/domux_parser.py --jsonl predictions.txt > parsed.jsonl
```

Library usage:

```python
from parser.domux_parser import parse

res = parse("set|air conditioner|temperature|22|celsius|bedroom|*")
res.valid          # True
res.slots[0].value # 22 (int)
res.slots[0].unit  # "Celsius" (Title Case normalized)
res.to_json()      # Structured JSON string
```

## Output Conventions

- `*` (don't-care) fields → `None`
- `value` / `floor` coerced to `int`/`float` when numeric, otherwise kept as string (e.g., AC `mode=Cool`)
- Each segment carries `valid` and `errors`; malformed segments are **flagged, not dropped**, so you can see exactly what the model emitted
- Validation logic (action enum, `*` semantics, `<think>` stripping, newline segmentation) matches [reward_plugin_slot.py](../training/rewards/reward_plugin_slot.py) to keep the parser and GRPO reward aligned

## New: Title Case Normalization (per [COMMAND_SPEC.md](../COMMAND_SPEC.md))

The parser automatically normalizes device/color/unit fields to canonical Title Case:

| Model Output (may vary in casing) | Normalized |
|---|---|
| `light` / `Light` | `Light` |
| `spotlight` / `spot light` | `Spot Light` |
| `air conditioner` / `ac` / `AC` | `AC` |
| `percent` / `Percent` | `Percent` |
| `blue` / `Blue` | `Blue` |
| `warm white` / `Warm White` | `Warm White` |

This ensures the parser's output conforms to the spec, saving downstream consumers from case-cleaning.

## New: Non-Control Output Detection (`kind`)

Model output falls into 3 categories:

- `kind: "control"` — contains `|` pipes, parsed into slots
- `kind: "non_control"` — no pipes (Q&A/chitchat), passed through verbatim in `text` field, **not flagged as malformed**
- `kind: "empty"` — empty output

Example:

```python
parse("Sorry, I can't help with that.")  
# → kind: non_control, text: "...", valid: True
```

## Floor Field Warning

Training data uses numeric floors `1/2/3`, but your example uses the string `Second Floor`. **The spec document ([COMMAND_SPEC.md](../COMMAND_SPEC.md)) shows all examples with `*` placeholders for floor—it never defines the actual format.**

Current parser behavior: **accepts string floors but flags a warning in `errors`**, making it easy to spot samples with inconsistent conventions:

```python
parse("turnOn|Light|*|*|*|bedroom|Second Floor")
# → slots[0].valid = False
#    slots[0].errors = ["floor is string 'Second Floor' (training data uses int 1/2/3); 
#                        spec ambiguous — verify intended format"]
```

**To resolve this, you must standardize**: either numeric floors everywhere (change model output to match training data) or string floors everywhere (re-annotate training data and update docs). Mixed types cause the reward function to score floor field matches as 0 (string ≠ int).

## Out of Scope (Important)

This layer performs **string → structured semantics only**. The following are intentionally excluded:

| Not Handled | Why / Who Should Handle It |
| --- | --- |
| `device=light` → HA domain (`curtain`→`cover`, `air conditioner`→`climate`) | Requires HA naming conventions; belongs in the landing layer |
| `room=living room` → `entity_id` (e.g., `light.living_room_ceiling`) | Requires HA runtime area/entity registry |
| `adjustUp`/`adjustDown` → concrete service call | **Stateful** — needs current device state + step size |
| `value=20 percent` → brightness 0-255 vs `brightness_pct` | Unit/dimension conversion; device-specific |
| `room=*` → which devices to control | **Product decision**, not a parsing problem |

Landing to HA is a separate layer (Resolver/Mapper) that needs HA runtime context. The recommended approach is to let HA's own conversation agent / intent layer resolve entities; this parser produces clean abstract semantics only.

## Testing

```bash
python parser/test_domux_parser.py   # or: python -m pytest parser/test_domux_parser.py
```

## Normalization Reference

Based on [COMMAND_SPEC.md](../COMMAND_SPEC.md):

**Devices** (Title Case):
- Strip Light, Floor Lamp, Spot Light (never "Spotlight"), Desk Lamp, TV Light Strip
- AC (maps `air conditioner`/`ac`), Curtain, Blind, Sheer, Music

**Colors** (Title Case):
- Blue, Red, Green, Yellow, Orange, Pink, Purple, Cyan, Lavender
- White, Warm White, Cool White, Sky Blue

**Units** (Title Case):
- Percent, Kelvin, Celsius

**Actions** (camelCase, unchanged):
- turnOn, turnOff, set, adjustUp, adjustDown, activate, deactivate, pause

**Modes** (Title Case):
- Fan, Dry, Heat, Cool, Reading

## Architecture Notes

- **Pure offline parser**: no network calls, no HA dependency
- **Permissive by default**: keeps malformed segments with `valid: false` rather than dropping them, so eval pipelines see the full failure mode
- **Spec-aligned normalization**: casing rules match the GRPO reward to avoid train/eval mismatch
- **Explicit non-control handling**: questions/chitchat pass through as `kind: non_control`, not misclassified as format errors

---

## Related Files

- [domux_parser.py](domux_parser.py) — Core parser + CLI
- [test_domux_parser.py](test_domux_parser.py) — 17 unit tests
- [README.md](README.md) — Chinese documentation
