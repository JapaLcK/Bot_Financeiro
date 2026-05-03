"""
scripts/send_update_email.py

Envia e-mail de novidades do PigBank AI para todos os usuários cadastrados.

Uso:
  cd "Bot Financeiro"
  python scripts/send_update_email.py

  # Apenas testar sem enviar (dry-run):
  python scripts/send_update_email.py --dry-run

  # Enviar apenas para um e-mail específico (teste):
  python scripts/send_update_email.py --test seu@email.com
"""
import sys
import os
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

import db
from core.services.email_service import send_email, _piggy_html, make_unsub_url, EMAIL_FROM_PIGGY

# ─── Conteúdo do e-mail ───────────────────────────────────────────────────────

SUBJECT = "📈 Investimentos, configurações e site mais organizado"

def build_html(user_id: int, email: str) -> str:
    unsub = make_unsub_url(user_id, email)
    content = """
      <p>Oi! Piggy por aqui. 🐷</p>
      <p>Passando para contar as novidades mais recentes do <strong>PigBank AI</strong>.</p>

      <div class="box" style="border-left-color:#34d399;">
        <h2 style="margin:0 0 20px;color:#fff;font-size:22px;line-height:1.35;">
          📈 Investimentos, configurações e site mais organizado
        </h2>

        <div style="margin:0 0 18px;">
          <p style="margin:0 0 4px;color:#fff;font-weight:700;">📈 Investimentos no dashboard</p>
          <p style="margin:0;color:rgba(255,255,255,.68);">Agora você pode acompanhar aplicações, aportes, saques e evolução dos investimentos em uma área dedicada.</p>
        </div>

        <div style="margin:0 0 18px;">
          <p style="margin:0 0 4px;color:#fff;font-weight:700;">🧾 Cálculo automático de rendimentos</p>
          <p style="margin:0;color:rgba(255,255,255,.68);">O Piggy calcula automaticamente CDI, Selic, IPCA e tributação para deixar seus investimentos mais fáceis de acompanhar.</p>
        </div>

        <div style="margin:0 0 18px;">
          <p style="margin:0 0 4px;color:#fff;font-weight:700;">🔔 Nova área de configurações</p>
          <p style="margin:0;color:rgba(255,255,255,.68);">Criamos uma área para o usuário ajustar notificações, dados pessoais e preferências da conta com mais facilidade.</p>
        </div>

        <div style="margin:0 0 18px;">
          <p style="margin:0 0 4px;color:#fff;font-weight:700;">🧭 Site mais fácil de explorar</p>
          <p style="margin:0;color:rgba(255,255,255,.68);">Organizamos melhor as páginas do site para explicar WhatsApp, funcionalidades, preços, suporte e novidades em abas separadas.</p>
        </div>

        <div style="margin:0;">
          <p style="margin:0 0 4px;color:#fff;font-weight:700;">🔐 Privacidade mais clara</p>
          <p style="margin:0;color:rgba(255,255,255,.68);">Atualizamos as políticas de privacidade para explicar melhor como seus dados são tratados.</p>
        </div>
      </div>

      <div class="box" style="text-align:center;margin-top:24px;">
        <a href="https://pigbankai.com/changelog"
           style="display:inline-block;background:linear-gradient(135deg,#7c3aed,#3b82f6);color:#fff;
                  text-decoration:none;padding:12px 28px;border-radius:12px;font-weight:700;font-size:15px;">
          Ver novidades
        </a>
      </div>

      <p style="margin-top:24px;">Qualquer dúvida, é só chamar o Piggy no WhatsApp.</p>
      <p class="sig">Um abraço,<br/><strong>Piggy 🐷</strong></p>
    """
    return _piggy_html(SUBJECT, content, unsub)


def build_text() -> str:
    return (
        "Oi! Piggy por aqui.\n\n"
        "Passando para contar as novidades mais recentes do PigBank AI.\n\n"
        "📈 Investimentos, configurações e site mais organizado\n\n"
        "- Investimentos no dashboard: agora você pode acompanhar aplicações, aportes, saques e evolução dos investimentos em uma área dedicada.\n"
        "- Cálculo automático de rendimentos: o Piggy calcula automaticamente CDI, Selic, IPCA e tributação para deixar seus investimentos mais fáceis de acompanhar.\n"
        "- Nova área de configurações: criamos uma área para o usuário ajustar notificações, dados pessoais e preferências da conta com mais facilidade.\n"
        "- Site mais fácil de explorar: organizamos melhor as páginas do site para explicar WhatsApp, funcionalidades, preços, suporte e novidades em abas separadas.\n"
        "- Privacidade mais clara: atualizamos as políticas de privacidade para explicar melhor como seus dados são tratados.\n\n"
        "Ver novidades: https://pigbankai.com/changelog\n\n"
        "Qualquer dúvida, é só chamar o Piggy no WhatsApp.\n\n"
        "Um abraço,\nPiggy"
    )


# ─── Busca de usuários ────────────────────────────────────────────────────────

def get_all_users_with_email():
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT user_id, email
            FROM auth_accounts
            WHERE email IS NOT NULL
              AND email != ''
              AND engagement_opt_out = false
            ORDER BY user_id
        """)
        return cur.fetchall()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Lista destinatários sem enviar")
    parser.add_argument("--test", metavar="EMAIL", help="Envia apenas para este e-mail de teste")
    args = parser.parse_args()

    if args.test:
        users = [{"user_id": 0, "email": args.test}]
    else:
        users = get_all_users_with_email()

    print(f"{'[DRY-RUN] ' if args.dry_run else ''}Destinatários encontrados: {len(users)}")
    print()

    ok = 0
    fail = 0
    for u in users:
        email = u["email"]
        uid = u["user_id"]
        if args.dry_run:
            print(f"  → {email} (user_id={uid})")
            continue

        html = build_html(uid, email)
        text = build_text()
        sent = send_email(
            to=email,
            subject=SUBJECT,
            html_body=html,
            text_body=text,
            from_addr=EMAIL_FROM_PIGGY,
            headers={"List-Unsubscribe": f"<{make_unsub_url(uid, email)}>"},
        )
        status = "✅" if sent else "❌"
        print(f"  {status} {email}")
        if sent:
            ok += 1
        else:
            fail += 1

    if not args.dry_run:
        print(f"\nEnviados: {ok} | Falhas: {fail}")


if __name__ == "__main__":
    main()
