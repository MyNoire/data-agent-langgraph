"""配置、事件和私有结果共用的凭据清理；不代替领域数据分级。"""

import dataclasses
from base64 import b64encode
import re
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit
from uuid import UUID

from pydantic import BaseModel, SecretStr

REDACTED = "[REDACTED]"
_URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>\"']+")
_CONNECTION = re.compile(r"\b(?:host|hostaddr|port|dbname|database|user|password|passfile|sslmode)\s*=\s*(?:'[^']*'|\"[^\"]*\"|[^\s;,]+)", re.I)
_ASSIGNMENT = re.compile(r"\b(?:api[_-]?key|password|passwd|secret|token|authorization)\s*[:=]\s*(?:'[^']*'|\"[^\"]*\"|[^\s;,]+)", re.I)
_SECRET_KEYS = frozenset({"dsn", "api_key", "model_api_key", "password", "passwd", "secret", "token", "authorization", "connection_string"})
CONFIG_FIELDS = frozenset({
    "provider", "mode", "model_name", "model_base_url", "model_temperature", "temperature",
    "prompt_version", "graph_version", "builder_version", "query_timeout_ms", "max_rows",
    "max_response_bytes", "evidence_mode", "dataset_sha256", "input_sha256", "source_sha256",
    "semantic_sha256", "corpus_id", "database_id", "schema_version", "scope", "profile_version",
    "renderer_version", "config_sha256", "input_hashes", "dataset_hash", "dataset_version", "method", "rerank",
    "workers",
})


def public_endpoint(value: str) -> str:
    """去除 URL 的用户信息、查询参数及片段；无效地址返回受控标记。"""
    try:
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.hostname:
            return REDACTED
        if parsed.scheme.lower() in {"postgres", "postgresql"}:
            return REDACTED
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        netloc = f"{host}:{parsed.port}" if parsed.port else host
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    except (ValueError, TypeError):
        return REDACTED


def _private_url(value: str, secrets: tuple[str, ...]) -> str:
    """只清理带凭据或秘密参数的正文 URL，保留普通业务地址的完整含义。"""
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() in {"postgres", "postgresql"}:
            return REDACTED
        secret_parameter = any(re.search(r"(?:api[_-]?key|token|password|secret|authorization|access[_-]?key|credential|signature|^sig$)", key, re.I)
                               for key, _ in parse_qsl(parsed.query, keep_blank_values=True))
        secret_fragment = bool(re.search(r"(?:api[_-]?key|token|password|secret|authorization)\s*[:=]", parsed.fragment, re.I))
        explicit_secret = any(secret and secret in unquote(value) for secret in secrets)
        if parsed.username is not None or parsed.password is not None or secret_parameter or secret_fragment or explicit_secret:
            return public_endpoint(value)
        return value
    except (ValueError, TypeError):
        return REDACTED


def clean_text(value: str, secrets: tuple[str, ...] = ()) -> str:
    """清理文字中的显式秘密、连接串及带凭据地址，保留业务正文和普通 URL。"""
    value = _URL.sub(lambda match: _private_url(match.group(), secrets), value)
    if re.search(r"\b(?:host|hostaddr|dbname|sslmode)\s*=", value, re.I):
        value = _CONNECTION.sub(REDACTED, value)
    value = _ASSIGNMENT.sub(REDACTED, value)
    expanded = set(s for s in secrets if isinstance(s, str) and s)
    for secret in tuple(expanded):
        try:
            password = urlsplit(secret).password
            if password:
                expanded.add(unquote(password))
                expanded.add(password)
        except ValueError:
            pass
        match = re.search(r"\bpassword\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|([^\s;,]+))", secret, re.I)
        if match:
            expanded.update(group for group in match.groups() if group)
    for secret in sorted(expanded, key=len, reverse=True):
        value = value.replace(secret, REDACTED)
    return value


def clean_value(value: Any, secrets: tuple[str, ...] = (), *, key: str = "") -> Any:
    """将领域结果转换为可写 JSON 的结构，仅清除凭据，保留原字段名与正文。

    bytes 仅在归档副本中转成带 base64 编码标识的局部值；调用方原始对象不变。
    非 UTF8 字节通过 surrogateescape 保留，除凭据清理外可逐字节恢复。
    """
    if key.lower() in _SECRET_KEYS or key.lower().endswith("_dsn") or isinstance(value, SecretStr):
        return REDACTED
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    elif isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    if isinstance(value, Mapping):
        return {str(k): clean_value(v, secrets, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_value(item, secrets) for item in value]
    if isinstance(value, bytes):
        # 先清理可读凭据再编码；不能把 base64 当作隐藏秘密的手段。
        safe_bytes = clean_text(value.decode("utf-8", errors="surrogateescape"), secrets).encode(
            "utf-8", errors="surrogateescape")
        return {"$querypilot_binary": {"encoding": "base64", "data": b64encode(safe_bytes).decode("ascii")}}
    if isinstance(value, str):
        if key.lower() in {"model_base_url", "base_url", "endpoint"}:
            return clean_text(public_endpoint(value), secrets)
        return clean_text(value, secrets)
    if isinstance(value, (Path, UUID, Enum)):
        return clean_text(str(value.value if isinstance(value, Enum) else value), secrets)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (int, float, bool)):
        return value
    raise TypeError("runtime result must contain JSON-compatible values")


def safe_config(parameters: Mapping[str, Any] | None, settings: Any,
                versions: dict[str, Any], secrets: tuple[str, ...]) -> dict[str, Any]:
    """从白名单构造配置，不序列化整个 Settings 或未获准字段。"""
    selected = {key: getattr(settings, key) for key in (
        "model_name", "model_base_url", "model_temperature", "query_timeout_ms", "max_rows", "max_response_bytes",
    ) if hasattr(settings, key)}
    selected.update({key: value for key, value in (parameters or {}).items() if key in CONFIG_FIELDS})
    if "scope" in selected:
        scope = selected["scope"]
        if isinstance(scope, BaseModel):
            scope = scope.model_dump()
        elif dataclasses.is_dataclass(scope):
            scope = dataclasses.asdict(scope)
        selected["scope"] = {key: value for key, value in scope.items()
                             if key in {"corpus_id", "database_id", "schema_version"}} if isinstance(scope, Mapping) else None
    for field in ("input_hashes",):
        if field in selected:
            # 摘要集合只接受 SHA256，不保存任意输入文件名和正文。
            values = selected[field]
            selected[field] = sorted(v for v in (values.values() if isinstance(values, Mapping) else values)
                                     if isinstance(v, str) and re.fullmatch(r"[a-fA-F0-9]{64}", v)) if isinstance(values, (Mapping, list, tuple)) else []
    return {"format_version": "querypilot-safe-config-v1", "versions": clean_value(versions, secrets),
            "parameters": clean_value(selected, secrets)}
