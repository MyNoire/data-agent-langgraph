"""Generate the editable QueryPilot architecture diagram.

Green denotes the verified Phase 0 path. Blue denotes the planned system.
Run with: python scripts/generate_architecture.py
"""

from pathlib import Path
from xml.etree import ElementTree as ET


OUT = Path(__file__).resolve().parents[1] / "docs" / "querypilot-architecture.drawio"


def style(fill: str, stroke: str, *, size: int = 16, dashed: bool = False) -> str:
    return (
        "rounded=1;arcSize=12;whiteSpace=wrap;html=1;align=center;"
        "verticalAlign=middle;spacing=10;fontFamily=Microsoft YaHei;"
        f"fontSize={size};fontColor=#17324D;fillColor={fill};"
        f"strokeColor={stroke};strokeWidth=2;"
        + ("dashed=1;dashPattern=6 4;" if dashed else "")
    )


root = ET.Element("mxfile", host="drawio", version="26.0.0")
diagram = ET.SubElement(root, "diagram", name="QueryPilot 总体架构", id="querypilot-architecture")
model = ET.SubElement(
    diagram,
    "mxGraphModel",
    dx="1800",
    dy="1100",
    grid="1",
    gridSize="10",
    page="1",
    pageScale="1",
    pageWidth="1860",
    pageHeight="1080",
    math="0",
    shadow="0",
)
graph_root = ET.SubElement(model, "root")
ET.SubElement(graph_root, "mxCell", id="0")
ET.SubElement(graph_root, "mxCell", id="1", parent="0")


def box(id: str, label: str, x: int, y: int, w: int, h: int, *, parent: str = "1", kind: str = "planned", font: int = 16) -> None:
    palettes = {
        "current": ("#E8F6EC", "#31945C"),
        "planned": ("#EAF3FF", "#477DBB"),
        "support": ("#F0EDFA", "#8770B3"),
        "data": ("#FFF3E4", "#C58B3A"),
        "neutral": ("#F5F7FA", "#9AA8B6"),
    }
    fill, stroke = palettes[kind]
    cell = ET.SubElement(
        graph_root,
        "mxCell",
        id=id,
        value=label,
        style=style(fill, stroke, size=font, dashed=kind == "planned"),
        vertex="1",
        parent=parent,
    )
    ET.SubElement(cell, "mxGeometry", x=str(x), y=str(y), width=str(w), height=str(h), attrib={"as": "geometry"})


def text(id: str, label: str, x: int, y: int, w: int, h: int, *, parent: str = "1", size: int = 16, color: str = "#26445E", bold: bool = False) -> None:
    cell = ET.SubElement(
        graph_root,
        "mxCell",
        id=id,
        value=label,
        style=(
            "text;html=1;whiteSpace=wrap;align=left;verticalAlign=middle;"
            f"fontFamily=Microsoft YaHei;fontSize={size};fontColor={color};"
            + ("fontStyle=1;" if bold else "")
        ),
        vertex="1",
        parent=parent,
    )
    ET.SubElement(cell, "mxGeometry", x=str(x), y=str(y), width=str(w), height=str(h), attrib={"as": "geometry"})


def panel(id: str, label: str, x: int, y: int, w: int, h: int, *, fill: str, stroke: str, header: int = 38, font: int = 18, parent: str = "1") -> None:
    cell = ET.SubElement(
        graph_root,
        "mxCell",
        id=id,
        value=label,
        style=(
            "swimlane;html=1;rounded=1;arcSize=8;horizontal=1;"
            f"startSize={header};container=1;pointerEvents=0;whiteSpace=wrap;"
            f"fontFamily=Microsoft YaHei;fontStyle=1;fontSize={font};"
            f"fontColor=#17324D;fillColor={fill};swimlaneFillColor={fill};"
            f"strokeColor={stroke};strokeWidth=2;"
        ),
        vertex="1",
        parent=parent,
    )
    ET.SubElement(cell, "mxGeometry", x=str(x), y=str(y), width=str(w), height=str(h), attrib={"as": "geometry"})


def arrow(id: str, source: str, target: str, *, from_side: str = "right", to_side: str = "left", dashed: bool = False, color: str = "#6C8195", label: str = "", waypoints: tuple[tuple[int, int], ...] = ()) -> None:
    sides = {
        "right": (1, 0.5), "left": (0, 0.5),
        "top": (0.5, 0), "bottom": (0.5, 1),
    }
    sx, sy = sides[from_side]
    tx, ty = sides[to_side]
    cell = ET.SubElement(
        graph_root,
        "mxCell",
        id=id,
        value=label,
        style=(
            "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;"
            "jettySize=auto;html=1;endArrow=blockThin;endFill=1;"
            f"strokeColor={color};strokeWidth=2;"
            f"exitX={sx};exitY={sy};exitDx=0;exitDy=0;"
            f"entryX={tx};entryY={ty};entryDx=0;entryDy=0;"
            + ("dashed=1;dashPattern=6 4;" if dashed else "")
        ),
        edge="1",
        parent="1",
        source=source,
        target=target,
    )
    geometry = ET.SubElement(cell, "mxGeometry", relative="1", attrib={"as": "geometry"})
    if waypoints:
        points = ET.SubElement(geometry, "Array", attrib={"as": "points"})
        for x, y in waypoints:
            ET.SubElement(points, "mxPoint", x=str(x), y=str(y))


