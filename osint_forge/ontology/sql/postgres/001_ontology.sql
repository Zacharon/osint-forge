-- Forge ontology schema, PostgreSQL dialect.
-- Keep in step with ../sqlite.sql. Spatial indexing with PostGIS is added in a
-- later migration so this one runs on any PostgreSQL 14+ server.

CREATE TABLE IF NOT EXISTS ontology_objects (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    label TEXT NOT NULL,
    properties JSONB NOT NULL,
    provenance JSONB NOT NULL,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    valid_from TEXT,
    valid_to TEXT,
    revision INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((lat IS NULL) = (lon IS NULL)),
    CHECK (lat IS NULL OR (lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180))
);
CREATE INDEX IF NOT EXISTS ontology_objects_type ON ontology_objects (type);
CREATE INDEX IF NOT EXISTS ontology_objects_latlon ON ontology_objects (lat, lon);

CREATE TABLE IF NOT EXISTS ontology_object_revisions (
    id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    body JSONB NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (id, revision)
);

CREATE TABLE IF NOT EXISTS ontology_links (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    from_id TEXT NOT NULL REFERENCES ontology_objects (id),
    to_id TEXT NOT NULL REFERENCES ontology_objects (id),
    evidence_ids JSONB NOT NULL,
    confidence DOUBLE PRECISION,
    rationale TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    provenance JSONB NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_links_from ON ontology_links (from_id, type);
CREATE INDEX IF NOT EXISTS ontology_links_to ON ontology_links (to_id, type);

CREATE TABLE IF NOT EXISTS ontology_audit (
    seq BIGSERIAL PRIMARY KEY,
    action TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    actor TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
