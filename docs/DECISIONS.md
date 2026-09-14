# Decisões de implementação

## D-001 — Preservar faixas de aceitação no domínio
**Data:** 2026-09-11
**Contexto:** resumir o catálogo a idade mínima/máxima e idade máxima do veículo
funciona para o fixture atual, mas perde lacunas e recusas intermediárias.
**Alternativas:** guardar somente limites globais; guardar faixas de aceitação.
**Decisão:** projetar cada faixa apenas em mínimo, máximo e motivo opcional de
recusa, preservando a ordem do catálogo. Reter planos válidos em `frozenset`.
**Consequência:** uma faixa suspensa continua sendo recusada sem alterar código;
nenhum dado de precificação ou referência ao payload fica retido no objeto.
Nesta tarefa o construtor puro `from_api` fica no domínio conforme solicitado;
o futuro cliente de planos deverá chamá-lo na fronteira de parse.

## D-002 — Serialização canônica do fingerprint
**Data:** 2026-09-11
**Contexto:** SHA-256 e participação dos cinco slots mais o dia já estavam
decididos; faltava definir a representação inequívoca da entrada.
**Alternativas:** concatenar campos com delimitadores; JSON de objeto ordenado;
JSON de sequência fixa.
**Decisão:** JSON compacto ASCII na ordem dia, plano, idade, ano do veículo,
CEP e início, com datas ISO e `null` para opcionais ausentes; então SHA-256.
Plano é normalizado para minúsculas como na API, e CEP antes da serialização.
**Consequência:** não há ambiguidade de delimitadores ou dependência do hash
aleatório do Python. Um vetor fixo protege a compatibilidade entre execuções;
mudar esse formato exigirá invalidar o cache futuro.

## D-003 — Metadados de normalização por resultado e por exceção
**Data:** 2026-09-11
**Contexto:** o futuro trace precisa saber se o ano foi ajustado também quando
a chamada recusa ou falha. Um campo mutável no provider mistura chamadas concorrentes.
**Alternativas:** retorno auxiliar que altera QuoteProvider; callback de observação;
metadado nos resultados e exceções existentes.
**Decisão:** `ano_normalizado` booleano, opcional por palavra-chave e falso por padrão,
em Quote, Declined, QuoteUnavailable e QuoteContractError. Nos resultados imutáveis,
não participa da igualdade. `suspeita_contrato` fica em QuoteUnavailable, sem
alterar sua classificação transitória. O provider calcula ambos por chamada e
não mantém estado de última tentativa. A requisição e seu fingerprint não mudam.
**Consequência:** QuoteProvider continua retornando Quote | Declined, e o trace
pode ler o metadado mesmo em falhas. Há um custo de carregar proveniência técnica
nos resultados de domínio. O futuro cache deve distinguir essa proveniência da
tentativa atual: um resultado cacheado não representa nova normalização física.

## D-004 — Cache de catálogo com TTL monotônico após parse válido
**Data:** 2026-09-11
**Contexto:** as duas projeções precisam corresponder à mesma leitura do catálogo,
sem guardar o payload bruto nem depender de ajustes no relógio de calendário.
**Alternativas:** caches separados; expiração por datetime; devolver dados vencidos
quando a atualização falhar.
**Decisão:** PlanosClient guarda um único agregado imutável de AcceptanceRules e
ProductFacts. O TTL configurável começa após fetch e parse bem-sucedidos, usando
Clock.monotonic; no instante do vencimento já há nova leitura. TTL zero desativa
reuso. Erros não são cacheados, não há retry ou fallback para dados expirados.
`get()` distingue indisponibilidade (PlanosUnavailable) de contrato inválido
(QuoteContractError). `current()` traduz somente indisponibilidade para None,
permitindo o guard falhar aberto conforme previsto.
**Consequência:** catálogo vencido exige um novo GET; consultas concorrentes durante
uma expiração podem fazer leituras duplicadas. Não há coordenação de fetch nesta fase.

## D-005 — Full jitter e deadline absoluto do retry
**Data:** 2026-09-11
**Contexto:** teto de tentativas não limita espera total; verificar o tempo só entre
chamadas permite ultrapassar o orçamento enquanto uma chamada está em andamento.
**Alternativas:** limitar apenas sleeps; usar timer de sistema não injetado;
disputar a execução com um deadline através do sleep injetado.
**Decisão:** full jitter uniforme entre zero e o teto exponencial limitado por
`max_delay`, com RNG callable injetado. Deadline calculado na entrada por
Clock.monotonic, inclusive antes de qualquer trabalho síncrono da folha. Um timer
injetado cancela e aguarda a operação em andamento ao vencer o orçamento. Delay
igual ou maior que o tempo restante não é iniciado. Resultado já concluído tem
preferência se resultado e timer estiverem prontos no mesmo despertar.
**Consequência:** não depende de temporização real nos testes; requer que a folha
coopere com cancelamento. `tentativas` conta invocações lógicas do provider interno,
incluindo uma chamada cancelada por deadline, não chamadas físicas hedgeadas.

## D-006 — Promoção conservadora de suspeita de contrato
**Data:** 2026-09-11
**Contexto:** uma falha sem corpo esperado pode vir de proxy; promovê-la isoladamente
ocultaria indisponibilidade real. O hedge pode terminar com uma falha suspeita depois
de já observar uma não suspeita, e perder essa evidência altera indevidamente o destino.
**Alternativas:** promover na primeira suspeita; nunca promover; usar limiar de
falhas esgotadas preservando evidência de todas as chamadas.
**Decisão:** limiar padrão de três tentativas lógicas, configurável a partir de dois.
Só promove no esgotamento, quando todas as tentativas terminaram em falha suspeita.
Sucesso ou Declined encerra imediatamente, mesmo após atingir o limiar de suspeitas.
O hedge propaga a mesma última QuoteUnavailable, preserva `suspeita_contrato`
física e anota `todas_falhas_suspeitas` como conjunção das evidências. Retry usa
esse resumo, não apenas a marca da última chamada física.
**Consequência:** não promove um conjunto misto de falhas, nem uma chamada pendente
cancelada pelo budget. Três falhas independentes a 20% têm probabilidade de 0,8%,
mas isso não é probabilidade posterior de bug: proxies podem falhar correlacionados.
O limiar é heurístico e precisa de observação em produção, não prova de contrato.

## D-007 — Conclusões concorrentes e medição com tempo virtual
**Data:** 2026-09-11
**Contexto:** FIRST_COMPLETED devolve um conjunto sem ordem. Escolher um elemento
arbitrário pode ocultar erro de contrato ou propagar a falha física errada.
**Alternativas:** escolher arbitrariamente; observar ordem e dar prioridade a erros
de contrato; esperar sempre ambas, prejudicando a latência.
**Decisão:** o hedge registra a ordem de conclusão por chamada. Quote e Declined
são respostas válidas; erros de contrato/programação prontos no mesmo despertar
têm precedência. A primeira resposta válida vence e os demais tasks/timer são
cancelados e aguardados, inclusive em cancelamento externo. Falha rápida não gera hedge.
**Consequência:** testa empates e cancelamento sem espera real. O suporte de teste
usa event loop virtual compatível com CPython 3.12 e acessa suas filas internas;
uma migração de Python/event loop requer revisar esse suporte.

