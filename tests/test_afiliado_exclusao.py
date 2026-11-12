"""Afiliado que exclui a própria conta (A3, D8 = B, D9 = A): pendência, histórico e saques ficam,
desvinculados do usuário, com os dados financeiros retidos. Chave Pix e nota do saque saem.

Postgres real, rotas reais, `delete_user_data` real.
"""
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from core.services.afiliado_reembolso import reenviar_pendentes
from db.affiliates import (
    SaqueOrfao,
    get_affiliate_stats,
    list_affiliate_payouts,
    mark_payout_paid,
    registrar_pagamento_realizado,
    reject_payout,
    request_payout,
)
from datetime import datetime
from zoneinfo import ZoneInfo
from db.connection import get_conn
from test_afiliado_area import ME, _afiliado, _esperando_lock, _painel_aberto, _sacar  # noqa: F401
from test_afiliado_pendencia import (  # noqa: F401
    OVERVIEW,
    URL,
    _acoes,
    _aprovar,
    _encerrar,
    _eventos,
    _limpa_fila,
    _pagar,
    _paga_e_saca,
    _parcial,
    _post,
    _realizado,
    _rejeitar,
    _rev,
    _saque,
    _total,
    adm,
    canal,
)
from test_afiliado_reembolso import _logs, _paga, mundo, script  # noqa: F401


def _excluir(c):
    db.delete_user_data(c.uid)


def _saque_db(saque):
    with get_conn() as conn:
        return conn.execute("select * from affiliate_payouts where id = %s", (saque,)).fetchone()


def _saque_com_pix(c):
    r = _sacar(c)
    assert r.status_code == 200, r.text
    return r.json()["payout"]["id"]


# ── A3: pendência e histórico ficam ──────────────────────────────────────────

def test_exclusao_do_afiliado_mantem_recuperacao(canal, adm):
    c = _afiliado()
    a, saque = _paga_e_saca(c)
    _total(a)
    assert _rev(a.fatura)["status"] == "recovery"
    _excluir(c)
    rev = _rev(a.fatura)
    assert rev is not None, "a recuperação sumiu com a conta do afiliado"
    assert (rev["status"], rev["recovery_cents"], rev["commission_id"], rev["affiliate_code"]) == (
        "recovery", 10_000, None, c.aff["code"])
    assert [(e["action"], e["payout_id"]) for e in _eventos(a.fatura)] == [("paid_before_refund", saque)]
    assert _encerrar(adm, a).status_code == 200
    assert _rev(a.fatura)["status"] == "resolved"


def test_exclusao_do_afiliado_visivel_no_admin(canal, adm):
    c = _afiliado()
    a, saque = _paga_e_saca(c)
    _total(a)
    _excluir(c)
    visto = adm.get(OVERVIEW).json()
    linha = next(r for r in visto["refund_recoveries"] if r["id"] == _rev(a.fatura)["id"])
    assert (linha["code"], linha["recovery_cents"], linha["commission_id"]) == (c.aff["code"], 10_000, None)
    assert any(e["code"] == c.aff["code"] and e["action"] == "paid_before_refund" for e in visto["refund_history"])


def test_recuperacao_aponta_saque_que_sobrou(canal, adm):
    c = _afiliado()
    a, saque = _paga_e_saca(c)
    _total(a)
    _excluir(c)
    linha = next(r for r in adm.get(OVERVIEW).json()["refund_recoveries"] if r["id"] == _rev(a.fatura)["id"])
    pago = _saque_db(saque)
    assert (linha["payout_id"], linha["payout_cents"], linha["payout_status"]) == (saque, 10_000, "paid")
    assert linha["payout_paid_at"] and pago["paid_at"]


