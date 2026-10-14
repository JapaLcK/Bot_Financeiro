"""B2/M2: card `fatura`, `get_open_bill`, `pay_bill` da IA e "usar saldo para pagar" do WhatsApp apontam
para a MESMA fatura (`db.fatura_a_pagar`): a do período ANTERIOR (já fechada, vence primeiro) se `open` e
com saldo; senão a do período de hoje. Faturas mais velhas nunca viram "a fatura", nem com o dia de
fechamento mudado (sem o fallback O5; limite declarado). Mais: o pagamento é atômico (débito + baixa na
mesma transação, sem pagar em dobro) e a resposta diz QUAL fatura."""
import threading
from datetime import date, timedelta

import pytest

import db
from _cards_apoio import mostrar
from conftest import usuario_pagante
from core.services.ai_chat import streaming
from core.services.ai_chat.tools import get_tool
from db.cartoes_resumo import resumo_cartoes
from db.connection import get_conn
from tests._fusao_of_helpers import uid_pro  # noqa: F401  (fixture)
from tests._pendencia_credito_helpers import diga
from test_api_app_sessao import completo
from utils_date import billing_period_for_close_day, today_tz

FECHA = 10


def periodo(delta):
    """(início, fim) do período de hoje deslocado `delta` períodos."""
    ps, pe = billing_period_for_close_day(today_tz(), FECHA)
    for _ in range(abs(delta)):
        ps, pe = (billing_period_for_close_day(ps - timedelta(days=1), FECHA) if delta < 0
                  else billing_period_for_close_day(pe + timedelta(days=1), FECHA))
    return ps, pe


def fatura(u, c, delta, total, pago=0, status="open"):
    ps, pe = periodo(delta)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into credit_bills (user_id, card_id, period_start, period_end, status, total,"
                    " paid_amount) values (%s,%s,%s,%s,%s,%s,%s) returning id", (u, c, ps, pe, status, total, pago))
        i = cur.fetchone()["id"]
        conn.commit()
    return int(i)


def estado(u):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id, status, coalesce(paid_amount,0) as pago from credit_bills where user_id=%s", (u,))
        return {r["id"]: (r["status"], float(r["pago"])) for r in cur.fetchall()}


def card(u):
    return mostrar(u, card="fatura")[0]["abas"][0]["cards"][0]["open_bill"]


def abre(u):
    return get_tool("get_open_bill").execute(u, {"card_name": "Nubank"})


def paga(u, valor):
    return get_tool("pay_bill").execute(u, {"card_name": "Nubank", "amount": valor})


@pytest.fixture
def u():
    return completo(usuario_pagante(plan="pro_max"))


def cartao(u):
    return db.create_card(u, "Nubank", FECHA, 17)


def test_1_entre_fechamento_e_vencimento_a_fechada_vence_primeiro(u):
    c = cartao(u)
    antiga, atual = fatura(u, c, -1, 200), fatura(u, c, 0, 140)
    assert db.fatura_a_pagar(u, c, today_tz()) == antiga
    assert db.get_current_open_bill_id(u, c, today_tz()) == atual  # o período não muda
    assert (card(u)["id"], card(u)["total"]) == (antiga, 200.0)
    assert abre(u)["bill_id"] == antiga
    assert "registrado" in paga(u, 200)
    assert estado(u)[antiga] == ("paid", 200.0) and estado(u)[atual] == ("open", 0.0)


def test_2_parcelado_com_futuras_usa_a_atual():
    u = completo(usuario_pagante(plan="pro_max"))
    c = cartao(u)
    db.add_credit_purchase_installments(u, c, 300, "casa", "3x", date.today(), 3)
    db.add_credit_purchase(u, c, 40, "Lazer", "avista", date.today())
    atual = db.get_current_open_bill_id(u, c, today_tz())
    assert db.fatura_a_pagar(u, c, today_tz()) == atual
    assert (card(u)["id"], card(u)["total"], abre(u)["total"]) == (atual, 140.0, 140.0)
    paga(u, 140)
    assert estado(u)[atual] == ("paid", 140.0)
    assert sorted(v for k, v in estado(u).items() if k != atual) == [("open", 0.0), ("open", 0.0)]
    assert mostrar(u, card="fatura")[0]["abas"][0]["cards"][0]["credit_used"] == 200.0  # soma tudo


def test_3_a_mais_antiga_paga_usa_a_proxima_com_saldo(u):
    c = cartao(u)
    fatura(u, c, -1, 200, 200, "paid")
    atual = fatura(u, c, 0, 140)
    assert card(u)["id"] == atual and abre(u)["bill_id"] == atual
    paga(u, 50)
    assert estado(u)[atual] == ("open", 50.0)


