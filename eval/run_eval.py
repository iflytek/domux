"""
Smart Home Control - Model Evaluation Script
=============================================

Evaluates a model on the smart-home control instruction-parsing task.
The model converts a natural-language command into one or more structured
control instructions of seven pipe-separated fields:

    action|device|attribute|value|unit|room|floor

- Multiple instructions (multi-intent) are separated by a newline.
- Omitted fields are represented by `*`.
- The model takes the raw user instruction as input (no system prompt).

Metrics (reported per category and overall):
    - Format compliance rate : output parses into valid 7-field lines
    - Result accuracy         : full set match against the gold output
    - Slot F1                 : field-level F1 = 2PR/(P+R),
                                P = correct slots / predicted slots,
                                R = correct slots / gold slots
    - Intent F1               : instruction-level F1 = 2PR/(P+R),
                                P = correct instructions / output instructions,
                                R = correct instructions / gold instructions
    - Average latency         : per-request inference time

Dataset: smart_home_control_test_set.jsonl  (JSON Lines)
    Each line: {"category": ..., "query": ..., "output": ...}

Dependencies:
    pip install requests

Usage:
    1. Fill in the API configuration below (API_KEY / BASE_URL / MODEL).
    2. python run_eval.py

License: Apache-2.0
"""

import os
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import requests

# ==================== API configuration ====================
# Fill in your model API details before running.
API_KEY = "your api key"          # e.g. "sk-..."
BASE_URL = "your api base url"     # OpenAI-compatible base, no trailing slash, e.g. "http://localhost:8000/v1"
MODEL = "your model name"         # served model name

# ==================== Paths (relative to this script) ====================
# Resolve paths relative to this script's directory so the script works
# regardless of the current working directory it is launched from.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = os.path.join(SCRIPT_DIR, "smart_home_control_test_set.jsonl")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "eval_results.jsonl")
SUMMARY_FILE = os.path.join(SCRIPT_DIR, "eval_summary.json")

# ==================== Tuning ====================
MAX_WORKERS = 20            # concurrent requests (1-20 recommended)
REQUEST_TIMEOUT = 30        # per-request timeout (seconds)
MAX_TOKENS = 256
WARMUP_SAMPLES = 5          # leading samples excluded from latency stats
# Restrict to specific categories, or None for all.
# e.g. TEST_CATEGORIES = ["single_intent", "multi_intent"]
TEST_CATEGORIES = None

# ==================== Global state ====================
progress_lock = Lock()
progress_counter = {"current": 0, "total": 0}

# ==================== API call ====================
def call_model_api(query, timeout=REQUEST_TIMEOUT):
    """Call the model's OpenAI-compatible chat/completions endpoint.

    Returns (output_text, elapsed_seconds, error_message).
    On success error_message is None; on failure output_text is None.
    """
    url = f"{BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": query}],
        "temperature": 0.0,
        "max_tokens": MAX_TOKENS,
    }

    start = time.time()
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        elapsed = time.time() - start
        if resp.status_code == 200:
            output = resp.json()["choices"][0]["message"]["content"].strip()
            return output, elapsed, None
        return None, elapsed, f"HTTP {resp.status_code}: {resp.text}"
    except Exception as exc:
        return None, time.time() - start, str(exc)


# ==================== Evaluation logic ====================
def parse_instructions(s):
    """Parse output/gold into a set of 7-field instruction tuples.

    Instructions are separated by newlines or '&'. Each valid instruction
    must contain exactly seven '|'-separated fields.
    """
    if not s:
        return set()
    insts = set()
    for part in str(s).replace("&", "\n").split("\n"):
        part = part.strip()
        if part and part.count("|") == 6:
            insts.add(tuple(f.strip() for f in part.split("|")))
    return insts


def check_format(output):
    """Return True if every non-empty line has exactly seven fields."""
    if not output:
        return False
    for line in str(output).replace("&", "\n").split("\n"):
        line = line.strip()
        if line and line.count("|") != 6:
            return False
    return True


def check_accuracy(model_output, gold):
    """Full set-match (order-independent) between model output and gold."""
    return parse_instructions(model_output) == parse_instructions(gold)


def slot_counts(model_output, gold):
    """Slot-level (field-level) TP / predicted / gold counts for one sample.

    Instructions are matched greedily by their full 7-field tuple; within a
    matched pair, each of the 7 fields equal to the gold field counts as one
    correct slot. Unmatched predicted/gold instructions contribute their
    fields only to the predicted/gold totals.

    Returns (correct_slots, predicted_slots, gold_slots), aggregated later
    into Slot F1 = 2PR/(P+R), P = correct/predicted, R = correct/gold.
    """
    pred = list(parse_instructions(model_output))
    gold_insts = list(parse_instructions(gold))

    predicted_slots = len(pred) * 7
    gold_slots = len(gold_insts) * 7

    # Greedily align instructions: identical tuples first, then field overlap.
    remaining_gold = gold_insts.copy()
    correct = 0
    for p in pred:
        if not remaining_gold:
            break
        # best-matching gold instruction (max equal fields)
        best_i, best_match = -1, -1
        for i, g in enumerate(remaining_gold):
            eq = sum(1 for a, b in zip(p, g) if a == b)
            if eq > best_match:
                best_match, best_i = eq, i
        if best_i >= 0:
            correct += best_match
            remaining_gold.pop(best_i)

    return correct, predicted_slots, gold_slots


