"""A cauda do `tipo` legado: os cinco leitores que mostravam número errado.

Mesma armadilha dos irmãos `test_tipo_legado_no_dashboard.py` e
`test_tipo_legado_na_tendencia.py`, agora fora do dashboard. `launches.tipo` tem
duas formas para a mesma coisa — a moderna ('despesa', 'receita') e a legada
('saida', 'entrada'). Nenhum escritor de hoje grava a legada, mas quem filtra só
a moderna DESCARTA ou SUBCONTA a linha antiga, sem erro e sem log:

| número na tela                  | quem lia                                  |
|---------------------------------|-------------------------------------------|
| "Gastos em <mês>" do saldo      | `get_summary_by_period_impl` (db_support) |
| manchete do Repórter            | `_month_stats` (core/services/piggy_agents)|
| "quanto gastei" / top categorias| `get_top_expense_categories` (db/accounts)|
| rodapé da lista de UM DIA       | `core/handlers/launches.py` (list_launches)|
| "📋 Hoje" do saldo              | `core/handlers/balance.py::check`          |

Os dois primeiros vinham do SQL (`TIPO_DESPESA_SQL`/`TIPO_RECEITA_SQL`/
`TIPO_CANON_SQL`, db/connection.py — a fonte única, não se cria outra); os três
últimos, de comparação em Python.

Os dois meio-consertos que provam que a categoria estava aberta, não a folha:
`_month_stats` já lia `('despesa','saida')` em `saiu` e só `'receita'` em
`entrou` — sinal do "sobrou" INVERTIDO; e o rodapé do dia (launches.py:545) lia
só a moderna dentro da MESMA função cujo rodapé sem data (launches.py:647) já
lia as duas.

Incidência: a produção deu ZERO linhas legadas em 27/08/2026 (mesma medição do
cabeçalho do irmão do dashboard). Isto fecha a classe; não muda número de
usuário nenhum hoje.

Controle NEGATIVO — um por conserto, todos injetados em caso que estava VERDE:
  • `_month_stats` de volta a `tipo = 'receita'`
      → `test_manchete_do_reporter_nao_inverte_o_sinal` (sobrou +150 × −150)
  • `get_summary_by_period_impl` de volta a `select tipo ... group by tipo`
      → `test_resumo_do_periodo_nao_descarta_a_linha_legada` (despesa 150 × 50)
  • `get_top_expense_categories` de volta a `and tipo = 'despesa'`
      → `test_quanto_gastei_soma_a_linha_legada` (mercado 150 × 50)
  • `launches.py:545-546` de volta a `== "despesa"` / `== "receita"`
      → `test_rodape_do_dia_soma_a_linha_legada` ("R$ 150,00" × "R$ 50,00")
  • `balance.py:32` de volta a `== "despesa"`
      → `test_hoje_do_saldo_mostra_a_linha_legada` (2 linhas × 1)
Controle POSITIVO: `test_base_sem_linha_legada_nao_muda_nenhum_numero` — a MESMA
base só com a forma moderna responde exatamente o mesmo nos cinco pontos. Sem
ele o grupo passaria num código que somasse a mesma linha DUAS vezes.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

import db
import core.handle_incoming as HI
from core.services.piggy_agents import _month_stats
from core.types import IncomingMessage
from db.connection import get_conn
from utils_date import today_tz

# §0.1: o INSERT da linha legada já existe no irmão — importa, não recria.
from tests.test_tipo_legado_no_dashboard import _grava_tipo_legado, _hoje_as


@pytest.fixture
def uid_wa():
    """Usuário com id CURTO (< 1e9), que é o que o caminho do WhatsApp aceita —
    mesmo molde de `tests/test_categoria_frase_estado_vazio.py`."""
    from tests.conftest import promote_to_pro

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    yield promote_to_pro(uid)


def _base(uid, *, legado: bool) -> None:
    """A MESMA base nas duas formas: 50 + 100 de despesa e 300 de receita.

    Com `legado=True` a segunda despesa entra como 'saida' e a receita como
    'entrada' — a base que existe numa instalação antiga. Com `legado=False`,
    tudo moderno: é 100% da produção de hoje, e é o controle positivo.
    """
    db.add_launch_and_update_balance(
        uid, "despesa", 50, "mercado", None,
        categoria="mercado", criado_em=_hoje_as(10),
    )
    if legado:
        _grava_tipo_legado(uid, "saida", 100, "mercado")
        _grava_tipo_legado(uid, "entrada", 300, "rendimentos")
    else:
        db.add_launch_and_update_balance(
            uid, "despesa", 100, "feira", None,
            categoria="mercado", criado_em=_hoje_as(9),
        )
        db.add_launch_and_update_balance(
            uid, "receita", 300, "freela", None,
            categoria="rendimentos", criado_em=_hoje_as(9),
        )


# ── os cinco pontos, cada um lido como o usuário lê ─────────────────────────

def _resumo(uid) -> dict:
    hoje = today_tz()
    return db.get_summary_by_period(uid, hoje.replace(day=1), hoje)


def _stats_do_mes(uid) -> dict:
    hoje = today_tz()
    first = hoje.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    with get_conn() as conn, conn.cursor() as cur:
        return _month_stats(cur, uid, first, nxt)


def _top_categorias(uid) -> dict[str, float]:
    hoje = today_tz()
    rows = db.get_top_expense_categories(
        uid, hoje.replace(day=1), hoje, limit=1000, by_bill_month=True
    )
    return {r["categoria"]: r["total"] for r in rows}


def _diga(uid, texto: str) -> str:
    msg = IncomingMessage(platform="whatsapp", user_id=uid, text=texto,
                          message_id="m", attachments=[], external_id="e", raw={})
    saida = HI.handle_incoming(msg)
    return saida[0].text if saida else ""


def _linhas_do_hoje(resposta: str) -> list[str]:
    """Os bullets sob "📋 *Hoje*" da resposta do `saldo`."""
    linhas = resposta.splitlines()
    i = next(n for n, l in enumerate(linhas) if l.startswith("📋 *Hoje*"))
    return [l for l in linhas[i + 1:] if l.startswith("  • ")]


# ── um teste por conserto ───────────────────────────────────────────────────

def test_resumo_do_periodo_nao_descarta_a_linha_legada(uid_wa):
    """`group by tipo` cru devolve 'saida' como CHAVE PRÓPRIA, e o `if tipo in
    out` a joga fora sem exceção e sem log: o "Gastos em <mês>" sai menor."""
    _base(uid_wa, legado=True)
    r = _resumo(uid_wa)
    assert r["despesa"] == 150.0, r
    assert r["receita"] == 300.0, r


def test_manchete_do_reporter_nao_inverte_o_sinal(uid_wa):
    """Meio-conserto: `saiu` já lia as duas formas e `entrou` só a moderna, o
    que não subconta — INVERTE o sinal do "sobrou" da manchete do mês."""
    _base(uid_wa, legado=True)
    s = _stats_do_mes(uid_wa)
    assert s["entrou"] == 300.0, s
    assert s["saiu"] == 150.0, s
    assert s["sobrou"] == 150.0, s


def test_quanto_gastei_soma_a_linha_legada(uid_wa):
    """As top categorias do "quanto gastei" saem do mesmo `and tipo='despesa'`."""
    _base(uid_wa, legado=True)
    assert _top_categorias(uid_wa) == {"mercado": 150.0}


def test_rodape_do_dia_soma_a_linha_legada(uid_wa):
    """Pela CONVERSA: o rodapé da lista de UM DIA discordava do rodapé da lista
    sem data, que é a mesma função e já somava as duas formas."""
    _base(uid_wa, legado=True)
    resposta = _diga(uid_wa, "lancamentos de hoje")
    assert "💸 Gastos: R$ 150,00" in resposta, resposta
    assert "💰 Receitas: R$ 300,00" in resposta, resposta


def test_hoje_do_saldo_mostra_a_linha_legada(uid_wa):
    """Pela CONVERSA: no `saldo`, a linha legada SUMIA da lista "📋 Hoje"."""
    _base(uid_wa, legado=True)
    resposta = _diga(uid_wa, "saldo")
    assert len(_linhas_do_hoje(resposta)) == 2, resposta
    assert "R$ 150,00" in resposta, resposta  # "Gastos em <mês>", via db_support


# ── controle POSITIVO ───────────────────────────────────────────────────────

def test_base_sem_linha_legada_nao_muda_nenhum_numero(uid_wa):
    """A base de 100% da produção de hoje (zero linhas legadas) responde
    EXATAMENTE o mesmo nos cinco pontos. É o que prova que canonizar não passou
    a contar a mesma linha duas vezes."""
    _base(uid_wa, legado=False)

    r = _resumo(uid_wa)
    assert (r["despesa"], r["receita"]) == (150.0, 300.0), r

    s = _stats_do_mes(uid_wa)
    assert (s["entrou"], s["saiu"], s["sobrou"]) == (300.0, 150.0, 150.0), s

    assert _top_categorias(uid_wa) == {"mercado": 150.0}

    lista = _diga(uid_wa, "lancamentos de hoje")
    assert "💸 Gastos: R$ 150,00" in lista, lista
    assert "💰 Receitas: R$ 300,00" in lista, lista

    saldo = _diga(uid_wa, "saldo")
    assert len(_linhas_do_hoje(saldo)) == 2, saldo
    assert "R$ 150,00" in saldo, saldo
