import re
import unicodedata
from decimal import Decimal
from datetime import datetime, time, timedelta
from uuid import NAMESPACE_OID, uuid5

from psycopg.types.json import Jsonb

from utils_date import _tz, add_months, billing_period_for_close_day

from .accounts import delete_launch_and_rollback
from .cards import (
    add_imported_credit_purchase,
    extract_installment_info,
    get_or_create_open_finance_card,
    remove_single_credit_transaction,
)
from .connection import get_conn
from .users import ensure_user


def _rollback_imported_of(rows: list[dict]) -> None:
    """Reverte os artefatos importados de um conjunto de transações OF (launches + fatura).

    Cada `row` traz: user_id, imported_launch_id, imported_credit_tx_id, launch_source.
    - Cartão: `undo_credit_transaction` (ajusta o total da fatura).
    - Launch: só apaga se for do OF (`source='open_finance'`); se foi auto-merge num lançamento
      MANUAL, preserva o manual (só desvincula — a OF tx some depois de qualquer jeito).
    Cada função gerencia própria conexão; falhas são engolidas pra não travar a limpeza.
    """
    for r in rows:
        uid = r.get("user_id")
        ctx = r.get("imported_credit_tx_id")
        if ctx:
            try:
                # remoção ÚNICA: apagar 1 parcela não pode cascatear o parcelamento inteiro.
                remove_single_credit_transaction(uid, ctx)
            except Exception:
                pass
        lid = r.get("imported_launch_id")
        if lid and (r.get("launch_source") == "open_finance"):
            try:
                delete_launch_and_rollback(uid, lid)
            except Exception:
                pass


MOCK_OPEN_FINANCE_INSTITUTIONS = {
    "nubank": {
        "id": "mock-nubank",
        "name": "Nubank",
        "connector_id": "612",
    },
    "itau": {
        "id": "mock-itau",
        "name": "Itaú",
        "connector_id": "601",
    },
    "bradesco": {
        "id": "mock-bradesco",
        "name": "Bradesco",
        "connector_id": "603",
    },
}


def _mock_open_finance_institution(key: str | None = None) -> dict:
    normalized = (key or "nubank").strip().lower()
    normalized = normalized.replace("ú", "u").replace("ã", "a")
    normalized = normalized.replace(" ", "")
    return MOCK_OPEN_FINANCE_INSTITUTIONS.get(normalized, MOCK_OPEN_FINANCE_INSTITUTIONS["nubank"])


def create_mock_open_finance_connection(user_id: int, institution_key: str | None = None) -> dict:
    """
    Simula o fluxo Pluggy/Open Finance para desenvolvimento.
    Nao altera o saldo manual do PigBank; salva dados importados em tabelas separadas.
    """
    ensure_user(user_id)
    institution = _mock_open_finance_institution(institution_key)
    provider_item_id = f"mock-pluggy-{user_id}-{institution['id']}"
    now = datetime.now(_tz())
    today = now.date()

    raw_connection = {
        "provider": "mock_pluggy",
        "connectorId": institution["connector_id"],
        "environment": "sandbox",
        "products": ["ACCOUNTS", "TRANSACTIONS", "CREDIT_CARDS"],
    }

    account_specs = [
        {
            "provider_account_id": f"{provider_item_id}-checking",
            "name": f"{institution['name']} Conta",
            "type": "CHECKING_ACCOUNT",
            "subtype": "CONTA_CORRENTE",
            "balance": Decimal("4320.75"),
            "transactions": [
                ("tx-salary", "Salário", Decimal("6500.00"), today - timedelta(days=6), "receita"),
                ("tx-market", "Mercado", Decimal("-184.32"), today - timedelta(days=4), "alimentação"),
                ("tx-pix", "Pix recebido", Decimal("250.00"), today - timedelta(days=3), "transferência"),
                ("tx-uber", "Uber", Decimal("-38.90"), today - timedelta(days=1), "transporte"),
            ],
        },
        {
            "provider_account_id": f"{provider_item_id}-card",
            "name": f"{institution['name']} Cartão",
            "type": "CREDIT_CARD",
            "subtype": "CARTAO_CREDITO",
            "balance": Decimal("-845.90"),
            "transactions": [
                ("tx-card-ifood", "iFood", Decimal("-72.40"), today - timedelta(days=5), "alimentação"),
                ("tx-card-streaming", "Streaming", Decimal("-39.90"), today - timedelta(days=2), "assinaturas"),
                ("tx-card-pharmacy", "Farmácia", Decimal("-56.12"), today - timedelta(days=1), "saúde"),
            ],
        },
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into open_finance_connections (
                    user_id, provider, provider_item_id, status, institution_id,
                    institution_name, consent_url, consent_expires_at, last_sync_at, raw, updated_at
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (user_id, provider, provider_item_id)
                do update set status = excluded.status,
                              institution_name = excluded.institution_name,
                              consent_url = excluded.consent_url,
                              consent_expires_at = excluded.consent_expires_at,
                              last_sync_at = excluded.last_sync_at,
                              raw = excluded.raw,
                              updated_at = excluded.updated_at
                returning id, provider_item_id, status, institution_name, consent_url, last_sync_at
                """,
                (
                    user_id,
                    "mock_pluggy",
                    provider_item_id,
                    "ACTIVE",
                    institution["id"],
                    institution["name"],
                    f"https://mock.pluggy.local/connect/{provider_item_id}",
                    now + timedelta(minutes=30),
                    now,
                    Jsonb(raw_connection),
                    now,
                ),
            )
            connection = cur.fetchone()
            connection_id = connection["id"]

            account_count = 0
            transaction_count = 0

            for account in account_specs:
                account_raw = {
                    "connectorId": institution["connector_id"],
                    "institution": institution["name"],
                    "mock": True,
                }
                cur.execute(
                    """
                    insert into open_finance_accounts (
                        connection_id, provider_account_id, name, type,
                        subtype, currency, balance, raw, updated_at
                    )
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    on conflict (connection_id, provider_account_id)
                    do update set name = excluded.name,
                                  type = excluded.type,
                                  subtype = excluded.subtype,
                                  currency = excluded.currency,
                                  balance = excluded.balance,
                                  raw = excluded.raw,
                                  updated_at = excluded.updated_at
                    returning id
                    """,
                    (
                        connection_id,
                        account["provider_account_id"],
                        account["name"],
                        account["type"],
                        account["subtype"],
                        "BRL",
                        account["balance"],
                        Jsonb(account_raw),
                        now,
                    ),
                )
                account_id = cur.fetchone()["id"]
                account_count += 1

                for tx_id, description, amount, transaction_date, category in account["transactions"]:
                    provider_transaction_id = f"{account['provider_account_id']}-{tx_id}"
                    tx_raw = {
                        "mock": True,
                        "providerItemId": provider_item_id,
                        "accountId": account["provider_account_id"],
                    }
                    cur.execute(
                        """
                        insert into open_finance_transactions (
                            account_id, provider_transaction_id, description,
                            amount, transaction_date, category, raw
                        )
                        values (%s,%s,%s,%s,%s,%s,%s)
                        on conflict (account_id, provider_transaction_id)
                        do update set description = excluded.description,
                                      amount = excluded.amount,
                                      transaction_date = excluded.transaction_date,
                                      category = excluded.category,
                                      raw = excluded.raw
                        """,
                        (
                            account_id,
                            provider_transaction_id,
                            description,
                            amount,
                            transaction_date,
                            category,
                            Jsonb(tx_raw),
                        ),
                    )
                    transaction_count += 1

        conn.commit()

    return {
        "connection": connection,
        "accounts_synced": account_count,
        "transactions_synced": transaction_count,
    }


def get_open_finance_snapshot(user_id: int, limit: int = 8) -> dict:
    ensure_user(user_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, provider, provider_item_id, status, institution_name, last_sync_at,
                       last_attempt_at, status_reason, health, reconnected_at
                from open_finance_connections
                where user_id=%s
                order by updated_at desc, id desc
                """,
                (user_id,),
            )
            connections = [dict(r) for r in (cur.fetchall() or [])]
            # `ui` é o estado exibível — decidido por `connection_ui_state`, a única
            # função que o decide. O front deixou de derivar rótulo do `status`:
            # ele não sabe de produto atrasado nem de item que sumiu.
            from core.services.pluggy_health import connection_ui_state
            for c in connections:
                c["ui"] = connection_ui_state(c)

            cur.execute(
                """
                select c.id as connection_id, c.institution_name, a.id, a.name, a.type,
                       a.subtype, a.currency, a.balance, a.updated_at
                from open_finance_accounts a
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id=%s
                order by c.updated_at desc, a.type, a.name
                """,
                (user_id,),
            )
            accounts = cur.fetchall()

            cur.execute(
                """
                select c.institution_name, a.name as account_name, t.id, t.description,
                       t.amount, t.transaction_date, t.category
                from open_finance_transactions t
                join open_finance_accounts a on a.id = t.account_id
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id=%s
                order by t.transaction_date desc, t.id desc
                limit %s
                """,
                (user_id, limit),
            )
            transactions = cur.fetchall()

            cur.execute(
                """
                select c.institution_name, i.id, i.name, i.type, i.subtype, i.balance
                from open_finance_investments i
                join open_finance_connections c on c.id = i.connection_id
                where c.user_id=%s
                order by i.balance desc nulls last, i.id
                """,
                (user_id,),
            )
            investments = cur.fetchall()

    return {
        "connections": connections,
        "accounts": accounts,
        "transactions": transactions,
        "investments": investments,
    }


def count_open_finance_connections(user_id: int, provider: str = "pluggy") -> int:
    """Quantos bancos o usuário tem conectados (default: só Pluggy reais), pro
    gate por nº de bancos. Conexão PAUSED (trial vencido) NÃO conta — senão o
    ex-trialer que assina fica travado de reconectar o próprio banco."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            if provider:
                cur.execute(
                    "select count(*) as n from open_finance_connections"
                    " where user_id=%s and provider=%s and upper(coalesce(status,'')) <> 'PAUSED'",
                    (user_id, provider),
                )
            else:
                cur.execute(
                    "select count(*) as n from open_finance_connections where user_id=%s",
                    (user_id,),
                )
            return cur.fetchone()["n"]


def list_open_finance_user_ids() -> list[int]:
    """user_ids distintos com pelo menos 1 banco Pluggy conectado (pros ticks proativos)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select distinct user_id from open_finance_connections where provider='pluggy'")
            return [r["user_id"] for r in cur.fetchall()]


def list_pluggy_item_ids(user_id: int | None = None) -> list[str]:
    """Item ids Pluggy ativos (todos, ou de um usuário). Usado no refresh periódico.

    Conexões PAUSED ficam de fora: o item já foi deletado na Pluggy (trial venceu),
    então não há o que refrescar/deletar de novo.
    """
    sql = (
        "select provider_item_id from open_finance_connections "
        "where provider='pluggy' and upper(coalesce(status,'')) <> 'PAUSED'"
    )
    with get_conn() as conn:
        with conn.cursor() as cur:
            if user_id is None:
                cur.execute(sql)
            else:
                cur.execute(sql + " and user_id=%s", (user_id,))
            return [r["provider_item_id"] for r in cur.fetchall() if r["provider_item_id"]]


def list_pluggy_connections_for_trial_sweep() -> list[dict]:
    """Conexões Pluggy candidatas à varredura de trial vencido (todas as não-PAUSED).

    A resolução de tier (trial ativo? assinatura?) fica no serviço — aqui só se lista
    quem ainda ocupa slot pago na Pluggy."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, user_id, provider_item_id, status
                from open_finance_connections
                where provider='pluggy' and upper(coalesce(status,'')) <> 'PAUSED'
                order by user_id, id
                """
            )
            return cur.fetchall()


