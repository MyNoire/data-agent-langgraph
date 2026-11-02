# 本机 PostgreSQL 的 BM25 安装记录

2026-10-31：已在现有 `querypilot-knowledge-knowledge-db-1` 容器、`querypilot_knowledge` 数据库中安装并验证。没有另建数据库、实例或数据卷；当前容器 ID 和卷保持。完整 R3 查询入口尚未接入，这份记录证明数据库侧能力。

例如，现有知识中的 `chlorine` 和 `cl` 均能检索到结果；中文词项“元素、组成”能命中知识。结果来自真实插件索引和 BM25 得分，没有自定义名称加分。

## 实际版本与安装位置

| 项目 | 当前本机结果 |
| --- | --- |
| PostgreSQL | 18.6，安装前后相同 |
| pgvector | public 中的 0.8.7，安装前后相同 |
| pg_search | 0.26.4，Debian 12 Bookworm / amd64 官方包 |
| 包 SHA256 | `1452e93475767b445f4fea27bcdcb2c334dbddee6abca0dac68614ef195a4fb6` |
| 预加载 | `shared_preload_libraries = 'pg_search'` |
| 原知识索引 | `querypilot_kb.knowledge_chunks_bm25_idx`，`USING paradedb` |

安装包约54 MB，下载内容与官方 Release API 的 SHA256 相同。依赖模拟确认 0 升级、0 删除，只新增插件与4个运行库；实际 PG/vector 版本也再次核对。

插件文件直接安装在当前容器中。**停止/启动或重启同一个容器会保留；删除并重建容器时，需要先在目标镜像恢复插件文件。** 当前镜像本身仍是原 pgvector 镜像，不能假设它已包含 pg_search。数据库卷中的预加载配置和索引会保留，重建前应使用包含相同插件的镜像；不能让裸镜像带着缺失的预加载库启动。

## 本次做过哪些安装操作

以下记录实际执行的路径，当前已完成，无需重新安装。未来重建环境时应先检查实际平台和已有版本。

1. 下载官方固定发行包并校验 SHA256，再复制到现有容器的 `/tmp/querypilot-pg-search-0.26.4-053051.deb`。
2. 容器中运行 `dpkg-deb -I` 和 `apt-get -s install --no-install-recommends <包路径>`，检查版本、依赖与安装动作。
3. 运行 `apt-get install -y --no-install-recommends <包路径>`，检查 `ldd /usr/lib/postgresql/18/lib/pg_search.so` 无缺失库。
4. 原预加载项为空，执行 `ALTER SYSTEM SET shared_preload_libraries = 'pg_search'`，重启同一个容器。以后若存在其他预加载项，须保留原列表。
5. 在现有库执行 `CREATE EXTENSION pg_search`，建立下节索引；没有改原知识列、迁移文件、账号属性或原表权限。

