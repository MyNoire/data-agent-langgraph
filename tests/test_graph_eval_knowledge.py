"""图评测的 knowledge 来源：Scope 从检索题集来，结构写法和渲染版本记进运行配置与报告。"""

import json

import pytest

from querypilot.evaluation.api import RetrievalCase, RetrievalCaseSet, export_public, knowledge_scopes_from_cases, run_graph
from querypilot.retrieval.api import KnowledgeScope, RetrievalHit
from querypilot.sql_generation.api import GeneratedSql

from test_graph_eval import ITEMS_DDL, MATCHING_SQL, _cases, _run_config, _sample_run

SAMPLE_SCOPE = KnowledgeScope(corpus_id="a" * 64, database_id="sample", schema_version="b" * 64)

ITEMS_TABLE = '''数据库: sample; 表: "main"."items"
表用途: 记录物品编号和名称。
记录粒度: 一件物品。
字段目录: "id", "name"
行标识线索（主键声明、成员顺序）: 未声明
出向关联（声明外键）: 未声明'''

ITEMS_NAME = '''数据库: sample; 字段: "main"."items"."name"
声明类型: TEXT
not_null 声明标志: False; 主键位置: 0; hidden: 0
默认 SQL 表达式（仅文本）: 未声明
展示名: 来源未说明
字段解释: 物品名称
取值解释: 来源未说明'''

SAMPLE_CHUNKS = [
    RetrievalHit(rank=1, score=0.0, chunk_id="00000000-0000-5000-8000-000000000001",
                 chunk_key='["column","sample","main","items","name"]', knowledge_type="column", title="t",
                 content=ITEMS_NAME, content_hash="c" * 64, schema_name="main", table_name="items", column_name="name"),
    RetrievalHit(rank=2, score=0.0, chunk_id="00000000-0000-5000-8000-000000000002",
                 chunk_key='["table","sample","main","items"]', knowledge_type="table", title="t",
                 content=ITEMS_TABLE, content_hash="c" * 64, schema_name="main", table_name="items"),
]


def _recording(schemas: list[str], scopes: list[KnowledgeScope]):
    """假生成器和假知识读取：分别记下收到的 Schema 和 Scope。"""
    def generator(question, schema, *_):
        schemas.append(schema)
        return GeneratedSql(MATCHING_SQL)

    def reader(scope):
        scopes.append(scope)
        return SAMPLE_CHUNKS
    return generator, reader


def _retrieval_cases(tmp_path, scopes: list[KnowledgeScope]):
    """写出一份检索题集，每个 Scope 一道题，返回文件路径。"""
    case_set = RetrievalCaseSet(cases=[
        RetrievalCase(question_id=index, db_id=scope.database_id, difficulty="simple", question=f"r{index}",
                      frozen=False, scope=scope, relevant_tables=[], relevant_columns=[])
        for index, scope in enumerate(scopes, start=1)])
    path = tmp_path / "retrieval_cases.json"
    path.write_text(case_set.model_dump_json(), encoding="utf-8")
    return path


def test_knowledge_run_gives_the_model_rendered_knowledge_and_records_format_render_version_and_scopes(tmp_path):
    """输入：一道 sample 的题，context_source="knowledge"、structure_format="ddl"，对照表 sample → SAMPLE_SCOPE。

    输出：
    - 假知识读取收到 SAMPLE_SCOPE；模型收到 items 的表用途、建表语句和 name 列。
    - 报告、运行配置和公开导出都记下 structure_format=ddl、schema_render_version=r5-schema-v1 和各库的 Scope。
    - 运行目录里的任何文件都不含知识正文。
    """
    schemas, scopes = [], []
    generator, reader = _recording(schemas, scopes)
    cases, settings = _sample_run(tmp_path, _cases(1))

    report = run_graph(cases, settings, generator=generator, context_source="knowledge", structure_format="ddl",
                       knowledge_scopes={"sample": SAMPLE_SCOPE}, knowledge_reader=reader)

    assert scopes == [SAMPLE_SCOPE]
    assert schemas == [f'''表 "items"
  表用途: 记录物品编号和名称。
  记录粒度: 一件物品。
{ITEMS_DDL}
  列 "name" TEXT: 物品名称''']
    assert report["summary"]["success"] == 1
    expected = {"context_source": "knowledge", "structure_format": "ddl", "schema_render_version": "r5-schema-v1",
                "knowledge_scopes": {"sample": SAMPLE_SCOPE.model_dump()}}
    assert {key: report[key] for key in expected} == expected
    config = _run_config(settings, report["run_id"])
    assert {key: config[key] for key in expected} == expected
    run_dir = next(path for path in settings.artifacts_dir.glob("runs/evaluation/*/*") if path.name == report["run_id"])
    public = export_public(run_dir, tmp_path / "public.json")
    assert {key: public[key] for key in expected} == expected
    assert not [path for path in run_dir.rglob("*") if path.is_file() and "物品" in path.read_text(encoding="utf-8")]


def test_sources_without_knowledge_record_no_structure_format_render_version_or_scopes(tmp_path):
    """输入：context_source="schema" 的一次运行。输出：报告和运行配置里没有这三项，不让人误以为用了某种写法。"""
    cases, settings = _sample_run(tmp_path, _cases(1))

    report = run_graph(cases, settings, generator=lambda *_: GeneratedSql(MATCHING_SQL))

    for fields in (report, _run_config(settings, report["run_id"])):
        assert not {"structure_format", "schema_render_version", "knowledge_scopes"} & set(fields)