def test_exclusao_do_afiliado_fecha_pendencia_aberta_de_comissao_solta(canal, monkeypatch):
    """Solta, nunca paga nem em saque: não há o que bloquear nem recuperar, e a pendência fecha."""
    _limpa_fila()
    c = _afiliado()
    a = _paga(c)
    monkeypatch.delenv("ADMIN_NOTIFY_WEBHOOK_URL")
    _parcial(a)
    monkeypatch.setenv("ADMIN_NOTIFY_WEBHOOK_URL", URL)
    _excluir(c)
    rev = _rev(a.fatura)
    assert (rev["status"], rev["affiliate_code"]) == ("resolved", c.aff["code"])
    assert [(e["action"], e["actor"]) for e in _eventos(a.fatura)] == [("affiliate_deleted", "sistema")]
    assert reenviar_pendentes() == 0 and canal.enviados == []


def test_exclusao_do_afiliado_sem_pii(canal, adm):
    c = _afiliado()
    a = _paga(c)
    saque = _saque_com_pix(c)
    _total(a)
    assert _realizado(adm, saque).status_code == 200
    with get_conn() as conn:
        conn.execute("update affiliate_payouts set note = %s where id = %s", (f"pago a {c.email}", saque))
        conn.commit()
    pix = _saque_db(saque)["pix_key_enc"]
    assert pix
    _excluir(c)
    with get_conn() as conn:
        linhas = [conn.execute("select * from affiliate_refund_reviews where affiliate_code = %s", (c.aff["code"],)).fetchall(),
                  conn.execute("select e.* from affiliate_refund_review_events e join affiliate_refund_reviews r"
                               " on r.id = e.review_id where r.affiliate_code = %s", (c.aff["code"],)).fetchall(),
                  conn.execute("select * from affiliate_payouts where affiliate_code = %s", (c.aff["code"],)).fetchall()]
    texto = str(linhas)
    assert all(linhas) and [p for p in (c.email, pix, str(c.uid), "chave-pix-de-teste") if p in texto] == []


# ── D8 = B: saques ficam ─────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["paid", "rejected", "requested"])
def test_exclusao_mantem_saques(adm, status):
    c = _afiliado()
    _paga(c)
    saque = _saque_com_pix(c)
    if status == "paid":
        assert _pagar(adm, saque).status_code == 200
    elif status == "rejected":
        assert _rejeitar(adm, saque).status_code == 200
    antes = _saque_db(saque)
    _excluir(c)
    depois = _saque_db(saque)
    assert depois is not None, "o saque sumiu com a conta do afiliado"
    assert {k: depois[k] for k in ("id", "amount_cents", "status", "requested_at", "paid_at")} == {
        k: antes[k] for k in ("id", "amount_cents", "status", "requested_at", "paid_at")}
    assert (depois["affiliate_id"], depois["affiliate_code"], depois["pix_key_enc"], depois["note"]) == (
        None, c.aff["code"], None, None)


def test_exclusao_zera_pii_do_saque(adm):
    c = _afiliado()
    _paga(c)
    saque = _saque_com_pix(c)
    assert _rejeitar(adm, saque).status_code == 200  # nota "reembolso" do admin
    assert _saque_db(saque)["note"]
    _excluir(c)
    linha = next(p for p in adm.get(OVERVIEW).json()["payouts"] if p["id"] == saque)
    assert (linha["code"], linha.get("email"), linha.get("pix_key"), linha["note"], linha["orphan"]) == (
        c.aff["code"], None, None, None, True)
    texto = adm.get(OVERVIEW).text
    assert c.email not in texto  # a chave Pix é a mesma em toda a suíte: quem a mede é a linha acima


@pytest.mark.parametrize("fim", ["realizado", "rejeitar"])
def test_saque_orfao_aberto(adm, fim):
    """D9 = A: o saque aberto fica órfão. "Marcar pago" e QR recusados; o admin registra ou rejeita."""
    c = _afiliado()
    _paga(c)
    saque = _saque_com_pix(c)
    _excluir(c)
    assert _saque_db(saque)["status"] == "requested"
    assert _pagar(adm, saque).status_code == 409
    assert adm.get(f"/admin/api/affiliates/payouts/{saque}/pix").status_code in (409, 422)
    assert _saque_db(saque)["status"] == "requested"
    if fim == "realizado":
        r = _realizado(adm, saque, receipt_ref="E-ORFAO-1", justification="Pix feito antes da exclusão")
        assert r.status_code == 200, r.text
        assert _saque_db(saque)["status"] == "paid"
        with get_conn() as conn:
            e = conn.execute("select * from affiliate_refund_review_events where payout_id = %s"
                             " and action = 'payment_recorded'", (saque,)).fetchone()
        assert (e["review_id"], e["receipt_ref"], e["event_on"] is not None) == (None, "E-ORFAO-1", True)
        assert any(h["payout_id"] == saque and h["code"] == c.aff["code"] for h in adm.get(OVERVIEW).json()["refund_history"])
    else:
        assert _rejeitar(adm, saque).status_code == 200
        assert (_saque_db(saque)["status"], _saque_db(saque)["note"]) == ("rejected", None)


