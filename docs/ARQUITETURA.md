# Arquitetura

Documento técnico do sistema. Para as decisões e seus fundamentos, ver o
`README.md`. Para as regras que governam alterações de código, ver `AGENTS.md`.

Estado da Tarefa 9: núcleo determinístico, ingestão, SQLite, cliente OpenRouter e
extrator avaliado em isolamento. Grafo LangGraph com checkpointer assíncrono,
conversador com a tool `cotar`, roteamento de objeção, outbox de escalação e a
composição única `open_sales_stack` estão implementados e medidos fim a fim contra a
API local. Resolução de mídia, webhook de WhatsApp e console permanecem desenho.

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
  infrastructure/  quote/, planos/, llm/, media/, handoff/, persistence/, tracing/,
                   privacy/, wiring.py   (SQLite, httpx)
  interfaces/      cli, replay, trace, rendering, conversation_report
```

`domain/` e `application/` não importam `langgraph`, `httpx` nem `sqlite3`. O
grafo é detalhe de orquestração, não o núcleo — é isso que permite testar as
políticas sem subir infraestrutura.

Os adapters que existem consomem os mesmos casos de uso:

| Adapter | Papel |
|---|---|
| `cli` | conversa no terminal, com `--trace` e retomada por `--conversation` |
| `replay` | envelopes redigidos de conversas do dataset; o harness fim a fim usa os mesmos |
| `trace` | inspeção de uma cotação ou de uma conversa inteira, só leitura |

Webhook de WhatsApp e console Streamlit não existem. Nenhum adapter contém lógica. Se
um precisa de algo que os casos de uso não expõem, o buraco está na camada de aplicação.
A CLI achou dois: o índice da próxima mensagem numa conversa retomada, agora
`Ingestor.next_index`, e a inspeção ligada às conexões vivas, agora `SalesStack.inspector`,
que drena a timeline antes de ler.

---

## 3. Ciclo de vida de um turno

```
cli/replay
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

`OutboundMessage` carrega Intent e payload tipado (ApresentarCotacao, PedirDado,
Declined ou HandoffDecision positiva). Não contém fala pronta do canal. A outbox
persiste essa intenção; seu consumidor e os efeitos de entrega ainda não existem.

`build_ingestor` monta Ingestor, PrivacyRedactor e SQLiteConversations. A entrada
redige corpo, extrai hash opcional do CPF e descarta referência de mídia antes de
persistir. Mídia tem status nao_resolvido. O índice único descarta provider_message_id
repetido. asyncio.Lock protege a entrada por conversa; um worker por conversa
serializa consumo. A janela configurável de silêncio é 500 ms, usando Clock/sleep
injetados. IngestedTurn entrega mensagens ordenadas por índice e contador de
objeções; substitui identidade externa pelo id técnico do lead.

`async with Ingestor` drena no encerramento; wait_idle é a barreira do replay.
Falha do consumidor preserva o turno em memória para nova tentativa explícita
de wait_idle, sem duplicar a contagem. Cancelar a barreira aguarda os workers.
O consumidor deve ser idempotente se produzir efeitos; dedup de entrada não
garante exatamente uma entrega. Uma queda do processo perde a janela em memória;
reprocessamento durável dos turnos fica para a orquestração futura.

Replay lê parquet local, filtra mensagens de vendedor e ordena exclusivamente por
message_index dentro de cada conversa. `interfaces.replay` aceita --conversation
repetido e redige a saída; AUTOSEGURO_DATASET substitui o arquivo local padrão. O harness
em tests/golden recebe somente mensagens redigidas, sem labels, e calcula acurácia
exata por idade e veiculo_texto. A amostra de 48 conversas é estratificada; o
corpus completo de 2.500 casos e a auditoria de CPF são slow. O oráculo encontra
751 recusas com AcceptanceRules e data de referência do próprio dataset (2026).

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

### 4.1. Cliente de linguagem

