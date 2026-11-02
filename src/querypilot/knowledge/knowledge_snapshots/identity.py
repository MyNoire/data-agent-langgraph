"""版本化编码与身份函数；不读取文件，不改写知识内容。"""

from hashlib import sha256
import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from querypilot.knowledge.knowledge_catalog.api import CatalogSnapshot

NAMESPACE = uuid5(NAMESPACE_URL, "urn:querypilot:knowledge-corpus:v1")


def canonical_json(value: Any) -> bytes:
    """生成 C(x)。

    参数: value 为可序列化对象，数组顺序有语义。
    返回: 无 BOM、无末尾换行的 UTF-8 原字节。
    异常: ValueError/TypeError 表示非有限数或不可编码内容。
    副作用: 无；不规范化字符串，不排序数组。
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(raw: bytes) -> str:
    """计算字节 SHA-256。输入：明确选定的字节；输出：64 位小写摘要；无副作用。"""
    return sha256(raw).hexdigest()


def schema_version(catalog: CatalogSnapshot) -> str:
    """计算已收集结构身份。

    参数: catalog 为原始结构，包含所有列属性及有序键成员。
    返回: 规范结构副本的 SHA-256。
    副作用: 无；排序仅作用于 model_dump 副本，定位和原数组保持不变。
    """
    payload = catalog.model_dump(mode="json")
    payload["tables"].sort(key=lambda table: table["name"])
    for table in payload["tables"]:
        table["columns"].sort(key=lambda column: (column["ordinal"], column["name"]))
        table["foreign_keys"].sort(key=lambda fk: fk["foreign_key_id"])
    return digest(canonical_json(payload))


def content_hash(title: str, content: str) -> str:
    """计算正文身份。输入：原标题/正文；输出：H(C([title,content]))；无副作用。"""
    return digest(canonical_json([title, content]))


def object_id(kind: str, corpus_id: str, database_id: str, key: str) -> str:
    """生成持久对象身份。

    参数: kind 为 document/chunk/relation，其他参数为快照、库及完整原 key。
    返回: 固定 namespace 下的 UUIDv5 字符串。
    异常: ValueError 表示不支持的对象类型。
    副作用: 无；不拼接或拆分真实标识符。
    """
    if kind not in {"document", "chunk", "relation"}:
        raise ValueError("不支持的对象类型")
    return str(uuid5(NAMESPACE, canonical_json([kind, corpus_id, database_id, key]).decode("utf-8")))


def json_file(value: Any) -> bytes:
    """编码新 JSON 文件。输入：对象；输出：C(x)+LF；异常同 C(x)；无副作用。"""
    return canonical_json(value) + b"\n"


def jsonl_file(rows: list[dict[str, Any]], key: str) -> bytes:
    """编码集合 JSONL。输入：行与原 key 字段；输出：按 key 排序的规范行；不改行。"""
    return b"".join(json_file(row) for row in sorted(rows, key=lambda row: row[key]))


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """严格解析属性。输入：JSON 属性对；输出：字典；异常：重复属性 ValueError。"""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("重复 JSON 属性")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    """拒绝非 JSON 数值。输入：常量名称；输出：无；始终抛 ValueError。"""
    raise ValueError("禁止非有限 JSON 数值")


def parse_json(raw: bytes) -> Any:
    """严格读取 JSON。

    参数: raw 为 UTF-8 原字节。
    返回: JSON 数据；不把内容当作指令。
    异常: ValueError/UnicodeError 表示重复属性、非有限数或无效 UTF-8。
    副作用: 无；不回写来源。源 BOM 兼容既有 UTF-8-sig，新产物规范字节另核对。
    """
    value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_pairs,
                       parse_constant=_reject_constant)
    # 1e999 也可能解析成 inf，parse_constant 无法捕获它。
    canonical_json(value)
    return value
