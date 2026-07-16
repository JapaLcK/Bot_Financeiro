"""
email_service.py
Serviço de envio de e-mails transacionais via Resend API.

Variáveis de ambiente necessárias:
  RESEND_API_KEY      — chave da API do Resend (re_xxxxxxxx)
  EMAIL_FROM          — remetente institucional (default: "PigBank <suporte@pigbankai.com>")
  EMAIL_FROM_PIGGY    — remetente Piggy/conversacional (default: "Piggy do PigBank <oi@pigbankai.com>")
  SUPPORT_EMAIL       — e-mail público de suporte (default: "suporte@pigbankai.com")
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

EMAIL_FROM          = os.getenv("EMAIL_FROM",          "PigBank <suporte@pigbankai.com>")
EMAIL_FROM_PIGGY    = os.getenv("EMAIL_FROM_PIGGY",    "Piggy do PigBank <oi@pigbankai.com>")
SUPPORT_EMAIL       = os.getenv("SUPPORT_EMAIL",       "suporte@pigbankai.com")


def _get_resend():
    import resend
    resend.api_key = os.getenv("RESEND_API_KEY", "")
    return resend


def _log_email_event(level: str, event_type: str, message: str, *, to: str, subject: str, error: str = "") -> None:
    try:
        from core.observability import log_system_event_sync
        log_system_event_sync(
            level, event_type, message,
            source="email_service",
            details={"to": to, "subject": subject, **({"error": error} if error else {})},
        )
    except Exception:
        pass


def send_email(
    to: str,
    subject: str,
    html_body: str,
    text_body: Optional[str] = None,
    from_addr: Optional[str] = None,
    headers: Optional[dict] = None,
    attachments: Optional[list] = None,
) -> bool:
    """Envia e-mail via Resend API. Retorna True em sucesso, nunca lança exceção.

    attachments: lista no formato Resend, ex:
    [{"filename": "x.pdf", "content": <base64 str>, "content_type": "application/pdf"}].
    """
    api_key = os.getenv("RESEND_API_KEY", "")
    if not api_key:
        logger.warning("Resend não configurado (RESEND_API_KEY ausente). E-mail para <%s> não enviado.", to)
        return False
    try:
        resend = _get_resend()
        params: dict = {
            "from":    from_addr or EMAIL_FROM,
            "to":      [to],
            "subject": subject,
            "html":    html_body,
        }
        if text_body:
            params["text"] = text_body
        if headers:
            # Resend usa campo dedicado `reply_to`; header customizado é ignorado
            hdrs = dict(headers)
            reply_to = hdrs.pop("Reply-To", None) or hdrs.pop("reply-to", None)
            if reply_to:
                params["reply_to"] = reply_to
            if hdrs:
                params["headers"] = hdrs
        if attachments:
            params["attachments"] = attachments
        resend.Emails.send(params)
        logger.info("E-mail enviado para <%s>: %s", to, subject)
        _log_email_event("info", "email_sent", f"E-mail enviado para {to}", to=to, subject=subject)
        return True
    except Exception as exc:
        logger.error("Falha ao enviar e-mail para <%s>: %s", to, exc)
        _log_email_event("error", "email_failed", f"Falha ao enviar para {to}: {exc}", to=to, subject=subject, error=str(exc))
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
      <h1>PigBank</h1>
      <p>Seu assistente financeiro inteligente</p>
    </div>
    <div class="body">{content}</div>
    <div class="footer">
      Você recebeu este e-mail porque criou uma conta no PigBank.<br/>
      Dúvidas? Use o comando <strong>ajuda</strong> no bot ou acesse <a href="https://pigbankai.com">pigbankai.com</a>
    </div>
  </div>
</body>
</html>"""


def send_verification_email(to: str, code: str) -> bool:
    """Envia o código de verificação de 6 dígitos para confirmar o e-mail no cadastro."""
    content = f"""
      <p>Olá! 👋</p>
      <p>Use o código abaixo para confirmar seu e-mail e finalizar o cadastro no <strong>PigBank</strong>.</p>
      <div class="code-box"><code>{code}</code>
      <p class="warn">⚠️ Este código expira em <strong>15 minutos</strong>.<br/>
        Se você não tentou criar uma conta, ignore este e-mail.</p>
    """
    html = _base_html("Confirme seu e-mail — PigBank", content)
    text = f"PigBank — Código de verificação: {code}\n\nExpira em 15 minutos."
    return send_email(to=to, subject=f"🔐 {code} — seu código de verificação PigBank", html_body=html, text_body=text)


def _whatsapp_link(greeting: str = "Oi! Quero ativar minha conta no PigBank 🐷") -> str:
    """Link que abre o chat DIRETO com o bot no WhatsApp, já com uma saudação
    preenchida — o usuário só toca em enviar. O bot reconhece o número pelo
    phone_hash do cadastro e vincula sozinho (auto-link), sem código nem prazo.
    Usa WHATSAPP_NUMBER (número dialável do bot) do ambiente; sem esse número,
    cai no seletor genérico do WhatsApp (degradado, mas não quebra o e-mail)."""
    import urllib.parse
    text = urllib.parse.quote(greeting)
    number = "".join(ch for ch in os.getenv("WHATSAPP_NUMBER", "") if ch.isdigit())
    if number:
        return f"https://api.whatsapp.com/send?phone={number}&text={text}"
    return f"https://wa.me/?text={text}"


