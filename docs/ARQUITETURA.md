# Arquitetura

Documento técnico do sistema. Para as decisões e seus fundamentos, ver o
`README.md`. Para as regras que governam alterações de código, ver `AGENTS.md`.

---

## 1. Princípio organizador

**O preço é sagrado e vem de um único lugar. Tudo em volta é conversa, política e
evidência.**

Operacionalmente: nenhum valor monetário exibido ao lead foi produzido pelo LLM.
Prêmio, franquia e pro-rata vêm do payload da API de cotação e são renderizados
por template determinístico. O modelo decide *quando* cotar; nunca *quanto*.

Toda a estrutura abaixo existe para tornar essa garantia estrutural em vez de
depender de instrução.

---

## 2. Camadas

Hexagonal. Dependência sempre aponta para dentro.

```
src/
  domain/          entidades, value objects, políticas. ZERO import de framework
  application/     casos de uso e portas (Protocol)
  agent/           LangGraph: grafo, nós, prompts, templates
  infrastructure/  quote/, planos/, media/, persistence/, privacy/   (SQLite, httpx)
  interfaces/      webhook, cli, replay, streamlit_app
```

`domain/` e `application/` não importam `langgraph`, `httpx` nem `sqlite3`. O
grafo é detalhe de orquestração, não o núcleo — é isso que permite testar as
políticas sem subir infraestrutura.

Quatro adapters de entrada consomem os mesmos casos de uso:

| Adapter | Papel |
|---|---|
| `webhook` | produção, canal real |
| `cli` | conversa manual no terminal |
| `replay` | reprocessa conversas do dataset; gera o log de execução; roda em CI |
| `streamlit_app` | console de demonstração e avaliação |

Nenhum contém lógica. Se um adapter precisa de algo que os casos de uso não
expõem, o buraco está na camada de aplicação.

---

## 3. Ciclo de vida de um turno

```
webhook/cli/replay
   ↓
InboundMessage (envelope canônico)
   ↓
ingest        dedup → agrupamento de rajada → resolução de mídia → redação de PII
   ↓
extract       LLM, structured output, preenche slots com proveniência
   ↓
policy        código: aceitação e escalação decidem a rota
   ↓
quote         cadeia de decorators contra a API
   ↓
present       template determinístico
   ↓
OutboundMessage → outbox → adapter
```

Desvios: `objection` (LLM, tratamento das seis objeções) e `handoff` (escalação
com snapshot).

### Envelope canônico

Todos os canais traduzem para `InboundMessage` e recebem `OutboundMessage`. As
colunas do dataset correspondem quase exatamente ao envelope, então o replay não é
um caminho especial: a mesma estrutura que chegaria do WhatsApp roda no teste de
regressão.

O envelope carrega **intenção**, não capacidade de canal. "Apresentar cotação" é a
intenção; como isso vira texto puro, painel ou mensagem com botões é problema do
adapter.

`OutboundMessage` é persistido antes de entregue — é a tabela outbox, e resolve
entrega duplicada ou perdida se o processo cair entre gravar e enviar.

---

## 4. Portas principais

```python
class QuoteProvider(Protocol):
    async def quote(self, req: QuoteRequest) -> QuoteOutcome: ...

class MediaResolver(Protocol):
    async def resolve(self, media: MediaRef) -> ResolvedMedia: ...

class HandoffSink(Protocol):
    async def emit(self, decision: HandoffDecision) -> None: ...

class AcceptanceRulesProvider(Protocol):
    async def current(self) -> AcceptanceRules | None: ...

class Clock(Protocol):
    def today(self) -> date: ...
    def now(self) -> datetime: ...
```

`Clock`, a função de `sleep` e o gerador aleatório são injetados por construtor em
todo componente que os usa. Sem isso, testar backoff custa segundos reais e testar
cache exige mexer no relógio da máquina.

---

## 5. Estado da conversa

Cinco slots, que são exatamente o que a API consome:

```python
plano_id     Literal["essencial", "completo", "premium"]
idade        int
veiculo_ano  int                  # ano-modelo
cep          str                  # 8 dígitos, zero à esquerda preservado
data_inicio  date
```

Cada slot carrega **proveniência**: `digitado` ou `transcrito`.

