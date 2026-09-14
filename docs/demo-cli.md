# Demonstração da CLI

Sessão real de `python -m interfaces.cli --trace`, gravada no commit final (tarefa 13):
- extrator e conversador reais, via OpenRouter;
- API do desafio numa instância nova, com `QUOTE_SEED=14`, 20% de falha e 10% de lentidão;
- `.env` com os valores do `.env.example`, aceitos pela verificação de partida (D-040);
- quatro mensagens de roteiro pela entrada padrão.

Cada resposta do agente vem seguida do turno por baixo, que é o que o `--trace` imprime. A mensagem do lead aparece em cada turno já redigida.

**A escada aparece no turno 4 — falha, retry, cotação:**
1. A primeira chamada leva HTTP 503 em 24 ms. A falha volta antes da janela de 100 ms, então o hedge não dispara: falha rápida propaga na hora e fica com o retry.
2. O retry faz a segunda chamada, que cota em 37 ms. A cotação lógica inteira leva 89 ms.
3. O lead recebe o preço da tabela, com carência e pro-rata. Não percebe a falha.

A semente 14 foi escolhida porque o sorteio da API começa com uma falha seguida de um sucesso. O caminho com chamada lenta resgatada pelo hedge está em [`execucao-completa.md`](execucao-completa.md), e a escada esgotada, em [`execucao-escalacao.md`](execucao-escalacao.md).

```bash
# API numa instância nova: o sorteio começa na primeira /quote
cd ../namastex-fde-challenge/quote-service
QUOTE_SEED=14 QUOTE_FAILURE_RATE=0.20 QUOTE_SLOW_RATE=0.10 \
  uv run --with fastapi --with uvicorn uvicorn app.main:app --port 18015
# a conversa, por roteiro na entrada padrão
printf '%s\n' "Oi, quero fazer um seguro para o meu carro" \
  "Tenho 32 anos, é um Onix 2020, cep 01310-100" "Pode começar em 15/10/2026" \
  "Quero o plano Completo" "/sair" \
  | uv run --env-file .env python -m interfaces.cli --trace \
      --quote-url http://127.0.0.1:18015 --conversation cli-demo --database /tmp/cli-demo.sqlite
```

**Três observações:**
- A redação do LLM varia entre execuções. Preço, franquia, carência e pro-rata não variam: vêm do payload da `/quote`, por template.
- A demonstração da tarefa 12 (semente 1) mostrava um HTTP 500 depois de 108 ms com o hedge já disparado. O hedge disparou pela latência — a chamada não tinha voltado em 100 ms —, não pela falha. O teste `test_fast_http_500_is_a_single_call_without_waiting_for_the_window` (`tests/unit/test_hedge.py`) confirma que falha rápida produz uma chamada só, sem esperar a janela.
- Naquela sessão, o `.env` local ainda tinha os valores da tarefa 8 e a conversa escalou por limite de tokens. Hoje a CLI nem parte com eles: `Não foi possível iniciar: Configuração inválida na partida — LLM_TIMEOUT_SECONDS: configurado 2 s, abaixo do p99.9 medido de 6.9 s (D-038); LLM_CONVERSATION_TOKEN_LIMIT: configurado 4000, abaixo do máximo medido de …`.

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
| extract | ativa | 1260 ms | — |
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
| extract | ativa | 1633 ms | — |
| policy | ativa | 1 ms | — |

**Enviado ao lead**

> Pode informar ou confirmar a data desejada para início da vigência?

**Agente:** Temos três opções de plano para o seguro do seu Onix 2020: 

- Essencial: cobre colisão, roubo e furto.
- Completo: cobre colisão, roubo, furto, terceiros e vidros.
- Premium: cobre colisão, roubo, furto, terceiros, vidros, carro reserva e assistência 24h.

Qual dessas opções você prefere para fazermos a cotação?
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
| extract | ativa | 1882 ms | — |
| policy | ativa | 2 ms | — |
| converse | ativa | 1761 ms | — |
| decisao | segue | 0 ms | — |

**Enviado ao lead**

> Temos três opções de plano para o seguro do seu Onix 2020: 
>
> - Essencial: cobre colisão, roubo e furto.
> - Completo: cobre colisão, roubo, furto, terceiros e vidros.
> - Premium: cobre colisão, roubo, furto, terceiros, vidros, carro reserva e assistência 24h.
>
> Qual dessas opções você prefere para fazermos a cotação?
quote_http_unavailable HTTP 503: {"error":"upstream_unavailable","message":"Servico de cotacao temporariamente indisponivel. Tente novamente."}

**Agente:** Plano Completo
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
| extract | ativa | 2220 ms | — |
| policy | ativa | 2 ms | — |
| converse | ativa | 1258 ms | — |
| decisao | segue | 0 ms | — |
| quote | ativa | 104 ms | — |
| present | cotada | 0 ms | — |

**Tentativas de cotação**

| tentativa | status | HTTP | latência | origem | hedge |
|---|---|---|---|---|---|
| 1 | unavailable | 503 | 24 ms | api | não |
| 2 | quoted | 200 | 37 ms | api | não |
| desfecho | quoted | — | 89 ms | api | não |

**Enviado ao lead**

> Plano Completo
> Mensalidade: R$ 241,38.
> Franquia: R$ 3.000,00.
> Coberturas: colisão, roubo, furto, danos a terceiros e vidros.
> Carência de 30 dias para roubo e furto, contada do início da vigência.
> Primeiro mês (pro-rata): R$ 132,37, referente a 17 dias de um mês de 31 dias.
Para retomar: python -m interfaces.cli --conversation cli-demo
