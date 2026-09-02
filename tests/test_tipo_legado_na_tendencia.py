"""A forma legada do `tipo` também conta nos leitores de TENDÊNCIA mensal.

Irmão do `tests/test_tipo_legado_no_dashboard.py`, encaminhado do #158 para cá
por verificabilidade (`CLAUDE.md` §4): lá o conserto foi nos números do MÊS do
dashboard, aqui são os leitores que respondem a mesma pergunta por outras telas —

| leitor                        | onde aparece         | lia antes                  |
|-------------------------------|----------------------|----------------------------|
| `get_spending_trend`          | tendência da IA      | só a moderna, as duas fora |
| `compute_kpis` (SQL + Python) | KPI da Análise       | 'saida' sim, 'entrada' NÃO |
| `compute_evolution`           | barra do mês, mesma  | 'saida' sim, 'entrada' NÃO |
|                               | tela do KPI          |                            |
| `compute_history_quick_stats` | cards do Histórico   | a legada nem no total      |
| `list_history`                | lista do Histórico   | 'all' E o filtro por tipo  |

Os três últimos vieram de varrer `db/analytics.py` atrás do mesmo defeito depois
que o revisor nomeou só o `compute_evolution` — a regra de sempre: "achei um
caso" não é "resolvi a categoria" (`CLAUDE.md` §2).

O `entrada` faltando é o pior deles: a receita legada some do income, o `net`
sobe, e a `savings_rate` (net/income) sai errada nos DOIS sentidos ao mesmo
tempo. No `list_history` o efeito é outro e igualmente ruim — a linha some da
tela sem erro nenhum.

Conserto: `TIPO_CANON_SQL` colapsa a forma legada na moderna no SQL, e o Python
deixa de repetir a lista de aliases — uma regra, um lugar (§0.7).

Medido na produção (Railway) em 27/08/2026 21:50 UTC: `count(*) filter (where
tipo='saida')` = 0 e o mesmo para 'entrada' em `launches`. Nenhum número de
usuário muda hoje; isto fecha a classe, não apaga um incêndio.

Referencial de data: `date.today()` governa a JANELA, não o rótulo do mês. Ele é
o fuso do PROCESSO, que desde `utils_date.align_process_tz` é o mesmo do app —
antes disso os dois divergiam, e é essa divergência que o parágrafo abaixo
descreve. É dele que saem o `range_start` do `get_spending_trend`
(`db/accounts.py:367,377`), o `resolve_window` (`db/analytics.py:77`) e as chaves
de bucket do `compute_evolution` (`db/analytics.py:335`). Por isso o arquivo
inteiro grava e lê por ele, com janela explícita de `resolve_window(months=1)` em
vez de `month_range_today()`: gravar em `today_tz()` — que cai em
`America/Sao_Paulo` quando ninguém define `REPORT_TIMEZONE`/`TZ`, e o workflow do
CI não define (`utils_date._tz`) — e ler por uma janela de `date.today()` punha a
escrita fora da janela e fora do mês procurado quando os dois apontavam dias
diferentes (31/08 23:30 em São Paulo já é 01/09 em UTC). Era essa a flakiness que
este commit fechou, e ela não precisava da virada do mês para existir. Hoje as
duas âncoras concordam por construção (`align_process_tz` escreve `TZ` e chama
`tzset()`), então a disciplina deste arquivo virou defesa em profundidade.

O RÓTULO do mês, esse, vem de outro lugar em cada leitor, e em nenhum deles é o
processo: `get_spending_trend` (db/accounts.py) converte `criado_em at time
zone %s`, com `tz_name()` no parâmetro, e `compute_evolution` usa
`DATE_TRUNC('month', criado_em)` sobre um `timestamptz`
(`db/schema.py:216`), isto é, o fuso da SESSÃO do Postgres (`db/analytics.py:309`)
— que desde `align_process_tz` também é `America/Sao_Paulo`, então os dois agora
concordam; antes o mesmo instante podia sair em meses diferentes. O que protege
os testes disso é a HORA de escrita: sempre no meio do dia (10h a 14h), com folga
larga sobre os offsets em jogo. Quem mexer nessas horas mexe nessa folga.

Ou seja, não há um referencial só: este arquivo ancora a JANELA, que é o que está
ao alcance dele. O `America/Sao_Paulo` que estava hardcoded no `get_spending_trend`
NÃO está mais lá: a #179 trocou os dois literais por `tz_name()` (db/accounts.py),
e era, DOS CINCO QUE A #179 CONVERTEU, o único que ignorava `REPORT_TIMEZONE` e
`TZ` ao mesmo tempo — os quatro que ficaram de dívida também ignoram as duas, e
cada um traz um comentário `DÍVIDA (#179)` no próprio local:
`billing_commands.py::_format_plan_expires`, `email_service.py::_fmt_brl_date`,
`proactive_ai_scheduler.py::RUN_HOUR_UTC` e o `const APP_TZ` de `dashboard.js`
(`grep -rn 'DÍVIDA (#179)'` acha os quatro). Sem número de linha de propósito —
símbolo greppável não apodrece, e este próprio PR empurrou o `RUN_HOUR_UTC` dez
linhas para baixo no mesmo commit em que a referência foi escrita.
Hoje o rótulo do mês segue o fuso do app como todo o resto — o que não muda nada
neste arquivo, que escreve no meio do dia justamente para não depender disso. O
fuso da SESSÃO também saiu de fora: ele passou a ser o do app, imposto pelo
processo (`align_process_tz`).

Controle NEGATIVO: volte `{TIPO_CANON_SQL} as tipo` + o filtro para
`tipo, valor` + `and tipo in ('despesa','receita')` em `get_spending_trend`
(db/accounts.py), ou o par equivalente em `compute_kpis` (db/analytics.py) —
`test_tendencia_conta_as_duas_formas` e `test_kpis_contam_as_duas_formas` ficam
vermelhos, cada um pelo seu lado.
Controle POSITIVO: `test_base_moderna_responde_o_mesmo_de_sempre` — a base de
100% da produção hoje, que não pode ter mudado de número.
"""
from datetime import date, datetime, time, timedelta