Três regras sobre o estado:

1. **Slot transcrito nunca vai direto para cotação.** Volta como confirmação de um
   turno. Transcrição erra em dígito, e 2018 versus 2008 cruza faixa de
   multiplicador sem que o lead perceba — ele falou certo.
2. **O CEP é imutável depois de coletado.** Uma vez no estado, vai em toda chamada.
   Omiti-lo zera o agravo de região, e 35,8% dos leads estão em faixa agravada.
3. **O CEP é sempre `str`.** Nunca `int`, nunca sem `zfill(8)`. Os prefixos
   agravados incluem "07" e "08"; perder o zero converte em "7" e "8" e anula o
   agravo silenciosamente.

O agente **não coleta CPF**. A API não usa, e coletar PII desnecessária é decisão
ativa de não fazer. O agente **coleta a data de vigência**, que a API usa e que o
processo humano nunca pedia.

---

## 6. A cadeia de cotação

```
ApplicationTrace → Guard → Cache → Retry → Hedge → WireTrace → Http
```

Cada camada implementa `QuoteProvider`. Comportamento novo entra como decorator
novo, nunca como condicional dentro de um existente.

| Camada | Responsabilidade |
|---|---|
| `ApplicationTrace` | registra a solicitação lógica e a origem da resolução |
| `Guard` | recusa determinística sem tocar a rede; falha aberto |
| `Cache` | cotação equivalente já obtida; erro de cache nunca propaga |
| `Retry` | backoff com jitter, teto de tentativas e de tempo |
| `Hedge` | segunda chamada especulativa aos 1,5s |
| `WireTrace` | telemetria física: status, latência, tentativa |
| `Http` | única classe que conhece status code |

**Dois níveis de rastreio, não um.** `WireTrace` sozinho não registra o que o
`Guard` e o `Cache` resolvem — esses pedidos nunca chegam ao HTTP. Como
`quote_attempts.origem` prevê `cache` e `regra_local`, é o `ApplicationTrace`
externo que os registra.

### Números de resiliência

A API falha em 20% das chamadas (5xx imediato) e responde em 8 segundos em outros
10%. A falha é sorteada por chamada, **independente e sem estado**.

Com timeout de 2s, a resposta lenta também é falha do ponto de vista do cliente:

| Cenário | Taxa por tentativa | Residual em 3 tentativas |
|---|---|---|
| sem hedge | 30% | 2,7% |
| com hedge aos 1,5s | 23% | 1,2% |

Por isso **o hedge não é otimização, é componente necessário**: sem ele a falha
residual triplica. Ele é viável porque `/quote` é função pura, sem efeito
colateral — a chamada duplicada não causa dano.

O hedge trata apenas latência. Falha rápida é responsabilidade do `Retry`.

### Cache exato

`cotar()` é determinística sobre os cinco slots mais `date.today()`. O cache não é
aproximação — é o valor exato.

```
chave = sha256(dia_referencia | plano_id | idade | veiculo_ano | cep | data_inicio)
TTL   = até meia-noite
```

O dia entra na chave porque a API deriva a idade do veículo de `date.today()`.
Recusas também são cacheadas, por serem igualmente determinísticas.

### Escada de degradação

| Nível | Ação | Lead percebe |
|---|---|---|
| N0 | chamada direta, timeout de 2s | não |
| N1 | retry com backoff e jitter | não |
| N2 | cache de cotação equivalente | não |
| N3 | escalação com snapshot | sim |

Nunca: preço estimado pelo agente.

N0 a N2 são a cadeia. N3 vive no grafo — se a cadeia entregasse escalação, o
`QuoteProvider` passaria a saber enviar mensagem no WhatsApp.

---

## 7. Taxonomia de erro

| Status | Significado | Retenta |
|---|---|---|
| `200` | cotação | — |
| `422 cotacao_recusada` | a seguradora respondeu, e recusou | Nunca |
| `400 payload_invalido` | erro de contrato nosso | Nunca |
| `5xx`, `408`, `425`, `429`, timeout | falha transitória | Sim |

Modelagem: recusa é **resultado**, não exceção.

```python
QuoteOutcome = Quote | Declined     # a seguradora respondeu
QuoteUnavailable                    # ninguém respondeu — transitório
QuoteContractError                  # nosso payload está errado — permanente
```

