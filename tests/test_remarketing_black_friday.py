"""Remarketing na Black Friday (23 a 30/11/2026): UM e-mail da campanha por pessoa no lugar dos degraus, o
"Termina hoje" no dia 30, WhatsApp e degraus pausados na janela, e a régua de volta em 1/12.

Postgres real (`remarketing_bf`, `remarketing_regua`); só o envio é falso. Controles (mutação e revertida):
sem o desvio `campanha_estado == "oferta"` em `rodar_uma_vez` os degraus e o WhatsApp saem na janela
(`test_janela_pausa_degraus_e_whatsapp...` vermelho); `do nothing` fora do claim reenvia (`test_...nao_reenvia`);
o desvio ligado FORA da janela some a régua de volta (`test_1_de_dezembro_a_regua_volta...`).
"""
from datetime import datetime, timedelta, timezone

import pytest

from core.services import email_service, remarketing
from core.services.remarketing_emails import montar_black_friday
from test_remarketing_job import AGORA, _envio, _sql, mundo  # noqa: F401 - fixture

DENTRO = datetime(2026, 11, 25, 15, 0, tzinfo=timezone.utc)          # 12h em Brasília
ULTIMO = datetime(2026, 12, 1, 2, 0, tzinfo=timezone.utc)             # 30/11 23h em Brasília
DEPOIS = datetime(2026, 12, 1, 3, 0, tzinfo=timezone.utc)             # 1/12 0h em Brasília
ASSUNTO_BF = "12 meses de Plus pelo preço de 6"


@pytest.fixture()
def emails(monkeypatch, mundo):  # noqa: F811
    saiu = []
    monkeypatch.setattr(email_service, "send_email", lambda to, subject, html, **kw: saiu.append(
        {"to": to, "subject": subject, "html": html, "text": kw.get("text_body")}) or True)
    return saiu


def _bf(uid, tipo="oferta"):
    linhas = _sql("select * from remarketing_bf where user_id = %s and tipo = %s", (uid, tipo))
    return linhas[0] if linhas else None


# `conta(t0_atras=...)` mede a partir de AGORA do test_remarketing_job (20/11): `- (Q - AGORA)` põe o T0 a `dur` de Q.
# O T0 tem de ser >= REMARKETING_DESDE (AGORA - 30 dias); o dia 30/11 fica a 10 dias de AGORA, dentro.


def _tick(quando):
    return remarketing.rodar_uma_vez(quando)


def test_janela_pausa_degraus_e_whatsapp_e_manda_um_email_da_campanha(mundo, emails):
    # T0 1h antes: na régua normal seria a etapa 1 (e-mail + WhatsApp). Na janela sai SÓ o da Black Friday.
    uid, _ = mundo.conta(t0_atras=timedelta(hours=1) - (DENTRO - AGORA))
    assert _tick(DENTRO) >= 1
    mine = [e for e in emails if e["to"] and e["subject"] == ASSUNTO_BF]
    assert len(mine) == 1 and "/black-friday?utm_source=email&amp;utm_medium=remarketing&amp;utm_campaign=black-friday-2026" in mine[0]["html"]
    assert "/retomar?t=" not in mine[0]["html"] and "Essencial" not in mine[0]["html"]
    assert mundo.enviados["wa"] == []                                  # WhatsApp pausado
    assert _envio(uid, 1) is None                                      # nenhuma etapa dos degraus
    assert _bf(uid)["status"] == "enviado" and _bf(uid)["email_ok"] is True


def test_quem_ja_recebeu_os_tres_degraus_recebe_o_da_campanha(mundo, emails):
    uid, _ = mundo.conta(t0_atras=timedelta(days=3) - (DENTRO - AGORA))
    for e in (1, 2, 3):
        _sql("insert into remarketing_envios (user_id, etapa, status) values (%s, %s, 'enviado')", (uid, e))
    _tick(DENTRO)
    assert [e["subject"] for e in emails] == [ASSUNTO_BF] and _bf(uid)["status"] == "enviado"


