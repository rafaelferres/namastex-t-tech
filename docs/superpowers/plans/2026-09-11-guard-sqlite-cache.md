# Tarefa 4 — Guard, SQLite e cache

**Objetivo:** completar Guard → Cache → Retry → Hedge → Http sem trace ou adapters.
**Especificação:** tarefa 4 da conversa; AGENTS.md e arquitetura, seções 6, 8 e 12.
**Execução:** inline com TDD nas interfaces públicas solicitadas pelo usuário.

- [x] Remedir quatro cenários com 10.000 execuções e budget configurável de 3,5 s.
- [x] Guard: testar recusa local, passagem, catálogo indisponível e ano seguinte;
  implementar `EligibilityGuardProvider(inner, rules, clock)` em `quote/guard.py`.
- [x] SQLite: testar pragmas, reaplicação, precisão, expiração e restart;
  implementar `connect`/`apply_schema`, schema somente quote_cache e
  `SQLiteQuoteCache(connection, clock)` implementando a porta existente.
- [x] Cache: testar miss/hit, recusa, chaves/dias, meia-noite, erros e cancelamento;
  implementar `CachingQuoteProvider(inner, cache, clock)` com logs sem PII.
- [x] Composição: testar via MockTransport e SQLite; criar `build_quote_provider`
  com cliente, cache, regras, Clock, sleep, RNG e configuração injetados.
- [x] Executar pytest/ruff/mypy; revisar; atualizar README, arquitetura e registros.

Resultados carregam origem sem afetar igualdade de domínio. SQLite serializa JSON
com Decimal em strings e instantes UTC; não armazena request nem CEP. Operações do
cache usam worker thread com lock por conexão para não bloquear o event loop no
busy_timeout. Startup aplica schema; o dono da conexão controla seu fechamento.
Expiração é calculada na entrada para a meia-noite local seguinte; escrita após
essa fronteira não renova resultado antigo. Cancelamento nunca é engolido.