Assim o `Retry` captura exatamente uma exceção, e a recusa atravessa a cadeia sem
que nenhuma camada precise conhecê-la.

### Erro de contrato disfarçado de falha

A API captura `(KeyError, ValueError, TypeError)` e devolve `400`. `AttributeError`
não está na lista — CEP enviado como `int` produz **500**, não 400.

Ou seja: um bug nosso se apresenta como falha transitória, o retry tenta três
vezes e o log diz "instabilidade". Mitigação: validação de schema antes do envio,
e classificação de `5xx` cujo corpo não tenha `error: upstream_unavailable` como
suspeito de contrato, marcado diferente no trace.

### Circuit breaker

Não há breaker sobre `/quote`. A falha é independente e sem estado; um breaker
convencional abriria numa sequência aleatória e recusaria chamadas com 80% de
chance de sucesso.

Existe um **breaker bimodal opcional** baseado em `GET /health`, que é estável e
fora do sorteio. Ele só abre em queda total do processo, poupando o orçamento de
tentativas quando não há serviço algum do outro lado.

---

## 8. Políticas

### Aceitação

```python
@dataclass(frozen=True)
class AcceptanceRules:
    idade_min: int          # 18
    idade_max: int          # 75
    veiculo_anos_max: int   # 20
    planos_validos: frozenset[str]
```

Construídas a partir de `GET /planos`, nunca hardcoded. Recusam 30% dos leads do
histórico (11,2% por idade, 21,2% por veículo), antes de qualquer chamada de rede.

**Normalização de ano-modelo futuro.** A idade do veículo é
`ano_atual − ano_modelo`. Modelo do ano seguinte é rotineiro no Brasil a partir do
segundo semestre, e produz idade negativa. Nenhuma faixa cobre valor negativo, e a
API devolve `422`.

Normalizar só no guard criaria divergência: o guard aceitaria e a API recusaria,
depois de uma ida à rede, com um motivo sem sentido para um carro zero-quilômetro.

Por isso a normalização acontece **no payload enviado à API**, não apenas no guard:
`veiculo_ano = min(veiculo_ano, ano_atual)`.

É neutro em preço — a faixa de 0 a 5 anos tem o mesmo multiplicador — então isto
contorna um off-by-one do sistema legado sem alterar valor. Três limites:

- normaliza **um único ano** à frente. Um modelo dois ou mais anos no futuro é
  erro de digitação, e o agente confirma com o lead em vez de ajustar em silêncio.
- **nunca no sentido inverso**: nenhuma normalização reduz a idade real de um
  veículo antigo.
- fica **registrado** em `quote_attempts.ano_normalizado`. Ajustar dado do usuário
  antes de enviar à fonte de verdade precisa aparecer na auditoria.

O dataset não contém nenhum caso — a armadilha só aparece em produção.

### Escalação

Motor determinístico. Uma classe por regra, implementando um `Protocol` comum,
avaliadas a cada turno.

| Gatilho | Frequência esperada |
|---|---|
| Documento recebido | parte dos 56,8% com mídia |
| Mídia não resolvida | — |
| Cotação esgotou o orçamento (N3) | ~1,2% |
| Desconto fora da tabela | ~52% dos leads objetam |
| Pedido explícito de humano | raro |
| Laço de esclarecimento no mesmo slot | — |
| Assunto fora de escopo | — |

**Recusa por regra de aceitação não é escalação.** É resposta final. Escalar 30%
do tráfego seria falhar no critério.

O LLM emite um sinal estruturado (`sugere_escalacao`, `motivo_sugerido`) como
insumo adicional. A política decide. As duas opiniões são gravadas, e a divergência
é métrica.

### Efeitos da escalação

`HandoffDecision` é domínio puro. Os efeitos passam pelo `HandoffSink`, nesta
ordem:

1. **mensagem ao lead** — primeiro, único efeito com prazo humano
2. **webhook ao time de vendas** — com o snapshot
3. **registro via API** — fila de atendimento

A decisão é persistida antes dos efeitos, que passam pela outbox e são retentados
individualmente. Escalação é o caminho onde as coisas já estão dando errado;
efeito parcial não pode deixar o lead avisado e o time sem saber.

