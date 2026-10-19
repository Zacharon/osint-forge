-- Forge ontology schema, SQLite dialect (local development and tests).
-- Keep in step with postgres/001_ontology.sql.

CREATE TABLE IF NOT EXISTS ontology_objects (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    label TEXT NOT NULL,
    properties TEXT NOT NULL,
    provenance TEXT NOT NULL,
    lat REAL,
    lon REAL,
    valid_from TEXT,
    valid_to TEXT,
    revision INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_objects_type ON ontology_objects (type);
CREATE INDEX IF NOT EXISTS ontology_objects_latlon ON ontology_objects (lat, lon);

CREATE TABLE IF NOT EXISTS ontology_object_revisions (
    id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    body TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (id, revision)
);

CREATE TABLE IF NOT EXISTS ontology_links (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    from_id TEXT NOT NULL REFERENCES ontology_objects (id),
    to_id TEXT NOT NULL REFERENCES ontology_objects (id),
    evidence_ids TEXT NOT NULL,
    confidence REAL,
    rationale TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    provenance TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_links_from ON ontology_links (from_id, type);
CREATE INDEX IF NOT EXISTS ontology_links_to ON ontology_links (to_id, type);

CREATE TABLE IF NOT EXISTS ontology_audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    actor TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