def pause_open_finance_connection(connection_id: int) -> int:
    """Marca a conexão como PAUSED (trial venceu sem virar assinatura).

    Decisão de produto: NÃO apaga nada do que foi importado — contas, transações,
    launches e faturas ficam visíveis; só o sync para ("reative seu banco" vira CTA
    de upgrade). O item na Pluggy é deletado pelo serviço ANTES de chamar isto
    (libera o slot pago do contrato)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update open_finance_connections set status='PAUSED', updated_at=now() where id=%s",
                (connection_id,),
            )
            updated = cur.rowcount
        conn.commit()
    return updated


def list_connections_needing_reconnect(user_id: int | None = None, within_days: int = 7) -> list[dict]:
    """Conexões em erro OU com consentimento vencendo em `within_days` dias.

    Base pra um aviso proativo de 'reconectar/renovar' (P1 #5/#6). Sem isso, o dado
    para de atualizar em silêncio — pior que não ter dado.
    """
    sql = """
        select id, user_id, provider_item_id, institution_name, status,
               consent_expires_at, last_sync_at
        from open_finance_connections
        where provider = 'pluggy'
          and (
            upper(coalesce(status, '')) in ('ERROR', 'LOGIN_ERROR', 'OUTDATED', 'WAITING_USER_INPUT')
            or (consent_expires_at is not null
                and consent_expires_at <= now() + make_interval(days => %s))
          )
    """
    params: list = [within_days]
    if user_id is not None:
        sql += " and user_id = %s"
        params.append(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def save_pluggy_open_finance_item(user_id: int, item: dict) -> dict:
    ensure_user(user_id)

    if not isinstance(item, dict):
        raise ValueError("Item Pluggy inválido.")

    item_id = item.get("id") or item.get("itemId")
    if not item_id:
        raise ValueError("Item Pluggy sem id.")
    item_id = str(item_id)

    connector = item.get("connector") or {}
    institution_id = (
        connector.get("id")
        or item.get("connectorId")
        or item.get("institutionId")
        or "pluggy"
    )
    institution_name = (
        connector.get("name")
        or connector.get("institutionName")
        or item.get("connectorName")
        or item.get("institutionName")
        or item.get("name")
        or "Banco conectado"
    )
    status = item.get("status") or item.get("executionStatus") or "UPDATING"
    now = datetime.now(_tz())

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into open_finance_connections (
                    user_id, provider, provider_item_id, status, institution_id,
                    institution_name, consent_url, consent_expires_at, raw, updated_at
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (user_id, provider, provider_item_id)
                do update set status = excluded.status,
                              institution_id = excluded.institution_id,
                              institution_name = excluded.institution_name,
                              raw = excluded.raw,
                              updated_at = excluded.updated_at,
                              -- Linha G da tabela de estados
                              -- (core/services/pluggy_health.py): reconectar
                              -- ZERA motivo e saúde. Sem isto a tela dizia
                              -- "Conexão perdida / Refaça a conexão" logo depois
                              -- de o usuário ter refeito — e um health velho com
                              -- item_status MISSING ainda pintava a tela.
                              status_reason = null,
                              health = null,
                              -- Só no ramo do CONFLITO: conexão nova nasce com
                              -- isto NULL e já é barrada pelo `last_sync_at`
                              -- NULL. O único chamador de produção é a rota
                              -- /pluggy/item (o widget), então isto carimba uma
                              -- vez por reconexão — webhook e sync não passam aqui.
                              reconnected_at = excluded.updated_at
                -- NÃO carimba last_sync_at: conectar não é sincronizar. Carimbar
                -- aqui fazia a conexão nascer "Atualizado agora" antes de qualquer
                -- sync e ligava `user_synced_within`, que segura o e-mail dos
                -- agentes achando que uma carteira estava se mexendo. Quem carimba
                -- sucesso é `mark_sync_result`, no fim de um sync real.
                -- `status` continua sendo escrito: é ele que tira a conexão de
                -- DELETED quando o usuário reconecta pelo widget. E o
                -- `reconnected_at` acima é o que impede o espelho ANTERIOR à
                -- reconexão de voltar à tela como "Atualizado".
                returning id, provider, provider_item_id, status, institution_name,
                          last_sync_at, reconnected_at
                """,
                (
                    user_id,
                    "pluggy",
                    item_id,
                    str(status).upper(),
                    str(institution_id),
                    str(institution_name),
                    None,
                    None,
                    Jsonb(item),
                    now,
                ),
            )
            connection = cur.fetchone()
        conn.commit()

    return connection


