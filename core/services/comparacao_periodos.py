"""Comparação de períodos equivalentes (PL-07, Plus/Pro): a regra única de "mês passado".

Quem decide a janela e o delta é este módulo; os consumidores (a tool `compare_periods` da IA,
`GET /api/v2/comparacao` e o KPI das Análises, com `meses=N`) só mostram. Dois caminhos, um formato
de saída; só a base do cartão muda (`base.cartao`):
- `mes` (aberto ou fechado): os números do "Entrou/Saiu do mês" (`db/resumo_mes`), cartão pela
  FATURA.
- datas informadas: se as DUAS janelas são meses-calendário inteiros (critério `_meses_inteiros` da tool), é o mesmo
  que `mes=`: cartão pela FATURA, mês todo. Senão é período livre, nas DUAS janelas: a regra das outras tools da IA (Q3), as de
  `get_top_categories`/`get_period_summary` (`db.get_top_expense_categories`, `db.get_summary_by_period`),
  cartão pela DATA DA COMPRA: a compra parcelada tem a mesma `purchased_at` em todas as parcelas e entra
  inteira no dia da compra. Essas leituras abrem a própria conexão (sem o snapshot único do caminho `mes`).

Mês aberto: o mês corrente INTEIRO (o que já está lançado nele, inclusive fatura que fecha e lançamento com data
futura) contra o anterior inteiro, o mesmo número do `resumo_do_mes`. Mês fechado: mês inteiro × mês inteiro.
`anterior_encurtado` é sempre False (mantido: é contrato do bloco).
Com `meses=N` (o KPI das Análises): os N meses até `ano`-`mes`, inteiros, contra os N antes deles.
"""
from __future__ import annotations

import calendar
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import accumulate

from core.observability import get_logger
from core.reports.weekly import _categoria_em_uma_linha, _pct
from core.services.plan_service import history_earliest_date, plan_gate_ok
import db
from db import lancamentos, resumo_mes
from db.connection import get_conn
from db.mes_detalhes import _em_reais
from utils_date import day_tz, now_tz

logger = get_logger(__name__)

MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro")
MAX_DIAS = 400
MIN_DATA, MAX_DATA = date(2000, 1, 1), date(2100, 12, 31)  # fim + 1 dia nunca estoura `date`
MAX_CATEGORIAS = 10
_CENT = Decimal("0.01")
BASES = {
    "fatura": {"regra": "entrou_e_saiu_do_mes", "cartao": "fatura", "origem": "lançamentos + faturas de cartão",
               "escolha": " Cartão pela fatura (igual ao painel do mês)."},
    "data_da_compra": {"regra": "periodo_livre", "cartao": "data_da_compra",
                       "origem": "lançamentos + compras no cartão pela data da compra",
                       "escolha": " Cartão pela data da compra (a compra parcelada entra inteira no dia da compra); "
                                  "pode diferir do painel do mês, que usa a fatura."},
}


def _mes(idx: int) -> tuple[date, date]:
    """(primeiro, último dia) do mês `idx` = ano*12 + mes - 1 (sem `replace(month=...)`)."""
    ano, m = divmod(idx, 12)
    return date(ano, m + 1, 1), date(ano, m + 1, calendar.monthrange(ano, m + 1)[1])


def _nome(d: date) -> str:
    return f"{MESES[d.month - 1]} de {d.year}"


def _janela(inicio: date, fim: date) -> dict:
    return {"inicio": inicio, "fim": fim, "dias": (fim - inicio).days + 1}


def _janelas(modo: str, escolha: str, atual: dict, anterior: dict, cartao: str = "fatura") -> dict:
    return {"modo": modo, "escolha": escolha, "atual": atual, "anterior": anterior, "cartao": cartao,
            "mesmo_tamanho": atual["dias"] == anterior["dias"], "anterior_encurtado": False}


