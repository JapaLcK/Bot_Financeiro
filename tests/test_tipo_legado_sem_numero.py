"""A forma legada de `tipo` nos quatro leitores que não mostram NÚMERO na tela.

Irmão de `test_tipo_legado_no_dashboard.py`, separado de propósito: lá o sintoma
é um total errado na cara do usuário; aqui é uma SUGESTÃO que não aparece, e o
controle negativo precisa ser montado com mais cuidado justamente porque não há
número para comparar.

| leitor                        | o que a linha legada tira do usuário          |
|-------------------------------|-----------------------------------------------|
| `find_recurring_candidate`    | subconta meses → não pergunta "virar fixo?"   |
| `get_uncategorized_launches`  | a linha nunca vira candidata a regra          |
| `get_internal_movement_total` | aporte legado fora da projeção de saldo       |
| `list_launches_by_tipo`       | bot pergunta o valor recorrente que já sabe   |

O terceiro NÃO é sintético — a versão anterior deste docstring dizia o oposto do
que o código faz. A migração retroativa de `db/schema.py:245-256` marca
`is_internal_movement` por CATEGORIA, sem olhar o `tipo`, e roda a cada
`init_db()`; então uma linha legada com categoria 'investimentos' (ou
'criptomoedas', 'bitcoin'…) sai de lá marcada como movimento interno. Medido num
banco descartável, uma única linha `tipo='saida'`/`categoria='investimentos'`:

    antes da migracao : is_internal_movement=False
    DEPOIS da migracao: is_internal_movement=True
    get_internal_movement_total: 500.0

O que continua valendo é que a produção deu ZERO linhas legadas em 27/08/2026
(ver o docstring do irmão) — o caminho é alcançável, a base de hoje é que está
vazia. `test_migracao_marca_a_linha_legada_como_interna` prende o mecanismo.

O quarto (`list_launches_by_tipo`, db/accounts.py:172) é o único em que o `tipo`
é PARÂMETRO e não literal: o conserto é `TIPO_CANON_SQL` no lado da coluna.

Controles NEGATIVOS (um por site) — reverta o filtro para `tipo = 'despesa'` em:
  • db/recurring.py  (find_recurring_candidate)  → `test_repeticao_legada...` vermelho
  • db/categories.py (get_uncategorized_launches) → `test_linha_legada_e_candidata...` vermelho
  • db/accounts.py   (get_internal_movement_total) → `test_aporte_legado_entra...` vermelho
  • db/accounts.py   (list_launches_by_tipo, `{TIPO_CANON_SQL}=%s` → `tipo=%s`)
    → `test_valor_recorrente_legado_e_sugerido` vermelho (None != 1200.0)
Controles POSITIVOS — são DOIS, porque um só não separa as duas maneiras de errar:
  • `test_base_sem_linha_legada_responde_o_mesmo` (não alargou de menos): base
    normal, 100% moderna, devolve os mesmos números;
  • `test_receita_nao_conta_como_despesa` (não alargou de MAIS): trocar os quatro
    filtros por `tipo is not null` deixaria receita/entrada/aporte entrarem como
    despesa, e sem este caso o grupo ficava verde nessa mutação (medido: 5 passed).

TETO MEDIDO do positivo "não alargou de menos" — ele pega 2 dos 4 sites, e os
outros 2 são cegos por construção da FUNÇÃO, não do teste. Medido injetando
`from launches, (values (1),(2)) as _dup(n)` (cada linha exatamente duas vezes)
em UM site por vez, e rodando só `test_base_sem_linha_legada_responde_o_mesmo`:

  db/categories.py  get_uncategorized_launches   → FAILED  (2 notas, esperava 1)
  db/accounts.py    get_internal_movement_total  → FAILED  (140.0 != 70.0)
  db/accounts.py    list_launches_by_tipo        → passed  (cego)
  db/recurring.py   find_recurring_candidate     → cego, não medido

Os dois cegos não têm conserto no teste: `find_recurring_candidate` devolve
`len(months)` sobre um `set`, e `infer_recurring_value` é um argmax sobre um
`Counter` — duplicação uniforme não muda nem conjunto nem qual valor é o mais
frequente. Para esses dois, o que a duplicação alcançaria é a janela `limit=200`
(200 linhas duplicadas = 100 reais), e isso precisa de outro método, não de mais
um caso aqui.
"""
from __future__ import annotations

from datetime import datetime

import db
from core.handlers.launches import infer_recurring_value
from db.accounts import add_launch_and_update_balance, get_internal_movement_total
from db.categories import get_uncategorized_launches
from db.recurring import find_recurring_candidate
from utils_date import _tz, today_tz

from tests.test_tipo_legado_no_dashboard import _grava_tipo_legado, _hoje_as


