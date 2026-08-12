"""
scripts/push_test.py — Verifica registro de device token e dispara push de teste.

RODAR NO AMBIENTE QUE TEM O BANCO DE PROD + as envs APNS_* (ou seja, no serviço
Railway "Dashboard"). Ex.: `railway run python scripts/push_test.py <user_id>`
ou pelo shell/one-off do serviço.

Uso:
    python scripts/push_test.py <user_id>            # só CONFERE se há token
    python scripts/push_test.py <user_id> --send     # confere E manda o push

O texto do push é genérico de propósito (regra Apple: nada de valor/saldo).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import list_push_tokens
from core.services import push_service


def main():
    args = [a for a in sys.argv[1:]]
    if not args or not args[0].isdigit():
        print("uso: python scripts/push_test.py <user_id> [--send]")
        sys.exit(1)
    user_id = int(args[0])
    do_send = "--send" in args

    tokens = list_push_tokens(user_id)
    print(f"[push_test] user {user_id}: {len(tokens)} token(s) registrado(s)")
    for t in tokens:
        tok = t["token"]
        print(f"  - …{tok[-8:]}  platform={t.get('platform')}  env={t.get('environment')}")

    if not tokens:
        print("[push_test] Nenhum token. Abra o app logado e aceite as notificações.")
        return

    print(f"[push_test] APNs configurado? {push_service.is_configured()}")
    if not do_send:
        print("[push_test] (rode com --send pra disparar o push de teste)")
        return

    if not push_service.is_configured():
        print("[push_test] Envs APNS_* ausentes — não dá pra enviar. Confira o serviço Dashboard.")
        return

    sent = push_service.send_push_to_user(
        user_id,
        "PigBank",
        "Teste de notificação 🐷 — se você recebeu, o push está funcionando!",
        collapse_id="push-test",
    )
    print(f"[push_test] APNs aceitou {sent} entrega(s).")


if __name__ == "__main__":
    main()