def escolher_janelas(hoje: date, ano: int, mes: int, meses: int = 1) -> dict:
    """Janelas (inclusivas) dos `meses` meses que terminam em `ano`-`mes` contra os `meses` meses antes deles
    (1 = o mês contra o anterior; mais de 1 = o KPI das Análises). `ValueError` em mês inválido ou futuro."""
    if not (all(type(v) is int for v in (ano, mes, meses)) and 1 <= mes <= 12 and 2000 <= ano <= 9998 and meses >= 1):
        raise ValueError("mês inválido; use AAAA-MM.")
    idx, idx_hoje = ano * 12 + mes - 1, hoje.year * 12 + hoje.month - 1
    if idx > idx_hoje:
        raise ValueError(f"mês no futuro; o mês corrente é {hoje.year:04d}-{hoje.month:02d}.")
    ini, fim = _mes(idx - meses + 1)[0], _mes(idx)[1]
    ant_ini, ant_fim = _mes(idx - 2 * meses + 1)[0], _mes(idx - meses)[1]
    a, p = _janela(ini, fim), _janela(ant_ini, ant_fim)
    um = meses == 1
    if idx < idx_hoje:
        return _janelas("mes_fechado", f"{_nome(ini).capitalize()} inteiro ({a['dias']} dias) contra "
                        f"{_nome(ant_ini)} inteiro ({p['dias']} dias)." if um else
                        f"{_nome(ini).capitalize()} a {_nome(fim)}, meses inteiros ({a['dias']} dias), contra "
                        f"{_nome(ant_ini)} a {_nome(ant_fim)} ({p['dias']} dias).", a, p)
    return _janelas("mes_aberto", f"{MESES[mes - 1].capitalize()} até agora (o que já está lançado no mês, igual ao Resumo do mês) "
                    f"contra {_nome(ant_ini)} inteiro. O mês ainda não acabou: a comparação tende a mostrar gasto menor." if um else
                    f"{_nome(ini).capitalize()} a {_nome(fim)}, com {MESES[mes - 1]} até agora (o que já está lançado, igual ao "
                    f"Resumo do mês), contra {_nome(ant_ini)} a {_nome(ant_fim)}, meses inteiros. O mês ainda não acabou: "
                    "a comparação tende a mostrar gasto menor.", a, p)


def _intervalos(hoje: date, atual: tuple[date, date], anterior: tuple[date, date], por_fatura: bool = False) -> dict:
    for ini, fim in (atual, anterior):
        if not (MIN_DATA <= ini and fim <= MAX_DATA):
            raise ValueError(f"use datas entre {MIN_DATA.year} e {MAX_DATA.year}.")
        if fim < ini:
            raise ValueError("o fim do período vem antes do início.")
        if (fim - ini).days + 1 > MAX_DIAS:
            raise ValueError(f"cada período pode ter no máximo {MAX_DIAS} dias.")
    a, p = _janela(*atual), _janela(*anterior)
    dmy = lambda d: f"{d:%d/%m/%Y}"  # noqa: E731
    escolha = f"Períodos informados: {dmy(a['inicio'])} a {dmy(a['fim'])} contra {dmy(p['inicio'])} a {dmy(p['fim'])}."
    if a["fim"] > hoje:
        escolha += (f" O período atual ainda não terminou (hoje é {dmy(hoje)}): só entra o que já está lançado"
                    " (parcelas de cartão e lançamentos com data futura); não é previsão.")
    if a["dias"] != p["dias"]:
        escolha += f" Os períodos têm tamanhos diferentes ({a['dias']} e {p['dias']} dias)."
    return _janelas("periodos_informados", escolha, a, p, cartao="fatura" if por_fatura else "data_da_compra")


def _linha(atual: Decimal, anterior: Decimal) -> dict:
    """`delta`, `pct` e `estado` de um par. Base zero nunca divide, e nunca vira +100%."""
    delta = atual - anterior
    if atual == 0 and anterior == 0:
        estado = "vazio"
    elif anterior == 0:
        estado = "nova"
    elif atual == 0:
        estado = "sem_gasto"
    else:
        estado = "igual" if delta == 0 else "variou"
    pct = _pct(delta, anterior, 1)
    if pct == 0:
        pct = abs(pct)  # sem "-0.0"
    # `total_*`: o `ai_guard` só lê como dinheiro o número sob chave monetária (ver `para_json`).
    return {"total_atual": atual, "total_anterior": anterior, "total_delta": delta, "pct": pct, "estado": estado}


def _dinheiro(v) -> Decimal:
    return Decimal(v).quantize(_CENT)


def _serie(linhas: list[dict], janela: dict, hoje: date, total: Decimal) -> list[Decimal] | None:
    """Gasto acumulado por dia da janela (o filtro do `dias[]` do Resumo; dia fora da janela, como compra de cartão
    anterior a ela, prende na ponta); `None` se a última posição não fecha o `total`. Na janela que contém hoje corta em hoje (ou no último lançamento de data futura)."""
    n, por_dia, ult = janela["dias"], [Decimal(0)] * janela["dias"], 0
    for r in _em_reais(linhas, set())[0]:
        if r["tipo"] == "saida":
            i = min(max((r["dia"] - janela["inicio"]).days, 0), n - 1)
            por_dia[i] += r["valor"]
            ult = max(ult, i + 1)
    acc = [_dinheiro(v) for v in accumulate(por_dia)]
    if acc[-1] != total:
        return None
    if janela["inicio"] <= hoje <= janela["fim"]:  # sobe em degrau até o lançamento de data futura (a última posição = total)
        n = max((hoje - janela["inicio"]).days + 1, ult)
    return acc[:n]


