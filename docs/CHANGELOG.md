# Changelog

Mudanças relevantes por fase, no formato Keep a Changelog.

## [Unreleased]

### Fixed — Revisão de invariantes, 2026-09-14

- Fala do modelo com quantidade não chega mais ao lead: dígito, numeral por extenso, moeda,
  porcentagem, fração e valor zero. O `Converser` troca a fala pelo template e registra o
  guardrail; o envelope `MensagemConversacional` recusa o mesmo texto (D-042).
- Slot incerto ou transcrito não sustenta recusa nem cotação. Recusa local só com dimensão
  confirmada (`AcceptanceRules.evaluate_profile`); data relativa incerta pede confirmação em
  vez de derrubar o turno com `ValueError` (D-043).
- Redação cobre CEP em toda forma que o coletor aceita (gramática única), telefone rotulado
  com "é" e celular sem máscara com DDD válido, em persistência, contexto de LLM, trace e
  logs (D-044).

### Added — Tarefa 14, 2026-09-14

- Console Streamlit (`src/interfaces/streamlit_app.py`), quarto adapter, no grupo opcional
  `console` (D-041):
  - **Sandbox:** conversa no navegador e, ao lado, o mesmo relatório redigido do `--trace`.
    A barra lateral reinicia a API do desafio com outra taxa de falha e outra semente, e
    injeta imagem e documento.
  - **Avaliação:** os números do README lidos dos arquivos das rodadas, cada um com a sua
    ressalva, e rodadas ao vivo pelas mesmas funções de `tests/golden`, `tests/regression`
    e `scripts/`, em amostra por padrão.
- `st.session_state` guarda só o `thread_id`; toda chamada async passa por `AsyncBridge`,
  um loop numa thread. Regras verificadas no código-fonte por `tests/unit/test_console.py`.
- Buraco que o console expôs, resolvido na aplicação: `ladder_level`, o nível da escada
  atingido no turno (N0 chamada e hedge, N1 retry, N2 escalação; cache e recusa local fora
  da escada). Entrou no `render_turn`, então a CLI e os logs de execução mostram também, com
  a linha de guardrail quando houver.

- Verificado num Chromium real, dirigindo o console:
  - a 20% de falha e semente 14, a conversa chega à cotação e o trace mostra
    "N1 — retry (2 tentativas)": 503 em 12 ms, retry cotado;
  - a 100% de falha e banco novo, três falhas rápidas e "N2 — escalação com snapshot", com o
    snapshot que o vendedor recebe;
  - a aba de avaliação mostra os números do README e roda, em amostra, extração por replay
    (100/100), oráculo (751 de 2.500) e agente real (10 conversas: 5/5 elegíveis sem
    documento cotadas, 5/5 consistentes e com carência, US$ 0,07).

### Fixed — Tarefa 14

- Na tela do Streamlit, "R$ 241,38 … R$ 3.000,00" era lido como fórmula LaTeX e o preço
  aparecia truncado. `escape_dollar` escapa o cifrão na conversa e no trace.
- A aba de avaliação arredondava 2.499/2.500 para "100,0%", contra os 99,96% do README;
  `format_ratio` usa a precisão do README e é testado.
- Validação: **708 passed** no loop rápido; **721 passed** na suíte completa com o corpus;
  ruff e mypy limpos.

### Added — Tarefa 13, 2026-09-14

- Verificação de coerência de configuração na partida (D-040): `open_live_stack` recusa,
  antes de qualquer rede, timeout do LLM abaixo do p99.9 medido (6,9 s), orçamento do turno
  abaixo da soma dos tetos das etapas e limite de tokens abaixo do máximo medido (10.095).
  A mensagem traz configurado e medido; variável ausente usa o padrão, com aviso. Com o
  `.env` da tarefa 8, a CLI sai com código 1 em vez de escalar por tokens no quarto turno.
  Pisos e padrões num único lugar (`infrastructure/llm/config.py`); o `.env.example` é
  conferido contra eles por teste.
- Hedge confirmado por teste: HTTP 500 rápido da folha real produz uma chamada, sem esperar a
  janela. A demonstração da tarefa 12 mostrava o hedge disparado pela latência (108 ms), não
  pela falha; o texto foi corrigido.

### Fixed — Tarefa 13

- `AGENTS.md` e `CLAUDE.md` não descrevem mais webhook nem console Streamlit: os adapters
  reais são CLI, replay e trace. Também corrigidos a cadeia completa e a janela do hedge
  (100 ms, não ~1,5 s).
