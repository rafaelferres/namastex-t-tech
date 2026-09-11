CREATE TABLE IF NOT EXISTS leads (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    channel_user_id TEXT NOT NULL,
    cpf_hash TEXT CHECK(cpf_hash IS NULL OR (length(cpf_hash)=64 AND cpf_hash NOT GLOB '*[^0-9a-f]*')),
    criado_em TEXT NOT NULL,
    UNIQUE(channel, channel_user_id)
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL REFERENCES leads(id),
    status TEXT NOT NULL CHECK(status IN ('ativa','cotada','recusada','escalada','encerrada')),
    slots TEXT NOT NULL DEFAULT '{}',
    objecoes_preco INTEGER NOT NULL DEFAULT 0 CHECK(objecoes_preco >= 0),
    iniciada_em TEXT NOT NULL,
    atualizada_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    indice INTEGER NOT NULL CHECK(indice >= 0),
    direcao TEXT NOT NULL CHECK(direcao IN ('inbound','outbound')),
    tipo TEXT NOT NULL CHECK(tipo IN ('text','audio','image','document')),
    corpo TEXT,
    media_status TEXT CHECK(media_status IN ('resolvido','nao_resolvido')),
    provider_message_id TEXT,
    criado_em TEXT NOT NULL,
    UNIQUE(conversation_id, indice)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_provider
    ON messages(provider_message_id) WHERE provider_message_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS handoffs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    motivo TEXT NOT NULL,
    sugerido_por_llm INTEGER NOT NULL CHECK(sugerido_por_llm IN (0,1)),
    snapshot TEXT NOT NULL,
    criado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbound_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    payload TEXT NOT NULL,
    destino TEXT NOT NULL CHECK(destino IN ('lead','webhook_vendas','api_fila')),
    status TEXT NOT NULL CHECK(status IN ('pendente','entregue','falhou')),
    tentativas INTEGER NOT NULL DEFAULT 0 CHECK(tentativas >= 0),
    criado_em TEXT NOT NULL,
    entregue_em TEXT,
    erro TEXT,
    proxima_tentativa_em TEXT
);

CREATE TABLE IF NOT EXISTS turn_events (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    etapa TEXT NOT NULL,
    status TEXT NOT NULL,
    latencia_ms INTEGER NOT NULL,
    erro TEXT,
    criado_em TEXT NOT NULL,
    sugestao TEXT
);
CREATE INDEX IF NOT EXISTS idx_turn_events_trace ON turn_events(trace_id, criado_em);

CREATE TABLE IF NOT EXISTS quote_cache (
    fingerprint TEXT PRIMARY KEY,
    outcome TEXT NOT NULL,
    expira_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quote_attempts (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
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