text("title", "QueryPilot｜面向业务分析的多轮 Text2SQL Agent", 40, 20, 1740, 58, size=30, bold=True)
text("subtitle", "整体架构与实施状态 · 绿色 = Phase 0 已实现 · 蓝色虚线 = 后续目标 · 紫色 = 状态与治理 · 橙色 = 数据/评测", 40, 80, 1740, 36, size=17)

# Left: the verified Phase 0 path. No LangGraph or RAG is implied here.
panel("p0", "Phase 0｜保留作对照：单轮 direct-prompt baseline", 40, 140, 550, 860, fill="#F8FCF9", stroke="#31945C")
box("p0_entry", "API / CLI 请求<br>指定 database_id", 80, 65, 470, 72, parent="p0", kind="current")
box("p0_schema", "读取注册的 SQLite Schema<br>构造全量 Schema prompt", 80, 170, 470, 82, parent="p0", kind="current")
box("p0_llm", "OpenAI-compatible 模型<br>直接生成 SQL", 80, 285, 470, 82, parent="p0", kind="current")
box("p0_guard", "sqlglot AST 安全校验<br>只读语句 / LIMIT", 80, 400, 470, 82, parent="p0", kind="current")
box("p0_exec", "SQLite 只读执行器<br>超时与行数限制", 80, 515, 470, 82, parent="p0", kind="current")
box("p0_eval", "离线评测：pytest + 固定 BIRD 12 题 smoke set<br>结果、错误与延迟记录", 80, 640, 470, 96, parent="p0", kind="current")
text("p0_note", "Phase 1 已新增线性 Graph；选库、RAG、记忆与修复仍待实现。", 80, 770, 470, 56, parent="p0", size=15, color="#41624B")
for i, (a, b) in enumerate([
    ("p0_entry", "p0_schema"), ("p0_schema", "p0_llm"),
    ("p0_llm", "p0_guard"), ("p0_guard", "p0_exec"),
    ("p0_exec", "p0_eval"),
]):
    arrow(f"p0_flow_{i}", a, b, from_side="bottom", to_side="top", color="#31945C", dashed=i == 4)

# Right: target architecture. All blue boxes are planned unless also shown on the left.
panel("target", "目标架构｜LangGraph 业务分析助手（规划）", 630, 140, 1150, 860, fill="#F9FBFF", stroke="#477DBB")
box("request", "业务问题 + 会话", 40, 65, 260, 70, parent="target", kind="neutral")
box("api", "FastAPI 入口<br>身份 / 授权 / 流式响应", 345, 65, 320, 70, parent="target")
box("runtime", "Runtime Context（每次调用注入）<br>用户权限 / 数据源配置", 710, 65, 390, 70, parent="target", kind="support")
arrow("req_api", "request", "api")
arrow("api_router", "api", "router", from_side="bottom", to_side="top", waypoints=((855, 300),))

panel("graph", "LangGraph 父图｜路由、上下文预算、子图交接与人工确认", 40, 180, 1070, 440, parent="target", fill="#F3F7FF", stroke="#477DBB", header=42, font=18)
box("router", "数据库路由<br>在授权目录中检索 / 澄清", 40, 65, 290, 90, parent="graph")
box("context", "Context Builder<br>本轮问题 + 相关历史 + 证据", 390, 65, 290, 90, parent="graph", kind="support")
box("checkpoint", "持久化 Checkpointer<br>恢复线程与执行状态", 740, 65, 290, 90, parent="graph", kind="support")
box("retrieval", "Schema Retrieval 子图<br>稀疏 + 向量 + Reranker<br>外键图扩展 / 来源追踪", 40, 220, 290, 150, parent="graph")
box("sql", "SQL 子图<br>生成 → AST 校验 → 只读执行<br>错误分类 → 有限修复", 390, 220, 290, 150, parent="graph")
box("analysis", "Analysis 子图<br>基于已验证结果计算 / 解释<br>生成可追溯结论", 740, 220, 290, 150, parent="graph")
arrow("router_retrieval", "router", "retrieval", from_side="bottom", to_side="top")
arrow("context_sql", "context", "sql", from_side="bottom", to_side="top", dashed=True, color="#8770B3")
arrow("checkpoint_analysis", "checkpoint", "analysis", from_side="bottom", to_side="top", dashed=True, color="#8770B3")
arrow("retrieval_sql", "retrieval", "sql")
arrow("sql_analysis", "sql", "analysis")

box("report", "带 SQL / 结果来源的答复", 750, 650, 350, 65, parent="target")
arrow("analysis_report", "analysis", "report", from_side="bottom", to_side="top")

box("business_db", "业务数据源<br>SQLite → PostgreSQL", 40, 745, 250, 82, parent="target", kind="data")
box("schema_index", "Schema / 业务术语索引<br>PostgreSQL + pgvector", 310, 745, 250, 82, parent="target", kind="data")
box("memory", "长期记忆<br>semantic / episodic", 580, 745, 250, 82, parent="target", kind="support")
box("eval", "评测 / 审计 / Trace<br>EX · Recall@K · 安全 · P95", 850, 745, 250, 82, parent="target", kind="data")

OUT.parent.mkdir(parents=True, exist_ok=True)
ET.indent(root, space="  ")
ET.ElementTree(root).write(OUT, encoding="utf-8", xml_declaration=True)
print(OUT)