def update_pluggy_open_finance_item_status(provider_item_id: str, status: str, raw: dict | None = None) -> int:
    item_id = (provider_item_id or "").strip()
    if not item_id:
        return 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update open_finance_connections
                set status=%s,
                    -- O par (status, status_reason) é UM estado só. Este caminho
                    -- (webhook) não passa pelo `resolve_connection_state` porque
                    -- ele não observa o item — só repete o que a Pluggy disse —,
                    -- mas grava o MESMO par que o resolvedor daria: linhas B/C da
                    -- tabela (item em erro) são ERROR + motivo VAZIO, porque quem
                    -- conta a história ali é o status/health, não o motivo de
                    -- ontem. Sem isto, `item/error` sobre ACTIVE/no_accounts
                    -- produzia ERROR/no_accounts — par incoerente (medido).
                    -- EXCEÇÃO, `item_missing`: WEBHOOK NÃO É EVIDÊNCIA DE ITEM VIVO.
                    -- Apagá-lo REBAIXA a mensagem — um `item/error` entregue com
                    -- atraso (replay) sobre o par que o job de saúde acabou de
                    -- gravar virava ERROR/None → "Erro temporário / Tentaremos de
                    -- novo automaticamente" (medido), e o usuário parava de ser
                    -- mandado refazer a conexão. Aceitar isso seria aceitar até
                    -- ~12h (OF_HEALTH_MAX_AGE_SEC) mostrando estado saudável numa
                    -- conexão que exige ação — a mentira que esta onda existe para
                    -- matar. Quem PODE limpar `item_missing` é só quem OBSERVA o
                    -- item: o job de saúde (GET /items) e o sync bem-sucedido, os
                    -- dois pelo `resolve_connection_state`. E o par continua
                    -- coerente: ERROR/item_missing é exatamente a linha A da tabela,
                    -- o mesmo par que o job de saúde grava.
                    status_reason=case
                        when lower(coalesce(status_reason,'')) = 'item_missing' then status_reason
                        else null end,
                    raw=coalesce(%s, raw),
                    updated_at=now()
                where provider='pluggy' and provider_item_id=%s
                  -- PAUSED e DELETED são estados locais TERMINAIS: PAUSED = trial
                  -- vencido (o item nem existe mais na Pluggy), DELETED = conexão
                  -- removida. Webhook atrasado (item/updated depois do item/deleted)
                  -- não pode ressuscitar nenhum dos dois.
                  and upper(coalesce(status,'')) not in ('PAUSED', 'DELETED')
                """,
                (status, Jsonb(raw) if raw is not None else None, item_id),
            )
            updated = cur.rowcount
        conn.commit()

    return updated


def save_open_finance_investments(connection_id: int, investments: list[dict]) -> dict:
    """Grava (upsert) os investimentos OF — inclui Caixinha (CDB). Espelho, não vira pocket ainda."""
    now = datetime.now(_tz())
    count = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for inv in investments:
                if not inv.get("provider_investment_id"):
                    continue
                cur.execute(
                    """
                    insert into open_finance_investments (
                        connection_id, provider_investment_id, name, type, subtype,
                        currency, balance, raw, updated_at
                    )
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    on conflict (connection_id, provider_investment_id)
                    do update set name=excluded.name, type=excluded.type, subtype=excluded.subtype,
                                  currency=excluded.currency, balance=excluded.balance,
                                  raw=excluded.raw, updated_at=excluded.updated_at
                    """,
                    (connection_id, inv["provider_investment_id"], inv.get("name"), inv.get("type"),
                     inv.get("subtype"), inv.get("currency") or "BRL", inv.get("balance") or Decimal("0"),
                     Jsonb(inv.get("raw") or {}), now),
                )
                count += 1
        conn.commit()
    return {"investments_synced": count}


# ── Banqueiro (agente cofre): caixinha OF ↔ meta do PigBank ───────────────────

def list_caixinha_candidates(user_id: int) -> list[dict]:
    """Caixinhas/cofrinhos OF do usuário (CDB de renda fixa OU nome de caixinha),
    já com a meta vinculada (se houver). Alimenta a UI de vínculo do Banqueiro."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select i.id as of_investment_id, i.name, i.balance, i.type, i.subtype,
                       p.id as pocket_id, p.name as pocket_name, p.target_amount
                from open_finance_investments i
                join open_finance_connections c on c.id = i.connection_id
                left join pockets p on p.of_investment_id = i.id and p.user_id = %s
                where c.user_id = %s
                  and (
                    (upper(coalesce(i.type,'')) = 'FIXED_INCOME'
                       and upper(coalesce(i.subtype,'')) = 'CDB')
                    or i.name ilike any (array['%%caixinha%%','%%cofrinho%%','%%reserva%%','%%objetivo%%'])
                  )
                  -- Nubank devolve toda posição de CDB via OF, inclusive caixinhas já
                  -- esvaziadas (saldo 0). Elas poluem a tela de vínculo sem servir pra
                  -- nada, então só mostramos candidatos com saldo > 0 — exceto os que já
                  -- estão vinculados a uma meta (mantidos pra permitir desvincular).
                  and (coalesce(i.balance, 0) > 0 or p.id is not null)
                order by i.balance desc nulls last
                """,
                (user_id, user_id),
            )
            return [dict(r) for r in (cur.fetchall() or [])]


def bind_pocket_to_caixinha(user_id: int, pocket_id: int, of_investment_id: int | None) -> bool:
    """Vincula (ou desvincula, of_investment_id=None) uma meta a uma caixinha OF.

    Inicializa of_last_seen_balance com o saldo ATUAL da caixinha, pra o Banqueiro
    contar só os aportes daqui pra frente (não o saldo histórico já acumulado)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if of_investment_id is None:
                cur.execute(
                    "update pockets set of_investment_id=null, of_last_seen_balance=null "
                    "where id=%s and user_id=%s",
                    (pocket_id, user_id),
                )
                conn.commit()
                return cur.rowcount > 0
            # valida que a caixinha é do usuário e pega o saldo atual
            cur.execute(
                """
                select i.balance from open_finance_investments i
                join open_finance_connections c on c.id = i.connection_id
                where i.id=%s and c.user_id=%s
                """,
                (of_investment_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return False
            bal = row["balance"] or 0
            # 1 caixinha OF por meta: solta qualquer vínculo anterior dessa caixinha
            cur.execute(
                "update pockets set of_investment_id=null, of_last_seen_balance=null "
                "where of_investment_id=%s and user_id=%s",
                (of_investment_id, user_id),
            )
            cur.execute(
                "update pockets set of_investment_id=%s, of_last_seen_balance=%s "
                "where id=%s and user_id=%s",
                (of_investment_id, bal, pocket_id, user_id),
            )
            ok = cur.rowcount > 0
            conn.commit()
            return ok


def list_banqueiro_pockets(user_id: int) -> list[dict]:
    """Caixinhas vinculadas a uma caixinha OF, com saldo e RENDIMENTO acumulado atuais
    (do banco) + os baselines já contabilizados pelo Banqueiro. Fonte do detector:
    delta de saldo = aporte + rendimento; `of_profit` separa os dois."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select p.id as pocket_id, p.name, p.emoji, p.target_amount, p.target_date,
                       p.of_last_seen_balance, p.of_last_seen_profit, p.of_investment_id,
                       i.balance as of_balance,
                       nullif(i.raw->>'amountProfit', '')::numeric as of_profit
                from pockets p
                join open_finance_investments i on i.id = p.of_investment_id
                where p.user_id = %s and p.of_investment_id is not null
                """,
                (user_id,),
            )
            return [dict(r) for r in (cur.fetchall() or [])]


def banqueiro_pocket_pace(user_id: int, pocket_id: int, days: int = 90) -> float:
    """Ritmo de aporte da caixinha OF (R$/mês) pelos aportes que o Banqueiro já
    detectou. Caixinha OF não gera lançamento deposito_caixinha, então o histórico
    vem dos eventos (cada evento carrega um array `moves` por caixinha — funciona
    tanto pro evento único quanto pro resumo consolidado de várias caixinhas)."""
    months = max(days / 30.0, 1.0)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select coalesce(sum((m->>'aporte')::numeric), 0) as net
                from agent_events e,
                     jsonb_array_elements(coalesce(e.payload->'moves', '[]'::jsonb)) m
                where e.user_id = %s
                  and e.kind = 'cofre'
                  and (m->>'pocket_id')::bigint = %s
                  and e.fired_at >= now() - make_interval(days => %s)
                """,
                (user_id, pocket_id, days),
            )
            net = float((cur.fetchone() or {}).get("net") or 0)
    return net / months


def update_pocket_of_last_seen(pocket_id: int, balance, profit=None) -> None:
    """Atualiza os baselines já contabilizados pelo Banqueiro (saldo e, opcionalmente,
    rendimento acumulado) depois de processar a caixinha."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if profit is None:
                cur.execute(
                    "update pockets set of_last_seen_balance=%s where id=%s",
                    (balance, pocket_id),
                )
            else:
                cur.execute(
                    "update pockets set of_last_seen_balance=%s, of_last_seen_profit=%s where id=%s",
                    (balance, profit, pocket_id),
                )
        conn.commit()


# Regra de auto-import de caixinha: só investimentos com CARA de caixinha (nome ~
# reserva/objetivo/cofrinho). Um CDB comum é investimento, não meta — não vira pocket
# (evita "caixinha fantasma"). Decisão de produto 2026-08-11.
_CAIXINHA_NAME_PATTERNS = ["%caixinha%", "%cofrinho%", "%reserva%", "%objetivo%", "%cofre%"]


def sync_open_finance_caixinhas(connection_id: int, user_id: int) -> dict:
    """Espelha as caixinhas do Open Finance como caixinhas do Pig. Idempotente.

    1. Auto-cria um pocket pra cada caixinha OF (com cara de caixinha) ainda não
       vinculada — `source='open_finance'`, read-only, juros interno OFF.
    2. Dedup: se já existe um pocket de mesmo nome não vinculado, VINCULA nele em
       vez de duplicar.
    3. Espelha o saldo do banco (`open_finance_investments.balance`) em TODAS as
       caixinhas vinculadas (auto-criadas e vinculadas na mão).

    NÃO mexe em `of_last_seen_balance` (baseline do Banqueiro) — o detector de aporte
    continua funcionando sobre o delta como antes.
    """
    created = linked = mirrored = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1. caixinhas OF desta conexão com cara de caixinha E SALDO > 0.
            # Saldo 0 = fundo/reserva vazia (ex.: Nubank "Reserva Planejada" que o
            # Pluggy devolve zerado) — não vira caixinha fantasma.
            cur.execute(
                """
                select i.id as of_id, i.name, coalesce(i.balance, 0) as balance,
                       nullif(i.raw->>'amountProfit', '')::numeric as profit
                from open_finance_investments i
                where i.connection_id = %s
                  and i.name ilike any (%s)
                  and coalesce(i.balance, 0) > 0
                """,
                (connection_id, _CAIXINHA_NAME_PATTERNS),
            )
            of_caixinhas = [dict(r) for r in (cur.fetchall() or [])]

            for oc in of_caixinhas:
                of_id = oc["of_id"]
                name = (oc["name"] or "Caixinha").strip()
                bal = oc["balance"]
                profit = oc["profit"]  # baseline de rendimento (amountProfit), p/ o Banqueiro

                # já vinculada a algum pocket deste user? nada a fazer
                cur.execute(
                    "select 1 from pockets where user_id=%s and of_investment_id=%s limit 1",
                    (user_id, of_id),
                )
                if cur.fetchone():
                    continue

                # dedup: pocket de mesmo nome, ainda sem vínculo → vincula nele
                cur.execute(
                    "select id from pockets where user_id=%s and lower(name)=lower(%s) "
                    "and of_investment_id is null limit 1",
                    (user_id, name),
                )
                same = cur.fetchone()
                if same:
                    cur.execute(
                        "update pockets set of_investment_id=%s, of_last_seen_balance=%s, "
                        "of_last_seen_profit=%s, balance=%s, interest_enabled=false "
                        "where id=%s and user_id=%s",
                        (of_id, bal, profit, bal, same["id"], user_id),
                    )
                    linked += 1
                    continue

                # cria novo — resolve colisão de nome (unique(user_id,name)) com sufixo
                new_name = name
                suffix = 0
                while True:
                    cur.execute(
                        "select 1 from pockets where user_id=%s and lower(name)=lower(%s)",
                        (user_id, new_name),
                    )
                    if not cur.fetchone():
                        break
                    suffix += 1
                    new_name = f"{name} (banco)" if suffix == 1 else f"{name} (banco {suffix})"
                cur.execute(
                    """
                    insert into pockets(
                        user_id, name, balance, source, of_investment_id,
                        of_last_seen_balance, of_last_seen_profit,
                        interest_enabled, interest_rate, interest_period,
                        interest_tax_profile, last_interest_date
                    )
                    values (%s,%s,%s,'open_finance',%s,%s,%s,false,1,'cdi','regressive_ir_iof',current_date)
                    """,
                    (user_id, new_name, bal, of_id, bal, profit),
                )
                created += 1

            # 3. espelha o saldo do banco em todas as caixinhas vinculadas (auto + manual)
            cur.execute(
                """
                update pockets p
                   set balance = i.balance, interest_enabled = false
                  from open_finance_investments i
                 where p.of_investment_id = i.id
                   and p.user_id = %s
                   and i.balance is not null
                   and p.balance is distinct from i.balance
                """,
                (user_id,),
            )
            mirrored = cur.rowcount

            # 4. Auto-cura: remove caixinhas AUTO-CRIADAS (source='open_finance') cujo
            # investimento do banco está zerado/sumiu — limpa as fantasmas já criadas
            # (ex.: "Reserva Planejada" do Nubank que veio com saldo 0).
            cur.execute(
                """
                delete from pockets p
                 using open_finance_investments i
                 where p.of_investment_id = i.id
                   and p.user_id = %s
                   and p.source = 'open_finance'
                   and coalesce(i.balance, 0) <= 0
                """,
                (user_id,),
            )
            cleaned = cur.rowcount
        conn.commit()
    return {"caixinhas_created": created, "caixinhas_linked": linked,
            "caixinhas_mirrored": mirrored, "caixinhas_cleaned": cleaned}


def get_open_finance_connection_by_item_id(provider_item_id: str, provider: str = "pluggy") -> dict | None:
    """A conexão daquele item da Pluggy (o webhook só traz o item, não o user).

    Levanta `AmbiguousItemError` quando o item aparece em mais de uma conexão. O
    `limit 1` sem `order by` que existia aqui escolhia um dono ao acaso — e a
    tabela permite (user_id, provider, provider_item_id), ou seja, dois usuários
    PODEM ter o mesmo item. Sincronizar a carteira do usuário sorteado é pior que
    falhar alto.
    """
    from .open_finance_state import AmbiguousItemError, get_connections_by_item_id

    rows = get_connections_by_item_id(provider_item_id, provider)
    if len(rows) > 1:
        raise AmbiguousItemError((provider_item_id or "").strip(), rows)
    return rows[0] if rows else None


def delete_open_finance_transactions(
    provider_item_id: str, transaction_ids: list[str], provider: str = "pluggy"
) -> int:
    """Remove transações OF pelo provider_transaction_id (evento transactions/deleted da Pluggy).

    P0 (integridade): antes de apagar o espelho OF, REVERTE o que foi importado —
    o launch (Fase 1) e a transação de fatura (Fase 1a). Sem isso, deletar uma transação
    no banco deixava launch/fatura órfãos, inflando gastos pra sempre.
    """
    item_id = (provider_item_id or "").strip()
    ids = [str(t).strip() for t in (transaction_ids or []) if str(t).strip()]
    if not item_id or not ids:
        return 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select t.id, c.user_id, t.imported_launch_id, t.imported_credit_tx_id,
                       l.source as launch_source
                from open_finance_transactions t
                join open_finance_accounts a on a.id = t.account_id
                join open_finance_connections c on c.id = a.connection_id
                left join launches l on l.id = t.imported_launch_id
                where c.provider = %s and c.provider_item_id = %s
                  and t.provider_transaction_id = any(%s)
                """,
                (provider, item_id, ids),
            )
            rows = cur.fetchall()

    if not rows:
        return 0

    _rollback_imported_of(rows)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from open_finance_transactions where id = any(%s)",
                ([r["id"] for r in rows],),
            )
            deleted = cur.rowcount
        conn.commit()

    return deleted


def save_open_finance_sync(connection_id: int, accounts: list[dict]) -> dict:
    """
    Grava contas + transações reais puxadas da Pluggy nas tabelas OF.
    `accounts` é uma lista de dicts já normalizados, cada um com uma lista `transactions`.
    Idempotente: usa os mesmos ON CONFLICT (connection_id, provider_account_id) e
    (account_id, provider_transaction_id) do fluxo mock. NÃO toca no saldo manual (isso é Fase 1).
    """
    now = datetime.now(_tz())
    account_count = 0
    transaction_count = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            for account in accounts:
                cur.execute(
                    """
                    insert into open_finance_accounts (
                        connection_id, provider_account_id, name, type,
                        subtype, currency, balance, raw, updated_at
                    )
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    on conflict (connection_id, provider_account_id)
                    do update set name = excluded.name,
                                  type = excluded.type,
                                  subtype = excluded.subtype,
                                  currency = excluded.currency,
                                  balance = excluded.balance,
                                  raw = excluded.raw,
                                  updated_at = excluded.updated_at
                    returning id
                    """,
                    (
                        connection_id,
                        account["provider_account_id"],
                        account["name"],
                        account["type"],
                        account.get("subtype"),
                        account.get("currency") or "BRL",
                        account.get("balance") or Decimal("0"),
                        Jsonb(account.get("raw") or {}),
                        now,
                    ),
                )
                account_db_id = cur.fetchone()["id"]
                account_count += 1

                for tx in account.get("transactions", []):
                    cur.execute(
                        """
                        insert into open_finance_transactions (
                            account_id, provider_transaction_id, description,
                            amount, transaction_date, transacted_at, category, raw
                        )
                        values (%s,%s,%s,%s,%s,%s,%s,%s)
                        on conflict (account_id, provider_transaction_id)
                        do update set description = excluded.description,
                                      amount = excluded.amount,
                                      transaction_date = excluded.transaction_date,
                                      transacted_at = excluded.transacted_at,
                                      category = excluded.category,
                                      raw = excluded.raw
                        """,
                        (
                            account_db_id,
                            tx["provider_transaction_id"],
                            tx["description"],
                            tx["amount"],
                            tx["transaction_date"],
                            tx.get("transacted_at"),
                            tx.get("category"),
                            Jsonb(tx.get("raw") or {}),
                        ),
                    )
                    transaction_count += 1

            # NÃO carimba status/last_sync_at aqui. Esta função é o ESPELHO e só
            # isso: ela roda igual com 0 contas (item deletado devolve
            # `/accounts` = 200 + results:[]) e carimbar ACTIVE aqui era o que
            # ressuscitava conexão morta — DELETED/ERROR viravam ACTIVE com
            # "sincronizado agora". Quem afirma sucesso é `mark_sync_result`, no
            # sync, DEPOIS de o `GET /items/{id}` confirmar que o item existe.
        conn.commit()

    return {"accounts_synced": account_count, "transactions_synced": transaction_count}


# ──────────────────────────────────────────────────────────────────────────────
# Fase 1 — import OF → launches (analytics) + saldo consolidado
# ──────────────────────────────────────────────────────────────────────────────

# Movimento interno = dinheiro indo pra poupança/caixinha/conta própria, NÃO gasto.
# Baseado na taxonomia real do Pluggy (GET /categories). Caixinha do Nubank é um CDB:
# aparece na conta como "Automatic investment"/"Fixed income" (aplicação/resgate).
# NÃO inclui "Proceeds interests and dividends" (isso é RENDA de investimento).
_OF_INTERNAL_CATEGORIES = (
    "automatic investment",   # aplicação automática (Caixinha do Nubank)
    "fixed income",           # CDB que lastreia a caixinha
    "same person transfer",   # transferência entre contas próprias (+ variantes por prefixo)
    "transfer - internal",
)
# Fallback por descrição, pra bancos sem a categoria enriquecida (Pro) do Pluggy.
_OF_INTERNAL_KEYWORDS = (
    "aplicacao", "aplicação", "resgate", "caixinha",
    "poupanca", "poupança", "cofrinho",
)

# Pagamento de fatura de cartão: aparece nas DUAS contas (saída na corrente + entrada
# no cartão). NÃO é gasto novo (as compras já entraram na fatura) nem estorno de compra.
# No lado BANK vira movimento interno (não conta em gasto/sobrou); no lado CREDIT é
# PULADO no import (senão duplica o pagamento e ainda bagunça o total da fatura).
_OF_CREDIT_PAYMENT_CATEGORIES = ("credit card payment",)
_OF_CREDIT_PAYMENT_KEYWORDS = (
    "pagamento de fatura", "pagamento fatura", "pagamento de cartao",
    "pagamento de cartão", "credit card payment", "pagamento recebido",
)


def is_credit_card_payment(category: str | None = None, description: str | None = None) -> bool:
    """True se a transação é pagamento de fatura de cartão (não é compra nem gasto novo)."""
    cat = (category or "").strip().lower()
    desc = (description or "").lower()
    return (
        any(cat == c or cat.startswith(c) for c in _OF_CREDIT_PAYMENT_CATEGORIES)
        or any(k in desc for k in _OF_CREDIT_PAYMENT_KEYWORDS)
    )


def classify_open_finance_launch(amount, category: str | None = None, description: str | None = None) -> dict:
    """Puro: decide tipo/valor/is_internal a partir da transação OF (amount já assinado).

    Caixinha do banco (aplicação/resgate), transferência entre contas próprias e
    pagamento de fatura de cartão viram movimento interno — ficam fora do cálculo de
    gastos/"sobrou" (igual `deposito_caixinha`).
    """
    v = Decimal(str(amount))
    tipo = "despesa" if v < 0 else "receita"
    cat = (category or "").strip().lower()
    desc = (description or "").lower()
    is_internal = (
        any(cat == c or cat.startswith(c) for c in _OF_INTERNAL_CATEGORIES)
        or any(k in desc for k in _OF_INTERNAL_KEYWORDS)
        or is_credit_card_payment(category, description)
    )
    return {"tipo": tipo, "valor": abs(v), "is_internal_movement": is_internal}


# ──────────────────────────────────────────────────────────────────────────────
# Fase 2 — reconciliação OF ↔ manual (não duplicar)
# ──────────────────────────────────────────────────────────────────────────────

# Descrições manuais genéricas (sem estabelecimento) — casam por valor+data mesmo
# sem bater o nome. Normalizadas (sem acento).
_GENERIC_MERCHANTS = {
    "", "almoco", "comida", "gasto", "compra", "lanche", "jantar", "cafe",
    "diversos", "outros", "conta", "boleto", "pix",
}

RECON_AMOUNT_TOL = Decimal("0.05")
RECON_DATE_WINDOW = 3  # dias (janela de graça: o OF chega 0-2 dias depois)


def _normalize_merchant(s) -> str:
    text = unicodedata.normalize("NFKD", str(s or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def merchant_similarity(a, b) -> bool:
    """Puro: dois estabelecimentos são 'parecidos'? Substring ou token (>=3) em comum."""
    na, nb = _normalize_merchant(a), _normalize_merchant(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    ta = {t for t in na.split() if len(t) >= 3}
    tb = {t for t in nb.split() if len(t) >= 3}
    return bool(ta & tb)


def _is_generic_merchant(s) -> bool:
    return _normalize_merchant(s) in _GENERIC_MERCHANTS


def pick_reconciliation_match(valor, tx_date, description, candidates) -> dict:
    """Puro. Decide se a transação OF casa com um lançamento manual.

    candidates: [{id, valor, ref_date(date), alvo, nota}]. Retorna {launch_id, verdict}:
      'auto' = mesclar sozinho (alto-confiança), 'ask' = perguntar (ambíguo), 'none' = sem match.
    """
    target = Decimal(str(valor))
    elig = []
    for c in candidates:
        try:
            cv = Decimal(str(c["valor"]))
        except (KeyError, TypeError, ValueError):
            continue
        if abs(cv - target) > RECON_AMOUNT_TOL:
            continue
        cd = c.get("ref_date")
        if cd is None or abs((cd - tx_date).days) > RECON_DATE_WINDOW:
            continue
        merchant = f"{c.get('alvo') or ''} {c.get('nota') or ''}"
        similar = merchant_similarity(description, merchant) or _is_generic_merchant(c.get("alvo"))
        elig.append({"id": c["id"], "diff": abs((cd - tx_date).days), "similar": similar})

    if not elig:
        return {"launch_id": None, "verdict": "none"}

    elig.sort(key=lambda e: (e["diff"], not e["similar"]))
    best = elig[0]
    # auto só quando há UM candidato claro, data apertada e estabelecimento bate (ou é genérico)
    if len(elig) == 1 and best["diff"] <= 1 and best["similar"]:
        return {"launch_id": best["id"], "verdict": "auto"}
    # 2+ candidatos, data folgada ou nome diverge → perguntar, sugerindo o melhor
    return {"launch_id": best["id"], "verdict": "ask"}


def _find_manual_candidates(cur, user_id: int, tipo: str, valor, tx_date) -> list[dict]:
    """Lançamentos manuais/OFX (não-OF) elegíveis a casar, ainda não vinculados a nenhuma OF tx."""
    cur.execute(
        """
        select id, valor, coalesce(posted_at, criado_em::date) as ref_date, alvo, nota
        from launches
        where user_id = %s
          and tipo = %s
          and coalesce(source, 'manual') <> 'open_finance'
          and is_internal_movement = false
          and abs(valor - %s) <= %s
          and coalesce(posted_at, criado_em::date) between %s and %s
          and not exists (
              select 1 from open_finance_transactions o where o.imported_launch_id = launches.id
          )
        """,
        (user_id, tipo, Decimal(str(valor)), RECON_AMOUNT_TOL,
         tx_date - timedelta(days=RECON_DATE_WINDOW), tx_date + timedelta(days=RECON_DATE_WINDOW)),
    )
    return cur.fetchall()


def import_open_finance_launches(user_id: int, connection_id: int | None = None) -> dict:
    """Importa transações OF (ainda não importadas) de contas BANK como `launches`.

    Modelo híbrido (Design Y): a transação vira lançamento pra alimentar sobrou/timeline/
    budgets, mas com `delta_conta=0` — NÃO move `accounts.balance` (o saldo do banco já é
    contado à parte, autoritativo, em `get_consolidated_balance`). Idempotente via
    `on conflict (user_id, source, external_id)` + filtro `imported_launch_id is null`.
    Cartão de crédito (type != BANK) fica de fora por ora.
    """
    ensure_user(user_id)
    inserted = 0
    auto_merged = 0
    pending = 0
    skipped_non_bank = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select t.id as of_tx_id, t.provider_transaction_id, t.description,
                       t.amount, t.transaction_date, t.transacted_at, t.category,
                       a.type as account_type
                from open_finance_transactions t
                join open_finance_accounts a on a.id = t.account_id
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id = %s
                  and t.imported_launch_id is null
                  and (%s::bigint is null or c.id = %s)
                order by t.transaction_date, t.id
                """,
                (user_id, connection_id, connection_id),
            )
            rows = cur.fetchall()

            for r in rows:
                if (r["account_type"] or "").upper() != "BANK":
                    skipped_non_bank += 1
                    continue

                cls = classify_open_finance_launch(r["amount"], r["category"], r["description"])

                # Reconciliação (Fase 2): gasto/receita não-interno tenta casar com manual.
                verdict, match_id = "none", None
                if not cls["is_internal_movement"]:
                    candidates = _find_manual_candidates(
                        cur, user_id, cls["tipo"], cls["valor"], r["transaction_date"]
                    )
                    pick = pick_reconciliation_match(
                        cls["valor"], r["transaction_date"], r["description"], candidates
                    )
                    verdict, match_id = pick["verdict"], pick["launch_id"]

                if verdict == "auto":
                    # Alto-confiança: mescla no lançamento manual — NÃO cria OF launch (1 real = 1 linha).
                    if r["category"]:
                        cur.execute(
                            "update launches set categoria=%s "
                            "where id=%s and (categoria is null or categoria in ('outros',''))",
                            (r["category"], match_id),
                        )
                    cur.execute(
                        "update open_finance_transactions "
                        "set imported_launch_id=%s, match_launch_id=%s, reconciliation_status='auto_merged' "
                        "where id=%s",
                        (match_id, match_id, r["of_tx_id"]),
                    )
                    auto_merged += 1
                    continue

                # Sem match ('none') ou ambíguo ('ask'): cria o OF launch.
                #
                # `criado_em` (timestamptz) dirige a exibição na lista:
                #   - banco mandou hora real (transacted_at) → usa o instante exato;
                #   - só data → meia-dia no fuso local (evita o "escorrega 1 dia"
                #     que acontecia gravando `date` cru como meia-noite UTC).
                # `time_known` sinaliza pro front mostrar HH:MM só quando é real.
                has_real_time = r["transacted_at"] is not None
                criado_em = (
                    r["transacted_at"] if has_real_time
                    else datetime.combine(r["transaction_date"], time(12, 0), tzinfo=_tz())
                )
                efeitos = {
                    "delta_conta": 0,  # analytics-only: não mexe no saldo manual
                    "open_finance": {"provider_transaction_id": r["provider_transaction_id"]},
                    "time_known": has_real_time,
                }
                cur.execute(
                    """
                    insert into launches(
                        user_id, tipo, valor, categoria, alvo, nota, criado_em, efeitos,
                        source, external_id, posted_at, currency, imported_at, is_internal_movement
                    )
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)
                    on conflict (user_id, source, external_id) do nothing
                    returning id
                    """,
                    (
                        user_id, cls["tipo"], cls["valor"], (r["category"] or "outros"),
                        r["description"], None, criado_em, Jsonb(efeitos),
                        "open_finance", r["provider_transaction_id"], r["transaction_date"], "BRL",
                        cls["is_internal_movement"],
                    ),
                )
                got = cur.fetchone()
                if got:
                    launch_id = got["id"]
                    inserted += 1
                else:
                    cur.execute(
                        "select id from launches where user_id=%s and source='open_finance' and external_id=%s",
                        (user_id, r["provider_transaction_id"]),
                    )
                    ex = cur.fetchone()
                    launch_id = ex["id"] if ex else None

                if launch_id is not None:
                    status = "pending" if verdict == "ask" else "imported"
                    cur.execute(
                        "update open_finance_transactions "
                        "set imported_launch_id=%s, match_launch_id=%s, reconciliation_status=%s where id=%s",
                        (launch_id, (match_id if verdict == "ask" else None), status, r["of_tx_id"]),
                    )
                    if verdict == "ask":
                        pending += 1

        conn.commit()

    return {
        "inserted": inserted,
        "auto_merged": auto_merged,
        "pending": pending,
        "skipped_non_bank": skipped_non_bank,
    }


