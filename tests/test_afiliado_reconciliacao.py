"""#1501: reembolso perdido de comissão já paga, achado pela reconciliação diária.

Postgres real; Stripe falsa por `simular_stripe` (fixture `mundo`), que conta as consultas e avisa pelo
`admin_notify._send` falso. A cadência e a janela são medidas pelo `now()` do banco, e o teste mexe nas datas
pelo próprio banco. Os controles negativos estão no relato do PR; os positivos ficam aqui.
"""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import core.services.afiliado_reconciliacao as reconciliacao
import db
from core.services.afiliado_reembolso import conferir_pendentes, reenviar_pendentes
from db.affiliate_reviews import registrar_reembolso
from db.affiliate_reviews_manutencao import limpar_sem_comissao
from db.connection import get_conn
from test_afiliado_exclusao import _excluir
from test_afiliado_pagantes import AGORA, _admin, _em_outra_conexao
from test_afiliado_pendencia import _acoes, _aprovar, _encerrar, _paga_e_saca, _quadros, _rev
from test_afiliado_reembolso import _logs, mundo  # noqa: F401
from test_afiliado_area import _afiliado


@pytest.fixture(autouse=True)
def _so_as_deste_teste(mundo):
    """As pagas de outros testes entram como conferidas agora, e os avisos pendentes deles como entregues:
    a fila e os avisos ficam só com os deste."""
    with get_conn() as conn:
        conn.execute("update affiliate_refund_reviews set notified_at = now() where notified_at is null")
        conn.execute("delete from affiliate_paid_refund_checks")
        conn.execute("insert into affiliate_paid_refund_checks select c.stripe_invoice_id, now()"
                     " from affiliate_commissions c where c.status = 'paid' on conflict do nothing")
        conn.execute("insert into affiliate_paid_refund_checks select r.stripe_invoice_id, now()"
                     " from affiliate_refund_reviews r where r.paid_cents is not null on conflict do nothing")
        conn.commit()


@pytest.fixture
def consultadas(monkeypatch):
    """As faturas que a reconciliação consultou, na ordem; `quebra[fatura]` levanta naquela."""
    vistas, quebra, real = type("Vistas", (list,), {})(), {}, reconciliacao.devolvido_na_stripe

    def devolvido(fatura, chave):
        vistas.append(fatura)
        if fatura in quebra:
            raise quebra[fatura]
        return real(fatura, chave)

    monkeypatch.setattr(reconciliacao, "devolvido_na_stripe", devolvido)
    vistas.quebra = quebra
    return vistas


def _volta():
    """O corpo do `_afiliado_alerta_worker` (a sondagem em `test_afiliado_conferencia` mede que é este)."""
    limpar_sem_comissao()
    conferir_pendentes()
    reconciliacao.reconciliar_pagas()
    reenviar_pendentes()


def _vence(fatura, horas=25):
    with get_conn() as conn:
        conn.execute("update affiliate_paid_refund_checks set checked_at = now() - make_interval(hours => %s)"
                     " where stripe_invoice_id = %s", (horas, fatura))
        conn.commit()


def _conferida_em(fatura):
    with get_conn() as conn:
        linha = conn.execute("select checked_at from affiliate_paid_refund_checks where stripe_invoice_id = %s",
                             (fatura,)).fetchone()
    return linha and linha["checked_at"]


def _paga_ha(com, dias):
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set created_at = now() - make_interval(days => %s) where id = %s",
                     (dias, com.id))
        conn.commit()


# ── O caso da triagem e o critério do dono ───────────────────────────────────

def test_paga_reembolsada_sem_webhook_abre_recuperacao(mundo):
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    mundo.stripe.cobra(a.fatura, devolvido=100_000)  # o `charge.refunded` se perdeu
    _volta()
    rev = _rev(a.fatura)
    assert (rev["status"], rev["recovery_cents"]) == ("recovery", 10_000)
    assert _acoes(a.fatura) == ["paid_before_refund"]
    assert len(mundo.alertas) == 1 and "recuperação pendente" in mundo.alertas[0]


