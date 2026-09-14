# Execução completa

Conversa real do dataset, `conv_00028`, pelo agente completo. O corpo abaixo
é a saída do comando de inspeção sobre o banco que a execução deixou:

```bash
uv run python -m interfaces.trace --conversation conv_00028 --database /tmp/exec-completa.sqlite
```

## Como reproduzir

```bash
# 1. API do desafio numa instância nova: o sorteio começa na primeira /quote
cd ../namastex-fde-challenge/quote-service && QUOTE_SEED=1 QUOTE_FAILURE_RATE=0.20 QUOTE_SLOW_RATE=0.10 uv run --with fastapi --with uvicorn uvicorn app.main:app --host 127.0.0.1 --port 18011
# 2. a conversa pelo agente, e este documento
AUTOSEGURO_DATASET=<conversations.parquet> uv run --env-file .env python -m scripts.execution_log --conversation conv_00028 --quote-url http://127.0.0.1:18011 --quote-env 'QUOTE_SEED=1 QUOTE_FAILURE_RATE=0.20 QUOTE_SLOW_RATE=0.10' --title 'Execução completa' --output docs/execucao-completa.md
```

O que se repete e o que não:

- **Sorteio da API**: fixo por `QUOTE_SEED=1 QUOTE_FAILURE_RATE=0.20 QUOTE_SLOW_RATE=0.10`, desde que a instância seja nova e
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

- Desfecho: `cotada`, em 7 turnos
- Turnos com mensagem do harness, não do dataset (o dataset não traz data de vigência): 4, 7
- Cotação conferida contra a tabela no perfil real do lead: 1 de 1 consistente

## Conversa `conv_00028`

### Turno 1

`conv_00028:conv_00028:0` · rota: extract → policy

**Mensagem do lead** (PII redigida na ingestão)

> Boa tarde, quero uma cotacao

**Slots após a extração**

nenhum slot coletado ainda

**Políticas**

- Aceitação: não avaliada: falta idade e ano-modelo
- Escalação: nenhuma regra disparou
- Falta dado: `idade`, pedido ao lead

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 1484 ms | — |
| policy | ativa | 2 ms | — |

**Enviado ao lead**

> Pode informar ou confirmar sua idade?

### Turno 2

`conv_00028:conv_00028:2` · rota: extract → policy

**Mensagem do lead** (PII redigida na ingestão)

> Renault Kwid 2011

**Slots após a extração**

| slot | valor | status | proveniência |
|---|---|---|---|
| veiculo_ano | 2011 | informado | digitado |

**Políticas**

- Aceitação: não avaliada: falta idade
- Escalação: nenhuma regra disparou
- Falta dado: `idade`, pedido ao lead

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 1638 ms | — |
| policy | ativa | 2 ms | — |

**Enviado ao lead**

> Pode informar ou confirmar sua idade?

### Turno 3

`conv_00028:conv_00028:5` · rota: extract → policy

**Mensagem do lead** (PII redigida na ingestão)

> Cep [CEP], tenho 31 anos, cpf [CPF]
> a placa é [PLACA] se precisar

**Slots após a extração**

| slot | valor | status | proveniência |
|---|---|---|---|
| idade | 31 | informado | digitado |
| veiculo_ano | 2011 | informado | digitado |

**Políticas**

- Aceitação: dentro das regras (idade 31, ano-modelo 2011)
- Escalação: nenhuma regra disparou
- Falta dado: `data_inicio`, pedido ao lead

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 1807 ms | — |
| policy | ativa | 1 ms | — |

**Enviado ao lead**

> Pode informar ou confirmar a data desejada para início da vigência?

### Turno 4

`conv_00028:conv_00028:11` · rota: extract → policy → converse

**Mensagem do lead** (PII redigida na ingestão)

> Pode começar em 15/10/2026.

**Slots após a extração**

| slot | valor | status | proveniência |
|---|---|---|---|
| idade | 31 | informado | digitado |
| veiculo_ano | 2011 | informado | digitado |
| data_inicio | 2026-10-15 | informado | digitado |

**Políticas**

- Aceitação: dentro das regras (idade 31, ano-modelo 2011)
- Escalação: nenhuma regra disparou
- Conversador: não sugeriu escalar; a política não escalou

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 3549 ms | — |
| policy | ativa | 1 ms | — |
| converse | ativa | 1317 ms | — |
| decisao | segue | 0 ms | — |