def send_welcome_email(to: str, link_code: str, dashboard_url: str = "") -> bool:
    """Envia e-mail de boas-vindas após o cadastro."""
    vincular_wpp = _whatsapp_link()
    dashboard_url = dashboard_url.rstrip("/") if dashboard_url else ""
    dashboard_section = f"""<p>Acompanhe tudo no seu dashboard:</p>
      <p style="text-align:center"><a class="btn" href="{dashboard_url}">📊 Abrir Dashboard</a></p>""" if dashboard_url else ""

    content = f"""
      <p>Olá! 👋</p>
      <p>Sua conta no <strong>PigBank</strong> foi criada com sucesso!
         Vamos conectar o Piggy ao seu WhatsApp pra você começar.</p>
      <p><strong>É só abrir o chat e mandar um oi</strong> — o Piggy reconhece o
         seu número automaticamente e já começa. Sem código, sem prazo. 🎉</p>
      <p style="text-align:center"><a class="btn" href="{vincular_wpp}">📱 Abrir chat no WhatsApp</a></p>
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
    html = _base_html("Bem-vindo ao PigBank!", content)
    text = (
        "Bem-vindo ao PigBank!\n\n"
        "Abra o chat com o bot no WhatsApp e mande um oi — reconhecemos seu número automaticamente.\n"
    )
    return send_email(to=to, subject="✅ Bem-vindo ao PigBank — vincule o bot e comece agora", html_body=html, text_body=text)


# ─── Unsubscribe helpers ──────────────────────────────────────────────────────

import hashlib as _hashlib
import hmac as _hmac
import base64 as _base64


def make_unsub_url(user_id: int, email: str) -> str:
    """Gera URL de descadastro com token HMAC estável (stateless, não expira)."""
    secret  = (os.getenv("JWT_SECRET") or "pigbank-unsub").encode()
    payload = f"{user_id}:{email}".encode()
    sig     = _hmac.new(secret, payload, _hashlib.sha256).digest()
    token   = _base64.urlsafe_b64encode(sig).decode().rstrip("=")
    base    = (os.getenv("DASHBOARD_URL") or "https://pigbankai.com").rstrip("/")
    return f"{base}/unsubscribe?uid={user_id}&token={token}"


# ─── Template simples para emails do Piggy ────────────────────────────────────
# Menos CSS pesado que _base_html → menor chance de cair em Promoções

def _piggy_html(title: str, content: str, unsub_url: str = "") -> str:
    unsub_line = (
        f'Não quer mais receber estes emails? '
        f'<a href="{unsub_url}" style="color:rgba(255,255,255,.35);text-decoration:underline;">Cancelar inscrição</a>'
        if unsub_url else ""
    )
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>{title}</title>
  <style>
    body{{margin:0;padding:0;background:#0a0d18;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#e2e8f0}}
    .wrap{{max-width:560px;margin:40px auto;background:#0f1320;border-radius:20px;border:1px solid rgba(255,255,255,.1);overflow:hidden;box-shadow:0 24px 64px rgba(0,0,0,.5)}}
    .hdr{{background:linear-gradient(135deg,#1e0a3c,#0c1a3a);padding:36px 32px;text-align:center;border-bottom:1px solid rgba(255,255,255,.08)}}
    .pig{{display:inline-block;width:56px;height:56px;border-radius:16px;background:linear-gradient(135deg,#7c3aed,#3b82f6);font-size:28px;line-height:56px;text-align:center;box-shadow:0 8px 24px rgba(124,58,237,.45);margin-bottom:14px}}
    .hdr h1{{margin:0;color:#fff;font-size:20px;font-weight:700;letter-spacing:-.02em}}
    .hdr p{{margin:6px 0 0;color:rgba(255,255,255,.45);font-size:13px}}
    .body{{padding:32px 32px 28px;font-size:15px;line-height:1.8;color:rgba(255,255,255,.82)}}
    .body p{{margin:0 0 16px}}
    .body ul,.body ol{{margin:0 0 16px;padding-left:20px;color:rgba(255,255,255,.72)}}
    .body li{{margin-bottom:6px}}
    .body strong{{color:#fff}}
    .cmd{{background:rgba(124,58,237,.12);border:1px solid rgba(124,58,237,.25);border-radius:8px;
          padding:10px 16px;margin:6px 0;font-family:monospace;font-size:14px;color:#a78bfa;display:block}}
    .box{{background:rgba(255,255,255,.04);border-left:3px solid #7c3aed;border-radius:0 10px 10px 0;padding:16px 20px;margin:20px 0}}
    .sig{{color:rgba(255,255,255,.55);font-size:14px;margin-top:8px!important}}
    .footer{{padding:18px 32px 24px;background:rgba(255,255,255,.02);border-top:1px solid rgba(255,255,255,.06);
             font-size:12px;color:rgba(255,255,255,.28);line-height:1.7;text-align:center}}
    .footer a{{color:rgba(255,255,255,.35);text-decoration:none}}
    a{{color:#a78bfa;text-decoration:none}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hdr">
      <div class="pig">🐷</div>
      <h1>PigBank</h1>
      <p>Seu assistente financeiro inteligente</p>
    </div>
    <div class="body">{content}</div>
    <div class="footer">
      Você recebe este email porque tem uma conta no PigBank.<br/>
      Dúvidas? Use o comando <strong>ajuda</strong> no bot ou acesse
      <a href="https://pigbankai.com">pigbankai.com</a><br/><br/>
      {unsub_line}
    </div>
  </div>
</body>
</html>"""


