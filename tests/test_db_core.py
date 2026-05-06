from decimal import Decimal

from db import (
    get_balance,
    add_launch_and_update_balance,
    list_launches,
    get_summary_by_period,
    create_pocket,
    pocket_deposit_from_account,
    pocket_withdraw_to_account,
    create_investment_db,
    investment_deposit_from_account,
    investment_withdraw_to_account,
    delete_launch_and_rollback,
    attempt_whatsapp_phone_link,
    confirm_email_verification,
    create_email_verification,
    create_card,
    get_conn,
    ensure_user,
    import_credit_ofx_bulk,
    list_cards,
)
import uuid

def D(x) -> Decimal:
    return Decimal(str(x))

def test_saldo_e_lancamentos(user_id):
    assert get_balance(user_id) == D("0")

    l1, seq1, bal = add_launch_and_update_balance(user_id, "receita", 1000, None, "salario")
    assert bal == D("1000")
    assert seq1 == 1

    l2, seq2, bal = add_launch_and_update_balance(user_id, "despesa", 120, None, "mercado")
    assert bal == D("880")
    assert seq2 == 2

    rows = list_launches(user_id, limit=10)
    assert len(rows) >= 2


def test_caixinha_deposito_saque(user_id):
    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    create_pocket(user_id, "viagem")

    _, bal_acc, bal_pocket, _ = pocket_deposit_from_account(user_id, "viagem", 300, "teste")
    assert bal_acc == D("700")
    assert bal_pocket == D("300")

    _, bal_acc, bal_pocket, _ = pocket_withdraw_to_account(user_id, "viagem", 100, "teste")
    assert bal_acc == D("800")
    assert bal_pocket == D("200")


def test_investimento_aporte_resgate(user_id):
    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    create_investment_db(user_id, "CDB Nubank", rate=0.01, period="monthly", nota="teste")

    _, bal_acc, bal_inv, _ = investment_deposit_from_account(user_id, "CDB Nubank", 200, "aporte")
    assert bal_acc == D("800")
    assert bal_inv == D("200")

    _, bal_acc, bal_inv, _, _ = investment_withdraw_to_account(user_id, "CDB Nubank", 50, "resgate")
    assert bal_acc == D("850")
    assert bal_inv == D("150")


def test_delete_launch_and_rollback(user_id):
    assert get_balance(user_id) == D("0")
    l1, _seq1, bal = add_launch_and_update_balance(user_id, "receita", 1000, None, "salario")
    l2, _seq2, bal = add_launch_and_update_balance(user_id, "despesa", 200, None, "mercado")
    assert get_balance(user_id) == D("800")

    delete_launch_and_rollback(user_id, int(l2))
    assert get_balance(user_id) == D("1000")


def test_summary_by_period_soma_receitas_e_despesas(user_id):
    from datetime import date

    add_launch_and_update_balance(user_id, "receita", 500, None, "pix")
    add_launch_and_update_balance(user_id, "despesa", 135, None, "rifa")

    summary = get_summary_by_period(user_id, date.today(), date.today())

    assert summary["receita"] == D("500")
    assert summary["despesa"] == D("135")


