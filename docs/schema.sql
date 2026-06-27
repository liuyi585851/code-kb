-- P1 阶段只读 RAG MVP 的 schema 草稿。
-- 目标数据库:Postgres 14+。

CREATE TABLE IF NOT EXISTS source_documents (
    docid TEXT PRIMARY KEY,
    system TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    content_type TEXT NOT NULL,
    parent_path TEXT,
    owner TEXT,
    author TEXT,
    can_edit BOOLEAN DEFAULT FALSE,
    source_acl_hash TEXT,
    last_modified TIMESTAMPTZ,
    raw_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS knowledge_atoms (
    atom_id UUID PRIMARY KEY,
    sub_kb_id TEXT NOT NULL,
    layer TEXT NOT NULL CHECK (layer IN ('L0', 'L1', 'L2', 'L3')),
    card_type TEXT NOT NULL,
    atom_type TEXT NOT NULL,
    status TEXT NOT NULL,
    text TEXT NOT NULL,
    contextual_prefix TEXT,
    source_docid TEXT NOT NULL REFERENCES source_documents(docid),
    source_anchor TEXT,
    section_path_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    owner TEXT,
    sensitivity TEXT,
    branch TEXT,
    version TEXT,
    confidence DOUBLE PRECISION DEFAULT 0,
    freshness DOUBLE PRECISION DEFAULT 0,
    usefulness DOUBLE PRECISION DEFAULT 0,
    ai_self_eval DOUBLE PRECISION DEFAULT 0,
    accuracy DOUBLE PRECISION DEFAULT 0,
    composite DOUBLE PRECISION DEFAULT 0,
    atom_version INTEGER NOT NULL DEFAULT 1,
    superseded_by UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_atoms_sub_kb_status
    ON knowledge_atoms(sub_kb_id, status);

CREATE INDEX IF NOT EXISTS idx_atoms_source_docid
    ON knowledge_atoms(source_docid);

CREATE INDEX IF NOT EXISTS idx_atoms_composite
    ON knowledge_atoms(composite DESC);

CREATE TABLE IF NOT EXISTS atom_versions (
    atom_id UUID NOT NULL REFERENCES knowledge_atoms(atom_id),
    atom_version INTEGER NOT NULL,
    text TEXT NOT NULL,
    contextual_prefix TEXT,
    changed_by TEXT,
    change_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (atom_id, atom_version)
);

CREATE TABLE IF NOT EXISTS retrieval_traces (
    trace_id UUID PRIMARY KEY,
    query_id UUID NOT NULL,
    user_id_hash TEXT,
    surface TEXT NOT NULL,
    query_text TEXT NOT NULL,
    selected_sub_kbs TEXT[] NOT NULL DEFAULT '{}',
    route_reason TEXT,
    rewritten_queries TEXT[] NOT NULL DEFAULT '{}',
    entities TEXT[] NOT NULL DEFAULT '{}',
    dense_hits UUID[] NOT NULL DEFAULT '{}',
    sparse_hits UUID[] NOT NULL DEFAULT '{}',
    rrf_top20 UUID[] NOT NULL DEFAULT '{}',
    reranker_top4 JSONB NOT NULL DEFAULT '[]'::jsonb,
    total_latency_ms INTEGER,
    retrieval_latency_ms INTEGER,
    generation_latency_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS answer_logs (
    answer_id UUID PRIMARY KEY,
    trace_id UUID NOT NULL REFERENCES retrieval_traces(trace_id),
    answer_text TEXT,
    cited_atoms UUID[] NOT NULL DEFAULT '{}',
    cited_sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    refused BOOLEAN NOT NULL DEFAULT FALSE,
    refusal_reason TEXT,
    confidence DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS feedback_events (
    feedback_id UUID PRIMARY KEY,
    answer_id UUID REFERENCES answer_logs(answer_id),
    trace_id UUID REFERENCES retrieval_traces(trace_id),
    user_id_hash TEXT,
    surface TEXT,
    signal TEXT NOT NULL,
    comment TEXT,
    ai_self_eval JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gap_candidates (
    gap_id UUID PRIMARY KEY,
    source_event TEXT NOT NULL,
    query_cluster_id TEXT,
    sub_kb_id TEXT,
    summary TEXT NOT NULL,
    example_queries TEXT[] NOT NULL DEFAULT '{}',
    suggested_owner TEXT,
    priority TEXT NOT NULL DEFAULT 'P2',
    status TEXT NOT NULL DEFAULT 'open',
    links JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS governance_items (
    item_id TEXT PRIMARY KEY,
    item_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    sub_kb_id TEXT,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    suggested_owner TEXT,
    source_ref TEXT,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'open',
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_governance_items_status
    ON governance_items(status, severity);

CREATE INDEX IF NOT EXISTS idx_governance_items_sub_kb
    ON governance_items(sub_kb_id, item_type);

CREATE TABLE IF NOT EXISTS governance_ticket_plans (
    ticket_id UUID PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES governance_items(item_id),
    item_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    sub_kb_id TEXT,
    title TEXT NOT NULL,
    target TEXT NOT NULL,
    assignee TEXT,
    status TEXT NOT NULL,
    description TEXT NOT NULL,
    operations JSONB NOT NULL DEFAULT '[]'::jsonb,
    external_ref TEXT,
    planned_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_governance_ticket_item
    ON governance_ticket_plans(item_id, target);

CREATE INDEX IF NOT EXISTS idx_governance_ticket_status
    ON governance_ticket_plans(status, target);

CREATE TABLE IF NOT EXISTS kb_registry_snapshots (
    snapshot_id UUID PRIMARY KEY,
    registry_version TEXT NOT NULL,
    registry_yaml TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
