"""用原创的小题目文件验证按库生成 SQL 题集，不读取正式 BIRD 资料。"""

from hashlib import sha256
import json

import pytest

from querypilot.evaluation.api import SqlCaseSetError, select_database_cases


def _fixture(tmp_path):
    """写出四道虚构题目（三个库）和一份冻结清单，返回题目文件和清单的路径。"""
    cases = [
        {"db_id": "alpha", "question_id": 1, "difficulty": "simple", "question": "原创问题一",
         "SQL": "SELECT 1", "evidence": ""},
        {"db_id": "beta", "question_id": 2, "difficulty": "moderate", "question": "原创问题二",
         "SQL": "SELECT 2", "evidence": ""},
        {"db_id": "gamma", "question_id": 3, "difficulty": "simple", "question": "原创问题三",
         "SQL": "SELECT 3", "evidence": ""},
        {"db_id": "alpha", "question_id": 4, "difficulty": "challenging", "question": "原创问题四",
         "SQL": "SELECT 4", "evidence": ""},
    ]
    source = tmp_path / "questions.json"
    source.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    frozen = tmp_path / "frozen_manifest.json"
    frozen.write_text(json.dumps({
        "dataset": "original-test", "revision": "test-v1", "source_url": "local-fixture",
        "source_sha256": sha256(source.read_bytes()).hexdigest(), "question_ids": [2, 3],
    }), encoding="utf-8")
    return source, frozen


def test_case_set_keeps_every_question_of_the_chosen_databases_in_source_order(tmp_path):
    """题集只含指定库的题，一道不少，顺序与来源文件相同。"""
    source, frozen = _fixture(tmp_path)
    output = tmp_path / "cases.json"

    summary = select_database_cases(source, ["alpha", "beta"], frozen, output)

    cases = json.loads(output.read_text(encoding="utf-8"))
    assert [case["question_id"] for case in cases] == [1, 2, 4]
    assert [case["db_id"] for case in cases] == ["alpha", "beta", "alpha"]
    assert cases[0]["question"] == "原创问题一" and cases[0]["SQL"] == "SELECT 1"
    assert summary["count"] == 3
    assert summary["databases"] == {"alpha": 2, "beta": 1}


def test_cases_listed_in_the_frozen_manifest_are_flagged(tmp_path):
    """题号在冻结清单里的题标为 frozen，其余为 False。"""
    source, frozen = _fixture(tmp_path)
    output = tmp_path / "cases.json"

    summary = select_database_cases(source, ["alpha", "beta"], frozen, output)

    cases = json.loads(output.read_text(encoding="utf-8"))
    assert {case["question_id"]: case["frozen"] for case in cases} == {1: False, 2: True, 4: False}
    assert summary["frozen"] == 1


def test_manifest_records_question_ids_and_hashes_without_question_text(tmp_path):
    """manifest 写在题集旁边：题号、冻结题号、来源和题集文件的 sha256，不含原题和 SQL。"""
    source, frozen = _fixture(tmp_path)
    output = tmp_path / "cases.json"

    summary = select_database_cases(source, ["alpha", "beta"], frozen, output)

    manifest_file = tmp_path / "cases_manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert summary["output"] == str(output) and summary["manifest"] == str(manifest_file)
    assert manifest["dataset"] == "original-test" and manifest["revision"] == "test-v1"
    assert manifest["source_url"] == "local-fixture"
    assert manifest["source_sha256"] == sha256(source.read_bytes()).hexdigest()
    assert manifest["case_set_file"] == "cases.json"
    assert manifest["case_set_sha256"] == sha256(output.read_bytes()).hexdigest()
    assert manifest["question_ids"] == {"alpha": [1, 4], "beta": [2]}
    assert manifest["frozen_question_ids"] == [2]
    raw = manifest_file.read_text(encoding="utf-8")
    assert "原创问题" not in raw and "SELECT" not in raw


def test_source_that_differs_from_the_frozen_manifest_is_refused(tmp_path):
    """来源文件的 hash 和冻结清单记录的不一致时拒绝生成，不写任何文件。"""
    source, frozen = _fixture(tmp_path)
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    output = tmp_path / "cases.json"

    with pytest.raises(SqlCaseSetError) as error:
        select_database_cases(source, ["alpha", "beta"], frozen, output)

    assert error.value.error_type == "source_version_mismatch"
    assert not output.exists() and not (tmp_path / "cases_manifest.json").exists()


def test_database_without_any_question_is_refused(tmp_path):
    """指定的库在来源文件里一道题都没有时拒绝生成：库名写错不会得到一份悄悄变小的题集。"""
    source, frozen = _fixture(tmp_path)
    output = tmp_path / "cases.json"

    with pytest.raises(SqlCaseSetError) as error:
        select_database_cases(source, ["alpha", "betta"], frozen, output)

    assert error.value.error_type == "data_input_invalid"
    assert not output.exists()


def test_command_writes_the_case_set_and_prints_counts(tmp_path, capsys):
    """命令行入口生成题集和 manifest，打印题数、各库题数和冻结题数，不打印题目。"""
    from querypilot.entrypoints.cli import main

    source, frozen = _fixture(tmp_path)
    output = tmp_path / "cases.json"

    code = main(["--artifacts-dir", str(tmp_path / "artifacts"), "data", "bird", "select-databases",
                 "--source", str(source), "--frozen-manifest", str(frozen),
                 "--database-id", "alpha", "--database-id", "beta", "--output", str(output)])

    printed = capsys.readouterr().out
    assert code == 0
    assert len(json.loads(output.read_text(encoding="utf-8"))) == 3
    assert (tmp_path / "cases_manifest.json").is_file()
    assert "3" in printed and "alpha" in printed and "beta" in printed
    assert "原创问题" not in printed


def test_case_set_and_manifest_are_written_with_unix_line_endings(tmp_path):
    """题集和 manifest 都只用换行符 LF：manifest 记了题集文件的 sha256，同一份来源在任何系统上重新生成都要得到同一个值。"""
    source, frozen = _fixture(tmp_path)
    output = tmp_path / "cases.json"

    select_database_cases(source, ["alpha", "beta"], frozen, output)

    assert b"\r" not in output.read_bytes()
    assert b"\r" not in (tmp_path / "cases_manifest.json").read_bytes()
