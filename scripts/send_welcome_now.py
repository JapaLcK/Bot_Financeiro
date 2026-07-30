"""
scripts/send_welcome_now.py
Dispara o e-mail de boas-vindas manualmente (útil pra testar / reenviar).

Uso:
  python scripts/send_welcome_now.py                         # manda pro Lucas, link_code dummy
  python scripts/send_welcome_now.py outro@email.com         # outro destinatário
  python scripts/send_welcome_now.py outro@email.com ABCD1234 # com link_code específico
"""
import sys
import os

# garante que o root do projeto está no path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.env import load_app_env
load_app_env()

from core.services.email_service import send_welcome_email


def main():
    email     = sys.argv[1] if len(sys.argv) > 1 else "lucaskuramoti06@gmail.com"
    link_code = sys.argv[2] if len(sys.argv) > 2 else "TESTE1234"
    dashboard_url = os.getenv("DASHBOARD_URL", "")

    print(f"Enviando boas-vindas para <{email}> (link_code={link_code})...")
    ok = send_welcome_email(email, link_code, dashboard_url)
    if ok:
        print("✅ Enviado. Confere a caixa de entrada (e o spam).")
    else:
        print("❌ Falhou. Verifica RESEND_API_KEY no .env e os logs acima.")
        sys.exit(1)


if __name__ == "__main__":
    main()
