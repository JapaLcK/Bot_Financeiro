"""IP, cidade e user-agent entram escapados no HTML dos e-mails de aviso (#554).

O UA vem do cabeçalho do cliente: quem faz login (ou pede a exportação) com um UA
`<a href="https://…">Proteja sua conta</a>` não pode plantar um link no e-mail que o
DONO da conta recebe. O escape é só no HTML; o corpo `text` reaproveita os mesmos
valores crus, de propósito: texto puro NÃO está imune (um cliente pode virar URL em
link), esta correção não afirma isso. O corte em 180/200 caracteres é no texto cru, antes
do escape.

CONTROLE NEGATIVO: sem o `_esc` em um sítio, o teste da função dele
fica vermelho (`href="https://exemplo.invalid"` aparece no HTML). POSITIVO: UA de
iPhone e cidade com acento aparecem byte a byte.
"""
from unittest.mock import patch

import pytest

import db
from core.audit import AuditEvent, _dispatch_new_login_email, record_audit_event
from core.services import email_service

UA_ATAQUE = '<a href="https://exemplo.invalid">Proteja sua conta</a>'
UA_ATAQUE_ESCAPADO = "&lt;a href=&quot;https://exemplo.invalid&quot;&gt;Proteja sua conta&lt;/a&gt;"
UA_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
CIDADE_ATAQUE = "<img src=x onerror=1>"


def _alerta(ip, ua, cidade="São Paulo, SP, BR"):
    return email_service.send_new_login_alert("a@b.c", ip=ip, city=cidade, user_agent=ua)


def _link(ip, ua, cidade=None):
    return email_service.send_data_export_link_email(
        "a@b.c", "https://pigbankai.com/x", request_ip=ip, request_user_agent=ua)


def _baixado(ip, ua, cidade=None):
    return email_service.send_data_export_completed_email(
        "a@b.c", "2026-10-09 10:00", request_ip=ip, request_user_agent=ua)


# (função, corte do UA em caracteres)
FUNCOES = [pytest.param(_alerta, 180, id="alerta_de_login"),
           pytest.param(_link, 200, id="exportacao_link"),
           pytest.param(_baixado, 200, id="exportacao_baixada")]


def _capturado(monkeypatch, chamada, *args):
    enviados = []
    monkeypatch.setattr(email_service, "send_email", lambda **kw: enviados.append(kw) or True)
    chamada(*args)
    assert len(enviados) == 1
    return enviados[0]["html_body"], enviados[0]["text_body"]


@pytest.mark.parametrize("chamada,_corte", FUNCOES)
def test_html_escapa_ua_ip_e_cidade_e_o_texto_fica_cru(monkeypatch, chamada, _corte):
    html, texto = _capturado(monkeypatch, chamada, "1.2.3.4<b>", UA_ATAQUE, CIDADE_ATAQUE)

    assert 'href="https://exemplo.invalid"' not in html and "<img src=x" not in html
    assert "1.2.3.4<b>" not in html and "1.2.3.4&lt;b&gt;" in html
    assert UA_ATAQUE_ESCAPADO in html
    if chamada is _alerta:
        assert "&lt;img src=x onerror=1&gt;" in html
        # o texto puro mantém o valor cru (decisão documentada)
        assert CIDADE_ATAQUE in texto and "1.2.3.4<b>" in texto and UA_ATAQUE in texto


@pytest.mark.parametrize("chamada,_corte", FUNCOES)
def test_ua_e_cidade_legitimos_aparecem_byte_a_byte(monkeypatch, chamada, _corte):
    html, _ = _capturado(monkeypatch, chamada, "203.0.113.9", UA_IPHONE, "São Paulo, SP, BR")

    assert UA_IPHONE in html and "203.0.113.9" in html
    if chamada is _alerta:
        assert "São Paulo, SP, BR" in html


@pytest.mark.parametrize("chamada,corte", FUNCOES)
def test_corte_acontece_antes_do_escape_sem_entidade_quebrada(monkeypatch, chamada, corte):
    ua = "a" * (corte - 2) + "<b>" + "x" * 50  # o `<` cai dentro do corte, o `>` fora

    html, _ = _capturado(monkeypatch, chamada, "1.2.3.4", ua)

    assert "a" * (corte - 2) + "&lt;b…" in html


def test_caminho_real_do_aviso_de_novo_login_escapa_o_ua(user_id, monkeypatch):
    user = db.register_auth_user(f"escape-{user_id}@t.com", "senha-forte-123")
    uid = int(user["user_id"])
    record_audit_event(uid, AuditEvent.LOGIN_FROM_NEW_IP, ip="203.0.113.1")
    record_audit_event(uid, AuditEvent.LOGIN_FROM_NEW_IP, ip="198.51.100.7")
    enviados = []
    monkeypatch.setattr(email_service, "send_email", lambda **kw: enviados.append(kw) or True)

    with patch("core.services.ipgeo.lookup_city", return_value=CIDADE_ATAQUE):
        _dispatch_new_login_email(uid, "198.51.100.7", UA_ATAQUE)

    assert len(enviados) == 1
    html = enviados[0]["html_body"]
    assert 'href="https://exemplo.invalid"' not in html and "<img src=x" not in html
    assert UA_ATAQUE_ESCAPADO in html
