"""Q43: caixinha e investimento manuais param de render.

Todo ativo manual recebe UMA acumulação final (marcador `interest_frozen_at` NULL →
carimbado sob o mesmo `for update`) e depois congela. Ativo novo nasce congelado.
Dias sem índice publicado na final são descartados (decisão do dono).

Regra de todo teste de "não rende": lote retroativo E CDI publicado depois do
cursor, de modo que o código anterior renderia.
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from threading import Event

import pytest

import db
import db.investments as investments_db
from tests._espera_lock import _esperar_backend_travado
from tests.test_investimento_juro_e_desfazer import T, _cdi, _investimento_com_lote, _lotes

D0 = T - timedelta(days=7)
CDI7 = {D0 + timedelta(days=i): 0.05 for i in range(1, 8)}   # D0+1 .. T
GANHO7 = Decimal(str(1000 * 1.0005 ** 7))


def _caixinha_com_lote(uid, nome, *, legado, juro=True, balance=1000, cursor=D0):
    """Caixinha com lote de 60 dias e cursor em `cursor`. `legado`: marcador NULL."""
    _, pid, _ = db.create_pocket(uid, nome)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """insert into pocket_lots(user_id, pocket_id, principal_initial, principal_remaining,
                   balance, opened_at, last_date, status)
               values (%s,%s,%s,%s,%s,%s,%s,'open')""",
            (uid, pid, balance, balance, balance, T - timedelta(days=60), cursor))
        cur.execute(
            "update pockets set balance=%s, interest_enabled=%s, "
            "interest_frozen_at = case when %s then null else interest_frozen_at end "
            "where id=%s and user_id=%s", (balance, juro, legado, pid, uid))
        conn.commit()
    return pid


def _linha(tabela, uid, id_):
    assert tabela in ("pockets", "investments")
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"select * from {tabela} where id=%s and user_id=%s", (id_, uid))
        return cur.fetchone()


def _saldo_lotes_caixinha(uid, pid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select balance, last_date from pocket_lots where user_id=%s and pocket_id=%s "
                    "and status='open' order by id", (uid, pid))
        return [tuple(r.values()) for r in cur.fetchall()]


def _approx(valor, esperado):
    return abs(Decimal(str(valor)) - Decimal(str(esperado))) < Decimal("0.000001")


# ── A. Congelamento na função ─────────────────────────────────────────────────

def test_A1_investimento_novo_nasce_congelado_e_nao_rende(user_id, monkeypatch):
    _cdi(monkeypatch, CDI7)
    inv = _investimento_com_lote(user_id, "cdb", "cdi", 1.0, D0, legado=False)
    db.accrue_all_investments(user_id, today=T)
    assert _lotes(user_id, inv) == [(Decimal("1000"), Decimal("1000"), "open", D0)]
    row = _linha("investments", user_id, inv)
    assert (row["balance"], row["last_date"]) == (Decimal("1000"), D0)


def test_A2_caixinha_nova_nasce_congelada_e_nao_rende(user_id, monkeypatch):
    _cdi(monkeypatch, CDI7)
    pid = _caixinha_com_lote(user_id, "viagem", legado=False)
    row = _linha("pockets", user_id, pid)
    assert row["interest_frozen_at"] is not None
    # juro=True isola o marcador: sem ele, `interest_enabled` sozinho deixaria render.
    assert row["interest_enabled"] is True
    db.accrue_all_pockets(user_id, today=T)
    assert _saldo_lotes_caixinha(user_id, pid) == [(Decimal("1000"), D0)]
    assert _linha("pockets", user_id, pid)["balance"] == Decimal("1000")


def test_A3_aporte_retroativo_em_investimento_congelado_nao_rende(user_id, monkeypatch):
    _cdi(monkeypatch, {T - timedelta(days=i): 0.05 for i in range(0, 30)})
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    _, inv, _ = db.create_investment_db(user_id, "cdb", rate=1.0, period="cdi",
                                        tax_profile="exempt_ir_iof")
    db.investment_deposit_from_account(user_id, "cdb", 500, "aporte",
                                       purchase_date=T - timedelta(days=30))
    db.accrue_all_investments(user_id, today=T)
    assert [x[0] for x in _lotes(user_id, inv)] == [Decimal("500")]
    assert _linha("investments", user_id, inv)["balance"] == Decimal("500")


# A4 (sem projeção) mora em tests/test_db_investments.py::test_accrue_all_nao_projeta_rendimento_q43.


# ── B. Acumulação final exatamente uma vez ────────────────────────────────────

def test_B1_final_rende_uma_vez_e_carimba(user_id, monkeypatch):
    cdi = dict(CDI7)
    _cdi(monkeypatch, cdi)
    inv = _investimento_com_lote(user_id, "cdb", "cdi", 1.0, D0)
    assert _linha("investments", user_id, inv)["interest_frozen_at"] is None
    db.accrue_all_investments(user_id, today=T)
    saldo, _, _, cursor = _lotes(user_id, inv)[0]
    assert _approx(saldo, GANHO7) and cursor == T
    assert _linha("investments", user_id, inv)["interest_frozen_at"] is not None

    cdi.update({T + timedelta(days=i): 0.05 for i in range(1, 4)})
    db.accrue_all_investments(user_id, today=T + timedelta(days=3))
    assert _lotes(user_id, inv)[0][0] == saldo and _lotes(user_id, inv)[0][3] == T


def test_B2_concorrencia_rende_uma_vez_so(user_id, monkeypatch):
    """Thread 1 para dentro do `_growth_for_period` segurando a linha (today=T-3,
    4 dias). Thread 2 chama com today=T: se não enxergar o carimbo, renderia os 3
    dias seguintes."""
    _cdi(monkeypatch, CDI7)
    inv = _investimento_com_lote(user_id, "cdb", "cdi", 1.0, D0)
    segurando, original = Event(), investments_db._growth_for_period

    def pausa(*a, **k):
        if not segurando.is_set():
            segurando.set()
            _esperar_backend_travado(5)
        return original(*a, **k)

    monkeypatch.setattr(investments_db, "_growth_for_period", pausa)

    def segunda():
        assert segurando.wait(5)
        return db.accrue_all_investments(user_id, today=T)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(db.accrue_all_investments, user_id, today=T - timedelta(days=3))
        f2 = pool.submit(segunda)
        f1.result(timeout=20), f2.result(timeout=20)
    assert _approx(_lotes(user_id, inv)[0][0], Decimal(str(1000 * 1.0005 ** 4)))


def test_B3_caixinha_legada_sem_juro_so_carimba(user_id, monkeypatch):
    _cdi(monkeypatch, CDI7)
    pid = _caixinha_com_lote(user_id, "viagem", legado=True, juro=False)
    db.accrue_all_pockets(user_id, today=T)
    assert _saldo_lotes_caixinha(user_id, pid) == [(Decimal("1000"), D0)]
    assert _linha("pockets", user_id, pid)["interest_frozen_at"] is not None


def test_B4_caixinha_legada_com_juro_rende_uma_vez_e_desliga(user_id, monkeypatch):
    from tests.test_delete_endpoints_nao_vazam import _client

    cdi = dict(CDI7)
    _cdi(monkeypatch, cdi)
    pid = _caixinha_com_lote(user_id, "viagem", legado=True)
    db.accrue_all_pockets(user_id, today=T)
    row = _linha("pockets", user_id, pid)
    assert _approx(row["balance"], GANHO7)
    assert row["interest_enabled"] is False and row["interest_frozen_at"] is not None

    cdi.update({T + timedelta(days=i): 0.05 for i in range(1, 4)})
    db.accrue_all_pockets(user_id, today=T + timedelta(days=3))
    assert _linha("pockets", user_id, pid)["balance"] == row["balance"]

    goals = _client(user_id).get(f"/goals/{user_id}/status").json()["goals"]
    assert [g["interest_enabled"] for g in goals if g["id"] == pid] == [False]


def test_B5_resgate_total_de_legado_inclui_o_ganho_final(user_id, monkeypatch):
    _cdi(monkeypatch, CDI7)
    _investimento_com_lote(user_id, "cdb", "cdi", 1.0, D0)
    antes = db.get_balance(user_id)
    _, _, bal_inv, _, taxes, _ = db.investment_withdraw_to_account(user_id, "cdb", withdraw_all=True)
    assert _approx(taxes["gross"], GANHO7) and bal_inv == 0
    assert _approx(db.get_balance(user_id) - antes, GANHO7)


# ── C. Caminhos reais não rendem ──────────────────────────────────────────────

def _atrasa_lotes(uid, tabela):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"update {tabela} set opened_at=%s, last_date=%s where user_id=%s",
                    (T - timedelta(days=60), D0, uid))
        conn.commit()


def test_C1_dashboard_http_nao_rende(user_id, monkeypatch):
    from tests.test_delete_endpoints_nao_vazam import _client, _headers

    _cdi(monkeypatch, CDI7)
    db.add_launch_and_update_balance(user_id, "receita", 3000, None, "seed")
    c, h = _client(user_id), _headers()
    # O JS em cache ainda manda interest_enabled=true.
    assert c.post(f"/pockets/{user_id}", json={"name": "viagem", "interest_enabled": True,
                                               "interest_rate": 1.0}, headers=h).status_code == 200
    assert c.post(f"/pockets/{user_id}/viagem/deposit", json={"amount": 1000}, headers=h).status_code == 200
    r = c.post(f"/investments/{user_id}", headers=h, json={
        "name": "cdb", "rate": 1.0, "period": "cdi", "initial_amount": 1000,
        "purchase_date": D0.isoformat(), "tax_profile": "exempt_ir_iof"})
    assert r.status_code == 200, r.text
    _atrasa_lotes(user_id, "pocket_lots")

    r = c.post(f"/pockets/{user_id}/viagem/withdraw", json={"amount": 400}, headers=h)
    assert r.status_code == 200 and float(r.json()["pocket_balance"]) == 600
    r = c.post(f"/pockets/{user_id}/viagem/deposit", json={"amount": 100}, headers=h)
    assert float(r.json()["pocket_balance"]) == 700
    r = c.post(f"/investments/{user_id}/deposit", json={"name": "cdb", "amount": 200}, headers=h)
    assert r.status_code == 200 and float(r.json()["investment_balance"]) == 1200
    r = c.post(f"/investments/{user_id}/withdraw", json={"name": "cdb", "amount": 300}, headers=h)
    assert r.status_code == 200 and float(r.json()["investment_balance"]) == 900
    assert r.json()["tax_summary"]["gross"] == 300.0
    assert db.get_balance(user_id) == Decimal("3000") - 1000 - 1000 + 400 - 100 - 200 + 300


def test_C2_conversa_whatsapp_nao_rende(monkeypatch):
    from conftest import usuario_pagante
    from tests.test_pending_rollback import _diga

    _cdi(monkeypatch, CDI7)
    uid = usuario_pagante()
    db.add_launch_and_update_balance(uid, "receita", 3000, None, "seed")
    db.create_investment_db(uid, "CDB", rate=1.0, period="cdi", tax_profile="exempt_ir_iof",
                            initial_amount=1000, purchase_date=D0)
    # "coloquei R$ 50,00 ..." cai na IA (sem chave aqui) — por isso "guardei". Acento
    # em nome de caixinha tem defeito próprio (cria "poupanca", depósito procura
    # "poupança"), fora da Q43; o acento fica no gasto.
    for frase in ("criar caixinha viagem", "coloquei 300 na caixinha viagem"):
        assert "✅" in _diga(uid, frase), frase
    _atrasa_lotes(uid, "pocket_lots")
    assert "mercado" in _diga(uid, "gastei 50 no mercado").lower()
    for frase in ("apliquei 200 no investimento CDB", "retirei 100 da caixinha viagem",
                  "gastei R$ 50,00 no açougue", "retirei 100 do investimento CDB",
                  "guardei R$ 50,00 na caixinha viagem"):
        assert "R$" in _diga(uid, frase), frase

    pockets = {p["name"]: p["balance"] for p in db.list_pockets(uid)}
    invs = {i["name"]: i["balance"] for i in db.list_investments(uid)}
    assert (pockets, invs) == ({"viagem": Decimal("250")}, {"CDB": Decimal("1100")})
    assert db.get_balance(uid) == Decimal("3000") - 1000 - 300 - 50 - 200 + 100 - 50 + 100 - 50


# ── D. Caixinha do banco não é afetada (controle positivo) ────────────────────

def test_D_caixinha_do_banco_segue_o_banco_e_nao_carimba(user_id):
    from tests.test_of_caixinha_autoimport import _save, _seed_connection

    conn_id = _seed_connection(user_id)
    raw = {"id": "cxq43", "name": "Caixinha Reserva", "type": "FIXED_INCOME", "subtype": "CDB"}
    _save(conn_id, [{**raw, "balance": 1000.0}])
    db.sync_open_finance_caixinhas(conn_id, user_id)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pockets set interest_frozen_at=null where user_id=%s returning id", (user_id,))
        pid = cur.fetchone()["id"]
        conn.commit()

    assert [p["balance"] for p in db.accrue_all_pockets(user_id)] == [Decimal("1000")]
    assert _linha("pockets", user_id, pid)["interest_frozen_at"] is None
    _save(conn_id, [{**raw, "balance": 1250.0}])
    db.sync_open_finance_caixinhas(conn_id, user_id)
    assert [p["balance"] for p in db.accrue_all_pockets(user_id)] == [Decimal("1250")]


def _espelho_desvinculado(uid, item):
    """Espelho LEGADO do banco (ver tests/test_of_caixinha_dinheiro.py::
    test_espelho_legado_sem_vinculo_continua_read_only): `source='open_finance'`,
    vínculo NULO, 800 espelhados e marcador NULL (anterior à Q43)."""
    from tests.test_of_caixinha_autoimport import _save, _seed_connection

    conn_id = _seed_connection(uid, item=item)
    _save(conn_id, [{"id": f"cx-{item}", "name": "Caixinha Nubank", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 800.0}])
    db.sync_open_finance_caixinhas(conn_id, uid)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pockets set of_investment_id=null, interest_frozen_at=null "
                    "where user_id=%s and source='open_finance' returning id", (uid,))
        pid = cur.fetchone()["id"]
        conn.commit()
    return pid


def _espelho_intacto(uid, pid):
    row = _linha("pockets", uid, pid)
    assert _saldo_lotes_caixinha(uid, pid) == []          # o espelho não virou lote
    assert row["balance"] == Decimal("800") and row["interest_frozen_at"] is None


def test_D2_espelho_desvinculado_nao_vira_lote_nem_carimba(user_id, monkeypatch):
    """Controle positivo: a caixinha manual legada do mesmo usuário é finalizada."""
    _cdi(monkeypatch, CDI7)
    pid = _espelho_desvinculado(user_id, "q43-d2")
    manual = _caixinha_com_lote(user_id, "viagem", legado=True, juro=False)
    db.accrue_all_pockets(user_id, today=T)
    _espelho_intacto(user_id, pid)
    assert _linha("pockets", user_id, manual)["interest_frozen_at"] is not None


def test_D3_varredura_ignora_espelho_desvinculado(user_id):
    _espelho_desvinculado(user_id, "q43-d3")
    assert user_id not in db.list_users_with_unfrozen_interest()


def test_D4_scheduler_com_investimento_legado_nao_toca_no_espelho(user_id, monkeypatch):
    """O usuário entra na varredura pelo investimento; o `accrue_all_pockets` da
    mesma rodada passa pelo espelho."""
    from core.services import investment_scheduler

    _cdi(monkeypatch, CDI7)
    pid = _espelho_desvinculado(user_id, "q43-d4")
    inv = _investimento_com_lote(user_id, "cdb", "cdi", 1.0, D0)
    assert user_id in db.list_users_with_unfrozen_interest()
    monkeypatch.setattr(db, "list_users_with_unfrozen_interest", lambda: [user_id])
    assert investment_scheduler.accrue_all_users_investments()["failed"] == 0
    _espelho_intacto(user_id, pid)
    assert _linha("investments", user_id, inv)["interest_frozen_at"] is not None


# ── E. Varredura e isolamento ─────────────────────────────────────────────────

def test_E_varredura_so_quem_falta_e_sem_vazar(monkeypatch):
    from conftest import usuario_pagante
    from core.services import investment_scheduler
    from tests.test_of_caixinha_autoimport import _save, _seed_connection

    _cdi(monkeypatch, CDI7)
    a, b, banco, frio = (usuario_pagante() for _ in range(4))
    inv_a = _investimento_com_lote(a, "cdb", "cdi", 1.0, D0)
    pid_b = _caixinha_com_lote(b, "viagem", legado=True)
    conn_id = _seed_connection(banco, item="q43-item")
    _save(conn_id, [{"id": "cxe", "name": "Caixinha E", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 10.0}])
    db.sync_open_finance_caixinhas(conn_id, banco)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pockets set interest_frozen_at=null where user_id=%s", (banco,))
        conn.commit()
    _investimento_com_lote(frio, "cdb", "cdi", 1.0, D0, legado=False)
    _caixinha_com_lote(frio, "viagem", legado=False)

    nossos = {a, b, banco, frio}
    lista = set(db.list_users_with_unfrozen_interest()) & nossos
    assert lista == {a, b}

    db.accrue_all_investments(a)
    db.accrue_all_pockets(a)
    assert _linha("investments", a, inv_a)["interest_frozen_at"] is not None
    assert _linha("pockets", b, pid_b)["interest_frozen_at"] is None

    real = db.list_users_with_unfrozen_interest
    monkeypatch.setattr(db, "list_users_with_unfrozen_interest",
                        lambda: [u for u in real() if u in nossos])
    assert investment_scheduler.accrue_all_users_investments()["failed"] == 0
    assert not set(real()) & nossos
    assert _approx(_linha("pockets", b, pid_b)["balance"], GANHO7)


# ── F. Quem grava interest_enabled ────────────────────────────────────────────

def test_F1_create_pocket_ignora_ligar_o_juro(user_id):
    _, pid, _ = db.create_pocket(user_id, "viagem", interest_enabled=True)
    assert _linha("pockets", user_id, pid)["interest_enabled"] is False


def test_F2_patch_meta_ignora_ligar_o_juro_e_nao_mexe_no_cursor(user_id):
    from tests.test_delete_endpoints_nao_vazam import _client, _headers

    pid = _caixinha_com_lote(user_id, "viagem", legado=False, juro=False)
    r = _client(user_id).patch(f"/pockets/{user_id}/{pid}/meta", headers=_headers(),
                               json={"interest_enabled": True, "interest_rate": 1.1})
    assert r.status_code == 200, r.text
    assert _linha("pockets", user_id, pid)["interest_enabled"] is False
    assert _saldo_lotes_caixinha(user_id, pid) == [(Decimal("1000"), D0)]


def test_F3_patch_so_de_nome_continua_funcionando(user_id):
    """Positivo."""
    from tests.test_delete_endpoints_nao_vazam import _client, _headers

    _, pid, _ = db.create_pocket(user_id, "viagem")
    r = _client(user_id).patch(f"/pockets/{user_id}/{pid}/meta", headers=_headers(),
                               json={"name": "ferias"})
    assert r.status_code == 200 and r.json()["pocket"]["name"] == "ferias"


def test_F4_post_responde_o_juro_gravado(user_id):
    from tests.test_delete_endpoints_nao_vazam import _client, _headers

    r = _client(user_id).post(f"/pockets/{user_id}", headers=_headers(),
                              json={"name": "viagem", "interest_enabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["pocket"]["interest_enabled"] is False
    assert _linha("pockets", user_id, r.json()["pocket"]["id"])["interest_enabled"] is False


def test_F4b_post_com_nome_da_legada_recusa_sem_tocar_nela(user_id):
    """Nome já existente dá 400 (#626) e a caixinha legada fica como estava."""
    from tests.test_delete_endpoints_nao_vazam import _client, _headers

    _, pid, _ = db.create_pocket(user_id, "viagem")
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pockets set interest_enabled=true, interest_frozen_at=null "
                    "where id=%s and user_id=%s", (pid, user_id))
        conn.commit()

    r = _client(user_id).post(f"/pockets/{user_id}", headers=_headers(),
                              json={"name": "viagem", "interest_enabled": False})
    assert r.status_code == 400, r.text
    linha = _linha("pockets", user_id, pid)
    assert linha["interest_enabled"] is True and linha["interest_frozen_at"] is None


