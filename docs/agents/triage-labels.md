# 分类标签

技能使用五个标准分类角色。下表把这些角色对应到本仓库实际使用的标签字符串。

| mattpocock/skills 中的标签 | 本仓库使用的标签 | 含义 |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | 需要维护者评估 |
| `needs-info` | `needs-info` | 等待补充信息 |
| `ready-for-agent` | `ready-for-agent` | 已写清楚，可以交给 agent 独立完成 |
| `ready-for-human` | `ready-for-human` | 需要人亲手实现 |
| `wontfix` | `wontfix` | 不处理 |

技能提到某个角色时（例如"标记为可交给 agent"），使用表中对应的标签字符串。工单在本地，标签写在工单文件顶部的 `Status:` 行。

在 QueryPilot 中，`ready-for-human` 用来标记结对核心工单（见 `AGENTS.md` 第 3 节）：用户写实现，Claude 准备失败的测试并审查。

要改用其他名称时，修改"本仓库使用的标签"一列。
