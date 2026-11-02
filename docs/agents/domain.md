# 领域文档

工程类技能在探索代码时如何使用本仓库的领域文档。

## 探索前先读

- 根目录的 **`GLOSSARY.md`**；或者根目录存在 **`GLOSSARY-MAP.md`** 时读它，它指向每个上下文各自的 `GLOSSARY.md`，按主题读相关的那几份。
- **`docs/adr/`**：读和即将改动的区域相关的 ADR（架构决策记录）。多上下文仓库还要查看 `src/<上下文>/docs/adr/` 中只适用于该上下文的决定。

这些文件不存在时**直接继续**，不提示缺失，也不建议提前创建。`/domain-modeling` 技能（经由 `/grill-with-docs` 和 `/improve-codebase-architecture` 调用）会在术语或决定真正确定时按需创建它们。

## 文件结构

单一上下文仓库（大多数仓库，本仓库也是）：

```
/
├── GLOSSARY.md
├── docs/adr/
│   ├── 0001-event-sourced-orders.md
│   └── 0002-postgres-for-write-model.md
└── src/
```

多上下文仓库（根目录有 `GLOSSARY-MAP.md`）：

```
/
├── GLOSSARY-MAP.md
├── docs/adr/                          ← 系统级决定
└── src/
    ├── ordering/
    │   ├── GLOSSARY.md
    │   └── docs/adr/                  ← 该上下文的决定
    └── billing/
        ├── GLOSSARY.md
        └── docs/adr/
```

## 使用术语表的词汇

输出中提到领域概念时（工单标题、重构建议、假设、测试名称），使用 `GLOSSARY.md` 中定义的术语，不要换成术语表明确不用的同义词。

需要的概念还不在术语表里，说明两种情况之一：要么你在发明项目并不使用的说法（重新考虑），要么确实有缺口（记下来交给 `/domain-modeling`）。

## 指出与 ADR 的冲突

输出与已有 ADR 矛盾时，明确指出，而不是悄悄推翻：

> _与 ADR-0007（订单事件溯源）相矛盾，但值得重新讨论，因为……_

## QueryPilot 补充

`GLOSSARY.md`、ADR 及其他领域文档用中文书写；代码标识符保留英文原名。