def test_F5_patch_so_ligando_o_juro_e_200_sem_vazar(user_id):
    from conftest import usuario_pagante
    from tests.test_delete_endpoints_nao_vazam import _client, _headers

    _, pid, _ = db.create_pocket(user_id, "viagem")
    antes = _linha("pockets", user_id, pid)
    r = _client(user_id).patch(f"/pockets/{user_id}/{pid}/meta", headers=_headers(),
                               json={"interest_enabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["pocket"]["id"] == pid and r.json()["pocket"]["interest_enabled"] is False
    assert _linha("pockets", user_id, pid) == antes

    outro = usuario_pagante()
    r = _client(outro).patch(f"/pockets/{outro}/{pid}/meta", headers=_headers(),
                             json={"interest_enabled": True})
    assert r.status_code == 404, r.text
    r = _client(user_id).patch(f"/pockets/{user_id}/999999999/meta", headers=_headers(),
                               json={"interest_enabled": True})
    assert r.status_code == 404, r.text


@pytest.mark.parametrize("payload", [{"name": "novo nome", "interest_enabled": False},
                                     {"interest_enabled": False}])
def test_F6_patch_desligando_com_o_bcb_fora_nao_perde_o_juro(user_id, bcb, monkeypatch, payload):
    """O `saveGoal` manda `interest_enabled: false` até num renomear. Com a busca
    falhando, o PATCH não desliga: a próxima rodada completa o juro e aí desliga."""
    import db.pockets as pockets_db
    from tests.test_delete_endpoints_nao_vazam import _client, _headers

    monkeypatch.setattr(pockets_db, "_today", lambda: G_HOJE)
    bcb["semeia"](G_D0, G_K)
    pid = _caixinha_com_lote(user_id, "viagem", legado=True, cursor=G_D0)
    r = _client(user_id).patch(f"/pockets/{user_id}/{pid}/meta", headers=_headers(), json=payload)
    assert r.status_code == 200, r.text
    assert bcb["chamadas"] > 0
    row = _linha("pockets", user_id, pid)
    assert r.json()["pocket"]["name"] == row["name"] == payload.get("name", "viagem")
    assert _approx(row["balance"], G_ATE_K)
    assert row["interest_frozen_at"] is None and row["interest_enabled"] is True

    bcb["resposta"] = G_CAUDA
    db.accrue_all_pockets(user_id, today=G_HOJE)
    row = _linha("pockets", user_id, pid)
    assert _approx(row["balance"], G_TUDO)
    assert row["interest_frozen_at"] is not None and row["interest_enabled"] is False


def test_F7_patch_desligando_sem_falha_grava_false(user_id, bcb, monkeypatch):
    """Positivo: a legada que carimba e o espelho do banco (que nunca carimba) desligam."""
    import db.pockets as pockets_db
    from tests.test_delete_endpoints_nao_vazam import _client, _headers

    monkeypatch.setattr(pockets_db, "_today", lambda: G_HOJE)
    bcb["semeia"](G_D0, G_K)
    bcb["resposta"] = []
    manual = _caixinha_com_lote(user_id, "viagem", legado=True, cursor=G_D0)
    espelho = _espelho_desvinculado(user_id, "q43-f7")
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pockets set interest_enabled=true where id=%s and user_id=%s",
                    (espelho, user_id))
        conn.commit()

    for pid in (manual, espelho):
        r = _client(user_id).patch(f"/pockets/{user_id}/{pid}/meta", headers=_headers(),
                                   json={"interest_enabled": False})
        assert r.status_code == 200, r.text
        assert r.json()["pocket"]["interest_enabled"] is False
        assert _linha("pockets", user_id, pid)["interest_enabled"] is False
    assert _linha("pockets", user_id, manual)["interest_frozen_at"] is not None
    _espelho_intacto(user_id, espelho)


# ── Migração ──────────────────────────────────────────────────────────────────

def test_migracao_default_now_nas_duas_tabelas(user_id):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select table_name, column_default from information_schema.columns "
                    "where table_schema = current_schema() and column_name='interest_frozen_at' "
                    "order by table_name")
        assert [(r["table_name"], r["column_default"]) for r in cur.fetchall()] == [
            ("investments", "now()"), ("pockets", "now()")]
        cur.execute("insert into pockets(user_id, name) values (%s,'q43') "
                    "returning interest_frozen_at, interest_enabled", (user_id,))
        row = cur.fetchone()
        conn.rollback()
    assert row["interest_frozen_at"] is not None and row["interest_enabled"] is False


