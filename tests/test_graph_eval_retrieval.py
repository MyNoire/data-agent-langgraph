"""图评测的 retrieval 来源：检索配置进运行配置和报告，检索失败的题记下类别后继续。"""

import json

import pytest

from querypilot.evaluation.api import export_public, run_graph
from querypilot.retrieval.api import KnowledgeRetrievalError, RetrievalResult
from querypilot.sql_generation.api import GeneratedSql

from test_graph_eval import MATCHING_SQL, _cases, _run_config, _sample_run
from test_graph_eval_knowledge import ITEMS_TABLE, SAMPLE_CHUNKS, SAMPLE_SCOPE, _retrieval_cases

NAME_HIT = SAMPLE_CHUNKS[0]   # items.name 列的 Hit；items 的表 Chunk 没被命中


def _fake_retriever(requests: list, *, fail: dict[str, str] | None = None):
    """假检索器：记下请求；问题在 fail 里时抛出对应类别的检索错误，否则只命中 items.name。

    结果里写上 Embedding / Rerank Profile，模拟 hybrid + 重排的真实返回。
    """
    def retriever(request):
        requests.append(request)
        if fail and request.query in fail:
            raise KnowledgeRetrievalError(fail[request.query])
        return RetrievalResult(
            request_id="r", outcome="matched", scope=request.scope, method=request.method,
            embedding_profile_id="embed-profile" if request.method != "lexical" else None,
            rerank_profile_id="rerank-profile" if request.rerank else None,
            query_sha256="0" * 64, top_k=request.top_k, knowledge_types=request.knowledge_types,
            hit_count=1, hits=[NAME_HIT], elapsed_ms=1.0)
    return retriever


def _table_reader(reads: list):
    """假表 Chunk 读取：只认识 items。"""
    def reader(scope, tables):
        reads.append(tables)
        return {"items": ITEMS_TABLE}
    return reader


def test_retrieval_run_records_retrieval_config_and_the_versions_that_actually_ran(tmp_path):
    """输入：一道 sample 的题，context_source="retrieval"，其他用默认值（hybrid + 重排）。

    输出：
    - 模型收到的 Schema 只有 items（表 Chunk 是按表名补读的），带命中的 name 列。
    - 运行配置和报告记下 method=hybrid、rerank=true、top_k=10，以及结构写法、渲染版本和 Scope。
    - 报告的 retrieval 列出这次实际用到的检索版本：方法、两个 Profile 和检索配置版本。
    - 公开导出带上这些字段；运行目录里的任何文件都不含知识正文。
    """
    schemas, requests, reads = [], [], []

    def generator(question, schema, *_):
        schemas.append(schema)
        return GeneratedSql(MATCHING_SQL)

    cases, settings = _sample_run(tmp_path, _cases(1))

    report = run_graph(cases, settings, generator=generator, context_source="retrieval",
                       knowledge_scopes={"sample": SAMPLE_SCOPE}, relevant_tables={1: ["items"]},
                       retriever=_fake_retriever(requests), table_chunk_reader=_table_reader(reads))

    assert [(r.method, r.rerank, r.top_k) for r in requests] == [("hybrid", True, 10)]
    assert reads == [["items"]]
    assert schemas == ['''表 "items"
  表用途: 记录物品编号和名称。
  记录粒度: 一件物品。
  字段目录: "id", "name"
  主键: 未声明
  外键: 未声明
  列 "name" TEXT: 物品名称''']
    expected = {"context_source": "retrieval", "structure_format": "columns", "schema_render_version": "r5-schema-v2",
                "knowledge_scopes": {"sample": SAMPLE_SCOPE.model_dump()}, "method": "hybrid", "rerank": True,
                "top_k": 10}
    config = _run_config(settings, report["run_id"])
    assert {key: config[key] for key in expected} == expected
    assert {key: report[key] for key in expected} == expected
    assert report["retrieval"] == [{"method": "hybrid", "embedding_profile_id": "embed-profile",
                                    "rerank_profile_id": "rerank-profile",
                                    "retrieval_config_version": "r4-hybrid-v1+r4b-rerank-v1"}]
    run_dir = next(path for path in settings.artifacts_dir.glob("runs/evaluation/*/*") if path.name == report["run_id"])
    public = export_public(run_dir, tmp_path / "public.json")
    assert {key: public[key] for key in (*expected, "retrieval")} == {**expected, "retrieval": report["retrieval"]}
    assert not [path for path in run_dir.rglob("*") if path.is_file() and "物品" in path.read_text(encoding="utf-8")]


def test_retrieval_failure_is_recorded_on_its_case_with_its_category_and_the_run_goes_on(tmp_path):
    """输入：两道题，第一道的检索报 embedding_error，第二道正常。

    输出：第一道记为 error，error_type 是检索错误的类别 embedding_error（不是笼统的异常类名）；第二道照常计分。
    """
    cases, settings = _sample_run(tmp_path, _cases(2))

    report = run_graph(cases, settings, generator=lambda *_: GeneratedSql(MATCHING_SQL), context_source="retrieval",
                       knowledge_scopes={"sample": SAMPLE_SCOPE}, relevant_tables={1: ["items"], 2: ["items"]},
                       retriever=_fake_retriever([], fail={"q1": "embedding_error"}),
                       table_chunk_reader=_table_reader([]))

    first, second = report["records"]
    assert (first["outcome"], first["error_type"]) == ("error", "embedding_error")
    assert second["execution_match"] is True