def _add_moderno(user_id, valor, alvo, categoria, ano, mes, dia=10):
    return add_launch_and_update_balance(
        user_id, "despesa", valor, alvo, alvo, categoria,
        criado_em=datetime(ano, mes, dia, 12, 0, tzinfo=_tz()),
    )


# ── 1) sugestão de gasto fixo ───────────────────────────────────────────────

def test_repeticao_legada_conta_como_mes_de_evidencia(user_id):
    """Duas ocorrências legadas em meses distintos são a MESMA evidência que
    duas modernas: `>= 1` é o que faz o bot perguntar "quer virar gasto fixo?"."""
    _grava_tipo_legado(user_id, "saida", 39.90, "assinaturas",
                       nota="Netflix", criado_em=datetime(2026, 6, 10, 12, 0))
    _grava_tipo_legado(user_id, "saida", 39.90, "assinaturas",
                       nota="Netflix", criado_em=datetime(2026, 7, 10, 12, 0))

    n = find_recurring_candidate(
        user_id, "netflix", 39.90, current_year=2026, current_month=8,
    )
    assert n == 2, n


def test_mes_legado_soma_com_mes_moderno(user_id):
    """O caso que mais dói: a base tem UMA linha de cada forma. Filtrando só a
    moderna sobra 1 mês — abaixo do limiar — e a sugestão nunca dispara."""
    _grava_tipo_legado(user_id, "saida", 55.00, "assinaturas",
                       nota="Spotify", criado_em=datetime(2026, 6, 10, 12, 0))
    _add_moderno(user_id, 55.00, "Spotify", "assinaturas", 2026, 7)

    n = find_recurring_candidate(
        user_id, "spotify", 55.00, current_year=2026, current_month=8,
    )
    assert n == 2, n


# ── 2) candidata a regra de categorização ───────────────────────────────────

def test_linha_legada_e_candidata_a_regra(user_id):
    _grava_tipo_legado(user_id, "saida", 31.00, "outros", nota="Padaria legada")
    _add_moderno(user_id, 12.00, "Padaria nova", "outros", *_ano_mes_hoje())

    notas = {l["nota"] for l in get_uncategorized_launches(user_id)}
    assert "Padaria legada" in notas, notas
    assert "Padaria nova" in notas, notas


# ── 3) aporte interno na projeção de saldo ──────────────────────────────────

def test_aporte_legado_entra_no_total_interno(user_id):
    hoje = today_tz()
    _grava_tipo_legado(user_id, "saida", 70.00, "caixinha", interno=True)
    assert get_internal_movement_total(user_id, hoje, hoje) == 70.0


def test_migracao_marca_a_linha_legada_como_interna(user_id):
    """O mecanismo que torna o caso acima ALCANÇÁVEL, e não sintético.

    A migração de `db/schema.py:245-256` roda a cada `init_db()` e marca
    `is_internal_movement` por CATEGORIA — o `tipo` não aparece no `where`, então
    a forma legada é marcada igual à moderna. Ninguém precisou gravar a flag à
    mão: o teste NÃO passa `interno=True`, quem liga é a migração.

    `init_db()` de verdade em vez de repetir o `update` aqui: repetir criaria a
    segunda cópia da regra (§0.7), e é justamente a divergência entre as duas que
    este teste existe para pegar."""
    from db import init_db

    _grava_tipo_legado(user_id, "saida", 500.00, "investimentos",
                       nota="aporte legado")
    assert _flag_interna(user_id, "aporte legado") is False, "pré-condição"

    init_db()

    assert _flag_interna(user_id, "aporte legado") is True, (
        "a migração deixou de marcar a linha legada — o caso acima virou sintético"
    )
    hoje = today_tz()
    assert get_internal_movement_total(user_id, hoje, hoje) == 500.0


# ── 4) valor recorrente auto-preenchido ─────────────────────────────────────

def test_valor_recorrente_legado_e_sugerido(user_id):
    """`infer_recurring_value` lê por `list_launches_by_tipo`, o único site em
    que o `tipo` é PARÂMETRO. Com só a forma moderna casando, o bot volta a
    PERGUNTAR "quanto foi o aluguel?" para quem já lançou o mesmo valor 4 vezes.

    4 linhas e não 2: `_RECURRING_MIN_COUNT` é 2, e sobrar folga deixa claro que
    o `None` do controle negativo é ausência de linha, não empate."""
    for mes in (5, 6, 7, 8):
        _grava_tipo_legado(user_id, "saida", 1200.00, "moradia", nota="aluguel",
                           criado_em=datetime(2026, mes, 5, 12, 0))

    assert infer_recurring_value(user_id, "despesa", "aluguel") == 1200.0


# ── controle POSITIVO (não alargou de MAIS) ─────────────────────────────────