O snapshot importa mais que o aviso: slots com proveniência, tentativas de cotação
com status e latência, e o motivo. Sem isso o vendedor recomeça do zero.

---

## 9. Apresentação

O template recebe o payload completo da API. O LLM recebe uma **projeção sem
números**.

```python
# o que o modelo vê como resultado da tool
{"status": "cotado",
 "plano_nome": "Completo",
 "coberturas": [...],
 "carencia_dias": 30,
 "coberturas_com_carencia": ["roubo", "furto"]}
```

`ProductFacts`, disponível ao conversador, contém apenas nomes de plano,
coberturas e a existência da carência. **Sem franquia, sem preço, sem
multiplicador** — franquia e pro-rata são monetários e pertencem ao template.

### A tool `cotar`

Um único parâmetro:

```json
{"name": "cotar",
 "parameters": {"plano_id": {"enum": ["essencial","completo","premium"]}}}
```

Idade, ano, CEP e data vêm do estado, não do modelo. Se o modelo redigitasse o CEP
a cada chamada, poderia omiti-lo ou trocar um dígito — e cada erro vale até 30% no
prêmio. O único campo que é escolha conversacional é o plano.

### Obrigações do template

Duas coisas que a baseline humana erra em 2.500 de 2.500 conversas:

- **carência de 30 dias em roubo e furto**, presente nos três planos
- **pro-rata do primeiro mês**, que só existe quando `data_inicio.day != 1` — a
  ausência do campo não é zero, é "não se aplica"

---

## 10. Pipeline de mídia

`MediaResolver` atua na ingestão, antes do agente. Um adapter por tipo:

| Tipo | Resolução | Resultado |
|---|---|---|
| Áudio | transcrição | vira texto, proveniência `transcrito` |
| Imagem | classificação de visão | anotação de contexto, nunca slot |
| Documento | nenhuma | não resolvido → escala |

Nenhum nó do grafo sabe que transcrição ou visão existem. O gatilho de escalação
não é "recebeu mídia" — é **mídia não resolvida**.

**Áudio** é dado biométrico: transcreve, redige a PII do texto, descarta o
arquivo. A transcrição entra no orçamento de tempo do turno.

**Imagem** devolve dois campos e nada mais:

```python
e_veiculo: bool
confianca: Literal["alta", "baixa"]
```

Não alimenta slot. Marca e modelo não servem a nenhum campo da API, e ano-modelo
não é obtível de forma confiável de uma foto — pedir isso criaria caminho para ano
alucinado entrar na cotação. Confiança baixa é tratada como "não sei", nunca como
"não é carro". A classificação não é bloqueante.

**Documento** nunca é enviado a provedor externo. Não existe adapter que o faça —
é ausência de código, não configuração. O único dado útil que uma CNH forneceria é
a idade, que o lead informa em texto de qualquer forma. O arquivo não é
persistido; registra-se que chegou e o tipo.

---

## 11. Identidade e privacidade

### Chave do lead

A chave primária é a **identidade de canal**: `wa_id` no WhatsApp,
`conversation_id` no replay.

O hash do CPF é atributo opcional, preenchido quando o lead informa
espontaneamente — não chave, porque o agente não pede CPF.

`sender_name` **nunca** é identidade. No histórico, 336 nomes distintos cobrem
2.500 conversas, e um mesmo nome aparece em até 17 conversas de pessoas diferentes.

### Redação de PII

Acontece na ingestão, antes de log, contexto de LLM ou banco.

- CPF com validação de dígito verificador — regex sozinho gera falso positivo
- e-mail, telefone, placa, CEP completo
- processador de redação no logger, para não vazar em traceback
- parser case-insensitive e não posicional: o histórico traz "CPF", "Cpf" e "cpf"
  no mesmo corpus, em ordem embaralhada

O console renderiza texto redigido por padrão.

---

## 12. Persistência

**SQLite é a única dependência de dados.** Não há Postgres nem Redis.

A escolha é deliberada: o sistema roda em processo único, e lock, debounce e
dedup não precisam de um servidor externo para funcionar nessa topologia.
Declarar uma dependência que o código não exercita custa mais credibilidade do
que a dependência agrega.