Para o portão estatístico são 10.000 execuções por configuração, seed 42 para
a folha e 2026 para jitter, tolerância de 0,5 ponto percentual. A folha sorteia
20% de falha imediata, 10% de latência de 8 s truncada pelo timeout de 2 s e 70%
de sucesso imediato. Janela de hedge 1,5 s, três tentativas e orçamento de 20 s
isolam a taxa residual do corte por deadline. Orçamento real menor e latências
de sucesso não desprezíveis podem produzir taxas diferentes. A medição exercita
os decorators reais; o tempo gasto nas 20.000 execuções é processamento, não sleep.

## D-008 — Budget de produção e baseline com deadline vinculante
**Data:** 2026-09-11
**Contexto:** os 20 s da Tarefa 3 nunca cortam tentativas; não representam a fatia
de cotação de um turno de aproximadamente 6 s.
**Alternativas:** manter números sem deadline como estimativa de produção;
reduzir timeout ou aumentar tentativas para perseguir 1,2%; medir com 3,5 s.
**Decisão:** padrão configurável `PRODUCTION_QUOTE_BUDGET = 3.5`, consumido pelo
wiring via QuoteConfig e pelos testes estatísticos. Com 10.000 execuções por
cenário, seeds 42/2026 e timeout 2 s, medimos: sem corte, 2,72% sem hedge e 1,18%
com hedge; com 3,5 s, 3,29% sem hedge e 2,43% com hedge. A baseline de produção
é empírica, com tolerância de 0,5 ponto percentual, não a fórmula independente
de três tentativas completas. Sem corte usa 20 s; máximo possível é 10,8 s.
**Consequência:** aumento com hedge de 1,25 ponto percentual é relevante e justifica
revisitar timeout/janela com distribuição de latência dos sucessos reais. Mantemos
2 s e três tentativas: mais tentativas não devolvem o tempo já consumido, e reduzir
timeout num duplo de sucesso imediato favoreceria artificialmente a medição.
Guard/cache não entram no budget do retry; deadline do turno inteiro ainda exige
propagação pela aplicação, incluindo catálogo e contenção SQLite.

## D-009 — Conexão dedicada e cancelamento seguro no cache SQLite
**Data:** 2026-09-11
**Contexto:** busy_timeout pode bloquear por 5 s. SQLite síncrono dentro de async
bloquearia o loop, inclusive timers de retry e hedge de outras conversas.
**Alternativas:** sqlite3 no loop; dependência aiosqlite; worker threads da stdlib
com conexão dedicada e serialização por lock.
**Decisão:** worker threads com lock por instância, uma instância por conexão;
startup síncrono configura conexão e schema. Operações aguardam o worker protegido
por shield mesmo se o consumidor cancelar, então propagam CancelledError. O dono
aguarda seus consumidores e fecha a conexão; uma escrita já iniciada pode concluir.
**Consequência:** não bloqueia o event loop, mas cancelamento pode aguardar o
busy_timeout e a fila de operações no lock; 5 s não é teto global de encerramento.
O teste com authorizer SQLite e eventos reproduziu fechamento durante
escrita após cancelamento; a implementação agora drena o worker antes de retornar.
Em memória, WAL não existe; o teste verifica memory nesse modo e wal em arquivo.

## D-010 — Origem imutável e validade até a meia-noite original
**Data:** 2026-09-11
**Contexto:** cache deve distinguir resultado histórico de nova chamada física,
e uma resposta pode atravessar a meia-noite durante o retry.
**Alternativas:** wrapper de resultado; estado mutável no provider; metadados no
resultado. Para expiração, calcular na entrada ou renovar na escrita.
**Decisão:** origem em Quote/Declined, keyword-only, fora da igualdade, padrão api.
Guard e cache devolvem cópias; ano_normalizado histórico permanece no hit e não
representa nova normalização física. Chave e vencimento usam a mesma leitura de
Clock.now; após a meia-noite original não escreve, nem serve hit atrasado. SQLite
salva instantes UTC e Decimal como strings no JSON, sem request nem CEP.
**Consequência:** o futuro trace deve combinar origem e normalização. O calendário
local precisa acompanhar a API; datas ingênuas usam o fuso local do processo.

## D-011 — Falha aberta também para catálogo malformado no guard
**Data:** 2026-09-11
**Contexto:** PlanosClient.current distingue indisponibilidade de erro de contrato
(D-004). Um guard estrito diante do segundo erro impediria uma cotação que a API
ainda poderia responder corretamente.
**Alternativas:** propagar erros de contrato do catálogo; falhar aberto em todo
erro de carregamento, mantendo observabilidade sem conteúdo externo.
**Decisão:** o guard captura Exception somente ao carregar regras, registra
rules_unavailable em nível ERROR sem mensagem externa/traceback e chama o interno.
None também deixa passar. CancelledError não é capturado. Consumidores diretos
do catálogo continuam vendo erro de contrato; erros da cotação não são engolidos.
**Consequência:** guard permanece otimização. Logs identificam falha da dependência
sem PII; diagnóstico detalhado seguro fica para a instrumentação futura.

## D-012 — Hedge calibrado e jitter constante para falhas independentes
**Data:** 2026-09-11
**Contexto:** D-008 mostrou 2,43% de falha com budget de 3,5 s contra 1,18% sem
corte. A API sorteia falhas independentes; não há recuperação por esperar mais.
**Alternativas:** manter janela 1,5 s; encurtar timeout; calibrar hedge no caminho
rápido e eliminar o crescimento do backoff.
**Decisão:** 500 POSTs sequenciais, mais 20 warmup, cliente httpx keep-alive no WSL,
API real em container separado na porta 18000, FAILURE_RATE=SLOW_RATE=0: mediana
15,8802 ms, p95 29,1736 ms, p99 44,7299 ms, máximo 98,9636 ms. Percentis nearest rank;
amostras em docs/measurements/task5-fast-path.json. Janela 100 ms (>2×p99) e jitter
uniforme 0–20 ms por pausa, sem crescimento (base_delay=max_delay=0.02). Timeout
permanece 2 s e máximo três tentativas. Isso supera a configuração de D-008.
**Consequência:** esperando mais não aumenta a chance de sucesso, apenas consome
budget. O retry genérico mantém compatibilidade exponencial para configurações
explícitas, mas ela foi descartada na política padrão de produção. A janela é
calibração local, não garantia de cauda sob carga ou em outro ambiente.

