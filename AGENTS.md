# AGENTS.md

Instruções para ferramentas de IA que trabalham neste repositório.
Leia por completo antes de escrever ou alterar código.

## O que é este projeto

Agente de vendas de seguro auto para a seguradora fictícia **AutoSeguro**. Atende
leads por WhatsApp: conversa, qualifica, cota usando uma API legada instável, e
decide sozinho quando resolver e quando passar para um humano.

Stack: Python 3.12, LangGraph, Postgres, Redis, httpx.

## Invariantes

Estas regras não são preferência de estilo. Quebrar qualquer uma delas invalida a
solução. Se uma tarefa parecer exigir quebrar uma, **pare e pergunte**.

### 1. Nenhum número que o lead vê foi produzido pelo LLM

Prêmio, franquia e valores de pro-rata saem **exclusivamente** do payload da
`POST /quote`, renderizados por template em `src/agent/templates/`.

O LLM decide *quando* cotar. Nunca *quanto* custa.

### 2. `base_mensal` e os multiplicadores nunca saem da API

`GET /planos` devolve o `plans.json` inteiro, incluindo `base_mensal`,
`faixa_etaria`, `idade_veiculo` e `regiao_cep`. Esses campos são **descartados no
parse** e nunca são materializados fora do cliente HTTP.

- `/planos` **não é ferramenta do LLM.** É infraestrutura.
- O fetch é projetado em duas estruturas, em `src/infrastructure/planos/projections.py`:
  - `AcceptanceRules` — limites de aceitação, consumido pelo guard, nunca vai ao contexto
  - `ProductFacts` — nome, coberturas, franquia, carência, pro-rata; pode chegar ao LLM
- Nunca implemente um calculador local de prêmio. Isso criaria uma segunda fonte
  de verdade que divergiria em silêncio.

### 3. Nem toda resposta ruim da `/quote` é uma falha

| Status | Significado | Retentar? |
|---|---|---|
| `200` | cotação | — |
| `422 cotacao_recusada` | a seguradora respondeu, e recusou | **Nunca** |
| `400 payload_invalido` | bug nosso | **Nunca** |
| `5xx`, `408`, `425`, `429`, timeout | falha transitória | Sim |

Tratar `422` como falha faz o agente retentar uma recusa determinística e dizer
"instabilidade" quando a resposta correta era "não aceitamos esse perfil".
Isso é o erro mais grave possível neste projeto.

Recusa é modelada como **resultado**, não exceção: `QuoteOutcome = Quote | Declined`.
Exceção só para infraestrutura (`QuoteUnavailable`) e bug nosso (`QuoteContractError`).

### 4. O CEP é imutável depois de coletado

`cep` é opcional na API, e omiti-lo zera o agravo de região (multiplicador 1.30).
**35,8% dos leads do dataset caem em prefixo agravado** (07, 08, 21, 26, 59).

Uma vez que o CEP entrou no estado da conversa, ele vai em toda chamada. Nunca
recote sem ele.

### 5. Recusa por regra de aceitação não é handoff

Idade fora de 18–75 e veículo com mais de 20 anos somam **30% dos leads**. O
agente recusa com o motivo e encerra. Escalar 30% do tráfego para humano é falhar
no critério de avaliação, não cumpri-lo.

### 6. PII é redigida na entrada

Antes de qualquer coisa tocar log, contexto de LLM ou banco.

- CPF com validação de dígito verificador (regex sozinho gera falso positivo)
- e-mail, telefone, placa, CEP completo
- processador de redação no logger, para não vazar em traceback
- a chave do lead é o **hash do CPF** — nunca `sender_name` (ver armadilhas)

## Arquitetura

Hexagonal. Dependência sempre aponta para dentro.

```
src/
  domain/          entidades e regras puras. ZERO import de framework
  application/     casos de uso e portas (Protocol)
  agent/           LangGraph: grafo, nós, prompts, templates
  infrastructure/  adapters: httpx, sqlalchemy, redis
  interfaces/      webhook FastAPI, CLI, replay do dataset, console Streamlit
```

`domain/` e `application/` não importam `langgraph`, `httpx`, `redis` nem
`sqlalchemy`. O grafo é detalhe de orquestração, não o núcleo.

### Cadeia de cotação

```
Guard → Cache → Retry → Hedge → Trace → Http
```

Cada camada é um decorator que implementa `QuoteProvider`. Adicionar
comportamento = adicionar classe, nunca editar as existentes.

- **Guard** — recusa local sem tocar a rede, usando `AcceptanceRules`. Falha
  aberto: se as regras não carregaram, deixa passar.
- **Cache** — `cotar()` é função pura, então o cache é exato. Chave = os cinco
  slots + dia de referência (a API deriva idade do veículo de `date.today()`).
  TTL até meia-noite. Cacheia `Declined` também. Erro de cache nunca propaga.
- **Retry** — backoff com jitter, teto de tentativas **e** teto de tempo total.
  Captura apenas `QuoteUnavailable`.
- **Hedge** — dispara a segunda chamada após ~1,5s. Existe só para a cauda de
  latência (10% das chamadas dormem 8s). Falha rápida é problema do Retry.
