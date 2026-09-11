# Fundação do domínio — plano de implementação

**Objetivo:** cumprir a Tarefa 1 fornecida pelo usuário, com testes offline abaixo de dois segundos.
**Arquitetura:** domínio puro em `src/domain`, portas em `src/application`, sem dependências de runtime.
**Stack:** Python 3.12, uv, pytest, pytest-asyncio, ruff e mypy estrito.
**Especificação:** Tarefa 1 da conversa e invariantes de `AGENTS.md`.

## Restrições

- Sem cliente HTTP, banco, LLM, cache concreto, decorators ou adapters.
- Dinheiro exclusivamente da resposta real de cotação, convertido em Decimal.
- Recusa é resultado; erro transitório e erro de contrato são exceções distintas.
- CEP opcional e imutável, normalizado antes do payload e fingerprint.
- Parse de aceitação guarda somente regras de aceitação, sem precificação.
- Datas de avaliação são argumentos explícitos; somente SystemClock acessa o relógio.
- Preservar alterações preexistentes em AGENTS.md e docs/ARQUITETURA.md.

## Execução local com TDD

1. Ferramental e contrato de resposta
   - Configurar `pyproject.toml`, criar os cabeçalhos dos registros e copiar a fixture real.
   - Capturar uma resposta da função original com data fixa, sem serviço ou rede.
   - Escrever `tests/unit/test_quote.py` para erros, recusa e parse; executar e observar falha.
   - Implementar objetos e parse em `src/domain/quote.py`; verificar verde.
2. Requisição de cotação
   - Acrescentar testes de CEP, payload, imutabilidade e fingerprint; observar falhas.
   - Implementar QuoteRequest, serialização canônica e SHA-256; verificar verde.
3. Aceitação
   - Escrever `tests/unit/test_acceptance.py` usando `tests/fixtures/plans.json`.
   - Cobrir limites do fixture, mudanças nas regras, plano inexistente e ano futuro; observar falhas.
   - Implementar `src/domain/acceptance.py` retendo faixas e razões de recusa; verificar verde.
4. Portas e fechamento
   - Definir contratos pequenos em `src/application/ports.py`, sem implementações de I/O.
   - Testar SystemClock com fontes de tempo substituídas por valores fixos antes de implementar.
   - Rodar pytest cronometrado, ruff e mypy; conferir ausência de precificação e chamadas proibidas.
   - Registrar decisões defensáveis e o resultado medido no changelog.

## Resultado da execução

- [x] Ferramental e fixtures reais preparados.
- [x] Falhas observadas antes da implementação; regressão de plano em maiúsculas corrigida.
- [x] Cotação, requisição, aceitação e portas implementadas.
- [x] 79 testes verdes (0,69 s), ruff e mypy estrito limpos.
- [x] Comando completo medido em 1,90 s com ambiente virtual no filesystem Linux.
- [x] Decisões D-001/D-002 e resultados registrados; arquitetura atualizada.
