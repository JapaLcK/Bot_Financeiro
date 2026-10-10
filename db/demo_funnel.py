"""db/demo_funnel.py — estado do "Testar o Piggy" (demo no WhatsApp).

Tudo vive em `demo_sessions`, tabela ANÔNIMA: sem user_id, e o demo nunca toca
users / user_identities / launches / ai_messages / auth_accounts (a única
leitura delas é `numero_tem_conta`, para NÃO deixar quem já tem conta cair no
demo). Toda query daqui filtra por `code` ou por `wa_hash`; o `wa_hash` vem do
wa_id do webhook assinado, nunca de texto digitado.

Fonte de verdade do limite (8 mensagens), do teto diário e do intervalo de 30
dias é o BANCO — reiniciar o processo não zera nada. Só o histórico da conversa
fica em memória (core/services/demo/conversa.py).

Molde: db/checkout_funnel.py — falha de telemetria/gravação nunca propaga e
vira `None`. Exceção: `esquecer_numeros_antigos`, que propaga como as outras
podas (o laço de table_cleanup registra o erro).
"""
from __future__ import annotations

import logging
import os
import re
import secrets

from core.crypto import hash_pii, hash_pii_optional
from core.services.demo.dados import ALFABETO, CODIGO_PADRAO, LIMITE_MSGS
from utils_phone import normalize_phone_e164, phone_lookup_candidates

from .connection import get_conn

_log = logging.getLogger(__name__)

CODIGO_RE = re.compile(CODIGO_PADRAO)
JANELA_DIAS = 30  # 1 teste por número a cada 30 dias = retenção do wa_hash

# Dentro das últimas 24h abertas; o teto (DEMO_DAILY_MAX) é argumento.
_ABERTAS_24H = "(select count(*) from demo_sessions where opened_at > now() - interval '24 hours')"


def teto_diario() -> int:
    """DEMO_DAILY_MAX (padrão 0 = DESLIGADO), lida a CADA chamada: interruptor sem deploy.
    <= 0 desliga o demo; valor ilegível também (fail-closed). O dono liga na Railway."""
    try:
        return int(os.getenv("DEMO_DAILY_MAX", "0"))
    except ValueError:
        return 0


def novo_codigo() -> str:
    return "".join(secrets.choice(ALFABETO) for _ in range(6))


def wa_hash(wa_id: str) -> str | None:
    """HMAC do telefone normalizado. Número inválido -> None (fora do demo).
    Mesmo hash de auth_accounts.phone_hash do mesmo número (hash_pii não leva
    `kind` em conta) — por isso o wa_hash é apagado após JANELA_DIAS."""
    try:
        return hash_pii(normalize_phone_e164(wa_id), kind="phone")
    except ValueError:
        return None


def criar_clique(utm_source: str | None, utm_campaign: str | None) -> str | None:
    """Linha do clique em /teste. None = não gravou (o botão segue sem código)."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                for _ in range(5):  # colisão em 30^6 é rara; 5 tentativas bastam
                    cur.execute(
                        "insert into demo_sessions (code, utm_source, utm_campaign, clicked_at) "
                        "values (%s, %s, %s, now()) on conflict (code) do nothing returning code",
                        (novo_codigo(), utm_source, utm_campaign),
                    )
                    row = cur.fetchone()
                    if row:
                        conn.commit()
                        return row["code"]
        return None
    except Exception as exc:
        _log.warning("demo_funnel criar_clique falhou: %s", exc)
        return None


def sessao_recente(h: str) -> dict | None:
    """Sessão aberta por este número nos últimos JANELA_DIAS: {code, msgs_used}."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select code, msgs_used from demo_sessions "
                    "where wa_hash = %s and opened_at > now() - make_interval(days => %s) "
                    "order by opened_at desc limit 1",
                    (h, JANELA_DIAS),
                )
                row = cur.fetchone()
        return dict(row) if row else None
    except Exception as exc:
        _log.warning("demo_funnel sessao_recente falhou: %s", exc)
        return None


