"""
core/services/log_retention.py — Retenção de `system_event_logs` (#318, #799).

Dois passos do job de poda (`core/services/table_cleanup.py::_cleanups`):
  - `purge_system_event_logs` apaga linha com mais de SYSTEM_EVENT_LOGS_RETENTION_DAYS
    dias, exceto `EVENTOS_PERMANENTES`;
  - `limpar_texto_das_falhas_de_remocao` tira o texto cru (exceção, uid) dos de
    `EVENTOS_FALHA_DELETE`, que não caem nunca.

Config (env, opcional):
  - SYSTEM_EVENT_LOGS_RETENTION_DAYS (default 90). `<= 0` desliga só a poda; 1 a 89
    sobe para 90 (piso: a maior janela de dedup não permanente é 90, ver a guarda);
    valor não inteiro cai no 90 (com aviso em stderr, como o piso).

Síncronas, devolvem `int` e PROPAGAM erro, como as outras podas:
`run_table_cleanup` registra a falha e o painel recebe `cleanup_job` nível error.
"""
from __future__ import annotations

import os
import sys

from psycopg import sql

from db.connection import get_conn
from db.open_finance_state import EVENTOS_FALHA_DELETE

LOTE = 5000
TETO_LOTES = 200
LOCK_TIMEOUT = "2s"
RETENCAO_MINIMA_DIAS = 90  # default do env e piso (DP1, decisão B)

# Eventos que a poda NUNCA apaga: alguém lê a linha sem janela, ou com janela
# maior que a retenção. Saem com a conta quando têm `user_id` na COLUNA (cascata),
# ou à mão; os de `EVENTOS_FALHA_DELETE` com coluna nula ficam sem prazo.
# Dedup novo com janela > RETENCAO_MINIMA_DIAS entra aqui e no §6 da política
# (`frontend/privacy.html`), senão `tests/test_eventos_permanentes_guarda.py` ou
# `tests/test_politica_retencao.py` reprova.
EVENTOS_PERMANENTES: frozenset[str] = frozenset({
    # `send_founder_email_once` (email_service.py): recent_event_exists, janela 3650
    "founder_email_sent",
    # `add_from_entities` (launches.py): recent_event_exists, janela 36500
    "onboarding_first_launch_nudge",
    # Pix com dinheiro de cliente a devolver ou cobrar (pix_valor.py); o runbook
    # do `docs/CLAUDE.md` consulta sem janela (decisão A)
    "pix_pagamento_parcial_retido",
    "pix_excesso_recebido",
    "pix_retido_completado",
}) | frozenset(EVENTOS_FALHA_DELETE)  # item órfão na Pluggy, lido sem janela (DP3)


def apagar_em_lotes(tabela: str, onde: str, params: tuple, *,
                    lote: int = LOTE, teto_lotes: int = TETO_LOTES) -> int:
    """DELETE em lotes de `lote` linhas, no máximo `teto_lotes` por chamada; uma
    transação (e uma conexão do pool) por lote, com `lock_timeout` local a ela.

    `onde` é fragmento SQL ESCRITO NO CÓDIGO, nunca entrada; os valores vão em
    `params`. A tabela precisa de `id` e `created_at` (a ordem casa com o índice
    de `created_at`). `skip locked`: réplicas podando juntas não brigam pela linha.
    Os lotes já commitados ficam se um lote falhar; o erro propaga, e quantos já
    tinham caído sai em stderr (o `errors` do `run_table_cleanup` só leva o erro)."""
    t = sql.Identifier(tabela)
    delete = sql.SQL(
        "delete from {t} where id in (select id from {t} where {onde}"
        " order by created_at limit %s for update skip locked)"
    ).format(t=t, onde=sql.SQL(onde))
    total = 0
    try:
        for _ in range(teto_lotes):
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # `true` = só nesta transação; não vaza para a conexão devolvida ao pool.
                    cur.execute("select set_config('lock_timeout', %s, true)", (LOCK_TIMEOUT,))
                    cur.execute(delete, (*params, lote))
                    n = cur.rowcount
                conn.commit()
            total += n
            if n < lote:
                break
    except Exception:
        print(f"[log_retention] {tabela}: {total} linha(s) apagada(s) antes da falha",
              file=sys.stderr, flush=True)
        raise
    return total