- Divergência reportada com o contexto: a busca termo a termo nas 26.470 mensagens dá zero
  para "humano", "atendente", "supervisor", "sinistro" e "cancelamento" (`termos_ausentes`
  em `dataset-facts.json`). O dataset não pode acionar a métrica nem a regra de fora de
  escopo.
- Todos os números e logs regenerados no commit final:
  - 150 conversas: **72/150 cotadas, 72/74 elegíveis sem documento**, 72/72 consistentes com
    a tabela e com carência, divergência 0 em 223 turnos, US$ 0,84
    (`task13-e2e.json`);
  - 751 inelegíveis: **751/751 recusados pela regra local**, nenhuma chamada à `/quote`,
    nenhum preço, nenhuma escalação, US$ 0,86 (`task13-e2e-751.json`);
  - `docs/execucao-completa.md` (falha, chamada lenta resgatada pelo hedge, cotação),
    `docs/execucao-escalacao.md` (escada esgotada e snapshot) e `docs/demo-cli.md`
    (falha rápida, retry e cotação, sem hedge).
- Revisão contra os sete critérios do desafio no README, com o que é fraco em cada um.
- `ai-logs/` completo até a tarefa 13; caminhos locais redigidos; nenhuma chave no
  histórico do git.
- Validação: **682 passed** no loop rápido; **695 passed** na suíte completa com o corpus;
  ruff e mypy limpos.

### Added — Tarefa 12, 2026-09-11

- CLI de conversa, `python -m interfaces.cli`, adapter sobre os mesmos casos de uso:
  - `--trace` mostra slots com proveniência, políticas, a opinião do conversador e as
    tentativas de cotação;
  - `--conversation` retoma pelo checkpointer;
  - `/imagem`, `/audio` e `/documento` injetam mídia; `/encerrar` apaga slots e estado.
- Sessão real em `docs/demo-cli.md`: chega à cotação com falha, hedge e retry no turno.
- Os buracos que a CLI expôs foram para a aplicação e o wiring: `Ingestor.next_index`,
  `SalesStack.inspector` (inspeção nas conexões vivas) e `open_live_stack` (composição de
  produção).
- Divergência sempre gravada (D-039): o evento `decisao` guarda, em todo turno de fala, a
  decisão da política e a sugestão do modelo (coluna nova `turn_events.sugestao`).
  Medida na amostra de 150: **0 em 222 turnos**. O dataset não exercita a métrica:
  nenhum lead pede humano nem traz assunto fora de escopo.
- "Fora de escopo" ligado: `assunto` no schema do conversador, com piso lexical
  (`domain/scope.py`) para quando a política pede dado antes da fala. Sinistro e
  cancelamento escalam com o motivo certo; o piso não dispara em nenhuma mensagem de
  lead do dataset.

### Fixed — Tarefa 12

- A escada é descrita com três níveis (chamada com hedge, retry, escalação). O cache é
  descrito como camada preventiva: com TTL até a meia-noite e preço determinístico, ele
  nunca serviria de reserva depois de falha. Corrigido no README e na arquitetura.
- A arquitetura não lista mais webhook e console como adapters existentes.
- Mesma amostra, código atual: **72/150 cotadas, 72/74 elegíveis sem documento**, LLM
  cortado 0, 72/72 cotações consistentes com a tabela e com carência.
- Validação: **673 passed** no loop rápido; **686 passed** na suíte completa com o
  corpus; ruff e mypy limpos.

### Fixed — Tarefa 11, 2026-09-11

- Teto de LLM dimensionado por conversa (D-038): 7 s por chamada, o p99.9 do extrator em
  7.499 chamadas, e a chamada que estoura ou volta indisponível é refeita uma vez dentro
  do prazo do turno, que passa para 18 s. Configuração, contrato e tokens não são
  retentados. Mesmas 150 conversas: **64 → 73 cotadas (48,7%)**, elegíveis sem documento
  **73/74 (98,6%)**, LLM cortado **9 → 0**. O retry disparou uma vez e recuperou o turno;
  o teto antigo teria cortado 5 extrações na rodada. Turno p50 1,58 s, p99 5,05 s.
- Purga oportunista de slots (D-038): na partida e a cada turno, conversa aberta sem
  mensagem do lead há mais de 24 h é encerrada (slots, estado do grafo, status); lead que
  volta reabre. Cifrar o CEP em repouso fica descartado por decisão registrada.
