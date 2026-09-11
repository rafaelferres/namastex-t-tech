"""Recalcula os números do README sobre o dataset e grava só agregados, nunca PII.

Uso: AUTOSEGURO_DATASET=<parquet> uv run python -m scripts.dataset_facts --quote-url <url>
A API precisa rodar com QUOTE_FAILURE_RATE=0 e QUOTE_SLOW_RATE=0. `/planos` só alimenta
`AcceptanceRules`; `/quote` só é lida via `Quote.from_api` (a tabela nunca sai da API).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import unicodedata
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from domain.acceptance import AcceptanceRules
from domain.quote import Quote, QuoteRequest
from infrastructure.privacy import PrivacyRedactor
from interfaces.replay import dataset_path, read_rows
from tests.fakes import CANONICAL_OBJECTIONS
from tests.golden.harness import Case, cases_from_rows
from tests.regression.oracle import negative_cases

type Row = dict[str, object]
type Fact = dict[str, object]

OUTPUT = Path("docs/measurements/dataset-facts.json")
# ponytail: prefixos copiados do README; o multiplicador de região fica dentro da API.
AGRAVADOS = ("07", "08", "21", "26", "59")
MEDIA = ("[documento]", "[imagem]", "[audio]")
SELLER_QUOTE = re.compile(r"plano (\w+) por R\$ ([\d.]+,\d{2})")
CEP_LABEL = re.compile(r"\bcep\s+(\d{5}-?\d{3})\b", re.IGNORECASE)
CEP_NAIVE = re.compile(r"\d{5}-?\d{3}")
COMPETITOR = re.compile(r"\.\.\. a (.+) me ofereceu menos")
COVERAGE = "Cobre colisao, roubo e furto"
ABSENT_TERMS = (
    "handoff", "atendente", "instabilidade", "carência", "pro-rata", "pro rata", "prorata",
    "data de vigência", "vigência", "sinistro", "cancelamento",
)


def fold(text: str) -> str:
    """Minúsculas sem acento: a busca casa com e sem acento."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def pct(numerador: int, denominador: int, definicao: str, readme: str) -> Fact:
    return {
        "valor": round(100 * numerador / denominador, 1),
        "unidade": "%",
        "numerador": numerador,
        "denominador": denominador,
        "definicao": definicao,
        "readme": readme,
    }


def value(valor: object, definicao: str, readme: str, **extra: object) -> Fact:
    return {"valor": valor, "definicao": definicao, "readme": readme, **extra}


def by_conversation(rows: list[Row]) -> dict[str, list[Row]]:
    grouped: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        grouped[str(row["conversation_id"])].append(row)
    for group in grouped.values():
        group.sort(key=lambda row: int(str(row["message_index"])))
    return dict(sorted(grouped.items()))


def body(row: Row) -> str:
    return str(row["message_body"])


def seller_quote(group: list[Row]) -> tuple[str, Decimal] | None:
    for row in group:
        if row["sender_role"] == "vendedor" and (match := SELLER_QUOTE.search(body(row))):
            return match[1].lower(), Decimal(match[2].replace(".", "").replace(",", "."))
    return None


def lead_cep(group: list[Row]) -> str | None:
    for row in group:
        if row["sender_role"] == "lead" and (match := CEP_LABEL.search(body(row))):
            return match[1]
    return None


def year(case: Case) -> int:
    return int(case.veiculo_texto.rsplit(" ", 1)[1])


