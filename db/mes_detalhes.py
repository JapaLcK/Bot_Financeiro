"""Agregados completos da Home sobre o leitor oficial de lançamentos do mês.

Totais mantêm TOTAIS_SQL. Categorias/dias retiram internos e moeda não BRL:
se a regra legada somou outra moeda, a divergência é explícita na cobertura.
O cartão pertence à fatura selecionada e conserva o dia original da compra.
"""
from datetime import date, timedelta
from decimal import Decimal

from core.services.plan_service import history_earliest_date

from . import lancamentos, resumo_mes
from .connection import get_conn
from .contas_hoje import listar as contas


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
        cur.execute("""select tipo in ('deposito_caixinha','aporte_investimento') as aporte,
                              upper(coalesce(currency,'BRL')) as moeda,
                              sum(valor) as total, count(*) as quantidade
                       from launches where user_id=%s and criado_em >= %s and criado_em < %s
                        and tipo in ('deposito_caixinha','aporte_investimento',
                                     'saque_caixinha','resgate_investimento')
                        group by 1, 2""",
                    (user_id, desde, fim))
        movimentos = cur.fetchall()
        conn.rollback()
    if corte and corte > inicio:
        motivos.add("inicio_do_historico")
    categorias, dias = {}, {}
    guardado = {"aportes": Decimal(0), "saques": Decimal(0)}
    fora = 0
    for r in movimentos:
        if r["moeda"] != "BRL":
            fora += r["quantidade"]
            motivos.add("outra_moeda")
        else:
            guardado["aportes" if r["aporte"] else "saques"] += r["total"]
    for r in linhas:
        if r["interno"]:
            continue
        if r["moeda"] != "BRL":
            fora += 1
            motivos.add("outra_moeda")
            continue
        motivos.update(r["motivos"])
        dia = r["dia"].isoformat()
        diario = dias.setdefault(dia, {"dia": dia, "entrou": Decimal(0), "saiu": Decimal(0)})
        diario["entrou" if r["tipo"] == "entrada" else "saiu"] += r["valor"]
        if r["tipo"] == "saida":
            categoria = categorias.setdefault(r["categoria"], {
                "categoria": r["categoria"], "valor": Decimal(0), "quantidade": 0})
            categoria["valor"] += r["valor"]
            categoria["quantidade"] += 1
    return {"mes": f"{ano:04d}-{mes:02d}", "ate": (fim - timedelta(days=1)).isoformat(),
            "entrou": total["entrou"], "saiu": total["saiu"],
            "categorias": sorted(categorias.values(), key=lambda c: (-c["valor"], c["categoria"] or "")),
            "dias": [dias[dia] for dia in sorted(dias)],
            "guardado": {**guardado, "liquido": guardado["aportes"] - guardado["saques"],
                         "cobertura": "movimentos_registrados"},
            "motivos": sorted(motivos), "fora_do_total": fora,
            "criterio_dias": "data_do_lancamento_ou_compra_na_fatura_do_mes"}