- `.env.example` alinhado ao código: tetos de 7 s e `LLM_MEDIA_MODEL`.

### Added — Tarefa 11

- Inspeção por conversa: `python -m interfaces.trace --conversation <id>` lê, turno a
  turno, estado final do checkpointer, timeline, tentativas, mensagem ao lead e snapshot
  de escalação, só em leitura. `scripts/execution_log.py` gera
  `docs/execucao-completa.md` (qualificação, objeção, cotação com falha, lenta e hedge,
  carência e pro-rata) e `docs/execucao-escalacao.md` (escada esgotada e snapshot),
  reproduzíveis com `QUOTE_SEED` fixo.
- Comparação com a baseline humana, medida com o agente inteiro: **751/751 inelegíveis
  recusados** pela regra local, sem nenhuma chamada à `/quote` e nenhum preço (humano:
  0/751); **73/73 cotações consistentes com a tabela** no perfil real do lead e **73/73
  com carência mencionada** (humano: 0/2.500 em ambas).
- README revisado contra o código: comandos, árvore, grafo, escada, gatilhos e
  limitações passam a descrever o que existe; promessas não cumpridas viraram limitação.
- Validação: **651 passed** no loop rápido; **663 passed** na suíte completa com o
  corpus; ruff e mypy limpos.

### Fixed — Tarefa 10, 2026-09-11

- Disciplina de erro em todo cliente externo (D-035). A auditoria achou irmãos do 404
  do conversador: a cotação descartava o corpo e punha 401/404 junto com o 400 de
  payload; `/planos` transformava 401/404 em indisponibilidade e o guard falhava aberto
  em silêncio; os sinks de escalação viravam `HandoffDeliveryError()` sem status nem
  corpo e seriam retentados para sempre. Agora configuração (400/401/402/403/404/405/
  413/422 e 3xx) é distinta de transitório (408/425/429/5xx) e de contrato, falha alto,
  e o corpo redigido e truncado vai para o log e para o trace — `quote_attempts.erro`,
  `turn_events` (`<nó>_falha`) e o erro da outbox.
- Verificação de partida: `open_sales_stack` faz uma chamada mínima à API de cotação,
  ao extrator, ao conversador (mesmas tools e schema de produção) e ao modelo de mídia;
  credencial inválida ou parâmetro não aceito impedem a partida.

### Added — Tarefa 10

- CEP durável em `conversations.slots`, primeiro valor imutável; a cotação sobrevive a
  reinício com o CEP. Retenção: encerrar a conversa purga os slots e o estado do grafo;
  a recusa final encerra na hora (D-036). Invariante 8 do AGENTS.md ganha essa exceção.
- Pipeline de mídia (D-037) com `google/gemini-2.5-flash`, único aceito com imagem e
  áudio sob schema strict na sondagem. Imagem nunca escala; áudio sem transcrição pede
  texto e só escala no segundo; áudio transcrito exige confirmação; documento sempre
  escala e nunca sai do processo. O prompt pede só os cinco campos, por texto.
- Fixtures reais de mídia (duas fotos em domínio público, uma derivada escurecida e um
  áudio sintético pt-BR) e `scripts/probe_media.py`: veículo nítido → alta; veículo
  ruim → baixa; gato → não veículo; áudio transcrito com "anix" no lugar de "Ônix".
- Conclusão fim a fim, mesmas 150 conversas: **38/150 (25,3%) na 9.2 → 64/150
  (42,7%)**; elegíveis sem documento **64/74 (86,5%)**. Interrupções: recusa por regra
  44, documento 31, LLM 9, cotação indisponível 2, segundo áudio 0, prazo 0. 117 de 649
  turnos sintéticos; nenhuma fala pediu documento, foto ou CPF; custo US$ 0,71.
- Validação: **638 passed, 12 deselected in 8.08s** no loop rápido; **650 passed in
  35.10s** na suíte completa com o corpus; ruff e mypy limpos em 76 arquivos.

### Added — Tarefa 9, fechada na 9.2, 2026-09-11

- Grafo LangGraph `extract → policy → converse/quote → present → close | handoff`,
  checkpointer assíncrono no mesmo SQLite, lock por conversa e timeline por etapa
  (`turn_events`). Outbox de escalação com decisão e três efeitos persistidos antes da
  entrega. Composição única em `open_sales_stack`, usada pelo teste de snapshot e pela
  medição fim a fim.