- **Trace** — colado no HTTP, registra cada chamada física em `quote_attempts`,
  inclusive as hedgeadas.

**Não coloque fallback de conversa na cadeia.** Promessa com job assíncrono e
handoff são decisões de conversa e vivem no grafo. Se o `QuoteProvider` passar a
saber mandar mensagem no WhatsApp, a abstração morreu.

### Handoff

Motor determinístico em `src/domain/handoff.py`. Uma classe por regra,
implementando um `Protocol` comum. O LLM pode sugerir handoff como sinal
adicional; quem decide é a política. Grave os dois — a divergência é material de
análise.

### Console Streamlit

`src/interfaces/streamlit_app.py` é o **quarto adapter**, ao lado de webhook, CLI
e replay. Ele consome exatamente os mesmos casos de uso.

Regras:

- **Nunca chame o grafo, a cadeia de cotação ou os repositórios direto do app.**
  Se o console precisar de algo que os casos de uso não expõem, o buraco está na
  camada de aplicação, não no Streamlit.
- **Nenhuma lógica de negócio no arquivo do app.** Ele monta widget, chama caso de
  uso e desenha resultado. Nada mais.
- **`st.session_state` guarda apenas o `thread_id`.** O estado da conversa vive no
  checkpointer do LangGraph. Streamlit reexecuta o script inteiro a cada
  interação; manter estado de conversa em `session_state` cria uma segunda cópia
  que diverge da persistida.
- **Streamlit é síncrono, a cadeia é async.** Use uma ponte única
  (`asyncio.run()` em um helper), nunca `asyncio.run()` espalhado por callback.
- **PII vem redigida por padrão.** Se houver alternância para ver o texto original,
  ela é explícita e registra que foi acionada.

O console tem duas abas:

**Sandbox** — conversa com o agente e um painel de trace ao vivo: slots extraídos
a cada turno, regras de handoff avaliadas, linhas de `quote_attempts` com status,
latência e origem, e qual nível da escada de degradação foi atingido. Controles
para forçar falha (`QUOTE_FAILURE_RATE`) e fixar `QUOTE_SEED`.

**Avaliação** — dispara o golden set de extração e o oráculo negativo das 751
recusas, mostra a tabela de resultados e compara com a baseline humana do
histórico.

A aba de avaliação **não reimplementa** as métricas: chama o mesmo código de
`tests/golden` e `tests/regression`. Métrica que só existe na UI não é
verificável em CI.

## Armadilhas conhecidas

Estas foram medidas no dataset. Não são hipóteses.

### `timestamp` não ordena

Apenas **5 de 2.500** conversas têm timestamp monotônico. Ordenar por `timestamp`
embaralha **67,6%** das mensagens — a resposta do vendedor vem antes da pergunta.

**Ordene sempre por `message_index`.** O timestamp serve só para medir intervalo
aproximado, e precisa de `abs()`.

### `sender_name` não é identidade

336 nomes distintos para 2.500 conversas. "Joao Gomes" aparece em 17 conversas que
são pessoas diferentes (idades e veículos distintos). Os CPFs, esses, são 2.500
distintos sem repetição.

Chavear lead por nome funde até 17 pessoas em um registro só.

### As cotações do histórico estão erradas

O vendedor sintético sorteia plano e preço de forma independente. **0 de 2.500**
cotações batem com a `base_mensal` do plano citado, e as 2.500 usam a mesma frase
de cobertura ("Cobre colisao, roubo e furto") nos três planos.

Nunca use as mensagens do vendedor como few-shot de cotação. Isso ensina o agente
exatamente o comportamento proibido pela invariante 1.

O dataset serve como: fixture de entrada, golden set de extração, e **oráculo
negativo** (as 751 conversas onde o vendedor cotou alguém inelegível).

### O que o dataset não tem

Zero ocorrências de: handoff, atendente, instabilidade, carência, pro-rata, data
de vigência, sinistro, cancelamento. Todos os comportamentos avaliados precisam
ser projetados do zero — não há baseline para imitar.

### Rajadas e mídia

23,1% dos turnos do lead têm 2+ mensagens seguidas, com pico de 6. Daí o debounce.
56,8% das conversas contêm mídia sem transcrição (`[documento] CNH_frente.pdf`,
`[imagem] ...`, `[audio] ...`). Não é caso raro.

### Instabilidade só na `/quote`

O sorteio de falha e o `sleep` estão dentro do handler de `/quote`. `GET /planos`
e `GET /health` respondem sempre. Não implemente resiliência para `/planos` além
de cache.

**Circuit breaker é contraindicado.** A falha é sorteada por chamada, independente
e sem estado — o serviço nunca cai de verdade. Um breaker abriria numa sequência
aleatória e passaria a recusar chamadas com 80% de chance de sucesso.

## Disciplina de teste

Este projeto é escrito com TDD. Teste primeiro, vermelho, verde, refatora.

Mas o TDD só se aplica onde existe resposta binária, e boa parte deste sistema é
determinística justamente por isso. Saber onde a linha passa é parte do trabalho.

### Onde TDD se aplica — escreva o teste antes, sempre

