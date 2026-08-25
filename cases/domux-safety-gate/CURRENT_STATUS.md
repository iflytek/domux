# 当前状态（2026-08-25）

- v1 baseline 原始 commit：`63f60d1884059379784c06ae35e84838d5525f9d`；
  DCO 等价 commit：`16161aef1c4457f9b233d71cf28a6b6e074efc67`（tree 相同）。
- v2 held-out 前原始冻结 commit：`ad243f999d75bce3f1be35667ff3eaa734ef70e5`；
  DCO 等价 commit：`f7186768855398d13ecb5a0b205db02f68190708`（tree 相同）。
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
- GitHub PR 已创建：`https://github.com/iflytek/domux/pull/24`，body 含 `Ref #20`。
- CLA 已由贡献者签署；DCO 历史等价映射已记录，本地复核已通过，等待远端门禁刷新。