def test_4_sem_saldo_em_nenhuma_card_zerado_e_nada_a_pagar(u):
    c = cartao(u)
    fatura(u, c, 0, 140, 140, "paid")
    assert card(u) == {"id": None, "total": 0.0, "paid_amount": 0.0, "due_amount": 0.0, "period_end": None}
    assert "Nada para pagar" in paga(u, 10) or "Não há fatura" in paga(u, 10)
    assert [p for _, p in estado(u).values()] == [140.0]


def test_4b_cartao_sem_nenhuma_fatura_fica_zerado(u):
    cartao(u)
    assert card(u)["id"] is None and card(u)["total"] == 0.0


def test_5_uma_fatura_so_continua_igual(u):
    c = cartao(u)
    db.add_credit_purchase(u, c, 40, "Lazer", "avista", date.today())
    (bid,) = estado(u)
    assert card(u)["id"] == bid and card(u)["total"] == 40.0 and abre(u)["bill_id"] == bid
    paga(u, 15)
    assert card(u)["paid_amount"] == 15.0 and estado(u)[bid][1] == 15.0


def test_6_whatsapp_pagar_com_saldo_paga_a_que_vence_primeiro(uid_pro):
    """"usar saldo para pagar" é o texto que chega ao bloco de `credit.py` que escolhia a fatura pelo
    período (as variações de "pagar fatura X" caem antes no fluxo de escolha, inalterado)."""
    c = cartao(uid_pro)
    db.add_launch_and_update_balance(uid_pro, "receita", 1000.0, None, "seed")
    antiga, atual = fatura(uid_pro, c, -1, 200), fatura(uid_pro, c, 0, 140)
    r = diga(uid_pro, "usar saldo para pagar nubank")
    assert "realizado" in r, r
    assert estado(uid_pro)[antiga] == ("paid", 200.0) and estado(uid_pro)[atual] == ("open", 0.0)


def test_6a_whatsapp_pagar_fatura_com_duas_abertas_pergunta_qual(uid_pro):
    c = cartao(uid_pro)
    fatura(uid_pro, c, -1, 200)
    fatura(uid_pro, c, 0, 140)
    r = diga(uid_pro, "pagar fatura nubank com saldo")
    assert "Qual você quer pagar" in r and r.index("200,00") < r.index("140,00")


def test_6b_whatsapp_consulta_fatura_mostra_a_que_vence_primeiro(uid_pro):
    c = cartao(uid_pro)
    fatura(uid_pro, c, -1, 200)
    fatura(uid_pro, c, 0, 140)
    assert "200" in diga(uid_pro, "fatura")


def test_7_isolamento_a_fatura_de_b_nao_entra(u):
    c = cartao(u)
    atual = fatura(u, c, 0, 140)
    b = completo(usuario_pagante(plan="pro_max"))
    cb = db.create_card(b, "Itau", FECHA, 17)
    fatura(b, cb, -1, 999)
    assert db.fatura_a_pagar(u, c, today_tz()) == atual
    assert db.fatura_a_pagar(u, cb, today_tz()) is None  # o cartão de b, visto por u
    assert db.fatura_a_pagar(b, cb, today_tz()) != atual
    assert card(u)["total"] == 140.0 and card(b)["total"] == 999.0


# ── só a vencida mais recente ────────────────────────────────────────────────

def test_8_faturas_velhas_nunca_pagas_nao_viram_a_fatura(u):
    c = cartao(u)
    ago, set_, out, atual = (fatura(u, c, d, 100 + d) for d in (-3, -2, -1, 0))
    assert db.fatura_a_pagar(u, c, today_tz()) == out  # a do período ANTERIOR
    assert (card(u)["id"], abre(u)["bill_id"]) == (out, out)
    assert "registrado" in paga(u, 50)
    assert estado(u)[out][1] == 50.0 and estado(u)[ago] == ("open", 0.0) and estado(u)[set_] == ("open", 0.0)
    assert len(db.list_open_bills(u)) == 4  # as velhas seguem na lista do painel


def test_9_anterior_aberta_com_total_zero_nao_vence(u):
    c = cartao(u)
    fatura(u, c, -1, 0)
    atual = fatura(u, c, 0, 140)
    assert db.fatura_a_pagar(u, c, today_tz()) == atual and card(u)["total"] == 140.0


# ── mensagem diz QUAL fatura foi paga ────────────────────────────────────────

def _rotulo(delta):
    return f"Nubank — {MESES[periodo(delta)[1].month - 1]}/{periodo(delta)[1].year}"


MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro",
         "Outubro", "Novembro", "Dezembro"]


