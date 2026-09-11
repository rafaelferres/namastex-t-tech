CREATE TABLE IF NOT EXISTS quote_cache (
    fingerprint TEXT PRIMARY KEY,
    outcome TEXT NOT NULL,
    expira_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quote_attempts (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    tentativa INTEGER NOT NULL CHECK(tentativa >= 0),
    status TEXT NOT NULL CHECK(status IN ('quoted', 'declined', 'unavailable', 'contract_error')),
    origem TEXT NOT NULL CHECK(origem IN ('api', 'cache', 'regra_local')),
    http_status INTEGER,
    latencia_ms INTEGER NOT NULL,
    hedge INTEGER NOT NULL DEFAULT 0,
    ano_normalizado INTEGER NOT NULL DEFAULT 0,
    erro TEXT,
    criado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_trace ON quote_attempts(trace_id);
