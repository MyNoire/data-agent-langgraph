"""用静态依赖和隔离反例守住模块公开入口，不导入业务代码。"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest


KNOWLEDGE_PARTS = ("knowledge_catalog", "knowledge_corpus", "knowledge_store")
EVALUATION_PARTS = ("sql", "retrieval", "datasets")
# datasets 沿用既有实现；例外只属于根 API 的这六个导入，不开放整个子能力。
DATASET_REEXPORTS = {
    ("querypilot.evaluation.api", "querypilot.evaluation.datasets._errors", "DataPreparationError"),
    ("querypilot.evaluation.api", "querypilot.evaluation.datasets._fetch", "fetch_bird"),
    ("querypilot.evaluation.api", "querypilot.evaluation.datasets._import", "import_bird"),
    ("querypilot.evaluation.api", "querypilot.evaluation.datasets._selection", "select_final100"),
    ("querypilot.evaluation.api", "querypilot.evaluation.datasets._freeze", "freeze_final100"),
    ("querypilot.evaluation.api", "querypilot.evaluation.datasets._prepare", "prepare_final100"),
}
ALLOWED_DEPENDENCIES = {
    "entrypoints": {"querying", "sql_generation", "databases", "evaluation", "knowledge", "retrieval", "runtime"},
    "querying": {"sql_generation", "databases", "runtime"},
    "sql_generation": {"runtime"},
    "databases": {"runtime"},
    "evaluation": {"evaluation.sql"},
    "evaluation.sql": {"querying", "sql_generation", "databases", "runtime"},
    "evaluation.retrieval": {"runtime"},
    "evaluation.datasets": {"databases", "runtime"},
    "knowledge": {*(f"knowledge.{part}" for part in KNOWLEDGE_PARTS), "runtime"},
    "knowledge.knowledge_catalog": {"runtime"},
    "knowledge.knowledge_corpus": {"knowledge.knowledge_catalog", "runtime"},
    "knowledge.knowledge_store": {"knowledge.knowledge_corpus", "runtime"},
    "retrieval": {"knowledge", "runtime"},
    "runtime": set(),
}


@dataclass(frozen=True, order=True)
class BoundaryIssue:
    """保存静态违规的位置和原因；仅用于测试报告，不读取文件内容。"""

    path: str
    line: int
    rule: str
    detail: str


@dataclass(frozen=True)
class ImportReference:
    """保存已经解析的导入目标、符号与本地名称。"""

    module: str
    symbol: str | None
    local_name: str
    line: int


@dataclass
class SourceModule:
    """保存 AST 与显式导出信息；不执行模块或触发可选驱动导入。"""

    path: Path
    name: str
    tree: ast.Module
    is_package: bool
    references: list[ImportReference]
    exports: set[str] | None


def capability(module_name: str) -> str:
    """识别源码所属能力，分别保留知识与评测的子边界。

    参数：module_name 为解析后的完整模块名。
    返回：顶层能力名或已定义的子能力名；脚本归入口，包外名称归 root。
    """
    if not module_name.startswith("querypilot."):
        return "entrypoints" if module_name.startswith("scripts.") else "root"
    parts = module_name.split(".")
    if parts[1] in {"__main__", "__init__"}:
        return "entrypoints"
    if parts[1] == "knowledge" and len(parts) > 2 and parts[2] in KNOWLEDGE_PARTS:
        return ".".join(parts[1:3])
    if parts[1] == "evaluation" and len(parts) > 2 and parts[2] in EVALUATION_PARTS:
        return ".".join(parts[1:3])
    return parts[1]


def _explicit_exports(tree: ast.Module) -> set[str] | None:
    """读取字面量 __all__；缺少声明返回 None，不尝试执行动态表达式。"""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            try:
                names = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                return None
            if isinstance(names, (list, tuple, set)) and all(isinstance(name, str) for name in names):
                return set(names)
    return None


def _absolute_from(source: SourceModule, node: ast.ImportFrom) -> str:
    """按 Python 包规则解析 from 的相对层级；非法越界以空目标表示。"""
    if not node.level:
        return node.module or ""
    package = source.name.split(".") if source.is_package else source.name.split(".")[:-1]
    if node.level > len(package):
        return ""
    prefix = package[: len(package) - node.level + 1]
    return ".".join(prefix + ([node.module] if node.module else []))


def _references(source: SourceModule, modules: dict[str, SourceModule]) -> list[ImportReference]:
    """解析所有静态 import/from，包括函数、TYPE_CHECKING 内和子模块形式。"""
    references = []
    for node in ast.walk(source.tree):
        if isinstance(node, ast.Import):
            references.extend(
                ImportReference(alias.name, None, alias.asname or alias.name.split(".")[0], node.lineno)
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            parent = _absolute_from(source, node)
            for alias in node.names:
                child = f"{parent}.{alias.name}" if parent else alias.name
                # from package import api 与 from package.api import operation 不同。
                if child in modules:
                    references.append(ImportReference(child, None, alias.asname or alias.name, node.lineno))
                else:
                    references.append(ImportReference(parent, alias.name, alias.asname or alias.name, node.lineno))
    return references


def _module_leak(
    modules: dict[str, SourceModule], module_name: str, symbol: str, seen: set[tuple[str, str]]
) -> str | None:
    """沿显式重导出追踪模块别名；返回被 API 泄漏的内部模块，不追踪普通函数实现。"""
    key = (module_name, symbol)
    if key in seen or module_name not in modules:
        return None
    seen = seen | {key}
    for reference in modules[module_name].references:
        if reference.local_name == symbol:
            if reference.symbol is None:
                return reference.module
            return _module_leak(modules, reference.module, reference.symbol, seen)
    return None


def _cyclic_components(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    """输入有向图；返回确定排序的强连通分量，只保留多节点环。"""
    counter = 0
    indexes: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    active: set[str] = set()
    cycles: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        """递归计算 Tarjan 分量；只修改当前图遍历的局部状态。"""
        nonlocal counter
        indexes[node] = low[node] = counter
        counter += 1
        stack.append(node)
        active.add(node)
        for target in sorted(graph.get(node, set())):
            if target not in indexes:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in active:
                low[node] = min(low[node], indexes[target])
        if low[node] == indexes[node]:
            component = []
            while True:
                member = stack.pop()
                active.remove(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                cycles.append(tuple(sorted(component)))

    for node in sorted(graph):
        if node not in indexes:
            visit(node)
    return sorted(cycles)


def scan_module_boundaries(project_root: Path) -> list[BoundaryIssue]:
    """扫描项目生产源码及脚本，返回按位置排序的违规，不导入或改写文件。

    参数：project_root 含 src/querypilot 和可选 scripts 目录。
    返回：私有跨模块依赖、错误方向、公开模块泄漏及依赖环列表。
    异常：无法读取文件或 Python 语法错误原样上抛，不能把扫描失败当通过。
    """
    source_root = project_root / "src"
    package_root = source_root / "querypilot"
    if not package_root.is_dir():
        raise FileNotFoundError(f"缺少生产源码目录：{package_root}")
    modules: dict[str, SourceModule] = {}
    paths = sorted(package_root.rglob("*.py")) + sorted((project_root / "scripts").rglob("*.py"))
    for path in paths:
        base = source_root if path.is_relative_to(source_root) else project_root
        parts = list(path.relative_to(base).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts.pop()
        name = ".".join(parts)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        modules[name] = SourceModule(path, name, tree, is_package, [], _explicit_exports(tree))
    for source in modules.values():
        source.references = _references(source, modules)

    issues: set[BoundaryIssue] = set()
    module_graph = {name: set() for name in modules}
    capability_graph = {name: set() for name in ALLOWED_DEPENDENCIES}
    for source in modules.values():
        owner = capability(source.name)
        relative_path = source.path.relative_to(project_root).as_posix()

        def report(line: int, rule: str, detail: str) -> None:
            """将当前文件的确定性诊断加入报告；不输出源码或敏感值。"""
            issues.add(BoundaryIssue(relative_path, line, rule, detail))

        if owner not in ALLOWED_DEPENDENCIES and source.name != "querypilot":
            report(1, "unknown-capability", f"目标结构未定义旧能力 {owner}")
        if source.name.endswith(".api") and source.exports is not None:
            # 即使调用方只 import api，也不能借 api.engine 取到内部模块。
            for symbol in sorted(source.exports):
                leaked = _module_leak(modules, source.name, symbol, set())
                if leaked and leaked.startswith("querypilot.") and not leaked.endswith(".api"):
                    report(1, "module-reexport", f"显式公开的 {source.name}.{symbol} 泄漏内部模块 {leaked}")
        for reference in source.references:
            target = reference.module
            if not (target == "querypilot" or target.startswith("querypilot.")):
                continue
            target_owner = capability(target)
            if target in modules:
                module_graph[source.name].add(target)
            if owner == target_owner:
                continue
            if target_owner != "root":
                capability_graph.setdefault(owner, set()).add(target_owner)
            public_api = f"querypilot.{target_owner}.api"
            dataset_reexport = (source.name, target, reference.symbol) in DATASET_REEXPORTS
            if target != public_api and not dataset_reexport:
                report(reference.line, "private-import", f"{source.name} → {target}；跨能力只能导入 {public_api}")
            if target_owner not in ALLOWED_DEPENDENCIES.get(owner, set()) and not dataset_reexport:
                report(reference.line, "dependency-direction", f"不允许 {owner} → {target_owner}")
            target_source = modules.get(target)
            if reference.symbol is not None:
                if reference.symbol == "*" or reference.symbol.startswith("_"):
                    report(reference.line, "non-public-symbol", f"禁止跨能力导入 {target}.{reference.symbol}")
                elif target_source and target_source.exports is not None and reference.symbol not in target_source.exports:
                    report(reference.line, "non-public-symbol", f"{target}.{reference.symbol} 未列入显式 __all__")
                leaked = _module_leak(modules, target, reference.symbol, set())
                if leaked and leaked.startswith("querypilot.") and not leaked.endswith(".api"):
                    report(reference.line, "module-reexport", f"{target}.{reference.symbol} 重导出内部模块 {leaked}")

    for component in _cyclic_components(module_graph):
        source = modules[component[0]]
        issues.add(BoundaryIssue(source.path.relative_to(project_root).as_posix(), 1, "module-cycle", " ↔ ".join(component)))
    for component in _cyclic_components(capability_graph):
        issues.add(BoundaryIssue("src/querypilot", 1, "capability-cycle", " ↔ ".join(component)))
    return sorted(issues)


def assert_production_boundaries(project_root: Path | None = None) -> None:
    """全量扫描真实生产文件；有违规即断言失败，由最终迁移验收调用。

    参数：可选项目根；默认按本测试文件定位。
    返回：无违规返回 None；异常包含每条违规的文件、行号与规则。
    """
    root = project_root or Path(__file__).resolve().parents[2]
    issues = scan_module_boundaries(root)
    assert not issues, "\n".join(f"{issue.path}:{issue.line} [{issue.rule}] {issue.detail}" for issue in issues)


def _fixture_project(root: Path, files: dict[str, str]) -> Path:
    """将隔离 Python 样例写入临时项目；不创建或导入真实业务包。"""
    (root / "src/querypilot").mkdir(parents=True)
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def _evaluation_fixture_files() -> dict[str, str]:
    """提供评测搬迁后的合法隔离源码，不读取或导入真实业务包。

    返回：包含根聚合、三种子能力及所需公开 API 的路径到源码映射。
    调用方可替换单个文件，以验证同一合法基线上的具体越界。
    """
    return {
        "src/querypilot/entrypoints/commands/evaluation.py": "from querypilot.evaluation.api import run_direct\n",
        "src/querypilot/entrypoints/commands/retrieval.py": "from querypilot.retrieval.api import RetrievalRequest\n",
        "src/querypilot/evaluation/__init__.py": "",
        "src/querypilot/evaluation/api.py": (
            "from .sql.api import run_direct, EvalReport\n"
            "from .datasets._errors import DataPreparationError\n"
            "from .datasets._fetch import fetch_bird\n"
            "from .datasets._import import import_bird\n"
            "from .datasets._selection import select_final100\n"
            "from .datasets._freeze import freeze_final100\n"
            "from .datasets._prepare import prepare_final100\n"
            "__all__ = ['run_direct', 'EvalReport', 'DataPreparationError', 'fetch_bird', "
            "'import_bird', 'select_final100', 'freeze_final100', 'prepare_final100']\n"
        ),
        "src/querypilot/evaluation/sql/__init__.py": "",
        "src/querypilot/evaluation/sql/api.py": (
            "from ._direct import run_direct\nfrom .models import EvalReport\n"
            "__all__ = ['run_direct', 'EvalReport']\n"
        ),
        "src/querypilot/evaluation/sql/models.py": "class EvalReport: pass\n",
        "src/querypilot/evaluation/sql/_direct.py": (
            "from querypilot.querying.api import ask\n"
            "from querypilot.sql_generation.api import generate\n"
            "from querypilot.databases.api import execute\n"
            "from querypilot.runtime.api import Settings\n"
            "from ._compare import normalized_rows\ndef run_direct(): pass\n"
        ),
        "src/querypilot/evaluation/sql/_compare.py": "def normalized_rows(): pass\n",
        "src/querypilot/evaluation/retrieval/__init__.py": "from querypilot.runtime.api import Settings\n",
        "src/querypilot/evaluation/datasets/__init__.py": "",
        "src/querypilot/evaluation/datasets/_errors.py": "class DataPreparationError(Exception): pass\n",
        "src/querypilot/evaluation/datasets/_fetch.py": "from ._errors import DataPreparationError\ndef fetch_bird(): pass\n",
        "src/querypilot/evaluation/datasets/_import.py": (
            "from querypilot.databases.api import execute\n"
            "from querypilot.runtime.api import Settings\ndef import_bird(): pass\n"
        ),
        "src/querypilot/evaluation/datasets/_selection.py": "def select_final100(): pass\n",
        "src/querypilot/evaluation/datasets/_freeze.py": "from ._selection import select_final100\ndef freeze_final100(): pass\n",
        "src/querypilot/evaluation/datasets/_prepare.py": "def prepare_final100(): pass\n",
        "src/querypilot/querying/api.py": "def ask(): pass\n__all__ = ['ask']\n",
        "src/querypilot/sql_generation/api.py": "def generate(): pass\n__all__ = ['generate']\n",
        "src/querypilot/databases/api.py": "def execute(): pass\n__all__ = ['execute']\n",
        "src/querypilot/runtime/api.py": "class Settings: pass\n__all__ = ['Settings']\n",
        "src/querypilot/retrieval/api.py": "from .models import RetrievalRequest\n__all__ = ['RetrievalRequest']\n",
        "src/querypilot/retrieval/models.py": "class RetrievalRequest: pass\n",
        "src/querypilot/knowledge/api.py": "def open_read_scope(): pass\n__all__ = ['open_read_scope']\n",
    }


@pytest.mark.parametrize(("module_name", "expected"), [
    ("querypilot.evaluation.api", "evaluation"),
    ("querypilot.evaluation.sql", "evaluation.sql"),
    ("querypilot.evaluation.sql._compare", "evaluation.sql"),
    ("querypilot.evaluation.retrieval._runner", "evaluation.retrieval"),
    ("querypilot.evaluation.datasets._fetch", "evaluation.datasets"),
    ("querypilot.retrieval.service", "retrieval"),
])
def test_evaluation_subcapability_ownership(module_name: str, expected: str) -> None:
    """验证评测子目录独立归属，且不会与检索本体混为同一能力。"""
    assert capability(module_name) == expected


def test_evaluation_aggregation_and_retrieval_types_are_legal(tmp_path: Path) -> None:
    """验证根 SQL 聚合、六项数据导出、子能力内部调用与入口读取已有类型合法。"""
    root = _fixture_project(tmp_path, _evaluation_fixture_files())
    assert scan_module_boundaries(root) == []


@pytest.mark.parametrize(("path", "source", "expected"), [
    ("evaluation/api.py", "from .sql._direct import run_direct", "private-import"),
    ("evaluation/api.py", "from .sql.models import EvalReport", "private-import"),
    ("evaluation/api.py", "from .retrieval.api import evaluate_retrieval_cases", "dependency-direction"),
    ("evaluation/api.py", "from .datasets._fetch import hidden", "private-import"),
    ("evaluation/api.py", "from .datasets._fetch import import_bird", "private-import"),
    ("evaluation/api.py", "from .datasets import _fetch", "private-import"),
    ("evaluation/api.py", "import querypilot.evaluation.datasets._fetch as fetch_bird", "private-import"),
    ("evaluation/api.py", "from .datasets.api import fetch_bird", "dependency-direction"),
    ("evaluation/api.py", "from .datasets._fetch import *", "non-public-symbol"),
    ("evaluation/_exports.py", "from .datasets._fetch import fetch_bird", "private-import"),
    ("evaluation/sql/_direct.py", "from ..datasets._fetch import fetch_bird", "private-import"),
    ("evaluation/sql/_direct.py", "from ..retrieval.api import evaluate_retrieval_cases", "dependency-direction"),
    ("evaluation/retrieval/_runner.py", "from ..sql._compare import normalized_rows", "private-import"),
    ("evaluation/retrieval/_runner.py", "from ..sql.api import run_direct", "dependency-direction"),
    ("evaluation/sql/_direct.py", "from ..api import run_direct", "dependency-direction"),
    ("evaluation/datasets/_fetch.py", "from ..api import fetch_bird", "dependency-direction"),
    ("evaluation/retrieval/_runner.py", "from ..api import run_direct", "dependency-direction"),
    ("retrieval/service.py", "from querypilot.evaluation.api import run_direct", "dependency-direction"),
    ("runtime/_events.py", "from querypilot.evaluation.sql.api import run_direct", "dependency-direction"),
    ("entrypoints/commands/evaluation.py", "from querypilot.evaluation.sql.api import run_direct", "dependency-direction"),
    ("entrypoints/commands/retrieval.py", "from querypilot.retrieval.models import RetrievalRequest", "private-import"),
    ("entrypoints/commands/retrieval.py", "from querypilot.retrieval.api import project_query", "non-public-symbol"),
    ("evaluation/retrieval/_runner.py", "from querypilot.retrieval.api import RetrievalRequest", "dependency-direction"),
    ("evaluation/retrieval/_runner.py", "from querypilot.knowledge.api import open_read_scope", "dependency-direction"),
    ("evaluation/sql/_direct.py", "from querypilot.knowledge.api import open_read_scope", "dependency-direction"),
    ("evaluation/datasets/_fetch.py", "from querypilot.sql_generation.api import generate", "dependency-direction"),
])
def test_evaluation_boundaries_reject_shortcuts(
    tmp_path: Path, path: str, source: str, expected: str
) -> None:
    """验证评测越界、例外泛化和 A 阶段尚未开放的检索依赖均被拒绝。"""
    files = _evaluation_fixture_files()
    files[f"src/querypilot/{path}"] = source + "\n"
    root = _fixture_project(tmp_path, files)
    assert expected in {issue.rule for issue in scan_module_boundaries(root)}


@pytest.mark.parametrize("files", [
    {
        "evaluation/api.py": "from .sql.api import scoring\n__all__ = ['scoring']\n",
        "evaluation/sql/api.py": "from . import _compare as scoring\n__all__ = ['scoring']\n",
    },
    {
        "evaluation/datasets/_fetch.py": "from . import _implementation as fetch_bird\n",
        "evaluation/datasets/_implementation.py": "",
    },
])
def test_evaluation_reexports_cannot_leak_internal_modules(tmp_path: Path, files: dict[str, str]) -> None:
    """验证根 SQL 间接重导出与 datasets 精确例外都不能泄漏内部模块。"""
    sources = _evaluation_fixture_files()
    sources.update({f"src/querypilot/{path}": source for path, source in files.items()})
    root = _fixture_project(tmp_path, sources)
    issues = scan_module_boundaries(root)
    assert any(issue.rule == "module-reexport" and "querypilot.evaluation.api" in issue.detail for issue in issues)


@pytest.mark.parametrize(("files", "expected"), [
    ({
        "evaluation/sql/_direct.py": "from .api import run_direct\ndef run_direct(): pass\n",
    }, {"module-cycle"}),
    ({
        "evaluation/sql/api.py": "from ..api import run_direct\n__all__ = ['run_direct']\n",
    }, {"module-cycle", "capability-cycle"}),
    ({
        "evaluation/sql/_direct.py": "from ..retrieval.api import evaluate_retrieval_cases\ndef run_direct(): pass\n",
        "evaluation/retrieval/api.py": "from ..sql.api import run_direct\n",
    }, {"module-cycle", "capability-cycle"}),
    ({
        "evaluation/datasets/_fetch.py": "from ..api import fetch_bird\n",
    }, {"module-cycle", "capability-cycle"}),
])
def test_evaluation_cycles_include_aggregation_and_dataset_exceptions(
    tmp_path: Path, files: dict[str, str], expected: set[str]
) -> None:
    """验证评测内部、根聚合及数据导出例外形成的模块环与能力环仍会报告。"""
    sources = _evaluation_fixture_files()
    sources.update({f"src/querypilot/{path}": source for path, source in files.items()})
    root = _fixture_project(tmp_path, sources)
    assert expected <= {issue.rule for issue in scan_module_boundaries(root)}


def test_legal_api_exports_and_internal_imports(tmp_path: Path) -> None:
    """验证普通 API、同能力内部、知识聚合和相对兄弟 API 依赖均合法。"""
    root = _fixture_project(tmp_path, {
        "src/querypilot/__main__.py": "from .entrypoints.cli import main\n",
        "src/querypilot/entrypoints/cli.py": "from querypilot.querying.api import ask\n",
        "src/querypilot/querying/api.py": "from ._graph import ask\n__all__ = ['ask']\n",
        "src/querypilot/querying/_graph.py": "from querypilot.databases import api\ndef ask(): pass\n",
        "src/querypilot/databases/api.py": "from ._engine import execute\n__all__ = ['execute']\n",
        "src/querypilot/databases/_engine.py": "from querypilot.runtime.api import Settings\ndef execute(): pass\n",
        "src/querypilot/runtime/api.py": "from .settings import Settings\n__all__ = ['Settings']\n",
        "src/querypilot/runtime/settings.py": "class Settings: pass\n",
        "src/querypilot/retrieval/service.py": "from querypilot.knowledge.api import open_read_scope\nfrom .models import Scope\n",
        "src/querypilot/retrieval/models.py": "class Scope: pass\n",
        "src/querypilot/knowledge/__init__.py": "",
        "src/querypilot/knowledge/api.py": "from .knowledge_store.api import open_read_scope\n__all__ = ['open_read_scope']\n",
        "src/querypilot/knowledge/knowledge_store/api.py": "from . import _read\nfrom ._read import open_read_scope\n__all__ = ['open_read_scope']\n",
        "src/querypilot/knowledge/knowledge_store/_read.py": "from ..knowledge_corpus.api import load\ndef open_read_scope(): pass\n",
        "src/querypilot/knowledge/knowledge_corpus/api.py": "from ..knowledge_catalog.api import verify_sources\ndef load(): pass\n__all__ = ['load']\n",
        "src/querypilot/knowledge/knowledge_catalog/api.py": "def verify_sources(): pass\n__all__ = ['verify_sources']\n",
        "scripts/task.py": "from querypilot.knowledge.api import open_read_scope\n",
    })
    assert scan_module_boundaries(root) == []


@pytest.mark.parametrize(("source", "expected"), [
    ("from querypilot.databases._engine import execute", "private-import"),
    ("import querypilot.databases._engine as engine", "private-import"),
    ("from ..databases._engine import execute", "private-import"),
    ("from ..databases import _engine", "private-import"),
    ("from querypilot.databases import execute", "private-import"),
    ("from querypilot.databases.api import hidden", "non-public-symbol"),
    ("from querypilot.databases.api import *", "non-public-symbol"),
    ("from querypilot.evaluation.api import run", "dependency-direction"),
    ("from querypilot.knowledge.api import build", "dependency-direction"),
    ("from querypilot.retrieval.api import retrieve", "dependency-direction"),
    ("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from querypilot.evaluation.api import run", "dependency-direction"),
])
def test_querying_rejects_private_or_forbidden_imports(tmp_path: Path, source: str, expected: str) -> None:
    """验证绝对、相对、包成员、星号和类型导入不能绕过公开边界。"""
    root = _fixture_project(tmp_path, {
        "src/querypilot/querying/_graph.py": source + "\n",
        "src/querypilot/databases/__init__.py": "from ._engine import execute\n",
        "src/querypilot/databases/_engine.py": "def execute(): pass\n",
        "src/querypilot/databases/api.py": "from ._engine import execute\nhidden = 1\n__all__ = ['execute']\n",
    })
    assert expected in {issue.rule for issue in scan_module_boundaries(root)}


@pytest.mark.parametrize(("path", "source"), [
    ("runtime/_events.py", "from querypilot.databases.api import execute\n"),
    ("knowledge/knowledge_catalog/_extract.py", "from ..knowledge_corpus.api import load\n"),
    ("knowledge/knowledge_store/_read.py", "from ..api import load\n"),
    ("retrieval/service.py", "from querypilot.knowledge.knowledge_store.api import open_read_scope\n"),
])
def test_runtime_and_knowledge_directions(tmp_path: Path, path: str, source: str) -> None:
    """验证 runtime 不反向依赖业务，知识子模块不反向聚合或越层访问。"""
    root = _fixture_project(tmp_path, {f"src/querypilot/{path}": source})
    assert "dependency-direction" in {issue.rule for issue in scan_module_boundaries(root)}


def test_explicit_reexport_cannot_expose_internal_module(tmp_path: Path) -> None:
    """验证 api 里改名重导出的内部模块仍不能作为跨能力捷径。"""
    root = _fixture_project(tmp_path, {
        "src/querypilot/querying/_graph.py": "from querypilot.databases.api import engine\n",
        "src/querypilot/databases/api.py": "from ._exports import engine\n__all__ = ['engine']\n",
        "src/querypilot/databases/_exports.py": "from . import _engine as engine\n",
        "src/querypilot/databases/_engine.py": "",
    })
    issues = scan_module_boundaries(root)
    assert any(issue.rule == "module-reexport" and "_engine" in issue.detail for issue in issues)


def test_importing_whole_api_cannot_hide_internal_module_export(tmp_path: Path) -> None:
    """验证 import api 后通过属性访问内部模块的绕路也会被导出审计拦住。"""
    root = _fixture_project(tmp_path, {
        "src/querypilot/querying/_graph.py": "import querypilot.databases.api as database\n",
        "src/querypilot/databases/api.py": "from . import _engine as engine\n__all__ = ['engine']\n",
        "src/querypilot/databases/_engine.py": "",
    })
    assert "module-reexport" in {issue.rule for issue in scan_module_boundaries(root)}


@pytest.mark.parametrize(("files", "rule"), [
    ({"runtime/_a.py": "from ._b import b\na = 1\n", "runtime/_b.py": "from ._a import a\nb = 1\n"}, "module-cycle"),
    ({"querying/api.py": "from querypilot.sql_generation.api import generate\n", "sql_generation/api.py": "from querypilot.querying.api import ask\n"}, "capability-cycle"),
])
def test_cycles_are_reported_without_importing_code(tmp_path: Path, files: dict[str, str], rule: str) -> None:
    """验证模块环和能力环都被发现；即使样例不能实际导入也能报告。"""
    root = _fixture_project(tmp_path, {f"src/querypilot/{path}": source for path, source in files.items()})
    assert rule in {issue.rule for issue in scan_module_boundaries(root)}


def test_scripts_and_legacy_files_are_not_omitted(tmp_path: Path) -> None:
    """验证扫描覆盖 scripts 和旧能力，不以阶段白名单掩盖迁移遗留。"""
    root = _fixture_project(tmp_path, {
        "src/querypilot/evals/baseline.py": "",
        "scripts/task.py": "from querypilot.databases._engine import execute\n",
    })
    issues = scan_module_boundaries(root)
    assert any(issue.path.startswith("scripts/") and issue.rule == "private-import" for issue in issues)
    assert any(issue.rule == "unknown-capability" and "evals" in issue.detail for issue in issues)


def test_scan_failure_is_not_silently_accepted(tmp_path: Path) -> None:
    """验证缺源码或无效 Python 必须失败，不能误报零违规。"""
    with pytest.raises(FileNotFoundError):
        scan_module_boundaries(tmp_path)
    root = _fixture_project(tmp_path, {"src/querypilot/runtime/api.py": "def invalid(\n"})
    with pytest.raises(SyntaxError):
        scan_module_boundaries(root)



def test_production_module_boundaries() -> None:
    """核对实际生产源码与工具；跨能力私有调用和循环不能进入交付。"""
    assert_production_boundaries()


if __name__ == "__main__":
    assert_production_boundaries()