def test_migracao_add_e_default_no_mesmo_statement():
    """O statement do init_db, aplicado a uma tabela com linhas: as antigas ficam
    NULL (recebem a final), a nova nasce carimbada, e a 2ª subida não mexe em nada.
    Um statement só: o init_db é autocommit, e separados um INSERT no meio nasce NULL."""
    import inspect
    import re

    from db import schema

    stmts = [s for s in re.findall(r'"""(.*?)"""', inspect.getsource(schema.init_db), re.S)
             if "add column if not exists interest_frozen_at" in s]
    assert len(stmts) == 2
    with db.get_conn() as conn, conn.cursor() as cur:
        for stmt in stmts:
            cur.execute("create temp table q43_mig (id int)")
            cur.execute("insert into q43_mig values (1), (2)")
            sql = re.sub(r"alter table (pockets|investments)", "alter table q43_mig", stmt)
            cur.execute(sql)
            cur.execute("insert into q43_mig(id) values (3)")
            cur.execute("select id, interest_frozen_at from q43_mig order by id")
            antes = [tuple(r.values()) for r in cur.fetchall()]
            assert [f is None for _, f in antes] == [True, True, False], stmt
            cur.execute(sql)
            cur.execute("select id, interest_frozen_at from q43_mig order by id")
            assert [tuple(r.values()) for r in cur.fetchall()] == antes
            cur.execute("drop table q43_mig")
        conn.rollback()


