# 02: 付费运行 small-to-big，写结论

**What to build:** 学习者得到"列级 / small-to-big / 全量知识"三组可追溯的真实结果，并按看结果前定好的规则选出冻结时用的知识结构。只新跑 small-to-big 一组，⑤ 和 ③ 复用已有运行。见 [spec](../spec.md)「实验」「文档」。

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] 运行前征得用户同意（付费：88 次模型调用，加上检索的 Embedding 和重排调用）
- [ ] 跑一次 `eval graph --context-source retrieval --small-to-big`，配置与 ⑤ `14d32c18` 相同：hybrid + 重排、`top_k=10`、`columns` 写法、检索题集 `retrieval_cases_values`、88 题 SQL 题集、`gpt-6.1-sol`、`direct-v2`、`QUERYPILOT_MAX_ROWS=10000`、`QUERYPILOT_QUERY_TIMEOUT_MS=20000`、4 个线程；记录运行目录和费用
- [ ] 与 ⑤ `14d32c18`、③ `50e2edf1` 比较：内容匹配和严格执行匹配（整体、非冻结、分库）；按库的提示词 token 平均值和最小/最大值（由逐题 `prompt_tokens` 算出）；`tables_complete` 拆分
- [ ] 列出 small-to-big 相对 ⑤ 在非冻结题上各自对、各自错的题号，对照 spec 补充说明里的 6 道"缺普通列"题；冻结题只计数，不逐题看
- [ ] 按判定规则给出结论：非冻结内容匹配相差不超过 3 题算打平，打平选提示词 token 更少的；small-to-big 多对 4 题及以上才改用它。写明前提：两个小库、噪声没测、表级比较推迟
- [ ] 不根据结果调整 `top_k`、渲染规则、提示词或知识内容
- [ ] 结果写进 SQL 评测文档；路线图在已完成部分记一行，并写明定下的知识结构
- [ ] 如果结论是改用 small-to-big，或结果与预期明显不同，问用户是否写 ADR
