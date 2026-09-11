# Changelog

Mudanças relevantes por fase, no formato Keep a Changelog.

## [0.1.0] - 2026-09-11

### Added

- Fundação do domínio: cotação e recusa são resultados distintos, dinheiro da
  API é preservado em Decimal e pro-rata ausente permanece `None`.
- Requisições imutáveis normalizam CEP e plano antes do payload e do
  fingerprint; todos os cinco slots e o dia participam da chave estável.
- Aceitação deriva do catálogo real, preserva motivos e faixas recusadas e
  normaliza a idade de veículo de ano futuro para zero, sem precificação local.
- Portas assíncronas pequenas para cotação, cache, regras e tentativas; relógio
  de calendário e monotônico substituível nos testes.
- Ferramental Python 3.12 com uv, lockfile, pytest/pytest-asyncio, ruff e mypy
  estrito. Fixtures reais e 79 testes offline, sem banco, serviço ou LLM.

### Validation

- Portão final: `uv run pytest`, **79 passed in 0.69s**; comando completo
  medido em **1,90 s**, incluindo a inicialização. Ruff limpo e mypy sem erros
  nos seis arquivos de `src/`.
- TDD: falhas de importação observadas antes de criar domínio, aceitação e
  relógio; regressão da normalização de plano falhou por asserção antes da correção.
- Fixture de planos idêntica byte a byte ao desafio; fingerprint conferido em
  dois processos com sementes de hash diferentes. Auditoria de `src/` sem
  precificação, imports de frameworks ou chamadas proibidas de relógio/sleep/random.
- Ambiente WSL: com `.venv` em `/mnt/c`, uma medição do comando completo levou
  4,67 s, embora os testes levassem 0,95 s. O ambiente criado nesta sessão foi
  movido para `/home/rafael/.venvs/namastex-test-tecnico-domain`, mantendo `.venv`
  como link local ignorado pelo Git. Após a mudança, a primeira execução levou
  2,23 s e a seguinte 1,90 s; o limite depende também do custo de inicialização
  e do filesystem, não apenas dos testes. Em outros ambientes basta `uv sync`;
  o código não depende desse caminho local.

### Known limitations

- O guard aceita ano-modelo futuro como solicitado, mas a API original ainda
  pode recusar esse mesmo perfil. Não houve alteração no serviço do desafio.
- HTTP, resiliência, cache concreto, persistência, grafo, LLM, templates e
  adapters continuam fora desta fase.