def test_repetir_e_reiniciar_nao_duplica(mundo, monkeypatch):
    adm = _admin(monkeypatch)
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    mundo.stripe.cobra(a.fatura, devolvido=100_000)
    for _ in range(3):
        _volta()
    _vence(a.fatura)  # como depois de um reinício: nada em memória, a cadência venceu
    for _ in range(3):
        _volta()
    rev = _rev(a.fatura)
    assert (rev["status"], rev["recovery_cents"], _acoes(a.fatura), len(mundo.alertas)) == (
        "recovery", 10_000, ["paid_before_refund"], 1)
    assert _encerrar(adm, a).status_code == 200
    _vence(a.fatura)
    _volta()
    rev = _rev(a.fatura)
    assert (rev["status"], rev["recovery_closed_cents"]) == ("resolved", 10_000)
    assert _acoes(a.fatura) == ["paid_before_refund", "recovery_recovered"]


def test_parcial_repetida_nao_duplica(mundo, monkeypatch):
    """O mesmo parcial achado de novo, antes e depois da revisão: nem evento, nem aviso, nem reabertura."""
    adm = _admin(monkeypatch)
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    mundo.stripe.cobra(a.fatura, devolvido=30_000)
    _volta()
    assert (_rev(a.fatura)["status"], len(mundo.alertas)) == ("open", 1)
    for _ in range(2):
        _vence(a.fatura)
        _volta()
    assert (_rev(a.fatura)["status"], _acoes(a.fatura), len(mundo.alertas)) == ("open", [], 1)
    assert _aprovar(adm, a, 6_000).status_code == 200
    _vence(a.fatura)
    _volta()
    rev = _rev(a.fatura)
    assert (rev["status"], rev["recovery_cents"], _acoes(a.fatura)) == ("recovery", 4_000, ["partial_approved"])


def test_parcial_paga_vai_para_revisao(mundo, monkeypatch):
    adm = _admin(monkeypatch)
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    mundo.stripe.cobra(a.fatura, devolvido=30_000)
    _volta()
    assert _rev(a.fatura)["status"] == "open"
    assert _aprovar(adm, a, 6_000).status_code == 200
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 4_000)


def test_maior_depois_reabre(mundo, consultadas):
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    mundo.stripe.cobra(a.fatura, devolvido=30_000)
    _volta()
    assert (_rev(a.fatura)["tipo"], _rev(a.fatura)["status"]) == ("partial", "open")
    mundo.stripe.cobra(a.fatura, devolvido=100_000)
    _vence(a.fatura)
    _volta()
    rev = _rev(a.fatura)
    assert (rev["tipo"], rev["status"], rev["recovery_cents"]) == ("total", "recovery", 10_000)
    assert consultadas == [a.fatura, a.fatura]


# ── Cadência, janela e falhas ────────────────────────────────────────────────

def test_sem_reembolso_espera_a_cadencia(mundo, consultadas):
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    _volta()
    assert consultadas == [a.fatura] and _conferida_em(a.fatura)
    _volta()
    _vence(a.fatura, horas=23)
    _volta()
    assert consultadas == [a.fatura]
    _vence(a.fatura, horas=25)
    _volta()
    assert consultadas == [a.fatura, a.fatura] and _rev(a.fatura) is None


@pytest.mark.parametrize("dias,consulta", [(539, True), (541, False)])
def test_janela(mundo, consultadas, dias, consulta):
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    _paga_ha(a, dias)
    _volta()
    assert (consultadas == [a.fatura]) is consulta


def test_queda_da_stripe_para_sem_gravar(mundo, consultadas):
    import stripe

    c = _afiliado()
    a, b = sorted((_paga_e_saca(c)[0], _paga_e_saca(c)[0]), key=lambda x: x.fatura)  # nunca conferidas: por fatura
    mundo.stripe.falha = stripe.APIConnectionError("fora")
    for _ in range(3):
        assert reconciliacao.reconciliar_pagas() == 0
    assert consultadas == [a.fatura] * 3 and (_conferida_em(a.fatura), _conferida_em(b.fatura)) == (None, None)
    mundo.stripe.falha = None
    assert reconciliacao.reconciliar_pagas() == 2
    assert consultadas[3:] == [a.fatura, b.fatura]  # a que falhou abre a volta seguinte