**Enviado ao lead**

> Temos três opções de plano para o seu Renault Kwid 2011: o Essencial cobre colisão, roubo e furto. O Completo cobre também terceiros e vidros. E o Premium inclui cobertura para terceiros, vidros, carro reserva e assistência 24h. Qual dessas opções você prefere para sua cotação?

### Turno 5

`conv_00028:conv_00028:7` · rota: extract → policy → converse → objection

**Mensagem do lead** (PII redigida na ingestão)

> achei caro pra esse carro

**Slots após a extração**

| slot | valor | status | proveniência |
|---|---|---|---|
| idade | 31 | informado | digitado |
| veiculo_ano | 2011 | informado | digitado |
| data_inicio | 2026-10-15 | informado | digitado |

**Políticas**

- Aceitação: dentro das regras (idade 31, ano-modelo 2011)
- Escalação: nenhuma regra disparou
- Conversador: não sugeriu escalar; a política não escalou
- Objeção: `caro_para_o_carro` (detectada por modelo)

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 1926 ms | — |
| policy | ativa | 2 ms | — |
| converse | ativa | 1477 ms | — |
| decisao | segue | 0 ms | — |
| objecao | modelo | 0 ms | caro_para_o_carro |
| objection | ativa | 0 ms | — |

**Enviado ao lead**

> Entendo. Posso comparar com um plano de cobertura mais enxuta para o seu carro.

### Turno 6

`conv_00028:conv_00028:9` · rota: extract → policy → converse → objection

**Mensagem do lead** (PII redigida na ingestão)

> pode mandar sim

**Slots após a extração**

| slot | valor | status | proveniência |
|---|---|---|---|
| idade | 31 | informado | digitado |
| veiculo_ano | 2011 | informado | digitado |
| data_inicio | 2026-10-15 | informado | digitado |

**Políticas**

- Aceitação: dentro das regras (idade 31, ano-modelo 2011)
- Escalação: nenhuma regra disparou
- Conversador: não sugeriu escalar; a política não escalou
- Objeção: `caro_para_o_carro` (detectada por modelo)

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 1984 ms | — |
| policy | ativa | 2 ms | — |
| converse | ativa | 1449 ms | — |
| decisao | segue | 0 ms | — |
| objecao | modelo | 0 ms | caro_para_o_carro |
| objection | ativa | 0 ms | — |

**Enviado ao lead**

> Entendo. Posso comparar com um plano de cobertura mais enxuta para o seu carro.

### Turno 7

`conv_00028:conv_00028:12` · rota: extract → policy → converse → quote → present

**Mensagem do lead** (PII redigida na ingestão)

> Pode cotar o plano Completo.

**Slots após a extração**

| slot | valor | status | proveniência |
|---|---|---|---|
| idade | 31 | informado | digitado |
| veiculo_ano | 2011 | informado | digitado |
| plano_id | Completo | informado | digitado |
| data_inicio | 2026-10-15 | informado | digitado |

**Políticas**

- Aceitação: dentro das regras (idade 31, ano-modelo 2011)
- Escalação: nenhuma regra disparou
- Conversador: não sugeriu escalar; a política não escalou

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 2055 ms | — |
| policy | ativa | 2 ms | — |
| converse | ativa | 1558 ms | — |
| decisao | segue | 0 ms | — |
| quote | ativa | 228 ms | — |
| present | cotada | 0 ms | — |

**Tentativas de cotação**

| tentativa | status | HTTP | latência | origem | hedge |
|---|---|---|---|---|---|
| 1 | unavailable | 500 | 19 ms | api | não |
| 2 | unavailable | sem resposta | 161 ms | api | não |
| 3 | quoted | 200 | 59 ms | api | sim |
| desfecho | quoted | — | 221 ms | api | não |

**Enviado ao lead**

> Plano Completo
> Mensalidade: R$ 304,36.
> Franquia: R$ 3.000,00.
> Coberturas: colisão, roubo, furto, danos a terceiros e vidros.
> Carência de 30 dias para roubo e furto, contada do início da vigência.
> Primeiro mês (pro-rata): R$ 166,91, referente a 17 dias de um mês de 31 dias.
