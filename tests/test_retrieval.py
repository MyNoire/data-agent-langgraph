"""检索协议必要边界；不把模型或 mock 测试当作 PG 召回证据。"""

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from querypilot.knowledge.api import KnowledgeScope
from querypilot.retrieval.api import (
    KnowledgeRetrievalError, RetrievalRequest, ScopeFile,
)


def scope_values():
    """输入：无；返回：原创三元范围；副作用：无，不访问正式知识。"""
    return {"corpus_id": "a" * 64, "database_id": "demo", "schema_version": "b" * 64}


def test_request_preserves_query_and_uses_strict_bounds():
    """输入：合法及临界请求；返回：断言通过；副作用：无。"""
    scope = KnowledgeScope(**scope_values())
    request = RetrievalRequest(query="  MAIN.ATOM.ELEMENT  ", scope=scope)
    assert request.query == "  MAIN.ATOM.ELEMENT  "
    assert request.top_k == 10
    assert request.knowledge_types == ["table", "column", "relationship"]
    assert len(RetrievalRequest(query="中" * 2000, scope=scope).query) == 2000
    # 模型仅校验原始文本；有效词项留给 Python 规范化与固定中文分词处理。
    assert RetrievalRequest(query="---", scope=scope).query == "---"


@pytest.mark.parametrize("change", [
    {"query": " "}, {"query": "x" * 2001}, {"query": "\x00"},
    {"query": "\ud800"}, {"query": 123}, {"top_k": True}, {"top_k": "5"},
    {"top_k": 0}, {"top_k": 51}, {"knowledge_types": []},
    {"knowledge_types": ["column", "column"]}, {"knowledge_types": ["unknown"]},
    {"scope": None}, {"extra": "reject"},
])
def test_request_rejects_invalid_input_without_echo(change):
    """输入：违规请求变化；返回：拒绝断言；异常：预期 ValidationError；无 I/O。"""
    with pytest.raises(ValidationError) as caught:
        RetrievalRequest.model_validate({"query": "private question", "scope": scope_values(), **change})
    assert "private question" not in str(caught.value)


def test_scope_and_scope_file_are_explicit_and_strict():
    """输入：范围/文件对象；返回：严格字段断言；异常：错误范围被拒绝；无 I/O。"""
    assert ScopeFile(scope=KnowledgeScope(**scope_values())).scope.database_id == "demo"
    for change in ({"corpus_id": "latest"}, {"schema_version": "B" * 64},
                   {"database_id": ""}, {"database_id": "\x00"}, {"database_id": " "}):
        with pytest.raises(ValidationError):
            KnowledgeScope(**{**scope_values(), **change})
    with pytest.raises(ValidationError):
        ScopeFile.model_validate({"scope": scope_values(), "dsn": "private"})


