"""Agregados completos da Home sobre o leitor oficial de lançamentos do mês.

Totais mantêm TOTAIS_SQL. Categorias/dias retiram internos e moeda não BRL:
se a regra legada somou outra moeda, a divergência é explícita na cobertura.
O cartão pertence à fatura selecionada e conserva o dia original da compra.
"""
import logging
from datetime import date, timedelta
from decimal import Decimal

from core.services.plan_service import history_earliest_date

from . import lancamentos, resumo_mes
from .connection import get_conn
from .contas_hoje import listar as contas


logger = logging.getLogger(__name__)
_AVISADAS: set = set()  # uma vez por origem, não por linha


def _em_reais(linhas, motivos: set) -> tuple[list, int]:
    """(linhas válidas, quantas ficaram fora): fora o interno e o que não é BRL (este conta em
    `fora_do_total` e levanta `outra_moeda`). Junta os `motivos` das válidas. Uma regra só."""
    validas, fora = [], 0
    for r in linhas:
        if r["interno"]:
            continue
        if r["moeda"] != "BRL":
            fora += 1
            motivos.add("outra_moeda")
            continue
        motivos.update(r["motivos"])
        validas.append(r)
    return validas, fora


def ler(user_id: int, ano: int, mes: int, agora) -> dict:
    inicio, fim = resumo_mes.mes_de(date(ano, mes, 1))
    corte = history_earliest_date(user_id, agora)
    desde = max(inicio, corte) if corte else inicio
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        total = resumo_mes.totais(cur, user_id, desde, fim)
        linhas, _ = lancamentos.listar(cur, user_id, desde, fim, limite=None)
        motivos = set(contas(cur, user_id)["motivos"]) & set(resumo_mes.MOTIVOS)
        # Não mistura pagamento de fatura/transferência interna com guardar.
        # Estes são os tipos gravados pelos writers de caixinha/investimento;
        # nenhuma variação do saldo bancário é inferida como aporte.
        cur.execute("""select tipo = any(%s) as aporte,
                              upper(coalesce(currency,'BRL')) as moeda,
                              sum(valor) as total, count(*) as quantidade
                       from launches where user_id=%s and criado_em >= %s and criado_em < %s
                        and tipo = any(%s)
                        group by 1, 2""",
                    (list(lancamentos.GUARDAR), user_id, desde, fim,
                     list(lancamentos.GUARDAR + lancamentos.RESGATAR)))
        movimentos = cur.fetchall()
        conn.rollback()
    if corte and corte > inicio:
        motivos.add("inicio_do_historico")
    categorias, dias = {}, {}
    origens = dict.fromkeys(("cartao", "banco", "carteira", "registro_antigo"), Decimal(0))
    guardado = {"aportes": Decimal(0), "saques": Decimal(0)}
    fora = 0
    saiu_agendado = Decimal(0)  # saída fora do cartão com data depois de `agora`: valor já conhecido
    for r in movimentos:
        if r["moeda"] != "BRL":
            fora += r["quantidade"]
            motivos.add("outra_moeda")
        else:
            guardado["aportes" if r["aporte"] else "saques"] += r["total"]
    validas, nao_brl = _em_reais(linhas, motivos)
    fora += nao_brl
    for r in validas:
        dia = r["dia"].isoformat()
        diario = dias.setdefault(dia, {"dia": dia, "entrou": Decimal(0), "saiu": Decimal(0)})
        diario["entrou" if r["tipo"] == "entrada" else "saiu"] += r["valor"]
        if r["tipo"] == "saida":
            if r["origem"] != "cartao" and r["dia"] > agora.date():
                saiu_agendado += r["valor"]
            categoria = categorias.setdefault(r["categoria"], {
                "categoria": r["categoria"], "valor": Decimal(0), "quantidade": 0})
            categoria["valor"] += r["valor"]
            categoria["quantidade"] += 1
            # O SQL só produz as 4; o que vier de fora cai em registro_antigo em vez de derrubar o mês.
            if r["origem"] not in origens:
                if r["origem"] not in _AVISADAS:
                    _AVISADAS.add(r["origem"])
                    logger.warning("mes_detalhes: origem desconhecida %r contada em registro_antigo", r["origem"])
                r = {**r, "origem": "registro_antigo"}
            origens[r["origem"]] += r["valor"]
    return {"mes": f"{ano:04d}-{mes:02d}", "ate": (fim - timedelta(days=1)).isoformat(),
            "entrou": total["entrou"], "saiu": total["saiu"],
            "categorias": sorted(categorias.values(), key=lambda c: (-c["valor"], c["categoria"] or "")),
            "origens": origens, "saiu_agendado": saiu_agendado,
            "dias": [dias[dia] for dia in sorted(dias)],
            "guardado": {**guardado, "liquido": guardado["aportes"] - guardado["saques"],
                         "cobertura": "movimentos_registrados"},
            "motivos": sorted(motivos), "fora_do_total": fora,
            "criterio_dias": "data_do_lancamento_ou_compra_na_fatura_do_mes"}


