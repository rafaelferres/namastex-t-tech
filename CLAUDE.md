# CLAUDE.md

@AGENTS.md

O arquivo acima é a fonte de verdade deste repositório: invariantes, arquitetura,
armadilhas medidas no dataset e comandos. Leia por completo antes de escrever
código. O que segue são apenas notas específicas do Claude Code.

## As três coisas que mais quebram aqui

Se você ler só um trecho, leia este.

1. **Nenhum valor monetário sai do LLM.** Prêmio, franquia e pro-rata vêm do
   payload da `/quote`, renderizados por template. Nunca escreva um número em
   prompt, exemplo, docstring ou fixture como se fosse preço real.
2. **`422` e `400` não são retentados.** Recusa é resultado (`Declined`), não
   exceção. Só `5xx` e timeout entram na escada de retry.
3. **`base_mensal` e multiplicadores nunca saem da API.** São descartados no parse
   de `/planos`. Se você se pegar precisando deles, a tarefa está errada.

## Trabalhando neste repo

**Antes de alterar a cadeia de cotação**, leia
`src/infrastructure/quote/` inteiro. São seis decorators pequenos, cada um com um
papel. Comportamento novo entra como decorator novo, não como `if` dentro de um
existente.

**Antes de mexer em prompt ou template**, confira o que está em
`src/agent/prompts/` versus `src/agent/templates/`. A fronteira é rígida:
prompt carrega comportamento, template carrega fato. Nunca migre um número de um
para o outro.

**Ao adicionar regra de handoff**, crie uma classe nova em `src/domain/handoff.py`
implementando o `Protocol` e registre na política. Não adicione condição a uma
regra existente.

**Ao tocar em qualquer coisa que leia o dataset**, ordene por `message_index`.
`timestamp` não é monotônico em 99,8% das conversas.

## TDD é o modo de trabalho padrão

Para qualquer código em `src/domain/`, `src/application/` ou
`src/infrastructure/`:

1. Escreva o teste que falha. Rode e **veja falhar** — teste que passa antes da
   implementação não está testando o que você acha.
2. Escreva o mínimo para passar.
3. Refatore com o teste verde.

Não escreva a implementação primeiro e o teste depois "para documentar". Se você
já escreveu a implementação, diga isso explicitamente em vez de apresentar como
TDD.

Comece pelo caso de recusa e pelo caso de falha, não pelo caminho feliz. É onde
este projeto é avaliado.

Loop rápido enquanto desenvolve:

```bash
uv run pytest -m "not slow"
```

Se esse comando levar mais que alguns segundos, algum teste ganhou I/O, `sleep`
real ou relógio de sistema. Conserte injetando `Clock`, `sleep` e o gerador
aleatório por construtor.

### Onde TDD não vale

Extração de slots e geração de fala dependem de LLM — isso é avaliação com
limiar (`tests/golden`), não teste binário. Não tente forçar vermelho-verde em
prompt. A exceção é `tests/regression`: as 751 conversas inelegíveis têm critério
binário, o agente recusa todas.

## Testes

Rode `uv run pytest` antes de considerar qualquer tarefa concluída.

Teste que envolve a API de cotação precisa de `QUOTE_SEED` fixo ou de um duplo de
teste. Teste que depende do sorteio de falha é flaky por construção e será
rejeitado.

A escada de degradação inteira é testável **sem Docker**: programe o duplo da
folha para falhar N vezes. Use o Docker só para o end-to-end:

```bash
QUOTE_FAILURE_RATE=1.0 docker compose up
```

## Adapters: CLI, console Streamlit, replay e trace

São os únicos que existem, em `src/interfaces/`. Não há webhook de WhatsApp — não
tente consertar nem estender o que não está no código.

`src/interfaces/cli.py` e `src/interfaces/streamlit_app.py` são adapters, não um
segundo cérebro. Eles chamam os mesmos casos de uso que o replay, pela composição
`open_live_stack`. `tests/unit/test_console.py` verifica as regras do console no
código-fonte: imports, ponte única, `session_state` só com `thread_id` e avaliação pelas
funções de `tests/`.

Se você precisar de lógica nova para um adapter funcionar, ela vai para
`src/application/` ou para o wiring e ganha teste. Nunca para o adapter.

Três coisas que quebram silenciosamente aqui:

- estado de conversa no adapter — guarde só o id da conversa (`thread_id` em
  `st.session_state`), o resto vive no checkpointer
- `asyncio.run()` espalhado — a CLI tem uma ponte única, em `main`; o console usa só
  `AsyncBridge.run`
- PII na saída — `--trace`, a inspeção e o painel de trace renderizam só texto redigido

Métrica de avaliação vive em `tests/golden`, `tests/regression` e `scripts/`.
Métrica que só existe num adapter não vale.

## Verificações antes de terminar

Rode mentalmente antes de dizer que acabou:

- Algum valor monetário foi gerado fora de template?
- Algum `except` captura `Declined` ou `QuoteContractError` junto com
  `QuoteUnavailable`?
- Algum log, exceção ou prompt carrega CPF, e-mail, telefone, placa ou CEP completo?
- Alguma chamada à `/quote` foi montada sem o `cep` que já estava no estado?

## Escopo

Este projeto é uma entrega de desafio técnico com prazo curto. Prefira a solução
menor que satisfaz as invariantes.

Não adicione: vector database, circuit breaker, calculador local de prêmio,
modelo de propensão ou scoring de lead, memória de longo prazo. Cada um desses foi
considerado e descartado por um motivo registrado no README — reintroduzir sem
discutir desfaz uma decisão deliberada.

Se uma tarefa parecer exigir quebrar uma invariante, pare e pergunte em vez de
encontrar um contorno.