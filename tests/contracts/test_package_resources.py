"""包内迁移资源与无仓库安装环境验证；不连接数据库。"""

from hashlib import sha256
from importlib.resources import files
from importlib.util import module_from_spec, spec_from_file_location
from inspect import getsourcefile
from pathlib import Path
import shutil

from querypilot.runtime.api import source_version

MIGRATION_SHA256 = "46fc097b474cd02136cd1e09118debafef28110dde643ab4015e0712863d3b5c"


def test_migration_resource_has_original_bytes():
    """通过标准包资源入口读取SQL，确认迁移整理没有改变数据库版本字节。"""
    resource = files("querypilot.knowledge.knowledge_store").joinpath("migrations/001_corpus.sql")
    assert resource.is_file()
    assert sha256(resource.read_bytes()).hexdigest() == MIGRATION_SHA256


def test_version_provider_without_git_does_not_assume_source_depth(tmp_path, monkeypatch):
    """把真实版本提供者放进任意安装深度，默认定位应明确返回不可得。"""
    destination = tmp_path / "arbitrary" / "deep" / "installation" / "version.py"
    destination.parent.mkdir(parents=True)
    shutil.copyfile(Path(getsourcefile(source_version)), destination)
    module_spec = spec_from_file_location("querypilot_installed_version_probe", destination)
    module = module_from_spec(module_spec)
    module_spec.loader.exec_module(module)

    def prohibit_git(*args, **kwargs):
        """没有仓库就不应尝试调用Git，更不能把它假设成干净工作树。"""
        raise AssertionError("no-Git install must not invoke Git")

    monkeypatch.setattr(module.subprocess, "run", prohibit_git)
    actual = module.source_version()
    assert actual["version_source"] == "unavailable"
    assert actual["git_revision"] is None
    assert actual["worktree_dirty"] is None
    assert actual["source_fingerprint"] is None
