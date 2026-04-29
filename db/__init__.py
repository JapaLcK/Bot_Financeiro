"""
db/__init__.py — Pacote de acesso ao banco de dados.

Re-exporta todos os símbolos públicos para manter compatibilidade com o código
existente que importa diretamente de `db` (ex: `from db import get_conn`).

Organização interna por domínio:
  connection  → get_conn
  schema      → init_db
  users       → gestão de usuários e identidades
  accounts    → saldo, lançamentos, OFX
  pockets     → caixinhas
  investments → investimentos e CDI
  categories  → regras de categorização
  pending     → ações pendentes
  cards       → cartões de crédito e faturas
  open_finance → conexões Open Finance/Pluggy
  reports     → relatório diário, auth, dashboard, engajamento
"""

# ── Conexão ──────────────────────────────────────────────────────────────────
from .connection import get_conn

# ── Schema / init ─────────────────────────────────────────────────────────────
from .schema import init_db

# ── Usuários ──────────────────────────────────────────────────────────────────
from .users import (
    ensure_user_tx,
    ensure_user,
    merge_users,
    choose_primary_user,
    user_score,
    get_or_create_canonical_user,
    create_link_code,
    create_platform_onboarding_token,
    consume_platform_onboarding_token,
    consume_link_code,
    bind_identity,
    link_platform_identity,
)

# ── Contas e Lançamentos ──────────────────────────────────────────────────────
from .accounts import (
    get_balance,
    set_balance,
    add_launch_and_update_balance,
    list_launches,
    update_launch_category,
    update_launch_categories_bulk,
    export_launches,
    get_launches_by_period,
    get_summary_by_period,
    delete_launch_and_rollback,
    get_ofx_import_by_hash,
    import_ofx_launches_bulk,
)

# ── Caixinhas ─────────────────────────────────────────────────────────────────
from .pockets import (
    list_pockets,
    pocket_withdraw_to_account,
    create_pocket,
    pocket_deposit_from_account,
    delete_pocket,
)

# ── Investimentos e CDI ───────────────────────────────────────────────────────
from .investments import (
    create_investment,
    create_investment_db,
    delete_investment,
    list_investments,
    accrue_all_investments,
    accrue_investment_db,
    investment_deposit_from_account,
    investment_withdraw_to_account,
    get_latest_cdi,
    get_latest_cdi_aa,
    get_latest_cdi_daily_pct,
    get_latest_selic_aa,
    get_latest_ipca_12m,
    get_dashboard_market_rates,
    _get_cdi_daily_map,
    _business_days_between,
)

# ── Categorias ────────────────────────────────────────────────────────────────
from .categories import (
    list_category_rules,
    add_category_rule,
    delete_category_rule,
    delete_category_rules_by_category,
    list_categories,
    get_memorized_category,
    upsert_category_rule,
    list_user_category_rules,
    resolve_category_rule_target,
)

# ── Ações pendentes ───────────────────────────────────────────────────────────
from .pending import (
    set_pending_action,
    get_pending_action,
    clear_pending_action,
)

# ── Cartões de crédito ────────────────────────────────────────────────────────
from .cards import (
    card_name_exists,
    create_card,
    delete_card,
    get_card_id_by_name,
    set_default_card,
    get_default_card_id,
    list_cards,
    get_card_by_id,
    get_card_credit_usage,
    set_card_limit,
    update_card_reminder_settings,
    mark_card_reminder_sent,
    add_months,
    bill_period_for_month,
    get_or_create_open_bill,
    get_or_create_bill_by_period,
    get_current_open_bill_id,
    add_credit_purchase,
    add_credit_purchase_installments,
    add_credit_refund,
    undo_credit_transaction,
    undo_installment_group,
    resolve_installment_group_id,
    get_open_bill_summary,
    pay_bill_amount,
    close_bill,
    get_next_bill_summary,
    list_open_bills,
    list_credit_card_due_reminders,
    list_installment_groups,
    monthly_summary_credit_debit,
    import_credit_ofx_bulk,
    consolidate_duplicate_bills,
    get_installment_group_summaries,
)

# ── Open Finance ──────────────────────────────────────────────────────────────
from .open_finance import (
    create_mock_open_finance_connection,
    get_open_finance_snapshot,
    disconnect_open_finance_connection,
)

