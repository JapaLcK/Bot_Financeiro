"""
email_service.py
Serviço de envio de e-mails transacionais via Resend API.

Variáveis de ambiente necessárias:
  RESEND_API_KEY   — chave da API do Resend (re_xxxxxxxx)
  EMAIL_FROM       — remetente (default: "PigBank AI <noreply@pigbankai.com>")
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

EMAIL_FROM = os.getenv("EMAIL_FROM", "PigBank AI <noreply@pigbankai.com>")


def _get_resend():
    import resend
    resend.api_key = os.getenv("RESEND_API_KEY", "")
    return resend


def send_email(to: str, subject: str, html_body: str, text_body: Optional[str] = None) -> bool:
    """Envia e-mail via Resend API. Retorna True em sucesso, nunca lança exceção."""
    api_key = os.getenv("RESEND_API_KEY", "")
    if not api_key:
        logger.warning("Resend não configurado (RESEND_API_KEY ausente). E-mail para <%s> não enviado.", to)
        return False
    try:
        resend = _get_resend()
        params: dict = {"from": EMAIL_FROM, "to": [to], "subject": subject, "html": html_body}
        if text_body:
            params["text"] = text_body
        resend.Emails.send(params)
        logger.info("E-mail enviado para <%s>: %s", to, subject)
        return True
    except Exception as exc:
        logger.error("Falha ao enviar e-mail para <%s>: %s", to, exc)
        return False


def _base_html(title: str, content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>{title}</title>
  <style>
    body{{margin:0;padding:0;background:#0a0d18;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#e2e8f0}}
    .wrapper{{max-width:560px;margin:40px auto;background:#0f1320;border-radius:20px;border:1px solid rgba(255,255,255,.1);overflow:hidden;box-shadow:0 24px 64px rgba(0,0,0,.5)}}
    .header{{background:linear-gradient(135deg,#1e0a3c,#0c1a3a);padding:36px 32px;text-align:center;border-bottom:1px solid rgba(255,255,255,.08)}}
    .logo-icon{{display:inline-block;width:56px;height:56px;border-radius:16px;background:linear-gradient(135deg,#7c3aed,#3b82f6);font-size:28px;line-height:56px;text-align:center;box-shadow:0 8px 24px rgba(124,58,237,.45);margin-bottom:14px}}
    .header h1{{margin:0;color:#fff;font-size:20px;font-weight:700;letter-spacing:-.02em}}
    .header p{{margin:6px 0 0;color:rgba(255,255,255,.45);font-size:13px}}
    .body{{padding:36px 32px}}
    .body p{{line-height:1.75;margin:0 0 16px;color:rgba(255,255,255,.82);font-size:15px}}
    .body ol,.body ul{{line-height:2;color:rgba(255,255,255,.72);font-size:14px;padding-left:20px;margin:0 0 16px}}
    .code-box{{background:rgba(124,58,237,.12);border:1px solid rgba(124,58,237,.3);border-radius:14px;padding:20px;text-align:center;margin:24px 0}}
    .code-box code{{font-size:40px;font-weight:800;color:#a78bfa;letter-spacing:10px}}
    .highlight{{background:rgba(255,255,255,.05);border-left:3px solid #7c3aed;padding:14px 18px;border-radius:8px;margin:16px 0}}
    .highlight code{{font-size:15px;color:#a78bfa;font-weight:600}}
    .btn{{display:inline-block;background:linear-gradient(135deg,#7c3aed,#3b82f6);color:#fff!important;text-decoration:none;padding:13px 28px;border-radius:12px;font-weight:700;font-size:15px;margin:8px 0;box-shadow:0 6px 20px rgba(124,58,237,.4)}}
    .warn{{font-size:12px;color:rgba(255,255,255,.38);text-align:center;margin-top:8px}}
    .footer{{padding:20px 32px;background:rgba(255,255,255,.03);border-top:1px solid rgba(255,255,255,.06);font-size:12px;color:rgba(255,255,255,.3);text-align:center;line-height:1.7}}
    .footer a{{color:#7c3aed;text-decoration:none}}
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="header">
      <div class="logo-icon">🐷</div>
      <h1>PigBank AI</h1>
      <p>Seu assistente financeiro inteligente</p>
    </div>
    <div class="body">{content}</div>
    <div class="footer">
      Você recebeu este e-mail porque criou uma conta no PigBank AI.<br/>
      Dúvidas? Use o comando <strong>ajuda</strong> no bot ou acesse <a href="https://pigbankai.com">pigbankai.com</a>
    </div>
  </div>
</body>
</html>"""