# ─── Conteúdo de engajamento ──────────────────────────────────────────────────

# Dicas de uso do bot — rotacionam pelo mês do ano (índice 0–5)
_TIPS: list[tuple[str, str, str]] = [
    (
        "Registre qualquer gasto em segundos",
        "Você sabia que pode registrar uma despesa com apenas algumas palavras?",
        """<p>Experimente enviar no bot:</p>
        <code class="cmd">gastei 50 mercado</code>
        <code class="cmd">paguei 120 conta de luz</code>
        <code class="cmd">gastei 35 ifood</code>
        <p>O PigBank entende linguagem natural — sem menus, sem formulários. 🚀</p>""",
    ),
    (
        "Crie caixinhas para seus objetivos",
        "Separe dinheiro para viagens, reservas e sonhos — sem misturar com o saldo do dia a dia.",
        """<p>Com as <strong>caixinhas</strong> você organiza o dinheiro por objetivo:</p>
        <code class="cmd">criar caixinha viagem com 500</code>
        <code class="cmd">adicionar 200 na caixinha viagem</code>
        <code class="cmd">caixinhas</code>
        <p>O saldo das caixinhas fica separado visualmente e não entra nos seus gastos mensais. 🐷</p>""",
    ),
    (
        "Categorias automáticas que aprendem com você",
        "Quanto mais você usa o bot, mais ele aprende seus padrões de gasto.",
        """<p>O PigBank <strong>memoriza</strong> suas categorias automaticamente.</p>
        <p>Se você digitar <code>gastei 30 ifood</code> e confirmar a categoria "Alimentação",
        da próxima vez que mencionar <em>ifood</em> o bot já categoriza sozinho — sem perguntar.</p>
        <p>Você também pode criar regras manualmente:</p>
        <code class="cmd">regra: uber → Transporte</code>
        <p>Economize tempo e mantenha seus relatórios sempre organizados. ✨</p>""",
    ),
    (
        "Acompanhe investimentos com rendimento automático",
        "Cadastre seus investimentos e veja o saldo crescer com o CDI em tempo real.",
        """<p>O PigBank calcula o rendimento dos seus investimentos automaticamente:</p>
        <code class="cmd">investimento: Tesouro Selic, R$ 2000, 100% CDI</code>
        <p>O saldo aparece atualizado no seu dashboard a cada acesso, com os juros já aplicados. 📈</p>
        <p>Use o comando <strong>investimentos</strong> para ver um resumo rápido pelo bot.</p>""",
    ),
    (
        "Relatório diário no horário que você escolher",
        "Receba um resumo do seu dia financeiro automaticamente, todo dia.",
        """<p>Você pode ativar o <strong>relatório diário automático</strong> e escolher o horário:</p>
        <code class="cmd">ligar report diario 20h</code>
        <code class="cmd">ligar report diario 8h30</code>
        <p>Todos os dias no horário configurado, o bot envia um resumo com:</p>
        <ul>
          <li>Gastos do dia</li>
          <li>Saldo atual</li>
          <li>Total em caixinhas e investimentos</li>
        </ul>
        <p>Para desligar, basta enviar: <code>desligar report diario</code> 🗓️</p>""",
    ),
    (
        "Importe seu extrato bancário com OFX",
        "Cansou de lançar tudo manualmente? Importe meses inteiros de uma vez.",
        """<p>A maioria dos bancos permite exportar o extrato em formato <strong>.OFX</strong>.
        Basta baixar o arquivo no app do seu banco e enviar direto no bot.</p>
        <p>O PigBank importa automaticamente, detecta duplicatas e mantém
        seu histórico limpo. 🏦</p>
        <p><em>Procure por "exportar extrato" ou "exportar OFX" no app do seu banco.</em></p>""",
    ),
]