Reexecução dos oito cenários, 10.000 amostras cada, seeds 42/2026: antes/depois,
sem hedge e sem corte 2,72%/2,72%; com hedge e sem corte 1,18%/1,18%; sem hedge
com 3,5 s 3,29%/3,29%; com hedge com 3,5 s 2,43%/1,27%. Restam 0,09 ponto
percentual: pares de chamadas lentas/falhas ainda podem consumir 2,1 s por rodada.
Sem hedge, dois timeouts somam 4 s. O duplo continua com sucesso instantâneo
para comparar a configuração, não prever performance da cadeia completa.

## D-013 — Correlação por contexto, sequência física e desfecho explícito
**Data:** 2026-09-11
**Contexto:** WireTrace não observa guard/cache; status HTTP pertence à folha,
e estado de última resposta no provider misturaria chamadas concorrentes.
**Alternativas:** adicionar HTTP nos resultados de domínio; hooks globais do
cliente; callback técnico com observação por contexto. Para níveis de trace,
coluna adicional ou reservar um valor na sequência existente.
**Decisão:** ContextCorrelationProvider recebe factory de ids internos, cria sessão
por resolução e observação distinta por chamada via ContextVars. Tentativa zero
é desfecho lógico; físicos são 1..N por início. Na topologia sequencial Retry(Hedge),
sobreposição física identifica hedge. A folha tem callback opcional de status,
cuja falha é best effort; nenhum status HTTP entra no domínio. Cancelamento gera
unavailable/CancelledError, HTTP ausente quando não houve resposta; suspeita de
contrato é sufixo da classe em erro. Não registra motivo, mensagem externa ou payload.
**Consequência:** consumidores não devem contar tentativa zero como chamada HTTP;
trocar topologia exige rever detecção de hedge. conversation_id ainda não tem FK,
pois conversations não existe. A inspeção ordena físicos e apresenta desfecho ao
final. Ano de normalização vem do calendário local, não do instante convertido em UTC.

## D-014 — Entrega não bloqueante e fila limitada para instrumentação
**Data:** 2026-09-11
**Contexto:** revisão reproduziu recusa em 10 ms virando QuoteUnavailable com duas
chamadas quando o recorder levava 4 s. Aguardar SQLite no WireTrace consumia o
orçamento e disparava hedge por latência da instrumentação, não da API.
**Alternativas:** aguardar gravação; timeout curto de escrita; tarefas ilimitadas;
fila limitada com drenagem explícita pelo dono dos recursos.
**Decisão:** AttemptRecorder.record passa a ser entrega síncrona não bloqueante;
BufferedAttemptRecorder recebe sink assíncrono, mantém até 1.024 eventos pendentes
mais uma escrita ativa e serializa a persistência em background. Ambos os traces
só submetem eventos. Overflow e falhas de escrita são avisos genéricos, sem PII.
SQLiteAttempts usa conexão própria; flush aguarda o worker, mesmo ao cancelar,
antes da inspeção/fechamento. A conexão do cache continua distinta, no mesmo arquivo.
**Consequência:** cotação não espera persistência de trace. Pode haver perda de
eventos em overflow ou crash, e leituras anteriores ao flush podem ser parciais.
O consumidor deve aguardar todos os produtores, drenar e só então fechar conexões.
Isso é best effort explícito, sem fila durável ou outras tabelas nesta fase.


## D-015 — Worker medido e drenagem automática por cotação
**Data:** 2026-09-11
**Contexto:** a tarefa 6 pediu medir insert síncrono antes de manter a fila de D-014.
500 inserts/commits reais, após 20 warmups, WAL/FULL: no /tmp mediana 6,398 ms,
p95 8,448 ms, p99 9,481 ms, máximo 16,909 ms; no workspace mediana 6,071 ms,
p95 10,201 ms, p99 29,969 ms, máximo 53,187 ms. Amostras e configuração em
measurements/task6-trace-{native,workspace}.json; script measure_trace_write.py.
**Alternativas:** insert direto; manter flush explícito; worker com barreira lógica.
**Decisão:** manter worker, pois o custo não é fração de milissegundo e a cauda
compete com o hedge de 100 ms. Supera a drenagem manual de D-014: ApplicationTrace
aguarda AttemptRecorder.finish(trace_id) automaticamente após o desfecho, inclusive
em erro. A barreira acompanha o último evento daquele trace, sem esperar eventos
posteriores de outras cotações. Cancelamento do consumidor aguarda a entrega;
cancelamento do worker liquida também as barreiras enfileiradas antes de propagar.
**Consequência:** não há flush manual para quem chama a cadeia. O retorno inclui
custo de drenagem (e fila anterior), fora dos 3,5 s de Retry/Hedge; latencia_ms
lógica mede até o resultado, antes da drenagem. Sob contenção esse custo ainda
precisa entrar no deadline do turno futuro. Falhas, overflow e cancelamentos de
escrita são registrados genericamente; crash ainda pode perder eventos pendentes.

## D-016 — Apresentação exata e catálogo puro
**Data:** 2026-09-11
**Contexto:** template precisa do nome do plano sem depender de infraestrutura,
e dinheiro não pode sofrer arredondamento silencioso no contexto Decimal.
**Alternativas:** importar projeção de infraestrutura; duplicar DTO; arredondar
para centavos; rejeitar valores fora do contrato de apresentação.
**Decisão:** ProductFacts passa ao domínio, reexportado na projeção existente.
Renderer usa Quote validado e catálogo, formata Decimal sem float e rejeita
fração significativa de centavo, moeda divergente e cobertura desconhecida.
Coberturas e carência vêm da cotação; não inferimos valores pelo plano. Goldens
são saídas da implementação original da API executada offline, sem copiar fórmula.
**Consequência:** novos códigos de cobertura exigem tradução explícita antes de
chegar ao lead. Textos de recusa/indisponibilidade/transição são provisórios,
mas a presença de valores e condições é verificada por 12 goldens versionados.

## D-017 — Decisão negativa auditável e snapshot sem duplicar tentativas
**Data:** 2026-09-11
**Contexto:** retornar None quando a política não escala perderia a divergência
quando somente o LLM sugere escalação. Ainda não existe estado de grafo concreto.
**Alternativas:** callback de auditoria; resultado apenas positivo; DTO duplicado
de tentativa; contexto mínimo puro e decisão explícita nos dois caminhos.
**Decisão:** regras retornam decisão ou None; a política sempre retorna decisão
com escalar, motivo, sugestao_llm e divergencia. Primeiro gatilho vence na ordem
especificada; laço usa limiar configurável padrão três, equilibrando esclarecimento
e repetição. Contexto usa cinco slots e proveniência já definidos na arquitetura.
Snapshot positivo mantém os mesmos QuoteAttempt por protocolo estrutural somente
leitura e copia os slots em mapa imutável, redigindo CEP sem alterar o pedido.
**Consequência:** silêncio do modelo frente à escalação e motivos discordantes
contam como divergência. Integração futura deve fornecer sinais explícitos,
reiniciar contagem ao avançar e persistir também decisões negativas. A política
não tenta detectar intenções em texto nem executa efeitos de escalação.