def test_exclusao_x_marcar_pago_pago_antes(adm):
    c = _afiliado()
    _paga(c)
    saque = _saque_com_pix(c)
    assert _pagar(adm, saque).status_code == 200
    _excluir(c)
    assert _saque_db(saque)["status"] == "paid"


def test_exclusao_x_pedido_de_saque_sem_deadlock(mundo):
    """Pedido e exclusão disputam o afiliado: a exclusão trava o afiliado antes das comissões, na ordem
    do `request_payout`. Com a ordem invertida, a exclusão pegaria as comissões e esperaria o afiliado."""
    c = _afiliado()
    _paga(c)
    with ThreadPoolExecutor(2) as pool, get_conn() as segura:
        segura.execute("select id from affiliates where id = %s for update", (c.aff["id"],))
        try:
            pedido = pool.submit(request_payout, c.aff["id"])
            assert _esperando_lock(1)
            exclusao = pool.submit(db.delete_user_data, c.uid)
            assert _esperando_lock(2)
        finally:
            segura.commit()
        assert exclusao.exception(timeout=20) is None
        pedido.exception(timeout=20)  # o pedido pode concluir ou achar o afiliado sumido; nunca deadlock
    with get_conn() as conn:
        assert conn.execute("select count(*) as n from affiliates where id = %s", (c.aff["id"],)).fetchone()["n"] == 0


def test_afiliado_vivo_inalterado(adm):
    """Positivo: quem não excluiu a conta não vê nada mudar."""
    vivo, saindo = _afiliado(), _afiliado()
    _paga(vivo)
    _paga(vivo, 70)
    pago = _saque_com_pix(vivo)
    assert _pagar(adm, pago).status_code == 200
    _paga(vivo, 60)
    _paga(saindo)
    _saque_com_pix(saindo)
    antes = (get_affiliate_stats(vivo.aff["id"]), list_affiliate_payouts(vivo.aff["id"]),
             TestClient(dashboard.app).get(ME, headers=vivo.h).json())
    _excluir(saindo)
    depois = (get_affiliate_stats(vivo.aff["id"]), list_affiliate_payouts(vivo.aff["id"]),
              TestClient(dashboard.app).get(ME, headers=vivo.h).json())
    assert depois == antes
    linha = next(p for p in adm.get(OVERVIEW).json()["payouts"] if p["id"] == pago)
    assert (linha["code"], linha["orphan"]) == (vivo.aff["code"], False)
    assert mark_payout_paid(request_payout(vivo.aff["id"])["id"])


# ── Migração de `affiliate_payouts` (tabela em produção) ─────────────────────

def _payouts_no_banco():
    with get_conn() as conn:
        fk = conn.execute("select confdeltype from pg_constraint where contype = 'f'"
                          " and conrelid = 'affiliate_payouts'::regclass and confrelid = 'affiliates'::regclass").fetchone()
        nulo = conn.execute("select is_nullable from information_schema.columns where table_schema = current_schema()"
                            " and table_name = 'affiliate_payouts' and column_name = 'affiliate_id'").fetchone()
        codigo = conn.execute("select 1 from information_schema.columns where table_schema = current_schema()"
                              " and table_name = 'affiliate_payouts' and column_name = 'affiliate_code'").fetchone()
    return fk["confdeltype"], nulo["is_nullable"], codigo is not None