def abrir_sessao(code: str | None, h: str, teto: int) -> str | None:
    """Liga `code` (clicado e ainda livre) ao número; sem código utilizável vira
    sessão orgânica com código novo. None = teto diário cheio OU erro de banco
    (os dois aparecem como "lotou" — limite declarado).
    ponytail: o teto é lido e escrito no mesmo statement mas sem lock: pode
    passar do teto em corrida entre instâncias; 1 worker serial hoje não
    excede. Advisory lock se escalar."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if code:
                    cur.execute(
                        "update demo_sessions set wa_hash = %(h)s, opened_at = now() "
                        "where code = %(c)s and wa_hash is null and opened_at is null "
                        f"and {_ABERTAS_24H} < %(teto)s returning code",
                        {"h": h, "c": code, "teto": teto},
                    )
                    row = cur.fetchone()
                    if row:
                        conn.commit()
                        return row["code"]
                cur.execute(
                    "insert into demo_sessions (code, wa_hash, opened_at) "
                    f"select %(c)s::text, %(h)s::text, now() where {_ABERTAS_24H} < %(teto)s "
                    "on conflict (code) do nothing returning code",
                    {"h": h, "c": novo_codigo(), "teto": teto},
                )
                row = cur.fetchone()
            conn.commit()
        return row["code"] if row else None
    except Exception as exc:
        _log.warning("demo_funnel abrir_sessao falhou: %s", exc)
        return None


def codigo_livre(code: str) -> bool:
    """Código de um clique que ninguém abriu ainda (o único que o "lotou" pode
    linkar em /t/{code}). Erro -> False: o "lotou" cai nos preços."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select 1 from demo_sessions where code = %s and clicked_at is not null "
                    "and wa_hash is null and opened_at is null",
                    (code,),
                )
                return cur.fetchone() is not None
    except Exception as exc:
        _log.warning("demo_funnel codigo_livre falhou: %s", exc)
        return False


def reservar_mensagem(code: str) -> int | None:
    """Conta a mensagem ANTES de chamar o modelo, atômico: de N threads sobre
    msgs_used=7, exatamente uma recebe 8. None = no limite (ou erro)."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "update demo_sessions set msgs_used = msgs_used + 1 "
                    "where code = %s and msgs_used < %s returning msgs_used",
                    (code, LIMITE_MSGS),
                )
                row = cur.fetchone()
            conn.commit()
        return int(row["msgs_used"]) if row else None
    except Exception as exc:
        _log.warning("demo_funnel reservar_mensagem falhou: %s", exc)
        return None


def devolver_mensagem(code: str) -> None:
    """Desfaz a reserva quando o modelo falhou (a mensagem não foi respondida)."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "update demo_sessions set msgs_used = msgs_used - 1 "
                    "where code = %s and msgs_used > 0",
                    (code,),
                )
            conn.commit()
    except Exception as exc:
        _log.warning("demo_funnel devolver_mensagem falhou: %s", exc)


def marcar_resposta(code: str, n: int) -> None:
    """Carimba a 1ª resposta e, ao bater o limite, o limit_at (só a 1ª vez)."""
    limite = ", limit_at = coalesce(limit_at, now())" if n >= LIMITE_MSGS else ""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "update demo_sessions set first_answer_at = coalesce(first_answer_at, now())"
                    f"{limite} where code = %s",
                    (code,),
                )
            conn.commit()
    except Exception as exc:
        _log.warning("demo_funnel marcar_resposta falhou: %s", exc)


def marcar_checkout(code: str) -> None:
    """Clique no link final (/t/{code}); só a 1ª vez grava."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "update demo_sessions set checkout_at = now() "
                    "where code = %s and checkout_at is null",
                    (code,),
                )
            conn.commit()
    except Exception as exc:
        _log.warning("demo_funnel marcar_checkout falhou: %s", exc)


def numero_tem_conta(wa_id: str) -> bool:
    """True se o número já pertence a uma conta (qualquer caminho). Erro -> True
    (fail-closed: na dúvida vai pro fluxo normal, nunca pro demo).

    Espelha attempt_whatsapp_phone_link_impl (db_support.py): telefone que casa
    com auth_accounts, OU identidade de WhatsApp cujo usuário tem conta web /
    outro canal (cobre `vincular CODIGO`, que liga sem olhar o telefone). Só-
    WhatsApp (usuário criado por mensagem anterior, sem conta) dá False e PODE
    usar o demo. O teste de paridade (tests/test_demo_whatsapp.py) compara os
    dois (§0.7)."""
    try:
        fones = [hash_pii_optional(c, kind="phone") for c in phone_lookup_candidates(wa_id)]
        ext = hash_pii_optional(wa_id, kind="external_id")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select exists (select 1 from auth_accounts where phone_hash = any(%(fones)s)) "
                    "or exists (select 1 from user_identities ui "
                    "           where ui.provider = 'whatsapp' and ui.external_id_hash = %(ext)s "
                    "             and (exists (select 1 from auth_accounts a where a.user_id = ui.user_id) "
                    "                  or exists (select 1 from user_identities o "
                    "                             where o.user_id = ui.user_id and o.provider <> 'whatsapp'))) "
                    "as tem",
                    {"fones": fones, "ext": ext},
                )
                return bool(cur.fetchone()["tem"])
    except Exception as exc:
        _log.warning("demo_funnel numero_tem_conta falhou (fail-closed): %s", exc)
        return True


def esquecer_numeros_antigos() -> int:
    """Poda: zera o wa_hash das sessões abertas há mais de JANELA_DIAS. Propaga
    erro (como as outras podas de table_cleanup)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update demo_sessions set wa_hash = null "
                "where wa_hash is not null and opened_at < now() - make_interval(days => %s)",
                (JANELA_DIAS,),
            )
            n = cur.rowcount
        conn.commit()
    return n
