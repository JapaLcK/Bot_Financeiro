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

SUBJECT = "🐷 Novidades do PigBank AI — o bot ficou mais inteligente!"

def build_html(user_id: int, email: str) -> str:
    unsub = make_unsub_url(user_id, email)
    content = """
      <p>Oi! Piggy por aqui. 🐷</p>
      <p>O PigBank AI recebeu várias melhorias hoje. Veja o que é novo:</p>

      <ul style="padding-left:20px;line-height:1.9;">
        <li>🎙️ <strong>Áudio no WhatsApp</strong> — fale seus gastos e eu registro na hora</li>
        <li>📷 <strong>Foto de comprovante</strong> — mande a foto e eu leio o valor automaticamente</li>
        <li>📄 <strong>Importação de fatura OFX</strong> — envie o arquivo direto no chat</li>
        <li>📦 <strong>Parcelamentos detectados</strong> — veja parcelas restantes ao importar a fatura</li>
        <li>💳 <strong>Cadastro rápido de cartão</strong> —
            <code style="background:rgba(124,58,237,.15);border-radius:6px;padding:1px 6px;color:#a78bfa;">criar cartão Nubank fechamento 01 vencimento 08</code></li>
        <li>📅 <strong>Nomes de fatura iguais ao banco</strong> — "Maio/2026" no bot = fatura de maio no app</li>
        <li>🔢 <strong>Total com descontos</strong> — estornos e descontos já subtraídos do total</li>
      </ul>

      <div class="box" style="text-align:center;margin-top:24px;">
        <a href="https://pigbankai.com/changelog"
           style="display:inline-block;background:linear-gradient(135deg,#7c3aed,#3b82f6);color:#fff;
                  text-decoration:none;padding:12px 28px;border-radius:12px;font-weight:700;font-size:15px;">
          Quer saber mais? Clique aqui →
        </a>
      </div>

      <p style="margin-top:24px;">Qualquer dúvida, é só chamar! 🚀</p>
      <p class="sig">Um abraço,<br/><strong>Piggy 🐷</strong></p>
    """
    return _piggy_html(SUBJECT, content, unsub)


def build_text() -> str:
    return (
        "Oi! Piggy por aqui. 🐷\n\n"
        "O PigBank AI recebeu várias melhorias hoje:\n\n"
        "🎙️ Áudio no WhatsApp — fale seus gastos e eu registro na hora\n"
        "📷 Foto de comprovante — mande a foto e eu leio o valor\n"
        "📄 Importação de fatura OFX — envie o arquivo direto no chat\n"
        "📦 Parcelamentos detectados — veja parcelas restantes na fatura\n"
        "💳 Cadastro rápido de cartão — criar cartão Nubank fechamento 01 vencimento 08\n"
        "📅 Nomes de fatura iguais ao banco\n"
        "🔢 Total com descontos e estornos já subtraídos\n\n"
        "Quer saber mais? https://pigbankai.com/changelog\n\n"
        "Qualquer dúvida, é só chamar!\n\nUm abraço, Piggy 🐷"
    )


# ─── Busca de usuários ────────────────────────────────────────────────────────

def get_all_users_with_email():
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT au.user_id, au.email
            FROM auth_users au
            WHERE au.email IS NOT NULL
              AND au.email != ''
              AND au.email_verified = true
            ORDER BY au.user_id
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
