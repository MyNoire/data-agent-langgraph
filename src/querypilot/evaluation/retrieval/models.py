"""检索题集的数据形状。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from querypilot.knowledge.api import KnowledgeScope


class CaseModel(BaseModel):
    """题集模型的公共配置：拒绝多余字段。"""

    model_config = ConfigDict(extra="forbid")


class RelevantColumn(CaseModel):
    """Relevant Set 中的一列，名称取 Corpus 里的写法。"""

    table_name: str
    column_name: str


class RetrievalCase(CaseModel):
    """一道检索评测题：英文原题、所属 Knowledge Scope 和 Relevant Set。不含金标准 SQL 与 evidence。"""

    question_id: int
    db_id: str
    difficulty: str
    question: str
    frozen: bool
    scope: KnowledgeScope
    relevant_tables: list[str]
    relevant_columns: list[RelevantColumn]


class RetrievalCaseSet(CaseModel):
    """一份检索题集。"""

    format_version: Literal["querypilot-retrieval-cases-v1"] = "querypilot-retrieval-cases-v1"
    cases: list[RetrievalCase]
