"""
Smoke test pós-deploy para exclusão real de conta temporária.

O teste cria dois usuários temporários no banco:
- vítima: será agendada via API e removida de verdade pela rotina de exclusão;
- controle: deve permanecer intacto para provar que a exclusão ficou no escopo certo.

Por segurança, o script só remove usuários criados por ele nesta execução e exige
confirmação explícita na linha de comando.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.env import load_app_env


CONFIRMATION = "EXCLUIR_APENAS_USUARIO_TEMPORARIO"
EMAIL_DOMAIN = "post-deploy.pigbankai.invalid"
ID_BASE = 9_000_000_000_000


class SafetyError(RuntimeError):
    pass


def _json_or_text(response: requests.Response):
    try:
        return response.json()
    except ValueError:
        return response.text[:500]


def _url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _assert_safe_base_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" and host not in {"localhost", "127.0.0.1", "::1"}:
        raise SafetyError("Use HTTPS em produção. HTTP só é aceito para localhost.")
    if not host:
        raise SafetyError("BASE_URL inválida.")


def _require_env() -> None:
    missing = [name for name in ("DATABASE_URL",) if not (os.getenv(name) or "").strip()]
    if missing:
        raise SafetyError(f"Variáveis obrigatórias ausentes: {', '.join(missing)}")
    db_host = (urlparse(os.getenv("DATABASE_URL", "")).hostname or "").lower()
    running_inside_railway = any(name.startswith("RAILWAY_") for name in os.environ)
    if db_host.endswith(".railway.internal") and not running_inside_railway:
        raise SafetyError(
            "DATABASE_URL usa host interno da Railway (*.railway.internal), que só resolve dentro da Railway. "
            "Rode este script em um serviço/job da Railway ou use a URL pública/TCP Proxy do Postgres."
        )


def _temp_user_id() -> int:
    return ID_BASE + secrets.randbelow(900_000_000)


def _temp_phone() -> str:
    return f"+1888{secrets.randbelow(10_000_000_000):010d}"


def _count_user_rows(cur, table: str, user_id: int) -> int:
    cur.execute("select to_regclass(%s) is not null as exists", (table,))
    row = cur.fetchone()
    if not row or not row["exists"]:
        return 0
    cur.execute(
        """
        select exists (
          select 1
          from information_schema.columns
          where table_schema = 'public'
            and table_name = %s
            and column_name = 'user_id'
        ) as exists
        """,
        (table,),
    )
    row = cur.fetchone()
    if not row or not row["exists"]:
        return 0
    cur.execute(f"select count(*) as total from {table} where user_id = %s", (user_id,))
    return int(cur.fetchone()["total"])


def _assert_account_email(cur, user_id: int, expected_email: str) -> None:
    cur.execute("select email from auth_accounts where user_id = %s", (user_id,))
    row = cur.fetchone()
    if not row:
        raise SafetyError(f"Conta temporária {user_id} não foi encontrada.")
    if row["email"] != expected_email:
        raise SafetyError(
            f"Abortado: user_id {user_id} pertence a {row['email']!r}, não ao teste {expected_email!r}."
        )
    if not expected_email.endswith(f"@{EMAIL_DOMAIN}"):
        raise SafetyError("Abortado: e-mail temporário não tem o domínio seguro do teste.")


def _cleanup_temp_user(user_id: int, expected_email: str) -> None:
    import db

    exists = False
    owned_rows = 0
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select email from auth_accounts where user_id = %s", (user_id,))
        row = cur.fetchone()
        if row and row["email"] != expected_email:
            raise SafetyError(f"Cleanup abortado: user_id {user_id} não pertence ao teste.")
        exists = bool(row)
        for table in ("users", "auth_accounts", "launches", "accounts"):
            if table == "users":
                cur.execute("select count(*) as total from users where id = %s", (user_id,))
                owned_rows += int(cur.fetchone()["total"])
            else:
                owned_rows += _count_user_rows(cur, table, user_id)
    db.delete_user_data(user_id)
    if exists or owned_rows:
        print(f"[post_deploy_deletion] cleanup OK: user_id={user_id}", flush=True)


def _cleanup_stale_temp_user(user_id: int) -> None:
    import db

    if int(user_id) < ID_BASE:
        raise SafetyError("Cleanup manual só aceita IDs temporários altos criados por este smoke test.")

    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select email from auth_accounts where user_id = %s", (user_id,))
        row = cur.fetchone()
        if row and not str(row["email"]).endswith(f"@{EMAIL_DOMAIN}"):
            raise SafetyError(f"Cleanup manual abortado: user_id {user_id} não pertence ao domínio temporário.")

        owned_rows = 0
        cur.execute("select count(*) as total from users where id = %s", (user_id,))
        owned_rows += int(cur.fetchone()["total"])
        for table in ("auth_accounts", "launches", "accounts"):
            owned_rows += _count_user_rows(cur, table, user_id)

    db.delete_user_data(user_id)
    print(f"[post_deploy_deletion] cleanup manual OK: user_id={user_id}; rows_before={owned_rows}", flush=True)


def _create_temp_account(user_id: int, email: str, password: str, launch_note: str) -> None:
    import db
    from db.users import _hash_password

    db.ensure_account_deletion_columns()
    db.ensure_user(user_id)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_accounts (user_id, email, password_hash, phone_e164, phone_status)
                values (%s, %s, %s, %s, 'confirmed')
                """,
                (user_id, email, _hash_password(password), _temp_phone()),
            )
            cur.execute(
                """
                insert into launches (user_id, tipo, valor, nota, categoria, criado_em)
                values (%s, 'despesa', 12.34, %s, 'smoke-test', now())
                """,
                (user_id, launch_note),
            )
        conn.commit()


