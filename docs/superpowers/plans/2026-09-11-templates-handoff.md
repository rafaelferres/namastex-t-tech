# Tarefa 6 — Templates e política de escalação

**Objetivo:** núcleo determinístico de apresentação e decisão, sem LLM ou rede nova.
**Especificação:** tarefa 6 da conversa; AGENTS.md e seções 8/9 da arquitetura.
**Execução:** TDD nas interfaces públicas; módulos puros; registros de API reais.

- [x] Marcar simulações slow e medir insert/commit síncrono em WAL/FULL.
- [x] Amarrar entrega dos eventos à fronteira lógica de ApplicationTrace, sem flush manual.
- [x] Capturar fixtures dos três planos × CEP normal/agravado × pro-rata ausente/presente
  executando a implementação original da API offline; escrever goldens revisáveis.
- [x] Testar e implementar renderização pura em agent/templates, Decimal sem float,
  carência derivada do payload, pro-rata opcional e textos provisórios específicos.
- [x] Modelar contexto mínimo com cinco slots/proveniência já definidos na arquitetura,
  e protocolo de leitura dos registros QuoteAttempt existentes, sem duplicar tentativas.
- [x] Testar cada regra e política ordenada; decisão positiva/negativa guarda sinal LLM,
  divergência e snapshot imutável. Sinais explícitos, sem classificação de texto nesta fase.
- [x] Revisão, suíte rápida, Ruff/mypy; atualizar decisões, arquitetura e changelog.

A medição justificou manter o worker: medianas de ~6 ms, p99 de até ~30 ms;
não é custo desprezível junto à janela de hedge de 100 ms. Drenagem automática
por trace_id, fora do retry, antes de concluir a chamada lógica.