def test_uma_vez_por_pessoa_e_retentativa_nao_reenvia(mundo, emails, monkeypatch):
    ok, _ = mundo.conta(t0_atras=timedelta(hours=2) - (DENTRO - AGORA))
    _tick(DENTRO)
    _tick(DENTRO + timedelta(minutes=5))
    assert len(emails) == 1                                            # 2º tick: nada
    # envio que levanta: a linha fecha `sem_canal` e o tick seguinte NÃO tenta de novo
    quebra, _ = mundo.conta(t0_atras=timedelta(hours=3) - (DENTRO - AGORA))
    monkeypatch.setattr(remarketing, "send_black_friday_email", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    _tick(DENTRO + timedelta(minutes=10))
    assert _bf(quebra)["status"] == "sem_canal" and "RuntimeError" in _bf(quebra)["motivo"]
    monkeypatch.undo()
    n = len(emails)
    _tick(DENTRO + timedelta(minutes=15))
    assert len(emails) == n


def test_comprou_ou_optout_nao_recebe_e_sai_da_fila(mundo, emails):
    t0 = timedelta(hours=2) - (DENTRO - AGORA)
    optout, _ = mundo.conta(t0_atras=t0, engagement_opt_out=True)
    comprou, _ = mundo.conta(t0_atras=t0, last_payment_status="active")
    normal, _ = mundo.conta(t0_atras=t0)                               # controle positivo: este recebe
    _tick(DENTRO)
    assert [e["subject"] for e in emails] == [ASSUNTO_BF]
    assert (_bf(optout)["status"], _bf(optout)["email_ok"]) == ("sem_canal", None)
    assert (_bf(comprou)["status"], _bf(comprou)["motivo"]) == ("sem_canal", "comprou")
    assert _bf(normal)["status"] == "enviado"


def test_ultimo_dia_manda_termina_hoje_e_quem_nao_recebeu_o_1o_recebe_so_ele(mundo, emails):
    antigo, _ = mundo.conta(t0_atras=timedelta(days=3) - (DENTRO - AGORA))
    novo, _ = mundo.conta(t0_atras=timedelta(hours=5) - (ULTIMO - AGORA))
    _tick(DENTRO)                                                      # dia 25: só o "oferta"
    assert {e["subject"] for e in emails} == {ASSUNTO_BF}
    antes = len(emails)
    _tick(ULTIMO)                                                      # dia 30: só o "Termina hoje"
    novos = emails[antes:]
    assert {e["subject"] for e in novos} == {"Termina hoje, às 23h59"}
    assert _bf(antigo, "ultimo_dia")["status"] == "enviado" and _bf(novo, "ultimo_dia")["status"] == "enviado"
    assert _bf(novo, "oferta") is None                                 # nunca dois e-mails no mesmo dia
    assert "acaba hoje à noite" in novos[0]["html"]                    # a prévia (preheader oculto)


def test_1_de_dezembro_a_regua_volta_com_o_degrau_e_o_whatsapp(mundo, emails):
    uid, fone = mundo.conta(t0_atras=timedelta(hours=1) - (DEPOIS - AGORA))
    _tick(DEPOIS)
    assert _bf(uid) is None and _envio(uid, 1)["status"] == "enviado"
    assert "/retomar?t=" in emails[0]["html"] and [w["nome"] for w in mundo.enviados["wa"]] == ["rm1_tpl"]


def test_montar_black_friday_escapa_nome_e_so_cita_plus_e_pro():
    for tipo, assunto in (("oferta", ASSUNTO_BF), ("ultimo_dia", "Termina hoje, às 23h59")):
        a, html, texto = montar_black_friday(tipo, '<b>Ana</b>', "https://x.test/black-friday?a=1&b=2", "https://x.test/u", "https://x.test")
        assert a == assunto and "<b>Ana</b>" not in html and "&lt;b&gt;Ana&lt;/b&gt;" in html
        assert "R$ 119,40" in html and "Plus e Pro anuais" in html and "Essencial" not in html + texto
        assert "https://x.test/black-friday?a=1&amp;b=2" in html and "Garantir meu Plus anual" in html
    assert montar_black_friday("oferta", "", "l", "u", "b")[2].startswith("Oi.\n")


def test_claim_atomico_so_um_ganha_por_pessoa_e_tipo(mundo):
    import db.remarketing as rm
    uid, _ = mundo.conta()
    assert rm.reivindicar_bf(uid, "oferta") is True and rm.reivindicar_bf(uid, "oferta") is False
    assert rm.reivindicar_bf(uid, "ultimo_dia") is True      # o outro tipo é outra linha