`application.llm.LLMClient` é a porta assíncrona. `LLMConfig` configura modelos
separados por papel, timeout HTTP e teto por chamada de 7 s — o p99.9 por chamada
(D-038; D-034 usava 4,5 s) — e limite de 16.000 tokens por conversa. O teto efetivo de
cada etapa vem de `TurnConfig`, que passa ao cliente o menor entre o teto da etapa e
o que resta do turno; a chamada que estoura ou volta indisponível é refeita uma vez,
dentro do mesmo prazo. `OpenRouterLLMClient` envia JSON Schema estrito e exige suporte
do provedor (`require_parameters`); não envia `parallel_tool_calls`, que nenhum
endpoint aceita junto com essa exigência (D-033).
Erros não transportam corpo HTTP nem credenciais. `BudgetedLLMClient` compartilha
contagem entre papéis e limita também a espera pelo lock; estouro de tokens gera
sinal determinístico consumido pela regra `OrcamentoTokensEsgotado`.
O contador é em memória por instância; persistência entre processos fica para a
orquestração. O wiring deve compartilhar a instância entre os papéis.

### 4.2. Extrator e avaliação

`SlotExtractor` recebe apenas mensagem atual e `Slots`. O prompt é provisório.
Cada slot é ausente (`None`), informado ou incerto, com proveniência. CEP exige
string e recupera sete dígitos; ano futuro permanece literal. Data informada é
ISO válida. Atualização incerta sem candidato não apaga valor já coletado. Áudio transcrito produz informação incerta até confirmação.

CEP explícito é capturado antes da redação, mantido privado e excluído do request
LLM. Estado já coletado não perde CEP. Em falha, as exceções de extração carregam
`.slots` preservados, sem expô-los na mensagem ou repr; o consumidor deve guardá-los.
A integração dessa captura com o estado do futuro grafo ainda será necessária:
texto já redigido pela ingestão não permite recuperar o CEP original.

`RecordedLLMClient` grava resposta redigida, uso e latência, sem gravar prompt ou
chave. A chave da fixture inclui conversa, posição, modelo, schema, contexto e
configuração. Reprodução é padrão e nunca recorre à rede se faltar captura.
O harness processa rajadas em ordem, sem gabarito no contexto, e para ao obter
idade e ano informados. A auditoria privada de CEP é separada da acurácia LLM.
A avaliação da tarefa 8 (7.298 capturas, idade 88,48%, ano 93,88%) rodou sob o
orçamento de tempo: das 2.500 conversas, 288 foram interrompidas e, nas 2.212
concluídas, ambos os slots estavam corretos — media progresso, não extração. A
avaliação isolada da tarefa 9 (`tests/golden/isolated.py`) roda sem cotação, grafo
nem orçamento de turno: gpt-4.1-mini acerta 99,96% das idades e 100% dos anos. Falhas também têm latência capturada; cancelamento externo não
é transformado em indisponibilidade artificial. Custo sem uso retornado é
desconhecido e estimado separadamente. Nenhuma chave é necessária para replay.

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
| `Hedge` | segunda chamada especulativa aos 100 ms |
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
| com hedge aos 100 ms | 23% | 1,2% |

Por isso **o hedge não é otimização, é componente necessário**: sem corte por deadline, ele reduz a falha
residual teórica de 2,7% para aproximadamente 1,2%. Ele é viável porque `/quote` é função pura, sem efeito
colateral — a chamada duplicada não causa dano.

O hedge trata apenas latência. Falha rápida é responsabilidade do `Retry`.

### Implementação de retry e hedge

`RetryingQuoteProvider` recebe provider interno, limites de tentativas/delays,
orçamento, sleep, RNG callable e Clock. Captura somente QuoteUnavailable;
QuoteContractError atravessa intacto, e Quote/Declined encerram a operação.
A configuração atual usa jitter uniforme 0–20 ms por pausa (base=max=0.02),
sem crescimento exponencial; o mecanismo genérico continua configurável. O deadline absoluto
inclui chamadas em andamento, canceladas e aguardadas quando o orçamento vence.
Não dorme se o próximo delay consumir todo o tempo restante (D-005).

