"""Régua de remarketing, PR 6: fila, encadeamento das etapas e falhas pós-claim.
Controles: sem o try em volta do pós-claim → `test_erro_pos_claim_...` vermelho; sem fechar comprou/excluído
→ `test_comprador_...` vermelho; sem a condição da etapa anterior em `candidatos` → `test_etapa_2_...` vermelho.
"""
from datetime import timedelta

import db.remarketing as rm
from core.services import remarketing
from tests.test_remarketing_job import AGORA, DESDE, _envio, _sql, mundo  # noqa: F401


def test_erro_pos_claim_fecha_com_motivo_e_conta_o_email(mundo, monkeypatch):
    uid, _ = mundo.conta()

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(rm, "numero_de_outra_conta", boom)
    remarketing._um(uid, 1, AGORA)
    r = _envio(uid)
    assert (r["status"], r["email_ok"], r["whatsapp_ok"], r["motivo"]) == ("enviado", True, None, "erro:RuntimeError")
    assert len(mundo.enviados["email"]) == 1 and mundo.enviados["wa"] == []


def test_comprador_antigo_nao_bloqueia_a_fila(mundo):
    comprou, _ = mundo.conta(t0_atras=timedelta(hours=2), last_payment_status="active")
    livre, _ = mundo.conta(t0_atras=timedelta(hours=1))
    mundo_uids = {comprou, livre}
    for _tick in range(2):
        for u, e in rm.candidatos(DESDE, AGORA, limite=1):
            if u in mundo_uids:
                remarketing._um(u, e, AGORA)
    assert _envio(comprou)["motivo"] == "comprou" and _envio(livre)["status"] == "enviado"


def test_etapa_2_e_3_exigem_a_anterior_enviada(mundo):
    sem, _ = mundo.conta(t0_atras=timedelta(hours=25))
    assert [u for u, _e in rm.candidatos(DESDE, AGORA) if u == sem] == []
    com, _ = mundo.conta(t0_atras=timedelta(hours=25))
    assert rm.reivindicar(com, 1)
    rm.fechar(com, 1, True, None, None)
    assert [(u, e) for u, e in rm.candidatos(DESDE, AGORA) if u == com] == [(com, 2)]
    quase, _ = mundo.conta(t0_atras=timedelta(hours=49))   # E2 fechada sem canal: E3 não sai
    assert rm.reivindicar(quase, 1) and rm.reivindicar(quase, 2)
    rm.fechar(quase, 1, True, None, None)
    rm.fechar(quase, 2, None, None, "email:sem_canal")
    assert [u for u, _e in rm.candidatos(DESDE, AGORA) if u == quase] == []


def test_send_template_none_nao_conta_como_enviado(mundo, monkeypatch):
    import adapters.whatsapp.wa_client as wa
    monkeypatch.setattr(wa, "send_template", lambda *a, **k: None)   # 401 da Meta
    uid, _ = mundo.conta()
    remarketing._um(uid, 1, AGORA)
    r = _envio(uid)
    assert (r["status"], r["email_ok"], r["whatsapp_ok"]) == ("enviado", True, False)


def test_etapa_sem_canal_nao_volta(mundo):
    uid, _ = mundo.conta(engagement_opt_out=True, whatsapp_updates_opt_out=True)
    remarketing._um(uid, 1, AGORA)
    assert _envio(uid)["status"] == "sem_canal"
    assert [u for u, _e in rm.candidatos(DESDE, AGORA) if u == uid] == []


def test_fechar_levanta_uma_vez_e_a_linha_fecha_na_retentativa(mundo, monkeypatch):
    uid, _ = mundo.conta()
    real, chamadas = rm.fechar, []

    def fechar_flaky(*a, **k):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise RuntimeError("conexao caiu")
        return real(*a, **k)
    monkeypatch.setattr(rm, "fechar", fechar_flaky)
    remarketing._um(uid, 1, AGORA)
    r = _envio(uid)
    assert len(chamadas) == 2 and (r["status"], r["email_ok"]) == ("enviado", True)
