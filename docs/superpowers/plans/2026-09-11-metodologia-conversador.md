# Metodologia, orçamento e conversador — Implementation Plan

> Execução: superpowers:subagent-driven-development; tarefas independentes revisadas antes da integração.

**Goal:** medir extração isolada e conclusão fim a fim separadamente; fechar conversador, grafo persistente e outbox.
**Architecture:** LangGraph assíncrono coordena regras puras, cadeia de cotação existente e intenções. SQLite compartilhado por conexões dedicadas, efeitos duráveis separados da decisão.
**Tech Stack:** Python 3.12, httpx/OpenRouter, Pydantic, LangGraph e AsyncSqliteSaver fixados no uv.lock.
**Spec:** Tarefa 9 fornecida pelo usuário nesta sessão; AGENTS.md e arquitetura 4/13.

## Global Constraints

- Nenhum preço sai do LLM. Quote completo só entra no template; conversador vê projeção sem números monetários.
- A tool cotar aceita somente plano_id; os demais campos vêm do estado, inclusive CEP privado imutável.
- Recusa local não é handoff e não invoca conversador nem HTTP de cotação.
- PII não aparece em prompt, log ou persistência em claro. Tratamento durável de CEP aguarda interpretação do usuário.
- Testes rápidos sem rede, sleep real ou LLM; TDD para comportamento determinístico.
- Avaliação isolada não importa/cota, não roda grafo e não aplica orçamento de turno/conversa. Timeout técnico de transporte continua finito e falhas de coleta são reportadas separadamente.
- Métrica da tarefa 8 teve falhas LLM, não cotação. Manter evidências anteriores e corrigir nomenclatura.
- Decisão e três efeitos são persistidos atomicamente antes de entrega; lead, webhook_vendas, api_fila; retry individual e idempotência por efeito.
- Não publicar nem misturar esta tarefa à PR 8. Branch feat/metodologia-conversador, base ab4351b.

## Task 1: Extração isolada

- [ ] Adicionar harness independente em tests/golden/isolated.py e entrada scripts/evaluate_isolated.py.
- [ ] Usar SlotExtractor com cliente sem BudgetedLLMClient; timeout técnico configurável (30 s inicial), sem prazo do turno. Alimentar rajadas do lead e acumular slots sem gabarito no contexto.
- [ ] Reportar acurácia por slot, cobertura, erros de coleta separados de erros semânticos, tokens, custo e latência. Não excluir silenciosamente casos sem resposta.
- [ ] Gravação/replay por modelo, retomada de falhas de coleta explícita. Preservar fixtures da tarefa 8 e compatibilidade dos hashes.
- [ ] Testes RED/GREEN sem rede para diferenciação de métricas, ausência de orçamento de conversa, acumulação e falta de fixture fatal.
- [ ] Medir mini e nano com o mesmo corpus; root executa as chamadas reais após validar o harness.

## Task 2: Conversador e tool

- [ ] Acrescentar suporte a definições/chamadas de tools na porta LLM e HTTP, mantendo hashes antigos quando tools estão ausentes.
- [ ] ConversationContext de linguagem recebe persona, histórico redigido, ProductFacts e projeção; nunca Quote bruto.
- [ ] Conversador devolve texto, enum de sugestão e eventual pedido cotar(plano_id); chamadas inesperadas/argumentos extras são erro de contrato.
- [ ] Projeção de Quote/Declined/Unavailable contém apenas status, nome, coberturas e presença de carência.
- [ ] Casos com Quote/Declined/Unavailable usam templates determinísticos na apresentação. Texto livre com números ou dinheiro é rejeitado/substituído por resposta segura.
- [ ] Validar saída estruturada, isolamento e taxonomia com MockTransport e duplo.

## Task 3: Outbox de escalação

- [ ] Portas pequenas e HandoffSink.emit(decision); três implementações para mensagem ao lead, webhook e API local de fila.
- [ ] Persistir decisão + três intenções na mesma transação; identificador estável evita duplicação após replay/restart.
- [ ] Dispatcher tenta lead primeiro, mas falha não impede outros destinos. Entregues não repetem; falhos são retentados individualmente com Clock/sleep injetados.
- [ ] Persistir tentativas, erro redigido e próxima tentativa por efeito; preservar API de SQLiteDelivery usada pelos testes existentes.
- [ ] Testar ordem, atomicidade, falhas independentes, retomada, snapshot completo e idempotência.

## Task 4: Grafo e ciclo do turno

- [ ] Fixar e verificar AsyncSqliteSaver; mesmo arquivo SQLite, conexão async dedicada, WAL/busy_timeout/foreign_keys.
- [ ] Estado serializável inclui slots/proveniência, histórico redigido, status, correlação, métricas de etapas e controle de objeções/laço.
- [ ] Caso de uso serializa por conversa, liga ingestão ao grafo e preserva CEP antes da redação. Privacidade de CEP depende da decisão do usuário.
- [ ] Rotas extract -> policy -> converse/tool -> quote -> present -> close/handoff. Objeção retorna à coleta no turno seguinte, sem loop ilimitado no mesmo input.
- [ ] Guard local avalia aceitação antes do conversador. Campos de qualificação ausentes usam pedido determinístico; plano pode ser escolhido via tool do conversador.
- [ ] Prazo único propagado: candidato 10 s, extração/quote/conversação medidos; 6 s como comparação. Timeout/indisponibilidade LLM e quote distinguíveis.
- [ ] Persistência sobrevive a reinício; sem paralelismo na mesma conversa; snapshot consulta QuoteAttempt existentes.

## Task 5: Medição fim a fim e entrega

- [ ] Rodar conversa real do dataset pelo grafo e API local, inspecionar timeline.
- [ ] Medir conclusão e interrupções separando recusa, falha de LLM, cotação e prazo; informar amostra e condições reais versus simuladas.
- [ ] Medir antes/depois do orçamento e tempos de extração, cotação e fala; escolher modelo com evidência da Task 1.
- [ ] README, DECISIONS append-only, arquitetura e CHANGELOG atualizados; testes rápidos, eval offline, ruff e mypy verdes.

## Ledger

- Base limpa: ab4351b; task 8 teve 464 testes verdes. Branch nova no workspace Linux existente.
- Ruling: isolamento nesta branch, sem novo diretório; mantém a configuração local e evita misturar alterações na PR 8.
- Ruling: a especificação já autoriza implementação e medição. Só a interpretação da persistência de CEP foi perguntada porque envolve a invariante de PII.
- Dependências: Task 1/2 compartilham LLMRequest; campos opcionais novos não podem alterar hashes legados. Task 3/4 compartilham HandoffDecision e SQLiteDelivery, preservando codecs. Tasks 1/5 separam coleta de inferência e prazo de serviço.
