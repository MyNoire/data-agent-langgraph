# 工单系统：本地 Markdown

本仓库的规格和工单以 markdown 文件存放在 `.scratch/`。

## 约定

- 每个功能一个目录：`.scratch/<功能名>/`
- 规格文件：`.scratch/<功能名>/spec.md`
- 实现工单一张一个文件：`.scratch/<功能名>/issues/<NN>-<slug>.md`，从 `01` 开始编号；不要把多张工单合并成一个文件
- 分类状态写在工单文件顶部的 `Status:` 行（取值见 `triage-labels.md`）
- 讨论和后续记录追加在文件末尾的 `## Comments` 标题下

## 当技能要求"发布到工单系统"时

在 `.scratch/<功能名>/` 下新建文件（目录不存在就先创建）。

## 当技能要求"读取相关工单"时

读取所引用路径的文件。用户通常会直接给出路径或工单编号。

## Wayfinding 操作

供 `/wayfinder` 使用。**地图**是一个文件，每张工单对应一个**子文件**。

- **地图**：`.scratch/<工作名>/map.md`（正文包括 Notes / Decisions-so-far / Fog 三部分）。
- **子工单**：`.scratch/<工作名>/issues/NN-<slug>.md`，从 `01` 开始编号，正文写待解决的问题。`Type:` 行记录类型（`research`/`prototype`/`grilling`/`task`），`Status:` 行记录 `claimed`/`resolved`。
- **阻塞关系**：顶部写 `Blocked by: NN, NN`。所列文件全部为 `resolved` 时该工单解除阻塞。
- **前沿**：扫描 `.scratch/<工作名>/issues/`，找出未关闭、未阻塞、未认领的文件，编号最小的优先。
- **认领**：开始工作前先把 `Status:` 改为 `claimed` 并保存。
- **解决**：在 `## Answer` 标题下写答案，把 `Status:` 改为 `resolved`，再在 `map.md` 的 Decisions-so-far 中追加一条要点和链接。

## QueryPilot 补充

- 规格、工单和讨论记录用中文书写。
- `.scratch/` 纳入 Git 管理（不忽略），是新工作的决定记录。`specs/` 是旧流程的只读历史。