Ligado no connect: `journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`.
Sem `busy_timeout`, escrita concorrente devolve `database is locked` em vez de
esperar.

### Convenções de tipo

SQLite não tem tipo decimal nem booleano nativo.

| Conceito | Representação |
|---|---|
| identificador | `TEXT`, uuid4 em forma canônica |
| instante | `TEXT`, ISO-8601 em UTC |
| valor monetário | `TEXT`, `Decimal` serializado — nunca `REAL` |
| booleano | `INTEGER` 0 ou 1 |
| documento | `TEXT` com JSON |

`REAL` para dinheiro é erro silencioso: `0.1 + 0.2` não é `0.3`, e um prêmio
arredondado de forma errada é exatamente o tipo de defeito que a invariante do
preço existe para evitar.

### Esquema

```sql
CREATE TABLE leads (
  id              TEXT PRIMARY KEY,
  channel         TEXT NOT NULL,          -- whatsapp | cli | replay
  channel_user_id TEXT NOT NULL,          -- wa_id ou equivalente
  cpf_hash        TEXT,                   -- opcional, nunca chave
  criado_em       TEXT NOT NULL,
  UNIQUE (channel, channel_user_id)
);

CREATE TABLE conversations (
  id            TEXT PRIMARY KEY,
  lead_id       TEXT NOT NULL REFERENCES leads(id),
  status        TEXT NOT NULL,            -- ativa | cotada | recusada | escalada | encerrada
  slots         TEXT NOT NULL DEFAULT '{}',
  iniciada_em   TEXT NOT NULL,
  atualizada_em TEXT NOT NULL
);

CREATE TABLE messages (
  id                   TEXT PRIMARY KEY,
  conversation_id      TEXT NOT NULL REFERENCES conversations(id),
  indice               INTEGER NOT NULL,  -- ordem confiável; timestamp NÃO ordena
  direcao              TEXT NOT NULL,     -- inbound | outbound
  tipo                 TEXT NOT NULL,     -- text | audio | image | document
  corpo                TEXT,              -- já redigido
  media_status         TEXT,              -- resolvido | nao_resolvido | null
  provider_message_id  TEXT,              -- dedup de webhook
  criado_em            TEXT NOT NULL,
  UNIQUE (conversation_id, indice)
);

CREATE UNIQUE INDEX idx_messages_provider
  ON messages(provider_message_id) WHERE provider_message_id IS NOT NULL;

CREATE TABLE quote_attempts (
  id              TEXT PRIMARY KEY,
  trace_id        TEXT NOT NULL,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  fingerprint     TEXT NOT NULL,          -- hash dos 5 slots + dia de referência
  tentativa       INTEGER NOT NULL,
  status          TEXT NOT NULL,          -- quoted | declined | unavailable | contract_error
  origem          TEXT NOT NULL,          -- api | cache | regra_local
  http_status     INTEGER,
  latencia_ms     INTEGER,
  hedge           INTEGER NOT NULL DEFAULT 0,
  ano_normalizado INTEGER NOT NULL DEFAULT 0,  -- ver seção 8
  erro            TEXT,
  criado_em       TEXT NOT NULL
);

CREATE INDEX idx_attempts_trace ON quote_attempts(trace_id);

CREATE TABLE quote_cache (
  fingerprint TEXT PRIMARY KEY,
  outcome     TEXT NOT NULL,              -- JSON de Quote ou Declined
  expira_em   TEXT NOT NULL               -- meia-noite do dia de referência
);

CREATE TABLE handoffs (
  id               TEXT PRIMARY KEY,
  conversation_id  TEXT NOT NULL REFERENCES conversations(id),
  motivo           TEXT NOT NULL,         -- enum da política
  sugerido_por_llm INTEGER NOT NULL,      -- para medir divergência
  snapshot         TEXT NOT NULL,
  criado_em        TEXT NOT NULL
);

CREATE TABLE outbound_messages (          -- outbox
  id              TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  payload         TEXT NOT NULL,
  destino         TEXT NOT NULL,          -- lead | webhook_vendas | api_fila
  status          TEXT NOT NULL,          -- pendente | entregue | falhou
  tentativas      INTEGER NOT NULL DEFAULT 0,
  criado_em       TEXT NOT NULL,
  entregue_em     TEXT
);
```

