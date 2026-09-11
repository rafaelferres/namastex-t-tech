# Task 1 — Extração isolada

## Entrega

- `tests/golden/isolated.py` executa `SlotExtractor` diretamente, sem
  `BudgetedLLMClient`, orçamento de turno ou limite de tokens por conversa.
- As rajadas do lead são fornecidas em ordem e os slots são acumulados até idade
  e ano estarem informados ou as rajadas terminarem.
- O timeout técnico é finito e configurável, com padrão de 30 s. Ele é usado no
  request do extrator e na configuração de transporte durante gravações reais.
- O relatório conserva todos os casos selecionados. Falhas de coleta têm
  cobertura, taxonomia e ids próprios. Acurácia semântica usa explicitamente os
  casos coletados; `accuracy_all_cases` usa o corpus selecionado e preserva slots
  corretos obtidos antes de uma falha posterior.
- Uso registra chamadas, respostas com uso, tokens, custo conhecido/completude,
  latência mediana/p95 e modelos observados.
- Capturas ficam em um diretório estável por modelo. Replay nunca usa rede e
  fixture ausente continua fatal.
- `--retry-collection-failures` move capturas de erro e o relatório anterior para
  `history/retry-NNNN`, fora de `responses`. O relatório novo declara que as
  tentativas de falha arquivadas não integram suas métricas de uso.
- `scripts/evaluate_isolated.py` suporta `--model`, `--directory`,
  `--concurrency`, `--limit`, corpus completo por padrão, `--timeout`, modo
  record/replay e retomada explícita.

O harness delimitado anterior em `tests/golden/evaluation.py` e
`tests/golden/runner.py` não foi alterado. Nenhuma avaliação paga foi executada.

## Evidência RED/GREEN

Primeiro RED:

```text
rtk uv run pytest tests/unit/test_isolated_evaluation.py -q
5 failed — ModuleNotFoundError: tests.golden.isolated
```

Segundo RED, após a revisão dos denominadores e da preservação de evidência:

```text
rtk uv run pytest tests/unit/test_isolated_evaluation.py -q
2 failed — contrato antigo de métricas e função de arquivo ainda ausente
```

GREEN focado:

```text
rtk uv run pytest tests/unit/test_isolated_evaluation.py -q
6 passed in 0.13s
```

Verificação final do harness novo e APIs legadas relacionadas:

```text
rtk uv run pytest tests/unit/test_isolated_evaluation.py tests/unit/test_evaluation_llm.py tests/golden/test_harness.py -q
21 passed in 0.16s

rtk uv run ruff check tests/golden/isolated.py scripts/evaluate_isolated.py tests/unit/test_isolated_evaluation.py
All checks passed!

rtk uv run mypy tests/golden/isolated.py scripts/evaluate_isolated.py
Success: no issues found in 2 source files

rtk git diff --check -- tests/golden/isolated.py scripts/evaluate_isolated.py tests/unit/test_isolated_evaluation.py
exit 0
```

## Comandos para a medição real

Com `OPENROUTER_API_KEY` já exportada no ambiente, sem inserir a chave na linha de
comando:

```bash
rtk uv run python -m scripts.evaluate_isolated \
  --mode record \
  --model openai/gpt-4.1-mini \
  --directory tests/fixtures/llm-isolated \
  --concurrency 16 \
  --timeout 30

rtk uv run python -m scripts.evaluate_isolated \
  --mode record \
  --model openai/gpt-4.1-nano \
  --directory tests/fixtures/llm-isolated \
  --concurrency 16 \
  --timeout 30
```

Os mesmos comandos com `--mode replay` não exigem credencial e não fazem
fallback para rede. Uma gravação interrompida pode ser retomada acrescentando
`--retry-collection-failures` ao comando `record`; respostas bem-sucedidas são
reutilizadas e a evidência das falhas anteriores permanece no histórico.