def test_migracao_payouts_banco_antigo(mundo):
    c = _afiliado()
    _paga(c)
    saque = _saque_com_pix(c)
    with get_conn() as conn:
        conn.execute("delete from affiliate_payouts where affiliate_id is null")
        nome = conn.execute("select conname from pg_constraint where contype = 'f' and conrelid = 'affiliate_payouts'::regclass"
                            " and confrelid = 'affiliates'::regclass").fetchone()["conname"]
        conn.execute(f'alter table affiliate_payouts drop constraint "{nome}", add constraint "{nome}"'
                     " foreign key (affiliate_id) references affiliates(id) on delete cascade")
        conn.execute("alter table affiliate_payouts alter column affiliate_id set not null")
        conn.execute("alter table affiliate_payouts drop column affiliate_code")
        conn.commit()
    assert _payouts_no_banco() == ("c", "NO", False)
    db.init_db()
    db.init_db()  # o boot seguinte não muda nada nem quebra
    assert _payouts_no_banco() == ("n", "YES", True)
    assert _saque_db(saque)["amount_cents"] == 10_000
    _excluir(c)
    assert _saque_db(saque)["affiliate_code"] == c.aff["code"]


def test_reembolso_maior_depois_da_exclusao_nao_desfaz_a_recuperacao(canal, adm):
    """A comissão saiu com o afiliado; um evento posterior da fatura não regrava a pendência como
    `no_commission`, que a limpeza de 30 dias apagaria."""
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    _parcial(a)
    _excluir(c)
    _total(a)
    rev = _rev(a.fatura)  # paga, com parcial, depois total: a recuperação de antes da exclusão (N1)
    assert (rev["status"], rev["recovery_cents"], rev["affiliate_code"]) == ("recovery", 10_000, c.aff["code"])
    c2 = _afiliado()
    b, _ = _paga_e_saca(c2)
    _total(b)
    _excluir(c2)
    _total(b)
    assert (_rev(b.fatura)["status"], _rev(b.fatura)["recovery_cents"]) == ("recovery", 10_000)


# ── N1: comissão paga de afiliado que excluiu a conta, e o reembolso que chega depois ──

def test_reembolso_total_de_comissao_paga_depois_da_exclusao_vira_recuperacao(canal, adm):
    """Sem exclusão daria `recovery` de R$ 100; a exclusão não pode mudar isso nem deixar a limpeza levar a linha."""
    import db.affiliate_reviews_manutencao as manutencao
    c = _afiliado()
    com, saque = _paga_e_saca(c)
    _excluir(c)
    _total(com)
    rev = _rev(com.fatura)
    assert (rev["status"], rev["recovery_cents"], rev["affiliate_code"]) == ("recovery", 10_000, c.aff["code"])
    assert len(canal.enviados) == 1 and "conta excluída" in canal.enviados[0]
    linha = next(r for r in adm.get(OVERVIEW).json()["refund_recoveries"] if r["id"] == rev["id"])
    assert (linha["payout_id"], linha["payout_cents"]) == (saque, 10_000)
    with get_conn() as conn:
        conn.execute("update affiliate_refund_reviews set created_at = now() - interval '400 days' where id = %s", (rev["id"],))
        conn.commit()
    manutencao.limpar_sem_comissao()
    assert _rev(com.fatura)["status"] == "recovery"


def test_parcial_aprovada_cheia_exclusao_e_total_vira_recuperacao(canal, adm):
    c = _afiliado()
    com, _ = _paga_e_saca(c)
    _parcial(com)
    assert _aprovar(adm, com, 10_000).status_code == 200  # aprovou cheio: nada a recuperar ainda
    _excluir(c)
    _total(com)
    assert (_rev(com.fatura)["status"], _rev(com.fatura)["recovery_cents"]) == ("recovery", 10_000)


def test_parcial_depois_da_exclusao_vai_para_revisao_e_o_admin_decide(canal, adm):
    c = _afiliado()
    com, _ = _paga_e_saca(c)
    _excluir(c)
    _parcial(com)
    assert _rev(com.fatura)["status"] == "open"
    assert _aprovar(adm, com, 6_000).status_code == 200
    assert (_rev(com.fatura)["status"], _rev(com.fatura)["recovery_cents"]) == ("recovery", 4_000)


