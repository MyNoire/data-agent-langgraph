"""知识命令只调用公开完整操作，持久数据与本次运行目录分开。"""

from collections import Counter
import json
import sys
from hashlib import sha256

from pydantic import ValidationError

from querypilot.databases.api import DatabaseRegistry
from querypilot.knowledge.api import (
    BuildConfig, Catalog, KnowledgeStorageError, KnowledgeStorageSettings,
    extract_catalog, enrich_catalog, curate_catalog, render_knowledge, build_corpus,
    verify_corpus, manage_store, import_corpus, verify_import, prepare_search, check_search,
)
from querypilot.runtime.api import publish_json, task_run


def _write_draft(draft, output_dir) -> None:
    """把已完整渲染的草稿写到新目录；输入先序列化，旧目标拒绝覆盖。"""
    groups = {"documents": draft.documents, "chunks": draft.chunks, "relations": draft.relations}
    texts = {name: "".join(json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n"
                          for item in items) for name, items in groups.items()}
    output_dir.mkdir(parents=True, exist_ok=False)
    for name, content in texts.items():
        with (output_dir / f"{name}.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)


def run(args, settings) -> int:
    """执行已解析知识命令，返回0；协议/服务/产物错误由统一入口保留分类。

    可选存储配置仅在store分支加载。独立导出与verify由本层拥有上下文，
    curate/build/store接收同一上下文并复用，不生成第二份运行。
    """
    storage = None
    secrets = ()
    group, action = args.knowledge_group, args.action
    if group == "store":
        try:
            storage = KnowledgeStorageSettings()
        except ValidationError as exc:
            raise KnowledgeStorageError("configuration_error", "知识库配置不合法") from exc
        secrets = tuple(value.get_secret_value() for value in
                        (storage.admin_dsn, storage.import_dsn, storage.read_dsn) if value is not None)
    if group == "catalog":
        operation, private = "knowledge_catalog", action in {"extract", "enrich"}
    elif group == "corpus":
        operation, private = "knowledge_corpus", action == "render"
    else:
        operation = "knowledge_import" if action == "import" else "knowledge_verify" if action == "verify" else "knowledge_manage"
        private = False
    hashes = []
    for name in ("catalog", "base", "plan", "semantic_file"):
        path = getattr(args, name, None)
        if path is not None and path.is_file():
            hashes.append(sha256(path.read_bytes()).hexdigest())
    curated = getattr(args, "curated_dir", None)
    if curated is not None:
        for name in ("manifest.json", "enriched_catalog.json", "quality_report.json"):
            path = curated / name
            if path.is_file():
                hashes.append(sha256(path.read_bytes()).hexdigest())
    with task_run(operation, settings, command=f"{group}.{action}", private=private, secrets=secrets,
                  config={"database_id": getattr(args, "database_id", None),
                          "profile_version": getattr(args, "profile_version", None),
                          "input_hashes": hashes}) as context:
        context.attach_stream(sys.stderr)
        args.run_id = context.run_id
        if group == "catalog" and action == "extract":
            database = DatabaseRegistry(settings.data_dir).get(args.database_id)
            result = extract_catalog(args.database_id, database)
            publish_json(args.output.resolve(), result.model_dump(mode="json"))
            summary = {"database_id": result.database_id, "tables": len(result.tables),
                       "columns": sum(len(table.columns) for table in result.tables),
                       "foreign_keys": sum(len(table.foreign_keys) for table in result.tables),
                       "output_file": str(args.output.resolve())}
        elif group == "catalog" and action == "enrich":
            catalog = Catalog.model_validate_json(args.catalog.read_text(encoding="utf-8"))
            result = enrich_catalog(catalog, args.description_dir)
            target = args.output_dir.resolve()
            target.mkdir(parents=True, exist_ok=False)
            publish_json(target / "enriched_catalog.json", result.enriched_catalog.model_dump(mode="json"))
            publish_json(target / "import_warnings.json", {"format_version": "description-warnings-v1",
                         "database_id": catalog.database_id,
                         "warnings": [item.model_dump(mode="json") for item in result.warnings]})
            descriptions = result.enriched_catalog.descriptions
            summary = {"database_id": catalog.database_id, "source_files": len(result.enriched_catalog.sources),
                       "catalog_columns": sum(len(table.columns) for table in catalog.tables),
                       "bound_columns": len(descriptions),
                       "columns_with_meaning_text": sum(item.description is not None or item.value_description is not None for item in descriptions),
                       "warnings": len(result.warnings),
                       "warning_counts": dict(sorted(Counter(item.code for item in result.warnings).items())),
                       "output_dir": str(target)}
        elif group == "catalog":
            result = curate_catalog(args.base, args.source_dir, args.plan, args.output_dir,
                                    run_context=context, settings=settings)
            summary = result.model_dump(mode="json")
        elif group == "corpus" and action == "render":
            result = render_knowledge(args.curated_dir, args.semantic_file)
            _write_draft(result, args.output_dir)
            summary = {"status": result.status, "documents": len(result.documents),
                       "chunks": len(result.chunks), "relations": len(result.relations),
                       "chunk_types": dict(Counter(chunk.knowledge_type for chunk in result.chunks)),
                       "max_chunk_chars": max((len(chunk.content) for chunk in result.chunks), default=0)}
        elif group == "corpus" and action == "build":
            result = build_corpus(args.curated_dir, args.semantic_file, args.output_dir, args.database_id,
                                    BuildConfig(profile_version=args.profile_version), run_context=context,
                                    settings=settings)
            summary = result.model_dump(mode="json")
        elif group == "corpus":
            result = verify_corpus(args.corpus_dir)
            summary = result.model_dump(mode="json")
        elif action == "import":
            result = import_corpus(args.corpus_dir, storage, run_context=context)
            summary = result.model_dump(mode="json")
        elif action == "verify":
            result = verify_import(args.corpus_dir, storage, run_context=context)
            summary = result.model_dump(mode="json")
        elif action in {"prepare-search", "check-search"}:
            result = (prepare_search if action == "prepare-search" else check_search)(storage, run_context=context)
            summary = result.model_dump(mode="json")
        else:
            result = manage_store(action, storage, run_context=context)
            summary = result.model_dump(mode="json")
        outcome = summary.get("outcome", summary.get("status", "succeeded"))
        outcome = outcome if outcome in {"imported", "reused", "verified"} else "succeeded"
        context.complete(result, outcome=outcome)
    print(json.dumps(summary, ensure_ascii=True, indent=2), flush=True)
    print(json.dumps({"event": "run_saved", "run_id": context.run_id,
                      "run_dir": str(context.run_dir)}), file=sys.stderr)
    return 0