def _csrf_headers(session: requests.Session, base_url: str) -> dict[str, str]:
    host = urlparse(base_url).hostname or ""
    csrf = ""
    for cookie in session.cookies:
        if cookie.name == "csrf_token" and (not cookie.domain or host.endswith(cookie.domain.lstrip("."))):
            csrf = cookie.value
    if not csrf:
        csrf = secrets.token_urlsafe(24)
        session.cookies.set("csrf_token", csrf, domain=host, path="/")
    return {"X-CSRF-Token": csrf}


def _login_temp_user(base_url: str, session: requests.Session, email: str, password: str, expected_user_id: int) -> None:
    session.get(_url(base_url, "/"), timeout=20)
    response = session.post(
        _url(base_url, "/auth/login"),
        json={"email": email, "password": password},
        headers=_csrf_headers(session, base_url),
        timeout=20,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Login falhou: {response.status_code} {_json_or_text(response)}")
    payload = response.json()
    if int(payload.get("user_id") or 0) != int(expected_user_id):
        raise SafetyError(f"Login retornou user_id inesperado: {payload}")


def _schedule_deletion_via_api(base_url: str, session: requests.Session, password: str, expected_user_id: int) -> None:
    response = session.request(
        "DELETE",
        _url(base_url, "/auth/account"),
        json={"password": password},
        headers=_csrf_headers(session, base_url),
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Agendamento falhou: {response.status_code} {_json_or_text(response)}")
    payload = response.json()
    if int(payload.get("user_id") or 0) != int(expected_user_id):
        raise SafetyError(f"DELETE /auth/account retornou user_id inesperado: {payload}")
    if payload.get("status") != "scheduled":
        raise SafetyError(f"DELETE /auth/account não agendou a exclusão: {payload}")


def _complete_deletion_for_exact_temp_user(user_id: int, expected_email: str) -> dict:
    import db

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            _assert_account_email(cur, user_id, expected_email)
            cur.execute(
                """
                select deletion_status
                from auth_accounts
                where user_id = %s
                for update
                """,
                (user_id,),
            )
            row = cur.fetchone()
            if not row or row["deletion_status"] != "scheduled":
                raise SafetyError(f"Conta temporária não está agendada para exclusão: {row}")
            cur.execute(
                """
                update auth_accounts
                set deletion_scheduled_for = now() - interval '1 minute',
                    deletion_status = 'processing',
                    deletion_processing_started_at = now()
                where user_id = %s
                  and email = %s
                  and deletion_status = 'scheduled'
                """,
                (user_id, expected_email),
            )
            if cur.rowcount != 1:
                raise SafetyError("Não foi possível marcar exatamente uma conta temporária para processamento.")
        conn.commit()

    return db.delete_user_data(user_id)


def _verify_deleted_and_scoped(victim_id: int, control_id: int, control_note: str) -> None:
    import db

    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select 1 from users where id = %s", (victim_id,))
        if cur.fetchone():
            raise RuntimeError("Usuário vítima ainda existe após a exclusão efetiva.")
        for table in ("auth_accounts", "launches", "accounts"):
            total = _count_user_rows(cur, table, victim_id)
            if total:
                raise RuntimeError(f"Tabela {table} ainda tem {total} registro(s) da vítima.")

        cur.execute("select 1 from users where id = %s", (control_id,))
        if not cur.fetchone():
            raise RuntimeError("Usuário controle foi apagado, então o escopo da exclusão falhou.")
        cur.execute("select nota from launches where user_id = %s", (control_id,))
        row = cur.fetchone()
        if not row or row["nota"] != control_note:
            raise RuntimeError("Lançamento do usuário controle não foi preservado.")


def run(base_url: str, confirm: str) -> int:
    if confirm != CONFIRMATION:
        raise SafetyError(f"Confirmação inválida. Use --confirm {CONFIRMATION}")

    _assert_safe_base_url(base_url)
    _require_env()

    marker = f"{int(time.time())}-{secrets.token_hex(4)}"
    password = f"PigBankSmoke!{secrets.token_hex(6)}"
    victim_id = _temp_user_id()
    control_id = _temp_user_id()
    victim_email = f"delete-{marker}@{EMAIL_DOMAIN}"
    control_email = f"keep-{marker}@{EMAIL_DOMAIN}"
    victim_note = f"post-deploy delete victim {marker}"
    control_note = f"post-deploy keep control {marker}"

    print(f"[post_deploy_deletion] base_url={base_url}")
    print(f"[post_deploy_deletion] vítima temporária: user_id={victim_id} email={victim_email}")
    print(f"[post_deploy_deletion] controle temporário: user_id={control_id} email={control_email}")

    try:
        _create_temp_account(victim_id, victim_email, password, victim_note)
        _create_temp_account(control_id, control_email, password, control_note)

        session = requests.Session()
        _login_temp_user(base_url, session, victim_email, password, victim_id)
        print("[post_deploy_deletion] login da vítima temporária OK")

        _schedule_deletion_via_api(base_url, session, password, victim_id)
        print("[post_deploy_deletion] DELETE /auth/account agendou somente a vítima temporária")

        result = _complete_deletion_for_exact_temp_user(victim_id, victim_email)
        if not result.get("deleted"):
            raise RuntimeError(f"Rotina de exclusão não confirmou remoção: {result}")
        print("[post_deploy_deletion] exclusão efetiva da vítima temporária OK")

        _verify_deleted_and_scoped(victim_id, control_id, control_note)
        print("[post_deploy_deletion] escopo OK: controle temporário permaneceu intacto")
    finally:
        _cleanup_temp_user(victim_id, victim_email)
        _cleanup_temp_user(control_id, control_email)

    print("[post_deploy_deletion] PASSOU")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_app_env()

    parser = argparse.ArgumentParser(description="Testa exclusão real de uma conta temporária pós-deploy.")
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "https://pigbankai.com"))
    parser.add_argument("--confirm", default="")
    parser.add_argument(
        "--cleanup-user-id",
        type=int,
        help="Remove resíduos de um user_id temporário criado por este smoke test.",
    )
    args = parser.parse_args(argv)

    try:
        if args.cleanup_user_id is not None:
            if args.confirm != CONFIRMATION:
                raise SafetyError(f"Confirmação inválida. Use --confirm {CONFIRMATION}")
            _require_env()
            _cleanup_stale_temp_user(args.cleanup_user_id)
            return 0
        return run(args.base_url, args.confirm)
    except Exception as exc:
        print(f"[post_deploy_deletion] FALHOU: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