def confirm_reconciliation(user_id: int, of_tx_id: int) -> dict:
    """Usuário confirma que a OF tx pendente é a MESMA do candidato: funde.

    Apaga o OF launch (delta_conta=0, não mexe no saldo) e revincula a OF tx no lançamento
    manual. Resultado: 1 transação real = 1 linha.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select o.imported_launch_id, o.match_launch_id, o.reconciliation_status
                from open_finance_transactions o
                join open_finance_accounts a on a.id = o.account_id
                join open_finance_connections c on c.id = a.connection_id
                where o.id = %s and c.user_id = %s
                """,
                (of_tx_id, user_id),
            )
            row = cur.fetchone()

    if not row or row["reconciliation_status"] != "pending" or not row["match_launch_id"]:
        return {"ok": False, "reason": "not_pending"}

    of_launch_id = row["imported_launch_id"]
    manual_id = row["match_launch_id"]
    if of_launch_id:
        try:
            delete_launch_and_rollback(user_id, of_launch_id)
        except Exception:
            pass

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update open_finance_transactions "
                "set imported_launch_id=%s, reconciliation_status='auto_merged' where id=%s",
                (manual_id, of_tx_id),
            )
        conn.commit()
    return {"ok": True, "merged_into": manual_id}


def reject_reconciliation(user_id: int, of_tx_id: int) -> dict:
    """Usuário diz que são DIFERENTES: mantém os dois lançamentos, limpa o estado pendente."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update open_finance_transactions o
                set reconciliation_status='imported', match_launch_id=null
                from open_finance_accounts a, open_finance_connections c
                where o.account_id = a.id and a.connection_id = c.id
                  and o.id = %s and c.user_id = %s and o.reconciliation_status = 'pending'
                """,
                (of_tx_id, user_id),
            )
            n = cur.rowcount
        conn.commit()
    return {"ok": n > 0}


