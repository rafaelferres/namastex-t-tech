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