# Insights de investimento — rotacionam pelo mês do ano (índice 0–5)
_INSIGHTS: list[tuple[str, str, str]] = [
    (
        "A regra dos 50/30/20",
        "Um dos frameworks mais simples e eficazes para organizar sua vida financeira.",
        """<p>A ideia é dividir sua renda líquida em três grupos:</p>
        <ul>
          <li><strong>50%</strong> para necessidades — moradia, alimentação, transporte, contas</li>
          <li><strong>30%</strong> para desejos — lazer, restaurantes, assinaturas, viagens</li>
          <li><strong>20%</strong> para poupança e investimentos — reserva de emergência, futuro</li>
        </ul>
        <p>Não precisa ser exato, mas ter uma referência já muda muito a forma como você toma
        decisões financeiras no dia a dia. 💡</p>""",
    ),
    (
        "Fundo de emergência: o primeiro passo",
        "Antes de qualquer investimento, existe uma prioridade: a reserva de emergência.",
        """<p>O fundo de emergência é um valor guardado em um investimento de
        <strong>liquidez diária</strong> (como o Tesouro Selic) para cobrir imprevistos
        sem precisar se endividar.</p>
        <p>A recomendação geral dos especialistas é:</p>
        <ul>
          <li><strong>3 meses</strong> de despesas para quem tem emprego CLT</li>
          <li><strong>6 meses</strong> para autônomos e empreendedores</li>
        </ul>
        <p>Com o PigBank você pode acompanhar exatamente qual é o seu custo mensal
        e saber quanto falta para atingir essa meta. 🎯</p>""",
    ),
    (
        "O poder dos juros compostos",
        "Einstein teria chamado de \"a oitava maravilha do mundo\". Veja por quê.",
        """<p>Nos juros compostos, você ganha juros sobre os juros que já acumulou —
        e isso cria um efeito exponencial ao longo do tempo.</p>
        <p><strong>Exemplo prático:</strong></p>
        <ul>
          <li>R$ 300 investidos por mês</li>
          <li>Rendimento de 12% ao ano (factível em renda fixa brasileira)</li>
          <li>Prazo: 10 anos</li>
        </ul>
        <p>Resultado: aproximadamente <strong>R$ 69.000</strong> — sendo que você depositou
        apenas R$ 36.000. O restante é rendimento. 📈</p>
        <p>Começar cedo importa mais do que o valor inicial.</p>""",
    ),
    (
        "O que é o CDI e por que ele importa",
        "A sigla aparece em quase todo investimento de renda fixa. Entenda o que significa.",
        """<p>O <strong>CDI (Certificado de Depósito Interbancário)</strong> é a taxa que os
        bancos cobram uns dos outros em empréstimos de curtíssimo prazo.</p>
        <p>Na prática, o CDI acompanha de perto a <strong>Selic</strong> — a taxa básica de
        juros definida pelo Banco Central — e serve como referência para a maioria dos
        investimentos de renda fixa no Brasil.</p>
        <p>Quando um CDB diz "100% do CDI", significa que vai render exatamente o que o
        CDI render no período. Um fundo que rende "110% do CDI" está entregando acima dessa
        referência. 💰</p>""",
    ),
    (
        "Dinheiro parado na conta corrente é prejuízo",
        "Não é alarmismo — é matemática. A inflação corrói o poder de compra todo mês.",
        """<p>Se a inflação anual está em 5% e seu dinheiro está na conta corrente
        sem rendimento, você está perdendo 5% do seu poder de compra por ano.</p>
        <p>Alternativas simples e seguras para quem está começando:</p>
        <ul>
          <li><strong>Tesouro Selic</strong> — rende a taxa básica de juros, resgate no dia seguinte</li>
          <li><strong>CDB de liquidez diária</strong> — oferecido pela maioria dos bancos digitais</li>
          <li><strong>Poupança</strong> — rende menos que as opções acima, mas ainda é melhor que zero</li>
        </ul>
        <p>Mesmo R$ 100 rendendo 10% ao ano é melhor do que R$ 100 parado. 🔒</p>""",
    ),
    (
        "Diversificação: o único almoço grátis das finanças",
        "Não é sobre ter muitos ativos — é sobre não concentrar todos os ovos numa só cesta.",
        """<p>Diversificar significa distribuir o risco entre diferentes tipos de investimento,
        setores e prazos. Isso não garante ganho, mas reduz o impacto de uma perda pontual.</p>
        <p>Uma distribuição básica para quem está começando:</p>
        <ul>
          <li><strong>Reserva de emergência</strong> (Tesouro Selic / CDB diário) — prioridade máxima</li>
          <li><strong>Renda fixa de médio prazo</strong> (CDB, LCI, LCA) — para objetivos de 1–3 anos</li>
          <li><strong>Renda variável</strong> (ações, FIIs) — apenas depois de ter a reserva montada</li>
        </ul>
        <p>Não existe portfólio perfeito — existe o portfólio adequado <em>para você</em>. 🌱</p>""",
    ),
]


def _tip_for_month() -> tuple[str, str, str]:
    """Seleciona a dica do mês baseada no mês atual (rotação simples)."""
    from datetime import datetime as _dt
    idx = (_dt.now().month - 1) % len(_TIPS)
    return _TIPS[idx]


def _insight_for_month() -> tuple[str, str, str]:
    """Seleciona o insight do mês baseado no mês atual (rotação simples)."""
    from datetime import datetime as _dt
    idx = (_dt.now().month - 1) % len(_INSIGHTS)
    return _INSIGHTS[idx]


def send_reengagement_email(to: str, user_id: int | None = None) -> bool:
    """
    Envia email de reengajamento para usuário inativo há 7+ dias.
    Piggy: saudoso, leve, sem pressão.
    """
    unsub = make_unsub_url(user_id, to) if user_id else ""
    content = """
      <p>Oi, sumido! Aqui é o Piggy. 🐷</p>
      <p>Faz um tempinho que você não aparece por aqui, e fiquei preocupado com as
         suas finanças. Tá tudo bem?</p>
      <p>Se quiser dar uma espiada rápida em como estão as coisas, é só mandar
         uma mensagem no bot:</p>
      <code class="cmd">saldo</code>
      <p>Dois segundinhos e você já sabe onde estão as coisas. Simples assim.</p>
      <p class="sig">Com carinho (e fome),<br/><strong>Piggy 🐷</strong></p>
    """
    html = _piggy_html("Piggy com saudade — PigBank", content, unsub)
    text = (
        "Oi, sumido! Aqui é o Piggy.\n\n"
        "Faz um tempinho que você não aparece. Manda 'saldo' no bot para "
        "ver como estão suas finanças.\n\nCom carinho, Piggy"
    )
    headers = {"List-Unsubscribe": f"<{unsub}>"} if unsub else {}
    return send_email(
        to=to,
        subject="Piggy com saudade de você",
        html_body=html,
        text_body=text,
        from_addr=EMAIL_FROM_PIGGY,
        headers=headers or None,
    )