def intent_counts(model_output, gold):
    """Instruction-level (intent) TP / output / gold counts for one sample.

    A predicted instruction is correct if its full 7-field tuple appears in
    the gold set. Used for multi-intent Intent F1 = 2PR/(P+R),
    P = correct/output, R = correct/gold.
    """
    pred = parse_instructions(model_output)
    gold_insts = parse_instructions(gold)
    correct = len(pred & gold_insts)
    return correct, len(pred), len(gold_insts)


def f1_score(correct, predicted, gold):
    """Harmonic mean of precision and recall from aggregated counts."""
    precision = correct / predicted if predicted else 0.0
    recall = correct / gold if gold else 0.0
    if precision + recall == 0:
        return 0.0, 0.0, 0.0
    f1 = 2 * precision * recall / (precision + recall)
    return f1, precision, recall


# ==================== Worker ====================
def eval_sample(sample):
    """Evaluate a single sample (used by the thread pool)."""
    idx = sample["idx"]
    category = sample["category"]
    query = sample["query"]
    gold = sample["output"]

    model_output, latency, error = call_model_api(query)

    with progress_lock:
        progress_counter["current"] += 1
        cur, tot = progress_counter["current"], progress_counter["total"]
        if cur % 20 == 0 or cur == tot:
            print(f"\r  Progress: {cur}/{tot} ({cur / tot * 100:.1f}%)",
                  end="", flush=True)

    if error:
        return {
            "idx": idx, "category": category, "query": query,
            "model_output": f"ERROR: {error}", "gold": gold,
            "latency": round(latency, 3), "format_valid": False,
            "result_correct": False,
            "slot": (0, 0, len(parse_instructions(gold)) * 7),
            "intent": (0, 0, len(parse_instructions(gold))),
            "error": error,
        }

    return {
        "idx": idx, "category": category, "query": query,
        "model_output": model_output, "gold": gold,
        "latency": round(latency, 3),
        "format_valid": check_format(model_output),
        "result_correct": check_accuracy(model_output, gold),
        "slot": slot_counts(model_output, gold),
        "intent": intent_counts(model_output, gold),
        "error": None,
    }


# ==================== Data loading ====================
def load_dataset(path):
    """Load the JSONL test set, grouped by category (order preserved)."""
    by_category = {}
    with open(path, encoding="utf-8") as f:
        for idx, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            cat = rec["category"]
            by_category.setdefault(cat, []).append({
                "idx": idx,
                "category": cat,
                "query": rec["query"],
                "output": rec["output"],
            })
    return by_category