`messages.indice` existe porque **`timestamp` não ordena**: no dataset, apenas 5
de 2.500 conversas têm timestamp monotônico, e ordenar por ele reposiciona 67,6%
das mensagens.

O esquema é aplicado por um `schema.sql` idempotente no startup. Com sete tabelas
e escopo fechado, alembic seria cerimônia sem retorno — e um comando a mais no
README.

### O que era do Redis

| Necessidade | Onde vive agora |
|---|---|
| lock por conversa | `asyncio.Lock` por `conversation_id`, em processo |
| debounce de rajada | janela em memória no `ingest`; 23,1% dos turnos têm 2+ mensagens |
| dedup de webhook | índice único em `messages.provider_message_id` |
| cache de cotação | tabela `quote_cache` — sobrevive a restart e aparece no console |
| cache de `/planos` | dicionário em memória com TTL |

O cache de cotação em tabela é melhor que em Redis para esta entrega: ele fica
visível no console de trace junto com as tentativas, o que torna o N2 da escada
demonstrável em vez de invisível.

### Checkpointer do LangGraph

`SqliteSaver`, no mesmo arquivo. Confirme se a variante assíncrona existe na
versão fixada — se não existir, a ponte precisa ser explícita e única, nunca
`asyncio.run()` espalhado.

---

## 13. Orçamentos

| Recurso | Limite |
|---|---|
| Turno completo | ~6s, com deadline propagation |
| Chamada individual à `/quote` | 2s |
| Disparo do hedge | 1,5s |
| Tentativas de cotação | 3, dentro do orçamento restante |
| Tokens por conversa | limite configurado; excedê-lo escala |

O orçamento decresce: se a extração consome 3s, restam 3s para a cotação. Esgotado
o orçamento, a execução para de tentar e transita limpa para a rota de
contingência, em vez de deixar o lead esperando.

Transcrição de áudio consome orçamento antes da cotação, então turnos que começam
com áudio têm menos margem.

---

## 14. Configuração de teste

```bash
QUOTE_SEED=42 docker compose up            # falhas reprodutíveis
QUOTE_FAILURE_RATE=1.0 docker compose up   # força a escada até o N3
```

O `docker compose` sobe apenas a API de cotação do desafio. A aplicação não tem
serviço de dados para orquestrar: o SQLite é um arquivo, e os testes usam banco em
memória (`:memory:`) ou arquivo temporário.

A escada inteira é testável **sem Docker**: a folha da cadeia é um duplo
programado para falhar N vezes. O Docker é para o end-to-end.

Nenhum teste depende do sorteio de falha sem seed fixo. E o gerador aleatório da
API é compartilhado entre requisições, então teste concorrente com seed não
produz sequência determinística — use duplos.

---

## 15. Descartado

| Componente | Motivo |
|---|---|
| Vector database / RAG | 6 objeções canônicas são um enum, não um corpus |
| Conhecimento de produto no prompt | já vem no payload da API; cópia envelhece |
| Circuit breaker sobre `/quote` | falha independente; abriria sem motivo |
| Calculador local de prêmio | segunda fonte de verdade divergindo em silêncio |
| Promessa assíncrona (nível entre N2 e N3) | exigiria worker e mensagem proativa |
| Scoring de lead | correlação 0,02 entre preço e desfecho |
| Memória de longo prazo | conversas têm 8 a 14 mensagens |
| Postgres e Redis | processo único; SQLite e primitivas em memória bastam |

---

## 16. Evolução

Mudanças que o desenho comporta sem reestruturação:

- **Interpretação de documento** — exigiria decisão de privacidade diferente, e o
  adapter correto seria CRLV (que traz ano-modelo), não CNH.
- **Nível de promessa assíncrona** — entra como nó no grafo mais worker, sem tocar
  a cadeia.
- **Retrieval** — se o catálogo crescer para dezenas de produtos ou entrar
  regulamentação, `ProductFacts` vira índice. O gatilho é corpus grande e disjunto,
  não número de casos de uso.
- **Multi-agente** — faria sentido com vários ramos de seguro e fluxos realmente
  distintos. Não com cinco slots.