Ao esgotar, a exceção informa `tentativas` lógicas. Se todas falharam, todas as
chamadas físicas foram suspeitas e houve pelo menos três tentativas (limiar
configurável), retorna erro de contrato em vez de indisponibilidade. A promoção
não acontece antes de esgotar o budget ou as tentativas (D-006).

`HedgingQuoteProvider` inicia a segunda chamada apenas se a primeira ainda estiver
pendente ao fim da janela injetada. Uma recusa é resposta válida. Após disparar,
espera a primeira resposta válida; erro de contrato tem prioridade entre conclusões
já disponíveis. Se ambas forem indisponíveis, propaga a última por ordem real
de conclusão, com o resumo `todas_falhas_suspeitas` preservando evidência de ambas.
Não altera a marca física `suspeita_contrato`. Perdedor e timer são sempre
cancelados e aguardados, também em cancelamento externo (D-007).

No wiring, WireTrace fica entre Hedge e Http e ApplicationTrace envolve toda a cadeia.
Cada tentativa lógica do retry pode produzir até duas chamadas físicas.

Portão medido com 10.000 execuções por configuração: **2,72% sem hedge** e
**1,18% com hedge**, respectivamente 13.822 e 14.036 chamadas físicas. O teste usa
seed 42, tempo virtual, sucesso imediato e budget de 20 s para permitir as três
tentativas completas. Não representa a disponibilidade do turno inteiro com
budget de 6 s; esse corte precisa de medição própria na integração.

### Orçamento de produção e calibração

QuoteConfig mantém budget 3,5 s, timeout 2 s, três tentativas; hedge agora 100 ms
com jitter uniforme 0–20 ms por pausa. A medição real em localhost (500 chamadas,
20 warmup, rates de falha/lentidão zero) deu mediana 15,88 ms, p95 29,17 ms,
p99 44,73 ms e máximo 98,96 ms. Janela >2×p99, com margem de 55,27 ms (D-012).

| Configuração | Budget | Antes | Agora |
|---|---|---:|---:|
| sem hedge | sem corte | 2,72% | 2,72% |
| com hedge | sem corte | 1,18% | 1,18% |
| sem hedge | 3,5 s | 3,29% | 3,29% |
| com hedge | 3,5 s | 2,43% | 1,27% |

São 10.000 execuções por cenário, seeds 42/2026, sucesso imediato no duplo.
A diferença restante de 0,09 ponto percentual vem de combinações de lentidão que
sobrevivem ao hedge; uma rodada pode consumir 2,1 s. Sem hedge, dois timeouts já
consomem 4 s. Os 20 s usados como ausência de corte nunca vinculam.
O budget cobre Retry/Hedge/Http; guard e I/O de cache ficam fora dele nesta fase.
O prazo total do turno exige propagação futura pela aplicação.

### Trace implementado

ApplicationTrace → Guard → Cache → Retry → Hedge → WireTrace → Http.
Ambos os traces recebem AttemptRecorder, CorrelationProvider e Clock. O provedor
ContextCorrelationProvider cria uma sessão por cotação usando factory injetada
para ids internos; ContextVars propagam sessão aos filhos asyncio sem misturar
cotações concorrentes. Não usar nome, CPF ou telefone como ids de correlação.
Tentativa zero representa desfecho lógico; 1..N é ordem de início físico. Na
composição sequencial Retry(Hedge), a chamada sobreposta é marcada hedge (D-013).

A folha HTTP observa o status num callback técnico task-local, sem HTTP no domínio.
WireTrace registra duração de cada chamada, ano normalizado, status e classe do
erro. Cancelamento é unavailable/CancelledError, com HTTP ausente se não houve
resposta. Suspeita de contrato é sufixo em erro, nunca conteúdo externo. Instantes
são UTC; normalização continua usando o calendário local da API.

