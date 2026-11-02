"""完整来源核对的公开行为，不把私有名称匹配器当作能力入口。"""

import json

import pytest

from querypilot.knowledge.knowledge_catalog.api import EnrichedCatalog, SourceVerificationError, verify_sources


@pytest.mark.parametrize("mutation", [None, "bytes", "record", "cells", "binding"])
def test_sources_verify_actual_bytes_records_and_bindings(r2_corpus, mutation):
    """输入：原创已批准包及污染；返回：完整通过或拒绝；仅修改内存副本。"""
    bundle = r2_corpus / "inputs" / "bundle"
    enriched = EnrichedCatalog.model_validate_json((bundle / "enriched_catalog.json").read_bytes())
    sources = {source.source_file: (bundle / "derived_source" / source.source_file).read_bytes()
               for source in enriched.sources}
    if mutation == "bytes":
        sources[next(iter(sources))] += b"\n"
    elif mutation == "record":
        enriched.descriptions[0].source.record_index += 1
    elif mutation == "cells":
        enriched.descriptions[0].description = "changed"
    elif mutation == "binding":
        enriched.descriptions[0].column_name = "wrong_column"
    if mutation:
        with pytest.raises(SourceVerificationError):
            verify_sources(enriched, sources)
    else:
        result = verify_sources(enriched, sources)
        assert result.status == "verified"
        assert result.source_count == len(sources)
        assert result.record_count > 0


def test_curate_owns_one_run_or_reuses_callers_run(tmp_path):
    """输入：原创批准修正；返回：直接调用和嵌套调用各一份运行；不双重关闭。"""
    from test_catalog_corrections import _inputs
    from querypilot.knowledge.api import curate_catalog
    from querypilot.runtime.api import task_run
    base, source, plan = _inputs(tmp_path)
    first = curate_catalog(base, source, plan, tmp_path / "first")
    with task_run("knowledge_catalog", command="knowledge catalog curate") as context:
        second = curate_catalog(base, source, plan, tmp_path / "second", run_context=context)
        assert not context._closed
        context.complete(second)
    assert first == second
    assert len(list((tmp_path / "artifacts/runs/knowledge_catalog").rglob("run.json"))) == 2
