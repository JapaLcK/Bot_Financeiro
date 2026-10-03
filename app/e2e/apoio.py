"""Apoio dos fluxos Maestro de `app/e2e/` — o lado do servidor que o app não tem.

Roda com o ambiente do ALVO já exportado (`rodar.sh`): banco descartável local
ou o do staging via `railway run`. Nunca aponte para produção.

    apoio.py semear            5 contas novas; grava contas.json em $E2E_DIR
    apoio.py totp <n>          código TOTP da conta n (espera o passo se faltar < 15 s)
    apoio.py totp-alheio <n>   código que a conta n NÃO aceita agora (controle negativo)
    apoio.py reset <n>         troca a senha da conta n pelo token do e-mail
    apoio.py revogar <n>       "outro aparelho" entra e encerra as outras sessões
    apoio.py campo <n> <chave> um campo de contas.json (email, senha, backup)

Contas: 1 e-mail+senha, 2 MFA TOTP, 3 MFA backup, 4 esqueci a senha, 5 revogada.
"""

import json
import os
import pathlib
import sys
import time
import urllib.request

import pyotp

ARQUIVO = pathlib.Path(os.environ["E2E_DIR"]) / "contas.json"
API = os.environ.get("E2E_API", "http://127.0.0.1:8000").rstrip("/")
NOME = "E2E"
MFA = {"2", "3"}


def _contas() -> dict:
    return json.loads(ARQUIVO.read_text())


def _http(metodo: str, rota: str, corpo: dict | None = None, bearer: str | None = None) -> dict:
    # User-Agent próprio: o Cloudflare do staging devolve 403 ao "Python-urllib".
    cab = {"X-PigBank-Client": "app", "Content-Type": "application/json", "User-Agent": "PigBankApp/e2e"}
    if bearer:
        cab["Authorization"] = f"Bearer {bearer}"
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(API + rota, data=dados, headers=cab, method=metodo)
    with urllib.request.urlopen(req, timeout=20) as r:  # HTTPError (4xx/5xx) sobe e derruba a rodada
        return json.loads(r.read() or b"{}")


def semear() -> None:
    from db import (
        confirm_email_verification, create_email_verification, mark_plan_selected, mfa_setup_secret, mfa_verify_and_enable,
    )

    rodada = os.environ["E2E_RUN"]
    contas = {}
    for n in "12345":
        email = f"delivered+e2e-{rodada}-{n}@resend.dev"
        senha = f"E2e-{rodada}-senha{n}"
        codigo = create_email_verification(email, senha, None, display_name=NOME)
        uid = confirm_email_verification(email, codigo)["user_id"]
        # Como todo usuário real (o webhook do pagamento marca): sem isto, com o
        # PLANS_V2 ligado (staging), o encerrar-sessões do `revogar` dá 402.
        mark_plan_selected(uid)
        conta = {"email": email, "senha": senha, "user_id": int(uid)}
        if n in MFA:
            segredo = mfa_setup_secret(uid, email)["secret"]
            conta["totp"] = segredo
            conta["backup"] = mfa_verify_and_enable(uid, pyotp.TOTP(segredo).now())[0]
        contas[n] = conta
    ARQUIVO.write_text(json.dumps(contas, indent=2))


def totp(segredo: str) -> str:
    # _TOTP_WINDOW=1 (db/mfa.py) aceita ±1 passo: o código do PRÓXIMO passo já vale
    # agora e vale até o fim do passo seguinte a ele — ≥ 60 s para o Maestro subir,
    # entrar e digitar (no staging, com a máquina carregada, 45 s não bastaram).
    return pyotp.TOTP(segredo).at(time.time() + 30)


def reset(conta: dict) -> None:
    from db import get_conn

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select token from password_reset_tokens where user_id = %s and used_at is null"
            " order by created_at desc limit 1",
            (conta["user_id"],),
        )
        linha = cur.fetchone()
    if not linha:
        sys.exit(f"sem token de reset para user_id={conta['user_id']} — o 04a não pediu?")
    conta["senha"] = conta["senha"] + "-nova"
    _http("POST", "/auth/reset-password", {"token": linha["token"], "new_password": conta["senha"]})


def revogar(conta: dict) -> None:
    sessao = _http("POST", "/auth/login", {"email": conta["email"], "password": conta["senha"]})
    r = _http("DELETE", f"/settings/{conta['user_id']}/sessions", bearer=sessao["access_token"])
    if not r.get("revoked"):
        sys.exit(f"nada revogado: {r}")


def main(args: list[str]) -> None:
    cmd = args[0]
    if cmd == "semear":
        return semear()
    contas = _contas()
    conta = contas[args[1]]
    if cmd == "totp-alheio":
        # Fora de todo passo que o servidor aceita (±1) de agora até 2 min: determinístico, nunca válido.
        t = pyotp.TOTP(conta["totp"])
        aceitos = {t.at(time.time() + 30 * k) for k in range(-2, 5)}
        print(next(c for c in (f"{i:06d}" for i in range(8)) if c not in aceitos))
    elif cmd == "totp":
        print(totp(conta["totp"]))
    elif cmd == "reset":
        reset(conta)
        ARQUIVO.write_text(json.dumps(contas, indent=2))
    elif cmd == "revogar":
        revogar(conta)
    elif cmd == "campo":  # campo <n> <chave>: o rodar.sh lê as contas por aqui, sem jq
        print(conta[args[2]])
    else:
        sys.exit(f"comando desconhecido: {cmd}")


if __name__ == "__main__":
    main(sys.argv[1:])