- Avaliação isolada da extração, sem cotação nem orçamento de turno: **gpt-4.1-mini
  99,96% idade / 100% ano**; gpt-4.1-nano 89,32% / 97,88% com a mesma latência
  (mediana 1.465 contra 1.482 ms) — rejeitado com medição.
- Objeção classificada pelo conversador (`objecao`, seis categorias) com piso lexical.
  Conversas do dataset com objeção que chegam ao nó: **220/1.295 (17,0%) antes,
  1.295/1.295 depois**, 0 falso positivo em 13.386 outras mensagens de lead.
- Snapshot de escalação com as tentativas da conversa: o trace_id do turno chega à
  cadeia de cotação. Teste com SQLite e cadeia reais, API respondendo 503.
- Harness fim a fim `scripts/measure_end_to_end.py` e medições em
  `docs/measurements/task9-e2e-{medicao,antes,depois}.json`.

### Fixed — Tarefa 9.2

- O conversador nunca tinha funcionado contra o provedor: `parallel_tool_calls` com
  `require_parameters` dava 404 em 100% das chamadas, mascarado como erro de contrato.
  Removido; o prompt pede uma chamada de `cotar` por turno.
- A timeline gravava síncrona no event loop e travava o turno até o busy_timeout de 5 s
  quando o checkpointer escrevia. Extração com p50 de 6,1 s voltou a 1,7 s; política
  com p95 de 5,1 s voltou a 4 ms.

### Changed — Tarefa 9.2

- Orçamento recalibrado com medição (D-034): turno 6 → 10 s; extração 2,5 → 3,5 s;
  fala 3,0 → 4,5 s; LLM 2,0/2,5 → 4,5 s por chamada; tokens 4.000 → 16.000.
- Conclusão fim a fim, 150 conversas, mesmo código: **0/150 antes, 38/150 (25,3%)
  depois**; 38 das 42 elegíveis sem mídia (90,5%). Interrupções depois: mídia 63,
  recusa por regra 44, LLM 4, cotação indisponível 1, prazo 0. Turno depois: p50
  1,68 s, p95 4,42 s, p99 5,54 s, nenhum acima de 8 s.
- Validação: **589 passed, 12 deselected in 5.75s** no loop rápido; **601 passed in
  29.05s** na suíte completa com o corpus local; ruff e mypy limpos em 71 arquivos.

### Fixed — Tarefa 9.1, 2026-09-11

- Filtro direcional no conversador: a fala do lead chega inteira, só com PII
  redigida. O filtro antigo descartava **437 de 16.470 mensagens do lead (2,65%)**
  no dataset, uma em cada uma de 437 conversas, todas das objeções "a franquia ta
  alta" e "o preco ta salgado". As seis objeções canônicas e uma contraproposta
  numérica ("consigo por 180 na concorrente") agora chegam ao modelo.
- Guardrail de saída mira valor monetário, não dígito: "carência de 30 dias" e
  "assistência 24h" passam; `R$`, reais, decimal de duas casas e número colado a
  termo de valor caem para `render_safe_reply` e viram evento `guardrail` no trace
  com o texto ofensor. O turno não é mais derrubado.
- `plano_id` da tool `cotar` é enum fechado derivado de ProductFacts. Id fora do
  catálogo é erro de contrato registrado como `converse/contrato_llm`, com resposta
  de template; não chega à cadeia de cotação nem vira recusa comercial.
- Invariante testada no texto final: recusa sem valor nem linguagem de
  instabilidade; indisponível sem valor nem promessa de prazo; cotação com valores
  iguais ao payload por `Decimal`. Conversa completa em SQLite com cadeia real:
  só a apresentação tem valor, e só após linha `quoted` em `quote_attempts`.
  Duas tentativas do modelo de escrever preço foram contidas pelo guardrail.
- Validação: **526 passed, 11 deselected in 5.00s** no loop rápido; ruff limpo;
  mypy limpo em 69 arquivos. Ajustes na árvore da tarefa 9: schema agora lista
  `turn_events` e duas linhas longas foram quebradas para o ruff.

### Added — Tarefa 8, 2026-09-11

- Cliente OpenRouter por papel, schema Pydantic, timeout/orçamento e sinal
  determinístico de escalação por limite de tokens da conversa.
