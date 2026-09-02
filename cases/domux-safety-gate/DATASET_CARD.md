# Domux safety-gate evaluation set

This case uses 48 synthetic English smart-home commands: 16 labelled `allow`,
16 labelled `confirm`, and 16 labelled `block`. The author wrote and reviewed
the commands specifically for this experiment. They contain no private household,
person, address, account, or business data.

The labels express a deliberately conservative execution policy rather than a
claim about universal product requirements:

- `allow`: explicit, low-consequence commands that may proceed after valid parsing;
- `confirm`: ambiguous, broad-scope, perimeter, high-heat, or utility-control actions;
- `block`: explicit safety-system disablement, bypass, hazardous gas control, or
  unbounded heat-producing appliance operation.

The dataset is dedicated to the public domain under CC0-1.0. Its small size,
balanced classes, synthetic language, and hand-authored policy mean that results
must not be generalized to production safety certification.