def test_10_ia_e_whatsapp_dizem_qual_fatura_foi_paga(uid_pro):
    c = cartao(uid_pro)
    db.add_launch_and_update_balance(uid_pro, "receita", 5000.0, None, "seed")
    fatura(uid_pro, c, -1, 200)
    fatura(uid_pro, c, 0, 140)
    r1 = diga(uid_pro, "usar saldo para pagar nubank")
    assert f"*{_rotulo(-1)}*" in r1, r1
    r2 = diga(uid_pro, "usar saldo para pagar nubank")  # a 2ª chamada paga a próxima: tem de dizer
    assert f"*{_rotulo(0)}*" in r2, r2


def test_10b_pay_bill_da_ia_diz_a_fatura_e_o_devido(u):
    c = cartao(u)
    fatura(u, c, -1, 200)
    r = paga(u, 50)
    assert f"da fatura {_rotulo(-1)} registrado" in r, r
    assert "R$ 150,00" in paga(u, 999) or "150,00" in paga(u, 999)


# ── corrida: dois pagamentos ao mesmo tempo ──────────────────────────────────

def saldo(u):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select balance from accounts where user_id=%s", (u,))
        return float(cur.fetchone()["balance"])


def corrida(fn, n=2):
    saida, barra = [], threading.Barrier(n)

    def w():
        barra.wait()
        saida.append(fn())
    ts = [threading.Thread(target=w) for _ in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return saida


@pytest.mark.parametrize("rodada", range(6))
def test_11_corrida_ia_paga_uma_vez_so(u, rodada):
    c = cartao(u)
    b = fatura(u, c, 0, 200)
    db.add_launch_and_update_balance(u, "receita", 5000.0, None, "seed")
    res = corrida(lambda: paga(u, 200))
    assert sum("registrado" in r for r in res) == 1, res
    assert estado(u)[b] == ("paid", 200.0) and saldo(u) == 4800.0


@pytest.mark.parametrize("rodada", range(6))
def test_12_corrida_whatsapp_paga_uma_vez_so(uid_pro, rodada):
    c = cartao(uid_pro)
    b = fatura(uid_pro, c, 0, 200)
    db.add_launch_and_update_balance(uid_pro, "receita", 5000.0, None, "seed")
    res = corrida(lambda: diga(uid_pro, "usar saldo para pagar nubank"))
    assert sum("realizado" in r for r in res) == 1, res
    assert estado(uid_pro)[b] == ("paid", 200.0) and saldo(uid_pro) == 4800.0


# ── atomicidade: falha no meio não debita; a retentativa debita uma vez ──────

class _Cur:
    def __init__(self, cur):
        self._c = cur

    def __enter__(self):
        self._c.__enter__()
        return self

    def __exit__(self, *a):
        return self._c.__exit__(*a)

    def execute(self, sql, *a, **k):
        if "set paid_amount = coalesce(paid_amount, 0) +" in sql:
            raise RuntimeError("falha injetada no update da fatura")
        return self._c.execute(sql, *a, **k)

    def __getattr__(self, n):
        return getattr(self._c, n)


class _Conn:
    def __init__(self, cm):
        self._cm = cm

    def __enter__(self):
        self._conn = self._cm.__enter__()
        return self

    def __exit__(self, *a):
        return self._cm.__exit__(*a)

    def cursor(self, *a, **k):
        return _Cur(self._conn.cursor(*a, **k))

    def __getattr__(self, n):
        return getattr(self._conn, n)


def quebra_o_update(monkeypatch):
    import db.cards as cards_mod
    real = cards_mod.get_conn
    monkeypatch.setattr(cards_mod, "get_conn", lambda *a, **k: _Conn(real(*a, **k)))


def pagamentos(u):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) n from launches where user_id=%s and categoria='pagamento_fatura'", (u,))
        return cur.fetchone()["n"]


def test_13_ia_falha_no_meio_nao_debita_e_a_retentativa_debita_uma_vez(u, monkeypatch):
    c = cartao(u)
    b = fatura(u, c, 0, 200)
    db.add_launch_and_update_balance(u, "receita", 5000.0, None, "seed")
    quebra_o_update(monkeypatch)
    assert "Não consegui" in paga(u, 200)
    assert (saldo(u), estado(u)[b], pagamentos(u)) == (5000.0, ("open", 0.0), 0)
    monkeypatch.undo()
    assert "registrado" in paga(u, 200)
    assert (saldo(u), estado(u)[b], pagamentos(u)) == (4800.0, ("paid", 200.0), 1)


def test_14_whatsapp_falha_no_meio_nao_debita_e_a_retentativa_debita_uma_vez(uid_pro, monkeypatch):
    c = cartao(uid_pro)
    b = fatura(uid_pro, c, 0, 200)
    db.add_launch_and_update_balance(uid_pro, "receita", 5000.0, None, "seed")
    quebra_o_update(monkeypatch)
    assert "realizado" not in diga(uid_pro, "usar saldo para pagar nubank")
    assert (saldo(uid_pro), estado(uid_pro)[b], pagamentos(uid_pro)) == (5000.0, ("open", 0.0), 0)
    monkeypatch.undo()
    assert "realizado" in diga(uid_pro, "usar saldo para pagar nubank")
    assert (saldo(uid_pro), estado(uid_pro)[b], pagamentos(uid_pro)) == (4800.0, ("paid", 200.0), 1)


