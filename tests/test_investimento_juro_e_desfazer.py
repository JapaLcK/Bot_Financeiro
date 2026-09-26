"""Resgate não pula juro; desfazer devolve o cursor e só desfaz o ÚLTIMO movimento.

Q43: investimento e caixinha manuais não rendem mais. O primeiro toque depois do
deploy é a acumulação FINAL, e dias sem índice publicado nela são descartados
(decisão do dono). Os testes dos defeitos 1 e 2 que esperavam o juro "depois"
agora provam que ele não vem: o resgate é o primeiro toque, congela o ativo.

Três defeitos, cada um com o grupo de testes e o controle negativo que o discrimina:

1. Resgate setava `last_date=hoje` nos lotes depois de um accrual que só avança até o
   último índice publicado: o juro dos dias sem índice sumia do saldo que ficou.
2. Desfazer não restaurava `last_date`: um accrual entre resgate e desfazer deixava o
   cursor à frente, e o saldo restaurado nunca recebia o juro daqueles dias.
3. Desfazer escrevia o `before` absoluto por cima de movimentos posteriores (criava
   dinheiro). Agora só o último movimento do investimento se desfaz.
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

import db
import db.investments as investments_db
import db.pockets as pockets_db

T = datetime.now(investments_db._tz()).date()


def _cdi(monkeypatch, mapa):
    monkeypatch.setattr(db, "_get_cdi_daily_map", lambda _c, _s, _e: dict(mapa))


def _investimento_com_lote(uid, nome, period, rate, last_date, balance=1000, legado=True):
    """`legado=True`: investimento anterior à Q43 (marcador NULL, falta a final)."""
    _, inv_id, _ = db.create_investment_db(uid, nome, rate=rate, period=period,
                                           tax_profile="exempt_ir_iof")
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """insert into investment_lots(user_id, investment_id, principal_initial,
                   principal_remaining, balance, opened_at, last_date, status)
               values (%s,%s,%s,%s,%s,%s,%s,'open')""",
            (uid, inv_id, balance, balance, balance, T - timedelta(days=60), last_date))
        cur.execute("update investments set balance=%s, last_date=%s, "
                    "interest_frozen_at = case when %s then null else interest_frozen_at end "
                    "where id=%s and user_id=%s",
                    (balance, last_date, legado, inv_id, uid))
        conn.commit()
    return inv_id


def _lotes(uid, inv_id):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select balance, principal_remaining, status, last_date from investment_lots "
                    "where user_id=%s and investment_id=%s order by opened_at, id", (uid, inv_id))
        return [tuple(r.values()) for r in cur.fetchall()]


def _estado(uid):
    """Tudo o que uma recusa não pode mexer: conta, lotes, agregados, launches."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select balance from accounts where user_id=%s", (uid,))
        conta = cur.fetchone()["balance"]
        cur.execute("select id, balance, principal_remaining, status, last_date from investment_lots "
                    "where user_id=%s order by id", (uid,))
        lotes = [tuple(r.values()) for r in cur.fetchall()]
        cur.execute("select name, balance from investments where user_id=%s order by name", (uid,))
        invs = [tuple(r.values()) for r in cur.fetchall()]
        cur.execute("select count(*) as n from launches where user_id=%s", (uid,))
        return conta, lotes, invs, cur.fetchone()["n"]


def _resgate(uid, nome, valor=None):
    if valor is None:
        return db.investment_withdraw_to_account(uid, nome, withdraw_all=True)[0]
    return db.investment_withdraw_to_account(uid, nome, valor)[0]


def _cursor_legado(uid, launch_id):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update launches set efeitos = efeitos #- "
                    "'{investment_lot_withdrawals,0,before,last_date}' where id=%s and user_id=%s",
                    (launch_id, uid))
        conn.commit()


# ── Defeito 1: resgate deixa o cursor onde o accrual parou ────────────────────

def test_resgate_parcial_com_ipca_atrasado_descarta_o_juro_sem_indice_q43(user_id, monkeypatch):
    # IPCA é mensal: `_growth_for_period` aplica cada mês `M` (1º dia) com
    # last_date < M <= hoje, fator (1+ipca) * (1+spread)^(1/12).
    d0 = (T.replace(day=1) - timedelta(days=1)).replace(day=1)
    ipca = {}
    monkeypatch.setattr(investments_db, "_get_ipca_monthly_map", lambda _c, _s, _e: dict(ipca))
    inv = _investimento_com_lote(user_id, "tesouro ipca", "ipca_spread", 0.06, d0)
    _resgate(user_id, "tesouro ipca", 400)
    assert _lotes(user_id, inv) == [(Decimal("600"), Decimal("600"), "open", d0)]

    ipca[T.replace(day=1)] = 0.50
    db.accrue_all_investments(user_id, today=T)
    assert _lotes(user_id, inv) == [(Decimal("600"), Decimal("600"), "open", d0)]