def send_tip_email(to: str, user_id: int | None = None) -> bool:
    """
    Envia email mensal com dica de uso do bot.
    Piggy: animado, professor entusiasmado.
    """
    title, subtitle, body_html = _tip_for_month()
    unsub = make_unsub_url(user_id, to) if user_id else ""
    content = f"""
      <p>Eita! Piggy aqui com uma dica boa. 🐷</p>
      <p>Descobri que muita gente não conhece esse recurso do PigBank, e achei
         que você ia curtir saber:</p>
      <p style="font-size:18px;font-weight:700;color:#fff;margin:20px 0 6px;">{title}</p>
      <p style="color:rgba(255,255,255,.5);margin-top:0">{subtitle}</p>
      <div class="box">{body_html}</div>
      <p>Espero que ajude! Qualquer dúvida, é só chamar no bot.</p>
      <p class="sig">Um abraço,<br/><strong>Piggy 🐷</strong></p>
    """
    html = _piggy_html(f"Dica do Piggy: {title}", content, unsub)
    text = f"Eita! Piggy aqui.\n\nDica do mês: {title}\n\n{subtitle}\n\nUm abraço, Piggy"
    headers = {"List-Unsubscribe": f"<{unsub}>"} if unsub else {}
    return send_email(
        to=to,
        subject=f"Uma dica do Piggy: {title}",
        html_body=html,
        text_body=text,
        from_addr=EMAIL_FROM_PIGGY,
        headers=headers or None,
    )


def send_insight_email(to: str, user_id: int | None = None) -> bool:
    """
    Envia email mensal com insight/curiosidade de investimentos.
    Piggy: pensativo, curioso, de igual para igual.
    """
    title, subtitle, body_html = _insight_for_month()
    unsub = make_unsub_url(user_id, to) if user_id else ""
    content = f"""
      <p>Piggy por aqui. Estava fuçando o mundo das finanças e não resisti
         em compartilhar isso com você. 🐷</p>
      <p style="font-size:18px;font-weight:700;color:#fff;margin:20px 0 6px;">{title}</p>
      <p style="color:rgba(255,255,255,.5);margin-top:0">{subtitle}</p>
      <div class="box">{body_html}</div>
      <p style="font-size:12px;color:rgba(255,255,255,.3);margin-top:20px;">
        Este conteúdo é educativo e não constitui recomendação de investimento.
        Consulte um profissional certificado antes de tomar decisões financeiras.
      </p>
      <p class="sig">Até a próxima,<br/><strong>Piggy 🐷</strong></p>
    """
    html = _piggy_html(f"O Piggy encontrou algo interessante: {title}", content, unsub)
    text = (
        f"Piggy por aqui.\n\n{title}\n\n{subtitle}\n\n"
        "Este conteúdo é educativo e não é recomendação de investimento.\n\n"
        "Até a próxima, Piggy"
    )
    headers = {"List-Unsubscribe": f"<{unsub}>"} if unsub else {}
    return send_email(
        to=to,
        subject=f"O Piggy encontrou algo interessante",
        html_body=html,
        text_body=text,
        from_addr=EMAIL_FROM_PIGGY,
        headers=headers or None,
    )


