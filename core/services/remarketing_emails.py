"""core/services/remarketing_emails.py — os 3 e-mails da régua de remarketing (PR 6).

HTML em tabelas, tema escuro. O desconto de cada etapa vem de `DESCONTO_POR_ETAPA` (só no plano, no 1º
pagamento; cadernos a preço cheio, e por isso o e-mail não os cita). Nenhum dado do usuário entra no HTML
além dos dois links (`link`, `unsub`), escapados aqui.
"""
from __future__ import annotations

from html import escape
from urllib.parse import quote

from core.services.retomada import DESCONTO_POR_ETAPA

PINK, BG, CARD, TXT, MUTED, NEON = "#FF2D8E", "#0C0C0D", "#1B1B1E", "#F6F4F1", "#9C9C9A", "#C6F11A"
CONTATO = "lucas@pigbankai.com"
MOTIVOS = ["💸 Achei caro", "🤔 Não entendi como funciona", "🔒 Medo de conectar o banco",
           "⏰ Sem tempo agora", "✍️ Outro motivo"]


def _btn(label: str, href: str, solid: bool = True) -> str:
    st = (f"background:{PINK};color:#fff;" if solid else f"background:transparent;color:{PINK};border:2px solid {PINK};")
    return (f'<a href="{href}" style="{st}display:inline-block;text-decoration:none;font-weight:700;'
            f'font-size:15px;padding:13px 26px;border-radius:999px;margin:6px">{label}</a>')


def _painel() -> str:
    # Mini painel ilustrativo (valores de exemplo, marcados como tal).
    linha = lambda ic, nome, val, cor: (  # noqa: E731
        f'<tr><td style="padding:10px 0;color:{TXT};font-size:14px">{ic}&nbsp; {nome}</td>'
        f'<td align="right" style="padding:10px 0;color:{cor};font-size:14px;font-weight:700">{val}</td></tr>')
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#111;border:1px solid rgba(255,255,255,.08);border-radius:18px;margin:22px 0">
 <tr><td style="padding:20px 22px">
  <div style="font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:{MUTED}">Seu painel (exemplo)</div>
  <div style="font-size:30px;font-weight:800;color:{TXT};margin:6px 0 2px">R$ 1.240<span style="font-size:16px;color:{MUTED}">,00</span></div>
  <div style="font-size:13px;color:{NEON};margin-bottom:14px">● sobra prevista até o fim do mês</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid rgba(255,255,255,.07)">
   {linha('🛒', 'Mercado', 'R$ 612', '#FF6FB0')}
   {linha('🍔', 'Delivery', 'R$ 284', '#FF6FB0')}
   {linha('💳', 'Fatura vence dia 10', 'R$ 1.180', '#FFB020')}
  </table>
  <div style="background:rgba(255,45,142,.10);border-radius:12px;padding:12px 14px;margin-top:12px;font-size:13px;color:#F1D9E6">
   🐷 <b>Piggy:</b> “Delivery já passou 30% do mês passado. Quer um limite?”</div>
 </td></tr></table>"""


def _tiles() -> str:
    # Uma linha por item (ícone à esquerda): 3 colunas quebravam palavra a palavra no celular.
    t = lambda ic, tit, sub: (  # noqa: E731
        f'<tr><td width="44" valign="middle" style="padding:10px 0 10px 14px;font-size:24px">{ic}</td>'
        f'<td valign="middle" style="padding:10px 14px 10px 8px"><div style="font-size:14px;font-weight:700;color:{TXT}">{tit}</div>'
        f'<div style="font-size:13px;color:{MUTED}">{sub}</div></td></tr>')
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#111;'
            'border-radius:14px;margin:18px 0">'
            + t('🏦', 'Direto do banco', 'Open Finance') + t('🔔', 'Aviso antes', 'da conta apertar')
            + t('💬', 'No WhatsApp', 'fala com o Piggy') + '</table>')


def _oferta(pct: int) -> str:
    return (f'<div style="background:linear-gradient(135deg,{PINK},#B0125F);border-radius:18px;padding:20px;'
            f'text-align:center;margin:20px 0"><div style="font-size:12px;letter-spacing:.12em;'
            f'text-transform:uppercase;color:#FFD3E8">Só pra você</div>'
            f'<div style="font-size:44px;font-weight:900;color:#fff;line-height:1.1">{pct}% OFF</div>'
            f'<div style="font-size:14px;color:#fff">no primeiro pagamento do plano</div></div>')


def _pagar(link: str) -> str:
    return (f'<div style="text-align:center;margin:26px 0 6px">{_btn("💳 Pagar com cartão", link)}'
            f'{_btn("⚡ Pagar com Pix", link, solid=False)}</div>'
            f'<div style="text-align:center;font-size:12px;color:{MUTED}">Tudo que você preencheu está salvo.</div>')


def _feedback(pergunta: str) -> str:
    def chip(r: str) -> str:
        href = escape(f"mailto:{CONTATO}?subject={quote('Por que ainda não assinei: ' + r)}")
        return (f'<a href="{href}" style="display:inline-block;margin:4px;padding:9px 14px;border-radius:999px;'
                f'background:#111;border:1px solid rgba(255,255,255,.14);color:{TXT};text-decoration:none;font-size:13px">{r}</a>')
    return (f'<div style="border-top:1px solid rgba(255,255,255,.07);margin:26px 0 0;padding-top:20px;text-align:center">'
            f'<div style="font-size:15px;font-weight:700;color:#fff">{pergunta}</div>'
            f'<div style="font-size:13px;color:{MUTED};margin:4px 0 10px">Um toque e eu leio pessoalmente. — Lucas</div>'
            + "".join(chip(r) for r in MOTIVOS) + '</div>')


def _email(titulo: str, lead: str, corpo: str, cta: str, extra: str, base: str, unsub: str) -> str:
    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/><title>{titulo}</title></head>
<body style="margin:0;padding:16px 0;background:{BG};font-family:Inter,-apple-system,Segoe UI,Roboto,sans-serif;color:{TXT}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:0 8px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:{CARD};border:1px solid rgba(255,255,255,.08);border-radius:24px;overflow:hidden">
<tr><td style="height:4px;background:{PINK}"></td></tr>
<tr><td align="center" style="padding:28px 20px 8px"><img src="{escape(base)}/brand/email-logo.png" width="60" alt="PigBank" style="display:block"/>
<div style="font-size:17px;font-weight:800;margin-top:12px">Pig<span style="color:{PINK}">Bank</span></div></td></tr>
<tr><td style="padding:12px 20px 4px;font-size:16px;line-height:1.7;color:#E7E4E0">
<p style="font-size:21px;line-height:1.4;font-weight:700;color:#fff;margin:0 0 14px">{lead}</p>
<p style="margin:0 0 8px">{corpo}</p>{extra}</td></tr>
<tr><td align="center" style="padding:6px 20px 30px">{cta}</td></tr>
<tr><td style="padding:20px 20px 26px;border-top:1px solid rgba(255,255,255,.07);font-size:12px;color:#7E7E7C;text-align:center;line-height:1.7">
Lucas, fundador do PigBank<br/>Não quer mais estes avisos? <a href="{unsub}" style="color:{MUTED}">Cancelar inscrição</a>.</td></tr>
</table></td></tr></table></body></html>"""