def test_resgate_parcial_com_cdi_atrasado_descarta_o_juro_sem_indice_q43(user_id, monkeypatch):
    # CDI: um fator por DIA do mapa (o mapa é o que o BCB publicou, só dias úteis
    # na vida real); rate=1.0 = 100% do CDI.
    d0 = T - timedelta(days=7)
    cdi = {}
    _cdi(monkeypatch, cdi)
    inv = _investimento_com_lote(user_id, "cdb", "cdi", 1.0, d0)
    _resgate(user_id, "cdb", 400)
    assert _lotes(user_id, inv)[0][3] == d0

    cdi.update({d0 + timedelta(days=i): 0.05 for i in range(1, 8)})
    db.accrue_all_investments(user_id, today=T)
    assert _lotes(user_id, inv) == [(Decimal("600"), Decimal("600"), "open", d0)]


def test_resgate_de_lote_com_juro_ate_hoje_nao_mexe_no_cursor(user_id, monkeypatch):
    """Positivo: lote em dia continua em dia, saldo = anterior − resgate."""
    _cdi(monkeypatch, {})
    inv = _investimento_com_lote(user_id, "cdb", "cdi", 1.0, T)
    _resgate(user_id, "cdb", 100)
    assert _lotes(user_id, inv) == [(Decimal("900"), Decimal("900"), "open", T)]


# ── Defeito 2: desfazer devolve o cursor ──────────────────────────────────────

def test_desfazer_resgate_total_com_indice_atrasado_nao_recebe_juro_depois_q43(user_id, monkeypatch):
    d0 = T - timedelta(days=7)
    cdi = {}
    _cdi(monkeypatch, cdi)
    inv = _investimento_com_lote(user_id, "cdb", "cdi", 1.0, d0)
    r = _resgate(user_id, "cdb")
    db.delete_launch_and_rollback(user_id, r)
    assert _lotes(user_id, inv) == [(Decimal("1000"), Decimal("1000"), "open", d0)]

    cdi.update({d0 + timedelta(days=i): 0.05 for i in range(1, 8)})
    db.accrue_all_investments(user_id, today=T)
    assert _lotes(user_id, inv) == [(Decimal("1000"), Decimal("1000"), "open", d0)]


def test_desfazer_resgate_parcial_depois_de_accrual_devolve_o_cursor(user_id, monkeypatch):
    d0 = T - timedelta(days=7)
    cdi = {}
    _cdi(monkeypatch, cdi)
    inv = _investimento_com_lote(user_id, "cdb", "cdi", 1.0, d0)
    r = _resgate(user_id, "cdb", 400)
    cdi.update({d0 + timedelta(days=i): 0.05 for i in range(1, 4)})
    db.accrue_all_investments(user_id, today=T)
    assert _lotes(user_id, inv) == [(Decimal("600"), Decimal("600"), "open", d0)]

    db.delete_launch_and_rollback(user_id, r)
    assert _lotes(user_id, inv) == [(Decimal("1000"), Decimal("1000"), "open", d0)]
    db.accrue_all_investments(user_id, today=T)
    assert _lotes(user_id, inv) == [(Decimal("1000"), Decimal("1000"), "open", d0)]


def test_desfazer_resgate_legado_sem_last_date_no_snapshot_continua_funcionando(user_id, monkeypatch):
    """Positivo: snapshot gravado antes do campo não mexe no cursor."""
    d0 = T - timedelta(days=7)
    _cdi(monkeypatch, {})
    inv = _investimento_com_lote(user_id, "cdb", "cdi", 1.0, d0)
    r = _resgate(user_id, "cdb", 400)
    _cursor_legado(user_id, r)
    antes = _lotes(user_id, inv)[0][3]
    db.delete_launch_and_rollback(user_id, r)
    assert _lotes(user_id, inv) == [(Decimal("1000"), Decimal("1000"), "open", antes)]


