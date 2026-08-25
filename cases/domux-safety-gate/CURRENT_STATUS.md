# 当前状态（2026-08-25）

- v1 baseline 已冻结：`63f60d1884059379784c06ae35e84838d5525f9d`。
- v2 在 held-out 前冻结：`ad243f999d75bce3f1be35667ff3eaa734ef70e5`。
- 原 48 条 Domux raw outputs 及三个 evidence hash 未变。
- parser 指标已分离：48/48 结构合法，39/48 旧 action vocabulary 接受。
- 256 组 real-output cross-pair：v2 危险放行 0/256；v1/parser-fixed 为 160/256 和 256/256。
- 20 条 development fault suite：v2 20/20。
- 冻结后 84 条独立 held-out：51/84，Macro F1 0.6152，失败全部保留，没有回调 v2。
- 待对外步骤：发布 Hugging Face Discussion，填真实 URL 为 `README.md`，跑 full validator，
  push fork，创建 body 含 `Ref #20` 的 PR。当前任务禁止这些对外动作。
