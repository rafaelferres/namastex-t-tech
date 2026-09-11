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
**Decisão:** cópia ativa em /home/rafael/namastex-test-tecnico, preservando a
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
