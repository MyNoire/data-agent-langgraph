# 02: 付费运行 small-to-big，写结论

**What to build:** 学习者得到"列级 / small-to-big / 全量知识"三组可追溯的真实结果，并按看结果前定好的规则选出冻结时用的知识结构。只新跑 small-to-big 一组，⑤ 和 ③ 复用已有运行。见 [spec](../spec.md)「实验」「文档」。

**Blocked by:** 01

**Status:** done

- [x] 运行前征得用户同意（付费：88 次模型调用，加上检索的 Embedding 和重排调用）
- [x] 跑一次 `eval graph --context-source retrieval --small-to-big`，配置与 ⑤ `14d32c18` 相同：hybrid + 重排、`top_k=10`、`columns` 写法、检索题集 `retrieval_cases_values`、88 题 SQL 题集、`gpt-6.1-sol`、`direct-v2`、`QUERYPILOT_MAX_ROWS=10000`、`QUERYPILOT_QUERY_TIMEOUT_MS=20000`、4 个线程；记录运行目录和费用
- [x] 与 ⑤ `14d32c18`、③ `50e2edf1` 比较：内容匹配和严格执行匹配（整体、非冻结、分库）；按库的提示词 token 平均值和最小/最大值（由逐题 `prompt_tokens` 算出）；`tables_complete` 拆分
- [x] 列出 small-to-big 相对 ⑤ 在非冻结题上各自对、各自错的题号，对照 spec 补充说明里的 6 道"缺普通列"题；冻结题只计数，不逐题看
- [x] 按判定规则给出结论：非冻结内容匹配相差不超过 3 题算打平，打平选提示词 token 更少的；small-to-big 多对 4 题及以上才改用它。写明前提：两个小库、噪声没测、表级比较推迟
- [x] 不根据结果调整 `top_k`、渲染规则、提示词或知识内容
- [x] 结果写进 SQL 评测文档；路线图在已完成部分记一行，并写明定下的知识结构
- [x] 如果结论是改用 small-to-big，或结果与预期明显不同，问用户是否写 ADR

## 注意事项

- 打开开关后，检索成功但全量读取失败的题会记为 error，`tables_complete` 记为 False。两个库每次全量读取只有 20 或 64 个 Chunk，预计不会发生；如果结果里出现，要单独列出来，不能算成"检索没拿全"。

## Comments

- 2026-11-21 运行（Claude，用户同意后）：
  - 命令：`QUERYPILOT_MAX_ROWS=10000 QUERYPILOT_QUERY_TIMEOUT_MS=20000 python -m querypilot eval graph --cases evals/bird_toxicology_student_club.json --workers 4 --context-source retrieval --structure-format columns --retrieval-cases evals/retrieval/retrieval_cases_values.json --method hybrid --rerank --small-to-big`。代码版本 `ebaba54`，工作区干净。
  - 运行目录 `artifacts/runs/evaluation/2026-11-21/3824e41691ab4667bd4dd36494cbc861/`，0 个 error。token：输入 168619、输出 7005，另有 88 次 Embedding、至多 88 次 Rerank。
  - 结果：非冻结内容匹配 53/72（⑤ 54、③ 54），严格匹配 36（⑤ 37、③ 35）。打平，按规则选 token 更少的 ⑤ 列级。结论与预期一致，所以没有问是否写 ADR。
  - `tables_complete`：80 / 8（⑤ 是 81 / 7），差的是一道冻结题，是两次运行之间检索服务的结果不同。没出现注意事项里说的全量读取失败。
  - 逐题差异和结论写在 SQL 评测文档的「知识结构：Small-to-Big」一节。
