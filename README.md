# AutoSeguro — agente de cotação de seguro auto

Agente que atende leads de seguro de veículo: conversa, qualifica, cota usando a API
legada da seguradora e decide sozinho quando resolver e quando passar para um humano.

Desafio técnico FDE / AI Engineer — Namastex.

**O que existe.** O agente completo roda num processo único:
- ingestão com redação de PII;
- grafo LangGraph com extrator e conversador via OpenRouter;
- cadeia de cotação resiliente;
- política de escalação com snapshot;
- outbox.

Ele é exercitado de ponta a ponta pelas conversas reais do dataset, e dá para conversar com
ele no terminal com `python -m interfaces.cli`.

**O que não existe.** Não há webhook de WhatsApp nem console. O ponto de entrada é o
envelope de mensagem, que hoje o replay do dataset e a CLI produzem. Ver
[Limitações](#limitações).

---

## Resultados

| Métrica | Baseline humana (histórico) | Agente |
|---|---:|---:|
| Extração de idade, 2.500 casos, isolada | — | **99,96%** (2.499/2.500) |
| Extração de ano-modelo, 2.500 casos, isolada | — | **100%** (2.500/2.500) |
| Conclusão fim a fim, elegíveis sem documento | — | **97,3%** (72/74) |
| Recusas corretas nas 751 inelegíveis | **0 / 751** | **751 / 751** |
| Cotações consistentes com a tabela | **0 / 2.500** | **72 / 72** |
| Menção de carência quando aplicável | **0 / 2.500** | **72 / 72** |

As três últimas linhas comparam o agente com o vendedor humano do histórico. O vendedor
cotou todos os inelegíveis, e nenhum dos seus preços bate com a tabela. O agente recusa
os 751 inelegíveis com o motivo e sem chamar a API, e só mostra preço que sai da tabela,
sempre com a carência.

Como cada linha foi medida:

- **Extração.** Só o extrator (`openai/gpt-4.1-mini`), sem cotação nem orçamento de
  turno. As rajadas do lead entram em ordem e o gabarito nunca entra no contexto. As
  capturas estão em `tests/fixtures/llm-isolated/` e são reproduzidas sem rede por
  `uv run python -m scripts.evaluate_isolated --mode replay --model openai/gpt-4.1-mini`.
- **Conclusão.**
  - Agente inteiro sobre 150 conversas do dataset, sorteadas com seed 2026.
  - A API local rodou com `QUOTE_SEED=42`, 20% de falha e 10% de lentidão.
  - Conta como concluída a conversa que apresentou cotação.
  - Dados da rodada no commit final, em `docs/measurements/task13-e2e.json`. A tarefa 12
    deu o mesmo 72/74 e a tarefa 11, 73/74; a diferença é uma cotação indisponível a mais
    no sorteio.
- **Recusas corretas.**
  - Agente inteiro sobre as 751 conversas que o oráculo marca como inelegíveis: idade
    fora de 18–75 ou veículo com mais de 20 anos na data da conversa.
  - Resultado: 751 recusadas pela regra local, com o motivo e sem nenhuma chamada à
    `/quote`; nenhuma cotação, nenhuma escalação; as 751 encerradas com slots vazios.
  - Dados da rodada no commit final em `docs/measurements/task13-e2e-751.json` (US$ 0,86;
    a da tarefa 11, `task11-e2e-751.json`, deu o mesmo resultado).
- **Cotações consistentes.**
  - Cada cotação que o lead viu foi conferida contra a `/quote` com o perfil **real** do
    lead, tirado do gabarito do dataset: idade, ano, CEP e a data de início informada.
  - Mensalidade, franquia e pro-rata tinham de ser iguais.
  - A conferência pega também extração errada, porque perfil trocado dá o preço de outro
    perfil.
- **Carência.** Aplica-se quando o payload da cotação traz carência, e as 72 traziam.
  Conta como mencionada quando o texto enviado a nomeia com os dias.

> **Ressalvas — leia antes dos números**
>
> 1. **Há duas taxas de conclusão.**
>    - **48,0% (72/150)** é sobre a amostra inteira. Esse denominador inclui 45 inelegíveis,
>      que devem ser recusados (e os 45 foram), e 31 elegíveis que mandaram documento. Esses
>      31 escalam por decisão de privacidade, não por falha.
>    - **97,3% (72/74)** é sobre as elegíveis sem documento, e é a taxa que mede o agente.
>      As duas que faltam são cotações indisponíveis depois da escada inteira.
> 2. **127 dos 673 turnos (18,9%) são sintéticos.**
>    - O dataset nunca traz data de vigência. O harness responde quando o agente pergunta
>      e, se a conversa acaba sem cotação, pede o plano Completo.
>    - Sem isso, nenhuma conversa do dataset cotaria. A taxa mede o agente diante de um
>      lead que responde ao que é perguntado.
> 3. **O roteamento de objeção sobre o dataset é 100% por construção.**
>    - A lista lexical foi escrita sobre as frases do gerador; que ela cubra o dataset só
>      prova que a lista cobre a lista.
>    - O número que generaliza é o do modelo. Nas 39 conversas da amostra em que uma
>      objeção chegou ao agente, as 39 foram ao nó de objeção, com 60 classificações feitas
>      pelo modelo e nenhuma pelo piso lexical.
> 4. **A resolução de mídia foi exercitada só por fixtures.** O dataset traz marcadores
>    (`[imagem] ...`), não arquivos. No replay a resolução nunca acontece; os ramos
>    resolvidos rodam contra o modelo real apenas sobre quatro fixtures ([tabela](#mídia-exercitada-de-verdade)).
> 5. **Os denominadores diferem de propósito.**
>    - A baseline humana é sobre as 2.500 cotações do histórico.
>    - A do agente é sobre as 72 cotações que ele de fato fez na amostra: ele não cota
>      sem os cinco campos, então não há cotação do agente nas outras conversas.

### Duas evidências da fase final

**A auditoria de clientes externos encontrou três irmãos do bug do 404** (D-035).
- **O bug original:** o conversador recebia 404 do OpenRouter em todas as chamadas, e o
  cliente convertia a resposta em "erro de contrato", descartando o corpo.
- **O resultado da auditoria:** a mesma régua, aplicada aos demais clientes, achou o
  mesmo defeito em três lugares, inclusive no cliente de cotação que servia de modelo.

| Cliente | Antes | Agora |
|---|---|---|
| API de cotação | corpo descartado; 401/404 na mesma classe do 400 de payload | erro de configuração, sem retry, corpo no trace |
| `/planos` | todo erro HTTP, inclusive 401/404, virava indisponível; o guard falhava aberto em silêncio | erro de configuração falha alto |
| Sinks de escalação | `HandoffDeliveryError()` sem status nem corpo, retentado para sempre | configuração distinta, detalhe gravado no erro da outbox |

A verificação de partida faz uma chamada mínima a cada dependência, com os parâmetros de
produção. O 404 do conversador pararia a partida no primeiro segundo.

**A transcrição trocou "Ônix" por "anix"** numa fixture real, com voz sintética pt-BR e
`google/gemini-2.5-flash`: "Tenho 32 anos e o meu carro é um anix, ano 2020." É a
justificativa medida, não uma hipótese, para a regra de que slot transcrito exige
confirmação antes de cotar.

### Alternativa rejeitada com número

| Modelo do extrator | Idade | Ano-modelo | Mediana / p95 por chamada | Custo, 2.500 casos |
|---|---:|---:|---:|---:|
| gpt-4.1-mini — **em uso** | **99,96%** | **100%** | 1.482 / 2.267 ms | US$ 2,86 |
| gpt-4.1-nano — rejeitado | 89,32% | 97,88% | 1.465 / 2.666 ms | US$ 0,78 |

O nano perde 10 pontos em idade e não melhora a latência: o custo é a ida até o
provedor, não a inferência. Relatórios em `tests/fixtures/llm-isolated/models/`.

---

## Rodando

Pré-requisitos: [uv](https://docs.astral.sh/uv/), o repo do desafio ao lado deste e uma
chave do OpenRouter.

```bash
# 1. API de cotação, do repo do desafio. Esta aplicação não tem serviço de dados.
#    As medições usaram a API fora do Docker, com sorteio fixo:
cd ../namastex-fde-challenge/quote-service
QUOTE_SEED=42 QUOTE_FAILURE_RATE=0.20 QUOTE_SLOW_RATE=0.10 \
  uv run --with fastapi --with uvicorn uvicorn app.main:app --port 18010
#    Alternativa: `docker compose up --build` no repo do desafio (porta 8000; para
#    sorteio fixo, descomente QUOTE_SEED no docker-compose.yml de lá).

# 2. dependências; o esquema SQLite é aplicado ao abrir o banco
uv sync

# 3. variáveis
cp .env.example .env               # preencha OPENROUTER_API_KEY
export AUTOSEGURO_DATASET=../namastex-fde-challenge/dataset/conversations.parquet
```

Conversa no terminal, com a API da etapa 1 no ar:

```bash
uv run --env-file .env python -m interfaces.cli --trace --quote-url http://127.0.0.1:18010
# retoma uma conversa pelo id impresso ao sair
uv run --env-file .env python -m interfaces.cli --conversation cli-1a2b3c4d
```

Comandos dentro da conversa:
- `/imagem <arquivo>`, `/audio <arquivo>` e `/documento <nome>` injetam mídia;
- `/encerrar` encerra a conversa e apaga slots e estado do grafo;
- `/sair` sai sem encerrar;
- `/ajuda` lista os comandos.

Com `--trace`, cada resposta vem acompanhada do que aconteceu por baixo: slots com
proveniência, políticas, opinião do conversador e tentativas de cotação com status,
latência e hedge. Uma sessão real, em que a cotação falha, é retentada e sai, está em
[`docs/demo-cli.md`](docs/demo-cli.md).

A partida recusa configuração incoerente antes de qualquer rede: timeout do LLM abaixo do
p99.9 medido, orçamento do turno abaixo da soma das etapas ou limite de tokens abaixo do
máximo medido. A mensagem traz o valor configurado e o medido (D-040).

O agente também roda sobre conversas do dataset:

```bash
# conclusão fim a fim, 150 conversas (consome LLM: ~4 min, ~US$ 0,80)
uv run --env-file .env python -m scripts.measure_end_to_end --scenario p999
# as 751 inelegíveis (~10 min, ~US$ 0,90)
uv run --env-file .env python -m scripts.measure_end_to_end --scenario p999 --ineligible
# uma conversa, com o log de execução em markdown (API numa instância nova)
uv run --env-file .env python -m scripts.execution_log --conversation conv_00028 \
  --quote-url http://127.0.0.1:18010 --output /tmp/execucao.md
# inspeção, turno a turno, de uma conversa já executada
uv run python -m interfaces.trace --conversation conv_00028 --database /tmp/autoseguro-execucao.sqlite
# envelopes redigidos de uma conversa, sem chamar o agente
uv run python -m interfaces.replay --conversation conv_00013
# números do dataset citados aqui (API com QUOTE_FAILURE_RATE=0 e QUOTE_SLOW_RATE=0)
uv run python -m scripts.dataset_facts --quote-url http://127.0.0.1:18013
```

Testes:

```bash
uv run pytest -m "not slow"        # loop rápido, offline
uv run pytest                      # inclui corpus e simulações; sem corpus, esses são pulados
uv run pytest -m eval              # extração da tarefa 8 por replay das capturas, sem rede
uv run ruff check src tests scripts && uv run mypy src
```

---

## A tese

**Nenhum número que o lead vê foi produzido pelo LLM.**

Prêmio, franquia e pro-rata saem exclusivamente do payload da `POST /quote`,
renderizados por template. O LLM decide *quando* cotar; nunca *quanto* custa.

Todas as decisões abaixo derivam disso.

---

## Decisões

Cada decisão vem com o número que a sustenta. Os números sobre o dataset são recalculados
por `scripts/dataset_facts.py` e ficam gravados em `docs/measurements/dataset-facts.json`.

### 1. `base_mensal` e os multiplicadores nunca saem da API

`GET /planos` devolve a tabela de preços inteira. Se ela chegar ao contexto do LLM, por
prompt ou por resultado de ferramenta, o modelo passa a ter tudo o que precisa para
calcular o prêmio sozinho. Em algum turno ele vai calcular, e o resultado vai estar
*quase* certo, o que é pior que uma alucinação óbvia.

Por isso `/planos` **não é ferramenta do LLM**. É infraestrutura, com cache de 5 minutos,
e é projetada em duas estruturas:

| Projeção | Conteúdo | Vai ao contexto? |
|---|---|---|
| `AcceptanceRules` | limites de idade, idade do veículo, planos válidos | Não |
| `ProductFacts` | nome, coberturas e se há carência | Sim |

Os campos de precificação não entram em nenhuma das duas: são descartados no parse.
Franquia, dias de carência e pro-rata chegam só pelo payload da `/quote`. Isso é uma
garantia estrutural, não uma instrução de prompt: o modelo não desobedece porque não tem
o dado.

### 2. Nem toda resposta ruim da `/quote` é uma falha

| Status | Significado | Retenta? |
|---|---|---|
| `200` | cotação | — |
| `422 cotacao_recusada` | a seguradora respondeu e recusou | Nunca |
| `400 payload_invalido` | bug nosso | Nunca |
| `401`, `403`, `404`, `3xx` | configuração (credencial, rota) | Nunca; falha alto |
| `5xx`, `408`, `425`, `429`, timeout | falha transitória | Sim |

Tratar `422` como falha faz o agente retentar uma recusa determinística e dizer "estamos
instáveis" quando a resposta correta era "não aceitamos esse perfil".

Recusa é modelada como **resultado**, não exceção:

```python
QuoteOutcome = Quote | Declined      # a seguradora respondeu
QuoteUnavailable                     # ninguém respondeu — transitório
QuoteContractError                   # nosso payload está errado — não transitório
```

### 3. Recusa por regra de aceitação não é handoff

Com ano-base 2026, 751 dos 2.500 leads (30,0%) são inelegíveis: 11,2% por idade acima de
75 anos e 21,2% por veículo com mais de 20 anos, e 60 leads caem nos dois critérios. O agente recusa com o motivo e encerra;
escalar 30% das conversas seria falhar no critério de handoff.

As regras são determinísticas e conhecidas, então a recusa acontece **antes** da rede. Nas
751, o agente não fez nenhuma chamada à `/quote`. A API deriva a idade do veículo de
`date.today()`, então essa fração cresce com o tempo.

### 4. O CEP é imutável depois de coletado

`cep` é opcional na `/quote`, e omiti-lo zera o agravo de região (multiplicador 1,30). No
dataset, **35,8% dos leads têm CEP em prefixo agravado** (07, 08, 21, 26, 59). Um agente
que esquece o CEP entrega um preço 30% menor por acidente. Uma vez coletado, o CEP vai em
toda chamada; o primeiro valor é imutável e sobrevive a reinício (D-036).

### 5. Sem circuit breaker

O `quote-service` sorteia a falha por chamada: 20% de 500/502/503 imediato e 10% de
`sleep` de 8 s. A falha é **independente e sem estado**, então o serviço nunca cai de
verdade. Um circuit breaker abriria numa sequência aleatória e passaria a recusar chamadas
com 80% de chance de sucesso.

Contando só as falhas imediatas, três tentativas deixam 0,2³ = 0,8% de falha residual.
Somada a lentidão truncada pelo timeout de 2 s, a simulação com os decorators reais mede
2,72% sem hedge e 1,27% com hedge dentro de 3,5 s ([tabela](#resiliência-simulada-e-calibração)).

### 6. Hedged requests para a cauda de latência

A `/quote` é uma **função pura**: sem estado, sem efeito colateral, resultado
determinístico dados os cinco campos e a data. Isso permite duas coisas:
- **Cache exato.** A chave são os cinco slots normalizados mais o dia de referência, com
  TTL até meia-noite. Recusas também entram no cache.
- **Hedge.** Uma segunda chamada dispara após 100 ms (p99 medido de 44,73 ms), e fica a
  primeira resposta que voltar. É seguro justamente porque a chamada não tem efeito
  colateral. No [log de execução](docs/execucao-completa.md), a segunda tentativa é uma
  chamada lenta e o hedge a resgata.

### 7. Um agente, sete responsabilidades, três com LLM

Não há supervisor nem enxame de especialistas. Um roteador com LLM introduziria
não-determinismo exatamente onde o desafio pede critério explícito.

| Responsabilidade | Usa LLM |
|---|---|
| Extrator de slots | sim — structured output, sem persona |
| Conversador | sim — persona, decide quando cotar, classifica objeção |
| Resolução de mídia (imagem e áudio) | sim — modelo multimodal, sem persona |
| Política de aceitação | não |
| Política de handoff | não |
| Cadeia de cotação | não |
| Apresentação | não |

O extrator não recebe persona, histórico nem `ProductFacts`. O conversador nunca vê os
campos de precificação. Numa recusa por regra, o conversador nem é chamado: a política
decide e o template escreve.

### 8. A chave do lead é a identidade de canal

`sender_name` não é identidade: são 336 nomes distintos para 2.500 conversas, e "Joao
Gomes" aparece em 17 conversas, com 17 veículos diferentes. A chave é `(channel,
channel_user_id)`, com o id de canal gravado como hash; no replay, `channel_user_id` é o
`conversation_id`. O agente não pede CPF (ver [O dataset](#o-dataset-como-foi-usado)); o
hash do CPF fica como atributo opcional quando o lead o informa por conta própria.

### 9. Ordenação por `message_index`, nunca por `timestamp`

Só **5 de 2.500** conversas do dataset têm timestamp monotônico. Ordenar por `timestamp`
reposiciona **67,6%** das mensagens: a resposta do vendedor vem antes da pergunta.

### 10. Envelope canônico de mensagem

A entrada é um `InboundMessage` único, e a saída, um `OutboundMessage` único, que carrega
intenção e não capacidade de canal. As colunas do parquet já são praticamente o envelope,
então o replay não é um caminho especial. Hoje só o replay produz o envelope; um adapter
de WhatsApp traduziria para o mesmo tipo.

A saída é gravada na outbox com id estável antes de qualquer entrega. A entrega ao canal
não está ligada ([Limitações](#limitações)).

### 11. Ano-modelo futuro é normalizado no payload

A API calcula a idade do veículo como `ano_atual − ano_modelo`, e um modelo do ano
seguinte daria idade −1 e um `422`. A requisição sai com
`veiculo_ano = min(veiculo_ano, ano_atual)`, o que é neutro em preço, porque a faixa de 0
a 5 anos tem multiplicador único. Normaliza só um ano à frente e grava
`quote_attempts.ano_normalizado`. Dois ou mais anos no futuro é erro de digitação, e o
agente confirma com o lead. O dataset cobre 2001 a 2024, então o caso só aparece em
produção.

### 12. SQLite, sem serviço de dados externo

Processo único:
- o lock por conversa é `asyncio.Lock`;
- o debounce é uma janela em memória;
- a deduplicação de entrada é um índice único em `provider_message_id`.

Nenhum deles precisa de servidor.

O cache de cotação é uma tabela e aparece na inspeção de trace, ao lado das tentativas.
Dinheiro é `TEXT` com `Decimal` serializado, nunca `REAL`. O gatilho para reintroduzir um
serviço externo é ter múltiplos workers, não volume de dados.

---

## Arquitetura

```
src/
  domain/          entidades e regras puras, sem import de framework
  application/     casos de uso e portas (Protocol)
  agent/           LangGraph: grafo, nós, prompts, templates
  infrastructure/  quote/, planos/, llm/, media/, handoff/, persistence/, tracing/,
                   privacy/, dataset/, http_errors.py, wiring.py
  interfaces/      cli (conversa), replay, trace (inspeção), rendering, conversation_report
```

Arquitetura hexagonal, com dependência sempre para dentro. `domain/` e `application/` não
importam `langgraph`, `httpx` nem `sqlite3`.

### O grafo

```
extract → policy ─┬─ fim (recusa por regra, ou pedido de dado)
                  ├─ handoff
                  └─ converse ─┬─ quote → present ─┬─ fim
                               │                   └─ handoff (cotação indisponível)
                               ├─ objection → fim
                               ├─ handoff
                               └─ fim
```

A ingestão fica antes do grafo, em `application.ingest`: redação, janela de 500 ms,
deduplicação e resolução de mídia. Extração e fala têm teto de 7 s por tentativa e um
retry dentro do prazo do turno (D-038).

### A cadeia de cotação

```
ApplicationTrace → Guard → Cache → Retry → Hedge → WireTrace → Http
```

Cada camada é um decorator que implementa `QuoteProvider`. `Clock`, `sleep` e o gerador
aleatório são injetados por construtor, então a cadeia inteira é testável offline em
milissegundos.

---

## Quando a `/quote` falha

A escada tem três níveis:

| Nível | Ação | Lead percebe? |
|---|---|---|
| N0 | chamada direta, timeout de 2 s, com hedge em 100 ms na cauda de latência | não |
| N1 | retry com jitter, até 3 tentativas dentro de 3,5 s | não |
| N2 | escalação com snapshot completo | sim |

Nunca: preço estimado pelo agente. N0 e N1 são a cadeia de decorators; N2 é decisão de
conversa e vive no grafo. Na rodada do commit final (tarefa 13), 2 das 74 cotações lógicas chegaram a N2.

**O cache não é nível da escada.**
- **Onde fica:** antes do retry. Ele evita a chamada quando a mesma cotação (cinco slots e o dia) já foi obtida hoje.
- **Por que não é reserva:** o preço é determinístico e o TTL vai até a meia-noite, então uma entrada do dia nunca está expirada. E, se a chamada falhou, não há entrada para servir. Por isso ele não pode servir de reserva depois da falha.
- **Histórico:** uma versão anterior deste README o listava como nível de degradação. Era erro de desenho, corrigido na D-039.

---

## Handoff

É um motor determinístico, com uma classe por regra, avaliadas em ordem a cada turno. O
conversador pode sugerir escalação, mas quem decide é a política. Em todo turno em que o
conversador fala, as duas opiniões ficam gravadas no evento `decisao` da timeline,
inclusive quando ninguém escala (D-039). A divergência medida está abaixo da tabela.

| Ordem | Regra | Dispara quando | Na amostra de 150 (tarefa 13) |
|---:|---|---|---:|
| 1 | Documento recebido | o lead manda documento; ele nunca sai do processo | 31 |
| 2 | Mídia não resolvida | o segundo áudio sem transcrição | 0 |
| 3 | Cotação esgotada | a escada da `/quote` terminou sem resposta | 2 |
| 4 | Limite de tokens | 16.000 tokens na conversa | 0 |
| 5 | Prazo do turno | 18 s esgotados — pior caso; a mediana do turno é 1,80 s e o p99, 5,70 s | 0 |
| 6 | LLM indisponível | o LLM falha também no retry | 0 |
| 7 | Desconto fora da tabela | pedido de desconto ou três objeções de preço | 0 |
| 8 | Pedido de humano | pedido explícito de atendente | 0 |
| 9 | Laço de esclarecimento | três pedidos seguidos do mesmo dado, sem avanço | 0 |
| 10 | Fora de escopo | sinistro, cobrança, cancelamento, renovação ou outro ramo, pela categoria do conversador ou pelo piso lexical | 0 |

A regra "fora de escopo" usa a categoria `assunto` que o conversador emite no schema, com
um piso lexical abaixo. O piso existe porque a política pede o dado que falta antes de o
conversador falar: sem ele, "bati o carro, quero abrir sinistro" receberia "qual a sua
idade?". O piso não dispara em nenhuma das mensagens de lead do dataset
(`tests/regression/test_scope_floor.py`).

**Divergência medida:** 0 em 223 turnos com fala (`docs/measurements/task13-e2e.json`).
- O modelo nunca sugeriu escalar, e a política nunca escalou depois de uma fala do conversador.
- As 33 escalações da amostra aconteceram fora dos turnos de fala: 31 por documento, decididas pela política antes do conversador falar, e 2 por cotação esgotada, depois da apresentação.
- **O dataset não tem como acionar a métrica nem a regra de fora de escopo.** A busca termo a termo nas 26.470 mensagens, de lead e de vendedor, sem caixa e sem acento, deu zero ocorrências de "humano", "atendente", "supervisor", "sinistro" e "cancelamento" (`termos_ausentes` em `docs/measurements/dataset-facts.json`, gerado por `scripts/dataset_facts.py`).
- Métrica implementada e testada nos dois sentidos, sobre um dataset que não pode acioná-la, é diferente de métrica quebrada. As duas opiniões e a divergência são testadas em `tests/unit/test_handoff.py` (`test_policy_keeps_both_opinions`), e o fora de escopo pelo modelo e pelo piso, em `tests/unit/test_graph.py`. O zero diz que o dataset não tem esses pedidos, não que o agente os trataria bem; o número que falta vem de tráfego real.

O handoff produz três efeitos:
1. **mensagem ao lead** — primeiro, porque é o único efeito com prazo humano;
2. **webhook ao time de vendas** — com o snapshot;
3. **registro via API** — para a fila de atendimento.

A decisão e os três efeitos são gravados na mesma transação, antes de qualquer entrega. O
snapshot traz os slots coletados, as tentativas de cotação com status e latência e o
motivo; o [log de escalação](docs/execucao-escalacao.md) mostra um snapshot real.

---

## Rastreabilidade

```sql
quote_attempts (
  id, trace_id, conversation_id, fingerprint, tentativa, status, origem,
  http_status, latencia_ms, hedge, ano_normalizado, erro, criado_em
)
-- status ∈ quoted | declined | unavailable | contract_error
-- origem ∈ api | cache | regra_local
```

A tabela tem uma linha por chamada física, hedge incluído, e uma por desfecho lógico
(tentativa zero). O `trace_id` do turno costura as tentativas, a timeline de etapas
(`turn_events`, com o corpo redigido de toda falha externa), o estado do grafo, a mensagem
ao lead e o handoff.

```bash
uv run python -m interfaces.trace <trace_id> --database arquivo.sqlite          # uma cotação
uv run python -m interfaces.trace --conversation <id> --database arquivo.sqlite # a conversa
```

A inspeção de conversa gera os [logs de execução](#log-de-execução-completa), abre o banco
somente para leitura e não reexecuta nada.

---

## Privacidade

O dataset é sintético, mas é tratado como se não fosse. CPF e CEP aparecem em todas as
2.500 conversas, e-mail e telefone em 1.379 mensagens cada e placa em 839. Uma versão
anterior deste README dizia 3.879 mensagens com CEP, porque a regex não tinha fronteira e
casava dentro dos telefones (2.500 + 1.379). O erro apareceu quando os fatos do dataset
passaram a ser gerados por `scripts/dataset_facts.py`, e não mais à mão.

A redação acontece **na entrada**, antes de qualquer coisa tocar log, contexto de LLM ou
banco:
- CPF, com validação de dígito verificador;
- e-mail, telefone, placa e CEP completo;
- processador de redação no logger, que cobre também argumentos e traceback;
- identidade de canal gravada como hash.

A auditoria dos 2.500 CPFs rotulados no dataset não achou falso positivo nem falso
negativo (`tests/regression/test_cpf_audit.py`). A inspeção e o replay só exibem texto
redigido.

### Retenção: slot é dado operacional, mensagem é log

O CEP é a exceção deliberada: sem ele a cotação perde o agravo de região. Por isso ele
fica em `conversations.slots` enquanto a conversa está aberta e nunca volta ao texto
persistido.

Os slots saem pelo encerramento da conversa, que apaga `conversations.slots` e o estado do
grafo. Três situações encerram:
- a recusa final, na hora;
- a conversa aberta sem mensagem do lead há mais de 24 h (a janela de atendimento do
  WhatsApp), por uma purga oportunista, sem agendador, feita na partida e no início de
  cada turno;
- o lead que volta reabre a conversa (D-036, D-038).

**Cifrar o CEP em repouso não é feito, por decisão, e não por omissão.** O SQLite é local
de processo único, o dado tem vida curta (no máximo 24 h de inatividade) e a chave ficaria
no mesmo disco. A purga é o controle.

---

## O dataset: como foi usado

O histórico contém uma armadilha: o vendedor sintético sorteia plano e preço de forma
independente. Nenhuma das 2.500 cotações bate com a tabela, as 2.500 usam a mesma frase
de cobertura nos três planos e nenhuma menciona carência. Usar essas mensagens como
few-shot ensinaria o comportamento que o desafio proíbe. **O histórico foi usado como
entrada e como baseline a ser batida, não como exemplo a ser imitado.**

| Camada do dado | Uso |
|---|---|
| Mensagens do lead | fixture, harness fim a fim, distribuição de tráfego |
| `lead_idade_informada`, `veiculo_texto` | gabarito de extração e de cotação |
| Cotações do vendedor | oráculo negativo e baseline |
| Preço, plano, cobertura | nada — a fonte de verdade é a API |

O vendedor humano pede CPF em todas as conversas, um dado que a API não usa, e nunca
pergunta a data de vigência, um dado que a API usa. O agente coleta só os campos que a
`/quote` consome e não pede CPF.

---

## Testes e avaliação

```
tests/unit/          domínio, decorators, políticas, templates, e integração com SQLite
                     real (test_*_integration.py, test_durable_slots.py) — sem rede
tests/golden/        extração contra os 2.500 casos com gabarito, por replay de capturas
tests/regression/    oráculo das 751 inelegíveis, auditoria de CPF, cobertura de objeção
```

A linha entre teste e avaliação importa:
- Extração e fala dependem de LLM, então são avaliação com limiar, não vermelho-verde.
- O oráculo das 751 é versionado e testado. A execução do agente sobre elas consome LLM,
  por isso roda pelo harness (`--ineligible`) e não pelo pytest.

A escada de degradação inteira roda **sem rede**: a folha da cadeia é um duplo programado
para falhar N vezes. Nenhum teste depende do sorteio da API.

### Conclusão fim a fim por fase

Mesmas 150 conversas em todas as colunas.

| Desfecho | 9.2 antes (6 s, LLM 2,5 s) | 9.2 depois (10 s, LLM 4,5 s) | Tarefa 10 (mídia) | Tarefa 11 (p99.9 + retry) | Tarefa 12 (assunto + divergência) | Tarefa 13 (commit final) |
|---|---:|---:|---:|---:|---:|---:|
| **Cotada** | **0** | **38 (25,3%)** | **64 (42,7%)** | **73 (48,7%)** | **72 (48,0%)** | **72 (48,0%)** |
| Recusa por regra | 42 | 44 | 44 | 45 | 45 | 45 |
| Documento recebido | 62¹ | 63¹ | 31 | 31 | 31 | 31 |
| Limite de tokens | 27 | 0 | 0 | 0 | 0 | 0 |
| LLM cortado ou indisponível | 19 | 4 | 9 | **0** | **0** | **0** |
| Cotação indisponível após a escada | 0 | 1 | 2 | 1 | 2 | 2 |
| Prazo do turno | 0 | 0 | 0 | 0 | 0 | 0 |
| Fora de escopo | — | — | — | — | 0 | 0 |

¹ Até a tarefa 9.2, documento, imagem e áudio escalavam juntos na primeira mídia.

O que mudou em cada coluna:
- **9.2 para tarefa 10:** a mídia deixou de escalar (D-037).
- **Tarefa 10 para tarefa 11:** o teto de LLM passou a ser dimensionado por conversa
  (D-038).
  - Antes, os tetos ficavam logo acima do p99 **por chamada**, o que dá cerca de 10% de
    corte **por conversa**. O resultado foram 9 cortes, todos na extração.
  - Agora o teto está no p99.9 por chamada, 7 s, e a chamada que estoura é refeita uma
    vez.
  - Nesta rodada, o teto antigo de 3,5 s teria cortado 5 extrações. Só uma passou de 7 s,
    e o retry recuperou o turno.
- **Recusa por regra, 44 para 45:** uma conversa inelegível que antes era cortada pelo LLM
  agora chega à recusa.
- **Tarefa 11 para tarefa 12:** o conversador passou a emitir `assunto`, e a decisão
  passou a ser gravada em todo turno de fala (D-039). A cotação que falta a mais é uma
  cotação indisponível no sorteio. O "fora de escopo" não disparou em nenhuma conversa.
- **Tarefa 12 para tarefa 13:** o agente não mudou de comportamento; a verificação de
  configuração (D-040) só age na partida da composição de produção. A rodada repetiu os
  números da tarefa 12 no commit final, para que números, logs e código venham do mesmo
  estado.

Nas 150 conversas da tarefa 13 (`docs/measurements/task13-e2e.json`):
- as 45 inelegíveis foram recusadas e nenhuma elegível foi recusada;
- nenhuma das falas dos 673 turnos pediu documento, foto ou CPF;
- as 72 cotações batem com a tabela no perfil real e trazem a carência;
- o máximo de tokens numa conversa foi 10.095, abaixo do limite de 16.000;
- o custo conhecido foi de US$ 0,84.

### Onde o tempo do turno vai

Tarefa 13, commit final, 673 turnos:

| Etapa | p50 | p95 | p99 | Máximo | Teto |
|---|---:|---:|---:|---:|---:|
| Extração (todo turno) | 1,71 s | 2,58 s | 3,54 s | 5,30 s | 7 s + retry |
| Fala do conversador (223 turnos) | 1,48 s | 2,53 s | 4,22 s | 5,25 s | 7 s + retry |
| Cotação (74 turnos) | 34 ms | 1,07 s | 2,25 s | 2,25 s | 3,5 s |
| Política | 1 ms | 3 ms | 5 ms | 491 ms | — |
| **Turno inteiro** | **1,80 s** | **4,29 s** | **5,70 s** | **7,60 s** | **18 s** |

Nenhuma chamada de LLM passou do teto de 7 s nesta rodada, então o retry não disparou. Na
tarefa 11, uma extração foi cortada em 7 s e o retry respondeu em cerca de 1,2 s.

O teto de 18 s é o pior caso, não a espera típica. A espera percebida pelo lead, com a
janela de rajada, foi p50 de 2,33 s, p95 de 4,83 s e máximo de 8,13 s.

### Mídia exercitada de verdade

O dataset não tem arquivo de mídia. Os ramos resolvidos rodam contra o modelo real
(`google/gemini-2.5-flash`) só sobre as fixtures de `tests/fixtures/media`, via
`scripts/probe_media.py` (`docs/measurements/task10-media.json`):

| Fixture | Resultado real | Latência | Resposta do agente |
|---|---|---:|---|
| Foto nítida de veículo | veículo, confiança alta | 1.984 ms | reconhece e segue |
| Foto escura e borrada | veículo, confiança baixa | 1.895 ms | nota neutra ("não sei") |
| Foto de gato | não é veículo, confiança alta | 1.669 ms | nota neutra, sem acusar |
| Áudio curto (voz sintética) | "Tenho 32 anos e o meu carro é um anix, ano 2020." | 1.901 ms | slots `transcrito`, que pedem confirmação |

Documento não tem adaptador e nunca é enviado a provedor externo.

### Resiliência simulada e calibração

Os decorators reais rodaram com uma folha simulada, sem rede, em 10.000 cotações. A folha
tem 20% de falha imediata, 10% de lentidão truncada pelo timeout de 2 s e 70% de sucesso,
com três tentativas:

| Configuração | Orçamento | Falha residual |
|---|---|---:|
| Sem hedge | sem corte por tempo | 2,72% |
| Com hedge | sem corte por tempo | 1,18% |
| Sem hedge | produção: 3,5 s | 3,29% |
| Com hedge | produção: 3,5 s | **1,27%** |

Reprodução: `uv run pytest tests/unit/test_residual_rate.py -q -s`.

A janela do hedge vem de uma calibração com 500 POSTs sequenciais contra a API sem falha
nem lentidão: mediana de 15,88 ms, p95 de 29,17 ms e p99 de 44,73 ms
(`docs/measurements/task5-fast-path.json`). A janela é de 100 ms, mais de duas vezes esse
p99. A gravação do trace roda num worker fora do caminho do retry, porque o p99 de
escrita chegou a 30 ms no disco montado (`docs/measurements/task6-trace-*.json`).

---

## O que ficou de fora, e por quê

**Vector database / RAG.** O corpus de objeções são seis variantes canônicas. Isso é um
enum, não um corpus, e indexar as respostas do vendedor seria indexar preço errado.

**Base de conhecimento no prompt.** Cobertura, franquia, carência e pro-rata já vêm no
payload da `/quote`. Uma cópia no prompt envelheceria enquanto a original não.

**Circuit breaker.** Ver decisão 5.

**Calculador local de prêmio.** Criaria uma segunda fonte de verdade, que divergiria em
silêncio.

**Scoring ou modelo de propensão.** Não há sinal. No dataset, a correlação entre o preço e
o desfecho "ganho" é de 0,02, e o desfecho não depende do plano (qui-quadrado, p = 0,58).

**Memória de longo prazo.** As conversas são curtas.

**Promessa com retomada assíncrona.** Exigiria worker, agendamento e mensagem proativa.
Um nível intermediário meia-boca é pior que um handoff honesto.

**Cifragem do CEP em repouso.** Ver [Retenção](#retenção-slot-é-dado-operacional-mensagem-é-log).

---

## Limitações

- **Não há canal de rede.** Não existe webhook de WhatsApp nem console Streamlit.
  - O agente é exercitado pelo replay do dataset e pela CLI.
  - O debounce de rajada é exercitado pelo replay e por testes com tempo virtual, não por um canal real.
- **A entrega da outbox não está ligada.**
  - Decisões de escalação, efeitos e respostas ao lead são gravados.
  - O `HandoffDispatcher`, com retry por efeito, existe e é testado, mas nenhum processo
    o executa.
  - Não há adapter de canal que envie a mensagem.
- **Mídia real depende do canal.** O adaptador lê arquivo local; baixar mídia do WhatsApp
  fica com o adapter do canal. OCR de CNH ficou fora, e documento sempre escala.
- **Texto de LLM não é determinístico.** Os logs de execução reproduzem o sorteio da API,
  mas a redação da fala pode variar. Preço, franquia, carência e pro-rata não variam.
- **A retenção é um parâmetro fixo** de `open_sales_stack`, de 24 h. A varredura por turno
  não tem índice em `atualizada_em`.
- **Processo único.** Lock, debounce e deduplicação ficam em memória. Múltiplos workers
  exigiriam coordenação externa.
- **A fração de leads inelegíveis cresce com o tempo**, porque a regra de idade do veículo
  usa `date.today()`. Os números deste README têm ano-base 2026.

---

## Log de execução completa

Os dois documentos são gerados por `scripts/execution_log.py`. O corpo de cada um é a
saída de `python -m interfaces.trace --conversation <id>` sobre o banco que a execução
deixou, e o comando que o reproduz está no topo.

- [`docs/execucao-completa.md`](docs/execucao-completa.md), com `conv_00028`:
  - qualificação em três turnos e data de vigência;
  - objeção classificada pelo modelo;
  - uma cotação com três tentativas: a primeira falha com HTTP 500, a segunda é lenta e
    fica sem resposta, e a terceira é o hedge, que a resgata;
  - apresentação com carência e pro-rata, conferida contra a tabela no perfil real.
- [`docs/execucao-escalacao.md`](docs/execucao-escalacao.md), com `conv_00076` e a API a
  100% de falha: a escada inteira falha, a conversa escala por `cotacao_esgotada` e o
  documento mostra o snapshot que o vendedor recebe.

---

## Uso de IA

`ai-logs/` contém as sessões de ferramentas de IA (Claude Code e Codex) usadas no desafio,
exportadas das sessões locais em ordem cronológica. [`ai-logs/README.md`](ai-logs/README.md)
diz o que é cada arquivo e o que foi removido na exportação.

---

## Revisão contra os critérios do desafio

Onde cada critério está demonstrado no repositório e, sem conserto nesta entrega, onde a
demonstração é fraca.

**1. Funciona de ponta a ponta.**
- **Onde:** `python -m interfaces.cli --trace`, com a sessão real em
  [`docs/demo-cli.md`](docs/demo-cli.md); o harness `scripts/measure_end_to_end.py` sobre
  150 conversas do dataset (`docs/measurements/task13-e2e.json`: 72/74 elegíveis sem
  documento); [`docs/execucao-completa.md`](docs/execucao-completa.md).
- **Fraco:** não há canal real — nem webhook de WhatsApp, nem entrega da outbox ligada. A
  taxa fim a fim depende de 18,9% de turnos sintéticos, porque o dataset não traz data de
  vigência.

**2. O que faz quando a `/quote` falha.**
- **Onde:** a cadeia em `src/infrastructure/quote/`, testada sem rede em
  `tests/unit/test_retry.py`, `test_hedge.py` e `test_residual_rate.py` (falha residual de
  1,27%); a escada em [Quando a `/quote` falha](#quando-a-quote-falha). Os três caminhos
  estão gravados com a API real:
  - falha e retry, em `docs/demo-cli.md`;
  - chamada lenta resgatada pelo hedge, em `docs/execucao-completa.md`;
  - escada esgotada e escalação com snapshot, em `docs/execucao-escalacao.md`.
- **Fraco:** o hedge nos logs depende de relógio de parede; é reproduzível na prática, não
  por construção. Não há nível intermediário de promessa assíncrona — é decisão, mas o lead
  numa indisponibilidade longa só tem a escalação.

**3. Critério de escalação explícito e defensável.**
- **Onde:** dez regras em `src/domain/handoff.py`, uma classe cada, testadas em
  `tests/unit/test_handoff.py`; a tabela em [Handoff](#handoff), com a contagem medida de
  cada regra. Recusa por regra não é handoff: 751 inelegíveis recusados sem escalar. A
  opinião do modelo e a decisão da política ficam gravadas em todo turno de fala
  (`turn_events.sugestao`).
- **Fraco:** o dataset não aciona pedido de humano, fora de escopo nem divergência — zero
  ocorrências dos termos nas 26.470 mensagens. Os limiares (três objeções de preço, três
  pedidos do mesmo dado) são escolha de desenho, sem calibração com tráfego real.

**4. Dá para rastrear o que aconteceu.**
- **Onde:** `quote_attempts`, com uma linha por chamada física, e `turn_events`, costurados
  pelo `trace_id` do turno; `python -m interfaces.trace` lê cotação ou conversa inteira, só
  em leitura. Os dois logs de execução são literalmente a saída desse comando.
- **Fraco:** a inspeção é por conversa, na linha de comando. Não há visão agregada nem
  alerta: as métricas agregadas só existem nas rodadas do harness.

**5. Cuidado com dado sensível.**
- **Onde:** redação na entrada em `src/infrastructure/privacy/`, com CPF validado por
  dígito verificador e auditado nos 2.500 casos (`tests/regression/test_cpf_audit.py`);
  processador de redação no logger; identidade de canal em hash; CEP como único slot em
  claro, purgado no encerramento ou após 24 h (D-036, D-038); logs de IA redigidos e nenhuma
  chave no histórico do git.
- **Fraco:** o CEP fica em claro no SQLite enquanto a conversa está aberta, sem cifragem
  (decisão registrada). Imagem e áudio vão a um provedor externo de LLM. A purga é
  oportunista, sem índice em `atualizada_em`.

**6. Qualidade e legibilidade das decisões.**
- **Onde:** [Decisões](#decisões), com o número que sustenta cada uma;
  [`docs/DECISIONS.md`](docs/DECISIONS.md), de D-001 a D-040, cada uma com alternativas e
  consequência; [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md); números do dataset gerados
  por script em `docs/measurements/dataset-facts.json`.
- **Fraco:** o volume. São 40 decisões, com cadeias de "supera D-0xx" que exigem leitura em
  ordem, e o README é longo. Falta um resumo de uma página.

**7. Como a IA foi usada.**
- **Onde:** [`ai-logs/`](ai-logs/README.md), com 23 sessões (Codex nas tarefas 1 a 9,
  Claude Code da 9 à 13) e um índice; `AGENTS.md` e `CLAUDE.md` como instruções versionadas
  para os agentes.
- **Fraco:** os logs são exportações com resultados de ferramenta cortados e raciocínio
  removido. Ligar um commit ao pedido que o gerou exige ler a sessão inteira.

---

## Registro

As decisões de implementação, em ordem, estão em [`docs/DECISIONS.md`](docs/DECISIONS.md)
(D-001 a D-040). O estado do sistema está em [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) e
o que cada fase entregou, com o número do portão de saída, em
[`docs/CHANGELOG.md`](docs/CHANGELOG.md). Os números da tarefa 8, de 88,48% em idade e
93,88% em ano, mediam progresso na conversa sob um corte de 2 s por chamada, não
acurácia; a extração isolada está em [Resultados](#resultados).

Validação no commit final (tarefa 13):
- `uv run pytest -m "not slow"`: **682 passed**;
- `uv run pytest` com o corpus local: **695 passed**;
- ruff e mypy limpos.
