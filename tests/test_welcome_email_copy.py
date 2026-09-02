"""E-mail de boas-vindas (send_welcome_email): a copy não pode prometer o bot
antes do plano.

Cadastro web novo cai no gate (/precos), então "abra o chat e mande um oi — o
Piggy já começa" era falso: o passo 1 é ativar o teste de 15 dias, o passo 2 é
o oi no WhatsApp. Os dois controles do CLAUDE.md §3:

  · asserção do conserto — os dois corpos (HTML e texto) levam ao /precos, e o
    CTA do plano vem ANTES do CTA do WhatsApp;
  · controle POSITIVO — o CTA do WhatsApp continua no e-mail e o trial de 15
    dias continua dito. Sem ele o grupo passaria num e-mail que só sabe cobrar,
    que é o oposto do que o produto oferece.

Não exercita o envio real (Resend está bloqueado no conftest, CLAUDE.md §6):
o assunto aqui é o texto, e é isso que ele mede.
"""
from __future__ import annotations

import core.services.email_service as email_service


def _capturar(monkeypatch) -> dict:
    """Chama o e-mail com o send_email capturado (o conftest já bloqueia rede)."""
    capturado: dict = {}

    def fake_send_email(**kwargs):
        capturado.update(kwargs)
        return True

    monkeypatch.setattr(email_service, "send_email", fake_send_email)
    assert email_service.send_welcome_email(
        "novo@example.com", "123456", "https://pigbankai.com"
    ) is True
    return capturado


def test_boas_vindas_leva_ao_precos_antes_do_whatsapp(monkeypatch):
    enviado = _capturar(monkeypatch)
    base = email_service._public_base_url()
    link_wpp = email_service._whatsapp_link()

    for campo in ("html_body", "text_body"):
        corpo = enviado[campo]
        assert f"{base}/precos" in corpo, f"{campo} não leva ao /precos"
        assert "15 dias grátis" in corpo, f"{campo} não diz que dá pra testar de graça"

    html = enviado["html_body"]
    assert html.index(f"{base}/precos") < html.index(link_wpp), (
        "o CTA do WhatsApp aparece antes do de ativar o teste — a ordem do "
        "e-mail é justamente o conserto"
    )


def test_boas_vindas_nao_promete_bot_liberado_no_cadastro(monkeypatch):
    enviado = _capturar(monkeypatch)
    corpos = enviado["html_body"] + "\n" + enviado["text_body"] + "\n" + enviado["subject"]

    for proibido in (
        "Sem código, sem prazo",          # dizia que não havia nada entre o cadastro e o bot
        "reconhecemos seu número automaticamente.",  # text_body antigo, sem passar pelo plano
        "Abrir Dashboard",                # o gate redireciona o dashboard pro /precos
        "Primeiros comandos",             # comandos como se já funcionassem
        "vincule o bot e comece agora",   # assunto antigo
    ):
        assert proibido not in corpos, f"a copy órfã ainda está no e-mail: {proibido!r}"


def test_controle_positivo_o_caminho_do_whatsapp_continua_no_email(monkeypatch):
    enviado = _capturar(monkeypatch)
    assert email_service._whatsapp_link() in enviado["html_body"]
    assert "WhatsApp" in enviado["text_body"]
    assert "gastei 50 mercado" in enviado["html_body"]