# ── Defeito 3: só o último movimento do investimento se desfaz ────────────────

def _carteira_com_cdb(uid, nome="cdb", aportes=(1000,)):
    db.add_launch_and_update_balance(uid, "receita", 2000, None, "seed")
    db.create_investment_db(uid, nome, rate=0.10, period="yearly", tax_profile="exempt_ir_iof")
    return [db.investment_deposit_from_account(uid, nome, v, "aporte")[0] for v in aportes]


def _recusa(uid, launch_id):
    antes = _estado(uid)
    with pytest.raises(db.InvestmentMovementNotLast) as exc:
        db.delete_launch_and_rollback(uid, launch_id)
    assert exc.value.motivo == "movimento_posterior"
    assert isinstance(exc.value, db.LaunchUnsafeRollback), "as portas antigas continuam pegando"
    assert _estado(uid) == antes


def test_desfazer_resgate_anterior_no_mesmo_lote_e_recusado(user_id):
    _carteira_com_cdb(user_id)
    r1 = _resgate(user_id, "cdb", 100)
    _resgate(user_id, "cdb", 100)
    _recusa(user_id, r1)


def test_desfazer_do_mais_novo_para_o_mais_velho_volta_ao_estado_inicial(user_id):
    """Positivo: na ordem certa, tudo se desfaz e o dinheiro volta inteiro."""
    (a,) = _carteira_com_cdb(user_id)
    r1 = _resgate(user_id, "cdb", 100)
    r2 = _resgate(user_id, "cdb", 100)
    for lid in (r2, r1, a):
        db.delete_launch_and_rollback(user_id, lid)
    conta, lotes, invs, _ = _estado(user_id)
    assert (conta, lotes, invs) == (Decimal("2000"), [], [("cdb", Decimal("0"))])


def test_desfazer_resgate_anterior_entre_lotes_e_recusado(user_id):
    _carteira_com_cdb(user_id, aportes=(300, 400))
    r1 = _resgate(user_id, "cdb", 300)  # fecha o lote A
    _resgate(user_id, "cdb", 100)       # só o lote B
    _recusa(user_id, r1)


def test_desfazer_aporte_anterior_e_recusado(user_id):
    a1, _a2 = _carteira_com_cdb(user_id, aportes=(300, 400))
    _recusa(user_id, a1)


def test_movimento_de_outro_investimento_ou_despesa_nao_bloqueia(user_id):
    """Positivo: a guarda é POR investimento, não por usuário."""
    _carteira_com_cdb(user_id)
    r = _resgate(user_id, "cdb", 100)
    db.create_investment_db(user_id, "tesouro", rate=0.10, period="yearly")
    db.investment_deposit_from_account(user_id, "tesouro", 50, "aporte")
    db.add_launch_and_update_balance(user_id, "despesa", 30, "mercado", "gastei")
    db.delete_launch_and_rollback(user_id, r)
    assert db.get_balance(user_id) == Decimal("2000") - 1000 - 50 - 30


def test_desfazer_criar_investimento_com_movimento_depois_e_recusado(user_id):
    """Com saldo zerado pelo resgate total, o `delete ... balance=0` do desfazer
    apagava investimento e lote e deixava aporte e resgate órfãos."""
    db.add_launch_and_update_balance(user_id, "receita", 2000, None, "seed")
    c, _, _ = db.create_investment_db(user_id, "cdb", rate=0.10, period="yearly",
                                      tax_profile="exempt_ir_iof")
    db.investment_deposit_from_account(user_id, "cdb", 1000, "aporte")
    _resgate(user_id, "cdb")
    _recusa(user_id, c)


def test_desfazer_criar_investimento_sem_movimento_depois_apaga(user_id):
    """Positivo: criar sem nada depois continua se desfazendo."""
    c, _, _ = db.create_investment_db(user_id, "cdb", rate=0.10, period="yearly")
    db.delete_launch_and_rollback(user_id, c)
    assert _estado(user_id)[2:] == ([], 0)


def test_desfazer_apagar_investimento_recriado_depois_e_recusado(user_id):
    db.create_investment_db(user_id, "cdb", rate=0.10, period="yearly")
    d, _ = db.delete_investment(user_id, "cdb")
    db.create_investment_db(user_id, "cdb", rate=0.20, period="yearly")
    _recusa(user_id, d)


