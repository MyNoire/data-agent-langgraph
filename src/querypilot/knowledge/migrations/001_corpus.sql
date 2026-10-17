-- R2 首版知识存储。只由管理入口在显式事务内执行，不执行旧 docs/sql 草案。
CREATE TABLE querypilot_kb.schema_migrations (
    version TEXT CONSTRAINT migrations_pk PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE querypilot_kb.knowledge_corpora (
    corpus_id TEXT CONSTRAINT corpora_pk PRIMARY KEY,
    database_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    core_digest TEXT NOT NULL,
    manifest JSONB NOT NULL,
    validation JSONB NOT NULL,
    counts JSONB NOT NULL,
    status TEXT NOT NULL,
    storage_contract_version TEXT NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT corpora_scope UNIQUE (corpus_id, database_id, schema_version),
    CONSTRAINT corpora_hashes CHECK (corpus_id ~ '^[0-9a-f]{64}$' AND schema_version ~ '^[0-9a-f]{64}$' AND core_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT corpora_state CHECK (status = 'stored' AND storage_contract_version = 'knowledge-storage-v1'),
    CONSTRAINT corpora_json CHECK (jsonb_typeof(manifest) = 'object' AND jsonb_typeof(validation) = 'object' AND jsonb_typeof(counts) = 'object'),
    CONSTRAINT corpora_database CHECK (length(database_id) > 0)
);

CREATE TABLE querypilot_kb.knowledge_documents (
    document_id UUID CONSTRAINT documents_pk PRIMARY KEY,
    corpus_id TEXT NOT NULL,
    database_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    document_key TEXT NOT NULL,
    dialect TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    metadata JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT documents_key UNIQUE (corpus_id, database_id, document_key),
    CONSTRAINT documents_scope UNIQUE (document_id, document_key, corpus_id, database_id, schema_version),
    CONSTRAINT documents_corpus FOREIGN KEY (corpus_id, database_id, schema_version)
        REFERENCES querypilot_kb.knowledge_corpora (corpus_id, database_id, schema_version) ON DELETE RESTRICT,
    CONSTRAINT documents_uuid CHECK (substring(document_id::text, 15, 1) = '5' AND substring(document_id::text, 20, 1) ~ '^[89ab]$'),
    CONSTRAINT documents_hash CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT documents_values CHECK (dialect = 'sqlite' AND source_kind = 'derived_catalog' AND length(document_key) > 0 AND length(title) > 0 AND length(content) > 0),
    CONSTRAINT documents_json CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE TABLE querypilot_kb.schema_relations (
    relation_id UUID CONSTRAINT relations_pk PRIMARY KEY,
    relation_key TEXT NOT NULL,
    corpus_id TEXT NOT NULL,
    database_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    source_document_id UUID NOT NULL,
    source_document_key TEXT NOT NULL,
    source_schema TEXT NOT NULL,
    source_table TEXT NOT NULL,
    source_columns TEXT[] NOT NULL,
    target_schema TEXT NOT NULL,
    target_table TEXT NOT NULL,
    target_columns TEXT[] NOT NULL,
    relation_origin TEXT NOT NULL,
    provenance JSONB NOT NULL,
    metadata JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT relations_key UNIQUE (corpus_id, database_id, relation_key),
    CONSTRAINT relations_scope UNIQUE (relation_id, relation_key, source_document_id, source_document_key, corpus_id, database_id, schema_version),
    CONSTRAINT relations_corpus FOREIGN KEY (corpus_id, database_id, schema_version)
        REFERENCES querypilot_kb.knowledge_corpora (corpus_id, database_id, schema_version) ON DELETE RESTRICT,
    CONSTRAINT relations_document FOREIGN KEY (source_document_id, source_document_key, corpus_id, database_id, schema_version)
        REFERENCES querypilot_kb.knowledge_documents (document_id, document_key, corpus_id, database_id, schema_version) ON DELETE RESTRICT,
    CONSTRAINT relations_uuid CHECK (substring(relation_id::text, 15, 1) = '5' AND substring(relation_id::text, 20, 1) ~ '^[89ab]$'),
    CONSTRAINT relations_values CHECK (relation_origin = 'declared_fk' AND length(relation_key) > 0 AND length(source_schema) > 0 AND length(source_table) > 0 AND length(target_schema) > 0 AND length(target_table) > 0),
    CONSTRAINT relations_arrays CHECK (
        cardinality(source_columns) > 0 AND cardinality(source_columns) = cardinality(target_columns)
        AND array_ndims(source_columns) = 1 AND array_ndims(target_columns) = 1
        AND array_lower(source_columns, 1) = 1 AND array_lower(target_columns, 1) = 1
        AND array_position(source_columns, NULL) IS NULL AND array_position(target_columns, NULL) IS NULL
        AND array_position(source_columns, '') IS NULL AND array_position(target_columns, '') IS NULL
    ),
    CONSTRAINT relations_json CHECK (jsonb_typeof(metadata) = 'object' AND jsonb_typeof(provenance) = 'object')
);

CREATE TABLE querypilot_kb.knowledge_chunks (
    chunk_id UUID CONSTRAINT chunks_pk PRIMARY KEY,
    chunk_key TEXT NOT NULL,
    corpus_id TEXT NOT NULL,
    database_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    document_id UUID NOT NULL,
    document_key TEXT NOT NULL,
    knowledge_type TEXT NOT NULL,
    schema_name TEXT,
    table_name TEXT,
    column_name TEXT,
    relation_id UUID,
    relation_key TEXT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    metadata JSONB NOT NULL,
    embedding public.vector,
    embedding_profile_id TEXT,
    embedding_dimensions INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chunks_key UNIQUE (corpus_id, database_id, chunk_key),
    CONSTRAINT chunks_corpus FOREIGN KEY (corpus_id, database_id, schema_version)
        REFERENCES querypilot_kb.knowledge_corpora (corpus_id, database_id, schema_version) ON DELETE RESTRICT,
    CONSTRAINT chunks_document FOREIGN KEY (document_id, document_key, corpus_id, database_id, schema_version)
        REFERENCES querypilot_kb.knowledge_documents (document_id, document_key, corpus_id, database_id, schema_version) ON DELETE RESTRICT,
    CONSTRAINT chunks_relation FOREIGN KEY (relation_id, relation_key, document_id, document_key, corpus_id, database_id, schema_version)
        REFERENCES querypilot_kb.schema_relations (relation_id, relation_key, source_document_id, source_document_key, corpus_id, database_id, schema_version) ON DELETE RESTRICT,
    CONSTRAINT chunks_uuid CHECK (substring(chunk_id::text, 15, 1) = '5' AND substring(chunk_id::text, 20, 1) ~ '^[89ab]$'),
    CONSTRAINT chunks_hash CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chunks_values CHECK (length(chunk_key) > 0 AND length(title) > 0 AND length(content) > 0),
    CONSTRAINT chunks_locator CHECK (
        (knowledge_type = 'table' AND schema_name IS NOT NULL AND length(schema_name) > 0
         AND table_name IS NOT NULL AND length(table_name) > 0 AND column_name IS NULL AND relation_id IS NULL AND relation_key IS NULL)
        OR (knowledge_type = 'column' AND schema_name IS NOT NULL AND length(schema_name) > 0
         AND table_name IS NOT NULL AND length(table_name) > 0 AND column_name IS NOT NULL
         AND length(column_name) > 0 AND relation_id IS NULL AND relation_key IS NULL)
        OR (knowledge_type = 'relationship' AND schema_name IS NULL AND table_name IS NULL
         AND column_name IS NULL AND relation_id IS NOT NULL AND relation_key IS NOT NULL)
    ),
    CONSTRAINT chunks_embedding CHECK (
        (embedding IS NULL AND embedding_profile_id IS NULL AND embedding_dimensions IS NULL)
        OR (embedding IS NOT NULL AND embedding_profile_id IS NOT NULL AND length(embedding_profile_id) > 0
         AND embedding_dimensions IS NOT NULL AND embedding_dimensions > 0
         AND public.vector_dims(embedding) = embedding_dimensions AND public.vector_norm(embedding) > 0)
    ),
    CONSTRAINT chunks_json CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX relations_source ON querypilot_kb.schema_relations (corpus_id, database_id, schema_version, source_table);
CREATE INDEX relations_target ON querypilot_kb.schema_relations (corpus_id, database_id, schema_version, target_table);
CREATE INDEX chunks_scope ON querypilot_kb.knowledge_chunks (corpus_id, database_id, schema_version, knowledge_type);