_TEXTOS = {
    1: ("Ei, você parou no meio do caminho 👀", "Ei, você parou no meio do caminho 👀",
        "Há pouco você abriu a assinatura do PigBank… e saiu antes de terminar. Foi o Wi-Fi, o boleto do cartão "
        "te assustou ou bateu dúvida? Seja o que for, guardei tudo: é só voltar e escolher cartão ou Pix.",
        "Travou em alguma coisa?"),
    2: ("Ontem você quase entrou… 🐷", "Ontem você quase entrou…",
        "Você começou, o e-mail de ontem chegou, e… nada. Tudo bem, eu sei que dinheiro é assunto sério. "
        "O Piggy lê seu banco sozinho, organiza cada gasto e te avisa antes da conta apertar.",
        "Sério, o que está te segurando?"),
    3: ("Você é difícil, hein? 😅", "Você é difícil, hein? 😅",
        "Dois e-mails e você firme. Respeito! Esse é o último, prometo. "
        "Depois disso o Piggy volta pro chiqueiro e não te incomoda mais.",
        "Antes de eu sumir: por que não rolou?"),
}
_BOTOES = {2: "Tá bom, me convenceu →", 3: "Ok, você venceu. Quero o Piggy 🐷"}


def montar(etapa: int, link: str, unsub: str, base: str) -> tuple[str, str, str]:
    """`(assunto, html, texto)` da etapa. `link` e `unsub` são URLs prontas, escapadas aqui."""
    assunto, lead, corpo, pergunta = _TEXTOS[etapa]
    l, u = escape(link), escape(unsub)
    cta = _pagar(l) if etapa == 1 else _btn(_BOTOES[etapa], l)
    pct = DESCONTO_POR_ETAPA[etapa]
    extra = _oferta(pct) + {1: _painel() + _feedback(pergunta),
             2: _tiles() + _painel() + _feedback(pergunta),
             3: _tiles() + _feedback(pergunta)}[etapa]
    html = _email(assunto, lead, corpo, cta, extra, base, u)
    texto = (f"{lead}\n\n{corpo}\n\n{pct}% OFF no primeiro pagamento do plano.\n\nVoltar de onde parou: {link}\n\n"
             f"{pergunta} Responda este e-mail ou escreva para {CONTATO}.\n\n"
             f"Lucas, fundador do PigBank\nNão quer mais estes avisos? {unsub}\n")
    return assunto, html, texto
