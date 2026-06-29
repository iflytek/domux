# Smart Home Control Instruction Parsing — Test Set

A benchmark for evaluating a model's ability to parse natural-language
smart-home commands into standardized structured control instructions.

The model reads a user's natural-language command and outputs one or more
control instructions in a fixed seven-field, pipe-separated format:

```
action|device|attribute|value|unit|room|floor
```

- Multiple instructions (multi-intent) are separated by a newline.
- Omitted / not-applicable fields are represented by `*`.
- The model takes the raw user instruction as input (no system prompt).

## Dataset

- **File**: `smart_home_control_test_set.jsonl` (JSON Lines, UTF-8)
- **Size**: 4,057 samples
- **Device scope**: three device categories — Light, AC, Curtain (67 device
  name variants in total, e.g. `Ceiling Light`, `LED Strip`, `Sheer`).

Each line is a JSON object:

```json
{"category": "single_intent", "query": "turn on the ceiling light in the garage", "output": "turnOn|Ceiling Light|*|*|*|Garage|*"}
```

| Field | Description |
| :--- | :--- |
| `category` | Test category (see below) |
| `query` | Natural-language user command (English) |
| `output` | Gold standard instruction(s); multiple lines joined by `\n` |

### Categories

| Category | Count | Share | Description |
| :--- | :---: | :---: | :--- |
| `single_intent` | 1,122 | 27.7% | One command controlling one device |
| `multi_intent` | 1,641 | 40.4% | One command controlling multiple devices |
| `omitted_attribute` | 735 | 18.1% | Command states the device but omits attribute values |
| `non_standard_naming` | 559 | 13.8% | Non-standard device naming (synonyms, plurals, etc.) |

## Output Format

Each instruction has seven `|`-separated fields. Use `*` for any field that
is not specified.

| # | Field | Meaning | Example values |
| :--- | :--- | :--- | :--- |
| 1 | `action` | Action to perform | `turnOn`, `turnOff`, `set`, `adjustUp`, `adjustDown` |
| 2 | `device` | Target device | `Ceiling Light`, `AC`, `Curtain`, `LED Strip` |
| 3 | `attribute` | Attribute being controlled | `brightness`, `color`, `colorTemperature`, `mode`, `temperature`, `position`, `windSpeed` |
| 4 | `value` | Target value | `Blue`, `26`, `50`, `Cool` |
| 5 | `unit` | Unit of the value | `Percent`, `Celsius`, `Kelvin`, `Level` |
| 6 | `room` | Room | `Living Room`, `Guest Bedroom`, `Majlis` |
| 7 | `floor` | Floor | `Ground Floor`, `First Floor`, `Second Floor` |

### Examples

| Category | Query | Output |
| :--- | :--- | :--- |
| single_intent | turn on the ceiling light in the garage | `turnOn\|Ceiling Light\|*\|*\|*\|Garage\|*` |
| multi_intent | set the ac to cool mode and turn it up a bit | `set\|AC\|mode\|Cool\|*\|*\|*`<br>`adjustUp\|AC\|temperature\|*\|*\|*\|*` |
| omitted_attribute | dim the reading light | `adjustDown\|Reading Light\|brightness\|*\|*\|*\|*` |
| non_standard_naming | set the led strip in the living room to blue | `set\|LED Strip\|color\|Blue\|*\|Living Room\|*` |

## Evaluation

An evaluation script `run_eval.py` is provided. It sends each `query` to an
OpenAI-compatible chat/completions endpoint, compares the model output against
the gold `output`, and reports metrics per category and overall.

### Requirements

```bash
pip install requests
```

### Configure

Open `run_eval.py` and fill in the API configuration near the top:

```python
API_KEY  = "your api key"        # e.g. "sk-..."
BASE_URL = "your api base url"   # OpenAI-compatible base, no trailing slash, e.g. "http://localhost:8000/v1"
MODEL    = "your model name"     # served model name
```

Optional tuning (same section):

| Variable | Default | Description |
| :--- | :--- | :--- |
| `MAX_WORKERS` | `20` | Concurrent requests (1–20 recommended) |
| `REQUEST_TIMEOUT` | `30` | Per-request timeout (seconds) |
| `MAX_TOKENS` | `256` | Generation cap |
| `WARMUP_SAMPLES` | `5` | Leading samples excluded from latency stats |
| `TEST_CATEGORIES` | `None` | Restrict to specific categories, e.g. `["single_intent"]`; `None` = all |

### Run

```bash
python run_eval.py
```

The script reads `smart_home_control_test_set.jsonl` (relative path) and writes
per-sample results to `eval_results.jsonl` and a metrics summary to
`eval_summary.json`.

### Metrics

| Metric | Definition |
| :--- | :--- |
| **Format compliance** | Output parses into valid 7-field lines |
| **Result accuracy** | Output instruction set exactly matches gold (order-independent) |
| **Slot F1** | Field-level F1 = 2PR/(P+R); P = correct slots / predicted slots, R = correct slots / gold slots |
| **Intent F1** | Instruction-level F1 = 2PR/(P+R); P = correct instructions / output instructions, R = correct instructions / gold instructions |
| **Average latency** | Mean per-request inference time (excluding warm-up) |

A prediction is counted correct only when its full set of instructions matches
the gold set; instruction order does not matter. Slot F1 measures field-level
extraction quality, while Intent F1 measures instruction-level matching for
multi-intent commands.

### Output

`eval_results.jsonl` — one JSON object per sample:

```json
{"idx": 1, "category": "single_intent", "query": "...", "model_output": "...", "gold": "...", "latency": 0.21, "format_valid": true, "result_correct": true, "error": null}
```

`eval_summary.json` — aggregated metrics per category and overall:

```json
{
  "model": "your model name",
  "categories": [
    {"category": "single_intent", "total": 1122, "format_compliance": 1.0, "result_accuracy": 0.9964, "slot_f1": 0.9994, "intent_f1": 0.9964, "avg_latency": 0.268, "slot": [c, p, g], "intent": [c, p, g]}
  ],
  "overall": {"total": 4057, "format_compliance": 0.9998, "result_accuracy": 0.9835, "slot_f1": 0.997, "intent_f1": 0.9874, "avg_latency": 0.31, "slot": [c, p, g], "intent": [c, p, g]}
}
```

The `slot` / `intent` triples are raw `[correct, predicted, gold]` counts, kept so
the F1 metrics can be recomputed or merged across runs.

Console output shows a per-category and overall summary table.

## Reference Results

Evaluation of the reference model on this test set:

| Category | Samples | Format | Accuracy | Slot F1 | Intent F1 | Latency |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| single_intent | 1,122 | 100.00% | 99.64% | 99.94% | 99.64% | 0.253s |
| multi_intent | 1,641 | 100.00% | 97.81% | 99.69% | 98.71% | 0.377s |
| omitted_attribute | 735 | 100.00% | 99.05% | 99.67% | 99.05% | 0.260s |
| non_standard_naming | 559 | 100.00% | 96.06% | 99.27% | 96.16% | 0.260s |
| **OVERALL** | **4,057** | **100.00%** | **98.30%** | **99.69%** | **98.69%** | — |

> Latency measured on a single-GPU deployment; values depend on hardware and
> serving configuration.

## License

Apache-2.0