# ── G. Falha de busca do índice não congela (P1 do Codex no #623) ─────────────
# CDI de verdade em `market_rates` e o BCB stubado em `_fetch_sgs_series_json`.
# Janela fixa passada: cursor em G_D0 (seg), cache até G_K (3 dias úteis), cauda
# útil 06, 09 e 10/06 até G_HOJE. Sem feriado nacional na janela.

G_D0, G_K, G_HOJE = date(2025, 6, 2), date(2025, 6, 5), date(2025, 6, 10)
G_CAUDA = [date(2025, 6, 6), date(2025, 6, 9), date(2025, 6, 10)]
G_ATE_K, G_TUDO = Decimal(str(1000 * 1.0005 ** 3)), Decimal(str(1000 * 1.0005 ** 6))


@pytest.fixture
def bcb(monkeypatch):
    """`bcb["resposta"]`: None = falha; lista de datas = o BCB publica essas
    (as do intervalo pedido, a 0,05%); dict na lista vai cru, como o BCB mandou.
    Apaga de `market_rates` só o que entrou."""
    from tests.test_funding_source import _apaga_cdi, _semeia_cdi

    criados, estado = [], {"resposta": None, "chamadas": 0}

    def fetch(_serie, ini, fim):
        estado["chamadas"] += 1
        resp = estado["resposta"](threading.current_thread().name) \
            if callable(estado["resposta"]) else estado["resposta"]
        if resp is None:
            return None
        crus = [d for d in resp if isinstance(d, dict)]
        dias = [d for d in resp if not isinstance(d, dict) and ini <= d <= fim]
        if dias:  # só o que não existia: a tabela é global, sem `user_id`
            with db.get_conn() as conn, conn.cursor() as cur:
                cur.execute("select ref_date from market_rates "
                            "where code='CDI' and ref_date = any(%s)", (dias,))
                existentes = {r["ref_date"] for r in cur.fetchall()}
            criados.extend(d for d in dias if d not in existentes)
        return [{"data": d.strftime("%d/%m/%Y"), "valor": "0.05"} for d in dias] + crus

    monkeypatch.setattr(investments_db, "_fetch_sgs_series_json", fetch)
    estado["semeia"] = lambda ini, fim: criados.extend(_semeia_cdi(ini, fim))
    estado["criados"] = criados
    yield estado
    _apaga_cdi(criados)