@pytest.mark.parametrize("erro", ["404", "403"])
def test_4xx_da_fatura_nao_trava(mundo, consultadas, erro):
    import stripe

    c = _afiliado()
    a, b = sorted((_paga_e_saca(c)[0], _paga_e_saca(c)[0]), key=lambda x: x.fatura)  # a quebrada vem na frente
    consultadas.quebra[a.fatura] = (stripe.InvalidRequestError("No such invoice", "invoice", http_status=404)
                                    if erro == "404" else stripe.PermissionError("sem acesso", http_status=403))
    mundo.stripe.cobra(b.fatura, devolvido=100_000)
    _volta()
    assert (_rev(b.fatura)["status"], _rev(b.fatura)["recovery_cents"]) == ("recovery", 10_000)
    assert _conferida_em(a.fatura) and _rev(a.fatura) is None
    with get_conn() as conn:
        logs = conn.execute("select details from system_event_logs where event_type = 'affiliate_paid_check_failed'"
                            " and details->>'http_status' = %s order by id desc limit 1", (erro,)).fetchall()
    assert logs and logs[0]["details"]["da_fatura"] is True and a.fatura not in str(logs)


def test_disputa_aberta_reconfere_amanha(mundo):
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    mundo.stripe.cobra(a.fatura, disputed=True)
    mundo.stripe.disputas[a.fatura] = [{"status": "needs_response", "amount": 100_000}]
    _volta()
    assert _conferida_em(a.fatura) and _rev(a.fatura) is None
    mundo.stripe.disputas[a.fatura] = [{"status": "lost", "amount": 100_000}]
    _volta()
    assert _rev(a.fatura) is None  # dentro da cadência
    _vence(a.fatura)
    _volta()
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 10_000)


def test_total_ja_registrado_nao_consulta(mundo, consultadas):
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    registrar_reembolso(a.fatura, "total", 100_000, AGORA)
    _volta()
    assert consultadas == []


def test_orfa_paga(mundo, consultadas):
    """Afiliado que excluiu a conta: a comissão paga só existe na linha da fatura (`paid_cents`)."""
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    _excluir(c)
    with get_conn() as conn:
        conn.execute("delete from affiliate_paid_refund_checks where stripe_invoice_id = %s", (a.fatura,))
        conn.commit()
    mundo.stripe.cobra(a.fatura, devolvido=100_000)
    _volta()
    rev = _rev(a.fatura)
    assert consultadas == [a.fatura]
    assert (rev["status"], rev["recovery_cents"], rev["commission_id"]) == ("recovery", 10_000, None)


def test_pix_fora(mundo, consultadas):
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set stripe_invoice_id = %s where id = %s", (f"pix:{a.id}", a.id))
        conn.commit()
    _volta()
    assert consultadas == []


def test_fila_do_1497_intacta(mundo):
    """Positivo: a reconciliação nunca marca a conferência nem mexe no saldo da paga sem reembolso."""
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    antes = _quadros(c)
    _volta()
    with get_conn() as conn:
        linha = conn.execute("select status, payment_check_pending from affiliate_commissions where id = %s",
                             (a.id,)).fetchone()
    assert (linha["status"], linha["payment_check_pending"], _quadros(c)) == ("paid", False, antes)


def test_dois_workers(mundo):
    """Sem claim, de propósito: duas instâncias na mesma volta repetem a consulta, e nada duplica."""
    c = _afiliado()
    pagas = [_paga_e_saca(c)[0] for _ in range(20)]
    mundo.stripe.cobra(pagas[7].fatura, devolvido=100_000)
    barreira = threading.Barrier(2)

    def worker():
        barreira.wait()
        return reconciliacao.reconciliar_pagas()

    with ThreadPoolExecutor(2) as pool:
        resultados = [f.result(timeout=60) for f in [pool.submit(worker), pool.submit(worker)]]
    assert all(isinstance(r, int) for r in resultados)
    assert _acoes(pagas[7].fatura) == ["paid_before_refund"]
    assert [_rev(p.fatura) for p in pagas if p is not pagas[7]] == [None] * 19


# ── Boot ─────────────────────────────────────────────────────────────────────

def test_init_db_cria_a_tabela_sem_travar_as_comissoes():
    """`create table if not exists` de tabela sem FK: o boot não pede lock em `affiliate_commissions`."""
    with get_conn() as conn:
        conn.execute("drop table affiliate_paid_refund_checks")
        conn.commit()
    leitor = _em_outra_conexao()
    try:
        leitor.execute("select count(*) from affiliate_commissions")  # transação aberta, como um relatório
        boot = threading.Thread(target=lambda: (db.init_db(), db.init_db()), daemon=True)
        boot.start()
        boot.join(25)
        assert not boot.is_alive(), "o boot fez fila atrás de uma leitura de affiliate_commissions"
    finally:
        leitor.rollback()
        leitor.close()
    with get_conn() as conn:
        assert conn.execute("select to_regclass('affiliate_paid_refund_checks') is not null as ok").fetchone()["ok"]