def send_new_login_alert(
    to: str,
    *,
    ip: Optional[str] = None,
    city: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> bool:
    """E-mail de aviso 'novo login detectado em sua conta'.

    Disparado quando o usuario faz login a partir de um IP nunca visto
    (excluindo o primeiro login pos-cadastro). Sem dados sensiveis no corpo
    alem da cidade aproximada e prefixo do UA.
    """
    safe_ip = (ip or "desconhecido").strip()
    safe_city = (city or "Localização não identificada").strip()
    safe_ua = (user_agent or "Dispositivo desconhecido").strip()
    if len(safe_ua) > 180:
        safe_ua = safe_ua[:180] + "…"

    content = f"""
      <p>Olá!</p>
      <p>Detectamos um <strong>novo login</strong> na sua conta do <strong>PigBank</strong> a partir de um dispositivo ou local que ainda não tínhamos visto.</p>
      <div class="highlight">
        <p style="margin:0">
          <strong>Local:</strong> {safe_city}<br/>
          <strong>IP:</strong> {safe_ip}<br/>
          <strong>Dispositivo:</strong> {safe_ua}
        </p>
      </div>
      <p>Foi você? Pode ignorar este aviso — registramos para que você sempre saiba quando alguém entra na sua conta.</p>
      <p class="warn"><strong>Não foi você?</strong> Acesse <a href="https://pigbankai.com">pigbankai.com</a>, troque sua senha imediatamente e ative a autenticação em 2 etapas (MFA) se ainda não usa.</p>
    """
    html = _base_html("Novo login detectado — PigBank", content)
    text = (
        "PigBank — Novo login detectado\n\n"
        f"Local: {safe_city}\n"
        f"IP: {safe_ip}\n"
        f"Dispositivo: {safe_ua}\n\n"
        "Foi você? Pode ignorar este aviso.\n"
        "Não foi você? Troque sua senha imediatamente em https://pigbankai.com"
    )
    return send_email(
        to=to,
        subject="🔔 Novo login detectado na sua conta — PigBank",
        html_body=html,
        text_body=text,
    )


def send_password_reset_email(to: str, reset_url: str) -> bool:
    """Envia e-mail com link de recuperação de senha."""
    content = f"""
      <p>Olá!</p>
      <p>Recebemos uma solicitação para redefinir a senha da sua conta no <strong>PigBank</strong>.</p>
      <p style="text-align:center"><a class="btn" href="{reset_url}">🔑 Redefinir minha senha</a></p>
      <p class="warn">⚠️ Este link expira em <strong>30 minutos</strong> e só pode ser usado uma vez.<br/>
        Se você não solicitou isso, ignore este e-mail.</p>
      <p style="font-size:12px;color:rgba(255,255,255,.25);word-break:break-all;text-align:center;margin-top:20px;">
        Link: {reset_url}</p>
    """
    html = _base_html("Redefinição de senha — PigBank", content)
    text = f"Redefinição de senha — PigBank\n\nLink (expira em 30 min):\n{reset_url}"
    return send_email(to=to, subject="🔑 Redefinir senha — PigBank", html_body=html, text_body=text)


def send_data_export_link_email(
    to: str,
    download_url: str,
    expires_in_minutes: int = 15,
    request_ip: str | None = None,
    request_user_agent: str | None = None,
) -> bool:
    """Envia link de uso único para download da exportação de dados (LGPD)."""
    safe_ip = (request_ip or "desconhecido").strip()
    safe_ua = (request_user_agent or "desconhecido").strip()
    if len(safe_ua) > 200:
        safe_ua = safe_ua[:200] + "…"

    content = f"""
      <p>Olá!</p>
      <p>Recebemos uma solicitação para baixar a cópia completa dos seus dados no <strong>PigBank</strong>.</p>
      <p style="text-align:center"><a class="btn" href="{download_url}">📦 Baixar meus dados</a></p>
      <p class="warn">⚠️ Este link expira em <strong>{expires_in_minutes} minutos</strong> e só pode ser usado <strong>uma única vez</strong>.</p>
      <div class="highlight">
        <p style="margin:0">Solicitação registrada a partir de:<br/>
        <strong>IP:</strong> {safe_ip}<br/>
        <strong>Dispositivo:</strong> {safe_ua}</p>
      </div>
      <p class="warn"><strong>Não foi você?</strong> Ignore este e-mail e troque sua senha imediatamente em <a href="https://pigbankai.com">pigbankai.com</a>. Sem o link acima, ninguém consegue baixar seus dados — mesmo com sua sessão ativa.</p>
      <p style="font-size:12px;color:rgba(255,255,255,.25);word-break:break-all;text-align:center;margin-top:20px;">
        Link: {download_url}</p>
    """
    html = _base_html("Baixar meus dados — PigBank", content)
    text = (
        "PigBank — link para baixar seus dados\n\n"
        f"Use o link abaixo (expira em {expires_in_minutes} min, uso único):\n{download_url}\n\n"
        f"Solicitado a partir de IP {safe_ip}.\n"
        "Se não foi você, ignore este e-mail e troque sua senha em https://pigbankai.com."
    )
    return send_email(
        to=to,
        subject="📦 Link para baixar seus dados — PigBank",
        html_body=html,
        text_body=text,
    )


def send_data_export_completed_email(
    to: str,
    completed_at: str,
    request_ip: str | None = None,
    request_user_agent: str | None = None,
) -> bool:
    """Confirma que a exportação foi efetivamente baixada (auditoria pro usuário)."""
    safe_ip = (request_ip or "desconhecido").strip()
    safe_ua = (request_user_agent or "desconhecido").strip()
    if len(safe_ua) > 200:
        safe_ua = safe_ua[:200] + "…"

    content = f"""
      <p>Olá!</p>
      <p>Confirmamos que a cópia completa dos seus dados no <strong>PigBank</strong> foi baixada com sucesso.</p>
      <div class="highlight">
        <p style="margin:0"><strong>Quando:</strong> {completed_at}<br/>
        <strong>IP:</strong> {safe_ip}<br/>
        <strong>Dispositivo:</strong> {safe_ua}</p>
      </div>
      <p class="warn"><strong>Não foi você?</strong> Sua sessão ou senha podem estar comprometidas. Acesse <a href="https://pigbankai.com">pigbankai.com</a>, troque sua senha imediatamente e entre em contato com <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a>.</p>
    """
    html = _base_html("Seus dados foram baixados — PigBank", content)
    text = (
        "PigBank — seus dados foram baixados\n\n"
        f"Quando: {completed_at}\nIP: {safe_ip}\n\n"
        f"Se não foi você, troque sua senha em https://pigbankai.com e fale com {SUPPORT_EMAIL}."
    )
    return send_email(
        to=to,
        subject="📥 Seus dados foram baixados — PigBank",
        html_body=html,
        text_body=text,
    )


def send_account_deletion_scheduled_email(to: str, scheduled_for: str) -> bool:
    """Confirma que a exclusão da conta foi agendada."""
    content = f"""
      <p>Olá!</p>
      <p>Recebemos uma solicitação para excluir sua conta no <strong>PigBank</strong>.</p>
      <div class="highlight">
        <p style="margin:0">A exclusão definitiva está agendada para:<br/>
        <strong>{scheduled_for}</strong></p>
      </div>
      <p>Durante o período de carência, sua conta fica bloqueada para evitar novas alterações e proteger seus dados contra exclusão acidental ou indevida.</p>
      <p>Após o prazo, removeremos os dados pessoais e financeiros vinculados à sua conta, salvo registros mínimos que precisem ser preservados por obrigação legal, segurança, prevenção de fraude ou defesa de direitos.</p>
      <p class="warn">Se você não solicitou essa exclusão, entre em contato com o suporte imediatamente:
        <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a>.</p>
    """
    html = _base_html("Exclusão de conta agendada — PigBank", content)
    text = (
        "PigBank — exclusão de conta agendada\n\n"
        f"A exclusão definitiva está agendada para: {scheduled_for}\n\n"
        "Durante o período de carência, sua conta fica bloqueada. "
        f"Se você não solicitou essa exclusão, entre em contato com o suporte imediatamente: {SUPPORT_EMAIL}."
    )
    return send_email(
        to=to,
        subject="Exclusão de conta agendada — PigBank",
        html_body=html,
        text_body=text,
    )


def send_account_deletion_completed_email(to: str) -> bool:
    """Confirma que a exclusão definitiva foi concluída."""
    content = f"""
      <p>Olá!</p>
      <p>A exclusão da sua conta no <strong>PigBank</strong> foi concluída.</p>
      <p>Removemos os dados pessoais e financeiros vinculados à conta, salvo registros mínimos que precisem ser preservados por obrigação legal, segurança, prevenção de fraude ou defesa de direitos.</p>
      <p>Se você acredita que isso foi um erro, fale com o suporte:
        <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a>.</p>
      <p>Este é o último e-mail transacional relacionado a essa conta.</p>
    """
    html = _base_html("Conta excluída — PigBank", content)
    text = (
        "PigBank — conta excluída\n\n"
        "A exclusão da sua conta foi concluída. "
        f"Se você acredita que isso foi um erro, fale com o suporte: {SUPPORT_EMAIL}. "
        "Este é o último e-mail transacional relacionado a essa conta."
    )
    return send_email(
        to=to,
        subject="Conta excluída — PigBank",
        html_body=html,
        text_body=text,
    )


# ─── E-mails transacionais de billing (PigBank+) ─────────────────────────────

def _fmt_brl_date(value) -> str:
    """Formata datetime/date em DD/MM/YYYY (horário de Brasília)."""
    if value is None:
        return "—"
    if isinstance(value, str):
        try:
            from datetime import datetime as _dt
            value = _dt.fromisoformat(value)
        except Exception:
            return str(value)
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    if hasattr(value, "tzinfo"):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_tz.utc)
        local = value.astimezone(_tz(_td(hours=-3)))
        return local.strftime("%d/%m/%Y")
    return str(value)


