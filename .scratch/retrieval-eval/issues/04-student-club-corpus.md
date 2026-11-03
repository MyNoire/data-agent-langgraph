# 04: student_club 的 Corpus

**What to build:** 知识库里多一个讲会员、活动、预算的业务库 student_club，可以被检索。构建流程和 Build Profile 都与 toxicology 一致。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Claude 起草 Data Semantics：每张表的中文用途、一行代表什么、每个外键关系的含义。起草时只参考表结构和本地完整 BIRD mini-dev 里的字段说明 CSV，不看题目、evidence 和金标准 SQL
- [ ] **检查点：用户审阅 Data Semantics 并通过**，之后才能构建
- [ ] 走现有命令完成抽取 → 丰富 → 修正 → 构建（`r1-4-v1`）→ 校验；修正计划只处理字段说明 CSV 本身的问题
- [ ] 导入正式知识库，建好 BM25 索引，回读核对通过
- [ ] 用一两条手写查询经 `retrieve_knowledge` 确认能检索到
- [ ] 原始数据和 Corpus 产物不进 Git；记录构建配置和产物位置

## Comments
