"""检索题集准备：经公开入口 prepare_retrieval_cases 观察生成的题集。

夹具是原创的 demo 库（items、events 两张表），不读取 BIRD 数据。
"""

import json
from hashlib import sha256
from pathlib import Path

import pytest

from querypilot.evaluation.api import RetrievalCaseError, prepare_retrieval_cases
from querypilot.entrypoints.cli import main
from querypilot.knowledge.api import load_corpus


def _write(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _question(question_id: int, sql: str, *, db_id: str = "demo", question: str = "Which labels belong to t1?",
              evidence: str = "label means activation status") -> dict:
    return {"question_id": question_id, "db_id": db_id, "question": question, "evidence": evidence,
            "SQL": sql, "difficulty": "simple"}


def _prepare(tmp_path: Path, corpus: Path, questions: list[dict], frozen: list[dict] | None = None) -> dict:
    questions_file = _write(tmp_path / "questions.json", questions)
    frozen_file = _write(tmp_path / "frozen.json", frozen or [])
    output = tmp_path / "cases" / "retrieval_cases.json"
    prepare_retrieval_cases(questions_file, frozen_file, [corpus], output)
    return json.loads(output.read_text(encoding="utf-8"))


def test_single_table_question_becomes_case_with_relevant_set_and_scope(tmp_path, r3_corpus_factory):
    corpus = r3_corpus_factory("demo")
    sql = "SELECT label FROM items WHERE tenant_id = 't1'"
    case_set = _prepare(tmp_path, corpus, [
        _question(7, sql),
        _question(8, "SELECT 1", db_id="other_db"),
    ])

    manifest = load_corpus(corpus).manifest
    assert case_set["cases"] == [{
        "question_id": 7,
        "db_id": "demo",
        "difficulty": "simple",
        "question": "Which labels belong to t1?",
        "frozen": False,
        "scope": {
            "corpus_id": manifest.corpus_id,
            "database_id": "demo",
            "schema_version": manifest.identity_descriptor.schema_versions["demo"],
        },
        "relevant_tables": ["items"],
        "relevant_columns": [
            {"table_name": "items", "column_name": "label"},
            {"table_name": "items", "column_name": "tenant_id"},
        ],
    }]
    raw = (tmp_path / "cases" / "retrieval_cases.json").read_text(encoding="utf-8")
    assert sql not in raw and "activation status" not in raw


def _cols(*pairs: str) -> list[dict]:
    return [{"table_name": p.split(".")[0], "column_name": p.split(".")[1]} for p in pairs]


@pytest.mark.parametrize(("sql", "tables", "columns"), [
    pytest.param(
        "SELECT T1.label FROM items AS T1 INNER JOIN events AS T2 ON T1.item_id = T2.item_ref "
        "WHERE tenant_ref = 'x'",
        ["events", "items"], _cols("events.item_ref", "events.tenant_ref", "items.item_id", "items.label"),
        id="alias-join-unqualified"),
    pytest.param("SELECT COUNT(*) FROM events", ["events"], [], id="count-star"),
    pytest.param("SELECT * FROM items", ["items"], [], id="select-star"),
    pytest.param("SELECT label FROM items WHERE item_id IN (SELECT item_ref FROM events)",
                 ["events", "items"], _cols("events.item_ref", "items.item_id", "items.label"), id="subquery"),
    pytest.param("SELECT i.label FROM items AS i WHERE EXISTS "
                 "(SELECT 1 FROM events AS e WHERE e.item_ref = i.item_id)",
                 ["events", "items"], _cols("events.item_ref", "items.item_id", "items.label"),
                 id="correlated-subquery"),
    pytest.param("SELECT LABEL FROM Items WHERE Tenant_ID = 't1'", ["items"],
                 _cols("items.label", "items.tenant_id"), id="case-insensitive"),
])
def test_relevant_set_resolves_common_gold_sql_shapes(tmp_path, r3_corpus_factory, sql, tables, columns):
    case_set = _prepare(tmp_path, r3_corpus_factory("demo"), [_question(1, sql)])
    case = case_set["cases"][0]
    assert (case["relevant_tables"], case["relevant_columns"]) == (tables, columns)


def test_questions_in_frozen_file_are_flagged(tmp_path, r3_corpus_factory):
    sql = "SELECT label FROM items"
    case_set = _prepare(tmp_path, r3_corpus_factory("demo"), [_question(7, sql), _question(9, sql)],
                        frozen=[_question(9, sql)])
    assert [(c["question_id"], c["frozen"]) for c in case_set["cases"]] == [(7, False), (9, True)]


def test_unresolvable_gold_sql_fails_whole_set_with_question_ids(tmp_path, r3_corpus_factory):
    questions = [
        _question(3, "SELECT ghost_column FROM items"),
        _question(4, "SELECT COUNT(*) FROM ghosts"),
        _question(5, "SELEC label FROM"),
        _question(6, "SELECT label FROM items"),
    ]
    with pytest.raises(RetrievalCaseError) as raised:
        _prepare(tmp_path, r3_corpus_factory("demo"), questions)
    assert raised.value.question_ids == [3, 4, 5]
    assert not (tmp_path / "cases").exists()


def test_manifest_records_ids_hashes_and_scopes_without_question_text(tmp_path, r3_corpus_factory):
    corpus = r3_corpus_factory("demo")
    sql = "SELECT label FROM items"
    case_set = _prepare(tmp_path, corpus, [_question(7, sql), _question(9, sql)], frozen=[_question(9, sql)])

    manifest_file = tmp_path / "cases" / "retrieval_cases_manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    digest = lambda path: sha256(path.read_bytes()).hexdigest()
    assert manifest == {
        "format_version": "querypilot-retrieval-cases-manifest-v1",
        "case_set_file": "retrieval_cases.json",
        "case_set_sha256": digest(tmp_path / "cases" / "retrieval_cases.json"),
        "questions_sha256": digest(tmp_path / "questions.json"),
        "frozen_sha256": digest(tmp_path / "frozen.json"),
        "scopes": {"demo": case_set["cases"][0]["scope"]},
        "question_ids": {"demo": [7, 9]},
        "frozen_question_ids": [9],
    }
    assert "Which labels" not in manifest_file.read_text(encoding="utf-8")


def _cli_inputs(tmp_path: Path, questions: list[dict]) -> list[str]:
    return ["--questions", str(_write(tmp_path / "questions.json", questions)),
            "--frozen", str(_write(tmp_path / "frozen.json", [questions[-1]]))]


def test_cli_writes_case_set_and_prints_summary(tmp_path, r3_corpus_factory, capsys):
    corpus = r3_corpus_factory("demo")
    sql = "SELECT label FROM items"
    output = tmp_path / "cases" / "retrieval_cases.json"
    code = main(["eval", "retrieval-cases", *_cli_inputs(tmp_path, [_question(7, sql), _question(9, sql)]),
                 "--corpus-dir", str(corpus), "--output", str(output)])

    assert code == 0
    assert json.loads(capsys.readouterr().out) == {
        "cases": {"demo": 2},
        "frozen_cases": 1,
        "output_file": str(output),
        "manifest_file": str(output.with_name("retrieval_cases_manifest.json")),
    }
    assert output.exists()


def test_cli_reports_failed_question_ids(tmp_path, r3_corpus_factory, capsys):
    corpus = r3_corpus_factory("demo")
    questions = [_question(3, "SELECT ghost_column FROM items"), _question(6, "SELECT label FROM items")]
    code = main(["eval", "retrieval-cases", *_cli_inputs(tmp_path, questions),
                 "--corpus-dir", str(corpus), "--output", str(tmp_path / "cases" / "c.json")])

    assert code == 1
    captured = capsys.readouterr()
    assert json.loads(captured.err) == {"outcome": "failed", "error_type": "retrieval_case_error",
                                        "question_ids": [3]}
    assert "ghost_column" not in captured.out + captured.err


def test_relevant_set_uses_corpus_spelling_of_mixed_case_names(tmp_path, r3_corpus_factory):
    corpus = r3_corpus_factory("demo", mixed_case=True)
    case_set = _prepare(tmp_path, corpus, [
        _question(1, "SELECT COUNT(*) FROM events WHERE item_ref = 'a'"),
        _question(2, "SELECT COUNT(*) FROM EVENTS AS e WHERE e.ITEM_REF = 'a'"),
    ])
    for case in case_set["cases"]:
        assert (case["relevant_tables"], case["relevant_columns"]) == (["Events"], _cols("Events.Item_Ref"))
