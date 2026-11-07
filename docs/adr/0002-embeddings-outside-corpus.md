# Embedding 向量存在 Corpus 之外的独立表里

Corpus 是知识的快照，校验入库后不再改动。Embedding 向量是用某个 Embedding Profile 从 Corpus 算出来的派生索引，不属于知识本身，所以单独存放在一张按 (Chunk, Embedding Profile) 存储的表里，不回写 `knowledge_chunks`。这样 Corpus 保持不可变，同一份 Corpus 也可以同时存放多个 Embedding Profile 的向量，便于比较不同的模型。

## Considered Options

- 直接 UPDATE `knowledge_chunks` 上已经预留的 `embedding` 列：改动最少，但会修改已经入库的 Corpus 行；而且每个 Chunk 只能存一种向量，比较两个模型时只能互相覆盖。