def test_attempt_whatsapp_phone_link_religa_quando_stale_uid_tem_auth_diferente(user_id):
    """
    Reproduz o bug uid=832398038:
    - stale_wa_uid: conta WA antiga, tem auth_account com telefone DIFERENTE do WA
    - user_id (fixture): conta correta, tem auth_account com o mesmo telefone do WA
    - current_user_id=stale_wa_uid é passado (simula get_or_create_canonical_user antigo)
    Esperado: status='linked', user_id retornado = user_id correto (target).
    A identidade WA deve apontar para o user_id correto depois.
    """
    wa_phone = "5511987650001"
    wa_email = f"wa-stale-{uuid.uuid4().hex[:8]}@example.com"
    stale_email = f"stale-{uuid.uuid4().hex[:8]}@example.com"
    stale_wa_uid = int(uuid.uuid4().int % 10_000_000_000)

    ensure_user(stale_wa_uid)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # conta correta: telefone bate com o WA
                cur.execute(
                    """
                    insert into auth_accounts (user_id, email, password_hash, phone_e164, phone_status)
                    values (%s, %s, %s, %s, 'pending')
                    """,
                    (user_id, wa_email, "hash", wa_phone),
                )
                # stale: tem auth_account, mas com outro telefone
                cur.execute(
                    """
                    insert into auth_accounts (user_id, email, password_hash, phone_e164, phone_status)
                    values (%s, %s, %s, %s, 'pending')
                    """,
                    (stale_wa_uid, stale_email, "hash", "5511000000000"),
                )
                # simula identidade WA stale apontando para stale_wa_uid
                cur.execute(
                    """
                    insert into user_identities (provider, external_id, user_id)
                    values ('whatsapp', %s, %s)
                    on conflict (provider, external_id) do update set user_id = excluded.user_id
                    """,
                    (wa_phone, stale_wa_uid),
                )
            conn.commit()

        result = attempt_whatsapp_phone_link(wa_phone, current_user_id=stale_wa_uid)

        assert result["status"] == "linked", f"esperado 'linked', veio {result}"
        assert result["user_id"] == user_id, (
            f"esperado user_id={user_id} (conta correta), veio {result['user_id']}"
        )

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select user_id
                    from user_identities
                    where provider = 'whatsapp' and external_id = %s
                    """,
                    (wa_phone,),
                )
                row = cur.fetchone()

        assert row is not None
        assert row["user_id"] == user_id, (
            f"identidade WA deve apontar para {user_id}, aponta para {row['user_id']}"
        )
    finally:
        # stale pode ter sido absorvido (merge) — tenta deletar mesmo assim
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from user_identities where provider='whatsapp' and external_id=%s", (wa_phone,))
                cur.execute("delete from auth_accounts where user_id = %s", (stale_wa_uid,))
                cur.execute("delete from users where id = %s", (stale_wa_uid,))
            conn.commit()


def test_attempt_whatsapp_phone_link_aceita_variacao_com_e_sem_nono_digito(user_id):
    email = f"wa-link-{uuid.uuid4().hex[:8]}@example.com"
    other_uid = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(other_uid)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into auth_accounts (user_id, email, password_hash, phone_e164, phone_status)
                    values (%s, %s, %s, %s, 'pending')
                    """,
                    (user_id, email, "hash", "556592741873"),
                )
            conn.commit()

        result = attempt_whatsapp_phone_link("5565992741873", current_user_id=other_uid)

        assert result["status"] == "linked"

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select user_id
                    from user_identities
                    where provider = 'whatsapp' and external_id = %s
                    """,
                    ("5565992741873",),
                )
                row = cur.fetchone()

        assert row["user_id"] == user_id
    finally:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from users where id = %s", (other_uid,))
            conn.commit()


def test_attempt_whatsapp_phone_link_aceita_identidade_existente_com_alias_do_mesmo_numero(user_id):
    email = f"wa-alias-{uuid.uuid4().hex[:8]}@example.com"
    old_wa = "556592741873"
    new_wa = "5565992741873"
    new_uid = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(new_uid)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into auth_accounts (user_id, email, password_hash, phone_e164, phone_status)
                    values (%s, %s, %s, %s, 'confirmed')
                    """,
                    (user_id, email, "hash", old_wa),
                )
                cur.execute(
                    """
                    insert into user_identities (provider, external_id, user_id)
                    values ('whatsapp', %s, %s)
                    """,
                    (old_wa, user_id),
                )
                cur.execute(
                    """
                    insert into user_identities (provider, external_id, user_id)
                    values ('whatsapp', %s, %s)
                    """,
                    (new_wa, new_uid),
                )
            conn.commit()

        add_launch_and_update_balance(new_uid, "despesa", 109, None, "rifa")

        result = attempt_whatsapp_phone_link(new_wa, current_user_id=new_uid)

        assert result["status"] == "linked"
        assert result["user_id"] == user_id
        assert get_balance(user_id) == D("-109")

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select user_id
                    from user_identities
                    where provider = 'whatsapp' and external_id = %s
                    """,
                    (new_wa,),
                )
                row = cur.fetchone()

        assert row["user_id"] == user_id
    finally:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from users where id = %s", (new_uid,))
            conn.commit()


def test_import_credit_ofx_nao_grava_limite_automaticamente(user_id):
    card_id = create_card(user_id, "Nubank", 10, 17)

    import_credit_ofx_bulk(
        user_id=user_id,
        card_id=card_id,
        tx_rows=[],
        file_hash=f"test-{uuid.uuid4().hex}",
        dt_start=None,
        dt_end=None,
        credit_limit=Decimal("4504.44"),
    )

    card = next(c for c in list_cards(user_id) if c["id"] == card_id)

    assert card["credit_limit"] is None


def test_confirm_email_verification_normaliza_telefone_armazenado_no_codigo():
    # Limpeza do user_id criado dentro do teste fica a cargo do autouse
    # _auto_cleanup_orphan_users (ver tests/conftest.py), que faz snapshot
    # de `users` antes/depois e apaga qualquer registro novo respeitando FKs.
    email = f"verify-{uuid.uuid4().hex[:8]}@example.com"
    raw_phone = "+55 (65) 99274-1873"

    code = create_email_verification(email, "123456", "5565992741873")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update email_verification_codes
                set phone_e164 = %s
                where email = %s and code = %s
                """,
                (raw_phone, email, code),
            )
        conn.commit()

    result = confirm_email_verification(email, code)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select phone_e164 from auth_accounts where user_id = %s",
                (result["user_id"],),
            )
            row = cur.fetchone()

    assert row["phone_e164"] == "5565992741873"
