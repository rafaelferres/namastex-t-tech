# Fixtures sanitizadas de avaliação

Origem: `../namastex-fde-challenge/dataset/conversations.parquet`, corpus sintético
de 26.470 mensagens / 2.500 conversas. Nenhum texto de vendedor nem nome de pessoa
é fornecido ao extrator. Mensagens do lead passam por `PrivacyRedactor` antes de
entrar nestes arquivos. O parquet bruto permanece fora deste repositório.

`sample.json`: 48 casos, escolhidos deterministicamente pelos dois menores IDs
de conversa em cada estrato de motivo de recusa/aceitação, presença de mídia,
presença de CEP e desfecho. CEP está presente em todo o corpus; essa dimensão
não produz divisão adicional nesta versão. Idade e `veiculo_texto` são labels
separados; o protocolo do extrator recebe somente a tupla de mensagens redigidas.

`replay.json`: primeira conversa, em ordem de ID, cujo fluxo de mensagens de lead
tem timestamps não monotônicos. Conserva índices e timestamps reais e remove
colunas desnecessárias; serve para provar ordenação por índice, não por tempo.

Referência temporal: data da mensagem índice zero de cada conversa. Todas são de
2026, coerente com `scripts/generate_dataset.py` upstream (`base_ts` fixa o ano
2026). O oráculo aplica `AcceptanceRules` de `tests/fixtures/plans.json` com essa
data e encontra **751** recusas. Não usa o relógio corrente nem seleciona IDs
pré-estabelecidos para obter esse número.

O placeholder `NullExtractor` retorna slots ausentes: **0% idade e 0% veículo**,
na amostra e nos 2.500 casos. Isso verifica o harness, não mede qualidade de LLM.
Não há agente conversacional nesta fase; o oráculo identifica perfis inelegíveis,
mas ainda não demonstra recusa sem preço de ponta a ponta.

Auditoria completa independente: 2.500 spans rotulados pelo prefixo CPF do
gerador, todos com checksum válido; 0 falsos positivos e 0 falsos negativos.
O teste compara apenas agregados e nunca imprime os spans.

Regenerar: `uv run python -m tests.golden.build_sample`.
Corpus completo: `uv run pytest tests/golden tests/regression -m slow -q -rs`.
Loop rápido: `uv run pytest tests/golden tests/regression -m 'not slow' -q`.
`AUTOSEGURO_DATASET` substitui o caminho padrão; ausência do corpus causa skip
explícito nos testes completos. Nenhuma avaliação faz chamada de rede ou LLM.
