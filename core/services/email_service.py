"""
email_service.py
Serviço de envio de e-mails transacionais via SMTP.

Variáveis de ambiente necessárias:
  SMTP_HOST      — ex: smtp.gmail.com  (default: smtp.gmail.com)
  SMTP_PORT      — ex: 587             (default: 587)
  SMTP_USER      — seu endereço de e-mail (remetente)
  SMTP_PASSWORD  — senha ou app-password
  EMAIL_FROM_NAME — nome exibido no remetente (default: "Bot Financeiro")

Compatível com: Gmail, Mailgun SMTP, SendGrid SMTP, Brevo, etc.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)

# ─── configuração via env ─────────────────────────────────────────────────────

def _cfg() -> dict:
    return {
        "host":      os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "port":      int(os.getenv("SMTP_PORT", "587")),
        "user":      os.getenv("SMTP_USER", ""),
        "password":  os.getenv("SMTP_PASSWORD", ""),
        "from_name": os.getenv("EMAIL_FROM_NAME", "Bot Financeiro"),
    }


# ─── envio genérico ───────────────────────────────────────────────────────────

def send_email(
    to: str,
    subject: str,
    html_body: str,
    text_body: Optional[str] = None,
) -> bool:
    """
    Envia um e-mail via SMTP com TLS.
    Retorna True em sucesso, False se o envio falhar (nunca lança exceção).
    """
    cfg = _cfg()

    if not cfg["user"] or not cfg["password"]:
        logger.warning(
            "SMTP não configurado (SMTP_USER / SMTP_PASSWORD ausentes). "
            "E-mail para <%s> não enviado.", to
        )
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f'{cfg["from_name"]} <{cfg["user"]}>'
        msg["To"]      = to

        if text_body:
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(cfg["user"], cfg["password"])
            server.sendmail(cfg["user"], [to], msg.as_string())

        logger.info("E-mail enviado para <%s>: %s", to, subject)
        return True

    except Exception as exc:
        logger.error("Falha ao enviar e-mail para <%s>: %s", to, exc)
        return False


# ─── templates ────────────────────────────────────────────────────────────────

def _base_html(title: str, content: str) -> str:
    """Wrapper HTML base com estilo simples e responsivo."""
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <style>
    body       {{ margin:0; padding:0; background:#f4f6f8; font-family:Arial,sans-serif; color:#333; }}
    .wrapper   {{ max-width:560px; margin:40px auto; background:#fff; border-radius:12px;
                  box-shadow:0 2px 8px rgba(0,0,0,.08); overflow:hidden; }}
    .header    {{ background:#1a1a2e; padding:28px 32px; text-align:center; }}
    .header h1 {{ margin:0; color:#fff; font-size:22px; letter-spacing:.5px; }}
    .header p  {{ margin:6px 0 0; color:#a0a8c0; font-size:13px; }}
    .body      {{ padding:32px; }}
    .body p    {{ line-height:1.7; margin:0 0 16px; }}
    .highlight {{ background:#f0f4ff; border-left:4px solid #4361ee;
                  padding:14px 18px; border-radius:6px; margin:20px 0; }}
    .highlight code {{ font-size:24px; font-weight:bold; color:#4361ee; letter-spacing:3px; }}
    .btn       {{ display:inline-block; background:#4361ee; color:#fff !important;
                  text-decoration:none; padding:12px 28px; border-radius:8px;
                  font-weight:bold; font-size:15px; margin:8px 0; }}
    .footer    {{ padding:20px 32px; background:#f9fafb; border-top:1px solid #eee;
                  font-size:12px; color:#888; text-align:center; }}
    .footer a  {{ color:#4361ee; text-decoration:none; }}
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="header">
      <h1>💸 Bot Financeiro</h1>
      <p>Controle financeiro pelo WhatsApp e Discord</p>
    </div>
    <div class="body">
      {content}
    </div>
    <div class="footer">
      Você recebeu este e-mail porque criou uma conta no Bot Financeiro.<br/>
      Dúvidas? Responda este e-mail ou use o comando <strong>ajuda</strong> no bot.
    </div>
  </div>
</body>
</html>"""


# ─── e-mails específicos ──────────────────────────────────────────────────────

def send_verification_email(to: str, code: str) -> bool:
    """
    Envia o código de verificação de 6 dígitos para confirmar o e-mail no cadastro.
    """
    content = f"""
      <p>Olá!</p>
      <p>Você está quase lá! Use o código abaixo para confirmar seu e-mail
         e finalizar o cadastro no <strong>Bot Financeiro</strong>.</p>

      <div class="highlight" style="text-align:center;">
        <code style="font-size:36px;letter-spacing:8px;font-weight:bold;color:#4361ee;">{code}</code>
      </div>

      <p style="font-size:13px;color:#888;text-align:center;">
        ⚠️ Este código expira em <strong>15 minutos</strong>.<br/>
        Se você não tentou criar uma conta, ignore este e-mail.
      </p>
    """

    html = _base_html("Confirme seu e-mail — Bot Financeiro", content)

    text = (
        f"Confirme seu e-mail — Bot Financeiro\n\n"
        f"Seu código de verificação é:\n\n"
        f"  {code}\n\n"
        f"Digite este código na página de cadastro para finalizar.\n"
        f"Expira em 15 minutos.\n\n"
        f"Se você não tentou criar uma conta, ignore este e-mail."
    )

    return send_email(
        to=to,
        subject=f"🔐 {code} é seu código de verificação — Bot Financeiro",
        html_body=html,
        text_body=text,
    )