官方包：[固定下载地址](https://github.com/paradedb/paradedb/releases/download/v0.26.4/postgresql-18-pg-search_0.26.4-1PARADEDB-bookworm_amd64.deb)。步骤依据：[官方安装文档](https://www.paradedb.com/docs/operate/deploy/self-hosted/extension)。

## 当前原知识索引

这是已实际执行的定义，当前索引已存在。不要重复执行或替换它；后续由 T046 将准备、配置检查与维护正式接入管理流程。

```sql
CREATE INDEX knowledge_chunks_bm25_idx
ON querypilot_kb.knowledge_chunks USING paradedb (
    chunk_id,
    (chunk_key::pdb.literal),
    (corpus_id::pdb.literal),
    (database_id::pdb.literal),
    (schema_version::pdb.literal),
    (knowledge_type::pdb.literal),
    (title::pdb.jieba),
    (content::pdb.jieba),
    (schema_name::pdb.simple),
    (table_name::pdb.simple),
    (column_name::pdb.simple),
    ((CASE WHEN jsonb_typeof(metadata->'display_name') = 'string'
        THEN metadata->>'display_name' END)::pdb.jieba('alias=display_name'))
);
```

名称/正文使用成熟分词配置，只有字符串 display_name 参与额外文本索引；没有将完整 metadata 或父文档全文加入搜索。chunk_key 和三元范围/类型的 literal 字段用于排序与过滤，验证查询不拿逻辑key或范围标识做全文召回。[建索引](https://www.paradedb.com/docs/reference/indexing/create-index)、[Jieba](https://www.paradedb.com/docs/reference/tokenizers/available-tokenizers/jieba)。

## 实际验证与未完成部分

- 原知识7类查询：标识符、英文、短编码、中文、关系、正常无匹配、OR部分命中。使用现有 reader 及 `knowledge.api.open_read_scope` 的 REPEATABLE READ READ ONLY 会话；动态词项/范围/类型/K 均绑定参数，检查范围/类型、3次有序身份重复性与真实 ParadeDB 执行计划。
- 同一数据库内创建6行原创评分样本，证明重复词提高得分、稀有词可排在常见词之前、同分次序稳定、中文词项匹配以及任意子串不会自动命中。只对本次自有表赋 reader SELECT；结束后表、索引和专属 schema 已全部精确清理。
- 原20块知识、4文档、5关系、1份corpus和001迁移的整行指纹未变；账号属性及原知识表权限未变。
- 现有 `knowledge store check` 和 `knowledge store verify --corpus-dir data/rag/toxicology/r1-4-corpus` 均实际成功，原内容与首次时间的 SHA256 回读核验通过。

本机证据：`artifacts/validation/2026-10-31/bm25-install-053051/`。其中保存官方包信息、安装日志、前后摘要、索引定义、原知识查询结果/执行计划和原创评分结果；详细业务结果仍进入统一 runtime 目录。没有运行会全表清理的旧 pytest fixture。

这不等于完整 R3 已实现：Python 查询过滤/OR核心、完整 `retrieve_knowledge`、正式10例的 Recall@K、运行配置/索引语料指纹的正式管理及评测接线继续按[任务清单](../../specs/003-r3-lexical-retrieval/tasks.md)。安装阶段结束时T045仍未勾选，仅记录当时的安装/基础验证子范围；T046～T048与五个结对核心未自动完成。没有性能提升或 SQL 准确率结论，没有调用模型。


## 2026-10-31：工程管理入口已接好

现在可用 `querypilot knowledge store prepare-search` 明确准备或复用数据库扩展/索引；`querypilot knowledge store check-search` 通过reader只读检查实际设施。当前均实际成功，原索引重复准备返回reused。缺系统包或预加载时，管理员命令明确失败，需要按上文安装；它不运行apt、不重启PG。版本/索引漂移也明确失败，不擅自重建。

库调用方从knowledge.api使用prepare_search/check_search；检索则在open_read_scope提供的同一只读一致事务内调用check_search_session，可对照expected_profile/expected_fingerprint，继续复用原范围和预算检查。返回的SearchIndexState包含实际search_index_profile（含实际index_schema）、index_corpus_fingerprint、indexed_chunks和指纹格式版本，不含知识正文。

index-corpus-sha256-v1涵盖全knowledge_chunks：按chunk_id排序，每行固定JSON数组依次为chunk_id、chunk_key、corpus_id、database_id、schema_version、knowledge_type、title、content、schema_name、table_name、column_name、字符串display_name或NULL。PG对其jsonb文本的UTF8字节做SHA256；Python只接收32字节摘要，以`index-corpus-sha256-v1`加换行开头，依次合并摘要，最后合并8字节大端块数，再取SHA256。NULL与空串保留区别，非搜索metadata与时间不参与搜索语料指纹；原知识完整性仍由独立verify负责。

两端分词的7类兼容探针、新增8项真实设施测试通过（包括同库自有表的首次管理创建和复用），原R2 check/verify也成功。旧PG查询字符投影已删除，Python prepare_query就绪。中文过滤/去重、完整R3和正式评测仍待结对，任务现为28/48，具体命令与验证边界见[当前指南](../../specs/003-r3-lexical-retrieval/quickstart.md#10-2026-10-31-工程准备后的当前入口)。本轮证据：`artifacts/validation/2026-10-31/bm25-prep-060943/`。

本轮已验证的是当前事务可见搜索行的逻辑语料摘要。删除、VACUUM或插件merge后，物理词频统计与相同逻辑摘要的对应关系尚未验证；完整检索稳定性仍由后续T014/T028实际验收，不据此宣称所有维护情形前后分数相同。
