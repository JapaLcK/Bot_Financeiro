"""
scripts/smoke_test_emails.py
Smoke-test de entrega/visual: dispara UM e-mail de cada categoria pros
destinatários informados.

  - Agentes:      Repórter (usa os compositores reais, com hero/arte)
  - Engajamento:  tip (dica de uso)
  - Transacional: verificação de e-mail (código de 6 dígitos)

Uso:
  python scripts/smoke_test_emails.py hiagojo2016@gmail.com lucaskuramoti06@gmail.com
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.env import load_app_env
load_app_env()

import db
db.init_db()

from core.services.email_service import (
    send_agent_report_email,
    send_tip_email,
    send_verification_email,
)
from core.services.piggy_agents import _agent_email_subject, _compose_agent_email


# Eventos sintéticos pro Repórter (mesma forma dos reais: payload.titulo/mensagem)
_REPORTER_EVENTS = [
    {"id": 0, "payload": {
        "titulo": "Seu mês fechou no azul",
        "mensagem": "Você gastou R$ 2.480 e recebeu R$ 3.900 — sobrou R$ 1.420, "
                    "24% a mais que no mês passado. Bora manter o ritmo. 🐷",
    }},
    {"id": 0, "payload": {
        "titulo": "iFood foi o vilão da semana",
        "mensagem": "R$ 312 em delivery nos últimos 7 dias. Só um aviso amigável "
                    "do seu Repórter.",
    }},
]


def _send_agent(email: str, user_id: int) -> bool:
    kind = "reporter"
    subject = _agent_email_subject(kind, _REPORTER_EVENTS)
    html = _compose_agent_email(kind, _REPORTER_EVENTS)
    return send_agent_report_email(email, user_id, subject, html, kind=kind)


def main():
    recipients = sys.argv[1:]
    if not recipients:
        print("Uso: python scripts/smoke_test_emails.py <email> [email2 ...]")
        sys.exit(1)

    for email in recipients:
        row = db.get_user_by_email(email)
        user_id = row["user_id"] if row else 0
        tag = f"user_id={user_id}" if row else "SEM cadastro (user_id=0)"
        print(f"\n=== {email} ({tag}) ===")

        r1 = _send_agent(email, user_id)
        print(f"  [agentes]      Repórter (hero) ....... {'OK ✅' if r1 else 'FALHOU ❌'}")

        r2 = send_tip_email(email, user_id or None)
        print(f"  [engajamento]  Dica de uso ........... {'OK ✅' if r2 else 'FALHOU ❌'}")

        r3 = send_verification_email(email, "482913")
        print(f"  [transacional] Código de verificação . {'OK ✅' if r3 else 'FALHOU ❌'}")

    print("\nPronto. Confira as duas caixas de entrada (e o Spam/Promoções).")


if __name__ == "__main__":
    main()
