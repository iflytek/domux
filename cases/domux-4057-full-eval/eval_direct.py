#!/usr/bin/env python3
"""Domux 官方指标评测（transformers 本地推理，无需 API 服务）。

指标定义、输出 schema 与 iflytek/domux eval/run_eval.py 完全一致（Apache-2.0），
仅将"调用 OpenAI 兼容 API"替换为"本地 transformers 批量生成"。
"""
import os, sys, time, json, argparse, torch
from transformers import AutoModelForCausalLM, AutoTokenizer

REV = "6c71a32f4d624cadfd9fce9d10240d8068e53456"
MODEL = "iFlytekOpenSource/Domux"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = "/root/domux/repo/eval/smart_home_control_test_set.jsonl"
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "eval_results.jsonl")
SUMMARY_FILE = os.path.join(SCRIPT_DIR, "eval_summary.json")
META_FILE = os.path.join(SCRIPT_DIR, "eval_meta.json")
MAX_NEW_TOKENS = 256
WARMUP_SAMPLES = 5

# ---------- metrics（逐字沿用 run_eval.py）----------
def parse_instructions(s):
    if not s:
        return set()
    insts = set()
    for part in str(s).replace("&", "\n").split("\n"):
        part = part.strip()
        if part and part.count("|") == 6:
            insts.add(tuple(f.strip() for f in part.split("|")))
    return insts

def check_format(output):
    if not output:
        return False
    for line in str(output).replace("&", "\n").split("\n"):
        line = line.strip()
        if line and line.count("|") != 6:
            return False
    return True

def check_accuracy(model_output, gold):
    return parse_instructions(model_output) == parse_instructions(gold)

def slot_counts(model_output, gold):
    pred = list(parse_instructions(model_output))
    gold_insts = list(parse_instructions(gold))
    predicted_slots = len(pred) * 7
    gold_slots = len(gold_insts) * 7
    remaining_gold = gold_insts.copy()
    correct = 0
    for p in pred:
        if not remaining_gold:
            break
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
    pred = parse_instructions(model_output)
    gold_insts = parse_instructions(gold)
    correct = len(pred & gold_insts)
    return correct, len(pred), len(gold_insts)

def f1_score(correct, predicted, gold):
    precision = correct / predicted if predicted else 0.0
    recall = correct / gold if gold else 0.0
    if precision + recall == 0:
        return 0.0, 0.0, 0.0
    f1 = 2 * precision * recall / (precision + recall)
    return f1, precision, recall