def test_G1_investimento_falha_nao_carimba_e_completa_depois(user_id, bcb):
    bcb["semeia"](G_D0, G_K)
    inv = _investimento_com_lote(user_id, "cdb", "cdi", 1.0, G_D0)
    db.accrue_all_investments(user_id, today=G_HOJE)
    assert bcb["chamadas"] > 0
    saldo, _, _, cursor = _lotes(user_id, inv)[0]
    assert _approx(saldo, G_ATE_K) and cursor == G_K
    assert _linha("investments", user_id, inv)["interest_frozen_at"] is None

    bcb["resposta"] = G_CAUDA  # a rede voltou: completa do cursor, sem dobrar
    db.accrue_all_investments(user_id, today=G_HOJE)
    saldo, _, _, cursor = _lotes(user_id, inv)[0]
    assert _approx(saldo, G_TUDO) and cursor == G_HOJE
    assert _linha("investments", user_id, inv)["interest_frozen_at"] is not None

    bcb["resposta"] = G_CAUDA + [G_HOJE + timedelta(days=i) for i in (1, 2, 3)]
    db.accrue_all_investments(user_id, today=G_HOJE + timedelta(days=3))
    assert _lotes(user_id, inv)[0][0] == saldo and _lotes(user_id, inv)[0][3] == G_HOJE


