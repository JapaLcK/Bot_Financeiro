"""Helpers compartilhados dos testes de `plan_grants` (PR 1a).

Sem prefixo `test_` de propósito: o pytest não coleta este arquivo. Ele existe
para os três arquivos de teste do assunto não carregarem três cópias do mesmo
`_conta`/`_ler` — cópia de helper é como dois testes passam a medir coisas
diferentes achando que medem a mesma.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from db.connection import get_conn
from db.plan_grants import list_grants
from db.schema import RESYNC_LEGACY_GRANTS_SQL


def garantir_system_event_logs() -> None:
    """`system_event_logs` nasce no `ensure_admin_tables` do startup, não no
    `init_db` — então num banco de teste (TestClient sem lifespan) ela não
    existe, e TODA dedup baseada em `recent_event_exists` vira no-op silencioso.

    Descoberto medindo: o teste de reentrega acusou 3 e-mails onde devia haver
    1, e a causa não era o dedup — era a tabela ausente fazendo
    `recent_event_exists` devolver False por exceção. Chama a função de
    produção em vez de recriar o DDL aqui (`CLAUDE.md` §0.7).
    """
    import asyncio

    from core.admin_dashboard import ensure_admin_tables
    asyncio.run(ensure_admin_tables())


def rodar_resync() -> None:
    """Executa EXATAMENTE o SQL que o init_db roda no boot — sem segunda cópia."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(RESYNC_LEGACY_GRANTS_SQL)
        conn.commit()


def conta(uid: int, plan: str, expires, status: str = "active") -> None:
    """Cria/atualiza a auth_accounts do usuário.

    UPDATE e só então INSERT: `auth_accounts` não tem unique em `user_id`
    (só em email/phone), então `on conflict (user_id)` não existe.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set plan=%s, plan_expires_at=%s,"
                "       last_payment_status=%s where user_id=%s",
                (plan, expires, status, uid),
            )
            if cur.rowcount == 0:
                cur.execute(
                    "insert into auth_accounts (user_id, email, password_hash, plan,"
                    "                           plan_expires_at, last_payment_status)"
                    " values (%s, %s, 'x', %s, %s, %s)",
                    (uid, f"gr-{uid}@t.local", plan, expires, status),
                )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(uid)


def ler(uid: int) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select plan, plan_expires_at, last_payment_status, stripe_customer_id"
                "  from auth_accounts where user_id=%s", (uid,))
            return dict(cur.fetchone())


def grant(uid: int, source: str) -> dict | None:
    linhas = [g for g in list_grants(uid) if g["source"] == source]
    return linhas[0] if linhas else None


def set_customer(uid: int, customer_id: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update auth_accounts set stripe_customer_id=%s where user_id=%s",
                        (customer_id, uid))
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(uid)


def sub_stripe(status: str, price_id: str, dias: int) -> dict:
    ts = int((datetime.now(timezone.utc) + timedelta(days=dias)).timestamp())
    return {"id": "sub_generico", "status": status, "current_period_end": ts,
            "items": {"data": [{"price": {"id": price_id}, "current_period_end": ts}]}}


def evt_paid(uid: int, sub_id: str, created: int) -> dict:
    return {"type": "invoice.paid", "id": f"evt_paid_{created}", "created": created,
            "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                "subscription": sub_id, "amount_paid": 0,
                                "id": f"in_{created}"}}}


def evt_deleted(uid: int, sub_id: str, created: int) -> dict:
    return {"type": "customer.subscription.deleted", "id": f"evt_del_{created}",
            "created": created,
            "data": {"object": {"id": sub_id, "metadata": {"finbot_user_id": str(uid)}}}}


def evt_checkout(uid: int, subscription, created: int, session_id: str) -> dict:
    """`subscription` pode ser string, objeto expandido (9g) ou None.

    `None` OMITE a chave — checkout de pagamento avulso não traz `subscription`.
    A versão anterior deste helper mandava a chave SEMPRE, e foi por isso que o
    PR inteiro não viu o `UnboundLocalError` do `_invoice_subscription_id`:
    nenhum teste exercitava o caminho em que ele cai no fallback do `parent`.
    """
    objeto = {"metadata": {"finbot_user_id": str(uid)}, "id": session_id}
    if subscription is not None:
        objeto["subscription"] = subscription
    return {"type": "checkout.session.completed", "id": f"evt_co_{created}",
            "created": created, "data": {"object": objeto}}


def espiao_email(vistos: dict, nome: str):
    """Substitui um remetente de e-mail e GRAVA a tupla de argumentos.

    Duas coisas não são detalhe. `__name__` é a CHAVE da dedupe do `_fire_email`
    (`finance_bot_websocket_custom.py`): três espiões com o mesmo nome viram um
    balde só e os e-mails 2 e 3 saem DEDUPADOS, com o teste acusando "não
    chamou". E o `return True` é o que faz o envio contar como confirmado —
    `_fire_email` trata falsy como falha, não grava a chave e não segue.
    """
    def _fn(*a, **kw):
        vistos[nome] = a
        return True
    _fn.__name__ = nome
    return _fn


def espiao_no_loop(onde: list[bool], fn):
    """Embrulha `fn` num espião que registra em `onde` se a chamada rodou NA
    THREAD DE UM EVENT LOOP (True = a chamada bloqueia o loop; False = executor)
    e então chama `fn` por baixo. `__name__` preservado (`functools.wraps`): é a
    chave da dedupe do `_fire_email` quando o embrulhado é um remetente.

    O observável é `asyncio.get_running_loop()`, que levanta `RuntimeError`
    quando NÃO há loop rodando na thread atual. Chamado direto do corpo da
    corrotina, ele devolve o loop (a chamada bloqueia o loop); despachado por
    `asyncio.to_thread`, roda numa worker do `ThreadPoolExecutor`, onde não há
    loop nenhum e ele levanta.

    **A primeira versão disto comparava com `threading.main_thread()` e não
    media NADA**: o `TestClient` do Starlette roda o ASGI num portal, com o
    event loop em thread SEPARADA da do teste, então a comparação dava
    "não é a main" com e sem `to_thread` — o controle negativo ficou verde e
    denunciou o teste (§3, 1ª pergunta: "quanto isso daria se a correção não
    fizesse nada?"). `get_running_loop` não depende da topologia de threads do
    cliente de teste, que é o que torna a medição válida sob `TestClient` e sob
    `asyncio.run`.
    """
    import asyncio
    import functools

    @functools.wraps(fn)
    def _espiao(*a, **kw):
        try:
            asyncio.get_running_loop()
            onde.append(True)      # há loop NESTA thread → a chamada o bloqueia
        except RuntimeError:
            onde.append(False)     # thread sem loop → executor
        return fn(*a, **kw)
    return _espiao


class FakeStripeSubs:
    """Só o `Subscription.list` que o `_find_active_subscription` usa.

    `status_viva` importa: aquele helper percorre a escada
    active > trialing > past_due e devolve a PRIMEIRA que casar, então uma
    assinatura `past_due` só aparece se a busca por `past_due` a devolver.
    """

    def __init__(self, ativa=None, erro=False, status_viva="active"):
        self._ativa, self._erro, self._status = ativa, erro, status_viva
        outer = self

        class _S:
            @staticmethod
            def list(customer=None, status=None, limit=None):
                if outer._erro:
                    raise RuntimeError("stripe fora do ar")
                if status == outer._status and outer._ativa:
                    return {"data": [outer._ativa]}
                return {"data": []}

        self.Subscription = _S
