"""Log de execução em markdown, turno a turno, legível para humano (tarefa 11).

Adapter de apresentação: recebe o que `InspectConversation` leu e só formata.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from application.inspect_conversation import TurnReport
from application.tracing import QuoteAttempt
from application.turns import TurnEvent
from domain.handoff import HandoffDecision
from domain.quote import Declined
from interfaces.rendering import render_outbound

_SLOT_ORDER = ("idade", "veiculo_ano", "plano_id", "data_inicio")
_LABELS = {"idade": "idade", "veiculo_ano": "ano-modelo"}


def _cell(value: object) -> str:
    text = "—" if value is None or value == "" else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _quote(text: str) -> list[str]:
    return [f"> {line}" if line else ">" for line in text.splitlines() or [""]]


def _table(header: Sequence[str], rows: Iterable[Sequence[object]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows]
    return lines


def _attempts(attempts: Sequence[QuoteAttempt]) -> list[str]:
    # Tentativas físicas em ordem de disparo; a resolução lógica (tentativa 0) fecha a tabela.
    ordered = sorted(attempts, key=lambda item: (item.tentativa == 0, item.tentativa))
    return _table(
        ("tentativa", "status", "HTTP", "latência", "origem", "hedge"),
        (
            (
                item.tentativa or "desfecho",
                item.status,
                # Chamada física sem HTTP: abandonada pelo hedge vencedor ou estourou o prazo.
                item.http_status if item.http_status or not item.tentativa else "sem resposta",
                f"{item.latencia_ms} ms",
                item.origem,
                "sim" if item.hedge else "não",
            )
            for item in ordered
        ),
    )


def _acceptance(report: TurnReport) -> str:
    known = {slot.nome: slot.valor for slot in report.slots}
    payload = report.resposta.payload if report.resposta is not None else None
    if report.status == "recusada" and isinstance(payload, Declined):
        where = "regra local, sem chamar a API" if payload.origem == "regra_local" else "API"
        return f"**recusada** ({where}): {payload.motivo}"
    missing = [label for key, label in _LABELS.items() if key not in known]
    if missing:
        return "não avaliada: falta " + " e ".join(missing)
    return f"dentro das regras (idade {known['idade']}, ano-modelo {known['veiculo_ano']})"


def _escalation(decision: HandoffDecision | None) -> str:
    if decision is None:
        return "nenhuma regra disparou"
    suggestion = decision.sugestao_llm
    llm = ""
    if suggestion is not None and suggestion.escalar:
        llm = f"; o conversador sugeriu `{suggestion.motivo}`"
    return f"**escala** — regra `{decision.motivo}`{llm}"


def _opinion(event: TurnEvent) -> str:
    said = f"sugeriu `{event.sugestao}`" if event.sugestao else "não sugeriu escalar"
    policy = "não escalou" if event.status == "segue" else f"escalou por `{event.status}`"
    agree = (event.sugestao or "segue") == event.status
    return f"{said}; a política {policy}" + ("" if agree else " (divergência)")


def _snapshot(decision: HandoffDecision) -> list[str]:
    snapshot = decision.snapshot
    if snapshot is None:
        return []
    lines = ["", "**Snapshot enviado ao vendedor**", "", f"- Motivo: `{snapshot.motivo}`", ""]
    lines += _table(
        ("slot", "valor", "proveniência"),
        ((name, slot.valor, slot.proveniencia) for name, slot in snapshot.slots.items()),
    )
    if snapshot.tentativas:
        lines += ["", "Tentativas de cotação da conversa, como o vendedor as recebe:", ""]
        lines += _attempts([item for item in snapshot.tentativas if isinstance(item, QuoteAttempt)])
    return lines


def _slot_rank(name: str) -> int:
    return _SLOT_ORDER.index(name) if name in _SLOT_ORDER else len(_SLOT_ORDER)


def render_turn(number: int, report: TurnReport) -> list[str]:
    lines = [f"### Turno {number}", "", f"`{report.trace_id}` · rota: {' → '.join(report.rota)}"]
    lines += ["", "**Mensagem do lead** (PII redigida na ingestão)", "", *_quote(report.entrada)]
    slots = sorted(report.slots, key=lambda slot: _slot_rank(slot.nome))
    lines += ["", "**Slots após a extração**", ""]
    lines += (
        _table(
            ("slot", "valor", "status", "proveniência"),
            ((slot.nome, slot.valor, slot.status, slot.proveniencia) for slot in slots),
        )
        if slots
        else ["nenhum slot coletado ainda"]
    )
    lines += ["", "**Políticas**", "", f"- Aceitação: {_acceptance(report)}"]
    lines.append(f"- Escalação: {_escalation(report.escalacao)}")
    opinion = next((event for event in report.etapas if event.etapa == "decisao"), None)
    if opinion is not None:
        lines.append(f"- Conversador: {_opinion(opinion)}")
    if report.pedido:
        lines.append(f"- Falta dado: `{report.pedido}`, pedido ao lead")
    if report.objecao:
        lines.append(f"- Objeção: `{report.objecao}` (detectada por {report.objecao_fonte})")
    if report.erro:
        lines.append(f"- Erro do turno: `{report.erro}`")
    lines += ["", "**Etapas do turno**", ""]
    lines += _table(
        ("etapa", "status", "latência", "erro"),
        ((e.etapa, e.status, f"{e.latencia_ms} ms", e.erro) for e in report.etapas),
    )
    if report.tentativas:
        lines += ["", "**Tentativas de cotação**", "", *_attempts(report.tentativas)]
    lines += ["", "**Enviado ao lead**", ""]
    lines += _quote(render_outbound(report.resposta)) if report.resposta else ["nada enviado"]
    if report.escalacao is not None:
        lines += _snapshot(report.escalacao)
    return lines


def render_conversation(conversation_id: str, reports: Sequence[TurnReport]) -> str:
    lines = [f"## Conversa `{conversation_id}`", ""]
    for number, report in enumerate(reports, start=1):
        lines += [*render_turn(number, report), ""]
    return "\n".join(lines)
