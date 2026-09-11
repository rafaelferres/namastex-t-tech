# Tarefa 2 — clientes HTTP e taxonomia

**Especificação:** Tarefa 2 da conversa, AGENTS.md e seção 7 da arquitetura.
**Objetivo:** implementar somente a folha HTTP de cotação e o catálogo com TTL em memória.

## Desenho

- `HttpQuoteProvider(client, timeout, clock)` mantém `quote(req) -> QuoteOutcome`.
- `ano_normalizado` acompanha Quote, Declined e as duas exceções. É informação
  por chamada, sem estado compartilhado, callback ou persistência.
- `QuoteUnavailable.suspeita_contrato` sinaliza 5xx sem a marca de indisponibilidade;
  não altera a classificação transitória. Corpos e exceções HTTP não são ecoados.
- Somente ano atual + 1 é ajustado numa cópia do payload. Request e fingerprint originais intactos.
- `PlanosClient.get()` entrega `Planos`, contendo AcceptanceRules e uma tupla de
  ProductFacts imutáveis. `current()` satisfaz AcceptanceRulesProvider.
- ProductFacts por plano: id, nome, coberturas e existência da carência aplicável.
- Cache guarda somente projeções válidas. TTL começa após o fetch e usa Clock.monotonic.
- Falhas de catálogo usam PlanosUnavailable para transporte/status e QuoteContractError
  para payload inválido; não há retry nem retorno de catálogo expirado.

## Ciclos de execução

- [x] Escrever testes de MockTransport para taxonomia, payload e metadados; observar falhas.
- [x] Implementar metadados de domínio e folha HTTP; verificar verde.
- [x] Escrever testes de projeções, erros e expiração com relógio falso; observar falhas.
- [x] Implementar projeções e catálogo; verificar verde.
- [x] Revisar os contratos, executar pytest, ruff e mypy; auditar chamadas e dados proibidos.
- [x] Atualizar decisões, arquitetura e changelog com os resultados medidos.

Resultado: 162 testes passaram em 2,01 s; ruff e mypy estrito limpos.
A revisão encontrou DecodingError escapando do catch de TransportError;
o caso foi reproduzido e corrigido capturando RequestError na fronteira.

Sem retry, hedge, cache de cotação, trace, persistência, grafo, prompts ou adapters de entrada.