## D-018 — Piso de desconto contado por turno
**Data:** 2026-09-11
**Contexto:** pedido explícito de desconto pode vir da interpretação do modelo;
essa não pode ser a única entrada capaz de disparar a política.
**Alternativas:** escalar na primeira objeção; esperar cinco; usar somente o sinal
estruturado do extrator; contar cada fragmento como objeção independente.
**Decisão:** DescontoForaTabela tem limiar configurável padrão três. Ingestor
reconhece expressões lexicais de preço alto/caro/fora do orçamento no texto unido
do turno, independentemente de LLM. Negações simples de caro são excluídas.
Cada turno soma no máximo um; duplicatas não somam. Contagem vive na conversa.
**Consequência:** três evita escalar a primeira objeção comum e limita insistência;
a regra não pretende compreender ironia ou toda paráfrase. Sinal explícito continua
válido antes do piso. Reexecução do mesmo turno após falha não incrementa novamente.

## D-019 — Identidade de canal pseudonimizada e CPF apenas como atributo
**Data:** 2026-09-11
**Contexto:** a tarefa 7 e a seção 11 superam a antiga chave CPF do AGENTS.
wa_id é também telefone; persistir identidade literal conflita com redação de PII.
**Alternativas:** guardar wa_id em claro; criptografar endereço desde já;
chave estável derivada da identidade sem armazenar endereço de entrega nesta fase.
**Decisão:** a chave lógica é (channel, channel_user_id). O repositório transforma
o segundo componente em SHA256 na escrita e consulta; UNIQUE usa esse par.
O consumidor recebe id técnico do lead. CPF válido informado espontaneamente
gera SHA256 dos onze dígitos; não é chave e não substitui valor já conhecido
silenciosamente. Nome não participa da identidade. AGENTS foi corrigido.
**Consequência:** hash é pseudonimização, não anonimização nem criptografia de
endereços. A entrega futura precisará resolver destino numa fronteira protegida;
esta outbox ainda não envia mensagens e não recupera wa_id a partir do hash.
CPF inválido nu não é convertido em telefone sem contexto; testes sintéticos
cobrem essa ambiguidade. Auditoria dos 2.500 spans CPF do dataset: todos válidos,
zero falso positivo/negativo frente aos rótulos independentes do gerador.

## D-020 — Rajada por silêncio, consumo serial e cancelamento após aceite
**Data:** 2026-09-11
**Contexto:** fragmentos não devem produzir respostas concorrentes. Dedup durável
não pode transformar cancelamento entre commit e fila em mensagem sem consumo.
**Alternativas:** janela fixa desde primeiro fragmento; debounce por silêncio;
segurar o mesmo lock durante entrada e todo consumo; fila durável de turnos.
**Decisão:** janela de silêncio configurável de 500 ms, com Clock/sleep injetados.
Lock por conversa protege entrada/estado; worker único serializa consumo e libera
o lock enquanto chama o consumidor. Aceite (persistir e enfileirar) é protegido
contra cancelamento do chamador. async with drena ao sair; wait_idle também
serve ao replay e aguarda workers ao ser cancelado. Falha do consumidor preserva
o turno pronto em memória, com o contador já calculado, para repetir wait_idle.
**Consequência:** outras conversas avançam independentemente. Erros não são
engolidos. Consumidor deve ser idempotente se produzir efeitos; o fluxo não
promete exactly-once. Crash perde agrupamentos em memória apesar de preservar
mensagens; recuperação de turnos em processo novo fica para orquestração futura.
CEP é redigido no texto; antes do futuro grafo será necessário extrair seu slot
privado na fronteira de entrada, mantendo-o fora do prompt/log. Esta fase só
prepara avaliação de idade e veículo; não extrai slots reais de cotação.

## D-021 — Evolução SQLite sem apagar evidências anteriores
**Data:** 2026-09-11
**Contexto:** traces da tarefa 5 não tinham FK e podem existir sem conversa.
Adicionar a restrição sem backfill perderia registros ou impediria startup.
**Alternativas:** descartar traces antigos; recusar arquivos antigos;
reconstruir tabela e criar identidade técnica explicitamente legada.
**Decisão:** startup transacional recria quote_attempts com FK e preserva linhas;
conversas órfãs ficam encerradas, com leads técnicos no canal legacy. Índice de
trace é recriado e reaplicar é idempotente. Sete tabelas agora existem.
Outbox armazena intenção/payload tipado e handoffs guarda também opinião/divergência.
Campos textuais de snapshots e recusas são redigidos na serialização, sem alterar
Decimal. Snapshot persistido aceita registros QuoteAttempt reais da tabela;
outras implementações do protocolo são rejeitadas antes de escrever.
**Consequência:** migração não inventa dados pessoais de leads históricos. O
protocolo de leitura do domínio permanece amplo, mas o adapter exige uma forma
persistível conhecida. Entrega, retry da outbox e efeitos continuam fora do escopo.

## D-022 — Avaliação sem vazamento de gabarito
**Data:** 2026-09-11
**Contexto:** o harness deve ser utilizável antes do extrator e não pode produzir
acurácia artificial usando o próprio gabarito como entrada.
**Alternativas:** fake oracular retornando labels; regex experimental; placeholder
que retorna campos ausentes e métricas testadas com exemplos independentes.
**Decisão:** Extractor recebe somente tupla de mensagens redigidas do lead. Labels
ficam em Case fora do argumento de extração. NullExtractor retorna ausência:
0% em idade e veiculo_texto é esperado e não mede qualidade de linguagem.
48 casos versionados são os dois menores IDs por estrato de aceitação/recusa,
mídia, presença de CEP e desfecho. Corpus completo local é slow. Referência do
oráculo é a data da mensagem índice zero (2026), nunca relógio de execução.
**Consequência:** mesma métrica serve à amostra e aos 2.500 casos; foram encontradas
751 recusas. Ausência do parquet local causa skip explícito dos testes completos.
O oráculo ainda não prova comportamento ponta a ponta de agente sem preço; isso
virá quando o grafo existir. Dados brutos permanecem fora do git.


## D-023 — Importação de testes medida no workspace montado
**Data:** 2026-09-11
**Contexto:** suíte rápida de 381 testes levou 8,36 s em /mnt/c. Perfil da coleta:
2.763 chamadas stat consumiram 4,564 s de 6,759 s instrumentados; o custo é
majoritariamente acesso ao sistema de arquivos, não espera das regras/ingestão.
**Alternativas:** manter modo prepend; remover testes do loop; usar importlib.
**Decisão:** pytest usa --import-mode=importlib. Coleta isolada caiu de 4,34 s
para 3,61 s; os mesmos 381 testes passaram em 7,79 s após a alteração. Dez casos
slow permanecem separados (oito estatísticos, corpus e auditoria CPF).
**Consequência:** ganho local modesto, sem retirar cobertura ou desabilitar
assertions. O tempo continua dominado pelo workspace montado; não prometemos
que a mudança transforma este ambiente em um loop de dois segundos.


