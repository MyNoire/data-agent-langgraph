"""原 direct-v2 提示词及模型适配器。"""

from dataclasses import dataclass

from openai import OpenAI
from querypilot.runtime.api import Settings

PROMPT_VERSION = "direct-v2"


@dataclass(frozen=True)
class GeneratedSql:
    """一次 SQL 生成的结果：候选 SQL，以及模型接口报告的 token 用量。

    prompt_tokens 是提示词的 token 数，completion_tokens 是模型输出的 token 数（推理模型的
    推理 token 也算在里面）；接口没有返回用量时两项为 None。
    """

    sql: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


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


def generate_sql(question: str, schema: str, evidence: str, settings: Settings) -> GeneratedSql:
    """调用 OpenAI-compatible 服务生成候选 SQL。

    参数:
        question: 用户自然语言问题。
        schema: 当前数据库的表列说明文本。
        evidence: 可选业务证据；生产路径和无证据基线传空字符串。
        settings: 模型地址、名称、密钥和可选温度配置。
    返回:
        GeneratedSql：去掉首尾 Markdown SQL 代码围栏后的模型输出文本，以及接口报告的 token 用量。
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
    sql = (response.choices[0].message.content or "").strip().removeprefix("```sql").removesuffix("```").strip()
    usage = response.usage
    return GeneratedSql(sql, getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))

