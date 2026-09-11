# Tarefa 7 — Envelope, ingestão, privacidade e replay

**Goal:** entrada canônica persistida com privacidade e harness intercambiável sem LLM.
**Spec:** tarefa 7 da conversa; AGENTS.md e arquitetura seções 3, 11 e 12.
**Architecture:** domínio puro; ingestão por portas pequenas; SQLite e redação como
adapters. Replay só traduz parquet para envelopes. Avaliação compartilha casos e
métrica entre amostra rápida e conjunto completo.
**Tech Stack:** Python 3.12, SQLite, asyncio, pyarrow para parquet, pytest.

## Restrições e decisões de execução

- Identidade `(channel, channel_user_id)` supera a menção antiga a chave CPF.
- Sem LLM, grafo, resolução de mídia ou entrega da outbox nesta tarefa.
- PII redigida antes de gravar ou entregar ao consumidor. CPF válido gera hash opcional.
- Domínio/application não importam SQLite ou pyarrow; relógio e sleep injetados.
- Três objeções de preço por conversa disparam a regra, independente do modelo.
- Testes nas interfaces públicas: política, redator/logger, repositórios,
  ingestão, replay e métricas. Vermelho observado antes de implementar cada fatia.

## Sequência

- [x] Envelopes em domain/messages.py e piso da regra em domain/handoff.py.
  InboundMessage(channel, conversation_id, channel_user_id, tipo, corpo,
  provider_message_id, indice, media_ref=None); mídia não resolvida por propriedade.
  OutboundMessage usa intenção enumerada e payload tipado, sem texto de canal.
- [x] PrivacyRedactor em infrastructure/privacy: redact(text)->str e
  cpf_hash(text)->str|None; formatter de logging redige inclusive traceback.
  Testes de CPF válido/inválido, ordem/grafia e demais PII.
- [x] Schema completo e migração transacional da FK de quote_attempts; preservar
  trace legado mediante conversas de migração explicitamente identificadas.
  Portas ConversationReader/Writer e MessageReader/Writer, implementação SQLite.
  Testar identidade, dedup, hash e integridade da migração.
- [x] Ingestor redige primeiro, persiste/dedup, agrupa por silêncio configurável
  e serializa consumo por conversa. Testes com eventos/relógio virtual, seis
  fragmentos, isolamento e ausência de execução paralela na mesma conversa.
- [x] Replay lê parquet real ordenado por message_index, somente mensagens de lead.
  Harness recebe extractor por protocolo, sem gabarito na entrada; reporta idade
  e veiculo_texto. Amostra estratificada versionada e corpus completo local slow.
- [x] Revisão de privacidade e integração; suíte rápida, corpus completo, lint,
  mypy; atualizar README, arquitetura, decisões e changelog com medições.

## Registro de progresso

Branch inicial f80162f, feat/ingestao-replay. Dataset original disponível em
../namastex-fde-challenge/dataset/conversations.parquet; não versionar PII bruta.


Concluído: 381 testes rápidos/391 totais coletados; corpus 2500/751, amostra48.
Revisões encontraram e corrigiram: PII em snapshot direto, payload de tentativa
não relível, perda de turno em erro/cancelamento e objeção dividida em fragmentos.
Ciclos red/green registrados na execução. Importlib reduz coleta sem remover testes;
loop rápido medido em 7,79s neste workspace /mnt/c. Docs D-018 a D-023.
