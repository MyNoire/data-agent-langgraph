"""集中定义环境变量配置；本地开发也可从项目根目录的 .env 读取。"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行参数，统一使用 QUERYPILOT_ 前缀并校验资源上限。"""

    model_config = SettingsConfigDict(env_prefix="QUERYPILOT_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    model_base_url: str = "http://127.0.0.1:8000/v1"
    model_name: str = "Qwen/Qwen2.5-Coder-7B-Instruct"
    model_api_key: str = "local"
    model_temperature: float | None = Field(default=None, ge=0, le=2)
    query_timeout_ms: int = Field(default=3000, ge=100, le=60000)
    max_rows: int = Field(default=100, ge=1, le=10000)
    max_response_bytes: int = Field(default=262144, ge=1024, le=10485760)


def get_settings() -> Settings:
    """读取当前进程的配置；测试可以直接构造 Settings 覆盖默认值。"""
    return Settings()
