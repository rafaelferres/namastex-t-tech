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
