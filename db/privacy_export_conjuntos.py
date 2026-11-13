"""db/privacy_export_conjuntos.py — o que a exportação LGPD leva, e o que fica de fora com o motivo.

Só dados, sem I/O; quem monta o ZIP é `db/privacy_export.py`. Toda tabela que chega a `users` por FK é
tabela principal de um dataset em `EXPORTADAS` ou chave de `NAO_EXPORTADAS`, e toda SQL filtra pelo
titular com `%(uid)s`: a guarda é `tests/test_exportacao_completa.py`.
"""
from .family_house import TABELAS_DA_CASA

# dataset (= csv/<dataset>.csv) -> (tabela principal, SQL). A ordem é a ordem dos datasets no ZIP.
EXPORTADAS: dict[str, tuple[str, str]] = {
    "usuario": ("users", "select * from users where id = %(uid)s"),
    "conta_login": ("auth_accounts", """
        select id, user_id, display_name, email, phone_e164, phone_status, phone_confirmed_at,
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
        where user_id = %(uid)s
    """),
    # Sem `external_id_hash` e `external_id_enc`: o hash e o cifrado do mesmo `external_id` que já sai.
    "identidades": ("user_identities",
                    "select provider, external_id, created_at from user_identities where user_id = %(uid)s"),
    "contas": ("accounts", "select * from accounts where user_id = %(uid)s"),
    "lancamentos": ("launches", "select * from launches where user_id = %(uid)s"),
    "declaracoes_bancarias": ("bank_movement_declarations",
                              "select * from bank_movement_declarations where user_id = %(uid)s"),
    "saques_depositos_dinheiro": ("of_cash_links", "select * from of_cash_links where user_id = %(uid)s"),
    "cobertura_open_finance": ("of_cash_coverage", "select * from of_cash_coverage where user_id = %(uid)s"),
    "orcamentos": ("category_budgets", "select * from category_budgets where user_id = %(uid)s"),
    "regras_categorias": ("user_category_rules", "select * from user_category_rules where user_id = %(uid)s"),
    # As três próximas não existem no schema e saem sempre vazias: pendente DP9 (consulta em produção).
    "gatilhos_categorias": ("user_category_triggers",
                            "select * from user_category_triggers where user_id = %(uid)s"),
    "candidatos_gatilhos_categorias": ("user_trigger_candidates",
                                       "select * from user_trigger_candidates where user_id = %(uid)s"),
    "feedback_categorias": ("user_category_feedback",
                            "select * from user_category_feedback where user_id = %(uid)s"),
    "acoes_pendentes": ("pending_actions", "select * from pending_actions where user_id = %(uid)s"),
    "caixinhas": ("pockets", "select * from pockets where user_id = %(uid)s"),
    "investimentos": ("investments", "select * from investments where user_id = %(uid)s"),
    "historico_patrimonio": ("patrimonio_fotos", "select * from patrimonio_fotos where user_id = %(uid)s"),
    "lotes_investimentos": ("investment_lots", "select * from investment_lots where user_id = %(uid)s"),
    "cartoes": ("credit_cards", "select * from credit_cards where user_id = %(uid)s"),
    "faturas_cartao": ("credit_bills", "select * from credit_bills where user_id = %(uid)s"),
    "transacoes_cartao": ("credit_transactions", "select * from credit_transactions where user_id = %(uid)s"),
    "preferencias_resumo_diario": ("daily_report_prefs",
                                   "select * from daily_report_prefs where user_id = %(uid)s"),
    "marcacoes_assinaturas": ("subscription_marks", "select * from subscription_marks where user_id = %(uid)s"),
    "insights_estado": ("insight_estado", "select * from insight_estado where user_id = %(uid)s"),
    "guia_do_painel": ("guia_painel", "select * from guia_painel where user_id = %(uid)s"),
    "metas": ("metas", "select * from metas where user_id = %(uid)s"),
    "importacoes_ofx": ("ofx_imports", "select * from ofx_imports where user_id = %(uid)s"),
    # Sem `code`: é o link mágico `/d/{code}`, que vale 2 h e abre a conta.
    "sessoes_dashboard": ("dashboard_sessions",
                          "select user_id, expires_at, created_at from dashboard_sessions where user_id = %(uid)s"),
    # O `raw` sai sem string com `token=` (o `webhookUrl` leva o do webhook): `_sem_token`, `db/privacy_export.py`.
    "conexoes_open_finance": ("open_finance_connections",
                              "select * from open_finance_connections where user_id = %(uid)s"),
    "contas_open_finance": ("open_finance_accounts", """
        select a.*
        from open_finance_accounts a
        join open_finance_connections c on c.id = a.connection_id
        where c.user_id = %(uid)s
    """),
    "transacoes_open_finance": ("open_finance_transactions", """
        select t.*
        from open_finance_transactions t
        join open_finance_accounts a on a.id = t.account_id
        join open_finance_connections c on c.id = a.connection_id
        where c.user_id = %(uid)s
    """),
    "investimentos_open_finance": ("open_finance_investments", """
        select i.*
        from open_finance_investments i
        join open_finance_connections c on c.id = i.connection_id
        where c.user_id = %(uid)s
    """),
    "historico_investimentos_open_finance": ("open_finance_investment_snapshots", """
        select s.*
        from open_finance_investment_snapshots s
        join open_finance_connections c on c.id = s.connection_id
        where c.user_id = %(uid)s
    """),
    "recorrencias_open_finance": ("of_recurring_payments", """
        select rp.*
        from of_recurring_payments rp
        join open_finance_connections c on c.id = rp.connection_id
        where c.user_id = %(uid)s
    """),
    "faturas_cartao_open_finance": ("of_card_bills", """
        select b.*
        from of_card_bills b
        join open_finance_accounts a on a.id = b.account_id
        join open_finance_connections c on c.id = a.connection_id
        where c.user_id = %(uid)s
    """),
    # Programa de afiliados (#1415): colunas NOMEADAS e só do titular. Ficam de fora
    # `referred_user_id` e `stripe_invoice_id` (identificam o indicado e a fatura dele),
    # o `pix_key_hash`, as pendências e eventos de reembolso e os pagamentos do indicado.
    # `pix_key_enc` sai daqui só para virar `chave_pix` em claro em `db/privacy_export.py`.
    # `count(r.id)` e não `count(*)`: o left join daria 1 a quem não tem indicado; conta
    # também a indicação de quem excluiu a conta, igual a `get_affiliate_stats`.
    # `original_amount_cents` só equivale ao de `list_affiliate_commissions` com a revisão da mesma
    # fatura (o join a exige; por construção ela nasce pela fatura da comissão; unique: no máximo uma).
    "afiliado": ("affiliates", """
        select a.code, a.status, a.commission_bps, a.created_at, a.pix_key_enc,
               count(r.id) as indicacoes
        from affiliates a
        left join affiliate_referrals r on r.affiliate_id = a.id
        where a.user_id = %(uid)s
        group by a.id
    """),
    "afiliado_comissoes": ("affiliate_commissions", """
        select c.id, c.invoice_amount_cents, c.amount_cents, c.commission_bps, c.paid_months,
               c.status, c.available_at, c.payout_id, c.payment_check_pending, c.created_at,
               v.amount_cents as original_amount_cents
        from affiliate_commissions c
        join affiliates a on a.id = c.affiliate_id
        left join affiliate_refund_reviews v
          on v.stripe_invoice_id = c.stripe_invoice_id
         and v.commission_id = c.id
         and v.amount_cents <> c.amount_cents
        where a.user_id = %(uid)s
    """),
    "afiliado_saques": ("affiliate_payouts", """
        select p.id, p.amount_cents, p.status, p.requested_at, p.paid_at, p.note, p.pix_key_enc
        from affiliate_payouts p
        join affiliates a on a.id = p.affiliate_id
        where a.user_id = %(uid)s
    """),
    "indicacao_recebida": ("affiliate_referrals", """
        select code_used, created_at
        from affiliate_referrals
        where referred_user_id = %(uid)s
    """),
    "eventos_login": ("auth_login_events", """
        select id, user_id, email, success, failure_reason, ip_address, user_agent, created_at
        from auth_login_events where user_id = %(uid)s
    """),
    "eventos_sistema": ("system_event_logs", """
        select id, level, event_type, message, source, user_id, details, created_at
        from system_event_logs where user_id = %(uid)s
    """),
    # Cobranças Pix (§3.2). Colunas NOMEADAS e não `select *`: o
    # `qr_payload_enc` fica de fora porque é o "copia e cola" que MOVE
    # dinheiro (§13.6) — cifrado no banco, ele sairia daqui em texto
    # inútil para a pessoa e útil para quem interceptasse o ZIP. O
    # rastreio (`ga_client_id`, `fbp`, `fbc`) entra: é dado sobre a
    # pessoa, e é justamente o que ela tem direito de ver.
    "cobrancas_pix": ("pix_charges", """
        select id, user_id, external_reference, asaas_payment_id,
               asaas_customer_id, plan, plan_stored, price_cents,
               credit_cents, amount_cents, currency, duration_days,
               stripe_subscription_id, public_token, status,
               due_date, qr_expires_at, access_starts_at,
               access_expires_at, ga_client_id, fbp, fbc,
               created_at, paid_at, canceled_at, refunded_at, purged_at
        from pix_charges
        where user_id = %(uid)s
    """),
}

