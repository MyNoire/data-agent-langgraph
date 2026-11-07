# 05: 评测支持 `--method`

**What to build:** 评测开发者用 `querypilot eval retrieval --method <lexical|vector|hybrid>` 选择检索方法，方法和 Embedding Profile 写进运行配置和报告，指标和 K 都不变。

**Blocked by:** 03

**Status:** ready-for-agent

- [ ] `evaluate_retrieval` 和 CLI 增加 `--method`，默认 `lexical`；每个检索请求都带上这个方法
- [ ] 运行配置和报告记录方法和 Profile（lexical 时为空）
- [ ] 沿用假检索器的运行器测试：方法传进每个请求，并写进运行配置和报告
- [ ] 检索评测文档写上 `--method` 的用法

## Comments