def reconcile_manual_launch(user_id: int, launch_id: int) -> dict:
    """Reconciliação REVERSA (P0 #3): usuário criou um lançamento manual; se já existe um OF
    launch gêmeo (importado antes), funde — apaga o OF launch e revincula a OF tx no manual.

    Chamar logo após criar um lançamento manual (bot/web). Best-effort e idempotente.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, tipo, valor, coalesce(posted_at, criado_em::date) as ref_date,
                       alvo, nota, coalesce(source,'manual') as source, is_internal_movement
                from launches where id=%s and user_id=%s
                """,
                (launch_id, user_id),
            )
            m = cur.fetchone()
            if not m or m["source"] == "open_finance" or m["is_internal_movement"]:
                return {"ok": False, "reason": "not_manual"}

            cur.execute(
                """
                select l.id, l.valor, coalesce(l.posted_at, l.criado_em::date) as ref_date,
                       o.id as of_tx_id, o.description as of_desc
                from launches l
                join open_finance_transactions o on o.imported_launch_id = l.id
                where l.user_id=%s and coalesce(l.source,'') = 'open_finance'
                  and l.tipo=%s and l.is_internal_movement = false
                  and abs(l.valor - %s) <= %s
                  and coalesce(l.posted_at, l.criado_em::date) between %s and %s
                  and o.reconciliation_status in ('imported','pending')
                """,
                (user_id, m["tipo"], m["valor"], RECON_AMOUNT_TOL,
                 m["ref_date"] - timedelta(days=RECON_DATE_WINDOW),
                 m["ref_date"] + timedelta(days=RECON_DATE_WINDOW)),
            )
            of_rows = cur.fetchall()

    if not of_rows:
        return {"ok": True, "matched": False}

    manual_desc = f"{m['alvo'] or ''} {m['nota'] or ''}"
    candidates = [
        {"id": r["id"], "valor": r["valor"], "ref_date": r["ref_date"], "alvo": r["of_desc"], "nota": None}
        for r in of_rows
    ]
    pick = pick_reconciliation_match(m["valor"], m["ref_date"], manual_desc, candidates)
    if pick["verdict"] != "auto":
        return {"ok": True, "matched": False, "verdict": pick["verdict"]}

    of_launch_id = pick["launch_id"]
    of_tx_id = next(r["of_tx_id"] for r in of_rows if r["id"] == of_launch_id)
    try:
        delete_launch_and_rollback(user_id, of_launch_id)
    except Exception:
        pass
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update open_finance_transactions "
                "set imported_launch_id=%s, match_launch_id=%s, reconciliation_status='auto_merged' where id=%s",
                (launch_id, launch_id, of_tx_id),
            )
        conn.commit()
    return {"ok": True, "matched": True, "merged_of_launch": of_launch_id}