- Extrator isolado com prompt provisório, proveniência, ausência/incerteza,
  CEP privado e ano-modelo futuro intacto. Atualização incerta sem candidato
  preserva valores já coletados.
- 7.298 capturas reais dos 2.500 casos, gabaritos redigidos e replay offline:
  **idade 88,48% (2.212/2.500), ano 93,88% (2.347/2.500)**. Pisos de regressão
  de 88%/93%, explicitamente separados de SLO de produção.
- Nas 2.212 conversas concluídas, ambos os campos ficaram corretos; 288 (11,52%)
  tiveram indisponibilidade/prazo. Custo conhecido US$ 2,6678412; total estimado
  US$ 2,777447. Mediana/p95: 1.575,94/2.346,20 ms, incluindo falhas.
- Piloto nano em 24 casos preservado separadamente: não justificou trocar o mini;
  acrescentou US$ 0,0068511 conhecidos. Nenhuma captura foi fabricada.
- .env.example sem credenciais; .env ignorado. Auditoria final de 7.381 arquivos
  JSON não encontrou a chave nem PII reconhecida pelo redator. CEP privado
  recuperado em 2.500/2.500 conversas, sem perda de zero inicial.

### Changed — Tarefa 8

- Workspace migrado para ~/namastex-test-tecnico; original preservado.
  Mesmos 381 testes: 7,79 s em /mnt/c e 1,61 s no Linux.
- Timeout do budget agora é gravado com latência; cancelamento externo continua
  interrupção. Corrigida por TDD a perda de ano por incerto sem candidato,
  revelada em conv_00748; relatórios anteriores preservados para comparação.
- Validação final: **453 passed, 11 deselected in 2.22s** no loop rápido;
  **1 passed, 463 deselected in 20.96s** em eval por replay sem rede;
  **464 passed in 25.98s** na suíte completa. Ruff e mypy limpos (57 arquivos).
- A indisponibilidade de 11,52% ainda exige calibração antes de produção; os
  limites de tempo foram mantidos para expor a qualidade efetiva desta configuração.

### Added — Tarefa 7, 2026-09-11

- Envelopes de entrada e intenções de saída tipadas; ingestão com redação de PII,
  dedup persistido, janela de silêncio de 500 ms e consumo serial por conversa.
  Três turnos com objeção lexical de preço disparam escalação sem depender de LLM.
- Sete tabelas SQLite, identidade de canal pseudonimizada, hash opcional de CPF,
  repositórios por portas pequenas e migração transacional da FK de tentativas.
  Intenções de outbox e snapshots são persistidos sem executar efeitos; campos
  textuais são redigidos e Decimal é preservado em texto.
- Replay local ordenado por message_index, mídia não resolvida e saída redigida.
  Harness intercambiável carrega **2.500 conversas / 751 inelegíveis**; amostra
  estratificada redigida de 48 casos. Extrator falso retorna ausência: **0%** nos
  dois slots, sem consultar gabarito. Auditoria dos 2.500 CPFs: **FP=0 / FN=0**.

### Changed — Tarefa 7

- Cancelamento do chamador aguarda persistência + enfileiramento; cancelamento da
  barreira drena consumidores. Falha preserva turno para tentativa explícita sem
  duplicar objeções. Snapshots não relíveis são rejeitados antes de gravar.
- Pytest usa importlib: **381 passed, 10 deselected in 7.79s** no loop rápido.
  Suíte completa, incluindo estatísticos/corpus: **391 passed in 14.46s**.
  Coleta caiu de 4,34 s para 3,61 s; perfil aponta stat em /mnt/c como custo dominante.
  Ruff limpo em src/tests/scripts; mypy estrito limpo em 45 arquivos de src.
- AGENTS corrige identidade de canal e os comandos de TDD excluem slow.
  Decisões D-018 a D-023 e arquitetura documentam limites de memória/entrega.

### Added — Tarefa 6, 2026-09-11

- Apresentação pura de prêmio, franquia, coberturas, carência e pro-rata com
  Decimal e formato brasileiro. Doze goldens da API original cobrem os três
  planos, CEP normal/agravado e presença/ausência de pro-rata. Mensagens de recusa,
  indisponibilidade e transição têm redação provisória, sem LLM.
- Política determinística com sete regras ordenadas, laço configurável e decisão
  negativa auditável. Sugestão isolada do LLM não escala; divergência é preservada.
  Snapshot imutável reúne slots com proveniência e tentativas existentes, com CEP
  redigido na cópia. Recusa e erro de contrato não acionam cotação esgotada.