def chi2_sf_even(x: float, df: int) -> float:
    """P(X² > x) em forma fechada; vale só para graus de liberdade pares."""
    assert df % 2 == 0, "ponytail: forma fechada só para df par; use scipy se df for ímpar"
    half = x / 2
    return math.exp(-half) * sum(half**i / math.factorial(i) for i in range(df // 2))


def eligibility(cases: tuple[Case, ...], rules: AcceptanceRules) -> dict[str, Fact]:
    plano = sorted(rules.planos_validos)[0]
    idade_aceita = next(f.minimo for f in rules.faixas_idade if f.motivo_recusa is None)
    refs = {date.fromisoformat(case.reference_date) for case in cases}
    idade = veiculo = ambos = 0
    for case in cases:
        ref = date.fromisoformat(case.reference_date)
        # Cada eixo isolado: o outro vai num valor aceito (veículo 0 km, idade aceita).
        by_age = rules.evaluate(QuoteRequest(plano, case.idade, ref.year), ref) is not None
        by_car = rules.evaluate(QuoteRequest(plano, idade_aceita, year(case)), ref) is not None
        idade += by_age
        veiculo += by_car
        ambos += by_age and by_car
    total = len(negative_cases(cases, rules))
    base = {"ano_base": sorted({ref.year for ref in refs}), "sobreposicao_idade_e_veiculo": ambos}
    return {
        "recusa_idade": pct(
            idade, len(cases),
            "leads recusados só pela faixa etária de /planos (AcceptanceRules), com o "
            "veículo em faixa aceita; conta também quem é recusado pelos dois eixos",
            "11,2%",
        ) | base,
        "recusa_veiculo": pct(
            veiculo, len(cases),
            "leads recusados só pela idade do veículo (ano-base - ano no fim de "
            "veiculo_texto), com idade aceita; conta também quem é recusado pelos dois eixos",
            "21,2%",
        ) | base,
        "recusa_total": pct(
            total, len(cases),
            "tests.regression.oracle.negative_cases: recusa por qualquer regra, ano-base = "
            "data da primeira mensagem",
            "30,0% (751 de 2.500)",
        ) | base,
    }


def seller_quotes_vs_api(
    convs: dict[str, list[Row]], cases: dict[str, Case], client: httpx.Client
) -> tuple[dict[str, Fact], list[tuple[str, Decimal, str]]]:
    quotes: list[tuple[str, Decimal, str]] = []
    bate = difere = recusada = sem_cep = 0
    for conversation_id, group in convs.items():
        found = seller_quote(group)
        if found is None:
            continue
        plano, preco = found
        case = cases[conversation_id]
        quotes.append((plano, preco, case.outcome))
        cep = lead_cep(group)
        sem_cep += cep is None
        request = QuoteRequest(plano, case.idade, year(case), cep)
        response = client.post("/quote", json=request.to_payload())
        if response.status_code == 422:
            recusada += 1
            continue
        response.raise_for_status()
        # Só o prêmio sobrevive; o payload bruto (com multiplicadores) não é guardado.
        if Quote.from_api(response.json()).premio_mensal == preco:
            bate += 1
        else:
            difere += 1
    elegiveis = bate + difere
    return {
        "conversas_com_cotacao_do_vendedor": value(
            len(quotes),
            "conversas com mensagem do vendedor 'plano X por R$ N'",
            "2.500 cotações",
            denominador=len(convs),
        ),
        "cotacao_vendedor_bate_com_quote": value(
            bate,
            "mensalidade do vendedor == premio_mensal de POST /quote com o perfil real "
            "(lead_idade_informada, ano no fim de veiculo_texto, 'cep 99999-999' do lead, "
            "plano citado), sem data_inicio; 422 conta como não bate",
            "0 de 2.500 batem com a base_mensal do plano citado",
            numerador=bate,
            denominador=len(quotes),
            quote_aceita=elegiveis,
            quote_422=recusada,
            sem_cep_no_texto=sem_cep,
            data_da_api=date.today().isoformat(),
        ),
    }, quotes


def objections(convs: dict[str, list[Row]]) -> Fact:
    texts: set[str] = set()
    heads: set[str] = set()
    competitors: set[str] = set()
    with_objection = 0
    for group in convs.values():
        found = False
        for row in group:
            text = body(row)
            head = text.split("...")[0]
            if row["sender_role"] != "lead" or head not in CANONICAL_OBJECTIONS:
                continue
            found = True
            texts.add(text)
            heads.add(head)
            if match := COMPETITOR.search(text):
                competitors.add(match[1])
        with_objection += found
    return pct(
        with_objection, len(convs),
        "conversas com mensagem do lead cuja frase antes de '...' está em "
        "tests.fakes.CANONICAL_OBJECTIONS; variantes/textos/concorrentes são distintos",
        "6 variantes canônicas, 36 textos distintos, 5 concorrentes; ~52% com objeção",
    ) | {
        "variantes_canonicas": len(heads),
        "textos_distintos": len(texts),
        "concorrentes": len(competitors),
    }


def timestamp_facts(convs: dict[str, list[Row]]) -> dict[str, Fact]:
    monotonic = strict = messages = moved = adjacent = outside_lis = 0
    for group in convs.values():
        ts = [datetime.fromisoformat(str(row["timestamp"])) for row in group]
        pairs = list(zip(ts, ts[1:], strict=False))
        monotonic += all(a <= b for a, b in pairs)
        strict += all(a < b for a, b in pairs)
        messages += len(ts)
        order = sorted(range(len(ts)), key=ts.__getitem__)  # estável: empate mantém índice
        moved += sum(position != index for position, index in enumerate(order))
        adjacent += sum(b < a for a, b in pairs)
        tails: list[datetime] = []
        for stamp in ts:
            cut = bisect_right(tails, stamp)
            tails[cut:cut + 1] = [stamp]
        outside_lis += len(ts) - len(tails)
    return {
        "timestamp_monotonico": value(
            monotonic,
            "conversas cujo timestamp, em ordem de message_index, é não decrescente",
            "5 de 2.500",
            denominador=len(convs),
            estritamente_crescente=strict,
        ),
        "timestamp_reposiciona": pct(
            moved, messages,
            "mensagens cuja posição muda ao reordenar cada conversa por timestamp (sort "
            "estável) em vez de message_index",
            "67,6%",
        ) | {
            "alternativa_inversao_adjacente": adjacent,
            "alternativa_fora_da_maior_subsequencia_crescente": outside_lis,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=dataset_path())
    parser.add_argument("--quote-url", required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if not args.dataset.is_file():
        parser.error("Dataset ausente; configure AUTOSEGURO_DATASET ou --dataset")

    rows = read_rows(args.dataset)
    convs = by_conversation(rows)
    cases = cases_from_rows(rows)
    case_by_id = {case.conversation_id: case for case in cases}
    n = len(convs)
    redactor = PrivacyRedactor()
    facts: dict[str, Fact] = {}

    with httpx.Client(base_url=args.quote_url, timeout=10, trust_env=False) as client:
        planos = client.get("/planos")
        planos.raise_for_status()
        rules = AcceptanceRules.from_api(planos.json())
        facts |= eligibility(cases, rules)
        quote_facts, quotes = seller_quotes_vs_api(convs, case_by_id, client)

    ceps = {cid: lead_cep(group) for cid, group in convs.items()}
    facts["cep_agravado"] = pct(
        sum(cep is not None and cep.startswith(AGRAVADOS) for cep in ceps.values()), n,
        "conversas cujo primeiro 'cep 99999-999' do lead começa com 07, 08, 21, 26 ou 59",
        "35,8%",
    ) | {"conversas_com_cep": sum(cep is not None for cep in ceps.values())}

    lead_names: dict[str, set[str]] = defaultdict(set)
    for cid, group in convs.items():
        for row in group:
            if row["sender_role"] == "lead":
                lead_names[str(row["sender_name"])].add(cid)
    joao = lead_names.get("Joao Gomes", set())
    facts["sender_name_distintos"] = value(
        len(lead_names),
        "valores distintos de sender_name nas mensagens do lead",
        "336 nomes distintos para 2.500 conversas",
        denominador=n,
        incluindo_vendedor=len({str(row["sender_name"]) for row in rows}),
        max_conversas_por_nome=max(len(ids) for ids in lead_names.values()),
    )
    facts["joao_gomes"] = value(
        len(joao),
        "conversas cujo lead tem sender_name 'Joao Gomes'; idades e veículos distintos entre elas",
        "17 conversas, idades e veículos distintos",
        idades_distintas=len({case_by_id[cid].idade for cid in joao}),
        veiculos_distintos=len({case_by_id[cid].veiculo_texto for cid in joao}),
    )
    facts |= timestamp_facts(convs)

    def messages_with(predicate: str | re.Pattern[str]) -> int:
        if isinstance(predicate, re.Pattern):
            return sum(bool(predicate.search(body(row))) for row in rows)
        return sum(predicate in redactor.redact(body(row)) for row in rows)

    facts["pii_cpf"] = pct(
        sum(any(redactor.cpf_hash(body(row)) for row in group) for group in convs.values()), n,
        "conversas com CPF de dígito verificador válido (PrivacyRedactor.cpf_hash)",
        "100% das conversas",
    )
    facts["pii_cep"] = value(
        messages_with("[CEP]"),
        "mensagens com [CEP] após PrivacyRedactor (telefone redigido antes do CEP)",
        "3.879 mensagens",
        regex_ingenua_sem_fronteira=messages_with(CEP_NAIVE),
        rotulo_cep=messages_with(CEP_LABEL),
    )
    facts["pii_email"] = value(
        messages_with("[EMAIL]"), "mensagens com [EMAIL] após PrivacyRedactor", "1.379"
    )
    facts["pii_telefone"] = value(
        messages_with("[TELEFONE]"), "mensagens com [TELEFONE] após PrivacyRedactor", "1.379"
    )
    facts["pii_placa"] = value(
        messages_with("[PLACA]"), "mensagens com [PLACA] após PrivacyRedactor", "839"
    )

    facts |= quote_facts
    seller_rows = [row for row in rows if row["sender_role"] == "vendedor"]
    quote_rows = [row for row in seller_rows if SELLER_QUOTE.search(body(row))]
    facts["frase_de_cobertura"] = value(
        sum(COVERAGE in body(row) for row in quote_rows),
        f"mensagens de cotação do vendedor contendo '{COVERAGE}'",
        "as 2.500 usam a mesma frase nos três planos",
        denominador=len(quote_rows),
        planos_com_a_frase=len({
            match[1] for row in quote_rows
            if COVERAGE in body(row) and (match := SELLER_QUOTE.search(body(row)))
        }),
    )
    facts["cotacao_menciona_carencia"] = value(
        sum("carencia" in fold(body(row)) for row in quote_rows),
        "mensagens de cotação do vendedor com 'carência' (sem caixa nem acento)",
        "nenhuma menciona carência",
        denominador=len(quote_rows),
    )

    def seller_convs(*terms: str) -> int:
        return sum(
            any(
                row["sender_role"] == "vendedor" and any(t in fold(body(row)) for t in terms)
                for row in group
            )
            for group in convs.values()
        )

    facts["vendedor_pede_cpf"] = pct(
        seller_convs("cpf"), n, "conversas em que o vendedor escreve 'cpf'", "100%"
    )
    facts["vendedor_pergunta_vigencia"] = value(
        seller_convs("vigencia", "data de inicio", "inicio do seguro"),
        "conversas em que o vendedor escreve 'vigência', 'data de início' ou 'início do seguro'",
        "nunca",
        denominador=n,
    )

    runs: list[int] = []
    for group in convs.values():
        size = 0
        for row in [*group, {"sender_role": "vendedor"}]:
            if row["sender_role"] == "lead":
                size += 1
            elif size:
                runs.append(size)
                size = 0
    facts["rajada_do_lead"] = pct(
        sum(size >= 2 for size in runs), len(runs),
        "turnos do lead (sequência máxima de mensagens do lead entre mensagens do vendedor, "
        "em ordem de message_index) com 2+ mensagens",
        "23,1%, pico de 6",
    ) | {"pico": max(runs)}

    facts["midia_sem_transcricao"] = pct(
        sum(any(body(row).startswith(MEDIA) for row in group) for group in convs.values()), n,
        "conversas com mensagem começando por [documento], [imagem] ou [audio]",
        "56,8%",
    ) | {"message_type_nao_text": sum(row["message_type"] != "text" for row in rows)}

    sizes = [len(group) for group in convs.values()]
    years = [year(case) for case in cases]
    facts["mensagens_por_conversa"] = value(
        [min(sizes), max(sizes)], "mínimo e máximo de mensagens por conversa", "8 a 14"
    )
    facts["ano_do_veiculo"] = value(
        [min(years), max(years)], "mínimo e máximo do ano no fim de veiculo_texto", "2001 a 2024"
    )
    facts["objecoes"] = objections(convs)

    prices = [float(price) for _, price, _ in quotes]
    won = [float(outcome == "ganho") for _, _, outcome in quotes]
    decided = [(p, o == "ganho") for _, p, o in quotes if o in ("ganho", "perdido")]
    outcomes = sorted({outcome for _, _, outcome in quotes})
    plans = sorted({plano for plano, _, _ in quotes})
    table = Counter((plano, outcome) for plano, _, outcome in quotes)
    by_plan = Counter(plano for plano, _, _ in quotes)
    by_outcome = Counter(outcome for _, _, outcome in quotes)
    chi2 = sum(
        (table[p, o] - expected) ** 2 / expected
        for p in plans
        for o in outcomes
        if (expected := by_plan[p] * by_outcome[o] / len(quotes))
    )
    df = (len(plans) - 1) * (len(outcomes) - 1)
    facts["correlacao_preco_desfecho"] = value(
        round(statistics.correlation(prices, won), 3),
        "Pearson (ponto-bisserial) entre preço do vendedor e desfecho == 'ganho', 2.500 cotações",
        "0,02",
        so_ganho_vs_perdido=round(
            statistics.correlation([float(p) for p, _ in decided], [float(w) for _, w in decided]),
            3,
        ),
    )
    facts["desfecho_por_plano"] = value(
        round(chi2_sf_even(chi2, df), 3),
        "p-valor do qui-quadrado de independência plano citado x desfecho (tabela 3x4)",
        "estatisticamente idêntica",
        qui_quadrado=round(chi2, 3),
        graus_de_liberdade=df,
    )

    corpus = [fold(body(row)) for row in rows]
    facts["termos_ausentes"] = value(
        {term: sum(fold(term) in text for text in corpus) for term in ABSENT_TERMS},
        "mensagens (lead e vendedor) contendo o termo, sem caixa e sem acento",
        "zero ocorrências",
    )

    negatives = {case.conversation_id for case in negative_cases(cases, rules)}
    facts["vendedor_cotou_inelegivel"] = value(
        sum(
            any(row["sender_role"] == "vendedor" and "R$" in body(row) for row in convs[cid])
            for cid in negatives
        ),
        "inelegíveis (oracle.negative_cases) em que o vendedor apresentou preço em R$",
        "0 / 751 recusas corretas",
        denominador=len(negatives),
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n")
    for key, fact in facts.items():
        print(f"{key}: {fact['valor']}")


if __name__ == "__main__":
    main()