def import_open_finance_credit(user_id: int, connection_id: int | None = None) -> dict:
    """Importa transações de contas de CRÉDITO do OF pra máquina de faturas (opção a).

    Auto-cria/vincula um cartão PigBank por conta de crédito e lança cada transação em
    `credit_transactions` (dedup por external_id), gravando o back-link `imported_credit_tx_id`.
    Assim compras de cartão entram na fatura, não no saldo — sem furar o "sobrou".
    """
    ensure_user(user_id)
    card_cache: dict[int, int] = {}
    links: list[tuple[int, int]] = []
    inserted = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select t.id as of_tx_id, t.provider_transaction_id, t.description,
                       t.amount, t.transaction_date, t.category, t.raw as tx_raw,
                       a.id as of_account_id, a.name as account_name, a.raw as account_raw
                from open_finance_transactions t
                join open_finance_accounts a on a.id = t.account_id
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id = %s
                  and t.imported_credit_tx_id is null
                  and upper(a.type) = 'CREDIT'
                  and (%s::bigint is null or c.id = %s)
                order by t.transaction_date, t.id
                """,
                (user_id, connection_id, connection_id),
            )
            rows = cur.fetchall()

    for r in rows:
        # Pagamento de fatura NÃO é compra: pular. Ele aparece na conta de crédito como
        # entrada (amount positivo) e, se importado, viraria um "estorno" que (a) duplica o
        # pagamento já visto na conta corrente e (b) reduz errado o total da fatura. O lado
        # BANK já o trata como movimento interno (classify_open_finance_launch).
        if is_credit_card_payment(r["category"], r["description"]):
            continue
        of_acc_id = r["of_account_id"]
        if of_acc_id not in card_cache:
            card_cache[of_acc_id] = get_or_create_open_finance_card(
                user_id, of_acc_id, r["account_name"], r["account_raw"]
            )
        # Parcelas (#11): cada parcela é uma tx separada; marca nº/total e agrupa por compra.
        inst_no, inst_total = extract_installment_info(r["tx_raw"])
        group_id = None
        if inst_total:
            # Heurística da doc Pluggy: cartão + estabelecimento + nº parcelas + valor total.
            # Sem o totalAmount, duas compras diferentes iguais (mesmo merchant/parcelas) colidiam.
            meta = (r["tx_raw"] or {}).get("creditCardMetadata") or {}
            total_amount = meta.get("totalAmount")
            # P2: adiciona o MÊS DE ORIGEM estimado (data da parcela menos nº parcela-1) pra separar
            # compras idênticas feitas em meses distintos — sem quebrar o agrupamento das parcelas de
            # UMA compra: todas back-calculam pro mesmo mês (parcela 1 em M, parcela 2 em M+1 → M).
            origin_ym = ""
            if inst_no and r["transaction_date"] is not None:
                oy, om = add_months(
                    r["transaction_date"].year, r["transaction_date"].month, -(int(inst_no) - 1)
                )
                origin_ym = f"{oy:04d}-{om:02d}"
            key = "|".join([
                str(card_cache[of_acc_id]),
                (r["description"] or "").strip().lower(),
                str(inst_total),
                str(total_amount) if total_amount is not None else "",
                origin_ym,
            ])
            group_id = uuid5(NAMESPACE_OID, key)
        tx_id, created = add_imported_credit_purchase(
            user_id, card_cache[of_acc_id], r["amount"], r["category"],
            r["transaction_date"], r["provider_transaction_id"],
            installment_no=inst_no, installments_total=inst_total, group_id=group_id,
        )
        if created:
            inserted += 1
        if tx_id is not None:
            links.append((r["of_tx_id"], tx_id))

    if links:
        with get_conn() as conn:
            with conn.cursor() as cur:
                for of_tx_id, credit_tx_id in links:
                    cur.execute(
                        "update open_finance_transactions set imported_credit_tx_id=%s where id=%s",
                        (credit_tx_id, of_tx_id),
                    )
            conn.commit()

    return {"inserted": inserted, "linked": len(links), "cards": len(card_cache)}


def _get_or_create_open_bill_cur(cur, user_id: int, card_id: int, ref_date) -> int | None:
    """Como get_or_create_open_bill, mas usando o cursor da transação em curso (sem abrir
    conexão aninhada, que arriscaria lock/inconsistência no meio de um update). Retorna o
    bill_id da fatura que contém ref_date (reabrindo se estava paga/fechada)."""
    cur.execute("select closing_day, user_id from credit_cards where id=%s limit 1", (card_id,))
    card = cur.fetchone()
    if not card or int(card["user_id"]) != int(user_id):
        return None
    period_start, period_end = billing_period_for_close_day(ref_date, int(card["closing_day"]))
    cur.execute(
        """
        insert into credit_bills (user_id, card_id, period_start, period_end, total, status)
        values (%s,%s,%s,%s,0,'open')
        on conflict (card_id, period_start, period_end) do update set user_id = excluded.user_id
        returning id, status
        """,
        (user_id, card_id, period_start, period_end),
    )
    row = cur.fetchone()
    bill_id = int(row["id"])
    if (row.get("status") or "").lower() in ("paid", "closed"):
        cur.execute("update credit_bills set status='open' where id=%s", (bill_id,))
    return bill_id


def sync_imported_open_finance_updates(user_id: int, connection_id: int | None = None) -> dict:
    """Propaga CORREÇÕES da Pluggy (transactions/updated) pros registros já importados.

    Sem isso, uma correção de valor/data/categoria atualizava só o espelho OF — o launch,
    a credit_transaction e o total da fatura ficavam com o valor velho. Mexe apenas em
    registros DO OF (source=open_finance); nunca sobrescreve lançamento manual auto-mesclado.
    """
    ensure_user(user_id)
    launches_updated = 0
    credit_updated = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1) Launches próprios do OF (conta BANK)
            cur.execute(
                """
                select o.amount, o.transaction_date, o.category, o.description,
                       l.id as launch_id, l.valor as cur_valor, l.categoria as cur_cat,
                       l.tipo as cur_tipo, l.is_internal_movement as cur_internal,
                       coalesce(l.posted_at, l.criado_em::date) as cur_date
                from open_finance_transactions o
                join open_finance_accounts a on a.id = o.account_id
                join open_finance_connections c on c.id = a.connection_id
                join launches l on l.id = o.imported_launch_id
                where c.user_id=%s and (%s::bigint is null or c.id=%s)
                  and upper(a.type)='BANK' and coalesce(l.source,'')='open_finance'
                """,
                (user_id, connection_id, connection_id),
            )
            for r in cur.fetchall():
                cls = classify_open_finance_launch(r["amount"], r["category"], r["description"])
                new_cat = r["category"] or "outros"
                changed = (
                    Decimal(str(r["cur_valor"])) != cls["valor"]
                    or r["cur_tipo"] != cls["tipo"]
                    or (r["cur_cat"] or "") != new_cat
                    or bool(r["cur_internal"]) != cls["is_internal_movement"]
                    or r["cur_date"] != r["transaction_date"]
                )
                if changed:
                    cur.execute(
                        """
                        update launches set valor=%s, tipo=%s, categoria=%s,
                               is_internal_movement=%s, posted_at=%s
                        where id=%s
                        """,
                        (cls["valor"], cls["tipo"], new_cat, cls["is_internal_movement"],
                         r["transaction_date"], r["launch_id"]),
                    )
                    launches_updated += 1

            # 2) Transações de cartão (ajusta o total da fatura pela diferença)
            cur.execute(
                """
                select o.amount, o.transaction_date, o.category,
                       ct.id as ct_id, ct.valor as cur_valor, ct.is_refund as cur_refund,
                       ct.categoria as cur_cat, ct.purchased_at as cur_date, ct.bill_id,
                       ct.card_id
                from open_finance_transactions o
                join open_finance_accounts a on a.id = o.account_id
                join open_finance_connections c on c.id = a.connection_id
                join credit_transactions ct on ct.id = o.imported_credit_tx_id
                where c.user_id=%s and (%s::bigint is null or c.id=%s)
                  and upper(a.type)='CREDIT'
                """,
                (user_id, connection_id, connection_id),
            )
            for r in cur.fetchall():
                amt = Decimal(str(r["amount"]))
                new_valor = -amt  # convenção canônica assinada (compra +, estorno -)
                new_refund = amt > 0
                new_cat = r["category"]
                changed = (
                    Decimal(str(r["cur_valor"])) != new_valor
                    or bool(r["cur_refund"]) != new_refund
                    or (r["cur_cat"] or None) != (new_cat or None)
                    or r["cur_date"] != r["transaction_date"]
                )
                if changed:
                    old_valor = Decimal(str(r["cur_valor"]))
                    old_bill_id = r["bill_id"]
                    new_bill_id = old_bill_id
                    # P1: se a data mudou de ciclo de fatura, a transação precisa MIGRAR de fatura.
                    # Sem isso, ela mantinha o bill_id antigo e o gasto continuava preso no mês/fatura
                    # velha (dashboard e analytics agrupam por credit_bills.period_end).
                    if r["cur_date"] != r["transaction_date"] and r["card_id"] is not None:
                        resolved = _get_or_create_open_bill_cur(
                            cur, user_id, r["card_id"], r["transaction_date"]
                        )
                        if resolved is not None:
                            new_bill_id = resolved
                    cur.execute(
                        "update credit_transactions set valor=%s, is_refund=%s, categoria=%s, "
                        "purchased_at=%s, bill_id=%s where id=%s",
                        (new_valor, new_refund, new_cat, r["transaction_date"], new_bill_id, r["ct_id"]),
                    )
                    if new_bill_id == old_bill_id:
                        # mesma fatura: ajusta só pela diferença de valor (fatura foi `total += valor`).
                        cur.execute(
                            "update credit_bills set total = total + %s where id=%s and user_id=%s",
                            (new_valor - old_valor, old_bill_id, user_id),
                        )
                    else:
                        # migrou de fatura: remove o valor antigo da fatura velha e soma o novo na nova.
                        cur.execute(
                            "update credit_bills set total = total - %s where id=%s and user_id=%s",
                            (old_valor, old_bill_id, user_id),
                        )
                        cur.execute(
                            "update credit_bills set total = total + %s where id=%s and user_id=%s",
                            (new_valor, new_bill_id, user_id),
                        )
                    credit_updated += 1

        conn.commit()

    return {"launches_updated": launches_updated, "credit_updated": credit_updated}


# ──────────────────────────────────────────────────────────────────────────────
# P2 — inteligência: salário (Fase 5) e anomalia de conta (Fase 6), via dados OF
# ──────────────────────────────────────────────────────────────────────────────

def detect_recurring_income_matches(credits, value_tol_pct=Decimal("0.10"), day_tol=3) -> list:
    """Puro. credits: [{id, valor, date}]. Retorna os ids que se repetem em MESES distintos
    (mesmo ~valor ±10%, ~dia ±3) — candidatos a renda recorrente / salário."""
    out = []
    for i, c in enumerate(credits):
        cv, cd = Decimal(str(c["valor"])), c["date"]
        for j, o in enumerate(credits):
            if i == j:
                continue
            od = o["date"]
            if (cd.year, cd.month) == (od.year, od.month):
                continue
            ov = Decimal(str(o["valor"]))
            hi = max(cv, ov)
            if hi > 0 and abs(cv - ov) / hi <= value_tol_pct and abs(cd.day - od.day) <= day_tol:
                out.append(c["id"])
                break
    return out


def detect_open_finance_salary(user_id: int, months: int = 4) -> dict | None:
    """Acha o crédito OF mais recente que parece salário (renda recorrente).

    Base pra confirmação proativa (Fase 5). O envio WhatsApp fica dormente até ter template
    Meta (igual o lembrete de boleto). Retorna {launch_id, valor, date} ou None.
    """
    ensure_user(user_id)
    since = datetime.now(_tz()).date() - timedelta(days=months * 31)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, valor, coalesce(posted_at, criado_em::date) as date
                from launches
                where user_id=%s and coalesce(source,'')='open_finance' and tipo='receita'
                  and is_internal_movement=false
                  and coalesce(posted_at, criado_em::date) >= %s
                order by coalesce(posted_at, criado_em::date)
                """,
                (user_id, since),
            )
            credits = cur.fetchall()

    ids = set(detect_recurring_income_matches(
        [{"id": c["id"], "valor": c["valor"], "date": c["date"]} for c in credits]
    ))
    recurring = [c for c in credits if c["id"] in ids]
    if not recurring:
        return None
    recurring.sort(key=lambda c: (c["date"], c["valor"]))
    cand = recurring[-1]
    return {"launch_id": cand["id"], "valor": cand["valor"], "date": cand["date"]}


