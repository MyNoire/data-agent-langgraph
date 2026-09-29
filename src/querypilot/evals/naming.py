"""为评测原始结果生成可辨认且不含凭据的文件名。"""

import re
from datetime import datetime, timezone
from pathlib import Path

from querypilot.evals.baseline import PROMPT_VERSION


def result_path(cases: Path, provider: str, model: str, evidence_mode: str,
                *, timestamp: datetime | None = None) -> Path:
    """生成包含题集、评测模式与时间的原始结果路径。

    参数:
        cases: 题集 JSON 路径；文件名用于区分 12 题和 100 题。
        provider: model、fixture 或 graph。
        model: 模型配置名称；仅在 model 模式写入文件名。
        evidence_mode: none 或 oracle；仅在 model 模式写入文件名。
        timestamp: 可选 UTC 时间，主要用于确定性测试；为空时取当前时间。
    返回:
        results 目录下不含密钥的 JSON 路径；创建文件时仍会检查重名。
    """
    dataset = re.sub(r"[^a-z0-9]+", "-", cases.stem.lower()).strip("-") or "cases"
    stamp = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp_text = stamp.strftime("%Y%m%dT%H%M%S%fZ")
    if provider == "fixture":
        run_name = "fixture"
    else:
        model_slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")[:60] or "unknown"
        if provider == "graph":
            run_name = f"graph-single-v1__model-{model_slug}__{PROMPT_VERSION}__evidence-none"
        else:
            run_name = f"model-{model_slug}__{PROMPT_VERSION}__evidence-{evidence_mode}"
    return Path("results") / f"{dataset}__{run_name}__{stamp_text}.json"
