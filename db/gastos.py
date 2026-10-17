"""`GET /api/v2/gastos` ("Para onde vai", visão do mês): as saídas do mês por categoria e por dia.

Nenhum SQL de mês novo: as MESMAS linhas do `lancamentos.listar` (sem guardado, que é opt-in) passadas
por `mes_detalhes._em_reais` (sem interno e sem outra moeda), então Σ categorias = Saiu do Resumo nos
meses sem `outra_moeda`. O mês pedido e o anterior saem de UMA transação repeatable read / read only.
`saidas_em_outra_moeda` é derivado (saídas não internas − saídas em reais, por chave): o `fora` do
`_em_reais` mistura entrada e movimento de caixinha.

`ler_categoria` (`GET /api/v2/gastos/categoria`): o detalhe de UMA categoria do mês, das MESMAS linhas
(`_mes`) filtradas pela chave: total, compras, valor médio, as 6 últimas, a série dos 3 últimos meses
(`null` no mês que o corte do plano toca; esse mês nem é lido), o calendário e os 5 estabelecimentos
(`db/estabelecimentos.py`). Uma transação repeatable read / read only, como `ler_mes`.
"""
import calendar
from collections import Counter
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext

from core.services.plan_service import history_earliest_date

from . import estabelecimentos, resumo_mes
from .categories import ensure_user_categories_seeded
from .connection import CAT_META_SQL, CAT_VAZIA_LABEL, cat_key_sql, cat_visual, get_conn
from .contas_hoje import listar as contas
from .estabelecimentos import SEM_DESCRICAO
from .lancamentos import MOTIVOS_ITEM, listar
from .mes_detalhes import _em_reais

MAIORES_POR_DIA = 3
ULTIMAS = 6
# A ordem da resposta: `resumo_mes.MOTIVOS` (das contas passam só os que estão nela; `inicio_do_historico`
# vem do corte do plano), os de `MOTIVOS_ITEM` que faltam e o do arredondamento. Derivada: motivo novo
# numa das duas fontes entra aqui sozinho.
MOTIVOS_GASTOS = (*resumo_mes.MOTIVOS, *(m for m in MOTIVOS_ITEM if m not in resumo_mes.MOTIVOS),
                  "arredondamento_por_grupo")


def _chave(r: dict) -> str:
    return r["categoria"] or CAT_VAZIA_LABEL


def _mes(cur, uid: int, ano: int, mes: int, corte):
    """O mês lido, ou `None` se o corte do plano o cobre inteiro."""
    inicio, fim = resumo_mes.mes_de(date(ano, mes, 1))
    if corte and corte >= fim:
        return None
    desde = max(inicio, corte) if corte else inicio
    linhas, _ = listar(cur, uid, desde, fim, limite=None)
    motivos: set = set()
    validas, _ = _em_reais(linhas, motivos)
    saidas = [r for r in validas if r["tipo"] == "saida"]
    fora = (Counter(_chave(r) for r in linhas if r["tipo"] == "saida" and not r["interno"])
            - Counter(_chave(r) for r in saidas))
    motivos.discard("outra_moeda")  # volta só se houver SAÍDA fora (`ler_mes`)
    return {"inicio": inicio, "fim": fim, "desde": desde, "saidas": saidas, "fora_por_chave": fora,
            "motivos": motivos}


def _soma(saidas, chave: str, ate: date) -> Decimal:
    return sum((r["valor"] for r in saidas if _chave(r) == chave and r["dia"] <= ate), Decimal(0))


def _visual(meta, chave: str) -> dict:
    m = meta.get(chave)
    return {"chave": chave, "nome": m["name"] if m else chave, **cat_visual(m and m["emoji"], m and m["color"])}


def _por_categoria(rs) -> list:
    """`{chave, valor}` do dia, das MESMAS linhas que dão o `total`; maior valor primeiro, empate pela chave."""
    por_chave: dict = {}
    for r in rs:
        por_chave[_chave(r)] = por_chave.get(_chave(r), Decimal(0)) + r["valor"]
    return [{"chave": k, "valor": v} for k, v in sorted(por_chave.items(), key=lambda kv: (-kv[1], kv[0]))]