def test_receita_nao_conta_como_despesa(user_id):
    """Alargar de menos e alargar demais são erros DIFERENTES, e o grupo só
    media o primeiro: trocar os filtros por `tipo is not null` deixava receita,
    entrada e aporte entrarem como despesa e dava `5 passed` mesmo assim.

    A base aqui é 100% NÃO-despesa. Todo leitor de despesa tem que devolver
    vazio/zero — e `list_launches_by_tipo('despesa')` não pode ver a receita."""
    _grava_tipo_legado(user_id, "entrada", 900.00, "salario", nota="Salario legado",
                       criado_em=datetime(2026, 6, 10, 12, 0))
    _grava_tipo_legado(user_id, "entrada", 900.00, "salario", nota="Salario legado",
                       criado_em=datetime(2026, 7, 10, 12, 0))
    add_launch_and_update_balance(
        user_id, "receita", 900.00, "Salario legado", "Salario legado", "outros",
        criado_em=datetime(2026, 8, 10, 12, 0, tzinfo=_tz()),
    )
    # aporte interno na forma que o produto grava (tipo próprio, nem despesa
    # nem receita): não é despesa em leitor nenhum.
    _grava_aporte_de_caixinha(user_id, 70.00)

    assert find_recurring_candidate(
        user_id, "salario legado", 900.00, current_year=2026, current_month=9,
    ) == 0
    assert get_uncategorized_launches(user_id) == []
    hoje = today_tz()
    assert get_internal_movement_total(user_id, hoje, hoje) == 0.0
    assert infer_recurring_value(user_id, "despesa", "Salario legado") is None
    # e o espelho: como RECEITA a mesma descrição é encontrada — senão o caso
    # acima passaria num código que simplesmente não acha nada.
    assert infer_recurring_value(user_id, "receita", "Salario legado") == 900.0


# ── controle POSITIVO: base sem linha legada não muda ───────────────────────

def test_base_sem_linha_legada_responde_o_mesmo(user_id):
    """Sem esta prova o grupo passaria num código que conta a mesma linha duas
    vezes — os quatro leitores têm que devolver o valor de sempre numa base
    100% moderna, que é a produção de hoje.

    TETO MEDIDO: ele pega 2 dos 4 sites (`get_uncategorized_launches` e
    `get_internal_movement_total`). Em `find_recurring_candidate` (`set` de
    meses) e em `list_launches_by_tipo` (argmax de um `Counter`) a contagem
    dupla é invisível por construção da FUNÇÃO — a tabela da medição está no
    docstring do módulo. Ali este caso prova só "o valor certo continua
    saindo"; não tente consertar isso com mais um caso."""
    _add_moderno(user_id, 39.90, "Netflix", "assinaturas", 2026, 6)
    _add_moderno(user_id, 39.90, "Netflix", "assinaturas", 2026, 7)
    assert find_recurring_candidate(
        user_id, "netflix", 39.90, current_year=2026, current_month=8,
    ) == 2

    _add_moderno(user_id, 12.00, "Padaria nova", "outros", *_ano_mes_hoje())
    notas = [l["nota"] for l in get_uncategorized_launches(user_id)]
    assert notas.count("Padaria nova") == 1, notas

    hoje = today_tz()
    _grava_interno_moderno(user_id, 70.00)
    assert get_internal_movement_total(user_id, hoje, hoje) == 70.0

    # `list_launches_by_tipo`: prova só que o valor certo continua saindo (ver o
    # TETO acima — duplicação uniforme não move um argmax).
    assert infer_recurring_value(user_id, "despesa", "Netflix") == 39.90


# ── auxiliares ──────────────────────────────────────────────────────────────

def _ano_mes_hoje():
    h = today_tz()
    return (h.year, h.month, h.day)


def _flag_interna(user_id, nota):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select is_internal_movement from launches where user_id=%s and nota=%s",
            (user_id, nota),
        )
        return cur.fetchone()["is_internal_movement"]


def _grava_aporte_de_caixinha(user_id, valor):
    """Aporte com o TIPO PRÓPRIO que db/pockets.py grava — nem despesa nem
    receita. É o que a mutação "alarga demais" arrastaria para dentro."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into launches (user_id, tipo, valor, categoria, nota, "
            "criado_em, is_internal_movement) values "
            "(%s,'deposito_caixinha',%s,'caixinha','aporte',%s,true)",
            (user_id, valor, _hoje_as(9)),
        )
        conn.commit()


def _grava_interno_moderno(user_id, valor):
    """Aporte interno na forma MODERNA. `add_launch_and_update_balance` não
    expõe `is_internal_movement`, então o espelho do legado também entra por SQL."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into launches (user_id, tipo, valor, categoria, nota, "
            "criado_em, is_internal_movement) values (%s,'despesa',%s,%s,%s,%s,true)",
            (user_id, valor, "caixinha", "aporte", _hoje_as(9)),
        )
        conn.commit()