def test_sources_without_retrieval_record_no_retrieval_config(tmp_path):
    """输入：knowledge 来源的一次运行。输出：没有 method、rerank、top_k、retrieval 这几项。"""
    cases, settings = _sample_run(tmp_path, _cases(1))

    report = run_graph(cases, settings, generator=lambda *_: GeneratedSql(MATCHING_SQL), context_source="knowledge",
                       knowledge_scopes={"sample": SAMPLE_SCOPE}, knowledge_reader=lambda scope: SAMPLE_CHUNKS)

    for fields in (report, _run_config(settings, report["run_id"])):
        assert not {"method", "rerank", "top_k", "retrieval"} & set(fields)


def test_cli_retrieval_flags_reach_the_graph_and_the_run_config_matches_the_runner(tmp_path, monkeypatch, capsys):
    """输入：eval graph --context-source retrieval --method lexical --no-rerank --retrieval-cases <题集>；
    再用同样的参数直接调用 run_graph。

    输出：命令行建检索器时用的是 lexical、不重排，检索请求也是这两项；两次运行写下的运行配置完全相同。
    """
    from querypilot.entrypoints import cli
    from querypilot.evaluation.sql import api as evaluation_api

    requests, built = [], []

    def shared_retriever(method, rerank):
        built.append((method, rerank))
        return _fake_retriever(requests)

    cases, settings = _sample_run(tmp_path, _cases(1))
    monkeypatch.setattr(evaluation_api, "generate_sql", lambda *_: GeneratedSql(MATCHING_SQL))
    monkeypatch.setattr(evaluation_api, "shared_retriever", shared_retriever)
    monkeypatch.setattr(evaluation_api, "load_table_chunks", _table_reader([]))
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    assert cli.main(["eval", "graph", "--cases", str(cases), "--context-source", "retrieval", "--method", "lexical",
                     "--no-rerank", "--retrieval-cases", str(_retrieval_cases(tmp_path, [SAMPLE_SCOPE]))]) == 0
    summary = json.loads(capsys.readouterr().out)
    direct = run_graph(cases, settings, generator=lambda *_: GeneratedSql(MATCHING_SQL), context_source="retrieval",
                       method="lexical", rerank=False, knowledge_scopes={"sample": SAMPLE_SCOPE},
                       relevant_tables={1: []}, retriever=_fake_retriever(requests),
                       table_chunk_reader=_table_reader([]))

    assert built == [("lexical", False)]
    assert [(r.method, r.rerank) for r in requests] == [("lexical", False), ("lexical", False)]
    assert _run_config(settings, summary["run_id"]) == _run_config(settings, direct["run_id"])


def test_cli_retrieval_defaults_match_the_graph():
    """输入：eval graph 不带检索参数。输出：method 为 hybrid、rerank 为开启，与查询图的默认值相同。"""
    from querypilot.entrypoints.cli import create_parser

    args = create_parser().parse_args(["eval", "graph"])

    assert (args.method, args.rerank) == ("hybrid", True)


def test_cli_retrieval_source_without_retrieval_cases_exits_before_any_run_is_recorded(tmp_path, monkeypatch):
    """输入：--context-source retrieval，没给 --retrieval-cases。输出：参数解析阶段就退出，没有运行记录。"""
    from querypilot.entrypoints import cli

    cases, settings = _sample_run(tmp_path, _cases(1))
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    with pytest.raises(SystemExit):
        cli.main(["eval", "graph", "--cases", str(cases), "--context-source", "retrieval"])

    assert not (tmp_path / "artifacts").exists()


def test_each_retrieval_case_records_whether_its_relevant_tables_were_all_retrieved(tmp_path):
    """输入：三道题，检索都只命中 items；
    - q1 的 Relevant Set 是 [items]：拿全；
    - q2 的 Relevant Set 是 [items, orders]：缺 orders，没拿全；
    - q3 的检索报 no_match：没有 Hit，记为没拿全。

    输出：逐题记录的 tables_complete 分别为 True / False / False；汇总按"拿全 / 没拿全"分别给出题数、执行
    匹配数和比例，整体和分库各一份。
    """
    cases, settings = _sample_run(tmp_path, _cases(3))

    report = run_graph(cases, settings, generator=lambda *_: GeneratedSql(MATCHING_SQL), context_source="retrieval",
                       knowledge_scopes={"sample": SAMPLE_SCOPE},
                       relevant_tables={1: ["items"], 2: ["items", "orders"], 3: ["items"]},
                       retriever=_fake_retriever([], fail={"q3": "no_match"}), table_chunk_reader=_table_reader([]))

    assert [record["tables_complete"] for record in report["records"]] == [True, False, False]
    split = {"complete": {"cases": 1, "matched": 1, "rate": 1.0},
             "incomplete": {"cases": 2, "matched": 1, "rate": 0.5}}
    assert report["tables_complete"] == {"overall": split, "by_database": {"sample": split}}


