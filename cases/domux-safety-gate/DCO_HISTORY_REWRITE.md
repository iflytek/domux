# DCO history rewrite map

PR #24 required every contribution commit to carry a Developer Certificate of Origin
`Signed-off-by` trailer. The branch was therefore rebased with `--signoff` after the
experiment and one-shot held-out evaluation were complete.

This operation changed commit identifiers only. It did not change the frozen source trees:

| Role | Commit recorded at experiment time | Canonical DCO-signed commit | Tree hash |
|---|---|---|---|
| v1 baseline | `63f60d1884059379784c06ae35e84838d5525f9d` | `16161aef1c4457f9b233d71cf28a6b6e074efc67` | `5034aa0877fc1d2912d2a35a66f6db910f73a678` |
| v2 pre-held-out freeze | `ad243f999d75bce3f1be35667ff3eaa734ef70e5` | `f7186768855398d13ecb5a0b205db02f68190708` | `b97f29f78d95630196e1298e565148e08ef61517` |

Historical evidence and the held-out generator prompt retain the original identifiers because
those are the identifiers that existed at execution time. `verify_v2_evidence.py` resolves the
canonical DCO-signed v2 commit and verifies the frozen `safety_gate.py` bytes against it.
