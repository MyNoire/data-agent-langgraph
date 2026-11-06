"""逐项检查18个旧工具对应的新参数与帮助；不下载、冻结或调用模型。"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from querypilot.entrypoints.cli import create_parser, main

MAPPINGS = [
    ("ask_graph.py", "query ask --database-id demo --question count", "query.ask"),
    ("run_eval.py", "eval direct --provider fixture --evidence-mode none", "eval.direct"),
    ("run_graph_eval.py", "eval graph", "eval.graph"),
    ("export_eval_public.py", "eval export-public --input old.json --output new.json", "eval.export-public"),
    ("export_catalog.py", "knowledge catalog extract --database-id demo --output catalog.json", "knowledge.catalog.extract"),
    ("enrich_catalog.py", "knowledge catalog enrich --catalog c --description-dir d --output-dir o", "knowledge.catalog.enrich"),
    ("curate_catalog.py", "knowledge catalog curate --base b --source-dir s --plan p --output-dir o", "knowledge.catalog.curate"),
    ("render_knowledge.py", "knowledge corpus render --curated-dir b --output-dir o", "knowledge.corpus.render"),
    ("build_corpus.py", "knowledge corpus build --curated-dir b --semantic-file s --output-dir o --database-id demo", "knowledge.corpus.build"),
    ("verify_corpus.py", "knowledge corpus verify --corpus-dir c", "knowledge.corpus.verify"),
    ("import_corpus.py", "knowledge store import --corpus-dir c", "knowledge.store.import"),
    ("verify_corpus_import.py", "knowledge store verify --corpus-dir c", "knowledge.store.verify"),
    ("manage_knowledge_db.py", "knowledge store check", "knowledge.store.check"),
    ("fetch_bird.py", "data bird fetch --archive a.zip --sha256 00 --dest d", "data.bird.fetch"),
    ("import_bird.py", "data bird import --root r --manifest m --limit 2 --questions-only --output o", "data.bird.import"),
    ("select_bird_final100.py", "data bird select-final100", "data.bird.select-final100"),
    ("freeze_bird_final100.py", "data bird freeze-final100 --database-root d --output o", "data.bird.freeze-final100"),
    ("prepare_bird_final100_data.py", "data bird prepare-final100 --database-source d --manifest m", "data.bird.prepare-final100"),
]


@pytest.mark.parametrize("old,arguments,command", MAPPINGS)
def test_each_old_tool_has_an_explicit_parser(old, arguments, command):
    """只解析每项旧能力的参数，确认不丢独有选项且具有明确动作。"""
    args = create_parser().parse_args(arguments.split())
    assert args.command == command
    assert old.endswith(".py")


def test_mappings_keep_key_defaults():
    """关键默认值：语料目标与运行根分别保留。"""
    parser = create_parser()
    direct = parser.parse_args(["eval", "direct"])
    assert (direct.provider, direct.evidence_mode, direct.cases) == ("model", "none", Path("evals/bird_smoke.json"))
    imports = parser.parse_args(["data", "bird", "import"])
    assert imports.limit == 20 and imports.output == Path("evals/bird_smoke.json")
    before = parser.parse_args(["--artifacts-dir", "runs", *MAPPINGS[8][1].split()])
    after = parser.parse_args([*MAPPINGS[8][1].split(), "--artifacts-dir", "runs"])
    assert before.artifacts_dir == after.artifacts_dir == Path("runs")
    assert before.output_dir == after.output_dir == Path("o")


@pytest.mark.parametrize("arguments", ["eval direct --output old.json", "eval graph --output old.json", "knowledge store import --corpus-dir c --output old.json"])
def test_flat_reports_are_not_silently_accepted(arguments, capsys):
    """原平铺结果选项明确拒绝，不能暗中写入旧results路径。"""
    with pytest.raises(SystemExit) as exc:
        create_parser().parse_args(arguments.split())
    assert exc.value.code == 2
    assert "old.json" not in capsys.readouterr().err


def test_parse_error_never_echoes_question_or_secret(capsys):
    """参数错误不回显任意用户值，即使放在错误选项后。"""
    with pytest.raises(SystemExit) as exc:
        main(["query", "ask", "--database-id", "d", "--question", "PRIVATE QUESTION", "--bad", "API_SECRET"])
    assert exc.value.code == 2
    raw = capsys.readouterr().err
    assert "PRIVATE QUESTION" not in raw and "API_SECRET" not in raw


def test_console_and_module_share_help():
    """安装的console脚本与module入口输出相同命令帮助，均不执行业务。"""
    import shutil
    executable = shutil.which("querypilot")
    assert executable is not None
    console = subprocess.run([executable, "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    module = subprocess.run([sys.executable, "-m", "querypilot", "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert console.returncode == module.returncode == 0
    assert console.stdout == module.stdout


def _sql_eval_input(tmp_path):
    """输入隔离目录 tmp_path，返回两题原创输入路径和配置；只写临时SQLite及JSON。"""
    from querypilot.databases.api import DatabaseRegistry
    from querypilot.runtime.api import Settings

    data = tmp_path / "data"
    data.mkdir()
    database = data / "tiny.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE items(id INTEGER)")
        connection.execute("INSERT INTO items VALUES (1)")
    DatabaseRegistry(data).register("tiny", database)
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([
        {"question_id": number, "db_id": "tiny", "question": "PRIVATE EVAL QUESTION",
         "SQL": "SELECT id FROM items"}
        for number in (1, 2)
    ]), encoding="utf-8")
    return cases, Settings(_env_file=None, data_dir=data, artifacts_dir=tmp_path / "artifacts")


@pytest.mark.parametrize("action,status", [("direct", "model_unavailable"), ("graph", "no_predictions")])
def test_sql_cli_unavailable_batch_is_completed_once_and_classified(tmp_path, monkeypatch, capsys, action, status):
    """检查无预测批次的完成、类别与运行所有权。

    参数：tmp_path、monkeypatch、capsys 为隔离测试夹具；action/status 为两种SQL模式及原状态。
    返回：None，通过断言报告结果；副作用仅限临时SQLite、运行目录和替身，不调用模型。
    """
    from querypilot.entrypoints import cli
    from querypilot.evaluation.sql import api as sql_api
    from querypilot.evaluation.api import read_report

    cases, settings = _sql_eval_input(tmp_path)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    def unavailable(*_):
        """忽略生成参数，始终抛出ConnectionError以模拟离线；不连接外部服务。"""
        raise ConnectionError("synthetic offline")

    monkeypatch.setattr(sql_api, "generate_sql", unavailable)
    assert main(["eval", action, "--cases", str(cases)]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    runs = list(settings.artifacts_dir.glob("runs/evaluation/*/*"))
    assert len(runs) == 1
    run = runs[0]
    record = json.loads((run / "run.json").read_text(encoding="utf-8"))
    config = json.loads((run / "config.json").read_text(encoding="utf-8"))
    assert record["status"] == "completed" and record["operation"] == "evaluation"
    assert config["parameters"]["mode"] == "sql"
    assert summary["status"] == status and "records" not in summary
    assert Path(summary["output_file"]) == run / "private/result.json"
    report = read_report(run)
    assert report["count"] == 2 and report["status"] == status
    assert report["execution_accuracy"] is None and report["valid_sql_rate"] is None
    safe = captured.err + (run / "events.jsonl").read_text(encoding="utf-8")
    assert "PRIVATE EVAL QUESTION" not in safe and "SELECT id FROM items" not in safe


def test_sql_export_cli_also_records_sql_category(tmp_path, monkeypatch, capsys):
    """检查旧报告导出及最外层SQL类别。

    参数：三个pytest夹具提供临时目录、配置替换和输出捕获；返回：None。
    副作用：只写临时旧报告、公开导出和运行记录，不执行SQL或模型。
    """
    from querypilot.entrypoints import cli
    from querypilot.runtime.api import Settings

    settings = Settings(_env_file=None, artifacts_dir=tmp_path / "artifacts")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    source, output = tmp_path / "legacy.json", tmp_path / "public.json"
    source.write_text(json.dumps({"records": [{"predicted_sql": "SELECT 1"}]}), encoding="utf-8")
    assert main(["eval", "export-public", "--input", str(source), "--output", str(output)]) == 0
    assert capsys.readouterr().out.strip() == str(output)
    runs = list(settings.artifacts_dir.glob("runs/evaluation/*/*"))
    assert len(runs) == 1
    config = json.loads((runs[0] / "config.json").read_text(encoding="utf-8"))
    assert config["parameters"]["mode"] == "sql"
    assert json.loads(output.read_text(encoding="utf-8"))["records"] == [{"predicted_sql": "SELECT 1"}]


def test_sql_cli_artifact_failure_keeps_exit_four(tmp_path, monkeypatch, capsys):
    """检查运行根无法创建时仍以产物失败退出4。

    参数：三个pytest夹具提供隔离输入、配置替换和输出捕获；返回：None。
    副作用：临时路径被普通文件占用，拒绝创建运行目录，不调用模型或输出成功报告。
    """
    from querypilot.entrypoints import cli

    cases, settings = _sql_eval_input(tmp_path)
    blocked = tmp_path / "blocked"
    blocked.write_text("occupied", encoding="utf-8")
    monkeypatch.setattr(cli, "get_settings", lambda: settings.model_copy(update={"artifacts_dir": blocked}))
    assert main(["eval", "direct", "--provider", "fixture", "--cases", str(cases)]) == 4
    captured = capsys.readouterr()
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["artifact_outcome"] == "failed"
    assert blocked.read_text(encoding="utf-8") == "occupied"