AttemptRecorder.record é uma entrega síncrona não bloqueante. O adapter
BufferedAttemptRecorder limita a fila a 1.024 pendentes, escreve fora do caminho
do retry e registra falha/overflow sem PII. ApplicationTrace chama automaticamente
finish(trace_id) após submeter o desfecho, antes de retornar, inclusive em erro ou
cancelamento. A barreira aguarda até o último evento daquele trace; não requer
flush manual. Cancelar o worker conclui as barreiras pendentes antes de propagar
cancelamento. Crash ainda pode perder pendentes. A latência lógica registrada
exclui a drenagem final. WAL/FULL medido: mediana ~6 ms, p99 até ~30 ms; worker
mantido para não disputar a janela de hedge de 100 ms (D-015, supera D-014).

`python -m interfaces.trace <trace_id> --database arquivo.sqlite` usa
InspectQuoteTrace e leitor SQLite através do wiring. Mostra tentativas por ordem
de início, seguidas do desfecho; ausência de desfecho é explícita. Modo somente
leitura. Pacotes planos de src são instalados por uv sync, incluindo schema.sql.

Com `--conversation <id>`, `InspectConversation` lê a conversa inteira, turno a turno:
estado final de cada turno no checkpointer, timeline, tentativas, mensagem ao lead e
decisão de escalação com o snapshot, costurados pelo `trace_id`. O adapter
`interfaces.conversation_report` formata em markdown; nada é reexecutado e tudo abre
somente leitura. `scripts/execution_log.py` roda uma conversa do dataset e grava essa
saída em `docs/execucao-completa.md` e `docs/execucao-escalacao.md`.

### Cache exato

`cotar()` é determinística sobre os cinco slots mais `date.today()`. O cache não é
aproximação — é o valor exato.

```
chave = sha256(dia_referencia | plano_id | idade | veiculo_ano | cep | data_inicio)
TTL   = até meia-noite
```

O dia entra na chave porque a API deriva a idade do veículo de `date.today()`.
Recusas também são cacheadas, por serem igualmente determinísticas.

O wiring atual monta a cadeia instrumentada descrita acima, com todas as
dependências injetadas. `EligibilityGuardProvider` marca recusas como regra_local,
preserva o request, avalia uma cópia com ano seguinte normalizado e falha aberto
se `current()` retornar None ou lançar exceção. Erro de parse continua explícito
para consumidores diretos do catálogo; no guard é registrado sem payload e deixa
a API decidir. Cancelamento propaga.

`CachingQuoteProvider` captura o calendário de `Clock.now()` uma vez para chave
e meia-noite seguinte. SQLite verifica expiração no get; resposta concluída após
a meia-noite original não é gravada. Cache copia o resultado com origem cache,
sem alterar preço nem a proveniência de normalização histórica. Erros de cache
são registrados só com códigos genéricos, sem request, CEP ou traceback.

### Escada de degradação

| Nível | Ação | Lead percebe |
|---|---|---|
| N0 | chamada direta, timeout de 2 s, com hedge em 100 ms na cauda de latência | não |
| N1 | retry com jitter, até 3 tentativas dentro do orçamento de 3,5 s | não |
| N2 | escalação com snapshot | sim |

Nunca: preço estimado pelo agente.

N0 e N1 são a cadeia. N2 vive no grafo — se a cadeia entregasse escalação, o
`QuoteProvider` passaria a saber enviar mensagem no WhatsApp.

**O cache não é nível da escada** (D-039). Ele fica antes do retry e evita a chamada
quando a mesma cotação — cinco slots e o dia — já foi obtida hoje. Como o preço é
determinístico e o TTL vai até a meia-noite, uma entrada do dia nunca está expirada; e, se
a chamada falhou, não há entrada para servir. Ele é camada preventiva, não reserva depois
da falha.

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

