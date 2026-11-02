"""SQL 评测完整操作：明确选择模式，保留领域报告，不包含模型策略。"""

from hashlib import sha256
from pathlib import Path

from querypilot.runtime.api import Settings, sanitize_private, task_run
from querypilot.sql_generation.api import PROMPT_VERSION, SqlGenerator, generate_sql
from .models import CaseRecord, CaseOutcome, EvalReport, EvalSummary
from ._reports import export_public, read_report


def _configuration(cases_path: Path, settings: Settings, provider: str, evidence_mode: str) -> dict:
    """提取批次安全配置与来源摘要。

    参数：cases_path 为题集路径，settings 为运行配置，其余参数为评测模式。
    返回：不含题目、SQL 或完整设置的配置字典；读取题集计算摘要，I/O 错误原样传播。
    """
    return {"mode": "sql", "dataset_sha256": sha256(cases_path.read_bytes()).hexdigest(), "provider": provider,
            "evidence_mode": evidence_mode if provider != "fixture" else None,
            "prompt_version": PROMPT_VERSION if provider != "fixture" else None,
            "graph_version": "single-v1" if provider == "graph" else None,
            "model": settings.model_name if provider != "fixture" else None,
            "model_base_url": settings.model_base_url if provider != "fixture" else None,
            "model_temperature": settings.model_temperature if provider != "fixture" else None,
            "max_rows": settings.max_rows, "query_timeout_ms": settings.query_timeout_ms,
            "max_response_bytes": settings.max_response_bytes}


def run_direct(cases_path: Path, settings: Settings, *, provider: str = "model",
               evidence_mode: str = "none", generator: SqlGenerator | None = None,
               run_context=None) -> EvalReport:
    """完整运行 direct 评测并保存私有报告。

    参数：题集、配置、fixture/model、none/oracle、四参生成器、可选外层上下文。
    返回：原 EvalReport；模型异常按原规则逐题记录，查库失败中止批次。
    副作用：只读数据库；model 可调用模型。顶级运行创建并关闭，嵌套只复用。
    """
    from ._direct import run_direct as perform

    if provider not in {"model", "fixture"} or evidence_mode not in {"none", "oracle"}:
        raise ValueError("invalid evaluation mode")
    owned = run_context is None
    with task_run("evaluation", settings, run_context=run_context, command="direct", private=True,
                  config=_configuration(cases_path, settings, provider, evidence_mode)) as context:
        result = perform(cases_path, settings, provider=provider, evidence_mode=evidence_mode,
                         generator=generator or generate_sql, run_context=context)
        secrets = (settings.model_api_key,) if settings.model_api_key != "local" else ()
        result = sanitize_private(result, secrets=secrets)
        if owned:
            context.complete(result)
        return result


def run_graph(cases_path: Path, settings: Settings, *, generator: SqlGenerator | None = None,
              run_context=None) -> EvalReport:
    """完整运行单图评测并保存私有报告。

    参数：题集、配置、固定或真实生成器及可选外层上下文。
    返回：原 EvalReport；逐题错误继续、无预测时比率为 None；不增加检索或修复。
    副作用：读取 SQLite，可调用模型；一个批次一份运行，子题不另建根。
    """
    from ._graph import run_graph as perform

    owned = run_context is None
    with task_run("evaluation", settings, run_context=run_context, command="graph", private=True,
                  config=_configuration(cases_path, settings, "graph", "none")) as context:
        result = perform(cases_path, settings, generator=generator or generate_sql, run_context=context)
        secrets = (settings.model_api_key,) if settings.model_api_key != "local" else ()
        result = sanitize_private(result, secrets=secrets)
        if owned:
            context.complete(result)
        return result


__all__ = [
    "run_direct", "run_graph", "read_report", "export_public",
    "CaseRecord", "CaseOutcome", "EvalReport", "EvalSummary",
]
