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
    # PR-B (#637), decisões do dono de 2026-11-12/13. As de `select *` não têm coluna de outra pessoa nem segredo.
    "conversas_ia": ("ai_messages", "select * from ai_messages where user_id = %(uid)s"),  # sem o `/d/{code}`: PB8
    "acoes_pendentes_ia": ("ai_pending_actions", "select * from ai_pending_actions where user_id = %(uid)s"),
    "perguntas_sem_resposta_ia": ("ai_fallback_log", "select * from ai_fallback_log where user_id = %(uid)s"),
    "insights_ia": ("ai_proactive_cache", "select * from ai_proactive_cache where user_id = %(uid)s"),
    # Sem `chave` e `payload_hash`: chaves internas de idempotência.
    "declaracoes_ia": ("ia_declaracao_livro", """
        select id, user_id, papel, estado, acao, launch_id, user_seq, criado_em, mudou_em
        from ia_declaracao_livro where user_id = %(uid)s
    """),
    "agentes": ("agents", "select * from agents where user_id = %(uid)s"),
    "alertas_agentes": ("agent_events", "select * from agent_events where user_id = %(uid)s"),
    "despesas_fixas": ("recurring_expenses", "select * from recurring_expenses where user_id = %(uid)s"),
    "receitas_fixas": ("recurring_incomes", "select * from recurring_incomes where user_id = %(uid)s"),
    "cobrancas_despesas_fixas": ("recurring_charges", "select * from recurring_charges where user_id = %(uid)s"),
    "creditos_receitas_fixas": ("recurring_income_credits",
                                "select * from recurring_income_credits where user_id = %(uid)s"),
    "sugestoes_fixas_dispensadas": ("recurring_suggestion_dismissed",
                                    "select * from recurring_suggestion_dismissed where user_id = %(uid)s"),
    "contas_a_pagar": ("bill_instances", "select * from bill_instances where user_id = %(uid)s"),
    "lotes_caixinhas": ("pocket_lots", "select * from pocket_lots where user_id = %(uid)s"),
    "categorias": ("user_categories", "select * from user_categories where user_id = %(uid)s"),
    "espacos_financeiros": ("financial_spaces", "select * from financial_spaces where user_id = %(uid)s"),
    "orcamento_domestico_potes": ("household_budget_config",
                                  "select * from household_budget_config where user_id = %(uid)s"),
    "orcamento_domestico_renda": ("household_budget_income",
                                  "select * from household_budget_income where user_id = %(uid)s"),
    "alertas_orcamento_enviados": ("budget_alert_sent", "select * from budget_alert_sent where user_id = %(uid)s"),
    # Sem `connect_token_hash`. Só por `user_id`: o mesmo `provider_item_id` pode ter linha de outro dono.
    "itens_open_finance": ("open_finance_item_registry", """
        select id, provider, provider_item_id, origin, status, last_event, created_at, updated_at, removal_tracked
        from open_finance_item_registry where user_id = %(uid)s
    """),
    "indicacao_prospeccao": ("prospect_referrals",
                             "select code, created_at from prospect_referrals where referred_user_id = %(uid)s"),
    "planos_concedidos": ("plan_grants", "select * from plan_grants where user_id = %(uid)s"),
    # Sem `phone_hash` (hash do telefone, a PK).
    "periodo_de_teste": ("plan_trials", "select user_id, started_at, model_version from plan_trials"
                                        " where user_id = %(uid)s"),
    # Sem `session_id` do checkout do Stripe.
    "funil_assinatura": ("checkout_funnel_events",
                         "select kind, created_at from checkout_funnel_events where user_id = %(uid)s"),
    "remarketing_envios": ("remarketing_envios", "select * from remarketing_envios where user_id = %(uid)s"),
    "remarketing_regua": ("remarketing_regua", "select * from remarketing_regua where user_id = %(uid)s"),
    "reconquista_teste": ("reconquista_trial", "select * from reconquista_trial where user_id = %(uid)s"),
    "auditoria_conta": ("audit_events", "select * from audit_events where user_id = %(uid)s"),
    # Sem `email_enc`, `jti`, `token`/`session_jti` e `secret_encrypted`: credenciais.
    "logins_google_apple": ("auth_identities", "select provider, provider_sub, email, created_at"
                                               " from auth_identities where user_id = %(uid)s"),
    "sessoes_login": ("auth_sessions", "select ip, user_agent, created_at, last_seen_at, revoked_at"
                                       " from auth_sessions where user_id = %(uid)s"),
    "dispositivos_push": ("push_tokens", "select platform, environment, created_at, updated_at"
                                         " from push_tokens where user_id = %(uid)s"),
    "verificacao_em_duas_etapas": ("user_mfa", "select enabled, activated_at, last_used_at, created_at"
                                               " from user_mfa where user_id = %(uid)s"),
    # P2 (a): agregado. O ator vira categoria por lista explícita (PB3 c): prefixo novo de gravador entra aqui.
    # Só a finalidade antes de `:` (PB4 a): `cli_inspect:<motivo>` traz texto livre do operador. Sem nome do
    # operador, `endpoint` e `extra`. `split_part` e não `like 'x:%'`: com parâmetro o `%` vira `%%`.
    "acessos_dados_pessoais": ("pii_access_log", """
        select (created_at at time zone 'America/Sao_Paulo')::date as dia,
               case split_part(actor, ':', 1) when 'user' then 'você'
                    when 'system' then 'sistema' when 'webhook' then 'sistema'
                    when 'admin' then 'equipe PigBank' when 'cli' then 'equipe PigBank'
                    else 'não identificado' end as quem,
               split_part(purpose, ':', 1) as finalidade, field as dado, count(*) as acessos
        from pii_access_log where subject_user_id = %(uid)s
        group by 1, 2, 3, 4 order by 1, 2, 3, 4
    """),
    # Plano Família (P1 a): cada um recebe a própria participação, sem id nem uid de outra pessoa. Duas colunas
    # de pessoa na mesma tabela: o papel certo é provado em tests/test_exportacao_familia.py.
    "familia_participacao": ("family_members",  # PB5 (a): toda linha da pessoa, em qualquer papel
                             "select role, joined_at, left_at, left_reason from family_members where user_id = %(uid)s"),
    "familia_permissoes": ("family_consents",  # o que ELA liberou; o viewer (titular do grupo) não sai
                           "select scope, granted_at, revoked_at from family_consents where member_user_id = %(uid)s"),
    # `log_read` só grava se o viewer for o titular do grupo (db/family_consents.py).
    "familia_leituras_dos_meus_dados": ("family_access_log", """
        select 'titular do grupo' as quem_leu, scope, recurso, at
        from family_access_log where member_user_id = %(uid)s
    """),
    "familia_grupo": ("family_groups", "select tier, created_at, members_suspended_at from family_groups"
                                       " where owner_user_id = %(uid)s"),
    "familia_assentos": ("family_seat_purchases", """
        select p.source, p.external_ref, p.amount_cents, p.status, p.valid_from, p.valid_until
        from family_seat_purchases p join family_groups g on g.id = p.group_id
        where g.owner_user_id = %(uid)s
    """),
}

_CREDENCIAL = ("credencial efêmera (código, token ou HMAC); o pedido de exportação já sai em"
               " eventos_sistema (data_export_requested)")

# tabela -> por que não sai no ZIP.
NAO_EXPORTADAS: dict[str, str] = {
    **dict.fromkeys((
        "account_reauth_codes", "data_export_tokens", "link_codes", "mfa_login_challenges",
        "password_reset_tokens", "platform_onboarding_tokens",
    ), _CREDENCIAL),
    "auth_refresh_tokens": "só `token_hash` e o mesmo IP e aparelho que as sessões de login",
    "user_mfa_backup_codes": "só hashes dos códigos de recuperação",
    "family_invites": ("convite do Plano Família: só o hash do token e as datas; a entrada no grupo sai em"
                       " familia_participacao, e quem usou o convite de outra pessoa é dado de terceiro"),
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
    ("indicacao_prospeccao", "code"): "código do link /i/{code} do funil de prospecção que a pessoa usou;"
                                      " não abre nada",
}
