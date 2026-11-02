"""存储路径入口先读源；读取端口保持有界和资源所有权。"""

import pytest

from querypilot.knowledge.api import KnowledgeStorageError, KnowledgeStorageSettings, import_snapshot, verify_import
from querypilot.knowledge.knowledge_store._read_session import ReadSession


@pytest.mark.parametrize("operation", [import_snapshot, verify_import])
def test_path_operations_reject_bad_source_before_database(tmp_path, operation):
    """输入：不存在的路径、无DSN配置；返回：来源错误优先；不连接数据库。"""
    with pytest.raises(KnowledgeStorageError) as caught:
        operation(tmp_path / "missing", KnowledgeStorageSettings(_env_file=None))
    assert caught.value.error_type == "source_invalid"


def test_read_session_fetches_incrementally_and_refuses_reuse(r2_corpus):
    """输入：受控游标替身；返回：逐行限额、参数绑定、关闭后拒绝；不宣称PG事务已验收。"""
    from querypilot.knowledge.api import KnowledgeReadError, KnowledgeScope, ReadLimits, ReadView, load_snapshot

    class Cursor:
        """只支持逐行读取；不提供fetchall，防止把无界加载隐藏在测试里。"""

        def __init__(self):
            """初始化三行受控数据；不连接数据库。"""
            self.rows = iter([{"value": "x" * 10}] * 3)
            self.closed = False

        def execute(self, statement, parameters):
            """保存原绑定参数供断言；不计算SQL。"""
            assert statement == "SELECT trusted_source WHERE corpus_id=%s"
            assert parameters == ("fixed",)

        def fetchone(self):
            """返回：下一行或空；无外部I/O。"""
            return next(self.rows, None)

        def close(self):
            """标记游标资源已关闭。"""
            self.closed = True

    class Connection:
        """只允许命名游标创建，证明客户端不通过execute预缓冲全部行。"""

        def cursor(self, *, name):
            """返回：本次独立游标；命名保证使用有界服务端读取形式。"""
            assert name.startswith("qp_read_")
            self.last = Cursor()
            return self.last

    held = load_snapshot(r2_corpus)
    scope = KnowledgeScope(corpus_id=held.manifest.corpus_id, database_id="demo",
                           schema_version=held.manifest.identity_descriptor.schema_versions["demo"])
    connection = Connection()
    session = ReadSession(connection, ReadView(scope=scope, counts=held.manifest.counts, search_bytes=1),
                          ReadLimits(max_result_bytes=50))
    with pytest.raises(KnowledgeReadError) as exceeded:
        session.fetch_rows("SELECT trusted_source WHERE corpus_id=%s", ("fixed",), 25)
    assert exceeded.value.error_type == "capacity_exceeded"
    assert connection.last.closed
    session._close()
    with pytest.raises(KnowledgeReadError):
        session.fetch_one("SELECT trusted_source WHERE corpus_id=%s", ("fixed",))