def send_pro_welcome_email(to: str, trial_end_at, dashboard_url: str = "") -> bool:
    """E-mail pós-checkout — usuário acabou de assinar (item 37)."""
    trial_end = _fmt_brl_date(trial_end_at)
    dash = (dashboard_url or "https://pigbankai.com").rstrip("/")
    cta = f'<p style="text-align:center;margin:24px 0"><a class="btn" href="{dash}/app">🐷 Abrir meu dashboard</a></p>'
    content = f"""
      <p>🐷✨ <strong>Tá dentro do PigBank+!</strong></p>
      <p>Sua assinatura começou agora. Os <strong>7 dias grátis</strong> vão até <strong>{trial_end}</strong> —
      só tem cobrança depois disso, e você pode cancelar quando quiser sem ser cobrado.</p>
      <p>Agora você desbloqueou:</p>
      <ul>
        <li>Caixinhas e cartões <strong>ilimitados</strong></li>
        <li>Investimentos com IR e IOF no automático</li>
        <li>Histórico completo + exportar CSV</li>
        <li>Importar extrato e fatura por OFX</li>
        <li>Piggy IA — converse sobre suas finanças</li>
      </ul>
      {cta}
      <p style="font-size:13px;color:rgba(255,255,255,.55)">Pra cancelar antes do fim do trial, é só mandar <strong>cancelar plano</strong> no bot ou acessar <a href="{dash}/conta">{dash}/conta</a>.</p>
    """
    html = _base_html("Bem-vindo ao PigBank+", content)
    text = (
        f"PigBank+ ativado!\n\n"
        f"Sua assinatura começou. 7 dias grátis até {trial_end} — só tem cobrança depois.\n\n"
        f"Abra o dashboard: {dash}/app\n"
        f"Pra cancelar antes: mande 'cancelar plano' no bot ou acesse {dash}/conta"
    )
    return send_email(
        to=to, subject="🐷 Tá dentro do PigBank+!",
        html_body=html, text_body=text,
    )


def send_trial_ending_email(to: str, trial_end_at, dashboard_url: str = "") -> bool:
    """E-mail 3 dias antes do trial acabar (item 38)."""
    trial_end = _fmt_brl_date(trial_end_at)
    dash = (dashboard_url or "https://pigbankai.com").rstrip("/")
    content = f"""
      <p>🐷 Oi, parceiro.</p>
      <p>Seu trial do PigBank+ termina em <strong>3 dias</strong> ({trial_end}).
      Na sequência, a primeira cobrança vai entrar automaticamente no cartão que você cadastrou.</p>
      <p>Se tá curtindo, não precisa fazer nada — só relaxar e seguir usando.</p>
      <p>Se mudou de ideia, sem stress: manda <strong>cancelar plano</strong> no bot até {trial_end} e
      você não é cobrado.</p>
      <p style="text-align:center;margin:24px 0">
        <a class="btn" href="{dash}/conta">Gerenciar minha assinatura</a>
      </p>
    """
    html = _base_html("Seu trial termina em 3 dias", content)
    text = (
        f"PigBank+ — trial termina em 3 dias ({trial_end}).\n\n"
        f"Pra continuar: não precisa fazer nada, a cobrança entra automaticamente.\n"
        f"Pra cancelar sem ser cobrado: mande 'cancelar plano' no bot até {trial_end}.\n\n"
        f"Gerenciar: {dash}/conta"
    )
    return send_email(
        to=to, subject="🐷 Seu trial PigBank+ termina em 3 dias",
        html_body=html, text_body=text,
    )