def test_comissao_nao_paga_nao_deixa_linha(canal):
    """Positivo: só comissão paga fica; a não paga sai com o afiliado, sem dinheiro a recuperar."""
    c = _afiliado()
    com = _paga(c)
    _excluir(c)
    assert _rev(com.fatura) is None


# ── Saque órfão guarda vínculo (decisão do dono): mesmo comportamento de afiliado ativo ──

def _orfao_de_170(c):
    """O exemplo do dono: saque de R$ 170, com a comissão de R$ 100 e a de R$ 70, e o afiliado exclui a conta."""
    a, b = _paga(c), _paga(c, 70)
    saque = _saque_com_pix(c)
    _excluir(c)
    return a, b, saque


def _bloqueado(adm, saque):
    return next(p for p in adm.get(OVERVIEW).json()["payouts"] if p["id"] == saque)["blocked"]


def test_orfao_pago_e_depois_reembolso_total_vira_recuperacao(canal, adm):
    c = _afiliado()
    a, _, saque = _orfao_de_170(c)
    assert _realizado(adm, saque).status_code == 200
    _total(a)
    rev = _rev(a.fatura)
    assert (rev["status"], rev["recovery_cents"], rev["affiliate_code"]) == ("recovery", 10_000, c.aff["code"])
    linha = next(r for r in adm.get(OVERVIEW).json()["refund_recoveries"] if r["id"] == rev["id"])
    assert (linha["payout_id"], linha["code"]) == (saque, c.aff["code"])


def test_reembolso_antes_do_registro_bloqueia_o_orfao(canal, adm):
    c = _afiliado()
    a, b, saque = _orfao_de_170(c)
    assert _bloqueado(adm, saque) is False  # positivo: sem reembolso, nada bloqueia
    _total(a)
    assert _rev(a.fatura)["status"] == "open" and _bloqueado(adm, saque) is True
    assert _pagar(adm, saque).status_code == 409
    assert _rev(b.fatura)["status"] == "resolved"  # a de R$ 70 não teve reembolso
    assert _realizado(adm, saque).status_code == 200  # D2: total fecha com o pagamento realizado
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 10_000)


@pytest.mark.parametrize("reembolso", ["total", "parcial"])
def test_rejeitar_orfao_com_reembolso_fecha_sem_recuperacao(canal, adm, reembolso):
    """A parcial também fecha: no saque vivo ela voltaria para revisão, mas a comissão saiu e nunca foi paga."""
    c = _afiliado()
    a, _, saque = _orfao_de_170(c)
    _total(a) if reembolso == "total" else _parcial(a)
    assert _rejeitar(adm, saque).status_code == 200
    rev = _rev(a.fatura)
    assert (rev["status"], rev["recovery_cents"]) == ("resolved", None)
    assert _acoes(a.fatura)[-1] == "payout_rejected"
    _total(a)  # reentrega depois: a comissão nunca foi paga
    assert _rev(a.fatura)["status"] == "resolved"


def test_orfao_rejeitado_e_depois_reembolso_nao_abre_nada(canal, adm):
    """A comissão nunca foi paga: o reembolso depois da rejeição não bloqueia nem recupera."""
    c = _afiliado()
    a, _, saque = _orfao_de_170(c)
    assert _rejeitar(adm, saque).status_code == 200
    _total(a)
    rev = _rev(a.fatura)
    assert (rev["status"], rev["recovery_cents"]) == ("resolved", None) and canal.enviados == []


def test_orfao_espera_o_saque(canal, adm):
    """D11 no órfão: o mesmo do saque vivo. Valor menor espera o pagamento, que vira recuperação."""
    c = _afiliado()
    a, _, saque = _orfao_de_170(c)
    _parcial(a)
    assert _bloqueado(adm, saque) is True
    assert _aprovar(adm, a, 6_000).status_code == 409
    assert _saque_db(saque)["amount_cents"] == 17_000 and _bloqueado(adm, saque) is True
    assert _realizado(adm, saque).status_code == 200
    assert _aprovar(adm, a, 6_000).status_code == 200
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 4_000)
    _total(a)
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 10_000)


