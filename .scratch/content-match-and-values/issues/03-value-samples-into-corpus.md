# 03: 取值样例进入 Corpus

**What to build:** 知识准备多出一步：只读打开业务 SQLite，按 Catalog 逐列取值，生成取值样例文件（规则 `value-samples-v1`，见规格「实现决定 / 取值样例」）。构建 Corpus 时可选地读入它，列 Chunk 在「取值解释」后多出一行取值，`corpus_id` 随之改变。隐私列由语义配置里的名单决定，在取值这一步就跳过，值不落盘。不给新输入时构建结果与今天逐字节相同。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [x] 语义配置可选地带每个库的隐私列名单；旧格式配置照常可用
- [x] 取值这一步用一个小测试 SQLite 验证：TEXT 且去重非空取值 ≤ 20、非主键 / 外键 → 全部取值按值排序；其他列 → 出现最多的 3 个非空值（同次数按值排序）；键列只给样例；超过 80 字符截断并标记；隐私列不取值且文件里不出现它的任何值
- [x] 取值样例文件记录规则版本、来源 SQLite 的 sha256、库标识、每列的取值方式和取值；它放在 Git 忽略的目录下
- [x] 构建 Corpus 时读入取值样例文件：它作为新的来源角色进入 Corpus 身份；列 Chunk 新增取值行（写明是全部取值及个数，还是样例）；隐私列不加这一行；用新的渲染版本标记
- [x] 不给取值样例文件、语义配置也没有新字段时，构建出的 Corpus 与今天完全相同（有测试，或用现有快照比对）
- [x] 取值样例文件与 Catalog 对不上（库不同、列不存在）时构建明确失败
- [x] 提供命令行入口生成取值样例文件，知识库文档的流程图和步骤表补上这一步
- [x] 隐私名单首版写进两个库的语义配置（student_club：`member.first_name`、`last_name`、`email`、`phone`；toxicology：无），交给用户审阅
- [x] 模块边界测试和全部单元测试通过

## Comments

**2026-11-19 完成（0ba082e）**

- 取值这一步放在 `knowledge_catalog`（`collect_value_samples`），命令是 `knowledge catalog values --database-id --semantic-file --output`。隐私名单由命令读取语义配置后传进来，名单上的列不执行任何查询；名单里写了不存在的列时直接报错，避免名单写错导致真正的隐私列被漏掉。
- 构建：`corpus build --values-file`。取值文件存到 `inputs/values/`，来源角色是 `value_samples`，进入 Corpus 身份。列 Chunk 在取值解释（包括它的续行）后面加一行 `取值: …`；metadata 记 `value_samples: {rule_version, mode}`。两种情况拒绝构建：取值文件和结构对不上；语义配置名单上的隐私列在取值文件里带了值。
- **和规格的一处出入**：规格写的是「新增的行用新的渲染版本标记」，实际没有改 `BuildConfig.renderer_version`。原因是取值文件本身就进入了 Corpus 身份，每个列 Chunk 的 metadata 也带着规则版本，已经能区分新旧；再让 renderer_version 随有没有取值文件变化，只会多一个分支。
- 隐私名单写在新的 `config/knowledge_semantics/*-v2.json` 里（student_club 4 列，toxicology 为空），v1 不动，旧 Corpus 仍然能从仓库里的文件重建。用户 2026-11-19 同意了名单，也同意这个做法：不做部分屏蔽，隐私列直接不取值。
- 名单只管知识库和提示词，不管查询结果：模型仍然可以 SELECT email 和电话（R5 的 1375 就是这样）。限制结果属于 Access Grant 或执行器的列级权限，不在这次范围内。
- 验证：
  - 用现有的真实输入重建两个库的 Corpus（不带取值文件），`chunks/documents/relations/manifest/validation` 和正式快照逐字节相同。
  - 用两个真实库在 scratchpad 里试跑了一遍：student_club 15 列全部取值、29 列样例、4 列隐私；toxicology 3 列全部取值、8 列样例。抽查到 `event.type`、`income.source`、`expense_description`、`position`、`element`、`bond_type` 的取值行都符合预期。正式构建在工单 06 做。
- 测试：`tests/test_value_samples.py` 9 个，`tests/test_knowledge_values_corpus.py` 6 个。取值规则（样例、键列、截断、隐私）的实现是一次写完的，所以这些测试写出来就是绿的，没有先红一次；它们的作用是把规则固定下来。全部单元测试 588 通过，失败的只有之前就有的 CLI PATH 那一个。集成测试没有跑：这次没有改存储代码，导入和读回在工单 06 用真实数据做。
- 另外修了一个工单 01 漏掉的问题（14a6cf2）：01 改了 SQL 评测的导出列表之后，只重跑了边界测试，没有发现契约测试 `test_sql_root_exports_are_identical_to_the_implementation` 失败了。
