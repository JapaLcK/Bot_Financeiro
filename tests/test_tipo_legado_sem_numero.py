"""A forma legada de `tipo` nos três leitores que não mostram NÚMERO na tela.

Irmão de `test_tipo_legado_no_dashboard.py`, separado de propósito: lá o sintoma
é um total errado na cara do usuário; aqui é uma SUGESTÃO que não aparece, e o
controle negativo precisa ser montado com mais cuidado justamente porque não há
número para comparar.

| leitor                        | o que a linha legada tira do usuário          |
|-------------------------------|-----------------------------------------------|
| `find_recurring_candidate`    | subconta meses → não pergunta "virar fixo?"   |
| `get_uncategorized_launches`  | a linha nunca vira candidata a regra          |
| `get_internal_movement_total` | aporte legado fora da projeção de saldo       |

O terceiro é sintético: `is_internal_movement` nasceu depois da forma legada, e
a produção deu ZERO linhas legadas em 27/08/2026 (ver o docstring do irmão).
Ele entra por ser o mesmo filtro cru, não por ter sintoma alcançável hoje.

Controles NEGATIVOS (um por site) — reverta o filtro para `tipo = 'despesa'` em:
  • db/recurring.py  (find_recurring_candidate)  → `test_repeticao_legada...` vermelho
  • db/categories.py (get_uncategorized_launches) → `test_linha_legada_e_candidata...` vermelho
  • db/accounts.py   (get_internal_movement_total) → `test_aporte_legado_entra...` vermelho
Controle POSITIVO: `test_base_sem_linha_legada_responde_o_mesmo` — base normal
(100% da produção hoje) devolve exatamente os mesmos números, sem contar duas vezes.
"""
from __future__ import annotations

from datetime import datetime

import db
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


# ── controle POSITIVO: base sem linha legada não muda ───────────────────────

def test_base_sem_linha_legada_responde_o_mesmo(user_id):
    """Sem esta prova o grupo passaria num código que conta a mesma linha duas
    vezes — os três leitores têm que devolver o valor de sempre numa base
    100% moderna, que é a produção de hoje."""
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


# ── auxiliares ──────────────────────────────────────────────────────────────

def _ano_mes_hoje():
    h = today_tz()
    return (h.year, h.month, h.day)


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