def test_pendencia_aberta_no_saque_sobrevive_a_exclusao_e_bloqueia(canal, adm):
    c = _afiliado()
    a, _ = _paga(c), _paga(c, 70)
    saque = _saque_com_pix(c)
    _parcial(a)
    _excluir(c)
    assert _rev(a.fatura)["status"] == "open" and _bloqueado(adm, saque) is True
    assert _aprovar(adm, a, 10_000).status_code == 200  # aprovado cheio: nada muda no saque
    assert _bloqueado(adm, saque) is False and _saque_db(saque)["amount_cents"] == 17_000


def test_pendencia_aberta_de_comissao_paga_sobrevive_a_exclusao(canal, adm):
    """Mesmo comportamento de afiliado ativo: a parcial de comissão paga segue para o admin decidir."""
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    _parcial(a)
    _excluir(c)
    assert _rev(a.fatura)["status"] == "open"
    assert _aprovar(adm, a, 6_000).status_code == 200
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 4_000)


def test_orfao_sem_reembolso_paga_sem_recuperacao(canal, adm):
    """Positivo: o vínculo não inventa recuperação."""
    c = _afiliado()
    a, b, saque = _orfao_de_170(c)
    assert _realizado(adm, saque).status_code == 200
    assert _saque_db(saque)["status"] == "paid"
    assert _rev(a.fatura)["status"] == "resolved" and _rev(a.fatura)["recovery_cents"] is None
    assert adm.get(OVERVIEW).json()["refund_recoveries"] == [] or all(
        r["id"] not in (_rev(a.fatura)["id"], _rev(b.fatura)["id"]) for r in adm.get(OVERVIEW).json()["refund_recoveries"])


def test_vinculo_do_orfao_nao_entra_na_limpeza(canal, adm):
    import db.affiliate_reviews_manutencao as manutencao
    c = _afiliado()
    a, _, saque = _orfao_de_170(c)
    with get_conn() as conn:
        conn.execute("update affiliate_refund_reviews set created_at = now() - interval '400 days'"
                     " where stripe_invoice_id = %s", (a.fatura,))
        conn.commit()
    manutencao.limpar_sem_comissao()
    _total(a)
    assert _rev(a.fatura)["status"] == "open" and _bloqueado(adm, saque) is True