A folha `HttpQuoteProvider` recebe AsyncClient, timeout e Clock. Executa um único
POST, sem seguir redirecionamentos. `200` exige contrato de Quote válido; `422`
retorna Declined com o motivo do corpo ou motivo genérico quando ele não é legível.
Qualquer status fora da taxonomia é QuoteContractError. Erros de transporte são
QuoteUnavailable; cancelamento da tarefa não é convertido em falha transitória.

`QuoteUnavailable.suspeita_contrato` marca somente 5xx sem a identificação
`upstream_unavailable`; isso é uma suspeita, não prova de bug. Continua retentável.
Mensagens das exceções não reproduzem corpo HTTP ou erro de transporte original.

`ano_normalizado` acompanha Quote, Declined e ambas as exceções por chamada,
sem estado compartilhado no provider (D-003). Nenhum status HTTP entra nos objetos
de domínio. WireTrace e ApplicationTrace consomem esses metadados.

### Mesma régua para todo cliente externo

LLM, `/planos`, `/quote` e os sinks de escalação classificam explicitamente: erro de
configuração (400, 401, 402, 403, 404, 405, 413, 422 e 3xx; na cotação, 401/403/404/405
e 3xx, porque 400 é contrato e 422 é recusa), transitório (408, 425, 429 e 5xx) e
contrato. Erro de configuração nunca vira indisponibilidade nem fala de reserva, e
falha alto. O corpo da resposta, redigido e truncado, acompanha toda falha no log e no
trace. `verify_dependencies` faz uma chamada mínima a cada dependência na abertura da
pilha e impede a partida com configuração errada (D-035).

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
@dataclass(frozen=True, slots=True)
class AcceptanceRules:
    planos_validos: frozenset[str]
    faixas_idade: tuple[FaixaAceitacao, ...]
    faixas_veiculo: tuple[FaixaAceitacao, ...]
