"""GRPO Reward Function for Pipe-Delimited Slot Filling Task.

Training Framework:
- ms_swift (ModelScope-swift) with GRPO (Group Relative Policy Optimization)
- Compatible with Qwen3.5/Qwen2.5 and other decoder-only LLMs
- Integrated via swift.rewards.ORM interface

Reward Design Principles:
1. Format Compliance (SlotFormat): Encourages the model to emit well-formed
   pipe-delimited segments with exactly 7 fields and valid action keywords.
   Partially correct outputs receive partial credit (0.3 + 0.7 * valid_ratio)
   to maintain training signal even during early phases.

2. Slot Accuracy (SlotAccuracy): Measures semantic correctness using weighted
   field matching with order-preserving alignment (LCS-based DP). This design:
   - Preserves segment order (turnOff→turnOn differs from turnOn→turnOff)
   - Isolates errors (one bad segment doesn't cascade to following ones)
   - Penalizes hallucinated extra segments via ratio penalty
   - Handles don't-care fields ('*') and numeric tolerance

Field Importance Weighting:
  action=0.25, device=0.25, attribute=0.20, value=0.15,
  unit=0.05, room=0.08, floor=0.02  (sum=1.0)

Usage in GRPO Training:
  Register these ORMs in swift config's `reward_model` section:
    reward_model:
      model: [slot_format, slot_accuracy]
      weights: [0.3, 0.7]   # balance format vs. accuracy
"""
import re
from typing import List
from swift.rewards import ORM, orms

# field order: action|device|attribute|value|unit|room|floor  (weights sum to 1.0)
FIELD_WEIGHTS = [0.25, 0.25, 0.20, 0.15, 0.05, 0.08, 0.02]
VALID_ACTIONS = {'turnOn', 'turnOff', 'set', 'adjustUp', 'adjustDown', 'pause', 'activate', 'deactivate'}
SEG_SEP = '\n'
NUM_FIELDS = 7


def _parse_slots(text: str) -> List[List[str]]:
    """Split into segments then fields.

    Keeps ALL non-empty segments (even malformed ones) so that a single bad
    segment cannot shift the alignment of everything after it. Field-count
    validity is handled later by the scorer, not by silently dropping segments.
    """
    if not text or not isinstance(text, str):
        return []
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    segments = [s.strip() for s in text.split(SEG_SEP) if s.strip()]
    return [seg.split('|') for seg in segments]


def _field_score(pred_val: str, gt_val: str) -> float:
    pred_val = (pred_val or '').strip()
    gt_val = (gt_val or '').strip()
    # don't-care field: the model must also emit '*', not hallucinate a value
    if gt_val == '*':
        return 1.0 if pred_val == '*' else 0.0
    if pred_val == gt_val:
        return 1.0
    # numeric tolerance ("22" vs "22.0")
    try:
        if abs(float(pred_val) - float(gt_val)) < 1e-6:
            return 1.0
    except (ValueError, TypeError):
        pass
    # case-only mismatch -> partial credit (exact match incl. whitespace already handled above)
    if pred_val and pred_val.lower() == gt_val.lower():
        return 0.7
    return 0.0


def _segment_score(pred_fields: List[str], gt_fields: List[str]) -> float:
    """Weighted field match in [0, 1]. Missing pred fields contribute 0 (their
    weight is simply not added), so short/malformed segments score low."""
    total = 0.0
    for i, gf in enumerate(gt_fields[:NUM_FIELDS]):
        pf = pred_fields[i] if i < len(pred_fields) else ''
        total += FIELD_WEIGHTS[i] * _field_score(pf, gf)
    return total


def _aligned_total(matrix: List[List[float]], n_gt: int, n_pred: int) -> float:
    """Order-PRESERVING alignment of gt segments to pred segments (LCS-style DP).

    Each gt segment may match at most one pred segment and matches must keep
    monotonic order, so a reordered answer is correctly penalized (segment
    order is semantically meaningful — e.g. turnOff-then-turnOn on the same
    device differs from turnOn-then-turnOff). A bad or missing segment in the
    middle only costs that one segment; following segments still align (no
    cascade misalignment).
    """
    if n_gt == 0 or n_pred == 0:
        return 0.0
    dp = [[0.0] * (n_pred + 1) for _ in range(n_gt + 1)]
    for i in range(1, n_gt + 1):
        for j in range(1, n_pred + 1):
            dp[i][j] = max(
                dp[i - 1][j - 1] + matrix[i - 1][j - 1],  # align gt_i <-> pred_j
                dp[i - 1][j],                              # skip gt_i
                dp[i][j - 1],                              # skip pred_j
            )
    return dp[n_gt][n_pred]


class SlotAccuracy(ORM):
    """pipe-format slot matching:
    - split by SEG_SEP into segments; align gt<->pred order-preservingly (LCS DP)
    - each segment scored by weighted field match
    - extra pred segments penalized by ratio
    """

    def __call__(self, completions, solution=None, **kwargs) -> List[float]:
        gts = solution or [None] * len(completions)
        return [self._score(c, g) for c, g in zip(completions, gts)]

    def _score(self, completion: str, gt_raw) -> float:
        pred_segs = _parse_slots(completion)
        gt_segs = _parse_slots(gt_raw) if gt_raw else []

        if not gt_segs:
            # nothing expected: correct iff the model also emits nothing
            return 1.0 if not pred_segs else 0.0
        if not pred_segs:
            return 0.0

        matrix = [[_segment_score(p, g) for p in pred_segs] for g in gt_segs]
        total = _aligned_total(matrix, len(gt_segs), len(pred_segs))
        score = total / len(gt_segs)

        # penalize spurious extra segments
        if len(pred_segs) > len(gt_segs):
            score *= len(gt_segs) / len(pred_segs)

        return round(score, 4)


class SlotFormat(ORM):
    """pipe-format validity check:
    - 1.0: every segment has exactly 7 non-empty fields and a valid action
    - partial: 0.3 + 0.7 * (valid segment ratio)
    - 0.2: has '|' but no fully valid segment; 0.0: no pipe format at all
    """

    def __call__(self, completions, **kwargs) -> List[float]:
        return [self._score(c) for c in completions]

    def _score(self, completion: str) -> float:
        text = re.sub(r'<think>.*?</think>', '', completion.strip(), flags=re.DOTALL).strip()
        if not text:
            return 0.0

        raw_segs = [s.strip() for s in text.split(SEG_SEP) if s.strip()]
        if not raw_segs:
            return 0.0

        valid = 0
        for seg in raw_segs:
            fields = [f.strip() for f in seg.split('|')]
            # exactly 7 fields, valid action, and no empty field ('*' for unspecified)
            if len(fields) == NUM_FIELDS and fields[0] in VALID_ACTIONS and all(fields):
                valid += 1

        if valid == 0:
            has_pipe = any('|' in s for s in raw_segs)
            return 0.2 if has_pipe else 0.0

        ratio = valid / len(raw_segs)
        return round(0.3 + 0.7 * ratio, 4)


orms['slot_accuracy'] = SlotAccuracy
orms['slot_format'] = SlotFormat