@pytest.mark.parametrize("primeiro", ["webhook", "realizado"])
def test_orfao_corrida_webhook_e_pagamento_realizado(canal, adm, primeiro):
    from test_afiliado_area import _esperando_lock
    from test_afiliado_reembolso import _evento
    from core.services.afiliado_reembolso import tratar_evento
    c = _afiliado()
    a, _, saque = _orfao_de_170(c)
    evento = _evento(a.fatura)
    realiza = lambda: registrar_pagamento_realizado(saque, datetime.now(ZoneInfo("America/Sao_Paulo")).date(),
                                                    "Pix feito antes", "E1234567", "adm")
    passos = [lambda: tratar_evento(evento), realiza] if primeiro == "webhook" else [realiza, lambda: tratar_evento(evento)]
    with ThreadPoolExecutor(2) as pool, get_conn() as segura:
        segura.execute("select id from affiliate_refund_reviews where stripe_invoice_id = %s for update", (a.fatura,))
        try:
            f1 = pool.submit(passos[0])
            assert _esperando_lock(1)
            f2 = pool.submit(passos[1])
            assert _esperando_lock(2)
        finally:
            segura.commit()
        f1.result(timeout=20)
        f2.result(timeout=20)
    assert (_saque_db(saque)["status"], _rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("paid", "recovery", 10_000)


# ── B1: o admin vê a comissão que saiu com o afiliado como ela era ──

def _revisar_no_admin(adm, rev):
    return next(r for r in adm.get(OVERVIEW).json()["refund_reviews"] if r["id"] == rev["id"])


def test_admin_ve_comissao_paga_de_afiliado_excluido(canal, adm):
    """Paga de R$ 80, reembolso de 30%, exclusão: o admin vê R$ 80 já pagos, e a diferença vira recuperação."""
    from test_afiliado_reembolso import _cobranca, _enviar
    c = _afiliado()
    com, saque = _paga_e_saca(c, 80)
    assert _enviar("charge.refunded", _cobranca(com.fatura, amount=80_000, devolvido=24_000)).status_code == 200
    _excluir(c)
    linha = _revisar_no_admin(adm, _rev(com.fatura))
    assert (linha["commission_cents"], linha["commission_status"], linha["affiliate_deleted"], linha["code"]) == (
        8_000, "paid", True, c.aff["code"])
    assert _aprovar(adm, com, 5_600).status_code == 200
    assert (_rev(com.fatura)["status"], _rev(com.fatura)["recovery_cents"]) == ("recovery", 2_400)


def test_admin_ve_pendencia_do_saque_orfao(canal, adm):
    """A parcial que já estava aberta no saque: valor, saque e "conta excluída" vêm do vínculo."""
    c = _afiliado()
    a, _ = _paga(c), _paga(c, 70)
    saque = _saque_com_pix(c)
    _parcial(a)
    _excluir(c)
    linha = _revisar_no_admin(adm, _rev(a.fatura))
    assert (linha["commission_cents"], linha["commission_status"], linha["payout_id"], linha["affiliate_deleted"]) == (
        10_000, "pending", saque, True)


def test_admin_ve_comissao_viva_como_antes(canal, adm):
    """Positivo: afiliado ativo segue com valor, status e saque da comissão, sem o selo."""
    c = _afiliado()
    a, _ = _paga(c), _paga(c, 70)
    saque = _saque_com_pix(c)
    _parcial(a)
    linha = _revisar_no_admin(adm, _rev(a.fatura))
    assert (linha["commission_cents"], linha["commission_status"], linha["payout_id"], linha["affiliate_deleted"]) == (
        10_000, "pending", saque, False)


# ── N5: o script vê a fatura de afiliado excluído ──

@pytest.mark.parametrize("caso", ["paga", "orfao"])
def test_script_ve_fatura_de_afiliado_excluido(adm, script, capsys, caso):
    """Stripe com a fatura 100% devolvida e o webhook perdido: o `--aplicar` tem de chegar à linha que ficou."""
    c = _afiliado()
    if caso == "paga":
        com, saque = _paga_e_saca(c)
    else:
        com, _ = _paga(c), _paga(c, 70)
        saque = _saque_com_pix(c)
    _excluir(c)
    script.stripe.cobrancas[com.fatura] = {"amount": 100_000, "amount_refunded": 100_000, "disputed": False}
    capsys.readouterr()
    script.rodar(aplicar=False)
    simulacao = capsys.readouterr().out
    assert _rev(com.fatura)["status"] == "resolved"  # simulação não grava
    script.rodar(aplicar=True)
    saida = capsys.readouterr().out
    rev = _rev(com.fatura)
    if caso == "paga":
        assert (rev["status"], rev["recovery_cents"]) == ("recovery", 10_000)
    else:
        assert rev["status"] == "open" and _pagar(adm, saque).status_code == 409
    assert f"segue_revisao: pendência {rev['id']} " in simulacao and f"segue_revisao: pendência {rev['id']} " in saida
    assert [p for p in (com.fatura, com.indicado.email, c.email) if p in simulacao + saida] == []


def test_script_ignora_orfao_rejeitado(adm, script, capsys):
    """Positivo: a comissão do saque rejeitado nunca foi paga; o script não abre nada."""
    c = _afiliado()
    com, _ = _paga(c), _paga(c, 70)
    saque = _saque_com_pix(c)
    _excluir(c)
    assert _rejeitar(adm, saque).status_code == 200
    script.stripe.cobrancas[com.fatura] = {"amount": 100_000, "amount_refunded": 100_000, "disputed": False}
    capsys.readouterr()
    script.rodar(aplicar=False)
    script.rodar(aplicar=True)
    assert _rev(com.fatura)["status"] == "resolved" and f"pendência {_rev(com.fatura)['id']} " not in capsys.readouterr().out
