# 当前状态（2026-08-25）

- v1 baseline 已冻结：`63f60d1884059379784c06ae35e84838d5525f9d`。
- v2 在 held-out 前冻结：`ad243f999d75bce3f1be35667ff3eaa734ef70e5`。
- 原 48 条 Domux raw outputs 及三个 evidence hash 未变。
- parser 指标已分离：48/48 结构合法，39/48 旧 action vocabulary 接受。
- 256 组 real-output cross-pair：v2 危险放行 0/256；v1/parser-fixed 为 160/256 和 256/256。
- 20 条 development fault suite：v2 20/20。
- 冻结后 84 条独立 held-out：51/84，Macro F1 0.6152，失败全部保留，没有回调 v2。
- Hugging Face Discussion 已公开发布并核验：
  `https://huggingface.co/iFlytekOpenSource/Domux/discussions/4`。
- 正式 `README.md` 已生成并回填 Discussion URL。
- 本地 23 项测试、原始证据校验、v2 重算、84 条 held-out 校验、官方案例校验、
  密钥／权重／大文件扫描与 diff 检查均通过。
- 待对外步骤：提交最终本地 commit，push fork，创建 body 含 `Ref #20` 的 PR；
  push 与创建 PR 前仍需操作时确认。
