"""统一命令入口；解析阶段不导入知识驱动或执行数据操作。"""

import argparse
import json
import sys
from pathlib import Path

from querypilot.entrypoints._arguments import SafeArgumentParser
from querypilot.runtime.api import RunArtifactError, get_settings


def _artifacts(parser):
    """让运行根选项可出现在命令首尾；子解析器不覆盖已解析值。"""
    parser.add_argument("--artifacts-dir", type=Path, default=argparse.SUPPRESS,
                        help="运行记录根目录，默认读取环境变量或 artifacts")
    return parser


def create_parser() -> argparse.ArgumentParser:
    """构造18个明确命令的参数树，返回解析器；不读取配置、不产生业务副作用。"""
    parser = _artifacts(SafeArgumentParser(prog="querypilot", description="QueryPilot 查询、知识准备与评测"))
    groups = parser.add_subparsers(dest="group", required=True)
    query = _artifacts(groups.add_parser("query", help="自然语言单图查询"))
    ask = _artifacts(query.add_subparsers(dest="action", required=True).add_parser("ask"))
    ask.add_argument("--database-id", required=True)
    ask.add_argument("--question", required=True)
    ask.set_defaults(command="query.ask")

    evaluation = _artifacts(groups.add_parser("eval", help="评测与显式报告导出"))
    eval_actions = evaluation.add_subparsers(dest="action", required=True)
    for name in ("direct", "graph"):
        command = _artifacts(eval_actions.add_parser(name))
        command.add_argument("--cases", type=Path, default=Path("evals/bird_smoke.json"))
        if name == "direct":
            command.add_argument("--provider", choices=("model", "fixture"), default="model")
            command.add_argument("--evidence-mode", choices=("none", "oracle"), default="none")
        command.set_defaults(command=f"eval.{name}")
    cases = _artifacts(eval_actions.add_parser("retrieval-cases", help="从 BIRD 题目和 Corpus 生成检索题集"))
    cases.add_argument("--questions", type=Path, required=True)
    cases.add_argument("--frozen", type=Path, default=Path("evals/bird_final100.json"))
    cases.add_argument("--corpus-dir", type=Path, action="append", required=True)
    cases.add_argument("--output", type=Path, required=True)
    cases.set_defaults(command="eval.retrieval-cases")
    export = _artifacts(eval_actions.add_parser("export-public"))
    export.add_argument("--input", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.set_defaults(command="eval.export-public")

    knowledge = _artifacts(groups.add_parser("knowledge", help="开发者知识准备与存储工具"))
    knowledge_groups = knowledge.add_subparsers(dest="knowledge_group", required=True)
    catalog = _artifacts(knowledge_groups.add_parser("catalog"))
    catalog_actions = catalog.add_subparsers(dest="action", required=True)
    extract = _artifacts(catalog_actions.add_parser("extract"))
    extract.add_argument("--database-id", required=True)
    extract.add_argument("--output", type=Path, required=True)
    extract.set_defaults(command="knowledge.catalog.extract")
    enrich = _artifacts(catalog_actions.add_parser("enrich"))
    for flag in ("catalog", "description-dir", "output-dir"):
        enrich.add_argument(f"--{flag}", type=Path, required=True)
    enrich.set_defaults(command="knowledge.catalog.enrich")
    curate = _artifacts(catalog_actions.add_parser("curate"))
    for flag in ("base", "source-dir", "plan", "output-dir"):
        curate.add_argument(f"--{flag}", type=Path, required=True)
    curate.set_defaults(command="knowledge.catalog.curate")
    corpus = _artifacts(knowledge_groups.add_parser("corpus"))
    corpus_actions = corpus.add_subparsers(dest="action", required=True)
    for name in ("render", "build"):
        command = _artifacts(corpus_actions.add_parser(name))
        command.add_argument("--curated-dir", dest="curated_dir", type=Path, required=True)
        command.add_argument("--semantic-file", "--semantics", dest="semantic_file", type=Path, required=name == "build")
        command.add_argument("--output-dir", type=Path, required=True)
        if name == "build":
            command.add_argument("--database-id", required=True)
            command.add_argument("--profile-version", default="r1-4-v1")
        command.set_defaults(command=f"knowledge.corpus.{name}")
    verify = _artifacts(corpus_actions.add_parser("verify"))
    verify.add_argument("--corpus-dir", type=Path, required=True)
    verify.set_defaults(command="knowledge.corpus.verify")
    store = _artifacts(knowledge_groups.add_parser("store"))
    store_actions = store.add_subparsers(dest="action", required=True)
    for name in ("import", "verify", "bootstrap", "migrate", "check", "prepare-search", "check-search"):
        command = _artifacts(store_actions.add_parser(name))
        if name in ("import", "verify"):
            command.add_argument("--corpus-dir", type=Path, required=True)
        command.set_defaults(command=f"knowledge.store.{name}")

    data = _artifacts(groups.add_parser("data", help="BIRD数据准备；不自动下载或重冻结"))
    bird = _artifacts(data.add_subparsers(dest="data_group", required=True).add_parser("bird"))
    bird_actions = bird.add_subparsers(dest="action", required=True)
    fetch = _artifacts(bird_actions.add_parser("fetch"))
    source = fetch.add_mutually_exclusive_group(required=True)
    source.add_argument("--url")
    source.add_argument("--archive", type=Path)
    fetch.add_argument("--sha256", required=True)
    fetch.add_argument("--dest", type=Path, default=Path("data/bird"))
    fetch.set_defaults(command="data.bird.fetch")
    imports = _artifacts(bird_actions.add_parser("import"))
    imports.add_argument("--root", type=Path, default=Path("data/bird"))
    imports.add_argument("--limit", type=int, default=20)
    imports.add_argument("--manifest", type=Path)
    imports.add_argument("--questions-only", action="store_true")
    imports.add_argument("--output", type=Path, default=Path("evals/bird_smoke.json"))
    imports.set_defaults(command="data.bird.import")
    for name in ("select-final100", "freeze-final100"):
        command = _artifacts(bird_actions.add_parser(name))
        command.add_argument("--source", type=Path, default=Path("data/bird/mini_dev_sqlite-00000-of-00001.json"))
        command.add_argument("--smoke-manifest", type=Path, default=Path("evals/bird_smoke_manifest.json"))
        if name == "freeze-final100":
            command.add_argument("--database-root", type=Path, required=True)
            command.add_argument("--output", type=Path, default=Path("evals/bird_final100_manifest.json"))
        command.set_defaults(command=f"data.bird.{name}")
    prepare = _artifacts(bird_actions.add_parser("prepare-final100"))
    prepare.add_argument("--database-source", type=Path, required=True)
    prepare.add_argument("--manifest", type=Path, default=Path("evals/bird_final100_manifest.json"))
    prepare.set_defaults(command="data.bird.prepare-final100")
    return parser


def main(argv: list[str] | None = None) -> int:
    """解析明确命令并调用薄适配，返回0/1/2/3/4；受控错误不打印原异常或argv。

    参数：argv 可供测试传入，None 使用正常进程参数。帮助不会读取知识配置。
    副作用：仅选中命令才导入对应能力、执行已请求动作和建立运行记录。
    """
    args = create_parser().parse_args(argv)
    try:
        settings = get_settings()
        if hasattr(args, "artifacts_dir"):
            settings = settings.model_copy(update={"artifacts_dir": args.artifacts_dir})
        if args.group == "query":
            from .commands.query import run
        elif args.group == "eval":
            from .commands.evaluation import run
        elif args.group == "knowledge":
            from .commands.knowledge import run
        else:
            from .commands.data import run
        return run(args, settings)
    except RunArtifactError as exc:
        payload = {"error_type": exc.error_type, "run_id": exc.run_id,
                   "operation_outcome": exc.operation_outcome, "artifact_outcome": "failed"}
        if args.command.startswith("knowledge.store."):
            _failure_output({"result_version": "knowledge-operation-error-v1", "outcome": "failed",
                             "import_run_id": exc.run_id, "database_outcome": exc.operation_outcome,
                             **payload})
        _error(payload)
        return 4
    except Exception as exc:
        category = getattr(exc, "error_type", None) or getattr(exc, "error_code", None) or type(exc).__name__
        payload = {"outcome": "failed", "error_type": category}
        if getattr(exc, "error_code", None):
            payload["error_code"] = exc.error_code
        if getattr(exc, "database_outcome", None):
            payload["database_outcome"] = exc.database_outcome
        # 领域异常由项目代码产生受控类别，不把 str(exc) 带入终端。
        if args.command.startswith("knowledge.store."):
            payload["result_version"] = "knowledge-operation-error-v1"
            payload["import_run_id"] = getattr(args, "run_id", None)
            payload["message"] = "知识库操作失败，请按错误类别检查输入或服务"
            payload.setdefault("database_outcome", None)
            if args.action in {"bootstrap", "migrate", "check", "prepare-search", "check-search"}:
                payload["command"] = args.action
            _failure_output(payload)
        _error(payload)
        return getattr(exc, "exit_code", 1)


def _error(payload: dict) -> None:
    """尽力写受控错误JSON；stderr故障不覆盖已经确定的业务退出码。"""
    try:
        print(json.dumps(payload, ensure_ascii=True), file=sys.stderr, flush=True)
    except OSError:
        pass


def _failure_output(payload: dict) -> None:
    """错误回执尽力输出；stdout故障不得覆盖原业务或产物退出码。"""
    try:
        print(json.dumps(payload, ensure_ascii=True), flush=True)
    except OSError:
        pass
