# AutoSeguro — agente de cotação de seguro auto

Agente que atende leads de seguro de veículo por WhatsApp: conversa, qualifica,
cota usando a API legada da seguradora e decide sozinho quando resolver e quando
passar para um humano.

Desafio técnico FDE / AI Engineer — Namastex.

---

## Rodando

Pré-requisitos: Docker, [uv](https://docs.astral.sh/uv/), e uma chave de API do
provedor de LLM.

```bash
# 1. API de cotação (do repo do desafio) + Postgres + Redis
docker compose up --build          # quote-api em :8000

# 2. dependências e migrations
uv sync
uv run alembic upgrade head

# 3. variáveis
cp .env.example .env               # preencha LLM_API_KEY

# 4. conversa
uv run python -m interfaces.cli
```

Console de avaliação:

```bash
uv run streamlit run src/interfaces/streamlit_app.py
```

Replay de uma conversa do dataset, de ponta a ponta:

```bash
uv run python -m interfaces.replay --conversation conv_00013
```

Testes:

```bash
uv run pytest                      # offline, sem rede, segundos
uv run pytest -m eval              # avaliação de LLM, consome API
```

---

## A tese

**Nenhum número que o lead vê foi produzido pelo LLM.**

Prêmio, franquia e pro-rata saem exclusivamente do payload da `POST /quote`,
renderizados por template. O LLM decide *quando* cotar; nunca *quanto* custa.

Todas as decisões abaixo derivam disso.

---

## Decisões

Cada decisão está com o número que a sustenta. Os números foram medidos no
dataset e no código do `quote-service`, não estimados.

### 1. `base_mensal` e os multiplicadores nunca saem da API

`GET /planos` devolve a tabela de preços inteira. Se ela chegar ao contexto do
LLM — por prompt ou por resultado de ferramenta — o modelo passa a ter tudo que
precisa para calcular o prêmio sozinho. Em algum turno ele vai calcular, e o
resultado vai estar *quase* certo, que é pior que uma alucinação óbvia.

Por isso `/planos` **não é ferramenta do LLM**. É infraestrutura, buscada uma vez,
cacheada, e projetada em duas estruturas:

| Projeção | Conteúdo | Vai ao contexto? |
|---|---|---|
| `AcceptanceRules` | limites de idade, idade do veículo, planos válidos | Não |
| `ProductFacts` | nome, coberturas, franquia, carência, pro-rata | Sim |

Os campos de precificação não entram em nenhuma das duas: são descartados no
parse. O sistema nunca os materializa fora do cliente HTTP.

Isso é uma garantia estrutural, não uma instrução de prompt. O modelo não
desobedece porque não tem o dado.

### 2. Nem toda resposta ruim da `/quote` é uma falha

| Status | Significado | Retenta? |
|---|---|---|
| `200` | cotação | — |
| `422 cotacao_recusada` | a seguradora respondeu, e recusou | Nunca |
| `400 payload_invalido` | bug nosso | Nunca |
| `5xx`, `408`, `425`, `429`, timeout | falha transitória | Sim |

Tratar `422` como falha faz o agente retentar uma recusa determinística, gastar
segundos e dizer "estamos instáveis" quando a resposta correta era "não
aceitamos esse perfil". São mensagens completamente diferentes para o lead.

Recusa é modelada como **resultado**, não exceção:

```python
QuoteOutcome = Quote | Declined      # a seguradora respondeu
QuoteUnavailable                     # ninguém respondeu — transitório
QuoteContractError                   # nosso payload está errado — não transitório
```

Com isso o decorator de retry captura exatamente uma exceção, e a recusa
atravessa a cadeia inteira sem que nenhuma camada precise saber que ela existe.

### 3. Recusa por regra de aceitação não é handoff

Medido no dataset, com ano-base 2026:

```
recusa por idade > 75 anos       11,2%
recusa por veículo > 20 anos     21,2%
total                            30,0%   (751 de 2.500 leads)
```

Quase um terço do tráfego é inelegível. O agente recusa com o motivo e encerra.
Escalar 30% das conversas para um humano seria falhar no critério de handoff, não
cumpri-lo.

Como as regras são determinísticas e conhecidas, a recusa acontece **antes** da
chamada de rede — o guard no topo da cadeia consulta `AcceptanceRules` e responde
na hora, sem gastar um retry num `422` previsível.

Observação: a API deriva a idade do veículo de `date.today()`, então essa fração
cresce sozinha com o tempo.

### 4. O CEP é imutável depois de coletado

`cep` é opcional na `/quote`, e omiti-lo zera o agravo de região (multiplicador
1.30). No dataset, **35,8% dos leads têm CEP em prefixo agravado** (07, 08, 21,
26, 59).

Um agente que esquece o CEP entrega um preço 30% menor por acidente. Pior: cria
um caminho onde "recotar" barateia. Uma vez que o CEP entra no estado da conversa,
ele vai em toda chamada.

### 5. Sem circuit breaker

O `quote-service` sorteia a falha por chamada:

```
QUOTE_FAILURE_RATE = 0.20   → 500/502/503 imediato
QUOTE_SLOW_RATE    = 0.10   → sleep de 8s
```

A falha é **independente e sem estado** — o serviço nunca cai de verdade. Um
circuit breaker calibrado normalmente abriria numa sequência aleatória de três ou
quatro falhas e passaria a recusar chamadas com 80% de chance de sucesso. Ele
pioraria o sistema.

Como a falha é independente, retry simples resolve quase tudo: três tentativas
deixam 0,8% de falha residual; quatro deixam 0,16%.

### 6. Hedged requests para a cauda de latência

A `/quote` é uma **função pura**: sem estado, sem efeito colateral, resultado
determinístico dados os cinco campos e a data. Isso permite duas coisas.

Primeiro, o cache é exato, não uma aproximação. Chave = os cinco slots
normalizados + o dia de referência, TTL até meia-noite. Recusas também são
cacheadas.

Segundo, dá para disparar uma segunda chamada após ~1,5s e ficar com a primeira
que voltar. Como 10% das chamadas dormem 8 segundos, isso elimina quase toda a
cauda de latência por um custo trivial — e é seguro justamente porque a chamada
não tem efeito colateral.

### 7. Um agente, seis responsabilidades, duas com LLM

Não há supervisor nem enxame de especialistas. As conversas do dataset têm 8 a 14
mensagens; o overhead de roteamento custaria mais turnos do que a especialização
economizaria. E um roteador com LLM introduziria não-determinismo exatamente onde
o desafio pede critério explícito.

| Responsabilidade | Usa LLM |
|---|---|
| Extrator de slots | sim — structured output, modelo barato, sem persona |
| Conversador | sim — persona, decide quando cotar |
| Política de aceitação | não |
| Política de handoff | não |
| Cadeia de cotação | não |
| Apresentação | não |

O extrator não recebe persona, histórico nem `ProductFacts`. O conversador nunca
vê os campos de precificação. Isso é isolamento de contexto como garantia, não
como organização.

Efeito colateral útil: numa recusa por regra, o conversador nem é chamado — a
política decide e o template escreve. 30% dos leads são atendidos com uma chamada
de LLM em vez de duas.

### 8. A chave do lead é o hash do CPF

`sender_name` não é identidade: 336 nomes distintos para 2.500 conversas.
"Joao Gomes" aparece em 17 conversas que são pessoas diferentes, com idades e
veículos distintos. Os CPFs são 2.500 distintos, sem repetição.

Chavear lead por nome funde até 17 pessoas num registro só. E o CPF é o dado que
precisa ser hasheado por privacidade de qualquer forma — uma decisão atende os
dois critérios.

### 9. Ordenação por `message_index`, nunca por `timestamp`

Apenas **5 de 2.500** conversas do dataset têm timestamp monotônico. Ordenar por
`timestamp` reposiciona **67,6%** das mensagens: a resposta do vendedor vem antes
da pergunta.

O timestamp serve apenas para medir intervalo aproximado, e com `abs()`.

### 10. Envelope canônico de mensagem

Todos os canais traduzem para um `InboundMessage` único e recebem um
`OutboundMessage` único. Os adapters não têm lógica — só tradução.

A propriedade que isso compra: as colunas do parquet já são praticamente o
envelope, então o replay do dataset não é um caminho especial. **A mesma mensagem
que chegaria do WhatsApp em produção é a que roda no teste de regressão.**

O envelope carrega intenção, não capacidade de canal. "Mostrar a cotação" é a
intenção; como isso vira texto, painel ou mensagem formatada é problema do
adapter.

`OutboundMessage` é persistido antes de entregue — é a tabela outbox, e resolve a
entrega duplicada ou perdida se o processo cair entre gravar e enviar.

---

## Arquitetura

```
src/
  domain/          entidades e regras puras. ZERO import de framework
  application/     casos de uso e portas (Protocol)
  agent/           LangGraph: grafo, nós, prompts, templates
  infrastructure/  quote/, planos/, persistence/, privacy/
  interfaces/      webhook, cli, replay, streamlit_app
```

Hexagonal, dependência sempre para dentro. `domain/` e `application/` não
importam `langgraph`, `httpx`, `redis` nem `sqlalchemy` — o grafo é detalhe de
orquestração, não o núcleo. É isso que permite testar toda a política de handoff
sem subir nada.

### O grafo

```
ingest → extract → policy → quote → present → close | handoff
             ↑        │                  │
         objection ◄──┘                  └──► handoff
```

`extract` faz structured output e é medido contra o golden set. `policy` avalia
aceitação e handoff antes de gastar rede. `present` renderiza template a partir do
payload da API.

### A cadeia de cotação

```
Guard → Cache → Retry → Hedge → Trace → Http
```

Cada camada é um decorator que implementa `QuoteProvider`. Comportamento novo
entra como decorator novo. `Clock`, `sleep` e o gerador aleatório são injetados
por construtor — por isso a cadeia inteira é testável offline em milissegundos.

---

## Quando a `/quote` falha

Escada de degradação explícita:

| Nível | Ação | Lead percebe? |
|---|---|---|
| N0 | chamada direta, timeout de 2s | não |
| N1 | retry com backoff e jitter, teto de tentativas e de tempo | não |
| N2 | cache de cotação equivalente | não |
| N3 | handoff com snapshot completo | sim |

Nunca: preço estimado pelo agente.

N0 a N2 são a cadeia de decorators — resiliência transparente. N3 é decisão de
conversa e vive no grafo: se a cadeia entregasse o handoff, o `QuoteProvider`
passaria a saber mandar mensagem no WhatsApp e a abstração teria morrido.

Toda tentativa física vira uma linha em `quote_attempts`, inclusive as hedgeadas.

---

## Handoff

Motor determinístico. Uma classe por regra, avaliadas a cada turno. O LLM pode
sugerir handoff como sinal adicional, mas quem decide é a política — e as duas
opiniões são gravadas, porque a divergência é material de análise.

Gatilhos:

| Gatilho | Frequência esperada |
|---|---|
| Mídia recebida sem transcrição | 56,8% das conversas contêm mídia |
| Cotação esgotou o budget de resiliência | ~0,2% com três tentativas |
| Pedido de desconto fora da tabela | ~52% dos leads chegam com objeção |
| Pedido explícito de humano | raro |
| Laço de esclarecimento no mesmo slot | — |
| Fora de escopo (sinistro, cobrança, cancelamento) | — |

O handoff produz três efeitos, através de um port `HandoffSink`:

1. **mensagem ao lead** — primeiro, porque é o único efeito com prazo humano
2. **webhook ao time de vendas** — com o snapshot
3. **registro via API** — para a fila de atendimento

A decisão é persistida antes dos efeitos; os efeitos passam pela outbox e são
retentados individualmente. Handoff é justamente o caminho onde as coisas já
estão dando errado, então efeito parcial não pode deixar o lead avisado e o time
sem saber.

O snapshot importa mais que o aviso: slots coletados, tentativas de cotação com
status e latência, e o motivo do escalonamento. Sem isso o vendedor recomeça do
zero e o handoff virou transferência de problema.

---

## Rastreabilidade

Postgres é fonte da verdade. A tabela central:

```sql
quote_attempts (
  id, trace_id, conversation_id, fingerprint,
  tentativa, status, http_status, latencia_ms, origem, erro, criado_em
)
-- status ∈ quoted | declined | unavailable | contract_error
-- origem ∈ api | cache | regra_local
```

Uma linha por chamada física. `trace_id` propaga por toda a cadeia; `fingerprint`
é o hash dos cinco slots + dia de referência, e liga tentativas da mesma cotação.

Para inspecionar uma conversa inteira:

```bash
uv run python -m interfaces.trace conv_00013
```

---

## Privacidade

O dataset é sintético, mas tratado como se não fosse. Medido: CPF aparece em
100% das conversas, CEP em 3.879 mensagens, e-mail e telefone em 1.379 cada,
placa em 839.

A redação acontece **na entrada**, antes de qualquer coisa tocar log, contexto de
LLM ou banco:

- CPF com validação de dígito verificador — regex sozinho gera falso positivo em
  placas e números soltos
- e-mail, telefone, placa e CEP completo
- processador de redação no logger, para não vazar em traceback
- identificador do lead armazenado como hash

O bloco de PII no dataset tem ordem embaralhada e caixa inconsistente ("CPF",
"Cpf", "cpf" — todos ocorrem), então o parser é case-insensitive e não posicional.

O console Streamlit renderiza texto redigido por padrão.

---

## O dataset: como foi usado

O histórico contém uma armadilha. O vendedor sintético sorteia plano e preço de
forma independente:

```
0 de 2.500 cotações batem com a base_mensal do plano citado
as 2.500 usam a mesma frase de cobertura nos três planos
nenhuma menciona carência
```

Usar essas mensagens como few-shot ensinaria o agente exatamente o comportamento
que o desafio proíbe. **O histórico foi usado como conjunto de entrada e como
baseline a ser batida, não como exemplo a ser imitado.**

| Camada do dado | Uso |
|---|---|
| Mensagens do lead | fixture, distribuição de tráfego, script de replay |
| `lead_idade_informada`, `veiculo_texto` | golden set de extração |
| Cotações do vendedor | oráculo negativo |
| Tom e fluxo | referência de registro e de ordem de qualificação |
| Preço, plano, cobertura | nada — a fonte de verdade é a API |

Um achado que virou decisão de produto: o vendedor humano pede CPF em 100% das
conversas (dado que a API não usa) e nunca pergunta a data de vigência (dado que
a API usa). O agente coleta os cinco campos que a `/quote` realmente consome, e
não pede CPF para cotar.

---

## Testes e avaliação

```
tests/unit/          domínio, decorators, políticas, templates — ms, sem I/O
tests/integration/   Postgres, Redis, /quote com QUOTE_SEED fixo
tests/golden/        extração contra 2.500 casos com ground truth
tests/regression/    as 751 conversas inelegíveis
```

A linha entre teste e avaliação importa. Extração de slots e geração de fala
dependem de LLM: não existe vermelho-verde para prompt, então isso é avaliação com
limiar. As 751 recusas, não — o critério é binário: o agente recusa todas e nunca
emite preço nelas.

A escada de degradação inteira roda **sem Docker**: a folha da cadeia é um duplo
programado para falhar N vezes. Nenhum teste depende do sorteio de falha da API.

Para o end-to-end:

```bash
QUOTE_SEED=42 docker compose up            # falhas reprodutíveis
QUOTE_FAILURE_RATE=1.0 docker compose up   # força a escada até o N3
```

### Resultados

| Métrica | Baseline humana | Agente |
|---|---|---|
| Extração de idade (2.500 casos) | — | `<preencher>` |
| Extração de ano do veículo | — | `<preencher>` |
| Recusas corretas (751 casos) | 0 / 751 | `<preencher>` |
| Cotações consistentes com a tabela | 0 / 2.500 | `<preencher>` |
| Menção de carência quando aplicável | 0 / 2.500 | `<preencher>` |

---

## O que ficou de fora, e por quê

**Vector database / RAG.** O corpus de objeções são 6 variantes canônicas, 36
textos distintos e 5 concorrentes. Isso é um enum, não um corpus — um dicionário
resolve com mais precisão e zero infraestrutura. E indexar as respostas do
vendedor seria indexar preço errado.

**Base de conhecimento no prompt.** Cobertura, franquia, carência e pro-rata já
vêm no payload da `/quote`. Duplicar no prompt criaria uma cópia que envelhece
enquanto a original não. O prompt carrega só comportamento.

**Circuit breaker.** Ver decisão 5.

**Calculador local de prêmio.** Tentador, já que as regras estão no `/planos`.
Criaria uma segunda fonte de verdade que divergiria em silêncio.

**Scoring / modelo de propensão.** Não há sinal: a correlação entre preço e
desfecho no dataset é 0,02, e a distribuição de desfecho por plano é
estatisticamente idêntica.

**Memória de longo prazo.** Conversas têm 8 a 14 mensagens.

**Promessa com retomada assíncrona.** Um nível intermediário entre cache e
handoff — "não consegui agora, te retorno" com job em background — exigiria
worker, agendamento e mensagem proativa. Dado o prazo, a escada vai do N2 direto
ao handoff com contexto completo. Um N3 meia-boca é pior que um handoff honesto.

---

## Limitações conhecidas

- **Lock por conversa e debounce de rajada** estão implementados mas só são
  exercitados pelo webhook. Nos adapters síncronos não há concorrência real. O
  dataset mostra que a rajada existe (23,1% dos turnos do lead têm 2+ mensagens,
  pico de 6), mas a demonstração completa depende do canal real.
- **Postgres e Redis** são mais do que este escopo exige — SQLite resolveria. A
  escolha foi por caminho de produção, não por necessidade.
- **Mídia** é detectada e escalada, nunca interpretada. Transcrição de áudio e
  OCR de CNH ficaram fora.
- A **fração de leads inelegíveis cresce com o tempo**, porque a regra de idade do
  veículo usa `date.today()`. Os números deste README têm ano-base 2026.

---

## Log de execução completa

`docs/execucao-completa.md` traz uma conversa do início ao fim: mensagens do lead,
extração turno a turno, avaliação das políticas, tentativas de cotação com status
e latência, e a mensagem final com carência e pro-rata.

Foi gerada por replay de uma conversa real do dataset, com `QUOTE_SEED` fixo.

---

## Uso de IA

`ai-logs/` contém as conversas com ferramentas de IA durante o desafio, incluindo
a análise exploratória do dataset que produziu os números deste README e as
decisões que foram revistas no caminho — RAG descartado, circuit breaker
rejeitado, base de conhecimento retirada do prompt.