def test_retrieval_run_fails_before_any_run_is_recorded_when_a_case_has_no_relevant_set(tmp_path):
    """输入：SQL 题集有题号 1 和 2，Relevant Set 只给了 1。输出：ValueError，没有运行记录。"""
    cases, settings = _sample_run(tmp_path, _cases(2))

    with pytest.raises(ValueError):
        run_graph(cases, settings, generator=lambda *_: GeneratedSql(MATCHING_SQL), context_source="retrieval",
                  knowledge_scopes={"sample": SAMPLE_SCOPE}, relevant_tables={1: ["items"]},
                  retriever=_fake_retriever([]), table_chunk_reader=_table_reader([]))

    assert not (tmp_path / "artifacts").exists()


def test_tables_complete_is_exported_and_absent_from_other_sources(tmp_path):
    """输入：一次 retrieval 运行和一次 knowledge 运行。

    输出：retrieval 的公开导出带逐题 tables_complete 和汇总；knowledge 的报告和记录里都没有这一项。
    """
    cases, settings = _sample_run(tmp_path, _cases(1))
    retrieval = run_graph(cases, settings, generator=lambda *_: GeneratedSql(MATCHING_SQL), context_source="retrieval",
                          knowledge_scopes={"sample": SAMPLE_SCOPE}, relevant_tables={1: ["items"]},
                          retriever=_fake_retriever([]), table_chunk_reader=_table_reader([]))
    knowledge = run_graph(cases, settings, generator=lambda *_: GeneratedSql(MATCHING_SQL), context_source="knowledge",
                          knowledge_scopes={"sample": SAMPLE_SCOPE}, knowledge_reader=lambda scope: SAMPLE_CHUNKS)

    run_dir = next(path for path in settings.artifacts_dir.glob("runs/evaluation/*/*") if path.name == retrieval["run_id"])
    public = export_public(run_dir, tmp_path / "public.json")
    assert public["records"][0]["tables_complete"] is True
    assert public["tables_complete"] == retrieval["tables_complete"]
    assert "tables_complete" not in knowledge
    assert "tables_complete" not in knowledge["records"][0]


def test_relevant_tables_come_from_the_retrieval_case_set(tmp_path):
    """输入：检索题集两道题（题号 1、2）。输出：题号 → Relevant Set 的表。"""
    from querypilot.evaluation.api import RetrievalCase, RetrievalCaseSet, relevant_tables_from_cases

    path = tmp_path / "retrieval_cases.json"
    path.write_text(RetrievalCaseSet(cases=[
        RetrievalCase(question_id=1, db_id="sample", difficulty="simple", question="r1", frozen=False,
                      scope=SAMPLE_SCOPE, relevant_tables=["items"], relevant_columns=[]),
        RetrievalCase(question_id=2, db_id="sample", difficulty="simple", question="r2", frozen=False,
                      scope=SAMPLE_SCOPE, relevant_tables=["items", "orders"], relevant_columns=[]),
    ]).model_dump_json(), encoding="utf-8")

    assert relevant_tables_from_cases(path) == {1: ["items"], 2: ["items", "orders"]}


def test_runner_itself_refuses_a_case_without_a_relevant_set(tmp_path):
    """输入：绕过 run_graph 的入口检查，直接调用评测运行器，Relevant Set 少了题号 2。

    输出：开始前 ValueError，模型一次都没被调用。不能把缺 Relevant Set 的题当作"拿全"（空集合是任何集合的
    子集），那会抬高"拿全"那一组的匹配率。
    """
    from querypilot.evaluation.sql._graph import run_graph as run_cases
    from querypilot.runtime.api import task_run

    calls = []
    cases, settings = _sample_run(tmp_path, _cases(2))

    with pytest.raises(ValueError):
        with task_run("evaluation", settings, command="graph", private=True) as context:
            run_cases(cases, settings, generator=lambda question, *_: calls.append(question) or GeneratedSql(MATCHING_SQL),
                      context_source="retrieval", knowledge_scopes={"sample": SAMPLE_SCOPE},
                      relevant_tables={1: ["items"]}, retriever=_fake_retriever([]),
                      table_chunk_reader=_table_reader([]), run_context=context)

    assert calls == []


def test_cli_and_graph_agree_on_which_sources_need_retrieval_cases():
    """命令行为了解析快不导入 querying，自己写了一份"读知识库的来源"；图加了新来源、命令行忘了加时这里报错。"""
    from querypilot.entrypoints import cli
    from querypilot.querying.api import KNOWLEDGE_SOURCES

    assert cli.KNOWLEDGE_SOURCES == KNOWLEDGE_SOURCES