def send_pro_charged_email(to: str, amount_brl: float, next_charge_at, dashboard_url: str = "") -> bool:
    """E-mail de confirmação de cobrança após o trial (item 39)."""
    valor = f"R$ {amount_brl:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    proxima = _fmt_brl_date(next_charge_at)
    dash = (dashboard_url or "https://pigbankai.com").rstrip("/")
    content = f"""
      <p>🐷 Cobrança confirmada — valeu por continuar com a gente!</p>
      <p>Seu PigBank+ tá renovado. Detalhes da cobrança:</p>
      <ul>
        <li><strong>Valor:</strong> {valor}</li>
        <li><strong>Próxima cobrança:</strong> {proxima}</li>
      </ul>
      <p>Sua nota fiscal e histórico de pagamentos ficam no portal Stripe — abra em <a href="{dash}/conta">{dash}/conta</a>.</p>
      <p>Qualquer dúvida, é só responder este email ou usar <strong>ajuda</strong> no bot.</p>
    """
    html = _base_html("Pagamento confirmado — PigBank+", content)
    text = (
        f"PigBank+ renovado.\n\n"
        f"Valor: {valor}\n"
        f"Próxima cobrança: {proxima}\n\n"
        f"Histórico de pagamentos: {dash}/conta"
    )
    return send_email(
        to=to, subject=f"✓ Pagamento confirmado — PigBank+ ({valor})",
        html_body=html, text_body=text,
    )


def send_payment_failed_email(to: str, dashboard_url: str = "") -> bool:
    """E-mail quando pagamento falha — Stripe vai retentar (item 40)."""
    dash = (dashboard_url or "https://pigbankai.com").rstrip("/")
    content = f"""
      <p>🐷 Opa, tivemos um problema.</p>
      <p>A cobrança do seu PigBank+ <strong>não passou</strong>. Pode ser cartão expirado, saldo insuficiente,
      ou banco recusando a transação.</p>
      <p>Não se preocupa — a gente vai tentar de novo automaticamente nos próximos dias. Mas pra evitar perder o
      acesso aos recursos Pro, vale dar uma olhada agora:</p>
      <p style="text-align:center;margin:24px 0">
        <a class="btn" href="{dash}/conta">Atualizar cartão</a>
      </p>
      <p style="font-size:13px;color:rgba(255,255,255,.55)">Enquanto isso, seu plano fica como <strong>past_due</strong>.
      Se as tentativas falharem, ele volta pra Free e os recursos Pro são bloqueados.</p>
    """
    html = _base_html("Pagamento falhou — atualize seu cartão", content)
    text = (
        f"PigBank+ — pagamento falhou.\n\n"
        f"Vamos tentar de novo automaticamente, mas pra evitar perder acesso:\n"
        f"Atualize o cartão em {dash}/conta\n\n"
        f"Se as tentativas falharem, o plano volta pra Free."
    )
    return send_email(
        to=to, subject="⚠️ PigBank+ — pagamento falhou, atualize seu cartão",
        html_body=html, text_body=text,
    )


def send_subscription_canceled_email(to: str, expires_at, dashboard_url: str = "") -> bool:
    """E-mail de confirmação de cancelamento (item 41)."""
    has_grace = expires_at is not None
    fim = _fmt_brl_date(expires_at) if has_grace else None
    dash = (dashboard_url or "https://pigbankai.com").rstrip("/")
    support_email = os.getenv("SUPPORT_EMAIL", "suporte@pigbankai.com").strip() or "suporte@pigbankai.com"

    if has_grace:
        access_html = (
            f"<p>Tudo certo — você continua com acesso aos recursos Pro <strong>até {fim}</strong>. "
            f"Depois disso, sua conta volta automaticamente pro plano Free e os limites Pro são desativados.</p>"
            f"<p>Se mudar de ideia antes dessa data, é só mandar <strong>assinar plano</strong> no bot.</p>"
        )
        access_text = (
            f"Você mantém acesso aos recursos Pro até {fim}. Depois disso, a conta volta pra Free."
        )
    else:
        access_html = (
            "<p>Tudo certo — sua conta voltou pro plano <strong>Free</strong> a partir de agora. "
            "Os limites Pro foram desativados.</p>"
            "<p>Se mudar de ideia, é só mandar <strong>assinar plano</strong> no bot.</p>"
        )
        access_text = "Sua conta voltou pro plano Free a partir de agora. Os limites Pro foram desativados."

    content = f"""
      <p>🐷 Sua assinatura PigBank+ foi cancelada.</p>
      {access_html}
      <p>Valeu por ter dado uma chance pra gente. Se quiser contar o que faltou ou poderia melhorar,
      responde este email ou escreve pra <a href="mailto:{support_email}">{support_email}</a> — leitura garantida.</p>
      <p style="text-align:center;margin:24px 0">
        <a class="btn" href="{dash}/app">Abrir o dashboard</a>
      </p>
    """
    html = _base_html("Assinatura cancelada — PigBank+", content)
    text = (
        f"PigBank+ cancelado.\n\n"
        f"{access_text}\n\n"
        f"Mudou de ideia? Mande 'assinar plano' no bot.\n\n"
        f"Dúvidas ou feedback? Responde este email ou escreve pra {support_email} — leitura garantida."
    )
    return send_email(
        to=to, subject="PigBank+ — assinatura cancelada",
        html_body=html, text_body=text,
    )