def test_desfazer_apagar_investimento_sem_nada_depois_recria(user_id):
    """Positivo: o desfazer do apagar continua recriando o investimento."""
    db.create_investment_db(user_id, "cdb", rate=0.10, period="yearly")
    d, _ = db.delete_investment(user_id, "cdb")
    db.delete_launch_and_rollback(user_id, d)
    assert _estado(user_id)[2] == [("cdb", Decimal("0"))]


def test_aporte_digitado_com_outra_caixa_cai_na_mesma_linha(user_id):
    """#596: "cdb" e "CDB" são o MESMO investimento (índice `(user_id, lower(name))`).
    Criar "CDB" resolve para o "cdb" existente, o aporte "CDB" cai nele, e a guarda
    do desfazer vê o segundo aporte como posterior ao primeiro."""
    (a1,) = _carteira_com_cdb(user_id, "cdb", aportes=(100,))
    assert db.create_investment_db(user_id, "CDB", rate=0.10, period="yearly")[0] is None
    a2 = db.investment_deposit_from_account(user_id, "CDB", 200, "aporte")[0]
    assert _estado(user_id)[2] == [("cdb", Decimal("300"))]
    _recusa(user_id, a1)
    db.delete_launch_and_rollback(user_id, a2)
    assert _estado(user_id)[2] == [("cdb", Decimal("100"))]


# ── Caixinha: o saque também deixa o cursor onde o CDI parou ─────────────────

def test_saque_parcial_de_caixinha_com_cdi_atrasado_descarta_o_juro_sem_indice_q43(user_id, monkeypatch):
    d0 = T - timedelta(days=7)
    cdi = {}
    _cdi(monkeypatch, cdi)
    _, pid, _ = db.create_pocket(user_id, "viagem", interest_rate=1.0)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """insert into pocket_lots(user_id, pocket_id, principal_initial, principal_remaining,
                   balance, opened_at, last_date, status)
               values (%s,%s,1000,1000,1000,%s,%s,'open')""",
            (user_id, pid, T - timedelta(days=60), d0))
        cur.execute("update pockets set balance=1000, interest_frozen_at=null, interest_enabled=true "
                    "where id=%s and user_id=%s", (pid, user_id))
        conn.commit()
    db.pocket_withdraw_to_account(user_id, "viagem", 400)

    cdi.update({d0 + timedelta(days=i): 0.05 for i in range(1, 8)})
    with db.get_conn() as conn, conn.cursor() as cur:
        saldo = pockets_db.accrue_pocket_db(cur, user_id, pid, today=T)
        conn.commit()
    assert saldo == Decimal("600")


# ── Conversa: pelo `handle_incoming`, com estado real de outro fluxo ──────────

def test_conversa_apagar_resgate_anterior_recusa_e_o_ultimo_apaga():
    from conftest import usuario_pagante
    from tests.test_pending_rollback import _diga

    uid = usuario_pagante()
    _carteira_com_cdb(uid)
    r1 = _resgate(uid, "cdb", 100)
    r2 = _resgate(uid, "cdb", 100)
    assert "mercado" in _diga(uid, "gastei 50 no mercado").lower()
    s1, s2 = db.get_launch_user_seq(uid, r1), db.get_launch_user_seq(uid, r2)

    antes = _estado(uid)
    assert "sim" in _diga(uid, f"apagar #{s1}").lower()
    resp = _diga(uid, "sim")
    assert f"#{s1}" in resp and "não é o mais recente do investimento" in resp, resp
    assert _estado(uid) == antes

    for seq in (s2, s1):
        _diga(uid, f"apagar #{seq}")
        assert "apagado" in _diga(uid, "sim").lower()
    conta, lotes, _, _ = _estado(uid)
    assert conta == Decimal("2000") - 1000 - 50
    assert [(b, s) for _, b, _, s, _ in lotes] == [(Decimal("1000"), "open")]


def test_dashboard_apagar_resgate_anterior_responde_400_com_a_frase(user_id):
    """Porta HTTP: a subclasse tem frase própria, não a genérica da mãe."""
    from tests.test_delete_endpoints_nao_vazam import _client, _headers

    _carteira_com_cdb(user_id)
    r1 = _resgate(user_id, "cdb", 100)
    _resgate(user_id, "cdb", 100)
    resp = _client(user_id).delete(f"/launches/{user_id}/{r1}", headers=_headers())
    assert resp.status_code == 400, resp.text
    assert "não é o mais recente do investimento" in resp.json()["detail"], resp.text