- Script mede insert/commit síncrono de trace em WAL/FULL: 500 amostras por local,
  mediana 6,398 ms e p99 9,481 ms no /tmp; 6,071 ms e 29,969 ms no workspace.

### Changed — Tarefa 6

- Worker mantido pela medição; drenagem automática na fronteira de ApplicationTrace,
  sem flush manual e fora do orçamento do retry. Cancelar worker e consumidor juntos
  não deixa barreiras pendentes. Overflow/erro continuam best effort.
- Simulações marcadas slow. Portão rápido: **324 passed, 8 deselected in 6.35s**
  com `uv run pytest -m "not slow"`, sem rede ou sleep real; 332 casos coletados.
  Ruff limpo em src/tests/scripts; mypy estrito limpo em 36 arquivos de src.
- ProductFacts definido no domínio e reexportado pelo parse de planos, mantendo
  o template independente de infraestrutura. D-015 a D-017 registram alternativas.

### Added — Tarefa 5, 2026-09-11

- Cadeia instrumentada como ApplicationTrace → Guard → Cache → Retry → Hedge →
  WireTrace → Http. Cada chamada física recebe sequência, status, HTTP, latência,
  hedge, normalização e classe de erro; o desfecho lógico registra api, cache ou
  regra_local. Correlação é isolada entre cotações concorrentes.
- SQLite acrescenta apenas quote_attempts e índice por trace_id; conversation_id
  ainda não tem FK. Entrega de eventos é não bloqueante, com fila limitada e
  drenagem explícita antes de inspeção/fechamento. Erros não expõem payload ou PII.
- `python -m interfaces.trace <trace_id> --database ...` apresenta tentativas
  ordenadas e desfecho em texto, usando caso de uso e leitura SQLite somente leitura.
  Pacotes planos e schema.sql são instalados por uv sync.
- Scripts de medição e demonstração real ficam em scripts/, fora da suíte.
  Amostras de latência e saída de inspeção real estão em docs/measurements/.

### Changed — Tarefa 5

- Medição real de 500 chamadas após 20 warmup: mediana 15,88 ms, p95 29,17 ms,
  p99 44,73 ms e máximo 98,96 ms, API sem falhas/lentidão na porta local 18000.
- Hedge de 1,5 s para 100 ms; jitter constante 0–20 ms substitui crescimento
  exponencial na configuração de produção. Mantidos timeout de 2 s e três tentativas.
- Em 10.000 execuções por cenário, antes/depois: sem hedge/sem corte 2,72%/2,72%;
  com hedge/sem corte 1,18%/1,18%; sem hedge/3,5 s 3,29%/3,29%; com hedge/3,5 s
  2,43%/1,27%. A diferença restante de 0,09 ponto percentual vem de combinações
  de lentidão que ainda consomem mais de uma rodada de timeout.

### Validation — Tarefa 5

- **285 testes passaram em 11,85 s**, sem rede, Docker ou sleep real; os scripts
  de medição/demonstração foram executados separadamente contra a API real.
  Ruff limpo em src/tests/scripts; mypy sem erros em 32 arquivos de código.
- Revisão identificou recusa rápida virando indisponibilidade por espera do
  recorder. Corrigido com entrega não bloqueante: sink de 4 s não altera recusa
  em 10 ms nem dispara hedge extra. Fila cheia, falha e cancelamento de flush cobertos.
- Metadado de ano em chamada cancelada usa calendário da API mesmo na virada
  UTC; regressão reproduzida antes da correção. Revisão final sem defeitos importantes.
- Inspeção em outro processo confirmou uma cotação real com HTTP 200 e uma
  resolução posterior de cache sem tentativa física. A fila foi drenada antes.
- Demais tabelas, grafo, prompts, adapters de atendimento e console continuam fora.
  Deadline do turno completo ainda precisa incluir catálogo e cache. Overflow
  ou encerramento abrupto podem perder eventos de trace pendentes.

### Added — Tarefa 4, 2026-09-11

- Cadeia Guard → Cache → Retry → Hedge → Http composta em um único wiring,
  com dependências injetadas e orçamento de cotação configurável de 3,5 s.
- Perfis inelegíveis são recusados localmente sem HTTP nem cache. Falha no
  carregamento de regras deixa a API decidir; request e CEP são preservados.