import db
from db import insights
from db.analytics import (
    compute_behavioral_patterns, compute_evolution, compute_history_quick_stats,
    compute_kpis, list_history, resolve_window,
)


def _grava(user_id, tipo, valor, categoria="mercado", dia=None):
    """A forma legada não tem escritor (nem deve ter): entra por SQL, que é como
    ela existe numa base de verdade."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into launches (user_id, tipo, valor, categoria, nota, "
            "criado_em, is_internal_movement) values (%s,%s,%s,%s,%s,%s,false)",
            (user_id, tipo, valor, categoria, "legado",
             datetime.combine(dia or date.today(), time(10, 0))),
        )
        conn.commit()


def _mes_corrente(trend):
    hoje = date.today()
    linha = [t for t in trend if t["year"] == hoje.year and t["month"] == hoje.month]
    assert linha, trend
    return linha[0]


# ── get_spending_trend: a tool de tendência da IA ──────────────────────────

def test_tendencia_conta_as_duas_formas(pro_user_id):
    _grava(pro_user_id, "despesa", 50)
    _grava(pro_user_id, "saida", 100)      # despesa legada
    _grava(pro_user_id, "receita", 200, categoria="rendimentos")
    _grava(pro_user_id, "entrada", 300, categoria="rendimentos")   # receita legada

    linha = _mes_corrente(db.get_spending_trend(pro_user_id, months=1))
    assert linha["despesa"] == 150.0, linha
    assert linha["receita"] == 500.0, linha


# ── compute_kpis: o SQL e o agregador Python da Análise ────────────────────

def test_kpis_contam_as_duas_formas(pro_user_id):
    """`entrada` era a que faltava: sem ela o income cai, o net sobe e a
    savings_rate mente nos dois sentidos de uma vez."""
    _grava(pro_user_id, "despesa", 50)
    _grava(pro_user_id, "saida", 100)
    _grava(pro_user_id, "receita", 200, categoria="rendimentos")
    _grava(pro_user_id, "entrada", 300, categoria="rendimentos")

    start, end = resolve_window(months=1)
    k = compute_kpis(pro_user_id, start, end)

    assert k["total_expense"] == 150.0, k
    assert k["total_income"] == 500.0, k
    assert k["net"] == 350.0, k
    assert k["transactions_count"] == 4, k


def test_as_duas_telas_dao_o_mesmo_numero(pro_user_id):
    """O ponto do apontamento: o mesmo mês não pode valer um número na Análise e
    outro na tendência da IA."""
    _grava(pro_user_id, "saida", 100)
    _grava(pro_user_id, "entrada", 300, categoria="rendimentos")

    start, end = resolve_window(months=1)
    k = compute_kpis(pro_user_id, start, end)
    linha = _mes_corrente(db.get_spending_trend(pro_user_id, months=1))

    assert (k["total_expense"], k["total_income"]) == (linha["despesa"], linha["receita"])


# ── controle positivo ──────────────────────────────────────────────────────

def test_base_moderna_responde_o_mesmo_de_sempre(pro_user_id):
    """Controle POSITIVO — 100% da produção hoje (zero linhas legadas, medido em
    27/08/2026 21:50 UTC). Um alias que mudasse ESTES números seria pior que o
    descasamento que ele conserta.

    A movimentação interna continua fora dos dois leitores, que é a outra coisa
    que um alias mal escrito quebraria.
    """
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 45, "compra", None, categoria="mercado",
        criado_em=datetime.combine(date.today(), time(12, 0)),
    )
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 80, "freela", None, categoria="rendimentos",
        criado_em=datetime.combine(date.today(), time(13, 0)),
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 500, "aporte", None, categoria="investimento_aporte",
        criado_em=datetime.combine(date.today(), time(14, 0)), is_internal_movement=True,
    )

    linha = _mes_corrente(db.get_spending_trend(pro_user_id, months=1))
    assert (linha["despesa"], linha["receita"]) == (45.0, 80.0), linha

    start, end = resolve_window(months=1)
    k = compute_kpis(pro_user_id, start, end)
    assert (k["total_expense"], k["total_income"]) == (45.0, 80.0), k


# ── os outros três sites que a varredura do arquivo achou ──────────────────
#
# O apontamento nomeou o `compute_evolution`. Varrendo `db/analytics.py` atrás do
# mesmo defeito — 'receita' aceita SEM 'entrada' —, ele aparecia em três lugares,
# cada um de uma forma:
#
#   compute_evolution            filtro + agregador Python  (a barra do mês)
#   compute_history_quick_stats  contagem: a legada nem no total entrava
#   list_history                 filtro 'all' E o filtro por tipo pedido
#
# Os três estão aqui porque um teste por site é o que impede o próximo achado de
# ser "faltou o irmão".

def test_evolucao_conta_as_duas_formas(pro_user_id):
    """O front busca `compute_kpis` e `compute_evolution` para a MESMA tela: a
    receita legada não pode entrar no KPI de income e faltar na barra do mês."""
    _grava(pro_user_id, "saida", 100)
    _grava(pro_user_id, "entrada", 300, categoria="rendimentos")

    mes = date.today().strftime("%Y-%m")
    linha = [b for b in compute_evolution(pro_user_id, months=1) if b["month"] == mes]
    assert linha, compute_evolution(pro_user_id, months=1)
    assert (linha[0]["income"], linha[0]["expense"]) == (300.0, 100.0), linha
    assert linha[0]["net"] == 200.0, linha

    start, end = resolve_window(months=1)
    k = compute_kpis(pro_user_id, start, end)
    assert (k["total_income"], k["total_expense"]) == (300.0, 100.0), k


def test_contagem_do_historico_nao_perde_a_linha_legada(pro_user_id):
    """`compute_history_quick_stats` filtrava `('despesa','receita','saida')`: a
    linha 'entrada' não era contada NEM como receita NEM no total, então os
    cards do topo do Histórico vinham menores que a lista logo abaixo."""
    _grava(pro_user_id, "despesa", 50)
    _grava(pro_user_id, "saida", 100)
    _grava(pro_user_id, "receita", 200, categoria="rendimentos")
    _grava(pro_user_id, "entrada", 300, categoria="rendimentos")

    start, end = resolve_window(months=1)
    st = compute_history_quick_stats(pro_user_id, start, end)
    assert st["total_count"] == 4, st


def test_historico_lista_a_linha_legada_nos_dois_filtros(pro_user_id):
    """Duas portas no mesmo `list_history`: o 'all' e o filtro por tipo pedido.
    A segunda montava `tipo = 'receita'` cru — a linha 'entrada' ficava invisível
    no histórico, que é a pior classe de erro num caminho de dinheiro."""
    _grava(pro_user_id, "saida", 100)
    _grava(pro_user_id, "entrada", 300, categoria="rendimentos")
    start, end = resolve_window(months=1)
    janela = dict(from_date=start, to_date=end)

    assert list_history(pro_user_id, **janela)["total"] == 2

    receitas = list_history(pro_user_id, tipo="receita", **janela)
    assert [i["valor"] for i in receitas["items"]] == [300.0], receitas

    despesas = list_history(pro_user_id, tipo="despesa", **janela)
    assert [i["valor"] for i in despesas["items"]] == [100.0], despesas

    # e o `tipo` DEVOLVIDO tem que vir na forma moderna: o front decide cor,
    # sinal e ícone com `i.tipo === "receita"` estrito (`_historyRowHTML`,
    # frontend/dashboard.js:5932). Cru, a receita legada — agora visível — era
    # desenhada como despesa, inclusive sob o filtro "Receitas".
    assert [i["tipo"] for i in receitas["items"]] == ["receita"], receitas
    assert [i["tipo"] for i in despesas["items"]] == ["despesa"], despesas
    assert sorted(i["tipo"] for i in list_history(pro_user_id, **janela)["items"]) == [
        "despesa", "receita",
    ]


def test_os_tres_sites_novos_nao_mudam_a_base_moderna(pro_user_id):
    """Controle POSITIVO dos três — a base de 100% da produção hoje."""
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 45, "compra", None, categoria="mercado",
        criado_em=datetime.combine(date.today(), time(12, 0)),
    )
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 80, "freela", None, categoria="rendimentos",
        criado_em=datetime.combine(date.today(), time(13, 0)),
    )
    start, end = resolve_window(months=1)
    janela = dict(from_date=start, to_date=end)

    mes = date.today().strftime("%Y-%m")
    linha = [b for b in compute_evolution(pro_user_id, months=1) if b["month"] == mes][0]
    assert (linha["income"], linha["expense"]) == (80.0, 45.0), linha
    assert compute_history_quick_stats(pro_user_id, **janela)["total_count"] == 2
    assert list_history(pro_user_id, **janela)["total"] == 2
    assert [i["valor"] for i in
            list_history(pro_user_id, tipo="receita", **janela)["items"]] == [80.0]


# ── salary burn: o irmão que ainda lia só a receita moderna ────────────────

def _mes_passado(dia: int, meses: int = 1, hoje: date | None = None) -> date:
    """Um dia de N meses atrás — o `salary_burn` só olha meses FECHADOS."""
    primeiro = (hoje or date.today()).replace(day=1)
    for _ in range(meses):
        primeiro = (primeiro - timedelta(days=1)).replace(day=1)
    return primeiro.replace(day=dia)


def test_salary_burn_conta_a_receita_legada(pro_user_id):
    """`_compute_salary_burn` fixava `tipo = 'receita'` enquanto a query de
    despesa logo abaixo, no mesmo helper, já aceitava ('despesa','saida').

    A assimetria é silenciosa e enviesa para o lado errado: sem a receita legada
    o `expected_income` sai MENOR, o alvo de 80% cai junto, e o "dia em que você
    queima o salário" é reportado mais cedo do que a realidade — ou o helper
    devolve `ok=false` por achar que não houve receita nenhuma.

    Controle NEGATIVO: `AND {TIPO_RECEITA_SQL}` de volta para
    `AND tipo = 'receita'` → `expected_income` cai para 0.0 e `ok` vira False.
    """
    _grava(pro_user_id, "entrada", 1000, categoria="rendimentos", dia=_mes_passado(5))
    _grava(pro_user_id, "saida", 900, dia=_mes_passado(3))

    burn = compute_behavioral_patterns(pro_user_id, months=6)["salary_burn"]
    assert burn["expected_income"] == 1000.0, burn
    assert burn["ok"] is True, burn
    assert burn["avg_day_to_80pct"] == 3, burn


def test_salary_burn_com_receita_moderna_nao_muda(pro_user_id):
    """Controle POSITIVO — a base de 100% da produção hoje: mesma cena com as
    formas modernas responde exatamente o mesmo."""
    _grava(pro_user_id, "receita", 1000, categoria="rendimentos", dia=_mes_passado(5))
    _grava(pro_user_id, "despesa", 900, dia=_mes_passado(3))

    burn = compute_behavioral_patterns(pro_user_id, months=6)["salary_burn"]
    assert burn["expected_income"] == 1000.0, burn
    assert burn["ok"] is True, burn
    assert burn["avg_day_to_80pct"] == 3, burn


# ── e o irmão do salary_burn no caminho de FALLBACK ────────────────────────
#
# `_detect_salary_burn_fast` (db/insights.py) é o que o painel de Análise mostra
# quando o LLM cai — mesma pergunta, outro caminho. A receita lá era
# `tipo = 'receita'` cru enquanto a despesa, na função de baixo, já aceitava as
# duas formas.
#
# Chamado DIRETO de propósito: `compute_active_insights` engole exceção de
# detector (`except Exception: continue`, db/insights.py), então um teste que
# passasse por ele ficaria verde com a query quebrada.

_MSG_70_EM_50 = (
    "Já consumiu 70% da sua receita média mensal "
    "e só 50% do mês passou. Vale dar uma freada."
)


def _burn_fast(user_id, monkeypatch, progresso=50.0):
    """Fixa o progresso do mês; sem isso o gate 25–90 faz o teste passar ou
    falhar conforme o DIA em que a suíte roda."""
    monkeypatch.setattr(insights, "_month_progress_pct", lambda *_a, **_k: progresso)
    return insights._detect_salary_burn_fast(user_id)


def _renda_legada(user_id):
    _grava(user_id, "entrada", 1000, categoria="rendimentos", dia=_mes_passado(5, 1))
    _grava(user_id, "entrada", 1000, categoria="rendimentos", dia=_mes_passado(5, 2))


def test_burn_fast_conta_a_receita_legada(pro_user_id, monkeypatch):
    """Com a receita legada fora, `incomes` fica com menos de 2 meses e a função
    devolve [] — o alerta some do painel enquanto o endpoint de patterns segue
    reportando a renda. Duas telas, o mesmo número, respostas diferentes.

    Discrimina o lado da RECEITA sozinho: a despesa aqui é moderna de propósito,
    então reverter `{TIPO_DESPESA_SQL}` não mexe neste teste.

    Controle NEGATIVO: `{TIPO_RECEITA_SQL}` de volta para `tipo = 'receita'` →
    lista vazia aqui.
    """
    _renda_legada(pro_user_id)
    _grava(pro_user_id, "despesa", 700, dia=date.today())

    alertas = _burn_fast(pro_user_id, monkeypatch)
    assert len(alertas) == 1, alertas
    assert alertas[0]["type"] == "salary_burn_fast", alertas
    assert alertas[0]["severity"] == "warning", alertas
    # 700/1000 = 70% da receita esperada contra 50% do mês → gap de 20pp.
    # A mensagem carrega DOIS percentuais; comparar a string inteira é o que
    # separa "imprimiu 70 e 50" de "imprimiu o gasto nas duas posições".
    assert alertas[0]["message"] == _MSG_70_EM_50, alertas


def test_burn_fast_despesa_legada_segue_contando(pro_user_id, monkeypatch):
    """GUARDA, não conserto: o lado da despesa desta função nunca esteve quebrado.
    O predicado já era `tipo in ('despesa','saida')` e a troca por
    `{TIPO_DESPESA_SQL}` é no-op — este teste passa COM e SEM este diff.

    Ele existe para o depois: fica vermelho se alguém tirar o `'saida'` da
    constante compartilhada, ou quebrar a f-string desta query.
    """
    _grava(pro_user_id, "receita", 1000, categoria="rendimentos", dia=_mes_passado(5, 1))
    _grava(pro_user_id, "receita", 1000, categoria="rendimentos", dia=_mes_passado(5, 2))
    _grava(pro_user_id, "saida", 700, dia=date.today())

    alertas = _burn_fast(pro_user_id, monkeypatch)
    assert len(alertas) == 1, alertas
    assert alertas[0]["message"] == _MSG_70_EM_50, alertas


def test_burn_fast_com_a_base_moderna_nao_muda(pro_user_id, monkeypatch):
    """Controle POSITIVO — a base de 100% da produção hoje (zero linhas legadas):
    a mesma cena nas formas modernas tem que dar o MESMO alerta. Um alias que
    mexesse neste número seria pior que o descasamento que ele conserta."""
    _grava(pro_user_id, "receita", 1000, categoria="rendimentos", dia=_mes_passado(5, 1))
    _grava(pro_user_id, "receita", 1000, categoria="rendimentos", dia=_mes_passado(5, 2))
    _grava(pro_user_id, "despesa", 700, dia=date.today())

    alertas = _burn_fast(pro_user_id, monkeypatch)
    assert len(alertas) == 1, alertas
    assert alertas[0]["type"] == "salary_burn_fast", alertas
    assert alertas[0]["severity"] == "warning", alertas
    assert alertas[0]["message"] == _MSG_70_EM_50, alertas


def test_burn_fast_sem_gap_nao_alerta(pro_user_id, monkeypatch):
    """Controle POSITIVO do outro lado: aceitar a forma legada não pode virar
    alerta em quem gasta no ritmo do mês (gap de 10pp, abaixo do corte de 15).

    O `[]` sozinho NÃO provaria isso — com a receita legada invisível a função
    também devolve [], por `len(incomes) < 2`. Por isso os dois passos no mesmo
    teste, sobre a MESMA renda legada: o segundo mostra que ela foi reconhecida,
    e aí o `[]` do primeiro só pode ter vindo do gap.
    """
    _renda_legada(pro_user_id)
    _grava(pro_user_id, "despesa", 600, dia=date.today())

    # 600/1000 = 60% contra 50% do mês → gap de 10pp, abaixo do corte
    assert _burn_fast(pro_user_id, monkeypatch) == []

    # +100 → 70%, gap de 20pp: agora alerta. Este passo é o controle negativo
    # embutido — com a receita legada fora do filtro ele fica vermelho.
    _grava(pro_user_id, "despesa", 100, dia=date.today())
    alertas = _burn_fast(pro_user_id, monkeypatch)
    assert len(alertas) == 1, alertas
    assert alertas[0]["message"] == _MSG_70_EM_50, alertas


# ── e o irmão convertido junto: _detect_category_spike ─────────────────────

def test_spike_query_convertida_ainda_executa(pro_user_id, monkeypatch):
    """GUARDA da f-string, não conserto: em `_detect_category_spike` o predicado já
    era `tipo in ('despesa','saida')` e a troca por `{TIPO_DESPESA_SQL}` é no-op —
    este teste passa COM e SEM este diff.

    Ele existe porque a conversão para f-string é o risco real: uma chave ou um
    `%` a mais e a query estoura, e `compute_active_insights` engole exceção de
    detector (`except Exception: continue`), então o insight sumiria do painel SEM
    erro nenhum. Chamado direto, é o mínimo que fica vermelho aí — e também se
    alguém tirar o `'saida'` da constante compartilhada.

    Mesmo referencial de data dos `burn_fast`: a função lê `date.today()`.
    """
    monkeypatch.setattr(insights, "_month_progress_pct", lambda *_a, **_k: 87.0)
    _grava(pro_user_id, "saida", 100, dia=_mes_passado(5, 1))
    _grava(pro_user_id, "saida", 100, dia=_mes_passado(5, 2))
    _grava(pro_user_id, "saida", 500, dia=date.today())

    spikes = insights._detect_category_spike(pro_user_id)
    assert len(spikes) == 1, spikes
    # 500 contra média de 100 nos dois meses anteriores com dado = +400%
    assert spikes[0]["type"] == "category_spike", spikes
    assert spikes[0]["key"] == "spike:mercado", spikes
    assert spikes[0]["title"] == "Mercado subiu 400% no mês", spikes
