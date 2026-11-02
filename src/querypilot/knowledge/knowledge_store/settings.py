"""按需读取知识库秘密配置；不会给原 Settings 添加 PostgreSQL 前置。"""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Role = Literal["admin", "import", "read"]
ROLE_NAMES = {"import": "querypilot_kb_importer", "read": "querypilot_kb_reader"}
DATABASE_NAMES = {"querypilot_knowledge", "querypilot_knowledge_test"}


class KnowledgeStorageError(ValueError):
    """保留异常 cause，通过受控消息对外报告；不打印底层连接或 SQL 错误。"""

    def __init__(self, error_type: str, message: str, *, database_outcome: str | None = None):
        """输入：受控类别/消息/已知结果；输出：异常；副作用：无。"""
        super().__init__(message)
        self.error_type = error_type
        self.database_outcome = database_outcome

    @property
    def exit_code(self) -> int:
        """输入：当前异常；输出：CLI 的 1 或 3；副作用：无。"""
        return 3 if self.error_type in {
            "connection_failed", "permission_denied", "lock_timeout",
            "statement_timeout", "database_error", "commit_outcome_unknown",
        } else 1


class KnowledgeStorageSettings(BaseSettings):
    """输入：环境/.env；输出：秘密 DSN 和有界超时；异常由入口安全转译。"""

    model_config = SettingsConfigDict(env_prefix="QUERYPILOT_KNOWLEDGE_", env_file=".env", extra="ignore")
    admin_dsn: SecretStr | None = None
    import_dsn: SecretStr | None = None
    read_dsn: SecretStr | None = None
    connect_timeout_s: int = Field(default=5, ge=1, le=60)
    lock_timeout_ms: int = Field(default=5000, ge=100, le=60000)
    statement_timeout_ms: int = Field(default=30000, ge=1000, le=600000)

    def connection_info(self, role: Role) -> dict[str, str]:
        """读取并校验指定账号的连接参数。

        参数：role 为 admin/import/read。
        返回：仅供连接的私有参数字典，禁止打印或序列化。
        异常：KnowledgeStorageError；原解析 cause 保留但不对外输出。
        副作用：无连接；只有知识入口才加载 psycopg optional extra。
        """
        secret = getattr(self, role + "_dsn")
        if secret is None:
            raise KnowledgeStorageError("configuration_error", "缺少当前命令所需知识库 DSN")
        try:
            from psycopg.conninfo import conninfo_to_dict
            info = conninfo_to_dict(secret.get_secret_value())
        except Exception as exc:
            # libpq 的 DSN 解析异常可能含原连接串；仅在此解析边界安全转译并保留 cause。
            raise KnowledgeStorageError("configuration_error", "知识库驱动缺失或 DSN 不合法") from exc
        if not info.get("host") or not info.get("user") or info.get("dbname") not in DATABASE_NAMES:
            raise KnowledgeStorageError("configuration_error", "需显式指定主机、账号和专用知识数据库")
        if any(c in info["host"] for c in ", ") or "service" in info:
            raise KnowledgeStorageError("configuration_error", "暂不支持服务别名或多主机 DSN")
        if role != "admin" and info["user"] != ROLE_NAMES[role]:
            raise KnowledgeStorageError("configuration_error", "运行账号名称不符合角色契约")
        if role == "admin" and info["user"] in ROLE_NAMES.values():
            raise KnowledgeStorageError("configuration_error", "管理账号必须与运行账号分离")
        return info

    def bootstrap_info(self) -> dict[Role, dict[str, str]]:
        """输入：当前配置；输出：同目标三个私有参数；异常：目标/密码错误；无连接。"""
        infos = {role: self.connection_info(role) for role in ("admin", "import", "read")}
        endpoints = {(v["host"], v.get("port", "5432"), v["dbname"], v.get("hostaddr")) for v in infos.values()}
        if len(endpoints) != 1 or any(not infos[r].get("password") for r in ("import", "read")):
            raise KnowledgeStorageError("configuration_error", "初始化需同目标的独立账号与本机密码")
        return infos
