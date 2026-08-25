# Domux Safety Gate 本地审核索引

正式 case 的发布前最终稿为 `README.SUBMISSION_DRAFT.md`。它已包含：

- 真实 Domux / Tesla T4 / NF4 / fixed revision 运行证据；
- v1 parser metric 更正与 input-only 安全限制；
- v1 vs parser-fixed v1 vs output-aware v2 受控消融；
- 256 组 real-output cross-pair mismatch attack；
- 20 条 development fault injection；
- v2 冻结后一次性 84 条独立 held-out 及所有失败；
- 可重算命令、局限、隐私和许可说明。

正式发布前唯一不能在本地填写的字段是尚未产生的公开 Domux Hugging Face
Discussion URL。获得 URL 后，将它同时填入 frontmatter `channels` 与正文，再复制为
`README.md`。

重要边界：held-out 的 `51/84` 不得重标、重跑或回调 v2；该结果不是
Domux 模型准确率，也不是产品安全认证。
