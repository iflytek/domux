# Domux HER Hack-Astron #4 — AutoDL 执行手册（RTX 4090D 24GB）

目标：下载 Domux（5B，BF16 ≈ 10.25GB）→ vLLM 起 OpenAI 兼容服务 → 官方评测脚本跑 4057 条测试集 → 收集证据 → 发 HF Discussion → 提 case PR。

挑战活动 issue：https://github.com/iflytek/domux/issues/20 （Ref #20，勿 Closes）

---

## 0. 开工前（网页/本地办好，别等开机后）

- [ ] 注册 HF 账号：https://huggingface.co/join
- [ ] 打开模型页 https://huggingface.co/iFlytekOpenSource/Domux ，**同意 Gemma 使用条款**（gated 模型，不同意下载不了）
- [ ] 生成 HF token：https://huggingface.co/settings/tokens （Read 权限即可）
- [ ] AutoDL 充值 ¥30（4090D 约 ¥2.5–4/小时，整个任务 4–6 小时足够）

## 1. 租卡

- 镜像选 **PyTorch 2.x + CUDA 12.x**（公共镜像即可）
- 记下实例配置，案例里要写：RTX 4090D 24GB、AutoDL 付费云 GPU
- 开机后打开 JupyterLab → 终端

## 2. 一键执行（整段粘贴到终端）

```bash
# ---- 0) 学术加速（拉 HF 模型必需）----
source /etc/network_turbo

# ---- 1) 登录 HF（用你已生成并验证过的 token）----
pip install -U "huggingface_hub[cli]" -q
hf auth login          # 交互式粘贴 token
# 或跳过交互：export HF_TOKEN=<你的 hf_... token>（已验证可访问 gated 模型）

# ---- 2) 模型放数据盘（系统盘会满）----
export HF_HOME=/root/autodl-tmp/hf

# ---- 3) 克隆官方仓库（拿评测脚本和 4057 条测试集）----
git clone https://github.com/iflytek/domux.git && cd domux

# ---- 4) 安装 vLLM ----
pip install -U vllm -q
vllm --version > vllm_version.txt

# ---- 5) 固定 revision：取最新 commit SHA 并记录下来（案例必填）----
python - <<'EOF'
from huggingface_hub import list_repo_commits
sha = list_repo_commits("iFlytekOpenSource/Domux")[0].commit_id
print("PINNED_SHA=", sha)
open("pinned_revision.txt","w").write(sha)
EOF
SHA=$(cat pinned_revision.txt)
echo "SHA=$SHA"

# ---- 6) 下载固定版本（约 10GB，学术加速下几分钟）----
hf download iFlytekOpenSource/Domux --revision "$SHA"

# ---- 7) 后台起 vLLM 服务（BF16，4090D 24GB 无压力）----
nohup vllm serve iFlytekOpenSource/Domux --revision "$SHA" \
  --dtype bfloat16 --max-model-len 4096 \
  --gpu-memory-utilization 0.9 --port 8000 > vllm.log 2>&1 &

# ---- 8) 等服务就绪（出现 "Application startup complete" 即 OK）----
grep -m1 "Application startup complete" <(tail -f vllm.log)
```

## 3. 跑官方评测（4057 条，2–4 小时）

```bash
# 配置评测脚本（本地 vLLM 无需真实 key）
python - <<'EOF'
p = "eval/run_eval.py"
s = open(p).read()
s = s.replace('API_KEY = "your api key"', 'API_KEY = "EMPTY"')
s = s.replace('BASE_URL = "your api base url"', 'BASE_URL = "http://localhost:8000/v1"')
s = s.replace('MODEL = "your model name"', 'MODEL = "iFlytekOpenSource/Domux"')
open(p, "w").write(s)
print("patched OK")
EOF

# 跑（保留完整日志作证据）
python eval/run_eval.py 2>&1 | tee eval_log.txt
```

**中途去看进度**：新开一个终端 `tail -f eval_log.txt`。

**结果文件**：`eval/eval_summary.json`（官方指标汇总）、`eval/eval_results.jsonl`（逐条结果）。

## 4. 错误簇分析（加分项，1 分钟）

```bash
python - <<'EOF'
import json
from collections import Counter
rows = [json.loads(l) for l in open("eval/eval_results.jsonl", encoding="utf-8")]
fails = [r for r in rows if not r["result_correct"]]
print("总条数:", len(rows), "| 失败:", len(fails))
print("按类别:", dict(Counter(r["category"] for r in fails)))
for r in fails[:5]:
    print("\n---", r["category"], "---")
    print("Q:", r["query"][:60])
    print("P:", r["model_output"][:120].replace("\n", " / "))
    print("G:", r["gold"][:120].replace("\n", " / "))
EOF
```

## 5. 关机前必须收集的证据（漏了就得重新开机补）

- [ ] `eval_log.txt`（进度 + 汇总表）
- [ ] `eval/eval_summary.json`、`eval/eval_results.jsonl`
- [ ] `vllm.log` 开头 20 行（版本/配置/启动信息）
- [ ] `nvidia-smi` 输出或截图
- [ ] `cat vllm_version.txt`、`python --version`、`pip list | grep -E "vllm|torch"` 截图
- [ ] 3–5 条典型输入的原始输出截图（成功 + 失败各一些）

**收集完立刻关机**（关机不计费、环境保留），写案例在本地写。如要补跑，开机环境还在。

## 6. 提交（本地做，用配套草稿）

1. 发 HF Discussion：用 `hf_discussion_post.md` 草稿（标题 `[HER Hack-Astron #4] ...`）→ 记下讨论编号
2. 等 iflytek/domux **PR #15（案例框架）合并**后，fork 仓库，复制 `cases/TEMPLATE/` → `cases/<你的案例id>/`，按 `case_readme_filled.md` 填好（frontmatter 的 testedRevision 填第 5 步的 SHA，channels 填 Discussion 链接）
3. 提 PR：标题 `[case] <案例id> - <一句话结果>`，描述写 **Ref #20**（不能 Closes）
   - **不要提交任何权重文件**（safetensors/gguf/pt 等）
   - 不贴 token、个人缓存路径、家庭隐私数据
4. 发完 PR 把链接补到 Discussion 里

## 常见问题

| 问题 | 解法 |
|---|---|
| HF 下载慢/失败 | 重新 `source /etc/network_turbo`；或 `export HF_ENDPOINT=https://hf-mirror.com` |
| 磁盘满 | 确认 `HF_HOME=/root/autodl-tmp/hf` 已生效（数据盘） |
| OOM / 显存不足 | vLLM 启动参数改 `--gpu-memory-utilization 0.8` 或 `--max-model-len 2048` |
| 服务没起来 | `tail -20 vllm.log` 看报错；`curl -s localhost:8000/v1/models` 应返回模型列表 |
| 评测报错连不上 | 确认第 8 步 grep 到了 startup complete 再跑评测 |
