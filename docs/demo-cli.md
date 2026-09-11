# Demonstração da CLI

Sessão real de `python -m interfaces.cli --trace`:
- extrator e conversador reais, via OpenRouter;
- API do desafio numa instância nova, com `QUOTE_SEED=1`, 20% de falha e 10% de lentidão;
- quatro mensagens de roteiro pela entrada padrão.

Cada resposta do agente vem seguida do turno por baixo, que é o que o `--trace` imprime. A mensagem do lead aparece em cada turno já redigida.

**A escada aparece no turno 4:**
1. A primeira chamada leva HTTP 500 depois de 108 ms. Como passou da janela de 100 ms, o hedge já tinha disparado.
2. O hedge sorteia uma chamada lenta e fica sem resposta no timeout de 2 s.
3. O retry faz a terceira chamada, que cota.

O lead recebe o preço da tabela, com carência e pro-rata.

```bash
# API numa instância nova: o sorteio começa na primeira /quote
cd ../namastex-fde-challenge/quote-service
QUOTE_SEED=1 QUOTE_FAILURE_RATE=0.20 QUOTE_SLOW_RATE=0.10 \
  uv run --with fastapi --with uvicorn uvicorn app.main:app --port 18011
# a conversa, por roteiro na entrada padrão
printf '%s\n' "Oi, quero fazer um seguro para o meu carro" \
  "Tenho 32 anos, é um Onix 2020, cep 01310-100" "Pode começar em 15/10/2026" \
  "Quero o plano Completo" "/sair" \
  | uv run --env-file .env python -m interfaces.cli --trace \
      --quote-url http://127.0.0.1:18011 --conversation cli-demo --database /tmp/cli-demo.sqlite
```

**Duas observações:**
- A redação do LLM varia entre execuções. Preço, franquia, carência e pro-rata não variam: vêm do payload da `/quote`, por template.
- Esta sessão rodou com `LLM_TIMEOUT_SECONDS=7.0 LLM_BUDGET_SECONDS=7.0 LLM_CONVERSATION_TOKEN_LIMIT=16000` exportados. O `.env` local ainda tinha os valores da tarefa 8, e com eles a conversa escalou por limite de tokens no turno 4. O `.env.example` já traz os valores certos.

---
Conversa cli-demo. /ajuda lista os comandos.

**Agente:** Pode informar ou confirmar sua idade?
### Turno 1

`cli-demo:cli-demo:0` · rota: extract → policy

**Mensagem do lead** (PII redigida na ingestão)

> Oi, quero fazer um seguro para o meu carro

**Slots após a extração**

nenhum slot coletado ainda

**Políticas**

- Aceitação: não avaliada: falta idade e ano-modelo
- Escalação: nenhuma regra disparou
- Falta dado: `idade`, pedido ao lead

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 1103 ms | — |
| policy | ativa | 3 ms | — |

**Enviado ao lead**

> Pode informar ou confirmar sua idade?

**Agente:** Pode informar ou confirmar a data desejada para início da vigência?
### Turno 2

`cli-demo:cli-demo:1` · rota: extract → policy

**Mensagem do lead** (PII redigida na ingestão)

> Tenho 32 anos, é um Onix 2020, cep [CEP]

**Slots após a extração**

| slot | valor | status | proveniência |
|---|---|---|---|
| idade | 32 | informado | digitado |
| veiculo_ano | 2020 | informado | digitado |

**Políticas**

- Aceitação: dentro das regras (idade 32, ano-modelo 2020)
- Escalação: nenhuma regra disparou
- Falta dado: `data_inicio`, pedido ao lead

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 1538 ms | — |
| policy | ativa | 2 ms | — |

**Enviado ao lead**

> Pode informar ou confirmar a data desejada para início da vigência?

**Agente:** Temos três opções de planos para você escolher:

• Essencial: cobre colisão, roubo e furto.
• Completo: cobre colisão, roubo, furto, danos a terceiros e vidros.
• Premium: cobre colisão, roubo, furto, terceiros, vidros, carro reserva e assistência 24h.

Qual dessas opções você prefere para seguir com a cotação?
### Turno 3

`cli-demo:cli-demo:2` · rota: extract → policy → converse

**Mensagem do lead** (PII redigida na ingestão)

> Pode começar em 15/10/2026

**Slots após a extração**

| slot | valor | status | proveniência |
|---|---|---|---|
| idade | 32 | informado | digitado |
| veiculo_ano | 2020 | informado | digitado |
| data_inicio | 2026-10-15 | informado | digitado |

**Políticas**

- Aceitação: dentro das regras (idade 32, ano-modelo 2020)
- Escalação: nenhuma regra disparou
- Conversador: não sugeriu escalar; a política não escalou

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 2612 ms | — |
| policy | ativa | 1 ms | — |
| converse | ativa | 1622 ms | — |
| decisao | segue | 0 ms | — |

**Enviado ao lead**

> Temos três opções de planos para você escolher:
>
> • Essencial: cobre colisão, roubo e furto.
> • Completo: cobre colisão, roubo, furto, danos a terceiros e vidros.
> • Premium: cobre colisão, roubo, furto, terceiros, vidros, carro reserva e assistência 24h.
>
> Qual dessas opções você prefere para seguir com a cotação?

quote_http_unavailable HTTP 500: {"error":"upstream_unavailable","message":"Servico de cotacao temporariamente indisponivel. Tente novamente."}
quote_http_unavailable transporte: ReadTimeout
Agente: Plano Completo
Mensalidade: R$ 241,38.
Franquia: R$ 3.000,00.
Coberturas: colisão, roubo, furto, danos a terceiros e vidros.
Carência de 30 dias para roubo e furto, contada do início da vigência.
Primeiro mês (pro-rata): R$ 132,37, referente a 17 dias de um mês de 31 dias.
### Turno 4

`cli-demo:cli-demo:3` · rota: extract → policy → converse → quote → present

**Mensagem do lead** (PII redigida na ingestão)

> Quero o plano Completo

**Slots após a extração**

| slot | valor | status | proveniência |
|---|---|---|---|
| idade | 32 | informado | digitado |
| veiculo_ano | 2020 | informado | digitado |
| plano_id | Completo | informado | digitado |
| data_inicio | 2026-10-15 | informado | digitado |

**Políticas**

- Aceitação: dentro das regras (idade 32, ano-modelo 2020)
- Escalação: nenhuma regra disparou
- Conversador: não sugeriu escalar; a política não escalou

**Etapas do turno**

| etapa | status | latência | erro |
|---|---|---|---|
| extract | ativa | 1936 ms | — |
| policy | ativa | 1 ms | — |
| converse | ativa | 909 ms | — |
| decisao | segue | 0 ms | — |
| quote | ativa | 2176 ms | — |
| present | cotada | 0 ms | — |

**Tentativas de cotação**

| tentativa | status | HTTP | latência | origem | hedge |
|---|---|---|---|---|---|
| 1 | unavailable | 500 | 108 ms | api | não |
| 2 | unavailable | sem resposta | 2007 ms | api | sim |
| 3 | quoted | 200 | 19 ms | api | não |
| desfecho | quoted | — | 2166 ms | api | não |

**Enviado ao lead**

> Plano Completo
> Mensalidade: R$ 241,38.
> Franquia: R$ 3.000,00.
> Coberturas: colisão, roubo, furto, danos a terceiros e vidros.
> Carência de 30 dias para roubo e furto, contada do início da vigência.
> Primeiro mês (pro-rata): R$ 132,37, referente a 17 dias de um mês de 31 dias.

Para retomar: python -m interfaces.cli --conversation cli-demo