def detect_bill_increase(expenses, threshold_pct=Decimal("0.15")) -> list:
    """Puro. expenses: [{merchant, valor, date}]. Agrupa por estabelecimento; se aparece em
    >=2 meses e o mais recente subiu >= threshold vs a média anterior → flag de aumento."""
    from collections import defaultdict
    groups: dict = defaultdict(list)
    for e in expenses:
        key = _normalize_merchant(e["merchant"])
        if key:
            groups[key].append(e)
    out = []
    for items in groups.values():
        by_month = {}
        for e in items:
            by_month[(e["date"].year, e["date"].month)] = e  # último do mês vence
        if len(by_month) < 2:
            continue
        ordered = [by_month[k] for k in sorted(by_month)]
        latest = ordered[-1]
        prior = ordered[:-1]
        avg_prior = sum(Decimal(str(e["valor"])) for e in prior) / len(prior)
        lv = Decimal(str(latest["valor"]))
        if avg_prior > 0 and (lv - avg_prior) / avg_prior >= threshold_pct:
            out.append({
                "merchant": latest["merchant"],
                "old": avg_prior,
                "new": lv,
                "pct": (lv - avg_prior) / avg_prior * 100,
            })
    return out


def detect_open_finance_bill_increase(user_id: int, months: int = 4) -> list:
    """Detecta contas que aumentaram usando dados OF (Fase 6) — pega até conta que o usuário
    nem cadastrou como recorrente. Complementa `_detect_recurring_increase` (só olha manual)."""
    ensure_user(user_id)
    since = datetime.now(_tz()).date() - timedelta(days=months * 31)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select alvo, nota, valor, coalesce(posted_at, criado_em::date) as date
                from launches
                where user_id=%s and coalesce(source,'')='open_finance' and tipo='despesa'
                  and is_internal_movement=false
                  and coalesce(posted_at, criado_em::date) >= %s
                """,
                (user_id, since),
            )
            rows = cur.fetchall()
    expenses = [{"merchant": (r["alvo"] or r["nota"] or ""), "valor": r["valor"], "date": r["date"]} for r in rows]
    return detect_bill_increase(expenses)


# Contas BANK que compõem o saldo corrente do usuário. Uma definição só, usada
# pelo saldo consolidado E pela escolha de origem do lançamento — se as duas
# divergirem, o bot recusa um aporte que a tela diz que cabe (ou o contrário).
#
# Dedup por conta REAL (provider_account_id): reconectar o banco cria uma nova
# connection_id com a MESMA conta (a unicidade é por conexão), o que somaria o
# mesmo saldo 2x. DISTINCT ON pega o saldo da conexão mais recente por conta.
# PAUSED/DELETED mantêm o espelho local para histórico, mas não representam uma
# conexão atual e portanto não podem compor o saldo corrente.
# Só contas em BRL: o saldo manual é em reais e não há conversão de câmbio —
# somar USD 100 como R$ 100 mentiria o total.
BANK_ACCOUNTS_SQL = """
    select * from (
        select distinct on (a.provider_account_id)
            a.id, a.name, a.balance, c.institution_name,
            upper(coalesce(c.status, '')) as connection_status
        from open_finance_accounts a
        join open_finance_connections c on c.id = a.connection_id
        where c.user_id=%s and upper(a.type) = 'BANK'
          and upper(coalesce(a.currency, 'BRL')) = 'BRL'
        order by a.provider_account_id, c.id desc
    ) uniq
    where connection_status not in ('PAUSED', 'DELETED')
