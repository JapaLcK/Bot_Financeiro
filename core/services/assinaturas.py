"""Lista de assinaturas do usuário, a partir do Recurring Payments da Pluggy.

Fonte: `db/of_recurring.py` (uma linha por ocorrência casada, só despesa, só
conexão viva). Usada pela `/api/v2/assinaturas`, pelo Detetive e pelo chat dele.
Dinheiro sai `Decimal`, na escala em que o sync gravou (sem arredondar).
"""
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from statistics import median

from db.cards import extract_installment_info
from db.of_recurring import marcas, ocorrencias_do_usuario
from db.open_finance_categories import categoria_pigbank
from utils_date import add_months, clamp_day
from utils_text import guess_category, is_internal_category, merchant_key

DIAS_ATIVA = 40  # última cobrança há até 40 dias = ativa


def _mensal(linhas) -> bool:
    """O Recurring Payments só detecta ~mensal (30±5 dias, doc da Pluggy), e
    status, `proxima` e `total_mensal` supõem isso. É resposta externa: grupo cujo
    intervalo mediano é de outra ordem (semanal, anual) fica fora em vez de ser
    somado como mensal. A faixa é larga de propósito: uma cobrança extra no meio do
    mês derruba a mediana para ~15 dias e não faz da Netflix uma semanal. Com uma
    ocorrência casada só, não há intervalo: vale a Pluggy."""
    datas = [r["transaction_date"] for r in linhas]
    gaps = [(b - a).days for a, b in zip(datas, datas[1:])]
    return not gaps or 10 <= median(gaps) <= 45


def _merchant(r) -> dict:
    """Só os campos texto: tipo inesperado da Pluggy conta como ausente."""
    m = r["merchant"] if isinstance(r["merchant"], dict) else {}
    return {k: m[k] for k in ("name", "category") if isinstance(m.get(k), str)}


def _final(numero) -> str | None:
    n = (numero or "")[-4:]
    return n if n.isascii() and n.isdigit() else None


def _eh_servico(ult, categoria, marca) -> bool:
    if marca == "assinatura" or categoria == "assinaturas":
        return True
    m = _merchant(ult)
    textos = (ult["description"], m.get("name"), m.get("category"))
    return any(isinstance(t, str) and guess_category(t) == "assinaturas" for t in textos)


def _item(linhas: list, chave: str, categoria, marca, today: date) -> dict:
    ult = linhas[-1]
    valores = [abs(r["amount"]) for r in linhas]
    valor = valores[-1]
    # Reajuste: de trás para frente até o primeiro valor diferente.
    i = len(valores) - 1
    while i > 0 and valores[i - 1] == valor:
        i -= 1
    valor_anterior = valores[i - 1] if i > 0 else None
    reajuste_em = linhas[i]["transaction_date"].isoformat() if i > 0 else None

    ultima = ult["transaction_date"]
    dias = Counter(r["transaction_date"].day for r in linhas)
    maior = max(dias.values())
    dia = ultima.day if dias[ultima.day] == maior else next(d for d, n in dias.items() if n == maior)
    ativa = (today - ultima).days <= DIAS_ATIVA
    y, m = add_months(ultima.year, ultima.month, 1)
    proxima = date(y, m, clamp_day(y, m, dia))
    while ativa and proxima < today:  # cobrada há 32–40 dias: a "próxima" já passou
        y, m = add_months(y, m, 1)
        proxima = date(y, m, clamp_day(y, m, dia))

    return {
        "chave": chave,
        "nome": _merchant(ult).get("name") or ult["description"],
        "categoria": categoria,
        "valor": valor,
        "valor_anterior": valor_anterior,
        "reajuste_em": reajuste_em,
        "dia": dia,
        "proxima": proxima.isoformat(),
        "ultima": ultima.isoformat(),
        "desde": linhas[0]["transaction_date"].isoformat(),
        "meses": len({(r["transaction_date"].year, r["transaction_date"].month) for r in linhas}),
        "meio": {"tipo": "cartao" if ult["account_type"] == "CREDIT" else "conta",
                 "nome": ult["account_name"], "final": _final(ult["account_number"])},
        "status": "ativa" if ativa else "possivelmente_cancelada",
        "marcada": marca == "assinatura",
    }


def listar_assinaturas(user_id: int, today: date) -> dict:
    marcados = marcas(user_id)
    linhas_rp = ocorrencias_do_usuario(user_id)  # em ordem de data
    # Parcela e movimento interno saem por grupo da Pluggy: o grupo vizinho com
    # o mesmo descritor não derruba o legítimo.
    por_rp: dict[int, list] = defaultdict(list)
    for r in linhas_rp:
        por_rp[r["rp_id"]].append(r)
    fora = {k for k, linhas in por_rp.items()
            if not _mensal(linhas) or any(is_internal_category(categoria_pigbank(r["category"]))
                   or extract_installment_info({"creditCardMetadata": r["cc_meta"] if isinstance(r["cc_meta"], dict) else None})[1]
                   for r in linhas)}
    # Por chave, os sobreviventes viram cadeias (`por_rp` já está na ordem da 1ª
    # ocorrência): o grupo que começa depois do fim de uma cadeia é reajuste que a
    # Pluggy partiu em dois (um item, com valor_anterior) e cola na cadeia já
    # terminada de valor mais próximo — com dois serviços intercalados, a última
    # cadeia pode ser a do outro. O que se sobrepõe a todas é outro serviço do
    # mesmo comerciante (iCloud e Apple Music) e sai separado.
    cadeias: dict[str, list[list]] = defaultdict(list)
    for k, linhas in por_rp.items():
        if k in fora:
            continue
        cs = cadeias[merchant_key(linhas[0]["description"])]  # description é do grupo
        ini, v = linhas[0]["transaction_date"], abs(linhas[0]["amount"])
        antes = [c for c in cs if c[-1]["transaction_date"] < ini]
        if antes:
            min(antes, key=lambda c: (abs(abs(c[-1]["amount"]) - v),
                                      -c[-1]["transaction_date"].toordinal())).extend(linhas)
        else:
            cs.append(list(linhas))
    cadeias.pop("", None)  # descrição só de pontuação não identifica ninguém

    servicos, outras, chaves = [], [], set()
    for chave, linhas in ((c, ls) for c, lista in cadeias.items() for ls in lista):
        ult = linhas[-1]
        categoria = categoria_pigbank(ult["category"])
        chaves.add(chave)
        marca = marcados.get(chave)
        if marca == "ignorar":
            continue
        destino = servicos if _eh_servico(ult, categoria, marca) else outras
        destino.append(_item(linhas, chave, categoria, marca, today))

    servicos.sort(key=lambda x: -x["valor"])
    outras.sort(key=lambda x: -x["valor"])
    total = sum((x["valor"] for x in servicos if x["status"] == "ativa"), Decimal(0))
    return {"servicos": servicos, "outras": outras, "total_mensal": total,
            "total_anual": total * 12, "chaves": sorted(chaves)}