def test_G2_bcb_sem_valores_carimba_com_o_que_tem(user_id, bcb):
    """Positivo: `[]` é resposta, não falha."""
    bcb["semeia"](G_D0, G_K)
    bcb["resposta"] = []
    inv = _investimento_com_lote(user_id, "cdb", "cdi", 1.0, G_D0)
    db.accrue_all_investments(user_id, today=G_HOJE)
    assert bcb["chamadas"] > 0
    saldo, _, _, cursor = _lotes(user_id, inv)[0]
    assert _approx(saldo, G_ATE_K) and cursor == G_K
    assert _linha("investments", user_id, inv)["interest_frozen_at"] is not None


def test_G3_cache_com_buraco_e_falha_nao_carimba_nem_pula_o_buraco(user_id, bcb):
    """Com `return cached` na falha, o cursor ia a 05/06 por cima do 04/06, e a
    próxima busca começava em 06/06: o 04 nunca rendia."""
    buraco = date(2025, 6, 4)
    bcb["semeia"](date(2025, 6, 3), date(2025, 6, 3))
    bcb["semeia"](G_K, G_K)  # falta 04/06
    inv = _investimento_com_lote(user_id, "cdb", "cdi", 1.0, G_D0)
    db.accrue_all_investments(user_id, today=G_HOJE)
    assert bcb["chamadas"] > 0
    saldo, _, _, cursor = _lotes(user_id, inv)[0]
    assert _approx(saldo, Decimal(str(1000 * 1.0005))) and cursor == date(2025, 6, 3)
    assert _linha("investments", user_id, inv)["interest_frozen_at"] is None

    bcb["resposta"] = [buraco] + G_CAUDA  # a rede voltou: o buraco rende uma vez
    db.accrue_all_investments(user_id, today=G_HOJE)
    saldo, _, _, cursor = _lotes(user_id, inv)[0]
    assert _approx(saldo, G_TUDO) and cursor == G_HOJE
    assert _linha("investments", user_id, inv)["interest_frozen_at"] is not None


