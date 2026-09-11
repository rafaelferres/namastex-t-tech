CREATE TABLE IF NOT EXISTS quote_cache (
    fingerprint TEXT PRIMARY KEY,
    outcome TEXT NOT NULL,
    expira_em TEXT NOT NULL
);