def send_welcome_email(to: str, link_code: str, dashboard_url: str = "") -> bool:
    """
    Envia e-mail de boas-vindas após o cadastro.
    Inclui o código de vinculação e instruções de como começar.
    """
    vincular_wpp = f"https://wa.me/?text=vincular%20{link_code}"
    dashboard_url = dashboard_url.rstrip("/") if dashboard_url else ""

    dashboard_section = ""
    if dashboard_url:
        dashboard_section = f"""
        <p>Quando quiser ver seus gráficos e relatórios, acesse o dashboard:</p>
        <p style="text-align:center">
          <a class="btn" href="{dashboard_url}">📊 Abrir Dashboard</a>
        </p>"""

    content = f"""
      <p>Olá! 👋</p>
      <p>Sua conta no <strong>Bot Financeiro</strong> foi criada com sucesso.
         Agora é só vincular o bot ao WhatsApp ou Discord para começar a controlar
         suas finanças direto pelo chat.</p>

      <p><strong>Como vincular ao WhatsApp:</strong></p>
      <ol style="line-height:2">
        <li>Clique no botão abaixo (ou copie o código manualmente)</li>
        <li>Envie a mensagem que será pré-preenchida no WhatsApp</li>
        <li>Pronto! O bot vai confirmar a vinculação</li>
      </ol>

      <p style="text-align:center">
        <a class="btn" href="{vincular_wpp}">📱 Vincular no WhatsApp</a>
      </p>

      <p><strong>Como vincular ao Discord:</strong></p>
      <p>No servidor do bot, envie a mensagem:</p>
      <div class="highlight">
        <code>vincular {link_code}</code>
      </div>
      <p style="font-size:13px;color:#888;">
        ⚠️ Este código expira em <strong>15 minutos</strong>.
        Se expirar, faça login novamente para gerar um novo código.
      </p>

      {dashboard_section}

      <p>Alguns comandos para começar:</p>
      <ul style="line-height:2">
        <li><code>gastei 50 mercado</code> — registrar despesa</li>
        <li><code>recebi 1000 salário</code> — registrar receita</li>
        <li><code>saldo</code> — ver saldo atual</li>
        <li><code>ajuda</code> — ver todos os comandos</li>
      </ul>

      <p>Qualquer dúvida, use o comando <code>ajuda</code> no bot.</p>
      <p>Bom controle financeiro! 🚀</p>
    """

    html = _base_html("Bem-vindo ao Bot Financeiro!", content)

    text = (
        f"Bem-vindo ao Bot Financeiro!\n\n"
        f"Sua conta foi criada. Use o código abaixo para vincular o bot:\n\n"
        f"  Código: {link_code}\n\n"
        f"No WhatsApp: envie 'vincular {link_code}' para o bot.\n"
        f"No Discord:  envie 'vincular {link_code}' no servidor.\n\n"
        f"O código expira em 15 minutos.\n\n"
        f"Comandos básicos:\n"
        f"  gastei 50 mercado\n"
        f"  recebi 1000 salário\n"
        f"  saldo\n"
        f"  ajuda\n"
    )

    return send_email(
        to=to,
        subject="✅ Conta criada — vincule o bot e comece agora",
        html_body=html,
        text_body=text,
    )


def send_password_reset_email(to: str, reset_url: str) -> bool:
    """
    Envia e-mail com link de recuperação de senha.
    O link expira em 30 minutos.
    """
    content = f"""
      <p>Olá!</p>
      <p>Recebemos uma solicitação para redefinir a senha da sua conta no
         <strong>Bot Financeiro</strong>.</p>
      <p>Clique no botão abaixo para criar uma nova senha:</p>

      <p style="text-align:center">
        <a class="btn" href="{reset_url}">🔑 Redefinir minha senha</a>
      </p>

      <p style="font-size:13px;color:#888;">
        ⚠️ Este link expira em <strong>30 minutos</strong> e só pode ser usado uma vez.<br/>
        Se você não solicitou a redefinição, ignore este e-mail — sua senha não será alterada.
      </p>

      <p style="font-size:12px;color:#aaa;word-break:break-all;">
        Se o botão não funcionar, copie e cole este link no navegador:<br/>
        {reset_url}
      </p>
    """

    html = _base_html("Redefinição de senha — Bot Financeiro", content)

    text = (
        f"Redefinição de senha — Bot Financeiro\n\n"
        f"Acesse o link abaixo para criar uma nova senha (expira em 30 min):\n\n"
        f"{reset_url}\n\n"
        f"Se você não solicitou isso, ignore este e-mail."
    )

    return send_email(
        to=to,
        subject="🔑 Redefinir senha — Bot Financeiro",
        html_body=html,
        text_body=text,
    )
