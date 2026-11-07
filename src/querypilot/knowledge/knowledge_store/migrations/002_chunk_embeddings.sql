-- R4 向量表。向量是按 Embedding Profile 从 Corpus 算出的派生索引，不写回 Chunk 行（ADR 0002）。
ALTER TABLE querypilot_kb.knowledge_chunks DROP CONSTRAINT chunks_embedding;
ALTER TABLE querypilot_kb.knowledge_chunks DROP COLUMN embedding, DROP COLUMN embedding_profile_id, DROP COLUMN embedding_dimensions;

CREATE TABLE querypilot_kb.chunk_embeddings (
    chunk_id UUID NOT NULL,
    embedding_profile_id TEXT NOT NULL,
    embedding_dimensions INTEGER NOT NULL,
    embedding public.vector NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT embeddings_pk PRIMARY KEY (chunk_id, embedding_profile_id),
    CONSTRAINT embeddings_chunk FOREIGN KEY (chunk_id)
        REFERENCES querypilot_kb.knowledge_chunks (chunk_id) ON DELETE RESTRICT,
    CONSTRAINT embeddings_values CHECK (
        length(embedding_profile_id) > 0 AND embedding_dimensions > 0
        AND public.vector_dims(embedding) = embedding_dimensions AND public.vector_norm(embedding) > 0
    )
);
