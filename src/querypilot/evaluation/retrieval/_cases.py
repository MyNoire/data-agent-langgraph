"""从 BIRD 题目和 Corpus 生成检索题集。"""

from hashlib import sha256
import json
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from querypilot.knowledge.api import KnowledgeScope, load_corpus

from .models import RelevantColumn, RetrievalCase, RetrievalCaseSet


class RetrievalCaseError(ValueError):
    """题集无法生成。question_ids 是金标准 SQL 解析失败或引用了 Corpus 外对象的题号。"""

    def __init__(self, message: str, question_ids: list[int] | None = None):
        super().__init__(message)
        self.question_ids = question_ids or []


def _relevant_set(sql: str, schema: dict[str, dict[str, str]]) -> tuple[list[str], list[RelevantColumn]]:
    """解析金标准 SQL，返回它引用的表和列。"""
    # 不展开 *：SELECT * 和 COUNT(*) 都不把列算进 Relevant Set。
    tree = qualify(sqlglot.parse_one(sql, read="sqlite"), schema=schema, dialect="sqlite", expand_stars=False)
    tables, columns = set(), set()
    for scope in traverse_scope(tree):
        for source in scope.sources.values():
            if isinstance(source, exp.Table):
                if source.name not in schema:
                    raise ValueError("引用了 Corpus 中不存在的表")
                tables.add(source.name)
        for column in scope.columns:
            source = scope.sources.get(column.table)
            if isinstance(source, exp.Table):
                columns.add((source.name, column.name))
    return sorted(tables), [RelevantColumn(table_name=t, column_name=c) for t, c in sorted(columns)]


def prepare_retrieval_cases(questions_file: Path, frozen_file: Path, corpus_dirs: list[Path],
                            output_file: Path) -> RetrievalCaseSet:
    """生成检索题集：corpus_dirs 中各库在 questions_file 里的全部题目。

    参数：
        questions_file：BIRD mini-dev 题目文件。
        frozen_file：冻结题文件，其中的 question_id 标为 frozen。
        corpus_dirs：每个库一份已校验的 Corpus 目录，提供 Knowledge Scope 和表列名称。
        output_file：题集输出位置。
    返回：
        写出的 RetrievalCaseSet。金标准 SQL 和 evidence 只用于解析，不写进题集。
    副作用：
        写出题集文件，并在旁边写出可提交的 `<题集文件名>_manifest.json`。
    异常：
        RetrievalCaseError：有题无法解析时整批失败，列出全部失败题号，不写任何文件。
    """
    corpora = {}
    for corpus_dir in corpus_dirs:
        corpus = load_corpus(corpus_dir)
        database_id = corpus.manifest.identity_descriptor.database_scope[0]
        schema: dict[str, dict[str, str]] = {}
        for chunk in corpus.chunks:
            if chunk.knowledge_type == "column":
                schema.setdefault(chunk.table_name, {})[chunk.column_name] = "TEXT"
        scope = KnowledgeScope(
            corpus_id=corpus.manifest.corpus_id, database_id=database_id,
            schema_version=corpus.manifest.identity_descriptor.schema_versions[database_id],
        )
        corpora[database_id] = (scope, schema)

    questions = json.loads(questions_file.read_text(encoding="utf-8"))
    frozen_ids = {item["question_id"] for item in json.loads(frozen_file.read_text(encoding="utf-8"))}
    cases, failed = [], []
    for item in questions:
        if item["db_id"] not in corpora:
            continue
        scope, schema = corpora[item["db_id"]]
        try:
            tables, columns = _relevant_set(item["SQL"], schema)
        except (sqlglot.errors.SqlglotError, ValueError):
            failed.append(item["question_id"])
            continue
        cases.append(RetrievalCase(
            question_id=item["question_id"], db_id=item["db_id"], difficulty=item["difficulty"],
            question=item["question"], frozen=item["question_id"] in frozen_ids, scope=scope,
            relevant_tables=tables, relevant_columns=columns,
        ))

    if failed:
        raise RetrievalCaseError(f"{len(failed)} 道题的金标准 SQL 无法解析到 Corpus 中的表和列", failed)
    case_set = RetrievalCaseSet(cases=cases)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(case_set.model_dump_json(indent=2) + "\n", encoding="utf-8")
    _write_manifest(output_file, questions_file, frozen_file, corpora, case_set)
    return case_set


def _write_manifest(output_file: Path, questions_file: Path, frozen_file: Path, corpora: dict,
                    case_set: RetrievalCaseSet) -> None:
    """在题集旁写出 manifest：题号、来源 hash 和 Knowledge Scope，不含原题。"""
    def digest(path: Path) -> str:
        return sha256(path.read_bytes()).hexdigest()

    question_ids: dict[str, list[int]] = {database_id: [] for database_id in corpora}
    for case in case_set.cases:
        question_ids[case.db_id].append(case.question_id)
    manifest = {
        "format_version": "querypilot-retrieval-cases-manifest-v1",
        "case_set_file": output_file.name,
        "case_set_sha256": digest(output_file),
        "questions_sha256": digest(questions_file),
        "frozen_sha256": digest(frozen_file),
        "scopes": {database_id: scope.model_dump(mode="json") for database_id, (scope, _) in corpora.items()},
        "question_ids": question_ids,
        "frozen_question_ids": [case.question_id for case in case_set.cases if case.frozen],
    }
    manifest_file = output_file.with_name(f"{output_file.stem}_manifest.json")
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
