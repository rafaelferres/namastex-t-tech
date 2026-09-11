# Tarefa 5 — Recalibração e rastreabilidade

**Objetivo:** calibrar resiliência com medição real e instrumentar resolução lógica
e chamadas físicas, com inspeção humana pela CLI.
**Especificação:** tarefa 5 da conversa; AGENTS.md e arquitetura, seções 6 e 12.
**Execução:** inline, TDD nas interfaces de configuração, trace, recorder e inspeção.

- [x] Medir API real isolada, 500 chamadas + 20 warmup, preservar distribuição.
- [x] Ajustar QuoteConfig: hedge 100 ms, jitter uniforme 0–20 ms por tentativa.
- [x] Remedir quatro cenários antes/depois, explicando resíduo por timeout.
- [x] Adicionar DTO de evento/portas e provedor de correlação isolado por contexto.
- [x] Testar e implementar WireTrace e ApplicationTrace best effort, sem PII.
- [x] Adicionar quote_attempts e índice, recorder/reader SQLite, evolução idempotente.
- [x] Integrar wiring, comando de inspeção com caso de uso e cotação real.
- [x] Revisar, validar pytest/ruff/mypy e atualizar README, arquitetura e registros.

Decisões de desenho: contexto por cotação propagado pelo asyncio; sequência física
por início de chamada, hedge identificado por chamada sobreposta dentro da cadeia
Retry(Hedge). Status HTTP observado na folha via callback técnico, sem entrar no
domínio. Evento lógico usa tentativa zero; físicos usam 1..N. Cancelamento físico
é unavailable com classe CancelledError, sem afirmar resposta HTTP inexistente.
Trace persiste só ids internos, fingerprint e metadados; nunca request/corpo/erro textual.

Revisão: entrega de eventos alterada para não bloqueante após reproduzir
recusa rápida convertida em indisponibilidade pelo I/O de trace. Fila limitada
a 1.024 pendentes e flush explícito fora da cotação; calendário local preservado
no metadado de normalização durante cancelamento. Portão final: 285 testes,
11,85 s; Ruff e mypy limpos.