# ==================== Main ====================
def main():
    print("=" * 70)
    print("Smart Home Control - Model Evaluation")
    print("=" * 70)
    print(f"Input:  {INPUT_FILE}")
    print(f"Output: {OUTPUT_FILE}")
    print(f"Model:  {MODEL}")
    print(f"API:    {BASE_URL}")
    print(f"Concurrency: {MAX_WORKERS}")

    if API_KEY == "your api key" or BASE_URL == "your api base url":
        print("\nError: please fill in API_KEY / BASE_URL / MODEL before running.")
        return

    if not os.path.exists(INPUT_FILE):
        print(f"\nError: dataset not found: {INPUT_FILE}")
        return

    by_category = load_dataset(INPUT_FILE)
    total_all = sum(len(v) for v in by_category.values())
    print(f"\nLoaded {total_all} samples across {len(by_category)} categories:")
    for cat, items in sorted(by_category.items()):
        print(f"  - {cat}: {len(items)}")

    all_results = []
    summary = []

    for category in sorted(by_category.keys()):
        if TEST_CATEGORIES is not None and category not in TEST_CATEGORIES:
            print(f"\n[Skip] {category} (not selected)")
            continue

        samples = by_category[category]
        print(f"\n{'=' * 70}")
        print(f"Category: {category}  ({len(samples)} samples)")
        print(f"{'=' * 70}")

        with progress_lock:
            progress_counter["current"] = 0
            progress_counter["total"] = len(samples)

        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(eval_sample, s): s for s in samples}
            for fut in as_completed(futures):
                results.append(fut.result())
        print()

        results.sort(key=lambda x: x["idx"])
        all_results.extend(results)

        fmt_ok = sum(1 for r in results if r["format_valid"])
        correct = sum(1 for r in results if r["result_correct"])
        latencies = [r["latency"] for r in results
                     if r["error"] is None and r["idx"] > WARMUP_SAMPLES]
        n = len(samples)
        avg_lat = sum(latencies) / len(latencies) if latencies else 0.0

        # Slot F1 (field-level), aggregated over the category
        slot_c = sum(r["slot"][0] for r in results)
        slot_p = sum(r["slot"][1] for r in results)
        slot_g = sum(r["slot"][2] for r in results)
        slot_f1, _, _ = f1_score(slot_c, slot_p, slot_g)

        # Intent F1 (instruction-level), aggregated over the category
        intent_c = sum(r["intent"][0] for r in results)
        intent_p = sum(r["intent"][1] for r in results)
        intent_g = sum(r["intent"][2] for r in results)
        intent_f1, _, _ = f1_score(intent_c, intent_p, intent_g)

        print(f"  Format compliance: {fmt_ok / n * 100:.2f}% ({fmt_ok}/{n})")
        print(f"  Result accuracy:   {correct / n * 100:.2f}% ({correct}/{n})")
        print(f"  Slot F1:           {slot_f1 * 100:.2f}%")
        print(f"  Intent F1:         {intent_f1 * 100:.2f}%")
        print(f"  Avg latency: {avg_lat:.3f}s")

        summary.append({
            "category": category, "total": n,
            "format_valid": fmt_ok, "result_correct": correct,
            "slot": (slot_c, slot_p, slot_g),
            "intent": (intent_c, intent_p, intent_g),
            "slot_f1": slot_f1, "intent_f1": intent_f1,
            "avg_latency": avg_lat,
        })

    # Write per-sample results (JSONL)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for r in all_results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Overall summary
    print(f"\n{'=' * 70}")
    print("Summary")
    print(f"{'=' * 70}")
    tot = sum(s["total"] for s in summary)
    tot_fmt = sum(s["format_valid"] for s in summary)
    tot_cor = sum(s["result_correct"] for s in summary)
    tot_slot = [sum(s["slot"][i] for s in summary) for i in range(3)]
    tot_intent = [sum(s["intent"][i] for s in summary) for i in range(3)]
    overall_slot_f1, _, _ = f1_score(*tot_slot)
    overall_intent_f1, _, _ = f1_score(*tot_intent)

    header = (f"{'Category':<22}{'Samples':>8}{'Format':>9}{'Accuracy':>10}"
              f"{'SlotF1':>9}{'IntentF1':>10}{'Latency':>10}")
    print(header)
    for s in summary:
        print(f"{s['category']:<22}{s['total']:>8}"
              f"{s['format_valid'] / s['total'] * 100:>8.2f}%"
              f"{s['result_correct'] / s['total'] * 100:>9.2f}%"
              f"{s['slot_f1'] * 100:>8.2f}%"
              f"{s['intent_f1'] * 100:>9.2f}%"
              f"{s['avg_latency']:>9.3f}s")
    if tot:
        print(f"{'OVERALL':<22}{tot:>8}"
              f"{tot_fmt / tot * 100:>8.2f}%"
              f"{tot_cor / tot * 100:>9.2f}%"
              f"{overall_slot_f1 * 100:>8.2f}%"
              f"{overall_intent_f1 * 100:>9.2f}%")

    # Write metrics summary (per-category + overall) as JSON
    overall_lat = [r["latency"] for r in all_results
                   if r["error"] is None and r["idx"] > WARMUP_SAMPLES]
    summary_data = {
        "model": MODEL,
        "categories": [
            {
                "category": s["category"],
                "total": s["total"],
                "format_compliance": round(s["format_valid"] / s["total"], 4) if s["total"] else 0.0,
                "result_accuracy": round(s["result_correct"] / s["total"], 4) if s["total"] else 0.0,
                "slot_f1": round(s["slot_f1"], 4),
                "intent_f1": round(s["intent_f1"], 4),
                "avg_latency": round(s["avg_latency"], 3),
                "slot": list(s["slot"]),
                "intent": list(s["intent"]),
            }
            for s in summary
        ],
        "overall": {
            "total": tot,
            "format_compliance": round(tot_fmt / tot, 4) if tot else 0.0,
            "result_accuracy": round(tot_cor / tot, 4) if tot else 0.0,
            "slot_f1": round(overall_slot_f1, 4),
            "intent_f1": round(overall_intent_f1, 4),
            "avg_latency": round(sum(overall_lat) / len(overall_lat), 3) if overall_lat else 0.0,
            "slot": tot_slot,
            "intent": tot_intent,
        },
    }
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, ensure_ascii=False, indent=2)

    print(f"\nDone. Per-sample results saved to: {OUTPUT_FILE}")
    print(f"Metrics summary saved to:        {SUMMARY_FILE}")


if __name__ == "__main__":
    main()