def _categorias(atual: list[dict], anterior: list[dict]) -> list[dict]:
    """Casa as duas janelas pela chave da categoria; o rótulo é a grafia do lado atual, senão do anterior (no
    período livre, a de maior total: as linhas vêm por total desc e a última a ser lida manda, daí o `reversed`)."""
    por: dict[str, dict] = {}
    for lado, rows in (("anterior", anterior), ("atual", atual)):
        for r in reversed(rows):
            d = por.setdefault(r["chave"], {"atual": _dinheiro(0), "anterior": _dinheiro(0)})
            d[lado] += _dinheiro(r["total"])  # soma: no período livre a grafia só difere em caixa/acento
            d["categoria"] = _categoria_em_uma_linha(r["categoria"])
    linhas = [{"categoria": d["categoria"], **_linha(d["atual"], d["anterior"])} for d in por.values()]
    return sorted((x for x in linhas if x["estado"] != "vazio"), key=lambda x: (-abs(x["total_delta"]), x["categoria"]))


def _chave(nome: str) -> str:
    """Casamento de categoria no período livre, onde a consulta agrupa por texto exato: caixa e acento
    não separam (o `cat_key_sql` do caminho `mes`, em Python)."""
    return "".join(c for c in unicodedata.normalize("NFKD", nome.lower()) if not unicodedata.combining(c))


def _totais_compra(user_id: int, w: dict) -> tuple[dict, list[dict]]:
    """Totais e categorias do período livre, REUSANDO as consultas das outras tools da IA (cartão pela
    data da compra); o formato é o de `resumo_mes.totais`/`categorias`. Cada consulta abre a sua conexão, então
    isto roda FORA do `with get_conn()` de `_comparar` (o pool é pequeno). Categoria vazia sai "outros" (a consulta
    da main já agrupou vazio com o "outros" real e não dá para separar); em `mes=` sai "sem categoria"."""
    ini, fim = w["inicio"], w["fim"]
    cats = db.get_top_expense_categories(user_id, ini, fim, limit=None, by_bill_month=False)
    entrou = Decimal(str(db.get_summary_by_period(user_id, ini, fim).get("receita") or 0))
    totais = {"entrou": entrou, "saiu": sum((Decimal(str(c["total"])) for c in cats), Decimal(0)),
              "n": 1 if cats or entrou else 0, "n_cartao": resumo_mes.n_compras_cartao(user_id, ini, fim)}
    return totais, [{"chave": _chave(c["categoria"]), "categoria": c["categoria"], "total": c["total"]} for c in cats]