def _calendario(atual, saidas) -> tuple[list, dict]:
    """Os dias com saída dentro da grade e a linha `fora_da_grade`, das `saidas` dadas."""
    por_dia: dict = {}
    fora_da_grade = {"quantidade": 0, "valor": Decimal(0)}
    for r in saidas:  # a ordem do `listar`: a mais recente primeiro
        if atual["desde"] <= r["dia"] < atual["fim"]:
            por_dia.setdefault(r["dia"], []).append(r)
        else:
            fora_da_grade["quantidade"] += 1
            fora_da_grade["valor"] += r["valor"]
    dias = [{"dia": d.isoformat(), "total": sum((r["valor"] for r in rs), Decimal(0)),
             "maiores": [{"descricao": (r["descricao"] or "").strip() or SEM_DESCRICAO, "valor": r["valor"],
                          "categoria": _chave(r)}
                         for r in sorted(rs, key=lambda r: -r["valor"])[:MAIORES_POR_DIA]],
             "quantidade": len(rs), "categorias": _por_categoria(rs)}
            for d, rs in sorted(por_dia.items())]
    return dias, fora_da_grade


def ler_mes(uid: int, ano: int, mes: int, agora) -> dict:
    corte = history_earliest_date(uid, agora)
    ensure_user_categories_seeded(uid)  # conexão própria e escreve: fora da transação de leitura
    a_ano, a_mes = (ano - 1, 12) if mes == 1 else (ano, mes - 1)
    ant_inicio = date(a_ano, a_mes, 1)
    cortou_ant = bool(corte and corte > ant_inicio)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        atual = _mes(cur, uid, ano, mes, corte)
        ant = _mes(cur, uid, a_ano, a_mes, corte) if atual and not cortou_ant else None
        cur.execute(CAT_META_SQL, (uid,))
        meta = {r["cat"]: r for r in cur.fetchall()}
        conta_motivos = set(contas(cur, uid)["motivos"]) & set(resumo_mes.MOTIVOS)
        conn.rollback()

    hoje = agora.date()
    corrente = (ano, mes) == (hoje.year, hoje.month)
    ultimo = calendar.monthrange(ano, mes)[1]
    ultimo_ant = calendar.monthrange(a_ano, a_mes)[1]
    dia_de_corte = hoje.day if corrente else ultimo
    limite = date(ano, mes, dia_de_corte)
    # Mês corrente: o mesmo dia do anterior (31/03 contra fim de fevereiro). Mês passado: o anterior
    # inteiro, sem `min` (fevereiro contra janeiro conta os dias 29 a 31).
    limite_ant = date(a_ano, a_mes, min(hoje.day, ultimo_ant) if corrente else ultimo_ant)
    inicio_mes = date(ano, mes, 1)
    base = {"mes": f"{ano:04d}-{mes:02d}", "hoje": hoje.isoformat(), "dia_de_corte": dia_de_corte,
            "historico_desde": corte.isoformat() if corte and corte > inicio_mes else None}
    vistos = set(conta_motivos)
    if cortou_ant:
        vistos.add("inicio_do_historico")
    if atual is None:
        return {**base, "disponivel": False, "total": None, "categorias": [], "dias": [],
                "fora_da_grade": {"quantidade": 0, "valor": Decimal(0)}, "saidas_em_outra_moeda": 0,
                "anterior_total_ate_corte": None, "motivos": [m for m in MOTIVOS_GASTOS if m in vistos]}

    por_chave: dict = {}
    for r in atual["saidas"]:
        c = por_chave.setdefault(_chave(r), {"valor": Decimal(0), "quantidade": 0})
        c["valor"] += r["valor"]
        c["quantidade"] += 1
    categorias = sorted(({**_visual(meta, k), **c, "valor_ate_corte": _soma(atual["saidas"], k, limite),
                          "anterior_ate_corte": _soma(ant["saidas"], k, limite_ant) if ant else None}
                         for k, c in por_chave.items()), key=lambda c: (-c["valor"], c["chave"]))
    dias, fora_da_grade = _calendario(atual, atual["saidas"])
    em_outra_moeda = sum(atual["fora_por_chave"].values())
    vistos |= atual["motivos"] | (ant["motivos"] if ant else set())
    if em_outra_moeda:
        vistos.add("outra_moeda")
    return {**base, "disponivel": True, "total": sum((c["valor"] for c in categorias), Decimal(0)),
            "categorias": categorias, "dias": dias, "fora_da_grade": fora_da_grade,
            "saidas_em_outra_moeda": em_outra_moeda,
            # todas as saídas do anterior até o corte, também as de categoria que não existe neste mês
            "anterior_total_ate_corte": (sum((r["valor"] for r in ant["saidas"] if r["dia"] <= limite_ant),
                                             Decimal(0)) if ant else None),
            "motivos": [m for m in MOTIVOS_GASTOS if m in vistos]}