| Alvo | Por que é testável |
|---|---|
| `AcceptanceRules` | função pura sobre regras carregadas |
| Cadeia de decorators | duplos de teste na folha, sem rede |
| Política de handoff | regra é classe pura, entra contexto, sai decisão |
| Templates de apresentação | payload entra, texto sai |
| Redação de PII | texto entra, texto redigido sai |
| Repositórios | contrato verificável contra Postgres de teste |
| Roteamento do grafo | com nós de LLM substituídos por duplos |

Para isso funcionar, três coisas **precisam** ser injetadas por construtor:
`Clock`, a função de `sleep` e o gerador aleatório. Sem isso, testar retry com
backoff custa segundos reais e testar cache exige mexer no relógio da máquina.
Se você encontrar `time.sleep`, `random.random()` ou `date.today()` chamados
direto dentro de `src/`, é bug de testabilidade.

Teste da escada de degradação roda **sem Docker**: a folha da cadeia é um duplo
programado para falhar N vezes.

### Onde TDD não se aplica — use avaliação

Extração de slots e geração de fala dependem de LLM. Não existe vermelho-verde
para prompt.

Isso vira **avaliação com limiar**, não teste binário:

- `tests/golden/` — extração contra os 2.500 casos com ground truth. Falha se a
  acurácia cair abaixo do limiar registrado.
- `tests/regression/` — as 751 conversas inelegíveis. Aqui o critério **é**
  binário: o agente tem que recusar as 751 e nunca emitir preço nelas.

Não trate avaliação como teste unitário nem o contrário. Avaliação tem limiar e
custa dinheiro de API; teste é binário, offline e roda em segundos.

### Pirâmide

```
muitos   unitários de domínio e decorators   ms, sem I/O
alguns   integração (Postgres, Redis, /quote com seed fixo)
poucos   end-to-end via replay do dataset
à parte  avaliação de LLM, rodada sob demanda
```

Teste que depende do sorteio de falha da `/quote` sem `QUOTE_SEED` é flaky por
construção e será rejeitado.

### Ao corrigir um bug

Escreva primeiro o teste que reproduz. Se ele passar de primeira, você não
entendeu o bug ainda.

## Comandos

```bash
# API de cotação (do repo do desafio)
docker compose up --build            # http://localhost:8000

# desenvolvimento
uv sync
uv run pytest                        # unitários + integração, offline
uv run pytest tests/unit -q          # loop de TDD, roda em segundos
uv run pytest --cov=src --cov-report=term-missing
uv run ruff check src tests
uv run mypy src

# avaliação (consome API de LLM, rode sob demanda)
uv run pytest tests/golden -m eval        # extração vs 2.500 casos
uv run pytest tests/regression -m eval    # as 751 recusas

# console de teste e avaliação
uv run streamlit run src/interfaces/streamlit_app.py

# conversa manual
uv run python -m interfaces.cli

# replay de uma conversa do dataset
uv run python -m interfaces.replay --conversation conv_00013
```

### Testes determinísticos

```bash
QUOTE_SEED=42 docker compose up              # falhas reprodutíveis
QUOTE_FAILURE_RATE=1.0 docker compose up     # força a escada até o N4
```

Nunca escreva teste que dependa da sorte do sorteio de falha.

## Convenções

- Python 3.12, type hints em tudo, `from __future__ import annotations`
- `dataclass(frozen=True, slots=True)` para value objects de domínio
- Portas são `Protocol`, não ABC. Pequenas e separadas por papel — prefira
  `ConversationReader` e `ConversationWriter` a um repositório-deus
- `Decimal` para dinheiro, nunca `float`
- Injeção por construtor. A composição acontece em um único ponto de wiring
- Nomes de domínio em português (`Cotacao`, `Recusa`, `carencia`); infraestrutura
  e conceitos técnicos em inglês
- Não escreva interface para algo com uma implementação só e sem intenção de ter
  outra. SOLID aqui é a cadeia de decorators e a lista de regras de handoff, não
  ceremônia

## Rastreabilidade

Toda mensagem e toda cotação precisam de id e status. `quote_attempts` é a tabela
que importa:

```
trace_id, conversation_id, fingerprint, tentativa, status, http_status,
latencia_ms, origem
```

`status` ∈ `quoted | declined | unavailable | contract_error`
`origem` ∈ `api | cache | regra_local`

Uma linha por chamada física.

## Antes de abrir PR

- [ ] O teste foi escrito antes da implementação
- [ ] Nenhum teste novo depende de rede, relógio real ou `sleep` real
- [ ] Nenhum valor monetário é gerado fora de template
- [ ] `base_mensal` e multiplicadores não aparecem em nenhum prompt, log ou contexto
- [ ] `422` e `400` não são retentados
- [ ] Ordenação de mensagem usa `message_index`
- [ ] Nenhuma PII em log, em mensagem de exceção ou na tela do console
- [ ] O console Streamlit não ganhou lógica que os casos de uso não tenham
- [ ] `uv run pytest` e `uv run ruff check` passam
- [ ] Decisão nova e não óbvia está no README, com o porquê