_PR_B = "pendente: entra no PR-B da exportação (#637), decisões do dono de 2026-11-12"
_CREDENCIAL = ("credencial efêmera (código, token ou HMAC); o pedido de exportação já sai em"
               " eventos_sistema (data_export_requested)")

# tabela -> por que não sai no ZIP.
NAO_EXPORTADAS: dict[str, str] = {
    **dict.fromkeys((
        "agents", "agent_events", "ai_messages", "ai_pending_actions", "ai_fallback_log", "ai_proactive_cache",
        "ia_declaracao_livro", "recurring_expenses", "recurring_incomes", "recurring_charges",
        "recurring_income_credits", "recurring_suggestion_dismissed", "bill_instances", "pocket_lots",
        "user_categories", "financial_spaces", "household_budget_config", "household_budget_income",
        "budget_alert_sent", "open_finance_item_registry", "prospect_referrals", "plan_grants", "plan_trials",
        "checkout_funnel_events", "remarketing_envios", "remarketing_regua", "reconquista_trial",
        "audit_events", "auth_identities", "auth_sessions", "push_tokens", "user_mfa",
        "pii_access_log",  # P2: sai agregado
        # P1: cada pessoa recebe só a própria participação
        "family_groups", "family_members", "family_consents", "family_access_log", "family_invites",
        "family_seat_purchases",
    ), _PR_B),
    **dict.fromkeys((
        "account_reauth_codes", "data_export_tokens", "link_codes", "mfa_login_challenges",
        "password_reset_tokens", "platform_onboarding_tokens",
    ), _CREDENCIAL),
    "auth_refresh_tokens": "só `token_hash` e o mesmo IP e aparelho que as sessões de login",
    "user_mfa_backup_codes": "só hashes dos códigos de recuperação",
    "ebook_entregas": "decidido fora (docs/CLAUDE.md, ebook)",
    "stripe_email_pendente": "decidido fora (docs/CLAUDE.md): só `versao`, sem dado pessoal",
    "affiliate_referral_payments": "decidido fora no #1532: pagamentos do indicado",
    "affiliate_refund_review_events": "decidido fora no #1532: eventos internos da revisão de reembolso",
    "affiliate_refund_reviews": "#1532: só `original_amount_cents`, por join em afiliado_comissoes",
    **dict.fromkeys(TABELAS_DA_CASA, "item do grupo, não da pessoa (docs/CLAUDE.md, Plano Família)"),
}

# (dataset, coluna que a SQL devolve) que casa o padrão de segredo da guarda e não é segredo, com o motivo.
_VIRA_CHAVE_PIX = "sai da SQL só para virar `chave_pix` em claro (`_chave_pix_em_claro`); o cifrado não chega ao ZIP"
COLUNAS_SEM_SEGREDO: dict[tuple[str, str], str] = {
    ("importacoes_ofx", "file_hash"): "hash do arquivo importado, para deduplicar; não é credencial",
    ("afiliado", "code"): "código público do link de indicação `/r/{code}`, feito para ser divulgado",
    ("afiliado", "pix_key_enc"): _VIRA_CHAVE_PIX,
    ("afiliado_saques", "pix_key_enc"): _VIRA_CHAVE_PIX,
    ("cobrancas_pix", "public_token"): "id do poll da cobrança, que também filtra por `user_id`"
                                       " (`buscar_por_public_token`, db/pix_charges.py): sem a sessão não abre nada",
}
