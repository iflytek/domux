# Frozen v1 baseline

- Commit: `63f60d1884059379784c06ae35e84838d5525f9d`
- Implementation: `safety_gate_v1.py`
- Characterization: input-aware rule-based gate with a fail-closed structured-output parser.
- The v1 policy reasons over the natural-language input. It does not inspect output-field
  semantics beyond the legacy parser's action whitelist.

## Frozen artifact hashes

| Artifact | SHA-256 |
|---|---|
| `safety_gate.py` at the frozen commit | `a4c9cb1b9167228bd9861214b43d38c6204ea0d296b29b4b3a691d8228be5908` |
| `evidence/domux_raw.jsonl` | `a2bc81052d5422e9fb1419ab94060f8b77d49f5b6d1276e69342ef7df077b454` |
| `evidence/domux_raw.metadata.json` | `653530f7becb0e7b2fe887d1eed29dcfbef4a35755c739f0be475fb230d8c0df` |
| `evidence/safety_report.json` | `f0cf4be347c4ce007e7ca32be3b8022536d144aa4aac7cbfebb8da5c8ce221a9` |

## Corrected parser interpretation

- Structural schema compliance: 48/48 samples and 53/53 non-empty output lines have exactly
  seven pipe-delimited fields.
- Legacy parser action-vocabulary acceptance: 39/48 samples.
- The historical `format_compliance=0.8125` field is retained as immutable v1 evidence but is
  methodologically misnamed; it combines syntax with the eight-action whitelist.
