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
