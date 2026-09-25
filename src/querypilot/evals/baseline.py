"""Direct-prompt Text2SQL 基线及逐题 smoke 评测。"""

import json
import hashlib
import statistics
import time
from collections import Counter
from pathlib import Path

from openai import OpenAI

from querypilot.config import Settings
from querypilot.database.executor import execute_readonly
from querypilot.database.registry import DatabaseRegistry
from querypilot.database.schema import format_schema, inspect_sqlite


PROMPT_VERSION = "direct-v2"


def direct_prompt(question: str, schema: str, evidence: str = "") -> str:
    """把问题和 Schema 拼成 direct-prompt 模型输入。

    参数:
        question: 用户的自然语言问题。
        schema: 当前数据库的表列说明文本。
        evidence: 可选业务证据；空字符串表示不提供 oracle 证据。
    返回:
        要求模型只输出一条 SQLite SELECT 的 Prompt 文本。
    """
    evidence_line = f"Evidence: {evidence}\n" if evidence else ""
    return (
        "Generate exactly one read-only SQLite SELECT query. Return SQL only, without Markdown.\n"
        f"Schema:\n{schema}\n"
        f"{evidence_line}"
        f"Question: {question}"
    )


def generate_sql(question: str, schema: str, evidence: str, settings: Settings) -> str:
    """调用 OpenAI-compatible 服务生成候选 SQL。

    参数:
        question: 用户自然语言问题。
        schema: 当前数据库的表列说明文本。
        evidence: 可选业务证据；生产路径和无证据基线传空字符串。
        settings: 模型地址、名称、密钥和可选温度配置。
    返回:
        去掉首尾 Markdown SQL 代码围栏后的模型输出文本。
    异常:
        模型服务或网络失败时向上抛出 OpenAI 客户端异常。
    """
    client = OpenAI(base_url=settings.model_base_url, api_key=settings.model_api_key, timeout=60)
    request = {
        "model": settings.model_name,
        "messages": [{"role": "user", "content": direct_prompt(question, schema, evidence)}],
    }
    # 部分推理模型不接受 temperature；默认省略，仅在明确配置时传入。
    if settings.model_temperature is not None:
        request["temperature"] = settings.model_temperature
    response = client.chat.completions.create(**request)
    return (response.choices[0].message.content or "").strip().removeprefix("```sql").removesuffix("```").strip()


def _normalized_rows(rows: list[list[object]]) -> Counter[tuple[str, ...]]:
    """把结果行标准化为不依赖顺序的多重集合。

    参数:
        rows: SQL 执行器返回的二维结果行列表。
    返回:
        以序列化行元组为键的 Counter；保留重复行数量。
    """
    return Counter(tuple(json.dumps(value, sort_keys=True, default=str) for value in row) for row in rows)


def run_eval(cases_path: Path, settings: Settings, *, provider: str = "model",
             evidence_mode: str = "none", output: Path | None = None) -> dict:
    """逐题生成、执行并比较预测 SQL，形成 smoke 结果。

    参数:
        cases_path: JSON 题集文件路径，题目需包含 db_id、question 和 SQL。
        settings: 数据库目录、模型连接及执行资源限制。
        provider: ``model`` 调用真实模型；``fixture`` 直接使用标准 SQL。
        evidence_mode: ``none`` 不提供标注证据；``oracle`` 提供题目 evidence。
        output: 可选 JSON 输出路径；为空时只返回内存结果。
    返回:
        包含配置、逐题记录、有效 SQL 率、执行匹配率和延迟的字典。
    异常:
        ValueError: provider 或 evidence_mode 取值无效。
    说明:
        fixture 仅验证管线；当前行数上限和比较器不等于官方 BIRD EX。
    """
    if provider not in {"model", "fixture"}:
        raise ValueError("provider must be model or fixture")
    if evidence_mode not in {"none", "oracle"}:
        raise ValueError("evidence_mode must be none or oracle")
    case_bytes = cases_path.read_bytes()
    cases = json.loads(case_bytes)
    registry = DatabaseRegistry(settings.data_dir)
    records = []
    for index, case in enumerate(cases):
        started = time.monotonic()
        database_id = case["db_id"]
        path = registry.get(database_id)
        schema = format_schema(inspect_sqlite(path))
        evidence = case.get("evidence", "") if provider == "model" and evidence_mode == "oracle" else ""
        record = {"index": index, "database_id": database_id, "question": case["question"],
                  "evidence_used": bool(evidence), "valid_sql": False, "execution_match": False}
        try:
            # fixture 是管线自检；只有 model 模式才实际调用 Text2SQL 模型。
            prediction = case["SQL"] if provider == "fixture" else generate_sql(case["question"], schema, evidence, settings)
            record["predicted_sql"] = prediction
            actual = execute_readonly(path, prediction, database_id=database_id, max_rows=settings.max_rows,
                                      timeout_ms=settings.query_timeout_ms, max_response_bytes=settings.max_response_bytes)
            record["valid_sql"] = True
            gold = execute_readonly(path, case["SQL"], database_id=database_id, max_rows=settings.max_rows,
                                    timeout_ms=settings.query_timeout_ms, max_response_bytes=settings.max_response_bytes)
            # 同一列位置的值需要相等；行顺序不影响结果，重复行仍计入。
            record["execution_match"] = _normalized_rows(actual.rows) == _normalized_rows(gold.rows)
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        record["latency_ms"] = round((time.monotonic() - started) * 1000, 3)
        records.append(record)
    latencies = sorted(record["latency_ms"] for record in records)
    # 若所有题目都没拿到预测 SQL，报告模型不可用而非虚构 0% 准确率。
    model_unavailable = provider == "model" and bool(records) and all("predicted_sql" not in item for item in records)
    result = {
        "dataset": str(cases_path), "dataset_sha256": hashlib.sha256(case_bytes).hexdigest(),
        "provider": provider, "status": "model_unavailable" if model_unavailable else "completed",
        "evidence_mode": evidence_mode if provider == "model" else None,
        "model": settings.model_name if provider == "model" else None,
        "model_base_url": settings.model_base_url if provider == "model" else None,
        "model_temperature": settings.model_temperature if provider == "model" else None,
        "prompt_version": PROMPT_VERSION if provider == "model" else None,
        "count": len(records), "max_rows": settings.max_rows, "query_timeout_ms": settings.query_timeout_ms,
        "valid_sql_rate": sum(item["valid_sql"] for item in records) / len(records) if records and not model_unavailable else None,
        "execution_accuracy": sum(item["execution_match"] for item in records) / len(records) if records and not model_unavailable else None,
        "p50_latency_ms": statistics.median(latencies) if latencies else None,
        "p95_latency_ms": latencies[min(len(latencies) - 1, int(len(latencies) * .95))] if latencies else None,
        "records": records,
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