def _comparar(user_id: int, j: dict, agora: datetime) -> dict:
    j = dict(j)
    cartao = j.pop("cartao")
    por_compra = cartao == "data_da_compra"
    base = BASES[cartao]
    out = {**j, "base": {**{k: base[k] for k in ("regra", "cartao", "origem")}, "cartao_lancamentos": None},
           "despesa": None, "receita": None, "resultado": None,
           "categorias": [], "categorias_omitidas": 0, "motivo": None, "motivos": [], "nota": None, "serie": None}
    # `mes=` com N meses (o KPI): "dia 15 de 92" não diz nada e a série diária de dois anos não tem leitor.
    um_mes = (j["atual"]["inicio"].year, j["atual"]["inicio"].month) == (j["atual"]["fim"].year, j["atual"]["fim"].month)
    n_meses = j["modo"] in ("mes_aberto", "mes_fechado") and not um_mes
    if j["modo"] == "mes_aberto" and not n_meses:  # a legenda "dia 17 de 31" do app: `atual.fim` é o fim do mês, não hoje (mesmos nomes do forecast_month_end)
        out["dias_corridos"], out["dias_no_mes"] = day_tz(agora).day, j["atual"]["dias"]
    corte = history_earliest_date(user_id, agora)
    if corte and corte > min(j["atual"]["inicio"], j["anterior"]["inicio"]):  # o modelo pode trocar A e B
        out["motivo"] = "inicio_do_historico"  # o plano não enxerga o período mais antigo inteiro
        return out
    (ia, fa), (ip, fp) = ((w["inicio"], w["fim"] + timedelta(days=1)) for w in (j["atual"], j["anterior"]))
    from db.contas_hoje import listar  # no corpo: o import de módulo fecha ciclo (como `resumo_do_mes`)

    cat_a = cat_p = None
    vistos: set = set()
    if por_compra:  # fora do bloco: cada consulta abre a própria conexão
        (ta, cat_a), (tp, cat_p) = (_totais_compra(user_id, j[lado]) for lado in ("atual", "anterior"))
    with get_conn() as conn, conn.cursor() as cur:
        if not por_compra:  # um snapshot só para os totais e as categorias do caminho `mes`/meses inteiros
            cur.execute("set transaction isolation level repeatable read, read only")
            ta, tp = resumo_mes.totais(cur, user_id, ia, fa), resumo_mes.totais(cur, user_id, ip, fp)
        # Categorias e motivos são extras: se falharem, os totais saem. Savepoint nas categorias porque um erro de
        # SQL aborta a transação inteira e derrubaria os motivos, lidos depois.
        if tp["n"] and not por_compra:
            try:
                with conn.transaction():
                    cat_a, cat_p = resumo_mes.categorias(cur, user_id, ia, fa), resumo_mes.categorias(cur, user_id, ip, fp)
            except Exception as exc:
                logger.warning("comparacao_periodos: categorias indisponíveis (%s)", type(exc).__name__)
        if not por_compra and not n_meses:  # a série diária das duas janelas; extra como as categorias (savepoint). Período livre: sem linha
            try:
                with conn.transaction():
                    serie = {lado: _serie(lancamentos.listar(cur, user_id, i, f, limite=None)[0], j[lado], day_tz(agora),
                                          _dinheiro(t["saiu"])) for lado, i, f, t in (("atual", ia, fa, ta), ("anterior", ip, fp, tp))}
                out["serie"] = None if None in serie.values() else serie
            except Exception as exc:
                logger.warning("comparacao_periodos: série indisponível (%s)", type(exc).__name__)
        try:
            vistos = set(listar(cur, user_id)["motivos"])  # os mesmos avisos do resumo do mês (último: sem savepoint)
        except Exception as exc:
            logger.warning("comparacao_periodos: motivos indisponíveis (%s)", type(exc).__name__)
    out["motivos"] = [m for m in resumo_mes.MOTIVOS if m in vistos]  # `listar` nunca devolve o do histórico
    if out["motivos"]:
        out["nota"] = f"Número pode estar desatualizado ou incompleto ({', '.join(out['motivos'])}); avise o usuário."
    out["base"]["cartao_lancamentos"] = {"atual": int(ta["n_cartao"]), "anterior": int(tp["n_cartao"])}
    out["escolha"] += base["escolha"]
    if j["modo"] == "mes_aberto" and not ta["n_cartao"]:
        out["escolha"] += " Nenhuma fatura de cartão no período." if n_meses else " Nenhuma fatura de cartão no mês."
    out["despesa"] = _linha(_dinheiro(ta["saiu"]), _dinheiro(tp["saiu"]))
    out["receita"] = _linha(_dinheiro(ta["entrou"]), _dinheiro(tp["entrou"]))
    (r, dsp) = out["receita"], out["despesa"]
    res_a, res_p = r["total_atual"] - dsp["total_atual"], r["total_anterior"] - dsp["total_anterior"]
    out["resultado"] = {"total_atual": res_a, "total_anterior": res_p, "total_delta": res_a - res_p, "pct": None,
                        "estado": "igual" if res_a == res_p else "variou"}
    if not tp["n"]:
        out["motivo"] = "sem_base"
    elif cat_a is None:
        out["motivo"] = "categorias_indisponiveis"
    else:
        todas = _categorias(cat_a, cat_p)
        out["categorias"], out["categorias_omitidas"] = todas[:MAX_CATEGORIAS], max(0, len(todas) - MAX_CATEGORIAS)
    return out


def comparar_mes(user_id: int, ano: int, mes: int, agora: datetime | None = None, meses: int = 1) -> dict | None:
    """O mês `ano`-`mes` (ou os `meses` meses até ele) contra o anterior equivalente. `None` = o plano não
    permite (nada foi lido)."""
    if not plan_gate_ok(user_id, "financial_comparison"):
        return None
    agora = agora or now_tz()
    return _comparar(user_id, escolher_janelas(day_tz(agora), ano, mes, meses), agora)


def comparar_intervalos(user_id: int, atual: tuple[date, date], anterior: tuple[date, date],
                        agora: datetime | None = None, por_fatura: bool = False) -> dict | None:
    """Dois intervalos inclusivos quaisquer (`atual` contra `anterior`). `None` = o plano não permite.
    Cartão pela data da compra (período livre), ou pela fatura se `por_fatura`: o chamador só liga isso quando
    as DUAS janelas são meses-calendário inteiros (a regra do painel), nunca misturando bases."""
    if not plan_gate_ok(user_id, "financial_comparison"):
        return None
    agora = agora or now_tz()
    return _comparar(user_id, _intervalos(day_tz(agora), atual, anterior, por_fatura), agora)


def para_json(v):
    """Decimal → número JSON e date → ISO, em qualquer profundidade. Dinheiro e pct já saem quantizados (2 e 1
    casas) das contas em Decimal, então `float` só muda o tipo. O `ai_guard` só reconhece número JSON como
    evidência (o `Decimal` virado texto deixaria a afirmação certa "sem evidência"), igual a `_resumo_periodo`."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: para_json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [para_json(x) for x in v]
    return v
