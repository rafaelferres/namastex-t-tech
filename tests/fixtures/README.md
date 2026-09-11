# Origem das fixtures

`plans.json` é uma cópia byte a byte de
`namastex-fde-challenge/quote-service/data/plans.json`, no repositório vizinho.

`quote.json` foi capturado da função `cotar` original em
`quote-service/app/quote_logic.py`, sem servidor ou rede, substituindo apenas
o relógio por 2026-09-11. Entrada: plano `completo`, idade 30, veículo 2026,
CEP sintético `01310100`, início `2026-09-15`.
Os valores monetários são saída dessa função, não cálculos duplicados nos testes.

As fixtures contêm o payload original completo; somente projeções permitidas
podem ser retidas por objetos de domínio. A leitura de arquivos ocorre no setup
da sessão de pytest; os comportamentos testados executam em memória.
