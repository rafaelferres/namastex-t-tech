# Execução com escalação

Conversa real do dataset, `conv_00076`, pelo agente completo. O corpo abaixo
é a saída do comando de inspeção sobre o banco que a execução deixou:

```bash
uv run python -m interfaces.trace --conversation conv_00076 --database /tmp/exec-escalacao.sqlite
```

## Como reproduzir

```bash
# 1. API do desafio numa instância nova: o sorteio começa na primeira /quote
cd ../namastex-fde-challenge/quote-service && QUOTE_SEED=1 QUOTE_FAILURE_RATE=1.0 uv run --with fastapi --with uvicorn uvicorn app.main:app --host 127.0.0.1 --port 18012
# 2. a conversa pelo agente, e este documento
AUTOSEGURO_DATASET=<conversations.parquet> uv run --env-file .env python -m scripts.execution_log --conversation conv_00076 --quote-url http://127.0.0.1:18012 --quote-env 'QUOTE_SEED=1 QUOTE_FAILURE_RATE=1.0' --title 'Execução com escalação' --output docs/execucao-escalacao.md
```

O que se repete e o que não:

- **Sorteio da API**: fixo por `QUOTE_SEED=1 QUOTE_FAILURE_RATE=1.0`, desde que a instância seja nova e
  esta seja a única conversa contra ela.
- **Hedge**: depende de relógio de parede (a chamada lenta dorme 8 s, o hedge dispara
  em ~1,5 s); estável na prática, não por construção.
- **Texto do LLM**: extrator e conversador são modelos reais; a redação da fala e, em
  raros casos, um slot podem variar entre execuções. Preço, franquia, carência e
  pro-rata nunca variam: vêm do payload da `/quote` por template.

## Configuração

- Extrator `openai/gpt-4.1-mini`, conversador `openai/gpt-4.1` (OpenRouter)
- Turno de 18 s; teto por chamada de LLM de 7 s com um retry (D-038); cotação até 3,5 s
- Limite de 16.000 tokens por conversa; timeout HTTP do LLM de 7 s

## Resultado

- Desfecho: `indisponibilidade`, em 5 turnos
- Turnos com mensagem do harness, não do dataset (o dataset não traz data de vigência): 4, 5
- Cotação conferida contra a tabela no perfil real do lead: não se aplica, nenhuma cotação foi apresentada

## Conversa `conv_00076`

### Turno 1

`conv_00076:conv_00076:0` · rota: extract → policy

**Mensagem do lead** (PII redigida na ingestão)

> Ola! Vi o anuncio de voces, quanto fica o seguro?

**Slots após a extração**

nenhum slot coletado ainda

**Políticas**

- Aceitação: não avaliada: falta idade e ano-modelo
- Escalação: nenhuma regra disparou
- Falta dado: `idade`, pedido ao lead

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 1333 ms | — |
| policy | ativa | 2 ms | — |

**Enviado ao lead**

> Pode informar ou confirmar sua idade?

### Turno 2

`conv_00076:conv_00076:2` · rota: extract → policy

**Mensagem do lead** (PII redigida na ingestão)

> Corolla, ano 2023

**Slots após a extração**

| slot | valor | status | proveniência |
|---|---|---|---|
| veiculo_ano | 2023 | informado | digitado |

**Políticas**

- Aceitação: não avaliada: falta idade
- Escalação: nenhuma regra disparou
- Falta dado: `idade`, pedido ao lead

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 1476 ms | — |
| policy | ativa | 3 ms | — |

**Enviado ao lead**

> Pode informar ou confirmar sua idade?

### Turno 3

`conv_00076:conv_00076:5` · rota: extract → policy

**Mensagem do lead** (PII redigida na ingestão)

> Tenho 64 anos, cpf [CPF], cep [CEP]
> qualquer coisa me chama

**Slots após a extração**

| slot | valor | status | proveniência |
|---|---|---|---|
| idade | 64 | informado | digitado |
| veiculo_ano | 2023 | informado | digitado |

**Políticas**

- Aceitação: dentro das regras (idade 64, ano-modelo 2023)
- Escalação: nenhuma regra disparou
- Falta dado: `data_inicio`, pedido ao lead

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 1540 ms | — |
| policy | ativa | 2 ms | — |

**Enviado ao lead**

> Pode informar ou confirmar a data desejada para início da vigência?

### Turno 4

`conv_00076:conv_00076:9` · rota: extract → policy → converse

**Mensagem do lead** (PII redigida na ingestão)

> Pode começar em 15/10/2026.

**Slots após a extração**

| slot | valor | status | proveniência |
|---|---|---|---|
| idade | 64 | informado | digitado |
| veiculo_ano | 2023 | informado | digitado |
| data_inicio | 2026-10-15 | informado | digitado |

**Políticas**

- Aceitação: dentro das regras (idade 64, ano-modelo 2023)
- Escalação: nenhuma regra disparou

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 1963 ms | — |
| policy | ativa | 2 ms | — |
| converse | ativa | 1550 ms | — |

**Enviado ao lead**

> Temos três opções de planos para seu Corolla 2023:
>
> 1. Essencial: cobre colisão, roubo e furto.
> 2. Completo: inclui colisão, roubo, furto, danos a terceiros e proteção de vidros.
> 3. Premium: cobre tudo do Completo, mais carro reserva e assistência 24h.
>
> Qual desses você prefere para seguirmos com a cotação?

### Turno 5

`conv_00076:conv_00076:10` · rota: extract → policy → converse → quote → present → handoff

**Mensagem do lead** (PII redigida na ingestão)

> Pode cotar o plano Completo.

**Slots após a extração**

| slot | valor | status | proveniência |
|---|---|---|---|
| idade | 64 | informado | digitado |
| veiculo_ano | 2023 | informado | digitado |
| plano_id | Completo | informado | digitado |
| data_inicio | 2026-10-15 | informado | digitado |

**Políticas**

- Aceitação: dentro das regras (idade 64, ano-modelo 2023)
- Escalação: **escala** — regra `cotacao_esgotada`
- Erro do turno: `quote`

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 1630 ms | — |
| policy | ativa | 2 ms | — |
| converse | ativa | 1431 ms | — |
| quote | ativa | 94 ms | quote |
| present | escalada | 0 ms | quote |
| handoff | escalada | 94 ms | quote |

**Tentativas de cotação**

| tentativa | status | HTTP | latência | origem | hedge |
|---|---|---|---|---|---|
| 1 | unavailable | 500 | 20 ms | api | não |
| 2 | unavailable | 502 | 3 ms | api | não |
| 3 | unavailable | 502 | 3 ms | api | não |
| desfecho | unavailable | — | 47 ms | api | não |

**Enviado ao lead**

> Seu atendimento precisa continuar com uma pessoa da equipe. As informações já coletadas acompanham o encaminhamento.

**Snapshot enviado ao vendedor**

- Motivo: `cotacao_esgotada`

| slot | valor | proveniência |
|---|---|---|
| plano_id | Completo | digitado |
| idade | 64 | digitado |
| veiculo_ano | 2023 | digitado |
| data_inicio | 2026-10-15 | digitado |

Tentativas de cotação da conversa, como o vendedor as recebe:

| tentativa | status | HTTP | latência | origem | hedge |
|---|---|---|---|---|---|
| 1 | unavailable | 500 | 20 ms | api | não |
| 2 | unavailable | 502 | 3 ms | api | não |
| 3 | unavailable | 502 | 3 ms | api | não |
| desfecho | unavailable | — | 47 ms | api | não |
