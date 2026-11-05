# 05: 第一次基线评测

**What to build:** 得到一组可以追溯的 BM25 检索基线数字：toxicology 和 student_club 共 88 题，英文原题，不带 evidence。

**Blocked by:** 01, 03, 04

**Status:** ready-for-agent

- [ ] 生成两个库的完整题集；manifest 提交进 Git
- [ ] 在正式知识库上运行检索评测，记录配置、命令和产物位置
- [ ] 用中文总结两个库的主指标、Hits to Cover 和 Column Recall@10，以及非冻结题里的典型失败类型
- [ ] 按 ADR 0001，这一张只分析，不修改知识、参数或代码；改进作为下一项工作

## Comments