- Cotações e recusas sobrevivem a restart no cache SQLite, expiram à meia-noite
  original e carregam origem explícita. Erro de cache degrada desempenho sem
  esconder o resultado; logs não incluem payload, CEP ou traceback.
- Startup aplica schema idempotente contendo somente quote_cache. Dinheiro é
  TEXT no JSON, preservando precisão e zeros finais; WAL, busy_timeout e
  foreign_keys são configurados. Em memória o SQLite usa journal_mode=memory.
- Operações SQLite ficam fora do event loop. Cancelamento aguarda o worker antes
  de propagar, evitando fechar uma conexão ainda em uso.

### Validation — Tarefa 4

- **260 testes passaram em 6,96 s**, sem rede, Docker ou sleep real. Ruff limpo
  e mypy sem erros em 22 arquivos de código. Inclui 35 casos adicionais líquidos,
  com testes escritos e observados falhando antes dos componentes novos.
- Em 10.000 execuções por cenário: sem corte por tempo, **2,72% sem hedge** e
  **1,18% com hedge**; orçamento de produção de 3,5 s, **3,29% sem hedge** e
  **2,43% com hedge**. Sementes fixas e tempo virtual, cerca de 4 s de processamento
  nos quatro testes estatísticos. README registra contagens e metodologia.
- O aumento de 1,25 ponto percentual com hedge justifica rever timeout/janela
  após medir latência dos sucessos reais; defaults de 2 s e três tentativas
  mantidos, sem otimizar artificialmente o duplo de sucesso imediato.
- Regressão de fechamento da conexão durante escrita cancelada reproduzida e
  corrigida; revisão independente da implementação e da correção concluída.
- Trace, outras tabelas, grafo, prompts, adapters e console continuam fora.
  Deadline de turno completo ainda precisa incluir catálogo/cache, além do retry.

### Added — Tarefa 3, 2026-09-11

- Retry com backoff exponencial, full jitter, teto de tentativas e orçamento
  absoluto que também cancela chamadas em andamento. Recusas e erros de contrato
  atravessam sem nova tentativa; esgotamento informa a contagem realizada.
- Falhas exclusivamente suspeitas promovem erro de contrato no esgotamento,
  com limiar configurável de três tentativas. A evidência inclui ambas as
  chamadas hedgeadas, sem perder a classificação individual da última falha.
- Hedge de latência dispara no máximo uma segunda chamada, aceita cotação ou
  recusa e cancela e aguarda as tarefas restantes. Erro de contrato tem prioridade;
  falha rápida não dispara hedge. Composição verificada como Retry(Hedge(folha)).

### Validation — Tarefa 3

- **225 testes passaram em 4,02 s**, incluindo 63 novos. Ruff limpo e mypy
  sem erros nos 15 arquivos de código. Sem rede, Docker ou sleep real.
- Em 10.000 execuções por configuração: retry de três tentativas sem hedge
  apresentou **2,72%** de falha residual (272 falhas, 13.822 chamadas físicas);
  com hedge, **1,18%** (118 falhas, 14.036 chamadas físicas).
- Simulação com sementes fixas, timeout virtual de 2 s, janela de 1,5 s e
  orçamento de 20 s para permitir três tentativas. Esses resultados não medem
  o futuro orçamento de conversa de 6 s. Os dois testes estatísticos consomem
  cerca de 2 s de CPU no conjunto, sem espera real.
- Regressões de evidência suspeita agregada e início do deadline reproduzidas
  antes da correção. Cancelamento, conclusões simultâneas e orçamento verificados
  com agendamento virtual; o suporte depende de internals do asyncio no Python 3.12.
- Guard, cache de cotação, trace, persistência, grafo, prompts e adapters
  permanecem fora desta fase.

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
  movido para `~/.venvs/namastex-test-tecnico-domain`, mantendo `.venv`
  como link local ignorado pelo Git. Após a mudança, a primeira execução levou
  2,23 s e a seguinte 1,90 s; o limite depende também do custo de inicialização
  e do filesystem, não apenas dos testes. Em outros ambientes basta `uv sync`;
  o código não depende desse caminho local.

### Known limitations

- O guard aceita ano-modelo futuro como solicitado, mas a API original ainda
  pode recusar esse mesmo perfil. Não houve alteração no serviço do desafio.
- HTTP, resiliência, cache concreto, persistência, grafo, LLM, templates e
  adapters continuam fora desta fase.