def _dias() -> int:
    bruto = os.getenv("SYSTEM_EVENT_LOGS_RETENTION_DAYS", str(RETENCAO_MINIMA_DIAS))
    try:
        dias = int(bruto)
    except ValueError:
        dias = None
    if dias is None or 0 < dias < RETENCAO_MINIMA_DIAS:
        print(f"[log_retention] SYSTEM_EVENT_LOGS_RETENTION_DAYS={bruto!r} inválido ou abaixo"
              f" do piso; usando {RETENCAO_MINIMA_DIAS}", file=sys.stderr, flush=True)
        return RETENCAO_MINIMA_DIAS
    return dias


def _tabela_existe() -> bool:
    """A tabela nasce no `ensure_admin_tables` do startup, não no `init_db`: num
    banco sem lifespan (testes) ela não existe, e a poda não pode virar erro."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select to_regclass('public.system_event_logs') as t")
        return bool((cur.fetchone() or {}).get("t"))


def purge_system_event_logs() -> int:
    dias = _dias()
    if dias <= 0 or not _tabela_existe():
        return 0
    return apagar_em_lotes(
        "system_event_logs",
        "created_at < now() - make_interval(days => %s) and event_type <> all(%s)",
        (dias, list(EVENTOS_PERMANENTES)),
    )


# Depois da limpeza, cada evento de `EVENTOS_FALHA_DELETE` fica com este texto fixo
# e só com estas chaves de `details`: `item_id`/`items` são o que o diagnóstico lê
# (`itens_com_remocao_remota_falha`); `motivo`/`sqlstate`/`status_code` são o
# motivo técnico estruturado (nome da exceção, código), sem texto livre. Sai o
# resto: `error` (texto cru da exceção, que já trouxe corpo de resposta da Pluggy)
# e `user_id` de formatos antigos. Os escritores ainda gravam o texto cru (tirar na
# origem é a fatia C): ele vive até a próxima volta do job.
TEXTO_FALHA_DELETE = {
    "pluggy_item_delete_failed": "Falha ao deletar item(s) na Pluggy.",
    "pluggy_disconnect_auth_failed": "Sem apiKey para deletar item(s) na Pluggy.",
    "account_reset_pluggy_cleanup_failed": "Reset: limpeza remota na Pluggy falhou.",
    "account_deletion_pluggy_cleanup_failed": "Exclusão de conta: limpeza remota na Pluggy falhou.",
}
CHAVES_FALHA_DELETE = ("item_id", "items", "motivo", "sqlstate", "status_code")


def limpar_texto_das_falhas_de_remocao() -> int:
    """Reescreve `message` e `details` das linhas de `EVENTOS_FALHA_DELETE` que
    ainda têm algo a limpar. Idempotente: a 2ª volta não acha nada.

    ponytail: o filtro por `event_type` não tem índice próprio (o `_level_type`
    começa por `level`): um seq scan por dia, na tabela já podada a 90 dias."""
    if not _tabela_existe():
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select set_config('lock_timeout', %s, true)", (LOCK_TIMEOUT,))
            cur.execute(
                """
                with alvo as (
                    select l.id, m.texto,
                           coalesce((select jsonb_object_agg(e.key, e.value)
                                       from jsonb_each(case when jsonb_typeof(l.details) = 'object'
                                                            then l.details end) e
                                      where e.key = any(%(chaves)s)), '{}'::jsonb) as limpo
                      from system_event_logs l
                      join unnest(%(ev)s::text[], %(txt)s::text[]) as m(ev, texto)
                        on m.ev = l.event_type
                )
                update system_event_logs l set message = a.texto, details = a.limpo
                  from alvo a
                 where l.id = a.id
                   and (l.message is distinct from a.texto or l.details is distinct from a.limpo)
                """,
                {"chaves": list(CHAVES_FALHA_DELETE), "ev": list(EVENTOS_FALHA_DELETE),
                 "txt": [TEXTO_FALHA_DELETE[e] for e in EVENTOS_FALHA_DELETE]},
            )
            n = cur.rowcount
        conn.commit()
    return n