def load_dataset(path):
    by_category = {}
    with open(path, encoding="utf-8") as f:
        for idx, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            cat = rec["category"]
            by_category.setdefault(cat, []).append({
                "idx": idx, "category": cat,
                "query": rec["query"], "output": rec["output"],
            })
    return by_category

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（自检用）")
    ap.add_argument("--batch", type=int, default=8)
    args = ap.parse_args()

    tok = AutoTokenizer.from_pretrained(MODEL, revision=REV)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    print("loading model...", flush=True)
    mdl = AutoModelForCausalLM.from_pretrained(MODEL, revision=REV,
                                               dtype=torch.bfloat16).to("cuda")
    mdl.eval()

    by_category = load_dataset(INPUT_FILE)
    flat = []
    for cat in sorted(by_category.keys()):
        flat.extend(by_category[cat])
    if args.limit:
        flat = flat[:args.limit]

    print(f"total samples: {len(flat)} | batch={args.batch} | model={MODEL} rev={REV[:8]}", flush=True)
    results = []
    t_all = time.time()
    done = 0
    for i in range(0, len(flat), args.batch):
        chunk = flat[i:i + args.batch]
        texts = [tok.apply_chat_template([{"role": "user", "content": s["query"]}],
                                         add_generation_prompt=True, tokenize=False)
                 for s in chunk]
        enc = tok(texts, return_tensors="pt", padding=True).to("cuda")
        t0 = time.time()
        with torch.inference_mode():
            out = mdl.generate(**enc, max_new_tokens=MAX_NEW_TOKENS, do_sample=False,
                               pad_token_id=tok.pad_token_id)
        wall = time.time() - t0
        latency = wall / len(chunk)
        input_lens = enc["input_ids"].shape[1]
        for j, s in enumerate(chunk):
            dec = tok.decode(out[j][input_lens:], skip_special_tokens=True).strip()
            results.append({
                "idx": s["idx"], "category": s["category"], "query": s["query"],
                "model_output": dec, "gold": s["output"],
                "latency": round(latency, 3),
                "format_valid": check_format(dec),
                "result_correct": check_accuracy(dec, s["output"]),
                "slot": slot_counts(dec, s["output"]),
                "intent": intent_counts(dec, s["output"]),
                "error": None,
            })
        done += len(chunk)
        if done % 50 < args.batch or done >= len(flat):
            print(f"  progress {done}/{len(flat)} | wall {time.time()-t_all:.0f}s", flush=True)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # summary（schema 与 run_eval.py 一致）
    cats = sorted({r["category"] for r in results})
    summary = []
    for cat in cats:
        rs = [r for r in results if r["category"] == cat]
        n = len(rs)
        fmt = sum(1 for r in rs if r["format_valid"])
        cor = sum(1 for r in rs if r["result_correct"])
        lat = [r["latency"] for r in rs if r["idx"] > WARMUP_SAMPLES]
        slot_f1, _, _ = f1_score(*[sum(r["slot"][k] for r in rs) for k in range(3)])
        intent_f1, _, _ = f1_score(*[sum(r["intent"][k] for r in rs) for k in range(3)])
        summary.append({
            "category": cat, "total": n,
            "format_valid": fmt, "result_correct": cor,
            "slot": [sum(r["slot"][k] for r in rs) for k in range(3)],
            "intent": [sum(r["intent"][k] for r in rs) for k in range(3)],
            "slot_f1": slot_f1, "intent_f1": intent_f1,
            "avg_latency": round(sum(lat) / len(lat), 3) if lat else 0.0,
        })
    tot = sum(s["total"] for s in summary)
    tot_fmt = sum(s["format_valid"] for s in summary)
    tot_cor = sum(s["result_correct"] for s in summary)
    overall_slot_f1, _, _ = f1_score(*[sum(s["slot"][k] for s in summary) for k in range(3)])
    overall_intent_f1, _, _ = f1_score(*[sum(s["intent"][k] for s in summary) for k in range(3)])
    all_lat = [r["latency"] for r in results if r["idx"] > WARMUP_SAMPLES]
    summary_data = {
        "model": MODEL,
        "categories": [{
            "category": s["category"], "total": s["total"],
            "format_compliance": round(s["format_valid"] / s["total"], 4),
            "result_accuracy": round(s["result_correct"] / s["total"], 4),
            "slot_f1": round(s["slot_f1"], 4),
            "intent_f1": round(s["intent_f1"], 4),
            "avg_latency": s["avg_latency"],
            "slot": s["slot"], "intent": s["intent"],
        } for s in summary],
        "overall": {
            "total": tot,
            "format_compliance": round(tot_fmt / tot, 4),
            "result_accuracy": round(tot_cor / tot, 4),
            "slot_f1": round(overall_slot_f1, 4),
            "intent_f1": round(overall_intent_f1, 4),
            "avg_latency": round(sum(all_lat) / len(all_lat), 3) if all_lat else 0.0,
            "slot": [sum(s["slot"][k] for s in summary) for k in range(3)],
            "intent": [sum(s["intent"][k] for s in summary) for k in range(3)],
        },
    }
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, ensure_ascii=False, indent=2)

    import transformers as _tr
    meta = {
        "model": MODEL, "revision": REV,
        "framework": "transformers %s + torch %s" % (_tr.__version__, torch.__version__),
        "hardware": "RTX 4090D 24GB (AutoDL payg), BF16",
        "generation": {"max_new_tokens": MAX_NEW_TOKENS, "do_sample": False,
                       "temperature_equivalent": 0.0, "batch_size": args.batch},
        "metric_source": "iflytek/domux eval/run_eval.py (verbatim)",
        "latency_method": "per-batch wall time / batch size, warmup 5 samples excluded",
    }
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("\n=== SUMMARY ===", flush=True)
    print(f"{'Category':<22}{'Samples':>8}{'Format':>9}{'Accuracy':>10}{'SlotF1':>9}{'IntentF1':>10}{'Latency':>10}")
    for s in summary:
        print(f"{s['category']:<22}{s['total']:>8}{s['format_valid']/s['total']*100:>8.2f}%"
              f"{s['result_correct']/s['total']*100:>9.2f}%{s['slot_f1']*100:>8.2f}%"
              f"{s['intent_f1']*100:>9.2f}%{s['avg_latency']:>9.3f}s")
    print(f"{'OVERALL':<22}{tot:>8}{tot_fmt/tot*100:>8.2f}%{tot_cor/tot*100:>9.2f}%"
          f"{overall_slot_f1*100:>8.2f}%{overall_intent_f1*100:>9.2f}%", flush=True)
    print(f"done in {time.time()-t_all:.0f}s | results -> {OUTPUT_FILE}", flush=True)

if __name__ == "__main__":
    main()