def test_import_does_not_require_pg_driver_or_model_client():
    """输入：禁止 PG/模型客户端的独立进程；返回：导入成功断言；只启动本地 Python。"""
    code = '''
import importlib.abc, sys
blocked = {"psycopg", "openai", "langchain_openai"}
class BlockOptionalClients(importlib.abc.MetaPathFinder):
    """测试导入钩子；禁止加载可选数据库和模型客户端。"""
    def find_spec(self, fullname, path=None, target=None):
        """输入：模块定位参数；返回：None；异常：禁止的模块抛出 ModuleNotFoundError。"""
        if fullname.split(".")[0] in blocked:
            raise ModuleNotFoundError("Optional client deliberately unavailable", name=fullname)
sys.meta_path.insert(0, BlockOptionalClients())
import querypilot.retrieval.api
import querypilot.entrypoints.commands.retrieval

assert not blocked.intersection(sys.modules)
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_original_fixture_builds_legal_databases_and_versions(r3_corpus_factory):
    """输入：原创工厂；返回：三份合法范围/别名/有序 FK 断言；只读临时构建产物。"""
    from querypilot.knowledge.knowledge_corpus.api import load_corpus as load_verified_corpus

    original = load_verified_corpus(r3_corpus_factory("first"))
    version = load_verified_corpus(r3_corpus_factory("version", profile_version="r3-fixture-v2"))
    database = load_verified_corpus(r3_corpus_factory("database", database_id="demo_other"))
    assert len({s.manifest.corpus_id for s in (original, version, database)}) == 3
    assert original.manifest.counts.model_dump() == {
        "documents": 2, "chunks": 8, "relations": 1,
        "chunk_types": {"table": 2, "column": 5, "relationship": 1},
    }
    assert {c.database_id for c in database.chunks} == {"demo_other"}
    assert any(c.metadata.get("display_name") == "activation label" for c in original.chunks)
    assert original.relations[0].source_columns == ["tenant_ref", "item_ref"]
    assert original.relations[0].target_columns == ["tenant_id", "item_id"]


def test_scope_modes_are_equivalent_but_not_mixed(tmp_path):
    """输入：同一范围的参数与文件；返回：等价/互斥断言；只写临时配置。"""
    from querypilot.entrypoints.commands.retrieval import resolve_scope

    path = tmp_path / "scope.json"
    path.write_text(ScopeFile(scope=KnowledgeScope(**scope_values())).model_dump_json(), encoding="utf-8")
    assert resolve_scope(**scope_values()) == resolve_scope(scope_file=path)
    for values in ({}, {"corpus_id": "a" * 64}, {**scope_values(), "scope_file": path}):
        with pytest.raises(KnowledgeRetrievalError) as caught:
            resolve_scope(**values)
        assert caught.value.error_type == "input_error"


@pytest.mark.parametrize("raw", [
    b'{"scope":{},"scope":{}}', b'[]', b'{"scope":{},"extra":"private"}',
    b'{"scope":', b'\xff', b' ' * (16 * 1024 + 1),
])
def test_invalid_scope_file_is_safe_input_error(tmp_path, raw):
    """输入：重复键/非法/超长配置；返回：安全拒绝断言；只写测试文件。"""
    from querypilot.entrypoints.commands.retrieval import resolve_scope

    path = tmp_path / "scope.json"
    path.write_bytes(raw)
    with pytest.raises(KnowledgeRetrievalError) as caught:
        resolve_scope(scope_file=path)
    assert caught.value.error_type == "input_error"
    assert "private" not in str(caught.value)


def test_valid_scope_file_respects_exact_byte_budget_and_rejects_duplicate_keys(tmp_path):
    """输入：合法边界文件及重复字段；返回：容量与键唯一性断言；只写临时配置。"""
    from querypilot.entrypoints.commands.retrieval import resolve_scope

    path = tmp_path / "scope.json"
    scope = KnowledgeScope(**scope_values())
    raw = ScopeFile(scope=scope).model_dump_json().encode("utf-8")
    at_limit = raw + b" " * (16 * 1024 - len(raw))
    path.write_bytes(at_limit)
    assert resolve_scope(scope_file=path) == scope
    scope_json = json.dumps(scope_values())
    duplicate_scope = f'{{"scope":{scope_json},"scope":{scope_json}}}'.encode("utf-8")
    duplicate_field = raw.replace(b'"database_id":"demo"', b'"database_id":"demo","database_id":"demo"')
    for invalid in (at_limit + b" ", duplicate_scope, duplicate_field):
        path.write_bytes(invalid)
        with pytest.raises(KnowledgeRetrievalError) as caught:
            resolve_scope(scope_file=path)
        assert caught.value.error_type == "input_error"


def test_parser_never_echoes_sensitive_argument(capsys):
    """输入：含秘密样式原值的语法错误；返回：安全提示断言；异常：SystemExit(2)。"""
    from querypilot.entrypoints.commands.retrieval import build_retrieval_parser

    with pytest.raises(SystemExit) as caught:
        build_retrieval_parser().parse_args(["--query", "private question", "--unknown", "secret-value"])
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert "private question" not in output.out + output.err
    assert "secret-value" not in output.out + output.err
    assert output.out == ""
    assert output.err.startswith("usage: retrieve_knowledge ")
    assert output.err.endswith("参数语法错误；请使用 --help 查看支持的参数。\n")


def test_relocated_parser_preserves_arguments_and_defaults(tmp_path):
    """输入：原有开发参数；返回：参数值、路径和默认值断言；副作用：无。"""
    from querypilot.entrypoints.commands.retrieval import build_retrieval_parser

    parser = build_retrieval_parser()
    arguments = parser.parse_args([
        "--query", "  原子 cl  ", "--scope-file", str(tmp_path / "scope.json"),
        "--top-k", "5", "--knowledge-type", "column", "--knowledge-type", "table",
        "--output", str(tmp_path / "result.json"),
    ])
    assert arguments.query == "  原子 cl  "
    assert arguments.scope_file == tmp_path / "scope.json"
    assert arguments.top_k == 5
    assert arguments.knowledge_type == ["column", "table"]
    assert arguments.output == tmp_path / "result.json"
    defaults = parser.parse_args(["--query", "atom"])
    assert defaults.top_k == 10
    assert defaults.knowledge_type is None
    assert defaults.scope_file is None and defaults.output is None


def test_shared_parser_preserves_unified_cli_safe_syntax_error(capsys):
    """输入：统一入口含敏感原值的语法错误；返回：原固定输出断言；异常：SystemExit(2)。"""
    from querypilot.entrypoints._arguments import SafeArgumentParser
    from querypilot.entrypoints.cli import create_parser
    from querypilot.entrypoints.commands.retrieval import build_retrieval_parser

    parser = create_parser()
    assert isinstance(parser, SafeArgumentParser)
    assert isinstance(build_retrieval_parser(), SafeArgumentParser)
    with pytest.raises(SystemExit) as caught:
        parser.parse_args([
            "query", "ask", "--database-id", "demo", "--question", "private question",
            "--unknown", "secret-value",
        ])
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "querypilot: 参数不合法，请查看对应命令 --help\n"