def test_G4_caixinha_falha_nao_carimba_nem_desliga_e_completa_depois(user_id, bcb):
    bcb["semeia"](G_D0, G_K)
    pid = _caixinha_com_lote(user_id, "viagem", legado=True, cursor=G_D0)
    db.accrue_all_pockets(user_id, today=G_HOJE)
    assert bcb["chamadas"] > 0
    row = _linha("pockets", user_id, pid)
    assert _approx(row["balance"], G_ATE_K)
    assert row["interest_frozen_at"] is None and row["interest_enabled"] is True

    bcb["resposta"] = G_CAUDA
    db.accrue_all_pockets(user_id, today=G_HOJE)
    row = _linha("pockets", user_id, pid)
    assert _approx(row["balance"], G_TUDO)
    assert row["interest_frozen_at"] is not None and row["interest_enabled"] is False


def test_G5_varredura_com_falha_nao_conta_erro_e_volta_na_proxima(user_id, bcb, monkeypatch):
    """O scheduler acrua com o `today` real: a janela aqui é a do relógio. O cache
    vai até o dia útil anterior ao último dia útil <= hoje, então a cauda sempre
    tem dia útil a buscar, em qualquer dia do ano."""
    from core.services import investment_scheduler
    from utils_date import is_br_business_day

    uteis = [T - timedelta(days=i) for i in range(30, -1, -1)
             if is_br_business_day(T - timedelta(days=i))]
    bcb["semeia"](T - timedelta(days=30), uteis[-2])
    inv = _investimento_com_lote(user_id, "cdb", "cdi", 1.0, T - timedelta(days=31))
    real = db.list_users_with_unfrozen_interest
    monkeypatch.setattr(db, "list_users_with_unfrozen_interest",
                        lambda: [u for u in real() if u == user_id])

    assert investment_scheduler.accrue_all_users_investments()["failed"] == 0
    assert bcb["chamadas"] > 0
    assert user_id in real()

    bcb["resposta"] = uteis
    assert investment_scheduler.accrue_all_users_investments()["failed"] == 0
    assert user_id not in real()
    assert _lotes(user_id, inv)[0][3] == uteis[-1]


def test_G6_falha_de_uma_thread_nao_segura_o_carimbo_da_outra(monkeypatch, bcb):
    """B para dentro do `_growth_for_period` (já leu o contador); A roda inteiro
    com a rede caída; B segue com a rede respondendo. Contador global faria B
    enxergar a falha de A."""
    from conftest import usuario_pagante

    bcb["semeia"](G_D0, G_K)
    bcb["resposta"] = lambda thread: G_CAUDA if thread.startswith("q43B") else None
    a, b = usuario_pagante(), usuario_pagante()
    inv_a = _investimento_com_lote(a, "cdb", "cdi", 1.0, G_D0)
    inv_b = _investimento_com_lote(b, "cdb", "cdi", 1.0, G_D0)
    dentro, libera, original = Event(), Event(), investments_db._growth_for_period

    def pausa(*args, **kw):
        if threading.current_thread().name.startswith("q43B") and not dentro.is_set():
            dentro.set()
            assert libera.wait(10)
        return original(*args, **kw)

    monkeypatch.setattr(investments_db, "_growth_for_period", pausa)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="q43B") as pool:
        fb = pool.submit(db.accrue_all_investments, b, today=G_HOJE)
        assert dentro.wait(10)
        db.accrue_all_investments(a, today=G_HOJE)
        libera.set()
        fb.result(timeout=20)

    assert _linha("investments", a, inv_a)["interest_frozen_at"] is None
    assert _approx(_lotes(a, inv_a)[0][0], G_ATE_K)
    assert _linha("investments", b, inv_b)["interest_frozen_at"] is not None
    assert _approx(_lotes(b, inv_b)[0][0], G_TUDO)


def test_G7_item_que_nao_parseia_nao_carimba_e_completa_depois(user_id, bcb):
    """Resposta com os dias da cauda E um item lixo: não virou índice inteira, é falha."""
    bcb["semeia"](G_D0, G_K)
    bcb["resposta"] = G_CAUDA + [{"erro": "x"}]
    inv = _investimento_com_lote(user_id, "cdb", "cdi", 1.0, G_D0)
    db.accrue_all_investments(user_id, today=G_HOJE)
    assert bcb["chamadas"] > 0
    assert _linha("investments", user_id, inv)["interest_frozen_at"] is None

    bcb["resposta"] = G_CAUDA  # a rede voltou limpa
    db.accrue_all_investments(user_id, today=G_HOJE)
    saldo, _, _, cursor = _lotes(user_id, inv)[0]
    assert _approx(saldo, G_TUDO) and cursor == G_HOJE
    assert _linha("investments", user_id, inv)["interest_frozen_at"] is not None


def test_G8_item_que_nao_parseia_nao_grava_memo(user_id, bcb):
    """Sem memo, a chamada seguinte no mesmo processo busca de novo em vez de
    carimbar sem os dias que faltam."""
    bcb["semeia"](G_D0, G_K)
    bcb["resposta"] = [G_CAUDA[0], {"erro": "x"}]
    inv = _investimento_com_lote(user_id, "cdb", "cdi", 1.0, G_D0)
    db.accrue_all_investments(user_id, today=G_HOJE)
    antes = bcb["chamadas"]
    assert antes > 0
    db.accrue_all_investments(user_id, today=G_HOJE)
    assert bcb["chamadas"] > antes
    assert _linha("investments", user_id, inv)["interest_frozen_at"] is None


