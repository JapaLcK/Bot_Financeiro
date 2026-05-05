"""
Descobre seu user_id a partir do email ou telefone.

Uso:
    python -m scripts.whoami <email_ou_telefone>

Ex:
    python -m scripts.whoami lucaskuramoti06@gmail.com
    python -m scripts.whoami +5511999998888
"""
import sys
from dotenv import load_dotenv

load_dotenv()

from db.connection import get_conn


def run(query: str) -> None:
    is_phone = query.startswith("+") or query.replace(" ", "").isdigit()
    with get_conn() as conn:
        with conn.cursor() as cur:
            if is_phone:
                cur.execute(
                    "select id, user_id, email, phone_e164, phone_status "
                    "from auth_accounts where phone_e164 = %s",
                    (query,),
                )
            else:
                cur.execute(
                    "select id, user_id, email, phone_e164, phone_status "
                    "from auth_accounts where lower(email) = lower(%s)",
                    (query,),
                )
            rows = cur.fetchall()

    if not rows:
        print(f"Nenhuma conta encontrada para: {query}")
        return

    for r in rows:
        print(f"user_id    : {r['user_id']}")
        print(f"email      : {r['email']}")
        print(f"phone_e164 : {r['phone_e164']}")
        print(f"status     : {r['phone_status']}")
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.whoami <email_ou_telefone>")
        sys.exit(2)
    run(sys.argv[1])