MAIORES_POR_CATEGORIA = 5


def ler_periodo(user_id: int, de: tuple[int, int], ate: tuple[int, int], agora) -> dict:
    """Os meses inteiros `de`..`ate` (AAAA, M), pela mesma regra do mês: entrou/saiu por mês
    (`TOTAIS_SQL` com o corte do plano), a soma deles e as categorias SOMADAS no período, com as
    `MAIORES_POR_CATEGORIA` maiores linhas (uma por parcela, como no total). Um snapshot só."""
    meses, a, m = [], *de
    while (a, m) <= ate:
        meses.append((a, m))
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    inicio, fim = date(*de, 1), resumo_mes.mes_de(date(*ate, 1))[1]
    corte = history_earliest_date(user_id, agora)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        por_mes = []
        for a, m in meses:
            ini_m, fim_m = resumo_mes.mes_de(date(a, m, 1))
            t = resumo_mes.totais(cur, user_id, max(ini_m, corte) if corte else ini_m, fim_m)
            por_mes.append({"mes": f"{a:04d}-{m:02d}", "entrou": t["entrou"], "saiu": t["saiu"]})
        linhas, _ = lancamentos.listar(cur, user_id, max(inicio, corte) if corte else inicio, fim, limite=None)
        motivos = set(contas(cur, user_id)["motivos"]) & set(resumo_mes.MOTIVOS)
        conn.rollback()
    if corte and corte > inicio:
        motivos.add("inicio_do_historico")
    validas, fora = _em_reais(linhas, motivos)
    categorias: dict = {}
    for r in validas:
        if r["tipo"] == "saida":
            c = categorias.setdefault(r["categoria"], {"categoria": r["categoria"], "valor": Decimal(0),
                                                       "quantidade": 0, "_linhas": []})
            c["valor"] += r["valor"]
            c["quantidade"] += 1
            c["_linhas"].append(r)
    for c in categorias.values():
        top = sorted(c.pop("_linhas"), key=lambda r: (r["valor"], r["dia"]), reverse=True)
        c["maiores"] = [lancamentos._item(r) for r in top[:MAIORES_POR_CATEGORIA]]
    return {"inicio": por_mes[0]["mes"], "fim": por_mes[-1]["mes"], "ate": (fim - timedelta(days=1)).isoformat(),
            "meses": por_mes,
            "entrou": sum((x["entrou"] for x in por_mes), Decimal(0)),
            "saiu": sum((x["saiu"] for x in por_mes), Decimal(0)),
            "categorias": sorted(categorias.values(), key=lambda c: (-c["valor"], c["categoria"] or "")),
            "motivos": sorted(motivos), "historico_desde": corte.isoformat() if corte and corte > inicio else None,
            "fora_do_total": fora}