# ── Relatórios, Auth, Dashboard, Engajamento ──────────────────────────────────
from .reports import (
    set_daily_report_enabled,
    set_daily_report_hour,
    get_daily_report_prefs,
    list_users_with_daily_report_enabled,
    list_identities_by_user,
    mark_daily_report_sent,
    claim_daily_report_send,
    was_daily_report_sent_today,
    get_last_ofx_import_end_date,
    register_auth_user,
    login_auth_user,
    get_auth_user,
    auto_link_auth_user,
    create_dashboard_session,
    get_dashboard_session,
    consume_dashboard_session,
    update_user_plan,
    get_user_by_stripe_customer,
    set_stripe_customer,
    create_email_verification,
    confirm_email_verification,
    attempt_whatsapp_phone_link,
    create_password_reset_token,
    consume_password_reset_token,
    update_last_activity,
    get_users_for_engagement,
    mark_reengagement_sent,
    mark_tip_sent,
    mark_insight_sent,
    set_engagement_opt_out,
    get_user_by_email,
)

__all__ = [
    # connection
    "get_conn",
    # schema
    "init_db",
    # users
    "ensure_user_tx", "ensure_user", "merge_users", "choose_primary_user", "user_score",
    "get_or_create_canonical_user", "create_link_code", "create_platform_onboarding_token",
    "consume_platform_onboarding_token", "consume_link_code", "bind_identity",
    "link_platform_identity",
    # accounts
    "get_balance", "set_balance", "add_launch_and_update_balance", "list_launches",
    "update_launch_category", "update_launch_categories_bulk", "export_launches",
    "get_launches_by_period", "get_summary_by_period", "delete_launch_and_rollback",
    "get_ofx_import_by_hash", "import_ofx_launches_bulk",
    # pockets
    "list_pockets", "pocket_withdraw_to_account", "create_pocket",
    "pocket_deposit_from_account", "delete_pocket",
    # investments
    "create_investment", "create_investment_db", "delete_investment", "list_investments",
    "accrue_all_investments", "accrue_investment_db", "investment_deposit_from_account",
    "investment_withdraw_to_account", "get_latest_cdi", "get_latest_cdi_aa",
    "get_latest_cdi_daily_pct", "get_latest_selic_aa", "get_latest_ipca_12m",
    "get_dashboard_market_rates", "_get_cdi_daily_map", "_business_days_between",
    # categories
    "list_category_rules", "add_category_rule", "delete_category_rule",
    "delete_category_rules_by_category", "list_categories",
    "get_memorized_category", "upsert_category_rule", "list_user_category_rules",
    "resolve_category_rule_target",
    # pending
    "set_pending_action", "get_pending_action", "clear_pending_action",
    # cards
    "card_name_exists", "create_card", "delete_card", "get_card_id_by_name",
    "set_default_card", "get_default_card_id", "list_cards", "get_card_by_id",
    "get_card_credit_usage", "set_card_limit", "update_card_reminder_settings",
    "mark_card_reminder_sent", "add_months", "bill_period_for_month",
    "get_or_create_open_bill", "get_or_create_bill_by_period", "get_current_open_bill_id",
    "add_credit_purchase", "add_credit_purchase_installments", "add_credit_refund",
    "undo_credit_transaction", "undo_installment_group", "resolve_installment_group_id",
    "get_open_bill_summary", "pay_bill_amount", "close_bill", "get_next_bill_summary",
    "list_open_bills", "list_credit_card_due_reminders", "list_installment_groups",
    "monthly_summary_credit_debit", "import_credit_ofx_bulk", "consolidate_duplicate_bills",
    "get_installment_group_summaries",
    # open finance
    "create_mock_open_finance_connection", "get_open_finance_snapshot",
    "disconnect_open_finance_connection",
    # reports
    "set_daily_report_enabled", "set_daily_report_hour", "get_daily_report_prefs",
    "list_users_with_daily_report_enabled", "list_identities_by_user",
    "mark_daily_report_sent", "claim_daily_report_send", "was_daily_report_sent_today",
    "get_last_ofx_import_end_date",
    "register_auth_user", "login_auth_user", "get_auth_user", "auto_link_auth_user",
    "create_dashboard_session", "get_dashboard_session", "consume_dashboard_session",
    "update_user_plan", "get_user_by_stripe_customer", "set_stripe_customer",
    "create_email_verification", "confirm_email_verification", "attempt_whatsapp_phone_link",
    "create_password_reset_token", "consume_password_reset_token",
    "update_last_activity", "get_users_for_engagement", "mark_reengagement_sent",
    "mark_tip_sent", "mark_insight_sent", "set_engagement_opt_out", "get_user_by_email",
]
