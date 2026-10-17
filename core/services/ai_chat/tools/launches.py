"""
core/services/ai_chat/tools/launches.py — tools de lançamentos (despesas/receitas).

Read:
  - list_recent_launches: últimos N lançamentos do user (default 10)
  - get_period_summary: soma de despesas e receitas em um período (default: mês corrente)
  - get_top_categories: top N categorias de gasto no período (despesas + cartão)
  - get_category_spend: total gasto em UMA categoria específica no período
  - get_largest_expenses: top N gastos INDIVIDUAIS (não agregados) no período
  - compare_periods: receita, despesa e categorias entre períodos equivalentes (PL-07)
  - get_spending_trend: tendência mensal (totais dos últimos N meses)
  - forecast_month_end: projeção de fechamento do mês baseado no ritmo atual

Write (auto-executado, SEM confirmação):
  - add_launch: IA extrai os args, delega pra `core.handlers.launches.add_from_entities`
    (a mesma fn que o bot tradicional usa) e devolve a resposta padrão. No
    WhatsApp com WA_IA_PRIMEIRO, pede "sim" antes quando os args não batem
    com o texto do usuário (`confirmar_se`, `core/services/wa_ia_primeiro.py`).

Write (PEDE confirmação — destrutivo):
  - delete_launch: apaga um lançamento (despesa, receita ou compra no cartão).
    Bifurca entre `launches` (user_seq) e `credit_transactions` (id global).
  - delete_all_launches: apaga TODOS os lançamentos da conta corrente de uma vez,
    revertendo o saldo. Só pra pedidos explícitos de "apagar tudo".
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import db
from db import resumo_mes
from db.connection import cat_key
# Helper único das portas destrutivas, com o nível por parâmetro (a docstring
# dele lista quais portas e explica o critério). Cópia local logava ERROR onde o
# WhatsApp logava WARNING — a mesma condição contava como erro no admin por uma
# porta e não pela outra.
from core.observability import _log_falha
from core.pg_text import violacao_finito
from core.services.wa_ia_primeiro import armaria_q40, ativo, precisa_confirmar_lancamento
from utils_date import _tz, now_tz, today_tz
from utils_text import fmt_brl, valor_valido

from .. import streaming
from .._context import CATEGORIAS_NAO_ACHADAS_DO_TURNO, CURRENT_PLATFORM, CURRENT_USER_MESSAGE, JANELAS_DO_TURNO
from ..cards import MAX_CATEGORIA, MAX_MESES_PERIODO, card_ainda_resolve, mostrar_da_tool
from ._base import Tool

# A causa vai pro LOG, nunca pro usuário: o texto do psycopg pode trazer o valor
# e a descrição da linha (`DETAIL: Key (…)=(…)`), e "connection to server was
# lost" não diz nada pra quem só quer apagar um gasto. A mensagem genérica ainda
# tem de dizer o que fazer — sem isso o usuário fica sem ação.
_ERRO_APAGAR = (
    "🐷 Não consegui apagar agora — deu um erro do meu lado. "
    "Tenta de novo em alguns minutos; se continuar, me chama que eu vejo isso."
)


def _list_recent_launches(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    limit = int(args.get("limit") or 10)
    limit = max(1, min(limit, 50))

    # Saque em espécie/transferência (receita/despesa interna) vai como
    # "entrada"/"saída": como "receita", o modelo o leria como ganho.
    from core.handlers.launches import _rotulo_interno

    rows = db.list_launches(user_id, limit=limit)
    return {
        "launches": [
            {
                "id": r["id"],
                "user_seq": r.get("user_seq"),
                "tipo": _rotulo_interno(r) or r["tipo"],
                "valor": float(r["valor"] or 0),
                "alvo": r.get("alvo"),
                "nota": r.get("nota"),
                "categoria": r.get("categoria"),
                "criado_em": r["criado_em"].isoformat() if r.get("criado_em") else None,
            }
            for r in rows
        ]
    }


def _parse_iso_date(s: str | None) -> date | None:
    """AAAA-MM-DD; o modelo também manda `2026-10-01T00:00:00[Z]`, `2026-10-01 00:00:00` e espaços
    em volta: vale a parte da data. (Só as tools de `launches.py` leem daqui; `investments.py` tem cópia própria, sem isto.)"""
    if not isinstance(s, str):
        return None
    s = s.strip()
    if len(s) > 10 and s[10] in "T ":
        s = s[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _data_invalida(args: dict[str, Any], *campos: str) -> dict[str, str] | None:
    """Campo de data dado pelo modelo que não lê (`2026-09-31`) é erro, não "hoje" em silêncio."""
    c = next((c for c in campos if args.get(c) and _parse_iso_date(args[c]) is None), None)
    return {"error": f"data inválida: {c} (use AAAA-MM-DD)"} if c else None


_AVISO_12 = ("explique ao usuário que o Piggy consulta até 12 meses e que estes são os 12 mais "
             "recentes do período pedido")

_AVISO_FIM = ("diga ao usuário que o período foi até o fim do mês atual e que os meses seguintes "
              "ficaram de fora")


def _limita_periodo(start: date, end: date) -> tuple[date, date, dict[str, Any]]:
    """(início, fim, extra) com o teto de `MAX_MESES_PERIODO` meses-calendário tocados, nunca erro.
    Período que começa no passado e entra no futuro termina no fim do mês corrente ("este ano");
    mês só futuro fica como está. Fim cortado: `fim_ajustado`. Mais de 12 meses: os 12 mais
    recentes, e `extra` leva `limitado_a_12_meses`. Os dois levam o `pedido` ORIGINAL e um aviso
    para o modelo explicar."""
    hoje = today_tz()
    pedido = {"start_date": start.isoformat(), "end_date": end.isoformat()}
    fim_atual = resumo_mes.mes_de(hoje)[1] - timedelta(days=1)
    extra: dict[str, Any] = {}
    if start <= hoje and end > fim_atual:
        end = fim_atual
        extra = {"fim_ajustado": True, "pedido": pedido, "aviso_fim": _AVISO_FIM}
    corte = end.year * 12 + end.month - MAX_MESES_PERIODO  # índice (ano*12+mês-1) do 1º mês aceito
    if start.year * 12 + start.month - 1 >= corte:
        return start, end, extra
    return (date(corte // 12, corte % 12 + 1, 1), end,
            {**extra, "limitado_a_12_meses": True, "pedido": pedido, "aviso_limite": _AVISO_12})


def _meses_inteiros(start: date, end: date) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """((ano, mês) do 1º, (ano, mês) do último) quando o período são meses-calendário INTEIROS, senão
    None. Inteiro = dia 1 até o último dia do mês, ou, no mês corrente, até hoje ou depois (o default
    das tools e "esse mês"). Só aí vale a regra única do mês (cartão pela fatura, `db/resumo_mes.py`)."""
    hoje = today_tz()
    prox = resumo_mes.mes_de(end)[1]
    if start.day != 1 or end < start:
        return None
    if end == prox - timedelta(days=1) or (hoje.replace(day=1) == end.replace(day=1) and end >= hoje):
        return (start.year, start.month), (end.year, end.month)
    return None


_MSG_1_MES = {
    "categorias": 'use show_card categorias com meses=["{mes}"]',
    "resumo": (
        'use show_card resumo com meses=["{mes}"]. Vários meses (mês a mês, comparar) = UMA chamada: '
        "show_card periodo com de/ate, ou categorias com meses=[...] (até 3). "
        'Saldo ("meu saldo", "quanto tenho") = show_card contas.'
    ),
}


def _recusa_se_for_do_card(
    user_id: int, start: date, end: date, extra: dict[str, Any], card_1_mes: str = "categorias",
    categoria: str | None = None,
) -> dict[str, Any] | None:
    """No app, meses inteiros (passados ou o corrente) são cards: 1 mês = `card_1_mes` (`categorias` ou
    `resumo`, com a mensagem de `_MSG_1_MES`), 2 a 12 = `periodo` (`mes_detalhes`). Mês futuro o `show_card`
    recusa (`mes_pedido`): esse segue pela tool, senão não tem saída. `extra` é o de `_limita_periodo`: o
    período pedido passou de 12 meses. Sem saída pelo card = o dado: o turno já gastou as vagas de card
    (`MAX_POR_TURNO`) ou já mostrou o card para o qual a recusa mandaria (`card_repetido`). E 1 mês que o
    plano corta (`history_earliest_date` depois do dia 1): o card não traz o `historico_desde`, o dado sim
    (2 a 12 meses vão ao `periodo`, que traz). Também devolve o dado a conta sem acesso ao app
    (`exigir_acesso_do_app`) e o turno em que o `show_card` do card-alvo já falhou (`FALHAS_DE_CARD_DO_TURNO`): o card
    responderia erro, e a recusa não deixaria saída. `categoria` (só `get_category_spend`) vai na mensagem: o `show_card`
    a devolve em `categoria_pedida`; mas se o `show_card` deste turno já respondeu `encontrada=false` para essa
    `categoria` (`CATEGORIAS_NAO_ACHADAS_DO_TURNO`, comparada pela `cat_key`), devolve o dado: o número sai em texto, sem um 2º
    card. Qualquer outra categoria, ou nenhuma `encontrada=false`, segue a regra normal (a janela pode ser outra). A janela
    mandada ao `periodo` fica em `JANELAS_DO_TURNO` (`mostrar_card._da_recusa`)."""
    meses = _meses_inteiros(start, end)
    hoje = today_tz()
    if not (streaming.ativo() and meses and meses[1] <= (hoje.year, hoje.month)):
        return None
    if categoria and len(categoria) > MAX_CATEGORIA:  # o `show_card` a recusaria (`categoria_invalida`): o dado é a saída
        return None
    if categoria and cat_key(categoria) in map(cat_key, CATEGORIAS_NAO_ACHADAS_DO_TURNO.get() or ()):  # o card já disse que não a tem: o dado
        return None
    um = meses[0] == meses[1]
    alvo = card_1_mes if um else "periodo"
    if not card_ainda_resolve(user_id, alvo):
        return None
    avisos = "".join(f". Depois, {extra[k]}" for k in ("aviso_limite", "aviso_fim") if k in extra)
    cat = f" e categoria={json.dumps(categoria, ensure_ascii=False)}" if categoria else ""
    if um:
        from core.services.plan_service import history_earliest_date

        corte = history_earliest_date(user_id, now_tz())
        if corte and corte > start:
            return None
        return {"error": "mes_inteiro_no_card", "message": _MSG_1_MES[card_1_mes].format(mes="{:04d}-{:02d}".format(*meses[0])) + cat + avisos}
    de, ate = ("{:04d}-{:02d}".format(*m) for m in meses)
    if (janelas := JANELAS_DO_TURNO.get()) is not None:
        janelas.append({"de": de, "ate": ate, "extra": extra})
    return {"error": "periodo_no_card", "message": f"use show_card periodo com de={de} e ate={ate}{cat}{avisos}"}


def _recusa_comparar(user_id: int, ma, mb) -> dict[str, Any] | None:
    """No app, `compare_periods` de DOIS meses inteiros, diferentes e passados (`ma`/`mb` = `_meses_inteiros` de cada
    lado) vira o card `categorias` com `meses=[...]` (o card com uma aba por mês é a comparação). Fora disso o dado, como
    antes: lado de mais de um mês ou de tamanhos diferentes, mês futuro, o corrente em qualquer lado (os mesmos dias,
    F3), card que não resolve (vaga, na tela, falhou, sem acesso) e o plano que corta o histórico no mês mais antigo
    (o card não traz o `historico_desde`). Vem DEPOIS do `plan_gate_ok`: quem não tem a comparação não a ganha pelo card."""
    hoje = today_tz()
    if not (streaming.ativo() and ma and mb and ma[0] == ma[1] and mb[0] == mb[1] and ma != mb
            and max(ma[0], mb[0]) < (hoje.year, hoje.month) and card_ainda_resolve(user_id, "categorias")):
        return None
    from core.services.plan_service import history_earliest_date

    meses = sorted((ma[0], mb[0]))
    corte = history_earliest_date(user_id, now_tz())
    if corte and corte > date(*meses[0], 1):
        return None
    lista = json.dumps(["{:04d}-{:02d}".format(*m) for m in meses], separators=(",", ":"))
    return {"error": "comparar_no_card", "message": f"use show_card categorias com meses={lista}"}


def _janela(user_id: int, start: date, end: date) -> tuple[date, date, bool, dict[str, Any]]:
    """(início, fim, by_bill_month, extra) das tools de lista: em meses inteiros, o último mês TODO (o
    corrente também: a fatura que fecha depois de hoje entra, como na regra), o cartão pela
    fatura e o MESMO corte da janela do plano que `resumo_do_mes` aplica (`extra` leva
    `motivos: [inicio_do_historico]` quando o corte cai no dia 1 do mês pedido ou depois; o "mês
    anterior cortado" do resumo não tem par em lista). `extra` leva `cartao_por` pela regra da janela
    (meses inteiros: fatura; livre: data da compra); quem soma de outro jeito sobrescreve. Mês todo cortado: início > fim = lista
    vazia, e `extra` devolve as datas do mês pedido (sobrescrevem as da janela). Período livre
    não corta (limite declarado)."""
    if not _meses_inteiros(start, end):
        return start, end, False, {"cartao_por": "data_da_compra"}
    from core.services.plan_service import history_earliest_date
    from utils_date import now_tz

    end = resumo_mes.mes_de(end)[1] - timedelta(days=1)
    corte = history_earliest_date(user_id, now_tz())
    if corte and corte >= start:
        extra = {"cartao_por": "fatura", "motivos": ["inicio_do_historico"], "historico_desde": corte.isoformat() if corte > start else None}
        if corte > end:
            extra |= {"start_date": start.isoformat(), "end_date": end.isoformat()}
        return corte, end, True, extra
    return start, end, True, {"cartao_por": "fatura"}


def _resumo_periodo(user_id: int, start: date, end: date) -> dict[str, Any]:
    """Receita/despesa/saldo de um período, com o teto de 12 meses (`_limita_periodo`). Meses
    inteiros = a MESMA regra do /painel (cartão pela fatura, corte da janela do plano): 1 mês no
    `resumo_do_mes`, 2 a 12 no `ler_periodo` (o do card `periodo`). Período livre = `launches` +
    cartão pela data da compra (a regra da lista de `get_top_categories`), avisado em `nota`. Dinheiro
    sai em float (o `ai_guard` só reconhece número JSON como evidência; `Decimal` vira string e a
    afirmação certa apareceria como "sem evidência"), mas a conta é em Decimal: o saldo
    não mistura float com Decimal."""
    start, end, limite = _limita_periodo(start, end)
    if erro := _recusa_se_for_do_card(user_id, start, end, limite, "resumo"):
        return erro
    meses = _meses_inteiros(start, end)
    if meses:
        a, b = meses
        inicio, prox = date(*a, 1), resumo_mes.mes_de(date(*b, 1))[1]
        if a == b:
            r = resumo_mes.resumo_do_mes(user_id, *a)  # mês futuro também: parcelas e datas futuras já lançadas
            motivos = list(r["motivos"])
        else:
            from db.mes_detalhes import ler_periodo

            r = ler_periodo(user_id, a, b, now_tz())
            motivos = [m for m in resumo_mes.MOTIVOS if m in r["motivos"]]
        historico_desde = None  # a mesma semântica do card `periodo`: o corte quando ele cortou, senão None
        if "inicio_do_historico" in motivos:
            # o resumo também levanta o motivo quando o corte cai no mês ANTERIOR (o `anterior` dele
            # sai None); aqui o período pedido está inteiro, então só vale o corte a partir do dia 1 dele
            from core.services.plan_service import history_earliest_date

            corte = history_earliest_date(user_id, now_tz())
            if not (corte and corte >= inicio):
                motivos.remove("inicio_do_historico")
            elif corte > inicio:
                historico_desde = corte.isoformat()
        hoje = today_tz()
        futuro = b > (hoje.year, hoje.month)
        nota = (
            "mês futuro: só o que já está lançado (parcelas do cartão, lançamentos com data futura); "
            "não é previsão."
            if futuro else
            ("Mês inteiro" if a == b else "Meses inteiros")
            + ", com as compras no cartão pela fatura que fecha no mês (igual ao painel)."
        )
        if "inicio_do_historico" in motivos:  # limite do plano não é "incompleto": o aviso abaixo é só dos outros motivos
            nota += (f" Limite do plano (inicio_do_historico){f': histórico a partir de {historico_desde}' if historico_desde else ''};"
                     " antes disso não é falta de gasto.")
        nota += _aviso_motivos(motivos)
        if futuro:
            motivos.append("mes_futuro")
        return {
            "start_date": inicio.isoformat(),
            "end_date": (prox - timedelta(days=1)).isoformat(),
            "receita": float(r["entrou"]),
            "despesa": float(r["saiu"]),
            "saldo_periodo": float(r["entrou"] - r["saiu"]),
            "cartao_incluido": True,
            "cartao_por": "fatura",
            "motivos": motivos,
            "historico_desde": historico_desde,
            "nota": nota,
            **limite,
        }
    receita = Decimal(str(db.get_summary_by_period(user_id, start, end).get("receita") or 0))
    # A MESMA consulta e os mesmos argumentos de get_top_categories nesse período: total e categorias batem.
    categorias = db.get_top_expense_categories(user_id, start, end, limit=None, by_bill_month=False)
    despesa = sum((Decimal(str(c["total"])) for c in categorias), Decimal(0))
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "receita": float(receita),
        "despesa": float(despesa),
        "saldo_periodo": float(receita - despesa),
        "cartao_incluido": True,
        "cartao_por": "data_da_compra",
        "nota": (
            "Período livre: as compras no cartão entram pela data da compra (o painel de mês inteiro "
            "usa a fatura), então o número pode diferir do painel. Avise o usuário."
        ),
        **limite,
    }


def _get_period_summary(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    if erro := _data_invalida(args, "start_date", "end_date"):
        return erro
    today = date.today()
    start = _parse_iso_date(args.get("start_date")) or today.replace(day=1)
    end = _parse_iso_date(args.get("end_date")) or today

    if end < start:
        return {"error": "end_date anterior a start_date"}

    return _resumo_periodo(user_id, start, end)


def _texto_parcelamento(r: dict[str, Any]) -> str | None:
    """Texto pronto: o modelo confundia `parcela` "1/3" (valor da parcela) com compra em 3x."""
    if r.get("parcelas"):
        return f"compra de {fmt_brl(r['valor'])} em {r['parcelas']}x"
    if r.get("parcela"):
        n, total = r["parcela"].split("/")
        return f"parcela {n} de {total} — {fmt_brl(r['valor'])} neste período"
    return None


def _get_largest_expenses(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    if erro := _data_invalida(args, "start_date", "end_date"):
        return erro
    today = date.today()
    start = _parse_iso_date(args.get("start_date")) or today.replace(day=1)
    end = _parse_iso_date(args.get("end_date")) or today

    if end < start:
        return {"error": "end_date anterior a start_date"}

    try:
        limit = int(args.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 20))

    categoria = (args.get("categoria") or "").strip() or None

    start, end, limite = _limita_periodo(start, end)
    start, end, por_fatura, extra = _janela(user_id, start, end)
    rows = db.get_largest_expenses(
        user_id, start, end, limit, categoria=categoria, by_bill_month=por_fatura, juntar_parcelas=True,
    )
    for r in rows:
        r["parcelamento"] = _texto_parcelamento(r)
        # só o texto pronto vai à IA: os campos crus foram mal lidos ("parcelado em 3x" numa parcela OF)
        r.pop("parcela", None)
        r.pop("parcelas", None)
    resultado = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "categoria": categoria,
        "expenses": rows,
        "count": len(rows),
        **limite,
        **extra,
    }
    if rows and mostrar_da_tool(user_id, "maiores_gastos", resultado):  # o card é este mesmo resultado
        return _resumo_maiores(resultado)
    return resultado


def _resumo_maiores(r: dict[str, Any]) -> dict[str, Any]:
    """O que o MODELO vê quando o card `maiores_gastos` está na tela: a lista está lá, e o modelo que a vê inteira a copia
    (como `cards._RESUMOS`). Dinheiro em float sob chave `total_*`/`valor*` (o `_CHAVE_MONETARIA_RE` do `ai_guard` casa `^total_`)."""
    rows = r["expenses"]
    return {
        **{k: v for k, v in r.items() if k != "expenses"},
        # soma só dos `count` itens listados, NÃO o gasto do período (chave `total_*`: o `ai_guard` a reconhece como dinheiro)
        "total_dos_itens": float(sum((Decimal(str(x["valor"])) for x in rows), Decimal(0))),
        "maior": max(rows, key=lambda x: x["valor"]),
        "categorias": list(dict.fromkeys(x["categoria"] for x in rows if x.get("categoria")))[:3],
        "card_na_tela": True,
    }


def _compare_periods(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    """Adaptador de `core.services.comparacao_periodos` (PL-07): `mes` OU as 4 datas."""
    from core.services import comparacao_periodos as cp
    from core.services.plan_service import plan_gate_ok
    recusa = {"error": "pro_required", "message": "Comparações entre períodos estão disponíveis nos planos Plus e Pro."}
    if not plan_gate_ok(user_id, "financial_comparison"):
        return recusa
    if erro := _data_invalida(args, "period_a_start", "period_a_end", "period_b_start", "period_b_end"):
        return erro
    a_ini, a_fim, b_ini, b_fim = (_parse_iso_date(args.get(k)) for k in (
        "period_a_start", "period_a_end", "period_b_start", "period_b_end"))
    try:
        if args.get("mes"):
            try:
                m = datetime.strptime(str(args["mes"]), "%Y-%m")
            except ValueError:
                return {"error": "mes deve vir como AAAA-MM."}
            i = m.year * 12 + m.month - 1  # M e o anterior, os dois lados do `comparar_mes`
            if erro := _recusa_comparar(user_id, *(((j // 12, j % 12 + 1),) * 2 for j in (i, i - 1))):
                return erro
            r = cp.comparar_mes(user_id, m.year, m.month)
        elif a_ini and a_fim and b_ini and b_fim:
            # o mesmo teto de 12 meses e fim ajustado das outras tools; os avisos vão no lado que foi cortado
            (a_ini, a_fim, ex_a), (b_ini, b_fim, ex_b) = _limita_periodo(a_ini, a_fim), _limita_periodo(b_ini, b_fim)
            # as duas janelas são meses inteiros (`_meses_inteiros`, como `_resumo_periodo`): cartão pela fatura e o
            # mês TODO, o corrente também; senão, data da compra nas duas, sem misturar
            meses = (_meses_inteiros(a_ini, a_fim), _meses_inteiros(b_ini, b_fim))
            if erro := _recusa_comparar(user_id, *meses):
                return erro
            hoje = today_tz()
            corrente_x_anterior = tuple((m[0] == m[1] == (d.year, d.month)) if m else False for m, d in zip(
                meses, (hoje.replace(day=1) - timedelta(days=1), hoje)))
            if all(corrente_x_anterior):  # "outubro contra setembro" = `mes=<corrente>`: os mesmos N dias, o mesmo número
                r = cp.comparar_mes(user_id, *meses[1][0])
            else:
                if all(meses):
                    (a_ini, a_fim), (b_ini, b_fim) = ((date(*m[0], 1), resumo_mes.mes_de(date(*m[1], 1))[1] - timedelta(days=1))
                                                     for m in meses)
                r = cp.comparar_intervalos(user_id, (b_ini, b_fim), (a_ini, a_fim), por_fatura=all(meses))
            if r:
                r["anterior"].update(ex_a)
                r["atual"].update(ex_b)
        else:
            return {"error": "informe mes (AAAA-MM) ou period_a_start, period_a_end, period_b_start, period_b_end em ISO (YYYY-MM-DD)."}
    except ValueError as exc:
        return {"error": str(exc)}
    return recusa if r is None else cp.para_json(r)


def _tendencia(despesas: list[float]) -> dict[str, Any]:
    """Último mês vs média dos anteriores: +/-10% = subindo/descendo, senão estável.
    Pura: serve ao `get_spending_trend` e ao card `renda` (mesmas linhas, `cards.py`)."""
    out = {"tendencia_despesa": "estavel", "variacao_pct": 0.0}
    if len(despesas) >= 2:
        media_ant = sum(despesas[:-1]) / (len(despesas) - 1)
        if media_ant > 0:
            var = ((despesas[-1] - media_ant) / media_ant) * 100
            out["variacao_pct"] = round(var, 1)
            if var > 10:
                out["tendencia_despesa"] = "subindo"
            elif var < -10:
                out["tendencia_despesa"] = "descendo"
    return out


def _get_spending_trend(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    from core.services.plan_service import plan_gate_ok
    if not plan_gate_ok(user_id, "financial_comparison"):
        return {"error": "pro_required", "message": "Tendências e comparações estão disponíveis nos planos Plus e Pro."}
    try:
        months = int(args.get("months") or 6)
    except (TypeError, ValueError):
        months = 6
    limite = {}
    if months > MAX_MESES_PERIODO:
        limite = {"limitado_a_12_meses": True, "pedido": {"months": months}, "aviso_limite": _AVISO_12}
    months = max(1, min(months, MAX_MESES_PERIODO))

    rows = db.get_spending_trend(user_id, months)

    # Calcula análise: média, saldo médio, tendência (subindo/descendo/estável).
    # Tendência olha o ÚLTIMO mês vs média dos anteriores — se variou +/-10%
    # marca como subindo/descendo, senão estável.
    summary = {
        "media_despesa": 0.0,
        "media_receita": 0.0,
        "saldo_medio": 0.0,
        **_tendencia([r["despesa"] for r in rows]),
    }
    if rows:
        total_d = sum(r["despesa"] for r in rows)
        total_r = sum(r["receita"] for r in rows)
        n = len(rows)
        summary["media_despesa"] = round(total_d / n, 2)
        summary["media_receita"] = round(total_r / n, 2)
        summary["saldo_medio"] = round((total_r - total_d) / n, 2)

    return {
        "months": months,
        "data": rows,  # list of {year, month, despesa, receita}
        "summary": summary,
        **limite,
    }


def _aviso_motivos(motivos) -> str:
    """O aviso de número incompleto: o limite do plano (`inicio_do_historico`) não conta, tem nota própria."""
    outros = [m for m in motivos if m != "inicio_do_historico"]
    return f" Número pode estar desatualizado ou incompleto ({', '.join(outros)}); avise o usuário." if outros else ""


def _projetar_mes(m: dict[str, Any], dias_corridos: int, dias_no_mes: int) -> dict[str, Any]:
    """A conta do fim do mês, pura: `m` é o `mes_detalhes.ler` do mês corrente. Decimal por dentro, float na saída."""
    entrou, saiu, saiu_cartao = m["entrou"], m["saiu"], m["origens"]["cartao"]
    # Lançamento fora do cartão com data depois de hoje é valor conhecido: entra uma vez, não esticado pelo
    # ritmo (como a fatura). O leitor soma só as linhas que já alimentam `saiu`, sem o cartão.
    saiu_agendado = m["saiu_agendado"]
    saiu_no_ritmo = saiu - saiu_cartao - saiu_agendado
    saiu_projetado = saiu_no_ritmo * dias_no_mes / dias_corridos + saiu_cartao + saiu_agendado
    saldo_projetado = round(entrou - saiu_projetado, 2) + 0  # `+ 0` tira o -0.0 (Decimal('-0.00') + 0 = 0.00)
    d = lambda x: float(round(x, 2))  # noqa: E731
    return {
        "receita": d(entrou),
        "despesa": d(saiu),
        "saldo_ate_agora": d(entrou - saiu),
        "despesa_cartao": d(saiu_cartao),
        "despesa_agendada": d(saiu_agendado),
        "despesa_no_ritmo": d(saiu_no_ritmo),
        "despesa_projetada": d(saiu_projetado),
        "saldo_projetado": d(saldo_projetado),
        "vai_fechar_negativo": saldo_projetado < 0,  # sobre o valor já arredondado: nunca -0.0 com True
    }


def _forecast_month_end(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    """Projeção do fim do mês corrente, lendo o mês pelo MESMO leitor do card `categorias`
    (`mes_detalhes.ler`): receita e despesa até agora são as do painel (pagamento de fatura, transferência
    e guardar ficam fora). Só o cartão do Open Finance entra como está na fatura (`origens["cartao"]`); o
    resto, inclusive cartão manual, é extrapolado pelo ritmo diário, salvo o lançamento com data futura
    (`despesa_agendada`, constante); a receita não é projetada. Conta em
    Decimal, sai float. As chaves seguem o padrão `receita`/`despesa*`/`saldo*` porque é o que o `ai_guard`
    (`_CHAVE_MONETARIA_RE`) reconhece como dinheiro."""
    from calendar import monthrange
    from core.services.plan_service import plans_v2_enabled, plan_gate_ok
    if plans_v2_enabled() and not plan_gate_ok(user_id, "forecast"):
        return {"error": "pro_required", "message": "A projeção de fechamento está disponível nos planos Plus e Pro."}

    from db.mes_detalhes import ler

    agora = now_tz()
    hoje = agora.date()
    m = ler(user_id, hoje.year, hoje.month, agora)
    dias_no_mes = monthrange(hoje.year, hoje.month)[1]
    nota = (
        "`receita` e `despesa` são as do painel do mês (pagamento de fatura, transferência e guardar ficam "
        "fora). Só as compras do cartão conectado pelo Open Finance entram como estão na fatura que fecha no "
        "mês (`despesa_cartao`); o resto do gasto até hoje, inclusive cartão lançado à mão, é projetado pelo ritmo "
        "diário (`despesa_no_ritmo`); lançamento fora do cartão com data depois de hoje, no mês, também entra "
        "como está (`despesa_agendada`), sem esticar; receita com data futura no mês já está em `receita`, "
        "como no painel, e não é projetada."
    )
    nota += _aviso_motivos(m["motivos"])
    resultado = {
        "mes": m["mes"],
        "hoje": hoje.isoformat(),
        "dias_corridos": hoje.day,
        "dias_no_mes": dias_no_mes,
        **_projetar_mes(m, hoje.day, dias_no_mes),
        "cartao_incluido": True,
        "cartao_por": "fatura",
        "motivos": m["motivos"],
        "nota_metodologia": nota,
    }
    # O card é este mesmo resultado (uma só conta); a nota é para o modelo, não para a tela.
    # Mês sem lançamento nenhum: tudo zerado não vira card (cara de sucesso); o modelo recebe o resultado normal.
    if (resultado["receita"] or resultado["despesa"]) and mostrar_da_tool(
            user_id, "fim_do_mes", {k: v for k, v in resultado.items() if k not in ("nota_metodologia", "cartao_incluido")}):
        resultado["card_na_tela"] = True
    return resultado


def _get_top_categories(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    if erro := _data_invalida(args, "start_date", "end_date"):
        return erro
    today = date.today()
    start = _parse_iso_date(args.get("start_date")) or today.replace(day=1)
    end = _parse_iso_date(args.get("end_date")) or today

    if end < start:
        return {"error": "end_date anterior a start_date"}

    try:
        limit = int(args.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 20))

    start, end, limite = _limita_periodo(start, end)
    if erro := _recusa_se_for_do_card(user_id, start, end, limite):
        return erro

    start, end, por_fatura, extra = _janela(user_id, start, end)
    rows = db.get_top_expense_categories(user_id, start, end, limit, by_bill_month=por_fatura)
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "categories": rows,
        "count": len(rows),
        **limite,
        **extra,
    }


def _get_category_spend(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    if erro := _data_invalida(args, "start_date", "end_date"):
        return erro
    today = date.today()
    start = _parse_iso_date(args.get("start_date")) or today.replace(day=1)
    end = _parse_iso_date(args.get("end_date")) or today

    if end < start:
        return {"error": "end_date anterior a start_date"}

    categoria = (args.get("categoria") or "").strip()
    if not categoria:
        return {"error": "categoria obrigatória"}

    start, end, limite = _limita_periodo(start, end)
    if erro := _recusa_se_for_do_card(user_id, start, end, limite, categoria=categoria):
        return erro

    start, end, _, extra = _janela(user_id, start, end)  # o cartão já é pela fatura; muda a janela
    total = db.sum_spent_in_category_period(user_id, categoria, start, end)
    return {
        "categoria": categoria,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total": total,
        **limite,
        **extra,
        "cartao_por": "fatura",  # sum_spent_in_category_period soma o cartão sempre pela fatura, até em período livre
    }


# ─── Write: add_launch ──────────────────────────────────────────────────────

_TIPOS_VALIDOS = ("despesa", "receita")


def _parse_iso_datetime_for_launch(s: str | None) -> datetime | None:
    """YYYY-MM-DD → datetime ao meio-dia no tz local (evita edge cases com DST)."""
    if not s:
        return None
    try:
        d = date.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    return datetime.combine(d, time(12, 0), tzinfo=_tz())


def _add_launch_execute(user_id: int, args: dict[str, Any]) -> str:
    """IA já extraiu os args; aqui só valida e delega pra fonte única de
    verdade (`add_from_entities` no handler). Toda lógica de categorização,
    learn, budget alert e formato de resposta vive lá."""
    tipo = (args.get("tipo") or "").strip().lower()
    if tipo not in _TIPOS_VALIDOS:
        return "🐷 Tipo inválido — precisa ser despesa ou receita."

    try:
        valor = float(args.get("valor") or 0)
    except (TypeError, ValueError, OverflowError):
        return "🐷 Valor inválido."
    if valor <= 0:
        return "🐷 O valor precisa ser maior que zero."
    if not valor_valido(valor):
        return "🐷 Valor inválido."

    from core.handlers import forma_pagamento as fp
    from core.handlers.launches import add_from_entities

    forma = forma_declarada(args)
    decisao = fp.decidir(user_id, forma)
    if decisao == fp.BANCO:
        return fp.msg_banco(user_id, tipo, valor)
    ents = dict(
        tipo=tipo,
        valor=valor,
        alvo=(args.get("alvo") or "").strip() or None,
        nota=(args.get("nota") or "").strip() or None,
        categoria=(args.get("categoria") or "").strip() or None,
        # Hashtag fixada pelo código na confirmação: o cross-check com a
        # regra local não a troca ("explicit", não "ai").
        category_reason=(args.get("_category_reason")
                         or ("explicit" if args.get("_categoria_explicita") else "ai")),
        criado_em=_parse_iso_datetime_for_launch(args.get("data")),
    )
    platform = CURRENT_PLATFORM.get()
    if decisao != fp.CARTEIRA:
        if platform != "whatsapp" or not ativo(user_id):
            return _PERGUNTE_A_FORMA
        # WA_IA_PRIMEIRO: a pergunta vai ao usuário (Q40, como o recibo da
        # imagem em `core/handlers/pending.py`); a resposta "dinheiro" grava
        # pelo `forma_pagamento.resolver`, fluxo "entities".
        ents["criado_em"] = ents["criado_em"].isoformat() if ents["criado_em"] else None
        return fp.perguntar(
            user_id, {"fluxo": "entities", "entities": ents, "platform": platform},
            fp.pergunta_lancamento(tipo, valor))

    return add_from_entities(user_id, **ents, platform=platform, forma_pagamento=forma)


def forma_declarada(args: dict[str, Any]) -> str:
    """Q40: a tool só DECLARA a forma; quem decide se grava é o servidor. Fora
    do enum vira "desconhecida" — o modelo não inventa uma terceira forma."""
    from core.handlers import forma_pagamento as fp
    forma = args.get("forma_pagamento")
    return forma if forma in (fp.DINHEIRO, fp.BANCO) else fp.DESCONHECIDA


def _dia_efetivo(args: dict[str, Any]):
    """O dia que a gravação usa: o de `_parse_iso_datetime_for_launch(data)` no
    `_tz()`, ou hoje quando `data` falta/não se lê. Fonte única do resumo e do
    congelamento da pendência."""
    from utils_date import today_tz
    dia = _parse_iso_datetime_for_launch(args.get("data"))
    return dia.astimezone(_tz()).date() if dia else today_tz()


def _congela_pendencia(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    """`ao_confirmar` do add_launch: o "sim" grava o que a pergunta mostrou.
    (1) O dia efetivo vira `data` — um "sim" depois da meia-noite não grava no
    dia novo. (2) Com hashtag no texto do usuário, a pendência guarda a
    categoria dela, canonizada pela mesma chamada que a gravação faz, e a marca
    de proveniência (do código; o runner descarta chave "_" vinda do modelo)."""
    from core.services.category_service import infer_category
    from parsers import _extract_explicit_category
    args = {**args, "data": _dia_efetivo(args).isoformat()}
    _, hashtag = _extract_explicit_category(CURRENT_USER_MESSAGE.get())
    if hashtag:
        return {**args, "categoria": infer_category(user_id, "", hashtag).category,
                "_categoria_explicita": True}
    # Sem hashtag: a categoria que a execução VAI usar (o cross-check do
    # `add_from_entities`, mesma função) e o motivo dela, para o "sim" gravar
    # o que o resumo mostra e aprender como hoje.
    categoria = str(args.get("categoria") or "").strip()
    if not categoria:
        return args          # a execução infere (com IA); o resumo não mostra categoria
    from core.handlers.launches import resolver_categoria_da_ia
    nota = str(args.get("nota") or "").strip() or str(args.get("alvo") or "").strip()
    categoria, motivo = resolver_categoria_da_ia(user_id, categoria, nota)
    return {**args, "categoria": categoria, "_category_reason": motivo}


def _add_launch_summary(args: dict[str, Any]) -> str:
    """Tudo o que a gravação vai usar e o usuário precisa ver para recusar:
    tipo, valor, alvo, nota, categoria, o DIA efetivo (o do mesmo parser da
    gravação; ausente/ilegível = hoje) e a forma, se veio."""
    from core.handlers import forma_pagamento as fp
    from core.handlers.launches import _fmt_date_label
    try:
        valor = fmt_brl(float(args.get("valor") or 0))
    except (TypeError, ValueError):
        valor = str(args.get("valor"))
    # str(): o modelo pode mandar número ou lista; o resumo não pode levantar.
    partes = [f"{str(args.get('tipo') or 'despesa').strip().lower()} de {valor}"]
    alvo = str(args.get("alvo") or "").strip()
    nota = str(args.get("nota") or "").strip()
    if alvo or nota:
        partes.append(f"em {alvo or nota}")
    if alvo and nota and nota != alvo:
        partes.append(f"({nota})")
    categoria = str(args.get("categoria") or "").strip()
    if categoria:
        partes.append(f"#{categoria}")
    resumo = [" ".join(partes), _fmt_date_label(_dia_efetivo(args))]
    forma = forma_declarada(args)
    if forma == fp.DINHEIRO:
        resumo.append("em dinheiro")
    elif forma == fp.BANCO:
        resumo.append("pelo banco")
    return ", ".join(resumo)


# Instrução ao MODELO (volta como resultado da tool). Não arma pendência: a
# pergunta aberta da IA (#598) leva a resposta do usuário de volta para ela.
_PERGUNTE_A_FORMA = (
    "🐷 Nada foi gravado. Pergunte ao usuário se foi em dinheiro vivo ou pelo "
    "banco (Pix, cartão, débito) e chame de novo com `forma_pagamento`."
)


# ─── Write: delete_launch (PEDE confirmação — destrutivo) ───────────────────

def _delete_launch_summary(args: dict[str, Any]) -> str:
    lid = args.get("launch_id")
    return f'apagar o lançamento #{lid}' if lid else "apagar lançamento"


def _try_int(s: str) -> int | None:
    try:
        n = int(s)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _delete_launch_validate(user_id: int, args: dict[str, Any]) -> str | None:
    """Roda ANTES da confirmação. Se o ID nem existe, mostra erro imediato
    em vez de pedir 'confirma apagar #X?' enganoso. Crítico porque o LLM
    às vezes inventa IDs quando o user diz 'aquele' sem especificar.

    Aceita 3 formas:
      1. user_seq de launches (#5, #142) — número pequeno
      2. id de credit_transaction (numérico, mais alto)
      3. código de parcelamento (PCxxxxxxxx, ex: PC81524273)
    """
    raw_id = str(args.get("launch_id") or "").strip()
    if not raw_id:
        return "🐷 Faltou o ID do lançamento."

    lid = _try_int(raw_id)
    if lid is not None:
        # user_seq de launches?
        if db.resolve_user_seq_to_id(user_id, lid):
            return None
        # id de credit_transaction?
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select 1 from credit_transactions where user_id=%s and id=%s",
                    (user_id, lid),
                )
                if cur.fetchone():
                    return None

    # Código de parcelamento (PCxxxxxxxx ou só os hex do início do group_id)
    if db.resolve_installment_group_id(user_id, raw_id):
        return None

    return (
        f"🐷 Não achei o lançamento '{raw_id}'. "
        f"Manda o ID exato (aparece no histórico como #N, ou o código do "
        f"parcelamento tipo PCxxxxxxxx) ou pede pra eu listar os últimos lançamentos."
    )


def _delete_launch_execute(user_id: int, args: dict[str, Any]) -> str:
    """Apaga um lançamento. Tenta na ordem:
      1. user_seq de `launches` (cenário comum: "#5") → delete_launch_and_rollback
      2. id global de credit_transactions → undo_credit_transaction (auto desfaz
         grupo inteiro se for parcelado)
      3. código de parcelamento (PCxxxxxxxx) → undo_installment_group
    """
    raw_id = str(args.get("launch_id") or "").strip()
    if not raw_id:
        return "🐷 Faltou o ID do lançamento."

    lid = _try_int(raw_id)
    if lid is not None:
        # 1. user_seq de launches
        internal_id = db.resolve_user_seq_to_id(user_id, lid)
        if internal_id:
            try:
                aviso = db.delete_launch_and_rollback(user_id, internal_id)
                if aviso:  # apagou a fundida: o banco volta para a lista (P3)
                    return f"🗑️ Lançamento #{lid} apagado. {aviso}"
                return f"🗑️ Lançamento #{lid} apagado. Saldo revertido."
            except LookupError:
                return f"🐷 Não achei o lançamento #{lid}."
            except db.LaunchNoEffects as e:
                # Lançamento sem `efeitos`: condição PERMANENTE, não erro
                # técnico. `_ERRO_APAGAR` manda tentar de novo em alguns
                # minutos — conselho que nunca ia funcionar aqui. Mesma
                # distinção do `delete_all_launches` (`kept_no_effects` ×
                # `errors`).
                _log_falha("delete_launch_sem_efeitos", user_id, e,
                           nivel=logging.WARNING, launch_id=internal_id, user_seq=lid)
                return (
                    f"🐷 O lançamento #{lid} é antigo e não guarda o que precisaria "
                    f"ser revertido, então mantive ele intacto pra não bagunçar seu saldo."
                )
            except db.InvestmentMovementNotLast as e:
                # Antes do `LaunchUnsafeRollback` (subclasse); mesma frase do WhatsApp.
                _log_falha("delete_launch_movimento_posterior", user_id, e,
                           nivel=logging.WARNING, launch_id=internal_id, user_seq=lid)
                return f"🐷 Não apaguei o lançamento #{lid}. {e}"
            except db.PocketHasMovement as e:
                # Idem: subclasse, antes da mãe; mesma frase do WhatsApp.
                _log_falha("delete_launch_caixinha_com_movimento", user_id, e,
                           nivel=logging.WARNING, launch_id=internal_id, user_seq=lid)
                return f"🐷 Não apaguei o lançamento #{lid}. {e}"
            except db.LaunchUnsafeRollback as e:
                # `efeitos` existe mas não dá pra revertê-lo por inteiro —
                # mesma condição PERMANENTE do WhatsApp (`core/handlers/
                # pending.py`), mesma frase.
                _log_falha("delete_launch_inseguro", user_id, e,
                           nivel=logging.WARNING, launch_id=internal_id, user_seq=lid)
                return (
                    f"🐷 Não consigo reverter o lançamento #{lid} com segurança, "
                    f"então mantive ele intacto pra não bagunçar seu saldo."
                )
            except db.InvestmentLotHasWithdrawal as e:
                # TEMPORÁRIA e com contorno: apagar o resgate reabre o lote.
                # Nem `_ERRO_APAGAR` (retry que nunca funciona) nem "é antigo"
                # (o dado está inteiro) servem aqui.
                _log_falha("delete_launch_lote_com_resgate", user_id, e,
                           nivel=logging.WARNING, launch_id=internal_id, user_seq=lid)
                return (
                    f"🐷 Não dá pra desfazer o aporte #{lid}: esse lote já teve "
                    f"resgate. Apaga o resgate primeiro e depois volta aqui pra "
                    f"apagar o aporte."
                )
            except Exception as e:
                if violacao_finito(e):
                    raise  # runner responde MSG_VALOR_WHATSAPP
                _log_falha("delete_launch", user_id, e, launch_id=internal_id, user_seq=lid)
                return _ERRO_APAGAR

        # 2. id de credit_transaction
        try:
            result = db.undo_credit_transaction(user_id, lid)
        except db.CompraDoBanco as e:
            return f"🐷 {e}"
        except Exception as e:
            _log_falha("undo_credit_transaction", user_id, e, credit_tx_id=lid)
            return _ERRO_APAGAR
        if result is not None:
            removed = int(result.get("removed_count") or 1)
            if removed > 1:
                return f"🗑️ Parcelamento apagado ({removed} parcelas)."
            return f"🗑️ Compra no crédito #{lid} apagada."

    # 3. Código de parcelamento (PCxxxxxxxx)
    group_id = db.resolve_installment_group_id(user_id, raw_id)
    if group_id:
        try:
            result = db.undo_installment_group(user_id, group_id)
        except db.CompraDoBanco as e:
            return f"🐷 {e}"
        except Exception as e:
            _log_falha("undo_installment_group", user_id, e, group_id=group_id)
            return _ERRO_APAGAR
        if result:
            removed = int(result.get("removed_count") or 0)
            return f"🗑️ Parcelamento {raw_id.upper()} apagado ({removed} parcelas)."

    return f"🐷 Não achei o lançamento '{raw_id}'."


# ─── Write: delete_all_launches (PEDE confirmação — destrutivo em massa) ─────

def _delete_all_launches_summary(args: dict[str, Any]) -> str:
    return (
        "apagar todas as despesas e receitas da conta corrente (e desfazer "
        "pagamentos de fatura), revertendo o saldo da conta. Suas caixinhas e "
        "investimentos NÃO são afetados"
    )


def _delete_all_launches_validate(user_id: int, args: dict[str, Any]) -> str | None:
    """Bloqueia a confirmação se não há nada pra apagar — evita um 'confirma
    apagar tudo?' inútil quando o histórico já está vazio."""
    try:
        n = db.count_launches(user_id)
    except Exception:
        return None  # deixa seguir; o execute lida com lista vazia
    if n <= 0:
        return "🐷 Você não tem nenhum lançamento pra apagar — seu histórico já está limpo."
    return None


def _amostra_ids(ids: list) -> str:
    """Amostra de 5 em user_seq (o #N que o user vê), com cauda só quando sobra:
    7 ids → '#3, #7, #9, #11, #12 e mais 2'; 3 ids → '#3, #7, #9' (sem cauda).
    Exemplo medido chamando a função — o anterior ('#3, #7, #9 e mais 4') não
    era produzível: com 3 ids `resto` dá -2 e a cauda nem aparece."""
    resto = len(ids) - 5
    amostra = ", ".join(f"#{i}" for i in ids[:5])
    return f"{amostra} e mais {resto}" if resto > 0 else amostra


def _delete_all_launches_execute(user_id: int, args: dict[str, Any]) -> str:
    try:
        result = db.delete_all_launches_and_rollback(user_id)
    except Exception as e:
        if violacao_finito(e):
            raise  # runner responde MSG_VALOR_WHATSAPP
        _log_falha("delete_all_launches", user_id, e)
        return _ERRO_APAGAR

    deleted = int(result.get("deleted") or 0)
    antigos = result.get("kept_no_effects") or []
    inseguros = result.get("kept_unsafe") or []
    erros = result.get("errors") or []
    restauradas = int(result.get("restored") or 0)  # sombras do banco devolvidas: contam no `remaining`
    # None = a recontagem falhou ("não conferi"), NÃO "sobrou zero".
    sobraram = result.get("remaining")

    # "não havia nada" SÓ quando não havia mesmo: com linha contada antes,
    # dizer isso é fingir sucesso.
    if not deleted and not antigos and not inseguros and not erros and not sobraram:
        return "🐷 Não havia nenhum lançamento pra apagar."

    partes = []
    if deleted:
        plural = "s" if deleted != 1 else ""
        partes.append(
            f"🗑️ Apaguei {deleted} lançamento{plural} da conta corrente e reverti o "
            f"saldo. Suas caixinhas e investimentos seguem intactos."
        )
    if restauradas:
        p = "ões" if restauradas != 1 else "ão"
        partes.append(f"🏦 {restauradas} transaç{p} do banco continua{'m' if restauradas != 1 else ''} na sua lista, "
                      "porque aconteceu de verdade.")
    # Frases distintas: "antigo demais pra reverter", "não sei reverter" e
    # "erro técnico" são causas diferentes, e uma queda de banco não pode sair
    # como lançamento antigo.
    if antigos:
        p = "s" if len(antigos) != 1 else ""
        partes.append(
            f"⚠️ {len(antigos)} lançamento{p} antigo{p} não guarda o que precisaria ser "
            f"revertido, então mantive intacto pra não bagunçar seu saldo: "
            f"{_amostra_ids(antigos)}."
        )
    # Terceira causa: o `efeitos` existe, mas não sei revertê-lo por inteiro.
    # UMA frase só — a sub-causa (chave desconhecida × degenerado × lote
    # ausente × fora do escopo) vai pro log do `delete_all_launches` como
    # CÓDIGO ENUMERADO (`motivo=`, atributo da exceção — não a mensagem dela),
    # e o usuário não pode agir sobre a diferença.
    if inseguros:
        p = "s" if len(inseguros) != 1 else ""
        partes.append(
            f"⚠️ Mantive {len(inseguros)} lançamento{p} que não consigo reverter "
            f"com segurança: {_amostra_ids(inseguros)}."
        )
    if erros:
        p = "s" if len(erros) != 1 else ""
        partes.append(
            f"❌ {len(erros)} lançamento{p} deu erro técnico e continua aí: "
            f"{_amostra_ids(erros)}. Já registrei a falha — tenta de novo em alguns minutos."
        )
    # `remaining` é conferência, não fato novo: quando bate com o que as três
    # listas já disseram, repetir "sobrou N" só duplica a mensagem. Fala apenas
    # quando DISCORDA — que é o caso que a recontagem existe pra pegar (delete
    # casou zero linhas e ninguém levantou). `None` não discorda de nada.
    esperado = len(antigos) + len(inseguros) + len(erros) + restauradas
    if sobraram is not None and sobraram != esperado:
        p = "s" if sobraram != 1 else ""
        partes.append(
            f"⚠️ A conferência não bateu: ainda tem {sobraram} lançamento{p} no "
            f"histórico, e eu esperava {esperado}. Dá uma olhada no seu extrato."
        )
    return "\n".join(partes)


TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "list_recent_launches",
                "description": "Retorna os lançamentos (despesas e receitas) mais recentes do usuário, em ordem cronológica decrescente. Use pra perguntas tipo 'meus últimos gastos', 'o que registrei hoje', 'mostra os lançamentos'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50,
                            "default": 10,
                            "description": "Quantos lançamentos retornar (1 a 50, padrão 10).",
                        },
                    },
                },
            },
        },
        is_write=False,
        execute=_list_recent_launches,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_period_summary",
                "description": "Retorna o resumo financeiro de um período: total de receitas e despesas reais (NÃO inclui aportes de investimento — pra aportes, use `get_investment_contributions`). Use pra 'quanto gastei esse mês?', 'meu resumo de abril', 'quanto recebi em maio'. Aceita até 12 meses (mais que isso vem cortado nos 12 mais recentes, com `limitado_a_12_meses`: explique o limite). Meses-calendário inteiros (dia 1 até o último dia, ou até hoje no mês corrente; um mês ou vários) = a regra do painel: cartão pela fatura de cada mês (`cartao_por: fatura`). Outro período (um dia, uma semana, 10/03 a 25/04) é período livre: o cartão entra pela data da compra (`cartao_por: data_da_compra`) e a `nota` pede que você avise o usuário.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_date": {
                            "type": "string",
                            "description": "Data inicial AAAA-MM-DD. Sempre passe ('este ano' = 1º de janeiro até hoje; 'esse mês' = dia 1 até hoje).",
                        },
                        "end_date": {
                            "type": "string",
                            "description": "Data final AAAA-MM-DD, inclusiva. Sempre passe.",
                        },
                    },
                    "required": ["start_date", "end_date"],
                },
            },
        },
        is_write=False,
        execute=_get_period_summary,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_top_categories",
                "description": (
                    "Retorna as maiores categorias de GASTO no período "
                    "(soma despesas reais + compras no cartão, exclui "
                    "movimentações internas e reembolsos). Use pra 'onde "
                    "gastei mais?', 'quais minhas maiores categorias?', "
                    "'em que torrei mais dinheiro?', 'top 3 categorias do "
                    "mês'. Período de meses "
                    "inteiros conta o mês todo com o cartão pela fatura (igual ao painel). Até 12 meses."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_date": {
                            "type": "string",
                            "description": "Data inicial AAAA-MM-DD. Sempre passe ('este ano' = 1º de janeiro até hoje; 'esse mês' = dia 1 até hoje).",
                        },
                        "end_date": {
                            "type": "string",
                            "description": "Data final AAAA-MM-DD, inclusiva. Sempre passe.",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "default": 5,
                            "description": "Top N categorias (1 a 20, padrão 5).",
                        },
                    },
                    "required": ["start_date", "end_date"],
                },
            },
        },
        is_write=False,
        execute=_get_top_categories,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_category_spend",
                "description": (
                    "Retorna quanto o user gastou em UMA categoria específica "
                    "num período (soma despesas reais + compras no cartão, "
                    "exclui movimentações internas e reembolsos). Use quando o "
                    "user nomeia a categoria: 'quanto gastei na categoria "
                    "outros esta semana?', 'quanto torrei em mercado em julho?', "
                    "'gastei quanto com transporte esse mês?'. Pra ranking de "
                    "várias categorias use `get_top_categories`. Período que é um mês inteiro conta o mês "
                    "todo com o cartão pela fatura (igual ao painel)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "categoria": {
                            "type": "string",
                            "description": "Nome da categoria (ex: 'outros', 'mercado', 'transporte').",
                        },
                        "start_date": {
                            "type": "string",
                            "description": "Data inicial AAAA-MM-DD. Sempre passe ('este ano' = 1º de janeiro até hoje; 'esse mês' = dia 1 até hoje).",
                        },
                        "end_date": {
                            "type": "string",
                            "description": "Data final AAAA-MM-DD, inclusiva. Sempre passe.",
                        },
                    },
                    "required": ["categoria", "start_date", "end_date"],
                },
            },
        },
        is_write=False,
        execute=_get_category_spend,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_largest_expenses",
                "description": (
                    "Retorna os MAIORES GASTOS INDIVIDUAIS do user no "
                    "período — um a um, não agregado. Diferente de "
                    "`get_top_categories` (que soma por categoria). Use pra "
                    "'qual meu maior gasto?', 'meus 5 maiores gastos do "
                    "mês', 'top 3 compras', 'em que gastei mais de uma vez "
                    "só'. Passe `categoria` pra restringir a uma categoria "
                    "('meus maiores gastos em mercado'). Inclui despesas reais "
                    "+ compras no cartão. Período que é um "
                    "mês inteiro conta o mês todo com o cartão pela fatura (igual ao painel). "
                    "Cada item traz `categoria`, `instituicao` (banco ou cartão), "
                    "`data` e `valor`. Quando houver `parcelamento`, copie esse "
                    "texto como está; não reescreva nem calcule. "
                    "É ESTA a tool de 'mais detalhes' depois de uma "
                    "lista de categorias ou de gastos: chame sem `categoria`, "
                    "limit=20, e agrupe pelo campo `categoria`."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_date": {
                            "type": "string",
                            "description": "Data inicial AAAA-MM-DD. Sempre passe ('este ano' = 1º de janeiro até hoje; 'esse mês' = dia 1 até hoje).",
                        },
                        "end_date": {
                            "type": "string",
                            "description": "Data final AAAA-MM-DD, inclusiva. Sempre passe.",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "default": 5,
                            "description": "Top N gastos (1 a 20, padrão 5). Use 1 pra 'qual meu maior gasto'.",
                        },
                        "categoria": {
                            "type": "string",
                            "description": "Opcional. Restringe os maiores gastos a essa categoria (ex: 'outros', 'mercado').",
                        },
                    },
                    "required": ["start_date", "end_date"],
                },
            },
        },
        is_write=False,
        execute=_get_largest_expenses,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "compare_periods",
                "description": (
                    "Compara receita, despesa e categorias entre 2 períodos equivalentes. "
                    "Use pra 'gastei mais em abril ou maio?', 'esse mês vs o anterior', "
                    "'compara com o mês passado'. Pra mês use `mes` (AAAA-MM; o mês corrente "
                    "compara os mesmos dias do anterior; datas do mês corrente contra o anterior inteiro dão o mesmo "
                    "resultado); datas só pra períodos livres ou meses antigos. "
                    "Cartão: com `mes`, ou com datas que são meses-calendário inteiros dos dois lados, "
                    "entra pela FATURA (a regra do 'Saiu do mês', igual ao painel); com qualquer período "
                    "parcial, pela DATA DA COMPRA nos dois lados (parcelado entra inteiro no dia da "
                    "compra), como as outras consultas de período livre (`base.cartao` diz qual). Cite `escolha` "
                    "(o que foi comparado e a regra do cartão) e `pct` EXATAMENTE como veio, sem "
                    "arredondar; estado 'nova' não tem percentual."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mes": {"type": "string", "description": "Mês a comparar com o anterior, AAAA-MM."},
                        "period_a_start": {"type": "string", "description": "Início do período A (o mais antigo) em ISO (YYYY-MM-DD)."},
                        "period_a_end":   {"type": "string", "description": "Fim do período A em ISO (YYYY-MM-DD)."},
                        "period_b_start": {"type": "string", "description": "Início do período B (o atual) em ISO (YYYY-MM-DD)."},
                        "period_b_end":   {"type": "string", "description": "Fim do período B em ISO (YYYY-MM-DD)."},
                    },
                },
            },
        },
        is_write=False,
        execute=_compare_periods,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_spending_trend",
                "description": (
                    "Retorna a tendência mensal (totais de despesa e receita) "
                    "dos últimos N meses, incluindo o atual. JÁ inclui análise "
                    "calculada (média, saldo médio, direção da tendência, "
                    "variação %). USE QUANDO O USER PEDIR tendência, evolução, "
                    "direção dos gastos ou média mensal. Exemplos: 'tendência "
                    "últimos 6 meses', 'como tá evoluindo meus gastos', 'meus "
                    "gastos estão subindo ou caindo?', 'gastos mês a mês', "
                    "'média por mês'. O TOTAL de um período ('este ano', 'meus "
                    "últimos 3 meses', 'trimestre') NÃO é tendência: use "
                    "a tool de resumo do período. Default é 6."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "months": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 6,
                            "description": "Quantos meses olhar pra trás (até 12; acima disso a resposta vem cortada com aviso; padrão 6).",
                        },
                    },
                },
            },
        },
        is_write=False,
        execute=_get_spending_trend,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "forecast_month_end",
                "description": (
                    "Projeta como o mês corrente vai fechar. Use pra 'no ritmo "
                    "atual vou fechar no negativo?', 'projeção do mês', 'vou "
                    "estourar?'. Os números até agora (`receita`, `despesa`) são os "
                    "do painel do mês. IMPORTANTE pra mensagem: (1) "
                    "`saldo_ate_agora` é receita menos despesa no mês corrente, "
                    "NÃO o saldo da conta. (2) Só as compras do cartão conectado "
                    "pelo Open Finance (`despesa_cartao`) entram como estão na "
                    "fatura; o resto do gasto até hoje, inclusive cartão lançado à mão, é "
                    "projetado pelo ritmo diário (`despesa_no_ritmo`). Lançamento "
                    "fora do cartão com data depois de hoje, no mês, entra como "
                    "está (`despesa_agendada`), sem esticar. Receita com data "
                    "futura no mês já está em `receita`, como no painel; a "
                    "receita não é projetada."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=False,
        execute=_forecast_month_end,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "add_launch",
                "description": (
                    "Registra uma despesa ou receita NÃO-CARTÃO (sai/entra da "
                    "conta corrente). Use pra 'gastei 50 no mercado', 'recebi "
                    "1000 de salário', 'paguei 80 de luz'. NÃO use pra compras "
                    "no cartão de crédito (essa é outra ferramenta). Categoria "
                    "é inferida automaticamente — só passe `categoria` se o "
                    "user disse explicitamente. EXECUTA DIRETO (sem perguntar "
                    "'confirma?'); o user reverte depois se quiser."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tipo": {
                            "type": "string",
                            "enum": ["despesa", "receita"],
                            "description": (
                                "despesa (saiu) ou receita (entrou). PADRÃO = despesa: "
                                "só use receita quando o user marca entrada explícita "
                                "('recebi', 'receita', 'ganhei', 'salário', 'caiu'). "
                                "Mensagem que é só valor+descrição ('77,90 mercado') é despesa."
                            ),
                        },
                        "valor": {
                            "type": "number",
                            "minimum": 0.01,
                            "description": "Valor em reais (ex: 50, 1234.56).",
                        },
                        "alvo": {
                            "type": "string",
                            "description": "Onde o dinheiro foi/veio. Ex: 'mercado', 'salário', 'uber'.",
                        },
                        "categoria": {
                            "type": "string",
                            "description": "Categoria explícita. Omita se o user não mencionou — o sistema infere.",
                        },
                        "nota": {
                            "type": "string",
                            "description": "Observação livre, opcional.",
                        },
                        "data": {
                            "type": "string",
                            "description": "Data do lançamento em ISO 8601 (YYYY-MM-DD). Omita pra usar hoje.",
                        },
                        "forma_pagamento": {
                            "type": "string",
                            "enum": ["dinheiro", "banco"],
                            "description": (
                                "Como o dinheiro saiu/entrou, SÓ se o usuário disse: "
                                "'dinheiro' (dinheiro vivo, espécie) ou 'banco' (Pix, "
                                "cartão, débito, transferência). 'Boleto' diz o que foi "
                                "pago, não como. Nunca invente: omita se ele não disse."
                            ),
                        },
                    },
                    "required": ["tipo", "valor"],
                },
            },
        },
        is_write=True,
        requires_confirmation=False,
        execute=_add_launch_execute,
        valor_do_usuario="valor",
        summary=_add_launch_summary,
        confirmar_se=precisa_confirmar_lancamento,
        ao_confirmar=_congela_pendencia,
        # Só a Q40 (WhatsApp com a flag); fora disso o add_launch não conta.
        arma_pendencia_no_execute=armaria_q40,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "delete_launch",
                "description": (
                    "Apaga um lançamento (despesa, receita ou compra no "
                    "cartão). Use pra 'apaga o gasto #5', 'remove o último', "
                    "'apaga aquela compra'. Reverte saldo automaticamente. "
                    "Se for parcela de parcelamento, derruba o grupo inteiro. "
                    "ESCRITA DESTRUTIVA — pede confirmação (não tem undo)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "launch_id": {
                            "type": "string",
                            "description": (
                                "ID exatamente como aparece no histórico. "
                                "Aceita: '#5' (user_seq de lançamento normal), "
                                "número grande (id de compra no cartão), OU "
                                "'PCxxxxxxxx' (código de parcelamento, ex: "
                                "PC81524273). Use sempre o que o user disse "
                                "literalmente, NUNCA invente."
                            ),
                        },
                    },
                    "required": ["launch_id"],
                },
            },
        },
        is_write=True,
        requires_confirmation=True,
        summary=_delete_launch_summary,
        execute=_delete_launch_execute,
        validate=_delete_launch_validate,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "delete_all_launches",
                "description": (
                    "Apaga de uma vez todas as despesas e receitas da CONTA "
                    "CORRENTE (e desfaz pagamentos de fatura), revertendo o saldo "
                    "da conta. NÃO mexe em caixinhas nem investimentos — eles ficam "
                    "intactos. Use SOMENTE quando o user pedir explicitamente pra "
                    "apagar/zerar/limpar TUDO: 'apaga todos os lançamentos', 'limpa "
                    "meu histórico', 'zera meus lançamentos', 'apaga tudo'. NÃO use "
                    "pra apagar um lançamento específico — pra isso é `delete_launch`. "
                    "ESCRITA DESTRUTIVA EM MASSA — pede confirmação, não tem undo."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=True,
        requires_confirmation=True,
        summary=_delete_all_launches_summary,
        execute=_delete_all_launches_execute,
        validate=_delete_all_launches_validate,
    ),
]
