"""Quanto os investimentos renderam no Mês e no Total (card `rendimento`), por categoria e por ativo.

Função PURA (`calcular`): sem cursor, sem banco; quem lê o espelho e as fotos é `db/rendimento_contratado.ler`.
A Pluggy não manda rentabilidade (lastMonthRate/amountProfit vêm nulos): o valor é ESTIMADO pelo saldo.

Modelo CUSTO (`renda_fixa`, `tesouro`): ganho = saldo B − aplicado A. Total = B − A; Mês = (B − A) − (B0 − A0),
com B0/A0 da foto-base (a última confirmada até o fim do mês passado). Aporte sobe B e A juntos: só o juro fica.
Modelo COTAÇÃO (demais tipos): sem custo, o Total é `sem_custo`; Mês = B − B0, só com a mesma quantidade de cotas.

Limites declarados: resgate total no mês sai junto com o ganho realizado; resgate parcial (A < A0) aparece como vier,
pode ser perda que é ganho realizado (decisão do dono); o Total não inclui ganho já realizado; dividendos/JCP não
entram; o saldo de renda fixa pode ser líquido de IR; o Mês pode incluir até 6 dias do mês anterior; aplicação nova
no mês fica sem Mês até o mês seguinte; histórico só desde 30/09/2026.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .investido import TIPOS

CENTAVO = Decimal("0.01")
CUSTO = ("renda_fixa", "tesouro")
JANELA_DIAS = 6  # a foto-base vale até 6 dias antes do fim do mês passado


def fim_do_mes_anterior(hoje: date) -> date:
    return hoje.replace(day=1) - timedelta(days=1)


def _q(v: Decimal) -> Decimal:
    return v.quantize(CENTAVO, ROUND_HALF_UP) + 0  # + 0: sem "-0.00"


def _sem(motivo: str) -> dict:
    return {"valor": None, "pct": None, "motivo": motivo, "base": None}


def _ok(valor: Decimal, base: Decimal) -> dict:
    # pct do ativo sai do valor exato; base <= 0 não tem percentual
    return {"valor": _q(valor), "pct": _q(valor / base * 100) if base > 0 else None, "motivo": None, "base": base}


def _total(a: dict) -> dict:
    if a["saldo_ausente"]:
        return _sem("saldo_ausente")
    if a["tipo"] not in CUSTO:
        return _sem("sem_custo")
    if a["A"] is None or a["A"] <= 0:
        return _sem("aplicado_ausente")
    return _ok(a["B"] - a["A"], a["A"])


def _mes(a: dict) -> dict:
    b = a["base"]
    if a["saldo_ausente"]:
        return _sem("saldo_ausente")
    if b is None:
        return _sem("sem_base")
    if a["atualizado_em"] is None or a["atualizado_em"] <= b["observed_on"]:
        return _sem("sem_leitura_no_mes")  # o banco não leu de novo desde a base: seria um R$ 0,00 falso
    if a["tipo"] in CUSTO:
        if any(x is None or x <= 0 for x in (a["A"], b["A0"])):
            return _sem("aplicado_ausente")
        if b["B0"] is None:
            return _sem("saldo_ausente")
        return _ok((a["B"] - a["A"]) - (b["B0"] - b["A0"]), b["B0"])
    if a["Q"] is None or b["Q0"] is None:
        return _sem("quantidade_ausente")
    if a["Q"] != b["Q0"]:
        return _sem("quantidade_mudou")
    if b["B0"] is None:
        return _sem("saldo_ausente")
    return _ok(a["B"] - b["B0"], b["B0"])


def _periodo(rs: list[dict]) -> dict:
    """Soma dos valores (já em centavos) dos ativos COM dado; pct = Σvalor ÷ Σbase."""
    com = [r for r in rs if r["valor"] is not None]
    if not com:
        return {"valor": None, "pct": None, "sem_dado": len(rs)}
    valor, base = sum(r["valor"] for r in com), sum(r["base"] for r in com)
    return {"valor": valor, "pct": _q(valor / base * 100) if base > 0 else None, "sem_dado": len(rs) - len(com)}


def _saldo(ativos: list[dict]) -> Decimal | None:
    """Σ dos saldos; 0 com algum saldo ausente é `None` (não se sabe), como `investido.calcular`."""
    s = sum((a["B"] for a in ativos), Decimal("0.00"))
    return None if s == 0 and any(a["saldo_ausente"] for a in ativos) else s


def calcular(ativos: list[dict], hoje: date) -> dict:
    """`ativos`: [{tipo (chave de TIPOS), nome, instituicao, logo_proprio, saldo_ausente, B, A, Q,
    base: {observed_on, B0, A0, Q0} | None, atualizado_em (date no fuso do app)}]."""
    calc = [(a, _mes(a), _total(a)) for a in ativos]
    por_tipo: dict[str, list] = {}
    for c in calc:
        por_tipo.setdefault(c[0]["tipo"], []).append(c)

    def ativo(c):
        a, m, t = c
        pub = lambda r: {k: r[k] for k in ("valor", "pct", "motivo")}  # noqa: E731
        return {"nome": a["nome"], "instituicao": a["instituicao"], "logo_proprio": a["logo_proprio"],
                "saldo": None if a["saldo_ausente"] else a["B"] + 0, "mes": pub(m), "total": pub(t)}

    categorias = []
    for tipo, cs in por_tipo.items():
        cs = sorted(cs, key=lambda c: (c[0]["saldo_ausente"], -c[0]["B"], c[0]["nome"] or ""))
        categorias.append({"tipo": tipo, "rotulo": TIPOS[tipo], "saldo": _saldo([c[0] for c in cs]),
                           "quantidade": len(cs), "mes": _periodo([c[1] for c in cs]),
                           "total": _periodo([c[2] for c in cs]), "ativos": [ativo(c) for c in cs]})
    categorias.sort(key=lambda c: (c["saldo"] is None, -(c["saldo"] or 0), c["rotulo"]))
    return {"saldo": _saldo(ativos), "quantidade": len(ativos),
            "mes": _periodo([c[1] for c in calc]), "total": _periodo([c[2] for c in calc]),
            "mes_desde": fim_do_mes_anterior(hoje), "categorias": categorias}