def test_15_whatsapp_pagar_fatura_depois_da_escolha_tambem_e_atomico(uid_pro, monkeypatch):
    """`_execute_pay_bill` (credit.py:126): "pagar fatura nubank" com 2 abertas pergunta; "1" paga."""
    c = cartao(uid_pro)
    antiga = fatura(uid_pro, c, -1, 200)
    fatura(uid_pro, c, 0, 140)
    db.add_launch_and_update_balance(uid_pro, "receita", 5000.0, None, "seed")
    assert "Qual você quer pagar" in diga(uid_pro, "pagar fatura nubank")
    quebra_o_update(monkeypatch)
    assert "registrado" not in diga(uid_pro, "1")
    assert (saldo(uid_pro), estado(uid_pro)[antiga], pagamentos(uid_pro)) == (5000.0, ("open", 0.0), 0)
    monkeypatch.undo()
    assert "Qual você quer pagar" in diga(uid_pro, "pagar fatura nubank")
    assert "registrado" in diga(uid_pro, "1")
    assert (saldo(uid_pro), estado(uid_pro)[antiga], pagamentos(uid_pro)) == (4800.0, ("paid", 200.0), 1)


# ── dia de fechamento mudado ─────────────────────────────────────────────────

def test_16_fechamento_mudado_nem_a_anterior_nem_a_futura_viram_a_fatura(u):
    """Limite declarado (sem o O5): com o dia de fechamento mudado a fatura aparece zerada até o próximo
    período. Compara TOTAIS, não ids: `get_open_bill` cria a fatura do período de hoje."""
    c = cartao(u)
    fatura(u, c, -1, 200)
    fatura(u, c, 0, 140)
    fatura(u, c, 1, 100)
    novo = (FECHA + 12) % 28 + 1
    db.cards.update_card_meta(u, c, closing_day=novo)
    assert (card(u)["total"], abre(u)["total"]) == (0.0, 0.0)


def test_16b_fatura_de_seis_periodos_atras_nao_vira_a_fatura(uid_pro):
    """Cartão parado: a velha nunca paga não é "a fatura" (era o O5) e o "usar saldo" não a paga."""
    c = cartao(uid_pro)
    db.add_launch_and_update_balance(uid_pro, "receita", 1000.0, None, "seed")
    velha = fatura(uid_pro, c, -6, 100)
    assert resumo_cartoes(uid_pro)[0]["open_bill"]["id"] is None and db.fatura_a_pagar(uid_pro, c, today_tz()) != velha
    assert "Nenhuma fatura aberta para pagar" in diga(uid_pro, "usar saldo para pagar nubank")
    assert (estado(uid_pro)[velha], saldo(uid_pro)) == (("open", 0.0), 1000.0)


# ── rótulo da consulta "fatura" ──────────────────────────────────────────────

def test_17_consulta_fatura_rotula_a_pagar_ou_atual(uid_pro):
    c = cartao(uid_pro)
    fatura(uid_pro, c, -1, 200)
    fatura(uid_pro, c, 0, 140)
    assert "Fatura a pagar (Nubank)" in diga(uid_pro, "fatura")


def test_17b_so_a_atual_continua_atual(uid_pro):
    c = cartao(uid_pro)
    fatura(uid_pro, c, 0, 140)
    assert "Fatura atual (Nubank)" in diga(uid_pro, "fatura")


# ── a confirmação do pay_bill diz qual fatura ────────────────────────────────

def test_18_confirmacao_do_pay_bill_traz_o_rotulo_e_o_sim_paga_essa(u, monkeypatch):
    from core.services.ai_chat import runner
    c = cartao(u)
    antiga = fatura(u, c, -1, 200)
    fatura(u, c, 0, 140)
    hist, pergunta = runner._dispatch_tool(u, "pay_bill", {"card_name": "Nubank", "amount": 50})
    assert _rotulo(-1) in (pergunta or hist), (hist, pergunta)
    pend = db.ai_get_pending_action(u)
    assert _rotulo(-1) in pend["summary"] and pend["tool_args"].get("_bill_id") == antiga
    monkeypatch.setattr(db, "fatura_a_pagar", lambda *a, **k: None)  # o que o "sim" paga é o confirmado, não o recalculado
    assert "registrado" in get_tool("pay_bill").execute(u, pend["tool_args"])
    assert estado(u)[antiga] == ("open", 50.0)
