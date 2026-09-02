# Clarify-and-Commit synthetic scenario data card

## Summary

This case uses 64 synthetic smart-home base scenarios. Sixteen records are a
development split for implementation and smoke tests. Forty-eight records form
a fixed, policy-informed conformance split, balanced across four ambiguity
strata:

| Stratum | Development | Evaluation |
|---|---:|---:|
| Duplicate entity names | 4 | 12 |
| Missing entity or required slot | 4 | 12 |
| Context-dependent reference | 4 | 12 |
| Unresolved negation or correction | 4 | 12 |
| **Total** | **16** | **48** |

Every base contains a matched pair: one explicit command and one ambiguous
command that points to the same intended state change. The pair is one
statistical unit; the two commands are not treated as independent samples.

## Creation and freeze history

The scenario candidate was drafted before the case implementation or any
formal Domux output was inspected. Its original SHA-256 was
`e81b7879760e6c15c5dcde0a516946e8baac68a15d3aeef3aa119b63e8eb5578`.
Before any model run, a semantic review made eight declared changes only:

1. canonicalized entity domains and deterministic Home Assistant state fields;
2. added one unrelated control entity per base for over-binding tests;
3. normalized synthetic support entity IDs to Home Assistant-safe identifiers;
4. aligned climate fixtures with Home Assistant's state-as-HVAC-mode contract;
5. added operation-relevant light and cover capability metadata;
6. replaced one unsafe implicit multi-mode climate turn-on with an explicitly
   confirmed HVAC mode;
7. rewrote twelve correction probes whose final wording had already removed the
   ambiguity; and
8. made confirmation, context dependencies and controlled deltas explicit.

The resulting evaluation bytes were then frozen at SHA-256
`dfdf7a0f40ec7df111ca56e5443b90aa6958f5a0bb7eca0eea0a63fa5f8d4d50`.
`freeze.json` also records the development and combined-file hashes. Any change
to the evaluation bytes invalidates the formal run; failed or inconvenient rows
remain in the denominator.

The downstream grounding policy, evaluator, fixtures and regression tests were
subsequently developed with the 48 scenario commands, structures and expected
labels visible. This split is therefore **not an unseen holdout for the runtime
policy**. The one-shot property applies only to the model raw-output evidence:
after the code checkpoint is frozen, all 96 clear/ambiguous probes are generated
once, without inspecting or selectively rerunning their outputs.

The registered model artifact is independently pinned in
`snapshot_manifest.json`: all thirteen root files, byte sizes, SHA-256 hashes,
Hub etags and the exact Hub commit are recorded. `freeze.json` binds that
manifest file, while the runner verifies the per-file Hub download metadata and
bytes before loading a model.

The records were created for this case with AI-assisted drafting and deterministic
validation. They are not derived from the official 4,057-row Domux test set, a
private conversation, or real household telemetry. The submitter reviews the
final data and evidence before publication.

## Schema

Each JSONL record contains:

- synthetic entity inventory and a controlled initial-state projection;
- session context with only synthetic prior turns and entity IDs;
- paired clear and ambiguous commands;
- displayed clarification answer and candidate IDs (maximum three);
- the confirmed seven-slot instruction and intended entity;
- exact before/after controlled projection;
- context state dependencies and one unrelated control entity; and
- split, category, schema version and license metadata.

Volatile Home Assistant fields such as timestamps and context IDs are excluded.
Light projections use `state`, `brightness`, `rgb_color` and
`color_temp_kelvin`; cover projections use `state` and `current_position`;
climate projections use Home Assistant's HVAC mode in `state`, plus
`temperature` and `fan_mode` from attributes.

## Intended use

The data measures whether a downstream integration:

- distinguishes the ambiguous probe from its explicit matched control;
- keeps the intended entity inside a deterministic set of at most three choices;
- performs no service call before clarification and confirmation;
- executes the confirmed state delta; and
- rejects a pre-declared replay, expiry, state or candidate change without
  over-binding unrelated state.

It is not a general Domux accuracy benchmark and it does not estimate ambiguity
prevalence in household traffic. It is a deterministic conformance suite for
the recorded formal v1 run. Its raw model outputs and report are never edited,
omitted, or selectively rerun. The final top-level integration was improved
after inspecting the v1 failures, so it is a reference implementation rather
than a basis for relabelling the original v1 result. The retained scripts can
run a fresh end-to-end observation; they do not claim byte-identical
reproduction of the historical v1 policy execution.

## Safety and privacy

Only synthetic Light, Curtain and AC entities are present. Locks, alarms, gas,
doors, cameras, health devices and real hardware are excluded. The data contains
no token, endpoint, private address, personal filesystem path, real user name,
real home layout or business prompt.

## License

To the extent copyright applies, the original synthetic records and this data
card are offered under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
This does not change the licenses of Domux, Gemma, Home Assistant or any runtime.

## Limitations

- Forty-eight evaluation bases give wide confidence intervals, even for 48/48.
- Wilson intervals and exact McNemar results describe outcomes on this fixed,
  policy-informed suite only; they do not establish population generalization.
- The 1:1 clear/ambiguous construction does not represent production prevalence,
  so measured precision is not a production positive predictive value.
- A passing B2 quality gate means only that the predeclared clean and guard
  checks passed on this suite; it is not a whole-model or production-safety
  certification.
- Clarification answers are controlled selections, not a user study or an ASR
  evaluation.
- Synthetic entity names and state transitions cannot establish production
  safety, accessibility, exactly-once delivery or real-hardware reliability.
