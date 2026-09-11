# Tarefa 3 — Retry e hedge

**Objetivo:** decorators QuoteProvider com orçamento, jitter e hedge de latência,
verificados sem rede ou espera real, incluindo medição de falha residual.
**Especificação:** Tarefa 3 da conversa, AGENTS.md e seção 6 da arquitetura.

## Decisões de interface

- `RetryingQuoteProvider(inner, *, max_attempts, base_delay, max_delay, budget,
  sleep, rng, clock, contract_threshold=3)`. `rng` é callable sem argumentos,
  por exemplo `Random(seed).random`; `sleep` é callable assíncrono de segundos.
- Full jitter: `rng() * min(max_delay, base_delay * 2**indice)`. Limites finitos;
  nenhuma espera que consuma todo o orçamento remanescente inicia nova tentativa.
- Deadline também cancela chamada em andamento, usando sleep injetado.
- Erro agregado contém `tentativas`. Promoção de contrato só no esgotamento,
  se todas as falhas forem suspeitas e o limiar configurável for alcançado.
- `HedgingQuoteProvider(inner, *, hedge_delay, sleep)`. No máximo duas chamadas.
  Resposta válida inclui Declined. Falha rápida não abre hedge. Contrato tem
  prioridade entre conclusões disponíveis no mesmo despertar; demais falhas
  transitórias aguardam a outra chamada. Sempre cancela e aguarda perdedor/timer.
- Composição no teste e documentação: Retry(Hedge(Http)). Sem wiring de aplicação.

## Execução com TDD

- [x] Criar agendamento virtual de teste e folha programável com latências virtuais.
- [x] Escrever e observar testes vermelhos de retry, budget, jitter e suspeitas.
- [x] Implementar retry e metadado de contagem; verificar verde.
- [x] Escrever e observar testes vermelhos de hedge, cancelamento e composição.
- [x] Implementar hedge; verificar verde.
- [x] Medir cadeia real contra duplo probabilístico semeado, com orçamento que
  permita três tentativas; assertar margem ao redor de 2,7% e 1,2167%.
- [x] Revisar, executar suíte/ruff/mypy e atualizar README, arquitetura e registros.

Fora do escopo: guard, cache, trace, persistência, grafo, prompts e adapters.
