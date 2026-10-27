"""Compra parcelada do Open Finance: a chave do grupo, o nome e o "n de N pagas" (funções puras).

O banco manda cada parcela como uma transação com a descrição "LOJA 02/12". A chave do grupo
não pode carregar esse sufixo (muda a cada parcela) nem depender do mês da parcela (todas
podem chegar no mesmo dia): usa a descrição sem o "n/N" e o dia da compra (`purchaseDate`).

`progresso` é a regra do "n de N pagas" (dono, 2026-10-24): espelho de `progresso` em
`webapp/src/dashboard/parts/cardIa/cartao.tsx`, `app/src/features/painel/complementos.tsx` e
`frontend/dashboard.js`, todos presos por `tests/fixtures/parcelas_progresso.json`."""
from __future__ import annotations

import re
from datetime import date
from uuid import NAMESPACE_OID, UUID, uuid5

from utils_date import add_months

# O "n/N" só no FIM, com "parc"/"parcela", separador, parênteses e ponto opcionais. O (?<!\d)
# impede cortar o fim de um número maior ("PEDIDO 1010/12" não é a parcela 10).
_FIM = re.compile(
    r"[\s\-–—(]*(?:parc(?:ela)?\.?\s*)?(?<!\d)0*(\d{1,3})\s*(?:/|\s+de\s+)\s*0*(\d{1,3})[\s.)]*$",
    re.IGNORECASE,
)
_CAUDA = 40  # o sufixo cabe aqui; olhar só a cauda evita o custo quadrático do search em texto longo


def sem_parcela(desc: str | None, n: int | None, total: int | None) -> str:
    """A descrição sem o "n/N" do fim. Só corta se for EXATAMENTE a parcela n de `total`
    ("PAG 10/12" na parcela 3/12 fica) e se sobrar texto."""
    desc = (desc or "").strip()
    base = max(0, len(desc) - _CAUDA)
    m = _FIM.search(desc, base) if n and total else None
    if m and (int(m[1]), int(m[2])) == (n, total) and desc[:m.start()].strip():
        return desc[:m.start()].strip()
    return desc


def chave_grupo_of(card_id: int, desc: str | None, raw: dict | None, tx_date: date | None,
                   n: int | None, total: int | None) -> UUID:
    """O group_id da compra (cartão + loja + nº de parcelas + valor total + dia da compra).
    `n`/`total` vêm de `extract_installment_info` (o chamador já os tem)."""
    meta = (raw or {}).get("creditCardMetadata") or {}
    total_amount = meta.get("totalAmount")
    try:
        sufixo = date.fromisoformat(str(meta.get("purchaseDate"))[:10]).isoformat()
    except ValueError:
        # Sem purchaseDate: o mês de origem estimado (mês da parcela − (n−1)), como era.
        sufixo = ""
        if n and tx_date is not None:
            oy, om = add_months(tx_date.year, tx_date.month, -(int(n) - 1))
            sufixo = f"{oy:04d}-{om:02d}"
    loja = " ".join(sem_parcela(desc, n, total).lower().split())
    return uuid5(NAMESPACE_OID, "|".join([
        str(card_id), loja, str(total),
        str(total_amount) if total_amount is not None else "", sufixo,
    ]))


def nome_grupo(nota: str | None, desc: str | None, n: int | None, total: int | None) -> str:
    """Nota do usuário; senão a descrição do banco sem o "n/N"; senão "Parcelamento"."""
    return (nota or "").strip() or sem_parcela(desc, n, total) or "Parcelamento"


def progresso(g: dict) -> dict:
    """{"pagas", "n", "quitada"} de {"installments_total", "parcelas": [{"installment_no", "is_paid"}]}.
    N é o total do banco (ou o que o PigBank viu, sem total) e as parcelas antes da 1ª vista contam
    como pagas: o Open Finance só traz as parcelas a partir da que está na fatura."""
    parcelas = g.get("parcelas") or []
    nos = [int(p["installment_no"] or 0) for p in parcelas]
    total = int(g.get("installments_total") or 0)
    n = max(total if total > 0 else len(parcelas), max(nos, default=0))
    antes = max(0, min(nos) - 1) if nos else 0
    pagas = min(n, antes + sum(1 for p in parcelas if p["is_paid"]))
    return {"pagas": pagas, "n": n, "quitada": pagas >= n and all(p["is_paid"] for p in parcelas)}


def cobre_todas(parcelas: list, n: int) -> bool:
    """As parcelas vistas cobrem as N (sem número repetido)? Se não, total/restante em R$ seriam só o
    que se viu e não se mostram (dono, 2026-10-26). Sem número (manual antigo), conta as linhas."""
    nos = {int(p["installment_no"] or 0) for p in parcelas} - {0}
    return (len(nos) if nos else len(parcelas)) >= n