def test_G9_valor_nao_finito_nao_entra_no_indice(user_id, bcb):
    """`float("nan")` não levanta: sem o `isfinite`, o NaN ia para o cache, o
    `market_rates` e o saldo, e o ativo congelava."""
    dia = G_CAUDA[-1]
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select 1 from market_rates where code='CDI' and ref_date=%s", (dia,))
        if cur.fetchone() is None:
            bcb["criados"].append(dia)  # sem o conserto, o NaN entraria aqui
    bcb["semeia"](G_D0, G_K)
    bcb["resposta"] = G_CAUDA[:-1] + [{"data": dia.strftime("%d/%m/%Y"), "valor": "nan"}]
    inv = _investimento_com_lote(user_id, "cdb", "cdi", 1.0, G_D0)
    db.accrue_all_investments(user_id, today=G_HOJE)
    assert bcb["chamadas"] > 0
    assert _linha("investments", user_id, inv)["interest_frozen_at"] is None
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from market_rates where value = 'NaN'")
        assert cur.fetchone()["n"] == 0
    assert _lotes(user_id, inv)[0][0].is_finite()


def test_G10_item_malformado_da_selic_nao_carimba(user_id, bcb):
    """O mesmo guarda em `_get_sgs_daily_map` (SELIC/IPCA), que G1–G9 não passam."""
    bcb["resposta"] = [{"erro": "x"}]
    inv = _investimento_com_lote(user_id, "tesouro selic", "selic_spread", 0.001, G_D0)
    db.accrue_all_investments(user_id, today=G_HOJE)
    assert bcb["chamadas"] > 0
    assert _linha("investments", user_id, inv)["interest_frozen_at"] is None

    bcb["resposta"] = []  # a rede respondeu "sem valores": não é falha, carimba
    db.accrue_all_investments(user_id, today=G_HOJE)
    assert _linha("investments", user_id, inv)["interest_frozen_at"] is not None


def test_G11_item_malformado_no_meio_da_cauda_nao_pula_o_dia(user_id, bcb):
    """[06 válido, 09 malformado, 10 válido]: com `return cached` o cursor ia a
    10/06 e a próxima busca começava em 11/06 — o 09 nunca rendia e a chamada
    seguinte congelava com juro a menos."""
    malformado = {"data": "09/06/2025", "valor": "x"}
    bcb["semeia"](G_D0, G_K)
    bcb["resposta"] = [G_CAUDA[0], malformado, G_CAUDA[2]]
    inv = _investimento_com_lote(user_id, "cdb", "cdi", 1.0, G_D0)
    db.accrue_all_investments(user_id, today=G_HOJE)
    assert bcb["chamadas"] > 0
    saldo, _, _, cursor = _lotes(user_id, inv)[0]
    assert _approx(saldo, Decimal(str(1000 * 1.0005 ** 4))) and cursor == G_CAUDA[0]
    assert _linha("investments", user_id, inv)["interest_frozen_at"] is None

    bcb["resposta"] = G_CAUDA  # a rede voltou completa: 09 e 10 rendem uma vez cada
    db.accrue_all_investments(user_id, today=G_HOJE)
    saldo, _, _, cursor = _lotes(user_id, inv)[0]
    assert _approx(saldo, G_TUDO) and cursor == G_HOJE
    assert _linha("investments", user_id, inv)["interest_frozen_at"] is not None


def _br(d):
    return {"data": d.strftime("%d/%m/%Y"), "valor": "0.04"}


@pytest.mark.parametrize("semeados, resposta, esperado", [
    # falha (None) com buraco no 04/06: só o 03 antes do buraco
    ([date(2025, 6, 3), G_K], None, [date(2025, 6, 3)]),
    # [06 válido, 09 malformado, 10 válido]: nada depois do 09
    ([date(2025, 6, 3), date(2025, 6, 4), G_K],
     [_br(G_CAUDA[0]), {"data": "09/06/2025", "valor": "x"}, _br(G_CAUDA[2])],
     [date(2025, 6, 3), date(2025, 6, 4), G_K, G_CAUDA[0]]),
], ids=["falha", "invalido"])
def test_G12_selic_na_falha_devolve_so_o_prefixo_sem_buraco(monkeypatch, semeados, resposta, esperado):
    """O mesmo conserto de G3/G11 em `_get_sgs_daily_map`, que eles não passam.
    Transação com rollback: `market_rates` é global."""
    chamadas = []
    monkeypatch.setattr(investments_db, "_sgs_answered", {})
    monkeypatch.setattr(investments_db, "_fetch_sgs_series_json",
                        lambda *a: chamadas.append(a) or resposta)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from market_rates where code='SELIC_DAILY' "
                    "and ref_date between %s and %s", (G_D0, G_HOJE))
        for d in semeados:
            cur.execute("insert into market_rates(code, ref_date, value) "
                        "values ('SELIC_DAILY', %s, 0.04)", (d,))
        out = investments_db._get_sgs_daily_map(cur, "SELIC_DAILY", 11, date(2025, 6, 3), G_HOJE)
        conn.rollback()
    assert chamadas
    assert sorted(out) == esperado