def _atras(ano: int, mes: int, n: int) -> tuple[int, int]:
    a, m = divmod(ano * 12 + mes - 1 - n, 12)
    return a, m + 1


def ler_categoria(uid: int, ano: int, mes: int, categoria: str, agora) -> dict:
    corte = history_earliest_date(uid, agora)
    ensure_user_categories_seeded(uid)  # conexão própria e escreve: fora da transação de leitura
    meses = [_atras(ano, mes, n) for n in (2, 1, 0)]
    cortado = [bool(corte and corte > date(a, m, 1)) for a, m in meses]  # monotônico: o M-2 é o 1º
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        lidos = [None if c else _mes(cur, uid, a, m, corte) for (a, m), c in zip(meses[:2], cortado)]
        atual = _mes(cur, uid, ano, mes, corte)  # None = o mês todo está antes do corte
        lidos.append(atual)
        cur.execute(f"select {cat_key_sql('%s::text')} as chave", (categoria,))
        chave = cur.fetchone()["chave"]
        cur.execute(CAT_META_SQL, (uid,))
        meta = {r["cat"]: r for r in cur.fetchall()}
        conta_motivos = set(contas(cur, uid)["motivos"]) & set(resumo_mes.MOTIVOS)
        saidas = [r for r in atual["saidas"] if _chave(r) == chave] if atual else []
        estabs = estabelecimentos.ler(cur, uid, saidas)
        conn.rollback()

    total = sum((r["valor"] for r in saidas), Decimal(0)) if atual else None
    dias, fora_da_grade = _calendario(atual, saidas) if atual else ([], {"quantidade": 0, "valor": Decimal(0)})
    em_outra_moeda = atual["fora_por_chave"].get(chave, 0) if atual else 0
    vistos = conta_motivos.union(*(x["motivos"] for x in lidos if x))
    if cortado[0]:
        vistos.add("inicio_do_historico")
    if em_outra_moeda:
        vistos.add("outra_moeda")
    inicio_mes = date(ano, mes, 1)
    with localcontext(prec=60):  # o contexto de 28 dígitos estoura o quantize com total >= ~1e27 (numeric aceita)
        medio = (total / len(saidas)).quantize(Decimal("0.01"), ROUND_HALF_UP) if saidas else None
    return {"mes": f"{ano:04d}-{mes:02d}", "disponivel": atual is not None, "hoje": agora.date().isoformat(),
            "historico_desde": corte.isoformat() if corte and corte > inicio_mes else None,
            "categoria": _visual(meta, chave), "total": total,
            "compras": len(saidas) if atual else None,
            "valor_medio": medio,
            "ultimas": [{"data": r["dia"].isoformat(), "descricao": (r["descricao"] or "").strip() or SEM_DESCRICAO,
                         "mensagem": r["mensagem"], "origem": r["origem"], "valor": r["valor"]}
                        for r in saidas[:ULTIMAS]],
            "serie": [{"mes": f"{a:04d}-{m:02d}", "valor": _soma(x["saidas"], chave, date.max) if x and not c else None}
                      for (a, m), c, x in zip(meses, cortado, lidos)],
            "estabelecimentos": estabs, "dias": dias, "fora_da_grade": fora_da_grade,
            "saidas_em_outra_moeda": em_outra_moeda, "motivos": [m for m in MOTIVOS_GASTOS if m in vistos]}