def send_verification_email(to: str, code: str) -> bool:
    """Envia o código de verificação de 6 dígitos para confirmar o e-mail no cadastro."""
    content = f"""
      <p>Olá! 👋</p>
      <p>Use o código abaixo para confirmar seu e-mail e finalizar o cadastro no <strong>PigBank AI</strong>.</p>
      <div class="code-box"><code>{code}</code></div>
      <p class="warn">⚠️ Este código expira em <strong>15 minutos</strong>.<br/>
        Se você não tentou criar uma conta, ignore este e-mail.</p>
    """
    html = _base_html("Confirme seu e-mail — PigBank AI", content)
    text = f"PigBank AI — Código de verificação: {code}\n\nExpira em 15 minutos."
    return send_email(to=to, subject=f"🔐 {code} — seu código de verificação PigBank AI", html_body=html, text_body=text)


def send_welcome_email(to: str, link_code: str, dashboard_url: str = "") -> bool:
    """Envia e-mail de boas-vindas após o cadastro."""
    vincular_wpp = f"https://wa.me/?text=vincular%20{link_code}"
    dashboard_url = dashboard_url.rstrip("/") if dashboard_url else ""
    dashboard_section = f"""<p>Acompanhe tudo no seu dashboard:</p>
      <p style="text-align:center"><a class="btn" href="{dashboard_url}">📊 Abrir Dashboard</a></p>""" if dashboard_url else ""

    content = f"""
      <p>Olá! 👋</p>
      <p>Sua conta no <strong>PigBank AI</strong> foi criada com sucesso!
         Vincule o bot ao WhatsApp ou Discord para começar.</p>
      <p><strong>Vincular no WhatsApp:</strong></p>
      <p style="text-align:center"><a class="btn" href="{vincular_wpp}">📱 Vincular no WhatsApp</a></p>
      <p><strong>Vincular no Discord:</strong> envie no servidor do bot:</p>
      <div class="highlight"><code>vincular {link_code}</code></div>
      <p class="warn">⚠️ Este código expira em <strong>15 minutos</strong>. Faça login novamente se expirar.</p>
      {dashboard_section}
      <p><strong>Primeiros comandos:</strong></p>
      <ul>
        <li><code>gastei 50 mercado</code> — registrar despesa</li>
        <li><code>recebi 1000 salário</code> — registrar receita</li>
        <li><code>saldo</code> — ver saldo atual</li>
        <li><code>ajuda</code> — todos os comandos</li>
      </ul>
      <p>Bom controle financeiro! 🚀</p>
    """
    html = _base_html("Bem-vindo ao PigBank AI!", content)
    text = f"Bem-vindo ao PigBank AI!\n\nCódigo de vinculação: {link_code}\n\nExpira em 15 minutos."
    return send_email(to=to, subject="✅ Bem-vindo ao PigBank AI — vincule o bot e comece agora", html_body=html, text_body=text)


def send_password_reset_email(to: str, reset_url: str) -> bool:
    """Envia e-mail com link de recuperação de senha."""
    content = f"""
      <p>Olá!</p>
      <p>Recebemos uma solicitação para redefinir a senha da sua conta no <strong>PigBank AI</strong>.</p>
      <p style="text-align:center"><a class="btn" href="{reset_url}">🔑 Redefinir minha senha</a></p>
      <p class="warn">⚠️ Este link expira em <strong>30 minutos</strong> e só pode ser usado uma vez.<br/>
        Se você não solicitou isso, ignore este e-mail.</p>
      <p style="font-size:12px;color:rgba(255,255,255,.25);word-break:break-all;text-align:center;margin-top:20px;">
        Link: {reset_url}</p>
    """
    html = _base_html("Redefinição de senha — PigBank AI", content)
    text = f"Redefinição de senha — PigBank AI\n\nLink (expira em 30 min):\n{reset_url}"
    return send_email(to=to, subject="🔑 Redefinir senha — PigBank AI", html_body=html, text_body=text)