## D-024 — Workspace Linux e avaliação real como portão pendente
**Data:** 2026-09-11
**Contexto:** pequenas operações em /mnt/c dominavam a coleta do pytest.
**Alternativas:** otimizar testes; manter montagem; migrar workspace.
**Decisão:** cópia ativa em ~/namastex-test-tecnico, preservando a
original como backup. Os mesmos 381 testes passaram em 1,61 s, contra 7,79 s.
Cliente e extrator usam duplos no loop rápido; o portão eval exige capturas reais.
**Consequência:** credencial OpenRouter ausente impede obter acurácia, custo e
limiares. Nenhuma resposta sintética será publicada como avaliação medida.

## D-025 — CEP privado e slots incertos fora da inferência monetária
**Data:** 2026-09-11
**Contexto:** a extração precisa conservar CEP, mas PII não pode entrar no LLM.
**Alternativas:** enviar CEP redigido e perder o slot; enviar PII ao modelo;
capturar CEP determinísticamente antes da redação.
**Decisão:** captura privada de CEP explícito, imutável após coletado; schema
rejeita inteiro e recupera sete dígitos. Prompt recebe só mensagem redigida e
slots públicos. Incerto é estado explícito, separado de ausência. Ano futuro não
é corrigido. Em falha, exceção de extração preserva slots para o consumidor.
**Consequência:** captura precisa ser conectada à fronteira de ingestão no futuro
grafo; não se tenta reconstruir CEP de texto já redigido. Auditoria de CEP mede
captura privada, não acurácia do modelo. Prompt de extração permanece provisório.

## D-026 — Capturas por chamada e limite de tokens compartilhado
**Data:** 2026-09-11
**Contexto:** repetição do golden set não pode gastar tokens nem mudar respostas.
**Alternativas:** cache só por prompt; capturas por posição e configuração;
consultar modelo em todo teste.
**Decisão:** capturas imutáveis por conversa/posição/modelo/schema/contexto/config;
replay não tem fallback. Uso e custo retornados pelo provedor são preservados,
custo ausente fica desconhecido. Orçamento cobre lock e chamada; 4.000 tokens por
conversa é limite inicial compartilhado entre papéis. Exceder dispara regra
independente de sugestão do LLM. Timeout 2 s e orçamento 2,5 s reservam tempo para
cotação dentro do turno. Candidato do extrator: openai/gpt-4.1-mini; conversador
configurado separadamente como openai/gpt-4.1, ainda sem implementação.
**Consequência:** modelo, latência e limite precisam ser validados na execução
real. Contadores são locais à instância, não duráveis. Capturas incluem somente
resposta redigida e metadados, nunca chave ou prompt em claro.
**Referências:** [Structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs),
[usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting).


## D-027 — Captura de timeout sem confundir cancelamento externo
**Data:** 2026-09-11
**Contexto:** o piloto real respondeu corretamente os oito casos, mas seu p95 de
2,41 s ficou próximo do orçamento de 2,5 s. Teste determinístico demonstrou que
o timeout externo cancelava o gravador antes de persistir a falha, impedindo replay.
**Alternativas:** aumentar orçamento para mascarar o caso; gravar todo cancelamento
como falha; compartilhar a identificação do deadline que expirou.
**Decisão:** o budget publica seu deadline no contexto da chamada. O gravador
converte apenas cancelamento por esse deadline em indisponibilidade gravada.
Cancelamento externo continua interrupção e não cria uma falha artificial.
Falhas carregam latência medida pelo Clock, preservada na reprodução e incluída
no p95. Corpo de erro e credencial continuam fora da exceção e da captura.
**Consequência:** métricas incluem as chamadas lentas interrompidas, sem viés de
medir apenas sucesso. Cobrança sem resposta permanece desconhecida; eventual
estimativa é identificada separadamente do custo observado.


## D-028 — Incerto sem candidato não apaga dado já coletado
**Data:** 2026-09-11
**Contexto:** conv_00748 informa Renault Duster 2003; ao extrair idade no turno
seguinte, o modelo devolveu ano incerto com valor null. O merge apagava 2003.
**Alternativas:** qualquer atualização substitui; marcar o valor anterior incerto;
preservar informação até receber um candidato novo.
**Decisão:** ausência de candidato não apaga valor anterior, inclusive quando
status é incerto. Um candidato incerto diferente continua representável para
confirmação. A regressão reproduziu o caso antes da correção.
**Consequência:** replay das mesmas capturas subiu ano de 93,84% para 93,88%,
sem modificar resposta do modelo nem alterar custo ou latência medidos.

## D-029 — Mini mantido, pisos de regressão separados de qualidade de produção
**Data:** 2026-09-11
**Contexto:** 2.500 casos reais: 88,48% em idade e 93,88% em ano após corrigir o
merge. Das conversas, 288 falharam por indisponibilidade/prazo; nas 2.212 restantes,
ambos os slots ficaram corretos. Mediana 1.575,94 ms, p95 2.346,20 ms.
**Alternativas:** aumentar orçamento; trocar modelo; manter limites e registrar
qualidade efetiva sem ocultar indisponibilidade.
**Decisão:** manter openai/gpt-4.1-mini e orçamento 2,5 s, timeout 2 s. Piloto nano
em 24 casos teve duas interrupções e um erro de idade; não justificou a troca.
Pisos inteiros imediatamente abaixo do medido: idade 88%, ano 93%. Capturas reais
são versionadas; replay é padrão sem rede. Valores originais anteriores ao ajuste
de merge também são preservados.
**Consequência:** pisos detectam regressão, não aprovam produção. A perda de 11,52%
por indisponibilidade/prazo exige calibração futura. Custo observado US$ 2,6678412
em 7.010 respostas com uso; estimativa US$ 2,777447 nas 7.298 chamadas pela média
das respostas conhecidas. Custos desconhecidos não foram tratados como zero.
Comparação nano adicionou US$ 0,0068511 conhecidos, separadamente.