"""


def list_bank_accounts(user_id: int) -> list[dict]:
    """Contas BANK conectadas e ativas, com saldo e rótulo para exibição.

    Mesmo recorte do `get_consolidated_balance` — as duas leem `BANK_ACCOUNTS_SQL`.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(BANK_ACCOUNTS_SQL + " order by balance desc nulls last, id", (user_id,))
            rows = [dict(r) for r in (cur.fetchall() or [])]

    for r in rows:
        # "Nubank · Conta" quando os dois existem; o que houver, senão.
        partes = [p for p in ((r.get("institution_name") or "").strip(),
                              (r.get("name") or "").strip()) if p]
        r["label"] = " · ".join(partes) or "Banco conectado"
        r["balance"] = r.get("balance") or Decimal("0")
    return rows


def pending_bank_outflows(user_id: int) -> dict[int, Decimal]:
    """Saídas já lançadas contra uma conta do banco que o sync ainda não refletiu.

    O saldo em `open_finance_accounts` é um espelho: o Pig não escreve nele. Então um
    aporte com origem `bank` não reduz nada, e sem esta conta o MESMO saldo autorizaria
    infinitos lançamentos — medido: 3 aportes de R$ 800 aceitos contra R$ 1.387,76.

    O corte é `criado_em > a.updated_at`: `updated_at` é carimbado a cada sync
    (save_open_finance_sync), então lançamentos anteriores a ele já estão embutidos no
    saldo que o banco mandou e não podem ser descontados duas vezes.

    Só saídas. Resgate com destino `bank` devolve dinheiro pro banco, mas creditar
    disponibilidade antes do sync confirmar seria adiantar dinheiro que ainda não chegou.

    Devolve {of_account_id: total_pendente}.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select (l.efeitos->'funding_source'->>'of_account_id')::bigint as of_account_id,
                       coalesce(sum(l.valor), 0) as total
                from launches l
                join open_finance_accounts a
                  on a.id = (l.efeitos->'funding_source'->>'of_account_id')::bigint
                where l.user_id = %s
                  and l.efeitos->'funding_source'->>'kind' = 'bank'
                  and l.tipo in ('aporte_investimento', 'deposito_caixinha')
                  and l.criado_em > a.updated_at
                group by 1
                """,
                (user_id,),
            )
            return {int(r["of_account_id"]): Decimal(str(r["total"] or 0))
                    for r in (cur.fetchall() or []) if r["of_account_id"] is not None}


def assert_bank_covers(cur, user_id: int, of_account_id, valor) -> None:
    """Autoriza uma saída contra uma conta do banco — DENTRO da transação que a grava.

    O `funding.resolve` no serviço decide e explica; quem *autoriza* é aqui. Sem isto a
    checagem virava check-then-act: medido com duas threads, dois aportes de R$ 800
    simultâneos passaram contra um saldo de R$ 1.000 (R$ 1.600 gravados). O caminho da
    Carteira nunca teve esse furo porque o `select ... for update` serializa.

    O `for update` na linha da conta é o que serializa aqui: a segunda transação espera
    a primeira commitar e então enxerga o lançamento dela no pendente.

    Ordem de lock: SEMPRE depois do `accounts ... for update` que os chamadores já fazem,
    para não inverter a ordem entre transações e criar deadlock.
    """
    if of_account_id is None:
        return  # origem banco sem conta identificada: não há o que conferir

    # Join na conexão para filtrar pelo DONO. Sem isso a query autorizaria contra o
    # saldo de uma conta de outro usuário — regra dura do CLAUDE.md §5 ("toda query
    # com WHERE user_id = %s"). Hoje todo `funding_source` nasce de
    # funding.list_sources(user_id), mas esta função é pública em db/ e está a um
    # chamador descuidado de virar vazamento real.
    cur.execute(
        """
        select a.balance, a.updated_at
        from open_finance_accounts a
        join open_finance_connections c on c.id = a.connection_id
        where a.id = %s and c.user_id = %s
        for update of a
        """,
        (int(of_account_id), user_id),
    )
    conta = cur.fetchone()
    if not conta:
        # Vazio cobre dois casos que não dá para separar aqui: conta desconectada no
        # meio do fluxo, e conta que não é deste usuário. Entre liberar os dois e
        # negar os dois, negar é o lado seguro — o custo é o caso raro de desconexão
        # virar uma recusa, e aí o usuário reconecta.
        raise ValueError("INSUFFICIENT_ACCOUNT")
    cur.execute(
        """
        select coalesce(sum(l.valor), 0) as total
        from launches l
        where l.user_id = %s
          and l.efeitos->'funding_source'->>'kind' = 'bank'
          and (l.efeitos->'funding_source'->>'of_account_id')::bigint = %s
          and l.tipo in ('aporte_investimento', 'deposito_caixinha')
          and l.criado_em > %s
        """,
        (user_id, int(of_account_id), conta["updated_at"]),
    )
    pendente = Decimal(str(cur.fetchone()["total"] or 0))
    disponivel = Decimal(str(conta["balance"] or 0)) - pendente
    if disponivel < Decimal(str(valor)):
        raise ValueError("INSUFFICIENT_ACCOUNT")


def get_consolidated_balance(user_id: int) -> dict:
    """Saldo consolidado = saldo manual + soma dos saldos das contas BANK conectadas.

    Cartão (type CREDIT) fica de fora (é dívida, não saldo disponível). Auto-atualiza
    conforme o sync refresca os saldos autoritativos dos bancos. `of_bank_count` é o
    nº de contas BANK conectadas — 0 = sem banco, e o chamador pode mostrar só o manual.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select coalesce(balance, 0) as b from accounts where user_id=%s", (user_id,))
            row = cur.fetchone()
            manual = row["b"] if row else Decimal("0")

            cur.execute(
                f"select coalesce(sum(balance), 0) as b, count(*) as n from ({BANK_ACCOUNTS_SQL}) s",
                (user_id,),
            )
            of_row = cur.fetchone()
            of_bank = of_row["b"]
            of_count = int(of_row["n"] or 0)

    return {
        "manual": manual,
        "open_finance_bank": of_bank,
        "of_bank_count": of_count,
        "consolidated": (manual or Decimal("0")) + (of_bank or Decimal("0")),
    }


def disconnect_open_finance_connection(
    user_id: int, connection_id: int | None = None, *,
    swept_out: list[str] | None = None,
) -> int:
    """Desconecta banco(s) e LIMPA o que foi importado (P0 integridade).

    Política: reverte launches OF + transações de fatura, apaga os cartões auto-criados
    que ficaram vazios, e só então remove a conexão (cascade nas contas/transações OF).
    Lançamentos MANUAIS que foram auto-mesclados são preservados (só desvinculados).

    `swept_out` (opcional, saída): recebe os provider_item_id Pluggy das
    conexões que ESTE delete varreu (sem PAUSED — item já morto na Pluggy).
    A rota do disconnect compara com o que a limpeza remota enumerou e faz um
    2º passe no que ficou de fora (item salvo entre a enumeração e o delete —
    Codex PR #217, 12º). Out-param em vez de mudar o retorno: o `int` é
    contrato de 3 chamadores.
    """
    ensure_user(user_id)

    # 1. junta as transações OF importadas + os cartões auto-criados desta conexão.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select t.id, c.user_id, t.imported_launch_id, t.imported_credit_tx_id,
                       l.source as launch_source
                from open_finance_transactions t
                join open_finance_accounts a on a.id = t.account_id
                join open_finance_connections c on c.id = a.connection_id
                left join launches l on l.id = t.imported_launch_id
                where c.user_id = %s and (%s::bigint is null or c.id = %s)
                """,
                (user_id, connection_id, connection_id),
            )
            rows = cur.fetchall()

            cur.execute(
                """
                select cc.id as card_id
                from credit_cards cc
                join open_finance_accounts a on a.id = cc.open_finance_account_id
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id = %s and (%s::bigint is null or c.id = %s)
                """,
                (user_id, connection_id, connection_id),
            )
            card_ids = [r["card_id"] for r in cur.fetchall()]

    # 2. reverte launches/fatura importados.
    _rollback_imported_of(rows)

    # 3. apaga cartões auto-criados que ficaram sem transações + remove a conexão.
    with get_conn() as conn:
        with conn.cursor() as cur:
            for cid in card_ids:
                cur.execute("select count(*) as n from credit_transactions where card_id=%s", (cid,))
                if cur.fetchone()["n"] == 0:
                    cur.execute("delete from credit_cards where id=%s and user_id=%s", (cid, user_id))

            if connection_id is None:
                cur.execute(
                    "delete from open_finance_connections where user_id=%s "
                    "returning provider, provider_item_id, status",
                    (user_id,),
                )
            else:
                cur.execute(
                    "delete from open_finance_connections where user_id=%s and id=%s "
                    "returning provider, provider_item_id, status",
                    (user_id, connection_id),
                )
            varridas = cur.fetchall()
            deleted = len(varridas)
            if swept_out is not None:
                swept_out.extend(sorted({
                    r["provider_item_id"] for r in varridas
                    if r["provider"] == "pluggy" and r["provider_item_id"]
                    and str(r["status"] or "").upper() != "PAUSED"
                }))
        conn.commit()

    return deleted


def user_synced_within(user_id: int, minutes: int) -> bool:
    """True se alguma conexão Open Finance do usuário sincou nos últimos N minutos.

    Sinal barato de "sync possivelmente em andamento": sync_pluggy_user processa
    os itens em sequência e cada um carimba last_sync_at ao terminar, então um
    carimbo recente significa que os itens seguintes ainda podem estar por vir.
    Serve pra segurar o e-mail dos agentes whole-portfolio enquanto a carteira
    pode estar a meio caminho (ver _AGENT_EMAIL_MIN_AGE_MIN em piggy_agents)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select 1 from open_finance_connections
                where user_id = %s
                  and last_sync_at is not null
                  and last_sync_at >= now() - make_interval(mins => %s)
                limit 1
                """,
                (user_id, minutes),
            )
            return cur.fetchone() is not None
