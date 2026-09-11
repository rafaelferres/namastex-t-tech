# Registros das sessões com IA

Conversas com as ferramentas de IA usadas neste desafio, exportadas dos logs locais
(`.jsonl`) para markdown. Um arquivo por sessão, numerado pela ordem de início, em
horário de Brasília (UTC−3).

O fluxo foi: o Codex conduziu as tarefas 1 a 9 numa sessão principal (01) e delegou
partes a subagentes (02 a 17). O Claude Code assumiu a partir da tarefa 9 (18).

| # | Arquivo | Ferramenta | Assunto |
|---|---|---|---|
| 01 | [01-codex-2026-09-10-2359.md](01-codex-2026-09-10-2359.md) | Codex CLI | Sessão principal: tarefas 1 a 9, da fundação do domínio ao conversador, com a PR de cada tarefa |
| 02 | [02-codex-2026-09-11-0008.md](02-codex-2026-09-11-0008.md) | Codex CLI (subagente) | Revisão da fundação do domínio (tarefa 1) |
| 03 | [03-codex-2026-09-11-0041.md](03-codex-2026-09-11-0041.md) | Codex CLI (subagente) | Revisão do cliente HTTP e da taxonomia de erro (tarefa 2) |
| 04 | [04-codex-2026-09-11-0058.md](04-codex-2026-09-11-0058.md) | Codex CLI (subagente) | Revisão de retry e hedge (tarefa 3) |
| 05 | [05-codex-2026-09-11-0757.md](05-codex-2026-09-11-0757.md) | Codex CLI (subagente) | Revisão de guard, SQLite e cache (tarefa 4) |
| 06 | [06-codex-2026-09-11-0827.md](06-codex-2026-09-11-0827.md) | Codex CLI (subagente) | Revisão da recalibração e da rastreabilidade (tarefa 5) |
| 07 | [07-codex-2026-09-11-0848.md](07-codex-2026-09-11-0848.md) | Codex CLI (subagente) | Revisão de apresentação e escalação (tarefa 6) |
| 08 | [08-codex-2026-09-11-0906.md](08-codex-2026-09-11-0906.md) | Codex CLI (subagente) | Tarefa 7: redação de PII e proteção dos logs |
| 09 | [09-codex-2026-09-11-0907.md](09-codex-2026-09-11-0907.md) | Codex CLI (subagente) | Tarefa 7: replay do dataset |
| 10 | [10-codex-2026-09-11-0907.md](10-codex-2026-09-11-0907.md) | Codex CLI (subagente) | Tarefa 7: persistência SQLite |
| 11 | [11-codex-2026-09-11-0935.md](11-codex-2026-09-11-0935.md) | Codex CLI (subagente) | Tarefa 8: cliente de LLM |
| 12 | [12-codex-2026-09-11-0935.md](12-codex-2026-09-11-0935.md) | Codex CLI (subagente) | Tarefa 8: extrator de slots |
| 13 | [13-codex-2026-09-11-0937.md](13-codex-2026-09-11-0937.md) | Codex CLI (subagente) | Tarefa 8: harness de avaliação da extração |
| 14 | [14-codex-2026-09-11-1134.md](14-codex-2026-09-11-1134.md) | Codex CLI (subagente) | Tarefa 9: avaliação isolada da extração |
| 15 | [15-codex-2026-09-11-1136.md](15-codex-2026-09-11-1136.md) | Codex CLI (subagente) | Tarefa 9: revisão do conversador |
| 16 | [16-codex-2026-09-11-1144.md](16-codex-2026-09-11-1144.md) | Codex CLI (subagente) | Tarefa 9: outbox de entrega |
| 17 | [17-codex-2026-09-11-1144.md](17-codex-2026-09-11-1144.md) | Codex CLI (subagente) | Tarefa 9: revisão do harness de avaliação |
| 18 | [18-claude-code-2026-09-11-1153.md](18-claude-code-2026-09-11-1153.md) | Claude Code | Sessão principal: tarefas 9, 9.1, 9.2, 10 e 11. Exportada ao fim da tarefa 11 |
| 19 | [19-claude-code-2026-09-11-1632.md](19-claude-code-2026-09-11-1632.md) | Claude Code | Resumo automático de sessão (tarefas 9.2 e 10) |
| 20 | [20-claude-code-2026-09-11-1643.md](20-claude-code-2026-09-11-1643.md) | Claude Code | Resumo automático de sessão (tarefa 10) |
| 21 | [21-claude-code-2026-09-11-1647.md](21-claude-code-2026-09-11-1647.md) | Claude Code | Resumo automático de sessão (tarefa 10, PR aberta) |
| 22 | [22-claude-code-2026-09-11-1709.md](22-claude-code-2026-09-11-1709.md) | Claude Code | Resumo automático de sessão (tarefa 10 concluída) |

As sessões 19 a 22 são chamadas de resumo que o Claude Code fez sozinho durante a
sessão 18: um pedido e uma resposta, sem ferramentas.

## Como foram exportados

Um script Python, só com biblioteca padrão e fora do repositório, lê cada `.jsonl` e
gera o markdown. Cada arquivo abre com ferramenta, início, fim e contagem de
mensagens. O que ficou:

- pedidos do usuário e respostas do assistente, na íntegra;
- cada chamada de ferramenta em uma linha, com os argumentos principais, cortada em
  cerca de 300 caracteres;
- resultados de ferramenta cortados em cerca de 800 caracteres, com a marca
  `[… N caracteres omitidos]`;
- resumos de compactação de contexto, em blocos recolhíveis.

## O que foi removido

- **Raciocínio interno:** blocos de thinking do Claude e reasoning criptografado do
  Codex.
- **Instruções de sistema e de ambiente:** blocos `<system-reminder>`, mensagens
  `developer`, `turn_context` e `world_state` do Codex, e o AGENTS.md e o contexto de
  ambiente que a ferramenta injeta. Do `session_meta` ficaram só a data e o diretório.
- **Hooks, anexos e metadados internos:** registros de hook, anexos, snapshots e
  contagem de tokens. Quando um hook bloqueou uma chamada, o resultado aparece como
  `[mensagem de bloqueio de hook omitida]`.
- **Mensagens criptografadas entre agentes do Codex:** o texto da tarefa de cada
  subagente chega cifrado no log. Aparece só o cabeçalho (remetente, destinatário e
  tipo).
- **Transcrições internas dos subagentes do Claude Code:** na sessão 18 aparecem só a
  chamada e o resultado de cada um.

Também passou por redação:

- e-mails viram `[EMAIL]`;
- chaves, tokens, cabeçalhos `Authorization: Bearer`, JWTs e atribuições `*_KEY=`,
  `*_TOKEN=` e `*_SECRET=` viram `[SEGREDO]`;
- CPF, telefone, CEP e placa viram `[CPF]`, `[TELEFONE]`, `[CEP]` e `[PLACA]`. Os dados
  do dataset são sintéticos, mas foram redigidos por precaução;
- caminhos do diretório pessoal viram `~`.
