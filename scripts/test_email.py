"""
scripts/test_email.py
Envia um email de teste de engajamento para o endereço especificado.

Uso:
  python scripts/test_email.py tip       lucaskuramoti06@gmail.com
  python scripts/test_email.py insight   lucaskuramoti06@gmail.com
  python scripts/test_email.py reeng     lucaskuramoti06@gmail.com
"""
import sys
import os

# garante que o root do projeto está no path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.env import load_app_env
load_app_env()

import db

# Garante que as migrações (colunas novas) existam no banco local
db.init_db()

from core.services.email_service import (
    send_tip_email,
    send_insight_email,
    send_reengagement_email,
)

TIPOS = {
    "tip":    ("Dica de uso",            send_tip_email),
    "insight":("Insight de investimento", send_insight_email),
    "reeng":  ("Reengajamento",           send_reengagement_email),
}

def main():
    if len(sys.argv) < 3:
        print("Uso: python scripts/test_email.py <tipo> <email>")
        print("Tipos disponíveis:", ", ".join(TIPOS))
        sys.exit(1)

    tipo  = sys.argv[1].lower()
    email = sys.argv[2]

    if tipo not in TIPOS:
        print(f"Tipo '{tipo}' inválido. Use: {', '.join(TIPOS)}")
        sys.exit(1)

    # Busca o user_id no banco para gerar o link de cancelamento
    row = db.get_user_by_email(email)
    if row:
        user_id = row["user_id"]
        print(f"Usuário encontrado: user_id={user_id}")
    else:
        user_id = None
        print("⚠️  Usuário não encontrado no banco — link de cancelamento não será incluído.")

    label, fn = TIPOS[tipo]
    print(f"Enviando email de '{label}' para {email}...")

    ok = fn(email, user_id)
    if ok:
        print("✅ Enviado com sucesso! Verifique sua caixa de entrada.")
    else:
        print("❌ Falha no envio. Verifique se RESEND_API_KEY está configurada no .env")

if __name__ == "__main__":
    main()
