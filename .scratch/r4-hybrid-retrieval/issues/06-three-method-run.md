# 06: 三方法评测与分析

**What to build:** 在同一份 88 题、同一份 Corpus 上，用 `lexical`、`vector`、`hybrid` 各跑一次检索评测，得到可以追溯的对比数字，并用非冻结题说明语义召回补上了什么、又让什么变差了。

**Blocked by:** 04, 05

**Status:** ready-for-agent

- [ ] **征得用户同意后**，三种方法各跑一次：top_k=20，K=5/10，Column Recall@10；记录命令、题集 sha256、Knowledge Scope、Profile 和三个运行目录
- [ ] 中文对比表：三种方法的主指标、Table Recall、Hits to Cover、Column Recall@10 和检索耗时，分整体和两个库
- [ ] 用临时脚本读三份 `result.json`，逐题标出非冻结题中"BM25 失败、vector 或 hybrid 救回"和"hybrid 比 BM25 变差"的题，并归纳原因；按 ADR 0001，只分析，不改知识、参数或代码
- [ ] 更新检索评测文档（第 3 节加三方法结果）、知识库文档（向量与混合检索一节）和路线图
- [ ] 提醒用户更新面试笔记 `检索评测指标设计.md`

## Comments
