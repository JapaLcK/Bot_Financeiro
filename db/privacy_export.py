"""
db/privacy_export.py — Exportação LGPD: o ZIP que o titular baixa sobre si mesmo.

Saiu de `db/privacy.py`, que continua reexportando `build_user_export_zip`.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from core.crypto import ATOR_TITULAR, PiiAccessContext, decrypt_pii_optional

from .connection import get_conn
from .privacy import _table_exists

logger = logging.getLogger(__name__)


class PrivacyJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, (bytes, bytearray, memoryview)):
            return bytes(obj).hex()
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return super().default(obj)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, cls=PrivacyJSONEncoder, ensure_ascii=False))


def _fetch_rows(cur, name: str, sql: str, params: tuple) -> tuple[str, list[dict]]:
    cur.execute(sql, params)
    return name, [dict(row) for row in cur.fetchall()]


# A9 (dono, 2026-10-29): chaves de topo de `system_event_logs.details` que não vão para o ZIP do
# titular. A pilha mostra o NOSSO código (caminho, linha, trecho), não é dado dele; o banco e o painel
# admin continuam com ela. Só o nível de cima: nenhum gravador aninha `traceback`.
_DETAILS_FORA_DA_EXPORTACAO = frozenset({"traceback"})


_CHAVE_INDECIFRAVEL = "(indecifrável)"


def _chave_pix_em_claro(rows: list[dict], user_id: int) -> None:
    """Troca `pix_key_enc` por `chave_pix` em claro: o cifrado nunca chega ao ZIP. O token de uso
    único já foi consumido, então uma linha indecifrável vira marcador em vez de derrubar a exportação."""
    for row in rows:
        enc = row.pop("pix_key_enc")
        try:
            row["chave_pix"] = decrypt_pii_optional(enc, ctx=PiiAccessContext(
                purpose="data_export", actor=ATOR_TITULAR, subject_user_id=user_id, field="pix_key"))
        except Exception:
            row["chave_pix"] = _CHAVE_INDECIFRAVEL
            logger.warning("PII indecifrável na exportação: user_id=%s field=pix_key", user_id,
                           extra={"user_id": user_id})


def build_user_export_zip(user_id: int) -> bytes:
    datasets: dict[str, list[dict]] = {}

    with get_conn() as conn, conn.cursor() as cur:
        queries = [
            ("usuario", "select * from users where id = %s", (user_id,)),
            (
                "conta_login",
                """
                select id, user_id, email, phone_e164, phone_status, phone_confirmed_at,
                       whatsapp_verified_at, plan, plan_expires_at, created_at,
                       stripe_customer_id, engagement_opt_out, last_activity_at,
                       last_tip_sent_at, tip_email_opt_out, last_insight_sent_at,
                       insight_email_opt_out,
                       -- só a adesão VIVA (o critério da tela; a morta espera a próxima escrita para sair)
                       case when pb_insight_adesao_viva(insight_aviso_aderiu_em, insight_aviso_coberto_ate)
                            then insight_aviso_aderiu_em end as insight_aviso_aderiu_em,
                       insight_aviso_coberto_ate,
                       whatsapp_updates_opt_out,
                       last_reengagement_sent_at, deletion_requested_at,
                       deletion_scheduled_for, deletion_status,
                       deletion_processing_started_at, dashboard_profile, signup_quiz
                from auth_accounts
                where user_id = %s
                """,
                (user_id,),
            ),
            ("identidades", "select * from user_identities where user_id = %s", (user_id,)),
            ("contas", "select * from accounts where user_id = %s", (user_id,)),
            ("lancamentos", "select * from launches where user_id = %s", (user_id,)),
            ("declaracoes_bancarias", "select * from bank_movement_declarations where user_id = %s", (user_id,)),
            ("saques_depositos_dinheiro", "select * from of_cash_links where user_id = %s", (user_id,)),
            ("cobertura_open_finance", "select * from of_cash_coverage where user_id = %s", (user_id,)),
            ("orcamentos", "select * from category_budgets where user_id = %s", (user_id,)),
            ("regras_categorias", "select * from user_category_rules where user_id = %s", (user_id,)),
            ("gatilhos_categorias", "select * from user_category_triggers where user_id = %s", (user_id,)),
            ("candidatos_gatilhos_categorias", "select * from user_trigger_candidates where user_id = %s", (user_id,)),
            ("feedback_categorias", "select * from user_category_feedback where user_id = %s", (user_id,)),
            ("acoes_pendentes", "select * from pending_actions where user_id = %s", (user_id,)),
            ("caixinhas", "select * from pockets where user_id = %s", (user_id,)),
            ("investimentos", "select * from investments where user_id = %s", (user_id,)),
            ("historico_patrimonio", "select * from patrimonio_fotos where user_id = %s", (user_id,)),
            ("lotes_investimentos", "select * from investment_lots where user_id = %s", (user_id,)),
            ("cartoes", "select * from credit_cards where user_id = %s", (user_id,)),
            ("faturas_cartao", "select * from credit_bills where user_id = %s", (user_id,)),
            ("transacoes_cartao", "select * from credit_transactions where user_id = %s", (user_id,)),
            ("preferencias_resumo_diario", "select * from daily_report_prefs where user_id = %s", (user_id,)),
            ("marcacoes_assinaturas", "select * from subscription_marks where user_id = %s", (user_id,)),
            ("insights_estado", "select * from insight_estado where user_id = %s", (user_id,)),
            ("guia_do_painel", "select * from guia_painel where user_id = %s", (user_id,)),
            ("metas", "select * from metas where user_id = %s", (user_id,)),
            ("importacoes_ofx", "select * from ofx_imports where user_id = %s", (user_id,)),
            ("sessoes_dashboard", "select code, user_id, expires_at, created_at from dashboard_sessions where user_id = %s", (user_id,)),
            (
                "conexoes_open_finance",
                "select * from open_finance_connections where user_id = %s",
                (user_id,),
            ),
            (
                "contas_open_finance",
                """
                select a.*
                from open_finance_accounts a
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id = %s
                """,
                (user_id,),
            ),
            (
                "transacoes_open_finance",
                """
                select t.*
                from open_finance_transactions t
                join open_finance_accounts a on a.id = t.account_id
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id = %s
                """,
                (user_id,),
            ),
            (
                "investimentos_open_finance",
                """
                select i.*
                from open_finance_investments i
                join open_finance_connections c on c.id = i.connection_id
                where c.user_id = %s
                """,
                (user_id,),
            ),
            (
                "historico_investimentos_open_finance",
                """
                select s.*
                from open_finance_investment_snapshots s
                join open_finance_connections c on c.id = s.connection_id
                where c.user_id = %s
                """,
                (user_id,),
            ),
            (
                "recorrencias_open_finance",
                """
                select rp.*
                from of_recurring_payments rp
                join open_finance_connections c on c.id = rp.connection_id
                where c.user_id = %s
                """,
                (user_id,),
            ),
            (
                "faturas_cartao_open_finance",
                """
                select b.*
                from of_card_bills b
                join open_finance_accounts a on a.id = b.account_id
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id = %s
                """,
                (user_id,),
            ),
            # Programa de afiliados (#1415): colunas NOMEADAS e só do titular. Ficam de fora
            # `referred_user_id` e `stripe_invoice_id` (identificam o indicado e a fatura dele),
            # o `pix_key_hash`, as pendências e eventos de reembolso e os pagamentos do indicado.
            # `pix_key_enc` sai daqui só para virar `chave_pix` em claro logo abaixo.
            # `count(r.id)` e não `count(*)`: o left join daria 1 a quem não tem indicado; conta
            # também a indicação de quem excluiu a conta, igual a `get_affiliate_stats`.
            # `original_amount_cents` só equivale ao de `list_affiliate_commissions` com a revisão da mesma
            # fatura (o join a exige; por construção ela nasce pela fatura da comissão; unique: no máximo uma).
            (
                "afiliado",
                """
                select a.code, a.status, a.commission_bps, a.created_at, a.pix_key_enc,
                       count(r.id) as indicacoes
                from affiliates a
                left join affiliate_referrals r on r.affiliate_id = a.id
                where a.user_id = %s
                group by a.id
                """,
                (user_id,),
            ),
            (
                "afiliado_comissoes",
                """
                select c.id, c.invoice_amount_cents, c.amount_cents, c.commission_bps, c.paid_months,
                       c.status, c.available_at, c.payout_id, c.payment_check_pending, c.created_at,
                       v.amount_cents as original_amount_cents
                from affiliate_commissions c
                join affiliates a on a.id = c.affiliate_id
                left join affiliate_refund_reviews v
                  on v.stripe_invoice_id = c.stripe_invoice_id
                 and v.commission_id = c.id
                 and v.amount_cents <> c.amount_cents
                where a.user_id = %s
                """,
                (user_id,),
            ),
            (
                "afiliado_saques",
                """
                select p.id, p.amount_cents, p.status, p.requested_at, p.paid_at, p.note, p.pix_key_enc
                from affiliate_payouts p
                join affiliates a on a.id = p.affiliate_id
                where a.user_id = %s
                """,
                (user_id,),
            ),
            (
                "indicacao_recebida",
                """
                select code_used, created_at
                from affiliate_referrals
                where referred_user_id = %s
                """,
                (user_id,),
            ),
        ]

        for name, sql, params in queries:
            table_name = sql.split(" from ", 1)[-1].split()[0].strip()
            if table_name and table_name.isidentifier() and not _table_exists(cur, table_name):
                datasets[name] = []
                continue
            datasets[name] = _fetch_rows(cur, name, sql, params)[1]

        optional_queries = [
            (
                "eventos_login",
                "select id, user_id, email, success, failure_reason, ip_address, user_agent, created_at from auth_login_events where user_id = %s",
                (user_id,),
            ),
            (
                "eventos_sistema",
                "select id, level, event_type, message, source, user_id, details, created_at from system_event_logs where user_id = %s",
                (user_id,),
            ),
            # Cobranças Pix (§3.2). Colunas NOMEADAS e não `select *`: o
            # `qr_payload_enc` fica de fora porque é o "copia e cola" que MOVE
            # dinheiro (§13.6) — cifrado no banco, ele sairia daqui em texto
            # inútil para a pessoa e útil para quem interceptasse o ZIP. O
            # rastreio (`ga_client_id`, `fbp`, `fbc`) entra: é dado sobre a
            # pessoa, e é justamente o que ela tem direito de ver.
            (
                "cobrancas_pix",
                """
                select id, user_id, external_reference, asaas_payment_id,
                       asaas_customer_id, plan, plan_stored, price_cents,
                       credit_cents, amount_cents, currency, duration_days,
                       stripe_subscription_id, public_token, status,
                       due_date, qr_expires_at, access_starts_at,
                       access_expires_at, ga_client_id, fbp, fbc,
                       created_at, paid_at, canceled_at, refunded_at, purged_at
                from pix_charges
                where user_id = %s
                """,
                (user_id,),
            ),
        ]
        for name, sql, params in optional_queries:
            table_name = sql.split(" from ", 1)[-1].split()[0].strip()
            if _table_exists(cur, table_name):
                datasets[name] = _fetch_rows(cur, name, sql, params)[1]

    # O saque decifra o próprio snapshot: a chave usada naquele pedido, não a cadastrada hoje.
    for name in ("afiliado", "afiliado_saques"):
        _chave_pix_em_claro(datasets[name], user_id)

    for evento in datasets.get("eventos_sistema", []):
        if isinstance(evento["details"], dict):
            evento["details"] = {k: v for k, v in evento["details"].items()
                                 if k not in _DETAILS_FORA_DA_EXPORTACAO}

    manifest = {
        "generated_at": datetime.now(timezone.utc),
        "user_id": user_id,
        "format": "json+csv",
        "datasets": {name: len(rows) for name, rows in datasets.items()},
        "notes": [
            "Hashes de senha não são exportados.",
            "Arquivos CSV são cópias tabulares; dados aninhados também aparecem no JSON completo.",
            "Do programa de afiliados saem só os dados do titular, com a chave Pix em claro (a cadastrada"
            " e a usada em cada saque); as pessoas indicadas não são identificadas, e de quem você indicou"
            " sai só a contagem.",
        ],
    }
    payload = {"manifesto": manifest, "dados": datasets}

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "dados.json",
            json.dumps(_json_safe(payload), ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "manifesto.json",
            json.dumps(_json_safe(manifest), ensure_ascii=False, indent=2),
        )
        for name, rows in datasets.items():
            csv_buffer = io.StringIO()
            fieldnames = sorted({key for row in rows for key in row.keys()})
            writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames or ["sem_dados"])
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    key: json.dumps(_json_safe(value), ensure_ascii=False) if isinstance(value, (dict, list)) else value
                    for key, value in row.items()
                })
            zf.writestr(f"csv/{name}.csv", csv_buffer.getvalue())

    return buffer.getvalue()