```

Cada FaixaAceitacao guarda mínimo, máximo e motivo opcional de recusa,
preservando a ordem do catálogo (D-001).

`PlanosClient.get()` entrega as duas projeções do mesmo GET, construídas em
`infrastructure/planos/projections.py`. ProductFacts é definido em domain/product.py
e reexportado pelo módulo de projeções. Por plano contém apenas id,
nome, coberturas e `tem_carencia`, derivado de dias positivos e coberturas aplicáveis.
Não guarda preço, franquia, duração numérica de carência ou o payload original.
`current()` expõe AcceptanceRules para o guard e retorna None se o catálogo
estiver indisponível; erro de contrato continua explícito.

O cache guarda apenas as projeções e usa TTL monotônico desde o parse válido.
No vencimento busca novamente, sem retry nem retorno de dados vencidos (D-004).

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

Na folha implementada, só `ano_atual + 1` muda numa cópia do payload. Anos
passados e dois ou mais anos à frente são enviados intactos; request e fingerprint
originais são preservados. Para dois ou mais anos futuros, cabe ao futuro grafo
tratar a recusa e solicitar confirmação, sem ajustar o dado silenciosamente.

O dataset não contém nenhum caso — a armadilha só aparece em produção.

### Escalação

Motor determinístico. Uma classe por regra, implementando um `Protocol` comum,
avaliadas a cada turno.

| Gatilho | Frequência esperada |
|---|---|
| Documento recebido | parte dos 56,8% com mídia |
| Mídia não resolvida | — |
| Cotação esgotou o orçamento (N2) | ~1,2% |
| Desconto fora da tabela | ~52% dos leads objetam |
| Pedido explícito de humano | raro |
| Laço de esclarecimento no mesmo slot | — |
| Assunto fora de escopo (sinistro, cobrança, cancelamento, renovação, outro ramo) | categoria do conversador com piso lexical (D-039) |

**Recusa por regra de aceitação não é escalação.** É resposta final. Escalar 30%
do tráfego seria falhar no critério.

O conversador emite, no mesmo schema strict, `escalacao` (motivo sugerido ou nulo) e
`assunto`. A política decide. Em todo turno em que o conversador fala, o evento `decisao`
da timeline grava as duas opiniões — a decisão da política em `status`, a sugestão do
modelo em `sugestao` —, inclusive quando ninguém escala; a divergência é métrica (D-039).
`assunto` liga a regra "fora de escopo"; abaixo dele, um piso lexical reconhece sinistro,
cobrança, cancelamento, renovação e outro ramo antes de a política pedir dado.

Implementado em domain/handoff.py: HandoffPolicy avalia a lista de regras na
ordem da tabela e para na primeira que dispara. LacoEsclarecimento tem limiar
configurável, padrão três tentativas sem avanço no mesmo slot; o estado futuro
deve zerar a contagem quando houver progresso. Objeção de preço não equivale a
pedido explícito de desconto. Além desse sinal, três turnos com objeção de preço
acionam DescontoForaTabela independentemente do modelo; limite configurável.
Ingestor aplica detector lexical ao conjunto dos fragmentos textuais e persiste
a contagem por conversa. Uma rajada conta no máximo uma objeção. Outros
classificadores dos sinais conversacionais ainda não existem.

ConversationContext reúne os cinco slots e proveniência digitado/transcrito,
mídia/resolução, resultado final da cadeia e sinais explícitos. A política retorna
HandoffDecision também quando não escala: preserva sugestao_llm e divergencia.
Silêncio do modelo contra decisão positiva diverge; motivos positivos diferentes
também divergem. Sugestão isolada nunca escala. QuoteContractError e Declined
não acionam a regra de cotação esgotada.

Snapshot copia slots de forma imutável e redige o CEP nessa cópia. Reutiliza os
registros QuoteAttempt existentes por protocolo estrutural de leitura, sem
segunda representação das tentativas e sem importar application no domínio.
A proveniência é preservada; o pedido original continua com seu CEP para cotar.
O grafo abre `turn_correlation` ao cotar, então as tentativas herdam o trace_id do
turno; o snapshot lê `quote_attempts` da conversa inteira (D-032).

Objeção é classificada pelo conversador no campo estruturado `objecao` (seis
categorias ou `nenhuma`); se o modelo não classificar, o piso lexical
`objecao_lexical` reconhece as frases conhecidas. O roteamento acontece depois da
fala: tool `cotar`, depois objeção, depois fim. O nó responde com texto fixo por
categoria e grava a fonte na timeline (D-031).

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

O renderer puro implementado em agent/templates recebe payload (ou Quote validado)
e ProductFacts. Usa nome do catálogo e valores, coberturas, carência e pro-rata da
cotação. Decimal é formatado sem float nem arredondamento implícito; frações de
centavo significativas e coberturas desconhecidas causam QuoteContractError.
Existem 12 goldens da implementação original da API: três planos, CEP normal ou
agravado, com ou sem pro-rata. Mensagens de recusa, indisponibilidade e transição
são determinísticas e têm redação explicitamente provisória.

## 10. Pipeline de mídia

`MediaResolver` atua na ingestão, antes do agente, e não bloqueia: falha ou timeout
deixam a mensagem sem resolução e o turno segue. `LLMMediaResolver` usa
`google/gemini-2.5-flash` com schema strict (D-037).

| Tipo | Resolução | Resultado | Escala? |
|---|---|---|---|
| Áudio | transcrição | vira texto, proveniência `transcrito` (exige confirmação) | só o 2º sem transcrição |
| Imagem | classificação de visão | nota na resposta, nunca slot | nunca |
| Documento | nenhuma — nunca sai do processo | não resolvido | sempre |

Nenhum nó do grafo sabe que transcrição ou visão existem: recebem só a
`MediaResolution` da mensagem. Imagem de veículo com confiança alta é reconhecida;
qualquer outro caso vira nota neutra e o agente segue pedindo o dado por texto. Áudio
sem transcrição pede o dado por escrito e escala no segundo. O prompt pede apenas os
cinco campos, por texto, e nunca documento, foto ou CPF.

O dataset só traz marcadores (`[imagem] ...`), sem arquivo: no replay só os ramos sem
resolução são exercitados. Os ramos resolvidos são exercitados de verdade pelas quatro
fixtures de `tests/fixtures/media` via `scripts/probe_media.py`.

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
espontaneamente — não chave, porque o agente não pede CPF. A chave lógica é
(channel, channel_user_id). SQLite armazena SHA256 do identificador do canal e
aplica a mesma transformação na consulta, preservando identidade entre conversas
sem guardar wa_id/telefone em claro. É pseudonimização, não anonimização. Um CPF
já conhecido não é substituído silenciosamente. O futuro adapter de entrega
precisará resolver o endereço do canal em uma fronteira protegida: o hash não
permite recuperar o destinatário e esta fase não entrega mensagens.

`sender_name` **nunca** é identidade. No histórico, 336 nomes distintos cobrem
2.500 conversas, e um mesmo nome aparece em até 17 conversas de pessoas diferentes.

### Redação de PII

Acontece na ingestão, antes de log, contexto de LLM ou banco.

- CPF com validação de dígito verificador — regex sozinho gera falso positivo
- e-mail, telefone, placa, CEP completo
- processador de redação no logger, para não vazar em traceback
- parser case-insensitive e não posicional: o histórico traz "CPF", "Cpf" e "cpf"
  no mesmo corpus, em ordem embaralhada

PrivacyRedactor valida os dois dígitos de CPF e rejeita sequências repetidas.
Números nus de onze dígitos com CPF inválido não viram telefone sem contexto
telefônico explícito; formatos telefônicos reconhecíveis são redigidos.
RedactingFormatter atua após formatação, incluindo args, traceback e stack.
O wiring envolve handlers existentes do logger raiz; handlers adicionados depois
ou em loggers sem propagação devem instalar o mesmo formatter.

A CLI e a inspeção de trace exibem só texto redigido.

### Slots operacionais e retenção

Slot é dado operacional; mensagem é log. A redação protege o histórico — mensagens,
logs, contexto de LLM e trace. O CEP, único slot que a redação alcança, é guardado em
`conversations.slots` para cumprir a função de negócio: vai em toda cotação e sobrevive
a reinício. Primeiro valor imutável; os demais slots ficam no checkpointer (D-036).

Retenção: encerrar a conversa purga `conversations.slots` e apaga o estado do grafo.
A recusa final encerra automaticamente. Conversa aberta sem mensagem do lead há mais de
24 h é encerrada pelo mesmo caminho, na partida e no início de cada turno, sem agendador;
lead que volta reabre a conversa (D-038). Enquanto a conversa está aberta, o CEP fica em
claro no SQLite. Cifrar em repouso foi descartado por decisão: processo único, dado de
vida curta e chave no mesmo disco. A purga é o controle.

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

### Persistência implementada após a Tarefa 7

`infrastructure/persistence/schema.sql` contém as sete tabelas abaixo, índices
de dedup e trace. Cache guarda fingerprint, outcome JSON e expira_em em TEXT.
quote_attempts.conversation_id tem FK para conversations. O startup migra a
tabela antiga em transação, preservando traces órfãos por conversas encerradas
e leads técnicos identificados com canal legacy. Reaplicar é idempotente.
Tentativa zero é resolução lógica. `connect` aplica schema e três pragmas. WAL é verificado em arquivo temporário;
SQLite em memória usa journal_mode=memory por limitação do próprio SQLite.
Decimais são strings no JSON e instantes são ISO-8601 em UTC. Não há request
ou CEP persistido. Datas ingênuas de SystemClock são interpretadas no fuso local,
que deve ser o mesmo da API; relógios conscientes preservam seu fuso na meia-noite.

`SQLiteQuoteCache` usa uma conexão dedicada, worker threads e lock para serializar
operações sem bloquear o event loop. Cancelamento aguarda o worker antes de
propagar para permitir fechamento seguro; uma escrita já iniciada pode completar.
O busy_timeout pode acrescentar até 5 s por disputa de lock, fora do budget do
retry, além da espera por operações enfileiradas. Antes de prometer 6 s por turno, a aplicação precisa limitar também catálogo
e cache. Shutdown deve aguardar todos os consumidores antes de fechar a conexão.

SQLiteConversations implementa portas separadas de leitura/escrita de conversas,
mensagens e leitura de lead; identidade é validada antes de associar conversa.
SQLiteDelivery grava/lê intenções de saída e decisões com snapshot; não entrega
outbox nem dispara efeitos. Campos textuais são redigidos antes da gravação;
Decimal continua string. Snapshots persistíveis exigem os registros QuoteAttempt
existentes; implementações desconhecidas do protocolo são rejeitadas antes de
gravar, evitando dados que não podem ser relidos. Divergência e sugestão do modelo
ficam no JSON de handoffs.snapshot, inclusive quando a decisão é negativa.

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
  atualizada_em TEXT NOT NULL,
  objecoes_preco INTEGER NOT NULL DEFAULT 0
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

O esquema evoluirá no `schema.sql` idempotente aplicado no startup. Com sete tabelas
e escopo fechado, alembic seria cerimônia sem retorno — e um comando a mais no
README.

### O que era do Redis

| Necessidade | Onde vive agora |
|---|---|
| lock por conversa | `asyncio.Lock` por `conversation_id`, em processo |
| debounce de rajada | janela em memória no `ingest`; 23,1% dos turnos têm 2+ mensagens |
| dedup de webhook | índice único em `messages.provider_message_id` |
| cache de cotação | tabela `quote_cache` — sobrevive a restart e aparece na inspeção de trace |
| cache de `/planos` | dicionário em memória com TTL |

O cache de cotação em tabela é melhor que em Redis para esta entrega: ele fica
visível na inspeção de trace junto com as tentativas, com `origem=cache` na chamada que
ele evitou.

### Checkpointer do LangGraph

`AsyncSqliteSaver` sobre aiosqlite, no mesmo arquivo e em conexão dedicada
(`open_checkpointer`), sem ponte síncrona. Como ele segura transação entre awaits,
nenhuma outra escrita SQLite pode acontecer no event loop: a timeline grava em
thread, encadeada (D-033).

---

## 13. Orçamentos

| Recurso | Limite |
|---|---|
| Turno completo | 18 s, com deadline propagation: soma do caminho no p99.9 (era 10 s) |
| Extração | até 7 s por tentativa, um retry (p99.9 isolado 6,90 s) |
| Fala do conversador | até 7 s por tentativa, um retry |
| Cotação (cadeia) | até 3,5 s |
| Chamada individual à `/quote` | 2 s |
| Chamada individual ao LLM | 7 s |
| Disparo do hedge | 100 ms, calibrado no p99 local |
| Tentativas de cotação | 3, dentro do orçamento restante |
| Tokens por conversa | 16.000 (máximo medido 9.705); excedê-lo escala |

Valores de `TurnConfig` e `LLMConfig` (D-034, recalibrados em D-038). Medidos na mesma
amostra de 150 conversas: turno p50 1,58 s, p99 5,05 s, máximo 8,25 s. O teto é o pior
caso; a espera típica não mudou.

O orçamento decresce: se a extração consome 3s, restam 3s para a cotação. Esgotado
o orçamento, a execução para de tentar e transita limpa para a rota de
contingência, em vez de deixar o lead esperando.

Transcrição de áudio consome orçamento antes da cotação, então turnos que começam
com áudio têm menos margem.

---

## 14. Configuração de teste

```bash
QUOTE_SEED=42 docker compose up            # falhas reprodutíveis
QUOTE_FAILURE_RATE=1.0 docker compose up   # força a escada até a escalação (N2)
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
| Promessa assíncrona (nível entre N1 e N2) | exigiria worker e mensagem proativa |
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