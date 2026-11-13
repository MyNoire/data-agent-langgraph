# 本机 PostgreSQL + pg_search 安装与维护

知识库的 BM25 检索依赖 ParadeDB 的 pg_search 扩展。本机环境于 2026-10-31 安装完成，装在现有容器 `querypilot-knowledge-knowledge-db-1` 的 `querypilot_knowledge` 数据库里。

## 版本

| 项目 | 版本 |
| --- | --- |
| PostgreSQL | 18.6 |
| pgvector | 0.8.7（public schema） |
| pg_search | 0.26.4，Debian 12 Bookworm / amd64 官方包，SHA256 `1452e93475767b445f4fea27bcdcb2c334dbddee6abca0dac68614ef195a4fb6` |
| 预加载 | `shared_preload_libraries = 'pg_search'` |
| 知识索引 | `querypilot_kb.knowledge_chunks_bm25_idx`，`USING paradedb` |

**注意**：插件文件是直接装进当前容器的。停止、启动或重启同一个容器，插件都还在；但**删除并重建容器**时，要先在新镜像里装好同一版本的插件。数据库卷里保存着预加载配置和索引，如果用不带插件的镜像启动，PostgreSQL 会因为找不到预加载库而起不来。

## 安装步骤（重建环境时参考）

1. 下载[官方固定版本的安装包](https://github.com/paradedb/paradedb/releases/download/v0.26.4/postgresql-18-pg-search_0.26.4-1PARADEDB-bookworm_amd64.deb)，校验 SHA256 后复制进容器。
2. 先用 `dpkg-deb -I` 和 `apt-get -s install --no-install-recommends <包>` 检查版本和依赖，再正式安装；用 `ldd /usr/lib/postgresql/18/lib/pg_search.so` 确认没有缺失的库。
3. 执行 `ALTER SYSTEM SET shared_preload_libraries = 'pg_search'`，然后重启容器。如果已经有其他预加载项，要保留原来的列表。
4. 用项目命令建扩展和索引（见下一节），不要手写 SQL。

步骤依据是[官方安装文档](https://www.paradedb.com/docs/operate/deploy/self-hosted/extension)。

## 用项目命令管理

```powershell
uv run querypilot knowledge store prepare-search   # 管理员：创建或复用扩展和索引
uv run querypilot knowledge store check-search     # reader：只读检查实际的扩展、索引定义和索引语料
```

- `prepare-search` 遇到已有且一致的索引时返回 `reused`。缺少系统包或预加载配置时，它会明确失败，不会自己跑 apt，也不会重启 PostgreSQL。版本或索引定义和预期不一致时也会失败，不会擅自重建。
- 索引定义固定在 `src/querypilot/knowledge/knowledge_store/_search.py`：`title`、`content` 和展示名用 `jieba` 分词，表名、列名、schema 名用 `simple` 分词，Scope 和类型字段用 `literal` 做过滤。
- `check-search` 会返回索引语料指纹（`index-corpus-sha256-v1`）：它是按 `chunk_id` 排序后、所有可检索字段的摘要，可以用来确认索引覆盖的语料和预期一致。

检索行为的回归保护是 `tests/integration/test_toxicology_smoke.py`（L01–L10），它只读访问正式知识库。

## BM25 的统计量覆盖整个索引

BM25 分数用到整个索引的文档数、词频和平均长度，不按 Knowledge Scope 分开算。维护时要记住两点：

- **往知识库导入新的 Corpus，已有 Corpus 的 BM25 分数会变。** 对比实验前后不要改动索引里的 Corpus。
- **删掉的行在 `VACUUM` 之前仍然计入统计量。** 手工删除过 `knowledge_chunks` 的行之后，执行 `VACUUM querypilot_kb.knowledge_chunks`，否则分数取决于自动清理什么时候发生。

用下面的查询看索引的段；干净的状态下各段的 `num_docs` 加起来等于表的行数，多出来的就是还没清理的已删除行：

```sql
SELECT segno, mutable, num_docs, num_deleted FROM paradedb.index_info('querypilot_kb.knowledge_chunks_bm25_idx');
```

## 测试用的数据库

需要写入知识的集成测试都跑在同一个容器的专用测试库 `querypilot_knowledge_test` 上。测试夹具会在这个库里建 pg_search 扩展和同样定义的 BM25 索引（已有则复用），每个用例前后清空知识行并执行 `VACUUM`。正式库 `querypilot_knowledge` 只被只读的测试访问，`tests/integration/_bm25_fixtures.py` 在测试前后核对它的行和索引段都没有变。
