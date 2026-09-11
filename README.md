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
# 1. API de cotação (do repo do desafio). A aplicação não tem serviço de dados.
docker compose up --build          # quote-api em :8000

# 2. dependências. O esquema SQLite é aplicado no startup.
uv sync

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
uv run pytest -m "not slow"        # loop rápido, offline
uv run pytest -m slow              # simulações estatísticas determinísticas
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

Segundo, dá para disparar uma segunda chamada após 100 ms (p99 medido: 44,73 ms) e ficar com a primeira
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

### 8. A chave do lead é a identidade de canal

`sender_name` não é identidade: 336 nomes distintos para 2.500 conversas.
"Joao Gomes" aparece em 17 conversas que são pessoas diferentes, com idades e
veículos distintos. Chavear por nome funde 17 pessoas num registro só.

O CPF seria único, mas **o agente não pede CPF** (ver decisão 1 sobre coleta
mínima), então ele não existe em tempo real. A chave é `wa_id` no WhatsApp e
`conversation_id` no replay. O hash do CPF fica como atributo opcional, preenchido
quando o lead informa espontaneamente.

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

### 11. Ano-modelo futuro é normalizado no payload

A API calcula a idade do veículo como `ano_atual − ano_modelo`. No Brasil, modelo
do ano seguinte é rotineiro a partir do segundo semestre — um 2027 vendido em
2026. Isso produz idade −1, nenhuma faixa cobre valor negativo, e a API devolve
`422`: recusa de um carro zero-quilômetro.

Normalizar só na validação local criaria divergência — o guard aceitaria e a API
recusaria depois de uma ida à rede. Por isso a requisição sai com
`veiculo_ano = min(veiculo_ano, ano_atual)`.

É neutro em preço, já que a faixa de 0 a 5 anos tem multiplicador único: contorna
um off-by-one do sistema legado sem alterar valor. Com três limites: normaliza
apenas um ano à frente, nunca no sentido inverso, e grava
`quote_attempts.ano_normalizado` — ajustar dado do usuário antes de enviar à fonte
de verdade precisa aparecer na auditoria.

Dois ou mais anos no futuro não é ano-modelo, é erro de digitação. Aí o agente
confirma com o lead.

O dataset cobre 2001 a 2024, então nenhum caso aparece no replay. A armadilha só
existe em produção.

### 12. SQLite, sem serviço de dados externo

O sistema roda em processo único. Nessa topologia, lock por conversa é
`asyncio.Lock`, debounce é janela em memória, e dedup de webhook é índice único em
`provider_message_id`. Nenhum deles precisa de servidor.

Postgres e Redis estavam no desenho inicial por sinalizarem caminho de produção.
Mas uma dependência que o código não exercita custa mais credibilidade do que
agrega — Redis presente no `docker-compose` e ausente do caminho crítico lê pior
que a ausência dele.

O cache de cotação ficou melhor como tabela do que como Redis: fica visível no
console de trace ao lado das tentativas, o que torna o nível N2 da escada
demonstrável em vez de invisível.

Uma consequência que exige disciplina: SQLite não tem tipo decimal. Valor
monetário é `TEXT` com o `Decimal` serializado, nunca `REAL`. Erro de ponto
flutuante num sistema cuja invariante principal é a integridade do preço seria
irônico demais.

O gatilho para reintroduzir um serviço externo é topologia com múltiplos workers,
não volume de dados.

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
importam `langgraph`, `httpx` nem `sqlite3` — o grafo é detalhe de
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
ApplicationTrace → Guard → Cache → Retry → Hedge → WireTrace → Http
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

SQLite é a única dependência de dados. A tabela central:

```sql
quote_attempts (
  id, trace_id, conversation_id, fingerprint,
  tentativa, status, http_status, latencia_ms, origem, erro, criado_em
)
-- status ∈ quoted | declined | unavailable | contract_error
-- origem ∈ api | cache | regra_local
```

Uma linha por chamada física e uma por desfecho lógico (tentativa zero).
`trace_id` propaga por toda a cadeia; `fingerprint`
é o hash dos cinco slots + dia de referência, e liga tentativas da mesma cotação.

Para inspecionar uma conversa inteira:

```bash
uv run python -m interfaces.trace <trace_id> --database autoseguro.sqlite
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

### Retenção: slot é dado operacional, mensagem é log

A redação protege o histórico: mensagens, logs, contexto de LLM e trace. O CEP é a
exceção deliberada — sem ele a cotação perde o agravo de região —, então fica em
`conversations.slots` enquanto a conversa está aberta, sobrevive a reinício e nunca
volta ao texto persistido.

**Política: os slots são purgados no encerramento da conversa.** Encerrar apaga
`conversations.slots` e o estado do grafo; a recusa final encerra na hora. Permanecem o
histórico redigido e o trace. Enquanto a conversa está aberta o CEP fica em claro no
SQLite; cifragem em repouso e encerramento por inatividade ficam como evolução (D-036).

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
tests/integration/   SQLite em memória, /quote com QUOTE_SEED fixo
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

Duas métricas diferentes, medidas separadamente. Na tarefa 8 elas se misturaram e
produziram um número enganoso (ver abaixo).

#### Acurácia de extração — isolada, sem cotação

Só o extrator: sem cotação, sem grafo, sem orçamento de turno. As rajadas do lead
entram em ordem, os slots acumulam, e o gabarito nunca entra no contexto. 2.500
conversas, capturas versionadas em `tests/fixtures/llm-isolated/`.

| Modelo | Idade | Ano-modelo | Mediana / p95 por chamada | Custo |
|---|---:|---:|---:|---:|
| gpt-4.1-mini — **em uso** | **99,96%** (2.499/2.500) | **100%** (2.500/2.500) | 1.482 / 2.268 ms | US$ 2,86 |
| gpt-4.1-nano — **rejeitado** | 89,32% | 97,88% | 1.465 / 2.666 ms | US$ 0,78 |

O nano perde 10 pontos em idade e **não melhora a latência**: o tempo é a ida até o
OpenRouter, não a inferência. O ganho de orçamento tinha de vir do turno, não da
troca de modelo.

Os 88,48% (idade) e 93,88% (ano) da tarefa 8 mediam outra coisa. A extração rodava
sob o corte de 2 s por chamada, e 288 conversas eram interrompidas antes de chegar à
idade; nas 2.212 concluídas, os dois campos estavam corretos. Era gradiente de
progresso na conversa, não acurácia.

#### Taxa de conclusão fim a fim — sob a instabilidade real

Agente inteiro: ingestão, extrator e conversador reais, cadeia de cotação real contra
a API local com `QUOTE_SEED=42`, 20% de falha e 10% de lentidão. 150 conversas do
dataset sorteadas com seed 2026. Concluída = chegou a apresentar cotação.

| Desfecho | 9.2 antes (6 s, LLM 2,0/2,5 s, 4.000 tokens) | 9.2 depois (10 s, LLM 4,5 s, 16.000 tokens) | Tarefa 10 (mídia e prompt) |
|---|---:|---:|---:|
| **Cotada** | **0 (0%)** | **38 (25,3%)** | **64 (42,7%)** |
| Recusa por regra de aceitação | 42 (28,0%) | 44 (29,3%) | 44 (29,3%) |
| Documento recebido | 62 (41,3%)¹ | 63 (42,0%)¹ | 31 (20,7%) |
| Segundo áudio sem texto | — | — | 0 |
| Limite de tokens da conversa | 27 (18,0%) | 0 | 0 |
| LLM cortado ou indisponível | 19 (12,7%) | 4 (2,7%) | 9 (6,0%) |
| Cotação indisponível após a escada | 0 | 1 (0,7%) | 2 (1,3%) |
| Prazo do turno | 0 | 0 | 0 |

¹ Até a tarefa 9.2, documento, imagem e áudio escalavam juntos na primeira mídia.

**Três ressalvas, sem as quais os números leem como inflação:**

1. **Há duas taxas, e a significativa é a menor base.** 42,7% é sobre as 150
   conversas, cujo denominador inclui 45 inelegíveis — que devem ser recusados, e 44
   foram — e 31 elegíveis que mandaram documento, que escala por decisão de
   privacidade, não por falha. A taxa que mede o agente é a das **74 elegíveis sem
   documento: 64 cotaram (86,5%)**; as outras 10 são 8 cortes de LLM e 2 cotações
   indisponíveis. Na 9.2 a mesma conta deu 38 de 42 (90,5%), com base diferente: lá
   imagem e áudio também escalavam e tiravam da base as conversas mais longas.
2. **117 dos 649 turnos (18,0%) são sintéticos.** O dataset nunca traz data de
   vigência; o harness responde quando o agente pede e, se a conversa termina sem
   cotação, pede o plano Completo. Sem isso nenhuma conversa do dataset cotaria. A
   taxa mede o agente diante de um lead que responde ao que é perguntado.
3. **O 100% de roteamento de objeção sobre o dataset é por construção.** A lista
   lexical foi escrita sobre as 36 frases do gerador; que ela cubra as 1.295
   conversas só prova que a lista cobre a lista (o roteador antigo cobria 220). O
   número que generaliza é o do modelo: nas 34 conversas em que uma objeção chegou
   ao agente, as 34 foram ao nó, com 54 classificações feitas pelo modelo e nenhuma
   pelo piso.

- Todas as recusas são de leads inelegíveis pelo oráculo e nenhum inelegível recebeu
  cotação, nos três cenários. As 44 conversas recusadas terminaram encerradas, com
  slots vazios.
- Nenhuma das 649 falas do agente pediu documento, foto ou CPF.
- Mídia no replay: 33 imagens e 26 áudios chegaram só como marcador, sem arquivo.
  Nenhuma imagem escalou e nenhuma conversa teve dois áudios sem texto. Os ramos
  resolvidos são exercitados só pelas fixtures (tabela abaixo).
- No "antes", o prazo aparecia como corte de chamada de LLM em 2–2,5 s e como estouro
  de 4.000 tokens, não como prazo do turno.
- Tarefa 10: turno p50 1,72 s, p95 4,17 s, máximo 6,11 s, nenhum acima de 8 s; custo
  conhecido US$ 0,71 (`docs/measurements/task10-e2e-depois.json`).

#### Mídia exercitada de verdade

O dataset não tem arquivo de mídia. Os ramos resolvidos rodam contra o modelo real
(`google/gemini-2.5-flash`) só sobre as fixtures de `tests/fixtures/media`, via
`scripts/probe_media.py` (`docs/measurements/task10-media.json`):

| Fixture | Resultado real | Latência | Resposta do agente |
|---|---|---:|---|
| Foto nítida de veículo | veículo, confiança alta | 1.984 ms | reconhece e segue |
| Foto escura e borrada | veículo, confiança baixa | 1.895 ms | nota neutra ("não sei") |
| Foto de gato | não é veículo, confiança alta | 1.669 ms | nota neutra, sem acusar |
| Áudio curto (voz sintética) | "Tenho 32 anos e o meu carro é um anix, ano 2020." | 1.901 ms | slots `transcrito`, pedem confirmação |

A transcrição errou "Ônix" — é por isso que slot transcrito nunca vai direto para a
cotação. Documento não tem adaptador: nunca é enviado a provedor externo.

#### Onde o tempo do turno vai

Cenário "depois", 573 turnos:

| Etapa | p50 | p95 | p99 | Teto |
|---|---:|---:|---:|---:|
| Extração (todo turno) | 1,66 s | 2,61 s | 3,48 s | 3,5 s |
| Fala do conversador (123 turnos) | 1,65 s | 3,08 s | 4,08 s | 4,5 s |
| Cotação (39 turnos) | 70 ms | 2,05 s | 2,17 s | 3,5 s |
| Política | 2 ms | 4 ms | 12 ms | — |
| **Turno inteiro** | **1,68 s** | **4,42 s** | **5,54 s** | **10 s** |

Nenhum turno passou de 8 s; dois passaram de 6 s. Espera percebida pelo lead, com a
janela de rajada de 500 ms: p50 2,25 s, p95 4,99 s, máximo 7,31 s. Medições brutas em
`docs/measurements/task9-e2e-{medicao,antes,depois}.json`. Reprodução, com a API local
em `:18010`: `uv run --env-file .env python -m scripts.measure_end_to_end --scenario depois`.

#### Ainda a medir

| Métrica | Baseline humana | Agente |
|---|---|---|
| Recusas corretas (751 casos) | 0 / 751 | `<preencher>` |
| Cotações consistentes com a tabela | 0 / 2.500 | `<preencher>` |
| Menção de carência quando aplicável | 0 / 2.500 | `<preencher>` |

Resiliência medida com os decorators reais e uma folha simulada, sem rede:

| Configuração | Orçamento | Antes (Tarefa 4) | Depois (Tarefa 5) |
|---|---|---:|---:|
| Sem hedge, três tentativas | Sem corte por tempo | **2,72%** (272/10.000) | **2,72%** (272/10.000) |
| Com hedge, três tentativas | Sem corte por tempo | **1,18%** (118/10.000) | **1,18%** (118/10.000) |
| Sem hedge, três tentativas | Produção: 3,5 s | **3,29%** (329/10.000) | **3,29%** (329/10.000) |
| Com hedge, três tentativas | Produção: 3,5 s | **2,43%** (243/10.000) | **1,27%** (127/10.000) |

Os oito cenários são reexecutados com seed 42 na folha e 2026 no jitter. A folha
sorteia 20% de falha imediata, 10% de lentidão truncada pelo timeout de 2 s e 70%
de sucesso imediato. Vinte segundos representam ausência de corte por orçamento:
o máximo possível é 10,8 s antes e 6,34 s depois. Três tentativas em todos os casos.
Tolerância de 0,5 ponto percentual sobre teoria sem corte e baseline medida com
corte. A simulação mantém sucesso instantâneo para comparar apenas a recalibração;
não é previsão de throughput nem inclui carga de catálogo/cache/persistência.
Reprodução: `uv run pytest tests/unit/test_residual_rate.py -q -s`.

**Calibração real:** 500 POSTs sequenciais após 20 de aquecimento, HTTP keep-alive,
API do desafio em container separado na porta localhost:18000, com
`QUOTE_FAILURE_RATE=0` e `QUOTE_SLOW_RATE=0`, cliente em WSL. Mediana **15,88 ms**,
p95 **29,17 ms**, p99 **44,73 ms**, máximo **98,96 ms**. Percentis por nearest rank.
A distribuição bruta está em [task5-fast-path.json](docs/measurements/task5-fast-path.json).
Não confundir este máximo observado com teto garantido em outra máquina ou sob carga.

A janela caiu de 1,5 s para **100 ms**, mais de duas vezes o p99 medido. O jitter
passou de exponencial (tetos 100/200 ms nas duas pausas) para **uniforme 0–20 ms
em cada pausa**, configurando base_delay=max_delay=0.02. Esperar mais não recupera
um serviço que sorteia falhas independentes. QuoteConfig mantém todos os valores
substituíveis; a implementação genérica de retry preserva compatibilidade (D-012).

Restam **0,09 ponto percentual** entre 1,18% sem corte e 1,27% com 3,5 s.
O tempo restante é consumido por combinações de chamadas lentas cujo resgate também
falha: cada rodada pode chegar a 2,1 s. Sem hedge, dois timeouts de 2 s já ultrapassam
3,5 s, então reduzir só o jitter não muda seus 3,29%. Chamadas físicas antes/depois:
13.822/13.822, 14.036/14.036, 13.738/13.738 e 13.857/14.027, na ordem da tabela.

A cadeia completa é montada em `infrastructure.wiring.build_quote_provider`.
O chamador injeta cliente, cache, regras, relógio, sleep, RNG, correlação e recorder.
`BufferedAttemptRecorder(SQLiteAttempts(conexao_trace).record)` entrega eventos
sem disputar o orçamento do retry; use conexão de trace distinta da conexão do cache,
no mesmo arquivo SQLite. ApplicationTrace aguarda automaticamente `finish(trace_id)`
antes de retornar, inclusive em erro; não há flush manual na fronteira do chamador.
A fila comporta 1.024 eventos pendentes e descarta com aviso se lotar; queda abrupta
pode perder eventos pendentes. Nenhum payload ou mensagem de exceção é registrado.
Os 3,5 s cobrem Retry/Hedge/HTTP; deadline do turno completo ainda precisa incluir
catálogo e cache. Trace não entra na disputa desse orçamento (D-014).

Para medir e reproduzir uma execução real, com a API rápida já iniciada:

```bash
uv sync
uv run python scripts/measure_quote_latency.py --url http://127.0.0.1:18000 --samples 500
uv run python scripts/demo_quote_trace.py --url http://127.0.0.1:18000 --database autoseguro.sqlite
uv run python -m interfaces.trace <trace_id_impresso> --database autoseguro.sqlite
```

O demo usa perfil sintético; num banco novo, imprime ids de uma resolução API e
uma de cache. Em banco reutilizado, ambas podem vir do cache. A inspeção é somente
leitura, não cria banco ausente. Exemplo realmente executado:
[task5-real-trace.txt](docs/measurements/task5-real-trace.txt).

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

- **Debounce de rajada** só é exercitado de verdade pelo webhook. O dataset mostra
  que a rajada existe (23,1% dos turnos do lead têm 2+ mensagens, pico de 6), mas a
  demonstração completa depende do canal real.
- **Lock, debounce e dedup** funcionam em processo único. Numa topologia com
  múltiplos workers eles precisariam de coordenação externa — esse é o gatilho
  para reintroduzir um serviço como Redis, e não antes dele.
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

### Núcleo determinístico de apresentação e escalação (tarefa 6)

`agent.templates.render_quote` renderiza a cotação validada com ProductFacts:
prêmio e franquia em reais, coberturas, carência do payload e pro-rata apenas
quando presente. Os 12 goldens cobrem três planos, dois perfis de CEP e os dois
casos de pro-rata, capturados da implementação original da API sem rede.
Textos auxiliares são provisórios. A política pura avalia sete regras ordenadas;
a sugestão do LLM é registrada, mas não decide sozinha. Snapshot preserva slots
com proveniência e os registros de tentativas, com CEP redigido na cópia.

A gravação síncrona de trace foi medida com WAL/FULL, 500 amostras e 20 warmups:

| Arquivo SQLite | Mediana | p95 | p99 | Máximo |
|---|---:|---:|---:|---:|
| /tmp nativo | 6,398 ms | 8,448 ms | 9,481 ms | 16,909 ms |
| workspace /mnt/c | 6,071 ms | 10,201 ms | 29,969 ms | 53,187 ms |

O worker permanece: essa cauda importa numa janela de hedge de 100 ms.
ApplicationTrace drena automaticamente antes de retornar, fora do retry.
Medições brutas: `docs/measurements/task6-trace-native.json` e
`docs/measurements/task6-trace-workspace.json`; reprodução pelo script
`scripts/measure_trace_write.py --help`. Esse custo é local e precisa ser
remedido no ambiente de execução. Os testes estatísticos usam a marca `slow`;
o loop de desenvolvimento é `uv run pytest -m "not slow"`.


### Ingestão e avaliação offline (tarefa 7)

Envelopes canônicos separam entrada de intenção de saída. A ingestão redige PII,
deduplica pelo identificador do provedor e agrupa fragmentos por 500 ms de silêncio.
Cada conversa tem consumo serial. Três turnos com objeção lexical de preço
acionam o piso determinístico da regra de desconto, mesmo sem sinal do LLM.
Sete tabelas SQLite guardam identidade de canal pseudonimizada, mensagens redigidas,
traces com FK, snapshots e intenções de outbox. A outbox ainda não entrega efeitos.

```bash
uv run python -m interfaces.replay --conversation conv_00013
uv run python -m tests.golden          # amostra de 48 conversas
uv run python -m tests.golden --full   # corpus local, 2.500 conversas
uv run pytest tests/golden tests/regression -m slow -q
```

`AUTOSEGURO_DATASET` permite escolher o parquet local. Replay ordena por índice e
somente mensagens do lead chegam ao extrator. O relatório completo encontrou
**2.500 conversas e 751 inelegíveis**. `NullExtractor` retorna slots ausentes:
**0% em idade e veículo**, propositalmente; trocar o objeto passado a `evaluate`
ou `tests.golden.__main__.run` liga o extrator real à mesma métrica.
A amostra estratificada tem 48 casos, 32 inelegíveis; textos já estão redigidos.

A auditoria dos 2.500 CPFs rotulados no dataset encontrou todos com dígitos válidos,
**zero falsos positivos e zero falsos negativos**. Casos inválidos são testados
com entradas sintéticas; o corpus sozinho não demonstra cobertura desses casos.
Redação de logger cobre também argumentos e traceback após a formatação.

Envelopes e ingestão ainda não extraem slots de cotação. O futuro grafo precisará
capturar o CEP em estado privado antes da redação, preservando sua imutabilidade.
A janela é em memória: queda do processo exige recuperação futura de turnos a
partir das mensagens persistidas. Grafo, LLM, resolução de mídia, webhook,
console e efeitos da escalação continuam fora desta fase.

Validação da tarefa 7: **381 testes rápidos passaram em 7,79 s**, dez casos slow
excluídos, Ruff e mypy estrito limpos. O perfil local apontou acesso a arquivos
como custo dominante: stat consumiu 4,56 s na coleta instrumentada em /mnt/c.
O modo importlib reduziu a coleta de 4,34 s para 3,61 s sem remover testes.


### Extração estruturada e cliente LLM (tarefa 8)

> Registro histórico. Os números desta seção foram medidos sob o orçamento antigo
> (2 s por chamada, 4.000 tokens) e misturam progresso na conversa com acurácia.
> A acurácia isolada, a taxa de conclusão fim a fim e o nano medido nos 2.500 casos
> estão em [Resultados](#resultados) (tarefa 9, D-034).

Workspace ativo: `/home/rafael/namastex-test-tecnico`; original em `/mnt/c`
preservado. Os mesmos 381 testes caíram de 7,79 s para 1,61 s após a migração.

Avaliação real em 2.500 conversas com `openai/gpt-4.1-mini`, timeout de 2 s,
orçamento de 2,5 s, limite de 4.000 tokens por conversa e 16 conversas concorrentes.
As 24 chamadas do piloto inicial foram reaproveitadas. O replay usa as mesmas
respostas e não chama a rede. O prompt permanece provisório.

| Métrica | Resultado |
|---|---|
| Idade | **88,48% — 2.212/2.500** |
| Ano-modelo | **93,88% — 2.347/2.500** |
| Chamadas físicas | 7.298 |
| Conversas interrompidas por indisponibilidade/prazo | **288 — 11,52%** |
| Conversas concluídas sem interrupção | 2.212; idade e ano corretos nas 2.212 |
| CEP recuperado privadamente como string | 2.500/2.500; zero perdas de zero inicial |
| CEP inteiro emitido pelo modelo | 0; o prompt solicita CEP null |
| Tokens conhecidos, entrada / saída | 5.226.839 / 360.691 |
| Custo conhecido da execução completa | US$ 2,6678412 |
| Custo total estimado | **US$ 2,777447** |
| Latência mediana / p95 por chamada | **1.575,94 ms / 2.346,20 ms** |
| Conversas com limite de tokens excedido | 0 |

As 288 chamadas sem resposta de uso têm custo desconhecido. A estimativa aplica
às 7.298 chamadas o custo médio das 7.010 respostas com uso; não é uma fatura.
As latências incluem todas as chamadas, inclusive falhas. A auditoria de CEP
mede captura determinística antes da redação, não acurácia LLM; CEP não entra
no contexto enviado ao modelo.

A medição inicial tinha 93,84% em ano-modelo. Um caso (`conv_00748`) revelou que
uma atualização incerta sem candidato apagava o ano já informado. Corrigido o
merge com teste de regressão, as mesmas capturas produziram 93,88%. Os relatórios
iniciais foram preservados; nenhuma resposta foi fabricada ou substituída.

As falhas restantes são operacionais. Por formato, houve 99/808 interrupções em
marca/modelo/ano (12,25%), 102/831 em "e um modelo ano" (12,27%) e 87/861 em
"modelo, ano" (10,10%). Esses números não demonstram dificuldade linguística:
não houve erro de slot nas conversas concluídas após corrigir o merge.

O mini foi mantido pela qualidade observada nas respostas concluídas. Um piloto
comparativo do `openai/gpt-4.1-nano` em 24 casos teve duas interrupções e um erro
de idade: 87,5% em idade, 100% em ano; mediana 1.489,61 ms e p95 2.051,31 ms.
A amostra é pequena e não demonstrou melhoria suficiente para trocar o modelo.
Seu custo conhecido adicional foi US$ 0,0068511. Ambos oferecem JSON Schema;
capacidades e preços estão nas páginas oficiais do
[mini](https://openrouter.ai/openai/gpt-4.1-mini) e do
[nano](https://openrouter.ai/openai/gpt-4.1-nano).

Os pisos de regressão são **88% para idade e 93% para ano**, imediatamente abaixo
do observado. Não são SLOs de produção: 11,52% de conversas interrompidas ainda
exigem calibrar latência/roteamento antes de atender leads reais. Os limites de
tempo não foram aumentados para esconder essa perda.

Configure `.env` localmente a partir de `.env.example`; ela é ignorada pelo Git.
O corpus bruto continua externo, indicado por `AUTOSEGURO_DATASET`.

```bash
uv run pytest -m "not slow"
# Gravação explícita: exige chave local e consome tokens.
uv run --env-file .env python -m scripts.evaluate_extraction --mode record
# Reprodução: não usa chave nem rede.
uv run python -m scripts.evaluate_extraction --mode replay
uv run pytest -m eval
```

Capturas, gabaritos redigidos, relatório e limiares ficam em
`tests/fixtures/llm-evaluation/`; o piloto comparativo está em
`tests/fixtures/llm-comparison-nano/`. Fixture ausente ou inválida falha
explicitamente. Credencial e mensagens originais não fazem parte das capturas.

Validação final: **453 testes rápidos em 2,22 s**; **eval por replay em 20,96 s**,
sem rede; **464 testes da suíte completa em 25,98 s**. Ruff e mypy limpos
(57 arquivos).
