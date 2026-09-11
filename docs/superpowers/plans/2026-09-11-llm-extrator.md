# Tarefa 8 — Cliente LLM e extração isolada

**Objetivo:** cliente por papel, slots estruturados e medição reproduzível sem
expor PII nem preços ao extrator. Spec: tarefa 8 da conversa e invariantes AGENTS.
**Workspace:** /home/rafael/namastex-test-tecnico, branch feat/llm-extrator;
cópia /mnt/c preservada como origem da migração. Base 1e236c7.

- [x] Copiar repo para Linux, criar venv próprio e remedir suíte anterior.
- [x] Proteger .env; credencial apenas por ambiente; instalar Pydantic.
- [ ] Porta application/llm.py: LLMRequest(conversation_id, role, system, user,
  schema, budget), LLMResponse(content, model, prompt_tokens, completion_tokens,
  cost, latency_ms). LLMClient.complete(request). LLMRole extrator/conversador.
- [ ] infrastructure/llm: OpenRouter via httpx, modelos por papel, timeout/budget,
  contagem por conversa e TokenBudgetExceeded. Testes MockTransport e relógio falso.
- [ ] agent/schemas/slots.py: cinco slots opcionais, cada presente com valor,
  status informado/incerto e proveniência digitado/transcrito. StrictStr CEP,
  zfill sete dígitos; ano futuro preservado. Erros públicos sem conteúdo de entrada.
- [ ] agent/nodes/extract.py: extrair mensagem atual + estado; captura privada de
  CEP antes de redigir; estado enviado ao LLM exclui CEP. Atualizações ausentes
  preservam anteriores, proveniência vem da entrada. Prompt provisório versionado.
- [ ] Gravação/reprodução por hash de request/modelo/prompt/schema; fixtures sem
  credenciais ou prompt pessoal. Reprodução ausente falha, nunca chama rede.
- [ ] Adaptar harness para idade/ano-modelo/CEP privado, usar mensagens ordenadas
  por índice sem labels na entrada. Eval marcado slow+eval, padrão replay.
- [ ] Rodar avaliação paga quando OPENROUTER_API_KEY estiver disponível; gravar
  respostas reais, custo/tokens, mediana/p95, erros por formato e limites medidos.
- [ ] Revisões e portões: pytest not slow, pytest eval em replay, Ruff/mypy;
  atualizar README, arquitetura 4.1/4.2, DECISIONS e CHANGELOG.

## Decisões de execução

As seções 4.1/4.2 citadas não existem na main atual; escopo da tarefa é referência.
PII impede envio do CEP completo ao LLM: captura determinística privada preserva
o valor; schema continua rejeitando inteiro. Capturas devem vir do provedor real;
sem credencial não inventar respostas ou métricas de acurácia.

## Estado de entrega offline

Implementação e revisão concluídas; 448 testes rápidos verdes em 1,56 s,
458 não-eval em 9,26 s. Ruff/mypy limpos. Avaliação real, capturas e limiares
permanecem bloqueados pela ausência de OPENROUTER_API_KEY. Não marcar tarefa
completa até medir e reproduzir os 2.500 casos.

## Avaliação concluída

Credencial configurada localmente; 2.500 conversas gravadas e reproduzidas offline.
453 testes rápidos verdes em 2,22 s, eval verde em 20,96 s e 464 testes completos
em 25,98 s. Ruff/mypy limpos. README e decisões registram 88,48%/93,88%, custos,
latências e limiares. Não há pendência de credencial/capturas; permanece a
limitação operacional de 11,52% de conversas interrompidas nesta configuração.
