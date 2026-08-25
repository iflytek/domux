# PR title

`[case] domux-safety-gate - output-aware gate exposes input-only safety blind spots`

# PR body

Ref #20

## Summary

Adds a reproducible Domux safety-boundary case based on a real frozen Tesla T4 / NF4 run at
revision `6c71a32f4d624cadfd9fce9d10240d8068e53456`.

The case:

- corrects the historical `81.25% format compliance` claim to legacy action-vocabulary
  acceptance; all 48 samples and 53 non-empty lines are structurally seven-field;
- freezes the original input-aware v1 and adds a parser-only ablation;
- implements v2 output-side risk and input-output consistency inspection across all seven
  fields, including multi-line maximum severity and malformed fail-closed behavior;
- compares v1, parser-fixed v1, and v2 on the same frozen raw Domux outputs;
- adds a 256-pair real-output mismatch attack, a 20-case development fault suite, and an
  independently generated 84-case one-shot held-out evaluation;
- reports held-out failures without retuning the frozen v2 gate.

Headline held-out result: 51/84 exact, Macro F1 0.6152, 49/56 risky labels intercepted,
0/23 block labels passed as allow, and 18/28 false interventions. These are bounded test-set
results, not a claim of production safety or Domux model accuracy.

## Reproduction

```bash
cd cases/domux-safety-gate
python -m unittest -v \
  test_safety_gate.py test_safety_gate_v1.py test_dataset.py test_evaluate_safety.py
python verify_evidence.py --dataset example_safety_commands.jsonl \
  --responses evidence/domux_raw.jsonl --report evidence/safety_report.json
python verify_v2_evidence.py
```

No model weights, HF tokens, private cache paths, or private household data are included.

## Published Discussion

[INSERT_PUBLIC_DOMUX_DISCUSSION_URL]