## D-030 — Filtro direcional no conversador e guardrail observável
**Data:** 2026-09-11
**Contexto:** o conversador aplicava à entrada o mesmo filtro da saída. O histórico
do grafo contém só fala do lead, então o filtro não protegia nada e apagava objeções:
no dataset, 437 de 16.470 mensagens do lead (2,65%, uma por conversa em 437) —
todas "a franquia ta alta" e "o preco ta salgado", duas das seis objeções canônicas.
Na saída, qualquer dígito levantava LLMContractError e derrubava o turno, bloqueando
"carência de 30 dias" e "assistência 24h". `plano_id` aceitava texto livre, e um erro
de digitação do modelo virava `Declined("Plano inexistente")` para lead elegível.
**Alternativas:** filtro simétrico; classificar papel de cada mensagem do histórico;
filtro só na saída, com a regra de não comentar valor citado pelo lead no prompt.
**Decisão:** direcional. Entrada passa intacta após redação de PII; não confirmar
valor proposto pelo lead é regra de prompt. Saída mira padrão monetário: `R$`,
reais/centavos, decimal de duas casas, número (dígito ou extenso a partir de dez)
colado a termo de valor (custa, paga, mensalidade, prêmio, franquia, preço, parcela,
sai/fica por) ou seguido de "por mês"/"mensais". Violação troca a fala por
`render_safe_reply`, grava `turn_events` etapa `guardrail`/`violacao` com o texto
redigido na coluna `erro` e segue o turno. A tool `cotar` declara enum fechado dos
ProductFacts; plano fora dele é LLMContractError, que o grafo converte em resposta
de template e evento `converse`/`contrato_llm`, sem tocar a cadeia de cotação.
**Consequência:** o filtro é rede de proteção; a garantia continua sendo o modelo
não receber `base_mensal` nem multiplicadores. Afirmações qualitativas ("o custo é
baixo") deixam de ser bloqueadas, pois a invariante é valor, não vocabulário — isso
supera os casos equivalentes de e83ba25. Falso positivo cai no template, lado
seguro. Contagens um a nove por extenso não são detectadas perto de termo de valor;
preço real nessa faixa não existe no catálogo.

## D-031 — Objeção classificada pelo modelo, com piso lexical
**Data:** 2026-09-11
**Contexto:** o grafo roteava objeção por `price_objection` ("caro") antes do
conversador. Das 1.295 conversas do dataset com objeção, 220 (17,0%) chegavam ao nó;
"a franquia ta alta" e "o preco ta salgado", as mais frequentes, nunca chegavam.
**Alternativas:** ampliar só o léxico; chamada LLM dedicada à classificação; campo
estruturado na saída que o conversador já produz, com o léxico como piso.
**Decisão:** `objecao` obrigatório no schema strict do conversador — uma das seis
categorias ou `nenhuma`. O roteamento acontece depois do conversador: tool `cotar`,
depois objeção, depois fim. Se o modelo devolve `nenhuma`, `objecao_lexical` reconhece
as frases conhecidas; a frase principal vence o sufixo "… me ofereceu menos". O nó
mantém um texto fixo por categoria. A timeline grava a etapa `objecao` com a fonte
(`modelo` ou `lexico`) e a categoria, para medir divergência.
**Consequência:** o piso sozinho roteia 1.295/1.295, com 0 falso positivo em 13.386
outras mensagens de lead. Ele foi escrito sobre as mesmas 36 frases do gerador, então
100% é por construção; paráfrases novas dependem do modelo. O contador de objeções de
preço da ingestão (D-018) continua lexical e separado. Num turno em que ainda falta
dado, o pedido do dado vem antes da objeção.

## D-032 — trace_id do turno propagado à cadeia; snapshot da conversa inteira
**Data:** 2026-09-11
**Contexto:** não havia composição de produção. O grafo lia tentativas pelo trace_id
do turno, mas a cadeia criava correlação própria por cotação; fora dos testes o
snapshot da escalação saía sem tentativas.
**Alternativas:** a cadeia devolver o trace_id; parâmetro extra em `QuoteProvider`;
escopo do turno numa ContextVar lida pela fábrica de correlação.
**Decisão:** `turn_correlation(trace_id, conversation_id)` na aplicação; o grafo abre o
escopo ao cotar. `open_sales_stack` monta o `ContextCorrelationProvider` com fábrica que
lê o turno corrente e falha se não houver. O snapshot lê `quote_attempts` da conversa
inteira (`read_conversation`), não só do turno que escalou.
**Consequência:** `QuoteProvider` não muda. Cotação fora de turno falha explicitamente
na composição de produção. Teste com SQLite e cadeia reais: escalação por cotação
esgotada leva ao snapshot exatamente as linhas da tabela, com o trace_id do handoff.

## D-033 — Timeline fora do event loop; tool sem parallel_tool_calls
**Data:** 2026-09-11
**Contexto:** o primeiro piloto real mostrou extração com p50 de 6.085 ms (1.482 ms na
avaliação isolada) e `policy`/fala com p95 perto de 5.060 ms. `SQLiteTurnEvents` gravava
de forma síncrona no event loop; com o checkpointer segurando transação entre awaits,
o loop travava até o busy_timeout de 5 s e o evento se perdia. O mesmo piloto mostrou
que o conversador nunca tinha funcionado contra o provedor: OpenRouter respondia 404
"No endpoints found" em todas as chamadas. Uma matriz de seis variantes isolou a causa:
`parallel_tool_calls: false` com `require_parameters: true` não tem endpoint.
**Alternativas:** para a timeline, fila limitada como a das tentativas ou escrita em
thread encadeada; para a tool, remover `require_parameters` ou `parallel_tool_calls`.
**Decisão:** timeline grava em thread, encadeada para preservar a ordem, com `drain()`
antes de ler e ao fechar a pilha. Sai `parallel_tool_calls`; fica `require_parameters`,
que garante o schema strict. Mais de uma chamada continua erro de contrato, e o prompt
pede uma chamada por turno: sem essa regra o modelo chamava `cotar` três vezes para
"quanto fica o seguro?", abertura de 658 conversas do dataset.
**Consequência:** o 404 era convertido em LLMContractError sem registro do corpo; o
defeito só apareceu com chamada real. Nenhum teste offline teria pego os dois casos.

## D-034 — Orçamento do turno recalibrado com medição fim a fim
**Data:** 2026-09-11
**Contexto:** a tarefa 8 fixou turno de 6 s, LLM com timeout de 2 s e teto de 2,5 s
por chamada, e 4.000 tokens por conversa, por analogia e antes de existir medição de
LLM. Com D-033 corrigido, 150 conversas do dataset (amostra aleatória, seed 2026)
rodaram duas vezes pelo agente real: extrator gpt-4.1-mini e conversador gpt-4.1 via
OpenRouter, cadeia real contra a API local com QUOTE_SEED=42, 20% de falha e 10% de
lentidão. O lead do dataset nunca informa data de vigência; o harness a responde
quando o agente pede e, se a conversa acaba sem cotação, pede o Completo.
**Alternativas:** manter 6 s e trocar o extrator pelo nano; ampliar só o turno total;
dimensionar cada etapa pelo p99 medido sem corte (piloto de 30 conversas).
**Decisão:** turno de 10 s; extração até 3,5 s (p99 sem corte 3,07 s; isolado
3,26 s); fala até 4,5 s (p99 4,38 s); cotação até 3,5 s; LLM com timeout e teto de
4,5 s; 16.000 tokens por conversa (máximo medido 9.510). O nano fica rejeitado:
mediana 1.465 ms contra 1.482 ms do mini e 10 pontos a menos em idade — a latência é
a ida ao provedor, não a inferência.
**Consequência:** mesmo código, só a configuração muda. Antes: 0/150 cotadas — 27
escalações por tokens (o conversador estourava 4.000 já na primeira fala) e 19 por
LLM cortado em 2–2,5 s; o prazo aparecia como corte de chamada, não como prazo do
turno. Depois: 38/150 cotadas (25,3%), 38 das 42 elegíveis sem mídia (90,5%). Mídia
sem resolução (63) domina as interrupções e pertence a outra fase. Turno depois:
p50 1,68 s, p95 4,42 s, p99 5,54 s, máximo 6,78 s, nenhum acima de 8 s. Custos
conhecidos US$ 0,25 e US$ 0,50. As rodadas compartilham a instância da API: o sorteio
é independente por chamada, mas as sequências de falha diferem. Amostra de 150 dá
margem de cerca de ±7 pontos percentuais na taxa de conclusão.

## D-035 — Disciplina de erro em todo cliente externo e verificação de partida
**Data:** 2026-09-11
**Contexto:** o conversador recebeu 404 do OpenRouter em todas as chamadas e o cliente
converteu em "erro de contrato" descartando o corpo (D-033). A auditoria dos demais
clientes achou irmãos do mesmo defeito: `HttpQuoteProvider` classificava por status mas
descartava o corpo e punha 401/404 na mesma classe que o 400 de payload; `PlanosClient`
transformava qualquer erro HTTP, inclusive 401/404, em indisponibilidade, e o guard
falhava aberto em silêncio; os sinks de escalação convertiam tudo em
`HandoffDeliveryError()` sem status nem corpo, e a outbox retentaria para sempre gravando
só o nome da classe.
**Alternativas:** classificação por cliente, sem vocabulário comum; categoria única
"falha externa"; taxonomia comum com erro de configuração separado.
**Decisão:** `ConfigurationError` (aplicação) para credencial, rota, modelo ou parâmetro
rejeitado (400, 401, 402, 403, 404, 405, 413, 422 e 3xx), com subclasses por cliente;
transitório é 408, 425, 429 e 5xx; o resto é contrato. `infrastructure/http_errors`
descreve toda falha como `HTTP <status>: <corpo>`, com PII redigida e 500 caracteres, e
esse detalhe vai para o log e para o trace (`quote_attempts.erro`, `turn_events`, erro
da outbox). A cotação mantém sua taxonomia (400 contrato, 422 recusa) e ganha
`QuoteConfigurationError` como subclasse de contrato, sem retry. O guard não engole
erro de configuração. `verify_dependencies` roda na abertura da pilha com uma chamada
mínima à API de cotação, ao extrator, ao conversador (com as mesmas tools e schema de
produção) e ao modelo de mídia; configuração errada impede a partida, falha passageira
só é registrada. Supera D-013 no ponto "não registrar mensagem externa".
**Consequência:** o 404 de D-033 teria parado a partida no primeiro segundo. O corpo
só chega redigido e truncado; o traceback continua sem ele. Erro de configuração no
meio de um turno derruba o turno em vez de virar fala de reserva; na mídia, que é não
bloqueante por especificação, vira log em ERROR e o turno segue.

## D-036 — CEP durável em conversations.slots, purgado no encerramento
**Data:** 2026-09-11
**Contexto:** o CEP privado vivia em memória; após reinício o grafo recusava cotar.
Fail-safe correto, mas não desenho.
**Alternativas:** manter em memória; hash (impede cotar); cifrar com chave gerenciada;
persistir como slot operacional com política de retenção.
**Decisão:** slot é dado operacional, mensagem é log. O CEP vai para
`conversations.slots` (JSON), primeiro valor imutável, e as mensagens seguem redigidas.
Os demais slots de qualificação continuam no checkpointer, sem segunda cópia. Retenção:
encerrar a conversa (`SalesSession.close`) purga `conversations.slots` para `{}`, marca
`encerrada` e apaga o estado do grafo (`adelete_thread`). A recusa final encerra
automaticamente, como a invariante 5 já pedia. Estreita a invariante 8 do AGENTS.md
para esse caso, por decisão do responsável pela tarefa.
**Consequência:** a cotação sobrevive a reinício com o CEP. O CEP fica em claro no
SQLite enquanto a conversa está aberta; cifragem em repouso fica como evolução.
Encerramento por inatividade depende de um agendador que ainda não existe.

## D-037 — Mídia: imagem nunca escala, áudio pede texto, documento escala
**Data:** 2026-09-11
**Contexto:** na medição da tarefa 9, 63 de 105 conversas elegíveis escalaram por mídia
antes de cotar; parte disso contrariava a seção 10 da arquitetura (imagem não alimenta
slot). Numa chamada real, o conversador pediu "o documento do veículo".
**Alternativas:** escalar toda mídia; resolver tudo por LLM; resolver só imagem e áudio,
sem nunca enviar documento.
**Decisão:** `MediaResolver` na ingestão, não bloqueante. Imagem é classificada
(`e_veiculo`, `confianca`) e nunca escala: veículo com confiança alta é reconhecido,
qualquer outro caso vira nota neutra e o agente segue pedindo o dado por texto, sem
acusar. Áudio transcrito vira texto com proveniência `transcrito`, que exige confirmação;
áudio sem transcrição pede texto e só escala no segundo (limiar configurável). Documento
sempre escala e não tem adaptador. O prompt pede só os cinco campos, por texto. Modelo
`google/gemini-2.5-flash`: único aceito com imagem e áudio sob schema strict e
`require_parameters` na sondagem (`gemini-2.0-flash-001` voltou 404 e
`gpt-4o-audio-preview`, 400).
**Consequência:** o dataset só tem marcadores de mídia, sem arquivo; no replay a
resolução nunca acontece e imagem e áudio seguem os ramos sem resolução. Os ramos
resolvidos são exercitados de verdade só pelas quatro fixtures em `tests/fixtures/media`.
Na fixture real, a transcrição trocou "Ônix" por "anix" — é por isso que slot transcrito
exige confirmação.

## D-038 — Teto de LLM por conversa (p99.9 com um retry) e purga por inatividade
**Data:** 2026-09-11
**Contexto:** cortes de LLM eram 9 das 11 interrupções não previstas da tarefa 10, 8 delas
entre as 74 elegíveis sem documento (11%) e todas na extração. Os tetos estavam logo acima
do p99 **por chamada** (3,5 s na extração, 4,5 s na fala); com cerca de dez chamadas por
conversa, isso é ~10% **por conversa**. Nas 7.499 chamadas isoladas do extrator: p99
3,25 s, p99.9 6,90 s, máximo 14,6 s. O conversador tem 321 chamadas fim a fim, censuradas
em 4,5 s — pouco para um p99.9 —, mas a mesma mediana e a mesma causa de cauda (a ida ao
provedor, D-034). Em paralelo, o lead que abandona a conversa nunca a encerra, e o CEP de
D-036 ficaria em `conversations.slots` para sempre.
**Alternativas:** para o teto, só retry com teto no p99, só teto no p99.9, ou hedge da
chamada de LLM; para a retenção, agendador, cifragem do CEP em repouso ou nada.
**Decisão:** teto de 7 s por chamada (p99.9 do extrator, aplicado também à fala) em
`TurnConfig` e em `LLMConfig`. A chamada que estoura o teto ou volta indisponível é refeita
uma vez: geração não tem efeito colateral, e a tool `cotar` só executa depois, no grafo.
Configuração, contrato e estouro de tokens não são retentados. Cada tentativa usa o menor
entre o teto e o que resta do turno; o turno passa de 10 s para 18 s, a soma do caminho no
p99.9 (7 + 7 + 3,5). Retenção: purga oportunista, sem agendador — na partida e no início de
cada turno, conversa aberta sem mensagem do lead há mais de 24 h (a janela de atendimento
do WhatsApp) é encerrada pelo mesmo caminho do encerramento: slots, estado do grafo e
status. Lead que volta reabre a conversa; sem isso, a reaberta sairia da purga. Falha da
purga no turno é registrada e não custa a resposta. **Cifrar o CEP em repouso não é feito,
por decisão, não por omissão**: SQLite local de processo único, dado de vida curta (no
máximo 24 h de inatividade) e a chave ficaria no mesmo disco. A purga é o controle.
**Consequência:** mesmas 150 conversas: 64 → 73 cotadas (48,7%); elegíveis sem documento
73/74 (98,6%); LLM cortado 9 → 0. O retry disparou uma vez, na extração, e recuperou o
turno; resta uma cotação indisponível. Turno p50 1,58 s, p99 5,05 s, máximo 8,25 s: 18 s é
o pior caso, não a espera típica. A retenção de 24 h é parâmetro de `open_sales_stack`; a
varredura por turno não tem índice em `atualizada_em` e precisa de um se a tabela crescer.

## D-039 — Escada de três níveis, divergência sempre gravada, assunto ligado e CLI
**Data:** 2026-09-11
**Contexto:** a revisão da tarefa 11 deixou três lacunas. O cache era descrito como nível
N2 da escada, mas fica antes do retry: com preço determinístico e TTL até a meia-noite,
nunca haveria entrada do dia para servir depois de uma falha. A sugestão de escalação do
conversador só era gravada junto de um handoff, então "o modelo sugeriu e a política não
escalou" não existia como dado. A regra "fora de escopo" existia, mas nada preenchia o
assunto. E não havia como conversar com o agente: só o harness o executava.
**Alternativas:** manter o cache como nível; gravar a divergência numa tabela nova ou em
`handoffs`; ligar o assunto só pela categoria do modelo ou só por léxico; montar a pilha
dentro da própria CLI.
**Decisão:** escada com N0 (chamada direta com hedge), N1 (retry) e N2 (escalação); o
cache é descrito como camada preventiva. `turn_events` ganha a coluna `sugestao`, e o
evento `decisao` é gravado em todo turno em que o conversador fala: decisão da política em
`status`, sugestão do modelo em `sugestao`, inclusive quando ninguém escala. O conversador
emite `assunto` (enum) no schema strict, e um piso lexical (`domain/scope.py`) fica
abaixo dele, porque a política pede o dado que falta antes de o conversador falar. A CLI
(`interfaces.cli`) é adapter sobre os casos de uso. Os buracos que ela expôs foram para a
aplicação e o wiring, não para a CLI:
- `Ingestor.next_index`, para retomar uma conversa;
- `SalesStack.inspector`, a inspeção nas conexões vivas, com a timeline drenada;
- `open_live_stack`, a composição de produção que o harness fazia à mão.
**Consequência:** mesma amostra de 150, com o schema novo:
- divergência 0 em 222 turnos com fala: o modelo nunca sugeriu escalar e a política nunca
  escalou depois de uma fala;
- as 33 escalações (31 por documento, 2 por cotação) aconteceram fora dos turnos de fala;
- a métrica existe, mas o dataset não a exercita: nenhum lead pede humano nem traz
  assunto fora de escopo;
- o piso de assunto não dispara em nenhuma mensagem de lead do dataset;
- conclusão de 72/150 e 72/74 elegíveis sem documento; a diferença para a D-038 é uma
  cotação indisponível a mais no sorteio.

A CLI mostrou ainda que um `.env` local com os valores da tarefa 8 derruba a conversa por
limite de tokens: a composição de produção lê o ambiente, e o harness sobrescreve por
cenário.

## D-040 — Coerência de configuração verificada na partida, contra pisos medidos
**Data:** 2026-09-14
**Contexto:** a demonstração da tarefa 12 escalou por limite de tokens no quarto turno. O
`.env` local guardava os valores da tarefa 8: timeout de 2 s, teto de 2,5 s e 4.000 tokens.
O harness nunca viu, porque fixa os valores por cenário, e a verificação de partida (D-035)
só testava conectividade. É a mesma família do 404: caminho de produção que nenhum teste
validava.
**Alternativas:** fixar os valores no código e remover as variáveis; validar só o formato
(positivo e finito), como o `LLMConfig` já fazia; gerar o `.env.example` a partir do código;
comparar a configuração com pisos medidos.
**Decisão:** pisos num único lugar, `infrastructure/llm/config.py`: `LLM_P999_SECONDS = 6,9`
(D-038) e `MAX_CONVERSATION_TOKENS = 10.095`, ao lado de `ENV_DEFAULTS`, de onde saem
os padrões do `LLMConfig`. `verify_configuration` roda em `open_live_stack`, antes de
qualquer rede, e junta as violações num único `StartupCheckError`:
- timeout do LLM abaixo do p99.9 medido;
- orçamento do turno abaixo da soma dos tetos das etapas;
- limite de tokens por conversa abaixo do máximo medido.

O teto por chamada não tem piso próprio, porque o `LLMConfig` já exige teto ≥ timeout.
Variável ausente cai no padrão, com aviso no log. O `.env.example` é conferido, não gerado:
um teste o compara com `ENV_DEFAULTS` e passa seus valores pela verificação. A verificação
fica fora de `open_sales_stack` porque o harness roda, de propósito, cenários abaixo do piso
(`antes` e `depois`).
**Consequência:** com o `.env` da tarefa 8, a CLI sai com código 1 e a mensagem traz o valor
configurado e o medido ("LLM_TIMEOUT_SECONDS: configurado 2 s, abaixo do p99.9 medido de
6.9 s (D-038)"). Os pisos valem para um provedor e um período, e já se moveram uma vez: o
máximo de tokens era 9.510 em D-034 e a rodada da tarefa 13 mediu 10.095
(`docs/measurements/task13-e2e.json`), ainda com 37% de folga até os 16.000. Medição nova
exige atualizar a constante.
