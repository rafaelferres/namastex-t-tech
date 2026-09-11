# Changelog

Mudanças relevantes por fase, no formato Keep a Changelog.

## [Unreleased]

### Added — Tarefa 2, 2026-09-11

- A folha HTTP traduz respostas para Quote, Declined ou as exceções de domínio,
  com timeout injetado e sem retry, hedge, cache de cotação ou trace. Recusa 422
  continua sendo resultado mesmo quando o motivo não pode ser lido do JSON.
- Falhas 5xx sem a marca `upstream_unavailable` carregam `suspeita_contrato`,
  mantendo sua classificação transitória. Erros de requisição do httpx, incluindo
  transporte, timeout e decodificação de compressão, não escapam da fronteira.
- Ano-modelo exatamente um ano à frente é normalizado apenas no payload;
  CEP, data, request original e fingerprint são preservados. `ano_normalizado`
  acompanha resultados e exceções por chamada, inclusive em concorrência.
- O cliente de planos entrega regras de aceitação e fatos de produto imutáveis,
  sem precificação ou franquia. O cache de catálogo usa TTL monotônico injetado,
  sem reutilizar dados vencidos nem cachear erros; indisponibilidade das regras
  permite o guard falhar aberto.

### Validation

- **162 testes passaram em 2,01 s**: 79 anteriores e 83 novos (59 da folha HTTP
  e 24 de planos). Todos os testes HTTP usam MockTransport, sem rede real.
- `uv run ruff check src tests` limpo; `uv run mypy src` sem erros em 12 arquivos.
- Ciclos vermelhos observados para os 58 casos iniciais da folha e 24 de planos
  antes da implementação; regressão de compressão corrompida reproduzida antes
  da correção. Revisão independente identificou essa regressão.
- Busca em `src/` sem `base_mensal`, `multiplicador`, `date.today()`, `time.sleep()`
  ou uso de random. Casos de redirecionamento, cancelamento, concorrência e TTL
  exato cobertos sem sleeps ou relógio real.
- Os metadados não implementam trace ou persistência. A confirmação de ano dois
  ou mais anos no futuro continua sendo responsabilidade do futuro grafo.

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
