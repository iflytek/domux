## Domux safety-gate case

Ref #20

### What this adds

- A reproducible Domux integration experiment with a fail-closed execution safety gate.
- 48 original CC0-1.0 English synthetic safety-boundary commands.
- Fixed Hugging Face revision, GPU/NF4 inference script, raw-output evidence, recomputable
  safety report, and tests.

### Verified evidence

- `evidence/domux_raw.jsonl`: 48 raw Domux outputs and per-item inference latency.
- `evidence/domux_raw.metadata.json`: revision, environment, seed, generation parameters, and
  input hash.
- `evidence/safety_report.json`: evaluation report verified by `verify_evidence.py`.

### Public Discussion

[INSERT_PUBLIC_DOMUX_DISCUSSION_URL]

### Safety and scope

The reported decision metrics are integration metrics for **Domux output + an external,
rule-based safety policy**. They are not a claim that Domux independently classifies safety.
The case includes no model weights, token, private cache path, household data, or internal
endpoint.