@pytest.mark.parametrize("knowledge_scopes", [None, {"other": SAMPLE_SCOPE}])
def test_knowledge_run_without_a_scope_for_every_database_fails_before_any_run_is_recorded(tmp_path, knowledge_scopes):
    """输入：knowledge 来源，但没给对照表，或对照表里没有题集用到的 sample。

    输出：ValueError；没有建立运行记录，知识读取和模型都没被调用。
    """
    schemas, scopes = [], []
    generator, reader = _recording(schemas, scopes)
    cases, settings = _sample_run(tmp_path, _cases(1))

    with pytest.raises(ValueError):
        run_graph(cases, settings, generator=generator, context_source="knowledge",
                  knowledge_scopes=knowledge_scopes, knowledge_reader=reader)

    assert (schemas, scopes) == ([], [])
    assert not (tmp_path / "artifacts").exists()


def test_knowledge_scopes_come_from_the_retrieval_case_set(tmp_path):
    """输入：检索题集里 sample 有两道题（同一个 Scope），another 有一道。输出：每个库一项的对照表。"""
    another = KnowledgeScope(corpus_id="d" * 64, database_id="another", schema_version="e" * 64)

    scopes = knowledge_scopes_from_cases(_retrieval_cases(tmp_path, [SAMPLE_SCOPE, another, SAMPLE_SCOPE]))

    assert scopes == {"sample": SAMPLE_SCOPE, "another": another}


def test_retrieval_case_set_with_two_scopes_for_one_database_is_rejected(tmp_path):
    """输入：sample 的两道题用了不同的 Scope（不同 Corpus）。输出：ValueError，不随便挑一个。"""
    other_corpus = SAMPLE_SCOPE.model_copy(update={"corpus_id": "f" * 64})

    with pytest.raises(ValueError):
        knowledge_scopes_from_cases(_retrieval_cases(tmp_path, [SAMPLE_SCOPE, other_corpus]))


def test_cli_knowledge_flags_reach_the_graph_and_the_run_config_matches_the_runner(tmp_path, monkeypatch, capsys):
    """输入：eval graph --context-source knowledge --structure-format ddl --retrieval-cases <题集>；
    再用同样的参数直接调用 run_graph。

    输出：假知识读取收到题集里 sample 的 Scope；命令行与直接调用写下的运行配置完全相同。
    """
    from querypilot.entrypoints import cli
    from querypilot.evaluation.sql import api as evaluation_api

    schemas, scopes = [], []
    generator, reader = _recording(schemas, scopes)
    cases, settings = _sample_run(tmp_path, _cases(1))
    retrieval_cases = _retrieval_cases(tmp_path, [SAMPLE_SCOPE])
    monkeypatch.setattr(evaluation_api, "generate_sql", generator)
    monkeypatch.setattr(evaluation_api, "load_scope_chunks", reader)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    assert cli.main(["eval", "graph", "--cases", str(cases), "--context-source", "knowledge",
                     "--structure-format", "ddl", "--retrieval-cases", str(retrieval_cases)]) == 0
    summary = json.loads(capsys.readouterr().out)
    direct = run_graph(cases, settings, generator=generator, context_source="knowledge", structure_format="ddl",
                       knowledge_scopes={"sample": SAMPLE_SCOPE}, knowledge_reader=reader)

    assert scopes == [SAMPLE_SCOPE, SAMPLE_SCOPE]
    assert summary["structure_format"] == "ddl"
    assert _run_config(settings, summary["run_id"]) == _run_config(settings, direct["run_id"])


def test_cli_knowledge_source_without_retrieval_cases_exits_before_any_run_is_recorded(tmp_path, monkeypatch):
    """输入：--context-source knowledge，没给 --retrieval-cases。输出：参数解析阶段就退出，没有运行记录。"""
    from querypilot.entrypoints import cli

    cases, settings = _sample_run(tmp_path, _cases(1))
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    with pytest.raises(SystemExit):
        cli.main(["eval", "graph", "--cases", str(cases), "--context-source", "knowledge"])

    assert not (tmp_path / "artifacts").exists()


def test_cli_accepts_every_structure_format_the_renderer_knows():
    """输入：渲染认识的每一种 StructureFormat；输出：命令行都接受，原样传下去（命令行自己写了一份取值）。"""
    from typing import get_args

    from querypilot.entrypoints.cli import create_parser
    from querypilot.querying.api import StructureFormat

    parser = create_parser()
    parsed = [parser.parse_args(["eval", "graph", "--structure-format", value]).structure_format
              for value in get_args(StructureFormat)]

    assert parsed == list(get_args(StructureFormat))


def test_cli_retrieval_cases_without_the_database_fail_before_any_run_is_recorded(tmp_path, monkeypatch):
    """输入：--retrieval-cases 给的题集只有 another 库的 Scope，SQL 题集用的是 sample。

    输出：命令失败（非 0），没有建立运行记录。
    """
    from querypilot.entrypoints import cli

    cases, settings = _sample_run(tmp_path, _cases(1))
    another = KnowledgeScope(corpus_id="d" * 64, database_id="another", schema_version="e" * 64)
    retrieval_cases = _retrieval_cases(tmp_path, [another])
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    assert cli.main(["eval", "graph", "--cases", str(cases), "--context-source", "knowledge",
                     "--retrieval-cases", str(retrieval_cases)]) != 0
    assert not (tmp_path / "artifacts").exists()
