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
- **Hedge**: depende de relógio de parede. Ele dispara quando a chamada não voltou em
  100 ms; falha que volta antes disso propaga na hora e fica com o retry. Estável na
  prática, não por construção.
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
| extract | ativa | 1336 ms | — |
| policy | ativa | 3 ms | — |

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
| extract | ativa | 1442 ms | — |
| policy | ativa | 2 ms | — |

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
| extract | ativa | 1722 ms | — |
| policy | ativa | 1 ms | — |

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
- Conversador: não sugeriu escalar; a política não escalou

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 2411 ms | — |
| policy | ativa | 1 ms | — |
| converse | ativa | 2581 ms | — |
| decisao | segue | 0 ms | — |

**Enviado ao lead**

> Temos três planos disponíveis:
> - Essencial: cobre colisão, roubo e furto.
> - Completo: cobre colisão, roubo, furto, terceiros e vidros.
> - Premium: cobre colisão, roubo, furto, terceiros, vidros, carro reserva e assistência 24h.
> Qual dessas opções você gostaria de cotar para o seu Corolla 2023?

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
- Conversador: não sugeriu escalar; a política não escalou
- Erro do turno: `quote`

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 2399 ms | — |
| policy | ativa | 1 ms | — |
| converse | ativa | 986 ms | — |
| decisao | segue | 0 ms | — |
| quote | ativa | 58 ms | quote |
| present | escalada | 0 ms | quote |
| handoff | escalada | 27 ms | quote |

**Tentativas de cotação**

| tentativa | status | HTTP | latência | origem | hedge |
|---|---|---|---|---|---|
| 1 | unavailable | 500 | 14 ms | api | não |
| 2 | unavailable | 502 | 5 ms | api | não |
| 3 | unavailable | 502 | 2 ms | api | não |
| desfecho | unavailable | — | 44 ms | api | não |

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
| 1 | unavailable | 500 | 14 ms | api | não |
| 2 | unavailable | 502 | 5 ms | api | não |
| 3 | unavailable | 502 | 2 ms | api | não |
| desfecho | unavailable | — | 44 ms | api | não |
